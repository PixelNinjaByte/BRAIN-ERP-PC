"""
Brain ERP – Main window shell (post-login).

Sidebar is now a collapsible tree:

  ▪ HR                         ▸
  ▪ MATERIALS                  ▾
      MASTER DATA
        MATERIAL               ▾
          Create Material      CRMA
          Change Material      CHMA
          ...
        SUPPLIER               ▾
          Create Supplier      CRSU
          Change Supplier      CHSU
  ▪ PURCHASING                 ▸
  ...
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit,
    QStackedWidget, QScrollArea,
)

from .api_client import ApiClient, ApiError
from .dashboard_widget import DashboardWidget
from .mm.mm01_widget import MM01Widget
from .mm.po_widget import POWidget
from .mm.migo_widget import MIGOWidget
from .mm.pr_widget import PRWidget
from .mm.supplier_invoice_widget import SupplierInvoiceWidget
from .mm.supplier_widget import SupplierWidget
from .mm.rfq_widget import RFQWidget
from .mm.purchase_return_widget import PurchaseReturnWidget
from .mm.reports_widget import ReportsWidget
from .mm.material_import_widget import MaterialImportWidget
from .skeleton_card import SkeletonCardRow
from .admin.settings import SettingsWidget
from .sidebar_icons import (
    ICONS, CHEVRON_RIGHT, CHEVRON_DOWN, logo_svg, svg_pixmap,
)

# ── Cmm.material_import_widget────────────────────────────────────────────────────
_SIDE_BG       = "#1a1c20"      # near-black neutral — sidebar background
_SIDE_HOVER    = "#23262c"      # hover row
_SIDE_ACTIVE   = "#2563eb"      # selected / active row
_SIDE_TEXT     = "#c9ced8"      # leaf item text
_SIDE_GROUP    = "#8a909c"      # module / group label text (muted)
_SIDE_SECTION  = "#5f6673"      # T-code hints
_SIDE_DIVIDER  = "#2a2d33"
_SIDE_WIDTH    = 256

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
    "CRMA": 1, "CHMA": 1, "DIMA": 1, "MAGRW": 1,
    "MAIME": 10,
    "ME21N": 2, "ME23N": 2, "ME2L": 2, "ME2M": 2, "ME28": 2, "ME29N": 2, "PO": 2,
    "MIGO": 3, "GRN": 3, "MB51": 3,
    "ME51N": 4, "ME53N": 4, "PR": 4,
    "MR01": 5, "MIRO": 5, "LT01": 5, "VK11": 5,
    "XK01": 6, "XK02": 6, "SUPP": 6,
    "CRSU": 6, "CHSU": 6,
    "ME41": 7, "ME47": 7, "RFQ": 7,
    "PRTN": 8,
    "ZMRP": 9, "ME2PN": 9, "ZMMR": 9, "ZREP": 9,
    "ZMMI": 10,
    "SU01": 12
}

# ── Sidebar tree ──────────────────────────────────────────────────────────────
# Node = (label, code_hint, tcode, children)
#   children is None  → leaf (clickable, navigates to tcode)
#   children is []    → group with chevron but nothing inside yet (navigates to tcode)
#   children is [...] → expandable group
# Top-level labels must match a key in sidebar_icons.ICONS.
_NAV_TREE: list[tuple] = [
    ("ADMIN", "", "SU01", [
        ("AUTHORIZATION", "", "", [
                ("User Maintenance", "USMA",  "USMA",  None),
                ("Role Maintenance", "ROMA",  "ROMA",  None),
                ("T-Code Access Requests", "TCOA",  "TCOA",  None),
                ("My Access Requests", "MYACA",  "MYACA",  None),
        ]),
        ("ORGANIZATION", "", "", [
            ("Org Structure (Category / Unit / Department)", "ORSTC", "ORSTC", None),
            ("Company Configuration", "COCO", "COCO", None),
            ("Companies (Multi-Company Setup)", "COMUC", "COMUC", None),
            ("Master Data Link Check (Suppliers/Materials/PR by company)", "ZMDC", "ZMDC", None),
            ("Material Database Link (find / import materials for dropdowns)", "ZMAT", "ZMAT", None)
        ]),
        ("SYSTEM", "", "", [
            ("Number Range Maintenance", "NURAM", "NURAM", None),
            ("Release Strategy Config", "RESTC", "RESTC", None),
            ("Quality/Price Deduction Reserve (Confidential)", "QUPRD", "QUPRD", None)
        ]),
        ("MY WORKSPACE", "", "", [
            ("My Pending Approvals", "MYPEA", "MYPEA", None),
            ("My Account", "MYAC", "MYAC", None),
            ("My Preferences", "MYPR", "MYPR", None)
        ]),
        ("REPORTS", "", "", [
            ("Report Center (all reports)", "RECEA", "RECEA", None),
            ("AUTHORIZATION & AUDIT", "", "", [
                ("Users with Roles (Authorization Report)", "USWIR", "USWIR", None),
                ("Change Documents (Audit Log)", "CHDOA", "CHDOA", None),
                ("System Log", "SYLO", "SYLO", None)
            ])
        ]),
    ]),
    ("HR", "", "", [
        ("PERSONNEL ADMINISTRATION", "", "", [
            ("Create Employee", "CREM", "", None),
            ("Employee Master Data", "EMMAD", "", None)
        ]),
        ("ORGANIZATIONAL MANAGEMENT", "", "", [
            ("Organization Management", "ORMA", "", None)
        ]),
        ("TIME MANAGEMENT", "", "", [
            ("Leave Management", "LEMA", "", None),
            ("Attendance Sheet", "ATSH", "", None)
        ]),
        ("PAYROLL", "", "", [
            ("Process Payroll", "PRPA", "", None),
            ("Generate Payslips", "GEPA", "", None)
        ]),
        ("REPORTS", "", "", [
            ("PERSONNEL", "", "", [
                ("Employee List", "EMLI", "", None),
                ("Headcount by Department", "HEBYD", "", None)
            ]),
            ("TIME MANAGEMENT", "", "", [
                ("Leave Balances", "LEBA", "", None),
                ("Attendance Summary (Monthly)", "ATSUM", "", None)
            ]),
            ("PAYROLL", "", "", [
                ("Payroll Register", "PARE", "", None)
            ])
        ])
    ]),
    ("MATERIALS", "", "", [
        ("MASTER DATA", "", "", [
            ("MATERIAL", "", "", [
                ("Create Material",          "CRMA",  "CRMA",  None),
                ("Change Material",          "CHMA",  "CHMA",  None),
                ("Display Material",         "DIMA",  "DIMA",  None),
                ("Material Groups (w...)",   "MAGRW", "MAGRW", None),
                ("Material Import / Ex...",  "MAIME", "MAIME", None),
                ("Price List",               "PRLIM", "PRLIM", None),
            ]),
            ("SUPPLIER", "", "", [
                ("Create Supplier", "CRSU", "CRSU", None),
                ("Change Supplier", "CHSU", "CHSU", None),
            ]),
        ]),
        ("PURCHASING", "", "", [
            ("PURCHASE REQUISITION", "", "", [
                ("Create Purchase  Re...",          "ME51N",  "ME51N",  None),
                ("Display Purchase Re...",         "ME53N",  "ME53N",  None),
            ]),
            ("RFQ / QUOTATION", "", "", [
                ("Create RFQ (Request...", "ME41", "ME41", None),
                ("Compare Quotations", "ME47", "ME47", None),
            ]),
            ("BIDS / TENDERS", "", "", [
                ("Create Bid / Tender", "BID01", "BID01", None),
                ("Manage Bids / Tend...", "BID02", "BID02", None),
            ]),
            ("PURCHASE ORDER", "", "", [
                ("Create Purchase Or...", "ME21N", "ME21N", None),
                ("Display Purchase Or...", "ME23N", "ME23N", None),
            ]),
            ("SERVICE PROCUREMENT", "", "", [
                ("Service Entry Sheet", "ML81N", "ML81N", None),
            ]),
            ("INVENTORY MANAGEMENT", "", "", [
                ("GOOD MOVEMENT", "", "", [
                    ("Goods Movement(...", "MIGO", "MIGO", None),
                    ("Stock Transfer", "", "", None)
                ]),
                ("RETURNS", "", "", [
                    ("Purchase Return(go...", "PRTN", "PRTN", None)
                ]),
                ("PHYSICAL INVENTORY", "", "", [
                    ("Create Physical Inve...", "", "", None),
                    ("Post Physical Invent...", "", "", None)
                ]),
            ]),
            ("INVOICE VERIFICATION", "", "", [
                ("Invoice Verification", "INVE", "INVE", None)                
            ]),
            ("REPORTS", "", "", [
                ("PURCHASING", "", "", [
                    ("Purchasing  Orders by S...", "ME2L", "ME2L", None),
                    ("Purchase Orders by ...", "ME2M", "ME2M", None),
                    ("Purchase Requisition...", "PUREL", "PUREL", None),
                    ("Purchase Price Histo...", "PUPRH", "PUPRH", None),
                    ("MM Pending Report", "MMPER", "MMPER", None),
                    ("MM Procurement F...", "MMPRF", "MMPRF", None)
                ]),
                ("INVENTORY", "", "", [
                    ("Stock Overview", "STOV", "STOV", None),
                    ("List of Stock  Values (...", "MB5L", "MB5L", None),
                    ("Stocks for Posting D...", "STFOP", "STFOP", None),
                    ("Material Document ...", "MADOL", "MADOL", None),
                    ("Reorder Report (belo...", "REREB", "REREB", None),
                    ("Slow-Moving Items", "SLMOI", "SLMOI", None),
                    ("ABC Analysis", "ABAN", "ABAN", None)
                ]),
                ("INVOICE VERIFICATION", "", "", [
                    ("GR/IR Analysis (Rece...", "GRIRA", "GRIRA", None)
                ]),       
            ]),
            ("Relese Blocked Supplie...", "REBLS", "REBLS", None)
        ]),

    ]),
    ("SALES", "", "", [
        ("MASTER DATA", "", "", [
            ("Create Customer", "CRCU", "", None)
        ]),
        ("SALES", "", "", [
            ("QUOTATION", "", "", [
                ("Create Quotation", "CRQU", "", None)
            ]),
            ("SALES ORDER", "", "", [
                ("Create Sales Order", "CRSAO", "", None),
                ("Display Sales Orders", "DISAO", "", None)
            ])
        ]),
        ("SHIPPING", "", "", [
            ("Create Delivery Note", "CRDEN", "", None),
            ("Generate E-Way Bill", "GEEW", "", None)
        ]),
        ("BILLING", "", "", [
            ("Create Sales Invoice", "CRSAI", "", None),
            ("Display Invoices", "DIIN", "", None)
        ]),
        ("RETURNS", "", "", [
            ("Sales Return (goods back from customer)", "SAREG", "", None)
        ]),
        ("REPORTS", "", "", [
            ("SALES DOCUMENTS", "", "", [
                ("List of Sales Orders", "VA05", "", None),
                ("Backorders (Open Sales Order Items)", "BAOPS", "", None),
                ("Outbound Delivery Monitor", "OUDEM", "", None),
                ("List of Billing Documents", "LIOFB", "", None)
            ]),
            ("SALES ANALYSIS", "", "", [
                ("Customer Analysis (Sales by Customer)", "CUANS", "", None),
                ("Material Analysis (Sales by Material)", "MAANS", "", None)
            ])
        ])
    ]),
("BOM", "", "", [
        ("BILL OF MATERIAL", "", "", [
            ("Create BOM", "CRBO", "", None),
            ("Display BOM", "DIBO", "", None)
        ]),
        ("ROUTING & WORK CENTER", "", "", [
            ("Create Routing", "CRRO", "", None),
            ("Create Work Center", "CRWOC", "", None)
        ]),
        ("REPORTS", "", "", [
            ("BILL OF MATERIAL", "", "", [
                ("BOM Overview with Material Cost", "BOOVW", "", None)
            ])
        ])
    ]),
("PRODUCTION", "", "", [
        ("PLANNING", "", "", [
            ("Production Planning", "PRPL", "", None)
        ]),
        ("WORK ORDERS", "", "", [
            ("Create Work Order", "CRWOO", "", None),
            ("Work Order Completion", "WOORC", "", None)
        ]),
        ("REPORTS", "", "", [
            ("PRODUCTION ORDERS", "", "", [
                ("Production Report", "PRRE", "", None),
                ("Production Output & Variance", "PROUV", "", None),
                ("Material Consumption vs Requirement", "MACOV", "", None)
            ]),
            ("COSTING", "", "", [
                ("Production Cost Report", "PRCOR", "", None)
            ])
        ])
    ]),
 ("ACCOUNTING", "", "", [
        ("GENERAL ACCOUNT", "", "", [
            ("Chart of Accounts", "CHOFA", "", None),
            ("Post Journal Entry", "POJOE", "", None),
            ("Journal Register (Display / Reverse)", "JORED", "", None),
            ("Approve Journal Entries", "APJOE", "", None),
            ("Opening Balance Upload", "OPBAU", "", None),
            ("Create Cost Center", "CRCOC", "", None),
            ("Change Cost Center", "CHCOC", "", None),
            ("Display Cost Centers", "DICOC", "", None)
        ]),
        ("ACCOUNTS RECEIVABLE", "", "", [
            ("Customer Receipt", "CURE", "", None),
            ("Customer Credit Note", "CUCRN", "", None),
            ("Apply Advance / Open Credit Note", "APADO", "", None)
        ]),
        ("ACCOUNTS PAYABLE", "", "", [
            ("Supplier Payment", "SUPA", "", None),
            ("Supplier Payment Proposal", "SUPAP", "", None),
            ("Approve Payments", "APPA", "", None),
            ("Supplier Debit Note", "SUDEN", "", None)
        ]),
        ("BANK", "", "", [
            ("Bank Reconciliation", "BARE", "", None)
        ]),
        ("FIXED ASSETS", "", "", [
            ("Run Depreciation", "RUDE", "", None)
        ]),
        ("GST RETURNS", "", "", [
            ("E-Invoice and E-Way Compliance", "EINA", "", None)
        ]),
        ("TDS", "", "", [
            ("TDS Sections and Rates", "TDSEA", "", None)
        ]),
        ("PERIOD-END & CONFIGURATION", "", "", [
            ("Fiscal Years and Posting Periods", "FIYEA", "", None),
            ("Year-End Closing", "YEENC", "", None),
            ("Post Pending Documents to GL", "POPED", "", None),
            ("GL Account Determination", "GLACD", "", None),
            ("Finance Settings", "FISE", "", None),
            ("Exchange Rates", "EXRA", "", None)
        ]),
        ("REPORTS", "", "", [
            ("FINANCIAL STATEMENTS", "", "", [
                ("Balance Sheet", "BASH", "", None),
                ("P&L Statement", "PLST", "", None),
                ("Trial Balance", "TRBA", "", None),
                ("Cash Flow Statement", "CAFLS", "", None),
                ("G/L Account Balances", "GLA", "", None),
                ("GL Account Statement", "GLACS", "", None),
                ("Day Book", "DABO", "", None)
            ]),
            ("RECEIVABLES & PAYABLES", "", "", [
                ("Customer Open Items & Ageing", "CUOPI", "", None),
                ("Supplier Open Items & Ageing", "SUOPI", "", None)
            ]),
            ("BANK & CASH", "", "", [
                ("Cash and Bank Book", "CAANB", "", None)
            ]),
            ("COST CENTER ACCOUNTING", "", "", [
                ("Cost Center P&L and Budget", "COCEP", "", None)
            ]),
            ("FIXED ASSETS", "", "", [
                ("Asset Register and Depreciation", "ASREA", "", None)
            ]),
            ("GST & TDS", "", "", [
                ("GSTR-1 Outward Supplies", "GSOUS", "", None),
                ("GSTR-3B Summary Return", "GSTR3B", "", None),
                ("GSTR-2B Reconciliation", "GSBR", "", None),
                ("TDS Register and Returns", "TDREA", "", None)
            ])
        ])
    ]),
    ("QUALITY", "", "", [
        ("INSPECTION", "", "", [
            ("Inspection Lots", "INLO", "", None),
            ("Record Inspection Results", "REINR", "", None)
        ]),
        ("QUALITY NOTIFICATION", "", "", [
            ("Non-Conformance Report", "NOCOR", "", None)
        ]),
        ("REPORTS", "", "", [
            ("INSPECTION", "", "", [
                ("List of Inspection Lots", "LIOFI", "", None)
            ]),
            ("QUALITY NOTIFICATION", "", "", [
                ("List of Quality Notifications (NCR)", "LIOFQ", "", None)
            ])
        ])
    ]),
    ("PLANT MAINT.", "", "", [
        ("TECHNICAL OBJECTS", "", "", [
            ("Create Asset", "CRAS", "", None)
        ]),
        ("MAINTENANCE PROCESSING", "", "", [
            ("Create Maintenance Order", "CRMAO", "", None),
            ("Maintenance Plan", "MAPL", "", None)
        ]),
        ("REPORTS", "", "", [
            ("EQUIPMENT & ORDERS", "", "", [
                ("Equipment List", "EQLI", "", None),
                ("List of Maintenance Orders", "LIOFM", "", None),
                ("Maintenance Report", "MARE", "", None)
            ])
        ])
    ]),
    ("PROJECTS & DEVELOPMENTS", "", "", [
        ("PROJECT DEFINITION", "", "", [
            ("Create / Change Project (Project Definition)", "CRPR", "", None),
            ("Project Builder — Workspace (WBS, Funds, Team, Links)", "PRMAW", "", None)
        ]),
        ("BUDGET & COSTS", "", "", [
            ("WBS Budget Planning & Release", "PRBUR", "", None)
        ]),
        ("EXECUTION", "", "", [
            ("Project Planning — Activities, Gantt & Baseline", "PRACN", "", None)
        ]),
        ("REPORTS", "", "", [
            ("PROJECT REPORTS", "", "", [
                ("Project Portfolio / Project List", "PRLIP", "", None),
                ("Project Actual Cost Report", "PRACC", "", None),
                ("Project Budget vs Actual", "PRBUV", "", None)
            ])
        ]),
        ("Project Funds — Funding Sources & Allocation", "PJFM", "", None),
        ("Project Timesheets (HR link)", "PJTS", "", None),
        ("Project Developments, Issues, Risks & Change Requests", "PJDV", "", None),
        ("Project Document Linking (MM / FI / Production / HR)", "PJLK", "", None),
        ("Projects Administration & Cost Sync", "PJAD", "", None)
    ]),
    ("COMMUNICATION", "", "", [
        ("BUSINESS WORKPLACE", "", "", [
            ("Business Workplace (Inbox)", "BUWOI", "", None),
            ("Compose Message", "COME", "", None)
        ])
    ])
]

# Fixed links under the divider (no chevron)
_NAV_BOTTOM: list[tuple[str, str]] = [
    ("Companies", "COMP"),
    ("Settings",  "SU01"),
]

# Groups expanded on first show
_DEFAULT_OPEN: set[str] = set()


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
    """
    One row in the sidebar tree.

    level 0 : top module   (coloured SVG icon + bold caps + chevron)
    level 1 : sub group    (bold caps, indented)
    level 2 : sub group    (bold caps, indented more, chevron)
    level 3 : leaf         (normal weight + dim T-code on the right)
    bottom  : fixed link   (icon + normal-case text, e.g. Companies / Settings)
    """

    clicked = pyqtSignal(str)   # emits tcode

    _INDENT = {0: 14, 1: 28, 2: 40, 3: 52}

    def __init__(self, label: str, tcode: str, level: int = 0,
                 code: str = "", expandable: bool = False,
                 expanded: bool = False, icon_svg: str | None = None,
                 bottom: bool = False, parent=None):
        super().__init__(parent)
        self._tcode = tcode
        self._level = level
        self._active = False
        self._bottom = bottom
        self._expandable = expandable
        self.expanded = expanded
        self._is_leaf = not expandable and level >= 3

        if bottom:
            self.setFixedHeight(42)
        else:
            self.setFixedHeight(32 if self._is_leaf else 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        left = 16 if bottom else self._INDENT.get(level, 52)
        layout.setContentsMargins(left, 0, 14, 0)
        layout.setSpacing(8 if bottom else 6)

        if icon_svg:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(18, 18)
            icon_lbl.setPixmap(svg_pixmap(icon_svg, 18))
            icon_lbl.setStyleSheet("background: transparent;")
            layout.addWidget(icon_lbl)

        self._lbl = QLabel(label)
        layout.addWidget(self._lbl, 1)

        if code:
            code_lbl = QLabel(code)
            code_lbl.setStyleSheet(
                f"color: {_SIDE_SECTION}; font-size: 8px; font-weight: 700;"
                " background: transparent;"
            )
            layout.addWidget(code_lbl)

        self._arrow = None
        if expandable:
            self._arrow = QLabel()
            self._arrow.setFixedSize(12, 12)
            self._arrow.setStyleSheet("background: transparent;")
            layout.addWidget(self._arrow)
            self._refresh_arrow()

        self._apply_style(False)

    # ─── visuals ─────────────────────────────────────────────────────────
    def _refresh_arrow(self) -> None:
        if self._arrow is not None:
            svg = CHEVRON_DOWN if self.expanded else CHEVRON_RIGHT
            self._arrow.setPixmap(svg_pixmap(svg, 12))

    def _apply_style(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_SIDE_ACTIVE};")
        else:
            self.setStyleSheet(
                f"background: {_SIDE_HOVER};" if hover else "background: transparent;"
            )

        if self._bottom:
            txt = "white" if self._active else "#d1d5db"
            css = f"color: {txt}; font-size: 13px; font-weight: 500;"
        elif self._is_leaf:
            txt = "white" if self._active else _SIDE_TEXT
            css = (f"color: {txt}; font-size: 11px; font-weight: "
                   f"{700 if self._active else 500}; letter-spacing: 0.2px;")
        else:
            txt = "white" if self._active else _SIDE_GROUP
            css = (f"color: {txt}; font-size: 11px; font-weight: 700;"
                   " letter-spacing: 1px;")
        self._lbl.setStyleSheet(css + " background: transparent;")

    # ─── state ───────────────────────────────────────────────────────────
    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style(False)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self._refresh_arrow()

    # ─── events ──────────────────────────────────────────────────────────
    def enterEvent(self, e):
        if not self._active:
            self._apply_style(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._active:
            self._apply_style(False)
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

        root_vbox.addWidget(self._make_topbar())

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

        logo_circle = QLabel("B")
        logo_circle.setFixedSize(28, 28)
        logo_circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_circle.setStyleSheet(
            "background: #1a1f2e; color: white; font-size: 12px; font-weight: 800;"
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

        self.tcode_input = QLineEdit()
        self.tcode_input.setPlaceholderText("Hi, how can I help you?")
        self.tcode_input.setFixedWidth(260)
        self.tcode_input.setFixedHeight(32)
        self.tcode_input.setStyleSheet(
            "QLineEdit { background: #f9fafb; color: #111827;"
            " border: 1px solid #d1d5db;"
            " padding: 0px 10px; font-size: 12px; }"
            "QLineEdit:focus { border-color: #2563eb; background: white; }"
        )
        self.tcode_input.returnPressed.connect(self._on_tcode_enter)

        go_btn = QPushButton("›")
        go_btn.setFixedSize(32, 32)
        go_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " font-size: 18px; font-weight: 700; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        go_btn.clicked.connect(self._on_tcode_enter)

        company_btn = QPushButton("BRAIN.PRIGAG.COM  ▾")
        company_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #374151;"
            " border: 1px solid #d1d5db;"
            " padding: 4px 10px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { background: #f3f4f6; }"
        )

        bell_btn = QPushButton("N")
        bell_btn.setFixedSize(32, 32)
        bell_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: #374151; font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background: #f3f4f6; }"
        )

        who = self.session.get("full_name") or self.session.get("username") or "User"
        initials = who[0].upper() if who else "U"

        avatar = QLabel(initials)
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background: #2563eb; color: white;"
            " font-size: 11px; font-weight: 700;"
        )

        user_btn = QPushButton(f"  {who.split()[0]}  ▾")
        user_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #374151; border: none;"
            " font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { color: #111827; }"
        )
        user_btn.clicked.connect(self._do_logout)

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

        # ── Brand header block (blue gradient + SVG logo) ─────────────────
        brand_block = QFrame()
        brand_block.setFixedHeight(64)
        brand_block.setStyleSheet(
            "QFrame { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #1e5db3, stop:1 #1a2236); }"
            "QLabel { background: transparent; }"
        )
        bb_layout = QHBoxLayout(brand_block)
        bb_layout.setContentsMargins(10, 8, 12, 8)
        bb_layout.setSpacing(10)

        logo_lbl = QLabel()
        logo_lbl.setFixedSize(44, 44)
        logo_lbl.setPixmap(svg_pixmap(logo_svg(), 44))

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_col.setContentsMargins(0, 0, 0, 0)

        name_top = QLabel("BRAIN.PRIGAG.C...")
        name_top.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 800; letter-spacing: 0.3px;"
        )
        name_bot = QLabel("Powered by PRIGAG BRAIN")
        name_bot.setStyleSheet("color: #f59e0b; font-size: 10px;")
        name_col.addStretch(1)
        name_col.addWidget(name_top)
        name_col.addWidget(name_bot)
        name_col.addStretch(1)

        bb_layout.addWidget(logo_lbl)
        bb_layout.addLayout(name_col, 1)
        outer.addWidget(brand_block)

        # ── Scrollable nav tree ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet(f"background: {_SIDE_BG};")
        scroll.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #3a3f47;"
            " min-height: 30px; border-radius: 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }"
        )

        nav_container = QWidget()
        nav_container.setStyleSheet(f"background: {_SIDE_BG};")
        nav_vbox = QVBoxLayout(nav_container)
        nav_vbox.setContentsMargins(0, 4, 0, 0)
        nav_vbox.setSpacing(0)

        self._nav_rows = []
        self._add_nodes(nav_vbox, _NAV_TREE, level=0)

        # divider + fixed bottom links
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {_SIDE_DIVIDER}; margin: 0;")
        nav_vbox.addWidget(div)

        for label, tcode in _NAV_BOTTOM:
            row = _NavRow(label=label, tcode=tcode, level=0,
                          icon_svg=ICONS.get(label), bottom=True)
            row.clicked.connect(self.navigate)
            self._nav_rows.append(row)
            nav_vbox.addWidget(row)

        nav_vbox.addStretch(1)
        scroll.setWidget(nav_container)
        outer.addWidget(scroll, 1)

        return sidebar

    def _add_nodes(self, parent_layout: QVBoxLayout, nodes: list, level: int) -> None:
        """Recursively build the collapsible nav tree."""
        for label, code, tcode, children in nodes:
            expandable = children is not None
            has_kids = bool(children)
            row = _NavRow(
                label=label,
                tcode=tcode,
                level=level,
                code=code,
                expandable=expandable,
                expanded=has_kids and label in _DEFAULT_OPEN,
                icon_svg=ICONS.get(label) if level == 0 else None,
            )
            parent_layout.addWidget(row)

            if has_kids:
                box = QWidget()
                box.setStyleSheet("background: transparent;")
                box_lay = QVBoxLayout(box)
                box_lay.setContentsMargins(0, 0, 0, 0)
                box_lay.setSpacing(0)
                self._add_nodes(box_lay, children, level + 1)
                box.setVisible(row.expanded)
                parent_layout.addWidget(box)
                row.clicked.connect(lambda _t, r=row, b=box: self._toggle(r, b))
            else:
                # leaf, or group with arrow but no children yet → navigate
                row.clicked.connect(self.navigate)
                if tcode:
                    self._nav_rows.append(row)

    @staticmethod
    def _toggle(row: _NavRow, box: QWidget) -> None:
        row.set_expanded(not row.expanded)
        box.setVisible(row.expanded)

    # ─── Content area (stacked screens) ──────────────────────────────────────

    def _make_content_area(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background: {_CONTENT_BG};")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()

        self.dashboard = DashboardWidget(navigate=self.navigate, session=self.session)
        self.stack.addWidget(self.dashboard)                 # 0

        self.mm01 = MM01Widget(self.api)
        self.stack.addWidget(self.mm01)                      # 1

        self.po_widget = POWidget(self.api)
        self.stack.addWidget(self.po_widget)                 # 2

        self.migo_widget = MIGOWidget(self.api)
        self.stack.addWidget(self.migo_widget)               # 3

        self.pr_widget = PRWidget(self.api)
        self.stack.addWidget(self.pr_widget)                 # 4

        self.invoice_widget = SupplierInvoiceWidget(self.api)
        self.stack.addWidget(self.invoice_widget)            # 5

        self.supplier_widget = SupplierWidget(self.api)
        self.stack.addWidget(self.supplier_widget)           # 6

        self.rfq_widget = RFQWidget(self.api)
        self.stack.addWidget(self.rfq_widget)                # 7

        self.return_widget = PurchaseReturnWidget(self.api)
        self.stack.addWidget(self.return_widget)             # 8

        self.reports_widget = ReportsWidget(self.api)
        self.stack.addWidget(self.reports_widget)            # 9

        self.import_widget = MaterialImportWidget(self.api)
        self.stack.addWidget(self.import_widget)             # 10

        self._coming_soon = _ComingSoon()
        self.stack.addWidget(self._coming_soon)              # 11

        self.settings_widget = SettingsWidget(self.api, self.session)
        self.stack.addWidget(self.settings_widget)            # 12

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