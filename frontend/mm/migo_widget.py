"""
Brain ERP – Goods Movement Widget (MIGO).
Supports:
  - 101 Goods Receipt against Purchase Order
  - 122 Return to Vendor
  - 261 Issue to Work Order
  - 201 Issue to Cost Center
  - 501 Initial Stock Receipt
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


class MIGOWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._meta: dict = {}
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

        # Title
        title_box = QHBoxLayout()
        title = QLabel("MIGO · Goods Movement")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        title_box.addWidget(title)
        title_box.addStretch()
        l.addLayout(title_box)

        # Header section
        hdr_sec = _section("Movement Parameters")
        h_layout = QFormLayout(hdr_sec)

        self.movement_type = QComboBox()
        self.movement_type.addItem("101 – Goods Receipt against Purchase Order", "101")
        self.movement_type.addItem("122 – Return to Vendor from Warehouse", "122")
        self.movement_type.addItem("261 – Goods Issue for Work Order", "261")
        self.movement_type.addItem("201 – Goods Issue for Cost Center", "201")
        self.movement_type.addItem("501 – Receipt without PO (Initial Stock Entry)", "501")
        self.movement_type.currentIndexChanged.connect(self._on_movement_changed)

        self.doc_date = QDateEdit()
        self.doc_date.setCalendarPopup(True)
        self.doc_date.setDate(date.today())

        self.wh_combo = QComboBox()

        # PO search row for movement 101
        self.po_search_box = QHBoxLayout()
        self.po_id_input = QLineEdit()
        self.po_id_input.setPlaceholderText("Enter Purchase Order ID (e.g. 1)")
        load_po_btn = QPushButton("Fetch PO Lines")
        load_po_btn.setStyleSheet("background: #2563eb; color: white; padding: 4px 10px; border-radius: 4px;")
        load_po_btn.clicked.connect(self._fetch_po_lines)
        self.po_search_box.addWidget(self.po_id_input)
        self.po_search_box.addWidget(load_po_btn)

        self.vendor_bill_no = QLineEdit()
        self.vendor_bill_no.setPlaceholderText("Supplier Delivery Challan / Invoice #")

        h_layout.addRow("Movement Type *:", self.movement_type)
        h_layout.addRow("Posting Date *:", self.doc_date)
        h_layout.addRow("Destination Warehouse *:", self.wh_combo)
        h_layout.addRow("Purchase Order (for 101):", self.po_search_box)
        h_layout.addRow("Delivery Challan / Bill No:", self.vendor_bill_no)

        l.addWidget(hdr_sec)

        # Lines section
        lines_sec = _section("Goods Receipt / Movement Lines")
        lines_layout = QVBoxLayout(lines_sec)

        self.lines_table = QTableWidget(0, 6)
        self.lines_table.setHorizontalHeaderLabels(["Item", "Open / PO Qty", "Receiving Qty *", "Unit Price", "Storage Bin", "Batch / Serial"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lines_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        lines_layout.addWidget(self.lines_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Manual Item Line")
        add_btn.clicked.connect(self._add_manual_line)
        del_btn = QPushButton("Remove Selected Line")
        del_btn.clicked.connect(self._del_line)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        lines_layout.addLayout(btn_row)

        l.addWidget(lines_sec)

        # Action bar
        act_box = QHBoxLayout()
        self.post_btn = QPushButton("✓ Post Goods Movement Document")
        self.post_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        self.post_btn.clicked.connect(self._post_movement)
        self.status_lbl = QLabel("")
        act_box.addWidget(self.post_btn)
        act_box.addWidget(self.status_lbl)
        act_box.addStretch()
        l.addLayout(act_box)

        l.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)

    def reload_meta(self) -> None:
        try:
            self._meta = self.api.migo_meta()
            warehouses = self._meta.get("warehouses", [])
            self.wh_combo.clear()
            for w in warehouses:
                self.wh_combo.addItem(f"{w.get('name')} ({w.get('code')})", w.get("id"))
        except ApiError:
            pass

    def _on_movement_changed(self) -> None:
        mtype = self.movement_type.currentData()
        self.po_id_input.setEnabled(mtype == "101")

    def _fetch_po_lines(self) -> None:
        po_id_str = self.po_id_input.text().strip()
        if not po_id_str.isdigit():
            QMessageBox.warning(self, "Invalid PO", "Please enter a valid numeric Purchase Order ID.")
            return
        po_id = int(po_id_str)
        try:
            data = self.api.migo_po_lines(po_id)
            lines = data.get("lines", [])
            if not lines:
                QMessageBox.information(self, "PO Found", "No open/unreceived lines found for this PO.")
                return

            self.lines_table.setRowCount(0)
            for r, ln in enumerate(lines):
                self.lines_table.insertRow(r)

                item_label = QTableWidgetItem(f"{ln.get('item_code')} – {ln.get('item_name')}")
                item_label.setData(Qt.ItemDataRole.UserRole, ln.get("item_id"))
                item_label.setFlags(Qt.ItemFlag.ItemIsEnabled)

                open_qty = float(ln.get("qty_open", ln.get("qty", 0)))
                open_item = QTableWidgetItem(f"{open_qty:.2f} {ln.get('uom_code', '')}")
                open_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                rec_spin = QDoubleSpinBox()
                rec_spin.setRange(0.01, 1_000_000)
                rec_spin.setValue(open_qty)

                price_item = QTableWidgetItem(f"{float(ln.get('unit_price', 0)):.2f}")
                price_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                bin_input = QLineEdit()
                bin_input.setPlaceholderText("Bin/Rack")

                batch_input = QLineEdit()
                batch_input.setPlaceholderText("Lot #")

                self.lines_table.setItem(r, 0, item_label)
                self.lines_table.setItem(r, 1, open_item)
                self.lines_table.setCellWidget(r, 2, rec_spin)
                self.lines_table.setItem(r, 3, price_item)
                self.lines_table.setCellWidget(r, 4, bin_input)
                self.lines_table.setCellWidget(r, 5, batch_input)

            self.status_lbl.setText(f"✓ Loaded {len(lines)} line(s) from PO #{po_id}")
            self.status_lbl.setStyleSheet("color: #16a34a; font-weight: 600;")
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch PO lines: {e}")

    def _add_manual_line(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        item_combo = QComboBox()
        try:
            items = self.api.mm_list()
            for itm in items:
                item_combo.addItem(f"{itm.get('item_code')} - {itm.get('item_name')}", itm.get("id"))
        except Exception as e:
            item_combo.addItem("(failed to load items)")
            print(f"[migo_widget] mm_list error: {e}")

        rec_spin = QDoubleSpinBox()
        rec_spin.setRange(0.01, 1_000_000)
        rec_spin.setValue(1.0)

        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.0, 10_000_000)

        bin_input = QLineEdit()
        batch_input = QLineEdit()

        self.lines_table.setCellWidget(r, 0, item_combo)
        self.lines_table.setItem(r, 1, QTableWidgetItem("-"))
        self.lines_table.setCellWidget(r, 2, rec_spin)
        self.lines_table.setCellWidget(r, 3, price_spin)
        self.lines_table.setCellWidget(r, 4, bin_input)
        self.lines_table.setCellWidget(r, 5, batch_input)

    def _del_line(self) -> None:
        curr = self.lines_table.currentRow()
        if curr >= 0:
            self.lines_table.removeRow(curr)

    def _post_movement(self) -> None:
        mtype = self.movement_type.currentData()
        wh_id = self.wh_combo.currentData()
        if not wh_id:
            QMessageBox.warning(self, "Error", "Please select a warehouse.")
            return

        lines = []
        for r in range(self.lines_table.rowCount()):
            item_widget = self.lines_table.cellWidget(r, 0)
            item_id = None
            if isinstance(item_widget, QComboBox):
                item_id = item_widget.currentData()
            else:
                itm = self.lines_table.item(r, 0)
                item_id = itm.data(Qt.ItemDataRole.UserRole) if itm else None

            qty_widget = self.lines_table.cellWidget(r, 2)
            qty = qty_widget.value() if isinstance(qty_widget, QDoubleSpinBox) else 0.0

            bin_widget = self.lines_table.cellWidget(r, 4)
            bin_str = bin_widget.text().strip() if isinstance(bin_widget, QLineEdit) else ""

            batch_widget = self.lines_table.cellWidget(r, 5)
            batch_str = batch_widget.text().strip() if isinstance(batch_widget, QLineEdit) else ""

            if item_id and qty > 0:
                lines.append({
                    "item_id": item_id,
                    "qty": qty,
                    "storage_bin": bin_str,
                    "batch_number": batch_str,
                })

        if not lines:
            QMessageBox.warning(self, "Error", "No valid item lines to receive.")
            return

        payload = {
            "movement_type": mtype,
            "warehouse_id": wh_id,
            "posting_date": self.doc_date.date().toString("yyyy-MM-dd"),
            "po_id": int(self.po_id_input.text().strip()) if (mtype == "101" and self.po_id_input.text().strip().isdigit()) else None,
            "challan_number": self.vendor_bill_no.text().strip(),
            "lines": lines,
        }

        try:
            res = self.api.migo_post(payload)
            doc_no = res.get("data", {}).get("grn_number") or res.get("data", {}).get("doc_number") or "Document"
            QMessageBox.information(self, "Success", f"Goods Movement posted successfully!\nDocument Number: {doc_no}")
            self.status_lbl.setText(f"✓ Posted document {doc_no}")
            self.status_lbl.setStyleSheet("color: #16a34a; font-weight: 700;")
            self.lines_table.setRowCount(0)
            self.po_id_input.clear()
            self.vendor_bill_no.clear()
        except ApiError as e:
            QMessageBox.critical(self, "Posting Error", f"Failed to post Goods Movement:\n{e}")