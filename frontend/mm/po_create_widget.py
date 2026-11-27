"""
po_create_widget.py
--------------------
Desktop (PyQt5) "Create Purchase Order" (ME21N) widget.

Unlike the original server-rendered po_create.php, this version has NO
dependency on a live PHP/WordPress backend. All HTML/CSS/JS is generated
in Python and pushed into a QWebEngineView via setHtml() — nothing is
loaded from a URL. Data (suppliers, items, UOMs, PR lines, last prices)
is supplied by the host application as plain Python lists/dicts.

Form submission does not POST anywhere. The in-page JS collects the
header + line items into one JSON object and navigates to a fake
`poform:submit?...` URL; this widget intercepts that navigation
(acceptNavigationRequest), decodes the JSON, and emits it as a normal
Qt signal (`formSubmitted`) for your host app to persist however it likes
(write to a DB, call an API, append to a file, etc.).

Requires: PyQt5, PyQtWebEngine
    pip install PyQt5 PyQtWebEngine
"""

import sys
import json
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication, QMessageBox
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage

try:
    from ..api_client import ApiClient, ApiError
except ImportError:  # standalone execution
    ApiClient = None  # type: ignore
    ApiError = Exception  # type: ignore


# ---------------------------------------------------------------------- #
# Custom page: intercepts the fake "poform:" navigation used by the JS
# form instead of a real HTTP submit, so nothing ever leaves the process.
# ---------------------------------------------------------------------- #
class _FormBridgePage(QWebEnginePage):
    def __init__(self, on_submit, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame):
        if url.scheme() == "poform":
            self._on_submit(url)
            return False  # swallow the navigation, we handled it in Python
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class POCreateWidget(QWidget):
    """
    Offline "Create Purchase Order" form (ME21N equivalent).

    Parameters
    ----------
    currency : str
        Currency code shown on the (read-only) currency field.
    parent : QWidget, optional

    Signals
    -------
    formSubmitted(dict)
        Emitted when the user clicks "Save & Send for Approval". Payload:
        {
          "supplier_id": int, "po_date": str, "delivery_date": str,
          "payment_terms": str, "discount_amount": float, "remarks": str,
          "lines": [
            {"item_id": int, "uom_id": int, "qty": float, "unit_price": float,
             "discount_pct": float, "gst_rate": float, "line_total": float,
             "pr_line_id": int|None},
            ...
          ]
        }
    """

    formSubmitted = pyqtSignal(dict)

    def __init__(self, api: Optional["ApiClient"] = None, currency: str = "INR", parent: QWidget = None):
        if isinstance(api, str):
            currency = api
            api = None
        super().__init__(parent)
        self.api = api
        self.currency = currency
        self._loaded_once = False

        self.suppliers = []            # [{id, supplier_code, name}]
        self.items = []                # [{id, item_code, item_name, moving_avg_price, gst_rate, uom_id, uom_code}]
        self.uoms = []                 # [{id, code}]
        self.eligible_prs = []         # [{id, pr_number, pr_date, open_lines, status}]
        self.pr_lines_by_pr = {}       # {pr_id: [{id, item_id, item_code, item_name, qty_requested, qty_approved, est_price, uom_id, is_broken}]}
        self.supplier_last_price = {}  # {supplier_id: {item_id: {"price":.., "disc":.., "gst":..}}}

        self._build_ui()
        if self.api is not None:
            self.formSubmitted.connect(self._submit_to_api)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.api is not None and not self._loaded_once:
            self._loaded_once = True
            self.reload_data()

    def reload_data(self) -> None:
        """Fetch lookups from ApiClient.po_lookups() and render the form."""
        if self.api is None:
            self.render()
            return
        try:
            data = self.api.po_lookups() or {}
        except ApiError as e:
            QMessageBox.warning(self, "Purchase Order", f"Could not load PO lookups:\n{e}")
            self.render()
            return

        self.set_suppliers([
            {
                "id": s["id"],
                "supplier_code": s.get("supplier_code", ""),
                "name": s.get("supplier_name") or s.get("name", ""),
            }
            for s in (data.get("suppliers") or [])
        ])
        self.set_items(data.get("items") or [])
        self.set_uoms(data.get("uoms") or [])

        # Group flat pending_prs rows from /api/po/lookups by PR id
        prs_map: dict[int, dict] = {}
        pr_lines_by_pr: dict[str, list] = {}
        for row in (data.get("pending_prs") or []):
            pr_id = row.get("id")
            if pr_id is None:
                continue
            if pr_id not in prs_map:
                prs_map[pr_id] = {
                    "id": pr_id,
                    "pr_number": row.get("pr_number", ""),
                    "pr_date": row.get("pr_date", ""),
                    "open_lines": 0,
                }
            prs_map[pr_id]["open_lines"] += 1
            pr_lines_by_pr.setdefault(str(pr_id), []).append({
                "id": row.get("line_id") or row.get("id"),
                "item_id": row.get("item_id"),
                "item_code": row.get("item_code", ""),
                "item_name": row.get("item_name", ""),
                "qty_requested": row.get("qty_requested", 0),
                "qty_approved": row.get("qty_approved", 0),
                "est_price": row.get("est_price", 0),
                "uom_id": row.get("uom_id"),
            })
        self.set_eligible_prs(list(prs_map.values()), pr_lines_by_pr)
        self.render()

    def _submit_to_api(self, payload: dict) -> None:
        if self.api is None:
            return
        api_lines = []
        for ln in payload.get("lines") or []:
            if not ln.get("uom_id"):
                QMessageBox.warning(self, "Create PO", "Please select a UOM for every line item.")
                return
            api_lines.append({
                "item_id": ln["item_id"],
                "uom_id": ln["uom_id"],
                "qty_ordered": ln.get("qty_ordered", ln.get("qty", 0)),
                "unit_price": ln.get("unit_price", 0),
                "discount_pct": ln.get("discount_pct", 0),
                "gst_rate": ln.get("gst_rate", 0),
                "pr_line_id": ln.get("pr_line_id"),
            })
        api_payload = {
            "supplier_id": payload["supplier_id"],
            "po_date": payload.get("po_date") or None,
            "delivery_date": payload.get("delivery_date") or None,
            "payment_terms": payload.get("payment_terms") or "",
            "currency_code": self.currency,
            "discount_amount": payload.get("discount_amount", 0),
            "remarks": payload.get("remarks") or "",
            "lines": api_lines,
        }
        try:
            res = self.api.po_create(api_payload)
            msg = res.get("message") or "Purchase Order created successfully."
            QMessageBox.information(self, "Purchase Order Created", msg)
            self.reload_data()
        except ApiError as e:
            QMessageBox.warning(self, "Create PO Failed", str(e))

    # ------------------------------------------------------------------ #
    # Data setters — call these (any subset) before/after render()
    # ------------------------------------------------------------------ #

    def set_suppliers(self, suppliers: list):
        self.suppliers = suppliers or []

    def set_items(self, items: list):
        self.items = items or []

    def set_uoms(self, uoms: list):
        self.uoms = uoms or []

    def set_eligible_prs(self, prs: list, pr_lines_by_pr: dict = None):
        self.eligible_prs = prs or []
        self.pr_lines_by_pr = pr_lines_by_pr or {}

    def set_supplier_last_price(self, supplier_last_price: dict):
        self.supplier_last_price = supplier_last_price or {}

    # ------------------------------------------------------------------ #
    # Build / rebuild the page
    # ------------------------------------------------------------------ #

    def render(self):
        """(Re)build the HTML from current data and load it into the view."""
        html = self._build_html()
        self.view.setHtml(html, QUrl("about:blank"))

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        page = _FormBridgePage(self._handle_submit_url, self.view)
        self.view.setPage(page)
        layout.addWidget(self.view)

    def _handle_submit_url(self, url: QUrl):
        qs = parse_qs(urlparse(url.toString()).query)
        raw = qs.get("data", ["{}"])[0]
        try:
            payload = json.loads(unquote(raw))
        except (ValueError, json.JSONDecodeError):
            QMessageBox.warning(self, "Create PO", "Could not read the form data.")
            return
        self.formSubmitted.emit(payload)

    def _build_html(self) -> str:
        supplier_opts = "".join(
            f'<option value="{s["id"]}">{_esc(s.get("supplier_code",""))} — {_esc(s.get("name",""))}</option>'
            for s in self.suppliers
        )
        uom_opts = '<option value="">UOM</option>' + "".join(
            f'<option value="{u["id"]}">{_esc(u.get("code",""))}</option>' for u in self.uoms
        )
        pr_opts = '<option value="">— Select PR to pull items from —</option>' + "".join(
            f'<option value="{pr["id"]}">{_esc(pr.get("pr_number",""))} — {pr.get("pr_date","")} '
            f'({pr.get("open_lines",0)} item{"s" if pr.get("open_lines",0)!=1 else ""} pending)</option>'
            for pr in self.eligible_prs
        )

        items_json = json.dumps({str(i["id"]): i for i in self.items})
        uoms_json = json.dumps(self.uoms)
        pr_lines_json = json.dumps(self.pr_lines_by_pr)
        last_prices_json = json.dumps(self.supplier_last_price)

        html = _TEMPLATE
        html = html.replace("__SUPPLIER_OPTIONS__", supplier_opts)
        html = html.replace("__UOM_OPTIONS__", uom_opts)
        html = html.replace("__PR_OPTIONS__", pr_opts)
        html = html.replace("__ITEMS_JSON__", items_json)
        html = html.replace("__UOMS_JSON__", uoms_json)
        html = html.replace("__PR_LINES_JSON__", pr_lines_json)
        html = html.replace("__LAST_PRICES_JSON__", last_prices_json)
        html = html.replace("__CURRENCY__", _esc(self.currency))
        return html


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_TEMPLATE = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 16px; color: #212529; }
  h2 { font-size: 16px; background: #0d6efd; color: #fff; padding: 8px 12px; border-radius: 4px 4px 0 0; margin: 0; }
  .card { border: 1px solid #dee2e6; border-radius: 4px; margin-bottom: 16px; }
  .card-body { padding: 12px; }
  .row { display: flex; flex-wrap: wrap; gap: 10px; }
  .col { flex: 1 1 200px; }
  label { display: block; font-size: 12px; font-weight: 600; margin-bottom: 3px; }
  input, select { width: 100%; padding: 5px; font-size: 13px; border: 1px solid #ced4da; border-radius: 3px; box-sizing: border-box; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { border: 1px solid #dee2e6; padding: 5px; }
  th { background: #0d6efd; color: #fff; font-size: 11px; }
  .text-end { text-align: right; }
  .fw-bold { font-weight: 700; }
  .btn { padding: 6px 14px; border-radius: 4px; border: 1px solid transparent; cursor: pointer; font-size: 13px; }
  .btn-primary { background: #0d6efd; color: #fff; }
  .btn-outline-danger { background: #fff; color: #dc3545; border-color: #dc3545; padding: 2px 8px; }
  .btn-outline-primary { background: #fff; color: #0d6efd; border-color: #0d6efd; }
  .warn-row { background: #fff3cd; }
  .danger-row { background: #f8d7da; }
  .totals td { font-weight: 600; }
  .grand { background: #d1e7dd; }
</style>
</head>
<body>

<div class="card">
  <h2>Create from Purchase Requisition (optional)</h2>
  <div class="card-body row">
    <div class="col" style="flex:3 1 400px">
      <label>Purchase Requisition</label>
      <select id="pr-picker">__PR_OPTIONS__</select>
    </div>
    <div class="col" style="flex:1 1 120px; align-self:flex-end">
      <button type="button" class="btn btn-primary" style="width:100%" onclick="addPrLines()">+ Add Item(s)</button>
    </div>
  </div>
</div>

<div class="card">
  <h2>Purchase Order Header</h2>
  <div class="card-body">
    <div class="row">
      <div class="col">
        <label>Supplier *</label>
        <select id="sel-supplier">
          <option value="">— Select Supplier —</option>
          __SUPPLIER_OPTIONS__
        </select>
      </div>
      <div class="col" style="flex:0 1 150px">
        <label>PO Date *</label>
        <input type="date" id="po-date">
      </div>
      <div class="col" style="flex:0 1 150px">
        <label>Delivery Date</label>
        <input type="date" id="delivery-date">
      </div>
      <div class="col" style="flex:0 1 150px">
        <label>Payment Terms</label>
        <select id="payment-terms">
          <option>Net 30</option><option>Net 45</option><option>Net 60</option>
          <option>Immediate</option><option>Advance</option>
        </select>
      </div>
      <div class="col" style="flex:0 1 100px">
        <label>Currency</label>
        <input type="text" value="__CURRENCY__" readonly>
      </div>
      <div class="col" style="flex:0 1 120px">
        <label>Discount (__CURRENCY__)</label>
        <input type="number" id="po-discount" min="0" step="0.01" value="0" oninput="recalcAll()">
      </div>
    </div>
    <div class="row" style="margin-top:10px">
      <div class="col" style="flex:1 1 100%">
        <label>Notes</label>
        <input type="text" id="po-remarks" placeholder="Notes printed on the PO...">
      </div>
    </div>
  </div>
</div>

<div class="card">
  <h2>Line Items <button type="button" class="btn btn-outline-primary" style="float:right" onclick="addLine()">+ Add Line</button></h2>
  <div class="card-body" style="padding:0">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Material</th><th>UOM</th><th>Qty</th><th>Unit Price</th>
          <th>Disc%</th><th>GST%</th><th>Net Value</th><th></th>
        </tr>
      </thead>
      <tbody id="lines-body"></tbody>
      <tfoot>
        <tr class="totals"><td colspan="6" class="text-end">Taxable Amount:</td><td colspan="2"><input id="total-base" class="text-end" readonly value="0.00"></td><td></td></tr>
        <tr class="totals"><td colspan="6" class="text-end">GST Amount:</td><td colspan="2"><input id="total-tax" class="text-end" readonly value="0.00"></td><td></td></tr>
        <tr class="totals"><td colspan="6" class="text-end">Discount:</td><td colspan="2"><input id="total-discount" class="text-end" readonly value="0.00"></td><td></td></tr>
        <tr class="totals"><td colspan="6" class="text-end">Round Off:</td><td colspan="2"><input id="total-roundoff" class="text-end" readonly value="0.00"></td><td></td></tr>
        <tr class="totals grand"><td colspan="6" class="text-end">Grand Total:</td><td colspan="2"><input id="total-grand" class="text-end fw-bold" readonly value="0.00"></td><td></td></tr>
      </tfoot>
    </table>
  </div>
</div>

<button type="button" class="btn btn-primary" onclick="submitForm()">Save &amp; Send for Approval</button>

<script>
const itemsData   = __ITEMS_JSON__;      // id(str) -> item
const uomsData    = __UOMS_JSON__;       // [{id, code}]
const prLinesData = __PR_LINES_JSON__;   // prId(str) -> [line, ...]
const lastPrices  = __LAST_PRICES_JSON__;// supplierId -> itemId -> {price, disc, gst}

let lineCount = 0;

function uomOptionsHtml(selectedId) {
  let html = '<option value="">UOM</option>';
  uomsData.forEach(u => {
    const sel = String(u.id) === String(selectedId) ? 'selected' : '';
    html += `<option value="${u.id}" ${sel}>${u.code}</option>`;
  });
  return html;
}

function itemOptionsHtml() {
  let html = '<option value="">— Item —</option>';
  Object.keys(itemsData).forEach(id => {
    const it = itemsData[id];
    html += `<option value="${id}">${it.item_code} \u2013 ${it.item_name}</option>`;
  });
  return html;
}

function addLine(prefill) {
  prefill = prefill || {};
  const idx = lineCount++;
  const tbody = document.getElementById('lines-body');
  const tr = document.createElement('tr');
  tr.className = 'line-row' + (prefill.is_broken ? ' danger-row' : (prefill.pr_line_id ? ' warn-row' : ''));
  tr.dataset.prLineId = prefill.pr_line_id || '';

  const fixedItem = prefill.item_id && !prefill.is_broken;
  const itemCell = fixedItem
    ? `<div style="font-weight:600">${itemsData[prefill.item_id] ? itemsData[prefill.item_id].item_code + ' \u2013 ' + itemsData[prefill.item_id].item_name : prefill.item_name || ''}</div>
       ${prefill.qty_requested != null ? `<div style="font-size:11px;color:#6c757d">from PR (qty requested: ${prefill.qty_requested})</div>` : ''}`
    : `<select class="item-select" onchange="onItemSelected(this)">${itemOptionsHtml()}</select>`;

  tr.innerHTML = `
    <td class="line-num">${idx + 1}</td>
    <td>${itemCell}</td>
    <td><select class="line-uom">${uomOptionsHtml(prefill.uom_id)}</select></td>
    <td><input type="number" class="line-qty" min="0.001" step="0.001" value="${prefill.qty_approved || prefill.qty_requested || 1}" oninput="calcRow(this.closest('tr'))"></td>
    <td><input type="number" class="line-price" min="0" step="0.01" value="${prefill.est_price || (fixedItem && itemsData[prefill.item_id] ? itemsData[prefill.item_id].moving_avg_price : 0) || 0}" oninput="this.dataset.touched='1'; calcRow(this.closest('tr'))"></td>
    <td><input type="number" class="line-disc" min="0" max="100" step="0.01" value="0" oninput="calcRow(this.closest('tr'))"></td>
    <td><input type="number" class="line-tax" min="0" max="28" step="0.01" value="${(fixedItem && itemsData[prefill.item_id]) ? itemsData[prefill.item_id].gst_rate : 18}" oninput="calcRow(this.closest('tr'))"></td>
    <td><input type="text" class="line-total text-end fw-bold" readonly value="0.00"></td>
    <td><button type="button" class="btn btn-outline-danger" onclick="removeLine(this)">x</button></td>
  `;
  if (fixedItem) {
    tr.dataset.itemId = prefill.item_id;
  }
  tbody.appendChild(tr);
  calcRow(tr);
}

function removeLine(btn) {
  const rows = document.querySelectorAll('.line-row');
  if (rows.length <= 1) { alert('At least one line is required'); return; }
  btn.closest('tr').remove();
  renumber();
  recalcAll();
}

function renumber() {
  document.querySelectorAll('.line-row').forEach((row, i) => { row.querySelector('.line-num').textContent = i + 1; });
}

function onItemSelected(sel) {
  const row = sel.closest('tr');
  const item = itemsData[sel.value];
  if (!item) return;
  row.dataset.itemId = sel.value;
  const hist = supplierHistory(sel.value);
  row.querySelector('.line-price').value = (hist ? hist.price : item.moving_avg_price || 0).toFixed ? (hist ? hist.price : item.moving_avg_price || 0).toFixed(2) : (hist ? hist.price : item.moving_avg_price || 0);
  row.querySelector('.line-tax').value = hist && hist.gst != null ? hist.gst : (item.gst_rate || 18);
  if (hist && hist.disc != null) row.querySelector('.line-disc').value = hist.disc;
  const uomSel = row.querySelector('.line-uom'); if (uomSel) uomSel.value = item.uom_id;
  delete row.querySelector('.line-price').dataset.touched;
  calcRow(row);
}

function supplierHistory(itemId) {
  const sid = document.getElementById('sel-supplier').value;
  return (sid && lastPrices[sid] && lastPrices[sid][itemId]) ? lastPrices[sid][itemId] : null;
}

document.getElementById ? null : null;
document.addEventListener('DOMContentLoaded', () => {
  const sup = document.getElementById('sel-supplier');
  if (sup) sup.addEventListener('change', () => {
    document.querySelectorAll('.line-row').forEach(row => {
      const itemId = row.dataset.itemId;
      const priceEl = row.querySelector('.line-price');
      if (!itemId || !priceEl || priceEl.dataset.touched === '1') return;
      const hist = supplierHistory(itemId);
      const item = itemsData[itemId];
      if (!item) return;
      priceEl.value = hist ? hist.price : (item.moving_avg_price || 0);
      calcRow(row);
    });
  });
});

function calcRow(row) {
  const qty   = parseFloat(row.querySelector('.line-qty').value)   || 0;
  const price = parseFloat(row.querySelector('.line-price').value) || 0;
  const disc  = parseFloat(row.querySelector('.line-disc').value)  || 0;
  const base  = qty * price * (1 - disc / 100);
  row.querySelector('.line-total').value = base.toFixed(2);
  recalcAll();
}

function recalcAll() {
  let base = 0, tax = 0;
  document.querySelectorAll('.line-row').forEach(row => {
    const b = parseFloat(row.querySelector('.line-total').value) || 0;
    const t = parseFloat(row.querySelector('.line-tax').value)   || 0;
    base += b; tax += b * t / 100;
  });
  const discount = parseFloat(document.getElementById('po-discount').value) || 0;
  const preRound = base + tax - discount;
  const roundOff = Math.round(preRound) - preRound;
  document.getElementById('total-base').value     = base.toFixed(2);
  document.getElementById('total-tax').value      = tax.toFixed(2);
  document.getElementById('total-discount').value = discount.toFixed(2);
  document.getElementById('total-roundoff').value = roundOff.toFixed(2);
  document.getElementById('total-grand').value    = (preRound + roundOff).toFixed(2);
}

function addPrLines() {
  const prId = document.getElementById('pr-picker').value;
  if (!prId) return;
  const lines = prLinesData[prId] || [];
  lines.forEach(line => {
    const item = itemsData[line.item_id];
    addLine({
      item_id: line.item_id, item_name: line.item_name, qty_requested: line.qty_requested,
      qty_approved: line.qty_approved, est_price: line.est_price, uom_id: line.uom_id,
      pr_line_id: line.id, is_broken: !item
    });
  });
}

function submitForm() {
  const supplierId = document.getElementById('sel-supplier').value;
  if (!supplierId) { alert('Please select a supplier.'); return; }
  const lines = [];
  document.querySelectorAll('.line-row').forEach(row => {
    const itemSel = row.querySelector('.item-select');
    const itemId = itemSel ? itemSel.value : row.dataset.itemId;
    const qty = parseFloat(row.querySelector('.line-qty').value) || 0;
    if (!itemId || qty <= 0) return;
    lines.push({
      item_id: parseInt(itemId, 10),
      uom_id: row.querySelector('.line-uom').value ? parseInt(row.querySelector('.line-uom').value, 10) : null,
      qty: qty,
      unit_price: parseFloat(row.querySelector('.line-price').value) || 0,
      discount_pct: parseFloat(row.querySelector('.line-disc').value) || 0,
      gst_rate: parseFloat(row.querySelector('.line-tax').value) || 0,
      line_total: parseFloat(row.querySelector('.line-total').value) || 0,
      pr_line_id: row.dataset.prLineId ? parseInt(row.dataset.prLineId, 10) : null
    });
  });
  if (!lines.length) { alert('Add at least one line item.'); return; }

  const payload = {
    supplier_id: parseInt(supplierId, 10),
    po_date: document.getElementById('po-date').value,
    delivery_date: document.getElementById('delivery-date').value,
    payment_terms: document.getElementById('payment-terms').value,
    discount_amount: parseFloat(document.getElementById('po-discount').value) || 0,
    remarks: document.getElementById('po-remarks').value,
    lines: lines
  };
  window.location.href = 'poform:submit?data=' + encodeURIComponent(JSON.stringify(payload));
}

// seed one empty line and set today's date
document.getElementById('po-date').value = new Date().toISOString().slice(0, 10);
addLine();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- #
# Standalone demo
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)

    w = POCreateWidget(currency="INR")
    w.set_suppliers([
        {"id": 1, "supplier_code": "SUP-001", "name": "Acme Traders"},
        {"id": 2, "supplier_code": "SUP-002", "name": "Bharat Metals"},
    ])
    w.set_items([
        {"id": 10, "item_code": "RM-001", "item_name": "Steel Rod 12mm", "moving_avg_price": 450.0, "gst_rate": 18, "uom_id": 1, "uom_code": "KG"},
        {"id": 11, "item_code": "RM-002", "item_name": "Copper Wire 2mm", "moving_avg_price": 920.0, "gst_rate": 18, "uom_id": 1, "uom_code": "KG"},
    ])
    w.set_uoms([{"id": 1, "code": "KG"}, {"id": 2, "code": "PCS"}])
    w.set_eligible_prs(
        [{"id": 100, "pr_number": "PR-2026-001", "pr_date": "2026-11-20", "open_lines": 2, "status": "APPROVED"}],
        pr_lines_by_pr={
            "100": [
                {"id": 500, "item_id": 10, "item_code": "RM-001", "item_name": "Steel Rod 12mm",
                 "qty_requested": 100, "qty_approved": 100, "est_price": 440.0, "uom_id": 1},
            ]
        },
    )
    w.render()
    w.resize(1100, 800)
    w.setWindowTitle("ME21N — Create Purchase Order (offline)")
    w.formSubmitted.connect(lambda data: QMessageBox.information(w, "PO Submitted", json.dumps(data, indent=2)))
    w.show()

    sys.exit(app.exec())