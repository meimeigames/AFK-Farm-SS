"""Интеграционные проверки на настоящей Windows: окна, захват экрана, SendInput.

Создаётся тестовое окно Qt; программа находит его по заголовку, снимает его
изображение и печатает в него текст через SendInput (скан-коды), как в игре.
"""

from __future__ import annotations

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="только Windows")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _target_window(qapp, title: str):
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget
    w = QWidget()
    w.setWindowTitle(title)
    w.resize(360, 160)
    w.setStyleSheet("background: rgb(200, 30, 30);")
    edit = QLineEdit(w)
    edit.setStyleSheet("background: white; color: black;")
    lay = QVBoxLayout(w)
    lay.addWidget(edit)
    w.show()
    _pump(qapp, 0.5)
    return w, edit


def _game_window(title: str):
    from afkfarm.game_capture import GameWindow
    gw = GameWindow(title)
    gw._own_pid = -1          # тестовое окно в нашем же процессе — не исключать
    gw.RESEARCH_INTERVAL = 0
    return gw


def test_winapi_basics():
    from afkfarm import winapi
    assert winapi.IS_WINDOWS
    assert isinstance(winapi.list_windows(), list)
    pos = winapi.get_cursor_pos()
    assert pos is None or len(pos) == 2


def test_find_window_and_capture(qapp):
    from afkfarm.game_capture import GameCapture
    title = "AFK Farm test target 51234"
    w, _edit = _target_window(qapp, title)
    try:
        gw = _game_window(title)
        hwnd = gw.locate()
        assert hwnd, "окно не найдено по заголовку"
        rect = gw.client_rect()
        assert rect is not None and rect[2] > 100 and rect[3] > 50
        frame = GameCapture(gw).grab()
        if frame is None or frame.image.max() < 4:
            pytest.skip("захват экрана недоступен в этой сессии")
        assert frame.image.shape[1] == rect[2] and frame.image.shape[0] == rect[3]
    finally:
        w.close()


def test_sendinput_types_into_window(qapp):
    from afkfarm.clock import RealClock
    from afkfarm.config import Config
    from afkfarm.game_control import InputController
    title = "AFK Farm input target 77821"
    w, edit = _target_window(qapp, title)
    try:
        gw = _game_window(title)
        assert gw.locate()
        gw.activate()
        edit.setFocus()
        _pump(qapp, 0.4)
        if not gw.is_foreground():
            pytest.skip("Windows не дала активировать тестовое окно")
        cfg = Config()
        ctrl = InputController(gw, lambda: cfg, RealClock())
        for k in ("A", "F", "K", "1"):
            assert ctrl.press(k)
            _pump(qapp, 0.08)
        _pump(qapp, 0.3)
        assert edit.text().lower() == "afk1", edit.text()
    finally:
        w.close()


def test_elevation_detection_for_own_process():
    import os
    from afkfarm import winapi
    assert winapi.is_process_elevated(os.getpid()) == winapi.is_admin()
