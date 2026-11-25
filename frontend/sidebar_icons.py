"""
SVG assets for the Brain ERP sidebar (logo, module icons, chevrons).

Everything is an inline SVG string rendered through QtSvg, so it stays
crisp at any DPI and no image files need to ship with the app.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'

# Material-style gear outline path (used for ADMIN / PRODUCTION / Settings)
_GEAR_PATH = (
    "M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 "
    ".12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54"
    "a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94"
    "l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63"
    "-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39"
    "-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36"
    "-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47"
    "-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 "
    "3.6-1.62 3.6-3.6 3.6z"
)


def _gear(color: str) -> str:
    return f'{_HEAD}<path fill="{color}" d="{_GEAR_PATH}"/></svg>'


ICONS: dict[str, str] = {
    "ADMIN": _gear("#6b7280"),

    "HR": (
        f'{_HEAD}<g fill="#22b8e8">'
        '<circle cx="9" cy="8" r="3.6"/><path d="M2 20c0-3.7 3-6.2 7-6.2s7 2.5 7 6.2z"/>'
        '<circle cx="17.6" cy="9" r="2.8"/>'
        '<path d="M17.6 14c3 0 5.4 1.7 5.4 5h-5.1c0-2-.7-3.7-2-4.7.5-.2 1.1-.3 1.7-.3z"/>'
        '</g></svg>'
    ),

    "MATERIALS": (
        f'{_HEAD}<polygon points="12,2 21.5,7 12,12 2.5,7" fill="#fcd34d"/>'
        '<polygon points="2.5,7 12,12 12,22 2.5,17" fill="#f59e0b"/>'
        '<polygon points="21.5,7 12,12 12,22 21.5,17" fill="#d97706"/></svg>'
    ),

    "SALES": (
        f'{_HEAD}<path d="M1.5 3.5h3.2l.6 2.5" fill="none" stroke="#1f9d55" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        '<polygon points="5.3,6 22,6 19.3,14.5 8,14.5" fill="#1f9d55"/>'
        '<path d="M8 14.5l-.6 2.2h11" fill="none" stroke="#1f9d55" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="9" cy="20" r="1.9" fill="#1f9d55"/>'
        '<circle cx="17" cy="20" r="1.9" fill="#1f9d55"/></svg>'
    ),

    "BOM": (
        f'{_HEAD}<g fill="#6d4fe0"><rect x="9" y="2.5" width="6" height="5" rx=".8"/>'
        '<rect x="1" y="16.5" width="6" height="5" rx=".8"/>'
        '<rect x="9" y="16.5" width="6" height="5" rx=".8"/>'
        '<rect x="17" y="16.5" width="6" height="5" rx=".8"/></g>'
        '<path d="M12 7.5v9M4 16.5v-4.5h16v4.5" fill="none" stroke="#6d4fe0" '
        'stroke-width="1.8"/></svg>'
    ),

    "PRODUCTION": _gear("#f97316"),

    "ACCOUNTING": (
        f'{_HEAD}<polygon points="12,2 22.5,8 1.5,8" fill="#dc2626"/>'
        '<g fill="#dc2626"><rect x="3.5" y="10" width="3" height="8"/>'
        '<rect x="8.5" y="10" width="3" height="8"/>'
        '<rect x="13.5" y="10" width="3" height="8"/>'
        '<rect x="18.5" y="10" width="3" height="8"/>'
        '<rect x="1.5" y="19.5" width="21" height="2.5" rx=".6"/></g></svg>'
    ),

    "QUALITY": (
        f'{_HEAD}<rect x="4" y="4" width="16" height="18" rx="2.2" fill="#14b8a6"/>'
        '<rect x="8.5" y="1.8" width="7" height="4.6" rx="1.3" fill="#0f766e"/>'
        '<path d="M8 14l3 3 5-6" fill="none" stroke="#fff" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),

    "PLANT MAINT.": (
        f'{_HEAD}<path d="M5 20L16.5 8.5" stroke="#b5835a" stroke-width="2.6" '
        'stroke-linecap="round"/>'
        '<path d="M19 20L7.5 8.5" stroke="#8b9099" stroke-width="2.6" '
        'stroke-linecap="round"/>'
        '<circle cx="17.5" cy="6.5" r="3.4" fill="#8b9099"/>'
        '<rect x="3.5" y="3.2" width="7" height="4.6" rx="1" '
        'transform="rotate(-45 7 5.5)" fill="#b5835a"/></svg>'
    ),

    "PROJECTS & DEVELOPMENTS": (
        f'{_HEAD}<rect x="2.5" y="2.5" width="19" height="19" rx="3" fill="#7c3aed"/>'
        '<g fill="#fff"><rect x="6" y="6" width="5" height="12" rx=".8"/>'
        '<rect x="13" y="6" width="5" height="7" rx=".8"/></g></svg>'
    ),

    "COMMUNICATION": (
        f'{_HEAD}<path d="M3 6.5C3 5 4 4 5.5 4h13C20 4 21 5 21 6.5v8c0 1.5-1 2.5-2.5 '
        '2.5H11l-4.5 3.8V17H5.5C4 17 3 16 3 14.5z" fill="#ec4899"/>'
        '<g fill="#fff"><circle cx="8" cy="10.5" r="1.2"/>'
        '<circle cx="12" cy="10.5" r="1.2"/><circle cx="16" cy="10.5" r="1.2"/></g></svg>'
    ),

    # bottom links
    "Companies": (
        f'{_HEAD}<g fill="#4a90d9"><rect x="2" y="12" width="5" height="10" rx=".6"/>'
        '<rect x="9" y="7" width="5.5" height="15" rx=".6"/>'
        '<rect x="16.5" y="3" width="5.5" height="19" rx=".6"/></g>'
        '<g fill="#1a1c20"><rect x="3.4" y="14" width="2.2" height="2"/>'
        '<rect x="10.5" y="9.5" width="2.5" height="2"/>'
        '<rect x="10.5" y="13.5" width="2.5" height="2"/>'
        '<rect x="18" y="6" width="2.5" height="2"/>'
        '<rect x="18" y="10" width="2.5" height="2"/></g>'
        '<rect x="2" y="21" width="20" height="1.6" fill="#f59e0b"/></svg>'
    ),
    "Settings": _gear("#9ca3af"),
}

_CHEV_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="#6b7280" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
)
CHEVRON_RIGHT = _CHEV_HEAD + '<path d="M9 5l7 7-7 7"/></svg>'
CHEVRON_DOWN = _CHEV_HEAD + '<path d="M5 9l7 7 7-7"/></svg>'


def logo_svg() -> str:
    """Prigag mark: multicolour pinwheel on a white tile with a small wordmark."""
    cx, cy, r = 24.0, 20.0, 15.0
    colors = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa"]
    wedges = []
    for i, col in enumerate(colors):
        a0 = math.radians(i * 60 - 90 + 2)
        a1 = math.radians((i + 1) * 60 - 90 - 2)
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        wedges.append(
            f'<path d="M{cx} {cy} L{x0:.2f} {y0:.2f} A{r} {r} 0 0 1 {x1:.2f} {y1:.2f} Z" '
            f'fill="{col}"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
        '<rect width="48" height="48" rx="6" fill="#ffffff"/>'
        + "".join(wedges)
        + f'<circle cx="{cx}" cy="{cy}" r="4" fill="#ffffff"/>'
        '<text x="24" y="43" text-anchor="middle" font-family="Arial, sans-serif" '
        'font-size="6.5" font-weight="700" fill="#222222" letter-spacing=".4">PRIGAG</text>'
        '</svg>'
    )


_CACHE: dict[tuple[str, int], QPixmap] = {}


def svg_pixmap(svg: str, size: int, dpr: int = 2) -> QPixmap:
    """Render an SVG string to a crisp transparent pixmap of `size` logical px."""
    key = (svg, size)
    if key in _CACHE:
        return _CACHE[key]
    px = QPixmap(size * dpr, size * dpr)
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    px.setDevicePixelRatio(dpr)
    _CACHE[key] = px
    return px