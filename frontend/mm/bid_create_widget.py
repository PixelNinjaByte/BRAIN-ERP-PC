"""
Brain ERP - Create Bid / Tender (BID01).

Port of bid_create.php (MSME ERP / Prigag Online Pvt. Ltd.).
Rendered with QWebEngineView + QWebChannel, bootstrap5 / bootstrap-icons CDN
styling in the house look (white cards, gradient blue card headers).

Same layout as the PHP screen:
  Header Details (type, requirement, mode, dates, purch. group, storage
  location, Category > Unit > Department cost-centre cascade)
  Tabs: Bidding Details | Purchase Requisition Details | Service Items
  Choosing a PR pre-fills the Service Items from its lines.

ApiClient methods used
  EXISTING : pr_list
  NEW (hasattr()-guarded - the screen shows a clear message if missing)
    bid_lookups()   -> {"categories":[{id,code,name}], "units":[{id,code,name,category_id}],
                        "departments":[{id,code,name,plant_id}], "warehouses":[{id,code,name}],
                        "uoms":[{id,code}]}
    pr_get(pr_id)   -> PR dict with "lines" (same method ME41 uses)
    bid_create(payload) -> {"id"/"bid_id", "bid_number"}  (optionally inside {"data": ...})

bid_create payload (backend generates bid_number, status NEW, created_by):
    bid_type, requirement_type, mode_of_bid, sche_delivery_days, bid_creation_date,
    warehouse_id, purch_group, category_id, unit_id, department_id,
    bid_publish_date, bid_stage, bid_security_applic(0/1), bid_security_amount,
    bid_security_amount_fc, foreign_curr_appli(0/1), tax_rate, e_proc_applicable(0/1),
    bid_spec_applic(0/1), bid_spec_cost, bid_spec_cost_fc, download_start_dt,
    bid_subm_end_dt, bid_open_dt ("YYYY-MM-DD HH:MM:SS"), title_of_bid, bid_value,
    e_reverse_auction(0/1), pr_id,
    items: [{description, qty, uom_id, rate, value, pr_line_id}]

NOTE: msme_erp_render_cost_center_fields() was not available, so the cost-centre
block is assumed to be category_id / unit_id / department_id (the columns
bid_view.php joins on). Adjust if your backend differs.

Usage
    BidCreateWidget(api, on_created=lambda bid_id: ..., on_manage=lambda: ...)
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Optional

from PyQt6.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..api_client import ApiClient


# ── helpers ──────────────────────────────────────────────────────────────

class _Unavailable(RuntimeError):
    """Raised when an ApiClient method this screen needs does not exist yet."""


def _need(api: Any, name: str, hint: str = "") -> Any:
    fn = getattr(api, name, None)
    if fn is None:
        raise _Unavailable(f"ApiClient.{name}() is not available yet. {hint}".strip())
    return fn


def _unwrap(res: Any) -> Any:
    if isinstance(res, dict) and "data" in res:
        return res["data"]
    return res


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(f.readAll()).decode("utf-8", "replace")
        f.close()
        return data
    return ""


def _dt(v: Any) -> Optional[str]:
    """'2026-12-01T10:30' (datetime-local) -> '2026-12-01 10:30:00'."""
    v = str(v or "").strip()
    if not v:
        return None
    v = v.replace("T", " ")
    return v + ":00" if len(v) == 16 else v


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _flag(v: Any) -> int:
    return 1 if str(v) in ("1", "true", "True") else 0


class _Bridge(QObject):
    def __init__(self, handler: Callable[[str, dict], Any]):
        super().__init__()
        self._handler = handler

    @pyqtSlot(str, str, result=str)
    def call(self, op: str, payload: str) -> str:
        try:
            data = json.loads(payload) if payload else {}
            return json.dumps({"ok": True, "data": self._handler(op, data)}, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e) or type(e).__name__})


# ── widget ───────────────────────────────────────────────────────────────

class BidCreateWidget(QWidget):
    def __init__(
        self,
        api: ApiClient,
        on_created: Optional[Callable[[Optional[int]], None]] = None,
        on_manage: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.api = api
        self._on_created = on_created
        self._on_manage = on_manage

        self.view = QWebEngineView(self)
        self._channel = QWebChannel(self.view.page())
        self._bridge = _Bridge(self._handle)
        self._channel.registerObject("bridge", self._bridge)
        self.view.page().setWebChannel(self._channel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)

        self.reload()

    def reload(self) -> None:
        self.view.setHtml(_HTML.replace("__QWC__", _qwebchannel_js()), QUrl("https://brain-erp.local/"))

    def reload_data(self) -> None:
        self.view.page().runJavaScript("window.reloadLookups && window.reloadLookups();")

    # ── bridge handlers ──────────────────────────────────────────────────

    def _handle(self, op: str, d: dict) -> Any:
        if op == "init":
            return self._init()
        if op == "pr_lines":
            return self._pr_lines(int(d.get("pr_id") or 0))
        if op == "create":
            return self._create(d)
        if op == "manage":
            if self._on_manage:
                self._on_manage()
            return True
        raise ValueError(f"Unknown op: {op}")

    def _init(self) -> dict:
        warnings: list[str] = []
        lk = {"categories": [], "units": [], "departments": [], "warehouses": [], "uoms": []}
        try:
            raw = _unwrap(_need(self.api, "bid_lookups",
                                "Add it to ApiClient (GET /api/bids/lookups) returning "
                                "categories, units, departments, warehouses and uoms.")())
            if isinstance(raw, dict):
                for k in lk:
                    v = raw.get(k)
                    lk[k] = v if isinstance(v, list) else []
        except Exception as e:
            warnings.append(str(e))

        prs: list[dict] = []
        try:
            raw = _unwrap(self.api.pr_list())
            for p in raw if isinstance(raw, list) else []:
                if not isinstance(p, dict):
                    continue
                if str(p.get("status", "")).upper() not in ("SUBMITTED", "APPROVED"):
                    continue
                n = p.get("line_count")
                if n is None and isinstance(p.get("lines"), list):
                    n = len(p["lines"])
                if n == 0:
                    continue          # PHP only lists PRs that have lines
                prs.append({
                    "id": p.get("id"), "pr_number": p.get("pr_number", ""),
                    "pr_date": p.get("pr_date", ""), "status": p.get("status", ""),
                    "line_count": n,
                })
            prs.sort(key=lambda x: x["id"] or 0, reverse=True)
        except Exception as e:
            warnings.append(f"Could not load Purchase Requisitions: {e}")

        return {**lk, "prs": prs, "warnings": warnings, "today": date.today().isoformat()}

    def _pr_lines(self, pr_id: int) -> list[dict]:
        raw = _unwrap(_need(self.api, "pr_get",
                            "Add it to ApiClient (GET /api/purchase-requisitions/{id}) returning the PR with its lines.")(pr_id))
        lines = raw.get("lines", []) if isinstance(raw, dict) else raw
        out = []
        for l in lines if isinstance(lines, list) else []:
            if not isinstance(l, dict):
                continue
            out.append({
                "id": l.get("id"),
                "item_code": l.get("item_code") or "",
                "item_name": l.get("item_name") or "",
                "qty": l.get("qty_requested", l.get("qty", 0)),
                "uom_id": l.get("uom_id"),
                "uom_code": l.get("uom_code") or "",
            })
        return out

    def _create(self, d: dict) -> dict:
        items = []
        for it in d.get("items", []):
            desc = str(it.get("description", "")).strip()
            if not desc:
                continue
            qty, rate = _num(it.get("qty")), _num(it.get("rate"))
            items.append({
                "description": desc, "qty": qty,
                "uom_id": int(it["uom_id"]) if it.get("uom_id") else None,
                "rate": rate, "value": round(qty * rate, 2),
                "pr_line_id": int(it["pr_line_id"]) if it.get("pr_line_id") else None,
            })
        title = str(d.get("title_of_bid", "")).strip()
        if not title:
            raise ValueError("Title of Bid is required.")
        if not items:
            raise ValueError("Add at least one item/service line before saving the bid.")

        def opt_id(k: str) -> Optional[int]:
            return int(d[k]) if d.get(k) else None

        payload = {
            "bid_type": d.get("bid_type") or "SERVICE",
            "requirement_type": d.get("requirement_type") or "OTHERS",
            "mode_of_bid": d.get("mode_of_bid") or "OPEN",
            "sche_delivery_days": int(_num(d.get("sche_delivery_days"))),
            "bid_creation_date": d.get("bid_creation_date") or date.today().isoformat(),
            "warehouse_id": opt_id("warehouse_id"),
            "purch_group": str(d.get("purch_group", "")).strip(),
            "category_id": opt_id("category_id"),
            "unit_id": opt_id("unit_id"),
            "department_id": opt_id("department_id"),
            "bid_publish_date": d.get("bid_publish_date") or None,
            "bid_stage": d.get("bid_stage") or "SINGLE_PART",
            "bid_security_applic": _flag(d.get("bid_security_applic")),
            "bid_security_amount": _num(d.get("bid_security_amount")),
            "bid_security_amount_fc": _num(d.get("bid_security_amount_fc")),
            "foreign_curr_appli": _flag(d.get("foreign_curr_appli")),
            "tax_rate": _num(d.get("tax_rate")),
            "e_proc_applicable": _flag(d.get("e_proc_applicable")),
            "bid_spec_applic": _flag(d.get("bid_spec_applic")),
            "bid_spec_cost": _num(d.get("bid_spec_cost")),
            "bid_spec_cost_fc": _num(d.get("bid_spec_cost_fc")),
            "download_start_dt": _dt(d.get("download_start_dt")),
            "bid_subm_end_dt": _dt(d.get("bid_subm_end_dt")),
            "bid_open_dt": _dt(d.get("bid_open_dt")),
            "title_of_bid": title,
            "bid_value": _num(d.get("bid_value")),
            "e_reverse_auction": _flag(d.get("e_reverse_auction")),
            "pr_id": opt_id("pr_id"),
            "items": items,
        }
        res = _need(self.api, "bid_create",
                    "Add it to ApiClient (POST /api/bids) creating the bid and its items.")(payload)
        data = _unwrap(res) if isinstance(res, dict) else {}
        if not isinstance(data, dict):
            data = {}
        bid_id = data.get("id") or data.get("bid_id")
        if self._on_created:
            try:
                self._on_created(bid_id)
            except Exception:
                pass
        return {"bid_id": bid_id, "bid_number": data.get("bid_number") or "Bid"}


# ── page ─────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<style>
  body { background:#f0f2f5; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; color:#111827; }
  .card { border:0; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); overflow:hidden; }
  .hdr  { background:linear-gradient(135deg,#1565c0,#1e88e5); color:#fff; font-weight:700; font-size:.85rem; padding:.55rem 1rem; }
  .erp-table thead th { background:#f1f5f9; font-size:.75rem; text-transform:uppercase; letter-spacing:.03em; color:#475569; }
  .nav-tabs .nav-link { cursor:pointer; font-size:.85rem; }
  .nav-tabs .nav-link.active { font-weight:600; }
  .pane { display:none; } .pane.on { display:block; }
</style>
<script>__QWC__</script>
</head>
<body>
<div class="container-fluid py-3">
  <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
    <div><h5 class="mb-0 fw-bold"><i class="bi bi-megaphone me-2 text-primary"></i>Create Bid / Tender</h5>
      <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; Create Bid / Tender &middot; BID01</div></div>
    <button class="btn btn-sm btn-outline-secondary" id="btnManage"><i class="bi bi-clipboard-data-fill me-1"></i>Manage Bids (BID02)</button>
  </div>
  <div id="alerts"></div>
  <div id="app"><div class="text-muted small p-3">Loading...</div></div>
</div>

<script>
let bridge = null;
const S = { L: { categories: [], units: [], departments: [], warehouses: [], uoms: [], prs: [] }, today: '', prId: '', busy: false, tab: 'bidding' };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function call(op, payload) { return new Promise(res => bridge.call(op, JSON.stringify(payload || {}), r => res(JSON.parse(r)))); }
function alertBox(html, type) { $('alerts').innerHTML = html ? `<div class="alert alert-${type || 'danger'} small py-2">${html}</div>` : ''; window.scrollTo(0, 0); }

// small field builders -------------------------------------------------
const opt = (v, t, sel) => `<option value="${esc(v)}" ${String(sel) === String(v) ? 'selected' : ''}>${esc(t)}</option>`;
const sel = (f, label, opts, col, extra) => `<div class="col-md-${col || 3}"><label class="form-label small mb-1">${label}</label><select data-f="${f}" id="f-${f}" class="form-select form-select-sm" ${extra || ''}>${opts}</select></div>`;
const inp = (f, label, type, val, col, extra) => `<div class="col-md-${col || 3}"><label class="form-label small mb-1">${label}</label><input data-f="${f}" id="f-${f}" type="${type}" class="form-control form-control-sm" value="${esc(val ?? '')}" ${extra || ''}></div>`;
const yn = (f, label) => sel(f, label, opt('1', 'Yes', '0') + opt('0', 'No', '0'));

async function reloadLookups() {
  const r = await call('init');
  if (!r.ok) { alertBox(esc(r.error)); return; }
  Object.assign(S.L, r.data); S.today = r.data.today;
  if (r.data.warnings.length) alertBox(r.data.warnings.map(esc).join('<br>'), 'warning'); else alertBox('');
  build();
}
window.reloadLookups = reloadLookups;

function build() {
  const L = S.L;
  const prOpts = '<option value="">— None / manual entry —</option>' + L.prs.map(p => opt(p.id, `${p.pr_number} — ${p.pr_date} (${p.line_count ?? '?'} lines, ${p.status})`, S.prId)).join('');
  $('app').innerHTML = `
  <div class="card mb-3"><div class="hdr"><i class="bi bi-card-heading me-2"></i>Header Details</div><div class="card-body">
    <div class="row g-3">
      <div class="col-md-3"><label class="form-label small mb-1">Bid No.</label><input class="form-control form-control-sm" value="(auto on save)" disabled></div>
      ${sel('bid_type', 'Bid Type', opt('SERVICE', 'Service') + opt('GOODS', 'Goods') + opt('WORKS', 'Works'))}
      ${sel('requirement_type', 'Requirement Type', opt('OTHERS', 'Others') + opt('NEW', 'New Requirement') + opt('REPEAT', 'Repeat Order') + opt('EMERGENCY', 'Emergency'))}
      ${sel('mode_of_bid', 'Mode of Bid', opt('OPEN', 'Open') + opt('LIMITED', 'Limited') + opt('SINGLE', 'Single'))}
      ${inp('sche_delivery_days', 'Sche. Delivery Days', 'number', 0, 3, 'min="0"')}
      ${inp('bid_creation_date', 'Bid Creation Date', 'date', S.today)}
      <div class="col-md-3"><label class="form-label small mb-1">Status</label><input class="form-control form-control-sm" value="New" disabled></div>
      ${inp('bid_publish_date', 'Bid Publish Date', 'date', '')}
      ${inp('purch_group', 'Purch. Group', 'text', '', 3, 'placeholder="e.g. AEN-I(T&amp;C) RTN"')}
      ${sel('warehouse_id', 'Storage Location', '<option value="">— None —</option>' + L.warehouses.map(w => opt(w.id, `${w.code} — ${w.name}`)).join(''))}
    </div><hr>
    <div class="row g-3">
      ${sel('category_id', 'Category', '', 4)}${sel('unit_id', 'Unit / Plant', '', 4)}${sel('department_id', 'Department', '', 4)}
    </div>
  </div></div>

  <ul class="nav nav-tabs mb-3">
    <li class="nav-item"><a class="nav-link" data-tab="bidding">Bidding Details</a></li>
    <li class="nav-item"><a class="nav-link" data-tab="pr">Purchase Requisition Details</a></li>
    <li class="nav-item"><a class="nav-link" data-tab="items">Service Items</a></li>
  </ul>

  <div class="pane" id="pane-bidding">
    <div class="card mb-3"><div class="hdr">Data</div><div class="card-body"><div class="row g-3">
      ${sel('bid_stage', 'Bid Stage', opt('SINGLE_PART', 'Single Part') + opt('TWO_PART', 'Two Part'))}
      ${yn('bid_security_applic', 'Bid Security Applic')}${yn('bid_spec_applic', 'Bid Spec. Applic')}${yn('foreign_curr_appli', 'Foreign curr Appli')}
      ${inp('bid_security_amount', 'Bid Security Amount (INR)', 'number', 0, 3, 'step="0.01"')}
      ${inp('bid_security_amount_fc', 'Bid Secu. Amount (FC)', 'number', 0, 3, 'step="0.01"')}
      ${inp('tax_rate', 'Tax Rate (%)', 'number', 18, 3, 'step="0.01"')}
      ${yn('e_proc_applicable', 'E-Proc Applicable')}
      ${inp('bid_spec_cost', 'Bid Spec. Cost (INR)', 'number', 0, 3, 'step="0.01"')}
      ${inp('bid_spec_cost_fc', 'Bid Spec. Cost (FC)', 'number', 0, 3, 'step="0.01"')}
      ${inp('bid_value', 'Bid Value (INR)', 'number', 0, 3, 'step="0.01"')}
      ${yn('e_reverse_auction', 'E-Reverse Auction')}
      ${inp('download_start_dt', 'Download Start Dt/Tm', 'datetime-local', '', 4)}
      ${inp('bid_subm_end_dt', 'Bid Subm. End Dt/Tm', 'datetime-local', '', 4)}
      ${inp('bid_open_dt', 'Bid Open Date/Time', 'datetime-local', '', 4)}
    </div></div></div>
    <div class="card mb-3"><div class="hdr">Description</div><div class="card-body">
      <label class="form-label small mb-1">Title Of Bid <span class="text-danger">*</span></label>
      <textarea id="f-title_of_bid" data-f="title_of_bid" class="form-control form-control-sm" rows="4" placeholder="Full title/scope of the bid as it should appear on the public notice..."></textarea>
    </div></div>
  </div>

  <div class="pane" id="pane-pr">
    <div class="card mb-3"><div class="hdr">Source Purchase Requisition (optional)</div><div class="card-body">
      <div class="row g-2 align-items-end mb-3">
        <div class="col-md-6"><label class="form-label small mb-1">Purchase Requisition</label><select id="prPicker" class="form-select form-select-sm">${prOpts}</select></div>
        <div class="col-auto text-muted small">Choosing a PR pre-fills Service Items from its lines.</div>
      </div>
      <div id="prLines"><p class="text-muted small mb-0">No Purchase Requisition selected — add Service Items manually in the next tab.</p></div>
    </div></div>
  </div>

  <div class="pane" id="pane-items">
    <div class="card mb-3"><div class="hdr d-flex justify-content-between align-items-center"><span>Service Items</span>
      <button type="button" class="btn btn-sm btn-light" id="addRow"><i class="bi bi-plus-lg me-1"></i>Add Row</button></div>
      <div class="card-body p-0"><table class="table table-sm mb-0 erp-table"><thead><tr>
        <th>Description</th><th width="110">Qty</th><th width="120">UOM</th><th width="140">Est. Rate</th><th width="140">Est. Value</th><th width="40"></th>
      </tr></thead><tbody id="itemsBody"></tbody>
      <tfoot><tr><th colspan="4" class="text-end">Total Estimated Value</th><th id="itemsTotal">0.00</th><th></th></tr></tfoot></table></div></div>
  </div>

  <button type="button" class="btn btn-primary" id="btnSave"><i class="bi bi-save me-1"></i>Save Bid / Tender</button>`;

  fillCascade();
  $('f-category_id').onchange = () => { fillCascade('category'); };
  $('f-unit_id').onchange = () => { fillCascade('unit'); };
  $('prPicker').onchange = e => pickPr(e.target.value);
  $('addRow').onclick = () => addRow();
  $('btnSave').onclick = save;
  document.querySelectorAll('.nav-link[data-tab]').forEach(a => a.onclick = () => showTab(a.dataset.tab));
  showTab(S.tab);
  addRow();
}

function showTab(t) {
  S.tab = t;
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('on', p.id === 'pane-' + t));
  document.querySelectorAll('.nav-link[data-tab]').forEach(a => a.classList.toggle('active', a.dataset.tab === t));
}

// Category > Unit (plant) > Department cascade
function fillCascade(changed) {
  const L = S.L, cat = $('f-category_id'), unit = $('f-unit_id'), dep = $('f-department_id');
  if (!changed) cat.innerHTML = '<option value="">— None —</option>' + L.categories.map(c => opt(c.id, `${c.code} — ${c.name}`)).join('');
  if (!changed || changed === 'category') {
    const units = L.units.filter(u => !cat.value || String(u.category_id) === cat.value);
    unit.innerHTML = '<option value="">— None —</option>' + units.map(u => opt(u.id, `${u.code} — ${u.name}`)).join('');
  }
  const deps = L.departments.filter(d => !unit.value || String(d.plant_id) === unit.value);
  dep.innerHTML = '<option value="">— None —</option>' + deps.map(d => opt(d.id, `${d.code} — ${d.name}`)).join('');
}

// Service items table ------------------------------------------------
const uomOpts = selId => '<option value="">—</option>' + S.L.uoms.map(u => opt(u.id, u.code, selId)).join('');
function recalcTotal() {
  let t = 0; document.querySelectorAll('#itemsBody tr').forEach(tr => { t += parseFloat(tr.querySelector('.row-value').value) || 0; });
  $('itemsTotal').textContent = t.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function addRow(desc, qty, uomId, rate, prLineId) {
  const tr = document.createElement('tr');
  tr.dataset.prline = prLineId || '';
  tr.innerHTML = `<td><input type="text" class="form-control form-control-sm i-desc" value="${esc(desc || '')}" placeholder="Service / item description"></td>
    <td><input type="number" step="0.001" class="form-control form-control-sm i-qty" value="${qty ?? ''}"></td>
    <td><select class="form-select form-select-sm i-uom">${uomOpts(uomId)}</select></td>
    <td><input type="number" step="0.01" class="form-control form-control-sm i-rate" value="${rate || ''}"></td>
    <td><input type="text" class="form-control form-control-sm row-value" value="0.00" disabled></td>
    <td><button type="button" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button></td>`;
  $('itemsBody').appendChild(tr);
  const calc = () => { tr.querySelector('.row-value').value = ((parseFloat(tr.querySelector('.i-qty').value) || 0) * (parseFloat(tr.querySelector('.i-rate').value) || 0)).toFixed(2); recalcTotal(); };
  tr.querySelector('.i-qty').oninput = calc; tr.querySelector('.i-rate').oninput = calc;
  tr.querySelector('button').onclick = () => { tr.remove(); recalcTotal(); };
  calc();
}

async function pickPr(id) {
  S.prId = id; alertBox('');
  const box = $('prLines');
  if (!id) { box.innerHTML = '<p class="text-muted small mb-0">No Purchase Requisition selected — add Service Items manually in the next tab.</p>'; $('itemsBody').innerHTML = ''; addRow(); return; }
  const r = await call('pr_lines', { pr_id: +id });
  if (!r.ok) { alertBox(esc(r.error)); S.prId = ''; $('prPicker').value = ''; return; }
  box.innerHTML = `<table class="table table-sm table-hover mb-0 erp-table"><thead><tr><th>Item</th><th>Description</th><th>Qty</th><th>UOM</th></tr></thead><tbody>
    ${r.data.map(l => `<tr><td class="small fw-bold">${esc(l.item_code || '—')}</td><td class="small">${esc(l.item_name || '—')}</td><td class="small">${Number(l.qty || 0).toFixed(3)}</td><td class="small">${esc(l.uom_code || '—')}</td></tr>`).join('')}</tbody></table>`;
  $('itemsBody').innerHTML = '';
  if (r.data.length) r.data.forEach(l => addRow(l.item_name || l.item_code, l.qty, l.uom_id, 0, l.id)); else addRow();
}

async function save() {
  if (S.busy) return;
  alertBox('');
  const d = { pr_id: S.prId || null };
  document.querySelectorAll('[data-f]').forEach(el => { d[el.dataset.f] = el.value; });
  d.items = [...document.querySelectorAll('#itemsBody tr')].map(tr => ({
    description: tr.querySelector('.i-desc').value, qty: tr.querySelector('.i-qty').value, uom_id: tr.querySelector('.i-uom').value,
    rate: tr.querySelector('.i-rate').value, pr_line_id: tr.dataset.prline || null }));
  if (!d.title_of_bid.trim()) { showTab('bidding'); return alertBox('Title of Bid is required.'); }
  if (!d.items.some(i => i.description.trim())) { showTab('items'); return alertBox('Add at least one item/service line before saving the bid.'); }

  S.busy = true; const b = $('btnSave'); b.disabled = true; b.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving...';
  const r = await call('create', d);
  S.busy = false;
  if (!r.ok) { b.disabled = false; b.innerHTML = '<i class="bi bi-save me-1"></i>Save Bid / Tender'; return alertBox('Could not create Bid: ' + esc(r.error)); }
  $('app').innerHTML = `<div class="card"><div class="card-body text-center py-5">
    <i class="bi bi-check-circle text-success" style="font-size:2.5rem"></i>
    <h5 class="mt-2 fw-bold">Bid ${esc(r.data.bid_number)} created</h5>
    <p class="text-muted small">Status: New. Open it in BID02 to publish it.</p>
    <button class="btn btn-primary btn-sm me-2" id="btnAgain"><i class="bi bi-plus-circle me-1"></i>Create another</button>
    <button class="btn btn-outline-secondary btn-sm" id="btnMng"><i class="bi bi-clipboard-data-fill me-1"></i>Manage Bids (BID02)</button></div></div>`;
  $('btnAgain').onclick = () => { S.prId = ''; S.tab = 'bidding'; reloadLookups(); };
  $('btnMng').onclick = () => call('manage');
}

$('btnManage').onclick = () => call('manage');
new QWebChannel(qt.webChannelTransport, ch => { bridge = ch.objects.bridge; reloadLookups(); });
</script>
</body></html>
"""