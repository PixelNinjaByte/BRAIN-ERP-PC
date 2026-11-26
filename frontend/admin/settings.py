"""
Brain ERP – Settings screen, rendered with QWebEngineView.

Same architecture as MM01Widget (frontend/mm/mm01_widget.py): the whole UI
is one self-contained HTML/CSS/JS page (modelled on the WordPress MSME ERP
plugin's admin/settings.php). Python only does two things:
  * hosts the QWebEngineView
  * exposes a small QWebChannel bridge so the page can load/save settings
    and upload branding images without blocking the Qt GUI thread

Sections, same order as settings.php:
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
There is no `/api/settings/*` route on the FastAPI backend yet, so this
screen reads/writes a local JSON file via `_LocalSettingsStore` instead of
calling the server. Every section's Save button goes through the bridge's
"save_section" method (`_Bridge._save_section`) — the one place to swap in
a real `self.api.settings_save(...)` call once that backend endpoint
exists. "send_test_email" and "sync_users" are stubbed with clear
TODO(backend) markers for the same reason.

Branding file uploads (logo / stamp / background image) are sent from the
page as base64 (read client-side with FileReader — a page has no access to
real filesystem paths), decoded and written into
`frontend/assets/images/branding/` by `_Bridge._save_branding_file`, same
directory the previous native-widget version used. Previews are sent back
to the page as base64 data URIs so nothing needs a file:// URL.

Requires:  pip install PyQt6 PyQt6-WebEngine
"""

from __future__ import annotations

import base64
import json
import mimetypes
import traceback
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "QtWebEngine could not be imported. Install it with:\n"
        "    pip install PyQt6-WebEngine\n"
        "and make sure its version matches PyQt6 (pip show PyQt6 PyQt6-WebEngine; "
        "if they differ: pip install -U PyQt6 PyQt6-Qt6 PyQt6-WebEngine PyQt6-WebEngine-Qt6).\n"
        f"Original error: {_e}"
    ) from _e

from ..api_client import ApiClient

# ── Local on-disk settings store (stand-in until a backend endpoint exists) ──

_SETTINGS_DIR = Path.home() / ".brain_erp"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"
_BRANDING_DIR = Path(__file__).resolve().parent.parent / "assets" / "images" / "branding"
_BRANDING_FIELDS = ("logo", "stamp", "bg_image")


class _LocalSettingsStore:
    """Tiny JSON-file key/value store, namespaced by section.

    Stand-in for backend persistence: `_Bridge` is the only caller, so
    replacing this with real API calls later is a one-class change.
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


# ──────────────────────────────────────────────────────────────────────────────
# Python <-> JS bridge
# ──────────────────────────────────────────────────────────────────────────────

class _Job(QRunnable):
    def __init__(self, fn, req_id: str, bridge: "_Bridge"):
        super().__init__()
        self.fn, self.req_id, self.bridge = fn, req_id, bridge

    def run(self) -> None:
        try:
            out = {"ok": True, "data": self.fn()}
        except Exception as e:  # never let a worker die silently
            traceback.print_exc()
            out = {"ok": False, "error": str(e)}
        self.bridge.response.emit(self.req_id, json.dumps(out, default=str))


class _Bridge(QObject):
    """Exposed to JavaScript as `bridge`."""

    response = pyqtSignal(str, str)  # (request id, JSON string)

    def __init__(self, api: ApiClient, session: dict, store: _LocalSettingsStore):
        super().__init__()
        self.api = api
        self.session = session
        self.store = store

    @pyqtSlot(str, str, str)
    def call(self, req_id: str, method: str, payload_json: str) -> None:
        payload = json.loads(payload_json or "{}")
        handlers = {
            "bootstrap": lambda: self._bootstrap(),
            "save_section": lambda: self._save_section(payload),
            "save_branding_file": lambda: self._save_branding_file(payload),
            "send_test_email": lambda: self._send_test_email(payload),
            "sync_users": lambda: self._sync_users(),
        }
        fn = handlers.get(method)
        if fn is None:
            self.response.emit(req_id, json.dumps({"ok": False, "error": f"Unknown method {method}"}))
            return
        QThreadPool.globalInstance().start(_Job(fn, req_id, self))

    # ── handlers ────────────────────────────────────────────────────────────

    def _bootstrap(self) -> dict:
        data = self.store.load()
        data["branding"] = self._branding_with_previews(data.get("branding", {}))
        return {
            "settings": data,
            "session": {
                "company_name": self.session.get("company_name", ""),
                "full_name": self.session.get("full_name") or self.session.get("username", ""),
            },
            "api_base_url": self.api.base_url,
        }

    @staticmethod
    def _file_to_data_uri(path_str: str | None) -> str | None:
        if not path_str:
            return None
        p = Path(path_str)
        if not p.is_file():
            return None
        mime, _ = mimetypes.guess_type(str(p))
        mime = mime or "application/octet-stream"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _branding_with_previews(self, branding: dict) -> dict:
        out = dict(branding)
        for field in _BRANDING_FIELDS:
            out[f"{field}_preview"] = self._file_to_data_uri(branding.get(f"{field}_path"))
        return out

    def _save_section(self, payload: dict) -> dict[str, Any]:
        section = payload.get("section")
        data = dict(payload.get("data") or {})
        if not section:
            raise ValueError("Missing section")

        if section == "smtp":
            # Only overwrite the stored password if a new one was typed —
            # the field always starts blank client-side (see _HTML), so a
            # plain save without retyping it must not wipe the working one.
            existing = self.store.get_section("smtp")
            if not data.get("password"):
                data["password"] = existing.get("password", "")

        if section == "branding":
            # This path only ever carries bg_color (file fields are saved
            # immediately on choose via save_branding_file) — merge onto
            # whatever file paths are already stored rather than clobber them.
            existing = self.store.get_section("branding")
            existing.update(data)
            data = existing

        self.store.save_section(section, data)
        return self._branding_with_previews(data) if section == "branding" else data

    def _save_branding_file(self, payload: dict) -> dict[str, Any]:
        field = payload.get("field")
        if field not in _BRANDING_FIELDS:
            raise ValueError(f"Unknown branding field {field!r}")
        filename = payload.get("filename") or "upload"
        raw_b64 = payload.get("data") or ""
        if "," in raw_b64:  # strip a "data:<mime>;base64," prefix if present
            raw_b64 = raw_b64.split(",", 1)[1]
        raw = base64.b64decode(raw_b64)

        suffix = Path(filename).suffix.lower() or ".png"
        _BRANDING_DIR.mkdir(parents=True, exist_ok=True)
        dest = _BRANDING_DIR / f"{field}{suffix}"
        dest.write_bytes(raw)

        branding = self.store.get_section("branding")
        branding[f"{field}_path"] = str(dest)
        self.store.save_section("branding", branding)
        return self._branding_with_previews(branding)

    def _send_test_email(self, payload: dict) -> dict[str, Any]:
        # TODO(backend): no /api/settings/send-test-email route yet. Wire
        # this to self.api once SMTP sending is implemented server-side.
        to = (payload.get("to") or "").strip()
        if not to:
            return {"ok": False, "message": "Enter a valid email address to send the test to."}
        return {
            "ok": False,
            "message": (
                "Sending a live test email requires a backend SMTP endpoint, "
                "which doesn't exist yet. SMTP settings above are saved "
                "locally in the meantime."
            ),
        }

    def _sync_users(self) -> dict[str, Any]:
        # TODO(backend): no /api/users/sync route yet.
        return {"ok": False, "message": "User sync requires a backend endpoint that doesn't exist yet."}


# ──────────────────────────────────────────────────────────────────────────────
# Public widget
# ──────────────────────────────────────────────────────────────────────────────

class SettingsWidget(QWidget):
    """Top-level widget hosted by MainWindow."""

    def __init__(self, api: ApiClient, session: dict | None = None):
        super().__init__()
        self.api = api
        self.session = session or {}
        self._store = _LocalSettingsStore()

        self.view = QWebEngineView(self)

        self.bridge = _Bridge(api, self.session, self._store)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # qrc:/// base URL lets the page load qrc:///qtwebchannel/qwebchannel.js
        self.view.setHtml(_HTML, QUrl("qrc:///"))

    def reload_ui(self) -> None:
        self.view.setHtml(_HTML, QUrl("qrc:///"))


# ──────────────────────────────────────────────────────────────────────────────
# The UI (self-contained: no CDN needed)
# ──────────────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Settings</title>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;font-size:14px;color:#111827;background:#f0f2f5}
  .page{padding:16px 20px 40px;max-width:1500px;margin:0 auto}
  h1{font-size:19px;margin:0 0 14px;font-weight:700}
  .row{display:grid;grid-template-columns:7fr 5fr;gap:16px}
  @media(max-width:900px){.row{grid-template-columns:1fr}}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
  .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  @media(max-width:760px){.grid2,.grid3,.grid4{grid-template-columns:1fr 1fr}}
  .card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.09);margin-bottom:16px;overflow:hidden}
  .card-h{padding:8px 14px;font-weight:700;font-size:13px;border-bottom:1px solid #eef0f3;background:#fff}
  .card-h.blue{background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff;border:0}
  .card-h.dark{background:#1a1f2e;color:#fff;border:0}
  .card-b{padding:14px}
  .mb{margin-bottom:10px}
  label{display:block;font-weight:600;font-size:12.5px;margin-bottom:3px}
  .req{color:#dc2626}
  input[type=text],input[type=number],input[type=password],input[type=email],select,textarea{
    width:100%;padding:6px 9px;font:inherit;font-size:13px;color:#111827;background:#fff;
    border:1px solid #ced4da;border-radius:5px;outline:none}
  input:focus,select:focus,textarea:focus{border-color:#1976d2;box-shadow:0 0 0 3px rgba(25,118,210,.15)}
  textarea{resize:vertical}
  .hint{font-size:11.5px;color:#6b7280;margin-top:3px;margin-bottom:0}
  .btn{display:inline-block;border:1px solid transparent;border-radius:6px;padding:8px 14px;font:inherit;font-weight:700;font-size:12px;cursor:pointer;text-align:center}
  .btn-primary{background:#1a1f2e;color:#fff}.btn-primary:hover{background:#2563eb}
  .btn-primary:disabled{background:#9ca3af;cursor:default}
  .btn-out{background:#fff;color:#2563eb;border-color:#2563eb}.btn-out:hover{background:#eff6ff}
  .btn-sm{padding:5px 11px;font-size:11px}
  .save-row{display:flex;justify-content:flex-end;margin-top:10px}
  .alert{padding:8px 12px;border-radius:6px;font-size:12px;margin-top:10px}
  .alert-danger{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
  .alert-ok{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
  .alert-warn{background:#fffbeb;color:#92400e;border:1px solid #fde68a}
  /* toggle switch */
  .switch-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .switch{position:relative;display:inline-block;width:38px;height:21px;flex:0 0 auto}
  .switch input{opacity:0;width:0;height:0}
  .slider{position:absolute;cursor:pointer;inset:0;background:#d1d5db;border-radius:21px;transition:.15s}
  .slider:before{position:absolute;content:"";height:15px;width:15px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.15s}
  input:checked + .slider{background:#2563eb}
  input:checked + .slider:before{transform:translateX(17px)}
  .switch-label{font-weight:600;font-size:12.5px;cursor:pointer}
  /* branding file rows */
  .brand-field{text-align:left}
  .brand-preview{width:120px;height:60px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;
    display:flex;align-items:center;justify-content:center;overflow:hidden;margin-bottom:6px;font-size:9px;color:#9ca3af}
  .brand-preview img{max-width:100%;max-height:100%;object-fit:contain}
  .file-btn{display:inline-block;background:#f3f4f6;color:#111827;border:1px solid #d1d5db;border-radius:5px;
    padding:4px 10px;font-size:10.5px;cursor:pointer}
  .file-btn:hover{background:#e5e7eb}
  .file-btn input{display:none}
  .color-swatch{width:60px;height:26px;border:1px solid #d1d5db;border-radius:4px;padding:0;cursor:pointer}
  table.kv{width:100%;font-size:12px;border-collapse:collapse}
  table.kv td{padding:4px 0}
  table.kv td:first-child{color:#6b7280;width:120px}
  .about-line{font-size:12px;color:#374151;margin-bottom:4px}
  .brand-name{font-weight:700;color:#111827}
  .brand-name .ai{color:#f59e0b}
</style>
</head>
<body>

<div class="page">
  <h1>ERP Settings</h1>

  <div class="row">
    <!-- ───────────────────────── Left column ───────────────────────── -->
    <div>

      <!-- 1. Company Configuration -->
      <div class="card">
        <div class="card-h blue">🏢 Company Configuration</div>
        <div class="card-b">
          <div class="grid2 mb" style="grid-template-columns:2fr 1fr">
            <div><label>Company Name <span class="req">*</span></label>
              <input type="text" id="co_name"></div>
            <div><label>Currency</label>
              <select id="co_currency"></select></div>
          </div>
          <div class="grid2 mb">
            <div><label>GSTIN</label><input type="text" id="co_gstin" maxlength="15" placeholder="22AAAAA0000A1Z5"></div>
            <div><label>PAN Number</label><input type="text" id="co_pan" maxlength="10"></div>
          </div>
          <div class="grid2 mb">
            <div><label>MSME Reg. Number</label><input type="text" id="co_msme_reg"></div>
            <div><label>Fiscal Year Start (MM-DD)</label><input type="text" id="co_fiscal_start" placeholder="04-01 (April 1)"></div>
          </div>
          <div class="mb"><label>Timezone</label><select id="co_timezone"></select></div>
          <div class="mb"><label>Company Address</label><textarea id="co_address" rows="2"></textarea></div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_company">💾 Save Settings</button>
          </div>
          <div id="msg_company"></div>
        </div>
      </div>

      <!-- 2. Branding & Document Assets -->
      <div class="card">
        <div class="card-h dark">🎨 Branding &amp; Document Assets</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:12px">
            Logo shows in the sidebar and header. Background colour tints the
            workspace behind every page. Background image and stamp are
            saved here and can be wired into specific pages or printed
            documents (PO/Invoice PDFs, etc.) as those are built out.
            Files save immediately when chosen.
          </p>
          <div class="grid2 mb">
            <div class="brand-field">
              <label>Logo</label>
              <div class="brand-preview" id="pv_logo">No file</div>
              <label class="file-btn">Choose File…<input type="file" id="f_logo" accept="image/*"></label>
              <p class="hint">PNG with transparent background recommended.</p>
            </div>
            <div class="brand-field">
              <label>Stamp / Seal</label>
              <div class="brand-preview" id="pv_stamp">No file</div>
              <label class="file-btn">Choose File…<input type="file" id="f_stamp" accept="image/*"></label>
              <p class="hint">Used to mark documents as approved/authorized once wired into a print view.</p>
            </div>
          </div>
          <div class="grid2">
            <div class="brand-field">
              <label>Background Colour</label>
              <input type="color" id="bg_color" class="color-swatch" value="#f4f6f9">
              <div class="save-row" style="margin-top:8px">
                <button class="btn btn-primary btn-sm" id="save_bgcolor">Save Colour</button>
              </div>
            </div>
            <div class="brand-field">
              <label>Background Image</label>
              <div class="brand-preview" id="pv_bg_image">No file</div>
              <label class="file-btn">Choose File…<input type="file" id="f_bg_image" accept="image/*"></label>
              <p class="hint">Leave empty to use background colour only.</p>
            </div>
          </div>
          <div id="msg_branding"></div>
        </div>
      </div>

      <!-- 3. AI & Notifications -->
      <div class="card">
        <div class="card-h">✨ AI &amp; Notifications</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">
            Powers AI-assisted quote comparison on RFQ screens — sends
            submitted supplier quotes to Google's Gemini API and returns a
            plain-text recommendation. Leave blank to leave that disabled.
          </p>
          <div class="grid2 mb" style="grid-template-columns:2fr 1fr">
            <div><label>Google Gemini API Key</label>
              <input type="password" id="ai_key" placeholder="AIza..." autocomplete="off"></div>
            <div><label>Model</label><input type="text" id="ai_model"></div>
          </div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_ai">💾 Save AI Settings</button>
          </div>
          <div id="msg_ai"></div>
        </div>
      </div>

      <!-- 4. Quality Gate -->
      <div class="card">
        <div class="card-h">🛡️ Quality Gate (GRN &amp; Supplier Invoice)</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">
            Materials flagged <b>QC on GRN</b> in the material master always
            go to Quality Inspection from goods receipt; stock posts only
            after QC accepts. The first switch inspects every goods receipt
            regardless of that flag.
          </p>
          <div class="switch-row">
            <label class="switch"><input type="checkbox" id="qc_all_receipts"><span class="slider"></span></label>
            <label class="switch-label" for="qc_all_receipts">Inspect every goods receipt (not only QC-flagged materials)</label>
          </div>
          <div class="switch-row">
            <label class="switch"><input type="checkbox" id="qc_invoice_grn_check"><span class="slider"></span></label>
            <label class="switch-label" for="qc_invoice_grn_check">Block supplier invoices until the goods receipt is posted and QC-approved</label>
          </div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_quality">💾 Save Quality Settings</button>
          </div>
          <div id="msg_quality"></div>
        </div>
      </div>

      <!-- 5. RFQ Cut-off -->
      <div class="card">
        <div class="card-h">✨ RFQ Cut-off: AI Comparison &amp; Automatic Purchase Order</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">
            When an RFQ reaches its cut-off, the system closes open
            negotiations, runs the AI comparison, selects the best supplier
            and creates one Purchase Order for it, routed to the releasing
            authority.
          </p>
          <div class="switch-row">
            <label class="switch"><input type="checkbox" id="rfq_enabled"><span class="slider"></span></label>
            <label class="switch-label" for="rfq_enabled">Evaluate RFQs automatically at the cut-off</label>
          </div>
          <div class="mb" style="max-width:260px">
            <label>Default auto-compare after (hours)</label>
            <input type="number" min="0" id="rfq_hours">
            <p class="hint">Counted from RFQ creation. 0 = use only the Quote Due By date.</p>
          </div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_rfq">💾 Save Cut-off Settings</button>
          </div>
          <div id="msg_rfq"></div>
        </div>
      </div>

      <!-- 6. Supplier Negotiation -->
      <div class="card">
        <div class="card-h">🏷️ Supplier Negotiation (RFQ)</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">
            Controls automatic reverse-negotiation on RFQs: the moment a
            supplier submits their first quote, the system counter-offers a
            discount, stepping down on each decline until they accept or the
            settings below run out.
          </p>
          <div class="switch-row">
            <label class="switch"><input type="checkbox" id="nego_enabled"><span class="slider"></span></label>
            <label class="switch-label" for="nego_enabled">Auto-negotiate discounts on submitted RFQ quotes</label>
          </div>
          <div class="grid4 mb">
            <div><label>Opening Ask %</label><input type="number" step="0.01" min="0" max="99.99" id="nego_initial"></div>
            <div><label>Step min %</label><input type="number" step="0.01" min="0" id="nego_step_min"></div>
            <div><label>Step max %</label><input type="number" step="0.01" min="0" id="nego_step_max"></div>
            <div><label>Floor %</label><input type="number" step="0.01" min="0" id="nego_floor"></div>
          </div>
          <div style="max-width:160px"><label>Max Rounds</label><input type="number" step="1" min="1" id="nego_rounds"></div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_nego">💾 Save Negotiation Settings</button>
          </div>
          <div id="msg_nego"></div>
        </div>
      </div>

      <!-- 7. Email (SMTP) -->
      <div class="card">
        <div class="card-h">📧 Email (SMTP)</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">
            Routes ERP emails (RFQ invitations, notifications) through a
            real mailbox. For Gmail: enable 2-Step Verification, then
            generate an App Password and paste it below — not the normal
            account password.
          </p>
          <div class="switch-row">
            <label class="switch"><input type="checkbox" id="smtp_enabled"><span class="slider"></span></label>
            <label class="switch-label" for="smtp_enabled">Enable SMTP (uncheck to use the server's default mail)</label>
          </div>
          <div class="grid2 mb">
            <div><label>SMTP Host</label><input type="text" id="smtp_host"></div>
            <div><label>Port</label><input type="number" id="smtp_port"></div>
          </div>
          <div class="grid2 mb">
            <div><label>Encryption</label>
              <select id="smtp_encryption">
                <option value="tls">TLS (port 587)</option>
                <option value="ssl">SSL (port 465)</option>
              </select></div>
            <div><label>Gmail Address (SMTP Username)</label><input type="email" id="smtp_username" placeholder="you@gmail.com"></div>
          </div>
          <div class="grid2 mb">
            <div><label>App Password</label><input type="password" id="smtp_password" autocomplete="off"></div>
            <div><label>"From" Email <span class="hint" style="display:inline">(optional)</span></label><input type="email" id="smtp_from_email"></div>
          </div>
          <div class="mb" style="max-width:50%"><label>"From" Name <span class="hint" style="display:inline">(optional)</span></label><input type="text" id="smtp_from_name"></div>
          <div class="save-row">
            <button class="btn btn-primary" id="save_smtp">💾 Save SMTP Settings</button>
          </div>
          <div id="msg_smtp"></div>
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:14px 0">
          <div class="grid2" style="align-items:end">
            <div><label>Send a test email to</label><input type="email" id="test_email_to" placeholder="you@example.com"></div>
            <div><button class="btn btn-out" id="send_test" style="width:100%">✉️ Send Test Email</button></div>
          </div>
          <div id="msg_test"></div>
        </div>
      </div>

    </div>

    <!-- ───────────────────────── Right column ───────────────────────── -->
    <div>

      <!-- 8. Database Status -->
      <div class="card">
        <div class="card-h" style="background:#15803d;color:#fff">🗄️ Database Status</div>
        <div class="card-b">
          <table class="kv">
            <tr><td>Connection:</td><td id="db_conn">—</td></tr>
            <tr><td>Backend:</td><td id="db_backend">—</td></tr>
            <tr><td>Company:</td><td id="db_company">—</td></tr>
            <tr><td>User:</td><td id="db_user">—</td></tr>
          </table>
          <p class="hint" style="margin-top:10px">
            Full table/row diagnostics require a backend status endpoint
            (not implemented yet) — this card currently reflects only what
            the frontend already knows from the active session.
          </p>
        </div>
      </div>

      <!-- 9. ERP Users Sync -->
      <div class="card">
        <div class="card-h">🔐 ERP Users Sync</div>
        <div class="card-b">
          <p class="hint" style="margin-bottom:10px">Sync platform users into ERP user records.</p>
          <button class="btn btn-out" id="sync_users">🔄 Sync Users to ERP</button>
          <div id="msg_sync"></div>
        </div>
      </div>

      <!-- 10. About -->
      <div class="card">
        <div class="card-h">ℹ️ About</div>
        <div class="card-b">
          <div class="about-line brand-name">PRIGAG BR<span class="ai">AI</span>N</div>
          <div class="about-line">Developed by: Prigag Online Pvt. Ltd.</div>
          <div class="about-line">Website: <a href="#" onclick="return false">brain.prigag.com</a></div>
          <div class="about-line">Support: <a href="#" onclick="return false">support@prigag.com</a></div>
        </div>
      </div>

    </div>
  </div>
</div>

<script>
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var bridge, pending = {}, seq = 0;

  function call(method, payload){
    return new Promise(function(resolve){
      var id = String(++seq);
      pending[id] = resolve;
      bridge.call(id, method, JSON.stringify(payload || {}));
    });
  }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function banner(el, text, kind){
    if (!el) return;
    el.innerHTML = text ? '<div class="alert alert-' + kind + '">' + esc(text) + '</div>' : '';
    if (text) setTimeout(function(){ el.innerHTML = ''; }, 4000);
  }
  function val(id){ return $(id).value; }
  function num(id){ return parseFloat($(id).value || 0) || 0; }
  function checked(id){ return !!$(id).checked; }

  var CURRENCIES = [
    ['INR','Indian Rupee'], ['USD','US Dollar'], ['EUR','Euro'],
    ['GBP','British Pound'], ['AED','UAE Dirham'], ['SGD','Singapore Dollar']
  ];
  var TIMEZONES = ['Asia/Kolkata','Asia/Dubai','Asia/Singapore','America/New_York','Europe/London','UTC'];

  function fillSelect(sel, items){
    sel.innerHTML = items.map(function(o){
      return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + '</option>';
    }).join('');
  }
  fillSelect($('co_currency'), CURRENCIES.map(function(c){ return [c[0], c[0] + ' — ' + c[1]]; }));
  fillSelect($('co_timezone'), TIMEZONES.map(function(t){ return [t, t]; }));

  /* ── render helpers ───────────────────────────────── */
  function setPreview(el, dataUri){
    if (dataUri){ el.innerHTML = '<img src="' + dataUri + '">'; }
    else { el.textContent = 'No file'; }
  }

  function applySettings(s, session, apiBaseUrl){
    var co = s.company || {};
    $('co_name').value = co.name || session.company_name || '';
    $('co_currency').value = co.currency_code || 'INR';
    $('co_gstin').value = co.gstin || '';
    $('co_pan').value = co.pan_number || '';
    $('co_msme_reg').value = co.msme_reg_number || '';
    $('co_fiscal_start').value = co.fiscal_start || '04-01';
    $('co_timezone').value = co.timezone || 'Asia/Kolkata';
    $('co_address').value = co.address || '';

    var br = s.branding || {};
    $('bg_color').value = br.bg_color || '#f4f6f9';
    setPreview($('pv_logo'), br.logo_preview);
    setPreview($('pv_stamp'), br.stamp_preview);
    setPreview($('pv_bg_image'), br.bg_image_preview);

    var ai = s.ai || {};
    $('ai_key').value = ai.gemini_api_key || '';
    $('ai_model').value = ai.gemini_model || 'gemini-2.5-flash';

    var qg = s.quality_gate || {};
    $('qc_all_receipts').checked = !!qg.qc_all_receipts;
    $('qc_invoice_grn_check').checked = qg.invoice_grn_check !== undefined ? !!qg.invoice_grn_check : true;

    var rfq = s.rfq_cutoff || {};
    $('rfq_enabled').checked = !!rfq.enabled;
    $('rfq_hours').value = rfq.hours || 0;

    var ng = s.negotiation || {};
    $('nego_enabled').checked = !!ng.enabled;
    $('nego_initial').value = ng.initial_pct != null ? ng.initial_pct : 20;
    $('nego_step_min').value = ng.step_min != null ? ng.step_min : 2;
    $('nego_step_max').value = ng.step_max != null ? ng.step_max : 3;
    $('nego_floor').value = ng.floor_pct != null ? ng.floor_pct : 0;
    $('nego_rounds').value = ng.max_rounds != null ? ng.max_rounds : 15;

    var sm = s.smtp || {};
    $('smtp_enabled').checked = !!sm.enabled;
    $('smtp_host').value = sm.host || 'smtp.gmail.com';
    $('smtp_port').value = sm.port || 587;
    $('smtp_encryption').value = sm.encryption || 'tls';
    $('smtp_username').value = sm.username || '';
    $('smtp_password').value = '';
    $('smtp_password').placeholder = sm.password
      ? '•••••••••••••••• (saved — leave blank to keep)' : '16-character app password';
    $('smtp_from_email').value = sm.from_email || '';
    $('smtp_from_name').value = sm.from_name || '';

    $('db_conn').innerHTML = '<span style="color:#15803d;font-weight:700">● Connected via FastAPI backend</span>';
    $('db_backend').textContent = apiBaseUrl || '—';
    $('db_company').textContent = session.company_name || '—';
    $('db_user').textContent = session.full_name || '—';
  }

  /* ── section saves ────────────────────────────────── */
  function saveSection(section, data, msgEl, label){
    return call('save_section', {section: section, data: data}).then(function(r){
      if (!r.ok){ banner(msgEl, r.error, 'danger'); return; }
      banner(msgEl, (label || 'Settings') + ' saved successfully.', 'ok');
      return r.data;
    });
  }

  $('save_company').addEventListener('click', function(){
    if (!val('co_name').trim()){ banner($('msg_company'), 'Company Name is required.', 'danger'); return; }
    saveSection('company', {
      name: val('co_name').trim(), currency_code: val('co_currency'),
      gstin: val('co_gstin').trim(), pan_number: val('co_pan').trim(),
      msme_reg_number: val('co_msme_reg').trim(),
      fiscal_start: val('co_fiscal_start').trim() || '04-01',
      timezone: val('co_timezone'), address: val('co_address').trim()
    }, $('msg_company'));
  });

  $('save_bgcolor').addEventListener('click', function(){
    saveSection('branding', {bg_color: val('bg_color')}, $('msg_branding'), 'Background colour')
      .then(function(br){ if (br) applySettings({branding: br}, {}, null); });
  });

  $('save_ai').addEventListener('click', function(){
    saveSection('ai', {
      gemini_api_key: val('ai_key').trim(),
      gemini_model: val('ai_model').trim() || 'gemini-2.5-flash'
    }, $('msg_ai'), 'AI settings');
  });

  $('save_quality').addEventListener('click', function(){
    saveSection('quality_gate', {
      qc_all_receipts: checked('qc_all_receipts'),
      invoice_grn_check: checked('qc_invoice_grn_check')
    }, $('msg_quality'), 'Quality settings');
  });

  $('save_rfq').addEventListener('click', function(){
    saveSection('rfq_cutoff', {
      enabled: checked('rfq_enabled'),
      hours: parseInt(val('rfq_hours') || 0, 10) || 0
    }, $('msg_rfq'), 'Cut-off settings');
  });

  $('save_nego').addEventListener('click', function(){
    var stepMin = num('nego_step_min'), stepMax = Math.max(stepMin, num('nego_step_max'));
    var floor = Math.min(num('nego_initial'), num('nego_floor'));
    saveSection('negotiation', {
      enabled: checked('nego_enabled'), initial_pct: num('nego_initial'),
      step_min: stepMin, step_max: stepMax, floor_pct: floor,
      max_rounds: parseInt(val('nego_rounds') || 1, 10) || 1
    }, $('msg_nego'), 'Negotiation settings');
  });

  $('save_smtp').addEventListener('click', function(){
    saveSection('smtp', {
      enabled: checked('smtp_enabled'), host: val('smtp_host').trim() || 'smtp.gmail.com',
      port: parseInt(val('smtp_port') || 587, 10) || 587, encryption: val('smtp_encryption'),
      username: val('smtp_username').trim(), password: val('smtp_password').trim(),
      from_email: val('smtp_from_email').trim(), from_name: val('smtp_from_name').trim()
    }, $('msg_smtp'), 'SMTP settings').then(function(){ $('smtp_password').value = ''; });
  });

  $('send_test').addEventListener('click', function(){
    var to = val('test_email_to').trim();
    if (!to){ banner($('msg_test'), 'Enter a valid email address to send the test to.', 'danger'); return; }
    call('send_test_email', {to: to}).then(function(r){
      if (!r.ok){ banner($('msg_test'), r.error, 'danger'); return; }
      var d = r.data || {};
      banner($('msg_test'), d.message, d.ok ? 'ok' : 'warn');
    });
  });

  $('sync_users').addEventListener('click', function(){
    call('sync_users').then(function(r){
      if (!r.ok){ banner($('msg_sync'), r.error, 'danger'); return; }
      var d = r.data || {};
      banner($('msg_sync'), d.message, d.ok ? 'ok' : 'warn');
    });
  });

  /* ── branding file uploads (save immediately on choose) ───────────── */
  function wireFileInput(inputId, previewId, field, msgEl){
    $(inputId).addEventListener('change', function(){
      var file = this.files && this.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function(){
        call('save_branding_file', {field: field, filename: file.name, data: reader.result}).then(function(r){
          if (!r.ok){ banner(msgEl, r.error, 'danger'); return; }
          applySettings({branding: r.data}, {}, null);
          banner(msgEl, 'Image saved.', 'ok');
        });
      };
      reader.readAsDataURL(file);
    });
  }
  wireFileInput('f_logo', 'pv_logo', 'logo', $('msg_branding'));
  wireFileInput('f_stamp', 'pv_stamp', 'stamp', $('msg_branding'));
  wireFileInput('f_bg_image', 'pv_bg_image', 'bg_image', $('msg_branding'));

  /* ── start ────────────────────────────────────────── */
  new QWebChannel(qt.webChannelTransport, function(ch){
    bridge = ch.objects.bridge;
    bridge.response.connect(function(id, js){
      var cb = pending[id]; delete pending[id];
      if (cb) cb(JSON.parse(js));
    });
    call('bootstrap').then(function(r){
      if (!r.ok){ banner($('msg_company'), 'Could not load settings: ' + r.error, 'danger'); return; }
      var d = r.data || {};
      applySettings(d.settings || {}, d.session || {}, d.api_base_url);
    });
  });
})();
</script>
</body>
</html>
"""