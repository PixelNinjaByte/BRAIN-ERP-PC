"""
Brain ERP – MM01 (Create Material) screen, rendered with QWebEngineView.

The whole UI is HTML/CSS/JS (modelled on material_master.php MM01).
Python only does two things:
  * hosts the QWebEngineView
  * exposes a small QWebChannel bridge so the page can call ApiClient
    (mm_lookups / mm_create) without blocking the Qt GUI thread.

Requires:  pip install PyQt6 PyQt6-WebEngine
"""

from __future__ import annotations

import json
import traceback

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

from ..api_client import ApiClient, ApiError

# block_status / moving_avg_price exist in the HTML form (as in the PHP page).
# Set True once the FastAPI MM01 schema accepts them.
SEND_EXTRA_FIELDS = False


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
        except ApiError as e:
            out = {"ok": False, "error": str(e)}
        except Exception as e:  # never let a worker die silently
            traceback.print_exc()
            out = {"ok": False, "error": f"Unexpected error: {e}"}
        self.bridge.response.emit(self.req_id, json.dumps(out, default=str))


class _Bridge(QObject):
    """Exposed to JavaScript as `bridge`."""

    response = pyqtSignal(str, str)  # (request id, JSON string)

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

    @pyqtSlot(str, str, str)
    def call(self, req_id: str, method: str, payload_json: str) -> None:
        payload = json.loads(payload_json or "{}")
        if method == "lookups":
            fn = self.api.mm_lookups
        elif method == "create":
            if not SEND_EXTRA_FIELDS:
                payload.pop("block_status", None)
                payload.pop("moving_avg_price", None)
            fn = lambda: self.api.mm_create(payload)
        else:
            self.response.emit(req_id, json.dumps({"ok": False, "error": f"Unknown method {method}"}))
            return
        QThreadPool.globalInstance().start(_Job(fn, req_id, self))


# ──────────────────────────────────────────────────────────────────────────────
# Public widget
# ──────────────────────────────────────────────────────────────────────────────

class MM01Widget(QWidget):
    """Top-level widget hosted by MainWindow."""

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

        self.view = QWebEngineView(self)

        self.bridge = _Bridge(api)
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
<title>MM01</title>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;font-size:14px;color:#111827;background:#f4f6fa}
  .page{padding:16px 20px 40px;max-width:1500px;margin:0 auto}
  .crumbs{font-size:12px;color:#6b7280;margin-bottom:2px}
  h1{font-size:19px;margin:0 0 12px;font-weight:700}
  .row{display:grid;gap:16px}
  .cols3{grid-template-columns:repeat(3,minmax(0,1fr))}
  @media(max-width:1100px){.cols3{grid-template-columns:1fr 1fr}}
  @media(max-width:760px){.cols3{grid-template-columns:1fr}}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
  .card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.09);margin-bottom:16px;overflow:hidden}
  .card-h{padding:8px 14px;font-weight:700;font-size:13px;border-bottom:1px solid #eef0f3;background:#fff}
  .card-h.blue{background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff;border:0}
  .card-b{padding:14px}
  .mb{margin-bottom:10px}
  label{display:block;font-weight:600;font-size:12.5px;margin-bottom:3px}
  .req{color:#dc2626}
  input[type=text],input[type=number],select,textarea{width:100%;padding:6px 9px;font:inherit;font-size:13px;color:#111827;background:#fff;border:1px solid #ced4da;border-radius:5px;outline:none}
  input:focus,select:focus,textarea:focus{border-color:#1976d2;box-shadow:0 0 0 3px rgba(25,118,210,.15)}
  input.bad,select.bad{border-color:#dc2626;box-shadow:0 0 0 3px rgba(220,38,38,.12)}
  input[readonly],select:disabled{background:#f1f3f5}
  textarea{resize:vertical}
  .end{text-align:right}
  .hint{font-size:11.5px;color:#6b7280;margin-top:3px}
  .chk{display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px;margin-bottom:8px}
  .chk input{width:16px;height:16px}
  .btn{display:inline-block;border:1px solid transparent;border-radius:6px;padding:8px 14px;font:inherit;font-weight:600;font-size:13px;cursor:pointer;text-align:center}
  .btn-primary{background:#1565c0;color:#fff}.btn-primary:hover{background:#0d47a1}
  .btn-primary:disabled{background:#90caf9;cursor:default}
  .btn-out{background:#fff;color:#dc2626;border-color:#dc2626}.btn-out:hover{background:#fef2f2}
  .btn-gray{background:#fff;color:#374151;border-color:#d1d5db}.btn-gray:hover{background:#f3f4f6}
  .stack{display:grid;gap:8px}
  .alert{padding:10px 14px;border-radius:6px;font-size:13px;margin-bottom:12px;white-space:pre-line}
  .alert-danger{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
  .alert-ok{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
  .alert-warn{background:#fffbeb;color:#92400e;border:1px solid #fde68a}
  .alert-warn ul{margin:6px 0 8px;padding-left:20px}
  .badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;border-radius:10px;background:#6b7280;color:#fff}
  .preview{text-align:center;background:#f8f9fa;border-radius:8px;padding:14px;margin-bottom:12px}
  .pcode{font-family:Consolas,monospace;font-weight:700;font-size:19px;color:#1565c0;word-break:break-all}
  .pname{color:#6b7280;font-size:12.5px;margin-top:2px}
  table.kv{width:100%;font-size:12.5px;border-collapse:collapse}
  table.kv td{padding:3px 0}table.kv td:first-child{color:#6b7280;width:80px}
  .money{color:#2e7d32;font-weight:700}
</style>
</head>
<body>

<div class="page" id="tab-create">
  <div class="crumbs">MM › Materials › Create Material</div>
  <h1>Create Material Master — MM01</h1>

  <div id="msg"></div>
  <div id="dup" class="alert alert-warn" style="display:none">
    <b>Similar materials already in the master:</b>
    <ul id="dup-list"></ul>
    <label class="chk" style="margin:0"><input type="checkbox" id="cfdup"> Create anyway — this is a different material</label>
  </div>

  <form id="mm-form" autocomplete="off">
  <div class="row cols3">

    <!-- Col 1 -->
    <div>
      <div class="card">
        <div class="card-h blue">📦 Basic Data</div>
        <div class="card-b">
          <div class="mb"><label>Material Code</label>
            <input type="text" id="item_code" maxlength="50" style="text-transform:uppercase;font-weight:700" placeholder="Leave blank for automatic number (MAT000001…)">
            <div class="hint">Letters, digits and . _ - / only. Unique within the company.</div></div>
          <div class="mb"><label>Material Description <span class="req">*</span></label>
            <input type="text" id="item_name" maxlength="200"></div>
          <div class="mb"><label>Long Description</label>
            <textarea id="description" rows="2" placeholder="Specifications, dimensions, grade, standards..."></textarea></div>
          <div class="grid2 mb">
            <div><label>Material Type</label><select id="item_type"></select></div>
            <div><label>Category</label><select id="item_category"></select></div>
          </div>
          <div><label>Manufacturer / Brand</label><input type="text" id="manufacturer" maxlength="100"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">🏷️ Classification &amp; Tax</div>
        <div class="card-b">
          <div class="mb"><label>Material Group</label><select id="material_group"></select>
            <div class="hint">Inventory GL account: <b id="gl">company default</b></div></div>
          <div class="mb"><label>Base Unit of Measure <span class="req">*</span></label><select id="base_uom"></select></div>
          <div class="grid2">
            <div><label>HSN / SAC</label><input type="text" id="hsn_code" maxlength="8" inputmode="numeric" placeholder="4, 6 or 8 digits"></div>
            <div><label>GST Rate %</label><select id="gst_rate"></select></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Col 2 -->
    <div>
      <div class="card">
        <div class="card-h">💰 Valuation &amp; Pricing</div>
        <div class="card-b">
          <div class="hint" style="margin:0 0 8px;font-size:12.5px"><span>Valuation method:</span> <b style="color:#111827">Moving average price</b> (weighted over all warehouses)</div>
          <div class="grid2">
            <div><label>Standard Cost (₹)</label><input type="number" class="end" id="standard_price" value="0" min="0" step="0.0001">
              <div class="hint">Reference cost for production costing.</div></div>
            <div><label>Moving Avg Price (₹)</label><input type="number" class="end" id="moving_avg_price" value="0" min="0" step="0.0001">
              <div class="hint">Opening value; updated by every GRN.</div></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">📈 MRP / Planning</div>
        <div class="card-b">
          <div class="grid3 mb">
            <div><label>Min Stock</label><input type="number" class="end" id="min_stock" value="0" min="0" step="0.001"></div>
            <div><label>Max Stock</label><input type="number" class="end" id="max_stock" value="0" min="0" step="0.001"></div>
            <div><label>Reorder Pt.</label><input type="number" class="end" id="reorder_point" value="0" min="0" step="0.001"></div>
          </div>
          <div class="grid2">
            <div><label>Lead Time (days)</label><input type="number" id="lead_time_days" value="0" min="0"></div>
            <div><label>Lot Size</label><input type="number" id="lot_size" value="1" min="0.001" step="0.001"></div>
          </div>
          <div class="hint" style="margin-top:8px">Reorder point is compared with <b>total stock across all warehouses</b>.</div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">🛡️ Quality &amp; Storage</div>
        <div class="card-b">
          <div class="grid2 mb">
            <div><label>QC on GRN</label><select id="qc_required"><option value="0">Not required</option><option value="1">Required</option></select></div>
            <div><label>Batch Managed</label><select id="batch_managed"><option value="0">No batch</option><option value="1">Batch managed</option></select></div>
          </div>
          <div class="grid2">
            <div><label>Shelf Life (days)</label><input type="number" id="shelf_life_days" value="0" min="0" placeholder="0 = no expiry"></div>
            <div><label>Gross Weight (kg)</label><input type="number" id="gross_weight" value="0" min="0" step="0.001"></div>
          </div>
          <div class="hint" style="margin-top:8px">A shelf life automatically switches batch management on.</div>
        </div>
      </div>
    </div>

    <!-- Col 3 -->
    <div>
      <div class="card">
        <div class="card-h">🔘 Status</div>
        <div class="card-b">
          <label class="chk"><input type="checkbox" id="is_active" checked> Material is active</label>
          <label>Block status</label>
          <select id="block_status">
            <option value="">Not blocked</option>
            <option value="PROCUREMENT">Blocked for procurement</option>
            <option value="SALES">Blocked for sales</option>
            <option value="ALL">Blocked for all</option>
          </select>
          <div class="hint">Blocked materials disappear from the PR / PO or sales order / invoice pickers; stock and history are kept.</div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">👁️ Live Preview</div>
        <div class="card-b">
          <div class="preview"><div class="pcode" id="prev-code">———</div><div class="pname" id="prev-name">Description will appear here</div></div>
          <table class="kv">
            <tr><td>Category:</td><td><b id="prev-cat">—</b></td></tr>
            <tr><td>UOM:</td><td id="prev-uom">—</td></tr>
            <tr><td>GST:</td><td id="prev-gst">—</td></tr>
            <tr><td>Std cost:</td><td class="money" id="prev-price">—</td></tr>
          </table>
        </div>
      </div>

      <div class="card"><div class="card-b stack">
        <button type="submit" class="btn btn-primary" id="save">💾 Create Material Master</button>
        <button type="button" class="btn btn-out" id="reset">✕ Reset</button>
      </div></div>
    </div>

  </div>
  </form>
</div>

<script>
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var bridge, pending = {}, seq = 0, lookups = {};

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
    el.innerHTML = text ? '<div class="alert alert-' + kind + '">' + esc(text) + '</div>' : '';
  }

  /* ── lookups ──────────────────────────────────────── */
  function fillSelect(sel, items, selected){
    sel.innerHTML = items.map(function(o){
      return '<option value="' + esc(o.value) + '"' + (o.data ? ' data-gl="' + esc(o.data) + '"' : '') + '>' + esc(o.label) + '</option>';
    }).join('');
    if (selected !== undefined) sel.value = selected;
  }
  function applyLookups(){
    var L = lookups;
    fillSelect($('item_type'), Object.keys(L.item_types || {}).map(function(k){ return {value:k, label:L.item_types[k]}; }));
    fillSelect($('item_category'), Object.keys(L.item_categories || {}).map(function(k){ return {value:k, label:L.item_categories[k]}; }));
    var uoms = [{value:'', label:'UOM'}].concat((L.uoms || []).map(function(u){ return {value:u.id, label:u.code + ' — ' + u.name}; }));
    fillSelect($('base_uom'), uoms, '');
    var grps = [{value:'', label:'— None —'}].concat((L.material_groups || []).map(function(g){
      return {value:g.id, label:g.code + ' — ' + g.name, data: g.account_code ? (g.account_code + ' — ' + (g.account_name || '')) : ''}; }));
    fillSelect($('material_group'), grps, '');
    var rates = (L.gst_rates || [18]).map(function(r){ return {value:r, label:(+r) + '%'}; });
    fillSelect($('gst_rate'), rates);
    var def = rates.filter(function(r){ return +r.value === 18; })[0];
    if (def) $('gst_rate').value = def.value;
    updatePreview();
  }

  /* ── live preview ─────────────────────────────────── */
  function selText(id){ var s = $(id); return s.selectedIndex >= 0 ? s.options[s.selectedIndex].text : ''; }
  function updatePreview(){
    $('prev-code').textContent = $('item_code').value.trim().toUpperCase() || 'auto';
    $('prev-name').textContent = $('item_name').value || 'Description will appear here';
    $('prev-cat').textContent = selText('item_category') || '—';
    var u = selText('base_uom'); $('prev-uom').textContent = ($('base_uom').value && u) ? u.split(' — ')[0] : '—';
    $('prev-gst').textContent = $('gst_rate').value ? $('gst_rate').value + '%' : '—';
    $('prev-price').textContent = '₹' + (parseFloat($('standard_price').value || 0)).toFixed(4);
    var g = $('material_group'), go = g.options[g.selectedIndex];
    $('gl').textContent = (go && go.getAttribute('data-gl')) || 'company default';
  }
  document.querySelectorAll('#mm-form input, #mm-form select, #mm-form textarea').forEach(function(e){
    e.addEventListener('input', updatePreview); e.addEventListener('change', updatePreview);
  });
  $('standard_price').addEventListener('input', function(){ $('moving_avg_price').value = this.value; });
  $('shelf_life_days').addEventListener('input', function(){ if (+this.value > 0) $('batch_managed').value = '1'; });
  ['item_name','base_uom'].forEach(function(id){ $(id).addEventListener('input', function(){ this.classList.remove('bad'); }); });

  /* ── payload / save ───────────────────────────────── */
  function num(id){ return parseFloat($(id).value || 0) || 0; }
  function payload(confirmDup){
    return {
      item_code: $('item_code').value.trim(),
      item_name: $('item_name').value.trim(),
      description: $('description').value.trim(),
      item_type: $('item_type').value || 'MATERIAL',
      item_category: $('item_category').value || 'RAW',
      base_uom_id: $('base_uom').value === '' ? null : parseInt($('base_uom').value, 10),
      material_group_id: $('material_group').value === '' ? null : parseInt($('material_group').value, 10),
      hsn_code: $('hsn_code').value.trim(),
      gst_rate: num('gst_rate'),
      standard_price: num('standard_price'),
      moving_avg_price: num('moving_avg_price'),
      min_stock: num('min_stock'),
      max_stock: num('max_stock'),
      reorder_point: num('reorder_point'),
      lead_time_days: parseInt($('lead_time_days').value || 0, 10) || 0,
      lot_size: num('lot_size') || 1,
      gross_weight: num('gross_weight'),
      shelf_life_days: parseInt($('shelf_life_days').value || 0, 10) || 0,
      qc_required: $('qc_required').value === '1',
      batch_managed: $('batch_managed').value === '1',
      manufacturer: $('manufacturer').value.trim(),
      block_status: $('block_status').value,
      is_active: $('is_active').checked,
      confirm_dup: !!confirmDup
    };
  }

  function showDup(similar){
    $('dup-list').innerHTML = similar.map(function(s){
      return '<li><b>' + esc(s.item_code) + '</b> — ' + esc(s.item_name) + ' <span class="badge">' + esc(s.score) + '%</span></li>';
    }).join('');
    $('cfdup').checked = false;
    $('dup').style.display = '';
  }

  async function save(){
    banner($('msg'), '', 'danger');
    if (!$('item_name').value.trim()){ $('item_name').classList.add('bad'); banner($('msg'), 'Material description is required.', 'danger'); $('item_name').focus(); return; }
    if ($('base_uom').value === ''){ $('base_uom').classList.add('bad'); banner($('msg'), 'A valid Base UOM is required.', 'danger'); $('base_uom').focus(); return; }

    var btn = $('save'), label = btn.textContent;
    btn.disabled = true; btn.textContent = 'Saving…';
    var confirm = $('dup').style.display !== 'none' && $('cfdup').checked;
    var r = await call('create', payload(confirm));
    btn.disabled = false; btn.textContent = label;

    if (!r.ok){ banner($('msg'), r.error, 'danger'); return; }
    var resp = r.data || {}, data = resp.data || {};
    if (data.needs_confirmation){
      showDup(data.similar || []);
      banner($('msg'), 'A material with a very similar description already exists (listed below). Change the description, or tick "Create anyway" and save again.', 'danger');
      return;
    }
    resetForm();
    banner($('msg'), resp.message || '✅ Material created successfully.', 'ok');
  }
  $('mm-form').addEventListener('submit', function(e){ e.preventDefault(); save(); });

  function resetForm(){
    $('mm-form').reset();
    $('dup').style.display = 'none';
    var def = Array.prototype.filter.call($('gst_rate').options, function(o){ return +o.value === 18; })[0];
    if (def) $('gst_rate').value = def.value;
    document.querySelectorAll('.bad').forEach(function(e){ e.classList.remove('bad'); });
    updatePreview();
    $('item_name').focus();
  }
  $('reset').addEventListener('click', function(){ banner($('msg'), '', 'ok'); resetForm(); });

  /* ── start ────────────────────────────────────────── */
  new QWebChannel(qt.webChannelTransport, function(ch){
    bridge = ch.objects.bridge;
    bridge.response.connect(function(id, js){
      var cb = pending[id]; delete pending[id];
      if (cb) cb(JSON.parse(js));
    });
    call('lookups').then(function(r){
      if (!r.ok){ banner($('msg'), 'Could not load dropdown data: ' + r.error, 'danger'); $('save').disabled = true; return; }
      lookups = r.data || {};
      applyLookups();
    });
  });
})();
</script>
</body>
</html>
"""