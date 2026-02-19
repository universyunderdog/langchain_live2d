import asyncio
import io
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
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
    viseme_weights_changed = pyqtSignal(dict)
    emphasis_triggered = pyqtSignal(float)
    emotion_detected = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_queue: "queue.Queue[tuple[int, dict] | None]" = queue.Queue()
        self.audio_queue: "queue.Queue[tuple[int, bytes, list[tuple[int, int, float]], list[tuple[int, int]], list[tuple[int, str]], str] | None]" = queue.Queue()
        self._running = True
        self._enabled = True
        self._muted = False
        self._pygame_ready = False
        self._session_id = 0
        self._queue_empty = object()
        self._is_playing = False
        self._last_emphasis_ts = 0.0
        self._not_busy_since_ms = -1
        self._active_emotion_events: list[tuple[int, str]] = []
        self._next_emotion_event_idx = 0
        self._current_viseme_timeline: list[tuple[int, int]] = []
        self._last_viseme_id = -1
        self._last_viseme_weights: dict | None = None
        self._lipsync_advance_ms = int(os.getenv("TTS_LIPSYNC_ADVANCE_MS", "120"))
        self._debug_lipsync = os.getenv("TTS_DEBUG_LIPSYNC", "true").strip().lower() in {"1", "true", "yes", "on"}
        self._next_lipsync_log_ms = 0
        self._zero_hold_ms = int(os.getenv("TTS_ZERO_VISEME_HOLD_MS", "240"))
        self._open_carry_ms = int(os.getenv("TTS_OPEN_CARRY_MS", "480"))
        self._open_carry_floor = float(os.getenv("TTS_OPEN_CARRY_FLOOR", "0.20"))
        self._min_active_mouth = float(os.getenv("TTS_MIN_ACTIVE_MOUTH", "0.18"))
        self._last_nonzero_weights: dict | None = None
        self._last_nonzero_sync_ms = -1
        self._recent_open_ema = 0.0
        self._speech_nonzero_start_ms = -1
        self._speech_nonzero_end_ms = -1

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
            session_id, payload = item
            text = str((payload or {}).get("text", "")).strip()
            base_emotion = str((payload or {}).get("base_emotion", "")).strip().lower()
            emotion_plan = (payload or {}).get("emotion_timeline", []) or []
            if not self._enabled or session_id != self._session_id:
                continue
            if not text.strip():
                continue
            try:
                if base_emotion not in {"neutral", "happy", "sad", "angry", "surprised", "shy"}:
                    base_emotion = self._infer_emotion(text)
                audio, timeline, viseme_timeline = await self._synthesize(text, session_id)
                if audio and session_id == self._session_id:
                    emotion_events = self._build_emotion_events(
                        text=text,
                        timeline=timeline,
                        emotion_plan=emotion_plan,
                        base_emotion=base_emotion,
                    )
                    self.audio_queue.put((session_id, audio, timeline, viseme_timeline, emotion_events, base_emotion))
            except Exception as exc:
                self.error_occurred.emit(f"TTS synthesis failed: {exc}")

    async def _playback_loop(self):
        while self._running:
            if not self._ensure_pygame_ready():
                await asyncio.sleep(0.2)
                continue

            if self._pygame_ready and pygame and self._is_playing and not pygame.mixer.music.get_busy():
                now_ms = int(asyncio.get_running_loop().time() * 1000)
                if self._not_busy_since_ms < 0:
                    self._not_busy_since_ms = now_ms
                elif now_ms - self._not_busy_since_ms >= 320:
                    self._is_playing = False
                    self._not_busy_since_ms = -1
                    self._current_viseme_timeline = []
                    self._active_emotion_events = []
                    self._next_emotion_event_idx = 0
                    self._last_viseme_id = -1
                    self._last_viseme_weights = None
                    self._last_nonzero_weights = None
                    self._last_nonzero_sync_ms = -1
                    self._recent_open_ema = 0.0
                    self._speech_nonzero_start_ms = -1
                    self._speech_nonzero_end_ms = -1
                    self.viseme_weights_changed.emit({})
                    self.voice_finished.emit()
            else:
                self._not_busy_since_ms = -1

            item = await self._queue_get(self.audio_queue, timeout=0.1)
            if item is self._queue_empty:
                continue
            if item is None:
                return

            session_id, audio_data, timeline, viseme_timeline, emotion_events, base_emotion = item
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
                self._not_busy_since_ms = -1
                self._last_emphasis_ts = 0.0
                self._current_viseme_timeline = viseme_timeline or []
                self._active_emotion_events = emotion_events or []
                self._next_emotion_event_idx = 0
                self._last_viseme_id = -1
                self._last_viseme_weights = None
                self._last_nonzero_weights = None
                self._last_nonzero_sync_ms = -1
                self._recent_open_ema = 0.0
                nz = [t for t, vid in self._current_viseme_timeline if vid > 0]
                self._speech_nonzero_start_ms = nz[0] if nz else -1
                self._speech_nonzero_end_ms = nz[-1] if nz else -1
                self._next_lipsync_log_ms = 0
                self.viseme_weights_changed.emit({})
                self.voice_started.emit()
                # If timeline starts immediately, use it as initial emotion to avoid neutral->X flicker.
                if self._active_emotion_events and self._active_emotion_events[0][0] <= 0:
                    first_emo = self._active_emotion_events[0][1]
                    self.emotion_detected.emit(first_emo)
                    self._next_emotion_event_idx = 1
                else:
                    self.emotion_detected.emit(base_emotion or "neutral")
                if self._debug_lipsync:
                    logging.info(
                        "LipSync start: visemes=%d advance_ms=%d zero_hold_ms=%d min_active=%.2f",
                        len(self._current_viseme_timeline),
                        self._lipsync_advance_ms,
                        self._zero_hold_ms,
                        self._min_active_mouth,
                    )
                    if self._current_viseme_timeline:
                        logging.info("LipSync first visemes: %s", self._current_viseme_timeline[:10])
                    if self._active_emotion_events:
                        logging.info("Emotion events: %s", self._active_emotion_events[:12])
            except Exception as exc:
                self.error_occurred.emit(f"Voice playback failed: {exc}")
                continue

            while self._running and self._is_playing and pygame.mixer.music.get_busy():
                pos_ms = max(0, int(pygame.mixer.music.get_pos()))
                sync_pos_ms = max(0, pos_ms + self._lipsync_advance_ms)
                while self._next_emotion_event_idx < len(self._active_emotion_events):
                    at_ms, emo = self._active_emotion_events[self._next_emotion_event_idx]
                    if sync_pos_ms < at_ms:
                        break
                    if self._debug_lipsync:
                        logging.info(
                            "Emotion switch: pos=%d sync=%d at=%d -> %s",
                            pos_ms,
                            sync_pos_ms,
                            at_ms,
                            emo,
                        )
                    self.emotion_detected.emit(emo)
                    self._next_emotion_event_idx += 1
                viseme_id = self._viseme_for_pos(sync_pos_ms, self._current_viseme_timeline)
                if viseme_id != self._last_viseme_id:
                    self._last_viseme_id = viseme_id
                viseme_weights = self._viseme_weights_for_pos(sync_pos_ms, self._current_viseme_timeline)
                effective_weights = self._apply_zero_hold(viseme_weights, sync_pos_ms)
                effective_id = int(effective_weights.get("id", -1)) if effective_weights else -1
                # Emit every frame to keep viseme motion continuous instead of step-like.
                self._last_viseme_weights = effective_weights
                self.viseme_weights_changed.emit(effective_weights)
                if effective_id >= 0:
                    raw_level = self._viseme_open_level(effective_weights, sync_pos_ms)
                else:
                    # Pure viseme mode: do not fallback to legacy pulse waveform.
                    raw_level = 0.0
                if effective_id > 0:
                    self._recent_open_ema = self._recent_open_ema * 0.86 + raw_level * 0.14
                else:
                    raw_level = max(raw_level, self._recent_open_ema * 0.60)
                    self._recent_open_ema *= 0.94
                level = max(0.0, min(1.0, raw_level))
                # Prevent obvious mid-sentence mouth freeze on sparse/zero viseme segments.
                if (
                    self._speech_nonzero_start_ms >= 0
                    and self._speech_nonzero_end_ms > self._speech_nonzero_start_ms
                    and (self._speech_nonzero_start_ms + 60) <= sync_pos_ms <= (self._speech_nonzero_end_ms - 180)
                ):
                    level = max(level, self._min_active_mouth)
                if self._debug_lipsync and pos_ms >= self._next_lipsync_log_ms:
                    self._next_lipsync_log_ms = pos_ms + 180
                    logging.info(
                        "LipSync tick: pos=%d sync=%d viseme=%d level=%.3f dominant=%s weights=%s",
                        pos_ms,
                        sync_pos_ms,
                        effective_id,
                        level,
                        self._dominant_mouth(effective_weights),
                        self._format_weights(effective_weights),
                    )

                if level > 0.82 and (pos_ms - self._last_emphasis_ts) > 160:
                    self._last_emphasis_ts = pos_ms
                    self.emphasis_triggered.emit(min(1.0, level))

                await asyncio.sleep(0.03)

    async def _synthesize(self, text: str, session_id: int) -> tuple[bytes, list[tuple[int, int, float]], list[tuple[int, int]]]:
        if not HAS_TTS:
            raise RuntimeError("edge-tts is not installed. Please install requirements.txt")

        voice_name = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        # NOTE:
        # edge-tts Communicate(text=...) does not reliably parse raw SSML payloads.
        # Passing XML here can make the engine literally speak tags.
        # We keep a switch for compatibility, but both branches use plain text and
        # control prosody via supported arguments.
        use_ssml = os.getenv("TTS_USE_SSML", "false").strip().lower() in {"1", "true", "yes", "on"}
        rate = os.getenv("TTS_RATE", "+5%")
        pitch = os.getenv("TTS_PITCH", "+0Hz")
        volume = os.getenv("TTS_VOLUME", "+0%")
        output_format = os.getenv("TTS_OUTPUT_FORMAT", "riff-24khz-16bit-mono-pcm").strip()

        payload = (text or "").strip()
        if use_ssml:
            # Keep punctuation shaping, but do not send SSML/XML to edge-tts.
            payload = re.sub(r"\s+", " ", payload)

        return await self._stream_tts(
            payload,
            voice_name,
            session_id,
            rate=rate,
            pitch=pitch,
            volume=volume,
            output_format=output_format,
        )

    async def _stream_tts(
        self,
        payload: str,
        voice_name: str,
        session_id: int,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
        output_format: str = "riff-24khz-16bit-mono-pcm",
    ) -> tuple[bytes, list[tuple[int, int, float]], list[tuple[int, int]]]:
        communicate_kwargs = {
            "text": payload,
            "voice": voice_name,
            "rate": rate,
            "pitch": pitch,
            "volume": volume,
        }
        if output_format:
            communicate_kwargs["output_format"] = output_format
        try:
            communicate = edge_tts.Communicate(**communicate_kwargs)
        except TypeError:
            # Older edge-tts versions may not support output_format in constructor.
            communicate_kwargs.pop("output_format", None)
            logging.warning("edge-tts does not support output_format; fallback to default codec.")
            communicate = edge_tts.Communicate(**communicate_kwargs)
        buffer = bytearray()
        timeline: list[tuple[int, int, float]] = []
        viseme_timeline: list[tuple[int, int]] = []
        async for chunk in communicate.stream():
            if session_id != self._session_id:
                return b"", [], []
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                buffer.extend(chunk.get("data", b""))
            elif chunk_type == "WordBoundary":
                start_ms = int(int(chunk.get("offset", 0)) / 10000)
                dur_ms = max(70, int(int(chunk.get("duration", 0)) / 10000))
                token = str(chunk.get("text", "") or "")
                strength = self._boundary_strength(token)
                timeline.append((start_ms, dur_ms, strength))
            elif chunk_type in {"VisemeReceived", "Viseme", "viseme"}:
                start_ms = int(int(chunk.get("offset", chunk.get("audio_offset", 0))) / 10000)
                vid = chunk.get("viseme_id", chunk.get("visemeId", chunk.get("id", -1)))
                try:
                    viseme_id = int(vid)
                except Exception:
                    viseme_id = -1
                if viseme_id >= 0:
                    viseme_timeline.append((max(0, start_ms), viseme_id))
        if (not viseme_timeline) and self._rhubarb_enabled():
            try:
                rhubarb_timeline, wav_override = await self._run_rhubarb_on_audio(bytes(buffer))
                if rhubarb_timeline:
                    viseme_timeline = rhubarb_timeline
                    if wav_override:
                        buffer = bytearray(wav_override)
                        logging.info("Rhubarb playback audio override: wav bytes=%d", len(buffer))
                    logging.info("Rhubarb viseme events: %d", len(viseme_timeline))
            except Exception as exc:
                logging.warning("Rhubarb lip sync failed: %s", exc)

        logging.info("TTS viseme events: %d", len(viseme_timeline))
        return bytes(buffer), timeline, viseme_timeline

    def _rhubarb_enabled(self) -> bool:
        flag = os.getenv("TTS_USE_RHUBARB", "true").strip().lower()
        return flag in {"1", "true", "yes", "on"}

    def _resolve_rhubarb_exe(self) -> str:
        env = os.getenv("RHUBARB_EXE", "").strip()
        if env and Path(env).exists():
            return env
        default_path = Path(r"H:\Rhubarb-Lip-Sync-1.14.0-Windows\Rhubarb-Lip-Sync-1.14.0-Windows\rhubarb.exe")
        if default_path.exists():
            return str(default_path)
        return ""

    async def _run_rhubarb_on_audio(self, audio_bytes: bytes) -> tuple[list[tuple[int, int]], bytes | None]:
        if not audio_bytes:
            return [], None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._run_rhubarb_on_audio_sync(audio_bytes))

    def _run_rhubarb_on_audio_sync(self, audio_bytes: bytes) -> tuple[list[tuple[int, int]], bytes | None]:
        exe = self._resolve_rhubarb_exe()
        if not exe:
            return [], None

        value_to_viseme = {
            "X": 0,
            "A": 1,
            "B": 4,
            "C": 7,
            "D": 14,
            "E": 10,
            "F": 18,
            "G": 3,
        }

        with tempfile.TemporaryDirectory(prefix="pet_rhubarb_") as td:
            tdp = Path(td)
            out_json = tdp / "mouth.json"
            in_audio = self._prepare_rhubarb_input_audio(tdp, audio_bytes)
            if in_audio is None:
                return [], None

            recognizer = os.getenv("RHUBARB_RECOGNIZER", "phonetic").strip().lower()
            if recognizer not in {"phonetic", "pocketsphinx"}:
                recognizer = "phonetic"
            cmd = [exe, "-r", recognizer, "-f", "json", "-o", str(out_json), str(in_audio)]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"rhubarb exited with {proc.returncode}")
            if not out_json.exists():
                return [], None

            data = json.loads(out_json.read_text(encoding="utf-8", errors="ignore"))
            cues = data.get("mouthCues", []) if isinstance(data, dict) else []
            timeline: list[tuple[int, int]] = []
            for cue in cues:
                if not isinstance(cue, dict):
                    continue
                start = float(cue.get("start", 0.0) or 0.0)
                val = str(cue.get("value", "X") or "X").upper()
                vid = value_to_viseme.get(val, 0)
                timeline.append((max(0, int(start * 1000)), vid))

            timeline.sort(key=lambda x: x[0])
            collapsed: list[tuple[int, int]] = []
            last_vid = None
            for at_ms, vid in timeline:
                if vid == last_vid:
                    continue
                collapsed.append((at_ms, vid))
                last_vid = vid
            cleaned = self._cleanup_zero_visemes(collapsed)
            self._log_viseme_stats(cleaned)
            wav_override = None
            try:
                wav_override = in_audio.read_bytes()
            except Exception:
                wav_override = None
            return cleaned, wav_override

    def _prepare_rhubarb_input_audio(self, tdp: Path, audio_bytes: bytes) -> Path | None:
        if not audio_bytes:
            return None

        # If already a RIFF/WAVE stream, write directly.
        if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            wav_path = tdp / "tts_audio.wav"
            wav_path.write_bytes(audio_bytes)
            return wav_path

        # Otherwise assume compressed stream (typically mp3) and convert via ffmpeg.
        src_path = tdp / "tts_audio.src"
        src_path.write_bytes(audio_bytes)
        wav_path = tdp / "tts_audio.wav"

        project_ffmpeg = Path(__file__).resolve().parents[2] / "ffmpeg.exe"
        ffmpeg_exe = str(project_ffmpeg) if project_ffmpeg.exists() else shutil.which("ffmpeg")
        if not ffmpeg_exe:
            logging.warning(
                "Rhubarb requires WAV input, but ffmpeg was not found. Put ffmpeg.exe in project root or set PATH/FFMPEG_EXE."
            )
            return None
        logging.info("Using ffmpeg: %s", ffmpeg_exe)

        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_path),
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if proc.returncode != 0 or not wav_path.exists():
            logging.warning("ffmpeg convert to wav failed: %s", proc.stderr.strip())
            return None
        return wav_path

    def _cleanup_zero_visemes(self, timeline: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if len(timeline) < 3:
            return timeline
        out: list[tuple[int, int]] = []
        n = len(timeline)
        for i, (t_ms, vid) in enumerate(timeline):
            if vid != 0:
                out.append((t_ms, vid))
                continue
            if i == 0 or i == n - 1:
                out.append((t_ms, vid))
                continue
            prev_t, prev_vid = timeline[i - 1]
            next_t, next_vid = timeline[i + 1]
            gap_ms = max(0, next_t - t_ms)
            bridge_ms = max(0, next_t - prev_t)
            # Remove short X segment between two non-zero visemes.
            if prev_vid != 0 and next_vid != 0 and gap_ms <= 260 and bridge_ms <= 520:
                continue
            out.append((t_ms, vid))
        return out

    def _log_viseme_stats(self, timeline: list[tuple[int, int]]):
        if not self._debug_lipsync:
            return
        if not timeline:
            logging.info("Viseme stats: empty timeline")
            return
        zero_runs = 0
        longest_zero = 0
        for i, (t_ms, vid) in enumerate(timeline):
            if vid != 0:
                continue
            zero_runs += 1
            if i + 1 < len(timeline):
                longest_zero = max(longest_zero, max(0, timeline[i + 1][0] - t_ms))
        logging.info(
            "Viseme stats: cues=%d zero_runs=%d longest_zero_ms=%d",
            len(timeline),
            zero_runs,
            longest_zero,
        )

    def _boundary_strength(self, token: str) -> float:
        t = (token or "").strip()
        if not t:
            return 0.25
        if any(ch in t for ch in "???!??,??;:?"):
            return 0.32
        if len(t) <= 1:
            return 0.62
        if len(t) <= 3:
            return 0.78
        return 0.88

    def _viseme_for_pos(self, pos_ms: int, timeline: list[tuple[int, int]]) -> int:
        if not timeline:
            return -1
        current = -1
        for at_ms, viseme_id in timeline:
            if at_ms <= pos_ms:
                current = viseme_id
            else:
                break
        return current

    def _viseme_to_weights(self, viseme_id: int) -> dict:
        # Azure-style viseme id -> A/I/U/E/O weights.
        # Values tuned for fast expressive open/close and can be refined per model.
        mapping = {
            0: {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0},
            1: {"A": 0.78, "I": 0.08, "U": 0.05, "E": 0.12, "O": 0.12},
            2: {"A": 0.76, "I": 0.07, "U": 0.06, "E": 0.16, "O": 0.18},
            3: {"A": 0.25, "I": 0.58, "U": 0.10, "E": 0.70, "O": 0.10},
            4: {"A": 0.30, "I": 0.72, "U": 0.08, "E": 0.66, "O": 0.08},
            5: {"A": 0.24, "I": 0.60, "U": 0.12, "E": 0.76, "O": 0.10},
            6: {"A": 0.20, "I": 0.48, "U": 0.16, "E": 0.70, "O": 0.10},
            7: {"A": 0.16, "I": 0.26, "U": 0.74, "E": 0.16, "O": 0.70},
            8: {"A": 0.16, "I": 0.20, "U": 0.78, "E": 0.12, "O": 0.72},
            9: {"A": 0.22, "I": 0.24, "U": 0.66, "E": 0.18, "O": 0.78},
            10: {"A": 0.24, "I": 0.22, "U": 0.60, "E": 0.20, "O": 0.82},
            11: {"A": 0.80, "I": 0.06, "U": 0.08, "E": 0.10, "O": 0.22},
            12: {"A": 0.74, "I": 0.10, "U": 0.08, "E": 0.14, "O": 0.20},
            13: {"A": 0.28, "I": 0.56, "U": 0.12, "E": 0.66, "O": 0.18},
            14: {"A": 0.20, "I": 0.62, "U": 0.10, "E": 0.72, "O": 0.12},
            15: {"A": 0.76, "I": 0.10, "U": 0.10, "E": 0.14, "O": 0.30},
            16: {"A": 0.46, "I": 0.16, "U": 0.22, "E": 0.24, "O": 0.66},
            17: {"A": 0.56, "I": 0.12, "U": 0.16, "E": 0.20, "O": 0.44},
            18: {"A": 0.22, "I": 0.14, "U": 0.72, "E": 0.12, "O": 0.82},
            19: {"A": 0.14, "I": 0.34, "U": 0.66, "E": 0.30, "O": 0.44},
            20: {"A": 0.12, "I": 0.26, "U": 0.68, "E": 0.22, "O": 0.56},
            21: {"A": 0.08, "I": 0.22, "U": 0.60, "E": 0.20, "O": 0.52},
        }
        w = mapping.get(viseme_id)
        if w is None:
            # Fallback for unknown viseme ids: keep mouth moving instead of fully closed.
            fallback_cycle = (
                {"A": 0.72, "I": 0.16, "U": 0.08, "E": 0.12, "O": 0.18},  # A
                {"A": 0.20, "I": 0.72, "U": 0.10, "E": 0.66, "O": 0.10},  # I/E
                {"A": 0.14, "I": 0.24, "U": 0.74, "E": 0.16, "O": 0.68},  # U/O
                {"A": 0.22, "I": 0.60, "U": 0.12, "E": 0.72, "O": 0.16},  # E
                {"A": 0.22, "I": 0.18, "U": 0.62, "E": 0.16, "O": 0.82},  # O
            )
            w = fallback_cycle[abs(int(viseme_id)) % len(fallback_cycle)]
            if self._debug_lipsync:
                logging.info("LipSync unknown viseme id=%s, fallback weights applied", viseme_id)
        return {
            "A": float(w["A"]),
            "I": float(w["I"]),
            "U": float(w["U"]),
            "E": float(w["E"]),
            "O": float(w["O"]),
            "id": int(viseme_id),
        }

    def _viseme_weights_for_pos(self, pos_ms: int, timeline: list[tuple[int, int]]) -> dict:
        if not timeline:
            return {}

        # Find current viseme cue index.
        idx = 0
        for i, (at_ms, _vid) in enumerate(timeline):
            if at_ms <= pos_ms:
                idx = i
            else:
                break

        cur_time, cur_vid = timeline[idx]
        cur_w = self._viseme_to_weights(cur_vid)

        # Blend continuously through the whole segment to avoid hold-and-jump.
        if idx + 1 < len(timeline):
            next_time, next_vid = timeline[idx + 1]
            next_w = self._viseme_to_weights(next_vid)
            seg = max(1.0, float(next_time - cur_time))
            t = max(0.0, min(1.0, (pos_ms - cur_time) / seg))
            t = t * t * (3.0 - 2.0 * t)
            w = {
                "A": cur_w["A"] * (1.0 - t) + next_w["A"] * t,
                "I": cur_w["I"] * (1.0 - t) + next_w["I"] * t,
                "U": cur_w["U"] * (1.0 - t) + next_w["U"] * t,
                "E": cur_w["E"] * (1.0 - t) + next_w["E"] * t,
                "O": cur_w["O"] * (1.0 - t) + next_w["O"] * t,
                "id": int(cur_w.get("id", cur_vid)),
            }
            return self._coarticulate_silence(w, idx, pos_ms, timeline)

        return self._coarticulate_silence(cur_w, idx, pos_ms, timeline)

    def _coarticulate_silence(self, w: dict, idx: int, pos_ms: int, timeline: list[tuple[int, int]]) -> dict:
        # Rhubarb often inserts short X(=0) cues between phonemes.
        # Keep mouth continuity across short gaps to avoid speak-pause-speak visuals.
        if int(w.get("id", -1)) != 0:
            return w

        prev_nonzero = None
        for i in range(idx - 1, -1, -1):
            t_ms, vid = timeline[i]
            if vid != 0:
                prev_nonzero = (t_ms, vid)
                break

        next_nonzero = None
        for i in range(idx + 1, len(timeline)):
            t_ms, vid = timeline[i]
            if vid != 0:
                next_nonzero = (t_ms, vid)
                break

        influence = None
        dist = 10_000
        if prev_nonzero is not None:
            d = abs(pos_ms - prev_nonzero[0])
            if d < dist:
                dist = d
                influence = self._viseme_to_weights(prev_nonzero[1])
        if next_nonzero is not None:
            d = abs(next_nonzero[0] - pos_ms)
            if d < dist:
                dist = d
                influence = self._viseme_to_weights(next_nonzero[1])

        # Only blend for short silent bridges.
        if influence is None or dist > 160:
            return w

        t = max(0.0, min(1.0, 1.0 - (dist / 160.0)))
        t = t * t * (3.0 - 2.0 * t)
        return {
            "A": w["A"] * (1.0 - t) + influence["A"] * t,
            "I": w["I"] * (1.0 - t) + influence["I"] * t,
            "U": w["U"] * (1.0 - t) + influence["U"] * t,
            "E": w["E"] * (1.0 - t) + influence["E"] * t,
            "O": w["O"] * (1.0 - t) + influence["O"] * t,
            "id": int(w.get("id", 0)),
        }

    def _same_weights(self, a: dict | None, b: dict | None) -> bool:
        if not a and not b:
            return True
        if not a or not b:
            return False
        keys = ("A", "I", "U", "E", "O")
        return all(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) < 0.02 for k in keys)

    def _apply_zero_hold(self, weights: dict, sync_pos_ms: int) -> dict:
        if not weights:
            return {}
        vid = int(weights.get("id", -1))
        if vid > 0:
            self._last_nonzero_weights = weights
            self._last_nonzero_sync_ms = sync_pos_ms
            return weights
        if vid == 0 and self._last_nonzero_weights and self._last_nonzero_sync_ms >= 0:
            dt = sync_pos_ms - self._last_nonzero_sync_ms
            if 0 <= dt <= self._zero_hold_ms:
                # Keep prior mouth for short zero-viseme bridge, then decay to closed.
                t = dt / max(1.0, float(self._zero_hold_ms))
                k = max(0.0, 1.0 - t * t)
                prev = self._last_nonzero_weights
                return {
                    "A": float(prev.get("A", 0.0)) * k,
                    "I": float(prev.get("I", 0.0)) * k,
                    "U": float(prev.get("U", 0.0)) * k,
                    "E": float(prev.get("E", 0.0)) * k,
                    "O": float(prev.get("O", 0.0)) * k,
                    "id": 0,
                }
        return weights

    def _dominant_mouth(self, weights: dict | None) -> str:
        if not weights:
            return "none"
        keys = ("A", "I", "U", "E", "O")
        vals = {k: float(weights.get(k, 0.0)) for k in keys}
        best = max(vals, key=vals.get)
        return best if vals[best] > 0.01 else "none"

    def _format_weights(self, weights: dict | None) -> str:
        if not weights:
            return "{}"
        return (
            "{A=%.2f I=%.2f U=%.2f E=%.2f O=%.2f}"
            % (
                float(weights.get("A", 0.0)),
                float(weights.get("I", 0.0)),
                float(weights.get("U", 0.0)),
                float(weights.get("E", 0.0)),
                float(weights.get("O", 0.0)),
            )
        )

    def _viseme_open_level(self, weights: dict, pos_ms: int) -> float:
        if not weights:
            return 0.0
        a = float(weights.get("A", 0.0))
        i = float(weights.get("I", 0.0))
        u = float(weights.get("U", 0.0))
        e = float(weights.get("E", 0.0))
        o = float(weights.get("O", 0.0))
        open_base = max(a, o, e * 0.92, i * 0.80, u * 0.82)
        # Tiny micro-variation keeps lips alive without obvious pulsation.
        fine = 0.02 * abs(math.sin(pos_ms * 0.045))
        level = open_base * 0.95 + fine
        # Keep a short non-pulsed carry so brief viseme=0 gaps don't look like hard stops.
        if self._last_nonzero_sync_ms >= 0 and pos_ms >= self._last_nonzero_sync_ms:
            dt = pos_ms - self._last_nonzero_sync_ms
            if dt <= self._open_carry_ms:
                k = 1.0 - (dt / max(1.0, float(self._open_carry_ms)))
                carry = max(0.0, self._open_carry_floor * k)
                level = max(level, carry)
        return max(0.08, min(0.98, level))
    def _infer_emotion(self, text: str) -> str:
        t = (text or "").lower()
        if any(k in t for k in ["!", "?", "great", "happy", "??", "??"]):
            return "happy"
        if any(k in t for k in ["sorry", "sad", "??", "??", "??", "??"]):
            return "sad"
        if any(k in t for k in ["angry", "mad", "??", "?", "??", "??"]):
            return "angry"
        if any(k in t for k in ["?", "?", "wow", "surprised", "??", "??"]):
            return "surprised"
        return "neutral"

    def _build_emotion_events(
        self,
        text: str,
        timeline: list[tuple[int, int, float]],
        emotion_plan: list[dict],
        base_emotion: str,
    ) -> list[tuple[int, str]]:
        allowed = {"neutral", "happy", "sad", "angry", "surprised", "shy"}
        if timeline:
            total_ms = max(start + dur for start, dur, _ in timeline)
        else:
            total_ms = max(900, len(text) * 220)

        plan: list[tuple[int, str]] = []
        if isinstance(emotion_plan, list):
            for item in emotion_plan:
                if not isinstance(item, dict):
                    continue
                seg_text = str(item.get("text", "")).strip()
                emo = str(item.get("emotion", "")).strip().lower()
                if not seg_text:
                    continue
                if emo not in allowed:
                    emo = "neutral"
                seg_len = max(1, len(re.sub(r"\s+", "", seg_text)))
                plan.append((seg_len, emo))

        if not plan:
            one = base_emotion if base_emotion in allowed else "neutral"
            return [(0, one)]

        sum_len = max(1, sum(seg_len for seg_len, _ in plan))
        acc_len = 0
        events: list[tuple[int, str]] = []
        for seg_len, emo in plan:
            at_ms = int(total_ms * (acc_len / sum_len))
            events.append((max(0, at_ms), emo))
            acc_len += seg_len

        collapsed: list[tuple[int, str]] = []
        last_emo = None
        for at_ms, emo in events:
            if emo == last_emo:
                continue
            collapsed.append((at_ms, emo))
            last_emo = emo

        if not collapsed:
            return [(0, "neutral")]
        if collapsed[0][0] > 0:
            collapsed.insert(0, (0, collapsed[0][1]))
        return collapsed

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
                logging.warning("pygame import failed: %s", exc)
                return False
        if self._pygame_ready:
            return True
        try:
            pygame.mixer.init()
            self._pygame_ready = True
            return True
        except Exception as exc:
            logging.warning("pygame init failed: %s", exc)
            return False

    @pyqtSlot(str)
    def add_text(self, text: str):
        if self._enabled and text.strip():
            self.add_payload({"text": text})

    @pyqtSlot(dict)
    def add_payload(self, payload: dict):
        if not self._enabled:
            return
        text = str((payload or {}).get("text", "")).strip()
        if not text:
            return
        normalized = {
            "text": text,
            "base_emotion": str((payload or {}).get("base_emotion", "neutral")).strip().lower(),
            "emotion_timeline": (payload or {}).get("emotion_timeline", []) or [],
        }
        self.text_queue.put((self._session_id, normalized))

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
        self._current_viseme_timeline = []
        self._active_emotion_events = []
        self._next_emotion_event_idx = 0
        self._last_viseme_id = -1
        self._last_viseme_weights = None
        self._last_nonzero_weights = None
        self._last_nonzero_sync_ms = -1
        self._recent_open_ema = 0.0
        self._speech_nonzero_start_ms = -1
        self._speech_nonzero_end_ms = -1
        self._not_busy_since_ms = -1
        self.viseme_weights_changed.emit({})
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
            self._is_playing = False
        if not enabled:
            self._current_viseme_timeline = []
            self._active_emotion_events = []
            self._next_emotion_event_idx = 0
            self._last_viseme_id = -1
            self._last_viseme_weights = None
            self._last_nonzero_weights = None
            self._last_nonzero_sync_ms = -1
            self._recent_open_ema = 0.0
            self._speech_nonzero_start_ms = -1
            self._speech_nonzero_end_ms = -1
            self._not_busy_since_ms = -1
            self.viseme_weights_changed.emit({})

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
        self.viseme_weights_changed.emit({})
        self.text_queue.put(None)
        self.audio_queue.put(None)
        self.wait(3000)




