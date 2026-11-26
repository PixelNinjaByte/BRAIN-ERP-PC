"""
Brain ERP – Request for Quotation & Comparison Widget (ME41 / ME47).
Tabs:
  1. Create RFQ (ME41)
  2. Quotation Comparison Matrix (ME47)
"""

from __future__ import annotations

from datetime import date
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QFrame,
    QScrollArea, QMessageBox, QDateEdit
)

from ..api_client import ApiClient, ApiError


def _section(title: str) -> QGroupBox:
    gb = QGroupBox(title)
    gb.setStyleSheet(
        "QGroupBox { font-weight: 700; font-size: 11px; color: #111827;"
        " border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 14px;"
        " padding-top: 14px; background: #ffffff; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px;"
        " background: #f9fafb; color: #2563eb; }"
        "QLabel { color: #111827; }"
        "QLineEdit, QComboBox, QDoubleSpinBox, QDateEdit { color: #111827; background: #ffffff; }"
    )
    return gb


class RFQWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._items: list[dict] = []
        self._build_ui()
        self.reload_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #e5e7eb; background: #f8fafc; }"
            "QTabBar::tab { background: #e2e8f0; color: #111827; padding: 8px 18px;"
            " font-weight: 600; font-size: 12px; border-top-left-radius: 6px; border-top-right-radius: 6px; }"
            "QTabBar::tab:selected { background: #ffffff; color: #2563eb; border-bottom: 2px solid #2563eb; }"
        )

        self.tab_create = QWidget()
        self.tab_compare = QWidget()

        self._build_create_tab()
        self._build_compare_tab()

        self.tabs.addTab(self.tab_create, "Create RFQ (ME41)")
        self.tabs.addTab(self.tab_compare, "Quotation Comparison (ME47)")
        layout.addWidget(self.tabs)

    # ── TAB 1: CREATE RFQ ────────────────────────────────────────────────

    def _build_create_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        title = QLabel("ME41 · Request for Quotation (RFQ)")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        sec = _section("RFQ Parameters")
        f = QFormLayout(sec)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("e.g. Annual Raw Material Supply 2026-27 *")

        self.close_date = QDateEdit()
        self.close_date.setCalendarPopup(True)
        self.close_date.setDate(date.today())

        self.target_suppliers = QLineEdit()
        self.target_suppliers.setPlaceholderText("Comma-separated supplier IDs (e.g. 1, 2, 3)")

        f.addRow("RFQ Title *:", self.title_input)
        f.addRow("Submission Deadline *:", self.close_date)
        f.addRow("Invited Suppliers:", self.target_suppliers)
        l.addWidget(sec)

        lines_sec = _section("Requested Items & Quantities")
        ll = QVBoxLayout(lines_sec)
        self.lines_table = QTableWidget(0, 3)
        self.lines_table.setHorizontalHeaderLabels(["Item *", "Quantity *", "Target Price (Optional)"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lines_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        ll.addWidget(self.lines_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Item")
        add_btn.clicked.connect(self._add_line)
        del_btn = QPushButton("Remove Item")
        del_btn.clicked.connect(self._del_line)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        ll.addLayout(btn_row)
        l.addWidget(lines_sec)

        act = QHBoxLayout()
        save_btn = QPushButton("💾 Issue RFQ & Publish to Portal")
        save_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        save_btn.clicked.connect(self._save_rfq)
        act.addWidget(save_btn)
        act.addStretch()
        l.addLayout(act)

        l.addStretch()
        scroll.setWidget(body)
        QVBoxLayout(self.tab_create).addWidget(scroll)

    def _add_line(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        combo = QComboBox()
        for itm in self._items:
            combo.addItem(f"{itm.get('item_code')} - {itm.get('item_name')}", itm.get("id"))

        qty_spin = QDoubleSpinBox()
        qty_spin.setRange(0.01, 1_000_000)
        qty_spin.setValue(1.0)

        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.0, 10_000_000)

        self.lines_table.setCellWidget(r, 0, combo)
        self.lines_table.setCellWidget(r, 1, qty_spin)
        self.lines_table.setCellWidget(r, 2, price_spin)

    def _del_line(self) -> None:
        curr = self.lines_table.currentRow()
        if curr >= 0:
            self.lines_table.removeRow(curr)

    def _save_rfq(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "Error", "RFQ Title is required.")
            return

        lines = []
        for r in range(self.lines_table.rowCount()):
            combo = self.lines_table.cellWidget(r, 0)
            q_w = self.lines_table.cellWidget(r, 1)
            p_w = self.lines_table.cellWidget(r, 2)

            item_id = combo.currentData() if isinstance(combo, QComboBox) else None
            qty = q_w.value() if isinstance(q_w, QDoubleSpinBox) else 0.0
            price = p_w.value() if isinstance(p_w, QDoubleSpinBox) else 0.0

            if item_id and qty > 0:
                lines.append({"item_id": item_id, "qty": qty, "target_price": price})

        if not lines:
            QMessageBox.warning(self, "Error", "Add at least one item to the RFQ.")
            return

        payload = {
            "title": title,
            "deadline": self.close_date.date().toString("yyyy-MM-dd"),
            "target_suppliers": self.target_suppliers.text().strip(),
            "lines": lines,
        }

        try:
            res = self.api.rfq_create(payload)
            rfq_num = res.get("data", {}).get("rfq_number") or "RFQ"
            QMessageBox.information(self, "Success", f"RFQ #{rfq_num} created and published!")
            self.title_input.clear()
            self.lines_table.setRowCount(0)
            self.reload_rfqs()
            self.tabs.setCurrentIndex(1)
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to create RFQ: {e}")

    # ── TAB 2: QUOTATION COMPARISON ──────────────────────────────────────

    def _build_compare_tab(self) -> None:
        layout = QVBoxLayout(self.tab_compare)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Select RFQ:"))
        self.rfq_picker = QComboBox()
        self.rfq_picker.currentIndexChanged.connect(self._load_matrix)
        top_bar.addWidget(self.rfq_picker)

        ref_btn = QPushButton("🔄 Refresh RFQ List")
        ref_btn.clicked.connect(self.reload_rfqs)
        top_bar.addWidget(ref_btn)

        award_btn = QPushButton("🏆 Award Order to Selected Quote (L1)")
        award_btn.setStyleSheet("background: #16a34a; color: white; font-weight: 600; padding: 6px 14px; border-radius: 4px;")
        award_btn.clicked.connect(self._award_quote)
        top_bar.addWidget(award_btn)
        top_bar.addStretch()

        layout.addLayout(top_bar)

        self.compare_table = QTableWidget(0, 6)
        self.compare_table.setHorizontalHeaderLabels(["Rank", "Supplier", "Total Quote Price", "Delivery Days", "Status", "Bid ID"])
        self.compare_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.compare_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.compare_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        layout.addWidget(self.compare_table)

    def reload_data(self) -> None:
        try:
            self._items = self.api.mm_list()
        except ApiError:
            pass
        self.reload_rfqs()

    def reload_rfqs(self) -> None:
        try:
            rfqs = self.api.rfq_list()
            if not isinstance(rfqs, list):
                rfqs = []
            self.rfq_picker.clear()
            for r in rfqs:
                if isinstance(r, dict):
                    self.rfq_picker.addItem(f"{r.get('rfq_number', '')} – {r.get('title', '')}", r.get("id"))
        except ApiError:
            pass

    def _load_matrix(self) -> None:
        rfq_id = self.rfq_picker.currentData()
        if not rfq_id:
            return
        try:
            res = self.api.rfq_compare(rfq_id)
            bids = res.get("bids", [])
            self.compare_table.setRowCount(0)
            for i, b in enumerate(bids):
                self.compare_table.insertRow(i)
                rank_str = f"L{i+1}" if i < 3 else f"#{i+1}"
                self.compare_table.setItem(i, 0, QTableWidgetItem(rank_str))
                self.compare_table.setItem(i, 1, QTableWidgetItem(str(b.get("supplier_name", ""))))
                self.compare_table.setItem(i, 2, QTableWidgetItem(f"₹{float(b.get('total_amount', 0)):,.2f}"))
                self.compare_table.setItem(i, 3, QTableWidgetItem(str(b.get("delivery_days", "-"))))
                self.compare_table.setItem(i, 4, QTableWidgetItem(str(b.get("status", "SUBMITTED"))))
                self.compare_table.setItem(i, 5, QTableWidgetItem(str(b.get("id"))))
        except ApiError:
            self.compare_table.setRowCount(0)

    def _award_quote(self) -> None:
        rfq_id = self.rfq_picker.currentData()
        curr = self.compare_table.currentRow()
        if not rfq_id or curr < 0:
            QMessageBox.information(self, "Select", "Please select a quotation row from the matrix to award.")
            return

        bid_id_item = self.compare_table.item(curr, 5)
        sup_item = self.compare_table.item(curr, 1)
        if not bid_id_item:
            return
        bid_id = int(bid_id_item.text())
        sup_name = sup_item.text() if sup_item else ""

        confirm = QMessageBox.question(self, "Award Order", f"Award contract to {sup_name} for this RFQ?")
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.api.rfq_award(rfq_id, {"bid_id": bid_id})
                QMessageBox.information(self, "Awarded", f"Successfully awarded RFQ to {sup_name}!")
                self._load_matrix()
            except ApiError as e:
                QMessageBox.critical(self, "Error", f"Failed to award quote: {e}")
