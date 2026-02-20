import logging
import os
import sys
from pathlib import Path
import time

from PyQt5.QtCore import QPoint, QTimer, Qt
from PyQt5.QtGui import QCursor, QGuiApplication
from PyQt5.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout,
)

from app.core.local_model_server import LocalModelServer
from app.core.proactive_chat import ProactiveChatScheduler
from app.ui.action_menu import ModelActionPanel
from app.ui.chat_window import ChatWindow
from app.ui.live2d_webview import Live2DWebView
from app.ui.speech_bubble import AnimeSpeechBubble
from app.workers.llm_worker import LLMWorker
from app.workers.voice_worker import VoiceWorker


logger = logging.getLogger("live2d.window")

_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

    _WM_NCHITTEST = 0x0084
    _HTTRANSPARENT = -1
    _HTCLIENT = 1


class DesktopPetWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live2D Desktop Pet")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        screen = QGuiApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            short_edge = min(sg.width(), sg.height())
            _ = min(int(short_edge * 0.6), 1200)
        else:
            _ = 800
        self.resize(648, 720)
        logger.info("Window size: %dx%d", self.width(), self.height())

        self._model_server: LocalModelServer | None = None
        self.chat_window: ChatWindow | None = None
        self._last_pan_log_ts = 0.0
        self._drag_accum_x = 0.0
        self._drag_accum_y = 0.0
        self._drag_cursor_last: QPoint | None = None
        self._llm_busy = False
        self._voice_busy = False
        self._bubble_hide_timer = QTimer(self)
        self._bubble_hide_timer.setSingleShot(True)
        self._bubble_hide_timer.timeout.connect(self._hide_speech_bubble)

        self._cached_model_bounds: dict = {}
        self._is_model_dragging: bool = False

        self._action_panel = ModelActionPanel()
        self._action_panel.open_chat_clicked.connect(self._open_chat)
        self._action_panel.close_app_clicked.connect(self._quit_app)
        self._action_panel.eye_follow_toggled.connect(self._on_eye_follow_toggled)

        model_url = self._prepare_model_url()
        # Enable motion in main app; mouth priority is still enforced in web runtime.
        self.live2d_view = Live2DWebView(self, model_url=model_url)
        self.live2d_view.set_eye_follow_enabled(True)
        self.live2d_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.live2d_view.customContextMenuRequested.connect(self._show_action_panel)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.live2d_view)
        self.setStyleSheet("background: transparent;")
        self._speech_bubble = AnimeSpeechBubble(self)

        self.llm_worker = LLMWorker(self)
        self.voice_worker = VoiceWorker(self)
        self.proactive_scheduler = ProactiveChatScheduler(self)
        self._chat_follow_timer = QTimer(self)
        self._chat_follow_timer.setInterval(220)
        self._chat_follow_timer.timeout.connect(self._position_chat_window)
        self._window_follow_timer = QTimer(self)
        self._window_follow_timer.setInterval(16)
        self._window_follow_timer.timeout.connect(self._follow_window_with_model)

        self._wire_signals()
        self.llm_worker.start()
        self.voice_worker.start()
        self.proactive_scheduler.start()
        self._window_follow_timer.start()

    def nativeEvent(self, eventType, message):
        if _IS_WINDOWS and eventType == b"windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == _WM_NCHITTEST:
                    if self._is_model_dragging:
                        return True, _HTCLIENT
                    lp = msg.lParam
                    screen_x = ctypes.c_short(lp & 0xFFFF).value
                    screen_y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                    local_pos = self.mapFromGlobal(QPoint(screen_x, screen_y))
                    if self._is_point_on_model(local_pos):
                        return True, _HTCLIENT
                    return True, _HTTRANSPARENT
            except Exception:
                logger.debug("nativeEvent WM_NCHITTEST error", exc_info=True)
        return super().nativeEvent(eventType, message)

    def _is_point_on_model(self, pos: QPoint) -> bool:
        """Return whether a local point is inside the model interaction area."""
        bounds = self._cached_model_bounds
        if not bounds:
            return True
        left = float(bounds.get("left", 0))
        right = float(bounds.get("right", self.width()))
        top = float(bounds.get("top", 0))
        bottom = float(bounds.get("bottom", self.height()))

        model_w = right - left
        model_h = bottom - top
        if model_w <= 0 or model_h <= 0:
            return True
        padding = max(25, min(model_w, model_h) * 0.12)
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        rx = model_w / 2.0 + padding
        ry = model_h / 2.0 + padding

        dx = (pos.x() - cx) / rx
        dy = (pos.y() - cy) / ry
        return (dx * dx + dy * dy) <= 1.0

    def _wire_signals(self):
        # LLM
        self.llm_worker.chunk_ready.connect(self._append_assistant_text)
        self.llm_worker.status_changed.connect(self._append_status)
        self.llm_worker.error_occurred.connect(self._append_error)
        self.llm_worker.response_complete.connect(self._on_llm_response_complete)
        self.llm_worker.voice_payload_ready.connect(self.voice_worker.add_payload)
        self.llm_worker.new_session.connect(self.voice_worker.start_new_session)
        self.llm_worker.pet_command_ready.connect(self._apply_pet_command)

        # Voice
        self.voice_worker.error_occurred.connect(self._append_error)
        self.voice_worker.voice_started.connect(self._on_voice_started)
        self.voice_worker.voice_finished.connect(self._on_voice_finished)
        self.voice_worker.viseme_weights_changed.connect(self._on_viseme_weights)
        self.voice_worker.emphasis_triggered.connect(self._on_emphasis)
        self.voice_worker.emotion_detected.connect(self._on_emotion)

        # Proactive chat
        self.proactive_scheduler.prompt_ready.connect(self._on_proactive_prompt)

    def _on_voice_started(self):
        self._voice_busy = True
        self.proactive_scheduler.set_busy(True)
        self._bubble_hide_timer.stop()
        self.live2d_view.set_speaking(True)
        # Use immediate viseme-driven mouth control in main app, same as test tool.
        self.live2d_view.set_mouth_immediate(
            0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        )

    def _on_voice_finished(self):
        self._voice_busy = False
        self.proactive_scheduler.set_busy(self._llm_busy or self._voice_busy)
        self.proactive_scheduler.notify_activity()
        self.live2d_view.set_speaking(False)
        self.live2d_view.set_mouth_immediate(
            0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        )
        if self._speech_bubble.isVisible():
            self._bubble_hide_timer.start(3000)

    def _on_llm_response_complete(self):
        self._llm_busy = False
        self.proactive_scheduler.set_busy(self._llm_busy or self._voice_busy)
        self.proactive_scheduler.notify_activity()
        if not self._voice_busy and self._speech_bubble.isVisible():
            self._bubble_hide_timer.start(3000)

    def _on_proactive_prompt(self, prompt: str):
        if self._llm_busy or self._voice_busy:
            return
        self._llm_busy = True
        self.proactive_scheduler.set_busy(True)
        self.llm_worker.send_message(prompt)

    def _on_viseme_weights(self, weights: dict):
        if not weights:
            self.live2d_view.set_mouth_immediate(
                0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
            )
            return
        # Keep level fixed at 0.8 and let viseme weights define current mouth shape.
        self.live2d_view.set_mouth_immediate(0.8, weights)

    def _on_emphasis(self, strength: float):
        self.live2d_view.trigger_emphasis(strength)

    def _on_emotion(self, emotion: str):
        self.live2d_view.set_emotion(emotion)

    def _prepare_model_url(self) -> str:
        env_url = (os.getenv("LIVE2D_MODEL_URL") or "").strip()
        if env_url:
            return env_url

        model_file = self._find_model_file()
        if model_file is None:
            return ""

        root_dir = self._determine_root_dir(model_file)
        self._model_server = LocalModelServer(root_dir=root_dir, port=18080)
        self._model_server.start()
        return self._model_server.build_url(model_file)

    def _find_model_file(self) -> Path | None:
        env_path = (os.getenv("LIVE2D_MODEL_PATH") or "").strip()
        if env_path:
            p = Path(env_path)
            if p.exists() and p.is_file() and p.name.endswith((".model3.json", ".model.json")):
                return p.resolve()

        default_root = (Path(__file__).resolve().parents[2] / "models").resolve()
        if not default_root.exists():
            return None

        files = sorted(default_root.rglob("*.model3.json"))
        if files:
            return files[0].resolve()
        files = sorted(default_root.rglob("*.model.json"))
        if files:
            return files[0].resolve()
        return None

    def _determine_root_dir(self, model_file: Path) -> Path:
        default_root = (Path(__file__).resolve().parents[2] / "models").resolve()
        if default_root.exists():
            try:
                model_file.resolve().relative_to(default_root.resolve())
                return default_root.resolve()
            except Exception:
                pass
        return model_file.parent.resolve()

    def _ensure_chat_window(self) -> ChatWindow:
        if self.chat_window is None:
            self.chat_window = ChatWindow()
            self.chat_window.message_submitted.connect(self._send_message)
        return self.chat_window

    def _open_chat(self):
        self._action_panel.hide()
        chat = self._ensure_chat_window()
        chat.show()
        chat.raise_()
        chat.activateWindow()
        self._chat_follow_timer.start()
        self._position_chat_window()

    def _position_chat_window(self):
        if not self.chat_window or not self.chat_window.isVisible():
            self._chat_follow_timer.stop()
            return
        chat = self.chat_window
        avail = self._active_screen_geometry()
        geo = self.frameGeometry()
        x = geo.right() + 16
        y = geo.top() + 28
        if avail is None:
            chat.move(x, y)
            return

        chat_w = chat.width()
        chat_h = chat.height()
        if x + chat_w > avail.right():
            x = geo.left() - chat_w - 16
        x = max(avail.left(), min(x, avail.right() - chat_w))
        y = max(avail.top(), min(y, avail.bottom() - chat_h))
        chat.move(x, y)


    def _show_action_panel(self, pos: QPoint):
        global_pos = self.live2d_view.mapToGlobal(pos)
        panel_size = self._action_panel.sizeHint()
        x = global_pos.x() - panel_size.width() // 2
        y = global_pos.y() - panel_size.height() - 14
        self._action_panel.move(x, y)
        self._action_panel.show()
        self._action_panel.raise_()
        self._action_panel.activateWindow()

    def _on_eye_follow_toggled(self, enabled: bool):
        self.live2d_view.set_eye_follow_enabled(enabled)

    def _follow_window_with_model(self):
        def _on_bounds(bounds):
            if not isinstance(bounds, dict) or not bounds:
                return

            drag_active = bool(bounds.get("dragActive", False))
            self._is_model_dragging = drag_active
            self._update_speech_bubble_position(bounds)

            if not drag_active:
                self._drag_accum_x = 0.0
                self._drag_accum_y = 0.0
                self._drag_cursor_last = None
                return
            cursor = QCursor.pos()
            if self._drag_cursor_last is None:
                self._drag_cursor_last = cursor
                return
            dx = cursor.x() - self._drag_cursor_last.x()
            dy = cursor.y() - self._drag_cursor_last.y()
            self._drag_cursor_last = cursor
            self._pan_window_with_delta(bounds, float(dx), float(dy))

        self.live2d_view.get_model_bounds(_on_bounds)

    def _pan_window_with_delta(self, bounds: dict, raw_dx: float, raw_dy: float):
        if abs(raw_dx) < 0.5 and abs(raw_dy) < 0.5:
            return

        raw_dx, raw_dy = self._consume_bounds_overflow(bounds, raw_dx, raw_dy)

        self._drag_accum_x += raw_dx
        self._drag_accum_y += raw_dy
        apply_dx = int(self._drag_accum_x)
        apply_dy = int(self._drag_accum_y)
        if apply_dx == 0 and apply_dy == 0:
            return
        self._drag_accum_x -= apply_dx
        self._drag_accum_y -= apply_dy

        cur = self.frameGeometry()
        wanted_x = cur.x() + apply_dx
        wanted_y = cur.y() + apply_dy

        avail = self._active_screen_geometry()
        clamped_x = False
        clamped_y = False
        if avail is not None:
            min_x = avail.x()
            max_x = avail.x() + avail.width() - cur.width()
            min_y = avail.y()
            max_y = avail.y() + avail.height() - cur.height()
            if wanted_x < min_x:
                wanted_x = min_x
                clamped_x = True
            elif wanted_x > max_x:
                wanted_x = max_x
                clamped_x = True
            if wanted_y < min_y:
                wanted_y = min_y
                clamped_y = True
            elif wanted_y > max_y:
                wanted_y = max_y
                clamped_y = True

        applied_dx = wanted_x - cur.x()
        applied_dy = wanted_y - cur.y()
        self.move(wanted_x, wanted_y)

        residual_dx = apply_dx - applied_dx if clamped_x else 0.0
        residual_dy = apply_dy - applied_dy if clamped_y else 0.0
        if abs(residual_dx) > 0.01 or abs(residual_dy) > 0.01:
            self.live2d_view.nudge_model_offset(residual_dx, residual_dy)

        now = time.monotonic()
        if now - self._last_pan_log_ts > 0.5:
            self._last_pan_log_ts = now
            logger.info(
                "Drag pan raw=(%.3f, %.3f) applied=(%d, %d) clamp=(%s,%s) window=(%d, %d, %d, %d)",
                raw_dx, raw_dy, applied_dx, applied_dy,
                clamped_x, clamped_y, wanted_x, wanted_y, cur.width(), cur.height(),
            )

        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()

    def _consume_bounds_overflow(self, bounds: dict, dx: float, dy: float):
        vw = float(bounds.get("viewWidth", self.live2d_view.width()))
        vh = float(bounds.get("viewHeight", self.live2d_view.height()))
        left = float(bounds.get("left", 0))
        right = float(bounds.get("right", 0))
        top = float(bounds.get("top", 0))
        bottom = float(bounds.get("bottom", 0))

        overflow_x = 0.0
        overflow_y = 0.0
        if right > vw:
            overflow_x = right - vw
        elif left < 0:
            overflow_x = left
        if bottom > vh:
            overflow_y = bottom - vh
        elif top < 0:
            overflow_y = top

        if overflow_x > 0 and dx < 0:
            step = min(-dx, overflow_x)
            self.live2d_view.nudge_model_offset(-step, 0)
            dx += step
        elif overflow_x < 0 and dx > 0:
            step = min(dx, -overflow_x)
            self.live2d_view.nudge_model_offset(step, 0)
            dx -= step

        if overflow_y > 0 and dy < 0:
            step = min(-dy, overflow_y)
            self.live2d_view.nudge_model_offset(0, -step)
            dy += step
        elif overflow_y < 0 and dy > 0:
            step = min(dy, -overflow_y)
            self.live2d_view.nudge_model_offset(0, step)
            dy -= step

        return dx, dy

    def _active_screen_geometry(self):
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return None
        geo = screen.geometry()
        logger.debug(
            "Active screen geometry: x=%s y=%s w=%s h=%s",
            geo.x(), geo.y(), geo.width(), geo.height(),
        )
        return geo

    def _send_message(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        self._llm_busy = True
        self.proactive_scheduler.set_busy(True)
        self.proactive_scheduler.notify_activity()
        chat = self._ensure_chat_window()
        chat.append_user(text)
        self.llm_worker.send_message(text)

    def _apply_pet_command(self, payload: dict):
        expression = str(payload.get("expression", "neutral")).strip().lower()
        motion = str(payload.get("motion", "idle")).strip().lower()
        reply = str(payload.get("reply", "") or "")
        if expression == "neutral":
            inferred = self._infer_expression_from_text(reply)
            if inferred:
                expression = inferred
        # Do not set expression here, otherwise face changes before voice playback starts.
        # Expression is driven by voice timeline events during speaking.
        self.live2d_view.play_motion(motion)

    def _append_assistant_text(self, text: str):
        if text:
            self.proactive_scheduler.notify_activity()
            self._show_speech_bubble(text)
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_assistant(text)

    def _append_status(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_status(text)

    def _append_error(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_error(text)

    def _infer_expression_from_text(self, text: str) -> str:
        if not text:
            return ""
        t = text.lower()
        happy = ["开心", "高兴", "太好", "喜欢", "可爱", "哈哈", "谢谢", "真棒"]
        sad = ["难过", "抱歉", "对不起", "遗憾", "伤心", "失落", "可惜"]
        angry = ["生气", "讨厌", "别", "闭嘴", "愤怒", "气死", "不爽"]
        surprised = ["啊", "诶", "哇", "真的吗", "竟然", "居然", "原来", "天哪"]
        shy = ["害羞", "脸红", "不好意思", "小声", "嗯嗯"]

        if any(k in t for k in angry):
            return "angry"
        if any(k in t for k in sad):
            return "sad"
        if any(k in t for k in surprised) or "?" in t or "？" in t:
            return "surprised"
        if any(k in t for k in shy):
            return "shy"
        if any(k in t for k in happy):
            return "happy"
        return ""

    def _quit_app(self):
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        self._chat_follow_timer.stop()
        self._window_follow_timer.stop()
        self.proactive_scheduler.stop()
        self._bubble_hide_timer.stop()
        self._speech_bubble.hide()
        self._action_panel.close()
        if self.chat_window:
            self.chat_window.prepare_for_shutdown()
            self.chat_window.close()
        self.llm_worker.stop()
        self.voice_worker.stop()
        if self._model_server:
            self._model_server.stop()
        super().closeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()
        self._update_speech_bubble_position(self._cached_model_bounds or {})

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()
        self._update_speech_bubble_position(self._cached_model_bounds or {})

    def _show_speech_bubble(self, text: str):
        self._speech_bubble.show_text(text)
        self._update_speech_bubble_position(self._cached_model_bounds or {})
        self._bubble_hide_timer.stop()
        # Never auto-hide while a response is still being generated or spoken.
        if not self._voice_busy and not self._llm_busy:
            duration_ms = max(2400, min(7800, 1500 + len(text) * 35))
            self._bubble_hide_timer.start(duration_ms)

    def _hide_speech_bubble(self):
        if self._voice_busy or self._llm_busy:
            # Guard against stale timers firing during active generation/playback.
            self._bubble_hide_timer.start(1000)
            return
        self._speech_bubble.hide()

    def _update_speech_bubble_position(self, bounds: dict):
        if not self._speech_bubble.isVisible():
            return
        if not isinstance(bounds, dict) or not bounds:
            self._speech_bubble.move(max(8, self.width() - self._speech_bubble.width() - 10), 8)
            return
        cx = int(float(bounds.get("cx", self.width() * 0.5)))
        left = float(bounds.get("left", max(0, cx - 120)))
        right = float(bounds.get("right", min(self.width(), cx + 120)))
        model_w = max(1.0, right - left)
        top = int(float(bounds.get("top", self.height() * 0.25)))
        anchor_x = int(cx - model_w * 0.22)
        anchor_y = max(0, top - 18)
        local_head = QPoint(anchor_x, anchor_y)
        self._speech_bubble.update_anchor(local_head)

