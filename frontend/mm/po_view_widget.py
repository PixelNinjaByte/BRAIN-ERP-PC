"""
po_view_widget.py
------------------
Desktop (PyQt5) "Purchase Order List & View" (ME23N) widget.

Like po_create_widget.py, this has NO dependency on a live PHP/WordPress
backend. All HTML/CSS/JS is generated in Python and pushed into a
QWebEngineView via setHtml(). Data (the PO list, a PO's header + lines,
its release/approval trail) is supplied by the host application as plain
Python lists/dicts.

Navigation between "list" and "detail", and the Approve/Reject/Return
release action, don't hit a server — the in-page links/buttons navigate
to fake `poview:` URLs which this widget intercepts
(acceptNavigationRequest) and turns into Qt signals:

    viewPoRequested(int)      -- user clicked a PO row; ask your host app
                                 for that PO's detail and call show_detail()
    releaseDecision(dict)     -- user submitted Approve/Reject/Return
    backToListRequested()     -- user clicked "Back to list"

Requires: PyQt5, PyQtWebEngine
    pip install PyQt5 PyQtWebEngine
"""

import sys
import json
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QApplication, QMessageBox
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage

try:
    from ..api_client import ApiClient, ApiError
except ImportError:  # standalone execution
    ApiClient = None  # type: ignore
    ApiError = Exception  # type: ignore


class _NavBridgePage(QWebEnginePage):
    def __init__(self, on_navigate, parent=None):
        super().__init__(parent)
        self._on_navigate = on_navigate

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame):
        if url.scheme() == "poview":
            self._on_navigate(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class POViewWidget(QWidget):
    """
    Offline "Purchase Order List & View" screen (ME23N equivalent).

    Signals
    -------
    viewPoRequested(int)
        A PO row was clicked in the list. Your host app should look up
        that PO's full detail and call show_detail(po, lines, releases=...).
    releaseDecision(dict)
        {"release_id": int, "decision": "APPROVED"|"REJECTED"|"RETURNED",
         "remarks": str}
    backToListRequested()
        User clicked "Back to list" from the detail view.
    """

    viewPoRequested = pyqtSignal(int)
    releaseDecision = pyqtSignal(dict)
    backToListRequested = pyqtSignal()

    def __init__(self, api: Optional["ApiClient"] = None, currency: str = "INR", parent: QWidget = None):
        if isinstance(api, str):
            currency = api
            api = None
        super().__init__(parent)
        self.api = api
        self.currency = currency
        self._loaded_once = False
        self._current_po_id: Optional[int] = None
        self._build_ui()
        if self.api is not None:
            self.viewPoRequested.connect(self._open_po_from_api)
            self.backToListRequested.connect(self.reload_data)
            self.releaseDecision.connect(self._submit_release_to_api)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.api is not None and not self._loaded_once:
            self._loaded_once = True
            self.reload_data()

    def reload_data(self) -> None:
        """Fetch PO list from ApiClient.po_list() and render."""
        if self.api is None:
            return
        try:
            pos = self.api.po_list()
        except ApiError as e:
            QMessageBox.warning(self, "Purchase Orders", f"Could not load purchase orders:\n{e}")
            pos = []
        self.show_list(pos)

    def _open_po_from_api(self, po_id: int) -> None:
        if self.api is None:
            return
        try:
            data = self.api.po_get(po_id) or {}
        except ApiError as e:
            QMessageBox.warning(self, "Purchase Order", f"Could not load PO:\n{e}")
            return

        self._current_po_id = po_id
        po = dict(data.get("po") or {})
        lines = data.get("lines") or []
        releases = data.get("release_trail") or data.get("releases") or []
        pending = data.get("pending_release")
        if isinstance(pending, dict):
            po.setdefault("release_id", pending.get("id"))
            po.setdefault("release_status", pending.get("status", ""))
        po.setdefault("total_amount", po.get("taxable_total", 0))
        po.setdefault("tax_amount", po.get("gst_total", 0))
        self.show_detail(po, lines, releases=releases, can_release=bool(pending))

    def _submit_release_to_api(self, payload: dict) -> None:
        if self.api is None or not self._current_po_id:
            return
        decision_map = {
            "APPROVED": "approve",
            "REJECTED": "reject",
            "RETURNED": "return",
        }
        action = decision_map.get(str(payload.get("decision", "")).upper(), "approve")
        remarks = payload.get("remarks", "")
        try:
            res = self.api._request(
                "POST",
                f"/api/po/{self._current_po_id}/release",
                json={"action": action, "remarks": remarks},
            )
            QMessageBox.information(self, "Release Decision", res.get("message") or "Release decision saved.")
            self._open_po_from_api(self._current_po_id)
        except ApiError as e:
            QMessageBox.warning(self, "Release Failed", str(e))

    # ------------------------------------------------------------------ #
    # UI setup
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        page = _NavBridgePage(self._handle_nav, self.view)
        self.view.setPage(page)
        layout.addWidget(self.view)

    def _handle_nav(self, url: QUrl):
        qs = parse_qs(urlparse(url.toString()).query)
        host = url.host()  # for "poview:open?id=1" PyQt may parse host as '' and path holds it; handle both
        action = host or url.path().lstrip("/")

        if action == "open":
            po_id = qs.get("id", [None])[0]
            if po_id:
                self.viewPoRequested.emit(int(po_id))
        elif action == "list":
            self.backToListRequested.emit()
        elif action == "release":
            raw = qs.get("data", ["{}"])[0]
            try:
                payload = json.loads(unquote(raw))
            except (ValueError, json.JSONDecodeError):
                QMessageBox.warning(self, "Release Action", "Could not read the form data.")
                return
            self.releaseDecision.emit(payload)

    # ------------------------------------------------------------------ #
    # Public: list view
    # ------------------------------------------------------------------ #

    def show_list(self, pos: list):
        """
        pos: [{id, po_number, po_date, supplier, grand_total, status,
               release_status, delivery_date}]
        """
        rows = "".join(
            f"""<tr>
                  <td><a href="#" onclick="openPo({p['id']});return false;" class="fw-bold">{_esc(p.get('po_number',''))}</a></td>
                  <td>{_esc(p.get('po_date',''))}</td>
                  <td>{_esc(p.get('supplier_name') or p.get('supplier',''))}</td>
                  <td class="text-end">{self.currency} {float(p.get('grand_total') or 0):,.2f}</td>
                  <td><span class="badge {_status_class(p.get('status',''))}">{_esc(p.get('status',''))}</span></td>
                  <td><span class="badge {_release_class(p.get('release_status',''))}">{_esc(p.get('release_status') or '—')}</span></td>
                  <td>{_esc(p.get('delivery_date') or '—')}</td>
                </tr>"""
            for p in pos
        ) or '<tr><td colspan="7" style="text-align:center;color:#6c757d;padding:20px">No purchase orders found</td></tr>'

        statuses = sorted({p.get("status", "") for p in pos})
        status_opts = '<option value="">All</option>' + "".join(
            f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in statuses
        )

        html = _LIST_TEMPLATE
        html = html.replace("__ROWS__", rows)
        html = html.replace("__STATUS_OPTIONS__", status_opts)
        self.view.setHtml(html, QUrl("about:blank"))

    # ------------------------------------------------------------------ #
    # Public: detail view
    # ------------------------------------------------------------------ #

    def show_detail(self, po: dict, lines: list, releases: list = None, can_release: bool = False):
        """
        po: {id, po_number, po_date, supplier_name, supplier_code, status,
             release_status, release_id, created_by_name, total_amount,
             tax_amount, discount_amount, round_off, grand_total}
        lines: [{line_num, item_code, item_name, qty_ordered, qty_received,
                 uom_code, unit_price, discount_pct, gst_rate, line_total,
                 status, src_pr_number, src_pr_id}]
        releases: [{level_num, action, actor_name, action_at, remarks}]
        can_release: show the Approve/Reject/Return panel
        """
        releases = releases or []

        line_rows = "".join(
            f"""<tr>
                  <td>{l.get('line_num','')}</td>
                  <td><div class="fw-bold">{_esc(l.get('item_code',''))}</div><div style="color:#6c757d;font-size:11px">{_esc(l.get('item_name',''))}</div></td>
                  <td>{l.get('qty_ordered',0):,.3f} {_esc(l.get('uom_code',''))}</td>
                  <td>{l.get('qty_received',0):,.3f}</td>
                  <td>{self.currency} {l.get('unit_price',0):,.2f}</td>
                  <td>{l.get('discount_pct',0)}%</td>
                  <td>{l.get('gst_rate',0)}%</td>
                  <td class="fw-bold">{self.currency} {l.get('line_total',0):,.2f}</td>
                  <td><span class="badge {_status_class(l.get('status',''))}">{_esc(l.get('status',''))}</span></td>
                  <td>{('<span class=\"fw-bold\">' + _esc(l['src_pr_number']) + '</span>') if l.get('src_pr_number') else '—'}</td>
                </tr>"""
            for l in lines
        )

        release_rows = "".join(
            f"""<tr>
                  <td>L{r.get('level_num','')}</td>
                  <td><span class="badge {_status_class(r.get('action',''))}">{_esc(r.get('action',''))}</span></td>
                  <td>{_esc(r.get('action_by_name') or r.get('actor_name') or '—')}</td>
                  <td>{_esc(r.get('action_date') or r.get('action_at') or '')}</td>
                  <td>{_esc(r.get('remarks') or '')}</td>
                </tr>"""
            for r in releases
        ) or '<tr><td colspan="5" style="color:#6c757d">Awaiting first level approval...</td></tr>'

        release_panel = ""
        if can_release:
            release_panel = f"""
            <div class="card">
              <h2>Take Release Action</h2>
              <div class="card-body">
                <div style="display:flex;gap:16px;margin-bottom:10px">
                  <label><input type="radio" name="decision" value="APPROVED"> Approve</label>
                  <label><input type="radio" name="decision" value="REJECTED"> Reject</label>
                  <label><input type="radio" name="decision" value="RETURNED"> Return</label>
                </div>
                <label>Remarks</label>
                <input type="text" id="release-remarks" placeholder="Optional remarks...">
                <button type="button" class="btn btn-primary" style="margin-top:10px"
                        onclick="submitRelease({po['release_id']})">Submit Decision</button>
              </div>
            </div>"""

        html = _DETAIL_TEMPLATE
        html = html.replace("__PO_NUMBER__", _esc(po.get("po_number", "")))
        html = html.replace("__PO_DATE__", _esc(po.get("po_date", "")))
        html = html.replace("__SUPPLIER__", _esc(f"{po.get('supplier_code','')} — {po.get('supplier_name','')}"))
        html = html.replace("__CREATED_BY__", _esc(po.get("created_by_name") or "—"))
        html = html.replace("__STATUS__", _esc(po.get("status", "")))
        html = html.replace("__STATUS_CLASS__", _status_class(po.get("status", "")))
        html = html.replace("__RELEASE_STATUS__", _esc(po.get("release_status", "")))
        html = html.replace("__RELEASE_CLASS__", _release_class(po.get("release_status", "")))
        html = html.replace("__LINE_ROWS__", line_rows)
        html = html.replace("__RELEASE_ROWS__", release_rows)
        html = html.replace("__RELEASE_PANEL__", release_panel)
        html = html.replace("__TAXABLE__", f"{po.get('total_amount',0):,.2f}")
        html = html.replace("__TAX__", f"{po.get('tax_amount',0):,.2f}")
        html = html.replace("__GRAND__", f"{po.get('grand_total',0):,.2f}")
        html = html.replace("__CURRENCY__", _esc(self.currency))
        self.view.setHtml(html, QUrl("about:blank"))


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _status_class(status: str) -> str:
    return {
        "DRAFT": "bg-secondary", "APPROVED": "bg-success", "PARTIAL": "bg-info",
        "CLOSED": "bg-primary", "CANCELLED": "bg-danger", "REJECTED": "bg-danger",
        "OPEN": "bg-warning",
    }.get(status, "bg-secondary")


def _release_class(status: str) -> str:
    return {"NOT_RELEASED": "bg-warning", "RELEASED": "bg-success", "REJECTED": "bg-danger"}.get(status, "bg-secondary")


_BASE_STYLE = """
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 16px; color: #212529; }
  h2 { font-size: 16px; background: #0d6efd; color: #fff; padding: 8px 12px; border-radius: 4px 4px 0 0; margin: 0; }
  .card { border: 1px solid #dee2e6; border-radius: 4px; margin-bottom: 16px; }
  .card-body { padding: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { border: 1px solid #dee2e6; padding: 6px; }
  th { background: #f1f3f5; font-size: 11px; text-align: left; }
  .text-end { text-align: right; }
  .fw-bold { font-weight: 700; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; color: #fff; }
  .bg-secondary { background: #6c757d; } .bg-success { background: #198754; }
  .bg-info { background: #0dcaf0; color:#000; } .bg-primary { background: #0d6efd; }
  .bg-danger { background: #dc3545; } .bg-warning { background: #ffc107; color:#000; }
  .btn { padding: 6px 14px; border-radius: 4px; border: 1px solid transparent; cursor: pointer; font-size: 13px; }
  .btn-primary { background: #0d6efd; color: #fff; }
  .toolbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
  input, select { padding: 5px; font-size: 13px; border: 1px solid #ced4da; border-radius: 3px; }
"""

_LIST_TEMPLATE = """
<!doctype html><html><head><meta charset="utf-8"><style>""" + _BASE_STYLE + """
  .filters { display:flex; gap:10px; margin-bottom:12px; }
</style></head>
<body>
<div class="toolbar">
  <h3 style="margin:0">Purchase Orders</h3>
</div>
<div class="filters">
  <select id="f-status" onchange="applyFilters()">__STATUS_OPTIONS__</select>
  <input type="text" id="f-supplier" placeholder="Filter by supplier..." oninput="applyFilters()">
</div>
<table id="po-table">
  <thead><tr><th>PO Number</th><th>Date</th><th>Supplier</th><th>Grand Total</th><th>Status</th><th>Release</th><th>Delivery</th></tr></thead>
  <tbody>__ROWS__</tbody>
</table>
<script>
function openPo(id) { window.location.href = 'poview:open?id=' + id; }
function applyFilters() {
  const status = document.getElementById('f-status').value.toLowerCase();
  const supplier = document.getElementById('f-supplier').value.toLowerCase();
  document.querySelectorAll('#po-table tbody tr').forEach(row => {
    const cells = row.querySelectorAll('td');
    if (cells.length < 6) return;
    const rowStatus = cells[4].textContent.trim().toLowerCase();
    const rowSupplier = cells[2].textContent.trim().toLowerCase();
    const matchStatus = !status || rowStatus === status;
    const matchSupplier = !supplier || rowSupplier.includes(supplier);
    row.style.display = (matchStatus && matchSupplier) ? '' : 'none';
  });
}
</script>
</body></html>
"""

_DETAIL_TEMPLATE = """
<!doctype html><html><head><meta charset="utf-8"><style>""" + _BASE_STYLE + """
</style></head>
<body>
<div class="toolbar">
  <h3 style="margin:0">__PO_NUMBER__</h3>
  <div>
    <span class="badge __STATUS_CLASS__">__STATUS__</span>
    <span class="badge __RELEASE_CLASS__">__RELEASE_STATUS__</span>
    <button type="button" class="btn" style="background:#6c757d;color:#fff" onclick="window.location.href='poview:list'">&larr; Back to list</button>
  </div>
</div>

<div class="card">
  <h2>Header</h2>
  <div class="card-body">
    <div><strong>Date:</strong> __PO_DATE__</div>
    <div><strong>Supplier:</strong> __SUPPLIER__</div>
    <div><strong>Created by:</strong> __CREATED_BY__</div>
  </div>
</div>

<div class="card">
  <h2>Line Items — Receipt Status</h2>
  <div class="card-body" style="padding:0">
    <table>
      <thead><tr><th>Line</th><th>Material</th><th>Qty Ordered</th><th>Qty Received</th><th>Unit Price</th><th>Disc%</th><th>GST%</th><th>Net Value</th><th>Status</th><th>Source PR</th></tr></thead>
      <tbody>__LINE_ROWS__</tbody>
      <tfoot>
        <tr><td colspan="9" class="text-end fw-bold">Taxable Amount:</td><td class="fw-bold">__CURRENCY__ __TAXABLE__</td></tr>
        <tr><td colspan="9" class="text-end fw-bold">GST Amount:</td><td class="fw-bold">__CURRENCY__ __TAX__</td></tr>
        <tr style="background:#d1e7dd"><td colspan="9" class="text-end fw-bold">Grand Total:</td><td class="fw-bold">__CURRENCY__ __GRAND__</td></tr>
      </tfoot>
    </table>
  </div>
</div>

<div class="card">
  <h2>Releasing Strategy / Approval Trail</h2>
  <div class="card-body" style="padding:0">
    <table>
      <thead><tr><th>Level</th><th>Action</th><th>By</th><th>Date</th><th>Remarks</th></tr></thead>
      <tbody>__RELEASE_ROWS__</tbody>
    </table>
  </div>
</div>

__RELEASE_PANEL__

<script>
function submitRelease(releaseId) {
  const decisionEl = document.querySelector('input[name="decision"]:checked');
  if (!decisionEl) { alert('Please choose Approve, Reject, or Return.'); return; }
  const payload = {
    release_id: releaseId,
    decision: decisionEl.value,
    remarks: document.getElementById('release-remarks').value
  };
  window.location.href = 'poview:release?data=' + encodeURIComponent(JSON.stringify(payload));
}
</script>
</body></html>
"""


# ---------------------------------------------------------------------- #
# Standalone demo
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)

    w = POViewWidget(currency="INR")
    w.resize(1100, 800)
    w.setWindowTitle("ME23N — Purchase Orders (offline)")

    demo_pos = [
        {"id": 1, "po_number": "PO26000001", "po_date": "2026-11-20", "supplier": "Acme Traders",
         "grand_total": 53100.0, "status": "APPROVED", "release_status": "RELEASED", "delivery_date": "2026-11-28"},
        {"id": 2, "po_number": "PO26000002", "po_date": "2026-11-25", "supplier": "Bharat Metals",
         "grand_total": 12500.0, "status": "DRAFT", "release_status": "NOT_RELEASED", "delivery_date": None},
    ]
    demo_detail = {
        1: dict(po={
            "id": 1, "po_number": "PO26000001", "po_date": "2026-11-20",
            "supplier_name": "Acme Traders", "supplier_code": "SUP-001",
            "status": "APPROVED", "release_status": "RELEASED", "release_id": 10,
            "created_by_name": "Yuvaraj", "total_amount": 45000.0, "tax_amount": 8100.0,
            "grand_total": 53100.0,
        }, lines=[
            {"line_num": 10, "item_code": "RM-001", "item_name": "Steel Rod 12mm",
             "qty_ordered": 100, "qty_received": 100, "uom_code": "KG", "unit_price": 450.0,
             "discount_pct": 0, "gst_rate": 18, "line_total": 45000.0, "status": "CLOSED",
             "src_pr_number": "PR-2026-001", "src_pr_id": 100},
        ], releases=[
            {"level_num": 1, "action": "APPROVED", "actor_name": "Finance Head", "action_at": "2026-11-21 10:00", "remarks": "OK"},
        ], can_release=False),
    }

    def on_view_po(po_id):
        d = demo_detail.get(po_id)
        if d:
            w.show_detail(**d)

    def on_back_to_list():
        w.show_list(demo_pos)

    def on_release(payload):
        QMessageBox.information(w, "Release Decision", json.dumps(payload, indent=2))

    w.viewPoRequested.connect(on_view_po)
    w.backToListRequested.connect(on_back_to_list)
    w.releaseDecision.connect(on_release)

    w.show_list(demo_pos)
    w.show()

    sys.exit(app.exec())