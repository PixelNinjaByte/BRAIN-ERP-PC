"""
Brain ERP — /api/rfq
Request for Quotation module (ME41 / ME47).
Port of mm/rfq_create.php → Python / FastAPI.

Endpoints
---------
GET  /lookups              — suppliers with email, pending approved PRs
GET  /                     — paginated RFQ list
POST /                     — ME41 create RFQ (from PR or manual)
GET  /{rfq_id}             — RFQ detail + lines + suppliers invited
POST /{rfq_id}/bid         — ME47 record supplier bid / quotation
POST /{rfq_id}/compare     — compare all bids, mark L1/L2/L3
POST /{rfq_id}/close       — close RFQ (no more bids accepted)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from ..deps import require_login, require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RFQLine(BaseModel):
    item_id: int
    uom_id: int
    qty_required: float = Field(gt=0)
    pr_line_id: Optional[int] = None
    remarks: str = ""


class RFQCreateRequest(BaseModel):
    rfq_date: Optional[str] = None
    due_date: Optional[str] = None          # deadline for supplier response
    pr_id: Optional[int] = None             # source PR if converted
    supplier_ids: List[int] = Field(min_length=1)
    lines: List[RFQLine] = Field(min_length=1)
    remarks: str = ""


class BidLineItem(BaseModel):
    rfq_line_id: int
    unit_price: float = Field(ge=0)
    gst_rate: float = 0.0
    discount_pct: float = 0.0
    delivery_days: Optional[int] = None
    remarks: str = ""


class BidRequest(BaseModel):
    supplier_id: int
    bid_date: Optional[str] = None
    validity_days: int = 30
    payment_terms: str = ""
    lines: List[BidLineItem] = Field(min_length=1)
    remarks: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_rfq_lines(rfq_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT rl.*, i.item_code, i.item_name, u.code AS uom_code
           FROM rfq_lines rl
           LEFT JOIN items i ON i.id = rl.item_id
           LEFT JOIN units_of_measure u ON u.id = rl.uom_id
           WHERE rl.rfq_id=? ORDER BY rl.line_num""",
        (rfq_id,),
    )


def _build_rfq_suppliers(rfq_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT rs.*, s.supplier_name, s.email, s.phone
           FROM rfq_suppliers rs
           LEFT JOIN master_suppliers s ON s.id = rs.supplier_id
           WHERE rs.rfq_id=? ORDER BY rs.supplier_id""",
        (rfq_id,),
    )


def _build_bids(rfq_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT rb.*, s.supplier_name,
                  rbl.rfq_line_id, rbl.unit_price, rbl.gst_rate,
                  rbl.discount_pct, rbl.delivery_days,
                  rbl.base_price, rbl.gst_amount, rbl.line_total,
                  rbl.rank_position
           FROM rfq_bids rb
           LEFT JOIN master_suppliers s ON s.id = rb.supplier_id
           LEFT JOIN rfq_bid_lines rbl ON rbl.bid_id = rb.id
           WHERE rb.rfq_id=?
           ORDER BY rb.supplier_id, rbl.rfq_line_id""",
        (rfq_id,),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/lookups")
def lookups(user=Depends(require_login)):
    """Suppliers with email + pending approved PR lines for ME41 form."""
    cid = user["company_id"]

    suppliers = ErpDB.fetch_all(
        """SELECT id, supplier_code, supplier_name, email, payment_terms
           FROM master_suppliers
           WHERE company_id=? AND is_active=1
             AND COALESCE(email,'') != ''
           ORDER BY supplier_name""",
        (cid,),
    )

    pending_prs = ErpDB.fetch_all(
        """SELECT pr.id AS pr_id, pr.pr_number,
                  prl.id AS pr_line_id, prl.line_num,
                  prl.item_id, prl.qty_requested, prl.qty_approved,
                  prl.uom_id, prl.required_by,
                  i.item_code, i.item_name, u.code AS uom_code
           FROM purchase_requisitions pr
           JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
           LEFT JOIN items i ON i.id = prl.item_id
           LEFT JOIN units_of_measure u ON u.id = prl.uom_id
           WHERE pr.company_id=? AND pr.status='APPROVED'
             AND prl.status='OPEN'
           ORDER BY pr.pr_number, prl.line_num""",
        (cid,),
    )

    return ok({"suppliers": suppliers, "pending_prs": pending_prs})


@router.get("/")
def list_rfqs(
    status: str = "",
    pg: int = Query(default=1, ge=1),
    user=Depends(require_login),
):
    """Paginated RFQ list."""
    cid = user["company_id"]
    sql = """SELECT r.id, r.rfq_number, r.rfq_date, r.due_date,
                    r.status, r.remarks,
                    u.full_name AS created_by_name
             FROM rfqs r
             LEFT JOIN users u ON u.id = r.created_by
             WHERE r.company_id=? AND COALESCE(r.is_deleted,0)=0"""
    params: list = [cid]
    if status:
        sql += " AND r.status=?"
        params.append(status.upper())
    sql += " ORDER BY r.id DESC"
    return ok(ErpDB.paginate(sql, params, pg))


@router.post("/", dependencies=[Depends(require_tcode("ME41"))])
def create_rfq(body: RFQCreateRequest, user=Depends(require_login)):
    """ME41 — Create an RFQ, optionally from an approved PR."""
    cid = user["company_id"]
    uid = user["user_id"]

    # Validate suppliers belong to this company and are active
    for sid in body.supplier_ids:
        if not ErpDB.fetch_value(
            "SELECT id FROM master_suppliers WHERE id=? AND company_id=? AND is_active=1",
            (sid, cid),
        ):
            fail(f"Supplier ID {sid} not found or inactive.", 400)

    due = body.due_date or (
        date.today() + timedelta(days=7)).isoformat()

    try:
        ErpDB.begin_transaction()

        rfq_number = ErpDB.get_next_number(cid, "RFQ", date.today().year)

        rfq_id = ErpDB.insert("rfqs", {
            "company_id": cid,
            "branch_id": user.get("branch_id", 1),
            "rfq_number": rfq_number,
            "rfq_date": body.rfq_date or date.today().isoformat(),
            "due_date": due,
            "pr_id": body.pr_id or None,
            "status": "OPEN",
            "remarks": body.remarks,
            "created_by": uid,
        })

        # Insert lines
        for i, ln in enumerate(body.lines):
            ErpDB.insert("rfq_lines", {
                "rfq_id": rfq_id,
                "line_num": (i + 1) * 10,
                "item_id": ln.item_id,
                "uom_id": ln.uom_id,
                "qty_required": round(float(ln.qty_required), 3),
                "pr_line_id": ln.pr_line_id or None,
                "remarks": ln.remarks,
            })

        # Invite suppliers
        for sid in body.supplier_ids:
            ErpDB.insert("rfq_suppliers", {
                "rfq_id": rfq_id,
                "supplier_id": sid,
                "invited_on": date.today().isoformat(),
                "status": "INVITED",
            })

        ErpAuth.audit("rfqs", rfq_id, "INSERT", {},
                      {"rfq_number": rfq_number,
                       "suppliers": len(body.supplier_ids)})
        ErpDB.commit()
        return ok(
            {"rfq_id": rfq_id, "rfq_number": rfq_number},
            message=f"RFQ {rfq_number} created and {len(body.supplier_ids)} supplier(s) invited.",
        )

    except Exception as exc:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        logger.error("RFQ create error: %s", exc)
        fail(f"Could not create RFQ: {exc}", 500)


@router.get("/{rfq_id}")
def get_rfq(rfq_id: int, user=Depends(require_login)):
    """RFQ detail — header, lines, invited suppliers, bids received."""
    cid = user["company_id"]
    rfq = ErpDB.fetch_one(
        """SELECT r.*, u.full_name AS created_by_name
           FROM rfqs r
           LEFT JOIN users u ON u.id = r.created_by
           WHERE r.id=? AND r.company_id=?""",
        (rfq_id, cid),
    )
    if not rfq:
        fail("RFQ not found.", 404)

    return ok({
        "rfq": rfq,
        "lines": _build_rfq_lines(rfq_id),
        "suppliers": _build_rfq_suppliers(rfq_id),
        "bids": _build_bids(rfq_id),
    })


@router.post("/{rfq_id}/bid")
def record_bid(rfq_id: int, body: BidRequest, user=Depends(require_login)):
    """
    ME47 — Record a supplier's quotation / bid against an open RFQ.
    One bid per supplier per RFQ; a second call replaces the existing bid.
    """
    cid = user["company_id"]
    uid = user["user_id"]

    rfq = ErpDB.fetch_one(
        "SELECT * FROM rfqs WHERE id=? AND company_id=?", (rfq_id, cid))
    if not rfq:
        fail("RFQ not found.", 404)
    if rfq["status"] not in ("OPEN", "BIDDING"):
        fail("RFQ is not open for bids.", 400)

    # Validate supplier is invited
    invited = ErpDB.fetch_one(
        "SELECT id FROM rfq_suppliers WHERE rfq_id=? AND supplier_id=?",
        (rfq_id, body.supplier_id),
    )
    if not invited:
        fail("Supplier is not invited to this RFQ.", 400)

    try:
        ErpDB.begin_transaction()

        # Remove existing bid if re-submitting
        old_bid = ErpDB.fetch_one(
            "SELECT id FROM rfq_bids WHERE rfq_id=? AND supplier_id=?",
            (rfq_id, body.supplier_id),
        )
        if old_bid:
            ErpDB.query("DELETE FROM rfq_bid_lines WHERE bid_id=?",
                        (old_bid["id"],))
            ErpDB.query("DELETE FROM rfq_bids WHERE id=?", (old_bid["id"],))

        bid_id = ErpDB.insert("rfq_bids", {
            "rfq_id": rfq_id,
            "supplier_id": body.supplier_id,
            "bid_date": body.bid_date or date.today().isoformat(),
            "validity_days": body.validity_days,
            "payment_terms": body.payment_terms,
            "remarks": body.remarks,
            "created_by": uid,
        })

        bid_total = 0.0
        for bl in body.lines:
            base = round(
                float(bl.unit_price)
                * (1 - float(bl.discount_pct) / 100), 2)
            # qty comes from rfq_line
            rfq_line = ErpDB.fetch_one(
                "SELECT qty_required FROM rfq_lines WHERE id=? AND rfq_id=?",
                (bl.rfq_line_id, rfq_id),
            )
            qty = float(rfq_line["qty_required"]) if rfq_line else 1.0
            line_base = round(base * qty, 2)
            gst_amt = round(line_base * float(bl.gst_rate) / 100, 2)
            line_total = round(line_base + gst_amt, 2)
            bid_total += line_total

            ErpDB.insert("rfq_bid_lines", {
                "bid_id": bid_id,
                "rfq_line_id": bl.rfq_line_id,
                "unit_price": round(float(bl.unit_price), 2),
                "gst_rate": round(float(bl.gst_rate), 2),
                "discount_pct": round(float(bl.discount_pct), 2),
                "base_price": line_base,
                "gst_amount": gst_amt,
                "line_total": line_total,
                "delivery_days": bl.delivery_days,
                "remarks": bl.remarks,
                "rank_position": None,   # set by /compare
            })

        ErpDB.update("rfq_bids", {"bid_total": round(bid_total, 2)},
                     "id=?", (bid_id,))
        ErpDB.update("rfq_suppliers", {"status": "BID_RECEIVED"},
                     "rfq_id=? AND supplier_id=?", (rfq_id, body.supplier_id))
        # Flip RFQ to BIDDING once first bid arrives
        if rfq["status"] == "OPEN":
            ErpDB.update("rfqs", {"status": "BIDDING"},
                         "id=?", (rfq_id,))

        ErpDB.commit()
        return ok({"bid_id": bid_id, "bid_total": round(bid_total, 2)},
                  message="Bid recorded successfully.")

    except Exception as exc:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        logger.error("Bid record error: %s", exc)
        fail(f"Could not record bid: {exc}", 500)


@router.post("/{rfq_id}/compare")
def compare_bids(rfq_id: int, user=Depends(require_login)):
    """
    Rank all bids per RFQ line by unit_price (lowest = L1).
    Updates rank_position on rfq_bid_lines.
    Returns comparison matrix grouped by line.
    """
    cid = user["company_id"]
    rfq = ErpDB.fetch_one(
        "SELECT * FROM rfqs WHERE id=? AND company_id=?", (rfq_id, cid))
    if not rfq:
        fail("RFQ not found.", 404)

    lines = _build_rfq_lines(rfq_id)
    matrix: list = []

    for ln in lines:
        bids_for_line = ErpDB.fetch_all(
            """SELECT rbl.id AS bid_line_id, rbl.bid_id,
                      rbl.unit_price, rbl.gst_rate, rbl.discount_pct,
                      rbl.line_total, rbl.delivery_days,
                      s.supplier_name
               FROM rfq_bid_lines rbl
               JOIN rfq_bids rb ON rb.id = rbl.bid_id
               JOIN master_suppliers s ON s.id = rb.supplier_id
               WHERE rbl.rfq_line_id=?
               ORDER BY rbl.unit_price ASC""",
            (ln["id"],),
        )
        for rank, row in enumerate(bids_for_line, start=1):
            ErpDB.update("rfq_bid_lines",
                         {"rank_position": rank},
                         "id=?", (row["bid_line_id"],))
            row["rank_position"] = rank

        matrix.append({
            "rfq_line_id": ln["id"],
            "item_code": ln["item_code"],
            "item_name": ln["item_name"],
            "qty_required": ln["qty_required"],
            "bids": bids_for_line,
        })

    return ok({"matrix": matrix}, message="Bid comparison complete.")


@router.post("/{rfq_id}/close")
def close_rfq(rfq_id: int, user=Depends(require_login)):
    """Close an RFQ — no further bids accepted (PURCHASE_MANAGER / SUPER_ADMIN)."""
    cid = user["company_id"]
    if not ErpAuth.has_role(["PURCHASE_MANAGER", "SUPER_ADMIN"]):
        fail("Not authorized to close RFQs.", 403)

    rfq = ErpDB.fetch_one(
        "SELECT * FROM rfqs WHERE id=? AND company_id=?", (rfq_id, cid))
    if not rfq:
        fail("RFQ not found.", 404)
    if rfq["status"] == "CLOSED":
        fail("RFQ is already closed.", 400)

    ErpDB.update("rfqs", {"status": "CLOSED"},
                 "id=? AND company_id=?", (rfq_id, cid))
    ErpAuth.audit("rfqs", rfq_id, "CLOSE",
                  {"status": rfq["status"]}, {"status": "CLOSED"})
    return ok(message=f"RFQ {rfq['rfq_number']} closed.")