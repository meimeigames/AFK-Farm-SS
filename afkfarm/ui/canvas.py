"""Холст для снимка игры: выделение областей мышью, точки маршрута, зум и панорама.

* ЛКМ + перетаскивание — выделить прямоугольник;
* ЛКМ щелчок — поставить точку;
* колесо мыши — масштаб (вокруг курсора);
* ПКМ / средняя кнопка + перетаскивание — сдвиг изображения;
* двойной щелчок ПКМ — вписать изображение в окно.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..config import Rect
from .widgets import bgr_to_qimage


@dataclass
class CanvasRegion:
    name: str
    rect: Rect
    color: QColor
    label: str
    highlight: bool = False


@dataclass
class CanvasPoint:
    x: int
    y: int
    color: QColor
    label: str
    cross: bool = False


class RegionCanvas(QWidget):
    rectDrawn = Signal(object)      # Rect (координаты изображения)
    pointClicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.image: Optional[QImage] = None
        self.regions: List[CanvasRegion] = []
        self.points: List[CanvasPoint] = []
        self.overlay_lines: List[Tuple[int, int, int, int, QColor]] = []
        self.scale = 1.0
        self.offset = QPointF(0, 0)
        self._fitted = True
        self._drag_start: Optional[QPointF] = None
        self._drag_now: Optional[QPointF] = None
        self._pan_start: Optional[QPointF] = None
        self._pan_offset0 = QPointF(0, 0)
        self._mouse_img: Optional[Tuple[int, int]] = None
        self.placeholder = "Нет снимка. Нажмите «Снимок окна игры» или откройте изображение."
        self.allow_rect = True
        self.label_all = False

    # ------------------------------------------------------------------
    def set_image(self, img: Optional[np.ndarray]) -> None:
        self.image = bgr_to_qimage(img) if img is not None else None
        self._fitted = True
        self.fit()
        self.update()

    def image_size(self) -> Optional[Tuple[int, int]]:
        if self.image is None:
            return None
        return self.image.width(), self.image.height()

    def fit(self) -> None:
        if self.image is None:
            return
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        self.scale = max(0.05, s)
        self.offset = QPointF((self.width() - iw * self.scale) / 2, (self.height() - ih * self.scale) / 2)

    def resizeEvent(self, ev):
        if self._fitted:
            self.fit()
        super().resizeEvent(ev)

    def to_image(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.scale, (p.y() - self.offset.y()) / self.scale)

    def to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(self.offset.x() + x * self.scale, self.offset.y() + y * self.scale)

    # ------------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#101114"))
        if self.image is None:
            p.setPen(QColor("#7d838c"))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale < 1.0)
        target = QRectF(self.offset.x(), self.offset.y(),
                        self.image.width() * self.scale, self.image.height() * self.scale)
        p.drawImage(target, self.image)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(self.font())
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        for r in self.regions:
            if not r.rect.is_valid():
                continue
            tl = self.to_widget(r.rect.x, r.rect.y)
            rr = QRectF(tl.x(), tl.y(), r.rect.w * self.scale, r.rect.h * self.scale)
            pen = QPen(r.color, 3 if r.highlight else 1.5)
            if r.highlight:
                pen.setStyle(Qt.SolidLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(rr)
            if r.label and (r.highlight or self.label_all):
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(r.label) + 8
                bg = QRectF(rr.x(), rr.y() - fm.height() - 4, tw, fm.height() + 2)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawRect(bg)
                p.setPen(r.color)
                p.drawText(bg, Qt.AlignCenter, r.label)
                p.setBrush(Qt.NoBrush)
        for x0, y0, x1, y1, color in self.overlay_lines:
            p.setPen(QPen(color, 2))
            p.drawLine(self.to_widget(x0, y0), self.to_widget(x1, y1))
        prev = None
        for pt in self.points:
            c = self.to_widget(pt.x + 0.5, pt.y + 0.5)
            if not pt.cross and prev is not None:
                p.setPen(QPen(QColor(255, 255, 255, 120), 1.5, Qt.DashLine))
                p.drawLine(prev, c)
            if not pt.cross:
                prev = c
        for pt in self.points:
            c = self.to_widget(pt.x + 0.5, pt.y + 0.5)
            if pt.cross:
                p.setPen(QPen(pt.color, 2))
                p.drawLine(c + QPointF(-10, 0), c + QPointF(10, 0))
                p.drawLine(c + QPointF(0, -10), c + QPointF(0, 10))
                p.drawText(c + QPointF(8, -8), pt.label)
            else:
                p.setPen(QPen(QColor("#101114"), 1.5))
                p.setBrush(pt.color)
                p.drawEllipse(c, 9, 9)
                p.setPen(QColor("#101114"))
                p.drawText(QRectF(c.x() - 9, c.y() - 9, 18, 18), Qt.AlignCenter, pt.label)
        if self._drag_start is not None and self._drag_now is not None:
            p.setPen(QPen(QColor("#ffd54a"), 1.5, Qt.DashLine))
            p.setBrush(QColor(255, 213, 74, 40))
            p.drawRect(QRectF(self._drag_start, self._drag_now).normalized())
        if self._mouse_img is not None:
            x, y = self._mouse_img
            txt = f"x={x}  y={y}   масштаб {self.scale:.2f}×"
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRect(QRectF(6, self.height() - 26, 230, 20))
            p.setPen(QColor("#e6e6e6"))
            p.drawText(QRectF(10, self.height() - 26, 226, 20), Qt.AlignVCenter, txt)

    # ------------------------------------------------------------------
    def wheelEvent(self, ev):
        if self.image is None:
            return
        factor = 1.25 if ev.angleDelta().y() > 0 else 0.8
        pos = ev.position()
        img_pt = self.to_image(pos)
        new_scale = min(16.0, max(0.05, self.scale * factor))
        self.scale = new_scale
        self.offset = QPointF(pos.x() - img_pt.x() * new_scale, pos.y() - img_pt.y() * new_scale)
        self._fitted = False
        self.update()

    def mousePressEvent(self, ev):
        if self.image is None:
            return
        if ev.button() == Qt.LeftButton:
            self._drag_start = ev.position()
            self._drag_now = ev.position()
        elif ev.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan_start = ev.position()
            self._pan_offset0 = QPointF(self.offset)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() in (Qt.RightButton, Qt.MiddleButton):
            self._fitted = True
            self.fit()
            self.update()
        else:
            super().mouseDoubleClickEvent(ev)

    def mouseMoveEvent(self, ev):
        if self.image is None:
            return
        ip = self.to_image(ev.position())
        if 0 <= ip.x() < self.image.width() and 0 <= ip.y() < self.image.height():
            self._mouse_img = (int(ip.x()), int(ip.y()))
        else:
            self._mouse_img = None
        if self._pan_start is not None:
            d = ev.position() - self._pan_start
            self.offset = self._pan_offset0 + d
            self._fitted = False
        if self._drag_start is not None:
            self._drag_now = ev.position()
        self.update()

    def mouseReleaseEvent(self, ev):
        if self.image is None:
            return
        if ev.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan_start = None
            return
        if ev.button() != Qt.LeftButton or self._drag_start is None:
            return
        a = self.to_image(self._drag_start)
        b = self.to_image(ev.position())
        self._drag_start = None
        self._drag_now = None
        iw, ih = self.image.width(), self.image.height()
        x0 = int(max(0, min(a.x(), b.x())))
        y0 = int(max(0, min(a.y(), b.y())))
        x1 = int(min(iw, max(a.x(), b.x()) + 0.999))
        y1 = int(min(ih, max(a.y(), b.y()) + 0.999))
        dist = abs(ev.position().x() - (a.x() * self.scale + self.offset.x())) + \
            abs(ev.position().y() - (a.y() * self.scale + self.offset.y()))
        if self.allow_rect and (x1 - x0) >= 2 and (y1 - y0) >= 2 and dist > 4:
            self.rectDrawn.emit(Rect(x0, y0, x1 - x0, y1 - y0))
        elif 0 <= b.x() < iw and 0 <= b.y() < ih:
            self.pointClicked.emit(int(b.x()), int(b.y()))
        self.update()

    def leaveEvent(self, ev):
        self._mouse_img = None
        self.update()
        super().leaveEvent(ev)
