"""Простая симуляция игры для проверки логики бота без Windows.

Рисует полосы HP/MP, рамку цели и мини-карту и реагирует на «нажатия»
так же, как игра: TAB выбирает монстра, F атакует, зелья лечат и т.д.
"""

from __future__ import annotations

import math
from typing import List, Optional

import cv2
import numpy as np

from afkfarm.config import Config, Rect
from afkfarm.game_capture import Frame

W, H = 1280, 800
TARGET_BAR = Rect(610, 24, 80, 7)


def sim_config() -> Config:
    cfg = Config()
    cfg.regions.target_bar = TARGET_BAR
    cfg.window.start_delay = 0.0
    cfg.window.activate_on_start = False
    cfg.combat.skills[0].key = "1"
    cfg.combat.skills[1].key = "2"
    cfg.potions.hp_key = "9"
    cfg.potions.mp_key = "0"
    cfg.movement.mode = "roam"
    return cfg


class FakeGame:
    def __init__(self, monsters: int = 3, monster_dps_to_player: float = 4.0):
        self.hp = 100.0
        self.mp = 100.0
        self.target_hp: Optional[float] = None
        self.attacking = False
        self.monsters = monsters
        self.respawn = monsters
        self.player_dps = 9.0            # урон по монстру, %/с
        self.monster_dps = monster_dps_to_player
        self.moving_left = 0.0
        self.pos = np.array([0.0, 0.0])
        self.vel = np.array([0.0, 0.0])
        self.cursor = (0, 0)
        self.key_log: List[tuple] = []
        self.clicks: List[tuple] = []
        self.potion_cd = {"9": 0.0, "0": 0.0}
        self.t = 0.0
        self.unreachable_target = False
        self.hide_bars = False
        rng = np.random.default_rng(1)
        self.texture = (rng.random((400, 400)) * 255).astype(np.uint8)
        self.texture = cv2.GaussianBlur(self.texture, (0, 0), 3)

    # --- ввод -----------------------------------------------------------
    def on_key(self, name: str) -> None:
        self.key_log.append((round(self.t, 2), name))
        if name == "TAB":
            if self.monsters > 0:
                self.target_hp = 100.0
        elif name == "F":
            if self.target_hp is not None:
                self.attacking = True
                self.moving_left = 0.0
        elif name == "ESC":
            self.target_hp = None
            self.attacking = False
        elif name in ("1", "2"):
            if self.target_hp is not None and self.mp >= 5:
                self.mp -= 5
                if not self.unreachable_target:
                    self.target_hp -= 10
        elif name in ("9", "0"):
            if self.t >= self.potion_cd[name]:
                self.potion_cd[name] = self.t + 8.0
                if name == "9":
                    self.hp = min(100.0, self.hp + 35)
                else:
                    self.mp = min(100.0, self.mp + 40)

    def on_click(self, x: int, y: int) -> None:
        self.clicks.append((round(self.t, 2), x, y))
        self.moving_left = 2.0
        ang = math.atan2(y - 560, x - 640)
        self.vel = np.array([math.cos(ang), math.sin(ang)]) * 25.0
        self.attacking = False

    # --- время ------------------------------------------------------------
    def advance(self, dt: float) -> None:
        self.t += dt
        if self.moving_left > 0:
            step = min(dt, self.moving_left)
            self.pos += self.vel * step
            self.moving_left -= step
            if self.moving_left <= 0:
                self.monsters = self.respawn   # новое место — новые монстры
        if self.target_hp is not None:
            if self.attacking and not self.unreachable_target:
                self.target_hp -= self.player_dps * dt
            if self.attacking:
                self.hp -= self.monster_dps * dt
            if self.target_hp <= 0:
                self.target_hp = None
                self.attacking = False
                self.monsters -= 1
        self.hp = max(0.0, self.hp)

    # --- отрисовка -----------------------------------------------------------
    def render(self, cfg: Config) -> np.ndarray:
        img = np.full((H, W, 3), (70, 150, 90), np.uint8)
        reg = cfg.regions

        def bar(r: Rect, pct: float, color):
            cv2.rectangle(img, (r.x, r.y), (r.x2 - 1, r.y2 - 1), (25, 20, 20), -1)
            fw = int(round(r.w * max(0.0, min(100.0, pct)) / 100.0))
            if fw > 0:
                cv2.rectangle(img, (r.x, r.y), (r.x + fw - 1, r.y2 - 1), color, -1)
            # белый текст поверх полосы
            cv2.putText(img, f"{int(pct)}/100", (r.x + r.w // 3, r.y2 - 1),
                        cv2.FONT_HERSHEY_PLAIN, 0.6, (255, 255, 255), 1)

        if not self.hide_bars:
            bar(reg.hp_bar, self.hp, (40, 40, 210))
            bar(reg.mp_bar, self.mp, (210, 120, 40))
        if self.target_hp is not None:
            bar(TARGET_BAR, max(0.0, self.target_hp), (30, 30, 190))
        mm = reg.minimap
        ox = int(self.pos[0]) % 300
        oy = int(self.pos[1]) % 300
        tex = self.texture[oy:oy + mm.h, ox:ox + mm.w]
        img[mm.y:mm.y + mm.h, mm.x:mm.x + mm.w] = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
        return img


class FakeWindow:
    def __init__(self):
        self.foreground = True

    def locate(self):
        return 1

    def client_rect(self):
        return (0, 0, W, H)

    def is_foreground(self):
        return self.foreground

    def activate(self):
        return True


class FakeBackend:
    def __init__(self, game: FakeGame):
        self.game = game
        self.down = set()

    def key(self, info, up, use_scan):
        if up:
            self.down.discard(info.name)
        else:
            self.down.add(info.name)
            self.game.on_key(info.name)
        return True

    def move(self, x, y):
        self.game.cursor = (x, y)
        return True

    def button(self, button, up):
        if not up:
            self.game.on_click(*self.game.cursor)
        return True


class FakeCapture:
    def __init__(self, game: FakeGame, store, window: FakeWindow):
        self.game = game
        self.store = store
        self.window = window

    def grab(self):
        return Frame(image=self.game.render(self.store.get()), left=0, top=0,
                     timestamp=self.game.t, foreground=self.window.foreground)

    def close(self):
        pass
