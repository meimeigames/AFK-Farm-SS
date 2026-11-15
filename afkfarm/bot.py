"""Движок бота: главный цикл и конечный автомат режимов.

Цикл (каждые ``tick_interval`` секунд):
  1. снимок окна игры → распознавание HP, MP, цели, движения мини-карты;
  2. проверки безопасности (окно активно, распознавание, смерть, спам);
  3. зелья (наивысший приоритет);
  4. обработчик текущего режима: ПОИСК → АТАКА → (ЛУТ) → ПОИСК …;
     если цели не находятся — ПЕРЕМЕЩЕНИЕ, при нехватке HP/MP — ОТДЫХ.

Каждый обработчик делает не более одного действия за такт и никогда не
блокируется надолго: все ожидания реализованы через отметки времени.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from .clock import RealClock
from .combat import CombatManager
from .config import Config, ConfigStore
from .logger import log, log_throttled
from .movement import Navigator
from .potions import PotionManager
from .vision import Vision, VisionState, draw_overlay


class Mode(str, enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    WAIT_WINDOW = "wait_window"
    SEARCH = "search"
    ATTACK = "attack"
    LOOT = "loot"
    MOVE = "move"
    REST = "rest"
    RECOVER = "recover"


MODE_LABELS = {
    Mode.IDLE: "Ожидание",
    Mode.STARTING: "Запуск",
    Mode.WAIT_WINDOW: "Ожидание окна игры",
    Mode.SEARCH: "Поиск цели",
    Mode.ATTACK: "Атака",
    Mode.LOOT: "Сбор добычи",
    Mode.MOVE: "Перемещение",
    Mode.REST: "Отдых",
    Mode.RECOVER: "Восстановление",
}


class RunState(str, enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


RUN_LABELS = {
    RunState.STOPPED: "Остановлена",
    RunState.RUNNING: "Работает",
    RunState.PAUSED: "Пауза",
}


@dataclass
class BotStats:
    started_at: float = 0.0
    targets_found: int = 0
    kills: int = 0
    lost_targets: int = 0
    stuck: int = 0
    hp_potions: int = 0
    mp_potions: int = 0
    skills_used: int = 0
    buffs_used: int = 0
    moves: int = 0
    rests: int = 0


@dataclass
class BotStatus:
    run_state: RunState
    mode: Mode
    mode_label: str
    hp: Optional[float]
    mp: Optional[float]
    target_supported: bool
    target_present: bool
    target_hp: Optional[float]
    window_ok: bool
    foreground: bool
    stats: BotStats
    uptime: float
    message: str = ""
    motion: Optional[float] = None
    preview: Optional[np.ndarray] = None   # BGR, уменьшенный кадр с разметкой


class Bot:
    def __init__(self, store: ConfigStore, capture, controller, clock: Optional[RealClock] = None,
                 on_status: Optional[Callable[[BotStatus], None]] = None,
                 on_stopped: Optional[Callable[[str], None]] = None):
        self.store = store
        self.capture = capture
        self.ctrl = controller
        self.clock = clock or controller.clock
        self.on_status = on_status
        self.on_stopped = on_stopped
        self.vision = Vision()
        self.potions = PotionManager(controller, self.clock)
        self.combat = CombatManager(controller, self.clock)
        self.nav = Navigator(controller, self.clock)
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self.run_state = RunState.STOPPED
        self._pause_request: Optional[bool] = None
        self.stop_reason = ""
        self.heartbeat = 0.0
        self.last_vs = VisionState()
        self.message = ""
        self._reset_runtime()

    # ------------------------------------------------------------------
    # Управление жизненным циклом
    # ------------------------------------------------------------------
    def _reset_runtime(self) -> None:
        self.mode = Mode.IDLE
        self.stats = BotStats()
        self.started_at = self.clock.now()
        self.mode_since = self.started_at
        self.next_search = 0.0
        self.awaiting_confirm = False
        self.confirm_deadline = 0.0
        self.search_failures = 0
        self.target_absent_since: Optional[float] = None
        self.blind = False
        self.stuck_in_row = 0
        self.dead_since: Optional[float] = None
        self.recog_errors = 0
        self.loot_left = 0
        self.next_loot = 0.0
        self.next_move_tab = 0.0
        self.arrived_wait_until = 0.0
        self.rest_started = 0.0
        self.rest_hp_max = 0.0
        self.rest_block_until = 0.0
        self.recover_flash_until = 0.0
        self.last_status_emit = 0.0
        self.last_preview = 0.0
        self.window_ok = False
        self.foreground = False
        self.size_warned = False
        self.errors_in_row = 0
        self.potions.reset()
        self.vision.reset()
        self.nav.reset_move()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return False
        self.clock.stop_event.clear()
        self.ctrl.guard.reset()
        self._reset_runtime()
        self.stop_reason = ""
        self._pause_request = None
        self.run_state = RunState.RUNNING
        self.heartbeat = self.clock.now()
        self._thread = threading.Thread(target=self.run, name="afkfarm-bot", daemon=True)
        self._thread.start()
        return True

    def stop(self, reason: str = "остановлено пользователем") -> None:
        if not self.stop_reason:
            self.stop_reason = reason
        self.clock.stop_event.set()

    def pause(self) -> None:
        self._pause_request = True

    def resume(self) -> None:
        self._pause_request = False

    def toggle_pause(self) -> None:
        if self.run_state == RunState.PAUSED:
            self.resume()
        elif self.run_state == RunState.RUNNING:
            self.pause()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def run(self) -> None:
        cfg = self.store.get()
        log.info("Бот запущен. Аварийная остановка: %s, пауза: %s",
                 cfg.hotkeys.stop, cfg.hotkeys.pause)
        self._set_mode(Mode.STARTING)
        try:
            self._startup(cfg)
            while not self.clock.stop_event.is_set():
                t0 = self.clock.now()
                try:
                    self.tick()
                    self.errors_in_row = 0
                except Exception as exc:  # не даём циклу упасть из-за одного кадра
                    self.errors_in_row += 1
                    log.exception("Ошибка в цикле бота: %s", exc)
                    self.ctrl.release_all()
                    if self.errors_in_row >= 10:
                        self.stop("слишком много ошибок подряд")
                        break
                    self.clock.sleep(0.5)
                cfg = self.store.get()
                spent = self.clock.now() - t0
                self.clock.sleep(max(0.005, cfg.safety.tick_interval - spent))
        finally:
            self.ctrl.release_all()
            try:
                self.capture.close()
            except Exception:
                pass
            self.run_state = RunState.STOPPED
            self._set_mode(Mode.IDLE)
            reason = self.stop_reason or "остановлено"
            log.info("Бот остановлен (%s). Целей: %d, убито: %d, зелий HP/MP: %d/%d",
                     reason, self.stats.targets_found, self.stats.kills,
                     self.potions.hp_used, self.potions.mp_used)
            self._emit_status(force=True)
            if self.on_stopped:
                try:
                    self.on_stopped(reason)
                except Exception:
                    pass

    def _startup(self, cfg: Config) -> None:
        win = getattr(self.ctrl, "window", None)
        if cfg.window.activate_on_start and win is not None:
            if win.locate():
                win.activate()
            else:
                log.warning("Окно игры «%s» не найдено — жду появления", cfg.window.title)
        if cfg.window.start_delay > 0:
            log.info("Старт через %.1f с…", cfg.window.start_delay)
            end = self.clock.now() + cfg.window.start_delay
            while self.clock.now() < end:
                self.heartbeat = self.clock.now()   # сторожевой таймер видит, что мы живы
                if not self.clock.sleep(min(0.2, end - self.clock.now())):
                    return
        if not cfg.regions.target_bar.is_valid():
            log.warning("Полоса HP цели не откалибрована — включён «слепой» режим: "
                        "TAB + атака по таймеру (%.0f с). Откалибруйте полосу цели "
                        "для надёжной работы.", cfg.combat.blind_fight_time)
        self.started_at = self.clock.now()
        self.stats.started_at = self.started_at
        self._set_mode(Mode.SEARCH)
        self.next_search = self.clock.now()

    # ------------------------------------------------------------------
    # Один такт
    # ------------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock.now()
        self.heartbeat = now
        cfg = self.store.get()

        # Пауза / продолжение
        req = self._pause_request
        if req is not None:
            self._pause_request = None
            if req and self.run_state == RunState.RUNNING:
                self.run_state = RunState.PAUSED
                self.ctrl.release_all()
                log.info("Пауза")
            elif not req and self.run_state == RunState.PAUSED:
                self.run_state = RunState.RUNNING
                self.ctrl.guard.reset()
                self._enter_search(now, immediate=True)
                log.info("Продолжение работы")

        frame = self.capture.grab()
        if frame is None:
            self.window_ok = False
            self.foreground = False
            if self.run_state == RunState.RUNNING:
                self.message = "Окно игры не найдено"
                log_throttled(logging.WARNING, "no-window",
                              f"Окно игры «{cfg.window.title}» не найдено или свёрнуто", 15)
            self._emit_status(display_mode=Mode.WAIT_WINDOW)
            return
        self.window_ok = True
        self.foreground = frame.foreground

        vs = self.vision.analyze(frame.image, cfg, now, track_motion=True)
        self.last_vs = vs
        if vs.size_mismatch and not self.size_warned:
            self.size_warned = True
            log.warning("Размер окна игры %dx%d отличается от калибровки %dx%d — "
                        "проверьте области на вкладке «Калибровка».",
                        frame.width, frame.height,
                        cfg.regions.reference_width, cfg.regions.reference_height)

        if self.run_state == RunState.PAUSED:
            self._emit_status(frame=frame, vs=vs)
            return

        if cfg.window.require_foreground and not frame.foreground:
            self.message = "Окно игры не активно"
            self._emit_status(frame=frame, vs=vs, display_mode=Mode.WAIT_WINDOW)
            return
        self.message = ""

        # --- ошибки распознавания ---
        if vs.hp is None:
            self.recog_errors += 1
            log_throttled(logging.WARNING, "recog-hp",
                          "Не удаётся распознать полосу HP (перекрыта или неверная область)", 10)
            if self.recog_errors >= cfg.safety.recognition_error_limit:
                log.error("HP не распознаётся %d кадров подряд — пауза. Проверьте калибровку.",
                          self.recog_errors)
                self.recog_errors = 0
                self.pause()
        else:
            self.recog_errors = 0

        # --- смерть персонажа ---
        if vs.hp is not None and vs.hp <= 0.5:
            if self.dead_since is None:
                self.dead_since = now
            elif now - self.dead_since >= cfg.potions.death_confirm_time:
                self.dead_since = None
                if cfg.potions.death_action == "pause":
                    log.error("Персонаж погиб (HP = 0) — пауза")
                    self.pause()
                else:
                    log.error("Персонаж погиб (HP = 0) — остановка")
                    self.stop("персонаж погиб")
                return
        else:
            self.dead_since = None

        # --- защита от спама ---
        if self.ctrl.guard.spam_triggered(cfg.safety):
            log.error("Сработала защита от спама клавиш — пауза. Проверьте настройки задержек.")
            self.ctrl.guard.reset()
            self.pause()
            return

        # --- зелья ---
        action = self.potions.tick(now, vs, cfg)
        if action:
            self.recover_flash_until = now + 1.2
        self.stats.hp_potions = self.potions.hp_used + self.potions.crit_used
        self.stats.mp_potions = self.potions.mp_used

        # --- режим ---
        handler = {
            Mode.SEARCH: self._do_search,
            Mode.ATTACK: self._do_attack,
            Mode.LOOT: self._do_loot,
            Mode.MOVE: self._do_move,
            Mode.REST: self._do_rest,
        }.get(self.mode)
        if handler is None:
            self._enter_search(now, immediate=True)
        else:
            handler(now, vs, cfg, frame)

        self.stats.skills_used = self.combat.skills_used
        self.stats.buffs_used = self.combat.buffs_used
        self.stats.moves = self.nav.moves
        self._emit_status(frame=frame, vs=vs)

    # ------------------------------------------------------------------
    # Режимы
    # ------------------------------------------------------------------
    def _set_mode(self, mode: Mode) -> None:
        if mode != getattr(self, "mode", None):
            self.mode = mode
            self.mode_since = self.clock.now()
            log.debug("Режим: %s", MODE_LABELS[mode])

    def _enter_search(self, now: float, immediate: bool = False, delay: float = 0.0) -> None:
        self._set_mode(Mode.SEARCH)
        self.awaiting_confirm = False
        self.blind = False
        self.next_search = now + (0.0 if immediate else delay)

    def _begin_attack(self, now: float, vs: VisionState, cfg: Config, new_target: bool) -> None:
        self._set_mode(Mode.ATTACK)
        self.awaiting_confirm = False
        self.search_failures = 0
        self.target_absent_since = None
        if new_target:
            self.stats.targets_found += 1
        self.combat.start_fight(now, vs.target_hp)
        self.combat.press_attack(now, cfg)
        if vs.target_hp is not None:
            log.info("Цель выбрана (HP цели %.0f%%) — атака", vs.target_hp)

    def _need_rest(self, now: float, vs: VisionState, cfg: Config) -> bool:
        p = cfg.potions
        if not p.rest_enabled or not p.rest_key.strip() or now < self.rest_block_until:
            return False
        if vs.hp is not None and vs.hp < p.rest_start_hp:
            return True
        return vs.mp is not None and vs.mp < p.rest_start_mp

    # ПОИСК ----------------------------------------------------------------
    def _do_search(self, now, vs: VisionState, cfg: Config, frame) -> None:
        c = cfg.combat
        if not vs.target_supported:
            # Слепой режим: TAB + атака, бой по таймеру
            if now >= self.next_search and self.ctrl.press(c.target_key):
                self.blind = True
                self._set_mode(Mode.ATTACK)
                self.stats.targets_found += 1
                self.combat.start_fight(now, None)
                self.combat.press_attack(now, cfg)
            return

        if vs.target_present:
            self._begin_attack(now, vs, cfg, new_target=True)
            return

        if not self.awaiting_confirm:
            if self._need_rest(now, vs, cfg):
                self._enter_rest(now, vs, cfg)
                return
            idx = self.combat.due_buff(now, cfg, in_combat=False)
            if idx is not None:
                if self.combat.use_buff(now, cfg, idx):
                    return
            if now < self.combat.busy_until:
                return

        if self.awaiting_confirm:
            if now >= self.confirm_deadline:
                self.awaiting_confirm = False
                self.search_failures += 1
                self.next_search = now + c.search_delay
                log.debug("Цель не найдена (%d/%d)", self.search_failures, c.max_search_attempts)
                if self.search_failures >= max(1, c.max_search_attempts):
                    self.search_failures = 0
                    log.info("Рядом нет целей — перемещение")
                    self._enter_move(now, cfg, frame)
            return

        if now >= self.next_search:
            if self.ctrl.press(c.target_key):
                self.awaiting_confirm = True
                self.confirm_deadline = now + c.target_confirm_delay
            else:
                self.next_search = now + 0.5

    # АТАКА ----------------------------------------------------------------
    def _do_attack(self, now, vs: VisionState, cfg: Config, frame) -> None:
        c = cfg.combat
        if self.blind:
            if now - self.combat.fight_start >= c.blind_fight_time:
                self.stats.kills += 1
                self._after_target(now, cfg)
                return
            self.combat.rotation(now, vs, cfg)
            return

        if not vs.target_present:
            if self.target_absent_since is None:
                self.target_absent_since = now
            if now - self.target_absent_since >= c.target_lost_confirm:
                if self.combat.damage_dealt():
                    self.stats.kills += 1
                    self.stuck_in_row = 0
                    log.info("Цель уничтожена (всего: %d)", self.stats.kills)
                else:
                    self.stats.lost_targets += 1
                    log.info("Цель пропала")
                self._after_target(now, cfg)
            return
        self.target_absent_since = None
        self.combat.update_target(now, vs.target_hp)

        stuck = self.combat.is_stuck(now, cfg)
        too_long = self.combat.fight_too_long(now, cfg)
        if stuck or too_long:
            self.stats.stuck += 1
            self.stuck_in_row += 1
            why = "HP цели не уменьшается" if stuck else "бой длится слишком долго"
            log.warning("%s — сбрасываю цель (%d подряд)", why, self.stuck_in_row)
            if c.clear_target_key.strip():
                self.ctrl.press(c.clear_target_key)
            if self.stuck_in_row >= max(1, c.max_stuck_in_row):
                self.stuck_in_row = 0
                self._enter_move(now, cfg, frame)
            else:
                self._enter_search(now, delay=0.3)
            return

        idx = self.combat.due_buff(now, cfg, in_combat=True)
        if idx is not None and self.combat.use_buff(now, cfg, idx):
            return
        self.combat.rotation(now, vs, cfg)

    def _after_target(self, now: float, cfg: Config) -> None:
        c = cfg.combat
        if c.loot_key.strip() and c.loot_presses > 0:
            self._set_mode(Mode.LOOT)
            self.loot_left = c.loot_presses
            self.next_loot = now + 0.2
        else:
            self._enter_search(now, immediate=True)   # сразу TAB к следующей цели

    # ЛУТ ------------------------------------------------------------------
    def _do_loot(self, now, vs: VisionState, cfg: Config, frame) -> None:
        if vs.target_present:      # на нас напали — в бой
            self._begin_attack(now, vs, cfg, new_target=True)
            return
        if self.loot_left <= 0:
            self._enter_search(now, immediate=True)
            return
        if now >= self.next_loot:
            self.ctrl.press(cfg.combat.loot_key)
            self.loot_left -= 1
            self.next_loot = now + max(0.1, cfg.combat.loot_delay)

    # ПЕРЕМЕЩЕНИЕ ------------------------------------------------------------
    def _enter_move(self, now: float, cfg: Config, frame) -> None:
        if cfg.movement.mode == "none":
            self._enter_search(now, delay=max(1.0, cfg.combat.search_delay * 3))
            return
        target = self.nav.begin(now, cfg, frame.width, frame.height)
        if target is None:
            self._enter_search(now, delay=1.0)
            return
        self._set_mode(Mode.MOVE)
        self.arrived_wait_until = 0.0
        self.next_move_tab = now + cfg.movement.tab_interval
        self.vision.motion.reset()

    def _do_move(self, now, vs: VisionState, cfg: Config, frame) -> None:
        m = cfg.movement
        if vs.target_present:
            log.info("Цель найдена во время движения")
            self.nav.reset_move()
            self._begin_attack(now, vs, cfg, new_target=True)
            return
        if self.arrived_wait_until:
            if now >= self.arrived_wait_until:
                self.arrived_wait_until = 0.0
                self._enter_search(now, immediate=True)
            return
        if m.tab_while_moving and vs.target_supported and now >= self.next_move_tab:
            self.next_move_tab = now + max(0.5, m.tab_interval)
            self.ctrl.press(cfg.combat.target_key)
            return
        if self.nav.update(now, vs.motion, cfg) == "arrived":
            wait = self.nav.target.wait if self.nav.target else 0.0
            log.debug("Точка достигнута")
            self.nav.reset_move()
            self.search_failures = 0
            if wait > 0:
                self.arrived_wait_until = now + wait
            else:
                self._enter_search(now, immediate=True)

    # ОТДЫХ ------------------------------------------------------------------
    def _enter_rest(self, now: float, vs: VisionState, cfg: Config) -> None:
        if not self.ctrl.press(cfg.potions.rest_key):
            self.rest_block_until = now + 5.0
            return
        self._set_mode(Mode.REST)
        self.rest_started = now
        self.rest_hp_max = vs.hp or 0.0
        self.stats.rests += 1
        log.info("Отдых для восстановления HP/MP")

    def _do_rest(self, now, vs: VisionState, cfg: Config, frame) -> None:
        p = cfg.potions
        if vs.target_present:
            self.rest_block_until = now + 15.0
            self._begin_attack(now, vs, cfg, new_target=True)
            return
        hp = vs.hp if vs.hp is not None else 0.0
        mp = vs.mp if vs.mp is not None else 100.0
        self.rest_hp_max = max(self.rest_hp_max, hp)
        if hp < self.rest_hp_max - 5.0:
            log.warning("Во время отдыха падает HP — нас атакуют")
            self.rest_block_until = now + 15.0
            self._enter_search(now, immediate=True)
            return
        done = hp >= p.rest_until_hp and mp >= p.rest_until_mp
        if done or now - self.rest_started >= p.rest_max_time:
            self.rest_block_until = now + 10.0
            log.info("Отдых завершён (HP %.0f%%, MP %.0f%%)", hp, mp)
            self._enter_search(now, immediate=True)

    # ------------------------------------------------------------------
    # Статус для интерфейса
    # ------------------------------------------------------------------
    def _emit_status(self, frame=None, vs: Optional[VisionState] = None,
                     display_mode: Optional[Mode] = None, force: bool = False) -> None:
        if self.on_status is None:
            return
        now = self.clock.now()
        if not force and now - self.last_status_emit < 0.1:
            return
        self.last_status_emit = now
        cfg = self.store.get()
        vs = vs or self.last_vs
        mode = display_mode or self.mode
        if mode in (Mode.SEARCH, Mode.ATTACK, Mode.MOVE) and now < self.recover_flash_until:
            mode = Mode.RECOVER
        preview = None
        if frame is not None and cfg.show_preview and now - self.last_preview >= 0.5:
            self.last_preview = now
            try:
                img = draw_overlay(frame.image, cfg, vs)
                scale = 480.0 / max(1, img.shape[1])
                preview = cv2.resize(img, (480, max(1, int(img.shape[0] * scale))),
                                     interpolation=cv2.INTER_AREA)
            except Exception:
                preview = None
        status = BotStatus(
            run_state=self.run_state,
            mode=mode,
            mode_label=MODE_LABELS.get(mode, str(mode)),
            hp=vs.hp, mp=vs.mp,
            target_supported=vs.target_supported,
            target_present=vs.target_present,
            target_hp=vs.target_hp,
            window_ok=self.window_ok,
            foreground=self.foreground,
            stats=BotStats(**vars(self.stats)),
            uptime=max(0.0, now - self.started_at) if self.run_state != RunState.STOPPED else 0.0,
            message=self.message,
            motion=vs.motion,
            preview=preview,
        )
        try:
            self.on_status(status)
        except Exception:
            pass
