"""Вкладка «Маршрут»: точки перемещения, запись кликами, случайное блуждание."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFileDialog, QMessageBox, QPushButton, QSplitter, QVBoxLayout,
                               QWidget)

from ..config import Config, Rect, RoutePoint, app_dir, load_route, save_route
from ..logger import log
from ..movement import Navigator, RouteRecorder
from .canvas import CanvasPoint, CanvasRegion, RegionCanvas
from .tabs import ConfigTab, ObjectTable
from .widgets import check, combo, dspin, group, hint, ispin, row, scroll_wrap

ROUTE_COLUMNS = [
    {"title": "X", "attr": "x", "kind": "int", "max": 10000},
    {"title": "Y", "attr": "y", "kind": "int", "max": 10000},
    {"title": "Пауза", "attr": "wait", "kind": "float", "max": 600, "suffix": " с",
     "tooltip": "Сколько стоять в точке после прибытия, прежде чем искать цели."},
]


class RouteTab(ConfigTab):
    def __init__(self, window, current_cfg: Callable[[], Config],
                 test_click: Callable[[int, int], None], parent=None):
        super().__init__(parent)
        self.window = window
        self.current_cfg = current_cfg
        self.test_click = test_click
        self.recorder = RouteRecorder(window)
        self.ref_size = (1280, 800)
        self.character = (640, 560)
        b = self.binder

        self.canvas = RegionCanvas()
        self.canvas.allow_rect = False
        self.canvas.label_all = True
        self.canvas.placeholder = ("Снимок появится после «Снимка окна игры» на вкладке "
                                   "«Калибровка». Щелчок по снимку добавляет точку маршрута.")
        self.canvas.pointClicked.connect(self._canvas_point)

        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 4, 0)

        box, f = group("Режим перемещения")
        self.mode = b.add("movement.mode", combo([
            ("Маршрут по точкам", "route"),
            ("Случайное блуждание", "roam"),
            ("Стоять на месте", "none"),
        ], "Когда рядом нет целей, персонаж перемещается выбранным способом."))
        f.addRow("Режим:", self.mode)
        f.addRow("Обход точек:", b.add("movement.loop_mode", combo([
            ("По кругу 1→2→3→1", "loop"), ("Туда-обратно 1→2→3→2→1", "pingpong")])))
        f.addRow(hint("Если маршрут пуст, используется случайное блуждание."))
        pl.addWidget(box)

        box, lay = group("Точки маршрута", QVBoxLayout())
        self.points = ObjectTable(ROUTE_COLUMNS, lambda: RoutePoint(0, 0, 0.0), min_rows_height=200)
        self.points.changed.connect(self._points_changed)
        self.btn_rec = QPushButton("⏺ Запись кликами в игре")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setToolTip("Пока запись включена, каждый щелчок ЛКМ по окну игры добавляет "
                                "точку (персонаж при этом тоже идёт — удобно пройти маршрут).")
        self.btn_rec.toggled.connect(self._toggle_record)
        self.btn_cursor = QPushButton("＋ Точка под курсором")
        self.btn_cursor.setToolTip("Добавить точку там, где сейчас курсор в окне игры "
                                   "(или горячая клавиша на вкладке «Настройки»).")
        self.btn_cursor.clicked.connect(self.add_cursor_point)
        self.btn_clear = QPushButton("Очистить")
        self.btn_clear.clicked.connect(self._clear)
        self.btn_save = QPushButton("Сохранить…")
        self.btn_load = QPushButton("Загрузить…")
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load)
        self.btn_test = QPushButton("Проверить выбранную точку")
        self.btn_test.setToolTip("Активировать окно игры и кликнуть в выбранную точку.")
        self.btn_test.clicked.connect(self._test_point)
        lay.addWidget(self.points)
        lay.addWidget(row(self.btn_rec, self.btn_cursor))
        lay.addWidget(row(self.btn_save, self.btn_load, self.btn_clear))
        lay.addWidget(self.btn_test)
        lay.addWidget(hint("Точка — место клика по земле в окне игры. Точки лучше ставить "
                           "в центральной части экрана, не на интерфейс. Камеру во время "
                           "фарма не поворачивайте: маршрут задаётся относительно экрана."))
        pl.addWidget(box)

        box, f = group("Прибытие в точку")
        f.addRow(b.add("movement.detect_arrival", check(
            "Определять остановку по мини-карте", "Нужна область мини-карты (вкладка «Калибровка»).")))
        f.addRow("Мини-карта неподвижна:", b.add("movement.stable_time", dspin(0.2, 5, 0.1, 1, " с")))
        f.addRow("Минимум в пути:", b.add("movement.min_travel_time", dspin(0.2, 10, 0.1, 1, " с")))
        f.addRow("Максимум в пути:", b.add("movement.max_travel_time", dspin(1, 120, 0.5, 1, " с")))
        f.addRow(b.add("movement.tab_while_moving", check("Искать цели (TAB) во время движения")))
        f.addRow("Интервал TAB в пути:", b.add("movement.tab_interval", dspin(0.5, 10, 0.1, 1, " с")))
        pl.addWidget(box)

        box, f = group("Случайное блуждание")
        f.addRow("Радиус от персонажа, мин:", b.add("movement.roam_min_radius", ispin(30, 2000, 10, " пикс")))
        f.addRow("Радиус от персонажа, макс:", b.add("movement.roam_max_radius", ispin(40, 3000, 10, " пикс")))
        f.addRow("Отступ слева:", b.add("movement.margin_left", dspin(0, 0.45, 0.01, 2)))
        f.addRow("Отступ справа:", b.add("movement.margin_right", dspin(0, 0.45, 0.01, 2)))
        f.addRow("Отступ сверху:", b.add("movement.margin_top", dspin(0, 0.45, 0.01, 2)))
        f.addRow("Отступ снизу:", b.add("movement.margin_bottom", dspin(0, 0.45, 0.01, 2)))
        f.addRow("Разброс клика:", b.add("movement.click_jitter", ispin(0, 40, 1, " пикс")))
        f.addRow(hint("Отступы — доли окна, куда бот не кликает (там интерфейс игры). "
                      "Безопасная зона показана на снимке пунктиром."))
        pl.addWidget(box)
        pl.addStretch(1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.canvas)
        right = scroll_wrap(panel)
        right.setMinimumWidth(400)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([860, 420])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(25)
        self.rec_timer.timeout.connect(self._poll_record)
        self.changed.connect(self._refresh_canvas)

    # ------------------------------------------------------------------
    def load(self, cfg: Config) -> None:
        super().load(cfg)
        self.points.set_objects(copy.deepcopy(cfg.movement.points))
        self.ref_size = (cfg.regions.reference_width, cfg.regions.reference_height)
        self.character = (cfg.regions.character.x, cfg.regions.character.y)
        self._refresh_canvas()

    def apply(self, cfg: Config) -> None:
        super().apply(cfg)
        cfg.movement.points = self.points.objects()

    def set_snapshot(self, img: np.ndarray) -> None:
        self.canvas.set_image(img)
        self._refresh_canvas()

    def _points_changed(self):
        self.changed.emit()

    def _refresh_canvas(self):
        cfg = self.current_cfg()
        self.ref_size = (cfg.regions.reference_width, cfg.regions.reference_height)
        size = self.canvas.image_size() or self.ref_size
        sx = size[0] / max(1, self.ref_size[0])
        sy = size[1] / max(1, self.ref_size[1])
        pts = []
        for i, p in enumerate(self.points.objects()):
            pts.append(CanvasPoint(int(p.x * sx), int(p.y * sy), QColor("#ffd54a"), str(i + 1)))
        ch = cfg.regions.character
        pts.append(CanvasPoint(int(ch.x * sx), int(ch.y * sy), QColor("#5ee07a"), "персонаж", cross=True))
        self.canvas.points = pts
        x0, y0, x1, y1 = Navigator.safe_zone(cfg.movement, size[0], size[1])
        self.canvas.regions = [CanvasRegion("safe", Rect(x0, y0, x1 - x0, y1 - y0),
                                            QColor(120, 220, 255, 160), "безопасная зона кликов")]
        self.canvas.update()

    # ------------------------------------------------------------------
    def _to_ref(self, x: int, y: int, size: Tuple[int, int]) -> Tuple[int, int]:
        rw, rh = self.ref_size
        return int(round(x * rw / max(1, size[0]))), int(round(y * rh / max(1, size[1])))

    def _add_point(self, x: int, y: int) -> None:
        pts = self.points.objects()
        pts.append(RoutePoint(x, y, 0.0))
        self.points.set_objects(pts)
        self.points.table.selectRow(len(pts) - 1)
        self.changed.emit()
        log.info("Точка маршрута %d: (%d, %d)", len(pts), x, y)

    def _canvas_point(self, x: int, y: int):
        size = self.canvas.image_size()
        if size is None:
            return
        self._add_point(*self._to_ref(x, y, size))

    def add_cursor_point(self):
        pt = self.recorder.cursor_client_point()
        rect = self.window.client_rect()
        if pt is None or rect is None:
            log.warning("Курсор не над окном игры — точка не добавлена")
            return
        self._add_point(*self._to_ref(pt[0], pt[1], (rect[2], rect[3])))

    def _toggle_record(self, on: bool):
        if on:
            self.recorder.start()
            self.rec_timer.start()
            self.btn_rec.setText("⏹ Остановить запись")
            log.info("Запись маршрута: щёлкайте ЛКМ по земле в окне игры")
        else:
            self.recorder.stop()
            self.rec_timer.stop()
            self.btn_rec.setText("⏺ Запись кликами в игре")
            log.info("Запись маршрута остановлена")

    def stop_recording(self):
        self.btn_rec.setChecked(False)

    def _poll_record(self):
        pt = self.recorder.poll()
        if pt is None:
            return
        rect = self.window.client_rect()
        if rect is None:
            return
        self._add_point(*self._to_ref(pt[0], pt[1], (rect[2], rect[3])))

    def _clear(self):
        if self.points.table.rowCount() and QMessageBox.question(
                self, "Маршрут", "Удалить все точки маршрута?") == QMessageBox.Yes:
            self.points.set_objects([])
            self.changed.emit()

    def _save(self):
        d = app_dir() / "routes"
        d.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить маршрут", str(d / "route.json"),
                                              "Маршрут (*.json)")
        if path:
            save_route(self.points.objects(), Path(path))
            log.info("Маршрут сохранён: %s", path)

    def _load(self):
        d = app_dir() / "routes"
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить маршрут", str(d), "Маршрут (*.json)")
        if not path:
            return
        try:
            pts = load_route(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Маршрут", f"Не удалось загрузить: {exc}")
            return
        self.points.set_objects(pts)
        self.changed.emit()
        log.info("Маршрут загружен: %d точек", len(pts))

    def _test_point(self):
        r = self.points.table.currentRow()
        pts = self.points.objects()
        if not 0 <= r < len(pts):
            QMessageBox.information(self, "Маршрут", "Выберите точку в таблице.")
            return
        rect = self.window.client_rect()
        if rect is None:
            QMessageBox.warning(self, "Маршрут", "Окно игры не найдено.")
            return
        sx = rect[2] / max(1, self.ref_size[0])
        sy = rect[3] / max(1, self.ref_size[1])
        self.test_click(int(pts[r].x * sx), int(pts[r].y * sy))
