import asyncio
import os
import queue
import uuid

from dotenv import load_dotenv
from PyQt5.QtCore import QThread, pyqtSignal

from langchain_openai import ChatOpenAI

from app.core.pet_command import PetCommand


SYSTEM_PROMPT = """你是一个控制 Live2D 桌宠的 Agent。你必须只输出 JSON，格式如下：
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
5. emotion_timeline 至少 1 段，最多 6 段。"""


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
            streaming=True,
        )

    async def _process_message(self, user_text: str):
        self.new_session.emit()
        self.status_changed.emit("思考中...")
        try:
            raw_text, streamed_reply, emitted_any = await self._stream_json_and_feed_tts(user_text)
            cmd = PetCommand.from_llm_text(raw_text)
            if not cmd.reply.strip() and streamed_reply.strip():
                cmd.reply = streamed_reply.strip()
            if not cmd.reply.strip():
                cmd.reply = "我在。"

            self.pet_command_ready.emit(cmd.to_dict())
            self.chunk_ready.emit(cmd.reply)
            self.text_for_voice.emit(cmd.reply)

            if not emitted_any:
                # Fallback: parser miss or stream disabled upstream.
                self.voice_payload_ready.emit(cmd.to_voice_payload())
        except Exception as exc:
            self.error_occurred.emit(f"LLM 调用失败: {exc}")
        finally:
            self.response_complete.emit()
            self.status_changed.emit("就绪")

    async def _stream_json_and_feed_tts(self, user_text: str) -> tuple[str, str, bool]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]

        raw_parts: list[str] = []
        parser = _ReplyJsonStreamParser()
        pending = ""
        emitted_any = False

        async for chunk in self._model.astream(messages, config=self._config):
            delta = _chunk_to_text(chunk)
            if not delta:
                continue
            raw_parts.append(delta)
            reply_delta = parser.feed(delta)
            if not reply_delta:
                continue
            pending += reply_delta
            segments, pending = _split_tts_segments(pending, force=False)
            for seg in segments:
                if not _is_segment_speakable(seg):
                    continue
                emo = _infer_emotion(seg)
                self.voice_payload_ready.emit(
                    {
                        "text": seg,
                        "base_emotion": emo,
                        "emotion_timeline": [{"text": seg, "emotion": emo}],
                    }
                )
                emitted_any = True

        # Flush remaining parser output.
        pending += parser.pending_reply_text()
        segments, pending = _split_tts_segments(pending, force=True)
        for seg in segments:
            if not _is_segment_speakable(seg):
                continue
            emo = _infer_emotion(seg)
            self.voice_payload_ready.emit(
                {
                    "text": seg,
                    "base_emotion": emo,
                    "emotion_timeline": [{"text": seg, "emotion": emo}],
                }
            )
            emitted_any = True

        raw_text = "".join(raw_parts)
        streamed_reply = parser.full_reply_text().strip()
        return raw_text, streamed_reply, emitted_any

    def send_message(self, text: str):
        self.message_queue.put(text)

    def stop(self):
        self._running = False
        self.message_queue.put(None)
        self.wait(3000)


def _chunk_to_text(chunk) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)
    return str(content or "")


def _infer_emotion(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["生气", "愤怒", "讨厌", "angry", "mad"]):
        return "angry"
    if any(k in t for k in ["难过", "伤心", "sad", "遗憾"]):
        return "sad"
    if any(k in t for k in ["惊讶", "哇", "surpris", "竟然", "原来"]):
        return "surprised"
    if any(k in t for k in ["害羞", "脸红", "shy"]):
        return "shy"
    if any(k in t for k in ["开心", "高兴", "喜欢", "happy", "太好"]):
        return "happy"
    return "neutral"


def _split_tts_segments(text: str, force: bool = False) -> tuple[list[str], str]:
    buf = text or ""
    out: list[str] = []
    if not buf:
        return out, ""

    strong = set("。！？!?；;\n")
    weak = set("，,、：:")
    min_strong = 6
    min_weak = 12
    max_hold = 28

    start = 0
    i = 0
    while i < len(buf):
        ch = buf[i]
        seg_len = i - start + 1
        if ch in strong and seg_len >= min_strong:
            seg = buf[start : i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        elif ch in weak and seg_len >= min_weak:
            seg = buf[start : i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        elif seg_len >= max_hold:
            seg = buf[start : i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        i += 1

    tail = buf[start:]
    if force:
        tail_s = tail.strip()
        if tail_s:
            out.append(tail_s)
        tail = ""
    return out, tail


def _is_segment_speakable(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    # Require at least one CJK/letter/digit to avoid punctuation-only TTS calls.
    for ch in s:
        o = ord(ch)
        if ("0" <= ch <= "9") or ("A" <= ch <= "Z") or ("a" <= ch <= "z") or (0x4E00 <= o <= 0x9FFF):
            return True
    return False


class _ReplyJsonStreamParser:
    KEY = '"reply"'

    def __init__(self):
        self._buf = ""
        self._key_found = False
        self._in_value = False
        self._escaping = False
        self._done = False
        self._pending: list[str] = []
        self._full: list[str] = []

    def feed(self, delta: str) -> str:
        if self._done or not delta:
            return ""
        self._buf += delta
        i = 0

        while i < len(self._buf):
            ch = self._buf[i]
            if not self._key_found:
                kpos = self._buf.find(self.KEY, i)
                if kpos < 0:
                    keep = max(0, len(self._buf) - (len(self.KEY) + 4))
                    self._buf = self._buf[keep:]
                    return self._drain_pending()
                i = kpos + len(self.KEY)
                self._key_found = True
                continue

            if self._key_found and not self._in_value:
                q = self._buf.find('"', i)
                if q < 0:
                    self._buf = self._buf[max(0, len(self._buf) - 4) :]
                    return self._drain_pending()
                i = q + 1
                self._in_value = True
                self._escaping = False
                continue

            if self._in_value:
                if self._escaping:
                    decoded = _decode_json_escape(ch)
                    self._pending.append(decoded)
                    self._full.append(decoded)
                    self._escaping = False
                elif ch == "\\":
                    self._escaping = True
                elif ch == '"':
                    self._done = True
                    self._buf = ""
                    return self._drain_pending()
                else:
                    self._pending.append(ch)
                    self._full.append(ch)
                i += 1
                continue

            i += 1

        self._buf = ""
        return self._drain_pending()

    def _drain_pending(self) -> str:
        if not self._pending:
            return ""
        out = "".join(self._pending)
        self._pending.clear()
        return out

    def pending_reply_text(self) -> str:
        return self._drain_pending()

    def full_reply_text(self) -> str:
        return "".join(self._full)


def _decode_json_escape(ch: str) -> str:
    mapping = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    return mapping.get(ch, ch)
