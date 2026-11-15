"""Перемещение персонажа: маршрут из точек, случайное блуждание, запись маршрута.

Персонаж ходит кликом ЛКМ по земле. Точка маршрута — это место клика в окне
игры (координаты клиентской области). Если окно другого размера, чем при
записи, точки масштабируются пропорционально.

Прибытие определяется по мини-карте: пока персонаж идёт, изображение
мини-карты меняется; когда оно стабильно ``stable_time`` секунд — персонаж
остановился. Без мини-карты используется максимальное время перехода.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import winapi
from .config import Config, MovementConfig, RoutePoint
from .logger import log


@dataclass
class MoveTarget:
    x: int
    y: int
    wait: float
    label: str


class Navigator:
    def __init__(self, controller, clock):
        self.ctrl = controller
        self.clock = clock
        self.index = 0
        self.direction = 1
        self.heading = random.uniform(0, 2 * math.pi)
        self.moves = 0
        self.reset_move()

    def reset_move(self) -> None:
        self.target: Optional[MoveTarget] = None
        self.started = 0.0
        self.stable_since: Optional[float] = None

    def reset_route(self) -> None:
        self.index = 0
        self.direction = 1

    # ------------------------------------------------------------------
    @staticmethod
    def safe_zone(m: MovementConfig, w: int, h: int) -> Tuple[int, int, int, int]:
        x0 = int(w * m.margin_left)
        x1 = int(w * (1 - m.margin_right))
        y0 = int(h * m.margin_top)
        y1 = int(h * (1 - m.margin_bottom))
        if x1 - x0 < 20 or y1 - y0 < 20:
            return (int(w * 0.1), int(h * 0.15), int(w * 0.9), int(h * 0.8))
        return (x0, y0, x1, y1)

    def _route_point(self, cfg: Config, w: int, h: int) -> Optional[MoveTarget]:
        pts: List[RoutePoint] = cfg.movement.points
        if not pts:
            return None
        n = len(pts)
        if self.index >= n or self.index < 0:
            self.index = 0
        i = self.index
        p = pts[i]
        sx = w / max(1, cfg.regions.reference_width)
        sy = h / max(1, cfg.regions.reference_height)
        target = MoveTarget(int(p.x * sx), int(p.y * sy), p.wait, f"точка {i + 1}/{n}")
        # Следующий индекс
        if n == 1:
            self.index = 0
        elif cfg.movement.loop_mode == "pingpong":
            if not 0 <= i + self.direction < n:
                self.direction = -self.direction
            self.index = i + self.direction
        else:
            self.index = (i + 1) % n
        return target

    def _roam_point(self, cfg: Config, w: int, h: int) -> MoveTarget:
        m = cfg.movement
        cx, cy = cfg.regions.character.x, cfg.regions.character.y
        sx = w / max(1, cfg.regions.reference_width)
        sy = h / max(1, cfg.regions.reference_height)
        cx, cy = int(cx * sx), int(cy * sy)
        x0, y0, x1, y1 = self.safe_zone(m, w, h)
        r_min = max(30, min(m.roam_min_radius, m.roam_max_radius))
        r_max = max(r_min + 10, m.roam_max_radius)
        for _ in range(25):
            # Блуждание «с инерцией»: направление меняется плавно
            ang = self.heading + random.uniform(-1.2, 1.2)
            r = random.uniform(r_min, r_max)
            x = int(cx + r * math.cos(ang))
            y = int(cy + r * math.sin(ang) * 0.6)   # перспектива: по вертикали короче
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.heading = ang
                return MoveTarget(x, y, 0.0, "случайная точка")
            self.heading += math.pi / 3
        x = min(max(cx + random.randint(-r_min, r_min), x0), x1)
        y = min(max(cy - r_min // 2, y0), y1)
        return MoveTarget(x, y, 0.0, "случайная точка")

    def next_target(self, cfg: Config, w: int, h: int) -> Optional[MoveTarget]:
        mode = cfg.movement.mode
        if mode == "route":
            t = self._route_point(cfg, w, h)
            if t is not None:
                return t
            return self._roam_point(cfg, w, h)
        if mode == "roam":
            return self._roam_point(cfg, w, h)
        return None

    # ------------------------------------------------------------------
    def begin(self, now: float, cfg: Config, w: int, h: int) -> Optional[MoveTarget]:
        """Выбрать точку и кликнуть по ней. None — перемещение отключено/не удалось."""
        self.reset_move()
        t = self.next_target(cfg, w, h)
        if t is None:
            return None
        if not self.ctrl.click(t.x, t.y, "left", jitter=cfg.movement.click_jitter):
            return None
        self.target = t
        self.started = now
        self.moves += 1
        log.info("Перемещение: %s (%d, %d)", t.label, t.x, t.y)
        return t

    def update(self, now: float, motion: Optional[float], cfg: Config) -> str:
        """Вернуть 'moving' или 'arrived'."""
        m = cfg.movement
        elapsed = now - self.started
        if elapsed >= m.max_travel_time:
            return "arrived"
        if elapsed < m.min_travel_time:
            return "moving"
        if not m.detect_arrival or motion is None:
            return "moving"
        if motion < cfg.vision.motion_threshold:
            if self.stable_since is None:
                self.stable_since = now
            if now - self.stable_since >= m.stable_time:
                return "arrived"
        else:
            self.stable_since = None
        return "moving"


class RouteRecorder:
    """Запись точек маршрута кликами ЛКМ по окну игры (опрос состояния кнопки)."""

    def __init__(self, window):
        self.window = window
        self.active = False
        self._was_down = False

    def start(self) -> None:
        self.active = True
        self._was_down = winapi.is_key_down(winapi.VK_LBUTTON)

    def stop(self) -> None:
        self.active = False

    def cursor_client_point(self) -> Optional[Tuple[int, int]]:
        """Положение курсора в координатах клиентской области окна игры."""
        rect = self.window.client_rect()
        pos = winapi.get_cursor_pos()
        if rect is None or pos is None:
            return None
        left, top, w, h = rect
        x, y = pos[0] - left, pos[1] - top
        if 0 <= x < w and 0 <= y < h:
            return (x, y)
        return None

    def poll(self) -> Optional[Tuple[int, int]]:
        """Вызывать по таймеру. Возвращает точку при новом клике по окну игры."""
        if not self.active:
            return None
        down = winapi.is_key_down(winapi.VK_LBUTTON)
        clicked = down and not self._was_down
        self._was_down = down
        if not clicked or not self.window.is_foreground():
            return None
        return self.cursor_client_point()
