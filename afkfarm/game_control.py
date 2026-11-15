"""Управление клавиатурой и мышью через SendInput + защита от спама.

Все нажатия проходят через ``InputController``:
  * ввод отправляется только когда окно игры активно (настраивается);
  * ``InputGuard`` ограничивает частоту нажатий — даже при ошибке в логике
    программа не сможет «зажать» или заспамить клавишу;
  * каждая нажатая клавиша обязательно отпускается (в т.ч. при остановке).
"""

from __future__ import annotations

import collections
import logging
import random
import threading
from typing import Callable, Deque, Dict, Optional, Set, Tuple

from . import winapi
from .config import Config, SafetyConfig
from .keys import KeyCombo, KeyInfo, KeyParseError, key_info, parse_combo
from .logger import log, log_throttled


# ---------------------------------------------------------------------------
# Низкоуровневые «бэкенды» ввода
# ---------------------------------------------------------------------------

class WinInputBackend:
    """Реальный ввод в Windows."""

    def key(self, info: KeyInfo, up: bool, use_scan: bool) -> bool:
        return winapi.send_key(info.vk, info.scan, info.extended, up, use_scan)

    def move(self, x: int, y: int) -> bool:
        ok = winapi.set_cursor_pos(x, y)
        winapi.nudge_mouse()
        return ok

    def button(self, button: str, up: bool) -> bool:
        return winapi.send_mouse_button(button, up)


# ---------------------------------------------------------------------------
# Защита от спама
# ---------------------------------------------------------------------------

class InputGuard:
    """Скользящее окно 10 с: лимит на одну клавишу и общий лимит нажатий."""

    WINDOW = 10.0

    def __init__(self, clock):
        self.clock = clock
        self._per_key: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self._all: Deque[float] = collections.deque()
        self._last_input = -1e9
        self.blocked_streak = 0
        self.total_blocked = 0
        self._lock = threading.Lock()

    def _trim(self, dq: Deque[float], now: float) -> None:
        while dq and now - dq[0] > self.WINDOW:
            dq.popleft()

    def wait_gap(self, safety: SafetyConfig) -> None:
        """Выдержать минимальную паузу между любыми действиями."""
        gap = safety.min_input_gap - (self.clock.now() - self._last_input)
        if gap > 0:
            self.clock.sleep(min(gap, 1.0))

    def allow(self, key_id: str, safety: SafetyConfig) -> bool:
        with self._lock:
            now = self.clock.now()
            dq = self._per_key[key_id]
            self._trim(dq, now)
            self._trim(self._all, now)
            if len(dq) >= safety.max_same_key_per_10s:
                reason = f"клавиша {key_id}: больше {safety.max_same_key_per_10s} нажатий за 10 с"
            elif len(self._all) >= safety.max_inputs_per_10s:
                reason = f"больше {safety.max_inputs_per_10s} действий за 10 с"
            else:
                dq.append(now)
                self._all.append(now)
                self._last_input = now
                self.blocked_streak = 0
                return True
            self.blocked_streak += 1
            self.total_blocked += 1
        log_throttled(logging.WARNING, f"spam-{key_id}",
                      f"Защита от спама: нажатие заблокировано ({reason})", 5)
        return False

    def spam_triggered(self, safety: SafetyConfig) -> bool:
        return self.blocked_streak >= safety.spam_pause_after

    def reset(self) -> None:
        with self._lock:
            self._per_key.clear()
            self._all.clear()
            self.blocked_streak = 0


# ---------------------------------------------------------------------------
# Контроллер
# ---------------------------------------------------------------------------

class InputController:
    def __init__(self, window, cfg_getter: Callable[[], Config], clock,
                 backend=None):
        self.window = window
        self.cfg_getter = cfg_getter
        self.clock = clock
        self.backend = backend or WinInputBackend()
        self.guard = InputGuard(clock)
        self._held_keys: Set[KeyInfo] = set()
        self._held_buttons: Set[str] = set()
        self._lock = threading.RLock()
        self._combo_cache: Dict[str, Optional[KeyCombo]] = {}
        self.presses = 0
        self.clicks = 0

    # -- helpers ---------------------------------------------------------
    def _combo(self, text: str) -> Optional[KeyCombo]:
        if text in self._combo_cache:
            return self._combo_cache[text]
        try:
            combo = parse_combo(text)
        except KeyParseError as exc:
            log_throttled(logging.ERROR, f"badkey-{text}", f"Некорректная клавиша «{text}»: {exc}", 30)
            combo = None
        self._combo_cache[text] = combo
        return combo

    def can_send(self) -> bool:
        cfg = self.cfg_getter()
        if cfg.window.require_foreground and not self.window.is_foreground():
            log_throttled(logging.INFO, "not-foreground",
                          "Окно игры не активно — действия приостановлены", 10)
            return False
        return True

    # -- клавиатура --------------------------------------------------------
    def press(self, key_text: str, hold: Optional[float] = None) -> bool:
        """Нажать и отпустить клавишу/сочетание. Возвращает True при успехе."""
        if not key_text or not str(key_text).strip():
            return False
        combo = self._combo(str(key_text))
        if combo is None:
            return False
        cfg = self.cfg_getter()
        safety = cfg.safety
        with self._lock:
            if not self.can_send():
                return False
            self.guard.wait_gap(safety)
            if not self.guard.allow(str(combo), safety):
                return False
            use_scan = safety.input_mode != "vk"
            hold = safety.key_hold if hold is None else hold
            mods = [key_info(m) for m in combo.modifiers]
            main = combo.info
            pressed = []
            try:
                for m in mods:
                    self._key(m, False, use_scan)
                    pressed.append(m)
                    self.clock.sleep(0.015)
                self._key(main, False, use_scan)
                pressed.append(main)
                self.clock.sleep(max(0.01, hold))
            finally:
                # Отпускаем в обратном порядке — даже если ожидание прервано
                for info in reversed(pressed):
                    self._key(info, True, use_scan)
            self.presses += 1
            log.debug("Нажата клавиша %s", combo)
            return True

    def _key(self, info: KeyInfo, up: bool, use_scan: bool) -> None:
        try:
            self.backend.key(info, up, use_scan)
        finally:
            if up:
                self._held_keys.discard(info)
            else:
                self._held_keys.add(info)

    # -- мышь ----------------------------------------------------------------
    def click(self, client_x: int, client_y: int, button: str = "left", jitter: int = 0) -> bool:
        """Клик в точке клиентской области окна игры."""
        cfg = self.cfg_getter()
        safety = cfg.safety
        with self._lock:
            if not self.can_send():
                return False
            rect = self.window.client_rect()
            if rect is None:
                return False
            left, top, width, height = rect
            if jitter:
                client_x += random.randint(-jitter, jitter)
                client_y += random.randint(-jitter, jitter)
            client_x = max(1, min(width - 2, int(client_x)))
            client_y = max(1, min(height - 2, int(client_y)))
            self.guard.wait_gap(safety)
            if not self.guard.allow(f"MOUSE_{button.upper()}", safety):
                return False
            self.backend.move(left + client_x, top + client_y)
            self.clock.sleep(0.05)
            try:
                self.backend.button(button, False)
                self._held_buttons.add(button)
                self.clock.sleep(max(0.02, safety.key_hold))
            finally:
                self.backend.button(button, True)
                self._held_buttons.discard(button)
            self.clicks += 1
            log.debug("Клик %s в точке (%d, %d)", button, client_x, client_y)
            return True

    # -- аварийное отпускание ----------------------------------------------------
    def release_all(self) -> None:
        """Отпустить всё, что могло остаться нажатым."""
        cfg = self.cfg_getter()
        use_scan = cfg.safety.input_mode != "vk"
        for info in list(self._held_keys):
            try:
                self.backend.key(info, True, use_scan)
            except Exception:
                pass
        self._held_keys.clear()
        for b in list(self._held_buttons):
            try:
                self.backend.button(b, True)
            except Exception:
                pass
        self._held_buttons.clear()
        # Модификаторы отпускаем всегда — на случай внешнего сбоя
        for m in ("CTRL", "SHIFT", "ALT"):
            info = key_info(m)
            if info:
                try:
                    self.backend.key(info, True, use_scan)
                except Exception:
                    pass
