"""
Brain ERP - Supplier Master Widget (XK01 / XK02).

Rewritten to render the exact Bootstrap-based layout used by
supplier_create.php inside a QWebEngineView, instead of native Qt
widgets. Python <-> JavaScript communication goes through a
QWebChannel bridge object that exposes the same operations the old
widget used: create a supplier, list/search suppliers, and toggle
active/blocked status.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..api_client import ApiClient, ApiError


# ──────────────────────────────────────────────────────────────────────
# JS <-> Python bridge
# ──────────────────────────────────────────────────────────────────────
class SupplierBridge(QObject):
    """Exposed to the page's JavaScript as `bridge` via QWebChannel."""

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

    @pyqtSlot(str, result=str)
    def create_supplier(self, payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError) as e:
            return json.dumps({"ok": False, "error": f"Bad payload: {e}"})

        name = (payload.get("supplier_name") or "").strip()
        if not name:
            return json.dumps({"ok": False, "error": "Supplier Name is required."})

        try:
            res = self.api.supplier_create(payload)
            sup_code = (res.get("data") or {}).get("supplier_code") or "Supplier"
            return json.dumps({"ok": True, "supplier_code": sup_code})
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def list_suppliers(self, search: str) -> str:
        try:
            sups = self.api.supplier_list(search=search.strip())
            if not isinstance(sups, list):
                sups = []
            clean = [s for s in sups if isinstance(s, dict)]
            return json.dumps({"ok": True, "data": clean})
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e), "data": []})

    @pyqtSlot(int, result=str)
    def toggle_supplier(self, supplier_id: int) -> str:
        try:
            self.api.supplier_toggle(supplier_id)
            return json.dumps({"ok": True})
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})


# ──────────────────────────────────────────────────────────────────────
# HTML / CSS / JS — mirrors the layout & styling of supplier_create.php
# (XK01/XK02 block): gradient card header, bordered section labels with
# bootstrap-icons, two-column "form + directory table" layout, sticky
# orange table header, live client-side search.
# ──────────────────────────────────────────────────────────────────────
_PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XK01 - Create Supplier</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
<style>
  body { background:#f8fafc; color:#111827; font-size:14px; }
  .card { border:0; }
  .shadow-sm { box-shadow:0 1px 3px rgba(0,0,0,.08) !important; }
  .form-control-sm, .form-select-sm { font-size:.82rem; }
  .form-label.small { font-size:.75rem; margin-bottom:2px; }
  .section-label {
    font-size:.78rem; font-weight:700; color:#2563eb;
    border-bottom:1px solid #e5e7eb; padding-bottom:4px; margin-bottom:8px;
  }
  .supp-table thead th { position:sticky; top:0; background:#e65100; color:#fff; }
  .supp-table { font-size:.8rem; }
  .supp-row.editing { background:#fff8e1 !important; }
  #toast-box { position:fixed; top:14px; right:14px; z-index:2000; min-width:260px; }
  .scroll-pane { max-height:calc(100vh - 150px); overflow-y:auto; }
</style>
</head>
<body>

<div id="toast-box"></div>

<div class="container-fluid p-3">
  <div class="row g-3">

    <!-- LEFT: Create Supplier form -->
    <div class="col-xl-5 col-lg-6">
      <div class="card shadow-sm">
        <div class="card-header fw-bold small py-2 text-white"
             style="background:linear-gradient(135deg,#e65100,#f57c00)">
          <i class="bi bi-truck me-2"></i>New Supplier
        </div>
        <div class="card-body p-3">
          <form id="supplier-form" autocomplete="off">

            <div class="section-label"><i class="bi bi-person-vcard me-1"></i>Basic Information</div>
            <div class="row g-2 mb-2">
              <div class="col-5">
                <label class="form-label fw-semibold small">Supplier Code</label>
                <input type="text" id="f-supplier_code" class="form-control form-control-sm text-uppercase fw-bold"
                       placeholder="Auto (SUP000001)">
              </div>
              <div class="col-7">
                <label class="form-label fw-semibold small">Company / Name <span class="text-danger">*</span></label>
                <input type="text" id="f-supplier_name" class="form-control form-control-sm" required>
              </div>
            </div>
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label fw-semibold small">Contact Person</label>
                <input type="text" id="f-contact_person" class="form-control form-control-sm">
              </div>
              <div class="col-6">
                <label class="form-label fw-semibold small">Phone</label>
                <input type="tel" id="f-phone" class="form-control form-control-sm">
              </div>
            </div>
            <div class="row g-2 mb-3">
              <div class="col-6">
                <label class="form-label fw-semibold small">Email</label>
                <input type="email" id="f-email" class="form-control form-control-sm">
              </div>
              <div class="col-6">
                <label class="form-label fw-semibold small">Supplier Type</label>
                <select id="f-supplier_type" class="form-select form-select-sm">
                  <option>DOMESTIC</option>
                  <option>IMPORT</option>
                  <option>SERVICE</option>
                  <option>SUBCONTRACT</option>
                </select>
              </div>
            </div>

            <div class="section-label"><i class="bi bi-geo-alt me-1"></i>Address</div>
            <div class="mb-2">
              <label class="form-label fw-semibold small">Street Address</label>
              <input type="text" id="f-address" class="form-control form-control-sm">
            </div>
            <div class="row g-2 mb-3">
              <div class="col-5">
                <label class="form-label fw-semibold small">City</label>
                <input type="text" id="f-city" class="form-control form-control-sm">
              </div>
              <div class="col-4">
                <label class="form-label fw-semibold small">State</label>
                <select id="f-state" class="form-select form-select-sm">
                  <option value="">-</option>
                  <option>Andhra Pradesh</option><option>Arunachal Pradesh</option><option>Assam</option>
                  <option>Bihar</option><option>Chhattisgarh</option><option>Goa</option><option>Gujarat</option>
                  <option>Haryana</option><option>Himachal Pradesh</option><option>Jharkhand</option>
                  <option>Karnataka</option><option>Kerala</option><option>Madhya Pradesh</option>
                  <option>Maharashtra</option><option>Manipur</option><option>Meghalaya</option>
                  <option>Mizoram</option><option>Nagaland</option><option>Odisha</option><option>Punjab</option>
                  <option>Rajasthan</option><option>Sikkim</option><option>Tamil Nadu</option>
                  <option>Telangana</option><option>Tripura</option><option>Uttar Pradesh</option>
                  <option>Uttarakhand</option><option>West Bengal</option><option>Delhi</option>
                  <option>Jammu &amp; Kashmir</option><option>Ladakh</option><option>Puducherry</option>
                  <option>Chandigarh</option><option>Andaman &amp; Nicobar</option>
                </select>
              </div>
              <div class="col-3">
                <label class="form-label fw-semibold small">Pincode</label>
                <input type="text" id="f-pincode" class="form-control form-control-sm" maxlength="6">
              </div>
            </div>

            <div class="section-label"><i class="bi bi-receipt-cutoff me-1"></i>Tax &amp; Compliance</div>
            <div class="row g-2 mb-2">
              <div class="col-7">
                <label class="form-label fw-semibold small">GSTIN</label>
                <input type="text" id="f-gstin" class="form-control form-control-sm text-uppercase"
                       maxlength="15" placeholder="22AAAAA0000A1Z5">
              </div>
              <div class="col-5">
                <label class="form-label fw-semibold small">PAN</label>
                <input type="text" id="f-pan" class="form-control form-control-sm text-uppercase"
                       maxlength="10" placeholder="ABCDE1234F">
              </div>
            </div>
            <div class="row g-2 mb-3">
              <div class="col-6">
                <label class="form-label fw-semibold small">Payment Terms</label>
                <select id="f-payment_terms" class="form-select form-select-sm">
                  <option>Net 30</option><option>Net 45</option><option>Net 60</option>
                  <option>Net 90</option><option>Advance</option><option>Immediate</option>
                </select>
              </div>
              <div class="col-6">
                <label class="form-label fw-semibold small">Credit Days</label>
                <input type="number" id="f-credit_days" class="form-control form-control-sm" min="0" value="30">
              </div>
            </div>

            <div class="section-label"><i class="bi bi-bank me-1"></i>Bank Details</div>
            <div class="row g-2 mb-3">
              <div class="col-12">
                <label class="form-label fw-semibold small">Bank Name</label>
                <input type="text" id="f-bank_name" class="form-control form-control-sm" placeholder="HDFC / SBI / ICICI">
              </div>
              <div class="col-7">
                <label class="form-label fw-semibold small">Account Number</label>
                <input type="text" id="f-bank_account_number" class="form-control form-control-sm">
              </div>
              <div class="col-5">
                <label class="form-label fw-semibold small">IFSC Code</label>
                <input type="text" id="f-bank_ifsc" class="form-control form-control-sm text-uppercase" placeholder="HDFC0001234">
              </div>
            </div>

            <div class="d-grid gap-2">
              <button type="submit" class="btn btn-warning text-dark fw-semibold">
                <i class="bi bi-save me-1"></i>Save Supplier Master
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- RIGHT: Supplier Directory -->
    <div class="col-xl-7 col-lg-6">
      <div class="card shadow-sm">
        <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
          <span class="fw-bold small">
            <i class="bi bi-list-ul me-2"></i>All Suppliers
            <span id="supp-count" class="badge bg-secondary">0</span>
          </span>
          <div class="d-flex gap-2">
            <input type="text" id="supp-search" class="form-control form-control-sm" style="max-width:200px" placeholder="Search...">
            <button id="supp-refresh" type="button" class="btn btn-sm btn-outline-secondary" title="Refresh">
              <i class="bi bi-arrow-clockwise"></i>
            </button>
          </div>
        </div>
        <div class="card-body p-0 scroll-pane">
          <table class="table table-hover table-sm mb-0 supp-table">
            <thead>
              <tr>
                <th class="px-3 fw-semibold">Code</th>
                <th class="fw-semibold">Name</th>
                <th class="fw-semibold">City</th>
                <th class="fw-semibold">GSTIN</th>
                <th class="fw-semibold text-center">Credit</th>
                <th class="fw-semibold text-center">Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="supp-tbody">
              <tr><td colspan="7" class="text-center text-muted py-5">
                <i class="bi bi-truck display-6 d-block mb-2 opacity-25"></i>Loading suppliers...
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

  </div>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
  let bridge = null;
  let lastRows = [];

  function toast(msg, ok) {
    const box = document.getElementById('toast-box');
    const el = document.createElement('div');
    el.className = 'alert ' + (ok ? 'alert-success' : 'alert-danger') + ' shadow-sm py-2 px-3 mb-2';
    el.innerText = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  function escapeHtml(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function statusBadge(active) {
    return active
      ? '<span class="badge bg-success small">ACTIVE</span>'
      : '<span class="badge bg-danger small">BLOCKED</span>';
  }

  function renderRows(rows) {
    lastRows = rows;
    const tbody = document.getElementById('supp-tbody');
    document.getElementById('supp-count').innerText = rows.length;

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-5">' +
        '<i class="bi bi-truck display-6 d-block mb-2 opacity-25"></i>No suppliers yet.</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(s => {
      const id = s.id ?? '';
      const code = s.supplier_code ?? s.code ?? '';
      const name = s.supplier_name ?? s.name ?? '';
      const phone = s.phone ?? '';
      const city = s.city ?? '\u2014';
      const gstin = s.gstin ?? '\u2014';
      const creditDays = (s.credit_days ?? s.credit_days === 0) ? s.credit_days : '-';
      const active = s.is_active === undefined ? true : !!s.is_active;
      return (
        '<tr class="supp-row" data-id="' + escapeHtml(id) + '">' +
          '<td class="px-3 fw-bold text-primary">' + escapeHtml(code) + '</td>' +
          '<td><div class="fw-semibold">' + escapeHtml(name) + '</div>' +
          (phone ? '<div class="text-muted" style="font-size:.7rem">' + escapeHtml(phone) + '</div>' : '') + '</td>' +
          '<td class="text-muted">' + escapeHtml(city) + '</td>' +
          '<td style="font-size:.7rem;font-family:monospace">' + escapeHtml(gstin) + '</td>' +
          '<td class="text-center small">' + escapeHtml(creditDays) + (creditDays !== '-' ? 'd' : '') + '</td>' +
          '<td class="text-center">' + statusBadge(active) + '</td>' +
          '<td>' +
            '<button type="button" class="btn btn-xs btn-outline-secondary py-0 px-1 toggle-btn" ' +
            'data-id="' + escapeHtml(id) + '" title="Toggle Active/Block">' +
            '<i class="bi bi-toggle2-on"></i></button>' +
          '</td>' +
        '</tr>'
      );
    }).join('');

    document.querySelectorAll('.toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => toggleSupplier(parseInt(btn.dataset.id, 10)));
    });
  }

  function loadSuppliers(search) {
    if (!bridge) return;
    bridge.list_suppliers(search || '', function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) {
        renderRows(res.data);
      } else {
        toast('Could not load suppliers: ' + res.error, false);
        renderRows([]);
      }
    });
  }

  function toggleSupplier(id) {
    if (!bridge || !id) return;
    bridge.toggle_supplier(id, function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) {
        loadSuppliers(document.getElementById('supp-search').value);
      } else {
        toast('Could not toggle supplier status: ' + res.error, false);
      }
    });
  }

  function gatherPayload() {
    return {
      supplier_code: document.getElementById('f-supplier_code').value.trim(),
      supplier_name: document.getElementById('f-supplier_name').value.trim(),
      contact_person: document.getElementById('f-contact_person').value.trim(),
      phone: document.getElementById('f-phone').value.trim(),
      email: document.getElementById('f-email').value.trim(),
      supplier_type: document.getElementById('f-supplier_type').value,
      address: document.getElementById('f-address').value.trim(),
      city: document.getElementById('f-city').value.trim(),
      state: document.getElementById('f-state').value,
      pincode: document.getElementById('f-pincode').value.trim(),
      gstin: document.getElementById('f-gstin').value.trim().toUpperCase(),
      pan: document.getElementById('f-pan').value.trim().toUpperCase(),
      payment_terms: document.getElementById('f-payment_terms').value,
      credit_days: parseInt(document.getElementById('f-credit_days').value || '0', 10),
      bank_name: document.getElementById('f-bank_name').value.trim(),
      bank_account_number: document.getElementById('f-bank_account_number').value.trim(),
      bank_ifsc: document.getElementById('f-bank_ifsc').value.trim().toUpperCase()
    };
  }

  function resetForm() {
    document.getElementById('supplier-form').reset();
    document.getElementById('f-credit_days').value = 30;
  }

  document.getElementById('supplier-form').addEventListener('submit', function (e) {
    e.preventDefault();
    const payload = gatherPayload();
    if (!payload.supplier_name) {
      toast('Supplier Name is required.', false);
      return;
    }
    bridge.create_supplier(JSON.stringify(payload), function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) {
        toast('Supplier ' + res.supplier_code + ' created successfully!', true);
        resetForm();
        loadSuppliers(document.getElementById('supp-search').value);
      } else {
        toast('Failed to create supplier: ' + res.error, false);
      }
    });
  });

  document.getElementById('supp-search').addEventListener('input', function () {
    const q = this.value.toLowerCase();
    document.querySelectorAll('.supp-row').forEach(r => {
      r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  document.getElementById('supp-refresh').addEventListener('click', function () {
    loadSuppliers(document.getElementById('supp-search').value);
  });

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;
    loadSuppliers('');
  });
</script>
</body>
</html>
"""


class SupplierWidget(QWidget):
    """Supplier Master (XK01 / XK02) rendered via QWebEngineView."""

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        layout.addWidget(self.view)

        # Python <-> JS bridge
        self.bridge = SupplierBridge(self.api)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.view.setHtml(_PAGE_HTML, QUrl("qrc:/"))

    def reload_list(self) -> None:
        """Kept for callers that used to refresh the old native table."""
        self.view.page().runJavaScript(
            "loadSuppliers(document.getElementById('supp-search').value);"
        )