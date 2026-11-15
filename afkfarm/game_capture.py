"""Поиск окна игры и захват изображения его клиентской области (MSS)."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import winapi
from .logger import log, log_throttled


@dataclass
class Frame:
    image: np.ndarray       # BGR, размер = клиентская область окна
    left: int               # экранные координаты левого верхнего угла клиентской области
    top: int
    timestamp: float
    foreground: bool

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class GameWindow:
    """Находит окно игры по части заголовка и следит за его состоянием."""

    RESEARCH_INTERVAL = 1.0

    def __init__(self, title: str = ""):
        self.title = title
        self.hwnd: Optional[int] = None
        self._last_search = 0.0
        self._own_pid = os.getpid()
        self._lock = threading.Lock()

    def set_title(self, title: str) -> None:
        with self._lock:
            if title != self.title:
                self.title = title
                self.hwnd = None
                self._last_search = 0.0

    def set_hwnd(self, hwnd: Optional[int]) -> None:
        with self._lock:
            self.hwnd = hwnd

    def locate(self) -> Optional[int]:
        """Вернуть дескриптор окна (с повторным поиском не чаще раза в секунду)."""
        with self._lock:
            if self.hwnd and winapi.is_window(self.hwnd):
                return self.hwnd
            now = time.monotonic()
            if now - self._last_search < self.RESEARCH_INTERVAL:
                return None
            self._last_search = now
            hwnd = winapi.find_window(self.title, exclude_pid=self._own_pid)
            if hwnd and hwnd != self.hwnd:
                log.info("Найдено окно игры: «%s»", winapi.get_window_text(hwnd))
            self.hwnd = hwnd
            return hwnd

    def client_rect(self) -> Optional[Tuple[int, int, int, int]]:
        hwnd = self.locate()
        if not hwnd or winapi.is_minimized(hwnd):
            return None
        return winapi.get_client_rect_screen(hwnd)

    def is_foreground(self) -> bool:
        hwnd = self.locate()
        return bool(hwnd) and winapi.is_foreground(hwnd)

    def activate(self) -> bool:
        hwnd = self.locate()
        return bool(hwnd) and winapi.activate_window(hwnd)

    def window_title(self) -> str:
        hwnd = self.locate()
        return winapi.get_window_text(hwnd) if hwnd else ""


class GameCapture:
    """Снимок клиентской области окна. Экземпляр MSS создаётся отдельно в каждом потоке."""

    def __init__(self, window: GameWindow):
        self.window = window
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss  # импорт здесь, чтобы модуль загружался и без mss
            factory = getattr(mss, "MSS", None) or mss.mss   # mss 10+: MSS, раньше: mss()
            sct = factory()
            self._local.sct = sct
        return sct

    def close(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._local.sct = None

    def grab(self) -> Optional[Frame]:
        rect = self.window.client_rect()
        if rect is None:
            return None
        left, top, width, height = rect
        try:
            shot = self._sct().grab({"left": left, "top": top, "width": width, "height": height})
            bgra = np.asarray(shot, dtype=np.uint8)
            image = np.ascontiguousarray(bgra[:, :, :3])
        except Exception as exc:  # mss.ScreenShotError и др.
            log_throttled(logging.WARNING, "capture-fail", f"Ошибка захвата экрана: {exc}", 15)
            self.close()
            return None
        if image.size and float(image.max()) < 4:
            log_throttled(logging.WARNING, "capture-black",
                          "Захват возвращает чёрный кадр. Переключите игру в оконный режим "
                          "или «окно без рамки» (полноэкранный DirectX не захватывается).", 30)
        return Frame(image=image, left=left, top=top, timestamp=time.monotonic(),
                     foreground=self.window.is_foreground())

    def grab_screen_region(self, left: int, top: int, width: int, height: int) -> Optional[np.ndarray]:
        try:
            shot = self._sct().grab({"left": left, "top": top, "width": width, "height": height})
            return np.ascontiguousarray(np.asarray(shot, dtype=np.uint8)[:, :, :3])
        except Exception:
            return None
