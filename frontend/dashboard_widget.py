"""
Brain ERP – Dashboard / Home screen.

Matches BRAIN.PRIGAG.COM screenshot:
  • Light grey content area
  • Centred Prigag logo (pinwheel graphic rendered via SVG-in-label)
  • "Enter a T-Code or name above to begin."  (dark text)
  • "Type \\ICONS to browse every T-Code as a tile, or \\TREE to show/hide the module menu."
    (smaller grey subtext)

The logo is drawn with QPainter as a coloured pinwheel that closely
matches the Prigag® brand mark (6-petal fan of triangles in the brand
colours: red, blue, navy, green, orange, grey-silver).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QBrush, QPen, QFont,
    QRadialGradient, QConicalGradient, QPixmap,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)

_CONTENT_BG = "#f0f2f5"

# ── Dashboard logo image ──────────────────────────────────────────────────
# To change the dashboard logo, drop a replacement PNG at this exact path
# and filename — no code change needed:
#       frontend/assets/images/dashboard_logo.png
#
# Sizing: the logo is shown on screen at _LOGO_DISPLAY_SIZE px (square).
# Save your source file at 2x that (currently 380x380px) so it stays sharp
# on high-DPI displays; it will be downscaled to fit. Use PNG with a
# transparent background so it sits cleanly on the #f0f2f5 dashboard
# background. If the file is missing, the original hand-drawn Prigag
# pinwheel logo is used instead.
_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "images"
_LOGO_PATH = _LOGO_DIR / "dashboard_logo.png"
_LOGO_DISPLAY_SIZE = 190  # on-screen px, matches the old _PrigagLogo(size=190)

# ── Prigag pinwheel logo widget ───────────────────────────────────────────────

class _PrigagLogo(QWidget):
    """
    Renders the Prigag® pinwheel from scratch using QPainter.
    6 fan-blade petals arranged around a grey metallic hub.
    Colours (clockwise from top): red, navy, black, green, orange, silver.
    """

    _PETALS = [
        ("#cc2200", -90),   # top — red
        ("#1a237e", -30),   # top-right — dark navy/blue
        ("#111111", 30),    # right — near-black
        ("#2e7d32", 90),    # bottom — green
        ("#e65100", 150),   # bottom-left — deep orange
        ("#9e9e9e", 210),   # left — silver-grey
    ]

    def __init__(self, size: int = 200, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self._size / 2
        cy = self._size / 2
        R = self._size * 0.46   # outer radius of petals
        hub_r = self._size * 0.14  # centre hub radius

        import math

        for color_hex, start_angle_deg in self._PETALS:
            path = QPainterPath()
            path.moveTo(cx, cy)

            # Each petal sweeps 55°, centred on start_angle_deg
            half = 27.5
            a1 = math.radians(start_angle_deg - half)
            a2 = math.radians(start_angle_deg + half)
            # tip point
            tip_angle = math.radians(start_angle_deg)
            tip_x = cx + R * math.cos(tip_angle)
            tip_y = cy + R * math.sin(tip_angle)

            # arc points
            p1x = cx + (R * 0.72) * math.cos(a1)
            p1y = cy + (R * 0.72) * math.sin(a1)
            p2x = cx + (R * 0.72) * math.cos(a2)
            p2y = cy + (R * 0.72) * math.sin(a2)

            path.moveTo(cx, cy)
            path.lineTo(p1x, p1y)
            path.cubicTo(
                p1x + (tip_x - p1x) * 0.6, p1y + (tip_y - p1y) * 0.3,
                p2x + (tip_x - p2x) * 0.6, p2y + (tip_y - p2y) * 0.3,
                p2x, p2y,
            )
            path.lineTo(cx, cy)

            color = QColor(color_hex)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.drawPath(path)

        # Metallic hub — radial gradient silver disc
        hub_grad = QRadialGradient(cx - hub_r * 0.25, cy - hub_r * 0.25, hub_r)
        hub_grad.setColorAt(0.0, QColor("#e0e0e0"))
        hub_grad.setColorAt(0.5, QColor("#9e9e9e"))
        hub_grad.setColorAt(1.0, QColor("#616161"))
        painter.setBrush(QBrush(hub_grad))
        painter.drawEllipse(QPointF(cx, cy), hub_r, hub_r)

        painter.end()


def _make_logo_widget(size: int = _LOGO_DISPLAY_SIZE) -> QWidget:
    """
    Returns the widget used for the dashboard logo.

    If frontend/assets/images/dashboard_logo.png exists and loads, it is
    scaled to `size`x`size` and shown. Otherwise, falls back to the
    hand-painted _PrigagLogo pinwheel so the dashboard never shows a
    broken image.
    """
    if _LOGO_PATH.is_file():
        pixmap = QPixmap(str(_LOGO_PATH))
        if not pixmap.isNull():
            label = QLabel()
            label.setFixedSize(size, size)
            label.setPixmap(
                pixmap.scaled(
                    size, size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return label
    return _PrigagLogo(size=size)


# ── Dashboard widget ─────────────────────────────────────────────────────────

class DashboardWidget(QWidget):
    """
    Home screen — centred Prigag logo + T-code prompt, matching the
    BRAIN.PRIGAG.COM screenshot exactly.
    """

    def __init__(self, navigate: Callable[[str], None], session: dict):
        super().__init__()
        self._navigate = navigate
        self._session = session
        self.setStyleSheet(f"background: {_CONTENT_BG};")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Push everything to vertical centre
        root.addStretch(2)

        # ── Centre block ─────────────────────────────────────────────────
        centre_col = QVBoxLayout()
        centre_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        centre_col.setSpacing(0)

        # Thin top line above logo (like in screenshot)
        top_line = QFrame()
        top_line.setFixedHeight(1)
        top_line.setFixedWidth(240)
        top_line.setStyleSheet("background: #d1d5db;")
        top_line_row = QHBoxLayout()
        top_line_row.addStretch(1)
        top_line_row.addWidget(top_line)
        top_line_row.addStretch(1)
        centre_col.addLayout(top_line_row)

        # Logo (swap the file at frontend/assets/images/dashboard_logo.png
        # to change this without touching code — see _make_logo_widget)
        logo = _make_logo_widget()
        logo_row = QHBoxLayout()
        logo_row.addStretch(1)
        logo_row.addWidget(logo)
        logo_row.addStretch(1)
        centre_col.addLayout(logo_row)

        # PRIGAG® text under logo
        brand_lbl = QLabel("PRIGAG")
        brand_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_lbl.setStyleSheet(
            "font-size: 15px; font-weight: 800; color: #1a1f2e;"
            " letter-spacing: 3px; margin-top: 6px;"
        )
        centre_col.addWidget(brand_lbl)

        reg_lbl = QLabel("®")
        reg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reg_lbl.setStyleSheet("font-size: 9px; color: #6b7280; margin-top: -2px;")
        centre_col.addWidget(reg_lbl)

        centre_col.addSpacing(18)

        # Main prompt
        prompt = QLabel("Enter a T-Code or name above to begin.")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setStyleSheet(
            "font-size: 14px; color: #111827; font-weight: 500;"
        )
        centre_col.addWidget(prompt)

        centre_col.addSpacing(6)

        # Sub prompt  — \\ICONS and \\TREE in blue like screenshot
        sub_html = (
            "Type <span style='color:#2563eb;'>\\ICONS</span> to browse every "
            "T-Code as a tile, or "
            "<span style='color:#2563eb;'>\\TREE</span> to show/hide the module menu."
        )
        sub = QLabel(sub_html)
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("font-size: 11px; color: #6b7280;")
        centre_col.addWidget(sub)

        root.addLayout(centre_col)

        root.addStretch(3)