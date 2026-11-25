"""
Brain ERP – Material Import & Export Widget (ZMMI).
Two-step CSV batch preview & import/export for materials master.
"""

from __future__ import annotations

import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QFrame, QMessageBox, QCheckBox,
    QFileDialog
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
        "QCheckBox { color: #111827; }"
    )
    return gb


class MaterialImportWidget(QWidget):
    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._current_token: str = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header
        top = QHBoxLayout()
        title = QLabel("ZMMI · Material Master CSV Import & Export")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        top.addWidget(title)
        top.addStretch()

        template_btn = QPushButton("📄 Download CSV Template")
        template_btn.clicked.connect(self._download_template)
        export_btn = QPushButton("📥 Export Materials to CSV")
        export_btn.clicked.connect(self._export_csv)

        top.addWidget(template_btn)
        top.addWidget(export_btn)
        layout.addLayout(top)

        # Step 1: Input section
        in_sec = _section("Step 1: CSV Data Source")
        il = QVBoxLayout(in_sec)

        btn_row = QHBoxLayout()
        upload_btn = QPushButton("📂 Open CSV File...")
        upload_btn.setStyleSheet("background: #2563eb; color: white; padding: 6px 14px; font-weight: 600; border-radius: 4px;")
        upload_btn.clicked.connect(self._open_file)
        btn_row.addWidget(upload_btn)

        self.update_existing = QCheckBox("Update Existing Materials (match by Item Code)")
        self.update_existing.setChecked(False)
        btn_row.addWidget(self.update_existing)
        btn_row.addStretch()
        il.addLayout(btn_row)

        self.csv_editor = QTextEdit()
        self.csv_editor.setPlaceholderText("Or paste CSV text content directly here (headers must include at least item_name)...")
        self.csv_editor.setFixedHeight(110)
        self.csv_editor.setStyleSheet("background: white; color: #111827; font-family: monospace; font-size: 11px;")
        il.addWidget(self.csv_editor)

        preview_act = QHBoxLayout()
        preview_btn = QPushButton("🔍 Parse & Preview CSV")
        preview_btn.setStyleSheet(
            "QPushButton { background: #0284c7; color: white; font-weight: 700;"
            " padding: 8px 18px; border-radius: 5px; }"
            "QPushButton:hover { background: #0369a1; }"
        )
        preview_btn.clicked.connect(self._preview_csv)
        preview_act.addWidget(preview_btn)

        self.preview_stats = QLabel("Upload or paste CSV, then click Parse & Preview.")
        self.preview_stats.setStyleSheet("font-size: 12px; color: #4b5563; font-weight: 600;")
        preview_act.addWidget(self.preview_stats)
        preview_act.addStretch()
        il.addLayout(preview_act)
        layout.addWidget(in_sec)

        # Step 2: Validation preview table
        prev_sec = _section("Step 2: Preview & Validation Verification")
        pl = QVBoxLayout(prev_sec)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Line #", "Item Code", "Item Name", "Status", "Errors / Warnings"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet("background: white; color: #111827; border: 1px solid #e5e7eb;")
        pl.addWidget(self.table)

        confirm_act = QHBoxLayout()
        self.confirm_btn = QPushButton("✓ Confirm & Commit Import to Database")
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: 700;"
            " padding: 10px 24px; font-size: 13px; border-radius: 6px; }"
            "QPushButton:disabled { background: #9ca3af; }"
            "QPushButton:hover:!disabled { background: #15803d; }"
        )
        self.confirm_btn.clicked.connect(self._confirm_import)
        confirm_act.addWidget(self.confirm_btn)
        confirm_act.addStretch()
        pl.addLayout(confirm_act)
        layout.addWidget(prev_sec)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV File", "", "CSV Files (*.csv);;All Files (*)")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.csv_editor.setPlainText(content)
            self._preview_csv()

    def _preview_csv(self) -> None:
        content = self.csv_editor.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Error", "CSV content is empty.")
            return

        update = self.update_existing.isChecked()
        try:
            res = self.api.material_import_preview(content, update_existing=update)
            self._current_token = res.get("token", "")
            counts = res.get("counts", {})
            total = res.get("total_rows", 0)
            preview_rows = res.get("preview", [])

            self.preview_stats.setText(
                f"<b>Total:</b> {total} | <b>New:</b> {counts.get('NEW', 0)} | "
                f"<b>Update:</b> {counts.get('UPDATE', 0)} | <b>Skip:</b> {counts.get('SKIP', 0)} | "
                f"<b>Errors:</b> {counts.get('ERROR', 0)}"
            )

            self.table.setRowCount(0)
            for r, item in enumerate(preview_rows):
                self.table.insertRow(r)
                self.table.setItem(r, 0, QTableWidgetItem(str(item.get("line"))))
                self.table.setItem(r, 1, QTableWidgetItem(str(item.get("item_code", ""))))
                self.table.setItem(r, 2, QTableWidgetItem(str(item.get("item_name", ""))))

                st = item.get("status")
                st_item = QTableWidgetItem(st)
                if st == "NEW":
                    st_item.setForeground(Qt.GlobalColor.darkGreen)
                elif st == "UPDATE":
                    st_item.setForeground(Qt.GlobalColor.blue)
                elif st == "ERROR":
                    st_item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(r, 3, st_item)

                errs = " ".join(item.get("errors", []))
                self.table.setItem(r, 4, QTableWidgetItem(errs))

            self.confirm_btn.setEnabled(bool(self._current_token and (counts.get("NEW", 0) > 0 or counts.get("UPDATE", 0) > 0)))
        except ApiError as e:
            QMessageBox.critical(self, "Preview Error", f"Failed to preview CSV: {e}")

    def _confirm_import(self) -> None:
        if not self._current_token:
            return
        try:
            res = self.api.material_import_confirm(self._current_token)
            counts = res.get("counts", {})
            QMessageBox.information(
                self, "Import Completed",
                f"Batch import executed!\n"
                f"• Created: {counts.get('NEW', 0)}\n"
                f"• Updated: {counts.get('UPDATE', 0)}\n"
                f"• Skipped: {counts.get('SKIP', 0)}\n"
                f"• Errors: {counts.get('ERROR', 0)}"
            )
            self._current_token = ""
            self.confirm_btn.setEnabled(False)
            self.table.setRowCount(0)
            self.csv_editor.clear()
            self.preview_stats.setText("Import completed successfully!")
        except ApiError as e:
            QMessageBox.critical(self, "Import Error", f"Failed to commit import: {e}")

    def _download_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Template", "material_import_template.csv", "CSV Files (*.csv)")
        if path:
            try:
                import httpx
                r = httpx.get(f"{self.api.base_url}/api/mm/import/template")
                with open(path, "wb") as f:
                    f.write(r.content)
                QMessageBox.information(self, "Success", f"Template saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save template: {e}")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Materials", "materials_catalog.csv", "CSV Files (*.csv)")
        if path:
            try:
                import httpx
                r = httpx.get(f"{self.api.base_url}/api/mm/export")
                with open(path, "wb") as f:
                    f.write(r.content)
                QMessageBox.information(self, "Success", f"Materials catalog exported to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export materials: {e}")
