"""
Brain ERP – Purchase Requisitions Widget (ME51N / ME53N).
Tabs:
  1. Create Requisition (ME51N)
  2. Requisitions List & Approval (ME53N)
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


class PRWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
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

        self.tabs.addTab(self.tab_create, "Create PR (ME51N)")
        self.tabs.addTab(self.tab_list, "Requisitions List (ME53N)")
        layout.addWidget(self.tabs)

    # ── TAB 1: CREATE PR ──────────────────────────────────────────────────

    def _build_create_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: #f8fafc; color: #111827;")
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(12)

        hdr_sec = _section("Requisition Header")
        h_layout = QFormLayout(hdr_sec)

        self.req_date = QDateEdit()
        self.req_date.setCalendarPopup(True)
        self.req_date.setDate(date.today())

        self.department_input = QLineEdit()
        self.department_input.setPlaceholderText("e.g. Production / Maintenance / Stores")

        self.purpose_input = QLineEdit()
        self.purpose_input.setPlaceholderText("Reason for requisition")

        h_layout.addRow("Required By Date *:", self.req_date)
        h_layout.addRow("Requesting Dept *:", self.department_input)
        h_layout.addRow("Purpose / Justification:", self.purpose_input)
        l.addWidget(hdr_sec)

        # Lines
        lines_sec = _section("Requested Materials")
        lines_layout = QVBoxLayout(lines_sec)

        self.lines_table = QTableWidget(0, 4)
        self.lines_table.setHorizontalHeaderLabels(["Material Item *", "Requested Qty *", "Estimated Price", "Subtotal"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lines_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        lines_layout.addWidget(self.lines_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Material Line")
        add_btn.setStyleSheet("background: #2563eb; color: white; padding: 6px 12px; font-weight: 600; border-radius: 4px;")
        add_btn.clicked.connect(self._add_line)
        del_btn = QPushButton("Remove Selected Line")
        del_btn.setStyleSheet("background: #ef4444; color: white; padding: 6px 12px; font-weight: 600; border-radius: 4px;")
        del_btn.clicked.connect(self._del_line)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()

        self.totals_lbl = QLabel("<b>Estimated Total:</b> ₹0.00")
        self.totals_lbl.setStyleSheet("font-size: 13px; color: #111827;")
        btn_row.addWidget(self.totals_lbl)
        lines_layout.addLayout(btn_row)
        l.addWidget(lines_sec)

        # Action bar
        act_box = QHBoxLayout()
        self.save_btn = QPushButton("💾 Submit Purchase Requisition")
        self.save_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        self.save_btn.clicked.connect(self._save_pr)
        self.status_lbl = QLabel("")
        act_box.addWidget(self.save_btn)
        act_box.addWidget(self.status_lbl)
        act_box.addStretch()
        l.addLayout(act_box)

        l.addStretch()
        scroll.setWidget(body)
        QVBoxLayout(self.tab_create).addWidget(scroll)

    def _add_line(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        combo = QComboBox()
        for itm in self._items_lookup:
            combo.addItem(f"{itm.get('item_code')} – {itm.get('item_name')}", itm.get("id"))

        qty_spin = QDoubleSpinBox()
        qty_spin.setRange(0.01, 1_000_000)
        qty_spin.setValue(1.0)
        qty_spin.valueChanged.connect(self._calc_totals)

        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.0, 10_000_000)
        price_spin.valueChanged.connect(self._calc_totals)

        sub_item = QTableWidgetItem("0.00")
        sub_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

        self.lines_table.setCellWidget(r, 0, combo)
        self.lines_table.setCellWidget(r, 1, qty_spin)
        self.lines_table.setCellWidget(r, 2, price_spin)
        self.lines_table.setItem(r, 3, sub_item)

        self._calc_totals()

    def _del_line(self) -> None:
        curr = self.lines_table.currentRow()
        if curr >= 0:
            self.lines_table.removeRow(curr)
            self._calc_totals()

    def _calc_totals(self) -> None:
        total = 0.0
        for r in range(self.lines_table.rowCount()):
            q_w = self.lines_table.cellWidget(r, 1)
            p_w = self.lines_table.cellWidget(r, 2)
            qty = q_w.value() if isinstance(q_w, QDoubleSpinBox) else 0.0
            price = p_w.value() if isinstance(p_w, QDoubleSpinBox) else 0.0
            sub = qty * price
            total += sub
            item = self.lines_table.item(r, 3)
            if item:
                item.setText(f"{sub:,.2f}")
        self.totals_lbl.setText(f"<b>Estimated Total:</b> ₹{total:,.2f}")

    def _save_pr(self) -> None:
        dept = self.department_input.text().strip()
        if not dept:
            QMessageBox.warning(self, "Error", "Please enter the requesting department.")
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
                lines.append({
                    "item_id": item_id,
                    "qty": qty,
                    "estimated_cost": price,
                })

        if not lines:
            QMessageBox.warning(self, "Error", "Add at least one line item.")
            return

        payload = {
            "department": dept,
            "required_date": self.req_date.date().toString("yyyy-MM-dd"),
            "purpose": self.purpose_input.text().strip(),
            "lines": lines,
        }

        try:
            res = self.api.pr_create(payload)
            pr_num = res.get("data", {}).get("pr_number") or "PR"
            self.status_lbl.setText(f"✓ Requisition {pr_num} created!")
            self.status_lbl.setStyleSheet("color: #16a34a; font-weight: 700;")
            self.lines_table.setRowCount(0)
            self._calc_totals()
            self.reload_list()
            self.tabs.setCurrentIndex(1)
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to create PR: {e}")

    # ── TAB 2: LIST PRs ───────────────────────────────────────────────────

    def _build_list_tab(self) -> None:
        layout = QVBoxLayout(self.tab_list)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Status:"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["ALL", "SUBMITTED", "APPROVED", "REJECTED", "ORDERED"])
        self.status_combo.currentTextChanged.connect(self.reload_list)
        bar.addWidget(self.status_combo)

        ref_btn = QPushButton("🔄 Refresh")
        ref_btn.clicked.connect(self.reload_list)
        bar.addWidget(ref_btn)

        appr_btn = QPushButton("✓ Approve Requisition")
        appr_btn.setStyleSheet("background: #2563eb; color: white; font-weight: 600; padding: 6px 14px; border-radius: 4px;")
        appr_btn.clicked.connect(self._approve_selected)
        bar.addWidget(appr_btn)
        bar.addStretch()

        layout.addLayout(bar)

        self.pr_table = QTableWidget(0, 6)
        self.pr_table.setHorizontalHeaderLabels(["ID", "PR Number", "Date", "Department", "Status", "Items"])
        self.pr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pr_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.pr_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        layout.addWidget(self.pr_table)

    def reload_data(self) -> None:
        try:
            self._items_lookup = self.api.mm_list()
        except ApiError:
            pass
        self.reload_list()

    def reload_list(self) -> None:
        st = self.status_combo.currentText()
        st_param = "" if st == "ALL" else st
        try:
            prs = self.api.pr_list(status=st_param)
            if not isinstance(prs, list):
                prs = []
            self.pr_table.setRowCount(0)
            for r, pr in enumerate(prs):
                if not isinstance(pr, dict):
                    continue
                self.pr_table.insertRow(self.pr_table.rowCount())
                idx = self.pr_table.rowCount() - 1
                self.pr_table.setItem(idx, 0, QTableWidgetItem(str(pr.get("id", ""))))
                self.pr_table.setItem(idx, 1, QTableWidgetItem(str(pr.get("pr_number", ""))))
                self.pr_table.setItem(idx, 2, QTableWidgetItem(str(pr.get("pr_date", ""))))
                self.pr_table.setItem(idx, 3, QTableWidgetItem(str(pr.get("department", ""))))
                self.pr_table.setItem(idx, 4, QTableWidgetItem(str(pr.get("status", ""))))
                self.pr_table.setItem(idx, 5, QTableWidgetItem(str(pr.get("line_count", ""))))
        except ApiError:
            pass

    def _approve_selected(self) -> None:
        curr = self.pr_table.currentRow()
        if curr < 0:
            QMessageBox.information(self, "Select PR", "Please select a requisition to approve.")
            return
        pr_id_item = self.pr_table.item(curr, 0)
        pr_num_item = self.pr_table.item(curr, 1)
        if not pr_id_item:
            return
        pr_id = int(pr_id_item.text())
        pr_num = pr_num_item.text() if pr_num_item else f"#{pr_id}"

        confirm = QMessageBox.question(self, "Approve PR", f"Approve Purchase Requisition {pr_num}?")
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.api.pr_action(pr_id, "APPROVE")
                QMessageBox.information(self, "Success", f"Requisition {pr_num} approved!")
                self.reload_list()
            except ApiError as e:
                QMessageBox.critical(self, "Error", f"Failed to approve PR: {e}")
