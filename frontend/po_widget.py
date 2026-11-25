"""
Brain ERP – Purchase Orders Widget (ME21N / ME23N).
Tabs:
  1. Create Purchase Order (ME21N)
  2. Purchase Orders Directory & Release (ME23N)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QFrame,
    QScrollArea, QMessageBox, QDateEdit
)

from .api_client import ApiClient, ApiError


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


class POWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._lookups: dict = {}
        self._items_lookup: list[dict] = []
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
        self.tab_list = QWidget()

        self._build_create_tab()
        self._build_list_tab()

        self.tabs.addTab(self.tab_create, "Create PO (ME21N)")
        self.tabs.addTab(self.tab_list, "Purchase Orders List (ME23N)")

        layout.addWidget(self.tabs)

    # ── TAB 1: CREATE PO ──────────────────────────────────────────────────

    def _build_create_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header section
        header_sec = _section("PO Header Details")
        h_layout = QFormLayout(header_sec)
        h_layout.setSpacing(10)

        self.sup_combo = QComboBox()
        self.delivery_date = QDateEdit()
        self.delivery_date.setCalendarPopup(True)
        self.delivery_date.setDate(date.today())

        self.payment_terms = QLineEdit()
        self.payment_terms.setPlaceholderText("e.g. Net 30 Days")

        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional instructions for supplier")

        h_layout.addRow("Supplier *:", self.sup_combo)
        h_layout.addRow("Delivery Date *:", self.delivery_date)
        h_layout.addRow("Payment Terms:", self.payment_terms)
        h_layout.addRow("PO Notes:", self.notes_input)
        layout.addWidget(header_sec)

        # Line items section
        lines_sec = _section("Line Items")
        l_layout = QVBoxLayout(lines_sec)

        self.lines_table = QTableWidget(0, 5)
        self.lines_table.setHorizontalHeaderLabels(["Item *", "Quantity *", "Unit Price *", "GST Rate %", "Total"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lines_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        l_layout.addWidget(self.lines_table)

        btn_row = QHBoxLayout()
        add_line_btn = QPushButton("+ Add Item Line")
        add_line_btn.setStyleSheet("background: #2563eb; color: white; padding: 6px 12px; font-weight: 600; border-radius: 4px;")
        add_line_btn.clicked.connect(self._add_line)

        del_line_btn = QPushButton("Remove Selected Line")
        del_line_btn.setStyleSheet("background: #ef4444; color: white; padding: 6px 12px; font-weight: 600; border-radius: 4px;")
        del_line_btn.clicked.connect(self._del_line)

        btn_row.addWidget(add_line_btn)
        btn_row.addWidget(del_line_btn)
        btn_row.addStretch()

        self.totals_label = QLabel("<b>Net:</b> 0.00 | <b>Tax:</b> 0.00 | <b>Gross Total:</b> ₹0.00")
        self.totals_label.setStyleSheet("font-size: 13px; color: #111827; padding-right: 10px;")
        btn_row.addWidget(self.totals_label)

        l_layout.addLayout(btn_row)
        layout.addWidget(lines_sec)

        # Save action
        action_box = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save & Submit Purchase Order")
        self.save_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        self.save_btn.clicked.connect(self._save_po)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        action_box.addWidget(self.save_btn)
        action_box.addWidget(self.status_lbl)
        action_box.addStretch()

        layout.addLayout(action_box)
        layout.addStretch()

        scroll.setWidget(body)
        l = QVBoxLayout(self.tab_create)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(scroll)

    def _add_line(self) -> None:
        row = self.lines_table.rowCount()
        self.lines_table.insertRow(row)

        item_combo = QComboBox()
        for itm in self._items_lookup:
            code = itm.get("item_code", "")
            name = itm.get("item_name", "")
            item_combo.addItem(f"{code} – {name}", itm.get("id"))

        qty_spin = QDoubleSpinBox()
        qty_spin.setRange(0.01, 1_000_000)
        qty_spin.setValue(1.0)
        qty_spin.valueChanged.connect(self._calc_totals)

        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.0, 10_000_000)
        price_spin.setValue(0.0)
        price_spin.valueChanged.connect(self._calc_totals)

        tax_spin = QDoubleSpinBox()
        tax_spin.setRange(0.0, 100.0)
        tax_spin.setValue(18.0)
        tax_spin.valueChanged.connect(self._calc_totals)

        total_item = QTableWidgetItem("0.00")
        total_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

        self.lines_table.setCellWidget(row, 0, item_combo)
        self.lines_table.setCellWidget(row, 1, qty_spin)
        self.lines_table.setCellWidget(row, 2, price_spin)
        self.lines_table.setCellWidget(row, 3, tax_spin)
        self.lines_table.setItem(row, 4, total_item)

        self._calc_totals()

    def _del_line(self) -> None:
        curr = self.lines_table.currentRow()
        if curr >= 0:
            self.lines_table.removeRow(curr)
            self._calc_totals()

    def _calc_totals(self) -> None:
        net = 0.0
        tax = 0.0
        for r in range(self.lines_table.rowCount()):
            qty_widget = self.lines_table.cellWidget(r, 1)
            price_widget = self.lines_table.cellWidget(r, 2)
            tax_widget = self.lines_table.cellWidget(r, 3)

            qty = qty_widget.value() if isinstance(qty_widget, QDoubleSpinBox) else 0.0
            price = price_widget.value() if isinstance(price_widget, QDoubleSpinBox) else 0.0
            rate = tax_widget.value() if isinstance(tax_widget, QDoubleSpinBox) else 0.0

            line_base = qty * price
            line_tax = line_base * rate / 100.0
            net += line_base
            tax += line_tax

            tot_item = self.lines_table.item(r, 4)
            if tot_item:
                tot_item.setText(f"{line_base + line_tax:,.2f}")

        gross = net + tax
        self.totals_label.setText(f"<b>Net:</b> ₹{net:,.2f} | <b>Tax:</b> ₹{tax:,.2f} | <b>Gross Total:</b> ₹{gross:,.2f}")

    def _save_po(self) -> None:
        sup_id = self.sup_combo.currentData()
        if not sup_id:
            QMessageBox.warning(self, "Validation Error", "Please select a supplier.")
            return

        lines = []
        for r in range(self.lines_table.rowCount()):
            item_combo = self.lines_table.cellWidget(r, 0)
            qty_spin = self.lines_table.cellWidget(r, 1)
            price_spin = self.lines_table.cellWidget(r, 2)
            tax_spin = self.lines_table.cellWidget(r, 3)

            item_id = item_combo.currentData() if isinstance(item_combo, QComboBox) else None
            qty = qty_spin.value() if isinstance(qty_spin, QDoubleSpinBox) else 0.0
            price = price_spin.value() if isinstance(price_spin, QDoubleSpinBox) else 0.0
            rate = tax_spin.value() if isinstance(tax_spin, QDoubleSpinBox) else 0.0

            if item_id and qty > 0:
                lines.append({
                    "item_id": item_id,
                    "qty": qty,
                    "unit_price": price,
                    "gst_rate": rate
                })

        if not lines:
            QMessageBox.warning(self, "Validation Error", "Please add at least one line item.")
            return

        payload = {
            "supplier_id": sup_id,
            "delivery_date": self.delivery_date.date().toString("yyyy-MM-dd"),
            "payment_terms": self.payment_terms.text().strip(),
            "notes": self.notes_input.text().strip(),
            "lines": lines,
        }

        try:
            res = self.api.po_create(payload)
            po_num = res.get("data", {}).get("po_number") or "PO"
            self.status_lbl.setText(f"✓ Created {po_num} successfully!")
            self.status_lbl.setStyleSheet("color: #16a34a;")
            self.lines_table.setRowCount(0)
            self._calc_totals()
            self.reload_list()
            self.tabs.setCurrentIndex(1)
        except ApiError as e:
            self.status_lbl.setText(f"✗ Error: {e}")
            self.status_lbl.setStyleSheet("color: #dc2626;")

    # ── TAB 2: LIST POs ───────────────────────────────────────────────────

    def _build_list_tab(self) -> None:
        layout = QVBoxLayout(self.tab_list)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(["ALL", "DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "COMPLETED"])
        self.status_filter.currentTextChanged.connect(self.reload_list)
        filter_bar.addWidget(self.status_filter)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search PO number, supplier...")
        self.search_input.textChanged.connect(self.reload_list)
        filter_bar.addWidget(self.search_input)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.reload_list)
        filter_bar.addWidget(refresh_btn)

        self.release_btn = QPushButton("✓ Release / Approve PO")
        self.release_btn.setStyleSheet("background: #2563eb; color: white; font-weight: 600; padding: 6px 14px; border-radius: 4px;")
        self.release_btn.clicked.connect(self._release_selected_po)
        filter_bar.addWidget(self.release_btn)

        layout.addLayout(filter_bar)

        self.po_table = QTableWidget(0, 7)
        self.po_table.setHorizontalHeaderLabels(["ID", "PO Number", "Date", "Supplier", "Gross Total", "Status", "Items"])
        self.po_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.po_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.po_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        layout.addWidget(self.po_table)

    def reload_data(self) -> None:
        try:
            self._lookups = self.api.po_lookups()
            suppliers = self._lookups.get("suppliers", [])
            self.sup_combo.clear()
            for s in suppliers:
                self.sup_combo.addItem(f"{s.get('name')} ({s.get('code', '')})", s.get("id"))

            self._items_lookup = self.api.mm_list()
        except ApiError:
            pass
        self.reload_list()

    def reload_list(self) -> None:
        st = self.status_filter.currentText()
        search = self.search_input.text().strip()
        status_param = "" if st == "ALL" else st
        try:
            pos = self.api.po_list(status=status_param, search=search)
            if not isinstance(pos, list):
                pos = []
            self.po_table.setRowCount(0)
            for r, po in enumerate(pos):
                if not isinstance(po, dict):
                    continue
                self.po_table.insertRow(self.po_table.rowCount())
                row_idx = self.po_table.rowCount() - 1
                self.po_table.setItem(row_idx, 0, QTableWidgetItem(str(po.get("id", ""))))
                self.po_table.setItem(row_idx, 1, QTableWidgetItem(str(po.get("po_number", ""))))
                self.po_table.setItem(row_idx, 2, QTableWidgetItem(str(po.get("po_date", ""))))
                self.po_table.setItem(row_idx, 3, QTableWidgetItem(str(po.get("supplier_name", ""))))
                val = po.get("grand_total") or po.get("total_amount") or 0
                self.po_table.setItem(row_idx, 4, QTableWidgetItem(f"₹{float(val):,.2f}"))
                self.po_table.setItem(row_idx, 5, QTableWidgetItem(str(po.get("status", ""))))
                self.po_table.setItem(row_idx, 6, QTableWidgetItem(str(po.get("line_count", ""))))
        except ApiError:
            pass

    def _release_selected_po(self) -> None:
        curr = self.po_table.currentRow()
        if curr < 0:
            QMessageBox.information(self, "Select PO", "Please select a purchase order from the table to release.")
            return
        po_id_item = self.po_table.item(curr, 0)
        po_num_item = self.po_table.item(curr, 1)
        if not po_id_item:
            return
        po_id = int(po_id_item.text())
        po_num = po_num_item.text() if po_num_item else f"#{po_id}"

        confirm = QMessageBox.question(self, "Confirm Release", f"Approve and release Purchase Order {po_num}?")
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.api.po_release(po_id)
                QMessageBox.information(self, "Success", f"Purchase Order {po_num} released successfully!")
                self.reload_list()
            except ApiError as e:
                QMessageBox.critical(self, "Error", f"Failed to release PO: {e}")
