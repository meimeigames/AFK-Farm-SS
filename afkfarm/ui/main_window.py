"""Главное окно: связывает интерфейс, бота, горячие клавиши и настройки."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget)

from .. import APP_NAME, __version__, winapi
from ..bot import Bot, BotStatus, RunState
from ..clock import RealClock
from ..config import (Config, ConfigStore, app_dir, clone, load_config, save_config)
from ..game_capture import GameCapture, GameWindow
from ..game_control import InputController
from ..hotkeys import HotkeyListener
from ..keys import is_valid_combo
from ..logger import log, set_file_logging, setup_logging
from .calibration_tab import CalibrationTab
from .dashboard import Dashboard
from .route_tab import RouteTab
from .tabs import CombatTab, PotionsTab, SettingsTab

QUICK_HELP = """<h3>Быстрый старт</h3>
<ol>
<li>Запустите игру в <b>оконном режиме</b> или «окно без рамки».</li>
<li><b>Настройки</b> → «Заголовок окна» → «Выбрать…» и укажите окно игры.</li>
<li><b>Калибровка</b> → «Снимок окна игры» → «Найти полосы HP и MP».
    Затем выберите монстра в игре (TAB), сделайте снимок ещё раз и нажмите
    «Найти полосу HP цели». Выделите мини-карту и точку персонажа.
    Проверьте результат кнопкой «Живой тест».</li>
<li><b>Бой</b>: клавиши TAB/F и умения с перезарядками.</li>
<li><b>Зелья</b>: клавиши зелий, пороги HP/MP и перезарядки.</li>
<li><b>Маршрут</b>: режим перемещения и точки (запись кликами в игре).</li>
<li>Нажмите <b>Старт</b> (или F10) и переключитесь в игру.
    <b>F12</b> — аварийная остановка, <b>F11</b> — пауза.</li>
</ol>
<p>Все изменения настроек применяются сразу, даже во время работы.</p>
"""


class BotBridge(QObject):
    """Передаёт события из рабочих потоков в поток интерфейса."""
    status = Signal(object)
    stopped = Signal(str)
    logMessage = Signal(str, int)
    hotkey = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.resize(1360, 900)
        self.store = store
        self.working: Config = clone(store.get())
        self.bridge = BotBridge()

        # --- ядро ---
        self.game_window = GameWindow(self.working.window.title)
        self.capture = GameCapture(self.game_window)
        self.stop_event = threading.Event()
        self.clock = RealClock(self.stop_event)
        self.controller = InputController(self.game_window, self.store.get, self.clock)
        self.ui_controller = InputController(self.game_window, self.store.get, RealClock())
        self.bot = Bot(store, self.capture, self.controller, self.clock,
                       on_status=self.bridge.status.emit, on_stopped=self.bridge.stopped.emit)

        # --- журнал ---
        handler = setup_logging(self.working.log_to_file)
        handler.subscribe(lambda msg, lvl: self.bridge.logMessage.emit(msg, lvl))

        # --- вкладки ---
        self.dashboard = Dashboard()
        self.combat_tab = CombatTab()
        self.potions_tab = PotionsTab()
        self.route_tab = RouteTab(self.game_window, self.collect, self._test_click)
        self.calib_tab = CalibrationTab(self._grab_for_ui, self.collect)
        self.settings_tab = SettingsTab()
        self.config_tabs = [self.combat_tab, self.potions_tab, self.route_tab,
                            self.calib_tab, self.settings_tab]

        self.tabs = QTabWidget()
        self.tabs.addTab(self.dashboard, "Главная")
        self.tabs.addTab(self.combat_tab, "Бой")
        self.tabs.addTab(self.potions_tab, "Зелья")
        self.tabs.addTab(self.route_tab, "Маршрут")
        self.tabs.addTab(self.calib_tab, "Калибровка")
        self.tabs.addTab(self.settings_tab, "Настройки")
        self.setCentralWidget(self.tabs)

        self._load_tabs(self.working)
        for t in self.config_tabs:
            t.changed.connect(self._schedule_apply)
        self.settings_tab.windowTitleChanged.connect(self.game_window.set_title)
        self.calib_tab.snapshotTaken.connect(self.route_tab.set_snapshot)

        # --- сигналы ---
        self.dashboard.startClicked.connect(self.start_bot)
        self.dashboard.pauseClicked.connect(self.toggle_pause)
        self.dashboard.stopClicked.connect(lambda: self.stop_bot("кнопка «Стоп»"))
        self.bridge.status.connect(self._on_status)
        self.bridge.stopped.connect(self._on_stopped)
        self.bridge.logMessage.connect(self.dashboard.log.add)
        self.bridge.hotkey.connect(self._on_hotkey_ui)

        # --- таймеры ---
        self.apply_timer = QTimer(self)
        self.apply_timer.setSingleShot(True)
        self.apply_timer.setInterval(350)
        self.apply_timer.timeout.connect(self.apply_now)
        self.window_timer = QTimer(self)
        self.window_timer.setInterval(1000)
        self.window_timer.timeout.connect(self._update_window_status)
        self.window_timer.start()
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._check_watchdog)
        self.watchdog.start()

        # --- горячие клавиши ---
        self.hotkeys = HotkeyListener(self._hotkey_bindings, self._on_hotkey_thread)
        self.hotkeys.start()

        self._build_menu()
        self._refresh_static()
        self._update_window_status()
        self.statusBar().showMessage("Готово. Настройте калибровку и нажмите «Старт».")
        log.info("%s v%s запущен. Настройки: %s", APP_NAME, __version__, self.store.path)
        if not winapi.IS_WINDOWS:
            log.warning("Программа запущена не в Windows: управление игрой недоступно "
                        "(работают только интерфейс и калибровка по файлам).")

    # ------------------------------------------------------------------
    # Меню
    # ------------------------------------------------------------------
    def _build_menu(self):
        m = self.menuBar().addMenu("Файл")
        a = QAction("Сохранить настройки как…", self)
        a.triggered.connect(self._save_as)
        m.addAction(a)
        a = QAction("Загрузить настройки…", self)
        a.triggered.connect(self._load_from)
        m.addAction(a)
        a = QAction("Сбросить настройки по умолчанию", self)
        a.triggered.connect(self._reset_defaults)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Открыть папку программы", self)
        a.triggered.connect(lambda: self._open_folder(app_dir()))
        m.addAction(a)
        a = QAction("Открыть папку журналов", self)
        a.triggered.connect(lambda: self._open_folder(app_dir() / "logs"))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Выход", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("Справка")
        a = QAction("Быстрый старт", self)
        a.triggered.connect(lambda: QMessageBox.information(self, "Быстрый старт", QUICK_HELP))
        h.addAction(a)
        a = QAction("О программе", self)
        a.triggered.connect(self._about)
        h.addAction(a)

    def _about(self):
        QMessageBox.about(self, "О программе", (
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "Внешний помощник AFK-фарма. Работает только через снимки экрана, "
            "эмуляцию клавиатуры и мыши: без внедрения в процесс игры, чтения памяти "
            "и обхода защиты.<br><br>Использование автоматизации может нарушать правила "
            "игры — ответственность на пользователе."))

    def _open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _save_as(self):
        self.apply_now()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить настройки", str(app_dir() / "profile.json"),
                                              "Настройки (*.json)")
        if path:
            save_config(self.working, Path(path))
            log.info("Настройки сохранены: %s", path)

    def _load_from(self):
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить настройки", str(app_dir()),
                                              "Настройки (*.json)")
        if not path:
            return
        try:
            cfg = load_config(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Настройки", f"Не удалось загрузить: {exc}")
            return
        self._replace_config(cfg)
        log.info("Настройки загружены: %s", path)

    def _reset_defaults(self):
        if QMessageBox.question(self, "Сброс", "Сбросить все настройки (включая калибровку) "
                                "к значениям по умолчанию?") == QMessageBox.Yes:
            self._replace_config(Config())
            log.info("Настройки сброшены")

    def _replace_config(self, cfg: Config):
        self.working = clone(cfg)
        self._load_tabs(self.working)
        self.store.set(self.working)
        self._refresh_static()

    # ------------------------------------------------------------------
    # Настройки
    # ------------------------------------------------------------------
    def _load_tabs(self, cfg: Config):
        for t in self.config_tabs:
            t.load(cfg)

    def collect(self) -> Config:
        """Текущие значения всех вкладок в виде нового объекта Config."""
        cfg = clone(self.working)
        for t in self.config_tabs:
            t.apply(cfg)
        return cfg

    def _schedule_apply(self):
        self.apply_timer.start()

    def apply_now(self):
        self.apply_timer.stop()
        cfg = self.collect()
        self.working = cfg
        self.store.set(cfg)
        self._refresh_static()

    def _refresh_static(self):
        cfg = self.working
        self.game_window.set_title(cfg.window.title)
        self.dashboard.set_hotkeys(cfg.hotkeys.start, cfg.hotkeys.pause, cfg.hotkeys.stop)
        set_file_logging(cfg.log_to_file)
        if winapi.IS_WINDOWS and not winapi.is_admin():
            self.dashboard.admin_hint.setText("Если игра запущена от администратора — "
                                              "запустите программу так же.")

    # ------------------------------------------------------------------
    # Окно игры
    # ------------------------------------------------------------------
    def _update_window_status(self):
        hwnd = self.game_window.locate()
        title = self.working.window.title
        if not hwnd:
            self.dashboard.set_window_status(f"Окно игры <b>не найдено</b> (ищу «{title}»)", False)
            return
        rect = winapi.get_client_rect_screen(hwnd)
        name = winapi.get_window_text(hwnd)
        active = winapi.is_foreground(hwnd)
        size = f"{rect[2]}×{rect[3]}" if rect else "свёрнуто"
        ref = (self.working.regions.reference_width, self.working.regions.reference_height)
        warn = ""
        if rect and (rect[2], rect[3]) != ref:
            warn = f"  <span style='color:#f0b54a'>⚠ калибровка для {ref[0]}×{ref[1]}</span>"
        self.dashboard.set_window_status(
            f"Окно игры: <b>{name}</b> — {size}, {'активно' if active else 'не активно'}{warn}",
            True, active)

    def _grab_for_ui(self, bring_front: bool = True) -> Optional[np.ndarray]:
        """Снимок окна игры для калибровки.

        Снимается область экрана, поэтому окно программы не должно перекрывать игру:
        на время снимка программа сворачивается, а окно игры выводится вперёд.
        """
        if not (bring_front and winapi.IS_WINDOWS and self.game_window.locate()):
            frame = self.capture.grab()
            return None if frame is None else frame.image
        was_max = self.isMaximized()
        self.setWindowState(Qt.WindowMinimized)
        QApplication.processEvents()
        try:
            self.game_window.activate()
            time.sleep(0.5)
            frame = self.capture.grab()
        finally:
            self.setWindowState(Qt.WindowMaximized if was_max else Qt.WindowNoState)
            self.show()
            self.raise_()
            self.activateWindow()
        return None if frame is None else frame.image

    def _test_click(self, x: int, y: int):
        if self.bot.running:
            QMessageBox.information(self, "Маршрут", "Остановите бота перед проверкой точки.")
            return
        if not self.game_window.activate():
            QMessageBox.warning(self, "Маршрут", "Не удалось активировать окно игры.")
            return
        time.sleep(0.3)
        ok = self.ui_controller.click(x, y, "left")
        log.info("Проверка точки (%d, %d): %s", x, y, "клик выполнен" if ok else "не удалось")

    # ------------------------------------------------------------------
    # Управление ботом
    # ------------------------------------------------------------------
    def _validate(self, cfg: Config) -> List[str]:
        problems = []

        def chk(name, key, required=False):
            if not key.strip():
                if required:
                    problems.append(f"не задана клавиша: {name}")
            elif not is_valid_combo(key):
                problems.append(f"некорректная клавиша «{key}»: {name}")

        chk("выбор цели", cfg.combat.target_key, True)
        chk("атака", cfg.combat.attack_key, True)
        chk("сброс цели", cfg.combat.clear_target_key)
        chk("сбор добычи", cfg.combat.loot_key)
        if cfg.potions.hp_enabled:
            chk("зелье HP", cfg.potions.hp_key, True)
        if cfg.potions.mp_enabled:
            chk("зелье MP", cfg.potions.mp_key, True)
        chk("критическое HP", cfg.potions.hp_critical_key)
        if cfg.potions.rest_enabled:
            chk("отдых", cfg.potions.rest_key, True)
        for s in cfg.combat.skills:
            if s.enabled:
                chk(f"умение «{s.name}»", s.key)
        for bf in cfg.combat.buffs:
            if bf.enabled:
                chk(f"бафф «{bf.name}»", bf.key)
        needs_hp = (cfg.potions.hp_enabled or cfg.potions.mp_enabled or cfg.potions.rest_enabled
                    or cfg.combat.mode == "full")
        if needs_hp and not cfg.regions.hp_bar.is_valid():
            problems.append("не задана область полосы HP (вкладка «Калибровка») — нужна для зелий")
        if cfg.combat.mode == "simple" and not cfg.regions.target_bar.is_valid():
            problems.append("не задана «Полоса HP цели» (вкладка «Калибровка»): в простом режиме "
                            "бот по ней понимает, есть ли моб")
        return problems

    def _hotkey_conflicts(self, cfg: Config) -> List[str]:
        game_keys = {cfg.combat.target_key, cfg.combat.attack_key, cfg.combat.clear_target_key,
                     cfg.potions.hp_key, cfg.potions.mp_key, cfg.potions.hp_critical_key,
                     cfg.combat.loot_key, cfg.potions.rest_key}
        game_keys |= {s.key for s in cfg.combat.skills if s.enabled}
        game_keys |= {b.key for b in cfg.combat.buffs if b.enabled}
        game_keys = {k.upper() for k in game_keys if k}
        hk = cfg.hotkeys
        return [k for k in (hk.start, hk.pause, hk.stop, hk.record_point) if k and k.upper() in game_keys]

    def start_bot(self):
        if self.bot.running:
            return
        self.apply_now()
        cfg = self.working
        if not winapi.IS_WINDOWS:
            QMessageBox.warning(self, "Старт", "Управление игрой доступно только в Windows.")
            return
        problems = self._validate(cfg)
        if problems:
            QMessageBox.warning(self, "Проверьте настройки", "Нельзя запустить:\n\n• " + "\n• ".join(problems))
            return
        conflicts = self._hotkey_conflicts(cfg)
        if conflicts:
            QMessageBox.warning(self, "Горячие клавиши",
                                "Горячие клавиши программы совпадают с игровыми: "
                                + ", ".join(conflicts) + ".\nИзмените их на вкладке «Настройки».")
            return
        self.calib_tab.stop_live()
        self.route_tab.stop_recording()
        if self.bot.start():
            self.dashboard.set_run_state(RunState.RUNNING)
            self.statusBar().showMessage("Бот работает. Переключитесь в окно игры.")

    def toggle_pause(self):
        if self.bot.running:
            self.bot.toggle_pause()

    def stop_bot(self, reason: str = "остановлено пользователем"):
        if self.bot.running:
            self.bot.stop(reason)
        self.controller.release_all()

    def _on_status(self, st: BotStatus):
        self.dashboard.update_status(st)

    def _on_stopped(self, reason: str):
        self.dashboard.set_run_state(RunState.STOPPED)
        self.statusBar().showMessage(f"Бот остановлен: {reason}")
        if reason in ("персонаж погиб",) or "ошибок" in reason or "сторож" in reason:
            QApplication.beep()

    def _check_watchdog(self):
        if not self.bot.running or self.bot.run_state == RunState.STOPPED:
            return
        lag = time.monotonic() - self.bot.heartbeat
        if lag > self.working.safety.watchdog_timeout:
            log.error("Сторожевой таймер: цикл бота не отвечает %.0f с — аварийная остановка", lag)
            self.stop_bot("сторожевой таймер")

    # ------------------------------------------------------------------
    # Горячие клавиши
    # ------------------------------------------------------------------
    def _hotkey_bindings(self):
        hk = self.store.get().hotkeys
        return {"start": hk.start, "pause": hk.pause, "stop": hk.stop, "record_point": hk.record_point}

    def _on_hotkey_thread(self, action: str):
        # Остановку выполняем сразу из потока горячих клавиш — без ожидания интерфейса
        if action == "stop" and self.bot.running:
            self.bot.stop("горячая клавиша остановки")
            self.controller.release_all()
        self.bridge.hotkey.emit(action)

    def _on_hotkey_ui(self, action: str):
        if action == "start":
            if not self.bot.running:
                self.start_bot()
        elif action == "pause":
            self.toggle_pause()
        elif action == "stop":
            log.info("Аварийная остановка (горячая клавиша)")
        elif action == "record_point":
            if not self.bot.running:
                self.route_tab.add_cursor_point()

    # ------------------------------------------------------------------
    def closeEvent(self, ev: QCloseEvent):
        self.stop_bot("закрытие программы")
        self.bot.join(1.5)
        self.hotkeys.stop()
        self.apply_now()
        super().closeEvent(ev)

