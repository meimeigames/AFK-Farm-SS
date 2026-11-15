"""Проверки интерфейса в режиме offscreen (без реального экрана)."""

from __future__ import annotations

import os
import sys

import pytest

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from afkfarm.config import Buff, Config, ConfigStore, Rect, RoutePoint, Skill  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def test_key_edit_captures_tab_and_combos(app):
    from afkfarm.ui.widgets import KeyEdit
    w = KeyEdit()
    w.show()
    QTest.keyClick(w, Qt.Key_Tab)
    assert w.key() == "TAB"
    QTest.keyClick(w, Qt.Key_1, Qt.ControlModifier)
    assert w.key() == "CTRL+1"
    QTest.keyClick(w, Qt.Key_F5)
    assert w.key() == "F5"
    QTest.keyClick(w, Qt.Key_Escape)
    assert w.key() == "ESC"


def _custom_config() -> Config:
    cfg = Config()
    cfg.window.title = "7th Element"
    cfg.combat.target_key = "G"
    cfg.combat.action_delay = 0.55
    cfg.combat.skills = [Skill(True, "Огонь", "CTRL+2", 4.5, 0.7, 12, 0),
                         Skill(False, "Лёд", "SHIFT+3", 9.0, 1.2, 20, 40)]
    cfg.combat.buffs = [Buff(True, "Щит", "F3", 600, 1.5, True)]
    cfg.potions.hp_threshold = 63
    cfg.potions.hp_critical_key = "8"
    cfg.potions.death_action = "pause"
    cfg.movement.mode = "route"
    cfg.movement.loop_mode = "pingpong"
    cfg.movement.points = [RoutePoint(100, 200, 1.5), RoutePoint(300, 400, 0)]
    cfg.regions.target_bar = Rect(600, 20, 80, 8)
    cfg.vision.hp_color.h_min = 170
    cfg.vision.hp_slot_ref = 123.0
    cfg.hotkeys.stop = "CTRL+F12"
    cfg.safety.input_mode = "vk"
    cfg.show_preview = False
    return cfg


def test_main_window_roundtrip(app, tmp_path):
    from afkfarm.ui.main_window import MainWindow
    cfg = _custom_config()
    store = ConfigStore(cfg, path=tmp_path / "config.json")
    w = MainWindow(store)
    try:
        collected = w.collect()
        assert collected == cfg, "вкладки должны сохранять настройки без потерь"
        # изменение в интерфейсе попадает в хранилище
        w.potions_tab.binder.items[2][1].setValue(55)   # potions.hp_threshold
        w.apply_now()
        assert store.get().potions.hp_threshold == 55
        assert (tmp_path / "config.json").exists()
    finally:
        w.hotkeys.stop()
        w.close()


def test_calibration_autodetect_on_screenshot(app, tmp_path):
    import cv2
    from afkfarm.ui.main_window import MainWindow
    store = ConfigStore(Config(), path=tmp_path / "config.json")
    w = MainWindow(store)
    try:
        img = cv2.imread(os.path.join(os.path.dirname(__file__), "data", "screenshot_1280x800.jpg"))
        w.calib_tab.regions.hp_bar = Rect()
        w.calib_tab.set_snapshot(img)
        w.calib_tab._auto_bars()
        cfg = w.collect()
        assert cfg.regions.hp_bar.is_valid() and abs(cfg.regions.hp_bar.y - 752) <= 3
        assert "HP: <b>" in w.calib_tab.result.text()
    finally:
        w.hotkeys.stop()
        w.close()
