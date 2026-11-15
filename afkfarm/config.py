"""Настройки программы: dataclass-схема, значения по умолчанию, JSON-хранилище.

Конфиг публикуется для потока бота как неизменяемый снимок: интерфейс
собирает новый объект ``Config`` и передаёт его в ``ConfigStore.set``.
Бот на каждом такте берёт ``store.get()`` — так настройки меняются «на лету»
без перезапуска и без гонок данных.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import sys
import threading
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

def app_dir() -> Path:
    """Папка программы (рядом с .exe или корень проекта)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_config_path() -> Path:
    return app_dir() / "config.json"


# ---------------------------------------------------------------------------
# Базовые типы
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    """Прямоугольник в пикселях клиентской области окна игры."""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def is_valid(self) -> bool:
        return self.w > 0 and self.h > 0

    def as_tuple(self):
        return (self.x, self.y, self.w, self.h)

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h


@dataclass
class Point:
    x: int = 0
    y: int = 0


@dataclass
class HSVRange:
    """Диапазон цвета в HSV (шкала OpenCV: H 0..179, S/V 0..255).

    Если ``h_min > h_max`` — диапазон «переходит через ноль» (красный цвет).
    """
    h_min: int = 0
    h_max: int = 179
    s_min: int = 0
    s_max: int = 255
    v_min: int = 0
    v_max: int = 255


def red_range() -> HSVRange:
    return HSVRange(h_min=165, h_max=10, s_min=70, s_max=255, v_min=80, v_max=255)


def blue_range() -> HSVRange:
    return HSVRange(h_min=95, h_max=130, s_min=60, s_max=255, v_min=80, v_max=255)


# ---------------------------------------------------------------------------
# Разделы настроек
# ---------------------------------------------------------------------------

@dataclass
class WindowConfig:
    title: str = "7th Element|Седьмой элемент|Element"  # часть заголовка окна (варианты через «|»)
    require_foreground: bool = True     # действовать только когда окно игры активно
    activate_on_start: bool = True      # активировать окно игры при старте
    start_delay: float = 2.0            # задержка перед началом работы, с


@dataclass
class RegionsConfig:
    """Области интерфейса (координаты клиентской области окна).

    Значения по умолчанию сняты со скриншота 1280x800.
    """
    reference_width: int = 1280
    reference_height: int = 800
    hp_bar: Rect = field(default_factory=lambda: Rect(288, 752, 116, 9))
    mp_bar: Rect = field(default_factory=lambda: Rect(288, 764, 116, 9))
    target_bar: Rect = field(default_factory=Rect)        # калибруется при выбранной цели
    target_frame: Rect = field(default_factory=Rect)      # опц. шаблон рамки цели
    minimap: Rect = field(default_factory=lambda: Rect(970, 683, 76, 76))
    hotbar: Rect = field(default_factory=lambda: Rect(460, 675, 345, 105))
    hp_potion_slot: Rect = field(default_factory=Rect)
    mp_potion_slot: Rect = field(default_factory=Rect)
    character: Point = field(default_factory=lambda: Point(640, 640))


@dataclass
class VisionConfig:
    hp_color: HSVRange = field(default_factory=red_range)
    mp_color: HSVRange = field(default_factory=blue_range)
    target_color: HSVRange = field(default_factory=red_range)
    column_threshold: float = 0.25      # доля пикселей столбца, чтобы считать его заполненным
    min_confidence: float = 0.65        # ниже — показание считается ошибкой распознавания
    empty_v_max: int = 100              # макс. яркость пустой части полосы HP/MP (иначе — перекрыта)
    target_empty_v_max: int = 140       # то же для полосы цели
    smoothing: int = 3                  # медиана по N последним кадрам
    target_min_percent: float = 0.8     # минимальное заполнение полосы цели, чтобы считать цель выбранной
    target_frame_threshold: float = 0.70  # порог совпадения шаблона рамки цели
    use_target_frame_template: bool = False
    motion_threshold: float = 1.5       # порог изменения мини-карты (движение), %
    potion_slot_ready_ratio: float = 0.75  # яркость слота / эталон ниже — перезарядка
    hp_slot_ref: float = 0.0            # эталонная яркость слота зелья HP (0 = нет)
    mp_slot_ref: float = 0.0


@dataclass
class Skill:
    enabled: bool = True
    name: str = "Умение"
    key: str = ""
    cooldown: float = 5.0       # перезарядка умения, с
    cast_time: float = 0.5      # время каста (другие действия ждут), с
    min_mp: float = 0.0         # не использовать, если MP ниже, %
    min_target_hp: float = 0.0  # использовать только если HP цели выше, % (0 = всегда)


@dataclass
class Buff:
    enabled: bool = True
    name: str = "Бафф"
    key: str = ""
    interval: float = 300.0     # как часто обновлять, с
    cast_time: float = 1.0
    in_combat: bool = False     # разрешено ли использовать во время боя


SKILL_PERIOD = 5.0   # по умолчанию каждое умение 1–8 нажимается раз в 5 с


def default_skills() -> List[Skill]:
    """Цифры 1–8 периодически во время боя (перезарядку можно поменять под свои умения)."""
    return [Skill(True, f"Умение {i}", str(i), SKILL_PERIOD, 0.3, 0.0, 0.0) for i in range(1, 9)]


def _old_default_skills() -> List[Skill]:
    return [
        Skill(True, "Умение 1", "1", 3.0, 0.6, 5.0, 0.0),
        Skill(True, "Умение 2", "2", 6.0, 0.6, 10.0, 0.0),
        Skill(False, "Умение 3", "3", 10.0, 0.8, 15.0, 30.0),
    ]


@dataclass
class CombatConfig:
    # Режим боя:
    #   simple — нет полосы HP цели → клавиша выбора цели; полоса есть → атака без остановки,
    #            пока полоса не исчезнет. Без поиска по маршруту и перемещения.
    #   full   — поиск, умения, застревание, перемещение по маршруту.
    mode: str = "simple"
    target_key: str = "TAB"
    attack_key: str = "F"
    clear_target_key: str = "ESC"
    simple_attack_interval: float = 0.3  # простой режим: как часто жать атаку, пока цель жива
    simple_tab_interval: float = 0.6     # простой режим: как часто жать выбор цели, пока полосы нет
    simple_use_skills: bool = True       # простой режим: умения 1–8 периодически во время боя
    simple_reset_after: float = 0.0      # HP цели не меняется N с → сброс цели (0 — никогда)
    action_delay: float = 0.35          # минимальная пауза между боевыми нажатиями, с
    attack_repeat_interval: float = 3.0  # повторное нажатие атаки, если умения не готовы
    search_delay: float = 0.8           # пауза между попытками поиска цели, с
    target_confirm_delay: float = 0.35  # ожидание появления рамки цели после TAB, с
    max_search_attempts: int = 4        # затем — переход к перемещению
    target_lost_confirm: float = 0.25   # цель должна отсутствовать столько, чтобы считать её убитой
    stuck_timeout: float = 12.0         # HP цели не меняется N с — сменить цель
    max_fight_time: float = 120.0       # максимальная длительность боя с одной целью
    max_stuck_in_row: int = 3           # столько «застреваний» подряд — сменить место
    blind_fight_time: float = 8.0       # слепой режим (без полосы цели): длительность цикла атаки
    loot_key: str = ""
    loot_presses: int = 0
    loot_delay: float = 0.3
    skills: List[Skill] = field(default_factory=default_skills)
    buffs: List[Buff] = field(default_factory=list)


@dataclass
class PotionsConfig:
    check_interval: float = 0.3         # интервал проверки HP/MP, с
    min_gap: float = 1.0                # минимальная пауза между любыми зельями, с
    hp_enabled: bool = True
    hp_key: str = "0"
    hp_threshold: float = 50.0
    hp_cooldown: float = 10.0
    hp_critical_threshold: float = 30.0
    hp_critical_key: str = ""           # опц.: второе зелье/умение лечения
    hp_critical_cooldown: float = 30.0
    critical_recheck: float = 1.0       # как часто перепроверять готовность при критическом HP
    mp_enabled: bool = True
    mp_key: str = "9"
    mp_threshold: float = 50.0
    mp_cooldown: float = 10.0
    visual_cooldown_check: bool = False  # проверять затемнение слота зелья
    verify_time: float = 3.0            # через сколько секунд проверять эффект зелья
    no_effect_warn: int = 3             # предупреждение после N зелий без эффекта
    # Отдых (восстановление вне боя)
    rest_enabled: bool = False
    rest_key: str = ""
    rest_start_hp: float = 45.0
    rest_start_mp: float = 20.0
    rest_until_hp: float = 95.0
    rest_until_mp: float = 95.0
    rest_max_time: float = 60.0
    # Смерть персонажа
    death_action: str = "stop"          # stop | pause
    death_confirm_time: float = 3.0


@dataclass
class RoutePoint:
    x: int = 0
    y: int = 0
    wait: float = 0.0       # пауза после прибытия, с


@dataclass
class MovementConfig:
    mode: str = "roam"              # route | roam | none
    points: List[RoutePoint] = field(default_factory=list)
    loop_mode: str = "loop"         # loop | pingpong
    max_travel_time: float = 8.0    # максимум ожидания прибытия, с
    min_travel_time: float = 0.8
    detect_arrival: bool = True     # определять остановку по мини-карте
    stable_time: float = 0.7        # мини-карта неподвижна столько — прибыли
    tab_while_moving: bool = True   # искать цели во время движения
    tab_interval: float = 1.5
    roam_min_radius: int = 120
    roam_max_radius: int = 320
    margin_left: float = 0.12       # безопасная зона кликов (доли окна)
    margin_right: float = 0.12
    margin_top: float = 0.18
    margin_bottom: float = 0.22
    click_jitter: int = 4           # разброс клика, пикс.


@dataclass
class HotkeysConfig:
    start: str = "F10"
    pause: str = "F11"
    stop: str = "F12"
    record_point: str = "F8"


@dataclass
class SafetyConfig:
    input_mode: str = "scancode"        # scancode | vk
    key_hold: float = 0.08              # длительность удержания клавиши, с (при низком FPS — больше)
    min_input_gap: float = 0.06         # минимальная пауза между любыми нажатиями, с
    max_same_key_per_10s: int = 60      # защита от спама одной клавишей
    max_inputs_per_10s: int = 120       # общий лимит нажатий
    spam_pause_after: int = 15          # столько заблокированных нажатий подряд — пауза бота
    recognition_error_limit: int = 60   # кадров подряд с ошибкой распознавания — пауза
    tick_interval: float = 0.05         # период главного цикла, с
    watchdog_timeout: float = 10.0      # бот не отвечает N с — аварийная остановка


@dataclass
class Config:
    version: int = 4
    window: WindowConfig = field(default_factory=WindowConfig)
    regions: RegionsConfig = field(default_factory=RegionsConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    combat: CombatConfig = field(default_factory=CombatConfig)
    potions: PotionsConfig = field(default_factory=PotionsConfig)
    movement: MovementConfig = field(default_factory=MovementConfig)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    log_to_file: bool = True
    show_preview: bool = True


# ---------------------------------------------------------------------------
# Сериализация
# ---------------------------------------------------------------------------

def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    return obj


_HINTS_CACHE: dict = {}


def _hints(cls):
    if cls not in _HINTS_CACHE:
        _HINTS_CACHE[cls] = typing.get_type_hints(cls)
    return _HINTS_CACHE[cls]


def _convert(tp, value):
    origin = typing.get_origin(tp)
    if origin is typing.Union:  # Optional[X]
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if value is None:
            return None
        return _convert(args[0], value)
    if origin in (list, List):
        (inner,) = typing.get_args(tp) or (Any,)
        if not isinstance(value, list):
            raise ValueError("ожидался список")
        return [_convert(inner, v) for v in value]
    if dataclasses.is_dataclass(tp):
        return from_dict(tp, value)
    if tp is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "да")
        raise ValueError("ожидалось логическое значение")
    if tp is int:
        return int(round(float(value)))
    if tp is float:
        return float(value)
    if tp is str:
        return str(value)
    return value


def from_dict(cls, data: Any):
    """Создать dataclass из словаря. Неизвестные ключи игнорируются,
    отсутствующие или некорректные — берутся по умолчанию."""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    hints = _hints(cls)
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        try:
            setattr(obj, f.name, _convert(hints[f.name], data[f.name]))
        except (ValueError, TypeError):
            pass  # оставляем значение по умолчанию
    return obj


def clone(cfg: Config) -> Config:
    return copy.deepcopy(cfg)


def load_config(path: Optional[Path] = None) -> Config:
    path = Path(path) if path else default_config_path()
    if not path.exists():
        return Config()
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    cfg = from_dict(Config, data)
    return migrate(cfg, data)


def migrate(cfg: "Config", data: Any) -> "Config":
    """Обновить настройки, сохранённые старой версией программы."""
    old_version = data.get("version", 1) if isinstance(data, dict) else 1
    if old_version < 2:
        # v2: простой режим «F без остановки» — лимиты защиты от спама выше
        if cfg.safety.max_same_key_per_10s == 35:
            cfg.safety.max_same_key_per_10s = 60
        if cfg.safety.max_inputs_per_10s == 80:
            cfg.safety.max_inputs_per_10s = 120
    if old_version < 3:
        # v3: схема по умолчанию — F без остановки, умения 1–8, HP < 50% → 0, MP < 50% → 9.
        # Меняем только то, что пользователь не трогал (совпадает со старыми значениями).
        p = cfg.potions
        if (p.hp_key, p.hp_threshold, p.mp_key, p.mp_threshold) == ("9", 70.0, "0", 30.0):
            p.hp_key, p.hp_threshold = "0", 50.0
            p.mp_key, p.mp_threshold = "9", 50.0
            if p.hp_critical_threshold == 40.0:
                p.hp_critical_threshold = 30.0
        if cfg.combat.skills == _old_default_skills():
            cfg.combat.skills = default_skills()
            cfg.combat.simple_use_skills = True
    if old_version < 4:
        # v4: 40 мс удержания игра могла не заметить при низком FPS
        if abs(cfg.safety.key_hold - 0.04) < 1e-9:
            cfg.safety.key_hold = 0.08
    cfg.version = Config().version
    return cfg


def save_config(cfg: Config, path: Optional[Path] = None) -> Path:
    path = Path(path) if path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(to_dict(cfg), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def save_route(points: List[RoutePoint], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"points": [to_dict(p) for p in points]}, fh, ensure_ascii=False, indent=2)


def load_route(path: Path) -> List[RoutePoint]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    raw = data.get("points", []) if isinstance(data, dict) else data
    return [from_dict(RoutePoint, p) for p in raw if isinstance(p, dict)]


# ---------------------------------------------------------------------------
# Потокобезопасное хранилище
# ---------------------------------------------------------------------------

class ConfigStore:
    """Хранит актуальный снимок настроек. Снимок после публикации не изменяется."""

    def __init__(self, cfg: Optional[Config] = None, path: Optional[Path] = None):
        self._lock = threading.Lock()
        self._cfg = cfg or Config()
        self._version = 0
        self.path = Path(path) if path else default_config_path()

    def get(self) -> Config:
        with self._lock:
            return self._cfg

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def set(self, cfg: Config, save: bool = True) -> None:
        snapshot = clone(cfg)
        with self._lock:
            self._cfg = snapshot
            self._version += 1
        if save:
            try:
                save_config(snapshot, self.path)
            except OSError:
                pass
