import logging
import os
from pathlib import Path
import time

from PyQt5.QtCore import QPoint, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QCursor, QGuiApplication
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.local_model_server import LocalModelServer
from app.ui.chat_window import ChatWindow
from app.ui.live2d_webview import Live2DWebView
from app.workers.llm_worker import LLMWorker
from app.workers.voice_worker import VoiceWorker


logger = logging.getLogger("live2d.window")


class ModelActionPanel(QWidget):
    open_chat_clicked = pyqtSignal()
    close_app_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build_ui()

    def _build_ui(self):
        container = QWidget(self)
        container.setStyleSheet(
            """
            QWidget {
                background: rgba(255, 250, 252, 0.96);
                border: 1px solid #ffc5df;
                border-radius: 20px;
            }
            QPushButton {
                min-width: 44px;
                min-height: 44px;
                max-width: 44px;
                max-height: 44px;
                border-radius: 22px;
                border: 1px solid #ffb7d7;
                background: #fff;
                font-size: 16px;
                font-weight: 700;
                color: #5b4a5a;
            }
            QPushButton:hover {
                background: #ffe8f3;
            }
            """
        )
        self.chat_btn = QPushButton("C", container)
        self.close_btn = QPushButton("X", container)

        row = QHBoxLayout(container)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        row.addWidget(self.chat_btn)
        row.addWidget(self.close_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(container)

        self.chat_btn.clicked.connect(self.open_chat_clicked.emit)
        self.close_btn.clicked.connect(self.close_app_clicked.emit)


class DesktopPetWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live2D Desktop Pet")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(680, 760)

        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            g = primary.geometry()
            logger.info(
                "Primary screen geometry on init: x=%s y=%s w=%s h=%s",
                g.x(),
                g.y(),
                g.width(),
                g.height(),
            )

        self._model_server: LocalModelServer | None = None
        self.chat_window: ChatWindow | None = None
        self._last_pan_log_ts = 0.0
        self._drag_accum_x = 0.0
        self._drag_accum_y = 0.0
        self._drag_cursor_last: QPoint | None = None

        self._action_panel = ModelActionPanel()
        self._action_panel.open_chat_clicked.connect(self._open_chat)
        self._action_panel.close_app_clicked.connect(self._quit_app)

        model_url = self._prepare_model_url()
        self.live2d_view = Live2DWebView(self, model_url=model_url)
        self.live2d_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.live2d_view.customContextMenuRequested.connect(self._show_action_panel)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.live2d_view)
        self.setStyleSheet("background: transparent;")

        self.llm_worker = LLMWorker(self)
        self.voice_worker = VoiceWorker(self)
        self._chat_follow_timer = QTimer(self)
        self._chat_follow_timer.setInterval(220)
        self._chat_follow_timer.timeout.connect(self._position_chat_window)
        self._window_follow_timer = QTimer(self)
        self._window_follow_timer.setInterval(16)
        self._window_follow_timer.timeout.connect(self._follow_window_with_model)

        self._wire_signals()
        self.llm_worker.start()
        self.voice_worker.start()
        self._window_follow_timer.start()

    def _wire_signals(self):
        self.llm_worker.chunk_ready.connect(self._append_assistant_text)
        self.llm_worker.status_changed.connect(self._append_status)
        self.llm_worker.error_occurred.connect(self._append_error)
        self.llm_worker.text_for_voice.connect(self.voice_worker.add_text)
        self.llm_worker.new_session.connect(self.voice_worker.start_new_session)
        self.llm_worker.pet_command_ready.connect(self._apply_pet_command)

        self.voice_worker.error_occurred.connect(self._append_error)
        self.voice_worker.voice_started.connect(lambda: self.live2d_view.set_speaking(True))
        self.voice_worker.voice_finished.connect(lambda: self.live2d_view.set_speaking(False))

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

        default_root = Path(r"H:\live2dmodel")
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
        default_root = Path(r"H:\live2dmodel")
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

        def _on_bounds(bounds):
            if not self.chat_window or not self.chat_window.isVisible():
                return
            if not isinstance(bounds, dict) or not bounds:
                self._position_chat_window_fallback()
                return

            right = float(bounds.get("right", 0))
            top = float(bounds.get("top", 0))
            left = float(bounds.get("left", 0))

            top_global = self.live2d_view.mapToGlobal(QPoint(int(right), int(top)))
            left_global = self.live2d_view.mapToGlobal(QPoint(int(left), int(top)))

            chat = self.chat_window
            avail = self._active_screen_geometry()
            if avail is None:
                chat.move(top_global.x() + 16, max(20, top_global.y() - 20))
                return

            chat_w = chat.width()
            chat_h = chat.height()

            x = top_global.x() + 16
            y = top_global.y() - 16
            if x + chat_w > avail.right():
                x = left_global.x() - chat_w - 16
            x = max(avail.left(), min(x, avail.right() - chat_w))
            y = max(avail.top(), min(y, avail.bottom() - chat_h))
            chat.move(x, y)

        self.live2d_view.get_model_bounds(_on_bounds)

    def _position_chat_window_fallback(self):
        if not self.chat_window:
            return
        chat = self.chat_window
        geo = self.frameGeometry()
        x = geo.right() + 16
        y = geo.top() + 28
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

    def _follow_window_with_model(self):
        def _on_bounds(bounds):
            if not isinstance(bounds, dict) or not bounds:
                return
            drag_active = bool(bounds.get("dragActive", False))
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

        # If model is outside viewport, consume drag to bring it back first.
        raw_dx, raw_dy = self._consume_bounds_overflow(bounds, raw_dx, raw_dy)

        # Accumulate sub-pixel deltas to reduce jitter.
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
                raw_dx,
                raw_dy,
                applied_dx,
                applied_dy,
                clamped_x,
                clamped_y,
                wanted_x,
                wanted_y,
                cur.width(),
                cur.height(),
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
            geo.x(),
            geo.y(),
            geo.width(),
            geo.height(),
        )
        return geo

    def _send_message(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        chat = self._ensure_chat_window()
        chat.append_user(text)
        self.llm_worker.send_message(text)

    def _apply_pet_command(self, payload: dict):
        expression = str(payload.get("expression", "neutral")).strip().lower()
        motion = str(payload.get("motion", "idle")).strip().lower()
        self.live2d_view.set_expression(expression)
        self.live2d_view.play_motion(motion)

    def _append_assistant_text(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_assistant(text)

    def _append_status(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_status(text)

    def _append_error(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_error(text)

    def _quit_app(self):
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        self._chat_follow_timer.stop()
        self._window_follow_timer.stop()
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()
