"""Общие виджеты и привязка виджетов к полям конфига."""

from __future__ import annotations

import html
import logging
import sys
from typing import Any, Callable, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import keys

# ---------------------------------------------------------------------------
# Поле ввода клавиши
# ---------------------------------------------------------------------------

_QT_KEY_NAMES = {}


def _build_qt_map():
    K = Qt.Key
    m = {}
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        m[getattr(K, f"Key_{c}")] = c
    for d in "0123456789":
        m[getattr(K, f"Key_{d}")] = d
    for i in range(1, 13):
        m[getattr(K, f"Key_F{i}")] = f"F{i}"
    m.update({
        K.Key_Tab: "TAB", K.Key_Backtab: "TAB", K.Key_Escape: "ESC", K.Key_Space: "SPACE",
        K.Key_Return: "ENTER", K.Key_Enter: "ENTER", K.Key_Backspace: "BACKSPACE",
        K.Key_Insert: "INSERT", K.Key_Delete: "DELETE", K.Key_Home: "HOME", K.Key_End: "END",
        K.Key_PageUp: "PAGEUP", K.Key_PageDown: "PAGEDOWN", K.Key_Left: "LEFT", K.Key_Right: "RIGHT",
        K.Key_Up: "UP", K.Key_Down: "DOWN", K.Key_Minus: "MINUS", K.Key_Equal: "EQUALS",
        K.Key_BracketLeft: "LBRACKET", K.Key_BracketRight: "RBRACKET", K.Key_Semicolon: "SEMICOLON",
        K.Key_Apostrophe: "QUOTE", K.Key_QuoteLeft: "BACKQUOTE", K.Key_Backslash: "BACKSLASH",
        K.Key_Comma: "COMMA", K.Key_Period: "PERIOD", K.Key_Slash: "SLASH", K.Key_CapsLock: "CAPSLOCK",
    })
    return m


class KeyEdit(QLineEdit):
    """Поле, в которое клавиша задаётся нажатием (включая TAB и сочетания)."""

    keyChanged = Signal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        global _QT_KEY_NAMES
        if not _QT_KEY_NAMES:
            _QT_KEY_NAMES = _build_qt_map()
        self.setObjectName("keyEdit")
        self.setPlaceholderText("нажмите клавишу…")
        self.setClearButtonEnabled(True)
        self.setToolTip("Щёлкните и нажмите клавишу или сочетание (CTRL/SHIFT/ALT + клавиша).\n"
                        "Крестик справа — очистить.")
        self.setMinimumWidth(110)
        self.textChanged.connect(self._on_text)
        self.setKey(text)

    def key(self) -> str:
        return self.text().strip()

    def setKey(self, text: str) -> None:
        self.setText(text or "")
        self._validate()

    def _on_text(self, _t: str) -> None:
        self._validate()
        self.keyChanged.emit(self.key())

    def _validate(self) -> None:
        t = self.key()
        invalid = bool(t) and not keys.is_valid_combo(t)
        self.setProperty("invalid", "true" if invalid else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def event(self, ev):
        # Перехватываем TAB, иначе Qt переключит фокус
        if ev.type() == QEvent.KeyPress:
            self.keyPressEvent(ev)
            return True
        return super().event(ev)

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        k = ev.key()
        if k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr):
            return
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)
        name = None
        if sys.platform == "win32":
            name = keys.combo_from_vk(ev.nativeVirtualKey(), ctrl, shift, alt)
        if name is None:
            base = _QT_KEY_NAMES.get(k)
            if base is not None and (mods & Qt.KeypadModifier) and base.isdigit():
                base = f"NUM{base}"
            if base is not None:
                if k == Qt.Key_Backtab:
                    shift = True
                parts = [m for m, on in (("CTRL", ctrl), ("SHIFT", shift), ("ALT", alt)) if on]
                name = "+".join(parts + [base])
        if name:
            self.setKey(name)
            self.selectAll()

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self.selectAll()


# ---------------------------------------------------------------------------
# Числовые поля
# ---------------------------------------------------------------------------

def dspin(minimum: float, maximum: float, step: float = 0.1, decimals: int = 2,
          suffix: str = "", tooltip: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(minimum, maximum)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    if suffix:
        w.setSuffix(suffix)
    if tooltip:
        w.setToolTip(tooltip)
    w.setKeyboardTracking(False)
    w.setMinimumWidth(90)
    return w


def ispin(minimum: int, maximum: int, step: int = 1, suffix: str = "", tooltip: str = "") -> QSpinBox:
    w = QSpinBox()
    w.setRange(minimum, maximum)
    w.setSingleStep(step)
    if suffix:
        w.setSuffix(suffix)
    if tooltip:
        w.setToolTip(tooltip)
    w.setKeyboardTracking(False)
    w.setMinimumWidth(80)
    return w


def check(text: str, tooltip: str = "") -> QCheckBox:
    w = QCheckBox(text)
    if tooltip:
        w.setToolTip(tooltip)
    return w


def combo(items: List[Tuple[str, Any]], tooltip: str = "") -> QComboBox:
    w = QComboBox()
    for label, data in items:
        w.addItem(label, data)
    if tooltip:
        w.setToolTip(tooltip)
    return w


def hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("hint")
    lb.setWordWrap(True)
    return lb


def group(title: str, layout=None) -> Tuple[QGroupBox, Any]:
    box = QGroupBox(title)
    lay = layout or QFormLayout()
    if isinstance(lay, QFormLayout):
        lay.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.setHorizontalSpacing(12)
        lay.setVerticalSpacing(7)
    box.setLayout(lay)
    return box, lay


def scroll_wrap(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(widget)
    return sa


def row(*widgets, stretch: bool = True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


# ---------------------------------------------------------------------------
# Привязка виджетов к полям конфига
# ---------------------------------------------------------------------------

def get_path(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def set_path(obj, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


class Binder:
    """Двусторонняя связь «виджет ↔ поле конфига» по пути вида ``combat.target_key``."""

    def __init__(self, on_change: Callable[[], None]):
        self.items: List[Tuple[str, QWidget]] = []
        self.on_change = on_change
        self.loading = False

    def add(self, path: str, widget: QWidget) -> QWidget:
        self.items.append((path, widget))
        if isinstance(widget, KeyEdit):
            widget.keyChanged.connect(self._changed)
        elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
            widget.valueChanged.connect(self._changed)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._changed)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._changed)
        elif isinstance(widget, QLineEdit):
            widget.editingFinished.connect(self._changed)
        return widget

    def _changed(self, *_):
        if not self.loading:
            self.on_change()

    def load(self, cfg) -> None:
        self.loading = True
        try:
            for path, w in self.items:
                v = get_path(cfg, path)
                if isinstance(w, KeyEdit):
                    w.setKey(str(v))
                elif isinstance(w, QDoubleSpinBox):
                    w.setValue(float(v))
                elif isinstance(w, QSpinBox):
                    w.setValue(int(v))
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QComboBox):
                    i = w.findData(v)
                    w.setCurrentIndex(max(0, i))
                elif isinstance(w, QLineEdit):
                    w.setText(str(v))
        finally:
            self.loading = False

    def apply(self, cfg) -> None:
        for path, w in self.items:
            if isinstance(w, KeyEdit):
                v = w.key()
            elif isinstance(w, QDoubleSpinBox):
                v = float(w.value())
            elif isinstance(w, QSpinBox):
                v = int(w.value())
            elif isinstance(w, QCheckBox):
                v = bool(w.isChecked())
            elif isinstance(w, QComboBox):
                v = w.currentData()
            elif isinstance(w, QLineEdit):
                v = w.text()
            else:
                continue
            set_path(cfg, path, v)


# ---------------------------------------------------------------------------
# Журнал и изображения
# ---------------------------------------------------------------------------

_LEVEL_COLORS = {
    logging.DEBUG: "#7d838c",
    logging.INFO: "#d6d6d6",
    logging.WARNING: "#f0b54a",
    logging.ERROR: "#ff6b6b",
    logging.CRITICAL: "#ff3b3b",
}


class LogView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        f = self.font()
        f.setFamily("Consolas")
        f.setStyleHint(f.StyleHint.Monospace)
        self.setFont(f)

    def add(self, msg: str, level: int) -> None:
        color = _LEVEL_COLORS.get(level, "#d6d6d6")
        self.appendHtml(f'<span style="color:{color}">{html.escape(msg)}</span>')
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


def bgr_to_qimage(img: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(img[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def bgr_to_pixmap(img: np.ndarray) -> QPixmap:
    return QPixmap.fromImage(bgr_to_qimage(img))
