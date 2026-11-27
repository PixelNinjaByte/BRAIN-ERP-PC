"""
Brain ERP - Supplier Quotation Form (public RFQ page).

Port of rfq_public.php (MSME ERP / Prigag Online Pvt. Ltd.).
On the web this page is opened by a supplier from an emailed link
(?msme_rfq=<token>); the token IS the credential. In the desktop app the buyer
can paste that link/token to preview the form or key in a quotation received by
phone / WhatsApp on the supplier's behalf.

Rendered with QWebEngineView + QWebChannel, bootstrap5 / bootstrap-icons CDN.

ApiClient methods used (ALL NEW - none exist yet, all hasattr()-guarded)
  rfq_public_get(token)               -> record dict (contract below)
  rfq_public_submit(token, payload)   -> submit / update the quotation
  rfq_public_negotiation(token, action)  action in {"accept", "decline"}
                                      -> may return {"outcome": "EXHAUSTED"|...}

rfq_public_get contract (flat dict, optionally inside {"data": ...}):
  company_name, currency (default "₹"), rfq_number, supplier_name, due_date, notes,
  status (SENT|VIEWED|SUBMITTED), submitted_at, auto_award_status,
  negotiation_status (NONE|IN_PROGRESS|ACCEPTED|CLOSED|EXHAUSTED),
  negotiation_round, negotiation_discount_pct, total_quoted,
  payment_terms, delivery_period, incoterms, certifications, remarks,
  lines: [{id, item_id, item_code, item_name, qty, uom_code,
           unit_price, delivery_days, remarks}]   # last 3 = existing quote or null

rfq_public_submit payload:
  {"lines": {"<rfq_line_id>": {"unit_price", "delivery_days", "remarks"}},
   "payment_terms", "delivery_period", "incoterms", "certifications",
   "overall_remarks"}

Usage
    w = RFQPublicWidget(api)            # shows a token box
    w.set_token("abcd...")              # or open straight from an invite link
"""

from __future__ import annotations

import json
import re
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
    if isinstance(res, dict) and "data" in res and not ("lines" in res or "rfq_number" in res):
        return res["data"]
    return res


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(f.readAll()).decode("utf-8", "replace")
        f.close()
        return data
    return ""


def _extract_token(text: str) -> str:
    """Accept a full link (…?msme_rfq=TOKEN) or a bare token."""
    text = (text or "").strip()
    m = re.search(r"msme_rfq=([A-Za-z0-9_\-]+)", text)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9_\-]", "", text)


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

class RFQPublicWidget(QWidget):
    def __init__(self, api: ApiClient, token: str = ""):
        super().__init__()
        self.api = api
        self._pending_token = _extract_token(token)

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
        if ok and self._pending_token:
            self.set_token(self._pending_token)

    def set_token(self, token_or_link: str) -> None:
        """Load the quotation form for a token or full invite link."""
        tok = _extract_token(token_or_link)
        self._pending_token = tok
        if tok:
            self.view.page().runJavaScript(f"window.setToken && window.setToken({json.dumps(tok)});")

    def reload_data(self) -> None:
        self.view.page().runJavaScript("window.reloadRecord && window.reloadRecord();")

    # ── bridge handlers ──────────────────────────────────────────────────

    def _record(self, token: str) -> dict:
        hint = "Add it to ApiClient (public, no-auth GET /api/rfq-public/{token})."
        rec = _unwrap(_need(self.api, "rfq_public_get", hint)(token))
        if not isinstance(rec, dict):
            raise ValueError("This quotation link is invalid or has expired.")
        return rec

    def _handle(self, op: str, d: dict) -> Any:
        token = _extract_token(d.get("token", ""))
        if not token:
            raise ValueError("Invalid quotation link.")

        if op == "load":
            return {"record": self._record(token)}

        if op == "submit":
            _need(self.api, "rfq_public_submit",
                  "Add it to ApiClient (POST /api/rfq-public/{token}/submit).")(token, d.get("payload", {}))
            return {"record": self._record(token), "notice": "submitted"}

        if op == "nego":
            action = d.get("action")
            if action not in ("accept", "decline"):
                raise ValueError("Unknown negotiation action.")
            res = _need(self.api, "rfq_public_negotiation",
                        "Add it to ApiClient (POST /api/rfq-public/{token}/negotiation).")(token, action)
            res = res if isinstance(res, dict) else {}
            outcome = str(res.get("outcome") or (res.get("data") or {}).get("outcome") or "").upper()
            notice = "accepted" if action == "accept" else ("exhausted" if outcome == "EXHAUSTED" else "declined")
            return {"record": self._record(token), "notice": notice}

        raise ValueError(f"Unknown op: {op}")


# ── page ─────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<style>
  body { background:#f0f2f5; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; color:#111827; }
  .rfq-wrap { max-width: 900px; margin: 20px auto; padding: 0 15px 40px; }
  .rfq-header { background:#1565c0; color:#fff; border-radius:10px 10px 0 0; padding: 18px 24px; }
  .rfq-card { border:0; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); }
  .tokenbar { background:#fff; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); padding:12px 16px; }
</style>
<script>__QWC__</script>
</head>
<body>
<div class="rfq-wrap">
  <div class="tokenbar mb-3">
    <div class="small fw-bold text-muted mb-1"><i class="bi bi-link-45deg me-1"></i>Quotation link or token</div>
    <div class="input-group input-group-sm">
      <input id="tokenIn" class="form-control" placeholder="Paste the supplier's invite link (…?msme_rfq=TOKEN) or the token">
      <button id="btnLoad" class="btn btn-primary"><i class="bi bi-box-arrow-in-right me-1"></i>Open</button>
    </div>
  </div>
  <div id="app"><div class="text-muted small text-center py-5">Paste a quotation link above to open the supplier form.</div></div>
</div>

<script>
let bridge = null, pending = null;
const S = { token: '', rec: null, notice: '', error: '', busy: false };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nl2br = s => esc(s).replace(/\n/g, '<br>');
const money = n => Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INCOTERMS = { '': 'N/A — domestic', EXW:'EXW', FOB:'FOB', CIF:'CIF', CFR:'CFR', FCA:'FCA', CPT:'CPT', CIP:'CIP', DAP:'DAP', DPU:'DPU', DDP:'DDP' };

function call(op, payload) {
  return new Promise(res => bridge.call(op, JSON.stringify(Object.assign({ token: S.token }, payload || {})), r => res(JSON.parse(r))));
}

async function setToken(t) {
  if (!bridge) { pending = t; return; }
  S.token = t; $('tokenIn').value = t; S.notice = ''; S.error = '';
  $('app').innerHTML = '<div class="text-muted small text-center py-5">Loading...</div>';
  const r = await call('load');
  if (!r.ok) { S.rec = null; $('app').innerHTML = `<div class="alert alert-danger mt-2"><i class="bi bi-exclamation-triangle me-2"></i>${esc(r.error)}</div>`; return; }
  S.rec = r.data.record; render();
}
window.setToken = setToken;
window.reloadRecord = () => S.token && setToken(S.token);

function render() {
  const r = S.rec; if (!r) return;
  const cur = r.currency || '₹';
  const nego = r.negotiation_status || 'NONE';
  const negoTotal = nego !== 'NONE' ? Math.round(Number(r.total_quoted || 0) * (1 - Number(r.negotiation_discount_pct || 0) / 100) * 100) / 100 : 0;
  const closed = !!r.auto_award_status;

  let h = `<div class="card rfq-card mb-4">
    <div class="rfq-header d-flex justify-content-between align-items-center flex-wrap gap-2">
      <div><div class="fw-bold fs-5"><i class="bi bi-envelope-paper me-2"></i>Request for Quotation</div>
           <div class="small opacity-75">${esc(r.company_name || '')} — ${esc(r.rfq_number)}</div></div>
      <div class="text-end small"><div>Supplier: <strong>${esc(r.supplier_name)}</strong></div>
        ${r.due_date ? `<div>Quote due by: <strong>${esc(r.due_date)}</strong></div>` : ''}</div>
    </div>
    <div class="card-body">`;

  if (r.notes) h += `<p class="text-muted small">${nl2br(r.notes)}</p>`;

  if (S.notice === 'submitted') h += `<div class="alert alert-success"><i class="bi bi-check-circle me-2"></i>Thank you — your quotation has been submitted.</div>`;
  else if (S.notice === 'accepted') h += `<div class="alert alert-success"><i class="bi bi-check-circle me-2"></i>Offer accepted — thank you! Your price is recorded. All quotations are compared at the cut-off and the order goes to the best overall offer; we will contact you if you are selected.</div>`;
  else if (S.notice === 'declined') h += `<div class="alert alert-info"><i class="bi bi-arrow-repeat me-2"></i>Thanks for letting us know — here's a revised offer below.</div>`;
  else if (S.notice === 'exhausted') h += `<div class="alert alert-secondary"><i class="bi bi-info-circle me-2"></i>Thank you — we've noted that the discounted offers didn't work for you. Someone from our purchasing team will follow up with you directly.</div>`;
  else if (S.error) h += `<div class="alert alert-danger small">${esc(S.error)}</div>`;

  if (r.status === 'SUBMITTED' && !S.notice && nego === 'NONE')
    h += `<div class="alert alert-info small"><i class="bi bi-info-circle me-2"></i>You already submitted a quotation on ${esc(r.submitted_at)}. You can update it below and resubmit.</div>`;

  if (nego === 'IN_PROGRESS') {
    h += `<div class="card border-0 mb-4" style="background:#fff8e6;border:1px solid #ffe08a !important;"><div class="card-body">
      <div class="fw-bold mb-2"><i class="bi bi-tags me-2"></i>Counter-Offer — Round ${Number(r.negotiation_round || 0)}</div>
      <p class="mb-1 small">Your quoted total: <strong>${esc(cur)}${money(r.total_quoted)}</strong></p>
      <p class="mb-2">We'd like to request an additional <strong>${Number(r.negotiation_discount_pct || 0).toFixed(2)}% discount</strong> — new total: <strong class="text-success">${esc(cur)}${money(negoTotal)}</strong></p>
      <div class="d-flex gap-2">
        <button class="btn btn-success" id="btnAccept"><i class="bi bi-check-circle me-1"></i>Accept This Price</button>
        <button class="btn btn-outline-secondary" id="btnDecline"><i class="bi bi-x-circle me-1"></i>Decline</button>
      </div></div></div>`;
  } else if (nego === 'ACCEPTED') {
    h += `<div class="alert alert-success small"><i class="bi bi-check-circle me-2"></i>You accepted a ${Number(r.negotiation_discount_pct || 0).toFixed(2)}% discount (round ${Number(r.negotiation_round || 0)}) — your price of ${esc(cur)}${money(negoTotal)} is recorded. All quotations are compared at the cut-off and the order goes to the best overall offer; you will be contacted if you are selected.</div>`;
  } else if (nego === 'CLOSED') {
    h += `<div class="alert alert-secondary small"><i class="bi bi-lock me-2"></i>This RFQ has reached its cut-off and the open offer was closed. Our purchasing team will contact you if you are selected.</div>`;
  } else if (nego === 'EXHAUSTED' && S.notice !== 'exhausted') {
    h += `<div class="alert alert-secondary small"><i class="bi bi-info-circle me-2"></i>Our purchasing team will follow up with you directly about this quotation.</div>`;
  }

  if (nego === 'NONE') {
    if (closed) h += `<div class="alert alert-secondary small"><i class="bi bi-lock me-2"></i>This RFQ has closed and the quotations have been evaluated, so new quotations can no longer be submitted.</div>`;
    const rows = (r.lines || []).map(l => `<tr data-id="${l.id}">
      <td class="small"><div class="fw-bold">${l.item_code ? esc(l.item_code) : 'Item #' + esc(l.item_id)}</div><div class="text-muted" style="font-size:.75rem">${esc(l.item_name || '—')}</div></td>
      <td class="small">${Number(l.qty || 0).toFixed(3)} ${esc(l.uom_code || '—')}</td>
      <td><input type="number" step="0.01" min="0" class="form-control form-control-sm f-price" value="${l.unit_price ?? ''}" ${closed ? 'disabled' : ''}></td>
      <td><input type="number" min="0" class="form-control form-control-sm f-days" value="${l.delivery_days ?? ''}" ${closed ? 'disabled' : ''}></td>
      <td><input type="text" class="form-control form-control-sm f-rem" value="${esc(l.remarks ?? '')}" placeholder="e.g. brand, alt. spec" ${closed ? 'disabled' : ''}></td></tr>`).join('');
    const incoOpts = Object.entries(INCOTERMS).map(([v, lbl]) => `<option value="${v}" ${(r.incoterms || '') === v ? 'selected' : ''}>${esc(lbl)}</option>`).join('');
    const dis = closed ? 'disabled' : '';
    h += `<div class="table-responsive"><table class="table table-sm table-bordered align-middle">
      <thead class="table-light"><tr><th>Item</th><th>Qty</th><th style="width:140px">Unit Price (${esc(cur)})</th><th style="width:130px">Delivery (days)</th><th>Remarks</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="row g-3 mt-1 mb-2">
        <div class="col-12"><h6 class="fw-bold small text-muted mb-0">COMMERCIAL TERMS</h6></div>
        <div class="col-md-4"><label class="form-label small">Payment Terms</label>
          <input id="fPay" class="form-control form-control-sm" value="${esc(r.payment_terms || '')}" placeholder="e.g. 50% advance, balance on delivery" ${dis}></div>
        <div class="col-md-4"><label class="form-label small">Delivery Period</label>
          <input id="fDel" class="form-control form-control-sm" value="${esc(r.delivery_period || '')}" placeholder="e.g. 2 weeks from PO date" ${dis}></div>
        <div class="col-md-4"><label class="form-label small">Incoterms <span class="text-muted fw-normal">(for imports)</span></label>
          <select id="fInc" class="form-select form-select-sm" ${dis}>${incoOpts}</select></div>
        <div class="col-12"><label class="form-label small">Certificates / Awards <span class="text-muted fw-normal">(optional)</span></label>
          <textarea id="fCert" class="form-control form-control-sm" rows="2" placeholder="e.g. ISO 9001:2015, CE Certified, MSME Registered, any quality awards" ${dis}>${esc(r.certifications || '')}</textarea></div>
      </div>
      <div class="mb-3"><label class="form-label small">Overall Remarks (optional)</label>
        <textarea id="fRem" class="form-control form-control-sm" rows="2" ${dis}>${esc(r.remarks || '')}</textarea></div>
      <button class="btn btn-primary" id="btnSubmit" ${dis}><i class="bi bi-send me-1"></i>${r.status === 'SUBMITTED' ? 'Update Quotation' : 'Submit Quotation'}</button>`;
  }
  h += `</div></div>`;
  $('app').innerHTML = h;

  if ($('btnSubmit')) $('btnSubmit').onclick = submit;
  if ($('btnAccept')) $('btnAccept').onclick = () => {
    if (confirm(`Accept this offer at ${cur}${money(negoTotal)}? Your price is then considered with the other quotations at the cut-off; an order is placed only if you are selected.`)) nego_('accept');
  };
  if ($('btnDecline')) $('btnDecline').onclick = () => {
    if (confirm('Decline this offer? We will follow up with a revised discount request.')) nego_('decline');
  };
}

async function submit() {
  if (S.busy) return;
  const lines = {}; let any = false;
  document.querySelectorAll('tr[data-id]').forEach(tr => {
    const price = parseFloat(tr.querySelector('.f-price').value) || 0;
    if (price > 0) any = true;
    lines[tr.dataset.id] = { unit_price: price, delivery_days: tr.querySelector('.f-days').value, remarks: tr.querySelector('.f-rem').value.trim() };
  });
  if (!any) { S.error = 'Enter a unit price for at least one line item.'; S.notice = ''; return render(); }
  S.busy = true; $('btnSubmit').disabled = true;
  const r = await call('submit', { payload: {
    lines, payment_terms: $('fPay').value.trim(), delivery_period: $('fDel').value.trim(),
    incoterms: $('fInc').value, certifications: $('fCert').value.trim(), overall_remarks: $('fRem').value.trim(),
  }});
  S.busy = false;
  if (!r.ok) { S.error = r.error; S.notice = ''; return render(); }
  S.rec = r.data.record; S.notice = r.data.notice; S.error = ''; render();
}

async function nego_(action) {
  const r = await call('nego', { action });
  if (!r.ok) { S.error = r.error; S.notice = ''; return render(); }
  S.rec = r.data.record; S.notice = r.data.notice; S.error = ''; render();
}

$('btnLoad').onclick = () => setToken($('tokenIn').value);
$('tokenIn').addEventListener('keydown', e => { if (e.key === 'Enter') setToken($('tokenIn').value); });
new QWebChannel(qt.webChannelTransport, ch => { bridge = ch.objects.bridge; if (pending) { const t = pending; pending = null; setToken(t); } });
</script>
</body></html>
"""