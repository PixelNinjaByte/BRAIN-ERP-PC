"""
backend/routers/supplier_invoice.py
-----------------------------------
Converted from modules/mm/supplier_invoice.php - a dispatcher file that served three T-codes:

  MR01  Supplier Invoice Verification (MIRO)   /api/mr01/...
  LT01  Stock Transfer Order                   /api/lt01/...
  VK11  Price List / Condition Records         /api/vk11/...

(The PHP file's generic "T-code registered and authorized" fallback page is not needed in the API.)

Each T-code is guarded by its own require_tcode().

MR01 notes
  * Scan / upload / Gemini extraction (core/invoice_scan.php, AJAX) is a separate PHP file and is NOT
    in this router. POST /api/mr01/post accepts the `attachment_file` token it will produce
    ("<company_id>/<yyyy>/<32 hex>.<jpg|png|webp|pdf>") and validates it against ERP_INVOICE_DIR
    (env var, default ./data/invoices) exactly like msme_erp_invoice_valid_file().
  * The invoice row and its GL journal are one unit: a closed period or missing GL mapping rolls back
    the whole posting (PHP behaviour) and is returned as 409.
LT01 notes
  * Inbound cost uses the SOURCE warehouse's average cost. (The PHP used the destination's existing
    average, which is 0 for a new warehouse - it zeroed the value of transferred stock.)
VK11 notes
  * Name is now required when creating a list; an existing price_list_id must belong to the company.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.core import finance_engine as fe
from backend.core import stock
from backend.core.auth import ErpAuth
from backend.core.db import ErpDB
from backend.core.util import (claim_request_id, currency_symbol, get_option, opt_bool,release_request_id)
from backend.routers.deps import fail, ok, require_tcode

log = logging.getLogger("erp.supplier_invoice")

router = APIRouter(prefix="/api", tags=["MM-Invoice / LT01 / VK11"])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INVOICE_FILE_RE = re.compile(r"^(\d{1,6})/\d{4}/[a-f0-9]{32}\.(jpg|png|webp|pdf)$")


class BizError(Exception):
    """A validation / business-rule failure -> HTTP 400 with the message."""


def _ctx() -> dict:
    u = ErpAuth.user() or {}
    return {"company_id": int(u.get("company_id") or 0), "user_id": int(u.get("id") or 0),
            "currency": u.get("currency") or "INR"}


def _valid_date(s: Optional[str]) -> bool:
    if not s or not DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def invoice_valid_file(rel: Optional[str], company_id: int) -> str:
    """msme_erp_invoice_valid_file(): our own naming pattern, this company only, and the file must exist."""
    rel = str(rel or "")
    m = INVOICE_FILE_RE.match(rel)
    if not m or int(m.group(1)) != company_id:
        return ""
    base = os.environ.get("ERP_INVOICE_DIR", os.path.join("data", "invoices"))
    return rel if os.path.isfile(os.path.join(base, rel)) else ""


def invoice_find_duplicate(company_id: int, supplier_id: int, ref_no: str, exclude_id: int = 0) -> Optional[dict]:
    key = re.sub(r"\s+", "", ref_no).upper()
    if not key:
        return None
    return ErpDB.fetch_one(
        "SELECT id, invoice_number, invoice_date FROM supplier_invoices "
        "WHERE company_id=? AND supplier_id=? AND status<>'CANCELLED' AND id<>? "
        "AND UPPER(REPLACE(ref_invoice_num,' ',''))=? LIMIT 1",
        [company_id, supplier_id, exclude_id, key])


def invoice_grn_check(company_id: int, supplier_id: int, po_id: int) -> str:
    """No payment liability for goods still on QC hold (or never received against the chosen PO)."""
    if not opt_bool("msme_erp_invoice_grn_check", 1):
        return ""
    if po_id > 0:
        pend = ErpDB.fetch_all(
            "SELECT grn_number FROM goods_receipts WHERE company_id=? AND po_id=? AND status='PENDING_QC'",
            [company_id, po_id])
        if pend:
            return ("Quality approval pending: GRN " + ", ".join(p["grn_number"] for p in pend) +
                    " for this PO is still on QC hold. Complete the inspection (QA32) before booking the invoice.")
        posted = float(ErpDB.fetch_value(
            "SELECT COALESCE(SUM(l.qty_accepted),0) FROM goods_receipts gr JOIN goods_receipt_lines l ON l.grn_id=gr.id "
            "WHERE gr.company_id=? AND gr.po_id=? AND gr.status='POSTED' AND gr.movement_type='101'",
            [company_id, po_id]) or 0)
        if posted <= 0:
            return ("No accepted goods receipt (GRN) exists for this purchase order. "
                    "Receive the goods in MIGO and get QC approval first.")
    elif supplier_id > 0:
        pend = ErpDB.fetch_all(
            "SELECT grn_number FROM goods_receipts WHERE company_id=? AND supplier_id=? AND status='PENDING_QC'",
            [company_id, supplier_id])
        if pend:
            return ("This supplier has goods still on QC hold (GRN " + ", ".join(p["grn_number"] for p in pend) +
                    "). Link the invoice to a purchase order, or finish the inspection first.")
    return ""


# ===========================================================================
# MR01 - Supplier Invoice Verification (MIRO)
# ===========================================================================
@router.get("/mr01/meta", dependencies=[Depends(require_tcode("MR01"))])
def mr01_meta():
    c = _ctx()
    cid = c["company_id"]
    history = ErpDB.fetch_all(
        "SELECT si.*, s.name AS supp FROM supplier_invoices si JOIN suppliers s ON s.id=si.supplier_id "
        "WHERE si.company_id=? ORDER BY si.id DESC LIMIT 20", [cid])
    for h in history:
        h["has_attachment"] = bool(invoice_valid_file(h.get("attachment_file"), cid))
    return ok({
        "currency": c["currency"],
        "currency_symbol": currency_symbol(c["currency"]),
        "suppliers": ErpDB.fetch_all(
            "SELECT id, supplier_code, name, credit_days FROM suppliers WHERE company_id=? ORDER BY name", [cid]),
        "open_pos": ErpDB.fetch_all(
            "SELECT id, po_number, grand_total FROM purchase_orders WHERE company_id=? "
            "AND status IN('APPROVED','PARTIAL','CLOSED') ORDER BY id DESC LIMIT 50", [cid]),
        "tds_sections": ErpDB.fetch_all(
            "SELECT section, description, rate_pct FROM tds_sections WHERE company_id=? AND is_active=1 "
            "ORDER BY section", [cid]),
        "recent_invoices": history,
        "has_ai_key": str(get_option("msme_erp_gemini_api_key", "") or "").strip() != "",
    })


class MiroPostIn(BaseModel):
    supplier_id: int = 0
    po_id: int = 0
    ref_invoice_num: str = ""
    invoice_date: str = ""
    base_amount: float = 0
    tax_amount: float = 0
    tds_amount: float = 0
    credit_days: int = 30
    tds_section: str = ""
    reverse_charge: bool = False
    itc_ineligible: bool = False
    attachment_file: str = ""
    ai_extracted: bool = False
    request_id: Optional[str] = None


@router.post("/mr01/post", dependencies=[Depends(require_tcode("MR01"))])
def mr01_post(d: MiroPostIn):
    c = _ctx()
    cid, uid = c["company_id"], c["user_id"]
    if not claim_request_id(d.request_id):
        return fail("Duplicate submission ignored - this invoice was already posted.", 409)
    in_tx = False
    try:
        supplier_id = int(d.supplier_id or 0)
        ref_no = (d.ref_invoice_num or "").strip()
        inv_date = d.invoice_date or ""
        base, tax, tds = round(d.base_amount, 2), round(d.tax_amount, 2), round(d.tds_amount, 2)
        total = round(base + tax, 2)
        net = round(total - tds, 2)

        if not supplier_id:
            raise BizError("Select a supplier.")
        if ref_no == "":
            raise BizError("Enter the supplier's invoice number.")
        if not _valid_date(inv_date):
            raise BizError("Enter a valid invoice date.")
        if base < 0 or tax < 0 or tds < 0:
            raise BizError("Amounts cannot be negative.")
        if total <= 0:
            raise BizError("Invoice amount must be greater than zero.")
        if tds > total:
            raise BizError("TDS cannot exceed the invoice total.")

        # GST split, reverse charge / ITC flags, TDS section
        rcm = 1 if d.reverse_charge else 0
        itc_ok = 0 if d.itc_ineligible else 1
        tds_sec = (d.tds_section or "").strip()
        if tds > 0 and tds_sec == "":
            raise BizError("Select the TDS section when TDS is deducted.")
        if rcm:      # tax goes to the government: the supplier is owed the base only
            total = base
            net = round(base - tds, 2)
            if net < 0:
                raise BizError("TDS cannot exceed the invoice base on a reverse-charge invoice.")
        sup = ErpDB.fetch_one("SELECT gstin FROM suppliers WHERE id=? AND company_id=?", [supplier_id, cid])
        if not sup:
            raise BizError("Supplier not found.")
        po_id = int(d.po_id or 0)
        if po_id and not ErpDB.fetch_value("SELECT id FROM purchase_orders WHERE id=? AND company_id=?", [po_id, cid]):
            raise BizError("Purchase order not found.")

        gst = fe.gst_split(tax, sup.get("gstin"), cid)
        # three-way match: PO price x goods received vs what is being billed
        match_expected, _var, match_blocked, match_msg = fe.three_way_match(po_id, supplier_id, base)

        dup = invoice_find_duplicate(cid, supplier_id, ref_no)
        if dup:
            raise BizError(f"Supplier invoice {ref_no} is already posted for this supplier as "
                           f"{dup['invoice_number']} (dated {dup['invoice_date']}). Duplicate not allowed.")

        grn_err = invoice_grn_check(cid, supplier_id, po_id)
        if grn_err:
            raise BizError(grn_err)

        attach = invoice_valid_file(d.attachment_file, cid)
        ai_read = 1 if (attach and d.ai_extracted) else 0

        ErpDB.begin_transaction()
        in_tx = True
        inv_num = ErpDB.get_next_number(cid, "SINV", date.today().year)
        due = (datetime.strptime(inv_date, "%Y-%m-%d") + timedelta(days=max(0, int(d.credit_days or 0)))
               ).strftime("%Y-%m-%d")

        si_id = ErpDB.insert("supplier_invoices", {
            "company_id": cid, "invoice_number": inv_num, "invoice_date": inv_date,
            "supplier_id": supplier_id, "po_id": po_id or None, "ref_invoice_num": ref_no,
            "base_amount": base, "tax_amount": tax, "tds_amount": tds, "total_amount": total,
            "paid_amount": 0, "due_amount": net, "due_date": due,
            "status": "BLOCKED" if match_blocked else "PENDING",
            "block_reason": match_msg if match_blocked else None,
            "match_expected": match_expected,
            "cgst_amount": gst["cgst"], "sgst_amount": gst["sgst"], "igst_amount": gst["igst"],
            "itc_eligible": itc_ok, "reverse_charge": rcm, "tds_section": tds_sec or None,
            "attachment_file": attach or None, "ai_extracted": ai_read, "created_by": uid,
        })
        ErpAuth.audit("supplier_invoices", int(si_id), "INSERT", {},
                      {"invoice_number": inv_num, "ref": ref_no, "scan": "yes" if attach else "no"})
        # the invoice and its ledger entry are one unit
        fe.fin_post_supplier_invoice(int(si_id))
        ErpDB.commit()
        in_tx = False
    except BizError as e:
        release_request_id(d.request_id)
        if in_tx:
            ErpDB.rollback()
        return fail(str(e), 400)
    except fe.FinanceError as e:
        release_request_id(d.request_id)
        ErpDB.rollback()
        return fail(str(e), 409)
    except Exception as e:  # noqa: BLE001
        release_request_id(d.request_id)
        ErpDB.rollback()
        log.exception("MR01 post failed")
        return fail(str(e), 500)

    msg = (f"Supplier Invoice {inv_num} created. Due: {currency_symbol(c['currency'])}{net:,.2f} by {due}"
           + (f" — BLOCKED for payment: {match_msg} Release it in MRBR once checked." if match_blocked else ""))
    return ok({"invoice_id": int(si_id), "invoice_number": inv_num, "due_amount": net, "due_date": due,
               "status": "BLOCKED" if match_blocked else "PENDING",
               "block_reason": match_msg if match_blocked else None}, msg)


# ===========================================================================
# LT01 - Stock Transfer Order
# ===========================================================================
@router.get("/lt01/meta", dependencies=[Depends(require_tcode("LT01"))])
def lt01_meta():
    cid = _ctx()["company_id"]
    return ok({
        "warehouses": ErpDB.fetch_all(
            "SELECT id, code, name FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY code", [cid]),
        "items": ErpDB.fetch_all(
            "SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, "
            "COALESCE((SELECT SUM(sb.qty_on_hand) FROM stock_balances sb WHERE sb.item_id=i.id),0) AS qty_on_hand "
            "FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id "
            "WHERE i.company_id=? AND i.is_active=1 ORDER BY i.item_code", [cid]),
        "history": ErpDB.fetch_all(
            "SELECT st.*, fw.name AS from_wh, tw.name AS to_wh FROM stock_transfers st "
            "JOIN warehouses fw ON fw.id=st.from_wh_id JOIN warehouses tw ON tw.id=st.to_wh_id "
            "WHERE st.company_id=? ORDER BY st.id DESC LIMIT 20", [cid]),
    })


@router.get("/lt01/stock", dependencies=[Depends(require_tcode("LT01"))])
def lt01_stock(warehouse_id: int = Query(...)):
    """Quantity on hand per item in one warehouse (so the UI can show what is transferable)."""
    cid = _ctx()["company_id"]
    if not ErpDB.fetch_value("SELECT id FROM warehouses WHERE id=? AND company_id=?", [warehouse_id, cid]):
        return fail("Warehouse not found.", 404)
    return ok(ErpDB.fetch_all(
        "SELECT sb.item_id, sb.qty_on_hand, sb.avg_cost FROM stock_balances sb "
        "JOIN items i ON i.id=sb.item_id WHERE sb.warehouse_id=? AND i.company_id=? AND sb.qty_on_hand>0",
        [warehouse_id, cid]))


class TransferLineIn(BaseModel):
    item_id: Optional[int] = None
    uom_id: Optional[int] = None
    qty: float = 0


class TransferIn(BaseModel):
    from_wh_id: int = 0
    to_wh_id: int = 0
    lines: list[TransferLineIn] = Field(default_factory=list)
    request_id: Optional[str] = None


@router.post("/lt01/post", dependencies=[Depends(require_tcode("LT01"))])
def lt01_post(d: TransferIn):
    c = _ctx()
    cid, uid = c["company_id"], c["user_id"]
    if not d.from_wh_id or not d.to_wh_id:
        return fail("Select both warehouses.", 400)
    if d.from_wh_id == d.to_wh_id:
        return fail("Source and destination warehouse must be different.", 400)
    for w in (d.from_wh_id, d.to_wh_id):
        if not ErpDB.fetch_value("SELECT id FROM warehouses WHERE id=? AND company_id=?", [w, cid]):
            return fail("Warehouse not found in the active company.", 400)
    lines = [l for l in d.lines if l.item_id and float(l.qty or 0) > 0]
    if not lines:
        return fail("Enter at least one item with a quantity.", 400)
    if not claim_request_id(d.request_id):
        return fail("Duplicate submission ignored - this transfer was already posted.", 409)

    try:
        ErpDB.begin_transaction()
        sto_num = ErpDB.get_next_number(cid, "STO", date.today().year)
        sto_id = ErpDB.insert("stock_transfers", {
            "company_id": cid, "sto_number": sto_num, "transfer_date": date.today().isoformat(),
            "from_wh_id": d.from_wh_id, "to_wh_id": d.to_wh_id, "status": "DRAFT", "created_by": uid})
        for ln in lines:
            item_id, qty = int(ln.item_id), float(ln.qty)
            if not ErpDB.fetch_value("SELECT id FROM items WHERE id=? AND company_id=?", [item_id, cid]):
                raise BizError("A selected item does not belong to the active company.")
            uom_id = int(ln.uom_id) if ln.uom_id else int(
                ErpDB.fetch_value("SELECT base_uom_id FROM items WHERE id=?", [item_id]) or 0)
            ErpDB.insert("stock_transfer_lines", {"sto_id": sto_id, "item_id": item_id,
                                                  "uom_id": uom_id, "qty_transfer": qty})
            avg = float(ErpDB.fetch_value(
                "SELECT avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
                [item_id, d.from_wh_id]) or 0)
            # outward from source (raises on insufficient stock), then inward to destination at source cost
            stock.post_stock_movement(cid, item_id, d.from_wh_id, "TRANSFER", "311", 0.0, qty, 0.0,
                                      "stock_transfers", int(sto_id), sto_num, uid)
            stock.post_stock_movement(cid, item_id, d.to_wh_id, "TRANSFER", "311", qty, 0.0, avg,
                                      "stock_transfers", int(sto_id), sto_num, uid)
        ErpDB.update("stock_transfers", {"status": "TRANSFERRED",
                                         "transferred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                     "id=?", [sto_id])
        ErpAuth.audit("stock_transfers", int(sto_id), "INSERT", {}, {"sto_number": sto_num})
        ErpDB.commit()
    except BizError as e:
        ErpDB.rollback()
        release_request_id(d.request_id)
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        ErpDB.rollback()
        release_request_id(d.request_id)
        log.exception("LT01 failed")
        return fail(str(e), 400 if "Insufficient stock" in str(e) else 500)
    return ok({"sto_id": int(sto_id), "sto_number": sto_num, "status": "TRANSFERRED"},
              f"Stock Transfer {sto_num} completed.")


# ===========================================================================
# VK11 - Price List / Condition Records
# ===========================================================================
@router.get("/vk11/meta", dependencies=[Depends(require_tcode("VK11"))])
def vk11_meta():
    cid = _ctx()["company_id"]
    return ok({
        "price_lists": ErpDB.fetch_all(
            "SELECT pl.*, COUNT(pli.id) AS item_count FROM price_lists pl "
            "LEFT JOIN price_list_items pli ON pli.price_list_id=pl.id WHERE pl.company_id=? "
            "GROUP BY pl.id ORDER BY pl.id DESC", [cid]),
        "items": ErpDB.fetch_all(
            "SELECT id, item_code, item_name, moving_avg_price FROM items WHERE company_id=? AND is_active=1 "
            "ORDER BY item_code", [cid]),
        "default_valid_from": date.today().strftime("%Y-01-01"),
        "default_valid_to": date.today().strftime("%Y-12-31"),
    })


class PriceItemIn(BaseModel):
    item_id: Optional[int] = None
    unit_price: float = 0
    discount_pct: float = 0


class PriceListIn(BaseModel):
    price_list_id: Optional[int] = None      # omit to create a new list
    name: str = ""
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    items: list[PriceItemIn] = Field(default_factory=list)


@router.post("/vk11/save", dependencies=[Depends(require_tcode("VK11"))])
def vk11_save(d: PriceListIn):
    cid = _ctx()["company_id"]
    vf, vt = d.valid_from or None, d.valid_to or None
    for x in (vf, vt):
        if x and not _valid_date(x):
            return fail("Enter valid dates (YYYY-MM-DD).", 400)
    if vf and vt and vt < vf:
        return fail("'Valid To' cannot be before 'Valid From'.", 400)
    rows = [i for i in d.items if i.item_id]
    for i in rows:
        if i.unit_price < 0 or not (0 <= i.discount_pct <= 100):
            return fail("Prices cannot be negative and discount must be between 0 and 100%.", 400)
        if not ErpDB.fetch_value("SELECT id FROM items WHERE id=? AND company_id=?", [int(i.item_id), cid]):
            return fail("A selected item does not belong to the active company.", 400)
    try:
        ErpDB.begin_transaction()
        if not d.price_list_id:
            if not (d.name or "").strip():
                raise BizError("Price list name is required.")
            currency = ErpDB.fetch_value("SELECT currency_code FROM companies WHERE id=?", [cid]) or "INR"
            pl_id = ErpDB.insert("price_lists", {
                "company_id": cid, "name": d.name.strip(), "currency": currency,
                "valid_from": vf, "valid_to": vt, "is_active": 1})
        else:
            pl_id = int(d.price_list_id)
            if not ErpDB.fetch_value("SELECT id FROM price_lists WHERE id=? AND company_id=?", [pl_id, cid]):
                raise BizError("Price list not found.")
        for i in rows:
            ErpDB.insert("price_list_items", {
                "price_list_id": pl_id, "item_id": int(i.item_id), "unit_price": i.unit_price,
                "discount_pct": i.discount_pct, "valid_from": vf, "valid_to": vt})
        ErpDB.commit()
    except BizError as e:
        ErpDB.rollback()
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        ErpDB.rollback()
        log.exception("VK11 failed")
        return fail(str(e), 500)
    return ok({"price_list_id": int(pl_id), "items_saved": len(rows)}, "Price list saved.")