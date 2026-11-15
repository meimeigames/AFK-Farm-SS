"""Распознавание на реальных кадрах игры.

* screenshot_1280x800.jpg — скриншот: HP 12359/12359 (100%), MP 4806/4929 (97.5%).
* frame_target.jpg — кадр видео: HP 37603/43503 (86.4%), MP 12812/13366 (95.9%),
  выбрана цель «Курама», HP цели 73%.
* frame_notarget.jpg — кадр видео без выбранной цели, HP/MP полные.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from afkfarm import vision
from afkfarm.config import Config, HSVRange, Rect

DATA = Path(__file__).parent / "data"
TOL = 4.5   # допустимая погрешность, % (JPEG + масштабирование видео)


def img(name):
    im = cv2.imread(str(DATA / name))
    assert im is not None, name
    return im


@pytest.fixture
def cfg():
    return Config()


def test_screenshot_hp_mp(cfg):
    im = img("screenshot_1280x800.jpg")
    hp = vision.measure_bar(im, cfg.regions.hp_bar, cfg.vision.hp_color)
    mp = vision.measure_bar(im, cfg.regions.mp_bar, cfg.vision.mp_color)
    assert hp.ok and abs(hp.percent - 100.0) <= TOL, hp
    assert mp.ok and abs(mp.percent - 97.5) <= TOL, mp


def test_autodetect_player_bars(cfg):
    im = img("screenshot_1280x800.jpg")
    found = vision.detect_player_bars(im, cfg.vision.hp_color, cfg.vision.mp_color)
    assert found is not None
    hp_r, mp_r = found
    assert abs(hp_r.x - 288) <= 3 and abs(hp_r.y - 752) <= 3 and abs(hp_r.w - 116) <= 4
    assert mp_r.y > hp_r.y


def test_video_frame_vitals_and_target(cfg):
    im = img("frame_target.jpg")
    track_hp = Rect(332, 680, 102, 8)
    track_mp = Rect(332, 690, 102, 8)
    hp = vision.measure_bar(im, track_hp, cfg.vision.hp_color, empty_v_max=100)
    mp = vision.measure_bar(im, track_mp, cfg.vision.mp_color, empty_v_max=100)
    assert hp.ok and abs(hp.percent - 86.4) <= TOL, hp
    assert mp.ok and abs(mp.percent - 95.9) <= TOL, mp
    t = vision.measure_bar(im, Rect(620, 25, 79, 8), cfg.vision.target_color, empty_v_max=140)
    assert t.ok and abs(t.percent - 73.0) <= TOL, t


def test_target_absent(cfg):
    im = img("frame_notarget.jpg")
    t = vision.measure_bar(im, Rect(620, 25, 79, 8), cfg.vision.target_color, empty_v_max=140)
    assert not (t.ok and t.percent and t.percent > cfg.vision.target_min_percent)
    assert vision.detect_target_bar(im, cfg.vision.target_color) is None


def test_autodetect_target_bar(cfg):
    im = img("frame_target.jpg")
    r = vision.detect_target_bar(im, cfg.vision.target_color)
    assert r is not None
    assert abs(r.x - 620) <= 3 and abs(r.y - 25) <= 3


def test_minimap_autodetect(cfg):
    im = img("screenshot_1280x800.jpg")
    r = vision.detect_minimap(im)
    assert r is not None
    cx, cy = r.x + r.w / 2, r.y + r.h / 2
    assert abs(cx - 1005) <= 12 and abs(cy - 725) <= 12


def test_learn_color_from_full_bar(cfg):
    im = img("screenshot_1280x800.jpg")
    red = vision.learn_color(im, Rect(288, 752, 115, 9))
    blue = vision.learn_color(im, Rect(288, 764, 115, 8))
    assert red is not None and red.h_min > red.h_max      # красный — через 0
    assert blue is not None and 90 <= blue.h_min <= blue.h_max <= 135
    hp = vision.measure_bar(im, Rect(288, 752, 115, 9), red)
    assert hp.ok and hp.percent >= 95


def test_covered_bar_is_error_not_zero(cfg):
    im = img("screenshot_1280x800.jpg").copy()
    r = cfg.regions.hp_bar
    im[r.y - 5:r.y2 + 5, r.x - 5:r.x2 + 5] = (170, 180, 175)  # «окно» поверх полосы
    hp = vision.measure_bar(im, r, cfg.vision.hp_color, empty_v_max=cfg.vision.empty_v_max)
    assert hp.percent is None


@pytest.mark.parametrize("pct", [0, 5, 20, 37, 50, 64, 81, 99, 100])
def test_synthetic_bar_with_text(pct):
    im = np.zeros((30, 200, 3), np.uint8)
    im[:] = (60, 90, 60)
    rect = Rect(10, 10, 150, 10)
    cv2.rectangle(im, (10, 10), (159, 19), (20, 15, 15), -1)
    fw = int(round(150 * pct / 100))
    if fw:
        cv2.rectangle(im, (10, 10), (10 + fw - 1, 19), (40, 40, 220), -1)
    cv2.putText(im, "12345/67890", (40, 19), cv2.FONT_HERSHEY_PLAIN, 0.7, (255, 255, 255), 1)
    r = vision.measure_bar(im, rect, HSVRange(165, 10, 70, 255, 80, 255), empty_v_max=100)
    assert r.ok
    assert abs(r.percent - pct) <= 3.0, (pct, r)


def test_motion_detector():
    md = vision.MotionDetector()
    rng = np.random.default_rng(0)
    base = cv2.GaussianBlur((rng.random((200, 200)) * 255).astype(np.uint8), (0, 0), 2)
    frame = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    r = Rect(50, 50, 80, 80)
    assert md.update(frame, r) is None
    assert md.update(frame, r) < 0.5
    shifted = np.roll(frame, 6, axis=1)
    assert md.update(shifted, r) > 1.5


def test_vision_state_smoothing(cfg):
    v = vision.Vision()
    im = img("screenshot_1280x800.jpg")
    st = v.analyze(im, cfg)
    assert st.hp is not None and st.hp > 95
    assert st.target_supported is False


def test_motion_detector_with_time_interval():
    md = vision.MotionDetector(interval=0.3)
    rng = np.random.default_rng(1)
    base = cv2.GaussianBlur((rng.random((200, 400)) * 255).astype(np.uint8), (0, 0), 2)
    r = Rect(50, 50, 80, 80)
    t = 0.0
    vals = []
    for i in range(20):                      # «идём»: 2 пикселя за кадр 50 мс
        frame = cv2.cvtColor(np.roll(base, 2 * i, axis=1), cv2.COLOR_GRAY2BGR)
        vals.append(md.update(frame, r, t))
        t += 0.05
    assert vals[0] is None and vals[5] is None      # меньше 0.3 с истории
    assert vals[-1] is not None and vals[-1] > 2.0
    still = cv2.cvtColor(np.roll(base, 40, axis=1), cv2.COLOR_GRAY2BGR)
    for _ in range(10):                      # «стоим»
        v = md.update(still, r, t)
        t += 0.05
    assert v < 0.5
