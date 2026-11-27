"""
Brain ERP – Create Purchase Requisition screen (ME51N)  •  QWebEngineView.
Split out of the old combined PRWidget per request — this file is Create only.
See pr_list_widget.py for the Requisitions List / Approval screen (ME53N).

Bridge (window.backend): list_items() -> api.mm_list(), create_pr(json) -> api.pr_create().
on_created(pr_number) callback fires after a successful submit, so main_window.py can tell
the ME53N list screen to refresh (same wiring pattern as mm01_widget's on_created).
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ..api_client import ApiClient, ApiError


class _Backend(QObject):

    def __init__(self, api: ApiClient, on_created: Optional[Callable[[str], None]]):
        super().__init__()
        self.api = api
        self._on_created = on_created

    @pyqtSlot(result=str)
    def list_items(self) -> str:
        try:
            items = self.api.mm_list()
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e), "items": []})
        return json.dumps({"ok": True, "items": items})

    @pyqtSlot(str, result=str)
    def create_pr(self, payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
        except ValueError:
            return json.dumps({"ok": False, "error": "Malformed form data."})
        try:
            res = self.api.pr_create(payload)
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})
        pr_num = (res.get("data") or {}).get("pr_number") or "PR"
        if self._on_created:
            self._on_created(pr_num)
        return json.dumps({"ok": True, "message": f"Requisition {pr_num} created!", "pr_number": pr_num})


_PAGE_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  body { background:#f8fafc; color:#111827; font-size:.88rem; padding:16px; }
  .card { color:#111827; }
  .grad-header { background:linear-gradient(135deg,#1565c0,#1976d2); }
</style>
</head>
<body>

<h5 class="fw-bold mb-3">ME51N &middot; Create Purchase Requisition</h5>
<div id="alert-box"></div>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-header fw-bold small py-2 text-white grad-header"><i class="bi bi-file-earmark-text me-2"></i>Requisition Header</div>
  <div class="card-body p-3">
    <div class="row g-2">
      <div class="col-md-4">
        <label class="form-label small fw-semibold mb-1">Required By Date *</label>
        <input type="date" id="req-date" class="form-control form-control-sm">
      </div>
      <div class="col-md-4">
        <label class="form-label small fw-semibold mb-1">Requesting Dept *</label>
        <input id="req-dept" class="form-control form-control-sm" placeholder="e.g. Production / Maintenance / Stores">
      </div>
      <div class="col-md-4">
        <label class="form-label small fw-semibold mb-1">Purpose / Justification</label>
        <input id="req-purpose" class="form-control form-control-sm" placeholder="Reason for requisition">
      </div>
    </div>
  </div>
</div>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list-check me-2 text-primary"></i>Requested Materials</div>
  <div class="card-body p-3">
    <table class="table table-sm mb-2">
      <thead class="table-light"><tr><th>Material Item *</th><th style="width:140px">Requested Qty *</th><th style="width:160px">Estimated Price</th><th style="width:140px" class="text-end">Subtotal</th><th style="width:40px"></th></tr></thead>
      <tbody id="pr-lines"></tbody>
    </table>
    <button class="btn btn-sm btn-primary fw-semibold" onclick="addLine()"><i class="bi bi-plus-lg me-1"></i>Add Material Line</button>
  </div>
</div>

<div class="d-flex align-items-center gap-3">
  <button class="btn btn-success fw-bold px-4 py-2" onclick="submitPR()"><i class="bi bi-save me-2"></i>Submit Purchase Requisition</button>
  <span class="fw-bold" id="pr-total">Estimated Total: &#8377;0.00</span>
  <span id="pr-status" class="fw-bold"></span>
</div>

<script>
let backend = null;
let items = [];

new QWebChannel(qt.webChannelTransport, function(channel) {
  backend = channel.objects.backend;
  document.getElementById('req-date').value = new Date().toISOString().slice(0,10);
  loadItems();
  addLine();
});

function esc(v) { return (v===null||v===undefined?'':String(v)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function flash(msg, type) {
  const box = document.getElementById('alert-box');
  box.innerHTML = '<div class="alert alert-' + type + ' small py-2">' + esc(msg) + '</div>';
  setTimeout(() => { box.innerHTML = ''; }, 4000);
}

function loadItems() {
  backend.list_items(function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'warning'); return; }
    items = data.items || [];
  });
}

function itemOptions() {
  return items.map(it => '<option value="' + it.id + '">' + esc(it.item_code) + ' \u2014 ' + esc(it.item_name) + '</option>').join('');
}

function addLine() {
  const tbody = document.getElementById('pr-lines');
  const tr = document.createElement('tr');
  tr.innerHTML =
    '<td><select class="form-select form-select-sm line-item"><option value="">\u2014 select \u2014</option>' + itemOptions() + '</select></td>'
    + '<td><input type="number" class="form-control form-control-sm line-qty" min="0.01" step="0.01" value="1"></td>'
    + '<td><input type="number" class="form-control form-control-sm line-price" min="0" step="0.01" value="0"></td>'
    + '<td class="text-end fw-bold line-sub">0.00</td>'
    + '<td><button class="btn btn-sm btn-outline-danger py-0" onclick="this.closest(\'tr\').remove(); calcTotals();"><i class="bi bi-trash"></i></button></td>';
  tr.querySelector('.line-qty').addEventListener('input', calcTotals);
  tr.querySelector('.line-price').addEventListener('input', calcTotals);
  tbody.appendChild(tr);
  calcTotals();
}

function calcTotals() {
  let total = 0;
  document.querySelectorAll('#pr-lines tr').forEach(tr => {
    const qty = parseFloat(tr.querySelector('.line-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.line-price').value) || 0;
    const sub = qty * price;
    tr.querySelector('.line-sub').textContent = sub.toFixed(2);
    total += sub;
  });
  document.getElementById('pr-total').textContent = 'Estimated Total: \u20b9' + total.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
}

function submitPR() {
  const dept = document.getElementById('req-dept').value.trim();
  if (!dept) { flash('Please enter the requesting department.', 'danger'); return; }

  const lines = [];
  document.querySelectorAll('#pr-lines tr').forEach(tr => {
    const itemId = parseInt(tr.querySelector('.line-item').value || '0', 10);
    const qty = parseFloat(tr.querySelector('.line-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.line-price').value) || 0;
    if (itemId && qty > 0) lines.push({item_id: itemId, qty: qty, estimated_cost: price});
  });
  if (!lines.length) { flash('Add at least one line item.', 'danger'); return; }

  const payload = {
    department: dept,
    required_date: document.getElementById('req-date').value,
    purpose: document.getElementById('req-purpose').value.trim(),
    lines: lines,
  };

  backend.create_pr(JSON.stringify(payload), function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'danger'); return; }
    document.getElementById('pr-status').className = 'fw-bold text-success';
    document.getElementById('pr-status').textContent = '\u2713 ' + data.message;
    document.getElementById('pr-lines').innerHTML = '';
    addLine();
    document.getElementById('req-dept').value = '';
    document.getElementById('req-purpose').value = '';
  });
}
</script>
</body>
</html>
"""


class CreatePRWidget(QWidget):
    """Create PR (ME51N) only. Pass on_created to be notified (pr_number) after a successful
    submit — wire it to PRListWidget.reload in main_window.py so the list stays in sync."""

    def __init__(self, api: ApiClient, on_created: Optional[Callable[[str], None]] = None):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        self.channel = QWebChannel()
        self.backend = _Backend(api, on_created)
        self.channel.registerObject("backend", self.backend)
        self.view.page().setWebChannel(self.channel)
        self.view.setHtml(_PAGE_HTML, baseUrl=QUrl("qrc:///"))

        layout.addWidget(self.view)