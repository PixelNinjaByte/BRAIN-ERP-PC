"""
Brain ERP - Create RFQ (ME41).

Port of rfq_create.php (MSME ERP / Prigag Online Pvt. Ltd.).
Rendered with QWebEngineView + QWebChannel, bootstrap5 / bootstrap-icons CDN
styling in the house look (gradient blue card headers, white cards).

Flow (same 4 steps as the PHP screen):
  1. Select a Purchase Requisition (SUBMITTED / APPROVED)
  2. Tick the PR line items to quote
  3. Tick suppliers to invite (email box always editable)
  4. Due date, auto-compare hours, notes  ->  Create & Email RFQ

ApiClient methods used
  EXISTING : pr_list, rfq_create
  NEW      : pr_get(pr_id)      -> PR dict with "lines" (or a list of lines)
             supplier_list()    -> list of suppliers (id, supplier_code, name, email, status)
  The NEW ones are hasattr()-guarded; the screen shows a clear message if missing.

Usage
    self.rfq_create = CreateRFQWidget(api, on_created=lambda rfq_id: ..., on_compare=lambda: ...)
"""

from __future__ import annotations

import json
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
    """Accept either a bare list/dict or the usual {"data": ...} envelope."""
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


class _Bridge(QObject):
    def __init__(self, handler: Callable[[str, dict], Any]):
        super().__init__()
        self._handler = handler

    @pyqtSlot(str, str, result=str)
    def call(self, op: str, payload: str) -> str:
        try:
            data = json.loads(payload) if payload else {}
            return json.dumps({"ok": True, "data": self._handler(op, data)}, default=str)
        except Exception as e:  # ApiError, _Unavailable, anything else -> readable message
            return json.dumps({"ok": False, "error": str(e) or type(e).__name__})


# ── widget ───────────────────────────────────────────────────────────────

class CreateRFQWidget(QWidget):
    def __init__(
        self,
        api: ApiClient,
        on_created: Optional[Callable[[Optional[int]], None]] = None,
        on_compare: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.api = api
        self._on_created = on_created
        self._on_compare = on_compare

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
        html = _HTML.replace("__QWC__", _qwebchannel_js())
        self.view.setHtml(html, QUrl("https://brain-erp.local/"))

    # called by main_window when the screen is shown again
    def reload_data(self) -> None:
        self.view.page().runJavaScript("window.reloadAll && window.reloadAll();")

    # ── bridge handlers ──────────────────────────────────────────────────

    def _handle(self, op: str, d: dict) -> Any:
        if op == "init":
            return self._init()
        if op == "pr_lines":
            return self._pr_lines(int(d.get("pr_id") or 0))
        if op == "create":
            return self._create(d)
        if op == "compare":
            if self._on_compare:
                self._on_compare()
            return True
        raise ValueError(f"Unknown op: {op}")

    def _init(self) -> dict:
        warnings: list[str] = []

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
                prs.append({
                    "id": p.get("id"),
                    "pr_number": p.get("pr_number", ""),
                    "pr_date": p.get("pr_date", ""),
                    "status": p.get("status", ""),
                    "line_count": n,
                })
            prs.sort(key=lambda x: x["id"] or 0, reverse=True)
        except Exception as e:
            warnings.append(f"Could not load Purchase Requisitions: {e}")

        suppliers: list[dict] = []
        try:
            raw = _unwrap(_need(self.api, "supplier_list",
                                "Add it to ApiClient (GET /api/suppliers) to list suppliers.")())
            for s in raw if isinstance(raw, list) else []:
                if not isinstance(s, dict):
                    continue
                st = s.get("status")
                if st is not None and str(st).upper() != "ACTIVE":
                    continue
                suppliers.append({
                    "id": s.get("id"),
                    "supplier_code": s.get("supplier_code") or s.get("code") or "",
                    "name": s.get("name", ""),
                    "email": s.get("email") or "",
                })
            suppliers.sort(key=lambda x: str(x["name"]).lower())
        except Exception as e:
            warnings.append(str(e))

        return {"prs": prs, "suppliers": suppliers, "warnings": warnings}

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
                "item_id": l.get("item_id"),
                "item_code": l.get("item_code") or "",
                "item_name": l.get("item_name") or "",
                "qty": l.get("qty_requested", l.get("qty", 0)),
                "uom_code": l.get("uom_code") or "",
            })
        return out

    def _create(self, d: dict) -> dict:
        payload = {
            # PHP-equivalent fields
            "pr_id": d.get("pr_id"),
            "line_ids": d.get("line_ids", []),
            "suppliers": d.get("suppliers", []),      # [{supplier_id, email}]
            "due_date": d.get("due_date") or None,
            "auto_hours": d.get("auto_hours"),        # None = Settings default
            "notes": d.get("notes", ""),
            # Back-compat with the earlier native RFQ payload shape
            "title": d.get("title", ""),
            "deadline": d.get("due_date") or None,
            "target_suppliers": ",".join(str(s["supplier_id"]) for s in d.get("suppliers", [])),
            "lines": [
                {"item_id": l.get("item_id"), "qty": l.get("qty"), "target_price": 0.0}
                for l in d.get("lines", [])
            ],
        }
        res = self.api.rfq_create(payload)
        data = _unwrap(res) if isinstance(res, dict) else {}
        if not isinstance(data, dict):
            data = {}
        rfq_id = data.get("id") or data.get("rfq_id")
        if self._on_created:
            try:
                self._on_created(rfq_id)
            except Exception:
                pass
        return {
            "rfq_id": rfq_id,
            "rfq_number": data.get("rfq_number") or "RFQ",
            "emailed": data.get("emailed"),
            "failed": data.get("email_failures") or data.get("failed_sends") or [],
        }


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
  .erp-table td { vertical-align:middle; }
</style>
<script>__QWC__</script>
</head>
<body>
<div class="container-fluid py-3">
  <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
    <div>
      <h5 class="mb-0 fw-bold"><i class="bi bi-envelope-paper me-2 text-primary"></i>Create RFQ (Request for Quotation)</h5>
      <div class="text-muted small">MM &rsaquo; Purchasing &rsaquo; Create RFQ &middot; ME41</div>
    </div>
    <div class="d-flex gap-2">
      <button id="btnCompare" class="btn btn-sm btn-outline-secondary"><i class="bi bi-bar-chart-steps me-1"></i>Compare Quotations (ME47)</button>
      <button id="btnReload" class="btn btn-sm btn-outline-primary"><i class="bi bi-arrow-clockwise me-1"></i>Reload</button>
    </div>
  </div>
  <div id="alerts"></div>
  <div id="app"><div class="text-muted small p-3">Loading...</div></div>
</div>

<script>
let bridge = null;
const S = { prs: [], suppliers: [], pr: null, lines: [], busy: false, done: null };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function call(op, payload) {
  return new Promise(res => bridge.call(op, JSON.stringify(payload || {}), r => res(JSON.parse(r))));
}
function alertBox(html, type) {
  $('alerts').innerHTML = html ? `<div class="alert alert-${type || 'danger'} small py-2">${html}</div>` : '';
}

async function reloadAll() {
  alertBox('');
  const r = await call('init');
  if (!r.ok) { alertBox(esc(r.error)); return; }
  S.prs = r.data.prs; S.suppliers = r.data.suppliers;
  if (r.data.warnings.length) alertBox(r.data.warnings.map(esc).join('<br>'), 'warning');
  if (S.pr && !S.prs.find(p => p.id == S.pr.id)) { S.pr = null; S.lines = []; }
  render();
}
window.reloadAll = reloadAll;

async function pickPr(id) {
  alertBox('');
  S.done = null;
  if (!id) { S.pr = null; S.lines = []; render(); return; }
  const r = await call('pr_lines', { pr_id: +id });
  if (!r.ok) { S.pr = null; S.lines = []; alertBox(esc(r.error)); render(); return; }
  S.pr = S.prs.find(p => p.id == id) || { id: +id, pr_number: '#' + id };
  S.lines = r.data;
  render();
}

function render() {
  if (S.done) {
    const d = S.done;
    let h = `<div class="card"><div class="card-body text-center py-5">
      <i class="bi bi-check-circle text-success" style="font-size:2.5rem"></i>
      <h5 class="mt-2 fw-bold">${esc(d.rfq_number)} created</h5>`;
    if (d.failed && d.failed.length) {
      h += `<div class="alert alert-warning small text-start mx-auto" style="max-width:640px"><b>Some emails were NOT sent:</b><br>${d.failed.map(esc).join('<br>')}<br>Open the RFQ in ME47 to resend, fix the address, or share the link manually.</div>`;
    } else {
      h += `<p class="text-muted small mb-3">Published and emailed to ${d.count} supplier(s): ${d.emails.map(esc).join(', ')}.</p>`;
    }
    h += `<button class="btn btn-primary btn-sm me-2" id="btnAgain"><i class="bi bi-plus-circle me-1"></i>Create another</button>
      <button class="btn btn-outline-secondary btn-sm" id="btnCmp2"><i class="bi bi-bar-chart-steps me-1"></i>Compare Quotations</button>
      </div></div>`;
    $('app').innerHTML = h;
    $('btnAgain').onclick = () => { S.done = null; S.pr = null; S.lines = []; reloadAll(); };
    $('btnCmp2').onclick = () => call('compare');
    return;
  }

  const prOpts = S.prs.map(p =>
    `<option value="${p.id}" ${S.pr && S.pr.id == p.id ? 'selected' : ''}>${esc(p.pr_number)} — ${esc(p.pr_date)} (${p.line_count ?? '?'} lines, ${esc(p.status)})</option>`).join('');
  let h = `<div class="card mb-3"><div class="hdr"><i class="bi bi-1-circle me-2"></i>Select Purchase Requisition</div>
    <div class="card-body"><div class="row g-2 align-items-end">
      <div class="col-md-6"><select id="prSel" class="form-select form-select-sm"><option value="">— Choose a PR —</option>${prOpts}</select></div>
      ${S.prs.length ? '' : '<div class="col-auto text-muted small">No open Purchase Requisitions found. Create one first (ME51N).</div>'}
    </div></div></div>`;

  if (S.pr) {
    const lineRows = S.lines.map(l => `<tr>
      <td><input type="checkbox" class="rfq-line-chk" value="${l.id}" checked></td>
      <td class="small fw-bold">${l.item_code ? esc(l.item_code) : `<span class="text-danger">Item #${esc(l.item_id)} not found</span>`}</td>
      <td class="small">${esc(l.item_name || '—')}</td>
      <td class="small">${Number(l.qty || 0).toFixed(3)}</td>
      <td class="small">${esc(l.uom_code || '—')}</td></tr>`).join('');

    const supRows = S.suppliers.map(s => `<tr>
      <td><input type="checkbox" class="rfq-sup-chk" data-sid="${s.id}"></td>
      <td class="small fw-bold">${esc(s.name)}</td>
      <td class="small text-muted">${esc(s.supplier_code)}</td>
      <td><input type="email" id="sup-email-${s.id}" class="form-control form-control-sm d-inline-block" style="max-width:260px"
            value="${esc(s.email)}" placeholder="no email on file - type one">
          ${s.email ? '' : '<span class="badge bg-warning text-dark ms-1">no email on file</span>'}</td></tr>`).join('');

    h += `
    <div class="card mb-3"><div class="hdr"><i class="bi bi-2-circle me-2"></i>Line Items to Quote — ${esc(S.pr.pr_number)}</div>
      <div class="card-body p-0"><table class="table table-sm table-hover mb-0 erp-table">
        <thead><tr><th width="30"><input type="checkbox" id="chkAllLines" checked></th><th>Item</th><th>Description</th><th>Qty</th><th>UOM</th></tr></thead>
        <tbody>${lineRows || '<tr><td colspan="5" class="text-center text-muted py-3">This PR has no lines.</td></tr>'}</tbody>
      </table></div></div>

    <div class="card mb-3"><div class="hdr"><i class="bi bi-3-circle me-2"></i>Invite Suppliers</div>
      <div class="card-body p-0"><table class="table table-sm table-hover mb-0 erp-table">
        <thead><tr><th width="30"><input type="checkbox" id="chkAllSup" title="Tick every supplier that has an email"></th><th>Supplier</th><th>Code</th><th>Email to send to (editable)</th></tr></thead>
        <tbody>${supRows || '<tr><td colspan="4" class="text-center text-muted py-3">No active suppliers. Create one first (XK01).</td></tr>'}</tbody>
      </table></div></div>

    <div class="card mb-3"><div class="hdr"><i class="bi bi-4-circle me-2"></i>Quotation Due Date &amp; Notes</div>
      <div class="card-body"><div class="row g-3">
        <div class="col-md-3"><label class="form-label small mb-1">Quote Due By <span class="text-muted">(cut-off)</span></label>
          <input type="date" id="dueDate" class="form-control form-control-sm"></div>
        <div class="col-md-3"><label class="form-label small mb-1">Auto-compare after (hours)</label>
          <input type="number" min="0" step="1" id="autoHours" class="form-control form-control-sm" placeholder="Settings default">
          <div class="form-text" style="font-size:.7rem">AI comparison + automatic PO run at this time or after the due date, whichever comes first. 0 = due date only.</div></div>
        <div class="col-md-6"><label class="form-label small mb-1">Notes to Supplier (optional)</label>
          <textarea id="notes" class="form-control form-control-sm" rows="2" placeholder="Delivery location, payment terms, special instructions..."></textarea></div>
      </div></div></div>

    <button type="button" id="btnSubmit" class="btn btn-primary"><i class="bi bi-send me-1"></i>Create &amp; Email RFQ to Suppliers</button>`;
  }
  $('app').innerHTML = h;

  $('prSel').onchange = e => pickPr(e.target.value);
  if (S.pr) {
    $('chkAllLines').onchange = e => document.querySelectorAll('.rfq-line-chk').forEach(c => c.checked = e.target.checked);
    $('chkAllSup').onchange = e => document.querySelectorAll('.rfq-sup-chk').forEach(c => {
      const em = $('sup-email-' + c.dataset.sid); c.checked = e.target.checked && em.value.trim() !== '';
    });
    document.querySelectorAll('.rfq-sup-chk').forEach(c => c.onchange = () => {
      if (c.checked) { const em = $('sup-email-' + c.dataset.sid); if (!em.value.trim()) em.focus(); }
    });
    $('btnSubmit').onclick = submit;
  }
}

async function submit() {
  if (S.busy) return;
  alertBox('');
  const lineIds = [...document.querySelectorAll('.rfq-line-chk:checked')].map(c => +c.value);
  const lines = S.lines.filter(l => lineIds.includes(+l.id)).map(l => ({ item_id: l.item_id, qty: l.qty }));
  const suppliers = []; let bad = false;
  document.querySelectorAll('.rfq-sup-chk:checked').forEach(c => {
    const sid = +c.dataset.sid, em = $('sup-email-' + sid).value.trim();
    if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) suppliers.push({ supplier_id: sid, email: em }); else bad = true;
  });
  if (!S.pr) return alertBox('Select a valid Purchase Requisition.');
  if (!lineIds.length) return alertBox('Select at least one line item to quote.');
  if (!suppliers.length) return alertBox(bad ? 'A ticked supplier has no valid email address - type one in the Email box.' : 'Tick at least one supplier to invite.');

  const ah = $('autoHours').value.trim();
  const payload = {
    pr_id: S.pr.id, line_ids: lineIds, lines, suppliers,
    title: 'RFQ for ' + S.pr.pr_number,
    due_date: $('dueDate').value || null,
    auto_hours: ah === '' ? null : Math.max(0, parseInt(ah, 10) || 0),
    notes: $('notes').value.trim(),
  };
  S.busy = true; const btn = $('btnSubmit'); btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creating...';
  const r = await call('create', payload);
  S.busy = false;
  if (!r.ok) { alertBox('Could not create RFQ: ' + esc(r.error)); btn.disabled = false; btn.innerHTML = '<i class="bi bi-send me-1"></i>Create &amp; Email RFQ to Suppliers'; return; }
  S.done = { rfq_number: r.data.rfq_number, failed: r.data.failed, count: suppliers.length, emails: suppliers.map(s => s.email) };
  render();
}

$('btnReload').onclick = reloadAll;
$('btnCompare').onclick = () => call('compare');
new QWebChannel(qt.webChannelTransport, ch => { bridge = ch.objects.bridge; reloadAll(); });
</script>
</body></html>
"""