import asyncio
import os
import queue
import uuid

from dotenv import load_dotenv
from PyQt5.QtCore import QThread, pyqtSignal

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from app.core.pet_command import PetCommand


SYSTEM_PROMPT = """你是一个控制 Live2D 桌宠的 Agent。
你必须只输出 JSON，格式如下：
{
  "reply": "给用户的回复文本",
  "expression": "neutral|happy|sad|angry|surprised|shy",
  "motion": "idle|wave|tap_body|flick_head|jump",
  "emotion_timeline": [
    {"text": "分句1", "emotion": "happy"},
    {"text": "分句2", "emotion": "sad"}
  ]
}

规则：
1. 只输出 JSON，不要输出额外说明。
2. reply 要简洁自然。
3. expression 表示整体主情绪。
4. emotion_timeline 必须按 reply 的内容顺序给出分句情绪；每个 text 应是 reply 的连续片段。
5. emotion_timeline 至少 1 段，最多 6 段。
"""


class LLMWorker(QThread):
    chunk_ready = pyqtSignal(str)
    response_complete = pyqtSignal()
    error_occurred = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    text_for_voice = pyqtSignal(str)
    voice_payload_ready = pyqtSignal(dict)
    pet_command_ready = pyqtSignal(dict)
    new_session = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_queue: "queue.Queue[str | None]" = queue.Queue()
        self._running = True
        self._agent = None
        self._model = None
        self._config = {"configurable": {"thread_id": f"pet-{uuid.uuid4()}"}}

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._init_agent())
            self.status_changed.emit("就绪")
            while self._running:
                try:
                    text = self.message_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if text is None:
                    break
                loop.run_until_complete(self._process_message(text))
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            loop.close()

    async def _init_agent(self):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("未找到 OPENAI_API_KEY，请先配置 .env")

        model_name = os.getenv("MODEL_NAME")
        base_url = os.getenv("OPENAI_BASE_URL")
        self._model = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0.6,
            streaming=False,
        )
        self._agent = create_agent(
            model=self._model,
            tools=[],
            system_prompt=SYSTEM_PROMPT,
        )

    async def _process_message(self, user_text: str):
        self.new_session.emit()
        self.status_changed.emit("思考中...")
        try:
            result = await self._agent.ainvoke(
                {"messages": [{"role": "user", "content": user_text}]},
                config=self._config,
            )
            messages = result.get("messages", [])
            raw_text = str(messages[-1].content if messages else "")
            cmd = PetCommand.from_llm_text(raw_text)

            self.pet_command_ready.emit(cmd.to_dict())
            self.chunk_ready.emit(cmd.reply)
            self.text_for_voice.emit(cmd.reply)
            self.voice_payload_ready.emit(cmd.to_voice_payload())
        except Exception as exc:
            self.error_occurred.emit(f"LLM 调用失败: {exc}")
        finally:
            self.response_complete.emit()
            self.status_changed.emit("就绪")

    def send_message(self, text: str):
        self.message_queue.put(text)

    def stop(self):
        self._running = False
        self.message_queue.put(None)
        self.wait(3000)
