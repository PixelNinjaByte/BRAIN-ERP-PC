"""
Brain ERP – MM03 / DIMA (Display Material Master) screen, rendered with QWebEngineView.

Tcode : DIMA  (alias: MM03)
Purpose: Search / list all materials and display full material detail
         with Stock, Purchases, Where-used, Alt-UOM and History tabs.

The whole UI is HTML/CSS/JS (modelled on material_master.php MM03 section).
Python only does two things:
  * hosts the QWebEngineView
  * exposes a small QWebChannel bridge so the page can call ApiClient
    without blocking the Qt GUI thread.

API methods exposed to JS:
  lookups         → mm_lookups()
  list            → mm_list(filters)
  detail          → mm_detail(id)
  stock           → mm_stock(id)
  purchases       → mm_purchases(id)
  where_used      → mm_where_used(id)
  alt_uom         → mm_alt_uom(id)
  history         → mm_history(id)

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
        api = self.api

        if method == "lookups":
            fn = api.mm_lookups
        elif method == "list":
            fn = lambda: api.mm_list(payload)
        elif method == "detail":
            fn = lambda: api.mm_detail(payload.get("id"))
        elif method == "stock":
            fn = lambda: api.mm_stock(payload.get("id"))
        elif method == "purchases":
            fn = lambda: api.mm_purchases(payload.get("id"))
        elif method == "where_used":
            fn = lambda: api.mm_where_used(payload.get("id"))
        elif method == "alt_uom":
            fn = lambda: api.mm_alt_uom(payload.get("id"))
        elif method == "history":
            fn = lambda: api.mm_history(payload.get("id"))
        else:
            self.response.emit(
                req_id,
                json.dumps({"ok": False, "error": f"Unknown method: {method}"}),
            )
            return

        QThreadPool.globalInstance().start(_Job(fn, req_id, self))


# ──────────────────────────────────────────────────────────────────────────────
# Public widget
# ──────────────────────────────────────────────────────────────────────────────

class MM03Widget(QWidget):
    """Top-level widget hosted by MainWindow for DIMA / MM03."""

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
# The UI  (self-contained: no CDN needed)
# ──────────────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DIMA – Material Master Display</title>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%}
  body{font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;font-size:14px;color:#111827;background:#f4f6fa;display:flex;flex-direction:column;height:100%}
  /* ── layout ── */
  .topbar{display:flex;align-items:center;gap:10px;padding:10px 18px;background:#fff;border-bottom:1px solid #e5e7eb;flex-shrink:0}
  .crumbs{font-size:12px;color:#6b7280}
  h1{font-size:17px;margin:0;font-weight:700;flex:1}
  .body{display:flex;flex:1;overflow:hidden}
  /* ── list pane ── */
  .list-pane{width:520px;min-width:300px;border-right:1px solid #e5e7eb;display:flex;flex-direction:column;background:#fff;flex-shrink:0}
  .filter-bar{padding:10px 12px;border-bottom:1px solid #eef0f3;display:grid;grid-template-columns:1fr auto;gap:8px}
  .filter-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:6px}
  .tbl-wrap{flex:1;overflow-y:auto}
  table.mat-list{width:100%;border-collapse:collapse;font-size:12.5px}
  table.mat-list thead th{position:sticky;top:0;background:#1565c0;color:#fff;padding:7px 10px;text-align:left;white-space:nowrap;cursor:pointer;user-select:none}
  table.mat-list thead th.r{text-align:right}
  table.mat-list tbody tr{cursor:pointer;border-bottom:1px solid #f0f2f5}
  table.mat-list tbody tr:hover{background:#eff6ff}
  table.mat-list tbody tr.sel{background:#dbeafe}
  table.mat-list td{padding:6px 10px;vertical-align:top}
  table.mat-list td.code{font-weight:700;color:#1565c0;white-space:nowrap}
  table.mat-list td.r{text-align:right}
  .mat-name{font-weight:600;line-height:1.3}
  .mat-sub{font-size:11px;color:#6b7280}
  .pager{display:flex;gap:6px;align-items:center;padding:8px 12px;border-top:1px solid #eef0f3;font-size:12.5px;flex-shrink:0}
  .pager button{padding:3px 10px;border:1px solid #d1d5db;border-radius:4px;background:#fff;cursor:pointer;font-size:12px}
  .pager button:disabled{opacity:.45;cursor:default}
  /* ── summary badges ── */
  .stat-row{display:flex;gap:8px;padding:8px 12px;border-bottom:1px solid #eef0f3;flex-shrink:0;overflow-x:auto}
  .stat{text-align:center;padding:5px 10px;border-radius:6px;background:#f8f9fa;cursor:pointer;flex-shrink:0}
  .stat:hover{background:#eff6ff}
  .stat.active-f{background:#dbeafe;border:1px solid #93c5fd}
  .stat-num{font-size:16px;font-weight:700}
  .stat-lbl{font-size:10.5px;color:#6b7280}
  /* ── detail pane ── */
  .detail-pane{flex:1;overflow-y:auto;padding:0}
  .detail-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:#9ca3af;gap:8px}
  .detail-empty svg{opacity:.25}
  /* detail header */
  .dh{display:flex;align-items:flex-start;gap:14px;padding:14px 18px 10px;border-bottom:1px solid #e5e7eb;background:#fff;position:sticky;top:0;z-index:10}
  .dh-code{font-family:Consolas,monospace;font-size:20px;font-weight:700;color:#1565c0}
  .dh-name{font-size:14px;color:#374151;margin-top:2px}
  .dh-badges{display:flex;gap:6px;margin-top:4px;flex-wrap:wrap}
  .dh-actions{margin-left:auto;display:flex;gap:8px;align-items:flex-start}
  /* tabs */
  .tabs{display:flex;gap:0;border-bottom:2px solid #e5e7eb;background:#fff;padding:0 18px;flex-shrink:0}
  .tab{padding:8px 14px;font-size:13px;font-weight:600;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;white-space:nowrap}
  .tab.on{color:#1565c0;border-bottom-color:#1565c0}
  .tab-body{padding:16px 18px}
  /* cards */
  .card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.09);margin-bottom:14px;overflow:hidden}
  .card-h{padding:7px 14px;font-weight:700;font-size:12.5px;border-bottom:1px solid #eef0f3;background:#fff}
  .card-h.blue{background:linear-gradient(135deg,#1565c0,#1976d2);color:#fff;border:0}
  .card-b{padding:12px 14px}
  /* key-value table */
  table.kv{width:100%;border-collapse:collapse;font-size:13px}
  table.kv tr td{padding:5px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}
  table.kv tr:last-child td{border:0}
  table.kv td:first-child{color:#6b7280;width:46%;font-size:12.5px}
  table.kv td:last-child{font-weight:600}
  /* data table */
  table.dt{width:100%;border-collapse:collapse;font-size:12.5px}
  table.dt thead th{background:#f1f5f9;padding:6px 10px;text-align:left;border-bottom:2px solid #e5e7eb;white-space:nowrap}
  table.dt thead th.r{text-align:right}
  table.dt td{padding:5px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}
  table.dt td.r{text-align:right}
  table.dt tbody tr:hover{background:#f9fafb}
  /* misc */
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:1200px){.row3{grid-template-columns:1fr 1fr}}
  @media(max-width:900px){.row3,.row2{grid-template-columns:1fr}}
  .badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;background:#6b7280;color:#fff}
  .badge.raw{background:#1565c0}.badge.fg{background:#2e7d32}.badge.wip{background:#e65100}.badge.cons{background:#6a1b9a}.badge.spare{background:#00695c}.badge.trade{background:#37474f}
  .badge.ok{background:#15803d}.badge.blocked{background:#d97706;color:#fff}.badge.inactive{background:#6b7280}
  .money{color:#2e7d32;font-weight:700}
  .danger{color:#dc2626;font-weight:700}
  .muted{color:#6b7280}
  .hint{font-size:11.5px;color:#6b7280;margin-top:3px}
  label{display:block;font-weight:600;font-size:12px;margin-bottom:3px}
  input[type=text],select{padding:5px 8px;font:inherit;font-size:13px;border:1px solid #ced4da;border-radius:5px;outline:none;width:100%}
  input:focus,select:focus{border-color:#1976d2;box-shadow:0 0 0 3px rgba(25,118,210,.15)}
  .btn{display:inline-block;border:1px solid transparent;border-radius:6px;padding:6px 12px;font:inherit;font-weight:600;font-size:13px;cursor:pointer}
  .btn-primary{background:#1565c0;color:#fff}.btn-primary:hover{background:#0d47a1}
  .btn-gray{background:#fff;color:#374151;border-color:#d1d5db}.btn-gray:hover{background:#f3f4f6}
  .btn-sm{font-size:12px;padding:4px 10px}
  .alert{padding:9px 13px;border-radius:6px;font-size:13px;margin-bottom:10px}
  .alert-danger{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
  .alert-info{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe}
  .spin{display:inline-block;width:16px;height:16px;border:2px solid #93c5fd;border-top-color:#1565c0;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle}
  @keyframes spin{to{transform:rotate(360deg)}}
  .empty-row td{text-align:center;padding:28px;color:#9ca3af}
  /* stock table highlight */
  .low-stock{color:#dc2626;font-weight:700}
  .stock-pct{font-size:11px;color:#6b7280}
</style>
</head>
<body>

<!-- ── top bar ─────────────────────────────────────────── -->
<div class="topbar">
  <div style="flex:1">
    <div class="crumbs">MM › Materials</div>
    <h1>Material Master Display — DIMA / MM03</h1>
  </div>
  <div id="topbar-actions"></div>
</div>

<!-- ── main body ──────────────────────────────────────── -->
<div class="body">

  <!-- ════ LIST PANE ════ -->
  <div class="list-pane">

    <!-- summary stat badges -->
    <div class="stat-row" id="stat-row">
      <div class="stat active-f" data-st="active" onclick="filterStat(this)">
        <div class="stat-num" id="cnt-active">—</div><div class="stat-lbl">Active</div>
      </div>
      <div class="stat" data-st="" data-cat="RAW" onclick="filterStat(this)">
        <div class="stat-num" style="color:#1565c0" id="cnt-raw">—</div><div class="stat-lbl">RAW</div>
      </div>
      <div class="stat" data-st="" data-cat="FG" onclick="filterStat(this)">
        <div class="stat-num" style="color:#2e7d32" id="cnt-fg">—</div><div class="stat-lbl">FG</div>
      </div>
      <div class="stat" data-st="" data-cat="WIP" onclick="filterStat(this)">
        <div class="stat-num" style="color:#e65100" id="cnt-wip">—</div><div class="stat-lbl">WIP</div>
      </div>
      <div class="stat" data-st="" data-cat="CONSUMABLE" onclick="filterStat(this)">
        <div class="stat-num" style="color:#6a1b9a" id="cnt-cons">—</div><div class="stat-lbl">CONS</div>
      </div>
      <div class="stat" data-st="" data-cat="SPARE" onclick="filterStat(this)">
        <div class="stat-num" style="color:#00695c" id="cnt-spare">—</div><div class="stat-lbl">SPARE</div>
      </div>
      <div class="stat" data-st="blocked" onclick="filterStat(this)">
        <div class="stat-num" style="color:#d97706" id="cnt-blocked">—</div><div class="stat-lbl">Blocked</div>
      </div>
      <div class="stat" data-st="inactive" onclick="filterStat(this)">
        <div class="stat-num" id="cnt-inactive">—</div><div class="stat-lbl">Inactive</div>
      </div>
      <div class="stat" data-st="low" onclick="filterStat(this)">
        <div class="stat-num" style="color:#dc2626" id="cnt-low">—</div><div class="stat-lbl">Low stock</div>
      </div>
    </div>

    <!-- filter bar -->
    <div class="filter-bar">
      <div>
        <div class="filter-row">
          <div><label>Search</label><input type="text" id="f-search" placeholder="Code / name / HSN / brand" oninput="debouncedLoad()"></div>
          <div><label>Category</label>
            <select id="f-cat" onchange="loadList()">
              <option value="">All</option>
              <option value="RAW">RAW</option><option value="FG">FG</option><option value="WIP">WIP</option>
              <option value="CONSUMABLE">CONSUMABLE</option><option value="SPARE">SPARE</option><option value="TRADING">TRADING</option>
            </select>
          </div>
          <div><label>Status</label>
            <select id="f-st" onchange="loadList()">
              <option value="active">Active</option>
              <option value="low">Below reorder</option>
              <option value="blocked">Blocked</option>
              <option value="inactive">Inactive</option>
              <option value="all">All</option>
            </select>
          </div>
        </div>
      </div>
      <div style="display:flex;align-items:flex-end;gap:6px">
        <button class="btn btn-gray btn-sm" onclick="clearFilters()">Clear</button>
        <button class="btn btn-primary btn-sm" onclick="loadList()">🔍 Filter</button>
      </div>
    </div>

    <!-- list table -->
    <div class="tbl-wrap">
      <table class="mat-list">
        <thead>
          <tr>
            <th onclick="toggleSort('code')">Code <span id="s-code"></span></th>
            <th onclick="toggleSort('name')">Description <span id="s-name"></span></th>
            <th onclick="toggleSort('cat')">Cat <span id="s-cat"></span></th>
            <th class="r" onclick="toggleSort('stock')">Stock <span id="s-stock"></span></th>
            <th class="r" onclick="toggleSort('map')">MAP <span id="s-map"></span></th>
          </tr>
        </thead>
        <tbody id="mat-tbody">
          <tr><td colspan="5" style="text-align:center;padding:20px;color:#9ca3af"><span class="spin"></span> Loading…</td></tr>
        </tbody>
      </table>
    </div>

    <!-- pager -->
    <div class="pager">
      <button id="btn-prev" onclick="prevPage()" disabled>‹ Prev</button>
      <span id="pager-info" class="muted">—</span>
      <button id="btn-next" onclick="nextPage()" disabled>Next ›</button>
    </div>
  </div>

  <!-- ════ DETAIL PANE ════ -->
  <div class="detail-pane" id="detail-pane">
    <div class="detail-empty" id="detail-empty">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="#9ca3af"><path d="M20 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2ZM4 5h16M9 3h6"/></svg>
      <div style="font-size:15px;font-weight:600">Select a material to view details</div>
      <div class="muted" style="font-size:13px">Click any row in the list on the left</div>
    </div>
    <div id="detail-content" style="display:none">

      <!-- sticky header -->
      <div class="dh">
        <div>
          <div class="dh-code" id="dh-code">—</div>
          <div class="dh-name" id="dh-name">—</div>
          <div class="dh-badges" id="dh-badges"></div>
        </div>
        <div class="dh-actions">
          <div id="detail-msg"></div>
        </div>
      </div>

      <!-- tabs -->
      <div class="tabs" id="tabs">
        <div class="tab on" data-tab="basic" onclick="switchTab(this)">📋 Basic</div>
        <div class="tab" data-tab="stock" onclick="switchTab(this)">📦 Stock</div>
        <div class="tab" data-tab="purchases" onclick="switchTab(this)">🛒 Purchases</div>
        <div class="tab" data-tab="whereused" onclick="switchTab(this)">🔗 Where-used</div>
        <div class="tab" data-tab="altuom" onclick="switchTab(this)">↔ Alt UOM</div>
        <div class="tab" data-tab="history" onclick="switchTab(this)">📜 History</div>
      </div>

      <div class="tab-body">

        <!-- ── Basic tab ── -->
        <div id="tab-basic">
          <div id="basic-err"></div>
          <div class="row3">
            <div>
              <div class="card">
                <div class="card-h blue">📦 Basic Data</div>
                <div class="card-b">
                  <table class="kv" id="kv-basic"></table>
                </div>
              </div>
            </div>
            <div>
              <div class="card">
                <div class="card-h">🏷️ Classification &amp; Tax</div>
                <div class="card-b">
                  <table class="kv" id="kv-class"></table>
                </div>
              </div>
              <div class="card">
                <div class="card-h">💰 Valuation &amp; Pricing</div>
                <div class="card-b">
                  <table class="kv" id="kv-price"></table>
                </div>
              </div>
            </div>
            <div>
              <div class="card">
                <div class="card-h">📈 MRP / Planning</div>
                <div class="card-b">
                  <table class="kv" id="kv-mrp"></table>
                </div>
              </div>
              <div class="card">
                <div class="card-h">🛡️ Quality &amp; Storage</div>
                <div class="card-b">
                  <table class="kv" id="kv-quality"></table>
                </div>
              </div>
              <div class="card">
                <div class="card-h">🔘 Status</div>
                <div class="card-b">
                  <table class="kv" id="kv-status"></table>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Stock tab ── -->
        <div id="tab-stock" style="display:none">
          <div id="stock-err"></div>
          <div id="stock-loading" class="muted"><span class="spin"></span> Loading stock…</div>
          <div id="stock-wrap" style="display:none">
            <div class="card">
              <div class="card-h blue">📦 Stock by Warehouse</div>
              <div class="card-b" style="padding:0">
                <table class="dt" id="stock-tbl">
                  <thead><tr>
                    <th>Warehouse</th>
                    <th class="r">On-Hand</th>
                    <th class="r">Reserved</th>
                    <th class="r">Available</th>
                    <th class="r">Avg Cost</th>
                    <th class="r">Stock Value</th>
                  </tr></thead>
                  <tbody id="stock-tbody"></tbody>
                  <tfoot id="stock-tfoot" style="font-weight:700;background:#f8fafc"></tfoot>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Purchases tab ── -->
        <div id="tab-purchases" style="display:none">
          <div id="po-err"></div>
          <div id="po-loading" class="muted"><span class="spin"></span> Loading purchases…</div>
          <div id="po-wrap" style="display:none">
            <div class="card">
              <div class="card-h blue">🛒 Recent Purchase Orders (last 10)</div>
              <div class="card-b" style="padding:0">
                <table class="dt" id="po-tbl">
                  <thead><tr>
                    <th>PO Number</th><th>Date</th><th>Supplier</th>
                    <th class="r">Qty Ord.</th><th class="r">Qty Recv.</th>
                    <th class="r">Unit Price</th><th>Status</th>
                  </tr></thead>
                  <tbody id="po-tbody"></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Where-used tab ── -->
        <div id="tab-whereused" style="display:none">
          <div id="wu-err"></div>
          <div id="wu-loading" class="muted"><span class="spin"></span> Loading…</div>
          <div id="wu-wrap" style="display:none">
            <div class="row2">
              <div class="card">
                <div class="card-h">📋 Bills of Material</div>
                <div class="card-b" style="padding:0">
                  <table class="dt"><thead><tr><th>BOM</th><th>Parent</th><th class="r">Qty</th><th>UOM</th></tr></thead>
                  <tbody id="wu-bom-tbody"></tbody></table>
                </div>
              </div>
              <div class="card">
                <div class="card-h">📑 Open PRs / POs</div>
                <div class="card-b" style="padding:0">
                  <table class="dt"><thead><tr><th>Type</th><th>Number</th><th>Date</th><th class="r">Qty</th></tr></thead>
                  <tbody id="wu-doc-tbody"></tbody></table>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Alt UOM tab ── -->
        <div id="tab-altuom" style="display:none">
          <div id="au-err"></div>
          <div id="au-loading" class="muted"><span class="spin"></span> Loading…</div>
          <div id="au-wrap" style="display:none">
            <div class="card">
              <div class="card-h blue">↔ Alternate Units of Measure</div>
              <div class="card-b" style="padding:0">
                <table class="dt">
                  <thead><tr><th>Alt Unit</th><th>= (Base qty)</th><th>Base Unit</th></tr></thead>
                  <tbody id="au-tbody"></tbody>
                </table>
              </div>
            </div>
            <div class="hint">Use the MM02 screen to add or change alternate units.</div>
          </div>
        </div>

        <!-- ── History tab ── -->
        <div id="tab-history" style="display:none">
          <div id="hist-err"></div>
          <div id="hist-loading" class="muted"><span class="spin"></span> Loading…</div>
          <div id="hist-wrap" style="display:none">
            <div class="card">
              <div class="card-h blue">📜 Change History (last 50)</div>
              <div class="card-b" style="padding:0">
                <table class="dt">
                  <thead><tr><th>Date / Time</th><th>User</th><th>Action</th><th>Field</th><th>Old</th><th>New</th></tr></thead>
                  <tbody id="hist-tbody"></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

      </div><!-- /tab-body -->
    </div><!-- /detail-content -->
  </div><!-- /detail-pane -->

</div><!-- /body -->

<script>
(function(){
  /* ── bridge ───────────────────────────────────────────── */
  var bridge, pending = {}, seq = 0;
  function call(method, payload){
    return new Promise(function(resolve){
      var id = String(++seq);
      pending[id] = resolve;
      bridge.call(id, method, JSON.stringify(payload || {}));
    });
  }

  /* ── utils ───────────────────────────────────────────── */
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function fmt(n, dp){ return n == null ? '—' : parseFloat(n).toFixed(dp == null ? 2 : dp); }
  function fmtQty(n){ var v = parseFloat(n||0); return v === 0 ? '<span class="muted">0</span>' : fmt(v,3); }
  function fmtMoney(n){ return '₹' + fmt(n, 2); }
  function kv(label, val){ return '<tr><td>' + esc(label) + '</td><td>' + val + '</td></tr>'; }
  function kvRaw(label, html){ return '<tr><td>' + esc(label) + '</td><td>' + html + '</td></tr>'; }
  function emptyRow(cols, msg){ return '<tr class="empty-row"><td colspan="'+cols+'">'+esc(msg||'No records.')+'</td></tr>'; }

  function catBadge(cat){
    var map={RAW:'raw',FG:'fg',WIP:'wip',CONSUMABLE:'cons',SPARE:'spare',TRADING:'trade'};
    return '<span class="badge '+(map[cat]||'')+'">' + esc(cat) + '</span>';
  }
  function statusBadge(row){
    if (!row.is_active) return '<span class="badge inactive">Inactive</span>';
    if (row.block_status) return '<span class="badge blocked">Blocked: '+esc(row.block_status)+'</span>';
    return '<span class="badge ok">Active</span>';
  }

  /* ── sort state ───────────────────────────────────────── */
  var sortKey = 'code', sortDir = 'asc';
  function toggleSort(key){
    if(sortKey===key) sortDir = sortDir==='asc' ? 'desc' : 'asc';
    else { sortKey=key; sortDir='asc'; }
    ['code','name','cat','stock','map'].forEach(function(k){
      document.getElementById('s-'+k).textContent = '';
    });
    document.getElementById('s-'+sortKey).textContent = sortDir==='asc' ? ' ▲' : ' ▼';
    page = 1; loadList();
  }

  /* ── filter / stat ────────────────────────────────────── */
  var activeStat = document.querySelector('.stat.active-f');
  function filterStat(el){
    if(activeStat) activeStat.classList.remove('active-f');
    el.classList.add('active-f'); activeStat = el;
    var st = el.getAttribute('data-st') || '';
    var cat = el.getAttribute('data-cat') || '';
    document.getElementById('f-st').value = st;
    document.getElementById('f-cat').value = cat;
    page = 1; loadList();
  }
  function clearFilters(){
    document.getElementById('f-search').value = '';
    document.getElementById('f-cat').value = '';
    document.getElementById('f-st').value = 'active';
    page = 1; loadList();
  }

  /* ── debounce ─────────────────────────────────────────── */
  var _dbTimer;
  function debouncedLoad(){ clearTimeout(_dbTimer); _dbTimer = setTimeout(function(){ page=1; loadList(); }, 350); }

  /* ── pagination ───────────────────────────────────────── */
  var page = 1, totalPages = 1;
  function prevPage(){ if(page>1){ page--; loadList(); } }
  function nextPage(){ if(page<totalPages){ page++; loadList(); } }
  window.prevPage = prevPage; window.nextPage = nextPage;
  window.filterStat = filterStat; window.clearFilters = clearFilters; window.debouncedLoad = debouncedLoad;
  window.toggleSort = toggleSort;

  /* ── load list ────────────────────────────────────────── */
  function loadList(){
    var tbody = document.getElementById('mat-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:16px;color:#9ca3af"><span class="spin"></span></td></tr>';
    document.getElementById('btn-prev').disabled = true;
    document.getElementById('btn-next').disabled = true;

    var filters = {
      search: document.getElementById('f-search').value.trim(),
      cat:    document.getElementById('f-cat').value,
      st:     document.getElementById('f-st').value,
      sort:   sortKey,
      dir:    sortDir,
      page:   page,
      per_page: 30
    };
    call('list', filters).then(function(r){
      if(!r.ok){ tbody.innerHTML = '<tr><td colspan="5" class="empty-row">'+esc(r.error)+'</td></tr>'; return; }
      var d = r.data || {};
      var rows = d.rows || d.data || [];
      totalPages = d.total_pages || 1;
      var total  = d.total_rows  || rows.length;

      /* update stats from summary if provided */
      if(d.summary){
        var s = d.summary;
        document.getElementById('cnt-active').textContent   = s.active   ?? '—';
        document.getElementById('cnt-raw').textContent      = s.raw      ?? '—';
        document.getElementById('cnt-fg').textContent       = s.fg       ?? '—';
        document.getElementById('cnt-wip').textContent      = s.wip      ?? '—';
        document.getElementById('cnt-cons').textContent     = s.consumable?? '—';
        document.getElementById('cnt-spare').textContent    = s.spare    ?? '—';
        document.getElementById('cnt-blocked').textContent  = s.blocked  ?? '—';
        document.getElementById('cnt-inactive').textContent = s.inactive ?? '—';
        document.getElementById('cnt-low').textContent      = s.low_stock ?? '—';
      }

      /* pagination */
      document.getElementById('btn-prev').disabled = page <= 1;
      document.getElementById('btn-next').disabled = page >= totalPages;
      document.getElementById('pager-info').textContent =
        total + ' material' + (total!==1?'s':'') +
        (totalPages>1 ? ' — page ' + page + ' / ' + totalPages : '');

      if(!rows.length){ tbody.innerHTML = emptyRow(5,'No materials found.'); return; }
      tbody.innerHTML = rows.map(function(it){
        var low = parseFloat(it.reorder_point||0)>0 && parseFloat(it.stock_total||0)<=parseFloat(it.reorder_point||0);
        return '<tr data-id="'+esc(it.id)+'" onclick="selectMat('+it.id+')">' +
          '<td class="code">'+esc(it.item_code)+'</td>' +
          '<td><div class="mat-name">'+esc((it.item_name||'').substring(0,38))+(it.item_name&&it.item_name.length>38?'…':'')+'</div>'+
              (it.mg_name?'<div class="mat-sub">'+esc(it.mg_name)+'</div>':'')+'</td>' +
          '<td>'+catBadge(it.item_category)+'</td>' +
          '<td class="r'+(low?' low-stock':'')+'">'+fmtQty(it.stock_total)+(it.uom?' '+esc(it.uom):'')+(low?' ⚠':'')+'</td>' +
          '<td class="r money">'+fmtMoney(it.moving_avg_price)+'</td>' +
          '</tr>';
      }).join('');
    });
  }

  /* ── select material ──────────────────────────────────── */
  var currentId = null;
  function selectMat(id){
    currentId = id;
    /* highlight row */
    document.querySelectorAll('#mat-tbody tr').forEach(function(r){ r.classList.toggle('sel', +r.getAttribute('data-id')===id); });
    document.getElementById('detail-empty').style.display   = 'none';
    document.getElementById('detail-content').style.display = '';
    /* reset tabs to basic */
    switchTabById('basic');
    loadDetail(id);
  }
  window.selectMat = selectMat;

  /* ── tabs ─────────────────────────────────────────────── */
  var loadedTabs = {};
  function switchTab(el){
    document.querySelectorAll('.tab').forEach(function(t){ t.classList.remove('on'); });
    el.classList.add('on');
    var name = el.getAttribute('data-tab');
    ['basic','stock','purchases','whereused','altuom','history'].forEach(function(t){
      document.getElementById('tab-'+t).style.display = t===name ? '' : 'none';
    });
    if(currentId && !loadedTabs[name]){
      loadedTabs[name] = true;
      if(name==='stock')      loadStock(currentId);
      if(name==='purchases')  loadPurchases(currentId);
      if(name==='whereused')  loadWhereUsed(currentId);
      if(name==='altuom')     loadAltUom(currentId);
      if(name==='history')    loadHistory(currentId);
    }
  }
  function switchTabById(name){
    loadedTabs = {};
    document.querySelectorAll('.tab').forEach(function(t){
      t.classList.toggle('on', t.getAttribute('data-tab')===name);
    });
    ['basic','stock','purchases','whereused','altuom','history'].forEach(function(t){
      document.getElementById('tab-'+t).style.display = t===name ? '' : 'none';
    });
  }
  window.switchTab = switchTab;

  /* ── detail / basic ───────────────────────────────────── */
  function loadDetail(id){
    document.getElementById('kv-basic').innerHTML   = '<tr><td colspan="2"><span class="spin"></span></td></tr>';
    document.getElementById('kv-class').innerHTML   = '';
    document.getElementById('kv-price').innerHTML   = '';
    document.getElementById('kv-mrp').innerHTML     = '';
    document.getElementById('kv-quality').innerHTML = '';
    document.getElementById('kv-status').innerHTML  = '';

    call('detail',{id:id}).then(function(r){
      if(!r.ok){ document.getElementById('basic-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var d = r.data || {};
      var it = d.data || d || {};

      /* header */
      document.getElementById('dh-code').textContent = it.item_code || '—';
      document.getElementById('dh-name').textContent = it.item_name || '—';
      var badges = catBadge(it.item_category) + ' ' + statusBadge(it);
      if(it.block_status) badges += ' <span class="badge" style="background:#fca5a5;color:#7f1d1d">🚫 '+esc(it.block_status)+'</span>';
      document.getElementById('dh-badges').innerHTML = badges;

      /* basic */
      document.getElementById('kv-basic').innerHTML =
        kv('Material Code',  '<span style="font-family:Consolas;font-weight:700">'+esc(it.item_code)+'</span>') +
        kv('Description',    esc(it.item_name)) +
        (it.description ? kv('Long Description', esc(it.description)) : '') +
        kv('Material Type',  esc(it.item_type)) +
        kv('Category',       esc(it.item_category)) +
        (it.manufacturer ? kv('Manufacturer / Brand', esc(it.manufacturer)) : '');

      /* classification & tax */
      document.getElementById('kv-class').innerHTML =
        kv('Material Group', esc(it.mg_name || it.material_group_id || '—')) +
        kv('GL Account',     esc(it.account_code ? it.account_code + ' — ' + (it.account_name||'') : 'Company default')) +
        kv('Base UOM',       esc(it.uom_code ? it.uom_code + ' (' + it.uom_name + ')' : it.base_uom_id || '—')) +
        kv('HSN / SAC',      esc(it.hsn_code || '—')) +
        kv('GST Rate',       esc(it.gst_rate != null ? it.gst_rate + '%' : '—'));

      /* valuation */
      document.getElementById('kv-price').innerHTML =
        kv('Valuation Method', 'Moving Average Price') +
        kvRaw('Standard Cost',    '<span class="money">'+fmtMoney(it.standard_price)+'</span>') +
        kvRaw('Moving Avg Price', '<span class="money">'+fmtMoney(it.moving_avg_price)+'</span>');

      /* MRP */
      document.getElementById('kv-mrp').innerHTML =
        kv('Min Stock',    fmt(it.min_stock,3)) +
        kv('Max Stock',    fmt(it.max_stock,3)) +
        kv('Reorder Point',fmt(it.reorder_point,3)) +
        kv('Lead Time',    (it.lead_time_days||0)+' days') +
        kv('Lot Size',     fmt(it.lot_size,3));

      /* quality */
      document.getElementById('kv-quality').innerHTML =
        kv('QC on GRN',     it.qc_required   ? '✅ Required'    : '— Not required') +
        kv('Batch Managed', it.batch_managed  ? '✅ Yes'         : '— No') +
        kv('Shelf Life',    it.shelf_life_days > 0 ? it.shelf_life_days+' days' : '— No expiry') +
        kv('Gross Weight',  it.gross_weight > 0 ? fmt(it.gross_weight,3)+' kg' : '—');

      /* status */
      document.getElementById('kv-status').innerHTML =
        kvRaw('Active',       it.is_active ? '<span style="color:#15803d">✅ Active</span>' : '<span class="muted">⬜ Inactive</span>') +
        kv('Block Status',   it.block_status || 'Not blocked') +
        kv('Created',        it.created_at ? it.created_at.split('T')[0] : '—');
    });
  }

  /* ── stock tab ────────────────────────────────────────── */
  function loadStock(id){
    document.getElementById('stock-loading').style.display = '';
    document.getElementById('stock-wrap').style.display    = 'none';
    call('stock',{id:id}).then(function(r){
      document.getElementById('stock-loading').style.display = 'none';
      if(!r.ok){ document.getElementById('stock-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var rows = r.data || []; if(r.data && r.data.data) rows = r.data.data;
      document.getElementById('stock-wrap').style.display = '';
      var totQty=0, totVal=0;
      var html = rows.length ? rows.map(function(s){
        var avail = (parseFloat(s.qty_on_hand||0)-parseFloat(s.qty_reserved||0));
        totQty += parseFloat(s.qty_on_hand||0);
        totVal += parseFloat(s.qty_on_hand||0) * parseFloat(s.avg_cost||0);
        return '<tr>' +
          '<td><b>'+esc(s.wh_code||s.warehouse_code)+'</b> <span class="muted">'+esc(s.wh_name||s.warehouse_name||'')+'</span></td>' +
          '<td class="r">'+fmtQty(s.qty_on_hand)+'</td>' +
          '<td class="r muted">'+fmtQty(s.qty_reserved)+'</td>' +
          '<td class="r">'+(avail<0?'<span class="danger">':'')+fmt(avail,3)+(avail<0?'</span>':'')+'</td>' +
          '<td class="r money">'+fmtMoney(s.avg_cost)+'</td>' +
          '<td class="r money">'+fmtMoney(parseFloat(s.qty_on_hand||0)*parseFloat(s.avg_cost||0))+'</td>' +
          '</tr>';
      }).join('') : emptyRow(6,'No stock found.');
      document.getElementById('stock-tbody').innerHTML = html;
      document.getElementById('stock-tfoot').innerHTML = rows.length>1 ?
        '<tr><td><b>Total</b></td><td class="r"><b>'+fmt(totQty,3)+'</b></td><td class="r muted">—</td><td class="r">—</td><td class="r">—</td><td class="r money"><b>'+fmtMoney(totVal)+'</b></td></tr>' : '';
    });
  }

  /* ── purchases tab ────────────────────────────────────── */
  function loadPurchases(id){
    document.getElementById('po-loading').style.display = '';
    document.getElementById('po-wrap').style.display    = 'none';
    call('purchases',{id:id}).then(function(r){
      document.getElementById('po-loading').style.display = 'none';
      if(!r.ok){ document.getElementById('po-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var rows = r.data || []; if(r.data && r.data.data) rows = r.data.data;
      document.getElementById('po-wrap').style.display = '';
      document.getElementById('po-tbody').innerHTML = rows.length ? rows.map(function(p){
        var pct = parseFloat(p.qty_ordered||0)>0 ? Math.round(100*parseFloat(p.qty_received||0)/parseFloat(p.qty_ordered||0)) : 0;
        return '<tr>' +
          '<td><b>'+esc(p.po_number)+'</b></td>' +
          '<td class="muted">'+esc((p.po_date||'').split('T')[0])+'</td>' +
          '<td>'+esc(p.supplier||'—')+'</td>' +
          '<td class="r">'+fmt(p.qty_ordered,3)+'</td>' +
          '<td class="r"><span class="'+(pct>=100?'money':pct>0?'':'muted')+'">'+fmt(p.qty_received,3)+'</span> <span class="stock-pct">('+pct+'%)</span></td>' +
          '<td class="r money">'+fmtMoney(p.unit_price)+'</td>' +
          '<td><span class="badge '+(p.status==='APPROVED'||p.status==='CLOSED'?'ok':'')+'">'+esc(p.status)+'</span></td>' +
          '</tr>';
      }).join('') : emptyRow(7,'No purchase orders found.');
    });
  }

  /* ── where-used tab ───────────────────────────────────── */
  function loadWhereUsed(id){
    document.getElementById('wu-loading').style.display = '';
    document.getElementById('wu-wrap').style.display    = 'none';
    call('where_used',{id:id}).then(function(r){
      document.getElementById('wu-loading').style.display = 'none';
      if(!r.ok){ document.getElementById('wu-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var d = r.data || {}; if(r.data && r.data.data) d = r.data.data;
      document.getElementById('wu-wrap').style.display = '';
      var boms = d.boms || d.bom || [];
      var docs = d.docs || d.documents || [];
      document.getElementById('wu-bom-tbody').innerHTML = boms.length ? boms.map(function(b){
        return '<tr><td>'+esc(b.bom_code||b.bom_number||b.id)+'</td><td>'+esc(b.parent_code||b.parent_name||'—')+'</td><td class="r">'+fmt(b.qty,3)+'</td><td>'+esc(b.uom||'—')+'</td></tr>';
      }).join('') : emptyRow(4,'Not in any BOM.');
      document.getElementById('wu-doc-tbody').innerHTML = docs.length ? docs.map(function(d){
        return '<tr><td><b>'+esc(d.doc_type||'—')+'</b></td><td>'+esc(d.doc_number||d.number||'—')+'</td><td class="muted">'+esc((d.doc_date||d.date||'').split('T')[0])+'</td><td class="r">'+fmt(d.qty,3)+'</td></tr>';
      }).join('') : emptyRow(4,'No open PRs / POs.');
    });
  }

  /* ── alt uom tab ──────────────────────────────────────── */
  function loadAltUom(id){
    document.getElementById('au-loading').style.display = '';
    document.getElementById('au-wrap').style.display    = 'none';
    call('alt_uom',{id:id}).then(function(r){
      document.getElementById('au-loading').style.display = 'none';
      if(!r.ok){ document.getElementById('au-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var rows = r.data || []; if(r.data && r.data.data) rows = r.data.data;
      document.getElementById('au-wrap').style.display = '';
      document.getElementById('au-tbody').innerHTML = rows.length ? rows.map(function(a){
        return '<tr><td><b>'+esc(a.alt_code||a.code)+'</b> — '+esc(a.alt_name||a.name||'')+'</td>' +
          '<td class="r"><b>'+fmt(a.factor,6)+'</b></td>' +
          '<td>'+esc(a.base_code||a.base_uom||'—')+'</td></tr>';
      }).join('') : emptyRow(3,'No alternate units defined.');
    });
  }

  /* ── history tab ──────────────────────────────────────── */
  function loadHistory(id){
    document.getElementById('hist-loading').style.display = '';
    document.getElementById('hist-wrap').style.display    = 'none';
    call('history',{id:id}).then(function(r){
      document.getElementById('hist-loading').style.display = 'none';
      if(!r.ok){ document.getElementById('hist-err').innerHTML='<div class="alert alert-danger">'+esc(r.error)+'</div>'; return; }
      var rows = r.data || []; if(r.data && r.data.data) rows = r.data.data;
      document.getElementById('hist-wrap').style.display = '';
      document.getElementById('hist-tbody').innerHTML = rows.length ? rows.map(function(h){
        return '<tr>' +
          '<td class="muted" style="white-space:nowrap">'+esc((h.created_at||h.changed_at||'').replace('T',' ').split('.')[0])+'</td>' +
          '<td>'+esc(h.username||h.user||'—')+'</td>' +
          '<td><span class="badge '+(h.action==='INSERT'?'ok':h.action==='DELETE'?'blocked':'')+'">'+esc(h.action||'—')+'</span></td>' +
          '<td>'+esc(h.field_name||h.field||'—')+'</td>' +
          '<td class="muted">'+esc(h.old_value||'—')+'</td>' +
          '<td>'+esc(h.new_value||'—')+'</td>' +
          '</tr>';
      }).join('') : emptyRow(6,'No history records found.');
    });
  }

  /* ── start ────────────────────────────────────────────── */
  new QWebChannel(qt.webChannelTransport, function(ch){
    bridge = ch.objects.bridge;
    bridge.response.connect(function(id, js){
      var cb = pending[id]; delete pending[id];
      if(cb) cb(JSON.parse(js));
    });
    loadList();
  });
})();
</script>
</body>
</html>
"""
