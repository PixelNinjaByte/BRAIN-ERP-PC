"""
Brain ERP – MM01 (Create Material) screen  •  improved UI.

Two tabs:
  Create Material (MM01) – sectioned form with inline validation feedback.
  Materials List          – read-only list of created materials (GET /api/mm/materials).

Design goals
~~~~~~~~~~~~
* Clean light theme with clear black/dark text (#111827) for readability across all systems.
* Every field group lives in a styled QGroupBox with a clear heading.
* Required fields are marked with a red asterisk in the label.
* The status label gives colour-coded success / error feedback below the
  Save button without popping up a modal for every small thing.
* Duplicate-confirmation still uses a modal dialog (business logic requires it).
* No copy of server-side validation lives here – we rely on the FastAPI layer.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QPushButton, QLabel, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox, QFrame, QScrollArea,
    QSizePolicy,
)

from .api_client import ApiClient, ApiError


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _money_spin(maximum: float = 10_000_000) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(2)
    s.setRange(0, maximum)
    s.setSingleStep(1)
    s.setGroupSeparatorShown(True)
    s.setStyleSheet("color: #111827; background: #ffffff;")
    return s


def _req_label(text: str) -> QLabel:
    """Field label with black text and a red asterisk for required fields."""
    lbl = QLabel(f"<span style='color:#111827; font-weight:500;'>{text}</span> <span style='color:#dc2626;'>*</span>")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setStyleSheet("color: #111827;")
    return lbl


def _section(title: str) -> QGroupBox:
    """Consistently styled group-box section with dark text."""
    gb = QGroupBox(title)
    gb.setStyleSheet(
        "QGroupBox { font-weight: 700; font-size: 11px; color: #111827;"
        " border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 14px;"
        " padding-top: 14px; background: #ffffff; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px;"
        " background: #f9fafb; color: #2563eb; }"
        "QLabel { color: #111827; }"
        "QCheckBox { color: #111827; }"
        "QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { color: #111827; background: #ffffff; }"
    )
    return gb


# ──────────────────────────────────────────────────────────────────────────────
# Create Material tab
# ──────────────────────────────────────────────────────────────────────────────

class CreateMaterialTab(QWidget):

    def __init__(self, api: ApiClient, on_created: Callable[[], None]):
        super().__init__()
        self.api = api
        self._on_created = on_created
        self._lookups: dict = {}
        self._build_ui()
        self.reload_lookups()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Style the viewport, not QScrollArea directly, to avoid Qt CSS parse warnings
        scroll.viewport().setStyleSheet("background: #f8fafc;")

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(12)

        # ── page heading ─────────────────────────────────────────────────
        heading = QLabel("MM01 · Create Material")
        heading.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #111827; padding-bottom: 4px;"
        )
        body_layout.addWidget(heading)

        hint = QLabel(
            "<span style='color:#374151;'>Fields marked </span>"
            "<span style='color:#dc2626;'>*</span>"
            "<span style='color:#374151;'> are required. Leave <b>Item code</b> blank to auto-number.</span>"
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setStyleSheet("font-size: 11px;")
        body_layout.addWidget(hint)

        # ── two-column upper area ────────────────────────────────────────
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # LEFT column
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._section_basic())
        left.addWidget(self._section_tax())
        left.addStretch(1)

        # RIGHT column
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._section_classify())
        right.addWidget(self._section_flags())
        right.addStretch(1)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        body_layout.addLayout(cols)

        # ── planning / valuation (full width) ───────────────────────────
        body_layout.addWidget(self._section_planning())

        # ── action bar ──────────────────────────────────────────────────
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background: white; border-top: 1px solid #e5e7eb;"
            " border-radius: 0px; }"
        )
        ab_layout = QHBoxLayout(action_bar)
        ab_layout.setContentsMargins(20, 12, 20, 12)
        ab_layout.setSpacing(10)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(90)
        reset_btn.setStyleSheet(
            "QPushButton { background: #f3f4f6; color: #111827; border: 1px solid #d1d5db;"
            " padding: 8px 16px; border-radius: 6px; font-weight: 600; }"
            "QPushButton:hover { background: #e5e7eb; }"
        )
        reset_btn.clicked.connect(self._reset_form)

        self.save_btn = QPushButton("💾  Save Material")
        self.save_btn.setFixedWidth(160)
        self.save_btn.setDefault(True)
        self.save_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " padding: 8px 16px; border-radius: 6px; font-weight: 700; font-size: 12px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:disabled { background: #93c5fd; }"
        )
        self.save_btn.clicked.connect(self._save)

        ab_layout.addWidget(self.status_label)
        ab_layout.addWidget(reset_btn)
        ab_layout.addWidget(self.save_btn)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        outer.addWidget(action_bar)

    # ────────────────────────── section builders ──────────────────────────

    def _section_basic(self) -> QGroupBox:
        gb = _section("📋  Basic Data")
        f = QFormLayout()
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.item_code = QLineEdit()
        self.item_code.setPlaceholderText("Leave blank → auto-number (MAT000001 …)")
        self.item_name = QLineEdit()
        self.item_name.setPlaceholderText("Required — short description")
        self.description = QTextEdit()
        self.description.setFixedHeight(70)
        self.description.setPlaceholderText("Optional long description")
        self.manufacturer = QLineEdit()
        self.manufacturer.setPlaceholderText("Optional")

        lbl_code = QLabel("Item code")
        lbl_code.setStyleSheet("color: #111827;")
        lbl_desc = QLabel("Long text")
        lbl_desc.setStyleSheet("color: #111827;")
        lbl_mfg = QLabel("Manufacturer")
        lbl_mfg.setStyleSheet("color: #111827;")

        f.addRow(lbl_code, self.item_code)
        f.addRow(_req_label("Description"), self.item_name)
        f.addRow(lbl_desc, self.description)
        f.addRow(lbl_mfg, self.manufacturer)
        gb.setLayout(f)
        return gb

    def _section_classify(self) -> QGroupBox:
        gb = _section("🏷️  Classification")
        f = QFormLayout()
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.item_type = QComboBox()
        self.item_category = QComboBox()
        self.base_uom = QComboBox()
        self.material_group = QComboBox()

        lbl_type = QLabel("Material type")
        lbl_type.setStyleSheet("color: #111827;")
        lbl_cat = QLabel("Category")
        lbl_cat.setStyleSheet("color: #111827;")
        lbl_grp = QLabel("Material group")
        lbl_grp.setStyleSheet("color: #111827;")

        f.addRow(lbl_type, self.item_type)
        f.addRow(lbl_cat, self.item_category)
        f.addRow(_req_label("Base UOM"), self.base_uom)
        f.addRow(lbl_grp, self.material_group)
        gb.setLayout(f)
        return gb

    def _section_tax(self) -> QGroupBox:
        gb = _section("🧾  Tax & Compliance")
        f = QFormLayout()
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.hsn_code = QLineEdit()
        self.hsn_code.setPlaceholderText("4, 6 or 8 digits (e.g. 84715000)")
        self.gst_rate = QComboBox()

        lbl_hsn = QLabel("HSN / SAC code")
        lbl_hsn.setStyleSheet("color: #111827;")
        lbl_gst = QLabel("GST rate")
        lbl_gst.setStyleSheet("color: #111827;")

        f.addRow(lbl_hsn, self.hsn_code)
        f.addRow(lbl_gst, self.gst_rate)
        gb.setLayout(f)
        return gb

    def _section_flags(self) -> QGroupBox:
        gb = _section("⚙️  Status & Controls")
        layout = QVBoxLayout()
        layout.setSpacing(8)

        self.qc_required = QCheckBox("QC inspection required before use")
        self.batch_managed = QCheckBox("Batch / lot managed")
        self.is_active = QCheckBox("Active (available for transactions)")
        self.is_active.setChecked(True)

        for cb in (self.qc_required, self.batch_managed, self.is_active):
            cb.setStyleSheet("QCheckBox { spacing: 8px; color: #111827; font-weight: 500; }")
            layout.addWidget(cb)

        gb.setLayout(layout)
        return gb

    def _section_planning(self) -> QGroupBox:
        gb = _section("📊  Planning & Valuation")
        f = QFormLayout()
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.standard_price = _money_spin()
        self.min_stock = _money_spin()
        self.max_stock = _money_spin()
        self.reorder_point = _money_spin()

        def _spin(suffix: str = "") -> QSpinBox:
            sb = QSpinBox()
            sb.setRange(0, 3650)
            if suffix:
                sb.setSuffix(suffix)
            sb.setStyleSheet("color: #111827; background: #ffffff;")
            return sb

        def _dspin(suffix: str = "") -> QDoubleSpinBox:
            sb = QDoubleSpinBox()
            sb.setDecimals(3)
            sb.setRange(0, 1_000_000)
            if suffix:
                sb.setSuffix(suffix)
            sb.setStyleSheet("color: #111827; background: #ffffff;")
            return sb

        self.lead_time_days = _spin("  days")
        self.lot_size = _dspin()
        self.lot_size.setRange(0.001, 1_000_000)
        self.lot_size.setValue(1.0)
        self.gross_weight = _dspin("  kg")
        self.shelf_life_days = _spin("  days")

        row1 = QHBoxLayout()
        row1_left = QFormLayout()
        row1_left.setSpacing(10)
        row1_left.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def _lbl(t: str) -> QLabel:
            l = QLabel(t)
            l.setStyleSheet("color: #111827;")
            return l

        row1_left.addRow(_lbl("Standard price (₹)"), self.standard_price)
        row1_left.addRow(_lbl("Min stock"), self.min_stock)
        row1_left.addRow(_lbl("Max stock"), self.max_stock)
        row1_left.addRow(_lbl("Reorder point"), self.reorder_point)

        row1_right = QFormLayout()
        row1_right.setSpacing(10)
        row1_right.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        row1_right.addRow(_lbl("Lead time"), self.lead_time_days)
        row1_right.addRow(_lbl("Lot size"), self.lot_size)
        row1_right.addRow(_lbl("Gross weight"), self.gross_weight)
        row1_right.addRow(_lbl("Shelf life"), self.shelf_life_days)

        row1.addLayout(row1_left, 1)
        row1.addSpacing(24)
        row1.addLayout(row1_right, 1)

        f.addRow(row1)
        gb.setLayout(f)
        return gb

    # ────────────────────────── lookups ───────────────────────────────────

    def reload_lookups(self) -> None:
        try:
            self._lookups = self.api.mm_lookups()
        except ApiError as e:
            self.status_label.setStyleSheet("color: #dc2626; font-weight: 600;")
            self.status_label.setText(f"Could not load dropdown data: {e}")
            self.save_btn.setEnabled(False)
            return

        self.item_type.clear()
        for code, label in self._lookups.get("item_types", {}).items():
            self.item_type.addItem(label, code)

        self.item_category.clear()
        for code, label in self._lookups.get("item_categories", {}).items():
            self.item_category.addItem(label, code)

        self.base_uom.clear()
        for u in self._lookups.get("uoms", []):
            self.base_uom.addItem(f"{u['code']} — {u['name']}", u["id"])
        self.base_uom.setCurrentIndex(-1)

        self.material_group.clear()
        self.material_group.addItem("— None —", None)
        for g in self._lookups.get("material_groups", []):
            self.material_group.addItem(f"{g['code']} — {g['name']}", g["id"])

        self.gst_rate.clear()
        for rate in self._lookups.get("gst_rates", []):
            self.gst_rate.addItem(f"{rate:g}%", float(rate))

    # ────────────────────────── save / payload ───────────────────────────

    def _payload(self, confirm_dup: bool = False) -> dict:
        return {
            "item_code": self.item_code.text().strip(),
            "item_name": self.item_name.text().strip(),
            "description": self.description.toPlainText().strip(),
            "item_type": self.item_type.currentData() or "MATERIAL",
            "item_category": self.item_category.currentData() or "RAW",
            "base_uom_id": self.base_uom.currentData(),
            "material_group_id": self.material_group.currentData(),
            "hsn_code": self.hsn_code.text().strip(),
            "gst_rate": float(self.gst_rate.currentData() or 0.0),
            "standard_price": float(self.standard_price.value()),
            "min_stock": float(self.min_stock.value()),
            "max_stock": float(self.max_stock.value()),
            "reorder_point": float(self.reorder_point.value()),
            "lead_time_days": int(self.lead_time_days.value()),
            "lot_size": float(self.lot_size.value()),
            "gross_weight": float(self.gross_weight.value()),
            "shelf_life_days": int(self.shelf_life_days.value()),
            "qc_required": bool(self.qc_required.isChecked()),
            "batch_managed": bool(self.batch_managed.isChecked()),
            "manufacturer": self.manufacturer.text().strip(),
            "is_active": bool(self.is_active.isChecked()),
            "confirm_dup": confirm_dup,
        }

    def _save(self, confirm_dup: bool = False) -> None:
        if not self.item_name.text().strip():
            self._show_status("Material description is required.", error=True)
            self.item_name.setFocus()
            return
        if self.base_uom.currentData() is None:
            self._show_status("A valid Base UOM is required.", error=True)
            self.base_uom.setFocus()
            return

        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving…")
        self._show_status("", error=False)

        try:
            resp = self.api.mm_create(self._payload(confirm_dup))
        except ApiError as e:
            self._show_status(str(e), error=True)
            return
        finally:
            self.save_btn.setEnabled(True)
            self.save_btn.setText("💾  Save Material")

        data = resp.get("data") or {}
        if data.get("needs_confirmation"):
            self._ask_confirm_duplicate(data.get("similar") or [])
            return

        self._show_status(resp.get("message") or "✅  Material created successfully.", error=False)
        self._reset_form()
        self._on_created()

    def _ask_confirm_duplicate(self, similar: list) -> None:
        lines = [
            f"  •  {s.get('item_code')} – {s.get('item_name')} ({s.get('score')}% match)"
            for s in similar
        ]
        text = (
            "A very similar material already exists:\n\n"
            + "\n".join(lines)
            + "\n\nCreate this one anyway?"
        )
        reply = QMessageBox.question(
            self, "Similar material found", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._save(confirm_dup=True)
        else:
            self._show_status("Not saved — change the description or confirm to proceed.", error=False)

    def _show_status(self, msg: str, error: bool) -> None:
        color = "#dc2626" if error else "#16a34a"
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.status_label.setText(msg)

    def _reset_form(self) -> None:
        self.item_code.clear()
        self.item_name.clear()
        self.description.clear()
        self.manufacturer.clear()
        self.hsn_code.clear()
        self.item_type.setCurrentIndex(0)
        self.item_category.setCurrentIndex(0)
        self.base_uom.setCurrentIndex(-1)
        self.material_group.setCurrentIndex(0)
        self.gst_rate.setCurrentIndex(0)
        self.standard_price.setValue(0)
        self.min_stock.setValue(0)
        self.max_stock.setValue(0)
        self.reorder_point.setValue(0)
        self.lead_time_days.setValue(0)
        self.lot_size.setValue(1.0)
        self.gross_weight.setValue(0)
        self.shelf_life_days.setValue(0)
        self.qc_required.setChecked(False)
        self.batch_managed.setChecked(False)
        self.is_active.setChecked(True)
        self.item_name.setFocus()


# ──────────────────────────────────────────────────────────────────────────────
# Materials List tab
# ──────────────────────────────────────────────────────────────────────────────

class MaterialsListTab(QWidget):
    _COLUMNS = [
        ("item_code",       "Code"),
        ("item_name",       "Description"),
        ("item_type",       "Type"),
        ("item_category",   "Category"),
        ("uom_code",        "UOM"),
        ("group_name",      "Group"),
        ("gst_rate",        "GST %"),
        ("standard_price",  "Price (₹)"),
        ("is_active",       "Active"),
    ]

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        # toolbar
        top = QHBoxLayout()
        heading = QLabel("Materials List (latest 200)")
        heading.setStyleSheet("font-size: 13px; font-weight: 700; color: #111827;")
        top.addWidget(heading)
        top.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color: #374151; font-weight: 500;")
        top.addWidget(self.count_lbl)
        refresh_btn = QPushButton("⟳  Refresh")
        refresh_btn.setStyleSheet(
            "QPushButton { background: #f3f4f6; color: #111827; border: 1px solid #d1d5db;"
            " padding: 6px 14px; border-radius: 6px; font-weight: 500; }"
            "QPushButton:hover { background: #e5e7eb; }"
        )
        refresh_btn.clicked.connect(self.reload)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        # table
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in self._COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #e5e7eb; border: 1px solid #e5e7eb;"
            " border-radius: 6px; background: #ffffff; color: #111827; }"
            "QTableWidget::item { padding: 6px; color: #111827; }"
            "QHeaderView::section { background: #f9fafb; font-weight: 600;"
            " border: none; border-bottom: 1px solid #e5e7eb; padding: 6px; color: #111827; }"
        )
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #dc2626;")
        layout.addWidget(self.status_label)

        self.reload()

    def reload(self) -> None:
        try:
            rows = self.api.mm_list()
        except ApiError as e:
            self.status_label.setText(f"Could not load materials: {e}")
            return

        self.status_label.setText("")
        self.count_lbl.setText(f"{len(rows)} material(s)")
        self.table.setRowCount(len(rows))

        for r, row in enumerate(rows):
            for c, (key, _) in enumerate(self._COLUMNS):
                val = row.get(key)
                if key == "is_active":
                    val = "✅ Yes" if val else "❌ No"
                elif key == "standard_price" and val is not None:
                    val = f"₹ {float(val):,.2f}"
                item = QTableWidgetItem("" if val is None else str(val))
                item.setForeground(QColor("#111827"))
                if key == "item_code":
                    item.setFont(QFont("Consolas", 9))
                self.table.setItem(r, c, item)


# ──────────────────────────────────────────────────────────────────────────────
# Public widget (tab container)
# ──────────────────────────────────────────────────────────────────────────────

class MM01Widget(QTabWidget):
    """Top-level widget hosted by MainWindow: Create Material + Materials list."""

    def __init__(self, api: ApiClient):
        super().__init__()
        self.setStyleSheet(
            "QTabBar::tab { padding: 10px 20px; font-weight: 600; color: #111827; }"
            "QTabBar::tab:selected { color: #2563eb; border-bottom: 2px solid #2563eb; }"
        )
        self.materials_tab = MaterialsListTab(api)
        self.create_tab = CreateMaterialTab(api, on_created=self.materials_tab.reload)
        self.addTab(self.create_tab, "➕  Create Material (MM01)")
        self.addTab(self.materials_tab, "📋  Materials List")
