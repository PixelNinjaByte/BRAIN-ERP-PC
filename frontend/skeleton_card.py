"""
From Uiverse.io by mrhyddenn - https://uiverse.io/mrhyddenn/warm-wasp-21
MIT License, Copyright (c) mrhyddenn

PyQt6 adaptation of the shimmer skeleton loading card.
Features:
  - Exact recreation of warm-wasp-21 (circular avatar, title bar, description block)
  - Smooth 2000ms infinite loop linear shimmer wave (120deg gradient sweep)
  - Outline highlight on hover with elevated drop shadow effect
  - Auto-pauses when hidden to conserve CPU resources
"""

from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QVariantAnimation
from PyQt6.QtGui import (
    QPainter, QLinearGradient, QColor, QPainterPath, QPaintEvent,
)
from PyQt6.QtWidgets import QWidget, QGraphicsDropShadowEffect, QHBoxLayout


class SkeletonCard(QWidget):
    """
    Shimmer skeleton loading card adapted from Uiverse.io (warm-wasp-21).

    Renders a 190x90 card containing:
      1. Circular placeholder (70x70, border-radius 50%)
      2. Title bar placeholder (90x10, border-radius 5px)
      3. Description block placeholder (90x47, border-radius 5px)
    animated with a moving 120-degree linear shimmer gradient,
    plus outline highlight and shadow effect on hover.
    """

    CARD_WIDTH = 190
    CARD_HEIGHT = 90

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # (no pointing-hand cursor: this is a non-clickable placeholder)

        self._phase = 0.0  # 0.0 -> 1.0 progress over 2s cycle
        self._hover = False

        # Drop shadow effect
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(15)
        self._shadow.setOffset(0, 2)
        self._shadow.setColor(QColor(0, 0, 0, 35))
        self.setGraphicsEffect(self._shadow)

        # 2s infinite linear loop animation
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(2000)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)  # infinite
        self._anim.valueChanged.connect(self._on_anim_step)
        # Not started here: showEvent() starts it. Starting in __init__ meant a card that
        # was created but never shown kept ticking forever.

    def _on_anim_step(self, value: float) -> None:
        self._phase = value
        self.update()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        if hover:
            self._shadow.setBlurRadius(22)
            self._shadow.setOffset(0, 5)
            self._shadow.setColor(QColor(0, 0, 0, 50))
        else:
            self._shadow.setBlurRadius(15)
            self._shadow.setOffset(0, 2)
            self._shadow.setColor(QColor(0, 0, 0, 35))
        self.update()

    def enterEvent(self, event) -> None:
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_hover(False)
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.pause()
        # Navigating away while hovered sends no leaveEvent; clear it or the blue
        # outline is still there when the page is shown again.
        self._set_hover(False)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Card container background (pure white) with outline highlight
        card_rect = QRectF(0.5, 0.5, float(self.CARD_WIDTH - 1), float(self.CARD_HEIGHT - 1))
        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 6, 6)

        painter.fillPath(card_path, QColor("#ffffff"))

        # Highlight outline on hover
        border_color = QColor("#2563eb") if self._hover else QColor("#e5e7eb")
        painter.setPen(border_color)
        painter.drawPath(card_path)

        # Shimmer gradient angled at 120 degrees
        w = float(self.CARD_WIDTH)
        h = float(self.CARD_HEIGHT)

        cx = -w * 0.5 + self._phase * (w * 2.0)
        cy = h * 0.5

        angle_rad = math.radians(120.0)
        vec_x = math.sin(angle_rad)
        vec_y = -math.cos(angle_rad)
        span = 140.0

        x1 = cx - vec_x * span
        y1 = cy - vec_y * span
        x2 = cx + vec_x * span
        y2 = cy + vec_y * span

        shimmer = QLinearGradient(x1, y1, x2, y2)
        shimmer.setColorAt(0.00, QColor("#e5e5e5"))
        shimmer.setColorAt(0.30, QColor("#e5e5e5"))
        shimmer.setColorAt(0.38, QColor("#f0f0f0"))
        shimmer.setColorAt(0.40, QColor("#f0f0f0"))
        shimmer.setColorAt(0.48, QColor("#e5e5e5"))
        shimmer.setColorAt(1.00, QColor("#e5e5e5"))

        # 1. card_load: 70px x 70px circular avatar on left (x=10, y=10)
        circle_path = QPainterPath()
        circle_path.addEllipse(QRectF(10.0, 10.0, 70.0, 70.0))
        painter.fillPath(circle_path, shimmer)

        # 2. card_load_extreme_title: 90px x 10px rounded bar on right (x=90, y=12, r=5)
        title_path = QPainterPath()
        title_path.addRoundedRect(QRectF(90.0, 12.0, 90.0, 10.0), 5.0, 5.0)
        painter.fillPath(title_path, shimmer)

        # 3. card_load_extreme_descripion: 90px x 47px rounded rect on right (x=90, y=32, r=5)
        desc_path = QPainterPath()
        desc_path.addRoundedRect(QRectF(90.0, 32.0, 90.0, 47.0), 5.0, 5.0)
        painter.fillPath(desc_path, shimmer)


class SkeletonCardRow(QWidget):
    """Convenience widget showing a row of animated SkeletonCards for loading states."""

    def __init__(self, count: int = 3, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        # Vertical room for the drop shadows: graphics effects are clipped to the parent's
        # rectangle, and the row used to be exactly card-sized, so shadows were cut flat.
        layout.setContentsMargins(0, 6, 0, 18)
        layout.setSpacing(16)
        self.cards = [SkeletonCard(self) for _ in range(count)]
        for c in self.cards:
            layout.addWidget(c)
        layout.addStretch(1)