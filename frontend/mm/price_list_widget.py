"""
Brain ERP – VK11 / PRLIM (Price List / Condition Records), rendered with QWebEngineView.
Modelled on supplier_invoice.php → VK11 section.

Bridge methods:
  lookups   → price_list_lookups()
  list      → price_list_list()
  create    → price_list_create(payload)
  items     → price_list_items(id)
"""

from __future__ import annotations

import json
import traceback

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError as _e:
    raise ImportError(f"PyQt6-WebEngine not found: {_e}") from _e

from ..api_client import ApiClient, ApiError


class _Job(QRunnable):
    def __init__(self, fn, req_id: str, bridge: "_Bridge"):
        super().__init__()
        self.fn, self.req_id, self.bridge = fn, req_id, bridge

    def run(self) -> None:
        try:
            out = {"ok": True, "data": self.fn()}
        except ApiError as e:
            out = {"ok": False, "error": str(e)}
        except Exception as e:
            traceback.print_exc()
            out = {"ok": False, "error": f"Unexpected error: {e}"}
        self.bridge.response.emit(self.req_id, json.dumps(out, default=str))


class _Bridge(QObject):
    response = pyqtSignal(str, str)

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api

    @pyqtSlot(str, str, str)
    def call(self, req_id: str, method: str, payload_json: str) -> None:
        payload = json.loads(payload_json or "{}")
        api = self.api

        if method == "lookups":
            fn = api.vk11_meta
        elif method == "list":
            fn = api.vk11_meta          # list embedded in meta
        elif method == "create":
            fn = lambda: api.vk11_save(payload)
        elif method == "items":
            fn = lambda: api.vk11_items(payload.get("id"))
        else:
            self.response.emit(req_id, json.dumps({"ok": False, "error": f"Unknown method: {method}"}))
            return

        QThreadPool.globalInstance().start(_Job(fn, req_id, self))


class PriceListWidget(QWidget):
    """VK11 / PRLIM — Price List / Condition Records."""

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

        self.view.setHtml(_HTML, QUrl("qrc:///"))

    def reload_ui(self) -> None:
        self.view.setHtml(_HTML, QUrl("qrc:///"))


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VK11 – Price List</title>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%}
  body{font-family:"Segoe UI",system-ui,Arial,sans-serif;font-size:14px;color:#111827;background:#f4f6fa;display:flex;flex-direction:column;height:100%}
  .topbar{display:flex;align-items:center;gap:10px;padding:10px 18px;background:#fff;border-bottom:1px solid #e5e7eb;flex-shrink:0}
  .crumbs{font-size:12px;color:#6b7280}
  h1{font-size:17px;margin:0;font-weight:700}
  .body{display:flex;flex:1;overflow:hidden;gap:0}
  /* ── left form pane ── */
  .form-pane{width:460px;min-width:320px;border-right:1px solid #e5e7eb;overflow-y:auto;background:#fff;flex-shrink:0;padding:16px}
  /* ── right list pane ── */
  .list-pane{flex:1;overflow-y:auto;padding:16px}
  /* card */
  .card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.09);margin-bottom:14px;overflow:hidden}
  .card-h{padding:8px 14px;font-weight:700;font-size:13px;border-bottom:1px solid #eef0f3}
  .card-h.green{background:linear-gradient(135deg,#15803d,#16a34a);color:#fff;border:0}
  .card-h.blue{background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff;border:0}
  .card-b{padding:14px}
  label{display:block;font-weight:600;font-size:12px;margin-bottom:3px}
  input[type=text],input[type=date],input[type=number],select{width:100%;padding:5px 8px;font:inherit;font-size:13px;border:1px solid #ced4da;border-radius:5px;outline:none}
  input:focus,select:focus{border-color:#1976d2;box-shadow:0 0 0 3px rgba(25,118,210,.15)}
  .mb{margin-bottom:10px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .btn{display:inline-block;border:1px solid transparent;border-radius:6px;padding:7px 14px;font:inherit;font-weight:600;font-size:13px;cursor:pointer;width:100%;text-align:center}
  .btn-green{background:#15803d;color:#fff}.btn-green:hover{background:#166534}
  .btn-green:disabled{background:#86efac;cursor:default}
  .alert{padding:9px 13px;border-radius:6px;font-size:13px;margin-bottom:10px}
  .alert-danger{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
  .alert-ok{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
  .spin{display:inline-block;width:14px;height:14px;border:2px solid #93c5fd;border-top-color:#1565c0;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* items grid */
  .items-header{display:grid;grid-template-columns:1fr 110px 90px 24px;gap:4px;font-size:11px;font-weight:700;color:#6b7280;margin-bottom:4px;padding:0 2px}
  .item-row{display:grid;grid-template-columns:1fr 110px 90px 24px;gap:4px;margin-bottom:4px;align-items:center}
  .item-row input{font-size:12px;padding:4px 6px}
  .item-row select{font-size:12px;padding:4px 6px}
  .del-btn{background:transparent;border:none;color:#dc2626;cursor:pointer;font-size:16px;padding:0;line-height:1}
  .del-btn:hover{color:#991b1b}
  .add-btn{background:transparent;border:1px dashed #9ca3af;border-radius:5px;width:100%;padding:5px;font-size:12px;color:#374151;cursor:pointer;margin-top:4px}
  .add-btn:hover{background:#f9fafb}
  /* list table */
  table.dt{width:100%;border-collapse:collapse;font-size:12.5px}
  table.dt thead th{background:#1565c0;color:#fff;padding:7px 10px;text-align:left;white-space:nowrap}
  table.dt thead th.r{text-align:right}
  table.dt td{padding:6px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}
  table.dt td.r{text-align:right}
  table.dt tbody tr{cursor:pointer}
  table.dt tbody tr:hover{background:#eff6ff}
  table.dt tbody tr.sel{background:#dbeafe}
  .badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;border-radius:10px;background:#6b7280;color:#fff}
  .badge.ok{background:#15803d}
  .muted{color:#6b7280}
  /* items detail panel */
  .detail-panel{margin-top:12px}
  .detail-panel table.dt thead th{background:#374151}
  .empty{text-align:center;padding:20px;color:#9ca3af}
</style>
</head>
<body>

<div class="topbar">
  <div style="flex:1">
    <div class="crumbs">MM › Master Data › Price List</div>
    <h1>Price List / Condition Records — VK11 / PRLIM</h1>
  </div>
</div>

<div class="body">

  <!-- ════ LEFT: Create form ════ -->
  <div class="form-pane">
    <div class="card">
      <div class="card-h green">🏷️ Create / Add to Price List — VK11</div>
      <div class="card-b">
        <div id="form-msg"></div>

        <div class="mb">
          <label>Price List</label>
          <select id="pl-select" onchange="onPlChange()">
            <option value="">— New price list —</option>
          </select>
        </div>

        <div id="new-name-block" class="mb">
          <label>Price List Name <span style="color:#dc2626">*</span></label>
          <input type="text" id="pl-name" placeholder="e.g. Standard 2026">
        </div>

        <div class="grid2 mb">
          <div><label>Valid From</label><input type="date" id="pl-from"></div>
          <div><label>Valid To</label><input type="date" id="pl-to"></div>
        </div>

        <div style="margin-bottom:6px">
          <div class="items-header">
            <span>Material</span><span>Unit Price (₹)</span><span>Discount %</span><span></span>
          </div>
          <div id="item-rows"></div>
          <button class="add-btn" onclick="addRow()">＋ Add material</button>
        </div>

        <button class="btn btn-green" id="save-btn" onclick="savePl()" style="margin-top:8px">💾 Save Price List</button>
      </div>
    </div>
  </div>

  <!-- ════ RIGHT: Existing price lists ════ -->
  <div class="list-pane">
    <div class="card">
      <div class="card-h blue">📋 Existing Price Lists</div>
      <div class="card-b" style="padding:0">
        <table class="dt">
          <thead><tr>
            <th>Name</th><th>Currency</th>
            <th>Valid From</th><th>Valid To</th>
            <th class="r">Items</th><th>Active</th>
          </tr></thead>
          <tbody id="pl-tbody">
            <tr><td colspan="6" class="empty"><span class="spin"></span></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- detail panel (items of selected price list) -->
    <div class="detail-panel" id="detail-panel" style="display:none">
      <div class="card">
        <div class="card-h" id="detail-title">Price List Items</div>
        <div class="card-b" style="padding:0">
          <table class="dt">
            <thead><tr>
              <th>Material Code</th><th>Description</th>
              <th class="r">Unit Price (₹)</th><th class="r">Discount %</th>
              <th class="r">Net Price (₹)</th><th>Valid From</th><th>Valid To</th>
            </tr></thead>
            <tbody id="detail-tbody">
              <tr><td colspan="7" class="empty"><span class="spin"></span></td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

</div><!-- /body -->

<script>
(function(){
  /* ── bridge ── */
  var bridge, pending={}, seq=0;
  function call(method,payload){
    return new Promise(function(resolve){
      var id=String(++seq); pending[id]=resolve;
      bridge.call(id,method,JSON.stringify(payload||{}));
    });
  }
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];})}
  function fmt(n,dp){return n==null?'—':parseFloat(n).toFixed(dp==null?2:dp)}
  function emptyRow(cols,msg){return '<tr><td colspan="'+cols+'" class="empty">'+esc(msg||'No records.')+'</td></tr>'}

  /* ── state ── */
  var materials=[], priceLists=[], rowCount=0;
  var today=new Date().toISOString().split('T')[0];
  var yearEnd=today.substring(0,4)+'-12-31';

  /* ── load lookups ── */
  function init(){
    call('lookups').then(function(r){
      if(!r.ok){banner('Could not load data: '+r.error,'danger');return;}
      var d=r.data||{};
      materials=(d.materials||d.items||[]);
      priceLists=(d.price_lists||[]);
      populatePlSelect();
      renderList();
      // set default dates
      document.getElementById('pl-from').value=today;
      document.getElementById('pl-to').value=yearEnd;
      addRow(); addRow(); addRow(); // start with 3 rows
    });
  }

  function populatePlSelect(){
    var sel=document.getElementById('pl-select');
    sel.innerHTML='<option value="">— New price list —</option>'+
      priceLists.map(function(pl){return '<option value="'+esc(pl.id)+'">'+esc(pl.name)+'</option>';}).join('');
  }

  function onPlChange(){
    var v=document.getElementById('pl-select').value;
    document.getElementById('new-name-block').style.display=v?'none':'';
  }
  window.onPlChange=onPlChange;

  /* ── item rows ── */
  function matOptions(selected){
    return materials.map(function(m){
      return '<option value="'+esc(m.id)+'"'+(m.id==selected?' selected':'')+' data-map="'+esc(m.moving_avg_price||0)+'">'+esc(m.item_code+' — '+m.item_name)+'</option>';
    }).join('');
  }

  function addRow(itemId,price,disc){
    var i=rowCount++;
    var div=document.createElement('div');
    div.className='item-row'; div.id='row-'+i;
    div.innerHTML=
      '<select id="ri-'+i+'" onchange="onMatChange('+i+')">' +
        '<option value="">— Select —</option>'+matOptions(itemId||'')+'</select>'+
      '<input type="number" id="rp-'+i+'" value="'+(price!=null?price:'')+'" step="0.01" min="0" placeholder="Price">'+
      '<input type="number" id="rd-'+i+'" value="'+(disc!=null?disc:'0')+'" step="0.01" min="0" max="100" placeholder="Disc%">'+
      '<button class="del-btn" onclick="delRow('+i+')" title="Remove">×</button>';
    document.getElementById('item-rows').appendChild(div);
  }
  window.addRow=addRow;

  window.onMatChange=function(i){
    var sel=document.getElementById('ri-'+i);
    var opt=sel.options[sel.selectedIndex];
    if(opt){
      var map=opt.getAttribute('data-map');
      if(map) document.getElementById('rp-'+i).value=parseFloat(map).toFixed(2);
    }
  };
  window.delRow=function(i){
    var el=document.getElementById('row-'+i);
    if(el) el.remove();
  };

  /* ── render price list table ── */
  function renderList(){
    var tbody=document.getElementById('pl-tbody');
    if(!priceLists.length){tbody.innerHTML=emptyRow(6,'No price lists yet.');return;}
    tbody.innerHTML=priceLists.map(function(pl){
      return '<tr data-id="'+esc(pl.id)+'" onclick="selectPl('+pl.id+','+JSON.stringify(esc(pl.name))+')">' +
        '<td><b>'+esc(pl.name)+'</b></td>' +
        '<td>'+esc(pl.currency||'INR')+'</td>' +
        '<td class="muted">'+esc(pl.valid_from||'—')+'</td>' +
        '<td class="muted">'+esc(pl.valid_to||'—')+'</td>' +
        '<td class="r"><b>'+esc(pl.item_count||0)+'</b></td>' +
        '<td>'+(pl.is_active?'<span class="badge ok">✓ Active</span>':'<span class="badge">Inactive</span>')+'</td>' +
        '</tr>';
    }).join('');
  }

  /* ── select price list → show items ── */
  window.selectPl=function(id,name){
    document.querySelectorAll('#pl-tbody tr').forEach(function(r){r.classList.toggle('sel',+r.getAttribute('data-id')===id);});
    document.getElementById('detail-panel').style.display='';
    document.getElementById('detail-title').textContent='Items — '+name;
    document.getElementById('detail-tbody').innerHTML='<tr><td colspan="7" class="empty"><span class="spin"></span></td></tr>';
    call('items',{id:id}).then(function(r){
      if(!r.ok){document.getElementById('detail-tbody').innerHTML=emptyRow(7,r.error);return;}
      var rows=r.data||[]; if(r.data&&r.data.data) rows=r.data.data;
      document.getElementById('detail-tbody').innerHTML=rows.length?rows.map(function(it){
        var net=parseFloat(it.unit_price||0)*(1-parseFloat(it.discount_pct||0)/100);
        return '<tr>' +
          '<td><b>'+esc(it.item_code)+'</b></td>' +
          '<td>'+esc(it.item_name)+'</td>' +
          '<td class="r">'+fmt(it.unit_price,2)+'</td>' +
          '<td class="r">'+fmt(it.discount_pct,2)+'%</td>' +
          '<td class="r" style="color:#15803d;font-weight:700">'+fmt(net,2)+'</td>' +
          '<td class="muted">'+esc(it.valid_from||'—')+'</td>' +
          '<td class="muted">'+esc(it.valid_to||'—')+'</td>' +
          '</tr>';
      }).join(''):emptyRow(7,'No items in this price list.');
    });
  };

  /* ── save ── */
  function banner(msg,kind){
    var el=document.getElementById('form-msg');
    el.innerHTML=msg?'<div class="alert alert-'+(kind==='ok'?'ok':'danger')+'">'+esc(msg)+'</div>':'';
  }

  window.savePl=function(){
    var plId=document.getElementById('pl-select').value;
    var name=document.getElementById('pl-name').value.trim();
    if(!plId&&!name){banner('Enter a price list name or select an existing one.','danger');return;}

    var items=[];
    document.querySelectorAll('.item-row').forEach(function(row){
      var id=row.id.replace('row-','');
      var sel=document.getElementById('ri-'+id);
      var iid=sel&&sel.value;
      if(!iid) return;
      items.push({
        item_id:parseInt(iid,10),
        unit_price:parseFloat(document.getElementById('rp-'+id).value||0)||0,
        discount_pct:parseFloat(document.getElementById('rd-'+id).value||0)||0
      });
    });
    if(!items.length){banner('Add at least one material.','danger');return;}

    var btn=document.getElementById('save-btn');
    btn.disabled=true; btn.textContent='Saving…';
    var payload={
      price_list_id:plId?parseInt(plId,10):null,
      name:name,
      valid_from:document.getElementById('pl-from').value||null,
      valid_to:document.getElementById('pl-to').value||null,
      items:items
    };
    call('create',payload).then(function(r){
      btn.disabled=false; btn.textContent='💾 Save Price List';
      if(!r.ok){banner(r.error,'danger');return;}
      banner((r.data&&(r.data.message||r.data.data&&r.data.data.message))||'✅ Price list saved.','ok');
      // refresh
      call('lookups').then(function(r2){
        if(!r2.ok) return;
        var d=r2.data||{};
        materials=d.materials||d.items||[];
        priceLists=d.price_lists||[];
        populatePlSelect();
        renderList();
      });
    });
  };

  /* ── start ── */
  new QWebChannel(qt.webChannelTransport,function(ch){
    bridge=ch.objects.bridge;
    bridge.response.connect(function(id,js){
      var cb=pending[id]; delete pending[id];
      if(cb) cb(JSON.parse(js));
    });
    init();
  });
})();
</script>
</body>
</html>
"""
