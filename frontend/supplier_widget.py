"""
Brain ERP – Supplier Master Widget (XK01 / XK02).
Tabs:
  1. Create Supplier (XK01)
  2. Supplier Directory (XK02)
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QFrame, QScrollArea, QMessageBox
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
        "QLineEdit { color: #111827; background: #ffffff; }"
    )
    return gb


class SupplierWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._build_ui()
        self.reload_list()

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

        self.tabs.addTab(self.tab_create, "Create Supplier (XK01)")
        self.tabs.addTab(self.tab_list, "Suppliers Directory (XK02)")
        layout.addWidget(self.tabs)

    # ── TAB 1: CREATE SUPPLIER ───────────────────────────────────────────

    def _build_create_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        title = QLabel("XK01 · Create Supplier Master")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        sec_gen = _section("General & Tax Details")
        f_gen = QFormLayout(sec_gen)

        self.sup_code = QLineEdit()
        self.sup_code.setPlaceholderText("Leave blank to auto-number (SUP000001)")
        self.sup_name = QLineEdit()
        self.sup_name.setPlaceholderText("Legal Entity / Trade Name *")

        self.gstin = QLineEdit()
        self.gstin.setPlaceholderText("15-digit GSTIN (e.g. 29ABCDE1234F1Z5)")
        self.pan = QLineEdit()
        self.pan.setPlaceholderText("10-character PAN")

        f_gen.addRow("Supplier Code:", self.sup_code)
        f_gen.addRow("Supplier Name *:", self.sup_name)
        f_gen.addRow("GSTIN:", self.gstin)
        f_gen.addRow("PAN:", self.pan)
        l.addWidget(sec_gen)

        sec_con = _section("Contact & Banking Information")
        f_con = QFormLayout(sec_con)

        self.email = QLineEdit()
        self.phone = QLineEdit()
        self.address = QLineEdit()
        self.city = QLineEdit()
        self.state = QLineEdit()

        self.bank_name = QLineEdit()
        self.bank_acct = QLineEdit()
        self.bank_ifsc = QLineEdit()

        f_con.addRow("Email:", self.email)
        f_con.addRow("Phone:", self.phone)
        f_con.addRow("Address:", self.address)
        f_con.addRow("City / State:", self.city)
        f_con.addRow("Bank Name:", self.bank_name)
        f_con.addRow("Account Number:", self.bank_acct)
        f_con.addRow("IFSC Code:", self.bank_ifsc)
        l.addWidget(sec_con)

        act_box = QHBoxLayout()
        save_btn = QPushButton("💾 Save Supplier Master")
        save_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        save_btn.clicked.connect(self._save_supplier)
        act_box.addWidget(save_btn)
        act_box.addStretch()
        l.addLayout(act_box)

        l.addStretch()
        scroll.setWidget(body)
        QVBoxLayout(self.tab_create).addWidget(scroll)

    def _save_supplier(self) -> None:
        name = self.sup_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Supplier Name is required.")
            return

        payload = {
            "supplier_code": self.sup_code.text().strip(),
            "supplier_name": name,
            "gstin": self.gstin.text().strip().upper(),
            "pan": self.pan.text().strip().upper(),
            "email": self.email.text().strip(),
            "phone": self.phone.text().strip(),
            "address": self.address.text().strip(),
            "city": self.city.text().strip(),
            "bank_name": self.bank_name.text().strip(),
            "bank_account_number": self.bank_acct.text().strip(),
            "bank_ifsc": self.bank_ifsc.text().strip().upper(),
        }

        try:
            res = self.api.supplier_create(payload)
            sup_code = res.get("data", {}).get("supplier_code") or "Supplier"
            QMessageBox.information(self, "Success", f"Supplier {sup_code} created successfully!")
            self.sup_name.clear()
            self.sup_code.clear()
            self.gstin.clear()
            self.reload_list()
            self.tabs.setCurrentIndex(1)
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to create supplier: {e}")

    # ── TAB 2: SUPPLIERS DIRECTORY ───────────────────────────────────────

    def _build_list_tab(self) -> None:
        layout = QVBoxLayout(self.tab_list)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search suppliers by name, code, GSTIN...")
        self.search_bar.textChanged.connect(self.reload_list)
        bar.addWidget(self.search_bar)

        ref_btn = QPushButton("🔄 Refresh")
        ref_btn.clicked.connect(self.reload_list)
        bar.addWidget(ref_btn)

        toggle_btn = QPushButton("Toggle Active / Block")
        toggle_btn.clicked.connect(self._toggle_supplier)
        bar.addWidget(toggle_btn)

        layout.addLayout(bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Code", "Name", "GSTIN", "Phone", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        layout.addWidget(self.table)

    def reload_list(self) -> None:
        query = self.search_bar.text().strip()
        try:
            sups = self.api.supplier_list(search=query)
            if not isinstance(sups, list):
                sups = []
            self.table.setRowCount(0)
            for r, s in enumerate(sups):
                if not isinstance(s, dict):
                    continue
                self.table.insertRow(self.table.rowCount())
                idx = self.table.rowCount() - 1
                self.table.setItem(idx, 0, QTableWidgetItem(str(s.get("id", ""))))
                self.table.setItem(idx, 1, QTableWidgetItem(str(s.get("supplier_code", s.get("code", "")))))
                self.table.setItem(idx, 2, QTableWidgetItem(str(s.get("supplier_name", s.get("name", "")))))
                self.table.setItem(idx, 3, QTableWidgetItem(str(s.get("gstin", ""))))
                self.table.setItem(idx, 4, QTableWidgetItem(str(s.get("phone", ""))))
                status_str = "ACTIVE" if s.get("is_active", 1) else "BLOCKED"
                self.table.setItem(idx, 5, QTableWidgetItem(status_str))
        except ApiError:
            pass

    def _toggle_supplier(self) -> None:
        curr = self.table.currentRow()
        if curr < 0:
            QMessageBox.information(self, "Select Supplier", "Please select a supplier from the table.")
            return
        id_item = self.table.item(curr, 0)
        if not id_item:
            return
        sup_id = int(id_item.text())
        try:
            self.api.supplier_toggle(sup_id)
            self.reload_list()
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Could not toggle supplier status: {e}")
