"""
Brain ERP — Settings screen (PyQt6 port of the WordPress "MSME ERP" plugin's
admin/settings.php page).

Sections, in the same order as settings.php:
  1. Company Configuration
  2. Branding & Document Assets (logo / stamp / background colour & image)
  3. AI & Notifications (Gemini API key + model)
  4. Quality Gate (GRN / supplier invoice QC switches)
  5. RFQ Cut-off: AI Comparison & Automatic Purchase Order
  6. Supplier Negotiation (RFQ)
  7. Email (SMTP) + "send test email"
  8. Database Status (read-only)
  9. ERP Users Sync
  10. About

Persistence
-----------
There is no `/api/settings/*` route on the FastAPI backend yet (api_client.py
has no `settings_get`/`settings_save` methods), so this screen currently
reads/writes a local JSON file instead of calling the server — see
`_LocalSettingsStore` below. Every "Save" button funnels through
`SettingsWidget._persist(section, payload)`, which is the one place to swap
in a real `self.api.settings_*(...)` call once that backend endpoint exists;
nothing else in this file needs to change.

Branding file uploads (logo / stamp / background image) are copied into
`frontend/assets/images/branding/`, reusing the same "drop a file in a known
path" approach the dashboard logo already uses (see dashboard_widget.py).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QSpinBox,
    QDoubleSpinBox, QTextEdit, QGroupBox, QFrame, QScrollArea,
    QFileDialog, QColorDialog, QMessageBox, QSizePolicy,
)

from ..api_client import ApiClient, ApiError

# ── Local on-disk settings store (stand-in until a backend endpoint exists) ──

_SETTINGS_DIR = Path.home() / ".brain_erp"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

_BRANDING_DIR = Path(__file__).resolve().parent.parent / "assets" / "images" / "branding"


class _LocalSettingsStore:
    """Tiny JSON-file key/value store, namespaced by section.

    Stand-in for backend persistence: `SettingsWidget._persist()` is the only
    caller, so replacing this with real API calls later is a one-function
    change.
    """

    def __init__(self, path: Path = _SETTINGS_FILE):
        self._path = path

    def load(self) -> dict:
        if not self._path.is_file():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_section(self, section: str, payload: dict) -> None:
        data = self.load()
        data[section] = payload
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_section(self, section: str) -> dict:
        return self.load().get(section, {})


# ── Shared styling helpers (matches supplier_widget.py's `_section()`) ──────

_CONTENT_BG = "#f0f2f5"


def _section(title: str) -> QGroupBox:
    gb = QGroupBox(title)
    gb.setStyleSheet(
        "QGroupBox { font-weight: 700; font-size: 11px; color: #111827;"
        " border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 14px;"
        " padding-top: 14px; background: #ffffff; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px;"
        " background: #f9fafb; color: #2563eb; }"
        "QLabel { color: #111827; background: transparent; }"
        "QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {"
        " color: #111827; background: #ffffff; }"
    )
    return gb


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #6b7280; font-size: 10px;")
    return lbl


def _save_btn(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton { background: #1a1f2e; color: white; border: none;"
        " padding: 7px 16px; font-size: 11px; font-weight: 700; }"
        "QPushButton:hover { background: #2563eb; }"
    )
    return btn


# ── Currencies / timezones (mirrors msme_erp_currencies() / the TZ list) ────

_CURRENCIES = [
    ("INR", "Indian Rupee"), ("USD", "US Dollar"), ("EUR", "Euro"),
    ("GBP", "British Pound"), ("AED", "UAE Dirham"), ("SGD", "Singapore Dollar"),
]

_TIMEZONES = [
    "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore",
    "America/New_York", "Europe/London", "UTC",
]


class SettingsWidget(QWidget):
    """Admin Settings screen. Construct with the shared ApiClient/session."""

    def __init__(self, api: ApiClient, session: dict | None = None):
        super().__init__()
        self.api = api
        self.session = session or {}
        self._store = _LocalSettingsStore()
        self._branding_pending: dict[str, Path] = {}  # field -> source file chosen this session
        self._build_ui()

    # ── persistence seam — swap this for a real API call later ────────────
    def _persist(self, section: str, payload: dict[str, Any]) -> None:
        """
        Single call site for saving a settings section.

        TODO(backend): once `/api/settings/{section}` exists, replace the
        body with e.g. `self.api.settings_save(section, payload)` and drop
        `_LocalSettingsStore`. Every Save button below calls this method, so
        no other code needs to change when that happens.
        """
        try:
            self._store.save_section(section, payload)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", f"Could not save settings locally: {e}")
            return
        QMessageBox.information(self, "Settings saved", "Settings saved successfully.")

    # ── layout ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setStyleSheet(f"background: {_CONTENT_BG};")

        header = QLabel("ERP Settings")
        header.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #111827;"
            " padding: 16px 20px 8px 20px; background: transparent;"
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setStyleSheet(f"background: {_CONTENT_BG};")

        body = QWidget()
        body.setStyleSheet(f"background: {_CONTENT_BG};")
        grid = QHBoxLayout(body)
        grid.setContentsMargins(20, 0, 20, 20)
        grid.setSpacing(16)

        left_col = QVBoxLayout()
        left_col.setSpacing(0)
        right_col = QVBoxLayout()
        right_col.setSpacing(0)

        left_col.addWidget(self._build_company_card())
        left_col.addWidget(self._build_branding_card())
        left_col.addWidget(self._build_ai_card())
        left_col.addWidget(self._build_quality_gate_card())
        left_col.addWidget(self._build_rfq_cutoff_card())
        left_col.addWidget(self._build_negotiation_card())
        left_col.addWidget(self._build_smtp_card())
        left_col.addStretch(1)

        right_col.addWidget(self._build_db_status_card())
        right_col.addWidget(self._build_users_sync_card())
        right_col.addWidget(self._build_about_card())
        right_col.addStretch(1)

        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)

        grid.addWidget(left_wrap, 7)
        grid.addWidget(right_wrap, 5)

        scroll.setWidget(body)
        outer.addWidget(header)
        outer.addWidget(scroll, 1)

    # ── 1. Company Configuration ───────────────────────────────────────────
    def _build_company_card(self) -> QGroupBox:
        saved = self._store.get_section("company")
        gb = _section("Company Configuration")
        v = QVBoxLayout(gb)

        hint = _hint(
            "Editing the company you are working in. Creating additional "
            "companies is managed elsewhere (Companies)."
        )
        v.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)

        self.company_name = QLineEdit(saved.get("name", self.session.get("company_name", "")))
        form.addRow("Company Name *", self.company_name)

        self.currency = QComboBox()
        for code, name in _CURRENCIES:
            self.currency.addItem(f"{code} — {name}", code)
        saved_cur = saved.get("currency_code", "INR")
        idx = next((i for i, (c, _) in enumerate(_CURRENCIES) if c == saved_cur), 0)
        self.currency.setCurrentIndex(idx)
        form.addRow("Currency", self.currency)

        self.gstin = QLineEdit(saved.get("gstin", ""))
        self.gstin.setMaxLength(15)
        self.gstin.setPlaceholderText("22AAAAA0000A1Z5")
        form.addRow("GSTIN", self.gstin)

        self.pan = QLineEdit(saved.get("pan_number", ""))
        self.pan.setMaxLength(10)
        form.addRow("PAN Number", self.pan)

        self.msme_reg = QLineEdit(saved.get("msme_reg_number", ""))
        form.addRow("MSME Reg. Number", self.msme_reg)

        self.fiscal_start = QLineEdit(saved.get("fiscal_start", "04-01"))
        self.fiscal_start.setPlaceholderText("04-01 (April 1)")
        form.addRow("Fiscal Year Start (MM-DD)", self.fiscal_start)

        self.timezone = QComboBox()
        self.timezone.addItems(_TIMEZONES)
        saved_tz = saved.get("timezone", "Asia/Kolkata")
        if saved_tz in _TIMEZONES:
            self.timezone.setCurrentText(saved_tz)
        form.addRow("Timezone", self.timezone)

        self.address = QTextEdit(saved.get("address", ""))
        self.address.setFixedHeight(56)
        form.addRow("Company Address", self.address)

        v.addLayout(form)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save Settings")
        btn.clicked.connect(self._save_company)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _save_company(self) -> None:
        if not self.company_name.text().strip():
            QMessageBox.warning(self, "Missing field", "Company Name is required.")
            return
        self._persist("company", {
            "name": self.company_name.text().strip(),
            "currency_code": self.currency.currentData(),
            "gstin": self.gstin.text().strip(),
            "pan_number": self.pan.text().strip(),
            "msme_reg_number": self.msme_reg.text().strip(),
            "fiscal_start": self.fiscal_start.text().strip() or "04-01",
            "timezone": self.timezone.currentText(),
            "address": self.address.toPlainText().strip(),
        })

    # ── 2. Branding & Document Assets ──────────────────────────────────────
    def _build_branding_card(self) -> QGroupBox:
        saved = self._store.get_section("branding")
        gb = _section("Branding & Document Assets")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "Logo shows in the sidebar and header. Background colour tints "
            "the workspace behind every page. Background image and stamp "
            "are saved here and can be wired into printed documents "
            "(PO/Invoice PDFs, etc.) as those are built out."
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        self._logo_preview, logo_row = self._branding_file_row(
            "logo", saved.get("logo_path"),
            "PNG with transparent background recommended.",
        )
        grid.addWidget(QLabel("Logo"), 0, 0)
        grid.addLayout(logo_row, 1, 0)

        self._stamp_preview, stamp_row = self._branding_file_row(
            "stamp", saved.get("stamp_path"),
            "Used to mark documents as approved/authorized once wired into a print view.",
        )
        grid.addWidget(QLabel("Stamp / Seal"), 0, 1)
        grid.addLayout(stamp_row, 1, 1)

        bg_color_lbl = QLabel("Background Colour")
        self._bg_color = saved.get("bg_color", "#f4f6f9")
        self._bg_color_btn = QPushButton()
        self._bg_color_btn.setFixedSize(60, 26)
        self._bg_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_bg_color_btn()
        self._bg_color_btn.clicked.connect(self._pick_bg_color)
        grid.addWidget(bg_color_lbl, 2, 0)
        grid.addWidget(self._bg_color_btn, 3, 0, Qt.AlignmentFlag.AlignLeft)

        self._bgimg_preview, bgimg_row = self._branding_file_row(
            "bg_image", saved.get("bg_image_path"),
            "Leave empty to use background colour only.",
        )
        grid.addWidget(QLabel("Background Image"), 2, 1)
        grid.addLayout(bgimg_row, 3, 1)

        v.addLayout(grid)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save Branding")
        btn.clicked.connect(self._save_branding)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _branding_file_row(self, field: str, saved_path: str | None, hint_text: str):
        preview = QLabel()
        preview.setFixedSize(96, 48)
        preview.setStyleSheet("background: #f9fafb; border: 1px solid #e5e7eb;")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setScaledContents(False)
        if saved_path and Path(saved_path).is_file():
            self._set_preview_pixmap(preview, Path(saved_path))
        else:
            preview.setText("No file")
            preview.setStyleSheet(preview.styleSheet() + "color: #9ca3af; font-size: 9px;")

        choose_btn = QPushButton("Choose File…")
        choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        choose_btn.setStyleSheet(
            "QPushButton { background: #f3f4f6; color: #111827; border: 1px solid #d1d5db;"
            " padding: 4px 10px; font-size: 10px; }"
            "QPushButton:hover { background: #e5e7eb; }"
        )
        choose_btn.clicked.connect(lambda: self._choose_branding_file(field, preview))

        row = QVBoxLayout()
        row.setSpacing(4)
        row.addWidget(preview)
        row.addWidget(choose_btn)
        row.addWidget(_hint(hint_text))
        return preview, row

    def _set_preview_pixmap(self, label: QLabel, path: Path) -> None:
        pix = QPixmap(str(path))
        if not pix.isNull():
            label.setText("")
            label.setPixmap(pix.scaled(
                96, 48, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _choose_branding_file(self, field: str, preview: QLabel) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose {field.replace('_', ' ')} image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if not path:
            return
        self._branding_pending[field] = Path(path)
        self._set_preview_pixmap(preview, Path(path))

    def _refresh_bg_color_btn(self) -> None:
        self._bg_color_btn.setStyleSheet(
            f"background: {self._bg_color}; border: 1px solid #d1d5db;"
        )

    def _pick_bg_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._bg_color), self, "Background Colour")
        if color.isValid():
            self._bg_color = color.name()
            self._refresh_bg_color_btn()

    def _save_branding(self) -> None:
        saved = self._store.get_section("branding")
        for field in ("logo", "stamp", "bg_image"):
            src = self._branding_pending.get(field)
            if src is None:
                continue
            try:
                _BRANDING_DIR.mkdir(parents=True, exist_ok=True)
                dest = _BRANDING_DIR / f"{field}{src.suffix.lower()}"
                shutil.copyfile(src, dest)
                saved[f"{field}_path"] = str(dest)
            except OSError as e:
                QMessageBox.warning(self, "Upload failed", f"Could not save {field}: {e}")
                return
        saved["bg_color"] = self._bg_color
        self._branding_pending.clear()
        self._persist("branding", saved)

    # ── 3. AI & Notifications ──────────────────────────────────────────────
    def _build_ai_card(self) -> QGroupBox:
        saved = self._store.get_section("ai")
        gb = _section("AI & Notifications")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "Powers AI-assisted quote comparison on RFQ screens — sends "
            "submitted supplier quotes to Google's Gemini API and returns a "
            "plain-text recommendation. Leave blank to leave that disabled."
        ))

        form = QFormLayout()
        self.gemini_key = QLineEdit(saved.get("gemini_api_key", ""))
        self.gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key.setPlaceholderText("AIza...")
        form.addRow("Google Gemini API Key", self.gemini_key)

        self.gemini_model = QLineEdit(saved.get("gemini_model", "gemini-2.5-flash"))
        form.addRow("Model", self.gemini_model)
        v.addLayout(form)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save AI Settings")
        btn.clicked.connect(self._save_ai)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _save_ai(self) -> None:
        self._persist("ai", {
            "gemini_api_key": self.gemini_key.text().strip(),
            "gemini_model": self.gemini_model.text().strip() or "gemini-2.5-flash",
        })

    # ── 4. Quality Gate ─────────────────────────────────────────────────────
    def _build_quality_gate_card(self) -> QGroupBox:
        saved = self._store.get_section("quality_gate")
        gb = _section("Quality Gate (GRN & Supplier Invoice)")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "Materials flagged \"QC on GRN\" in the material master always "
            "go to Quality Inspection from goods receipt; stock posts only "
            "after QC accepts. The first switch inspects every goods "
            "receipt regardless of that flag."
        ))

        self.qc_all_receipts = QCheckBox("Inspect every goods receipt (not only QC-flagged materials)")
        self.qc_all_receipts.setChecked(bool(saved.get("qc_all_receipts", False)))
        v.addWidget(self.qc_all_receipts)

        self.invoice_grn_check = QCheckBox("Block supplier invoices until the goods receipt is posted and QC-approved")
        self.invoice_grn_check.setChecked(bool(saved.get("invoice_grn_check", True)))
        v.addWidget(self.invoice_grn_check)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save Quality Settings")
        btn.clicked.connect(self._save_quality_gate)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _save_quality_gate(self) -> None:
        self._persist("quality_gate", {
            "qc_all_receipts": self.qc_all_receipts.isChecked(),
            "invoice_grn_check": self.invoice_grn_check.isChecked(),
        })

    # ── 5. RFQ Cut-off ──────────────────────────────────────────────────────
    def _build_rfq_cutoff_card(self) -> QGroupBox:
        saved = self._store.get_section("rfq_cutoff")
        gb = _section("RFQ Cut-off: AI Comparison & Automatic Purchase Order")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "When an RFQ reaches its cut-off, the system closes open "
            "negotiations, runs the AI comparison, selects the best "
            "supplier and creates one Purchase Order for it, routed to the "
            "releasing authority."
        ))

        self.rfq_auto_enabled = QCheckBox("Evaluate RFQs automatically at the cut-off")
        self.rfq_auto_enabled.setChecked(bool(saved.get("enabled", False)))
        v.addWidget(self.rfq_auto_enabled)

        form = QFormLayout()
        self.rfq_auto_hours = QSpinBox()
        self.rfq_auto_hours.setRange(0, 999)
        self.rfq_auto_hours.setValue(int(saved.get("hours", 0)))
        form.addRow("Default auto-compare after (hours)", self.rfq_auto_hours)
        v.addLayout(form)
        v.addWidget(_hint(
            "Counted from RFQ creation. 0 = use only the Quote Due By date. "
            "Can be overridden per RFQ."
        ))

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save Cut-off Settings")
        btn.clicked.connect(self._save_rfq_cutoff)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _save_rfq_cutoff(self) -> None:
        self._persist("rfq_cutoff", {
            "enabled": self.rfq_auto_enabled.isChecked(),
            "hours": self.rfq_auto_hours.value(),
        })

    # ── 6. Supplier Negotiation ─────────────────────────────────────────────
    def _build_negotiation_card(self) -> QGroupBox:
        saved = self._store.get_section("negotiation")
        gb = _section("Supplier Negotiation (RFQ)")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "Controls automatic reverse-negotiation on RFQs: the moment a "
            "supplier submits their first quote, the system counter-offers "
            "a discount, stepping down on each decline until they accept "
            "or the settings below run out."
        ))

        self.nego_enabled = QCheckBox("Auto-negotiate discounts on submitted RFQ quotes")
        self.nego_enabled.setChecked(bool(saved.get("enabled", False)))
        v.addWidget(self.nego_enabled)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)

        def _pct_spin(value: float) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(0, 99.99)
            sp.setDecimals(2)
            sp.setSuffix(" %")
            sp.setValue(value)
            return sp

        grid.addWidget(QLabel("Opening Discount Ask %"), 0, 0)
        self.nego_initial = _pct_spin(float(saved.get("initial_pct", 20)))
        grid.addWidget(self.nego_initial, 1, 0)

        grid.addWidget(QLabel("Step Down min %"), 0, 1)
        self.nego_step_min = _pct_spin(float(saved.get("step_min", 2)))
        grid.addWidget(self.nego_step_min, 1, 1)

        grid.addWidget(QLabel("Step Down max %"), 0, 2)
        self.nego_step_max = _pct_spin(float(saved.get("step_max", 3)))
        grid.addWidget(self.nego_step_max, 1, 2)

        grid.addWidget(QLabel("Floor % (never ask below this)"), 2, 0)
        self.nego_floor = _pct_spin(float(saved.get("floor_pct", 0)))
        grid.addWidget(self.nego_floor, 3, 0)

        grid.addWidget(QLabel("Max Rounds"), 2, 1)
        self.nego_rounds = QSpinBox()
        self.nego_rounds.setRange(1, 999)
        self.nego_rounds.setValue(int(saved.get("max_rounds", 15)))
        grid.addWidget(self.nego_rounds, 3, 1)

        v.addLayout(grid)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save Negotiation Settings")
        btn.clicked.connect(self._save_negotiation)
        save_row.addWidget(btn)
        v.addLayout(save_row)
        return gb

    def _save_negotiation(self) -> None:
        step_min = self.nego_step_min.value()
        step_max = max(step_min, self.nego_step_max.value())
        floor = min(self.nego_initial.value(), self.nego_floor.value())
        self._persist("negotiation", {
            "enabled": self.nego_enabled.isChecked(),
            "initial_pct": self.nego_initial.value(),
            "step_min": step_min,
            "step_max": step_max,
            "floor_pct": floor,
            "max_rounds": self.nego_rounds.value(),
        })

    # ── 7. Email (SMTP) ─────────────────────────────────────────────────────
    def _build_smtp_card(self) -> QGroupBox:
        saved = self._store.get_section("smtp")
        gb = _section("Email (SMTP)")
        v = QVBoxLayout(gb)
        v.addWidget(_hint(
            "Routes ERP emails (RFQ invitations, notifications) through a "
            "real mailbox. For Gmail: enable 2-Step Verification, then "
            "generate an App Password and paste it below — not the normal "
            "account password."
        ))

        self.smtp_enabled = QCheckBox("Enable SMTP (uncheck to use the server's default mail)")
        self.smtp_enabled.setChecked(bool(saved.get("enabled", False)))
        v.addWidget(self.smtp_enabled)

        form = QFormLayout()
        self.smtp_host = QLineEdit(saved.get("host", "smtp.gmail.com"))
        form.addRow("SMTP Host", self.smtp_host)

        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(saved.get("port", 587)))
        form.addRow("Port", self.smtp_port)

        self.smtp_encryption = QComboBox()
        self.smtp_encryption.addItem("TLS (port 587)", "tls")
        self.smtp_encryption.addItem("SSL (port 465)", "ssl")
        self.smtp_encryption.setCurrentIndex(1 if saved.get("encryption") == "ssl" else 0)
        form.addRow("Encryption", self.smtp_encryption)

        self.smtp_username = QLineEdit(saved.get("username", ""))
        self.smtp_username.setPlaceholderText("you@gmail.com")
        form.addRow("Gmail Address (SMTP Username)", self.smtp_username)

        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        has_saved_pw = bool(saved.get("password"))
        self.smtp_password.setPlaceholderText(
            "•••••••••••••••• (saved — leave blank to keep)" if has_saved_pw
            else "16-character app password"
        )
        form.addRow("App Password", self.smtp_password)

        self.smtp_from_email = QLineEdit(saved.get("from_email", ""))
        form.addRow("\"From\" Email (optional)", self.smtp_from_email)

        self.smtp_from_name = QLineEdit(saved.get("from_name", ""))
        form.addRow("\"From\" Name (optional)", self.smtp_from_name)

        v.addLayout(form)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        btn = _save_btn("Save SMTP Settings")
        btn.clicked.connect(self._save_smtp)
        save_row.addWidget(btn)
        v.addLayout(save_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #e5e7eb;")
        v.addWidget(divider)

        test_row = QHBoxLayout()
        self.test_email_to = QLineEdit()
        self.test_email_to.setPlaceholderText("you@example.com")
        test_btn = QPushButton("Send Test Email")
        test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        test_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #2563eb;"
            " border: 1px solid #2563eb; padding: 6px 14px; font-size: 10px; font-weight: 700; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        test_btn.clicked.connect(self._send_test_email)
        test_row.addWidget(QLabel("Send a test email to:"))
        test_row.addWidget(self.test_email_to, 1)
        test_row.addWidget(test_btn)
        v.addLayout(test_row)

        return gb

    def _save_smtp(self) -> None:
        saved = self._store.get_section("smtp")
        payload = {
            "enabled": self.smtp_enabled.isChecked(),
            "host": self.smtp_host.text().strip() or "smtp.gmail.com",
            "port": self.smtp_port.value(),
            "encryption": self.smtp_encryption.currentData(),
            "username": self.smtp_username.text().strip(),
            "from_email": self.smtp_from_email.text().strip(),
            "from_name": self.smtp_from_name.text().strip(),
        }
        # Only overwrite the stored password if a new one was typed — the
        # field is always rendered blank (see _build_smtp_card), so a plain
        # save without retyping it must not wipe the working password.
        new_pw = self.smtp_password.text().strip()
        payload["password"] = new_pw if new_pw else saved.get("password", "")
        self._persist("smtp", payload)
        self.smtp_password.clear()

    def _send_test_email(self) -> None:
        to = self.test_email_to.text().strip()
        if not to:
            QMessageBox.warning(self, "Missing address", "Enter a valid email address to send the test to.")
            return
        # TODO(backend): no /api/settings/send-test-email route yet. Wire
        # this to self.api once SMTP sending is implemented server-side.
        QMessageBox.information(
            self, "Not wired up yet",
            "Sending a live test email requires a backend SMTP endpoint, "
            "which doesn't exist yet. SMTP settings above are saved locally "
            "in the meantime.",
        )

    # ── 8. Database Status (read-only) ──────────────────────────────────────
    def _build_db_status_card(self) -> QGroupBox:
        gb = _section("Database Status")
        v = QVBoxLayout(gb)

        rows = [
            ("Connection", "Connected via FastAPI backend"),
            ("Backend", self.api.base_url),
            ("Company", self.session.get("company_name", "—")),
            ("User", self.session.get("full_name") or self.session.get("username", "—")),
        ]
        form = QFormLayout()
        for label, value in rows:
            val_lbl = QLabel(str(value))
            val_lbl.setStyleSheet("color: #374151; font-size: 11px;")
            form.addRow(label + ":", val_lbl)
        v.addLayout(form)

        v.addWidget(_hint(
            "Full table/row diagnostics require a backend status endpoint "
            "(not implemented yet) — this card currently reflects only "
            "what the frontend already knows from the active session."
        ))
        return gb

    # ── 9. ERP Users Sync ────────────────────────────────────────────────────
    def _build_users_sync_card(self) -> QGroupBox:
        gb = _section("ERP Users Sync")
        v = QVBoxLayout(gb)
        v.addWidget(_hint("Sync platform users into ERP user records."))
        btn = QPushButton("Sync Users to ERP")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #2563eb;"
            " border: 1px solid #2563eb; padding: 6px 14px; font-size: 10px; font-weight: 700; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        btn.clicked.connect(self._sync_users)
        v.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        return gb

    def _sync_users(self) -> None:
        # TODO(backend): no /api/users/sync route yet.
        QMessageBox.information(
            self, "Not wired up yet",
            "User sync requires a backend endpoint that doesn't exist yet.",
        )

    # ── 10. About ────────────────────────────────────────────────────────────
    def _build_about_card(self) -> QGroupBox:
        gb = _section("About")
        v = QVBoxLayout(gb)
        lines = [
            "Brain ERP",
            "Developed by: Prigag Online Pvt. Ltd.",
            "Website: brain.prigag.com",
            "Support: support@prigag.com",
        ]
        for line in lines:
            lbl = QLabel(line)
            lbl.setStyleSheet("color: #374151; font-size: 11px;")
            v.addWidget(lbl)
        return gb
