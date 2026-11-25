"""
Brain ERP – Main window shell (post-login).

Visual layout (matches BRAIN.PRIGAG.COM screenshot):
┌──────────────────────────────────────────────────────────────┐
│  TOP BAR: logo · "BRAIN.PRIGAG.COM" · [T-code input] [>]    │
│           ───────────────── right: company ▾  🔔  K kasina ▾ │
├───────────────────┬──────────────────────────────────────────┤
│ SIDE PANEL (dark) │  CONTENT AREA (light grey)               │
│  ● Brand block    │  Home: centered Prigag logo + prompt     │
│  ─────────────── │  Other screens: stacked widgets           │
│  ADMIN          > │                                           │
│  HR             > │                                           │
│  MATERIALS      > │                                           │
│  ...            > │                                           │
│  ─────────────── │                                           │
│  Companies        │                                           │
│  Settings         │                                           │
├───────────────────┴──────────────────────────────────────────┤
│  FOOTER: "PRIGAG BRAIN v1.21.0 — Prigag Online Pvt. Ltd."   │
│           © 2026 Prigag Online Pvt. Ltd. All Rights Reserved │
│           support@prigag.com                                  │
└──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit,
    QListWidget, QListWidgetItem, QStackedWidget,
    QSizePolicy, QScrollArea, QSpacerItem,
)

from .api_client import ApiClient, ApiError
from .dashboard_widget import DashboardWidget
from .mm01_widget import MM01Widget
from .po_widget import POWidget
from .migo_widget import MIGOWidget
from .pr_widget import PRWidget
from .supplier_invoice_widget import SupplierInvoiceWidget
from .supplier_widget import SupplierWidget
from .rfq_widget import RFQWidget
from .purchase_return_widget import PurchaseReturnWidget
from .reports_widget import ReportsWidget
from .material_import_widget import MaterialImportWidget
from .skeleton_card import SkeletonCardRow

# ── Colours ───────────────────────────────────────────────────────────────────
_SIDE_BG       = "#1a1f2e"      # very dark navy — sidebar background
_SIDE_HEADER   = "#141824"      # slightly darker for brand block header
_SIDE_HOVER    = "#252d40"      # hover row
_SIDE_ACTIVE   = "#2563eb"      # selected / active row
_SIDE_TEXT     = "#c9d1e0"      # normal item text
_SIDE_SECTION  = "#5a6480"      # section header text
_SIDE_WIDTH    = 230

_TOP_BG        = "#ffffff"
_TOP_BORDER    = "#e5e7eb"
_TOP_HEIGHT    = 50

_FOOT_BG       = "#1a1f2e"
_FOOT_HEIGHT   = 28

_CONTENT_BG    = "#f0f2f5"

# ── T-code routing ────────────────────────────────────────────────────────────
_TCODE_INDEX: dict[str, int] = {
    "HOME": 0,
    "MM01": 1, "MM02": 1, "MM03": 1,
    "ME21N": 2, "ME23N": 2, "ME2L": 2, "ME2M": 2, "ME28": 2, "ME29N": 2, "PO": 2,
    "MIGO": 3, "GRN": 3, "MB51": 3,
    "ME51N": 4, "ME53N": 4, "PR": 4,
    "MR01": 5, "MIRO": 5, "LT01": 5, "VK11": 5,
    "XK01": 6, "XK02": 6, "SUPP": 6,
    "ME41": 7, "ME47": 7, "RFQ": 7,
    "PRTN": 8,
    "ZMRP": 9, "ME2PN": 9, "ZMMR": 9, "ZREP": 9,
    "ZMMI": 10,
}

# Sidebar nav — (label, tcode_or_None, is_section_header, has_arrow)
_NAV_ITEMS: list[tuple[str, str | None, bool, bool]] = [
    # top modules with right-arrow (expandable in future)
    ("ADMIN",                   "SU01",   False, True),
    ("HR",                      "PA30",   False, True),
    ("MATERIALS",               "MM01",   False, True),
    ("SALES",                   "VA05",   False, True),
    ("BOM",                     "COOIS",  False, True),
    ("PRODUCTION",              "COOIS",  False, True),
    ("ACCOUNTING",              "FB50",   False, True),
    ("QUALITY",                 "QA32",   False, True),
    ("PLANT MAINT.",            "SU01",   False, True),
    ("PROJECTS & DEVELOPMENTS", "SU01",   False, True),
    ("COMMUNICATION",           "SU01",   False, True),
    # divider
    ("",                        None,     True,  False),
    # bottom fixed links
    ("Companies",               "COMP",   False, False),
    ("Settings",                "SU01",   False, False),
]


# ── Coming-soon placeholder ───────────────────────────────────────────────────

class _ComingSoon(QWidget):
    def __init__(self, label: str = ""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        self.setStyleSheet(f"background: {_CONTENT_BG};")

        icon_lbl = QLabel("🚧")
        icon_lbl.setFont(QFont("Segoe UI Emoji", 42))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(f"{label}\nComing Soon")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #111827;")

        sub = QLabel(
            "This module is on the roadmap.\n"
            "The backend logic is already ported — UI components are loading."
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #4b5563; font-size: 12px;")
        sub.setWordWrap(True)

        layout.addWidget(icon_lbl)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(16)

        skeleton_box = QFrame()
        skeleton_box.setStyleSheet("background: transparent;")
        s_layout = QHBoxLayout(skeleton_box)
        s_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_layout.addWidget(SkeletonCardRow(count=2, parent=self))
        layout.addWidget(skeleton_box)


# ── Sidebar nav row widget ────────────────────────────────────────────────────

class _NavRow(QWidget):
    """Single clickable row in the sidebar."""

    clicked = pyqtSignal(str)   # emits tcode

    def __init__(self, label: str, tcode: str, has_arrow: bool,
                 icon: str = "", parent=None):
        super().__init__(parent)
        self._tcode = tcode
        self._active = False
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(18)
            icon_lbl.setStyleSheet("color: #7a8299; font-size: 13px;")
            layout.addWidget(icon_lbl)

        self._lbl = QLabel(label)
        self._lbl.setStyleSheet(
            f"color: {_SIDE_TEXT}; font-size: 11px; font-weight: 600;"
            " letter-spacing: 0.5px; background: transparent;"
        )
        layout.addWidget(self._lbl, 1)

        if has_arrow:
            arrow = QLabel("›")
            arrow.setStyleSheet(f"color: {_SIDE_SECTION}; font-size: 14px; background: transparent;")
            layout.addWidget(arrow)

        self._set_bg(False)

    def _set_bg(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_SIDE_ACTIVE}; border-radius: 0px;")
            self._lbl.setStyleSheet(
                "color: white; font-size: 11px; font-weight: 700;"
                " letter-spacing: 0.5px; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_SIDE_HOVER}; border-radius: 0px;")
        else:
            self.setStyleSheet("background: transparent; border-radius: 0px;")

    def set_active(self, active: bool) -> None:
        self._active = active
        self._set_bg(False)

    def enterEvent(self, e):
        if not self._active:
            self._set_bg(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._active:
            self._set_bg(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._tcode)
        super().mousePressEvent(e)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    logged_out = pyqtSignal()

    def __init__(self, api: ApiClient, session: dict):
        super().__init__()
        self.api = api
        self.session = session
        self._nav_rows: list[_NavRow] = []

        self.setWindowTitle("Brain ERP — Prigag Online Pvt. Ltd.")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)
        self._build_ui()

    # ─── Full UI ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root_w = QWidget()
        root_w.setStyleSheet(f"background: {_CONTENT_BG};")
        root_vbox = QVBoxLayout(root_w)
        root_vbox.setContentsMargins(0, 0, 0, 0)
        root_vbox.setSpacing(0)

        # Top bar
        root_vbox.addWidget(self._make_topbar())

        # Body = sidebar + content
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_hbox = QHBoxLayout(body)
        body_hbox.setContentsMargins(0, 0, 0, 0)
        body_hbox.setSpacing(0)
        body_hbox.addWidget(self._make_sidebar())
        body_hbox.addWidget(self._make_content_area(), 1)

        root_vbox.addWidget(body, 1)
        root_vbox.addWidget(self._make_footer())

        self.setCentralWidget(root_w)

    # ─── Top bar ─────────────────────────────────────────────────────────────

    def _make_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(_TOP_HEIGHT)
        bar.setStyleSheet(
            f"background: {_TOP_BG};"
            f"border-bottom: 1px solid {_TOP_BORDER};"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        # ── Left: logo circle + site name ────────────────────────────────
        logo_circle = QLabel("🧠")
        logo_circle.setFixedSize(28, 28)
        logo_circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_circle.setStyleSheet(
            "background: #1a1f2e; border-radius: 14px; font-size: 14px;"
        )

        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_col.setContentsMargins(0, 0, 0, 0)
        site_lbl = QLabel("BRAIN.PRIGAG.COM")
        site_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 800; color: #111827; letter-spacing: 0.5px;"
        )
        powered_lbl = QLabel("Powered by PRIGAG BRAIN")
        powered_lbl.setStyleSheet("font-size: 8px; color: #9ca3af;")
        brand_col.addWidget(site_lbl)
        brand_col.addWidget(powered_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #d1d5db;")
        sep.setFixedHeight(28)

        # ── Centre: T-code input ─────────────────────────────────────────
        self.tcode_input = QLineEdit()
        self.tcode_input.setPlaceholderText("Hi, how can I help you?")
        self.tcode_input.setFixedWidth(260)
        self.tcode_input.setFixedHeight(32)
        self.tcode_input.setStyleSheet(
            "QLineEdit { background: #f9fafb; color: #111827;"
            " border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 0px 10px; font-size: 12px; }"
            "QLineEdit:focus { border-color: #2563eb; background: white; }"
        )
        self.tcode_input.returnPressed.connect(self._on_tcode_enter)

        go_btn = QPushButton("›")
        go_btn.setFixedSize(32, 32)
        go_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 6px; font-size: 18px; font-weight: 700; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        go_btn.clicked.connect(self._on_tcode_enter)

        # ── Right: company selector + bell + user ────────────────────────
        company_btn = QPushButton("🏢  BRAIN.PRIGAG.COM  ▾")
        company_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #374151;"
            " border: 1px solid #d1d5db; border-radius: 5px;"
            " padding: 4px 10px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { background: #f3f4f6; }"
        )

        bell_btn = QPushButton("🔔")
        bell_btn.setFixedSize(32, 32)
        bell_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 14px; }"
            "QPushButton:hover { background: #f3f4f6; border-radius: 16px; }"
        )

        who = self.session.get("full_name") or self.session.get("username") or "User"
        initials = who[0].upper() if who else "U"

        avatar = QLabel(initials)
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background: #2563eb; color: white; border-radius: 13px;"
            " font-size: 11px; font-weight: 700;"
        )

        user_btn = QPushButton(f"  {who.split()[0]}  ▾")
        user_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #374151; border: none;"
            " font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { color: #111827; }"
        )
        user_btn.clicked.connect(self._do_logout)

        # Assemble
        layout.addWidget(logo_circle)
        layout.addLayout(brand_col)
        layout.addWidget(sep)
        layout.addStretch(1)
        layout.addWidget(self.tcode_input)
        layout.addWidget(go_btn)
        layout.addStretch(1)
        layout.addWidget(company_btn)
        layout.addWidget(bell_btn)
        layout.addWidget(avatar)
        layout.addWidget(user_btn)

        return bar

    # ─── Sidebar ─────────────────────────────────────────────────────────────

    def _make_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setFixedWidth(_SIDE_WIDTH)
        sidebar.setStyleSheet(f"background: {_SIDE_BG};")

        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Brand header block ────────────────────────────────────────────
        brand_block = QFrame()
        brand_block.setFixedHeight(68)
        brand_block.setStyleSheet(f"background: {_SIDE_HEADER};")
        bb_layout = QHBoxLayout(brand_block)
        bb_layout.setContentsMargins(12, 10, 12, 10)
        bb_layout.setSpacing(10)

        # Prigag logo circle (colourful pinwheel via emoji fallback)
        logo_lbl = QLabel("🧠")
        logo_lbl.setFixedSize(42, 42)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet(
            "background: white; border-radius: 21px; font-size: 20px;"
        )

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_col.setContentsMargins(0, 0, 0, 0)

        name_top = QLabel("BRAIN.PRIGAG.C...")
        name_top.setStyleSheet(
            "color: white; font-size: 11px; font-weight: 800; letter-spacing: 0.3px;"
            " background: transparent;"
        )
        name_bot = QLabel("Powered by PRIGAG BRAIN")
        name_bot.setStyleSheet(
            "color: #f59e0b; font-size: 8px; background: transparent;"
        )
        name_col.addWidget(name_top)
        name_col.addWidget(name_bot)

        bb_layout.addWidget(logo_lbl)
        bb_layout.addLayout(name_col)
        outer.addWidget(brand_block)

        # ── Orange accent line ────────────────────────────────────────────
        accent = QFrame()
        accent.setFixedHeight(2)
        accent.setStyleSheet("background: #f59e0b;")
        outer.addWidget(accent)

        # ── Scrollable nav list ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Apply scrollbar skin via the viewport/parent widget to avoid Qt CSS parse warnings
        # on bare QScrollArea setStyleSheet calls
        scroll.viewport().setStyleSheet(f"background: {_SIDE_BG};")

        nav_container = QWidget()
        nav_container.setStyleSheet(f"background: {_SIDE_BG};")
        nav_vbox = QVBoxLayout(nav_container)
        nav_vbox.setContentsMargins(0, 6, 0, 0)
        nav_vbox.setSpacing(0)

        self._nav_rows = []
        for label, tcode, is_divider, has_arrow in _NAV_ITEMS:
            if is_divider:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.HLine)
                div.setFixedHeight(1)
                div.setStyleSheet("background: #252d40; margin: 6px 0;")
                nav_vbox.addWidget(div)
            else:
                # Icon mapping
                icons = {
                    "ADMIN": "⚙", "HR": "👥", "MATERIALS": "📦",
                    "SALES": "🏷", "BOM": "🔩", "PRODUCTION": "🏭",
                    "ACCOUNTING": "💰", "QUALITY": "🔬",
                    "PLANT MAINT.": "🔧", "PROJECTS & DEVELOPMENTS": "📐",
                    "COMMUNICATION": "💬", "Companies": "🏢", "Settings": "⚙",
                }
                row = _NavRow(
                    label=label,
                    tcode=tcode or "",
                    has_arrow=has_arrow,
                    icon=icons.get(label, ""),
                )
                row.clicked.connect(self.navigate)
                self._nav_rows.append(row)
                nav_vbox.addWidget(row)

        nav_vbox.addStretch(1)
        scroll.setWidget(nav_container)
        outer.addWidget(scroll, 1)

        return sidebar

    # ─── Content area (stacked screens) ──────────────────────────────────────

    def _make_content_area(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background: {_CONTENT_BG};")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()

        # 0 → Dashboard (home with centered logo)
        self.dashboard = DashboardWidget(navigate=self.navigate, session=self.session)
        self.stack.addWidget(self.dashboard)

        # 1 → MM01/MM03
        self.mm01 = MM01Widget(self.api)
        self.stack.addWidget(self.mm01)

        # 2 → PO
        self.po_widget = POWidget(self.api)
        self.stack.addWidget(self.po_widget)

        # 3 → MIGO
        self.migo_widget = MIGOWidget(self.api)
        self.stack.addWidget(self.migo_widget)

        # 4 → PR
        self.pr_widget = PRWidget(self.api)
        self.stack.addWidget(self.pr_widget)

        # 5 → Supplier Invoice / LT01
        self.invoice_widget = SupplierInvoiceWidget(self.api)
        self.stack.addWidget(self.invoice_widget)

        # 6 → Supplier Master
        self.supplier_widget = SupplierWidget(self.api)
        self.stack.addWidget(self.supplier_widget)

        # 7 → RFQ
        self.rfq_widget = RFQWidget(self.api)
        self.stack.addWidget(self.rfq_widget)

        # 8 → Purchase Return
        self.return_widget = PurchaseReturnWidget(self.api)
        self.stack.addWidget(self.return_widget)

        # 9 → Reports
        self.reports_widget = ReportsWidget(self.api)
        self.stack.addWidget(self.reports_widget)

        # 10 → Material CSV Import
        self.import_widget = MaterialImportWidget(self.api)
        self.stack.addWidget(self.import_widget)

        # 11 → Coming soon (shared)
        self._coming_soon = _ComingSoon()
        self.stack.addWidget(self._coming_soon)

        layout.addWidget(self.stack)
        return wrapper

    # ─── Footer ──────────────────────────────────────────────────────────────

    def _make_footer(self) -> QFrame:
        foot = QFrame()
        foot.setFixedHeight(_FOOT_HEIGHT)
        foot.setStyleSheet(f"background: {_FOOT_BG};")
        layout = QHBoxLayout(foot)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        left = QLabel("PRIGAG BR<span style='color:#f59e0b;'>AIN</span> v1.21.0 — "
                      "<span style='color:#f59e0b;'>Prigag Online Pvt. Ltd.</span>")
        left.setTextFormat(Qt.TextFormat.RichText)
        left.setStyleSheet("color: #9ca3af; font-size: 9px;")

        center = QLabel("© 2026 Prigag Online Pvt. Ltd. All Rights Reserved.")
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setStyleSheet("color: #6b7280; font-size: 9px;")

        right = QLabel("support@prigag.com")
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.setStyleSheet("color: #2563eb; font-size: 9px;")

        layout.addWidget(left, 1)
        layout.addWidget(center, 2)
        layout.addWidget(right, 1)
        return foot

    # ─── Navigation ──────────────────────────────────────────────────────────

    def navigate(self, tcode: str) -> None:
        tcode = tcode.strip().upper()
        idx = _TCODE_INDEX.get(tcode, 11)
        self.stack.setCurrentIndex(idx)
        self.tcode_input.clear()

        # Sync sidebar selection
        for row in self._nav_rows:
            row.set_active(False)
        for row in self._nav_rows:
            if row._tcode.upper() == tcode:
                row.set_active(True)
                break

    def _on_tcode_enter(self) -> None:
        tcode = self.tcode_input.text().strip()
        if tcode:
            self.navigate(tcode)

    # ─── Logout ──────────────────────────────────────────────────────────────

    def _do_logout(self) -> None:
        try:
            self.api.logout()
        except ApiError:
            pass
        self.logged_out.emit()