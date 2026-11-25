"""
Brain ERP – Supplier Invoicing & Stock Transfers Widget (MR01 / LT01).
Tabs:
  1. Supplier Invoice Verification (MR01 / MIRO)
  2. Stock Transfer Orders (LT01)
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


class SupplierInvoiceWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._meta: dict = {}
        self._build_ui()
        self.reload_meta()

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

        self.tab_mr01 = QWidget()
        self.tab_lt01 = QWidget()

        self._build_mr01_tab()
        self._build_lt01_tab()

        self.tabs.addTab(self.tab_mr01, "Invoice Verification (MR01)")
        self.tabs.addTab(self.tab_lt01, "Stock Transfer (LT01)")
        layout.addWidget(self.tabs)

    # ── TAB 1: MR01 INVOICE VERIFICATION ─────────────────────────────────

    def _build_mr01_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        title = QLabel("MR01 · Supplier Invoice Verification (MIRO)")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        sec = _section("Invoice & PO Details")
        f = QFormLayout(sec)

        self.inv_num = QLineEdit()
        self.inv_num.setPlaceholderText("Vendor Invoice / Bill Number *")

        self.inv_date = QDateEdit()
        self.inv_date.setCalendarPopup(True)
        self.inv_date.setDate(date.today())

        self.sup_combo = QComboBox()

        po_box = QHBoxLayout()
        self.po_input = QLineEdit()
        self.po_input.setPlaceholderText("PO ID (e.g. 1)")
        check_btn = QPushButton("Check 3-Way Match")
        check_btn.setStyleSheet("background: #2563eb; color: white; padding: 4px 10px; border-radius: 4px;")
        check_btn.clicked.connect(self._check_3way_match)
        po_box.addWidget(self.po_input)
        po_box.addWidget(check_btn)

        self.base_amt = QDoubleSpinBox()
        self.base_amt.setRange(0.01, 100_000_000)
        self.base_amt.valueChanged.connect(self._recalc_tax)

        self.gst_rate = QComboBox()
        self.gst_rate.addItems(["18%", "12%", "5%", "28%", "0%"])
        self.gst_rate.currentTextChanged.connect(self._recalc_tax)

        self.total_display = QLabel("<b>Tax:</b> ₹0.00 | <b>Gross Total:</b> ₹0.00")
        self.total_display.setStyleSheet("font-size: 13px; color: #111827; font-weight: 600;")

        self.tds_section = QComboBox()
        self.tds_section.addItem("None", "")
        self.tds_section.addItem("194C – Contractors (1% / 2%)", "194C")
        self.tds_section.addItem("194J – Professional / Technical (10%)", "194J")
        self.tds_section.addItem("194Q – Purchase of Goods (0.1%)", "194Q")

        f.addRow("Invoice Number *:", self.inv_num)
        f.addRow("Invoice Date *:", self.inv_date)
        f.addRow("Supplier *:", self.sup_combo)
        f.addRow("Purchase Order ID *:", po_box)
        f.addRow("Taxable Base Amount *:", self.base_amt)
        f.addRow("GST Rate:", self.gst_rate)
        f.addRow("Invoice Total:", self.total_display)
        f.addRow("TDS Section:", self.tds_section)
        l.addWidget(sec)

        self.match_msg = QLabel("")
        self.match_msg.setStyleSheet("font-size: 12px; font-weight: 600; padding: 6px;")
        l.addWidget(self.match_msg)

        act_box = QHBoxLayout()
        self.post_inv_btn = QPushButton("✓ Post Supplier Invoice & GL Entries")
        self.post_inv_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        self.post_inv_btn.clicked.connect(self._post_invoice)
        act_box.addWidget(self.post_inv_btn)
        act_box.addStretch()
        l.addLayout(act_box)

        l.addStretch()
        scroll.setWidget(body)
        QVBoxLayout(self.tab_mr01).addWidget(scroll)

    def _recalc_tax(self) -> None:
        base = self.base_amt.value()
        rate_str = self.gst_rate.currentText().replace("%", "")
        rate = float(rate_str) if rate_str.isdigit() else 0.0
        tax = round(base * rate / 100.0, 2)
        gross = base + tax
        self.total_display.setText(f"<b>Tax:</b> ₹{tax:,.2f} | <b>Gross Total:</b> ₹{gross:,.2f}")

    def _check_3way_match(self) -> None:
        po_str = self.po_input.text().strip()
        if not po_str.isdigit():
            QMessageBox.warning(self, "Error", "Enter a valid numeric Purchase Order ID.")
            return
        po_id = int(po_str)
        try:
            meta = self.api.mr01_meta(po_id=po_id)
            match_data = meta.get("match", {})
            if match_data:
                expected = match_data.get("expected", 0)
                variance = match_data.get("variance", 0)
                blocked = match_data.get("blocked", False)
                msg = match_data.get("message", "")
                if blocked:
                    self.match_msg.setText(f"⚠ MATCH WARNING: {msg}")
                    self.match_msg.setStyleSheet("color: #dc2626; background: #fee2e2; border-radius: 4px;")
                else:
                    self.match_msg.setText(f"✓ Match verified! Expected GRN balance: ₹{expected:,.2f}")
                    self.match_msg.setStyleSheet("color: #16a34a; background: #dcfce7; border-radius: 4px;")
            else:
                self.match_msg.setText("✓ PO loaded.")
                self.match_msg.setStyleSheet("color: #2563eb;")
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to check PO: {e}")

    def _post_invoice(self) -> None:
        num = self.inv_num.text().strip()
        sup_id = self.sup_combo.currentData()
        po_str = self.po_input.text().strip()
        base = self.base_amt.value()

        if not num or not sup_id or not po_str.isdigit() or base <= 0:
            QMessageBox.warning(self, "Error", "Please fill in Invoice Number, Supplier, PO ID, and Base Amount.")
            return

        rate_str = self.gst_rate.currentText().replace("%", "")
        rate = float(rate_str) if rate_str.isdigit() else 0.0
        tax = round(base * rate / 100.0, 2)

        payload = {
            "invoice_number": num,
            "invoice_date": self.inv_date.date().toString("yyyy-MM-dd"),
            "supplier_id": sup_id,
            "po_id": int(po_str),
            "base_amount": base,
            "cgst_amount": round(tax / 2, 2),
            "sgst_amount": round(tax / 2, 2),
            "igst_amount": 0.0,
            "tds_section": self.tds_section.currentData() or None,
        }

        try:
            res = self.api.mr01_post(payload)
            inv_id = res.get("data", {}).get("invoice_id") or "Invoice"
            QMessageBox.information(self, "Success", f"Supplier Invoice #{num} posted successfully!\nID: {inv_id}")
            self.inv_num.clear()
            self.base_amt.setValue(0.0)
            self.po_input.clear()
            self.match_msg.setText("")
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to post invoice: {e}")

    # ── TAB 2: LT01 STOCK TRANSFER ────────────────────────────────────────

    def _build_lt01_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        title = QLabel("LT01 · Stock Transfer Order (Plant / Warehouse)")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        sec = _section("Transfer Details")
        f = QFormLayout(sec)

        self.from_wh = QComboBox()
        self.to_wh = QComboBox()
        self.transfer_item = QComboBox()

        self.transfer_qty = QDoubleSpinBox()
        self.transfer_qty.setRange(0.01, 1_000_000)
        self.transfer_qty.setValue(1.0)

        self.transfer_notes = QLineEdit()
        self.transfer_notes.setPlaceholderText("Optional remarks")

        f.addRow("From Warehouse *:", self.from_wh)
        f.addRow("To Warehouse *:", self.to_wh)
        f.addRow("Material Item *:", self.transfer_item)
        f.addRow("Transfer Quantity *:", self.transfer_qty)
        f.addRow("Remarks:", self.transfer_notes)
        l.addWidget(sec)

        act_box = QHBoxLayout()
        transfer_btn = QPushButton("⇄ Post Stock Transfer Movement")
        transfer_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        transfer_btn.clicked.connect(self._post_transfer)
        act_box.addWidget(transfer_btn)
        act_box.addStretch()
        l.addLayout(act_box)

        l.addStretch()
        scroll.setWidget(body)
        QVBoxLayout(self.tab_lt01).addWidget(scroll)

    def _post_transfer(self) -> None:
        src = self.from_wh.currentData()
        dst = self.to_wh.currentData()
        item_id = self.transfer_item.currentData()
        qty = self.transfer_qty.value()

        if src == dst:
            QMessageBox.warning(self, "Error", "Source and destination warehouses cannot be the same.")
            return

        payload = {
            "from_warehouse_id": src,
            "to_warehouse_id": dst,
            "item_id": item_id,
            "qty": qty,
            "notes": self.transfer_notes.text().strip(),
        }

        try:
            res = self.api.lt01_post(payload)
            doc = res.get("data", {}).get("transfer_number") or "Transfer"
            QMessageBox.information(self, "Success", f"Stock Transfer #{doc} posted successfully!")
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to post transfer: {e}")

    def reload_meta(self) -> None:
        try:
            self._meta = self.api.mr01_meta()
            suppliers = self._meta.get("suppliers", [])
            self.sup_combo.clear()
            for s in suppliers:
                self.sup_combo.addItem(f"{s.get('name')} ({s.get('code', '')})", s.get("id"))

            wh_meta = self.api.lt01_meta()
            warehouses = wh_meta.get("warehouses", [])
            self.from_wh.clear()
            self.to_wh.clear()
            for w in warehouses:
                self.from_wh.addItem(w.get("name"), w.get("id"))
                self.to_wh.addItem(w.get("name"), w.get("id"))

            items = self.api.mm_list()
            self.transfer_item.clear()
            for itm in items:
                self.transfer_item.addItem(f"{itm.get('item_code')} - {itm.get('item_name')}", itm.get("id"))
        except ApiError:
            pass
