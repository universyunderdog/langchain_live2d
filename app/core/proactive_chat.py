from __future__ import annotations

import os
import random
import time
from typing import Protocol

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


class ContextProvider(Protocol):
    """Future extension point (e.g. VLM screen understanding)."""

    def build_context_hint(self) -> str:
        ...


class EmptyContextProvider:
    def build_context_hint(self) -> str:
        return ""


class ProactiveChatScheduler(QObject):
    """Low-frequency proactive chat trigger with idle/busy guards."""

    prompt_ready = pyqtSignal(str)

    def __init__(self, parent=None, context_provider: ContextProvider | None = None):
        super().__init__(parent)
        self._enabled = os.getenv("PROACTIVE_CHAT_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._min_interval = max(120, int(os.getenv("PROACTIVE_CHAT_MIN_SEC", "480")))
        self._max_interval = max(self._min_interval, int(os.getenv("PROACTIVE_CHAT_MAX_SEC", "1200")))
        self._quiet_after_activity = max(20, int(os.getenv("PROACTIVE_CHAT_QUIET_SEC", "120")))
        self._busy = False
        self._last_activity_ts = time.time()
        self._next_due_ts = 0.0
        self._provider = context_provider or EmptyContextProvider()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._schedule_next()

    def start(self):
        if self._enabled:
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if self._enabled:
            self._schedule_next()
            self.start()
        else:
            self.stop()

    def set_busy(self, busy: bool):
        self._busy = bool(busy)
        if self._busy:
            self._last_activity_ts = time.time()

    def notify_activity(self):
        self._last_activity_ts = time.time()
        self._schedule_next()

    def _schedule_next(self):
        now = time.time()
        self._next_due_ts = now + random.uniform(float(self._min_interval), float(self._max_interval))

    def _tick(self):
        if not self._enabled:
            return
        now = time.time()
        if self._busy:
            return
        if now - self._last_activity_ts < self._quiet_after_activity:
            return
        if now < self._next_due_ts:
            return

        hint = (self._provider.build_context_hint() or "").strip()
        prompt = (
            "请你主动开启一段简短自然的关心式对话，"
            "1到2句即可，语气轻松，不要重复寒暄，不要自问自答。"
        )
        if hint:
            prompt += f"\n上下文参考：{hint}"
        self.prompt_ready.emit(prompt)
        self._last_activity_ts = now
        self._schedule_next()
