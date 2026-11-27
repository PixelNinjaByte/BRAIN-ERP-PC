"""
Brain ERP - Compare Quotations (ME47).

Port of rfq_compare.php (MSME ERP / Prigag Online Pvt. Ltd.).
Rendered with QWebEngineView + QWebChannel, bootstrap5 / bootstrap-icons CDN
styling (white cards, gradient blue headers, blue comparison-grid headers).

Two views, same as the PHP screen:
  LIST    - all RFQs (number, source PR, dates, status, invited / submitted)
  DETAIL  - RFQ summary + cut-off/auto-order status, quotation comparison grid
            (lowest-price highlighting, last discount %, discount amount,
            total quoted, net payable), commercial terms, Purchase Order card
            (create PO from lowest quote per line), tabs
            "Invited Suppliers & Links" (resend/send email, WhatsApp, copy link,
            final discount) and "Negotiation" (audit trail + manual log entry),
            AI comparison, Close Quotations & Evaluate / Retry.

All calculations that the PHP did inline (lowest per line, effective discount,
last terms, lowest net) are done in the page JS from the raw record, so the
backend only has to return data.

ApiClient methods used
  EXISTING : rfq_list()
  NEW (all hasattr()-guarded - the screen shows a clear message if one is missing)
    rfq_get(rfq_id)                          -> detail record (contract below)
    rfq_send_invite(rfq_id, rfq_supplier_id, email)
    rfq_nego(rfq_id, payload)                -> payload["nego_action"] in
                                                set_final | clear_final | log_round
    rfq_finalize(rfq_id, action)             -> action in finalize_quotes | retry_award
    rfq_ai_generate(rfq_id)
    rfq_create_po(rfq_id)                    -> lowest-price-per-open-line PO(s)
  Mutating calls may return {"message": str, "ok": bool, ...}; the message is
  shown as the flash banner (create_po may also return "po_id" / "po_ids").

rfq_get contract (flat dict, optionally inside {"data": ...}):
  rfq: {id, rfq_number, pr_id, pr_number, rfq_date, due_date, status, notes,
        auto_award_status, auto_award_at, auto_award_trigger,
        auto_award_supplier_name, auto_award_po: {id, po_number, release_status},
        auto_enabled (bool), auto_compare_at, quotes_finalized_at,
        ai_summary, ai_summary_at}
  currency            default "INR"
  link_base           e.g. "https://host/?msme_rfq="   (or give suppliers[].link)
  can_create_po       bool (ME21N permission), default true
  lines:     [{id, item_id, item_code, item_name, qty, uom_code, pr_line_id, ordered(bool)}]
  suppliers: [{id (rfq_suppliers.id), supplier_id, supplier_name, supplier_code,
               supplier_phone, supplier_master_email, email_to, token, link,
               status, sent_at, viewed_at, submitted_at, total_quoted,
               payment_terms, delivery_period, incoterms, certifications,
               negotiation_status, negotiation_round, negotiation_discount_pct,
               final_discount_pct, final_amount, final_set_at, final_remarks}]
  quotes:       {"<rfq_supplier_id>": {"<rfq_line_id>": {unit_price, delivery_days, remarks}}}
  negotiations: {"<rfq_supplier_id>": [{round_no, created_at, actor, action_type,
                                        channel, discount_pct, total_amount,
                                        remarks, by_name}]}      (oldest first)
  linked_pos: [{po_id, po_number, supplier_name, status, grand_total}]

Usage
    w = RFQCompareWidget(api, on_new_rfq=..., on_open_po=lambda po_id: ..., on_open_pr=lambda pr_id: ...)
    w.open_rfq(rfq_id)        # jump straight to a detail view (PHP: ME47&id=N)
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from PyQt6.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSlot
from PyQt6.QtGui import QDesktopServices
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
    if isinstance(res, dict) and "data" in res and "rfq" not in res:
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


def _flash(res: Any, default: str) -> dict:
    """Turn a mutating API response into {"message", "level"} for the banner."""
    r = res
    if isinstance(r, dict) and isinstance(r.get("data"), dict) and "message" not in r:
        r = {**r["data"], **{k: v for k, v in r.items() if k != "data"}}
    if not isinstance(r, dict):
        return {"message": default, "level": "success"}
    ok = r.get("ok", True) is not False and not r.get("error")
    msg = r.get("message") or r.get("msg") or r.get("error") or default
    level = r.get("level") or ("success" if ok else "danger")
    return {"message": msg, "level": level}


# ── widget ───────────────────────────────────────────────────────────────

class RFQCompareWidget(QWidget):
    def __init__(
        self,
        api: ApiClient,
        on_new_rfq: Optional[Callable[[], None]] = None,
        on_open_po: Optional[Callable[[int], None]] = None,
        on_open_pr: Optional[Callable[[int], None]] = None,
    ):
        super().__init__()
        self.api = api
        self._on_new_rfq = on_new_rfq
        self._on_open_po = on_open_po
        self._on_open_pr = on_open_pr
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
            self.open_rfq(self._pending_open)

    # public API ----------------------------------------------------------

    def open_rfq(self, rfq_id: Optional[int]) -> None:
        """Show the comparison detail for one RFQ (like ME47&id=N)."""
        if not rfq_id:
            self.reload_data()
            return
        self._pending_open = int(rfq_id)
        self.view.page().runJavaScript(f"window.openRfq && window.openRfq({int(rfq_id)});")

    def reload_data(self) -> None:
        self.view.page().runJavaScript("window.reloadAll && window.reloadAll();")

    # bridge handlers -----------------------------------------------------

    def _handle(self, op: str, d: dict) -> Any:
        rid = int(d.get("rfq_id") or 0)

        if op == "list":
            raw = _unwrap(self.api.rfq_list())
            return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

        if op == "get":
            rec = _unwrap(_need(self.api, "rfq_get",
                                "Add it to ApiClient (GET /api/rfqs/{id}) returning the detail record.")(rid))
            if not isinstance(rec, dict) or not rec.get("rfq"):
                raise ValueError("RFQ not found.")
            self._pending_open = None
            return rec

        if op == "invite":
            res = _need(self.api, "rfq_send_invite",
                        "Add it to ApiClient (POST /api/rfqs/{id}/invite).")(
                rid, int(d["rfq_supplier_id"]), str(d.get("email", "")).strip())
            return _flash(res, f"RFQ link emailed to {d.get('email')}.")

        if op == "nego":
            res = _need(self.api, "rfq_nego",
                        "Add it to ApiClient (POST /api/rfqs/{id}/negotiation).")(rid, d.get("payload", {}))
            return _flash(res, "Saved.")

        if op == "finalize":
            action = d.get("action")
            if action not in ("finalize_quotes", "retry_award"):
                raise ValueError("Unknown action.")
            res = _need(self.api, "rfq_finalize",
                        "Add it to ApiClient (POST /api/rfqs/{id}/finalize).")(rid, action)
            return _flash(res, "Quotations closed and evaluated.")

        if op == "ai":
            res = _need(self.api, "rfq_ai_generate",
                        "Add it to ApiClient (POST /api/rfqs/{id}/ai-compare).")(rid)
            return _flash(res, "AI comparison generated.")

        if op == "create_po":
            res = _need(self.api, "rfq_create_po",
                        "Add it to ApiClient (POST /api/rfqs/{id}/create-po).")(rid)
            out = _flash(res, "Purchase Order(s) created.")
            body = _unwrap(res) if isinstance(res, dict) else {}
            ids = (body.get("po_ids") if isinstance(body, dict) else None) or []
            if not ids and isinstance(body, dict) and body.get("po_id"):
                ids = [body["po_id"]]
            if len(ids) == 1 and self._on_open_po:
                self._on_open_po(int(ids[0]))      # PHP landed on ME23N when exactly one PO was made
                out["opened_po"] = True
            return out

        if op == "open_url":
            url = str(d.get("url", ""))
            if url.startswith("https://wa.me/"):
                QDesktopServices.openUrl(QUrl(url))
            return True

        if op == "new_rfq":
            if self._on_new_rfq:
                self._on_new_rfq()
            return True
        if op == "open_pr":
            if self._on_open_pr and d.get("id"):
                self._on_open_pr(int(d["id"]))
            return True
        if op == "open_po":
            if self._on_open_po and d.get("id"):
                self._on_open_po(int(d["id"]))
            return True

        raise ValueError(f"Unknown op: {op}")


# ── page ─────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<style>
  body { background:#f0f2f5; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; color:#111827; }
  .card { border:0; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); overflow:hidden; }
  .hdr  { background:linear-gradient(135deg,#1565c0,#1e88e5); color:#fff; font-weight:700; font-size:.85rem; padding:.55rem 1rem; }
  .erp-table thead th { background:#f1f5f9; font-size:.75rem; text-transform:uppercase; letter-spacing:.03em; color:#475569; }
  .rfq-blue thead th { background:#1565c0 !important; color:#fff !important; font-weight:600; font-size:.78rem; border-color:#1565c0 !important; text-transform:none; letter-spacing:0; }
  .rfq-tabs .nav-link { font-size:.85rem; cursor:pointer; }
  .rfq-tabs .nav-link.active { font-weight:600; }
  a[data-act] { cursor:pointer; }
</style>
<script>__QWC__</script>
</head>
<body>
<div class="container-fluid py-3" id="root"><div class="text-muted small p-3">Loading...</div></div>

<!-- Final discount modal -->
<div class="modal fade" id="finalDiscModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog"><div class="modal-content">
  <div class="modal-header py-2"><h6 class="modal-title"><i class="bi bi-percent me-1"></i>Final Discount &mdash; <span id="fd-name"></span></h6>
    <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
  <div class="modal-body">
    <div class="small text-muted mb-2">Total quoted: <strong><span id="fd-cur"></span><span id="fd-quoted"></span></strong></div>
    <div class="row g-2">
      <div class="col-6"><label class="form-label small">Enter as</label>
        <select id="fd-mode" class="form-select form-select-sm"><option value="PERCENT">Discount %</option><option value="NET">Final net amount</option></select></div>
      <div class="col-6"><label class="form-label small">Value</label>
        <input type="number" id="fd-value" class="form-control form-control-sm" min="0" step="0.01"></div>
      <div class="col-12 small text-success" id="fd-preview"></div>
      <div class="col-6"><label class="form-label small">Agreed via</label>
        <select id="fd-channel" class="form-select form-select-sm"><option value="">&mdash;</option><option>PHONE</option><option>EMAIL</option><option>MEETING</option><option>PORTAL</option><option>OTHER</option></select></div>
      <div class="col-12"><label class="form-label small">Remarks</label>
        <input type="text" id="fd-remarks" class="form-control form-control-sm" maxlength="255" placeholder="e.g. Agreed on call with sales head"></div>
    </div>
    <div class="small text-muted mt-2">This records the agreed discount and shows it on the comparison. It does not change any Purchase Order.</div>
    <div class="alert alert-danger small py-1 mt-2 d-none" id="fd-err"></div>
  </div>
  <div class="modal-footer py-2">
    <button type="button" class="btn btn-sm btn-primary" id="fd-save"><i class="bi bi-check2 me-1"></i>Save Final Discount</button>
    <button type="button" class="btn btn-sm btn-outline-danger" id="fd-clear" style="display:none">Clear</button>
  </div>
</div></div></div>

<!-- Log negotiation entry modal -->
<div class="modal fade" id="logRoundModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog"><div class="modal-content">
  <div class="modal-header py-2"><h6 class="modal-title"><i class="bi bi-chat-left-text me-1"></i>Log negotiation &mdash; <span id="lr-name"></span></h6>
    <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
  <div class="modal-body"><div class="row g-2">
    <div class="col-4"><label class="form-label small">Who</label>
      <select id="lr-actor" class="form-select form-select-sm"><option value="BUYER">Buyer (us)</option><option value="SUPPLIER">Supplier</option></select></div>
    <div class="col-4"><label class="form-label small">Type</label>
      <select id="lr-type" class="form-select form-select-sm"><option value="OFFER">Offer</option><option value="COUNTER">Counter</option><option value="NOTE">Note</option></select></div>
    <div class="col-4"><label class="form-label small">Channel</label>
      <select id="lr-channel" class="form-select form-select-sm"><option value="">&mdash;</option><option>PHONE</option><option>EMAIL</option><option>MEETING</option><option>PORTAL</option><option>OTHER</option></select></div>
    <div class="col-6"><label class="form-label small">Discount %</label>
      <input type="number" id="lr-pct" class="form-control form-control-sm" min="0" max="99.99" step="0.01"></div>
    <div class="col-6"><label class="form-label small">Amount</label>
      <input type="number" id="lr-amt" class="form-control form-control-sm" min="0" step="0.01"></div>
    <div class="col-12"><label class="form-label small">Remarks</label>
      <input type="text" id="lr-remarks" class="form-control form-control-sm" maxlength="255" placeholder="What was discussed / agreed"></div>
  </div>
  <div class="alert alert-danger small py-1 mt-2 d-none" id="lr-err"></div></div>
  <div class="modal-footer py-2"><button type="button" class="btn btn-sm btn-primary" id="lr-save">Save entry</button></div>
</div></div></div>

<script>
let bridge = null, pendingOpen = null;
const S = { view: 'list', list: [], d: null, tab: 'links', msg: null, busy: false, finalRs: null, logRs: null };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nl2br = s => esc(s).replace(/\n/g, '<br>');
const fmt = n => Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const r2 = n => Math.round(n * 100) / 100;
const SC = { SENT: 'secondary', VIEWED: 'info', SUBMITTED: 'success' };
const NB = { NONE: 'secondary', IN_PROGRESS: 'warning', ACCEPTED: 'success', EXHAUSTED: 'danger' };
const AB = { OFFER: 'primary', COUNTER: 'info', DECLINE: 'danger', ACCEPT: 'success', FINAL: 'success', FINAL_CLEARED: 'secondary', NOTE: 'secondary' };
const BB = { FINAL: 'success', ACCEPTED: 'success', 'LAST OFFER': 'primary', QUOTED: 'secondary' };

function call(op, payload) {
  return new Promise(res => bridge.call(op, JSON.stringify(payload || {}), r => res(JSON.parse(r))));
}
function flash(m, level) { S.msg = m ? { m, level: level || 'success' } : null; }

// ── data loading ────────────────────────────────────────────────────────
async function reloadAll() {
  if (S.view === 'detail' && S.d) return refresh();
  const r = await call('list');
  if (!r.ok) { flash(r.error, 'danger'); S.list = []; } else S.list = r.data;
  S.view = 'list'; render();
}
window.reloadAll = reloadAll;

async function openRfq(id) {
  if (!bridge) { pendingOpen = id; return; }
  const r = await call('get', { rfq_id: id });
  if (!r.ok) { flash(r.error, 'danger'); S.view = 'list'; return reloadAll(); }
  S.d = r.data; S.view = 'detail'; S.tab = 'links'; render();
}
window.openRfq = openRfq;

async function refresh() {
  const r = await call('get', { rfq_id: S.d.rfq.id });
  if (!r.ok) flash(r.error, 'danger'); else S.d = r.data;
  render();
}

async function mutate(op, payload) {
  if (S.busy) return false;
  S.busy = true;
  const r = await call(op, Object.assign({ rfq_id: S.d.rfq.id }, payload || {}));
  S.busy = false;
  if (!r.ok) { flash(r.error, 'danger'); render(); return false; }
  flash(r.data.message, r.data.level);
  if (r.data.opened_po) return true;
  await refresh();
  return r.data.level !== 'danger';
}

// ── calculations (ported from the PHP) ──────────────────────────────────
function calc(d) {
  const subs = d.suppliers, lines = d.lines, quotes = d.quotes || {}, nego = d.negotiations || {};
  const q = (s, l) => (quotes[s.id] || {})[l.id] || null;
  const lowestPerLine = {};
  lines.forEach(l => {
    let min = null;
    subs.forEach(s => { const x = q(s, l); if (x && x.unit_price !== null && (min === null || +x.unit_price < min)) min = +x.unit_price; });
    lowestPerLine[l.id] = min;
  });
  const submitted = subs.filter(s => s.status === 'SUBMITTED');
  const totals = submitted.map(s => +s.total_quoted);
  const lowestTotal = totals.length ? Math.min(...totals) : null;

  const effective = s => {
    if (s.status !== 'SUBMITTED') return { pct: 0, net: null, source: null };
    if (s.final_set_at && s.final_discount_pct !== null && s.final_discount_pct !== undefined)
      return { pct: +s.final_discount_pct, net: +s.final_amount, source: 'FINAL' };
    if ((s.negotiation_status || 'NONE') === 'ACCEPTED') {
      const pct = +s.negotiation_discount_pct;
      return { pct, net: r2(+s.total_quoted * (1 - pct / 100)), source: 'NEGOTIATION' };
    }
    return { pct: 0, net: null, source: null };
  };
  const lastNego = {};
  subs.forEach(s => (nego[s.id] || []).forEach(n => { if (+n.discount_pct > 0 || +n.total_amount > 0) lastNego[s.id] = n; }));
  const lastTerms = s => {
    if (s.status !== 'SUBMITTED') return { pct: null, net: null, basis: null, note: '' };
    const quoted = +s.total_quoted, ed = effective(s);
    if (ed.source === 'FINAL') return { pct: ed.pct, net: +ed.net, basis: 'FINAL', note: "buyer's final discount" };
    if (ed.source === 'NEGOTIATION') return { pct: ed.pct, net: +ed.net, basis: 'ACCEPTED', note: 'accepted in negotiation' };
    const n = lastNego[s.id];
    if (n && quoted > 0) {
      const amt = +n.total_amount; let pct = +n.discount_pct;
      const net = amt > 0 ? amt : r2(quoted * (1 - pct / 100));
      pct = pct > 0 ? pct : Math.max(0, r2((1 - net / quoted) * 100));
      return { pct, net, basis: 'LAST OFFER', note: `round ${+n.round_no} · ${String(n.actor).toLowerCase()} ${String(n.action_type).toLowerCase()}` };
    }
    return { pct: 0, net: quoted, basis: 'QUOTED', note: 'no discount negotiated' };
  };
  let lowestNet = null;
  subs.forEach(s => { const t = lastTerms(s); if (t.net !== null && t.net > 0 && (lowestNet === null || t.net < lowestNet)) lowestNet = t.net; });
  return { q, lowestPerLine, lowestTotal, lowestNet, lastTerms, submitted };
}

// ── rendering ───────────────────────────────────────────────────────────
function msgHtml() {
  return S.msg ? `<div class="alert alert-${S.msg.level} small py-2 d-flex justify-content-between align-items-start"><span>${esc(S.msg.m)}</span><button type="button" class="btn-close btn-sm" data-act="dismiss"></button></div>` : '';
}

function render() { $('root').innerHTML = S.view === 'detail' && S.d ? detailHtml() : listHtml(); }

function listHtml() {
  const rows = S.list.map(r => `<tr>
    <td><a data-act="open" data-id="${r.id}" class="fw-bold text-decoration-none">${esc(r.rfq_number)}</a></td>
    <td class="small">${esc(r.pr_number)}</td><td class="small">${esc(r.rfq_date)}</td><td class="small">${esc(r.due_date || '—')}</td>
    <td><span class="badge bg-${r.status === 'OPEN' ? 'warning' : 'secondary'}">${esc(r.status)}</span></td>
    <td class="small text-center">${+r.invited || 0}</td>
    <td class="small text-center"><span class="fw-bold text-${+r.submitted > 0 ? 'success' : 'muted'}">${+r.submitted || 0}</span> / ${+r.invited || 0}</td>
    <td><button class="btn btn-outline-primary py-0 px-1" data-act="open" data-id="${r.id}"><i class="bi bi-eye"></i></button></td></tr>`).join('');
  return `<div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
      <div><h5 class="mb-0 fw-bold"><i class="bi bi-bar-chart-steps me-2 text-primary"></i>Compare Quotations</h5>
        <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; Compare Quotations &middot; ME47</div></div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-primary" data-act="reload"><i class="bi bi-arrow-clockwise me-1"></i>Reload</button>
        <button class="btn btn-sm btn-primary" data-act="new"><i class="bi bi-envelope-paper me-1"></i>New RFQ (ME41)</button></div></div>
    ${msgHtml()}
    <div class="card"><div class="card-body p-0"><div class="table-responsive"><table class="table table-hover table-sm mb-0 erp-table">
      <thead><tr><th>RFQ Number</th><th>Source PR</th><th>RFQ Date</th><th>Due Date</th><th>Status</th><th>Suppliers</th><th>Submitted</th><th>Action</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8" class="text-center text-muted py-4">No RFQs yet. Create one (ME41).</td></tr>'}</tbody>
    </table></div></div></div>`;
}

function detailHtml() {
  const d = S.d, rfq = d.rfq, cur = d.currency || 'INR', subs = d.suppliers, lines = d.lines;
  const C = calc(d);
  const cc = n => esc(cur) + ' ' + fmt(n);

  // header / cut-off status
  const aas = String(rfq.auto_award_status || '');
  const trig = { AUTO_TIME: 'auto-compare time', AUTO_CUTOFF: 'RFQ cut-off date', MANUAL: 'manual cut-off' };
  const po = rfq.auto_award_po;
  let cut = '';
  if (aas === 'DONE') {
    cut = `<span class="badge bg-success">EVALUATED</span> <span class="small">on ${esc(rfq.auto_award_at)} (${esc(trig[rfq.auto_award_trigger] || rfq.auto_award_trigger)}) &rarr; <strong>${esc(rfq.auto_award_supplier_name || '—')}</strong> selected${po ? `, PO <a data-act="po" data-id="${po.id}" class="fw-bold">${esc(po.po_number)}</a> &mdash; ${po.release_status === 'RELEASED' ? 'released and sent to supplier' : 'awaiting release by the releasing authority'}` : ''}</span>`;
  } else if (aas === 'RUNNING') {
    cut = `<span class="badge bg-info">EVALUATING</span> <span class="small">started ${esc(rfq.auto_award_at)} &mdash; refresh in a moment.</span>`;
  } else if (aas === 'PO_FAILED' || aas === 'ERROR') {
    cut = `<span class="badge bg-danger">${esc(aas)}</span> <span class="small">The automatic evaluation stopped (see the comparison below). Use <em>Retry evaluation &amp; PO</em>, or order manually.</span>`;
  } else if (rfq.auto_enabled) {
    const w = []; if (rfq.auto_compare_at) w.push(`at <strong>${esc(rfq.auto_compare_at)}</strong>`); if (rfq.due_date) w.push(`after the cut-off date <strong>${esc(rfq.due_date)}</strong>`);
    cut = `<span class="badge bg-warning text-dark">PENDING</span> <span class="small">The AI comparison runs and the PO is raised automatically ${w.length ? w.join(' or ') : 'when you click <em>Close Quotations &amp; Evaluate</em> (no cut-off date or auto-compare time was set)'}. A supplier accepting an offer does not place the order.</span>`;
  } else {
    cut = `<span class="badge bg-secondary">OFF</span> <span class="small">Automatic evaluation is switched off in Settings.</span>`;
  }

  let h = `<div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
      <div><h5 class="mb-0 fw-bold"><i class="bi bi-bar-chart-steps me-2 text-primary"></i>RFQ: ${esc(rfq.rfq_number)}</h5>
        <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; RFQ: ${esc(rfq.rfq_number)} &middot; ME47</div></div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-primary" data-act="refresh"><i class="bi bi-arrow-clockwise me-1"></i>Refresh</button>
        <button class="btn btn-sm btn-primary" data-act="new"><i class="bi bi-envelope-paper me-1"></i>New RFQ</button>
        <button class="btn btn-sm btn-outline-secondary" data-act="back"><i class="bi bi-arrow-left me-1"></i>Back to List</button></div></div>
    ${msgHtml()}
    <div class="card mb-3"><div class="card-body"><div class="row g-3">
      <div class="col-md-3"><span class="text-muted small">Source PR:</span> <a data-act="pr" data-id="${rfq.pr_id}" class="fw-bold">${esc(rfq.pr_number)}</a></div>
      <div class="col-md-3"><span class="text-muted small">RFQ Date:</span> <span class="fw-bold">${esc(rfq.rfq_date)}</span></div>
      <div class="col-md-3"><span class="text-muted small">Due Date:</span> <span class="fw-bold">${esc(rfq.due_date || '—')}</span></div>
      <div class="col-md-3"><span class="text-muted small">Status:</span> <span class="badge bg-${rfq.status === 'OPEN' ? 'warning' : 'secondary'}">${esc(rfq.status)}</span></div>
      <div class="col-12"><span class="text-muted small">Cut-off &amp; auto-order:</span> ${cut}</div>
      ${rfq.notes ? `<div class="col-12"><span class="text-muted small">Notes:</span> ${nl2br(rfq.notes)}</div>` : ''}
    </div></div></div>`;

  // comparison grid
  const th = subs.map(s => `<th class="text-center">${esc(s.supplier_name)}<br><span class="badge bg-${SC[s.status] || 'secondary'}">${esc(s.status)}</span></th>`).join('');
  const body = lines.map(l => `<tr>
    <td class="small"><div class="fw-bold">${l.item_code ? esc(l.item_code) : 'Item #' + esc(l.item_id)}</div><div class="text-muted" style="font-size:.72rem">${esc(l.item_name || '—')}</div></td>
    <td class="small">${Number(l.qty || 0).toFixed(3)} ${esc(l.uom_code || '—')}</td>
    ${subs.map(s => {
      const x = C.q(s, l), low = x && C.lowestPerLine[l.id] !== null && +x.unit_price === +C.lowestPerLine[l.id];
      return `<td class="text-center small ${low ? 'table-success fw-bold' : ''}">${x ? `${esc(cur)} ${fmt(x.unit_price)}<br><span class="text-muted" style="font-size:.7rem">${x.delivery_days !== null && x.delivery_days !== '' && x.delivery_days !== undefined ? esc(x.delivery_days) + 'd delivery' : ''}</span>${x.remarks ? `<br><span class="text-muted fst-italic" style="font-size:.68rem">${esc(x.remarks)}</span>` : ''}` : '—'}</td>`;
    }).join('')}</tr>`).join('');
  const foot = `
    <tr><td colspan="2" class="text-end small text-muted">Last discount %:</td>${subs.map(s => { const t = C.lastTerms(s);
      return `<td class="text-center small text-muted">${t.basis === null ? '--' : `${fmt(t.pct)}% <span class="badge bg-${BB[t.basis] || 'secondary'}" style="font-size:.6rem">${esc(t.basis)}</span><div style="font-size:.68rem">${esc(t.note)}</div>`}</td>`; }).join('')}</tr>
    <tr><td colspan="2" class="text-end small text-muted">Discount Amount:</td>${subs.map(s => { const t = C.lastTerms(s);
      return `<td class="text-center small text-muted">${t.basis === null ? '--' : cc(Math.max(0, +s.total_quoted - +t.net))}</td>`; }).join('')}</tr>
    <tr><td colspan="2" class="text-end small">Total Quoted:</td>${subs.map(s => { const lo = s.status === 'SUBMITTED' && C.lowestTotal !== null && +s.total_quoted === +C.lowestTotal;
      return `<td class="text-center small ${lo ? 'table-success text-success' : ''}">${s.status === 'SUBMITTED' ? cc(s.total_quoted) : '--'}</td>`; }).join('')}</tr>
    <tr><td colspan="2" class="text-end small">Last price / Net payable:</td>${subs.map(s => { const t = C.lastTerms(s); const lo = t.net !== null && C.lowestNet !== null && Math.abs(t.net - C.lowestNet) < 0.005;
      return `<td class="text-center small ${lo ? 'table-success text-success' : ''}">${t.basis === null ? '--' : `${cc(t.net)}<div class="fw-normal" style="font-size:.68rem">${esc(t.basis)}${lo ? ' · lowest' : ''}</div>`}</td>`; }).join('')}</tr>`;
  h += `<div class="card mb-3"><div class="hdr"><i class="bi bi-bar-chart-steps me-2"></i>Quotation Comparison</div><div class="card-body p-0"><div class="table-responsive">
    <table class="table table-sm table-bordered mb-0 erp-table rfq-blue"><thead><tr><th>Item</th><th>Qty</th>${th}</tr></thead><tbody>${body}</tbody><tfoot class="table-light fw-bold">${foot}</tfoot></table></div></div></div>`;

  // commercial terms
  if (C.submitted.length) {
    h += `<div class="card mb-3"><div class="hdr"><i class="bi bi-file-earmark-text me-2"></i>Commercial Terms</div><div class="card-body p-0">
      <table class="table table-sm mb-0 erp-table rfq-blue"><thead><tr><th>Supplier</th><th>Payment Terms</th><th>Delivery Period</th><th>Incoterms</th><th>Certifications / Awards</th></tr></thead><tbody>
      ${C.submitted.map(s => `<tr><td class="small fw-bold">${esc(s.supplier_name)}</td><td class="small">${esc(s.payment_terms || '—')}</td><td class="small">${esc(s.delivery_period || '—')}</td><td class="small">${esc(s.incoterms || '—')}</td><td class="small">${s.certifications ? nl2br(s.certifications) : '—'}</td></tr>`).join('')}
      </tbody></table></div></div>`;
  }

  // purchase order card
  const openLines = lines.filter(l => !l.ordered), linked = d.linked_pos || [];
  const openSubmittedExist = openLines.some(l => C.submitted.some(s => C.q(s, l)));
  const mt = linked.length ? ' mt-3' : '';
  let poBody = '';
  if (linked.length) {
    poBody += `<div class="small mb-2 fw-bold text-success"><i class="bi bi-check-circle me-1"></i>Purchase Order(s) already created from this RFQ (${lines.length - openLines.length} of ${lines.length} item${lines.length === 1 ? '' : 's'} already ordered — those items are locked and won't be re-ordered):</div>
      <table class="table table-sm mb-0 erp-table rfq-blue"><thead><tr><th>PO Number</th><th>Supplier</th><th>Status</th><th class="text-end">Grand Total</th><th></th></tr></thead><tbody>
      ${linked.map(p => `<tr><td class="fw-bold">${esc(p.po_number)}</td><td class="small">${esc(p.supplier_name)}</td><td><span class="badge bg-secondary">${esc(p.status)}</span></td><td class="text-end small">${cc(p.grand_total)}</td><td><button class="btn btn-outline-primary py-0 px-1 small" data-act="po" data-id="${p.po_id}">View</button></td></tr>`).join('')}</tbody></table>`;
  }
  if (!openLines.length) poBody += `<div class="text-muted small${mt}">Every item on this RFQ already has a Purchase Order. Nothing left to award.</div>`;
  else if (!C.submitted.length || !openSubmittedExist) poBody += `<div class="text-muted small${mt}">The remaining ${openLines.length} item${openLines.length === 1 ? '' : 's'} have no submitted quotation yet — a Purchase Order can be created automatically once at least one supplier submits a price for ${openLines.length === 1 ? 'it' : 'them'}.</div>`;
  else if (d.can_create_po === false) poBody += `<div class="text-muted small${mt}">Your account isn't authorized for T-Code ME21N, so it can't create Purchase Orders from here.</div>`;
  else poBody += `<div class="small text-muted mb-2${mt}">Awards each ${linked.length ? 'remaining ' : ''}item to whichever submitted supplier quoted the lowest price for it — highlighted in green above. If different suppliers win different items, one PO is created per winning supplier automatically. ${linked.length ? 'Items that already have a PO (above) are skipped — they will never be re-ordered.' : 'No manual entry needed.'}</div>
      <button class="btn btn-success btn-sm" data-act="create_po"><i class="bi bi-bag-check me-1"></i>Create Purchase Order(s) ${linked.length ? 'for Remaining Item(s)' : 'Automatically'}</button>`;
  h += `<div class="card mb-3"><div class="hdr"><i class="bi bi-bag-check me-2"></i>Purchase Order</div><div class="card-body">${poBody}</div></div>`;

  // tabs
  h += `<ul class="nav nav-tabs rfq-tabs">
    <li class="nav-item"><a class="nav-link ${S.tab === 'links' ? 'active' : ''}" data-act="tab" data-tab="links"><i class="bi bi-link-45deg me-1"></i>Invited Suppliers &amp; Links</a></li>
    <li class="nav-item"><a class="nav-link ${S.tab === 'nego' ? 'active' : ''}" data-act="tab" data-tab="nego"><i class="bi bi-chat-left-dots me-1"></i>Negotiation</a></li></ul>
    <div class="mb-3">${S.tab === 'nego' ? negoTab(d, cc) : linksTab(d, C, cc)}</div>`;

  // AI comparison
  const inProg = C.submitted.some(s => (s.negotiation_status || 'NONE') === 'IN_PROGRESS');
  const fin = !!rfq.quotes_finalized_at, canAi = C.submitted.length && (!inProg || fin);
  let aiBtns = '';
  if (C.submitted.length && !rfq.auto_award_status && rfq.status === 'OPEN')
    aiBtns += `<button class="btn btn-sm btn-outline-success" data-act="finalize" data-action="finalize_quotes"><i class="bi bi-check2-circle me-1"></i>Close Quotations &amp; Evaluate</button>`;
  else if (aas === 'PO_FAILED' || aas === 'ERROR')
    aiBtns += `<button class="btn btn-sm btn-outline-danger" data-act="finalize" data-action="retry_award"><i class="bi bi-arrow-repeat me-1"></i>Retry evaluation &amp; PO</button>`;
  aiBtns += `<button class="btn btn-sm btn-outline-primary" data-act="ai" ${canAi ? '' : 'disabled'}><i class="bi bi-stars me-1"></i>${rfq.ai_summary ? 'Regenerate' : 'Generate'} AI Comparison</button>`;
  let aiBody;
  if (inProg && !fin) aiBody = `<div class="card-body text-muted small">Negotiation is still open with one or more suppliers. The AI comparison and the order follow automatically at the cut-off, or click "Close Quotations &amp; Evaluate" to do it now.</div>`;
  else if (rfq.ai_summary) aiBody = `<div class="card-body">${fin ? `<div class="text-success mb-2" style="font-size:.72rem"><i class="bi bi-check2-circle me-1"></i>Quotations closed on ${esc(rfq.quotes_finalized_at)}; any negotiation still open at that time was closed.</div>` : ''}
      <div style="white-space:pre-wrap" class="small">${esc(rfq.ai_summary)}</div>
      <div class="text-muted mt-2" style="font-size:.72rem">Generated ${esc(rfq.ai_summary_at)} — weighs price, delivery, and commercial terms (payment terms, delivery period, incoterms, certifications). AI-generated, always verify before deciding.</div></div>`;
  else aiBody = `<div class="card-body text-muted small">No AI comparison yet. It runs automatically at the cut-off, or click "Generate AI Comparison" for a preview.</div>`;
  h += `<div class="card mb-3"><div class="hdr d-flex justify-content-between align-items-center"><span><i class="bi bi-stars me-2"></i>AI Comparison</span><div class="d-flex gap-2">${aiBtns}</div></div>${aiBody}</div>`;
  return h;
}

function linksTab(d, C, cc) {
  const rows = d.suppliers.map(s => {
    const link = s.link || ((d.link_base || '') + (s.token || ''));
    const ns = s.negotiation_status || 'NONE';
    const finalSet = s.final_set_at && s.final_discount_pct !== null && s.final_discount_pct !== undefined;
    let ph = String(s.supplier_phone || '').replace(/\D+/g, ''); if (ph.length === 10) ph = '91' + ph;
    const wa = ph.length >= 11 ? `<a class="small" data-act="wa" data-url="https://wa.me/${ph}?text=${encodeURIComponent('Request for Quotation ' + d.rfq.rfq_number + ' - please fill your quotation here: ' + link)}"><i class="bi bi-whatsapp text-success me-1"></i>Send by WhatsApp</a>` : '';
    let fin;
    if (s.status !== 'SUBMITTED') fin = '<span class="text-muted">--</span>';
    else {
      const t = C.lastTerms(s);
      fin = (finalSet ? `<div class="fw-bold text-success">${fmt(s.final_discount_pct)}% off</div><div class="text-muted" style="font-size:.7rem">Net ${cc(s.final_amount)}</div>`
        : (t.basis === 'LAST OFFER' || t.basis === 'ACCEPTED') ? `<div class="text-muted" style="font-size:.72rem">Last: ${fmt(t.pct)}% &rarr; ${cc(t.net)} (${esc(t.basis.toLowerCase())})</div>` : '')
        + `<button class="btn btn-outline-primary py-0 px-1" style="font-size:.75rem" data-act="final" data-rs="${s.id}"><i class="bi bi-pencil-square me-1"></i>${finalSet ? 'Edit' : 'Set'}</button>`;
    }
    return `<tr>
      <td class="small fw-bold">${esc(s.supplier_name)}</td>
      <td class="small" style="min-width:300px"><div class="d-flex gap-1 align-items-center">
        <input type="email" id="inv-${s.id}" class="form-control form-control-sm" style="min-width:190px" value="${esc(s.email_to || s.supplier_master_email || '')}" placeholder="supplier@example.com">
        <button class="btn btn-sm btn-primary py-0 px-2" data-act="invite" data-rs="${s.id}" title="Email the quotation link to this address (also saves the address)"><i class="bi bi-send me-1"></i>${s.sent_at ? 'Resend' : 'Send'}</button></div>${wa}</td>
      <td><span class="badge bg-${SC[s.status] || 'secondary'}">${esc(s.status)}</span></td>
      <td class="small">${esc(s.sent_at || '--')}</td><td class="small">${esc(s.viewed_at || '--')}</td><td class="small">${esc(s.submitted_at || '--')}</td>
      <td class="small">${ns === 'NONE' ? '<span class="badge bg-secondary">--</span>' : `<span class="badge bg-${NB[ns] || 'secondary'}">${esc(ns)}</span><div class="text-muted" style="font-size:.7rem">Round ${+s.negotiation_round || 0} -- asking ${fmt(s.negotiation_discount_pct)}% off${ns === 'ACCEPTED' ? ' (accepted)' : ''}</div>`}</td>
      <td class="small">${fin}</td>
      <td class="small"><div class="input-group input-group-sm" style="width:260px"><input type="text" readonly id="lnk-${s.id}" value="${esc(link)}" class="form-control" onclick="this.select()"><button class="btn btn-outline-secondary" data-act="copy" data-rs="${s.id}" title="Copy link"><i class="bi bi-clipboard"></i></button></div></td></tr>`;
  }).join('');
  return `<div class="card" style="border-top-left-radius:0"><div class="card-body p-0"><div class="table-responsive"><table class="table table-sm mb-0 erp-table rfq-blue">
    <thead><tr><th>Supplier</th><th>Email</th><th>Status</th><th>Sent</th><th>Viewed</th><th>Submitted</th><th>Negotiation</th><th>Final Discount</th><th>Link</th></tr></thead><tbody>${rows}</tbody></table></div></div></div>`;
}

function negoTab(d, cc) {
  return d.suppliers.map(s => {
    const rows = (d.negotiations || {})[s.id] || [], ns = s.negotiation_status || 'NONE';
    const finalSet = s.final_set_at && s.final_discount_pct !== null && s.final_discount_pct !== undefined;
    const tbl = !rows.length ? '<div class="text-muted small p-3">No negotiation activity recorded for this supplier yet.</div>'
      : `<div class="table-responsive"><table class="table table-sm mb-0 erp-table rfq-blue"><thead><tr><th>Round</th><th>When</th><th>By</th><th>Action</th><th>Channel</th><th class="text-end">Discount %</th><th class="text-end">Amount</th><th>Remarks</th></tr></thead><tbody>
        ${rows.map(n => `<tr><td class="small">${+n.round_no}</td><td class="small">${esc(n.created_at)}</td>
          <td class="small">${esc(n.actor)}${n.by_name ? `<div class="text-muted" style="font-size:.7rem">${esc(n.by_name)}</div>` : ''}</td>
          <td><span class="badge bg-${AB[n.action_type] || 'secondary'}">${esc(n.action_type)}</span></td><td class="small">${esc(n.channel || '--')}</td>
          <td class="small text-end">${+n.discount_pct > 0 ? fmt(n.discount_pct) + '%' : '--'}</td><td class="small text-end">${+n.total_amount > 0 ? cc(n.total_amount) : '--'}</td>
          <td class="small">${esc(n.remarks || '')}</td></tr>`).join('')}</tbody></table></div>`;
    return `<div class="card mb-3" style="border-top-left-radius:0"><div class="card-header bg-white py-2 d-flex justify-content-between align-items-center flex-wrap gap-2">
      <div class="small"><span class="fw-bold">${esc(s.supplier_name)}</span> <span class="badge bg-${SC[s.status] || 'secondary'} ms-1">${esc(s.status)}</span>
        ${ns !== 'NONE' ? `<span class="badge bg-${NB[ns] || 'secondary'} ms-1">${esc(ns)}</span>` : ''}
        ${s.status === 'SUBMITTED' ? `<span class="text-muted ms-2">Quoted ${cc(s.total_quoted)}</span>` : ''}
        ${finalSet ? `<span class="text-success fw-bold ms-2"><i class="bi bi-check2-circle"></i> Final: ${fmt(s.final_discount_pct)}% off &rarr; ${cc(s.final_amount)}</span>` : ''}</div>
      <button class="btn btn-sm btn-outline-secondary py-0" data-act="log" data-rs="${s.id}"><i class="bi bi-plus-circle me-1"></i>Log entry</button></div>
      <div class="card-body p-0">${tbl}</div></div>`;
  }).join('');
}

// ── modals ──────────────────────────────────────────────────────────────
const modal = id => bootstrap.Modal.getOrCreateInstance($(id));
const sub = id => S.d.suppliers.find(s => s.id == id);

function fdPreview() {
  const s = S.finalRs, el = $('fd-preview'), quoted = s ? +s.total_quoted : 0, v = parseFloat($('fd-value').value);
  if (!quoted || isNaN(v)) { el.textContent = ''; return; }
  const mode = $('fd-mode').value, net = mode === 'PERCENT' ? quoted * (1 - v / 100) : v, pct = mode === 'PERCENT' ? v : (1 - v / quoted) * 100;
  el.textContent = `Net after discount: ${S.d.currency || 'INR'} ${net.toFixed(2)}  (${pct.toFixed(2)}% off)`;
}
function openFinal(rsId) {
  const s = S.finalRs = sub(rsId), set = s.final_set_at && s.final_discount_pct !== null && s.final_discount_pct !== undefined;
  $('fd-name').textContent = s.supplier_name; $('fd-cur').textContent = (S.d.currency || 'INR') + ' '; $('fd-quoted').textContent = fmt(s.total_quoted);
  $('fd-mode').value = 'PERCENT'; $('fd-value').value = set ? s.final_discount_pct : ''; $('fd-remarks').value = s.final_remarks || ''; $('fd-channel').value = '';
  $('fd-clear').style.display = set ? '' : 'none'; $('fd-err').classList.add('d-none'); fdPreview(); modal('finalDiscModal').show();
}
async function saveFinal() {
  const s = S.finalRs, mode = $('fd-mode').value, val = parseFloat($('fd-value').value), q = +s.total_quoted;
  const err = m => { $('fd-err').textContent = m; $('fd-err').classList.remove('d-none'); };
  if (s.status !== 'SUBMITTED' || q <= 0) return err('This supplier has not submitted a quotation yet, so there is nothing to discount.');
  if (isNaN(val)) return err('Enter a value.');
  if (mode === 'PERCENT' && (val < 0 || val >= 100)) return err('Final discount % must be between 0 and 99.99.');
  if (mode === 'NET' && (val <= 0 || val > q)) return err('Final net amount must be more than 0 and not above the total quoted.');
  modal('finalDiscModal').hide(); S.tab = 'nego';
  await mutate('nego', { payload: { nego_action: 'set_final', rfq_supplier_id: s.id, final_mode: mode, final_value: val, channel: $('fd-channel').value || null, remarks: $('fd-remarks').value.trim().slice(0, 255) } });
}
async function clearFinal() {
  if (!confirm('Remove the final discount for this supplier?')) return;
  modal('finalDiscModal').hide(); S.tab = 'nego';
  await mutate('nego', { payload: { nego_action: 'clear_final', rfq_supplier_id: S.finalRs.id, remarks: $('fd-remarks').value.trim().slice(0, 255) } });
}
function openLog(rsId) {
  const s = S.logRs = sub(rsId); $('lr-name').textContent = s.supplier_name;
  ['lr-pct', 'lr-amt', 'lr-remarks'].forEach(i => $(i).value = ''); $('lr-actor').value = 'BUYER'; $('lr-type').value = 'OFFER'; $('lr-channel').value = '';
  $('lr-err').classList.add('d-none'); modal('logRoundModal').show();
}
async function saveLog() {
  const pct = $('lr-pct').value === '' ? 0 : parseFloat($('lr-pct').value), amt = $('lr-amt').value === '' ? 0 : parseFloat($('lr-amt').value), rem = $('lr-remarks').value.trim().slice(0, 255);
  const err = m => { $('lr-err').textContent = m; $('lr-err').classList.remove('d-none'); };
  if (pct < 0 || pct >= 100 || amt < 0) return err('Discount % must be between 0 and 99.99 and the amount cannot be negative.');
  if (rem === '' && pct == 0 && amt == 0) return err('Enter a discount, an amount or a remark to log.');
  modal('logRoundModal').hide(); S.tab = 'nego';
  await mutate('nego', { payload: { nego_action: 'log_round', rfq_supplier_id: S.logRs.id, actor: $('lr-actor').value, action_type: $('lr-type').value, channel: $('lr-channel').value || null, discount_pct: pct, total_amount: amt, remarks: rem } });
}

// ── actions (event delegation) ──────────────────────────────────────────
document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  e.preventDefault();
  const a = el.dataset.act;
  if (a === 'dismiss') { flash(null); render(); }
  else if (a === 'open') { flash(null); openRfq(+el.dataset.id); }
  else if (a === 'reload') { flash(null); reloadAll(); }
  else if (a === 'refresh') { flash(null); refresh(); }
  else if (a === 'back') { flash(null); S.view = 'list'; S.d = null; reloadAll(); }
  else if (a === 'new') call('new_rfq');
  else if (a === 'pr') call('open_pr', { id: +el.dataset.id });
  else if (a === 'po') call('open_po', { id: +el.dataset.id });
  else if (a === 'tab') { S.tab = el.dataset.tab; render(); }
  else if (a === 'wa') call('open_url', { url: el.dataset.url });
  else if (a === 'copy') { const i = $('lnk-' + el.dataset.rs); i.select(); try { document.execCommand('copy'); flash('Link copied.', 'success'); render(); } catch (_) {} }
  else if (a === 'invite') {
    const email = $('inv-' + el.dataset.rs).value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { flash('Enter a valid email address to send to.', 'danger'); return render(); }
    await mutate('invite', { rfq_supplier_id: +el.dataset.rs, email });
  }
  else if (a === 'final') openFinal(el.dataset.rs);
  else if (a === 'log') openLog(el.dataset.rs);
  else if (a === 'create_po') {
    if (confirm('Create Purchase Order(s) automatically using the lowest-priced quote for each remaining item? This cannot be undone from here.')) await mutate('create_po');
  }
  else if (a === 'finalize') {
    const msg = el.dataset.action === 'retry_award' ? 'Re-run the comparison and create the Purchase Order?'
      : 'Close the quotations now? Open negotiations are closed, the AI comparison runs, the best supplier is selected and a Purchase Order is created and sent for release.';
    if (confirm(msg)) await mutate('finalize', { action: el.dataset.action });
  }
  else if (a === 'ai') await mutate('ai');
});
$('fd-save').onclick = saveFinal; $('fd-clear').onclick = clearFinal; $('lr-save').onclick = saveLog;
['fd-mode', 'fd-value'].forEach(i => $(i).addEventListener('input', fdPreview));

new QWebChannel(qt.webChannelTransport, ch => {
  bridge = ch.objects.bridge;
  if (pendingOpen) { const id = pendingOpen; pendingOpen = null; openRfq(id); } else reloadAll();
});
</script>
</body></html>
"""