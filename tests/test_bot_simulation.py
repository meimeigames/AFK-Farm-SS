"""Прогон полного цикла фарма на симуляции игры (виртуальное время)."""

from __future__ import annotations

import collections

from afkfarm.bot import Bot, RunState
from afkfarm.clock import FakeClock
from afkfarm.config import Config, ConfigStore, Rect
from afkfarm.game_control import InputController

from fake_game import FakeBackend, FakeCapture, FakeGame, FakeWindow, sim_config


def make_bot(cfg=None, game=None):
    cfg = cfg or sim_config()
    store = ConfigStore(cfg, path=None)
    game = game or FakeGame()
    window = FakeWindow()
    clock = FakeClock()
    clock.on_advance = game.advance
    ctrl = InputController(window, store.get, clock, backend=FakeBackend(game))
    cap = FakeCapture(game, store, window)
    statuses = []
    bot = Bot(store, cap, ctrl, clock, on_status=statuses.append)
    bot.run_state = RunState.RUNNING
    bot._startup(store.get())
    return bot, game, clock, store, window, statuses


def run_for(bot, clock, seconds):
    end = clock.now() + seconds
    while clock.now() < end:
        bot.tick()
        clock.sleep(bot.store.get().safety.tick_interval)


def max_presses_per_10s(key_log, key):
    times = [t for t, k in key_log if k == key]
    best = 0
    dq = collections.deque()
    for t in times:
        dq.append(t)
        while dq and t - dq[0] > 10.0:
            dq.popleft()
        best = max(best, len(dq))
    return best


def test_full_farm_cycle_kills_and_moves():
    bot, game, clock, *_ = make_bot()
    run_for(bot, clock, 240)
    assert bot.stats.kills >= 10, bot.stats
    assert bot.stats.targets_found >= bot.stats.kills
    assert bot.nav.moves >= 2, "после зачистки места бот должен перемещаться"
    assert game.hp > 0, "персонаж не должен погибнуть"
    assert bot.stats.hp_potions >= 1, "при падении HP должны использоваться зелья"
    # ни одна клавиша не спамится сверх лимита
    for key in {k for _, k in game.key_log}:
        assert max_presses_per_10s(game.key_log, key) <= bot.store.get().safety.max_same_key_per_10s
    assert bot.ctrl.guard.total_blocked == 0


def test_potions_respect_threshold_and_cooldown():
    cfg = sim_config()
    cfg.potions.hp_threshold = 70
    cfg.potions.hp_cooldown = 10
    game = FakeGame(monster_dps_to_player=6.0)
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 120)
    hp_times = [t for t, k in game.key_log if k == "9"]
    assert hp_times, "зелье HP не использовалось"
    gaps = [b - a for a, b in zip(hp_times, hp_times[1:])]
    assert all(g >= 9.9 for g in gaps), gaps


def test_critical_key_used_below_critical_threshold():
    cfg = sim_config()
    cfg.potions.hp_critical_key = "8"
    cfg.potions.hp_critical_threshold = 40
    game = FakeGame(monster_dps_to_player=12.0)
    game.player_dps = 3.0
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 60)
    assert any(k == "8" for _, k in game.key_log)


def test_mp_potion():
    cfg = sim_config()
    cfg.potions.mp_threshold = 60
    game = FakeGame()
    game.mp = 40
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 3)
    assert any(k == "0" for _, k in game.key_log)


def test_stuck_target_is_dropped():
    cfg = sim_config()
    cfg.combat.stuck_timeout = 5
    cfg.combat.skills = []
    game = FakeGame()
    game.unreachable_target = True
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 30)
    assert bot.stats.stuck >= 1
    assert any(k == "ESC" for _, k in game.key_log)


def test_no_input_when_window_inactive():
    bot, game, clock, store, window, _ = make_bot()
    window.foreground = False
    run_for(bot, clock, 10)
    assert game.key_log == []
    assert game.clicks == []
    window.foreground = True
    run_for(bot, clock, 5)
    assert game.key_log, "после активации окна бот продолжает работу"


def test_pause_stops_all_input():
    bot, game, clock, *_ = make_bot()
    run_for(bot, clock, 5)
    bot.pause()
    bot.tick()
    n = len(game.key_log)
    run_for(bot, clock, 20)
    assert len(game.key_log) == n
    bot.resume()
    run_for(bot, clock, 5)
    assert len(game.key_log) > n


def test_death_stops_bot():
    cfg = sim_config()
    cfg.potions.hp_enabled = False
    game = FakeGame(monster_dps_to_player=40.0)
    game.player_dps = 1.0
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 30)
    assert bot.clock.stop_event.is_set()
    assert "погиб" in bot.stop_reason


def test_recognition_error_pauses_without_spam():
    bot, game, clock, *_ = make_bot()
    game.hide_bars = True
    run_for(bot, clock, 10)
    assert bot.run_state == RunState.PAUSED
    potion_presses = [k for _, k in game.key_log if k in ("9", "0")]
    assert potion_presses == []


def test_route_mode_cycles_points():
    cfg = sim_config()
    cfg.movement.mode = "route"
    from afkfarm.config import RoutePoint
    cfg.movement.points = [RoutePoint(400, 400, 0), RoutePoint(900, 400, 0), RoutePoint(640, 300, 0)]
    game = FakeGame(monsters=0)
    game.respawn = 0
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 60)
    xs = [x for _, x, _ in game.clicks]
    assert len(xs) >= 4
    # порядок точек: 400 → 900 → 640 → 400 …
    assert abs(xs[0] - 400) <= 5 and abs(xs[1] - 900) <= 5 and abs(xs[2] - 640) <= 5


def test_blind_mode_without_target_bar():
    cfg = sim_config()
    cfg.regions.target_bar = Rect()
    bot, game, clock, *_ = make_bot(cfg)
    run_for(bot, clock, 30)
    assert any(k == "TAB" for _, k in game.key_log)
    assert any(k == "F" for _, k in game.key_log)
    assert max_presses_per_10s(game.key_log, "TAB") <= 5


def test_live_config_change_is_applied():
    bot, game, clock, store, *_ = make_bot()
    run_for(bot, clock, 5)
    cfg = store.get()
    import copy
    new = copy.deepcopy(cfg)
    new.combat.target_key = "G"
    store.set(new, save=False)
    game.key_log.clear()
    run_for(bot, clock, 20)
    keys = {k for _, k in game.key_log}
    assert "G" in keys and "TAB" not in keys


# ---------------------------------------------------------------------------
# Простой режим: нет полосы HP моба → TAB, есть → F без остановки
# ---------------------------------------------------------------------------

def simple_config():
    cfg = sim_config()
    cfg.combat.mode = "simple"
    cfg.combat.simple_use_skills = False
    return cfg


def test_default_setup_f_spam_skills_1_8_and_potions_at_50():
    """Настройки по умолчанию: TAB/F, цифры 1–8 периодически, 0 при HP<50%, 9 при MP<50%."""
    from fake_game import TARGET_BAR
    cfg = Config()
    cfg.regions.target_bar = TARGET_BAR
    cfg.window.start_delay = 0.0
    cfg.window.activate_on_start = False
    assert cfg.combat.mode == "simple"
    game = FakeGame(monsters=1000, monster_dps_to_player=6.0)
    game.hp_potion_key, game.mp_potion_key = "0", "9"
    game.skill_keys = {str(i) for i in range(1, 9)}
    game.player_dps = 3.0
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 120)
    keys = [k for _, k in game.key_log]
    assert bot.stats.kills >= 5, bot.stats
    for d in "12345678":
        assert keys.count(d) >= 5, (d, keys.count(d))
    assert keys.count("F") >= 150
    assert game.clicks == []
    hp_presses = [(hp, mp) for k, hp, mp in game.potion_log if k == "0"]
    mp_presses = [(hp, mp) for k, hp, mp in game.potion_log if k == "9"]
    assert hp_presses and all(hp < 51 for hp, _ in hp_presses), hp_presses
    assert mp_presses and all(mp < 51 for _, mp in mp_presses), mp_presses
    assert bot.ctrl.guard.total_blocked == 0
    assert bot.run_state == RunState.RUNNING


def test_simple_mode_tab_then_spam_attack_until_bar_disappears():
    game = FakeGame(monsters=1000)
    bot, game, clock, *_ = make_bot(simple_config(), game)
    run_for(bot, clock, 120)
    assert bot.stats.kills >= 8, bot.stats
    assert game.clicks == [], "в простом режиме персонаж не перемещается"
    keys = [k for _, k in game.key_log]
    assert set(keys) <= {"TAB", "F", "9", "0"}, set(keys)
    # TAB не нажимается во время боя (иначе цель сбрасывалась бы на 100% HP)
    assert keys.count("TAB") <= bot.stats.kills + 3
    # F жмётся без остановки: ~раз в 0.3 с
    f_times = [t for t, k in game.key_log if k == "F"]
    gaps = sorted(b - a for a, b in zip(f_times, f_times[1:]) if b - a < 2.0)
    median = gaps[len(gaps) // 2]
    assert 0.29 <= median <= 0.40, median
    assert min(gaps) >= 0.25 and max(gaps) <= 0.7, (min(gaps), max(gaps))   # зелье может «вклиниться»
    assert bot.ctrl.guard.total_blocked == 0


def test_simple_mode_without_monsters_only_presses_tab():
    game = FakeGame(monsters=0)
    game.respawn = 0
    cfg = simple_config()
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 30)
    keys = [k for _, k in game.key_log]
    assert set(keys) == {"TAB"}
    assert 40 <= len(keys) <= 55, len(keys)       # раз в 0.6 с
    assert game.clicks == []
    assert bot.run_state == RunState.RUNNING


def test_simple_mode_attack_interval_respects_spam_guard():
    cfg = simple_config()
    cfg.combat.simple_attack_interval = 0.05
    cfg.safety.max_same_key_per_10s = 35
    game = FakeGame(monsters=1000)
    game.player_dps = 2.0                  # долгие бои — много нажатий F
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 40)
    assert bot.ctrl.guard.total_blocked == 0
    assert bot.run_state == RunState.RUNNING
    assert max_presses_per_10s(game.key_log, "F") <= 35


def test_simple_mode_works_without_hp_bar_when_potions_off():
    cfg = simple_config()
    cfg.regions.hp_bar = Rect()
    cfg.potions.hp_enabled = False
    cfg.potions.mp_enabled = False
    game = FakeGame(monsters=1000)
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 30)
    assert bot.run_state == RunState.RUNNING
    assert bot.stats.kills >= 2


def test_simple_mode_requires_target_bar():
    cfg = simple_config()
    cfg.regions.target_bar = Rect()
    bot, game, clock, *_ = make_bot(cfg)
    assert bot.clock.stop_event.is_set()
    assert "полоса HP цели" in bot.stop_reason


def test_simple_mode_optional_reset_of_stuck_target():
    cfg = simple_config()
    cfg.combat.simple_reset_after = 5
    game = FakeGame(monsters=1000)
    game.unreachable_target = True
    bot, game, clock, *_ = make_bot(cfg, game)
    run_for(bot, clock, 20)
    assert any(k == "ESC" for _, k in game.key_log)
    assert bot.stats.stuck >= 1


def test_switch_mode_while_running():
    cfg = sim_config()
    bot, game, clock, store, *_ = make_bot(cfg)
    run_for(bot, clock, 10)
    import copy
    new = copy.deepcopy(store.get())
    new.combat.mode = "simple"
    store.set(new, save=False)
    game.clicks.clear()
    run_for(bot, clock, 60)
    assert game.clicks == []
    assert bot.stats.kills >= 2
