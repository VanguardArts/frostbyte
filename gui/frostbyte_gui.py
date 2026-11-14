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

import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QSlider, QSpinBox,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import frostbyte_common as fb

ACCENT = "#5ecbff"
DANGER = "#ff5964"
BG = "#0b0e14"
CARD = "#151a23"
CARD_EDGE = "#232a36"
TEXT = "#d7dde6"
MUTED = "#7d8794"

STYLESHEET = f"""
QMainWindow, QWidget {{ background: {BG}; color: {TEXT};
    font-family: 'Noto Sans', sans-serif; }}
QFrame#card {{ background: {CARD}; border: 1px solid {CARD_EDGE};
    border-radius: 12px; }}
QLabel#big {{ font-size: 30px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#title {{ font-size: 15px; font-weight: 600; letter-spacing: 1px; }}
QPushButton {{ background: #1c2230; border: 1px solid {CARD_EDGE};
    border-radius: 8px; padding: 8px 14px; color: {TEXT}; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked {{ background: {ACCENT}; color: #04121c;
    font-weight: 700; }}
QPushButton#danger:checked {{ background: {DANGER}; color: white; }}
QSlider::groove:horizontal {{ height: 6px; background: #232a36;
    border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -7px 0;
    border-radius: 9px; background: {ACCENT}; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider:disabled::handle:horizontal {{ background: #3a4250; }}
QTableWidget {{ background: {CARD}; gridline-color: {CARD_EDGE};
    border: 1px solid {CARD_EDGE}; border-radius: 8px; }}
QHeaderView::section {{ background: #1c2230; color: {MUTED};
    border: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QSpinBox {{ background: #1c2230; border: 1px solid {CARD_EDGE};
    border-radius: 6px; padding: 4px; }}
"""


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


class Sparkline(QWidget):
    """Minimal 60s history graph for one or two series (0-100 scale)."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.series = [deque(maxlen=60) for _ in colors]
        self.setMinimumHeight(90)

    def push(self, *values):
        for series, val in zip(self.series, values):
            series.append(val)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(CARD))
        p.setPen(QPen(QColor(CARD_EDGE), 1))
        for frac in (0.25, 0.5, 0.75):
            y = int(self.height() * frac)
            p.drawLine(0, y, self.width(), y)
        for series, color in zip(self.series, self.colors):
            if len(series) < 2:
                continue
            p.setPen(QPen(QColor(color), 2))
            w, h = self.width(), self.height()
            step = w / 59.0
            prev = None
            for i, val in enumerate(series):
                if val is None:
                    prev = None
                    continue
                x = int(i * step)
                y = int(h - (max(0, min(100, val)) / 100.0) * (h - 6) - 3)
                if prev is not None:
                    p.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)
        p.end()


class FanCard(QFrame):
    """One fan's display + manual slider."""
    sliderSet = pyqtSignal(int)

    def __init__(self, title, accent):
        super().__init__()
        self.setObjectName("card")
        self.accent = accent
        lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setObjectName("title")
        head.setStyleSheet(f"color: {accent};")
        lay.addWidget(head)

        row = QHBoxLayout()
        self.temp_lbl = QLabel("--.-°C")
        self.temp_lbl.setObjectName("big")
        self.rpm_lbl = QLabel("---- RPM")
        self.rpm_lbl.setObjectName("muted")
        self.set_lbl = QLabel("AUTO")
        self.set_lbl.setObjectName("muted")
        row.addWidget(self.temp_lbl)
        row.addStretch(1)
        rcol = QVBoxLayout()
        rcol.addWidget(self.rpm_lbl, alignment=Qt.AlignmentFlag.AlignRight)
        rcol.addWidget(self.set_lbl, alignment=Qt.AlignmentFlag.AlignRight)
        row.addLayout(rcol)
        lay.addLayout(row)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)
        self.slider.sliderReleased.connect(
            lambda: self.sliderSet.emit(self.slider.value()))
        lay.addWidget(self.slider)
        cap = QLabel("0% = firmware auto")
        cap.setObjectName("muted")
        lay.addWidget(cap)

    def update(self, temp, rpm, setting, manual_enabled):
        self.temp_lbl.setText("--.-°C" if temp is None else f"{temp:.1f}°C")
        self.rpm_lbl.setText("---- RPM" if rpm is None else f"{rpm} RPM")
        self.set_lbl.setText("AUTO" if setting in (None, 0)
                             else f"set {setting}%")
        self.slider.setEnabled(manual_enabled)
        if manual_enabled and not self.slider.isSliderDown():
            self.slider.setValue(setting or 0)


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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FrostByte")
        self.resize(760, 640)
        self.bridge = DaemonBridge()
        self.bridge.status.connect(self.on_status)
        self.bridge.lost.connect(self.on_lost)
        self._syncing = False
        self._curves_loaded = False
        self._build_ui()
        self._build_tray()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        # Header
        head = QHBoxLayout()
        logo = QLabel("❄ FrostByte")
        logo.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {ACCENT};")
        head.addWidget(logo)
        head.addStretch(1)
        self.power_lbl = QLabel("")
        self.power_lbl.setObjectName("muted")
        head.addWidget(self.power_lbl)
        lay.addLayout(head)

        # Profiles
        prof_row = QHBoxLayout()
        prof_row.addWidget(QLabel("Thermal profile:"))
        self.profile_btns = {}
        for name in ("quiet", "balanced", "performance"):
            b = QPushButton(name.capitalize())
            b.setCheckable(True)
            b.clicked.connect(lambda _c, n=name: self._send(
                {"cmd": "set_profile", "profile": n}))
            self.profile_btns[name] = b
            prof_row.addWidget(b)
        prof_row.addStretch(1)
        lay.addLayout(prof_row)

        # Mode buttons
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Fan mode:"))
        self.mode_btns = {}
        for mode, label in (("auto", "Auto"), ("curve", "Curve"),
                            ("manual", "Manual"), ("max", "MAX")):
            b = QPushButton(label)
            b.setCheckable(True)
            if mode == "max":
                b.setObjectName("danger")
            b.clicked.connect(lambda _c, m=mode: self._send(
                {"cmd": "set_mode", "mode": m}))
            self.mode_btns[mode] = b
            mode_row.addWidget(b)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        # Fan cards
        cards = QHBoxLayout()
        self.cpu_card = FanCard("CPU FAN", ACCENT)
        self.gpu_card = FanCard("GPU FAN", "#9dff70")
        self.cpu_card.sliderSet.connect(
            lambda v: self._slider_changed("cpu", v))
        self.gpu_card.sliderSet.connect(
            lambda v: self._slider_changed("gpu", v))
        cards.addWidget(self.cpu_card)
        cards.addWidget(self.gpu_card)
        lay.addLayout(cards)

        # Graph
        graph_card = QFrame()
        graph_card.setObjectName("card")
        gl = QVBoxLayout(graph_card)
        gl.addWidget(QLabel("Temperature history (60s, 0–100°C)"))
        self.graph = Sparkline([ACCENT, "#9dff70"])
        gl.addWidget(self.graph)
        lay.addWidget(graph_card)

        # Curves
        curves = QHBoxLayout()
        self.cpu_curve = CurveEditor("cpu", "CPU CURVE")
        self.gpu_curve = CurveEditor("gpu", "GPU CURVE")
        self.cpu_curve.applyRequested.connect(self._curve_apply)
        self.gpu_curve.applyRequested.connect(self._curve_apply)
        curves.addWidget(self.cpu_curve)
        curves.addWidget(self.gpu_curve)
        lay.addLayout(curves)

        # Toggles
        tog = QHBoxLayout()
        self.limiter_chk = QCheckBox("Battery limiter (charge to 80%)")
        self.limiter_chk.stateChanged.connect(
            lambda st: self._send({"cmd": "set_limiter",
                                   "enabled": bool(st)}))
        self.follow_chk = QCheckBox("Profile follows power source")
        self.follow_chk.setChecked(True)
        self.follow_chk.stateChanged.connect(
            lambda st: self._send({"cmd": "set_follow_power",
                                   "enabled": bool(st)}))
        tog.addWidget(self.limiter_chk)
        tog.addWidget(self.follow_chk)
        tog.addStretch(1)
        lay.addLayout(tog)

        self.statusBar().showMessage("connecting to daemon…")

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "frostbyte.svg")
        self.tray.setIcon(QIcon(icon_path))
        self.tray.setToolTip("FrostByte")
        menu = self.tray.contextMenu() or None
        from PyQt6.QtWidgets import QMenu
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
            for m, b in self.mode_btns.items():
                b.setChecked(m == mode)
            for name, b in self.profile_btns.items():
                b.setChecked(name == s["profile"])

            cpu, gpu = s["fan_setting"]
            manual = mode == "manual"
            self.cpu_card.update(s["cpu_temp"], s["cpu_rpm"], cpu, manual)
            self.gpu_card.update(s["gpu_temp"], s["gpu_rpm"], gpu, manual)
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

            if s["safety_override"]:
                self.statusBar().showMessage(
                    "⚠ SAFETY OVERRIDE - temperatures critical, fans at 100%")
        finally:
            self._syncing = False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FrostByte")
    app.setStyleSheet(STYLESHEET)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "frostbyte.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
