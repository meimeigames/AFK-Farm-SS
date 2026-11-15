"""Тонкая обёртка над WinAPI (ctypes): окна, курсор, SendInput, состояние клавиш.

На других ОС модуль импортируется без ошибок, но функции возвращают
«пустые» значения — это нужно для тестов и разработки вне Windows.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
GA_ROOT = 2
GA_ROOTOWNER = 3
SW_RESTORE = 9
VK_LBUTTON = 0x01
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12


class WinApiError(RuntimeError):
    pass


def ensure_windows() -> None:
    if not IS_WINDOWS:
        raise WinApiError("Функция доступна только в Windows")


# ---------------------------------------------------------------------------
# Окна
# ---------------------------------------------------------------------------

def get_window_text(hwnd) -> str:
    if not IS_WINDOWS or not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def list_windows() -> List[Tuple[int, str]]:
    """Видимые окна верхнего уровня с непустым заголовком."""
    if not IS_WINDOWS:
        return []
    result: List[Tuple[int, str]] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            title = get_window_text(hwnd)
            if title.strip():
                result.append((int(hwnd), title))
        return True

    user32.EnumWindows(_cb, 0)
    return result


def find_window(title_part: str, exclude_pid: Optional[int] = None) -> Optional[int]:
    """Найти окно по части заголовка (без учёта регистра).

    Точное совпадение имеет приоритет. Окна собственной программы пропускаются.
    """
    if not IS_WINDOWS or not title_part.strip():
        return None
    needle = title_part.strip().lower()
    exact = None
    partial = None
    for hwnd, title in list_windows():
        if exclude_pid is not None and window_pid(hwnd) == exclude_pid:
            continue
        low = title.lower()
        if low == needle and exact is None:
            exact = hwnd
        elif needle in low and partial is None:
            partial = hwnd
    return exact or partial


def window_pid(hwnd) -> int:
    if not IS_WINDOWS:
        return 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_window(hwnd) -> bool:
    return bool(IS_WINDOWS and hwnd and user32.IsWindow(hwnd))


def is_minimized(hwnd) -> bool:
    return bool(IS_WINDOWS and hwnd and user32.IsIconic(hwnd))


def get_client_rect_screen(hwnd) -> Optional[Tuple[int, int, int, int]]:
    """Клиентская область окна в экранных координатах: (left, top, width, height)."""
    if not IS_WINDOWS or not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return (pt.x, pt.y, w, h)


def get_foreground_window() -> Optional[int]:
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    return int(hwnd) if hwnd else None


def is_foreground(hwnd) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    if int(fg) == int(hwnd):
        return True
    root = user32.GetAncestor(fg, GA_ROOTOWNER)
    return bool(root) and int(root) == int(hwnd)


def activate_window(hwnd) -> bool:
    """Вывести окно на передний план."""
    if not IS_WINDOWS or not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.2)
    # Нажатие ALT снимает запрет Windows на смену активного окна
    _send_key_raw(VK_MENU, 0, False, False, use_scan=False)
    _send_key_raw(VK_MENU, 0, False, True, use_scan=False)
    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))
    time.sleep(0.15)
    return ok and is_foreground(hwnd)


# ---------------------------------------------------------------------------
# Курсор и клавиши
# ---------------------------------------------------------------------------

def get_cursor_pos() -> Optional[Tuple[int, int]]:
    if not IS_WINDOWS:
        return None
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return (pt.x, pt.y)


def set_cursor_pos(x: int, y: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(user32.SetCursorPos(int(x), int(y)))


def is_key_down(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _send_inputs(inputs) -> int:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return int(user32.SendInput(n, arr, ctypes.sizeof(INPUT)))


def _send_key_raw(vk: int, scan: int, extended: bool, up: bool, use_scan: bool) -> bool:
    if not IS_WINDOWS:
        return False
    flags = 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    if use_scan and scan:
        flags |= KEYEVENTF_SCANCODE
        inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    else:
        if not scan:
            scan = user32.MapVirtualKeyW(vk, 0)
        inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return _send_inputs([inp]) == 1


def send_key(vk: int, scan: int, extended: bool, up: bool, use_scan: bool = True) -> bool:
    return _send_key_raw(vk, scan, extended, up, use_scan)


def send_mouse_button(button: str, up: bool) -> bool:
    if not IS_WINDOWS:
        return False
    if button == "left":
        flag = MOUSEEVENTF_LEFTUP if up else MOUSEEVENTF_LEFTDOWN
    else:
        flag = MOUSEEVENTF_RIGHTUP if up else MOUSEEVENTF_RIGHTDOWN
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
    return _send_inputs([inp]) == 1


def nudge_mouse() -> None:
    """Сдвиг на 0 пикселей — заставляет игру получить WM_MOUSEMOVE."""
    if not IS_WINDOWS:
        return
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, 0)
    _send_inputs([inp])
