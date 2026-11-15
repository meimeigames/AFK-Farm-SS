"""Боевая логика: ротация умений, баффы, отслеживание прогресса по цели."""

from __future__ import annotations

from typing import Dict, Optional

from .config import Buff, Config, Skill
from .logger import log
from .vision import VisionState

NEVER = -1e9
MIN_SKILL_COOLDOWN = 0.3   # защита от нулевой перезарядки


def skill_id(index: int, skill: Skill) -> str:
    return f"{index}:{skill.key.upper()}"


def buff_id(index: int, buff: Buff) -> str:
    return f"{index}:{buff.key.upper()}"


class CombatManager:
    def __init__(self, controller, clock):
        self.ctrl = controller
        self.clock = clock
        self.skill_ready: Dict[str, float] = {}
        self.buff_ready: Dict[str, float] = {}
        self.skills_used = 0
        self.buffs_used = 0
        self.reset_fight()
        self.busy_until = NEVER
        self.last_action = NEVER

    # ------------------------------------------------------------------
    # Прогресс по цели
    # ------------------------------------------------------------------
    def reset_fight(self) -> None:
        self.fight_start = 0.0
        self.last_attack = NEVER
        self.initial_target_hp: Optional[float] = None
        self.min_target_hp: Optional[float] = None
        self.last_target_hp: Optional[float] = None
        self.last_progress = 0.0

    def start_fight(self, now: float, target_hp: Optional[float]) -> None:
        self.reset_fight()
        self.fight_start = now
        self.last_progress = now
        self.initial_target_hp = target_hp
        self.min_target_hp = target_hp
        self.last_target_hp = target_hp

    def update_target(self, now: float, target_hp: Optional[float]) -> None:
        if target_hp is None:
            return
        self.last_target_hp = target_hp
        if self.initial_target_hp is None:
            self.initial_target_hp = target_hp
        if self.min_target_hp is None or target_hp < self.min_target_hp - 0.9:
            self.min_target_hp = target_hp
            self.last_progress = now

    def is_stuck(self, now: float, cfg: Config) -> bool:
        return now - self.last_progress > cfg.combat.stuck_timeout

    def fight_too_long(self, now: float, cfg: Config) -> bool:
        return now - self.fight_start > cfg.combat.max_fight_time

    def damage_dealt(self) -> bool:
        if self.initial_target_hp is None or self.min_target_hp is None:
            return False
        return (self.initial_target_hp - self.min_target_hp) >= 3.0 or self.min_target_hp <= 35.0

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------
    def can_act(self, now: float, cfg: Config) -> bool:
        return now >= self.busy_until and now - self.last_action >= cfg.combat.action_delay

    def press_attack(self, now: float, cfg: Config) -> bool:
        if self.ctrl.press(cfg.combat.attack_key):
            self.last_attack = now
            self.last_action = now
            return True
        return False

    def use_ready_skill(self, now: float, vs: VisionState, cfg: Config) -> bool:
        """Нажать первое готовое умение (без повтора атаки). True — умение нажато."""
        if not self.can_act(now, cfg):
            return False
        return bool(self._skill(now, vs, cfg))

    def rotation(self, now: float, vs: VisionState, cfg: Config) -> Optional[str]:
        """Одно боевое действие за вызов: умение по приоритету либо повтор атаки."""
        if not self.can_act(now, cfg):
            return None
        used = self._skill(now, vs, cfg)
        if used is not None:
            return used or None
        if now - self.last_attack >= cfg.combat.attack_repeat_interval:
            if self.press_attack(now, cfg):
                return "attack"
        return None

    def _skill(self, now: float, vs: VisionState, cfg: Config) -> Optional[str]:
        """Попытаться нажать умение. Строка — нажато; "" — нажатие не прошло; None — нечего нажимать."""
        mp = vs.mp
        for i, sk in enumerate(cfg.combat.skills):
            if not sk.enabled or not sk.key.strip():
                continue
            sid = skill_id(i, sk)
            if now < self.skill_ready.get(sid, NEVER):
                continue
            if sk.min_mp > 0 and (mp is None or mp < sk.min_mp):
                continue
            if sk.min_target_hp > 0 and vs.target_hp is not None and vs.target_hp < sk.min_target_hp:
                continue
            if self.ctrl.press(sk.key):
                self.skill_ready[sid] = now + max(MIN_SKILL_COOLDOWN, sk.cooldown)
                self.busy_until = now + max(0.0, sk.cast_time)
                self.last_action = now
                self.skills_used += 1
                log.debug("Умение «%s» (%s)", sk.name, sk.key)
                return f"skill:{sk.name}"
            return ""     # нажать не удалось (окно не активно / лимит) — ждём
        return None

    def due_buff(self, now: float, cfg: Config, in_combat: bool) -> Optional[int]:
        for i, b in enumerate(cfg.combat.buffs):
            if not b.enabled or not b.key.strip():
                continue
            if in_combat and not b.in_combat:
                continue
            if now >= self.buff_ready.get(buff_id(i, b), NEVER):
                return i
        return None

    def use_buff(self, now: float, cfg: Config, index: int) -> bool:
        b = cfg.combat.buffs[index]
        if not self.can_act(now, cfg):
            return False
        if self.ctrl.press(b.key):
            self.buff_ready[buff_id(index, b)] = now + max(5.0, b.interval)
            self.busy_until = now + max(0.0, b.cast_time)
            self.last_action = now
            self.buffs_used += 1
            log.info("Бафф «%s» (%s)", b.name, b.key)
            return True
        return False
