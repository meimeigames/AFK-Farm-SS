"""Вкладки настроек: «Бой», «Зелья», «Настройки»."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                               QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QTableWidget, QVBoxLayout, QWidget)

from .. import winapi
from ..config import Buff, Config, Skill
from .widgets import (Binder, KeyEdit, check, combo, dspin, group, hint, ispin, row,
                      scroll_wrap)


class ConfigTab(QWidget):
    """База вкладки: ``load(cfg)`` заполняет виджеты, ``apply(cfg)`` пишет значения в cfg."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.binder = Binder(self.changed.emit)

    def load(self, cfg: Config) -> None:
        self.binder.load(cfg)

    def apply(self, cfg: Config) -> None:
        self.binder.apply(cfg)


# ---------------------------------------------------------------------------
# Таблица объектов (умения, баффы)
# ---------------------------------------------------------------------------

class ObjectTable(QWidget):
    """Редактируемая таблица списка dataclass-объектов с виджетами в ячейках."""

    changed = Signal()

    def __init__(self, columns: List[Dict[str, Any]], factory: Callable[[], Any],
                 parent=None, min_rows_height: int = 170):
        super().__init__(parent)
        self.columns = columns
        self.factory = factory
        self._loading = False
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c["title"] for c in columns])
        self.table.verticalHeader().setVisible(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(min_rows_height)
        hh = self.table.horizontalHeader()
        for i, c in enumerate(columns):
            if c.get("stretch"):
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            if c.get("tooltip"):
                self.table.horizontalHeaderItem(i).setToolTip(c["tooltip"])
        self.btn_add = QPushButton("＋ Добавить")
        self.btn_del = QPushButton("Удалить")
        self.btn_up = QPushButton("▲ Выше")
        self.btn_down = QPushButton("▼ Ниже")
        self.btn_add.clicked.connect(self._add)
        self.btn_del.clicked.connect(self._delete)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addWidget(row(self.btn_add, self.btn_del, 12, self.btn_up, self.btn_down))

    def _make_widget(self, col: Dict[str, Any], value: Any) -> QWidget:
        kind = col["kind"]
        if kind == "check":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.toggled.connect(self._emit)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 0, 0)
            hl.addWidget(w)
            holder.inner = w
            return holder
        if kind == "key":
            w = KeyEdit(str(value))
            w.keyChanged.connect(self._emit)
            return w
        if kind == "text":
            w = QLineEdit(str(value))
            w.editingFinished.connect(self._emit)
            return w
        if kind == "float":
            w = dspin(col.get("min", 0.0), col.get("max", 9999.0), col.get("step", 0.1),
                      col.get("decimals", 2), col.get("suffix", ""))
            w.setValue(float(value))
            w.valueChanged.connect(self._emit)
            return w
        if kind == "int":
            w = ispin(col.get("min", 0), col.get("max", 9999))
            w.setValue(int(value))
            w.valueChanged.connect(self._emit)
            return w
        raise ValueError(kind)

    @staticmethod
    def _read_widget(col: Dict[str, Any], w: QWidget) -> Any:
        kind = col["kind"]
        if kind == "check":
            return bool(w.inner.isChecked())
        if kind == "key":
            return w.key()
        if kind == "text":
            return w.text()
        if kind == "float":
            return float(w.value())
        if kind == "int":
            return int(w.value())
        return None

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def set_objects(self, objs: List[Any]) -> None:
        self._loading = True
        try:
            self.table.setRowCount(0)
            for obj in objs:
                self._append(obj)
        finally:
            self._loading = False

    def _append(self, obj: Any) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, col in enumerate(self.columns):
            self.table.setCellWidget(r, c, self._make_widget(col, getattr(obj, col["attr"])))
        self.table.resizeRowToContents(r)

    def objects(self) -> List[Any]:
        out = []
        for r in range(self.table.rowCount()):
            obj = self.factory()
            for c, col in enumerate(self.columns):
                setattr(obj, col["attr"], self._read_widget(col, self.table.cellWidget(r, c)))
            out.append(obj)
        return out

    def _add(self):
        self._append(self.factory())
        self.table.selectRow(self.table.rowCount() - 1)
        self.changed.emit()

    def _delete(self):
        r = self.table.currentRow()
        if r < 0:
            r = self.table.rowCount() - 1
        if r >= 0:
            self.table.removeRow(r)
            self.changed.emit()

    def _move(self, d: int):
        r = self.table.currentRow()
        objs = self.objects()
        if r < 0 or not 0 <= r + d < len(objs):
            return
        objs[r], objs[r + d] = objs[r + d], objs[r]
        self.set_objects(objs)
        self.table.selectRow(r + d)
        self.changed.emit()


# ---------------------------------------------------------------------------
# Вкладка «Бой»
# ---------------------------------------------------------------------------

SKILL_COLUMNS = [
    {"title": "Вкл", "attr": "enabled", "kind": "check"},
    {"title": "Название", "attr": "name", "kind": "text", "stretch": True},
    {"title": "Клавиша", "attr": "key", "kind": "key"},
    {"title": "Перезарядка", "attr": "cooldown", "kind": "float", "max": 3600, "suffix": " с",
     "tooltip": "Перезарядка умения. Раньше этого времени умение не нажимается."},
    {"title": "Каст", "attr": "cast_time", "kind": "float", "max": 30, "suffix": " с",
     "tooltip": "Время применения: в это время другие умения не нажимаются."},
    {"title": "Мин. MP", "attr": "min_mp", "kind": "float", "max": 100, "decimals": 0, "step": 1,
     "suffix": " %", "tooltip": "Не использовать, если MP ниже."},
    {"title": "HP цели >", "attr": "min_target_hp", "kind": "float", "max": 100, "decimals": 0,
     "step": 1, "suffix": " %",
     "tooltip": "Использовать, только если HP цели выше (0 — всегда).\n"
                "Например, сильные умения не тратить на почти убитую цель."},
]

BUFF_COLUMNS = [
    {"title": "Вкл", "attr": "enabled", "kind": "check"},
    {"title": "Название", "attr": "name", "kind": "text", "stretch": True},
    {"title": "Клавиша", "attr": "key", "kind": "key"},
    {"title": "Интервал", "attr": "interval", "kind": "float", "max": 36000, "decimals": 0,
     "step": 10, "suffix": " с", "tooltip": "Как часто обновлять бафф."},
    {"title": "Каст", "attr": "cast_time", "kind": "float", "max": 30, "suffix": " с"},
    {"title": "В бою", "attr": "in_combat", "kind": "check",
     "tooltip": "Разрешить использовать бафф во время боя."},
]


class CombatTab(ConfigTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        b = self.binder
        body = QWidget()
        grid = QGridLayout(body)
        grid.setHorizontalSpacing(12)

        box, f = group("Клавиши управления")
        f.addRow("Выбор ближайшей цели:", b.add("combat.target_key", KeyEdit()))
        f.addRow("Атака выбранной цели:", b.add("combat.attack_key", KeyEdit()))
        f.addRow("Сброс цели:", b.add("combat.clear_target_key", KeyEdit()))
        f.addRow(hint("В игре: TAB — ближайший монстр, F — атака. Сброс цели (ESC) нужен, "
                      "если монстр недоступен."))
        grid.addWidget(box, 0, 0)

        box, f = group("Задержки")
        f.addRow("Пауза между действиями:", b.add("combat.action_delay", dspin(
            0.05, 5, 0.05, 2, " с", "Минимальная пауза между боевыми нажатиями.")))
        f.addRow("Повтор атаки (F) каждые:", b.add("combat.attack_repeat_interval", dspin(
            0.5, 60, 0.5, 1, " с", "Если умения не готовы — повторно нажимать атаку.")))
        f.addRow("Пауза между поисками цели:", b.add("combat.search_delay", dspin(
            0.1, 30, 0.1, 2, " с")))
        f.addRow("Ожидание рамки цели после TAB:", b.add("combat.target_confirm_delay", dspin(
            0.1, 5, 0.05, 2, " с", "Сколько ждать появления рамки цели после нажатия TAB.")))
        f.addRow("Попыток поиска до перемещения:", b.add("combat.max_search_attempts", ispin(1, 50)))
        f.addRow("Цель пропала, если нет рамки:", b.add("combat.target_lost_confirm", dspin(
            0.05, 5, 0.05, 2, " с", "Защита от мерцания: столько рамка цели должна отсутствовать.")))
        grid.addWidget(box, 0, 1)

        box, f = group("Застревание и «слепой» режим")
        f.addRow("HP цели не меняется дольше:", b.add("combat.stuck_timeout", dspin(
            2, 300, 1, 0, " с", "Тогда цель сбрасывается (монстр недоступен/за препятствием).")))
        f.addRow("Максимальная длительность боя:", b.add("combat.max_fight_time", dspin(
            5, 3600, 5, 0, " с")))
        f.addRow("Застреваний подряд до смены места:", b.add("combat.max_stuck_in_row", ispin(1, 20)))
        f.addRow("Длительность боя в «слепом» режиме:", b.add("combat.blind_fight_time", dspin(
            1, 120, 0.5, 1, " с", "Если полоса HP цели не откалибрована, бот атакует по таймеру.")))
        grid.addWidget(box, 1, 0)

        box, f = group("Сбор добычи (необязательно)")
        f.addRow("Клавиша подбора:", b.add("combat.loot_key", KeyEdit()))
        f.addRow("Нажатий после убийства:", b.add("combat.loot_presses", ispin(0, 10)))
        f.addRow("Пауза между нажатиями:", b.add("combat.loot_delay", dspin(0.1, 5, 0.1, 2, " с")))
        f.addRow(hint("Оставьте пустым, если в игре автоподбор или лут не нужен."))
        grid.addWidget(box, 1, 1)

        box, lay = group("Боевые умения (порядок = приоритет)", QVBoxLayout())
        self.skills = ObjectTable(SKILL_COLUMNS, lambda: Skill(True, "Умение", "", 5.0, 0.5, 0.0, 0.0))
        self.skills.changed.connect(self.changed.emit)
        lay.addWidget(self.skills)
        lay.addWidget(hint("Каждое умение нажимается, когда прошла его перезарядка. Верхние строки "
                           "имеют приоритет. Сочетания: CTRL+1, SHIFT+2 и т.п. "
                           "(в игре слоты «C-1…C-0» — вероятно, CTRL+цифра)."))
        grid.addWidget(box, 2, 0, 1, 2)

        box, lay = group("Баффы", QVBoxLayout())
        self.buffs = ObjectTable(BUFF_COLUMNS, lambda: Buff(True, "Бафф", "", 300.0, 1.0, False),
                                 min_rows_height=120)
        self.buffs.changed.connect(self.changed.emit)
        lay.addWidget(self.buffs)
        grid.addWidget(box, 3, 0, 1, 2)
        grid.setRowStretch(4, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(body))

    def load(self, cfg: Config) -> None:
        super().load(cfg)
        self.skills.set_objects(copy.deepcopy(cfg.combat.skills))
        self.buffs.set_objects(copy.deepcopy(cfg.combat.buffs))

    def apply(self, cfg: Config) -> None:
        super().apply(cfg)
        cfg.combat.skills = self.skills.objects()
        cfg.combat.buffs = self.buffs.objects()


# ---------------------------------------------------------------------------
# Вкладка «Зелья»
# ---------------------------------------------------------------------------

class PotionsTab(ConfigTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        b = self.binder
        body = QWidget()
        grid = QGridLayout(body)
        grid.setHorizontalSpacing(12)

        box, f = group("Зелье здоровья (HP)")
        f.addRow(b.add("potions.hp_enabled", check("Использовать зелье HP")))
        f.addRow("Клавиша:", b.add("potions.hp_key", KeyEdit()))
        f.addRow("Пить, если HP ниже:", b.add("potions.hp_threshold", dspin(1, 99, 1, 0, " %")))
        f.addRow("Перезарядка зелья:", b.add("potions.hp_cooldown", dspin(
            0.5, 600, 0.5, 1, " с", "Пока не прошла перезарядка, клавиша не нажимается.")))
        grid.addWidget(box, 0, 0)

        box, f = group("Критическое HP")
        f.addRow("Критический порог:", b.add("potions.hp_critical_threshold", dspin(1, 99, 1, 0, " %")))
        f.addRow("Доп. клавиша (необяз.):", b.add("potions.hp_critical_key", KeyEdit()))
        f.addRow("Перезарядка доп. клавиши:", b.add("potions.hp_critical_cooldown", dspin(
            0.5, 600, 0.5, 1, " с")))
        f.addRow("Перепроверка готовности:", b.add("potions.critical_recheck", dspin(
            0.5, 30, 0.5, 1, " с",
            "При критическом HP и включённой визуальной проверке слот перепроверяется так часто.")))
        f.addRow(hint("Ниже критического порога сначала нажимается доп. клавиша (сильное "
                      "зелье или умение лечения), а готовность обычного зелья проверяется чаще."))
        grid.addWidget(box, 0, 1)

        box, f = group("Зелье маны (MP / ОМ)")
        f.addRow(b.add("potions.mp_enabled", check("Использовать зелье MP")))
        f.addRow("Клавиша:", b.add("potions.mp_key", KeyEdit()))
        f.addRow("Пить, если MP ниже:", b.add("potions.mp_threshold", dspin(1, 99, 1, 0, " %")))
        f.addRow("Перезарядка зелья:", b.add("potions.mp_cooldown", dspin(0.5, 600, 0.5, 1, " с")))
        grid.addWidget(box, 1, 0)

        box, f = group("Общие правила")
        f.addRow("Интервал проверки HP/MP:", b.add("potions.check_interval", dspin(
            0.05, 5, 0.05, 2, " с")))
        f.addRow("Мин. пауза между зельями:", b.add("potions.min_gap", dspin(
            0.2, 30, 0.1, 1, " с", "Защита от нажатия зелий подряд.")))
        f.addRow(b.add("potions.visual_cooldown_check", check(
            "Проверять перезарядку по затемнению слота",
            "Нужна калибровка слотов зелий на вкладке «Калибровка».")))
        f.addRow("Проверка эффекта через:", b.add("potions.verify_time", dspin(0.5, 30, 0.5, 1, " с")))
        f.addRow("Предупреждать после N зелий без эффекта:", b.add("potions.no_effect_warn",
                                                                  ispin(1, 20)))
        grid.addWidget(box, 1, 1)

        box, f = group("Отдых вне боя (необязательно)")
        f.addRow(b.add("potions.rest_enabled", check("Отдыхать при низком HP/MP")))
        f.addRow("Клавиша отдыха:", b.add("potions.rest_key", KeyEdit()))
        f.addRow("Начать, если HP ниже:", b.add("potions.rest_start_hp", dspin(1, 99, 1, 0, " %")))
        f.addRow("Начать, если MP ниже:", b.add("potions.rest_start_mp", dspin(1, 99, 1, 0, " %")))
        f.addRow("Отдыхать до HP:", b.add("potions.rest_until_hp", dspin(10, 100, 1, 0, " %")))
        f.addRow("Отдыхать до MP:", b.add("potions.rest_until_mp", dspin(10, 100, 1, 0, " %")))
        f.addRow("Максимум отдыха:", b.add("potions.rest_max_time", dspin(5, 600, 5, 0, " с")))
        grid.addWidget(box, 2, 0)

        box, f = group("Смерть персонажа")
        f.addRow("Действие:", b.add("potions.death_action", combo(
            [("Остановить программу", "stop"), ("Поставить на паузу", "pause")])))
        f.addRow("HP = 0 в течение:", b.add("potions.death_confirm_time", dspin(
            0.5, 30, 0.5, 1, " с", "Подтверждение, чтобы случайный кадр не остановил бота.")))
        grid.addWidget(box, 2, 1)
        grid.setRowStretch(3, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(body))


# ---------------------------------------------------------------------------
# Вкладка «Настройки»
# ---------------------------------------------------------------------------

class WindowPicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор окна игры")
        self.resize(520, 420)
        self.list = QListWidget()
        for hwnd, title in winapi.list_windows():
            it = QListWidgetItem(title)
            it.setData(Qt.UserRole, hwnd)
            self.list.addItem(it)
        if self.list.count() == 0:
            self.list.addItem("(окна не найдены — доступно только в Windows)")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Выберите окно «Седьмого элемента». В настройках сохранится его "
                           "заголовок; можно оставить только уникальную часть."))
        lay.addWidget(self.list)
        lay.addWidget(bb)

    def selected_title(self) -> Optional[str]:
        it = self.list.currentItem()
        if it is None or it.data(Qt.UserRole) is None:
            return None
        return it.text()


class SettingsTab(ConfigTab):
    windowTitleChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        b = self.binder
        body = QWidget()
        grid = QGridLayout(body)
        grid.setHorizontalSpacing(12)

        box, f = group("Окно игры")
        self.title_edit = QLineEdit()
        self.title_edit.setToolTip("Часть заголовка окна игры (без учёта регистра).\n"
                                   "Несколько вариантов — через «|», например: 7th Element|Седьмой элемент")
        b.add("window.title", self.title_edit)
        self.title_edit.editingFinished.connect(lambda: self.windowTitleChanged.emit(self.title_edit.text()))
        pick = QPushButton("Выбрать…")
        pick.clicked.connect(self._pick_window)
        f.addRow("Заголовок окна:", row(self.title_edit, pick, stretch=False))
        f.addRow(b.add("window.require_foreground", check(
            "Действовать только когда окно игры активно",
            "Если вы переключились в другое окно, бот не нажимает клавиши.")))
        f.addRow(b.add("window.activate_on_start", check("Активировать окно игры при старте")))
        f.addRow("Задержка перед стартом:", b.add("window.start_delay", dspin(0, 30, 0.5, 1, " с")))
        grid.addWidget(box, 0, 0)

        box, f = group("Глобальные горячие клавиши")
        f.addRow("Старт:", b.add("hotkeys.start", KeyEdit()))
        f.addRow("Пауза / продолжить:", b.add("hotkeys.pause", KeyEdit()))
        f.addRow("Аварийная остановка:", b.add("hotkeys.stop", KeyEdit()))
        f.addRow("Добавить точку маршрута (курсор):", b.add("hotkeys.record_point", KeyEdit()))
        f.addRow(hint("Работают, даже когда активно окно игры. Не назначайте эти клавиши "
                      "умениям в игре."))
        grid.addWidget(box, 0, 1)

        box, f = group("Безопасность и защита от спама")
        f.addRow("Способ ввода:", b.add("safety.input_mode", combo(
            [("Скан-коды (рекомендуется для игр)", "scancode"), ("Виртуальные коды", "vk")])))
        f.addRow("Удержание клавиши:", b.add("safety.key_hold", dspin(0.01, 0.5, 0.01, 2, " с")))
        f.addRow("Мин. пауза между нажатиями:", b.add("safety.min_input_gap", dspin(
            0.0, 2, 0.01, 2, " с")))
        f.addRow("Лимит одной клавиши за 10 с:", b.add("safety.max_same_key_per_10s", ispin(3, 200)))
        f.addRow("Общий лимит действий за 10 с:", b.add("safety.max_inputs_per_10s", ispin(5, 500)))
        f.addRow("Пауза после N заблокированных:", b.add("safety.spam_pause_after", ispin(1, 200)))
        f.addRow("Пауза после N кадров без HP:", b.add("safety.recognition_error_limit", ispin(5, 2000)))
        f.addRow("Период главного цикла:", b.add("safety.tick_interval", dspin(0.02, 1, 0.01, 2, " с")))
        f.addRow("Сторожевой таймер:", b.add("safety.watchdog_timeout", dspin(
            3, 120, 1, 0, " с", "Если цикл бота завис дольше — аварийная остановка.")))
        grid.addWidget(box, 1, 0)

        box, f = group("Прочее")
        f.addRow(b.add("log_to_file", check("Писать журнал в файл (папка logs)")))
        f.addRow(b.add("show_preview", check("Показывать превью «глазами бота» на главной")))
        grid.addWidget(box, 1, 1)
        grid.setRowStretch(2, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(body))

    def _pick_window(self):
        dlg = WindowPicker(self)
        if dlg.exec() == QDialog.Accepted:
            t = dlg.selected_title()
            if t:
                self.title_edit.setText(t)
                self.windowTitleChanged.emit(t)
                self.changed.emit()
