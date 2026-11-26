"""
Brain ERP – Procurement Reports & Planning Widget.
Tabs:
  1. Status Pipeline (ME2PN: PR pending PO, PO pending GRN, GRN pending Bill)
  2. Stock Reorder Point Monitoring (ZMRP)
  3. Procurement Analytics & Spend (ZMMR)
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QFrame, QMessageBox
)

from ..api_client import ApiClient, ApiError


class ReportsWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._build_ui()
        self.reload_reports()

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

        self.tab_pending = QWidget()
        self.tab_reorder = QWidget()
        self.tab_analytics = QWidget()

        self._build_pending_tab()
        self._build_reorder_tab()
        self._build_analytics_tab()

        self.tabs.addTab(self.tab_pending, "Status Pipeline (ME2PN)")
        self.tabs.addTab(self.tab_reorder, "Reorder Point Stock (ZMRP)")
        self.tabs.addTab(self.tab_analytics, "Procurement Tracking (ZMMR)")
        layout.addWidget(self.tabs)

    # ── TAB 1: PENDING CYCLE REPORT (ME2PN) ──────────────────────────────

    def _build_pending_tab(self) -> None:
        l = QVBoxLayout(self.tab_pending)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(10)

        bar = QHBoxLayout()
        title = QLabel("ME2PN · Material Management Status & Pending Cycle")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #111827;")
        ref_btn = QPushButton("🔄 Refresh Status Pipeline")
        ref_btn.clicked.connect(self._load_pending)
        bar.addWidget(title)
        bar.addStretch()
        bar.addWidget(ref_btn)
        l.addLayout(bar)

        self.pending_summary_lbl = QLabel("")
        self.pending_summary_lbl.setStyleSheet("font-size: 13px; color: #374151; padding: 6px; background: white; border: 1px solid #e5e7eb; border-radius: 6px;")
        l.addWidget(self.pending_summary_lbl)

        self.pending_table = QTableWidget(0, 5)
        self.pending_table.setHorizontalHeaderLabels(["Pipeline Stage", "Open Documents", "Pending Line Items", "Estimated Value", "Oldest Open"])
        self.pending_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pending_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        l.addWidget(self.pending_table)

    def _load_pending(self) -> None:
        try:
            res = self.api.pending_report()
            stages = res.get("stages", [
                {"stage": "PR Pending PO", "docs": res.get("pr_pending_po", 0), "lines": "-", "value": "-"},
                {"stage": "PO Pending GRN (In-transit)", "docs": res.get("po_pending_grn", 0), "lines": "-", "value": "-"},
                {"stage": "GRN Pending Invoice (GR/IR)", "docs": res.get("grn_pending_invoice", 0), "lines": "-", "value": "-"},
            ])
            self.pending_table.setRowCount(0)
            for r, s in enumerate(stages):
                self.pending_table.insertRow(r)
                self.pending_table.setItem(r, 0, QTableWidgetItem(str(s.get("stage"))))
                self.pending_table.setItem(r, 1, QTableWidgetItem(str(s.get("docs", 0))))
                self.pending_table.setItem(r, 2, QTableWidgetItem(str(s.get("lines", "-"))))
                self.pending_table.setItem(r, 3, QTableWidgetItem(str(s.get("value", "-"))))
                self.pending_table.setItem(r, 4, QTableWidgetItem(str(s.get("oldest", "-"))))

            self.pending_summary_lbl.setText("<b>Active Cycles:</b> PRs awaiting conversion, active procurement orders in delivery, and goods receipts awaiting vendor billing.")
        except ApiError:
            pass

    # ── TAB 2: REORDER POINT MONITORING (ZMRP) ───────────────────────────

    def _build_reorder_tab(self) -> None:
        l = QVBoxLayout(self.tab_reorder)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(10)

        bar = QHBoxLayout()
        title = QLabel("ZMRP · Material Reorder Point Monitoring & Auto-PR")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #111827;")
        bar.addWidget(title)
        bar.addStretch()

        ref_btn = QPushButton("🔄 Check Stock Levels")
        ref_btn.clicked.connect(self._load_reorder)
        create_prs_btn = QPushButton("⚡ Auto-Generate PRs for Deficit Items")
        create_prs_btn.setStyleSheet("background: #16a34a; color: white; font-weight: 600; padding: 6px 14px; border-radius: 4px;")
        create_prs_btn.clicked.connect(self._auto_create_prs)

        bar.addWidget(ref_btn)
        bar.addWidget(create_prs_btn)
        l.addLayout(bar)

        self.reorder_table = QTableWidget(0, 6)
        self.reorder_table.setHorizontalHeaderLabels(["Item Code", "Material Name", "Current Stock", "Reorder Point", "Deficit Qty", "Action Needed"])
        self.reorder_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.reorder_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        l.addWidget(self.reorder_table)

    def _load_reorder(self) -> None:
        try:
            items = self.api.reorder_report()
            if not isinstance(items, list):
                items = []
            self.reorder_table.setRowCount(0)
            for r, itm in enumerate(items):
                if not isinstance(itm, dict):
                    continue
                self.reorder_table.insertRow(self.reorder_table.rowCount())
                row_idx = self.reorder_table.rowCount() - 1
                self.reorder_table.setItem(row_idx, 0, QTableWidgetItem(str(itm.get("item_code", ""))))
                self.reorder_table.setItem(row_idx, 1, QTableWidgetItem(str(itm.get("item_name", ""))))
                self.reorder_table.setItem(row_idx, 2, QTableWidgetItem(f"{float(itm.get('current_stock', 0)):.2f}"))
                self.reorder_table.setItem(row_idx, 3, QTableWidgetItem(f"{float(itm.get('reorder_point', 0)):.2f}"))
                deficit = float(itm.get("deficit", 0))
                self.reorder_table.setItem(row_idx, 4, QTableWidgetItem(f"{deficit:.2f}"))
                status_item = QTableWidgetItem("CRITICAL" if deficit > 0 else "OK")
                if deficit > 0:
                    status_item.setForeground(Qt.GlobalColor.red)
                self.reorder_table.setItem(row_idx, 5, status_item)
        except ApiError:
            pass

    def _auto_create_prs(self) -> None:
        try:
            items = self.api.reorder_report()
            deficit_items = [itm for itm in items if float(itm.get("deficit", 0)) > 0]
            if not deficit_items:
                QMessageBox.information(self, "No Action Needed", "All stock levels are above reorder points.")
                return

            confirm = QMessageBox.question(
                self, "Confirm Requisitions",
                f"Generate automated Purchase Requisitions for {len(deficit_items)} deficit item(s)?"
            )
            if confirm == QMessageBox.StandardButton.Yes:
                res = self.api.reorder_create_pr(deficit_items)
                pr_count = res.get("pr_count", len(deficit_items))
                QMessageBox.information(self, "Success", f"Created {pr_count} Purchase Requisition(s) successfully!")
                self._load_reorder()
        except ApiError as e:
            QMessageBox.critical(self, "Error", f"Failed to generate PRs: {e}")

    # ── TAB 3: PROCUREMENT ANALYTICS (ZMMR) ──────────────────────────────

    def _build_analytics_tab(self) -> None:
        l = QVBoxLayout(self.tab_analytics)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(10)

        title = QLabel("ZMMR · Procurement Performance & Spend Follow-up")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #111827;")
        l.addWidget(title)

        self.analytics_table = QTableWidget(0, 4)
        self.analytics_table.setHorizontalHeaderLabels(["Metric / KPI", "Current Period", "Previous Period", "Variance"])
        self.analytics_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.analytics_table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        l.addWidget(self.analytics_table)

    def reload_reports(self) -> None:
        self._load_pending()
        self._load_reorder()
        try:
            res = self.api.mm_reports_summary()
            metrics = res.get("metrics", [
                {"name": "Total Purchase Orders Issued", "curr": str(res.get("po_count", "-")), "prev": "-", "var": "-"},
                {"name": "Total Goods Receipts (GRN)", "curr": str(res.get("grn_count", "-")), "prev": "-", "var": "-"},
                {"name": "Total Supplier Invoices Booked", "curr": str(res.get("invoice_count", "-")), "prev": "-", "var": "-"},
                {"name": "Total Procurement Spend", "curr": f"₹{float(res.get('total_spend', 0)):,.2f}", "prev": "-", "var": "-"},
            ])
            self.analytics_table.setRowCount(0)
            for r, m in enumerate(metrics):
                self.analytics_table.insertRow(r)
                self.analytics_table.setItem(r, 0, QTableWidgetItem(str(m.get("name"))))
                self.analytics_table.setItem(r, 1, QTableWidgetItem(str(m.get("curr"))))
                self.analytics_table.setItem(r, 2, QTableWidgetItem(str(m.get("prev"))))
                self.analytics_table.setItem(r, 3, QTableWidgetItem(str(m.get("var"))))
        except ApiError:
            pass
