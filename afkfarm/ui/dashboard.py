"""Главная вкладка: управление, состояние, показатели, журнал."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ..bot import BotStatus, Mode, RunState, RUN_LABELS
from .style import GREEN, ORANGE, RED, ACCENT, FG_DIM
from .widgets import LogView, bgr_to_pixmap, hint

_RUN_COLORS = {RunState.RUNNING: GREEN, RunState.PAUSED: ACCENT, RunState.STOPPED: RED}
_MODE_COLORS = {
    Mode.SEARCH: "#7fc8ff", Mode.ATTACK: "#ff7a6b", Mode.MOVE: "#b6e37a",
    Mode.RECOVER: "#ff9ed2", Mode.REST: "#ff9ed2", Mode.LOOT: "#ffd54a",
    Mode.WAIT_WINDOW: ORANGE, Mode.STARTING: ACCENT, Mode.IDLE: FG_DIM,
}


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


class StatCard(QFrame):
    def __init__(self, name: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        self.value.setAlignment(Qt.AlignCenter)
        lb = QLabel(name)
        lb.setObjectName("statName")
        lb.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value)
        lay.addWidget(lb)

    def set(self, v) -> None:
        self.value.setText(str(v))


class Dashboard(QWidget):
    startClicked = Signal()
    pauseClicked = Signal()
    stopClicked = Signal()
    adminClicked = Signal()
    testInputClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # --- окно игры ---
        win_card = _card()
        wl = QHBoxLayout(win_card)
        wl.setContentsMargins(12, 8, 12, 8)
        self.window_dot = QLabel("●")
        self.window_label = QLabel("Окно игры: поиск…")
        self.window_label.setTextFormat(Qt.RichText)
        self.admin_hint = hint("")
        self.btn_test_input = QPushButton("Проверить ввод")
        self.btn_test_input.setToolTip("Активировать окно игры и один раз нажать клавишу выбора цели.\n"
                                       "Если в игре выделился монстр — нажатия доходят.")
        self.btn_test_input.clicked.connect(self.testInputClicked.emit)
        wl.addWidget(self.window_dot)
        wl.addWidget(self.window_label, 1)
        wl.addWidget(self.admin_hint)
        wl.addWidget(self.btn_test_input)
        root.addWidget(win_card)

        # --- предупреждение о правах администратора ---
        self.admin_card = QFrame()
        self.admin_card.setObjectName("card")
        self.admin_card.setStyleSheet("QFrame#card { border: 1px solid #c94a4a; background: #3a1d1d; }")
        al = QHBoxLayout(self.admin_card)
        al.setContentsMargins(12, 8, 12, 8)
        self.admin_warn = QLabel(
            "⚠ <b>Игра запущена от имени администратора, а программа — нет.</b> Windows не пропускает "
            "нажатия из обычной программы в окно администратора: бот видит HP/MP, но в игре ничего "
            "не происходит.")
        self.admin_warn.setWordWrap(True)
        self.admin_warn.setTextFormat(Qt.RichText)
        self.btn_admin = QPushButton("Перезапустить от имени администратора")
        self.btn_admin.clicked.connect(self.adminClicked.emit)
        al.addWidget(self.admin_warn, 1)
        al.addWidget(self.btn_admin)
        self.admin_card.setVisible(False)
        root.addWidget(self.admin_card)

        # --- кнопки ---
        btns = QHBoxLayout()
        self.btn_start = QPushButton("▶  Старт")
        self.btn_start.setObjectName("startButton")
        self.btn_pause = QPushButton("⏸  Пауза")
        self.btn_pause.setObjectName("pauseButton")
        self.btn_stop = QPushButton("■  Стоп")
        self.btn_stop.setObjectName("stopButton")
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.setMinimumHeight(52)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btns.addWidget(b)
        self.btn_start.clicked.connect(self.startClicked.emit)
        self.btn_pause.clicked.connect(self.pauseClicked.emit)
        self.btn_stop.clicked.connect(self.stopClicked.emit)
        root.addLayout(btns)
        self.hotkey_hint = hint("")
        self.hotkey_hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.hotkey_hint)

        # --- состояние + превью ---
        mid = QHBoxLayout()
        state_card = _card()
        sl = QGridLayout(state_card)
        sl.setContentsMargins(14, 10, 14, 10)
        sl.setVerticalSpacing(8)
        sl.addWidget(QLabel("Статус:"), 0, 0)
        self.run_label = QLabel("Остановлена")
        self.run_label.setObjectName("bigState")
        sl.addWidget(self.run_label, 0, 1)
        sl.addWidget(QLabel("Режим:"), 1, 0)
        self.mode_label = QLabel("Ожидание")
        self.mode_label.setObjectName("bigState")
        sl.addWidget(self.mode_label, 1, 1)
        self.hp_bar = self._bar("hpBar")
        self.mp_bar = self._bar("mpBar")
        self.target_bar = self._bar("targetBar")
        sl.addWidget(QLabel("HP:"), 2, 0)
        sl.addWidget(self.hp_bar, 2, 1)
        sl.addWidget(QLabel("MP:"), 3, 0)
        sl.addWidget(self.mp_bar, 3, 1)
        sl.addWidget(QLabel("Цель:"), 4, 0)
        sl.addWidget(self.target_bar, 4, 1)
        self.message = QLabel("")
        self.message.setStyleSheet(f"color:{ORANGE}")
        self.message.setWordWrap(True)
        sl.addWidget(self.message, 5, 0, 1, 2)
        sl.setColumnStretch(1, 1)
        sl.setRowStretch(6, 1)
        mid.addWidget(state_card, 1)

        prev_card = _card()
        pl = QVBoxLayout(prev_card)
        pl.setContentsMargins(8, 8, 8, 8)
        self.preview = QLabel("Превью «глазами бота»\nпоявится после старта")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 300)
        self.preview.setStyleSheet(f"color:{FG_DIM}; background:#101114; border-radius:6px;")
        pl.addWidget(self.preview)
        mid.addWidget(prev_card)
        root.addLayout(mid)

        # --- статистика ---
        stats = QHBoxLayout()
        self.cards = {}
        for key, name in (("targets_found", "Найдено целей"), ("kills", "Убито"),
                          ("hp_potions", "Зелий HP"), ("mp_potions", "Зелий MP"),
                          ("skills_used", "Умений"), ("moves", "Перемещений"),
                          ("stuck", "Застреваний"), ("uptime", "Время работы")):
            c = StatCard(name)
            self.cards[key] = c
            stats.addWidget(c)
        self.cards["uptime"].set("0:00:00")
        root.addLayout(stats)

        # --- журнал ---
        log_card = _card()
        ll = QVBoxLayout(log_card)
        ll.setContentsMargins(8, 6, 8, 8)
        ll.addWidget(QLabel("Журнал событий"))
        self.log = LogView()
        ll.addWidget(self.log)
        root.addWidget(log_card, 1)

        self.set_run_state(RunState.STOPPED)

    @staticmethod
    def _bar(name: str) -> QProgressBar:
        b = QProgressBar()
        b.setObjectName(name)
        b.setRange(0, 1000)
        b.setValue(0)
        b.setFormat("—")
        return b

    @staticmethod
    def _set_bar(bar: QProgressBar, value: Optional[float], empty_text: str = "—") -> None:
        if value is None:
            bar.setValue(0)
            bar.setFormat(empty_text)
        else:
            bar.setValue(int(round(value * 10)))
            bar.setFormat(f"{value:.0f}%")

    def set_hotkeys(self, start: str, pause: str, stop: str) -> None:
        self.btn_start.setText(f"▶  Старт  [{start}]" if start else "▶  Старт")
        self.btn_pause.setText(f"⏸  Пауза  [{pause}]" if pause else "⏸  Пауза")
        self.btn_stop.setText(f"■  Стоп  [{stop}]" if stop else "■  Стоп")
        self.hotkey_hint.setText(f"Горячие клавиши работают и в игре. Аварийная остановка — {stop}.")

    def show_admin_warning(self, show: bool) -> None:
        self.admin_card.setVisible(show)

    def set_window_status(self, text: str, ok: bool, active: bool = False) -> None:
        color = GREEN if ok and active else (ACCENT if ok else RED)
        self.window_dot.setStyleSheet(f"color:{color}; font-size:16pt;")
        self.window_label.setText(text)

    def set_run_state(self, state: RunState) -> None:
        self.run_label.setText(RUN_LABELS[state])
        self.run_label.setStyleSheet(f"color:{_RUN_COLORS[state]}")
        self.btn_start.setEnabled(state == RunState.STOPPED)
        self.btn_pause.setEnabled(state != RunState.STOPPED)
        self.btn_stop.setEnabled(state != RunState.STOPPED)
        self.btn_pause.setText(self.btn_pause.text().replace("Продолжить", "Пауза")
                               if state != RunState.PAUSED else
                               self.btn_pause.text().replace("Пауза", "Продолжить"))
        if state == RunState.STOPPED:
            self.mode_label.setText("Ожидание")
            self.mode_label.setStyleSheet(f"color:{FG_DIM}")

    def update_status(self, st: BotStatus) -> None:
        self.set_run_state(st.run_state)
        if st.run_state != RunState.STOPPED:
            self.mode_label.setText(st.mode_label)
            self.mode_label.setStyleSheet(f"color:{_MODE_COLORS.get(st.mode, FG_DIM)}")
        self._set_bar(self.hp_bar, st.hp, "не распознано")
        self._set_bar(self.mp_bar, st.mp, "не распознано")
        if not st.target_supported:
            self._set_bar(self.target_bar, None, "полоса цели не откалибрована")
        else:
            self._set_bar(self.target_bar, st.target_hp if st.target_present else None, "нет цели")
        self.message.setText(st.message)
        s = st.stats
        for key in ("targets_found", "kills", "hp_potions", "mp_potions", "skills_used",
                    "moves", "stuck"):
            self.cards[key].set(getattr(s, key))
        if st.run_state != RunState.STOPPED:
            self.cards["uptime"].set(fmt_duration(st.uptime))
        if st.preview is not None:
            pm = bgr_to_pixmap(st.preview)
            self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation))
