"""Журнал событий: файл с ротацией + передача записей в интерфейс."""

from __future__ import annotations

import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Callable, List, Optional

from .config import app_dir

LOGGER_NAME = "afkfarm"
_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATEFMT = "%H:%M:%S"

log = logging.getLogger(LOGGER_NAME)


class CallbackHandler(logging.Handler):
    """Передаёт отформатированные записи подписчикам (например, в GUI)."""

    def __init__(self):
        super().__init__()
        self._subs: List[Callable[[str, int], None]] = []
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    def subscribe(self, fn: Callable[[str, int], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover
            return
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(msg, record.levelno)
            except Exception:
                pass


_callback_handler: Optional[CallbackHandler] = None
_file_handler: Optional[RotatingFileHandler] = None


def setup_logging(to_file: bool = True, level: int = logging.INFO) -> CallbackHandler:
    global _callback_handler
    log.setLevel(logging.DEBUG)
    log.propagate = False
    if _callback_handler is None:
        _callback_handler = CallbackHandler()
        _callback_handler.setLevel(level)
        log.addHandler(_callback_handler)
    set_file_logging(to_file)
    return _callback_handler


def set_file_logging(enabled: bool) -> None:
    global _file_handler
    if enabled and _file_handler is None:
        logs = app_dir() / "logs"
        try:
            logs.mkdir(parents=True, exist_ok=True)
            _file_handler = RotatingFileHandler(
                logs / "afkfarm.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            _file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            _file_handler.setLevel(logging.DEBUG)
            log.addHandler(_file_handler)
        except OSError:
            _file_handler = None
    elif not enabled and _file_handler is not None:
        log.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None


class Throttle:
    """Ограничивает частоту повторяющихся сообщений (защита журнала от спама)."""

    def __init__(self):
        self._last: dict = {}
        self._lock = threading.Lock()

    def ok(self, key: str, interval: float) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < interval:
                return False
            self._last[key] = now
            return True


_throttle = Throttle()


def log_throttled(level: int, key: str, msg: str, interval: float = 10.0) -> None:
    if _throttle.ok(key, interval):
        log.log(level, msg)
