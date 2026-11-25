"""
Brain ERP — /api/po
Purchase Order module (ME21N / ME23N).
Port of mm/po_create.php + mm/po_view.php → Python / FastAPI.

Endpoints
---------
GET  /lookups              — suppliers, items, UOMs, pending PRs, last prices
GET  /                     — paginated PO list (ME23N list)
POST /                     — create PO (ME21N)
GET  /{po_id}              — PO detail + lines + release trail (ME23N detail)
POST /{po_id}/release      — in-page approve / reject / return release action
"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from .deps import require_login, require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class POLine(BaseModel):
    item_id: int
    uom_id: int
    qty_ordered: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    discount_pct: float = 0.0
    gst_rate: float = 0.0
    pr_line_id: Optional[int] = None
    delivery_date: Optional[str] = None
    remarks: str = ""


class POCreateRequest(BaseModel):
    supplier_id: int
    po_date: Optional[str] = None
    delivery_date: Optional[str] = None
    payment_terms: str = ""
    currency_code: str = "INR"
    discount_amount: float = 0.0
    round_off: float = 0.0
    remarks: str = ""
    pr_id: Optional[int] = None          # source PR if converted
    lines: List[POLine] = Field(min_length=1)


class ReleaseActionRequest(BaseModel):
    action: str           # approve | reject | return
    remarks: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calc_totals(lines: List[POLine]) -> dict:
    """
    Reproduce PHP header total logic:
        base   = qty * price * (1 - disc/100)
        gst    = base * gst_rate/100
        taxable_total = sum(base)
        gst_total     = sum(gst)
        grand_total   = taxable_total + gst_total - discount_amount + round_off
    grand_total / round_off set at header level in _build_header().
    """
    taxable = 0.0
    gst_total = 0.0
    for ln in lines:
        base = round(float(ln.qty_ordered) * float(ln.unit_price)
                     * (1 - float(ln.discount_pct) / 100), 2)
        gst = round(base * float(ln.gst_rate) / 100, 2)
        taxable += base
        gst_total += gst
    return {
        "taxable_total": round(taxable, 2),
        "gst_total": round(gst_total, 2),
    }


def _build_po_lines_query(po_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT pol.*, i.item_code, i.item_name, u.code AS uom_code
           FROM purchase_order_lines pol
           LEFT JOIN items i ON i.id = pol.item_id
           LEFT JOIN units_of_measure u ON u.id = pol.uom_id
           WHERE pol.po_id = ?
           ORDER BY pol.line_num""",
        (po_id,),
    )


def _release_trail(po_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT ra.*, u.full_name AS action_by_name
           FROM release_actions ra
           LEFT JOIN users u ON u.id = ra.action_by
           WHERE ra.doc_type='purchase_order' AND ra.doc_id=?
           ORDER BY ra.id""",
        (po_id,),
    )


def _trigger_release_strategy(company_id: int, po_id: str, po_number: str,
                               grand_total: float, uid: int) -> None:
    """
    Auto-create document_releases row if a release strategy exists for
    purchase_order at this company (mirrors PHP auto-trigger logic).
    """
    strategy = ErpDB.fetch_one(
        """SELECT rs.* FROM release_strategies rs
           WHERE rs.company_id=? AND rs.doc_type='purchase_order'
             AND rs.is_active=1
             AND (rs.amount_from IS NULL OR rs.amount_from <= ?)
             AND (rs.amount_to   IS NULL OR rs.amount_to   >= ?)
           LIMIT 1""",
        (company_id, grand_total, grand_total),
    )
    if not strategy:
        return

    # First pending release level for this strategy
    level = ErpDB.fetch_one(
        """SELECT * FROM release_levels
           WHERE strategy_id=? ORDER BY sequence_no LIMIT 1""",
        (strategy["id"],),
    )
    if not level:
        return

    ErpDB.insert("document_releases", {
        "company_id": company_id,
        "doc_type": "purchase_order",
        "doc_id": po_id,
        "strategy_id": strategy["id"],
        "current_level": level["sequence_no"],
        "status": "PENDING",
        "created_by": uid,
    })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/lookups")
def lookups(user=Depends(require_login)):
    """Dropdown data for the ME21N create form."""
    cid = user["company_id"]

    suppliers = ErpDB.fetch_all(
        """SELECT id, supplier_code, supplier_name, gstin, payment_terms,
                  currency_code
           FROM master_suppliers
           WHERE company_id=? AND COALESCE(is_active,1)=1
           ORDER BY supplier_name""",
        (cid,),
    )

    items = ErpDB.fetch_all(
        """SELECT i.id, i.item_code, i.item_name, i.gst_rate,
                  i.moving_avg_price, u.id AS uom_id, u.code AS uom
           FROM items i
           LEFT JOIN units_of_measure u ON u.id = i.base_uom_id
           WHERE i.company_id=? AND i.is_active=1
             AND COALESCE(i.block_status,'') NOT IN ('PROCUREMENT','ALL')
             AND COALESCE(i.is_deleted,0)=0
           ORDER BY i.item_code""",
        (cid,),
    )

    uoms = ErpDB.fetch_all(
        "SELECT id, code, name FROM units_of_measure WHERE is_active=1 ORDER BY code")

    warehouses = ErpDB.fetch_all(
        "SELECT id, name, code FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY name",
        (cid,),
    )

    # Pending PRs (APPROVED, not yet fully on a PO)
    pending_prs = ErpDB.fetch_all(
        """SELECT pr.id, pr.pr_number, pr.pr_date, pr.purpose,
                  prl.id AS line_id, prl.line_num,
                  prl.item_id, prl.qty_requested, prl.qty_approved,
                  prl.est_price, prl.uom_id, prl.required_by,
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

    return ok({
        "suppliers": suppliers,
        "items": items,
        "uoms": uoms,
        "warehouses": warehouses,
        "pending_prs": pending_prs,
    })


@router.get("/supplier_last_prices")
def supplier_last_prices(
    supplier_id: int,
    user=Depends(require_login),
):
    """Historical last price per item from this supplier's past POs."""
    cid = user["company_id"]
    rows = ErpDB.fetch_all(
        """SELECT pol.item_id, pol.unit_price, pol.gst_rate,
                  po.po_date, po.po_number
           FROM purchase_order_lines pol
           JOIN purchase_orders po ON po.id = pol.po_id
           WHERE po.company_id=? AND po.supplier_id=?
             AND po.status NOT IN ('CANCELLED','REJECTED')
           ORDER BY po.po_date DESC""",
        (cid, supplier_id),
    )
    # Deduplicate: keep only the most recent price per item
    seen: dict = {}
    prices: list = []
    for r in rows:
        if r["item_id"] not in seen:
            seen[r["item_id"]] = True
            prices.append(r)
    return ok(prices)


@router.get("/")
def list_pos(
    status: str = "",
    supplier_id: Optional[int] = None,
    from_date: str = "",
    to_date: str = "",
    pg: int = Query(default=1, ge=1),
    user=Depends(require_login),
):
    """ME23N — paginated PO list for this company. Uses 'pg' param (matches PHP)."""
    cid = user["company_id"]
    sql = """SELECT po.id, po.po_number, po.po_date, po.status,
                    po.grand_total, po.currency_code,
                    s.supplier_name
             FROM purchase_orders po
             LEFT JOIN master_suppliers s ON s.id = po.supplier_id
             WHERE po.company_id=? AND COALESCE(po.is_deleted,0)=0"""
    params: list = [cid]

    if status:
        sql += " AND po.status=?"
        params.append(status)
    if supplier_id:
        sql += " AND po.supplier_id=?"
        params.append(supplier_id)
    if from_date:
        sql += " AND po.po_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND po.po_date <= ?"
        params.append(to_date)

    sql += " ORDER BY po.id DESC"
    result = ErpDB.paginate(sql, params, pg)
    return ok(result)


@router.post("/", dependencies=[Depends(require_tcode("ME21N"))])
def create_po(body: POCreateRequest, user=Depends(require_login)):
    """ME21N — Create a Purchase Order, optionally from an approved PR."""
    cid = user["company_id"]
    uid = user["user_id"]

    if not body.lines:
        fail("Add at least one PO line.", 400)

    # Validate supplier exists in this company
    sup = ErpDB.fetch_one(
        "SELECT id FROM master_suppliers WHERE id=? AND company_id=?",
        (body.supplier_id, cid),
    )
    if not sup:
        fail("Supplier not found.", 400)

    # Calculate totals
    totals = _calc_totals(body.lines)
    grand_total = round(
        totals["taxable_total"]
        + totals["gst_total"]
        - float(body.discount_amount)
        + float(body.round_off),
        2,
    )

    try:
        ErpDB.begin_transaction()

        po_number = ErpDB.get_next_number(cid, "PO", date.today().year)

        po_id = ErpDB.insert("purchase_orders", {
            "company_id": cid,
            "branch_id": user.get("branch_id", 1),
            "po_number": po_number,
            "po_date": body.po_date or date.today().isoformat(),
            "supplier_id": body.supplier_id,
            "delivery_date": body.delivery_date or None,
            "payment_terms": body.payment_terms,
            "currency_code": body.currency_code or "INR",
            "taxable_total": totals["taxable_total"],
            "gst_total": totals["gst_total"],
            "discount_amount": round(float(body.discount_amount), 2),
            "round_off": round(float(body.round_off), 2),
            "grand_total": grand_total,
            "remarks": body.remarks,
            "status": "DRAFT",
            "created_by": uid,
        })

        pr_line_ids_used: list[int] = []

        for i, ln in enumerate(body.lines):
            base_price = round(
                float(ln.qty_ordered)
                * float(ln.unit_price)
                * (1 - float(ln.discount_pct) / 100),
                2,
            )
            gst_amt = round(base_price * float(ln.gst_rate) / 100, 2)

            ErpDB.insert("purchase_order_lines", {
                "po_id": po_id,
                "line_num": (i + 1) * 10,
                "item_id": ln.item_id,
                "uom_id": ln.uom_id,
                "qty_ordered": round(float(ln.qty_ordered), 3),
                "unit_price": round(float(ln.unit_price), 2),
                "discount_pct": round(float(ln.discount_pct), 2),
                "base_price": base_price,
                "gst_rate": round(float(ln.gst_rate), 2),
                "gst_amount": gst_amt,
                "line_total": round(base_price + gst_amt, 2),
                "delivery_date": ln.delivery_date or body.delivery_date or None,
                "pr_line_id": ln.pr_line_id or None,
                "remarks": ln.remarks,
                "qty_received": 0,
                "qty_pending": round(float(ln.qty_ordered), 3),
                "status": "OPEN",
            })

            if ln.pr_line_id:
                pr_line_ids_used.append(ln.pr_line_id)

        # Flip PR line status → PO_CREATED
        for prl_id in pr_line_ids_used:
            ErpDB.update("purchase_requisition_lines",
                         {"status": "PO_CREATED"},
                         "id=?", (prl_id,))

        # If source PR is fully covered, flip PR header too
        if body.pr_id and pr_line_ids_used:
            remaining = ErpDB.fetch_value(
                "SELECT COUNT(*) FROM purchase_requisition_lines "
                "WHERE pr_id=? AND status='OPEN'",
                (body.pr_id,),
            )
            if not remaining:
                ErpDB.update("purchase_requisitions",
                             {"status": "PO_CREATED"},
                             "id=?", (body.pr_id,))

        # Auto-trigger release strategy if configured
        _trigger_release_strategy(cid, po_id, po_number, grand_total, uid)

        ErpAuth.audit("purchase_orders", po_id, "INSERT", {},
                      {"po_number": po_number, "grand_total": grand_total})
        ErpDB.commit()
        return ok(
            {"po_id": po_id, "po_number": po_number, "grand_total": grand_total},
            message=f"Purchase Order {po_number} created successfully.",
        )

    except Exception as exc:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        logger.error("PO create error: %s", exc)
        fail(f"Could not create PO: {exc}", 500)


@router.get("/{po_id}")
def get_po(po_id: int, user=Depends(require_login)):
    """ME23N detail — PO header + lines + release trail."""
    cid = user["company_id"]
    po = ErpDB.fetch_one(
        """SELECT po.*,
                  s.supplier_name, s.gstin AS supplier_gstin,
                  s.address AS supplier_address,
                  c.company_name, c.gstin AS company_gstin,
                  u.full_name AS created_by_name
           FROM purchase_orders po
           LEFT JOIN master_suppliers s ON s.id = po.supplier_id
           LEFT JOIN companies c ON c.id = po.company_id
           LEFT JOIN users u ON u.id = po.created_by
           WHERE po.id=? AND po.company_id=?""",
        (po_id, cid),
    )
    if not po:
        fail("Purchase order not found.", 404)

    lines = _build_po_lines_query(po_id)
    trail = _release_trail(po_id)

    # Check if current user can action releases (STORE_MANAGER or higher for GRN info)
    pending_release = ErpDB.fetch_one(
        "SELECT * FROM document_releases "
        "WHERE doc_type='purchase_order' AND doc_id=? AND status='PENDING'",
        (po_id,),
    )

    return ok({
        "po": po,
        "lines": lines,
        "release_trail": trail,
        "pending_release": pending_release,
    })


@router.post("/{po_id}/release")
def po_release_action(po_id: int, body: ReleaseActionRequest,
                       user=Depends(require_login)):
    """
    In-page release action (approve / reject / return).
    Mirrors po_view.php release block logic.
    Requires PURCHASE_MANAGER or SUPER_ADMIN.
    """
    cid = user["company_id"]
    uid = user["user_id"]
    action = body.action.upper()

    if action not in ("APPROVE", "REJECT", "RETURN"):
        fail("action must be approve | reject | return.", 400)

    if not ErpAuth.has_role(["PURCHASE_MANAGER", "SUPER_ADMIN"]):
        fail("Not authorized to action PO releases.", 403)

    po = ErpDB.fetch_one(
        "SELECT * FROM purchase_orders WHERE id=? AND company_id=?",
        (po_id, cid),
    )
    if not po:
        fail("Purchase order not found.", 404)

    release = ErpDB.fetch_one(
        "SELECT * FROM document_releases "
        "WHERE doc_type='purchase_order' AND doc_id=? AND status='PENDING'",
        (po_id,),
    )
    if not release:
        fail("No pending release found for this PO.", 400)

    try:
        ErpDB.begin_transaction()

        # Log the release action
        ErpDB.insert("release_actions", {
            "doc_type": "purchase_order",
            "doc_id": po_id,
            "release_id": release["id"],
            "action": action,
            "remarks": body.remarks,
            "action_by": uid,
            "action_date": date.today().isoformat(),
        })

        if action == "APPROVE":
            # Check if there is a next release level
            next_level = ErpDB.fetch_one(
                """SELECT rl.* FROM release_levels rl
                   WHERE rl.strategy_id=? AND rl.sequence_no > ?
                   ORDER BY rl.sequence_no LIMIT 1""",
                (release["strategy_id"], release["current_level"]),
            )
            if next_level:
                # Advance to next level
                ErpDB.update("document_releases",
                             {"current_level": next_level["sequence_no"]},
                             "id=?", (release["id"],))
            else:
                # All levels passed → release fully approved
                ErpDB.update("document_releases",
                             {"status": "RELEASED"},
                             "id=?", (release["id"],))
                ErpDB.update("purchase_orders",
                             {"status": "APPROVED"},
                             "id=? AND company_id=?", (po_id, cid))
                ErpAuth.audit("purchase_orders", po_id, "APPROVE",
                              {"status": po["status"]}, {"status": "APPROVED"})

        elif action == "REJECT":
            ErpDB.update("document_releases",
                         {"status": "REJECTED"},
                         "id=?", (release["id"],))
            ErpDB.update("purchase_orders",
                         {"status": "REJECTED"},
                         "id=? AND company_id=?", (po_id, cid))
            ErpAuth.audit("purchase_orders", po_id, "REJECT",
                          {"status": po["status"]}, {"status": "REJECTED"})

        else:  # RETURN
            ErpDB.update("document_releases",
                         {"status": "RETURNED"},
                         "id=?", (release["id"],))
            ErpDB.update("purchase_orders",
                         {"status": "RETURNED"},
                         "id=? AND company_id=?", (po_id, cid))
            ErpAuth.audit("purchase_orders", po_id, "RETURN",
                          {"status": po["status"]}, {"status": "RETURNED"})

        ErpDB.commit()
        label = {"APPROVE": "approved", "REJECT": "rejected", "RETURN": "returned"}[action]
        return ok(message=f"PO {po['po_number']} {label} successfully.")

    except Exception as exc:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        logger.error("PO release error: %s", exc)
        fail(f"Release action failed: {exc}", 500)