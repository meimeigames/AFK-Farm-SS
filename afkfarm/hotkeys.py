"""Глобальные горячие клавиши (работают, даже когда активно окно игры).

Используется опрос ``GetAsyncKeyState`` в отдельном потоке: не нужны
низкоуровневые хуки и права администратора, а клавиша не «съедается».
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

from . import winapi
from .keys import KeyParseError, key_info, parse_combo


class HotkeyListener(threading.Thread):
    def __init__(self, get_bindings: Callable[[], Dict[str, str]],
                 on_action: Callable[[str], None], poll_interval: float = 0.03):
        super().__init__(name="afkfarm-hotkeys", daemon=True)
        self.get_bindings = get_bindings
        self.on_action = on_action
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._prev: Dict[str, bool] = {}

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _combo_down(text: str, strict: bool = True) -> Optional[bool]:
        try:
            combo = parse_combo(text)
        except KeyParseError:
            return None
        for m in combo.modifiers:
            info = key_info(m)
            if info is None or not winapi.is_key_down(info.vk):
                return False
        # Без модификаторов — не реагируем, если зажат CTRL/ALT (сочетания игры).
        # Для аварийной остановки (strict=False) модификаторы не мешают.
        if strict and not combo.modifiers:
            if winapi.is_key_down(winapi.VK_CONTROL) or winapi.is_key_down(winapi.VK_MENU):
                return False
        return winapi.is_key_down(combo.info.vk)

    def run(self) -> None:
        if not winapi.IS_WINDOWS:
            return
        while not self._stop.wait(self.poll_interval):
            try:
                bindings = self.get_bindings()
            except Exception:
                continue
            for action, text in bindings.items():
                if not text:
                    continue
                down = self._combo_down(text, strict=(action != "stop"))
                if down is None:
                    continue
                key = f"{action}:{text}"
                was = self._prev.get(key, False)
                self._prev[key] = down
                if down and not was:
                    try:
                        self.on_action(action)
                    except Exception:
                        pass
