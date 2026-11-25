"""
Brain ERP - Material Import & Export Widget (ZMMI).

Rewritten to render the exact two-step (preview -> confirm) CSV import
layout used by material_import.php inside a QWebEngineView, instead of
native Qt widgets. Python <-> JavaScript communication goes through a
QWebChannel bridge object that exposes the same operations the old
widget used: preview a CSV, confirm/commit the import, download the
template, and export the current material master.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from ..api_client import ApiClient, ApiError


# ──────────────────────────────────────────────────────────────────────
# JS <-> Python bridge
# ──────────────────────────────────────────────────────────────────────
class MaterialImportBridge(QObject):
    """Exposed to the page's JavaScript as `bridge` via QWebChannel."""

    def __init__(self, api: ApiClient, parent_widget: QWidget):
        super().__init__()
        self.api = api
        self._parent = parent_widget  # used as QFileDialog owner

    @pyqtSlot(str, bool, result=str)
    def preview_csv(self, content: str, update_existing: bool) -> str:
        content = (content or "").strip()
        if not content:
            return json.dumps({"ok": False, "error": "CSV content is empty."})
        try:
            res = self.api.material_import_preview(content, update_existing=update_existing)
            return json.dumps({
                "ok": True,
                "token": res.get("token", ""),
                "counts": res.get("counts", {}),
                "total_rows": res.get("total_rows", 0),
                "preview": res.get("preview", []),
            })
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def confirm_import(self, token: str) -> str:
        if not token:
            return json.dumps({"ok": False, "error": "Preview has expired — validate the file again."})
        try:
            res = self.api.material_import_confirm(token)
            return json.dumps({"ok": True, "counts": res.get("counts", {}), "msgs": res.get("msgs", [])})
        except ApiError as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(result=str)
    def get_columns(self) -> str:
        """Best-effort: ask the API for the exact CSV column list, if it exposes one."""
        getter = getattr(self.api, "material_import_columns", None)
        if callable(getter):
            try:
                cols = getter()
                if isinstance(cols, list) and cols:
                    return json.dumps({"ok": True, "columns": cols})
            except ApiError:
                pass
        return json.dumps({"ok": False, "columns": []})

    @pyqtSlot(result=str)
    def download_template(self) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self._parent, "Save Template", "material_import_template.csv", "CSV Files (*.csv)"
        )
        if not path:
            return json.dumps({"ok": False, "cancelled": True})
        try:
            import httpx
            r = httpx.get(f"{self.api.base_url}/api/mm/import/template")
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return json.dumps({"ok": True, "path": path})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def export_csv(self, status_filter: str) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self._parent, "Export Materials", "materials_catalog.csv", "CSV Files (*.csv)"
        )
        if not path:
            return json.dumps({"ok": False, "cancelled": True})
        try:
            import httpx
            params = {"st": status_filter} if status_filter else {}
            r = httpx.get(f"{self.api.base_url}/api/mm/export", params=params)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return json.dumps({"ok": True, "path": path})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})


# ──────────────────────────────────────────────────────────────────────
# HTML / CSS / JS — mirrors the layout & styling of material_import.php
# (ZMMI): Export card (left), Columns info card (left), two-step
# Import card (right) that swaps between an upload form and a
# preview/confirm table, exactly like the PHP $preview / $done states.
# ──────────────────────────────────────────────────────────────────────
_PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZMMI - Material Import / Export</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
<style>
  body { background:#f8fafc; color:#111827; font-size:14px; }
  .card { border:0; }
  .shadow-sm { box-shadow:0 1px 3px rgba(0,0,0,.08) !important; }
  .form-control-sm, .form-select-sm { font-size:.82rem; }
  #toast-box { position:fixed; top:14px; right:14px; z-index:2000; min-width:260px; }
  #done-alert:empty { display:none; }
  .preview-table-wrap { max-height:340px; overflow:auto; }
  .preview-table { font-size:.78rem; }
  textarea#csv-paste { font-family:monospace; font-size:.75rem; height:110px; }
  code.cols { word-break:break-word; }
</style>
</head>
<body>

<div id="toast-box"></div>

<div class="container-fluid p-3">

  <div class="d-flex justify-content-between align-items-center mb-3">
    <h5 class="fw-bold mb-0">ZMMI &middot; Material Master CSV Import &amp; Export</h5>
  </div>

  <div id="done-alert" class="mb-3"></div>

  <div class="row g-3">

    <!-- LEFT: Export + Columns info -->
    <div class="col-lg-4">
      <div class="card shadow-sm mb-3">
        <div class="card-header bg-white fw-bold small py-2">
          <i class="bi bi-download me-2 text-success"></i>Export
        </div>
        <div class="card-body p-3 d-grid gap-2">
          <button type="button" id="export-all" class="btn btn-success btn-sm">
            <i class="bi bi-file-earmark-excel me-1"></i>All materials (CSV)
          </button>
          <button type="button" id="export-active" class="btn btn-outline-success btn-sm">
            Active materials only
          </button>
          <button type="button" id="download-template" class="btn btn-outline-secondary btn-sm">
            <i class="bi bi-file-earmark me-1"></i>Blank template with example
          </button>
          <div class="small text-muted">Exports open in Excel (UTF-8). The same layout is accepted for import.</div>
        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-header bg-white fw-bold small py-2">Columns</div>
        <div class="card-body p-3 small text-muted">
          <code class="cols" id="cols-text">Download the template for the exact column headers.</code>
          <hr class="my-2">
          <b>uom_code</b> (e.g. KG) and <b>group_code</b> are looked up by code. Blank <b>item_code</b> = automatic
          MAT number. Types: MATERIAL, SERVICE, ASSET, TRADING. Categories: RAW, WIP, FG, CONSUMABLE, SPARE, TRADING.
          <b>block_status</b>: blank, PROCUREMENT, SALES, ALL. HSN 4/6/8 digits. On update, blank cells keep the
          current value.
        </div>
      </div>
    </div>

    <!-- RIGHT: Import (two-step: upload -> preview/confirm) -->
    <div class="col-lg-8">
      <div class="card shadow-sm">
        <div class="card-header bg-white fw-bold small py-2">
          <i class="bi bi-upload me-2 text-primary"></i>Import
        </div>
        <div class="card-body p-3">

          <!-- Step 1: upload / paste form -->
          <div id="import-form">
            <div class="row g-3 align-items-end">
              <div class="col-md-7">
                <label class="form-label small fw-semibold mb-1">CSV file (max 2 MB, 5000 rows)</label>
                <input type="file" id="csv-file" accept=".csv,text/csv" class="form-control form-control-sm">
              </div>
              <div class="col-md-5">
                <div class="form-check">
                  <input class="form-check-input" type="checkbox" id="update-existing">
                  <label class="form-check-label small" for="update-existing">
                    Update materials that already exist (matched by code)
                  </label>
                </div>
              </div>
              <div class="col-12">
                <label class="form-label small fw-semibold mb-1">...or paste CSV text directly</label>
                <textarea id="csv-paste" class="form-control form-control-sm"
                  placeholder="Headers must include at least item_name"></textarea>
              </div>
              <div class="col-12">
                <button type="button" id="validate-btn" class="btn btn-primary">
                  <i class="bi bi-search me-1"></i>Validate &amp; preview
                </button>
              </div>
            </div>
          </div>

          <!-- Step 2: preview + confirm (hidden until a preview exists) -->
          <div id="import-preview" class="d-none">
            <div class="mb-2">
              <span class="badge bg-success">New <span id="cnt-new">0</span></span>
              <span class="badge bg-primary">Update <span id="cnt-update">0</span></span>
              <span class="badge bg-secondary">Skip <span id="cnt-skip">0</span></span>
              <span class="badge bg-danger">Errors <span id="cnt-error">0</span></span>
              <span class="text-muted small">of <span id="cnt-total">0</span> rows &mdash; nothing has been saved yet.</span>
            </div>
            <div class="preview-table-wrap mb-3">
              <table class="table table-sm preview-table">
                <thead class="table-light">
                  <tr><th>Line</th><th>Status</th><th>Code</th><th>Description</th><th>Problems</th></tr>
                </thead>
                <tbody id="preview-tbody"></tbody>
              </table>
            </div>
            <div id="preview-truncated-note" class="small text-muted mb-2 d-none">
              Showing the first 300 rows; the import still processes all rows.
            </div>
            <div class="d-flex gap-2">
              <button type="button" id="confirm-btn" class="btn btn-primary">
                <i class="bi bi-check2-circle me-1"></i>Import <span id="confirm-count">0</span> valid row(s)
              </button>
              <button type="button" id="cancel-btn" class="btn btn-outline-secondary">Cancel</button>
            </div>
            <div class="small text-muted mt-2">Rows with errors are skipped; fix them in the file and import again.</div>
          </div>

        </div>
      </div>
    </div>

  </div>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
  let bridge = null;
  let currentCsvContent = '';
  let currentToken = '';

  function toast(msg, ok) {
    const box = document.getElementById('toast-box');
    const el = document.createElement('div');
    el.className = 'alert ' + (ok ? 'alert-success' : 'alert-danger') + ' shadow-sm py-2 px-3 mb-2';
    el.innerText = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function escapeHtml(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  const STATUS_CLASS = { NEW: 'success', UPDATE: 'primary', SKIP: 'secondary', ERROR: 'danger' };

  // ---- file / paste input ----
  document.getElementById('csv-file').addEventListener('change', function (e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function (ev) {
      currentCsvContent = ev.target.result;
      document.getElementById('csv-paste').value = currentCsvContent;
    };
    reader.readAsText(file);
  });

  document.getElementById('csv-paste').addEventListener('input', function () {
    currentCsvContent = this.value;
  });

  // ---- step 1 -> validate & preview ----
  document.getElementById('validate-btn').addEventListener('click', function () {
    const content = (currentCsvContent || document.getElementById('csv-paste').value || '').trim();
    if (!content) {
      toast('Choose a CSV file or paste CSV content.', false);
      return;
    }
    const updateExisting = document.getElementById('update-existing').checked;
    bridge.preview_csv(content, updateExisting, function (resultJson) {
      const res = JSON.parse(resultJson);
      if (!res.ok) {
        toast('Failed to preview CSV: ' + res.error, false);
        return;
      }
      currentToken = res.token;
      renderPreview(res);
    });
  });

  function renderPreview(res) {
    const c = res.counts || {};
    document.getElementById('cnt-new').innerText = c.NEW || 0;
    document.getElementById('cnt-update').innerText = c.UPDATE || 0;
    document.getElementById('cnt-skip').innerText = c.SKIP || 0;
    document.getElementById('cnt-error').innerText = c.ERROR || 0;
    document.getElementById('cnt-total').innerText = res.total_rows || 0;
    document.getElementById('confirm-count').innerText = (c.NEW || 0) + (c.UPDATE || 0);

    const tbody = document.getElementById('preview-tbody');
    tbody.innerHTML = (res.preview || []).map(l => {
      const cls = STATUS_CLASS[l.status] || 'secondary';
      const errs = Array.isArray(l.errors) ? l.errors.join(' ') : (l.errors || '');
      return (
        '<tr>' +
          '<td>' + escapeHtml(l.line) + '</td>' +
          '<td><span class="badge bg-' + cls + '">' + escapeHtml(l.status) + '</span></td>' +
          '<td>' + escapeHtml(l.item_code || '(auto)') + '</td>' +
          '<td>' + escapeHtml((l.item_name || '').toString().slice(0, 40)) + '</td>' +
          '<td class="text-danger">' + escapeHtml(errs) + '</td>' +
        '</tr>'
      );
    }).join('');

    document.getElementById('preview-truncated-note').classList.toggle(
      'd-none', !(res.total_rows > 300)
    );

    document.getElementById('confirm-btn').disabled = ((c.NEW || 0) + (c.UPDATE || 0)) === 0;
    document.getElementById('import-form').classList.add('d-none');
    document.getElementById('import-preview').classList.remove('d-none');
  }

  // ---- step 2 -> confirm / cancel ----
  document.getElementById('confirm-btn').addEventListener('click', function () {
    if (!currentToken) return;
    bridge.confirm_import(currentToken, function (resultJson) {
      const res = JSON.parse(resultJson);
      if (!res.ok) {
        toast('Failed to commit import: ' + res.error, false);
        return;
      }
      showDone(res.counts || {}, res.msgs || []);
      resetToStep1();
    });
  });

  document.getElementById('cancel-btn').addEventListener('click', resetToStep1);

  function resetToStep1() {
    currentToken = '';
    currentCsvContent = '';
    document.getElementById('csv-file').value = '';
    document.getElementById('csv-paste').value = '';
    document.getElementById('import-preview').classList.add('d-none');
    document.getElementById('import-form').classList.remove('d-none');
  }

  function showDone(counts, msgs) {
    const hasErrors = (counts.ERROR || 0) > 0;
    const box = document.getElementById('done-alert');
    let html = '<div class="alert ' + (hasErrors ? 'alert-warning' : 'alert-success') + '">' +
      '<b>Import finished.</b> Created ' + (counts.NEW || 0) + ', updated ' + (counts.UPDATE || 0) +
      ', unchanged/skipped ' + (counts.SKIP || 0) + ', failed ' + (counts.ERROR || 0) + '.';
    if (msgs && msgs.length) {
      html += '<ul class="mb-0 mt-2 small">' + msgs.map(m => '<li>' + escapeHtml(m) + '</li>').join('') + '</ul>';
    }
    html += '</div>';
    box.innerHTML = html;
  }

  // ---- export / template ----
  document.getElementById('export-all').addEventListener('click', function () {
    bridge.export_csv('all', function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) toast('Materials exported to ' + res.path, true);
      else if (!res.cancelled) toast('Failed to export materials: ' + res.error, false);
    });
  });

  document.getElementById('export-active').addEventListener('click', function () {
    bridge.export_csv('active', function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) toast('Active materials exported to ' + res.path, true);
      else if (!res.cancelled) toast('Failed to export materials: ' + res.error, false);
    });
  });

  document.getElementById('download-template').addEventListener('click', function () {
    bridge.download_template(function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok) toast('Template saved to ' + res.path, true);
      else if (!res.cancelled) toast('Failed to save template: ' + res.error, false);
    });
  });

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;
    bridge.get_columns(function (resultJson) {
      const res = JSON.parse(resultJson);
      if (res.ok && res.columns && res.columns.length) {
        document.getElementById('cols-text').innerText = res.columns.join(', ');
      }
    });
  });
</script>
</body>
</html>
"""


class MaterialImportWidget(QWidget):
    """Material Import / Export (ZMMI) rendered via QWebEngineView."""

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
        self.bridge = MaterialImportBridge(self.api, self)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.view.setHtml(_PAGE_HTML, QUrl("qrc:/"))