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


def test_russian_layout_aliases():
    assert str(keys.parse_combo("ы")) == "S"
    assert str(keys.parse_combo("Ctrl+а")) == "CTRL+F"
    assert keys.key_info("ы").scan == keys.key_info("S").scan


def test_migration_from_v1(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 1, "safety": {"max_same_key_per_10s": 35,
                                                       "max_inputs_per_10s": 80}}), encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg.safety.max_same_key_per_10s == 60
    assert cfg.safety.max_inputs_per_10s == 120
    assert cfg.version == config.Config().version
    assert cfg.combat.mode == "simple"


def test_defaults_match_requested_setup():
    cfg = config.Config()
    assert cfg.combat.mode == "simple"
    assert (cfg.combat.target_key, cfg.combat.attack_key) == ("TAB", "F")
    assert [s.key for s in cfg.combat.skills if s.enabled] == list("12345678")
    assert cfg.combat.simple_use_skills
    assert (cfg.potions.hp_key, cfg.potions.hp_threshold) == ("0", 50.0)
    assert (cfg.potions.mp_key, cfg.potions.mp_threshold) == ("9", 50.0)


def test_migration_v2_replaces_untouched_old_defaults(tmp_path):
    old = config.Config()
    old.version = 2
    old.combat.skills = config._old_default_skills()
    old.combat.simple_use_skills = False
    old.potions.hp_key, old.potions.hp_threshold = "9", 70.0
    old.potions.mp_key, old.potions.mp_threshold = "0", 30.0
    p = tmp_path / "c.json"
    config.save_config(old, p)
    cfg = config.load_config(p)
    assert (cfg.potions.hp_key, cfg.potions.mp_key) == ("0", "9")
    assert len(cfg.combat.skills) == 8 and cfg.combat.simple_use_skills


def test_migration_keeps_user_changes(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 2, "potions": {"hp_key": "5", "hp_threshold": 60}}),
                 encoding="utf-8")
    cfg = config.load_config(p)
    assert (cfg.potions.hp_key, cfg.potions.hp_threshold) == ("5", 60.0)
