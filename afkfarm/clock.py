"""Часы с прерываемым ожиданием (реальные и тестовые)."""

from __future__ import annotations

import threading
import time


class RealClock:
    """Монотонное время; ``sleep`` прерывается установкой ``stop_event``."""

    def __init__(self, stop_event: threading.Event | None = None):
        self.stop_event = stop_event or threading.Event()

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> bool:
        """Подождать. Возвращает False, если ожидание прервано остановкой."""
        if seconds <= 0:
            return not self.stop_event.is_set()
        return not self.stop_event.wait(seconds)


class FakeClock:
    """Виртуальное время для тестов: ``sleep`` мгновенно сдвигает часы."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.stop_event = threading.Event()
        self.on_advance = None  # callback(dt) — например, симуляция игры

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> bool:
        if seconds > 0:
            self.t += seconds
            if self.on_advance:
                self.on_advance(seconds)
        return not self.stop_event.is_set()
