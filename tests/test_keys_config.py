from __future__ import annotations

import json

import pytest

from afkfarm import config, keys


@pytest.mark.parametrize("text,expected", [
    ("TAB", "TAB"), ("f", "F"), ("ctrl+1", "CTRL+1"), ("Shift + F2", "SHIFT+F2"),
    ("alt+ctrl+q", "CTRL+ALT+Q"), ("escape", "ESC"), ("num5", "NUM5"), ("-", "MINUS"),
])
def test_parse_combo(text, expected):
    assert str(keys.parse_combo(text)) == expected


@pytest.mark.parametrize("text", ["", "CTRL+", "Q+1", "FOO", "CTRL+SHIFT"])
def test_parse_combo_errors(text):
    with pytest.raises(keys.KeyParseError):
        keys.parse_combo(text)


def test_scancodes_known():
    assert keys.key_info("TAB").scan == 0x0F
    assert keys.key_info("F").scan == 0x21
    assert keys.key_info("1").scan == 0x02
    assert keys.key_info("0").scan == 0x0B
    assert keys.key_info("F12").vk == 0x7B
    assert keys.key_info("UP").extended


def test_combo_from_vk():
    assert keys.combo_from_vk(0x31, ctrl=True) == "CTRL+1"
    assert keys.combo_from_vk(0x09) == "TAB"
    assert keys.combo_from_vk(0x11) is None   # одиночный модификатор


def test_config_roundtrip(tmp_path):
    cfg = config.Config()
    cfg.combat.skills.append(config.Skill(True, "Тест", "CTRL+3", 7.5, 1.0, 20, 10))
    cfg.movement.points = [config.RoutePoint(10, 20, 1.5)]
    p = tmp_path / "c.json"
    config.save_config(cfg, p)
    loaded = config.load_config(p)
    assert loaded == cfg


def test_config_tolerates_garbage(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "combat": {"target_key": "G", "action_delay": "oops", "unknown": 1,
                   "skills": [{"name": "x", "key": "5", "cooldown": "3"}]},
        "potions": "not a dict",
        "extra": 42,
    }), encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg.combat.target_key == "G"
    assert cfg.combat.action_delay == config.CombatConfig().action_delay
    assert cfg.combat.skills[0].cooldown == 3.0
    assert cfg.potions == config.PotionsConfig()


def test_route_save_load(tmp_path):
    pts = [config.RoutePoint(1, 2, 0.0), config.RoutePoint(3, 4, 2.5)]
    p = tmp_path / "r.json"
    config.save_route(pts, p)
    assert config.load_route(p) == pts
