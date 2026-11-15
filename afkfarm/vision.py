"""Компьютерное зрение: HP/MP персонажа, полоса HP цели, мини-карта, слоты зелий.

Принцип измерения полосы
------------------------
Для каждого столбца пикселей в области полосы считается доля пикселей нужного
цвета (HSV). Столбец «заполнен», если доля >= ``column_threshold``. Белый текст
поверх полосы («37603/43503») закрывает только середину столбца, а цветные
пиксели сверху и снизу остаются, поэтому текст не мешает. Небольшие разрывы
заполняются, после чего берётся длина непрерывной заливки от левого края.

Достоверность (confidence) — доля столбцов, совпадающих с идеальной формой
«заполнено слева, пусто справа». Если полосу перекрыла подсказка или окно,
форма нарушается — показание отбрасывается, а не превращается в ложный 0%.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import Config, HSVRange, Rect, data_dir

TARGET_TEMPLATE_FILE = "target_frame.png"


# ---------------------------------------------------------------------------
# Базовые операции
# ---------------------------------------------------------------------------

def clip_rect(rect: Rect, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    if rect is None or not rect.is_valid():
        return None
    x0 = max(0, rect.x)
    y0 = max(0, rect.y)
    x1 = min(width, rect.x + rect.w)
    y1 = min(height, rect.y + rect.h)
    if x1 - x0 < 2 or y1 - y0 < 1:
        return None
    return x0, y0, x1, y1


def crop(image: np.ndarray, rect: Rect) -> Optional[np.ndarray]:
    c = clip_rect(rect, image.shape[1], image.shape[0])
    if c is None:
        return None
    x0, y0, x1, y1 = c
    return image[y0:y1, x0:x1]


def hsv_mask(hsv: np.ndarray, rng: HSVRange) -> np.ndarray:
    """Булева маска пикселей, попадающих в диапазон (с переходом H через 0)."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sv = (s >= rng.s_min) & (s <= rng.s_max) & (v >= rng.v_min) & (v <= rng.v_max)
    if rng.h_min <= rng.h_max:
        hm = (h >= rng.h_min) & (h <= rng.h_max)
    else:
        hm = (h >= rng.h_min) | (h <= rng.h_max)
    return hm & sv


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    out = mask.copy()
    idx = np.flatnonzero(mask)
    if idx.size < 2:
        return out
    gaps = np.diff(idx)
    for i in np.flatnonzero((gaps > 1) & (gaps <= max_gap + 1)):
        out[idx[i]:idx[i + 1]] = True
    return out


# ---------------------------------------------------------------------------
# Измерение полос
# ---------------------------------------------------------------------------

@dataclass
class BarReading:
    percent: Optional[float]   # None — показание недостоверно
    raw_percent: float
    confidence: float
    fill_px: int
    width: int

    @property
    def ok(self) -> bool:
        return self.percent is not None


def measure_bar(image: np.ndarray, rect: Rect, rng: HSVRange,
                column_threshold: float = 0.25, min_confidence: float = 0.65,
                empty_v_max: int = 255) -> BarReading:
    sub = crop(image, rect)
    if sub is None or sub.size == 0:
        return BarReading(None, 0.0, 0.0, 0, 0)
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = hsv_mask(hsv, rng)
    w = mask.shape[1]
    col_frac = mask.mean(axis=0)
    filled_raw = col_frac >= column_threshold
    filled = _close_gaps(filled_raw, max(2, int(round(w * 0.06))))
    idx = np.flatnonzero(filled)
    if idx.size == 0:
        end = 0
    else:
        start_tol = max(2, int(round(w * 0.12)))
        if idx[0] <= start_tol:
            end = int(idx[0])
            while end < w and filled[end]:
                end += 1
            # Если почти дошли до правого края — считаем полную полосу
            if end >= w - max(1, int(round(w * 0.02))):
                end = w
        else:
            # Заливка не начинается слева — формы полосы нет
            end = 0
    raw = 100.0 * end / w
    # Достоверность: насколько маска похожа на «ступеньку» в точке end
    left_ok = filled_raw[:end].sum() if end else 0
    right_ok = (~filled_raw[end:]).sum() if end < w else 0
    confidence = float(left_ok + right_ok) / w
    percent = raw if confidence >= min_confidence else None
    # Незаполненная часть настоящей полосы — тёмный «трек». Если там светло,
    # полосу что-то перекрыло (окно, подсказка) — показание недостоверно.
    if percent is not None and end < w and empty_v_max < 255:
        empty_v = float(np.median(hsv[:, end:, 2]))
        if empty_v > empty_v_max:
            percent = None
    return BarReading(percent, raw, confidence, end, w)


# ---------------------------------------------------------------------------
# Обучение цвета
# ---------------------------------------------------------------------------

def learn_color(image: np.ndarray, rect: Rect, base: Optional[HSVRange] = None) -> Optional[HSVRange]:
    """Подобрать диапазон HSV по заполненной полосе (снимать при 100% HP/MP)."""
    sub = crop(image, rect)
    if sub is None:
        return None
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    sel = hsv[(hsv[:, 1] >= 60) & (hsv[:, 2] >= 60)]
    if len(sel) < max(10, 0.2 * len(hsv)):
        return None
    hue = sel[:, 0]
    # Медиана оттенка с учётом кругового характера (красный около 0/179)
    near_zero = np.mean((hue < 20) | (hue > 160)) > 0.5
    shifted = (hue + 90) % 180 if near_zero else hue
    h_med = float(np.median(shifted))
    h_spread = float(np.percentile(np.abs(shifted - h_med), 90))
    tol = int(np.clip(h_spread + 4, 8, 20))
    if near_zero:
        h_med = (h_med - 90) % 180
    h_min = int(round(h_med - tol)) % 180
    h_max = int(round(h_med + tol)) % 180
    s_lo = float(np.percentile(sel[:, 1], 10))
    v_lo = float(np.percentile(sel[:, 2], 10))
    return HSVRange(h_min=h_min, h_max=h_max,
                    s_min=int(max(40, s_lo * 0.6)), s_max=255,
                    v_min=int(max(50, v_lo * 0.6)), v_max=255)


# ---------------------------------------------------------------------------
# Автопоиск элементов интерфейса
# ---------------------------------------------------------------------------

def _trim_component(raw: np.ndarray, x: int, y: int, w: int, h: int,
                    thr: float = 0.4) -> Optional[Rect]:
    """Обрезать рамку компоненты по реальной заливке (убирает «хвосты» от рамок)."""
    box = raw[y:y + h, x:x + w]
    row_frac = box.mean(axis=1)
    good = row_frac >= max(thr, 0.7 * float(row_frac.max()))
    if not good.any():
        return None
    # Самая длинная непрерывная полоса строк (отсекает текст над/под полосой)
    best_len, best_start, start = 0, 0, None
    for i, g in enumerate(list(good) + [False]):
        if g and start is None:
            start = i
        elif not g and start is not None:
            if i - start > best_len:
                best_len, best_start = i - start, start
            start = None
    y0, y1 = best_start, best_start + best_len
    cols = np.flatnonzero(box[y0:y1].mean(axis=0) >= 0.5)
    if cols.size == 0:
        return None
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    return Rect(x + x0, y + y0, x1 - x0, y1 - y0)


def find_bar_candidates(image: np.ndarray, rng: HSVRange,
                        region: Optional[Tuple[int, int, int, int]] = None,
                        min_width: int = 30) -> List[Rect]:
    """Горизонтальные вытянутые области заданного цвета (кандидаты в полосы)."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    raw = hsv_mask(hsv, rng)
    if region is not None:
        x0, y0, x1, y1 = region
        limited = np.zeros_like(raw)
        limited[y0:y1, x0:x1] = raw[y0:y1, x0:x1]
        raw = limited
    mask = cv2.morphologyEx(raw.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)))
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    rawf = raw.astype(np.float32)
    out: List[Rect] = []
    for i in range(1, n):
        x, y, w, h, _area = (int(v) for v in stats[i])
        if w < min_width:
            continue
        r = _trim_component(rawf, x, y, w, h)
        if r is None or r.w < min_width or not (3 <= r.h <= 30) or r.w / r.h < 5:
            continue
        fill = float(rawf[r.y:r.y2, r.x:r.x2].mean())
        if fill >= 0.6:
            out.append(r)
    return out


def detect_player_bars(image: np.ndarray, hp_rng: HSVRange, mp_rng: HSVRange
                       ) -> Optional[Tuple[Rect, Rect]]:
    """Найти пару «красная полоса над синей» в нижней половине экрана.

    Лучше всего работает при полных HP и MP.
    """
    H, W = image.shape[:2]
    region = (0, int(H * 0.45), int(W * 0.75), H)
    reds = find_bar_candidates(image, hp_rng, region)
    blues = find_bar_candidates(image, mp_rng, region)
    best = None
    best_score = -1.0
    for r in reds:
        for b in blues:
            gap = b.y - (r.y + r.h)
            if not (-2 <= gap <= max(16, 2 * r.h)):
                continue
            overlap = min(r.x2, b.x2) - max(r.x, b.x)
            if overlap < 0.6 * min(r.w, b.w):
                continue
            score = overlap + 0.01 * r.y
            if score > best_score:
                best_score = score
                best = (r, b)
    if best is None:
        return None
    r, b = best
    x0 = max(r.x, b.x)
    x1 = max(r.x2, b.x2)
    return Rect(x0, r.y, x1 - x0, r.h), Rect(x0, b.y, x1 - x0, b.h)


def detect_target_bar(image: np.ndarray, rng: HSVRange) -> Optional[Rect]:
    """Найти полосу HP цели в верхней центральной части экрана."""
    H, W = image.shape[:2]
    region = (int(W * 0.33), 0, int(W * 0.67), int(H * 0.12))
    cands = [c for c in find_bar_candidates(image, rng, region, min_width=25) if c.h >= 4]
    if not cands:
        return None
    cands.sort(key=lambda r: (r.w * r.h), reverse=True)
    return cands[0]


def detect_minimap(image: np.ndarray) -> Optional[Rect]:
    """Найти круглую мини-карту в правой нижней четверти экрана."""
    H, W = image.shape[:2]
    x0, y0 = int(W * 0.6), int(H * 0.6)
    sub = image[y0:, x0:]
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    min_r = max(20, int(min(W, H) * 0.04))
    max_r = int(min(W, H) * 0.12)
    circle = None
    # Берём самый «уверенный» круг (первый в выдаче), снижая порог при необходимости
    for param2 in (60, 50, 40, 30):
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=30,
                                   param1=100, param2=param2, minRadius=min_r, maxRadius=max_r)
        if circles is not None and len(circles[0]):
            circle = circles[0][0]
            break
    if circle is None:
        return None
    cx, cy, r = (float(v) for v in circle)
    inner = int(r * 0.7)
    return Rect(int(cx + x0 - inner), int(cy + y0 - inner), 2 * inner, 2 * inner)


# ---------------------------------------------------------------------------
# Движение (по мини-карте) и слоты
# ---------------------------------------------------------------------------

class MotionDetector:
    """Сравнивает соседние кадры области (мини-карты). Возвращает изменение в %."""

    def __init__(self):
        self._prev: Optional[np.ndarray] = None
        self.last_value: Optional[float] = None

    def reset(self) -> None:
        self._prev = None
        self.last_value = None

    def update(self, image: np.ndarray, rect: Rect) -> Optional[float]:
        sub = crop(image, rect)
        if sub is None:
            self.reset()
            return None
        gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        if self._prev is None or self._prev.shape != gray.shape:
            self._prev = gray
            self.last_value = None
            return None
        diff = float(np.mean(cv2.absdiff(gray, self._prev))) / 255.0 * 100.0
        self._prev = gray
        self.last_value = diff
        return diff


def region_brightness(image: np.ndarray, rect: Rect) -> Optional[float]:
    sub = crop(image, rect)
    if sub is None:
        return None
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[..., 2]))


# ---------------------------------------------------------------------------
# Шаблон рамки цели
# ---------------------------------------------------------------------------

def template_path() -> Path:
    return data_dir() / TARGET_TEMPLATE_FILE


def save_target_template(image: np.ndarray, rect: Rect) -> bool:
    sub = crop(image, rect)
    if sub is None:
        return False
    return bool(cv2.imwrite(str(template_path()), sub))


class TemplateChecker:
    def __init__(self):
        self._tpl: Optional[np.ndarray] = None
        self._mtime = None

    def _load(self) -> Optional[np.ndarray]:
        p = template_path()
        if not p.exists():
            self._tpl = None
            return None
        mtime = p.stat().st_mtime
        if self._tpl is None or mtime != self._mtime:
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            self._tpl = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img is not None else None
            self._mtime = mtime
        return self._tpl

    def score(self, image: np.ndarray, rect: Rect, pad: int = 6) -> Optional[float]:
        tpl = self._load()
        if tpl is None or not rect.is_valid():
            return None
        area = Rect(rect.x - pad, rect.y - pad, rect.w + 2 * pad, rect.h + 2 * pad)
        sub = crop(image, area)
        if sub is None or sub.shape[0] < tpl.shape[0] or sub.shape[1] < tpl.shape[1]:
            return None
        gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        return float(res.max())


# ---------------------------------------------------------------------------
# Сводный анализ кадра
# ---------------------------------------------------------------------------

@dataclass
class VisionState:
    hp: Optional[float] = None          # сглаженный %
    mp: Optional[float] = None
    hp_raw: Optional[float] = None
    mp_raw: Optional[float] = None
    hp_conf: float = 0.0
    mp_conf: float = 0.0
    target_supported: bool = False      # откалибрована ли полоса цели
    target_present: bool = False
    target_hp: Optional[float] = None
    target_conf: float = 0.0
    frame_score: Optional[float] = None
    motion: Optional[float] = None
    hp_slot_ready: Optional[bool] = None
    mp_slot_ready: Optional[bool] = None
    size_mismatch: bool = False
    timestamp: float = 0.0

    @property
    def vitals_ok(self) -> bool:
        return self.hp is not None


class Vision:
    """Анализирует кадры, хранит историю для сглаживания."""

    def __init__(self):
        self._hp_hist: Deque[float] = collections.deque(maxlen=9)
        self._mp_hist: Deque[float] = collections.deque(maxlen=9)
        self.motion = MotionDetector()
        self.templates = TemplateChecker()

    def reset(self) -> None:
        self._hp_hist.clear()
        self._mp_hist.clear()
        self.motion.reset()

    @staticmethod
    def _smooth(hist: Deque[float], value: Optional[float], n: int) -> Optional[float]:
        if value is None:
            return float(np.median(list(hist)[-n:])) if hist else None
        hist.append(value)
        return float(np.median(list(hist)[-max(1, n):]))

    def analyze(self, image: np.ndarray, cfg: Config, timestamp: float = 0.0,
                track_motion: bool = True) -> VisionState:
        v = cfg.vision
        reg = cfg.regions
        st = VisionState(timestamp=timestamp)
        H, W = image.shape[:2]
        st.size_mismatch = (W, H) != (reg.reference_width, reg.reference_height)

        hp = measure_bar(image, reg.hp_bar, v.hp_color, v.column_threshold, v.min_confidence,
                         v.empty_v_max)
        mp = measure_bar(image, reg.mp_bar, v.mp_color, v.column_threshold, v.min_confidence,
                         v.empty_v_max)
        st.hp_raw, st.hp_conf = hp.percent, hp.confidence
        st.mp_raw, st.mp_conf = mp.percent, mp.confidence
        n = max(1, int(v.smoothing))
        # При ошибке распознавания HP НЕ подставляем старое значение: это ошибка
        st.hp = self._smooth(self._hp_hist, hp.percent, n) if hp.ok else None
        st.mp = self._smooth(self._mp_hist, mp.percent, n) if mp.ok else None

        if reg.target_bar.is_valid():
            st.target_supported = True
            t = measure_bar(image, reg.target_bar, v.target_color,
                            v.column_threshold, v.min_confidence, v.target_empty_v_max)
            st.target_conf = t.confidence
            present = t.ok and t.percent is not None and t.percent >= v.target_min_percent
            if present and v.use_target_frame_template and reg.target_frame.is_valid():
                st.frame_score = self.templates.score(image, reg.target_frame)
                if st.frame_score is not None and st.frame_score < v.target_frame_threshold:
                    present = False
            st.target_present = bool(present)
            st.target_hp = t.percent if present else None

        if track_motion and reg.minimap.is_valid():
            st.motion = self.motion.update(image, reg.minimap)

        if v.hp_slot_ref > 0 and reg.hp_potion_slot.is_valid():
            b = region_brightness(image, reg.hp_potion_slot)
            st.hp_slot_ready = None if b is None else b >= v.hp_slot_ref * v.potion_slot_ready_ratio
        if v.mp_slot_ref > 0 and reg.mp_potion_slot.is_valid():
            b = region_brightness(image, reg.mp_potion_slot)
            st.mp_slot_ready = None if b is None else b >= v.mp_slot_ref * v.potion_slot_ready_ratio
        return st


# ---------------------------------------------------------------------------
# Отладочная картинка
# ---------------------------------------------------------------------------

REGION_COLORS: Dict[str, Tuple[int, int, int]] = {   # BGR
    "hp_bar": (60, 60, 255),
    "mp_bar": (255, 160, 40),
    "target_bar": (40, 200, 255),
    "target_frame": (200, 120, 255),
    "minimap": (80, 255, 80),
    "hotbar": (200, 200, 200),
    "hp_potion_slot": (120, 120, 255),
    "mp_potion_slot": (255, 200, 120),
}


def draw_overlay(image: np.ndarray, cfg: Config, state: Optional[VisionState] = None) -> np.ndarray:
    out = image.copy()
    reg = cfg.regions
    for name, color in REGION_COLORS.items():
        r: Rect = getattr(reg, name)
        if r.is_valid():
            cv2.rectangle(out, (r.x, r.y), (r.x2 - 1, r.y2 - 1), color, 1)
    if state is not None:
        def fill_line(r: Rect, pct: Optional[float], color):
            if pct is None or not r.is_valid():
                return
            x = r.x + int(round(r.w * pct / 100.0))
            cv2.line(out, (x, r.y - 3), (x, r.y2 + 2), color, 2)
        fill_line(reg.hp_bar, state.hp_raw, (255, 255, 255))
        fill_line(reg.mp_bar, state.mp_raw, (255, 255, 255))
        fill_line(reg.target_bar, state.target_hp, (255, 255, 255))
    p = reg.character
    cv2.drawMarker(out, (p.x, p.y), (0, 255, 255), cv2.MARKER_CROSS, 18, 2)
    return out
