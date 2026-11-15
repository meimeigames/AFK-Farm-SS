"""Вкладка «Калибровка»: области интерфейса, цвета полос, проверка распознавания."""

from __future__ import annotations

import copy
from typing import Callable, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import vision
from ..config import Config, Rect, RegionsConfig
from ..logger import log
from .canvas import CanvasPoint, CanvasRegion, RegionCanvas
from .tabs import ConfigTab
from .widgets import check, dspin, group, hint, ispin, row, scroll_wrap

REGION_DEFS = [
    ("hp_bar", "Полоса HP", "rect", "#ff5c5c",
     "Выделите всю полосу HP (красную) — от левого края до правого, включая пустую часть. "
     "Удобнее при полном HP. Колесо мыши — масштаб."),
    ("mp_bar", "Полоса MP", "rect", "#4f9dff",
     "Выделите всю синюю полосу MP (ОМ) под полосой HP."),
    ("target_bar", "Полоса HP цели", "rect", "#ffb347",
     "Выберите монстра в игре (TAB), сделайте снимок и выделите красную полосу HP в рамке "
     "цели вверху по центру. Без неё бот работает в «слепом» режиме."),
    ("target_frame", "Рамка цели (шаблон)", "rect", "#d58bff",
     "Необязательно. Выделите неизменную часть рамки цели (орнамент) и нажмите "
     "«Сохранить шаблон рамки» — это защитит от ложного обнаружения цели."),
    ("minimap", "Мини-карта", "rect", "#5ee07a",
     "Выделите внутреннюю часть круглой мини-карты. По её изменению бот понимает, что "
     "персонаж идёт или остановился."),
    ("hp_potion_slot", "Слот зелья HP", "rect", "#ff9e9e",
     "Необязательно. Выделите иконку зелья HP на панели — для проверки перезарядки по затемнению."),
    ("mp_potion_slot", "Слот зелья MP", "rect", "#9ec7ff",
     "Необязательно. Иконка зелья MP на панели."),
    ("hotbar", "Панель умений", "rect", "#c8c8c8",
     "Для справки: область панели быстрого доступа."),
    ("character", "Персонаж (точка)", "point", "#ffe14a",
     "Щёлкните по ногам персонажа. От этой точки считается случайное блуждание."),
]
REGION_INFO = {d[0]: d for d in REGION_DEFS}


def _swatch(color: str) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(QColor(color))
    return QIcon(pm)


class CalibrationTab(ConfigTab):
    snapshotTaken = Signal(object)    # np.ndarray (BGR)

    def __init__(self, grab_frame: Callable[[], Optional[np.ndarray]],
                 current_cfg: Callable[[], Config], parent=None):
        super().__init__(parent)
        self.grab_frame = grab_frame
        self.current_cfg = current_cfg
        self.regions = RegionsConfig()
        self.slot_refs = {"hp": 0.0, "mp": 0.0}
        self.snapshot: Optional[np.ndarray] = None
        self.vision = vision.Vision()
        self._updating_coords = False

        self.canvas = RegionCanvas()
        self.canvas.rectDrawn.connect(self._on_rect)
        self.canvas.pointClicked.connect(self._on_point)

        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 4, 0)

        # --- источник ---
        box, lay = group("Снимок", QVBoxLayout())
        self.btn_snap = QPushButton("📷  Снимок окна игры")
        self.btn_snap.setToolTip("Сделать снимок клиентской области окна игры.")
        self.btn_open = QPushButton("Открыть файл…")
        self.btn_save = QPushButton("Сохранить снимок…")
        self.btn_snap.clicked.connect(self.take_snapshot)
        self.btn_open.clicked.connect(self._open_file)
        self.btn_save.clicked.connect(self._save_file)
        self.snap_info = hint("Снимка нет")
        lay.addWidget(self.btn_snap)
        lay.addWidget(row(self.btn_open, self.btn_save))
        lay.addWidget(self.snap_info)
        pl.addWidget(box)

        # --- области ---
        box, lay = group("Области интерфейса", QVBoxLayout())
        self.region_list = QListWidget()
        self.region_list.setMaximumHeight(210)
        for key, label, _kind, color, _help in REGION_DEFS:
            it = QListWidgetItem(_swatch(color), label)
            it.setData(Qt.UserRole, key)
            self.region_list.addItem(it)
        self.region_list.currentRowChanged.connect(self._on_region_selected)
        self.region_help = hint("")
        self.sx, self.sy, self.sw, self.sh = (QSpinBox() for _ in range(4))
        for s in (self.sx, self.sy, self.sw, self.sh):
            s.setRange(0, 10000)
            s.setMinimumWidth(58)
            s.setMaximumWidth(78)
            s.setKeyboardTracking(False)
            s.valueChanged.connect(self._on_coords_edited)
        coords = QWidget()
        cg = QGridLayout(coords)
        cg.setContentsMargins(0, 0, 0, 0)
        for i, (lbl, w) in enumerate((("X", self.sx), ("Y", self.sy), ("Ш", self.sw), ("В", self.sh))):
            cg.addWidget(QLabel(lbl), 0, i * 2)
            cg.addWidget(w, 0, i * 2 + 1)
        self.btn_clear_region = QPushButton("Очистить область")
        self.btn_clear_region.clicked.connect(self._clear_region)
        lay.addWidget(self.region_list)
        lay.addWidget(self.region_help)
        lay.addWidget(coords)
        lay.addWidget(self.btn_clear_region)
        pl.addWidget(box)

        # --- автопоиск ---
        box, lay = group("Автоматический поиск", QVBoxLayout())
        b1 = QPushButton("Найти полосы HP и MP")
        b1.setToolTip("Лучше всего работает при 100% HP и MP.")
        b2 = QPushButton("Найти полосу HP цели")
        b2.setToolTip("Сначала выберите монстра с полным HP (TAB) и сделайте снимок.")
        b3 = QPushButton("Найти мини-карту")
        b1.clicked.connect(self._auto_bars)
        b2.clicked.connect(self._auto_target)
        b3.clicked.connect(self._auto_minimap)
        for b in (b1, b2, b3):
            lay.addWidget(b)
        pl.addWidget(box)

        # --- цвета и эталоны ---
        box, lay = group("Цвета и эталоны", QVBoxLayout())
        c1 = QPushButton("Запомнить цвета HP/MP (при 100%)")
        c2 = QPushButton("Запомнить цвет полосы цели")
        c3 = QPushButton("Запомнить слоты зелий (готовы)")
        c4 = QPushButton("Сохранить шаблон рамки цели")
        c1.clicked.connect(self._learn_vitals)
        c2.clicked.connect(self._learn_target)
        c3.clicked.connect(self._learn_slots)
        c4.clicked.connect(self._save_template)
        for c in (c1, c2, c3, c4):
            lay.addWidget(c)
        self.use_template = self.binder.add("vision.use_target_frame_template",
                                            check("Проверять цель по шаблону рамки"))
        lay.addWidget(self.use_template)
        self.slot_info = hint("")
        lay.addWidget(self.slot_info)
        pl.addWidget(box)

        # --- проверка ---
        box, lay = group("Проверка распознавания", QVBoxLayout())
        t1 = QPushButton("Проверить на снимке")
        self.btn_live = QPushButton("▶ Живой тест")
        self.btn_live.setCheckable(True)
        self.btn_live.setToolTip("Каждые 0,5 с делать снимок окна игры и показывать результат.")
        t1.clicked.connect(self.test_snapshot)
        self.btn_live.toggled.connect(self._toggle_live)
        self.result = QLabel("—")
        self.result.setTextFormat(Qt.RichText)
        self.result.setWordWrap(True)
        lay.addWidget(row(t1, self.btn_live))
        lay.addWidget(self.result)
        pl.addWidget(box)

        # --- дополнительные параметры ---
        box, f = group("Тонкая настройка цвета (HSV)")
        b = self.binder
        for title, path in (("HP", "vision.hp_color"), ("MP", "vision.mp_color"),
                            ("Цель", "vision.target_color")):
            f.addRow(QLabel(f"<b>{title}</b>"))
            f.addRow("Оттенок H от/до:", row(b.add(f"{path}.h_min", ispin(0, 179)),
                                             b.add(f"{path}.h_max", ispin(0, 179))))
            f.addRow("Насыщ. S / Яркость V от:", row(b.add(f"{path}.s_min", ispin(0, 255)),
                                                      b.add(f"{path}.v_min", ispin(0, 255))))
        f.addRow("Заполнение столбца ≥:", b.add("vision.column_threshold", dspin(0.05, 0.95, 0.05, 2)))
        f.addRow("Мин. достоверность:", b.add("vision.min_confidence", dspin(0.3, 1.0, 0.05, 2)))
        f.addRow("Яркость пустой части HP/MP ≤:", b.add("vision.empty_v_max", ispin(10, 255)))
        f.addRow("Яркость пустой части цели ≤:", b.add("vision.target_empty_v_max", ispin(10, 255)))
        f.addRow("Цель выбрана, если HP цели ≥:", b.add("vision.target_min_percent",
                                                        dspin(0.1, 20, 0.1, 1, " %")))
        f.addRow("Сглаживание (кадров):", b.add("vision.smoothing", ispin(1, 9)))
        f.addRow("Порог движения мини-карты:", b.add("vision.motion_threshold", dspin(0.1, 20, 0.1, 1)))
        f.addRow("Слот готов, если яркость ≥:", b.add("vision.potion_slot_ready_ratio",
                                                      dspin(0.3, 1.0, 0.05, 2, " × эталон")))
        f.addRow("Совпадение шаблона рамки ≥:", b.add("vision.target_frame_threshold",
                                                      dspin(0.3, 1.0, 0.05, 2)))
        pl.addWidget(box)
        pl.addStretch(1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.canvas)
        right = scroll_wrap(panel)
        right.setMinimumWidth(410)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([860, 430])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(hint("1) Сделайте снимок окна игры. 2) Выберите область в списке и обведите "
                             "её мышью (колесо — масштаб, ПКМ — сдвиг, двойной ПКМ — вписать). "
                             "3) Нажмите «Проверить на снимке» или «Живой тест»."))
        outer.addWidget(split, 1)

        self.live_timer = QTimer(self)
        self.live_timer.setInterval(500)
        self.live_timer.timeout.connect(self._live_tick)
        self.region_list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Конфиг
    # ------------------------------------------------------------------
    def load(self, cfg: Config) -> None:
        super().load(cfg)
        self.regions = copy.deepcopy(cfg.regions)
        self.slot_refs = {"hp": cfg.vision.hp_slot_ref, "mp": cfg.vision.mp_slot_ref}
        self._update_slot_info()
        self._refresh_canvas()
        self._on_region_selected(self.region_list.currentRow())

    def apply(self, cfg: Config) -> None:
        super().apply(cfg)
        cfg.regions = copy.deepcopy(self.regions)
        cfg.vision.hp_slot_ref = self.slot_refs["hp"]
        cfg.vision.mp_slot_ref = self.slot_refs["mp"]

    def _touch(self):
        if self.snapshot is not None:
            h, w = self.snapshot.shape[:2]
            self.regions.reference_width = w
            self.regions.reference_height = h
        self._refresh_canvas()
        self.changed.emit()

    # ------------------------------------------------------------------
    # Снимок
    # ------------------------------------------------------------------
    def take_snapshot(self) -> bool:
        img = self.grab_frame()
        if img is None:
            QMessageBox.warning(self, "Снимок", "Окно игры не найдено или свёрнуто.\n\n"
                                "Проверьте заголовок окна на вкладке «Настройки» "
                                "или откройте сохранённый скриншот из файла.")
            return False
        self.set_snapshot(img)
        return True

    def set_snapshot(self, img: np.ndarray, emit: bool = True) -> None:
        self.snapshot = img
        h, w = img.shape[:2]
        self.canvas.set_image(img)
        ref = (self.regions.reference_width, self.regions.reference_height)
        warn = "" if ref == (w, h) else (f"  ⚠ калибровка была для {ref[0]}×{ref[1]}")
        self.snap_info.setText(f"Снимок {w}×{h}{warn}")
        self._refresh_canvas()
        if emit:
            self.snapshotTaken.emit(img)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть скриншот", "",
                                              "Изображения (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            QMessageBox.warning(self, "Открыть", "Не удалось прочитать изображение.")
            return
        self.set_snapshot(img)

    def _save_file(self):
        if self.snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить снимок", "snapshot.png",
                                              "PNG (*.png)")
        if path:
            ok, buf = cv2.imencode(".png", self.snapshot)
            if ok:
                buf.tofile(path)

    # ------------------------------------------------------------------
    # Области
    # ------------------------------------------------------------------
    def current_key(self) -> str:
        it = self.region_list.currentItem()
        return it.data(Qt.UserRole) if it else "hp_bar"

    def _on_region_selected(self, _row: int):
        key = self.current_key()
        info = REGION_INFO[key]
        self.region_help.setText(info[4])
        is_point = info[2] == "point"
        self.canvas.allow_rect = not is_point
        self.sw.setEnabled(not is_point)
        self.sh.setEnabled(not is_point)
        self._show_coords()
        self._refresh_canvas()

    def _show_coords(self):
        key = self.current_key()
        val = getattr(self.regions, key)
        self._updating_coords = True
        try:
            if REGION_INFO[key][2] == "point":
                self.sx.setValue(val.x)
                self.sy.setValue(val.y)
                self.sw.setValue(0)
                self.sh.setValue(0)
            else:
                self.sx.setValue(val.x)
                self.sy.setValue(val.y)
                self.sw.setValue(val.w)
                self.sh.setValue(val.h)
        finally:
            self._updating_coords = False

    def _on_coords_edited(self, *_):
        if self._updating_coords:
            return
        key = self.current_key()
        if REGION_INFO[key][2] == "point":
            p = getattr(self.regions, key)
            p.x, p.y = self.sx.value(), self.sy.value()
        else:
            setattr(self.regions, key, Rect(self.sx.value(), self.sy.value(),
                                            self.sw.value(), self.sh.value()))
        self._touch()

    def _on_rect(self, rect: Rect):
        key = self.current_key()
        if REGION_INFO[key][2] == "point":
            return
        setattr(self.regions, key, rect)
        self._show_coords()
        self._touch()
        log.info("Область «%s»: x=%d y=%d %d×%d", REGION_INFO[key][1], rect.x, rect.y, rect.w, rect.h)

    def _on_point(self, x: int, y: int):
        key = self.current_key()
        if REGION_INFO[key][2] != "point":
            return
        p = getattr(self.regions, key)
        p.x, p.y = x, y
        self._show_coords()
        self._touch()

    def _clear_region(self):
        key = self.current_key()
        if REGION_INFO[key][2] == "rect":
            setattr(self.regions, key, Rect())
            self._show_coords()
            self._touch()

    def _refresh_canvas(self, state: Optional[vision.VisionState] = None):
        sel = self.current_key()
        regs = []
        for key, label, kind, color, _ in REGION_DEFS:
            if kind != "rect":
                continue
            regs.append(CanvasRegion(key, getattr(self.regions, key), QColor(color), label,
                                     highlight=(key == sel)))
        self.canvas.regions = regs
        ch = self.regions.character
        self.canvas.points = [CanvasPoint(ch.x, ch.y, QColor("#ffe14a"), "персонаж", cross=True)]
        lines = []
        if state is not None:
            for key, pct in (("hp_bar", state.hp_raw), ("mp_bar", state.mp_raw),
                             ("target_bar", state.target_hp)):
                r = getattr(self.regions, key)
                if pct is not None and r.is_valid():
                    x = r.x + r.w * pct / 100.0
                    lines.append((x, r.y - 4, x, r.y2 + 4, QColor("#ffffff")))
        self.canvas.overlay_lines = lines
        self.canvas.update()

    # ------------------------------------------------------------------
    # Автопоиск и обучение
    # ------------------------------------------------------------------
    def _need_snapshot(self) -> bool:
        if self.snapshot is None:
            QMessageBox.information(self, "Калибровка", "Сначала сделайте снимок окна игры "
                                    "или откройте скриншот.")
            return False
        return True

    def _auto_bars(self):
        if not self._need_snapshot():
            return
        cfg = self.current_cfg()
        found = vision.detect_player_bars(self.snapshot, cfg.vision.hp_color, cfg.vision.mp_color)
        if not found:
            QMessageBox.warning(self, "Автопоиск", "Полосы HP/MP не найдены. Выделите их вручную "
                                "или проверьте цвета в «Тонкой настройке».")
            return
        self.regions.hp_bar, self.regions.mp_bar = found
        log.info("Найдены полосы: HP %s, MP %s", found[0].as_tuple(), found[1].as_tuple())
        self._show_coords()
        self._touch()
        self.test_snapshot()

    def _auto_target(self):
        if not self._need_snapshot():
            return
        cfg = self.current_cfg()
        r = vision.detect_target_bar(self.snapshot, cfg.vision.target_color)
        if r is None:
            QMessageBox.warning(self, "Автопоиск", "Полоса HP цели не найдена. Выберите монстра "
                                "(TAB) и сделайте снимок заново, либо выделите полосу вручную.")
            return
        self.regions.target_bar = r
        log.info("Найдена полоса цели: %s", r.as_tuple())
        self._show_coords()
        self._touch()
        QMessageBox.information(self, "Автопоиск",
                                "Полоса цели найдена. Если у цели было не 100% HP, растяните "
                                "область вправо до конца полосы (поле «Ш»).")

    def _auto_minimap(self):
        if not self._need_snapshot():
            return
        r = vision.detect_minimap(self.snapshot)
        if r is None:
            QMessageBox.warning(self, "Автопоиск", "Мини-карта не найдена — выделите вручную.")
            return
        self.regions.minimap = r
        self._show_coords()
        self._touch()

    def _set_range_widgets(self, path: str, rng) -> None:
        for name in ("h_min", "h_max", "s_min", "v_min"):
            for p, w in self.binder.items:
                if p == f"{path}.{name}":
                    w.setValue(getattr(rng, name))

    def _learn_vitals(self):
        if not self._need_snapshot():
            return
        hp = vision.learn_color(self.snapshot, self.regions.hp_bar)
        mp = vision.learn_color(self.snapshot, self.regions.mp_bar)
        if hp is None or mp is None:
            QMessageBox.warning(self, "Цвета", "Не удалось определить цвет. Проверьте, что области "
                                "HP/MP выделены и полосы полные.")
            return
        self._set_range_widgets("vision.hp_color", hp)
        self._set_range_widgets("vision.mp_color", mp)
        log.info("Цвет HP: H %d–%d, MP: H %d–%d", hp.h_min, hp.h_max, mp.h_min, mp.h_max)
        self.changed.emit()
        self.test_snapshot()

    def _learn_target(self):
        if not self._need_snapshot():
            return
        rng = vision.learn_color(self.snapshot, self.regions.target_bar)
        if rng is None:
            QMessageBox.warning(self, "Цвета", "Выберите цель с полным HP и выделите её полосу.")
            return
        self._set_range_widgets("vision.target_color", rng)
        self.changed.emit()
        self.test_snapshot()

    def _learn_slots(self):
        if not self._need_snapshot():
            return
        got = []
        for kind, key in (("hp", "hp_potion_slot"), ("mp", "mp_potion_slot")):
            r = getattr(self.regions, key)
            b = vision.region_brightness(self.snapshot, r) if r.is_valid() else None
            if b:
                self.slot_refs[kind] = b
                got.append(kind.upper())
        if not got:
            QMessageBox.warning(self, "Слоты", "Сначала выделите слоты зелий HP и/или MP.")
            return
        self._update_slot_info()
        self.changed.emit()
        QMessageBox.information(self, "Слоты", f"Эталон сохранён: {', '.join(got)}. Включите "
                                "«Проверять перезарядку по затемнению слота» на вкладке «Зелья».")

    def _update_slot_info(self):
        parts = []
        for k in ("hp", "mp"):
            v = self.slot_refs[k]
            parts.append(f"{k.upper()}: {v:.0f}" if v > 0 else f"{k.upper()}: нет")
        self.slot_info.setText("Эталон яркости слотов — " + ", ".join(parts))

    def _save_template(self):
        if not self._need_snapshot():
            return
        r = self.regions.target_frame
        if not r.is_valid():
            QMessageBox.warning(self, "Шаблон", "Выделите область «Рамка цели (шаблон)».")
            return
        if vision.save_target_template(self.snapshot, r):
            self.use_template.setChecked(True)
            QMessageBox.information(self, "Шаблон", "Шаблон рамки цели сохранён.")

    # ------------------------------------------------------------------
    # Проверка
    # ------------------------------------------------------------------
    def _format_state(self, st: vision.VisionState) -> str:
        def pct(v):
            return "<span style='color:#ff6b6b'>ошибка</span>" if v is None else f"<b>{v:.1f}%</b>"
        lines = [
            f"HP: {pct(st.hp_raw)} (достоверность {st.hp_conf:.2f})",
            f"MP: {pct(st.mp_raw)} (достоверность {st.mp_conf:.2f})",
        ]
        if st.target_supported:
            tgt = (f"<b style='color:#ffb347'>выбрана, HP {st.target_hp:.1f}%</b>"
                   if st.target_present else "нет")
            lines.append(f"Цель: {tgt}")
            if st.frame_score is not None:
                lines.append(f"Совпадение рамки: {st.frame_score:.2f}")
        else:
            lines.append("Цель: <span style='color:#f0b54a'>полоса цели не задана</span>")
        if st.motion is not None:
            lines.append(f"Изменение мини-карты: {st.motion:.2f} (порог {self.current_cfg().vision.motion_threshold})")
        if st.hp_slot_ready is not None:
            lines.append(f"Слот HP: {'готов' if st.hp_slot_ready else 'перезарядка'}")
        if st.mp_slot_ready is not None:
            lines.append(f"Слот MP: {'готов' if st.mp_slot_ready else 'перезарядка'}")
        if st.size_mismatch:
            lines.append("<span style='color:#f0b54a'>Размер окна отличается от калибровки</span>")
        return "<br>".join(lines)

    def test_snapshot(self):
        if self.snapshot is None:
            return
        cfg = self.current_cfg()
        v = vision.Vision()
        st = v.analyze(self.snapshot, cfg, track_motion=False)
        self.result.setText(self._format_state(st))
        self._refresh_canvas(st)

    def _toggle_live(self, on: bool):
        if on:
            self.btn_live.setText("■ Остановить тест")
            self.vision.reset()
            self.live_timer.start()
        else:
            self.btn_live.setText("▶ Живой тест")
            self.live_timer.stop()

    def _live_tick(self):
        img = self.grab_frame()
        if img is None:
            self.result.setText("<span style='color:#ff6b6b'>Окно игры не найдено</span>")
            return
        self.set_snapshot(img, emit=False)
        st = self.vision.analyze(img, self.current_cfg(), track_motion=True)
        self.result.setText(self._format_state(st))
        self._refresh_canvas(st)

    def stop_live(self):
        self.btn_live.setChecked(False)
