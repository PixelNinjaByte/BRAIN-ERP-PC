"""
Brain ERP — Shared Documents Layer: Purchase Order & Sales Order
print / PDF / email / WhatsApp / share.
Direct port of PHP core/documents.php -> Python.

  - render_po_html() / render_so_html(): the printable document body (used
    by ME23N, VA03 and the public share page, so what you see, print,
    download and send is always identical) — ported faithfully, this is
    the actual PO/SO layout, not WordPress admin chrome.
  - Public page  build_public_doc_page(type, token)  (no login; the random
    token in purchase_orders/sales_orders.doc_token is the credential).
      print=True  opens the browser print dialog on load
      pdf=True    downloads a real PDF (generated in-browser via html2pdf.js)
  - doc_action_links(): Print / PDF / Email / WhatsApp / Share link data for
    the PO and SO screens — returned as plain data (see note below), not
    WP markup.
  - send_document_mail(): emails the document link to the party.

Replaces:
  ErpDB::wpdb()->query('ALTER TABLE ...')  -> ErpDB.get_columns() + ALTER
                                               TABLE (SQLite)
  add_query_arg()/home_url()               -> doc_url() against a
                                               configurable local app base
                                               URL (ErpDB.get_option
                                               'app_base_url', defaults to
                                               the FastAPI dev server)
  wp_ajax_msme_erp_doc_mail (WP AJAX)       -> send_document_mail(), a plain
                                               function a FastAPI route can
                                               call directly — no nonces,
                                               no admin-ajax.php
  wp_mail()                                 -> core.helpers.send_email()
                                               (already ported, SMTP-based)

NOTE ON doc_action_links(): the PHP version echoed a <button>/<a> group
plus an inline-JS mail modal wired to WordPress AJAX. There is no WP AJAX
or nonce system here, and (as with app.py's nav menus) rendering buttons
is a UI-layer decision, not a translation — so this returns the same
information (print/pdf/view URLs, WhatsApp link, mail prefill) as a plain
dict for a Qt dialog or a web template to build buttons from.

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import re
import secrets
import logging
from datetime import datetime
from html import escape
from typing import Optional
from urllib.parse import urlencode

from core.db import ErpDB
from core.auth import ErpAuth
from core.helpers import send_email, num_to_words

logger = logging.getLogger(__name__)

_doc_col_checked: set[str] = set()


# ------------------------------------------------------------
# Token + loaders
# ------------------------------------------------------------
def doc_table(doc_type: str) -> str:
    return "sales_orders" if doc_type == "so" else "purchase_orders"


def doc_ensure_col(doc_type: str) -> None:
    """Self-healing schema: add doc_token if an older install is missing it."""
    tbl = doc_table(doc_type)
    if tbl in _doc_col_checked:
        return
    _doc_col_checked.add(tbl)
    if "doc_token" not in ErpDB.get_columns(tbl):
        ErpDB.query(f"ALTER TABLE {tbl} ADD COLUMN doc_token VARCHAR(64)")
        ErpDB.query(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_doc_token ON {tbl}(doc_token)")


def doc_token(doc_type: str, record_id: int) -> str:
    doc_ensure_col(doc_type)
    tbl = doc_table(doc_type)
    tok = str(ErpDB.fetch_value(f"SELECT doc_token FROM {tbl} WHERE id=?", [record_id]) or "")
    if tok == "":
        tok = secrets.token_hex(20)
        ErpDB.query(f"UPDATE {tbl} SET doc_token=? WHERE id=?", [tok, record_id])
    return tok


def app_base_url() -> str:
    """Local app URL the public document page is served from."""
    return str(ErpDB.get_option("app_base_url", "http://127.0.0.1:8000"))


def doc_url(doc_type: str, record_id: int, extra: Optional[dict] = None) -> str:
    args = {"msme_doc": doc_token(doc_type, record_id), "t": doc_type, **(extra or {})}
    return f"{app_base_url().rstrip('/')}/documents?{urlencode(args)}"


def doc_load_po(record_id: int, company_id: Optional[int] = None) -> Optional[dict]:
    sql = """
        SELECT po.*, s.name AS supplier_name, s.supplier_code, s.gstin AS supplier_gstin,
               s.pan_number AS supplier_pan, s.address AS supplier_addr, s.city AS supplier_city,
               s.state AS supplier_state, s.pincode AS supplier_pincode, s.phone AS supplier_phone,
               s.email AS supplier_email,
               c.name AS company_name, c.address AS comp_addr, c.city AS comp_city, c.state AS comp_state,
               c.pincode AS comp_pincode, c.phone AS comp_phone, c.email AS comp_email,
               c.gstin AS comp_gstin, c.pan_number AS comp_pan
        FROM purchase_orders po
        JOIN suppliers s ON s.id = po.supplier_id
        JOIN companies c ON c.id = po.company_id
        WHERE po.id = ?""" + (" AND po.company_id = ?" if company_id else "")
    params = [record_id, company_id] if company_id else [record_id]
    po = ErpDB.fetch_one(sql, params)
    if not po:
        return None
    lines = ErpDB.fetch_all(
        """SELECT pol.*, i.item_code, i.item_name, u.code AS uom_code
           FROM purchase_order_lines pol
           JOIN items i ON i.id = pol.item_id
           LEFT JOIN units_of_measure u ON u.id = pol.uom_id
           WHERE pol.po_id = ? ORDER BY pol.line_num""", [record_id])
    groups = _gst_rate_groups(lines)
    bank = ErpDB.fetch_one(
        "SELECT * FROM bank_accounts WHERE company_id=? AND is_active=1 ORDER BY id LIMIT 1",
        [int(po["company_id"])])
    return {"po": po, "lines": lines, "groups": groups, "bank": bank}


def doc_load_so(record_id: int, company_id: Optional[int] = None) -> Optional[dict]:
    sql = """
        SELECT so.*, cu.name AS cust_name, cu.customer_code, cu.email AS cust_email, cu.phone AS cust_phone,
               cu.gstin AS cust_gstin, cu.pan_number AS cust_pan, cu.billing_address AS cust_addr,
               cu.shipping_address AS cust_ship_addr, cu.city AS cust_city, cu.state AS cust_state,
               cu.pincode AS cust_pincode,
               cm.name AS company_name, cm.address AS comp_addr, cm.city AS comp_city, cm.state AS comp_state,
               cm.pincode AS comp_pincode, cm.phone AS comp_phone, cm.email AS comp_email,
               cm.gstin AS comp_gstin, cm.pan_number AS comp_pan
        FROM sales_orders so
        LEFT JOIN customers cu ON cu.id = so.customer_id
        JOIN companies cm ON cm.id = so.company_id
        WHERE so.id = ?""" + (" AND so.company_id = ?" if company_id else "")
    params = [record_id, company_id] if company_id else [record_id]
    so = ErpDB.fetch_one(sql, params)
    if not so:
        return None
    lines = ErpDB.fetch_all(
        """SELECT sol.*, i.item_code, i.item_name, u.code AS uom_code
           FROM sales_order_lines sol
           LEFT JOIN items i ON i.id = sol.item_id
           LEFT JOIN units_of_measure u ON u.id = sol.uom_id
           WHERE sol.so_id = ? ORDER BY sol.line_num""", [record_id])
    groups = _gst_rate_groups(lines)
    bank = ErpDB.fetch_one(
        "SELECT * FROM bank_accounts WHERE company_id=? AND is_active=1 ORDER BY id LIMIT 1",
        [int(so["company_id"])])
    return {"so": so, "lines": lines, "groups": groups, "bank": bank}


def _gst_rate_groups(lines: list[dict]) -> dict[float, dict[str, float]]:
    groups: dict[float, dict[str, float]] = {}
    for l in lines:
        rate = float(l["gst_rate"])
        taxable = l["qty_ordered"] * l["unit_price"] * (1 - l["discount_pct"] / 100)
        g = groups.setdefault(rate, {"taxable": 0.0, "gst": 0.0})
        g["taxable"] += taxable
        g["gst"] += taxable * rate / 100
    return dict(sorted(groups.items()))


# ------------------------------------------------------------
# Small formatting helpers (mirror the PHP inline closures)
# ------------------------------------------------------------
def _money(n) -> str:
    return f"{float(n or 0):,.2f}"


def _qty(n) -> str:
    n = float(n or 0)
    return f"{n:,.0f}" if n == int(n) else f"{n:,.3f}"


def _pct(n) -> str:
    s = f"{float(n or 0):.2f}".rstrip("0").rstrip(".")
    return s


def _fmt_date(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = datetime.strptime(v[:10], "%Y-%m-%d")
    return v.strftime("%d %B %Y")


def _addr_block(addr, city, state, pin) -> str:
    l2 = " ".join(filter(None, [
        f"{city}{',' if city and state else ''}", state, (f" {pin}" if pin else "")
    ])).strip()
    l2 = re.sub(r"\s+,", ",", l2).strip()
    out = ""
    if addr:
        out += f'<div class="small">{escape(addr).replace(chr(10), "<br>")}</div>'
    if l2:
        out += f'<div class="small">{escape(l2)}</div>'
    return out


# ------------------------------------------------------------
# Purchase Order body (identical to what ME23N always printed)
# ------------------------------------------------------------
def render_po_html(po: dict, lines: list[dict], rate_groups: dict, bank: Optional[dict]) -> str:
    e = escape
    city_line = ", ".join(filter(None, [po.get("supplier_city"), po.get("supplier_state")]))
    comp_city_line = ", ".join(filter(None, [po.get("comp_city"), po.get("comp_state")]))

    rows = []
    total_qty = 0.0
    for l in lines:
        qty = float(l["qty_ordered"])
        taxable = qty * l["unit_price"] * (1 - l["discount_pct"] / 100)
        gst_unit = l["unit_price"] * l["gst_rate"] / 100
        line_amount = taxable * (1 + l["gst_rate"] / 100)
        total_qty += qty
        rows.append(f"""<tr>
          <td>{e(l['item_name'])}</td>
          <td class="text-center">{_qty(qty)} {e(l.get('uom_code') or '')}</td>
          <td class="text-end">Rs. {_money(l['unit_price'])}</td>
          <td class="text-end">Rs. {_money(gst_unit)} ({_pct(l['gst_rate'])}%)</td>
          <td class="text-end">Rs. {_money(line_amount)}</td>
        </tr>""")

    gst_rows = []
    for rate, g in rate_groups.items():
        if g["taxable"] <= 0 and g["gst"] <= 0:
            continue
        gst_rows.append(f'<tr><td class="text-muted">CGST @{_pct(rate / 2)}%</td><td class="text-end">Rs. {_money(g["gst"] / 2)}</td></tr>')
        gst_rows.append(f'<tr><td class="text-muted">SGST @{_pct(rate / 2)}%</td><td class="text-end">Rs. {_money(g["gst"] / 2)}</td></tr>')

    discount_row = (f'<tr><td class="text-muted">Discount</td><td class="text-end">- Rs. {_money(po["discount_amount"])}</td></tr>'
                     if float(po.get("discount_amount") or 0) > 0 else "")
    bank_block = ""
    if bank:
        bank_block = f"""
        <div class="fw-bold small mb-1">Bank Details</div>
        <div class="small">Account holder: {e(bank.get('account_name') or '—')}</div>
        <div class="small">Account number: {e(bank.get('account_num') or '—')}</div>
        <div class="small">Bank: {e(bank.get('bank_name') or '—')}</div>
        <div class="small">IFSC code: {e(bank.get('ifsc_code') or '—')}</div>
        {f'<div class="small">UPI ID: {e(bank["upi_id"])}</div>' if bank.get('upi_id') else ''}"""
    notes_block = (f'<div class="fw-bold small mt-3 mb-1">Notes</div><div class="small">{e(po["remarks"]).replace(chr(10), "<br>")}</div>'
                    if po.get("remarks") else "")

    return f"""<div class="row mb-3 align-items-start">
  <div class="col-7">
    <h4 class="fw-bold mb-1" style="color:#1560bd">{e(po.get('company_name') or '')}</h4>
    <div class="small">{e(po.get('comp_addr') or '').replace(chr(10), '<br>')}</div>
    <div class="small">{e(comp_city_line)}{(' ' + e(po['comp_pincode'])) if po.get('comp_pincode') else ''}</div>
    {f'<div class="small">Phone : {e(po["comp_phone"])}</div>' if po.get('comp_phone') else ''}
    <div class="small">GSTIN : {e(po.get('comp_gstin') or '—')}</div>
    {f'<div class="small">PAN Number : {e(po["comp_pan"])}</div>' if po.get('comp_pan') else ''}
  </div>
  <div class="col-5 text-md-end">
    <div class="fw-bold fs-4 mb-2">PURCHASE ORDER</div>
    <div class="small"><span class="text-muted">Purchase Order No :</span> <strong>{e(po['po_number'])}</strong></div>
    <div class="small"><span class="text-muted">Purchase Order Date :</span> {_fmt_date(po['po_date'])}</div>
    {f'<div class="small"><span class="text-muted">Delivery Date :</span> {_fmt_date(po["delivery_date"])}</div>' if po.get('delivery_date') else ''}
  </div>
</div>

<div class="row mb-3 g-2">
  <div class="col-6">
    <div class="text-white fw-bold px-2 py-1 mb-2" style="background:#1560bd;border-radius:4px">PURCHASE FROM</div>
    <div class="fw-bold">{e(po['supplier_name'])}</div>
    <div class="small">{e(po.get('supplier_addr') or '').replace(chr(10), '<br>')}</div>
    <div class="small">{e(city_line)}{(' — ' + e(po['supplier_pincode'])) if po.get('supplier_pincode') else ''}</div>
    {f'<div class="small">Phone : {e(po["supplier_phone"])}</div>' if po.get('supplier_phone') else ''}
    {f'<div class="small">PAN Number : {e(po["supplier_pan"])}</div>' if po.get('supplier_pan') else ''}
    <div class="small">GSTIN : {e(po.get('supplier_gstin') or '—')}</div>
  </div>
  <div class="col-6">
    <div class="text-white fw-bold px-2 py-1 mb-2" style="background:#1560bd;border-radius:4px">SHIP TO</div>
    <div class="fw-bold">{e(po.get('company_name') or '')}</div>
    <div class="small">{e(po.get('comp_addr') or '').replace(chr(10), '<br>')}</div>
    <div class="small">{e(comp_city_line)}</div>
    {f'<div class="small">Pin: {e(po["comp_pincode"])}</div>' if po.get('comp_pincode') else ''}
  </div>
</div>

<table class="table table-sm mb-0" style="font-size:.8rem">
  <thead class="text-white" style="background:#1560bd">
    <tr><th>Items</th><th class="text-center">Quantity</th><th class="text-end">Price per Unit</th>
        <th class="text-end">Tax per Unit</th><th class="text-end">Amount</th></tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<div class="text-white fw-bold d-flex justify-content-between px-2 py-2 mb-3" style="background:#1560bd;border-radius:0 0 4px 4px">
  <span>Sub Total</span><span>{_qty(total_qty)}</span><span>Rs. {_money(float(po['total_amount']) + float(po['tax_amount']))}</span>
</div>

<div class="row">
  <div class="col-md-6">
    {bank_block}
    {notes_block}
    <div class="fw-bold small mt-3 mb-1">Terms &amp; Conditions</div>
    <div class="small">
      1. Goods/services must conform to the specifications and quality agreed upon.<br>
      2. Delivery must be completed by the delivery date stated above.<br>
      3. Payment will be released as per the agreed payment terms.
    </div>
  </div>
  <div class="col-md-6">
    <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
      <tr><td class="text-muted">Taxable Amount</td><td class="text-end">Rs. {_money(po['total_amount'])}</td></tr>
      {''.join(gst_rows)}
      {discount_row}
      <tr><td class="text-muted">Round Off</td><td class="text-end">Rs. {_money(po['round_off'])}</td></tr>
      <tr class="border-top fw-bold"><td class="pt-2">Total Amount</td><td class="text-end pt-2">Rs. {_money(po['grand_total'])}</td></tr>
    </table>
  </div>
</div>

<div class="row mt-4 pt-4">
  <div class="col-6">
    <div class="border-top pt-1 mt-4" style="max-width:220px"><div class="small text-center text-muted">Supplier Signature</div></div>
  </div>
  <div class="col-6 text-end">
    <div class="border-top pt-1 mt-4 ms-auto" style="max-width:260px"><div class="small text-center text-muted">Authorised Signatory For {e(po.get('company_name') or '')}</div></div>
  </div>
</div>

<div class="text-center small mt-3">Thank You For Your Business !</div>
<div class="text-muted text-center mt-2 pt-2 border-top" style="font-size:.65rem">
  This is a computer generated document. | Generated by Prigag Brain — Developed by Prigag Online Pvt. Ltd. | www.prigag.com
</div>"""


# ------------------------------------------------------------
# Sales Order body
# ------------------------------------------------------------
def render_so_html(so: dict, lines: list[dict], rate_groups: dict, bank: Optional[dict]) -> str:
    e = escape
    taxable = float(so["total_amount"])
    gst = float(so["tax_amount"])
    grand = float(so["grand_total"])
    round_off = round(grand - (taxable + gst), 2)

    ship_to = (so.get("ship_to_addr") or "").strip() or so.get("cust_ship_addr") or so.get("cust_addr") or ""

    rows = []
    total_qty = 0.0
    for n, l in enumerate(lines, start=1):
        qty = float(l["qty_ordered"])
        tx = qty * l["unit_price"] * (1 - l["discount_pct"] / 100)
        amt = tx * (1 + l["gst_rate"] / 100)
        total_qty += qty
        disc = f'{_pct(l["discount_pct"])}%' if float(l["discount_pct"] or 0) > 0 else "—"
        rows.append(f"""<tr>
          <td>{n}</td>
          <td>{e(l.get('item_name') or '— missing item —')}</td>
          <td class="text-center">{_qty(qty)} {e(l.get('uom_code') or '')}</td>
          <td class="text-end">Rs. {_money(l['unit_price'])}</td>
          <td class="text-end">{disc}</td>
          <td class="text-end">{_pct(l['gst_rate'])}%</td>
          <td class="text-end">Rs. {_money(amt)}</td>
        </tr>""")

    gst_rows = []
    for rate, g in rate_groups.items():
        if g["taxable"] <= 0 and g["gst"] <= 0:
            continue
        gst_rows.append(f'<tr><td class="text-muted">CGST @{_pct(rate / 2)}%</td><td class="text-end">Rs. {_money(g["gst"] / 2)}</td></tr>')
        gst_rows.append(f'<tr><td class="text-muted">SGST @{_pct(rate / 2)}%</td><td class="text-end">Rs. {_money(g["gst"] / 2)}</td></tr>')
    round_off_row = (f'<tr><td class="text-muted">Round Off</td><td class="text-end">Rs. {_money(round_off)}</td></tr>'
                      if abs(round_off) >= 0.005 else "")

    bank_block = ""
    if bank:
        bank_block = f"""
        <div class="fw-bold small mb-1">Bank Details</div>
        <div class="small">Account holder: {e(bank.get('account_name') or '—')}</div>
        <div class="small">Account number: {e(bank.get('account_num') or '—')}</div>
        <div class="small">Bank: {e(bank.get('bank_name') or '—')}</div>
        <div class="small">IFSC code: {e(bank.get('ifsc_code') or '—')}</div>
        {f'<div class="small">UPI ID: {e(bank["upi_id"])}</div>' if bank.get('upi_id') else ''}"""
    notes_block = (f'<div class="fw-bold small mt-3 mb-1">Notes</div><div class="small">{e(so["remarks"]).replace(chr(10), "<br>")}</div>'
                    if so.get("remarks") else "")

    return f"""<div class="row mb-3 align-items-start">
  <div class="col-7">
    <h4 class="fw-bold mb-1" style="color:#198754">{e(so.get('company_name') or '')}</h4>
    {_addr_block(so.get('comp_addr'), so.get('comp_city'), so.get('comp_state'), so.get('comp_pincode'))}
    {f'<div class="small">Phone : {e(so["comp_phone"])}</div>' if so.get('comp_phone') else ''}
    <div class="small">GSTIN : {e(so.get('comp_gstin') or '—')}</div>
    {f'<div class="small">PAN Number : {e(so["comp_pan"])}</div>' if so.get('comp_pan') else ''}
  </div>
  <div class="col-5 text-end">
    <div class="fw-bold fs-4 mb-2">SALES ORDER</div>
    <div class="small"><span class="text-muted">Sales Order No :</span> <strong>{e(so['so_number'])}</strong></div>
    <div class="small"><span class="text-muted">Order Date :</span> {_fmt_date(so['so_date'])}</div>
    {f'<div class="small"><span class="text-muted">Delivery Date :</span> {_fmt_date(so["delivery_date"])}</div>' if so.get('delivery_date') else ''}
    {f'<div class="small"><span class="text-muted">Customer PO Ref :</span> {e(so["po_number"])}</div>' if so.get('po_number') else ''}
    {f'<div class="small"><span class="text-muted">Payment Terms :</span> {e(so["payment_terms"])}</div>' if so.get('payment_terms') else ''}
  </div>
</div>

<div class="row mb-3 g-2">
  <div class="col-6">
    <div class="text-white fw-bold px-2 py-1 mb-2" style="background:#198754;border-radius:4px">BILL TO</div>
    <div class="fw-bold">{e(so.get('cust_name') or '— customer record missing —')}</div>
    {_addr_block(so.get('cust_addr'), so.get('cust_city'), so.get('cust_state'), so.get('cust_pincode'))}
    {f'<div class="small">Phone : {e(so["cust_phone"])}</div>' if so.get('cust_phone') else ''}
    {f'<div class="small">PAN Number : {e(so["cust_pan"])}</div>' if so.get('cust_pan') else ''}
    <div class="small">GSTIN : {e(so.get('cust_gstin') or '—')}</div>
  </div>
  <div class="col-6">
    <div class="text-white fw-bold px-2 py-1 mb-2" style="background:#198754;border-radius:4px">SHIP TO</div>
    <div class="fw-bold">{e(so.get('cust_name') or '')}</div>
    <div class="small">{e(ship_to).replace(chr(10), '<br>')}</div>
  </div>
</div>

<table class="table table-sm mb-0" style="font-size:.8rem">
  <thead>
    <tr style="--bs-table-bg:#198754;--bs-table-color:#fff;--bs-table-border-color:#198754;background:#198754;color:#fff">
      <th style="width:32px">#</th><th>Items</th><th class="text-center">Quantity</th><th class="text-end">Price per Unit</th>
      <th class="text-end">Disc %</th><th class="text-end">Tax</th><th class="text-end">Amount</th>
    </tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<div class="text-white fw-bold d-flex justify-content-between px-2 py-2 mb-3" style="background:#198754;border-radius:0 0 4px 4px">
  <span>Sub Total</span><span>{_qty(total_qty)}</span><span>Rs. {_money(taxable + gst)}</span>
</div>

<div class="row">
  <div class="col-6">
    {bank_block}
    {notes_block}
    <div class="fw-bold small mt-3 mb-1">Terms &amp; Conditions</div>
    <div class="small">
      1. Goods will be dispatched as per the delivery date and quantities above.<br>
      2. Payment is due as per the payment terms stated.<br>
      3. Prices and taxes are as agreed on this order.
    </div>
  </div>
  <div class="col-6">
    <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
      <tr><td class="text-muted">Taxable Amount</td><td class="text-end">Rs. {_money(taxable)}</td></tr>
      {''.join(gst_rows)}
      {round_off_row}
      <tr class="border-top fw-bold"><td class="pt-2">Total Amount</td><td class="text-end pt-2">Rs. {_money(grand)}</td></tr>
    </table>
    <div class="small text-end text-muted fst-italic">{e(num_to_words(grand))}</div>
  </div>
</div>

<div class="row mt-4 pt-4">
  <div class="col-6">
    <div class="border-top pt-1 mt-4" style="max-width:220px"><div class="small text-center text-muted">Customer Acceptance</div></div>
  </div>
  <div class="col-6 text-end">
    <div class="border-top pt-1 mt-4 ms-auto" style="max-width:260px"><div class="small text-center text-muted">Authorised Signatory For {e(so.get('company_name') or '')}</div></div>
  </div>
</div>

<div class="text-center small mt-3">Thank You For Your Business !</div>
<div class="text-muted text-center mt-2 pt-2 border-top" style="font-size:.65rem">
  This is a computer generated document. | Generated by Prigag Brain — Developed by Prigag Online Pvt. Ltd. | www.prigag.com
</div>"""


# ------------------------------------------------------------
# Party contact helpers
# ------------------------------------------------------------
def doc_wa_number(phone: str) -> str:
    d = re.sub(r"\D+", "", phone or "")
    if len(d) == 10:
        return "91" + d
    if len(d) == 11 and d[0] == "0":
        return "91" + d[1:]
    return d


def doc_meta(doc_type: str, record_id: int) -> Optional[dict]:
    company_id = ErpAuth.company_id()
    if doc_type == "so":
        d = doc_load_so(record_id, company_id)
        if not d:
            return None
        r = d["so"]
        return {"number": r["so_number"], "label": "Sales Order", "company": r["company_name"],
                "party": r.get("cust_name") or "", "email": r.get("cust_email") or "",
                "phone": r.get("cust_phone") or "", "total": float(r["grand_total"]),
                "comp_email": r.get("comp_email") or "", "company_id": int(r["company_id"])}
    d = doc_load_po(record_id, company_id)
    if not d:
        return None
    r = d["po"]
    return {"number": r["po_number"], "label": "Purchase Order", "company": r["company_name"],
            "party": r["supplier_name"], "email": r.get("supplier_email") or "",
            "phone": r.get("supplier_phone") or "", "total": float(r["grand_total"]),
            "comp_email": r.get("comp_email") or "", "company_id": int(r["company_id"])}


# ------------------------------------------------------------
# Action links (data for a Qt dialog or web template to build buttons from)
# ------------------------------------------------------------
def doc_action_links(doc_type: str, record_id: int) -> Optional[dict]:
    m = doc_meta(doc_type, record_id)
    if not m:
        return None
    view = doc_url(doc_type, record_id)
    print_url = doc_url(doc_type, record_id, {"print": 1})
    pdf_url = doc_url(doc_type, record_id, {"pdf": 1})
    text = f"{m['label']} {m['number']} from {m['company']} (Rs. {_money(m['total'])}): {view}"
    wa_url = f"https://wa.me/{doc_wa_number(str(m['phone']))}?text={urlencode({'': text})[1:]}"
    return {
        "view_url": view, "print_url": print_url, "pdf_url": pdf_url, "whatsapp_url": wa_url,
        "share_text": text, "title": f"{m['label']} {m['number']}",
        "email_to_prefill": m["email"], "party": m["party"],
    }


# ------------------------------------------------------------
# Email the document link
# (replaces the wp_ajax_msme_erp_doc_mail handler — call this directly
# from a FastAPI route; no nonce/admin-ajax plumbing needed)
# ------------------------------------------------------------
def send_document_mail(doc_type: str, record_id: int, to_raw: str, message: str = "") -> tuple[bool, str]:
    to = [addr for addr in re.split(r"[,;\s]+", to_raw.strip()) if addr and "@" in addr]
    if not to:
        return False, "Enter a valid email address."

    m = doc_meta(doc_type, record_id)
    if not m:
        return False, "Document not found for your company."

    view = doc_url(doc_type, record_id)
    pdf = doc_url(doc_type, record_id, {"pdf": 1})
    e = escape
    intro = (f"<p>{e(message)}</p>" if message.strip()
              else f'<p>Please find our {e(m["label"])} <strong>{e(m["number"])}</strong> at the link below.</p>')
    body = (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222;max-width:560px">'
        f"<p>Dear {e(m['party'] or 'Sir/Madam')},</p>{intro}"
        f'<p style="margin:18px 0"><a href="{e(view)}" style="background:#1560bd;color:#fff;padding:10px 18px;'
        f'border-radius:5px;text-decoration:none;font-weight:600">View {e(m["label"])}</a> &nbsp; '
        f'<a href="{e(pdf)}" style="background:#198754;color:#fff;padding:10px 18px;border-radius:5px;'
        f'text-decoration:none;font-weight:600">Download PDF</a></p>'
        f'<p style="color:#666;font-size:12px">Amount: Rs. {_money(m["total"])}</p>'
        f"<p>Regards,<br><strong>{e(m['company'])}</strong></p></div>"
    )
    subject = f"{m['label']} {m['number']} — {m['company']}"
    ok, info = send_email(", ".join(to), subject, body)
    if not ok:
        return False, f"Mail server did not accept the message: {info}"

    if doc_type == "po":
        try:
            ErpDB.query("UPDATE purchase_orders SET supplier_notified_at=? WHERE id=? AND company_id=?",
                        [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), record_id, ErpAuth.company_id()])
        except Exception:
            logger.exception("Could not stamp supplier_notified_at")

    return True, f"Sent to {', '.join(to)}."


# ------------------------------------------------------------
# Public document page (served at e.g. GET /documents?msme_doc=<token>&t=po|so)
# Self-contained HTML: print button, html2pdf.js download, WhatsApp share.
# No WordPress involved — this is a route a FastAPI endpoint returns as-is.
# ------------------------------------------------------------
def build_public_doc_page(token: str, doc_type: str, print_flag: bool = False, pdf_flag: bool = False) -> tuple[int, str]:
    """Returns (http_status, html). 404 status carries a small error page."""
    def fail(msg: str) -> tuple[int, str]:
        return 404, (f'<!DOCTYPE html><meta charset="utf-8">'
                      f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                      f'<body style="font-family:Segoe UI,Arial;padding:40px;text-align:center;color:#555">'
                      f"<h3>{escape(msg)}</h3></body>")

    if len(token) < 20:
        return fail("Invalid document link.")

    doc_type = "so" if doc_type == "so" else "po"
    doc_ensure_col(doc_type)
    tbl = doc_table(doc_type)
    row = ErpDB.fetch_one(f"SELECT id, company_id FROM {tbl} WHERE doc_token=?", [token])
    if not row:
        return fail("This document link is invalid or no longer available.")

    ErpDB.use_company(int(row["company_id"]))
    if doc_type == "so":
        d = doc_load_so(int(row["id"]))
        num = (d["so"]["so_number"] if d else "") or ""
        label, co = "Sales Order", (d["so"]["company_name"] if d else "")
        inner = render_so_html(d["so"], d["lines"], d["groups"], d["bank"]) if d else ""
    else:
        d = doc_load_po(int(row["id"]))
        num = (d["po"]["po_number"] if d else "") or ""
        label, co = "Purchase Order", (d["po"]["company_name"] if d else "")
        inner = render_po_html(d["po"], d["lines"], d["groups"], d["bank"]) if d else ""

    if not d:
        return fail("Document not found.")

    fname = re.sub(r"[^A-Za-z0-9_\-]+", "_", f"{label}_{num}") + ".pdf"
    auto = "print" if print_flag else ("pdf" if pdf_flag else "")
    clean_url = f"{app_base_url().rstrip('/')}/documents?{urlencode({'msme_doc': token, 't': doc_type})}"
    wa_share = f"https://wa.me/?text={urlencode({'': f'{label} {num} from {co}: {clean_url}'})[1:]}"
    e = escape

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{e(label)} {e(num)} — {e(co)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<style>
  body{{background:#eef1f5;font-family:'Segoe UI',system-ui,-apple-system,sans-serif}}
  .doc-bar{{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #dee2e6;padding:8px 12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center}}
  .doc-sheet{{max-width:860px;margin:18px auto;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.1);border-radius:6px;padding:28px}}
  @page{{size:A4;margin:10mm}}
  @media print{{body{{background:#fff}}.doc-bar{{display:none!important}}.doc-sheet{{box-shadow:none;margin:0;max-width:none;padding:0}}
    *{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
</style></head><body>
<div class="doc-bar">
  <strong class="me-2">{e(label)} {e(num)}</strong>
  <button class="btn btn-sm btn-primary" onclick="window.print()"><i class="bi bi-printer"></i> Print</button>
  <button class="btn btn-sm btn-success" id="dlPdf"><i class="bi bi-file-earmark-pdf"></i> Download PDF</button>
  <button class="btn btn-sm btn-outline-secondary" id="shPdf"><i class="bi bi-share"></i> Share</button>
  <a class="btn btn-sm btn-outline-success" target="_blank" rel="noopener" href="{wa_share}"><i class="bi bi-whatsapp"></i> WhatsApp</a>
</div>
<div class="doc-sheet" id="doc-area">{inner}</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
<script>
(function(){{
  var FN = {_js_str(fname)}, TITLE = {_js_str(label + ' ' + num)}, URL_ = {_js_str(clean_url)}, AUTO = {_js_str(auto)};
  function job(){{
    return html2pdf().set({{margin:[8,8,8,8],filename:FN,image:{{type:'jpeg',quality:.98}},
      html2canvas:{{scale:2,useCORS:true,backgroundColor:'#ffffff'}},jsPDF:{{unit:'mm',format:'a4',orientation:'portrait'}},
      pagebreak:{{mode:['avoid-all','css','legacy']}}}}).from(document.getElementById('doc-area'));
  }}
  function dl(){{ if (typeof html2pdf==='undefined'){{ window.print(); return; }} job().save(); }}
  document.getElementById('dlPdf').onclick = dl;
  document.getElementById('shPdf').onclick = function(){{
    var fallback = function(){{
      if (navigator.share) navigator.share({{title:TITLE,url:URL_}}).catch(function(){{}});
      else if (navigator.clipboard) navigator.clipboard.writeText(URL_).then(function(){{ alert('Link copied.'); }});
      else window.prompt('Copy this link:', URL_);
    }};
    if (typeof html2pdf==='undefined' || !navigator.canShare) return fallback();
    job().outputPdf('blob').then(function(b){{
      var f = new File([b], FN, {{type:'application/pdf'}});
      if (navigator.canShare({{files:[f]}})) navigator.share({{files:[f],title:TITLE}}).catch(function(){{}});
      else fallback();
    }}).catch(fallback);
  }};
  window.addEventListener('load', function(){{
    if (AUTO==='print') setTimeout(function(){{ window.print(); }}, 400);
    if (AUTO==='pdf')   setTimeout(dl, 600);
  }});
}})();
</script>
</body></html>"""
    return 200, html


def _js_str(s: str) -> str:
    import json
    return json.dumps(s)