import asyncio
import io
import logging
import os
import queue
from functools import partial

from PyQt5.QtCore import QThread, pyqtSignal, pyqtSlot

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"

try:
    import edge_tts

    HAS_TTS = True
except ImportError:
    HAS_TTS = False
    edge_tts = None

pygame = None
HAS_PYGAME = False


class VoiceWorker(QThread):
    voice_started = pyqtSignal()
    voice_finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_queue: "queue.Queue[tuple[int, str] | None]" = queue.Queue()
        self.audio_queue: "queue.Queue[tuple[int, bytes] | None]" = queue.Queue()
        self._running = True
        self._enabled = True
        self._muted = False
        self._pygame_ready = False
        self._session_id = 0
        self._queue_empty = object()
        self._is_playing = False

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main_loop())
        finally:
            if self._pygame_ready and pygame:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
            loop.close()

    async def _main_loop(self):
        synth_task = asyncio.create_task(self._synthesis_loop())
        play_task = asyncio.create_task(self._playback_loop())
        while self._running:
            await asyncio.sleep(0.1)
        synth_task.cancel()
        play_task.cancel()
        for task in (synth_task, play_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _synthesis_loop(self):
        while self._running:
            item = await self._queue_get(self.text_queue, timeout=0.2)
            if item is self._queue_empty:
                continue
            if item is None:
                return
            session_id, text = item
            if not self._enabled or session_id != self._session_id:
                continue
            if not text.strip():
                continue
            try:
                audio = await self._synthesize(text, session_id)
                if audio and session_id == self._session_id:
                    self.audio_queue.put((session_id, audio))
            except Exception as exc:
                self.error_occurred.emit(f"TTS 合成失败: {exc}")

    async def _playback_loop(self):
        while self._running:
            if not self._ensure_pygame_ready():
                await asyncio.sleep(0.2)
                continue

            if self._pygame_ready and pygame and self._is_playing and not pygame.mixer.music.get_busy():
                self._is_playing = False
                self.voice_finished.emit()

            item = await self._queue_get(self.audio_queue, timeout=0.1)
            if item is self._queue_empty:
                continue
            if item is None:
                return

            session_id, audio_data = item
            if session_id != self._session_id or not audio_data:
                continue
            if not self._enabled:
                continue

            try:
                stream = io.BytesIO(audio_data)
                pygame.mixer.music.load(stream)
                pygame.mixer.music.set_volume(0.0 if self._muted else 1.0)
                pygame.mixer.music.play()
                self._is_playing = True
                self.voice_started.emit()
            except Exception as exc:
                self.error_occurred.emit(f"语音播放失败: {exc}")

    async def _synthesize(self, text: str, session_id: int) -> bytes:
        if not HAS_TTS:
            raise RuntimeError("edge-tts 未安装，请先安装 requirements.txt")
        voice_name = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        communicate = edge_tts.Communicate(text=text, voice=voice_name)
        buffer = bytearray()
        async for chunk in communicate.stream():
            if session_id != self._session_id:
                return b""
            if chunk.get("type") == "audio":
                buffer.extend(chunk.get("data", b""))
        return bytes(buffer)

    async def _queue_get(self, q: queue.Queue, timeout: float = 0.1):
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, partial(q.get, True, timeout))
        except queue.Empty:
            return self._queue_empty

    def _ensure_pygame_ready(self) -> bool:
        global pygame, HAS_PYGAME
        if not HAS_PYGAME:
            try:
                import pygame as _pygame

                pygame = _pygame
                HAS_PYGAME = True
            except Exception as exc:
                logging.warning("pygame 导入失败: %s", exc)
                return False
        if self._pygame_ready:
            return True
        try:
            pygame.mixer.init()
            self._pygame_ready = True
            return True
        except Exception as exc:
            logging.warning("pygame 初始化失败: %s", exc)
            return False

    @pyqtSlot(str)
    def add_text(self, text: str):
        if self._enabled and text.strip():
            self.text_queue.put((self._session_id, text))

    @pyqtSlot()
    def start_new_session(self):
        self._session_id += 1
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
            except Exception:
                break
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except Exception:
                break
        if self._pygame_ready and pygame:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        if self._is_playing:
            self._is_playing = False
            self.voice_finished.emit()

    @pyqtSlot(bool)
    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled and self._pygame_ready and pygame:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    @pyqtSlot(bool)
    def set_mute(self, muted: bool):
        self._muted = muted
        if self._pygame_ready and pygame:
            try:
                pygame.mixer.music.set_volume(0.0 if muted else 1.0)
            except Exception:
                pass

    def stop(self):
        self._running = False
        self.text_queue.put(None)
        self.audio_queue.put(None)
        self.wait(3000)
