"""Автоматическое использование зелий HP/MP.

Правила:
  * HP ниже порога — зелье HP, если оно не на перезарядке;
  * HP ниже критического порога — сначала «критическая» клавиша (если задана,
    например сильное зелье или умение лечения), затем повторная проверка
    готовности обычного зелья чаще обычного;
  * MP ниже порога — зелье MP;
  * между любыми зельями — минимальная пауза ``min_gap``;
  * перезарядка учитывается по таймеру и (опционально) по затемнению слота;
  * если зелье несколько раз подряд не дало эффекта — предупреждение в журнал.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .config import Config, PotionsConfig
from .logger import log, log_throttled
from .vision import VisionState

NEVER = -1e9


@dataclass
class _Pending:
    kind: str          # "hp" | "mp"
    at: float
    before: float


class PotionManager:
    def __init__(self, controller, clock):
        self.ctrl = controller
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self.next_check = 0.0
        self.last_any = NEVER
        self.last_hp = NEVER
        self.last_mp = NEVER
        self.last_crit = NEVER
        self.hp_used = 0
        self.mp_used = 0
        self.crit_used = 0
        self._pending: List[_Pending] = []
        self._no_effect = {"hp": 0, "mp": 0}

    # ------------------------------------------------------------------
    def _verify(self, now: float, vs: VisionState, p: PotionsConfig) -> None:
        keep = []
        for item in self._pending:
            if now - item.at < p.verify_time:
                keep.append(item)
                continue
            current = vs.hp if item.kind == "hp" else vs.mp
            if current is None:
                continue
            if current > item.before + 1.0 or current >= 99.0:
                self._no_effect[item.kind] = 0
            else:
                self._no_effect[item.kind] += 1
                if self._no_effect[item.kind] >= max(1, p.no_effect_warn):
                    name = "HP" if item.kind == "hp" else "MP"
                    log_throttled(logging.WARNING, f"potion-noeffect-{item.kind}",
                                  f"Зелье {name} не дало эффекта {self._no_effect[item.kind]} раз(а) "
                                  f"подряд — проверьте клавишу и наличие зелий.", 60)
        self._pending = keep

    def _slot_state(self, vs: VisionState, kind: str, p: PotionsConfig) -> Optional[bool]:
        if not p.visual_cooldown_check:
            return None
        return vs.hp_slot_ready if kind == "hp" else vs.mp_slot_ready

    def hp_ready(self, now: float, vs: VisionState, p: PotionsConfig, critical: bool) -> bool:
        if not p.hp_key:
            return False
        visual = self._slot_state(vs, "hp", p)
        if visual is False:
            return False                      # слот затемнён — перезарядка/нет зелий
        if now - self.last_hp >= p.hp_cooldown:
            return True
        # Критическое HP: слот выглядит готовым — перепроверяем чаще таймера
        return bool(critical and visual is True and now - self.last_hp >= max(0.5, p.critical_recheck))

    def mp_ready(self, now: float, vs: VisionState, p: PotionsConfig) -> bool:
        if not p.mp_key:
            return False
        visual = self._slot_state(vs, "mp", p)
        if visual is False:
            return False
        return now - self.last_mp >= p.mp_cooldown

    # ------------------------------------------------------------------
    def tick(self, now: float, vs: VisionState, cfg: Config) -> Optional[str]:
        """Проверить HP/MP и при необходимости выпить зелье.

        Возвращает описание действия или None. Нажимается не больше одной
        клавиши за вызов.
        """
        p = cfg.potions
        if now < self.next_check:
            return None
        self.next_check = now + max(0.05, p.check_interval)
        self._verify(now, vs, p)

        hp = vs.hp
        if hp is None or hp <= 0.5:
            return None          # ошибка распознавания или персонаж мёртв
        critical = hp < p.hp_critical_threshold

        gap = min(p.min_gap, 0.5) if critical else p.min_gap
        if now - self.last_any < gap:
            return None

        if p.hp_enabled and hp < p.hp_threshold:
            if (critical and p.hp_critical_key
                    and now - self.last_crit >= p.hp_critical_cooldown):
                if self.ctrl.press(p.hp_critical_key):
                    self.last_crit = self.last_any = now
                    self.crit_used += 1
                    self._pending.append(_Pending("hp", now, hp))
                    log.info("Критическое HP %.0f%% — клавиша %s", hp, p.hp_critical_key)
                    return "crit"
            if self.hp_ready(now, vs, p, critical):
                if self.ctrl.press(p.hp_key):
                    self.last_hp = self.last_any = now
                    self.hp_used += 1
                    self._pending.append(_Pending("hp", now, hp))
                    log.info("HP %.0f%% < %.0f%% — зелье HP (%s)", hp, p.hp_threshold, p.hp_key)
                    return "hp"

        mp = vs.mp
        if p.mp_enabled and mp is not None and mp < p.mp_threshold and self.mp_ready(now, vs, p):
            if self.ctrl.press(p.mp_key):
                self.last_mp = self.last_any = now
                self.mp_used += 1
                self._pending.append(_Pending("mp", now, mp))
                log.info("MP %.0f%% < %.0f%% — зелье MP (%s)", mp, p.mp_threshold, p.mp_key)
                return "mp"
        return None
