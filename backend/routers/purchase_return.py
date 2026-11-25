"""
backend/routers/purchase_return.py
-----------------------------------
Brain ERP — /api/purchase-return
Purchase Return (PRTN) — goods back to the supplier against a posted GRN.

Converted from modules/mm/purchase_return.php.
Business logic already ported to core/returns.py (purchase_return_create,
pr_returned_qty, RETURN_REASONS) — this router only adds the HTTP surface.

Endpoints
---------
GET  /reasons                    — return-reason code list (dropdown)
GET  /open-grns                  — posted GRNs eligible to be returned against
GET  /grn/{grn_id}               — GRN header + returnable lines + candidate
                                    supplier invoices (drives the "New Return" form)
GET  /                           — paginated purchase-return list (pg, page_size)
GET  /{return_id}                — single return, header + lines + debit-note info
POST /                            — create & post a purchase return (T-code PRTN)

PHP -> Python notes
  * The PHP screen was one page (list / new / view + POST). Split into the
    JSON endpoints above; HTML/flash/redirect handling belongs to the UI.
  * CSRF is dropped (per project rule).
  * `qty[grn_line_id]` (PHP array-indexed form field) becomes a JSON object
    `{"<grn_line_id>": <qty>}` in the POST body.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from core.returns import RETURN_REASONS, purchase_return_create
from .deps import require_login, require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/purchase-return", tags=["PRTN - Purchase Return"])

TCODE = "PRTN"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class PurchaseReturnCreate(BaseModel):
    grn_id: int
    return_date: Optional[str] = None          # YYYY-MM-DD; defaults to today in core
    reason_code: str = "OTHER"
    reason: str = Field(..., min_length=1, max_length=255)
    qty: Dict[int, float] = Field(default_factory=dict)   # {grn_line_id: qty}
    debit_note: str = "0"                      # '0' none | '-1' open note | invoice id


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
@router.get("/reasons", dependencies=[Depends(require_login)])
def get_reasons():
    return ok(RETURN_REASONS)


@router.get("/open-grns", dependencies=[Depends(require_tcode(TCODE))])
def open_grns():
    """Posted GRNs that can be returned against (excludes return-type 122 docs themselves)."""
    company_id = ErpAuth.company_id()
    rows = ErpDB.fetch_all(
        "SELECT g.id, g.grn_number, g.grn_date, s.name AS supplier, po.po_number "
        "FROM goods_receipts g "
        "JOIN suppliers s ON s.id=g.supplier_id "
        "LEFT JOIN purchase_orders po ON po.id=g.po_id "
        "WHERE g.company_id=? AND g.status='POSTED' AND COALESCE(g.movement_type,'101')<>'122' "
        "ORDER BY g.id DESC LIMIT 400",
        (company_id,),
    )
    return ok(rows)


@router.get("/grn/{grn_id}", dependencies=[Depends(require_tcode(TCODE))])
def grn_for_return(grn_id: int):
    """GRN header + returnable lines (accepted - already returned, with current stock-on-hand)
    + candidate supplier invoices, for the 'New Purchase Return' form."""
    company_id = ErpAuth.company_id()

    grn = ErpDB.fetch_one(
        "SELECT g.*, s.name AS supplier, w.name AS wh, po.po_number "
        "FROM goods_receipts g "
        "JOIN suppliers s ON s.id=g.supplier_id "
        "JOIN warehouses w ON w.id=g.warehouse_id "
        "LEFT JOIN purchase_orders po ON po.id=g.po_id "
        "WHERE g.id=? AND g.company_id=?",
        (grn_id, company_id),
    )
    if not grn:
        return fail("That goods receipt was not found.", 404)

    lines = ErpDB.fetch_all(
        "SELECT gl.*, i.item_code, i.item_name, u.code AS uom, "
        "COALESCE((SELECT SUM(prl.qty) FROM purchase_return_lines prl "
        "          JOIN purchase_returns pr ON pr.id=prl.return_id "
        "          WHERE prl.grn_line_id=gl.id AND pr.status='POSTED'),0) AS returned, "
        "COALESCE((SELECT qty_on_hand FROM stock_balances sb "
        "          WHERE sb.item_id=gl.item_id AND sb.warehouse_id=?),0) AS on_hand "
        "FROM goods_receipt_lines gl "
        "JOIN items i ON i.id=gl.item_id "
        "LEFT JOIN units_of_measure u ON u.id=gl.uom_id "
        "WHERE gl.grn_id=? AND gl.qty_accepted>0",
        (int(grn["warehouse_id"]), grn_id),
    )
    for l in lines:
        l["open_qty"] = round(float(l["qty_accepted"] or 0) - float(l["returned"] or 0), 3)

    inv_sql = (
        "SELECT id, invoice_number, due_amount, status, invoice_date "
        "FROM supplier_invoices WHERE company_id=? AND supplier_id=? AND status<>'CANCELLED'"
    )
    inv_params = [company_id, int(grn["supplier_id"])]
    if grn.get("po_id"):
        inv_sql += " AND po_id=?"
        inv_params.append(int(grn["po_id"]))
    inv_sql += " ORDER BY id DESC LIMIT 30"
    invoices = ErpDB.fetch_all(inv_sql, tuple(inv_params))

    return ok({"grn": grn, "lines": lines, "invoices": invoices, "reasons": RETURN_REASONS})


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------
@router.get("/", dependencies=[Depends(require_tcode(TCODE))])
def list_returns(pg: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)):
    company_id = ErpAuth.company_id()
    sql = (
        "SELECT pr.id, pr.return_number, pr.return_date, pr.total_amount, pr.status, pr.reason_code, "
        "s.name AS supplier, g.grn_number, n.note_number "
        "FROM purchase_returns pr "
        "JOIN suppliers s ON s.id=pr.supplier_id "
        "JOIN goods_receipts g ON g.id=pr.grn_id "
        "LEFT JOIN fin_notes n ON n.id=pr.debit_note_id "
        "WHERE pr.company_id=? ORDER BY pr.id DESC"
    )
    result = ErpDB.paginate(sql, (company_id,), pg, page_size)
    return ok(result)


@router.get("/{return_id}", dependencies=[Depends(require_tcode(TCODE))])
def get_return(return_id: int):
    company_id = ErpAuth.company_id()
    r = ErpDB.fetch_one(
        "SELECT pr.*, s.name AS supplier, s.supplier_code, g.grn_number, w.name AS wh, "
        "po.po_number, gs.grn_number AS stock_doc, "
        "n.note_number, n.total_amount AS note_total, n.ref_inv_number "
        "FROM purchase_returns pr "
        "JOIN suppliers s ON s.id=pr.supplier_id "
        "JOIN goods_receipts g ON g.id=pr.grn_id "
        "JOIN warehouses w ON w.id=pr.warehouse_id "
        "LEFT JOIN purchase_orders po ON po.id=pr.po_id "
        "LEFT JOIN goods_receipts gs ON gs.id=pr.stock_doc_id "
        "LEFT JOIN fin_notes n ON n.id=pr.debit_note_id "
        "WHERE pr.id=? AND pr.company_id=?",
        (return_id, company_id),
    )
    if not r:
        return fail("Return not found.", 404)

    lines = ErpDB.fetch_all(
        "SELECT l.*, i.item_code, i.item_name, u.code AS uom "
        "FROM purchase_return_lines l "
        "JOIN items i ON i.id=l.item_id "
        "LEFT JOIN units_of_measure u ON u.id=l.uom_id "
        "WHERE l.return_id=?",
        (return_id,),
    )
    return ok({"return": r, "lines": lines, "reason_label": RETURN_REASONS.get(r["reason_code"], r["reason_code"])})


# ---------------------------------------------------------------------------
# Create (post)
# ---------------------------------------------------------------------------
@router.post("/", dependencies=[Depends(require_tcode(TCODE))])
def create_return(body: PurchaseReturnCreate):
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    # core expects qty keyed by grn_line_id -> qty (dict with int-ish keys is fine)
    try:
        new_id = purchase_return_create(payload)
    except ValueError as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.exception("Purchase return post failed")
        return fail(f"Error posting purchase return: {e}", 500)

    num = ErpDB.fetch_value("SELECT return_number FROM purchase_returns WHERE id=?", (new_id,))
    return ok(
        {"return_id": new_id, "return_number": num},
        f"Purchase return {num} posted - stock reduced and the PO quantity re-opened.",
    )   