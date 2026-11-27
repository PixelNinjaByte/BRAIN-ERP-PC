"""
Brain ERP – Material Groups (OMSF) screen  •  mirrors material_groups.php, rendered via
QWebEngineView so the HTML/CSS/JS matches the PHP page almost 1:1 instead of being
re-built in native Qt widgets.

Layout mirrors the PHP page exactly:
  Left  (col-lg-4): "New material group" / "Change group" card — Code*, Name*, Description,
                     Inventory GL account (select, company-default option), Active checkbox,
                     Create/Save button + "New group" link when editing.
  Right (col-lg-8): "Groups" table — Code, Name, Inventory GL, Materials (count), Active, actions
                     (edit pencil, delete trash — delete only shown when n_items == 0, same as
                     PHP's `<?php if (!(int)$g['n_items']): ?>`).

Python <-> JS bridge
~~~~~~~~~~~~~~~~~~~~
QWebChannel exposes a `Backend` QObject (window.backend in JS) with pyqtSlots that mirror
the PHP POST actions (`mat_action=save` / `mat_action=delete`) and the two lookups the PHP
page builds server-side ($accts, $list):
  list_groups()      -> JSON list of groups, same columns as the PHP table + n_items/account_*.
  list_accounts()     -> JSON list of asset chart-of-accounts rows for the <select>.
  save_group(json)    -> create or update (group_id = 0 -> create), returns {"ok":..,"error":..}
  delete_group(id)    -> returns {"ok":..,"error":..} (server still enforces the n_items guard).

No server-side validation duplicated here — same principle as the PHP relying on its own
model layer: the FastAPI layer is the source of truth, this widget just renders + round-trips.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ..api_client import ApiClient, ApiError


# ──────────────────────────────────────────────────────────────────────────────
# Backend bridge — exposed to the page as window.backend
# ──────────────────────────────────────────────────────────────────────────────

class _Backend(QObject):

    groupsChanged = pyqtSignal()

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

    # ---- lookups (mirrors $accts in material_groups.php) ----
    @pyqtSlot(result=str)
    def list_accounts(self) -> str:
        try:
            accts = self.api.mat_groups_lookups().get("accounts", [])
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e), "accounts": []})
        return json.dumps({"ok": True, "accounts": accts})

    # ---- list (mirrors $list in material_groups.php) ----
    @pyqtSlot(result=str)
    def list_groups(self) -> str:
        try:
            rows = self.api.mat_groups_list()
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e), "groups": []})
        return json.dumps({"ok": True, "groups": rows})

    # ---- save (mirrors mat_action=save, create when group_id == 0) ----
    @pyqtSlot(str, result=str)
    def save_group(self, payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
        except ValueError:
            return json.dumps({"ok": False, "error": "Malformed form data."})

        gid = int(payload.get("group_id") or 0)
        data = {
            "code": (payload.get("code") or "").strip().upper(),
            "name": (payload.get("name") or "").strip(),
            "description": (payload.get("description") or "").strip(),
            "inventory_account_id": int(payload.get("inventory_account_id") or 0) or None,
            "is_active": bool(payload.get("is_active")),
        }
        try:
            if gid:
                self.api.mat_group_update(gid, data)
                msg = f"Group {data['code']} updated."
            else:
                self.api.mat_group_create(data)
                msg = f"Group {data['code']} created."
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})

        self.groupsChanged.emit()
        return json.dumps({"ok": True, "message": msg})

    # ---- delete (mirrors mat_action=delete, server enforces the n_items guard) ----
    @pyqtSlot(int, result=str)
    def delete_group(self, group_id: int) -> str:
        try:
            self.api.mat_group_delete(group_id)
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})
        self.groupsChanged.emit()
        return json.dumps({"ok": True, "message": "Group deleted."})


# ──────────────────────────────────────────────────────────────────────────────
# Page HTML/CSS/JS — mirrors material_groups.php's markup (Bootstrap 5 + bootstrap-icons,
# same card headers, same gradient on the form card, same table layout)
# ──────────────────────────────────────────────────────────────────────────────

_PAGE_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  body { background:#f8fafc; color:#111827; font-size:.9rem; padding:16px; }
  .card { color:#111827; }
  .grad-header { background:linear-gradient(135deg,#1565c0,#1976d2); }
</style>
</head>
<body>

<h5 class="fw-bold mb-3">OMSF &middot; Material Groups</h5>
<div id="alert-box"></div>

<div class="row g-3">
  <!-- Col 1: form — mirrors the PHP col-lg-4 "New material group" / "Change group" card -->
  <div class="col-lg-4">
    <div class="card border-0 shadow-sm">
      <div class="card-header fw-bold small py-2 text-white grad-header">
        <i class="bi bi-collection me-2"></i><span id="form-title">New material group</span>
      </div>
      <div class="card-body p-3">
        <form id="mg-form" onsubmit="return false;">
          <input type="hidden" id="group_id" value="0">
          <div class="row g-2 mb-2">
            <div class="col-5">
              <label class="form-label small fw-semibold mb-1">Code *</label>
              <input id="code" class="form-control form-control-sm text-uppercase fw-bold" maxlength="20" required>
            </div>
            <div class="col-7">
              <label class="form-label small fw-semibold mb-1">Name *</label>
              <input id="name" class="form-control form-control-sm" maxlength="100" required>
            </div>
          </div>
          <div class="mb-2">
            <label class="form-label small fw-semibold mb-1">Description</label>
            <input id="description" class="form-control form-control-sm" maxlength="255">
          </div>
          <div class="mb-2">
            <label class="form-label small fw-semibold mb-1">Inventory GL account</label>
            <select id="inventory_account_id" class="form-select form-select-sm">
              <option value="0">&mdash; Company default (OBYC: INVENTORY) &mdash;</option>
            </select>
            <div class="form-text small">Goods receipts of this group's materials are debited here (asset accounts only). Create more inventory accounts in FS00 (e.g. Finished Goods, Spares).</div>
          </div>
          <div class="form-check mb-3">
            <input class="form-check-input" type="checkbox" id="is_active" checked>
            <label class="form-check-label small fw-semibold" for="is_active">Active</label>
          </div>
          <div class="d-grid gap-2">
            <button type="button" class="btn btn-primary fw-semibold" id="save-btn" onclick="saveGroup()">
              <i class="bi bi-save me-1"></i><span id="save-label">Create group</span>
            </button>
            <button type="button" class="btn btn-outline-secondary btn-sm d-none" id="new-btn" onclick="resetForm()">New group</button>
          </div>
        </form>
      </div>
    </div>
  </div>

  <!-- Col 2: list — mirrors the PHP col-lg-8 "Groups" table -->
  <div class="col-lg-8">
    <div class="card border-0 shadow-sm">
      <div class="card-header bg-white fw-bold small py-2">
        Groups <span class="badge bg-secondary" id="group-count">0</span>
      </div>
      <div class="table-responsive">
        <table class="table table-hover table-sm mb-0" style="font-size:.82rem">
          <thead class="table-light">
            <tr>
              <th class="px-3">Code</th><th>Name</th><th>Inventory GL</th>
              <th class="text-center">Materials</th><th class="text-center">Active</th><th></th>
            </tr>
          </thead>
          <tbody id="group-rows">
            <tr><td colspan="6" class="text-center text-muted py-4">Loading…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let backend = null;
let groups = [];
let accounts = [];

new QWebChannel(qt.webChannelTransport, function(channel) {
  backend = channel.objects.backend;
  backend.groupsChanged.connect(loadGroups);
  loadAccounts();
  loadGroups();
});

function esc(v) {
  return (v === null || v === undefined ? '' : String(v))
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function flash(msg, type) {
  const box = document.getElementById('alert-box');
  box.innerHTML = '<div class="alert alert-' + type + ' small py-2">' + esc(msg) + '</div>';
  setTimeout(() => { box.innerHTML = ''; }, 4000);
}

function loadAccounts() {
  backend.list_accounts(function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'warning'); return; }
    accounts = data.accounts;
    const sel = document.getElementById('inventory_account_id');
    sel.innerHTML = '<option value="0">&mdash; Company default (OBYC: INVENTORY) &mdash;</option>';
    accounts.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.account_code + ' \u2014 ' + a.account_name;
      sel.appendChild(opt);
    });
  });
}

function loadGroups() {
  backend.list_groups(function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'danger'); return; }
    groups = data.groups;
    renderGroups();
  });
}

function renderGroups() {
  document.getElementById('group-count').textContent = groups.length;
  const body = document.getElementById('group-rows');
  if (!groups.length) {
    body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No groups yet.</td></tr>';
    return;
  }
  const curId = parseInt(document.getElementById('group_id').value || '0', 10);
  body.innerHTML = groups.map(g => {
    const gl = g.account_code ? esc(g.account_code + ' \u2014 ' + g.account_name) : '<span class="text-muted">default</span>';
    const activeIcon = g.is_active
      ? '<i class="bi bi-check-circle-fill text-success"></i>'
      : '<i class="bi bi-x-circle text-danger"></i>';
    const delBtn = (!g.n_items)
      ? '<button class="btn btn-sm btn-outline-danger py-0" onclick="deleteGroup(' + g.id + ', \'' + esc(g.code) + '\')"><i class="bi bi-trash"></i></button>'
      : '';
    return '<tr class="' + (curId === g.id ? 'table-primary' : '') + '">'
      + '<td class="px-3 fw-bold">' + esc(g.code) + '</td>'
      + '<td>' + esc(g.name) + '</td>'
      + '<td>' + gl + '</td>'
      + '<td class="text-center">' + (g.n_items|0) + '</td>'
      + '<td class="text-center">' + activeIcon + '</td>'
      + '<td class="text-end text-nowrap">'
      +   '<button class="btn btn-sm btn-outline-primary py-0 me-1" onclick="editGroup(' + g.id + ')"><i class="bi bi-pencil"></i></button>'
      +   delBtn
      + '</td></tr>';
  }).join('');
}

function editGroup(id) {
  const g = groups.find(x => x.id === id);
  if (!g) return;
  document.getElementById('group_id').value = g.id;
  document.getElementById('code').value = g.code;
  document.getElementById('name').value = g.name;
  document.getElementById('description').value = g.description || '';
  document.getElementById('inventory_account_id').value = g.inventory_account_id || 0;
  document.getElementById('is_active').checked = !!g.is_active;
  document.getElementById('form-title').textContent = 'Change group';
  document.getElementById('save-label').textContent = 'Save';
  document.getElementById('new-btn').classList.remove('d-none');
  renderGroups();
}

function resetForm() {
  document.getElementById('group_id').value = 0;
  document.getElementById('mg-form').reset();
  document.getElementById('is_active').checked = true;
  document.getElementById('form-title').textContent = 'New material group';
  document.getElementById('save-label').textContent = 'Create group';
  document.getElementById('new-btn').classList.add('d-none');
  renderGroups();
}

function saveGroup() {
  const code = document.getElementById('code').value.trim();
  const name = document.getElementById('name').value.trim();
  if (!code || code.length > 20) { flash('Group code is required (letters, digits, - _ ; max 20).', 'danger'); return; }
  if (!name) { flash('Group name is required.', 'danger'); return; }

  const payload = {
    group_id: parseInt(document.getElementById('group_id').value || '0', 10),
    code: code,
    name: name,
    description: document.getElementById('description').value.trim(),
    inventory_account_id: parseInt(document.getElementById('inventory_account_id').value || '0', 10),
    is_active: document.getElementById('is_active').checked,
  };
  backend.save_group(JSON.stringify(payload), function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'danger'); return; }
    flash(data.message, 'success');
    resetForm();
  });
}

function deleteGroup(id, code) {
  if (!confirm('Delete group ' + code + '?')) return;
  backend.delete_group(id, function(resp) {
    const data = JSON.parse(resp);
    if (!data.ok) { flash(data.error, 'danger'); return; }
    flash(data.message, 'success');
  });
}
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────────────────────
# Public widget
# ──────────────────────────────────────────────────────────────────────────────

class MaterialGroupsWidget(QWidget):
    """Top-level widget hosted by MainWindow for tcode OMSF — a QWebEngineView rendering
    the PHP-mirrored HTML page above, wired to the FastAPI layer via QWebChannel."""

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
        """Re-pull groups from the server (call after another screen changes group data)."""
        self.backend.groupsChanged.emit()