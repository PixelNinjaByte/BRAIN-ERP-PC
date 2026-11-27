"""
Brain ERP – Purchase Requisitions List & Approval screen (ME53N)  •  QWebEngineView.
Split out of the old combined PRWidget per request — this file is List/Approve only.
See pr_create_widget.py for the Create screen (ME51N).

Bridge (window.backend): list_prs(status) -> api.pr_list(), approve_pr(id) -> api.pr_action().
Call .reload() (e.g. from main_window.py after CreatePRWidget's on_created fires) to refresh
the table after a PR is created elsewhere.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ..api_client import ApiClient, ApiError


class _Backend(QObject):

    prsChanged = pyqtSignal()

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

    @pyqtSlot(str, result=str)
    def list_prs(self, status: str) -> str:
        try:
            prs = self.api.pr_list(status=status or "")
            if not isinstance(prs, list):
                prs = []
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e), "prs": []})
        return json.dumps({"ok": True, "prs": prs})

    @pyqtSlot(int, result=str)
    def approve_pr(self, pr_id: int) -> str:
        try:
            self.api.pr_action(pr_id, "APPROVE")
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})
        self.prsChanged.emit()
        return json.dumps({"ok": True, "message": "Requisition approved!"})


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
</style>
</head>
<body>

<h5 class="fw-bold mb-3">ME53N &middot; Requisitions List &amp; Approval</h5>
<div id="alert-box"></div>

<div class="d-flex align-items-center gap-2 mb-3">
  <label class="small fw-semibold mb-0">Status:</label>
  <select id="list-status" class="form-select form-select-sm" style="width:160px" onchange="loadPRs()">
    <option value="">ALL</option>
    <option value="SUBMITTED">SUBMITTED</option>
    <option value="APPROVED">APPROVED</option>
    <option value="REJECTED">REJECTED</option>
    <option value="ORDERED">ORDERED</option>
  </select>
  <button class="btn btn-sm btn-outline-secondary" onclick="loadPRs()"><i class="bi bi-arrow-clockwise me-1"></i>Refresh</button>
  <button class="btn btn-sm btn-primary" onclick="approveSelected()"><i class="bi bi-check-lg me-1"></i>Approve Requisition</button>
</div>

<div class="card border-0 shadow-sm">
  <div class="table-responsive"><table class="table table-hover table-sm mb-0">
    <thead class="table-light"><tr><th>ID</th><th>PR Number</th><th>Date</th><th>Department</th><th>Status</th><th>Items</th></tr></thead>
    <tbody id="pr-rows"><tr><td colspan="6" class="text-center text-muted py-4">Loading…</td></tr></tbody>
  </table></div>
</div>

<script>
let backend = null;
let selectedPrId = null;

new QWebChannel(qt.webChannelTransport, function(channel) {
  backend = channel.objects.backend;
  backend.prsChanged.connect(loadPRs);
  loadPRs();
});

function esc(v) { return (v===null||v===undefined?'':String(v)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function flash(msg, type) {
  const box = document.getElementById('alert-box');
  box.innerHTML = '<div class="alert alert-' + type + ' small py-2">' + esc(msg) + '</div>';
  setTimeout(() => { box.innerHTML = ''; }, 4000);
}

function loadPRs() {
  const status = document.getElementById('list-status').value;
  backend.list_prs(status, function(resp) {
    const data = JSON.parse(resp);
    const body = document.getElementById('pr-rows');
    if (!data.ok) { flash(data.error, 'danger'); body.innerHTML = ''; return; }
    const prs = data.prs || [];
    if (!prs.length) { body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No requisitions.</td></tr>'; return; }
    body.innerHTML = prs.map(pr => {
      const id = pr.id;
      return '<tr class="' + (selectedPrId === id ? 'table-primary' : '') + '" style="cursor:pointer" onclick="selectRow(' + id + ')">'
        + '<td>' + esc(id) + '</td><td class="fw-bold">' + esc(pr.pr_number) + '</td><td>' + esc(pr.pr_date) + '</td>'
        + '<td>' + esc(pr.department) + '</td><td><span class="badge bg-secondary">' + esc(pr.status) + '</span></td>'
        + '<td>' + esc(pr.line_count) + '</td></tr>';
    }).join('');
  });
}

function selectRow(id) { selectedPrId = id; loadPRs(); }

function approveSelected() {
  if (!selectedPrId) { flash('Please select a requisition to approve.', 'warning'); return; }
  if (!confirm('Approve Purchase Requisition #' + selectedPrId + '?')) return;
  backend.approve_pr(selectedPrId, function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'danger'); return; }
    flash(data.message, 'success');
  });
}
</script>
</body>
</html>
"""


class PRListWidget(QWidget):
    """Requisitions List & Approval (ME53N) only."""

    def __init__(self, api: ApiClient):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        self.channel = QWebChannel()
        self.backend = _Backend(api)
        self.channel.registerObject("backend", self.backend)
        self.view.page().setWebChannel(self.channel)
        self.view.setHtml(_PAGE_HTML, baseUrl=QUrl("qrc:///"))

        layout.addWidget(self.view)

    def reload(self) -> None:
        self.backend.prsChanged.emit()