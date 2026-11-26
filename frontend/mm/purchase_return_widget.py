"""
Brain ERP – Purchase Return Widget (PRTN).
Return goods to vendor against GRN with automatic inventory reversal
and debit note generation.
"""

from __future__ import annotations

from datetime import date
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QFrame,
    QScrollArea, QMessageBox, QDateEdit, QCheckBox
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


class PurchaseReturnWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._build_ui()
        self.reload_meta()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        title = QLabel("PRTN · Purchase Return to Vendor")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        sec = _section("Return Document Header")
        f = QFormLayout(sec)

        self.grn_combo = QComboBox()
        self.grn_combo.currentIndexChanged.connect(self._on_grn_selected)

        self.ret_date = QDateEdit()
        self.ret_date.setCalendarPopup(True)
        self.ret_date.setDate(date.today())

        self.reason_combo = QComboBox()
        self.make_debit_note = QCheckBox("Automatically create Supplier Debit Note in GL")
        self.make_debit_note.setChecked(True)
        self.make_debit_note.setStyleSheet("color: #111827; font-weight: 600;")

        self.remarks = QLineEdit()
        self.remarks.setPlaceholderText("Detailed return justification")

        f.addRow("Select Goods Receipt (GRN) *:", self.grn_combo)
        f.addRow("Return Date *:", self.ret_date)
        f.addRow("Return Reason *:", self.reason_combo)
        f.addRow("Debit Note:", self.make_debit_note)
        f.addRow("Remarks:", self.remarks)
        l.addWidget(sec)

        lines_sec = _section("Goods Receipt Lines for Return")
        ll = QVBoxLayout(lines_sec)
        self.lines_table = QTableWidget(0, 5)
        self.lines_table.setHorizontalHeaderLabels(["Item", "Received Qty", "Return Qty *", "Unit Price", "Tax Rate %"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lines_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        ll.addWidget(self.lines_table)
        l.addWidget(lines_sec)

        act = QHBoxLayout()
        post_btn = QPushButton("✓ Post Purchase Return & Reversal")
        post_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        post_btn.clicked.connect(self._post_return)
        act.addWidget(post_btn)
        act.addStretch()
        l.addLayout(act)

        l.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)

    def reload_meta(self) -> None:
        try:
            reasons = self.api.purchase_return_reasons()
            self.reason_combo.clear()
            for code, desc in reasons.items():
                self.reason_combo.addItem(f"{desc} ({code})", code)

            grns = self.api.purchase_return_open_grns()
            if not isinstance(grns, list):
                grns = []
            self.grn_combo.clear()
            self.grn_combo.addItem("-- Select Open GRN --", None)
            for g in grns:
                if isinstance(g, dict):
                    self.grn_combo.addItem(f"{g.get('grn_number', '')} – {g.get('supplier_name', '')} (PO #{g.get('po_id', '')})", g.get("id"))
        except ApiError:
            pass

    def _on_grn_selected(self) -> None:
        grn_id = self.grn_combo.currentData()
        if not grn_id:
            self.lines_table.setRowCount(0)
            return
        try:
            doc = self.api.purchase_return_grn(grn_id)
            lines = doc.get("lines", [])
            self.lines_table.setRowCount(0)
            for r, ln in enumerate(lines):
                self.lines_table.insertRow(r)

                itm_item = QTableWidgetItem(f"{ln.get('item_code')} - {ln.get('item_name')}")
                itm_item.setData(Qt.ItemDataRole.UserRole, ln.get("id"))  # grn line id
                itm_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                rec_item = QTableWidgetItem(str(ln.get("qty_received", ln.get("qty", 0))))
                rec_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                spin = QDoubleSpinBox()
                spin.setRange(0.0, float(ln.get("qty_received", ln.get("qty", 0))))
                spin.setValue(0.0)

                price_item = QTableWidgetItem(str(ln.get("unit_price", 0)))
                price_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                tax_item = QTableWidgetItem(str(ln.get("gst_rate", 18)))
                tax_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                self.lines_table.setItem(r, 0, itm_item)
                self.lines_table.setItem(r, 1, rec_item)
                self.lines_table.setCellWidget(r, 2, spin)
                self.lines_table.setItem(r, 3, price_item)
                self.lines_table.setItem(r, 4, tax_item)
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to load GRN lines: {e}")

    def _post_return(self) -> None:
        grn_id = self.grn_combo.currentData()
        if not grn_id:
            QMessageBox.warning(self, "Error", "Please select a GRN.")
            return

        lines = []
        for r in range(self.lines_table.rowCount()):
            itm_item = self.lines_table.item(r, 0)
            spin = self.lines_table.cellWidget(r, 2)
            grn_line_id = itm_item.data(Qt.ItemDataRole.UserRole) if itm_item else None
            ret_qty = spin.value() if isinstance(spin, QDoubleSpinBox) else 0.0

            if grn_line_id and ret_qty > 0:
                lines.append({"grn_line_id": grn_line_id, "return_qty": ret_qty})

        if not lines:
            QMessageBox.warning(self, "Error", "Enter return quantity for at least one item line.")
            return

        payload = {
            "grn_id": grn_id,
            "return_date": self.ret_date.date().toString("yyyy-MM-dd"),
            "reason": self.reason_combo.currentData(),
            "make_debit_note": self.make_debit_note.isChecked(),
            "remarks": self.remarks.text().strip(),
            "lines": lines,
        }

        try:
            res = self.api.purchase_return_create(payload)
            ret_num = res.get("data", {}).get("return_number") or "PRTN"
            QMessageBox.information(self, "Success", f"Purchase Return #{ret_num} posted successfully!")
            self.lines_table.setRowCount(0)
            self.reload_meta()
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to post purchase return: {e}")
