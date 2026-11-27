"""
Brain ERP - Manage Bids / Tenders (BID02).

Port of bid_view.php (MSME ERP / Prigag Online Pvt. Ltd.).
Rendered with QWebEngineView + QWebChannel, bootstrap5 / bootstrap-icons +
Chart.js CDN styling in the house look.

Two views, same as the PHP screen:
  LIST   - all bids with a status filter; click a row to open it
  DETAIL - header card, Bidding Terms, Service / Bid Items (+ total),
           Bidding Graph & Analytics (snapshot, bid mode / days-to-submit,
           timeline, cumulative-value line chart, value-by-item bar chart),
           and "Move Bid Status" buttons.
  Lifecycle (linear, as in the PHP):
     NEW -> PUBLISHED | CANCELLED
     PUBLISHED -> CLOSED | CANCELLED
     CLOSED -> AWARDED | CANCELLED

ApiClient methods used (ALL NEW - hasattr()-guarded)
    bid_list()                          -> list of bids:
        {id, bid_number, title_of_bid, bid_type, bid_stage, item_count,
         bid_publish_date, bid_subm_end_dt, status}
    bid_get(bid_id)                     -> detail record (optionally in {"data": ...}):
        {bid: {...all bids columns..., category_code, category_name, unit_code,
               dept_code, warehouse_code, warehouse_name, src_pr_number,
               created_by_name},
         items: [{line_num, description, qty, uom_code, estimated_rate, estimated_value}],
         currency: "₹"}
    bid_change_status(bid_id, new_status)
        -> backend must enforce the transition rules and, when moving to
           PUBLISHED with no bid_publish_date, set it to today. May return
           {"message": str, "ok": bool}.

Usage
    w = BidViewWidget(api, on_new_bid=lambda: ...)
    w.open_bid(bid_id)      # jump straight to a detail view (PHP: BID02&id=N)
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from PyQt6.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget

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
    if isinstance(res, dict) and "data" in res and "bid" not in res:
        return res["data"]
    return res


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(f.readAll()).decode("utf-8", "replace")
        f.close()
        return data
    return ""


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

class BidViewWidget(QWidget):
    def __init__(self, api: ApiClient, on_new_bid: Optional[Callable[[], None]] = None):
        super().__init__()
        self.api = api
        self._on_new_bid = on_new_bid
        self._pending_open: Optional[int] = None

        self.view = QWebEngineView(self)
        self._channel = QWebChannel(self.view.page())
        self._bridge = _Bridge(self._handle)
        self._channel.registerObject("bridge", self._bridge)
        self.view.page().setWebChannel(self._channel)
        self.view.loadFinished.connect(self._on_loaded)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)

        self.view.setHtml(_HTML.replace("__QWC__", _qwebchannel_js()), QUrl("https://brain-erp.local/"))

    def _on_loaded(self, ok: bool) -> None:
        if ok and self._pending_open:
            self.open_bid(self._pending_open)

    # public API ----------------------------------------------------------

    def open_bid(self, bid_id: Optional[int]) -> None:
        """Show the detail view for one bid (like BID02&id=N)."""
        if not bid_id:
            self.reload_data()
            return
        self._pending_open = int(bid_id)
        self.view.page().runJavaScript(f"window.openBid && window.openBid({int(bid_id)});")

    def reload_data(self) -> None:
        self.view.page().runJavaScript("window.reloadAll && window.reloadAll();")

    # bridge handlers -----------------------------------------------------

    def _handle(self, op: str, d: dict) -> Any:
        if op == "list":
            raw = _unwrap(_need(self.api, "bid_list",
                                "Add it to ApiClient (GET /api/bids).")())
            return [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []

        if op == "get":
            rec = _unwrap(_need(self.api, "bid_get",
                                "Add it to ApiClient (GET /api/bids/{id}) returning the bid, its items and joined names.")(
                int(d.get("bid_id") or 0)))
            if not isinstance(rec, dict) or not rec.get("bid"):
                raise ValueError("Bid not found.")
            self._pending_open = None
            return rec

        if op == "status":
            new_status = str(d.get("new_status", ""))
            if new_status not in ("PUBLISHED", "CLOSED", "AWARDED", "CANCELLED"):
                raise ValueError("Unknown status.")
            res = _need(self.api, "bid_change_status",
                        "Add it to ApiClient (POST /api/bids/{id}/status).")(int(d.get("bid_id") or 0), new_status)
            msg, level = f"Bid moved to {new_status.title()}.", "success"
            r = res
            if isinstance(r, dict) and isinstance(r.get("data"), dict) and "message" not in r:
                r = {**r["data"], **{k: v for k, v in r.items() if k != "data"}}
            if isinstance(r, dict):
                if r.get("ok") is False or r.get("error"):
                    level = "danger"
                msg = r.get("message") or r.get("msg") or r.get("error") or msg
            return {"message": msg, "level": level}

        if op == "new_bid":
            if self._on_new_bid:
                self._on_new_bid()
            return True

        if op == "print":
            # QtWebEngine has no print dialog for window.print(); offer "save as PDF".
            path, _ = QFileDialog.getSaveFileName(self, "Save bid as PDF", f"{d.get('name', 'bid')}.pdf", "PDF (*.pdf)")
            if path:
                self.view.page().printToPdf(path)
                QMessageBox.information(self, "Saved", f"Saved to {path}")
            return True

        raise ValueError(f"Unknown op: {op}")


# ── page ─────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body { background:#f0f2f5; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; color:#111827; }
  .card { border:0; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); overflow:hidden; }
  .hdr  { background:linear-gradient(135deg,#1565c0,#1e88e5); color:#fff; font-weight:700; font-size:.85rem; padding:.55rem 1rem; }
  .erp-table thead th { background:#f1f5f9; font-size:.75rem; text-transform:uppercase; letter-spacing:.03em; color:#475569; }
  tr[data-act] { cursor:pointer; }
  @media print { .no-print { display:none !important; } }
</style>
<script>__QWC__</script>
</head>
<body>
<div class="container-fluid py-3" id="root"><div class="text-muted small p-3">Loading...</div></div>

<script>
let bridge = null, pendingOpen = null, charts = [];
const S = { view: 'list', list: [], filter: '', d: null, msg: null, busy: false };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nl2br = s => esc(s).replace(/\n/g, '<br>');
const fmt = (n, dp = 2) => Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
const r2 = n => Math.round(n * 100) / 100;
const cap = s => { s = String(s || '').toLowerCase(); return s.charAt(0).toUpperCase() + s.slice(1); };
const SB = { NEW: 'info', PUBLISHED: 'primary', CLOSED: 'warning', AWARDED: 'success', CANCELLED: 'danger' };
const badge = s => `<span class="badge bg-${SB[s] || 'secondary'}${s === 'CLOSED' ? ' text-dark' : ''}">${esc(cap(s))}</span>`;
const TRANS = { NEW: ['PUBLISHED', 'CANCELLED'], PUBLISHED: ['CLOSED', 'CANCELLED'], CLOSED: ['AWARDED', 'CANCELLED'] };
const LABEL = { PUBLISHED: 'Publish', CLOSED: 'Close Submission', AWARDED: 'Mark Awarded', CANCELLED: 'Cancel Bid' };

function call(op, payload) { return new Promise(res => bridge.call(op, JSON.stringify(payload || {}), r => res(JSON.parse(r)))); }
function flash(m, level) { S.msg = m ? { m, level: level || 'success' } : null; }
const msgHtml = () => S.msg ? `<div class="alert alert-${S.msg.level} small py-2 d-flex justify-content-between align-items-start no-print"><span>${esc(S.msg.m)}</span><button type="button" class="btn-close btn-sm" data-act="dismiss"></button></div>` : '';
const killCharts = () => { charts.forEach(c => c.destroy()); charts = []; };

// ── loading ─────────────────────────────────────────────────────────────
async function reloadAll() {
  if (S.view === 'detail' && S.d) return refresh();
  const r = await call('list');
  if (!r.ok) { flash(r.error, 'danger'); S.list = []; } else S.list = r.data;
  S.view = 'list'; render();
}
window.reloadAll = reloadAll;

async function openBid(id) {
  if (!bridge) { pendingOpen = id; return; }
  const r = await call('get', { bid_id: id });
  if (!r.ok) { flash(r.error, 'danger'); S.view = 'list'; return reloadAll(); }
  S.d = r.data; S.view = 'detail'; render();
}
window.openBid = openBid;

async function refresh() {
  const r = await call('get', { bid_id: S.d.bid.id });
  if (!r.ok) flash(r.error, 'danger'); else S.d = r.data;
  render();
}

async function changeStatus(ns) {
  if (S.busy) return; S.busy = true;
  const r = await call('status', { bid_id: S.d.bid.id, new_status: ns });
  S.busy = false;
  if (!r.ok) flash(r.error, 'danger'); else flash(`${r.data.message}`, r.data.level);
  await refresh();
}

// ── rendering ───────────────────────────────────────────────────────────
function render() {
  killCharts();
  $('root').innerHTML = S.view === 'detail' && S.d ? detailHtml() : listHtml();
  if (S.view === 'detail' && S.d) drawCharts();
}

function listHtml() {
  const rows = S.list.filter(b => !S.filter || b.status === S.filter).map(b => `<tr data-act="open" data-id="${b.id}">
    <td class="small fw-bold">${esc(b.bid_number)}</td>
    <td class="small">${esc(String(b.title_of_bid || '').length > 60 ? String(b.title_of_bid).slice(0, 60) + '…' : (b.title_of_bid || ''))}</td>
    <td class="small">${esc(b.bid_type)}</td><td class="small">${esc(b.bid_stage)}</td><td class="small">${+b.item_count || 0}</td>
    <td class="small">${esc(b.bid_publish_date || '—')}</td><td class="small">${esc(b.bid_subm_end_dt || '—')}</td><td>${badge(b.status)}</td></tr>`).join('');
  const opts = ['', 'NEW', 'PUBLISHED', 'CLOSED', 'AWARDED', 'CANCELLED'].map(s => `<option value="${s}" ${S.filter === s ? 'selected' : ''}>${s ? cap(s) : 'All Statuses'}</option>`).join('');
  return `<div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
      <div><h5 class="mb-0 fw-bold"><i class="bi bi-clipboard-data-fill me-2 text-primary"></i>Manage Bids / Tenders</h5>
        <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; Manage Bids / Tenders &middot; BID02</div></div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-primary" data-act="reload"><i class="bi bi-arrow-clockwise me-1"></i>Reload</button>
        <button class="btn btn-sm btn-primary" data-act="new"><i class="bi bi-plus-lg me-1"></i>New Bid</button></div></div>
    ${msgHtml()}
    <div class="card mb-3"><div class="card-body py-2"><div class="row"><div class="col-md-3">
      <select id="statusFilter" class="form-select form-select-sm">${opts}</select></div></div></div></div>
    <div class="card"><div class="card-body p-0"><div class="table-responsive"><table class="table table-sm table-hover mb-0 erp-table">
      <thead><tr><th>Bid No.</th><th>Title</th><th>Type</th><th>Stage</th><th>Items</th><th>Publish Date</th><th>Subm. End</th><th>Status</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8" class="text-center text-muted py-4">No Bids/Tenders yet. Create one (BID01).</td></tr>'}</tbody>
    </table></div></div></div>`;
}

function detailHtml() {
  const { bid: b, items } = S.d, cur = S.d.currency || '₹', cc = n => esc(cur) + fmt(n);
  const total = items.reduce((t, i) => t + (+i.estimated_value || 0), 0);
  const taxAmt = r2(total * (+b.tax_rate || 0) / 100), grand = r2(total + taxAmt);
  const daysToSubmit = b.bid_subm_end_dt ? Math.floor((new Date(String(b.bid_subm_end_dt).replace(' ', 'T')).getTime() - Date.now()) / 86400000) : null;
  const active = ['NEW', 'PUBLISHED'].includes(b.status);
  const ms = [['Bid Created', b.bid_creation_date, 'bi-flag-fill'], ['Bid Published', b.bid_publish_date, 'bi-megaphone-fill'],
    ['Download Opens', b.download_start_dt, 'bi-download'], ['Submission Closes', b.bid_subm_end_dt, 'bi-hourglass-split'], ['Bid Opens', b.bid_open_dt, 'bi-unlock-fill']];
  const next = TRANS[b.status] || [];
  const th = (k, v) => `<tr><th width="160" class="text-muted">${k}</th><td>${v}</td></tr>`;
  const orDash = v => v ? esc(v) : '—';

  return `<div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2 no-print">
      <div><h5 class="mb-0 fw-bold"><i class="bi bi-clipboard-data-fill me-2 text-primary"></i>Bid / Tender: ${esc(b.bid_number)}</h5>
        <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; Bid: ${esc(b.bid_number)} &middot; BID02</div></div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-primary" data-act="refresh"><i class="bi bi-arrow-clockwise me-1"></i>Refresh</button>
        <button class="btn btn-sm btn-primary" data-act="new"><i class="bi bi-plus-lg me-1"></i>New Bid</button>
        <button class="btn btn-sm btn-outline-secondary" data-act="print"><i class="bi bi-printer me-1"></i>Print / PDF</button>
        <button class="btn btn-sm btn-outline-secondary" data-act="back"><i class="bi bi-arrow-left me-1"></i>Back</button></div></div>
    ${msgHtml()}

    <div class="card mb-3"><div class="hdr d-flex justify-content-between align-items-center"><span><i class="bi bi-clipboard2-check me-2"></i>${esc(b.bid_number)}</span>${badge(b.status)}</div>
    <div class="card-body"><div class="row g-3">
      <div class="col-md-6"><table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
        ${th('Bid Type:', esc(b.bid_type))}${th('Requirement Type:', esc(b.requirement_type))}${th('Mode of Bid:', esc(b.mode_of_bid))}
        ${th('Sche. Delivery Days:', +b.sche_delivery_days || 0)}
        ${th('Category / Unit / Dept:', `${b.category_code ? esc(b.category_code + ' — ' + (b.category_name || '')) : '—'} / ${orDash(b.unit_code)} / ${orDash(b.dept_code)}`)}
        ${th('Storage Location:', b.warehouse_code ? esc(b.warehouse_code + ' — ' + (b.warehouse_name || '')) : '—')}
        ${th('Purch. Group:', orDash(b.purch_group))}${th('Source PR:', orDash(b.src_pr_number))}</table></div>
      <div class="col-md-6"><table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
        ${th('Bid Creation Date:', orDash(b.bid_creation_date))}${th('Bid Publish Date:', orDash(b.bid_publish_date))}${th('Download Start:', orDash(b.download_start_dt))}
        ${th('Bid Subm. End:', orDash(b.bid_subm_end_dt))}${th('Bid Open Date/Time:', orDash(b.bid_open_dt))}${th('Created By:', orDash(b.created_by_name))}</table></div>
      <div class="col-12"><strong class="small text-muted">Title of Bid:</strong><div class="small">${nl2br(b.title_of_bid)}</div></div>
    </div></div></div>

    <div class="card mb-3"><div class="hdr">Bidding Terms</div><div class="card-body"><div class="row g-3" style="font-size:.83rem">
      <div class="col-md-3"><span class="text-muted">Bid Stage:</span> ${esc(b.bid_stage)}</div>
      <div class="col-md-3"><span class="text-muted">Bid Security:</span> ${+b.bid_security_applic ? `Yes (${cc(b.bid_security_amount)})` : 'No'}</div>
      <div class="col-md-3"><span class="text-muted">Bid Spec. Cost:</span> ${+b.bid_spec_applic ? cc(b.bid_spec_cost) : 'No'}</div>
      <div class="col-md-3"><span class="text-muted">Tax Rate:</span> ${fmt(b.tax_rate)}%</div>
      <div class="col-md-3"><span class="text-muted">Foreign Currency:</span> ${+b.foreign_curr_appli ? 'Yes' : 'No'}</div>
      <div class="col-md-3"><span class="text-muted">E-Proc Applicable:</span> ${+b.e_proc_applicable ? 'Yes' : 'No'}</div>
      <div class="col-md-3"><span class="text-muted">E-Reverse Auction:</span> ${+b.e_reverse_auction ? 'Yes' : 'No'}</div>
      <div class="col-md-3"><span class="text-muted">Bid Value:</span> ${cc(b.bid_value)}</div>
    </div></div></div>

    <div class="card mb-3"><div class="hdr"><i class="bi bi-list-ul me-2"></i>Service / Bid Items</div><div class="card-body p-0">
      <table class="table table-sm table-hover mb-0 erp-table"><thead><tr><th>Line</th><th>Description</th><th>Qty</th><th>UOM</th><th>Est. Rate</th><th>Est. Value</th></tr></thead><tbody>
      ${items.map(i => `<tr><td class="text-muted small">${esc(i.line_num)}</td><td class="small">${esc(i.description)}</td><td class="small">${fmt(i.qty, 3)}</td><td class="small">${esc(i.uom_code || '—')}</td><td class="small">${fmt(i.estimated_rate)}</td><td class="small fw-bold">${fmt(i.estimated_value)}</td></tr>`).join('')
        || '<tr><td colspan="6" class="text-center text-muted py-3">No items on this bid.</td></tr>'}</tbody>
      ${items.length ? `<tfoot><tr><th colspan="5" class="text-end">Total Estimated Value</th><th>${fmt(total)}</th></tr></tfoot>` : ''}</table></div></div>

    <div class="card mb-3"><div class="hdr d-flex justify-content-between align-items-center"><span><i class="bi bi-graph-up-arrow me-2"></i>Bidding Graph &amp; Analytics</span>${badge(b.status)}</div>
    <div class="card-body"><div class="row g-3">
      <div class="col-lg-3">
        <div class="border rounded p-3 mb-3" style="background:#f8f9fb">
          <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Snapshot</div>
          <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Total Items:</span><strong>${items.length}</strong></div>
          <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Est. Value:</span><strong>${cc(total)}</strong></div>
          <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Tax (${fmt(b.tax_rate)}%):</span><strong>${cc(taxAmt)}</strong></div>
          <div class="d-flex justify-content-between small"><span class="text-muted">Grand Total:</span><strong class="text-primary">${cc(grand)}</strong></div></div>
        <div class="border rounded p-3 mb-3" style="background:#f0f7ff;border-color:#bfe0ff !important">
          <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Mode: ${esc(b.mode_of_bid)}</div>
          <span class="badge bg-${active ? 'success' : 'secondary'}"><i class="bi bi-circle-fill me-1" style="font-size:.55rem"></i>${active ? 'Active' : esc(cap(b.status))}</span>
          ${daysToSubmit !== null && !isNaN(daysToSubmit) ? `<div class="small text-muted mt-2">${daysToSubmit >= 0 ? `${daysToSubmit} day(s) to submission close` : 'Submission window closed'}</div>` : ''}</div>
        <div class="border rounded p-3" style="background:#f8f9fb">
          <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Timeline</div>
          ${ms.map(m => `<div class="d-flex align-items-center small mb-2"><i class="bi ${m[2]} me-2 ${m[1] ? 'text-primary' : 'text-muted'}"></i><div class="flex-grow-1">${m[0]}</div><div class="${m[1] ? 'fw-bold' : 'text-muted'}">${m[1] ? esc(m[1]) : '—'}</div></div>`).join('')}</div>
      </div>
      <div class="col-lg-9">
        <div class="border rounded p-3 mb-3"><div class="small fw-bold text-muted mb-2"><i class="bi bi-activity me-1"></i>Cumulative Estimated Value by Line</div><div style="height:220px"><canvas id="bidTrendChart"></canvas></div></div>
        <div class="border rounded p-3"><div class="small fw-bold text-muted mb-2"><i class="bi bi-bar-chart-fill me-1"></i>Estimated Value by Item</div><div style="height:240px"><canvas id="bidVolumeChart"></canvas></div></div>
      </div></div></div></div>

    ${next.length ? `<div class="card mb-3 no-print"><div class="hdr">Move Bid Status</div><div class="card-body d-flex gap-2">
      ${next.map(ns => `<button class="btn btn-sm ${ns === 'CANCELLED' ? 'btn-outline-danger' : 'btn-primary'}" data-act="status" data-ns="${ns}">${esc(LABEL[ns] || ns)}</button>`).join('')}</div></div>` : ''}`;
}

function drawCharts() {
  if (typeof Chart === 'undefined') return;          // CDN unreachable (offline)
  const { bid: b, items } = S.d, cur = S.d.currency || '₹';
  if (!items.length) return;
  let run = 0;
  const labels = items.map(i => { const t = String(i.description || ''); return t.length > 18 ? t.slice(0, 18) + '…' : t; });
  const values = items.map(i => r2(+i.estimated_value || 0));
  const cum = values.map(v => (run = r2(run + v)));
  const yTicks = { ticks: { callback: v => cur + Number(v).toLocaleString('en-IN') } };
  charts.push(new Chart($('bidTrendChart'), { type: 'line',
    data: { labels, datasets: [{ label: `Cumulative Est. Value (${cur})`, data: cum, borderColor: '#1565c0', backgroundColor: 'rgba(21,101,192,0.10)', borderWidth: 2, pointBackgroundColor: '#f9a825', pointRadius: 4, tension: 0.35, fill: true }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: yTicks } } }));
  charts.push(new Chart($('bidVolumeChart'), { type: 'bar',
    data: { labels, datasets: [{ label: `Est. Value (${cur})`, data: values, backgroundColor: '#1565c0', hoverBackgroundColor: '#f9a825', borderRadius: 4, maxBarThickness: 40 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: yTicks } } }));
}

// ── actions ─────────────────────────────────────────────────────────────
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  const a = el.dataset.act;
  if (a === 'dismiss') { flash(null); render(); }
  else if (a === 'open') { flash(null); openBid(+el.dataset.id); }
  else if (a === 'reload') { flash(null); reloadAll(); }
  else if (a === 'refresh') { flash(null); refresh(); }
  else if (a === 'back') { flash(null); S.view = 'list'; S.d = null; reloadAll(); }
  else if (a === 'new') call('new_bid');
  else if (a === 'print') call('print', { name: S.d.bid.bid_number });
  else if (a === 'status') { if (confirm(`${LABEL[el.dataset.ns] || el.dataset.ns} — are you sure?`)) changeStatus(el.dataset.ns); }
});
document.addEventListener('change', e => { if (e.target.id === 'statusFilter') { S.filter = e.target.value; render(); } });

new QWebChannel(qt.webChannelTransport, ch => {
  bridge = ch.objects.bridge;
  if (pendingOpen) { const id = pendingOpen; pendingOpen = null; openBid(id); } else reloadAll();
});
</script>
</body></html>
"""