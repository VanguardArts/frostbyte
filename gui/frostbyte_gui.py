#!/usr/bin/env python3
#
# FrostByte - Open-source fan & thermal control for Acer Nitro/Predator laptops
# Copyright (C) 2026 FrostByte contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
"""FrostByte GUI - NitroSense-style dashboard for the FrostByte daemon."""

import math
import os
import shlex
import subprocess
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSettings, QSize, Qt,
    QTimer, pyqtProperty, pyqtSignal, QObject,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

import frostbyte_common as fb
import frostbyte_gpu as fb_gpu

SINGLE_INSTANCE_NAME = f"frostbyte-gui-{os.getuid()}"

# --- Themes (dark mode only) -------------------------------------------------
# accent = primary highlight, accent2 = secondary (GPU), danger = warnings/MAX
THEMES = {
    "nitro": {
        "label": "Nitro Red",
        "accent": "#ff3b47", "accent2": "#ffb84d",
        "bg": "#100a0c", "card": "#1b1114", "edge": "#331920",
        "text": "#eeddde", "muted": "#947279", "danger": "#ff8a5c",
    },
    "ember": {
        "label": "Ember",
        "accent": "#ffb02e", "accent2": "#ff6b4a",
        "bg": "#110d08", "card": "#1c1510", "edge": "#33261a",
        "text": "#ede2d2", "muted": "#9a8874", "danger": "#ff5964",
    },
    "solar": {
        "label": "Solar Yellow",
        "accent": "#ffd60a", "accent2": "#ff9f1c",
        "bg": "#121008", "card": "#1d190f", "edge": "#3a3016",
        "text": "#efe6cf", "muted": "#9c906d", "danger": "#ff5964",
    },
    "volt": {
        "label": "Volt Green",
        "accent": "#46d369", "accent2": "#5ecbff",
        "bg": "#0b0f08", "card": "#131a10", "edge": "#263317",
        "text": "#e4ecd8", "muted": "#81937a", "danger": "#ff5964",
    },
    "glacier": {
        "label": "Glacier (default)",
        "accent": "#5ecbff", "accent2": "#9dff70",
        "bg": "#0b0e14", "card": "#151a23", "edge": "#232a36",
        "text": "#d7dde6", "muted": "#7d8794", "danger": "#ff5964",
    },
    "abyss": {
        "label": "Abyss Indigo",
        "accent": "#818cf8", "accent2": "#38bdf8",
        "bg": "#0a0b14", "card": "#14162a", "edge": "#272b47",
        "text": "#dfe2f2", "muted": "#7e84a8", "danger": "#ff5964",
    },
    "ultraviolet": {
        "label": "Ultraviolet",
        "accent": "#a78bfa", "accent2": "#f472b6",
        "bg": "#0d0a14", "card": "#171126", "edge": "#2b2142",
        "text": "#e5dff0", "muted": "#897ea6", "danger": "#ff5964",
    },
}
DEFAULT_THEME = "glacier"

GAUGE_MAX_RPM = 7000.0
AMBER = "#ffb84d"


def _rgba(hexcolor, alpha):
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def make_stylesheet(t):
    """Build the full QSS stylesheet for a theme dict."""
    return f"""
QMainWindow, QWidget {{ background: {t['bg']}; color: {t['text']};
    font-family: 'Noto Sans', sans-serif; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['edge']};
    border-radius: 12px; }}
QLabel#big {{ font-size: 30px; font-weight: 700; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel#title {{ font-size: 13px; font-weight: 700; letter-spacing: 2px;
    color: {t['muted']}; }}
QPushButton {{ background: #1c2230; border: 1px solid {t['edge']};
    border-radius: 8px; padding: 8px 14px; color: {t['text']}; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:checked {{ background: {t['accent']}; color: #04121c;
    font-weight: 700; }}
QPushButton#tile {{ background: {_rgba(t['card'], 220)};
    border: 1px solid {t['edge']}; border-radius: 10px;
    padding: 8px 14px 8px 16px; font-weight: 600; text-align: left; }}
QPushButton#tile:hover {{ border-color: {t['accent']}; }}
QPushButton#tile:checked {{ border: 2px solid {t['accent']};
    background: {_rgba(t['accent'], 34)}; color: {t['text']}; }}
QPushButton#danger:checked {{ background: {t['danger']}; color: white;
    border-color: {t['danger']}; }}
QSlider::groove:horizontal {{ height: 6px; background: #232a36;
    border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -7px 0;
    border-radius: 9px; background: {t['accent']}; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 3px; }}
QSlider:disabled::handle:horizontal {{ background: #3a4250; }}
QTableWidget {{ background: {t['card']}; gridline-color: {t['edge']};
    border: 1px solid {t['edge']}; border-radius: 8px; }}
QHeaderView::section {{ background: #1c2230; color: {t['muted']};
    border: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QSpinBox {{ background: #1c2230; border: 1px solid {t['edge']};
    border-radius: 6px; padding: 4px 20px 4px 8px; min-height: 26px;
    selection-background-color: {t['accent']}; }}
QSpinBox::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 20px; height: 13px;
    background: #232a36; border: none; border-left: 1px solid {t['edge']};
    border-bottom: 1px solid {t['edge']}; border-top-right-radius: 5px; }}
QSpinBox::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 20px; height: 13px;
    background: #232a36; border: none; border-left: 1px solid {t['edge']};
    border-bottom-right-radius: 5px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: #2e3746; }}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
    background: {t['accent']}; }}
QSpinBox::up-arrow {{ width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-bottom: 6px solid {t['text']}; }}
QSpinBox::up-arrow:hover {{ border-bottom-color: {t['accent']}; }}
QSpinBox::down-arrow {{ width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid {t['text']}; }}
QSpinBox::down-arrow:hover {{ border-top-color: {t['accent']}; }}
QToolButton#adv {{ color: {t['muted']}; border: none; font-weight: 700;
    letter-spacing: 1px; padding: 8px 4px; text-align: left; }}
QToolButton#adv:hover {{ color: {t['accent']}; }}
QLabel#gpuval {{ font-size: 17px; font-weight: 700; color: {t['accent']}; }}
"""

STYLESHEET = make_stylesheet(THEMES[DEFAULT_THEME])


def temp_color(t, theme):
    if t is None:
        return theme["muted"]
    if t >= 85:
        return theme["danger"]
    if t >= 72:
        return AMBER
    return theme["text"]


def icon_pixmap(kind, color, size=26):
    """Draw a small line icon (no font/emoji dependency)."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if kind == "auto":           # two opposing arrows
        p.drawLine(6, 10, 19, 10)
        p.drawLine(19, 10, 15, 7)
        p.drawLine(19, 10, 15, 13)
        p.drawLine(20, 16, 7, 16)
        p.drawLine(7, 16, 11, 13)
        p.drawLine(7, 16, 11, 19)
    elif kind == "curve":        # rising polyline with end dots
        p.drawPolyline([QPointF(5, 19), QPointF(11, 15),
                        QPointF(16, 10), QPointF(21, 6)])
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(5, 19), 2.2, 2.2)
        p.drawEllipse(QPointF(21, 6), 2.2, 2.2)
    elif kind == "manual":       # three faders
        for y, kx in ((7, 10), (13, 16), (19, 9)):
            p.drawLine(5, y, 21, y)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(kx, y), 2.6, 2.6)
    elif kind == "max":          # triple chevron up
        for y in (18, 12, 6):
            p.drawPolyline([QPointF(7, y), QPointF(13, y - 3),
                            QPointF(19, y)])
    p.end()
    return QIcon(pm)


class DaemonBridge(QObject):
    """Polls the daemon on a timer and emits status dicts."""
    status = pyqtSignal(dict)
    lost = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.ok = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.poll()

    def poll(self):
        try:
            self.status.emit(fb.get_status(timeout=2.0))
            self.ok = True
        except fb.DaemonError:
            if self.ok:
                self.ok = False
            self.lost.emit()


class AngledFrame(QFrame):
    """Panel with sci-fi cut corners (top-right + bottom-left)."""

    def __init__(self, theme, cut=14, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.cut = cut

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, _event):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -2, -2)
        c = self.cut
        path = QPainterPath()
        path.moveTo(r.left(), r.top())
        path.lineTo(r.right() - c, r.top())
        path.lineTo(r.right(), r.top() + c)
        path.lineTo(r.right(), r.bottom())
        path.lineTo(r.left() + c, r.bottom())
        path.lineTo(r.left(), r.bottom() - c)
        path.closeSubpath()
        p.fillPath(path, QColor(t["card"]))
        p.setPen(QPen(QColor(t["edge"]), 1.5))
        p.drawPath(path)
        # accent notch on the top edge
        p.setPen(QPen(QColor(t["accent"]), 2.5))
        p.drawLine(r.left() + 14, r.top() + 1, r.left() + 52, r.top() + 1)
        p.end()


class GaugeWidget(QWidget):
    """NitroSense-style circular gauge: tick ring + arc + big RPM readout."""

    def __init__(self, title, theme, parent=None):
        super().__init__(parent)
        self.title = title
        self.theme = theme
        self._val = 0.0              # 0.0 .. 1.0, animated
        self.rpm = None
        self.temp = None
        self.setting = None
        self.anim = QPropertyAnimation(self, b"displayValue", self)
        self.anim.setDuration(650)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMinimumSize(240, 260)

    def _get(self):
        return self._val

    def _set(self, v):
        self._val = v
        self.update()

    displayValue = pyqtProperty(float, fget=_get, fset=_set)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_state(self, rpm, temp, setting):
        if rpm is not None:
            frac = rpm / GAUGE_MAX_RPM
        else:
            frac = (setting or 0) / 100.0
        self.rpm, self.temp, self.setting = rpm, temp, setting
        self.anim.stop()
        self.anim.setStartValue(self._val)
        self.anim.setEndValue(max(0.0, min(1.0, frac)))
        self.anim.start()

    def paintEvent(self, _event):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2.0, self.height() / 2.0 + 6
        radius = side / 2.0 - 22
        ticks = 48

        # tick ring
        for i in range(ticks):
            deg = 225.0 - i * (270.0 / (ticks - 1))
            a = math.radians(deg)
            lit = (i / (ticks - 1)) <= self._val
            arc_col = t["danger"] if self._val > 0.85 else t["accent"]
            p.setPen(QPen(QColor(arc_col if lit else t["edge"]),
                          3.0 if lit else 2.0))
            x1 = cx + (radius - 9) * math.cos(a)
            y1 = cy - (radius - 9) * math.sin(a)
            x2 = cx + radius * math.cos(a)
            y2 = cy - radius * math.sin(a)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # value arc
        if self._val > 0.004:
            ar = radius - 18
            arc_col = t["danger"] if self._val > 0.85 else t["accent"]
            p.setPen(QPen(QColor(arc_col), 5.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx - ar, cy - ar, 2 * ar, 2 * ar),
                      225 * 16, int(-self._val * 270 * 16))

        # readouts
        big = "--" if self.rpm is None else str(self.rpm)
        f = QFont("Noto Sans", int(radius * 0.30), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(t["text"]))
        p.drawText(QRectF(cx - radius, cy - radius * 0.42,
                          2 * radius, radius * 0.45),
                   Qt.AlignmentFlag.AlignHCenter, big)

        p.setFont(QFont("Noto Sans", int(radius * 0.11)))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(cx - radius, cy - radius * 0.02,
                          2 * radius, radius * 0.22),
                   Qt.AlignmentFlag.AlignHCenter, "RPM")

        temp = self.temp
        tcol = temp_color(temp, t)
        p.setFont(QFont("Noto Sans", int(radius * 0.13), QFont.Weight.Bold))
        p.setPen(QColor(tcol))
        tstr = "--.-\N{DEGREE SIGN}C" if temp is None else f"{temp:.1f}\N{DEGREE SIGN}C"
        p.drawText(QRectF(cx - radius, cy + radius * 0.18,
                          2 * radius, radius * 0.24),
                   Qt.AlignmentFlag.AlignHCenter, tstr)

        mode = "AUTO" if self.setting in (None, 0) else f"SET {self.setting}%"
        p.setFont(QFont("Noto Sans", int(radius * 0.085)))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(cx - radius, cy + radius * 0.44,
                          2 * radius, radius * 0.2),
                   Qt.AlignmentFlag.AlignHCenter, mode)

        # title under the dial
        p.setFont(QFont("Noto Sans", 12, QFont.Weight.Bold))
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(0, self.height() - 24, self.width(), 20),
                   Qt.AlignmentFlag.AlignHCenter, self.title)
        p.end()


class Sparkline(QWidget):
    """60s history graph for one or two series, auto-scaled to the data."""

    def __init__(self, colors, theme=None, parent=None):
        super().__init__(parent)
        self.colors = colors
        theme = theme or THEMES[DEFAULT_THEME]
        self.card = theme["card"]
        self.edge = theme["edge"]
        self.muted = theme["muted"]
        self.series = [deque(maxlen=60) for _ in colors]
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_theme(self, theme):
        self.colors = [theme["accent"], theme["accent2"]]
        self.card = theme["card"]
        self.edge = theme["edge"]
        self.muted = theme["muted"]
        self.update()

    def push(self, *values):
        for series, val in zip(self.series, values):
            series.append(val)
        self.update()

    def _scale(self):
        vals = [v for series in self.series for v in series if v is not None]
        if not vals:
            return 20.0, 90.0
        lo, hi = min(vals) - 4.0, max(vals) + 4.0
        if hi - lo < 12.0:
            pad = (12.0 - (hi - lo)) / 2.0
            lo, hi = lo - pad, hi + pad
        return max(0.0, lo), min(110.0, hi)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(self.card))
        lo, hi = self._scale()

        def ymap(v):
            frac = (max(lo, min(hi, v)) - lo) / (hi - lo)
            return h - 6 - frac * (h - 14)

        # gridlines at quartiles + tiny scale bounds on the left
        p.setPen(QPen(QColor(self.edge), 1))
        for frac in (0.25, 0.5, 0.75):
            y = int(6 + frac * (h - 14))
            p.drawLine(0, y, w, y)
        p.setFont(QFont("Noto Sans", 8))
        p.setPen(QColor(self.muted))
        p.drawText(QRectF(6, 4, 70, 12), Qt.AlignmentFlag.AlignLeft,
                   f"{hi:.0f}\N{DEGREE SIGN}C")
        p.drawText(QRectF(6, h - 16, 70, 12), Qt.AlignmentFlag.AlignLeft,
                   f"{lo:.0f}\N{DEGREE SIGN}C")

        # series: soft gradient fill under a 2px line
        step = w / 59.0
        for series, color in zip(self.series, self.colors):
            segments, cur = [], []
            for i, val in enumerate(series):
                if val is None:
                    if cur:
                        segments.append(cur)
                        cur = []
                    continue
                cur.append(QPointF(i * step, ymap(val)))
            if cur:
                segments.append(cur)
            for seg in segments:
                if len(seg) < 2:
                    continue
                grad = QLinearGradient(0, 0, 0, h)
                c = QColor(color)
                c.setAlpha(42)
                grad.setColorAt(0, c)
                c.setAlpha(0)
                grad.setColorAt(1, c)
                poly = QPolygonF(seg + [QPointF(seg[-1].x(), h - 3),
                                        QPointF(seg[0].x(), h - 3)])
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(grad))
                p.drawPolygon(poly)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(color), 2))
                path = QPainterPath(seg[0])
                for pt in seg[1:]:
                    path.lineTo(pt)
                p.drawPath(path)
        # per-series readouts: current value, min/max of the window
        p.setFont(QFont("Noto Sans", 9, QFont.Weight.Bold))
        off = 4
        for series, color in zip(self.series, self.colors):
            vals = [v for v in series if v is not None]
            if not vals:
                continue
            p.setPen(QColor(color))
            p.drawText(QRectF(self.width() - 150, off, 142, 14),
                       Qt.AlignmentFlag.AlignRight,
                       f"{vals[-1]:.0f}\N{DEGREE SIGN}C   "
                       f"min {min(vals):.0f}  max {max(vals):.0f}")
            off += 16
        p.end()


class ModeTile(QPushButton):
    """Checkable icon tile for a fan mode."""

    def __init__(self, key, label, kind, theme, parent=None):
        super().__init__(label, parent)
        self.key = key
        self.kind = kind
        self.setObjectName("danger" if kind == "max" else "tile")
        self.setCheckable(True)
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_theme(theme)

    def set_theme(self, theme):
        color = theme["danger"] if self.kind == "max" else theme["accent"]
        self.setIcon(icon_pixmap(self.kind, color))
        self.setIconSize(QSize(26, 26))


class SliderRow(QWidget):
    """One labeled fan slider (manual mode)."""
    changed = pyqtSignal(int)

    def __init__(self, title, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedHeight(36)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel(title)
        self.head.setFixedWidth(70)
        self.head.setStyleSheet(f"color: {theme['accent']}; font-weight: 700;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self._lab)
        self.slider.sliderReleased.connect(
            lambda: self.changed.emit(self.slider.value()))
        self.val = QLabel("AUTO")
        self.val.setFixedWidth(46)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.head)
        row.addWidget(self.slider, 1)
        row.addWidget(self.val)

    def _lab(self, v):
        self.val.setText("AUTO" if v == 0 else f"{v}%")

    def set_theme(self, theme):
        self.theme = theme
        self.head.setStyleSheet(f"color: {theme['accent']}; font-weight: 700;")

    def set_state(self, setting, enabled):
        self.slider.setEnabled(enabled)
        if enabled and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(setting or 0)
            self.slider.blockSignals(False)
            self._lab(setting or 0)
        if not enabled:
            self._lab(setting or 0)


class CurveEditor(QFrame):
    applyRequested = pyqtSignal(str, list)

    def __init__(self, which, title):
        super().__init__()
        self.which = which
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setObjectName("title")
        lay.addWidget(head)
        self.table = QTableWidget(6, 2)
        self.table.setHorizontalHeaderLabels(["Temp °C", "Fan %"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        btn = QPushButton("Apply curve")
        btn.clicked.connect(self._apply)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

    def set_points(self, points):
        self.table.setRowCount(max(6, len(points)))
        for row in range(self.table.rowCount()):
            for col in range(2):
                item = QTableWidgetItem("")
                if row < len(points):
                    item.setText(str(points[row][col]))
                self.table.setItem(row, col, item)

    def _apply(self):
        pts = []
        for row in range(self.table.rowCount()):
            t = self.table.item(row, 0)
            p = self.table.item(row, 1)
            if t and p and t.text().strip() and p.text().strip():
                try:
                    pts.append([int(float(t.text())), int(float(p.text()))])
                except ValueError:
                    pass
        if len(pts) >= 2:
            self.applyRequested.emit(self.which, pts)
        else:
            QMessageBox.warning(self, "FrostByte",
                                "A curve needs at least two points.")


class MainWindow(QMainWindow):
    def __init__(self, theme=None):
        super().__init__()
        self.theme_key = theme if theme in THEMES else DEFAULT_THEME
        self.theme = THEMES[self.theme_key]
        self.setWindowTitle("FrostByte")
        self.resize(1000, 800)
        self.setMinimumSize(920, 520)
        self.bridge = DaemonBridge()
        self.bridge.status.connect(self.on_status)
        self.bridge.lost.connect(self.on_lost)
        self._syncing = False
        self._curves_loaded = False
        self._build_ui()
        self._build_tray()
        self._start_instance_server()
        self.theme_btns[self.theme_key].setChecked(True)
        self._gpu_timer = QTimer(self)
        self._gpu_timer.timeout.connect(self._gpu_poll)
        self._gpu_timer.start(2500)
        self._gpu_poll()

    # ---------------------------------------------- single instance / toggle

    def _start_instance_server(self):
        self.server = QLocalServer(self)
        QLocalServer.removeServer(SINGLE_INSTANCE_NAME)  # stale socket
        if self.server.listen(SINGLE_INSTANCE_NAME):
            self.server.newConnection.connect(self._on_instance_msg)

    def _on_instance_msg(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._toggle(s))

    def _toggle(self, sock):
        sock.readAll()
        sock.disconnectFromServer()
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event):
        # Closing hides to the tray instead of quitting (Quit lives in the
        # tray menu) so the NitroSense key can always bring the window back.
        if self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.accept()

    def apply_theme(self, key):
        """Switch themes live and remember the choice per user."""
        if key not in THEMES:
            return
        self.theme_key = key
        self.theme = THEMES[key]
        QApplication.instance().setStyleSheet(make_stylesheet(self.theme))
        self.logo.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {self.theme['accent']};")
        for tile in self.mode_tiles.values():
            tile.set_theme(self.theme)
        self.cpu_gauge.set_theme(self.theme)
        self.gpu_gauge.set_theme(self.theme)
        for frame in self._frames:
            frame.set_theme(self.theme)
        self.cpu_row.set_theme(self.theme)
        self.gpu_row.set_theme(self.theme)
        self.graph.set_theme(self.theme)
        for k, b in self.theme_btns.items():
            b.setChecked(k == key)
        QSettings("FrostByte", "FrostByte").setValue("theme", key)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self._frames = []
        root = QWidget()
        lay = QVBoxLayout(root)

        # Header
        head = QHBoxLayout()
        self.logo = QLabel("❄ FrostByte")
        self.logo.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {self.theme['accent']};")
        head.addWidget(self.logo)
        head.addStretch(1)
        head.addWidget(QLabel("Theme:"))
        self.theme_btns = {}
        for key, t in THEMES.items():
            sw = QPushButton()
            sw.setCheckable(True)
            sw.setToolTip(t["label"])
            sw.setFixedSize(30, 30)
            lite = QColor(t["accent"]).lighter(140).name()
            sw.setStyleSheet(
                "QPushButton { border-radius: 15px; border: 2px solid #2a3140;"
                f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                f" stop:0 {t['accent']}, stop:1 {lite}); }}"
                "QPushButton:checked { border: 2px solid #ffffff; }")
            sw.clicked.connect(lambda _c, k=key: self.apply_theme(k))
            self.theme_btns[key] = sw
            head.addWidget(sw)
        head.addSpacing(12)
        self.power_lbl = QLabel("")
        self.power_lbl.setObjectName("muted")
        head.addWidget(self.power_lbl)
        lay.addLayout(head)

        # Body: left control column + right gauges
        body = QHBoxLayout()

        left = QVBoxLayout()
        cap = QLabel("FAN MODE")
        cap.setObjectName("title")
        left.addWidget(cap)
        self.mode_tiles = {}
        for key, label, kind in (("auto", "Auto", "auto"),
                                 ("curve", "Curve", "curve"),
                                 ("manual", "Manual", "manual"),
                                 ("max", "MAX", "max")):
            tile = ModeTile(key, label, kind, self.theme)
            tile.clicked.connect(lambda _c, m=key: self._send(
                {"cmd": "set_mode", "mode": m}))
            self.mode_tiles[key] = tile
            left.addWidget(tile)

        left.addSpacing(14)
        cap2 = QLabel("THERMAL PROFILE")
        cap2.setObjectName("title")
        left.addWidget(cap2)
        self.profile_btns = {}
        for name in ("quiet", "balanced", "performance"):
            b = QPushButton(name.capitalize())
            b.setObjectName("tile")
            b.setCheckable(True)
            b.clicked.connect(lambda _c, n=name: self._send(
                {"cmd": "set_profile", "profile": n}))
            self.profile_btns[name] = b
            left.addWidget(b)
        left.addStretch(1)

        leftw = QWidget()
        leftw.setLayout(left)
        leftw.setFixedWidth(210)
        body.addWidget(leftw)

        right = QVBoxLayout()
        gauges = QHBoxLayout()
        self.cpu_gauge = GaugeWidget("CPU FAN", self.theme)
        self.gpu_gauge = GaugeWidget("GPU FAN", self.theme)
        for g in (self.cpu_gauge, self.gpu_gauge):
            frame = AngledFrame(self.theme)
            frame.setMinimumHeight(300)
            frame.setMaximumHeight(320)
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(12, 10, 12, 8)
            fl.addWidget(g)
            self._frames.append(frame)
            gauges.addWidget(frame)
        right.addLayout(gauges)

        self.cpu_row = SliderRow("CPU", self.theme)
        self.gpu_row = SliderRow("GPU", self.theme)
        self.cpu_row.changed.connect(lambda v: self._slider_changed("cpu", v))
        self.gpu_row.changed.connect(lambda v: self._slider_changed("gpu", v))
        hint = QLabel("Manual mode: 0% = firmware auto")
        hint.setObjectName("muted")
        right.addSpacing(8)
        right.addWidget(self.cpu_row)
        right.addWidget(self.gpu_row)
        right.addWidget(hint)
        right.addStretch(1)
        body.addLayout(right, 1)
        lay.addLayout(body)

        # Temperature history
        gframe = AngledFrame(self.theme)
        gl = QVBoxLayout(gframe)
        gl.setContentsMargins(14, 10, 14, 12)
        glab = QLabel("TEMPERATURE HISTORY — 60s")
        glab.setObjectName("title")
        gl.addWidget(glab)
        self.graph = Sparkline([self.theme["accent"], self.theme["accent2"]],
                               theme=self.theme)
        gl.addWidget(self.graph)
        self._frames.append(gframe)
        lay.addWidget(gframe, 1)

        # Dedicated GPU monitor + launch controls
        ggframe = AngledFrame(self.theme)
        ggl = QVBoxLayout(ggframe)
        ggl.setContentsMargins(14, 10, 14, 12)
        self.gpu_title = QLabel("DEDICATED GPU")
        self.gpu_title.setObjectName("title")
        ggl.addWidget(self.gpu_title)
        gstats = QHBoxLayout()
        self._gpu_vals = {}
        for key, label in (("temperature.gpu", "TEMP °C"),
                           ("power.draw", "POWER W"),
                           ("clocks.sm", "CORE MHz"),
                           ("utilization.gpu", "UTIL %"),
                           ("memory.used", "VRAM MB")):
            box = QVBoxLayout()
            v = QLabel("—")
            v.setObjectName("gpuval")
            l = QLabel(label)
            l.setObjectName("muted")
            box.addWidget(v)
            box.addWidget(l)
            self._gpu_vals[key] = v
            gstats.addLayout(box)
        gstats.addStretch(1)
        ggl.addLayout(gstats)
        self.gpu_procs = QLabel("no processes on dGPU")
        self.gpu_procs.setObjectName("muted")
        ggl.addWidget(self.gpu_procs)
        gbtns = QHBoxLayout()
        self.gpu_launch_btn = QPushButton("▶ Launch on RTX 3050…")
        self.gpu_launch_btn.clicked.connect(self._gpu_launch)
        self.gpu_park_btn = QPushButton("Park dGPU (persistence off)")
        self.gpu_park_btn.setToolTip(
            "Drop persistence mode so the driver can unload when idle. "
            "The full power-gate is the runtime-PM modprobe option (see README).")
        self.gpu_park_btn.clicked.connect(self._gpu_park)
        gbtns.addWidget(self.gpu_launch_btn)
        gbtns.addWidget(self.gpu_park_btn)
        gbtns.addStretch(1)
        ggl.addLayout(gbtns)
        self._frames.append(ggframe)
        lay.addWidget(ggframe)

        # Advanced (collapsible): curves + extras
        self.adv_btn = QToolButton()
        self.adv_btn.setObjectName("adv")
        self.adv_btn.setText("▸  ADVANCED — fan curves, battery && extras")
        self.adv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.adv_btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(self._toggle_advanced)
        lay.addWidget(self.adv_btn)

        self.adv_panel = QFrame()
        self.adv_panel.setObjectName("card")
        adv = QVBoxLayout(self.adv_panel)
        curves = QHBoxLayout()
        self.cpu_curve = CurveEditor("cpu", "CPU CURVE")
        self.gpu_curve = CurveEditor("gpu", "GPU CURVE")
        self.cpu_curve.applyRequested.connect(self._curve_apply)
        self.gpu_curve.applyRequested.connect(self._curve_apply)
        curves.addWidget(self.cpu_curve)
        curves.addWidget(self.gpu_curve)
        adv.addLayout(curves)
        tog = QHBoxLayout()
        self.limiter_chk = QCheckBox("Battery limiter (charge to 80%)")
        self.limiter_chk.stateChanged.connect(
            lambda st: self._send({"cmd": "set_limiter", "enabled": bool(st)}))
        self.follow_chk = QCheckBox("Profile follows power source")
        self.follow_chk.setChecked(True)
        self.follow_chk.stateChanged.connect(
            lambda st: self._send({"cmd": "set_follow_power",
                                   "enabled": bool(st)}))
        tog.addWidget(self.limiter_chk)
        tog.addWidget(self.follow_chk)
        tog.addStretch(1)
        adv.addLayout(tog)
        crit = QHBoxLayout()
        clab = QLabel("Safety override — fans to 100% above (sustained ~6s):")
        clab.setObjectName("muted")
        crit.addWidget(clab)
        self.cpu_crit = QSpinBox()
        self.cpu_crit.setRange(85, 105)
        self.cpu_crit.setPrefix("CPU ")
        self.cpu_crit.setSuffix(" °C")
        self.cpu_crit.setKeyboardTracking(False)
        self.cpu_crit.valueChanged.connect(self._crit_changed)
        self.gpu_crit = QSpinBox()
        self.gpu_crit.setRange(75, 100)
        self.gpu_crit.setPrefix("GPU ")
        self.gpu_crit.setSuffix(" °C")
        self.gpu_crit.setKeyboardTracking(False)
        self.gpu_crit.valueChanged.connect(self._crit_changed)
        crit.addWidget(self.cpu_crit)
        crit.addWidget(self.gpu_crit)
        crit.addStretch(1)
        adv.addLayout(crit)
        self.adv_panel.hide()
        lay.addWidget(self.adv_panel)

        # Wrap the whole dashboard in a scroll area so it pages vertically
        # instead of squishing/overlapping when the window is short or maximised.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)

        self.statusBar().showMessage("connecting to daemon…")

    def _toggle_advanced(self, checked):
        self.adv_panel.setVisible(checked)
        self.adv_btn.setText(("▾  " if checked else "▸  ") +
                             "ADVANCED — fan curves, battery && extras")
        # make room for the panel (and give it back) so the gauges keep
        # their shape instead of being squeezed by the layout
        dh = self.adv_panel.sizeHint().height() + 8
        if checked:
            self.resize(self.width(), self.height() + dh)
        else:
            self.resize(self.width(), max(self.minimumHeight(),
                                          self.height() - dh))

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "frostbyte.svg")
        self.tray.setIcon(QIcon(icon_path))
        self.tray.setToolTip("FrostByte")
        menu = QMenu()
        show = QAction("Open FrostByte", self)
        show.triggered.connect(self.showNormal)
        menu.addAction(show)
        for label, mode in (("Fans: Auto", "auto"), ("Fans: MAX", "max")):
            act = QAction(label, self)
            act.triggered.connect(lambda _c, m=mode: self._send(
                {"cmd": "set_mode", "mode": m}))
            menu.addAction(act)
        menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.showNormal()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ------------------------------------------------------------ behaviour

    def _send(self, payload):
        try:
            fb.request(payload, timeout=2.0)
        except fb.DaemonError as exc:
            self.statusBar().showMessage(f"daemon error: {exc}")

    def _crit_changed(self, _value=0):
        self._send({"cmd": "set_crit", "cpu": self.cpu_crit.value(),
                    "gpu": self.gpu_crit.value()})

    def _slider_changed(self, which, value):
        s = getattr(self, "_last_status", None)
        if not s:
            return
        cpu, gpu = s["fan_setting"]
        cpu = value if which == "cpu" else (cpu or 0)
        gpu = value if which == "gpu" else (gpu or 0)
        self._send({"cmd": "set_fans", "cpu": cpu, "gpu": gpu})

    def _curve_apply(self, which, points):
        self._send({"cmd": "set_curve", "which": which, "points": points})
        if self._last_status and self._last_status["mode"] != "curve":
            self._send({"cmd": "set_mode", "mode": "curve"})

    def on_lost(self):
        self.statusBar().showMessage(
            "daemon unreachable - is frostbyte-daemon.service running?")

    def on_status(self, s):
        self._last_status = s
        if self._syncing:
            return
        self._syncing = True
        try:
            if not s["driver_present"]:
                self.statusBar().showMessage(
                    "DRIVER MISSING - acer-wmi sense interface not found")
            else:
                self.statusBar().showMessage(
                    f"driver {s['driver_version'] or s['driver_name']}  |  "
                    f"daemon {s['daemon_version']}")

            ac = s["ac_online"]
            self.power_lbl.setText(
                "🔌 AC power" if ac else "🔋 Battery" if ac is False else "")

            mode = s["mode"]
            for m, b in self.mode_tiles.items():
                b.setChecked(m == mode)
            for name, b in self.profile_btns.items():
                b.setChecked(name == s["profile"])

            cpu, gpu = s["fan_setting"]
            manual = mode == "manual"
            self.cpu_gauge.set_state(s["cpu_rpm"], s["cpu_temp"], cpu)
            self.gpu_gauge.set_state(s["gpu_rpm"], s["gpu_temp"], gpu)
            self.cpu_row.set_state(cpu, manual)
            self.gpu_row.set_state(gpu, manual)
            self.graph.push(s["cpu_temp"], s["gpu_temp"])

            # Load curves once so polling never clobbers an in-progress edit;
            # the daemon echoes back applied curves through get_status anyway.
            if not self._curves_loaded:
                self.cpu_curve.set_points(s["config"]["cpu_curve"])
                self.gpu_curve.set_points(s["config"]["gpu_curve"])
                self._curves_loaded = True

            if s.get("battery_limiter") is None:
                self.limiter_chk.setEnabled(False)
            else:
                self.limiter_chk.setChecked(bool(s["battery_limiter"]))
            self.follow_chk.setChecked(
                bool(s["config"]["follow_power_source"]))
            self.cpu_crit.setValue(int(s["config"].get("cpu_crit", 95)))
            self.gpu_crit.setValue(int(s["config"].get("gpu_crit", 88)))

            if s["safety_override"]:
                self.statusBar().showMessage(
                    "⚠ SAFETY OVERRIDE - temperatures critical, fans at 100%")
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ GPU

    def _gpu_poll(self):
        st = fb_gpu.gpu_status()
        if st is None:
            self.gpu_title.setText("DEDICATED GPU — not detected")
            for v in self._gpu_vals.values():
                v.setText("—")
            self.gpu_procs.setText("nvidia-smi unavailable")
            self.gpu_launch_btn.setEnabled(False)
            self.gpu_park_btn.setEnabled(False)
            return
        self.gpu_launch_btn.setEnabled(True)
        self.gpu_park_btn.setEnabled(True)
        name = st.get("name", "NVIDIA GPU").replace("NVIDIA GeForce ", "")
        self.gpu_title.setText(f"DEDICATED GPU — {name.upper()}")

        def _fmt(raw, suffix=""):
            if not raw or raw in ("[N/A]", "N/A"):
                return "—"
            return f"{raw}{suffix}"

        self._gpu_vals["temperature.gpu"].setText(
            _fmt(st.get("temperature.gpu"), "°C"))
        self._gpu_vals["power.draw"].setText(_fmt(st.get("power.draw"), "W"))
        self._gpu_vals["clocks.sm"].setText(_fmt(st.get("clocks.sm"), "MHz"))
        self._gpu_vals["utilization.gpu"].setText(
            _fmt(st.get("utilization.gpu"), "%"))
        used, total = st.get("memory.used"), st.get("memory.total")
        if used and used not in ("[N/A]", "N/A"):
            vram = (f"{used} / {total} MB"
                    if total and total not in ("[N/A]", "N/A")
                    else f"{used} MB")
        else:
            vram = "—"
        self._gpu_vals["memory.used"].setText(vram)
        procs = fb_gpu.gpu_processes()
        if procs:
            self.gpu_procs.setText(
                "on dGPU: " + ", ".join(
                    f"{p['name']} (pid {p['pid']})" for p in procs))
        else:
            self.gpu_procs.setText("no processes on dGPU")

    def _gpu_launch(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Launch on RTX 3050")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Executable (path or command + optional args):"))
        row = QHBoxLayout()
        cmd_edit = QLineEdit()
        cmd_edit.setPlaceholderText("/path/to/game  or  app")
        browse = QPushButton("Browse…")
        row.addWidget(cmd_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        hint = QLabel("Runs via PRIME render offload on the NVIDIA dGPU.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _pick():
            path, _filt = QFileDialog.getOpenFileName(
                dlg, "Select application or game", "/usr/bin", "All files (*)")
            if path:
                cmd_edit.setText(path)

        browse.clicked.connect(_pick)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cmd = cmd_edit.text().strip()
        if not cmd:
            return
        try:
            argv = shlex.split(cmd)
        except ValueError:
            self.statusBar().showMessage("couldn't parse command")
            return
        try:
            fb_gpu.launch_on_gpu(argv)
            self.statusBar().showMessage(f"launched on RTX 3050: {argv[0]}")
        except OSError as exc:
            self.statusBar().showMessage(f"launch failed: {exc}")

    def _gpu_park(self):
        # Best-effort runtime park: drop persistence so the driver can unload
        # when idle. Needs privileges (pkexec will prompt). The full power-gate
        # is the NVreg_DynamicPowerManagement modprobe option + reboot.
        try:
            subprocess.Popen(
                ["pkexec", "nvidia-smi", "--persistence-mode=0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.statusBar().showMessage(
                "park request sent (may prompt for your password)")
        except OSError as exc:
            self.statusBar().showMessage(f"park failed: {exc}")


def main(theme=None):
    app = QApplication(sys.argv)
    app.setApplicationName("FrostByte")
    if theme is None:
        theme = QSettings("FrostByte", "FrostByte").value(
            "theme", DEFAULT_THEME)
    app.setStyleSheet(make_stylesheet(THEMES.get(theme, THEMES[DEFAULT_THEME])))
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "frostbyte.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Single instance: if FrostByte is already running, ask it to toggle
    # its window (this is what the NitroSense key ultimately triggers).
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_NAME)
    if probe.waitForConnected(300):
        probe.write(b"toggle")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        sys.exit(0)

    win = MainWindow(theme)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
