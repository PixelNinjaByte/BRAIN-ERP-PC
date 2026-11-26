"""
Brain ERP — /api/pr
Purchase Requisition module (ME51N / ME53N).
Port of mm/pr_full.php → Python / FastAPI.

Endpoints
---------
GET  /lookups              — items, UOMs, cost-centres (create form dropdowns)
GET  /                     — paginated PR list (ME53N)
POST /                     — create PR or Service Request (ME51N)
GET  /{pr_id}              — single PR + lines detail (ME53N detail)
POST /{pr_id}/action       — approve / reject / convert-to-po (ME53N actions)
"""

from __future__ import annotations

import logging
from datetime import date
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

class PRLine(BaseModel):
    item_id: Optional[int] = None
    uom_id: Optional[int] = None
    qty_requested: float = Field(gt=0)
    est_price: float = 0.0
    required_by: Optional[str] = None
    remarks: str = ""
    # Service request only
    service_name: Optional[str] = None


class PRCreateRequest(BaseModel):
    pr_type: str = "MATERIAL"          # MATERIAL | SERVICE
    pr_date: Optional[str] = None
    required_by: Optional[str] = None
    purpose: str = ""
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    department_id: Optional[int] = None
    dept_id: Optional[int] = None
    lines: List[PRLine] = Field(min_length=1)


class PRActionRequest(BaseModel):
    action: str           # approve | reject | convert_po
    remarks: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_service_item(company_id: int, service_name: str, est_price: float,
                        user_id: int) -> tuple[int, int]:
    """
    Auto-create a lightweight SERVICE item master on the fly (same as PHP).
    Returns (item_id, uom_id).
    """
    uom_id = int(ErpDB.fetch_value(
        "SELECT id FROM units_of_measure WHERE UPPER(code)='NOS' LIMIT 1") or 0)
    code = ErpDB.get_next_number(company_id, "SRVITEM", date.today().year)
    item_id = ErpDB.insert("items", {
        "company_id": company_id,
        "item_code": code,
        "item_name": service_name.strip(),
        "item_type": "SERVICE",
        "item_category": "SERVICE",
        "base_uom_id": uom_id,
        "moving_avg_price": est_price,
        "is_active": 1,
        "created_by": user_id,
    })
    return item_id, uom_id


def _build_pr_lines_query(pr_id: int) -> list:
    return ErpDB.fetch_all(
        """SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
           FROM purchase_requisition_lines prl
           LEFT JOIN items i ON i.id = prl.item_id
           LEFT JOIN units_of_measure u ON u.id = prl.uom_id
           WHERE prl.pr_id = ?
           ORDER BY prl.line_num""",
        (pr_id,),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/lookups")
def lookups(user=Depends(require_login)):
    """Dropdown data for the ME51N create form."""
    cid = user["company_id"]
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
    categories = ErpDB.fetch_all(
        "SELECT id, name FROM factory_categories WHERE company_id=? ORDER BY name", (cid,))
    units = ErpDB.fetch_all(
        "SELECT id, name FROM plants WHERE company_id=? ORDER BY name", (cid,))
    departments = ErpDB.fetch_all(
        "SELECT id, name FROM departments WHERE company_id=? ORDER BY name", (cid,))
    return ok({
        "items": items,
        "uoms": uoms,
        "categories": categories,
        "units": units,
        "departments": departments,
    })


@router.get("/")
def list_prs(
    status: str = "",
    pr_type: str = "",
    pg: int = Query(default=1, ge=1),
    user=Depends(require_login),
):
    """ME53N — paginated list of PRs for this company."""
    cid = user["company_id"]
    sql = """SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.status,
                    pr.purpose, pr.required_by,
                    u.full_name AS requested_by_name
             FROM purchase_requisitions pr
             LEFT JOIN users u ON u.id = pr.requested_by
             WHERE pr.company_id=? AND COALESCE(pr.is_deleted,0)=0"""
    params: list = [cid]
    if status:
        sql += " AND pr.status=?"
        params.append(status)
    if pr_type:
        sql += " AND pr.pr_type=?"
        params.append(pr_type.upper())
    sql += " ORDER BY pr.id DESC"
    result = ErpDB.paginate(sql, params, pg)
    return ok(result)


@router.post("/", dependencies=[Depends(require_tcode("ME51N"))])
def create_pr(body: PRCreateRequest, user=Depends(require_login)):
    """ME51N — Create a Purchase Requisition or Service Request."""
    cid = user["company_id"]
    uid = user["user_id"]
    pr_type = "SERVICE" if body.pr_type.upper() == "SERVICE" else "MATERIAL"

    # Validate lines
    valid_lines = []
    for ln in body.lines:
        if pr_type == "SERVICE":
            has_name = ln.service_name and ln.service_name.strip()
            if not has_name and not ln.item_id:
                continue
        else:
            if not ln.item_id:
                continue
        if ln.qty_requested <= 0:
            continue
        valid_lines.append(ln)

    if not valid_lines:
        fail("Add at least one line with a valid item and quantity.", 400)

    try:
        ErpDB.begin_transaction()

        pr_number = ErpDB.get_next_number(cid, "PR", date.today().year)

        pr_id = ErpDB.insert("purchase_requisitions", {
            "company_id": cid,
            "branch_id": user.get("branch_id", 1),
            "pr_number": pr_number,
            "pr_date": body.pr_date or date.today().isoformat(),
            "pr_type": pr_type,
            "requested_by": uid,
            "required_by": body.required_by or None,
            "purpose": body.purpose,
            "status": "SUBMITTED",
            "created_by": uid,
            "category_id": body.category_id,
            "unit_id": body.unit_id,
            "department_id": body.dept_id or body.department_id,
        })

        for i, ln in enumerate(valid_lines):
            item_id = ln.item_id
            uom_id = ln.uom_id

            if pr_type == "SERVICE" and not item_id:
                # Auto-create service master (same as PHP)
                item_id, uom_id = _next_service_item(
                    cid, ln.service_name, ln.est_price, uid)

            if not uom_id:
                uom_id = int(ErpDB.fetch_value(
                    "SELECT base_uom_id FROM items WHERE id=?", (item_id,)) or 0)

            ErpDB.insert("purchase_requisition_lines", {
                "pr_id": pr_id,
                "line_num": (i + 1) * 10,
                "item_id": item_id,
                "uom_id": uom_id,
                "qty_requested": round(float(ln.qty_requested), 3),
                "est_price": round(float(ln.est_price), 2),
                "required_by": body.required_by or None,
                "remarks": ln.remarks,
                "status": "OPEN",
            })

        ErpAuth.audit("purchase_requisitions", pr_id, "INSERT", {},
                      {"pr_number": pr_number, "pr_type": pr_type})
        ErpDB.commit()
        label = "Service Request" if pr_type == "SERVICE" else "PR"
        return ok({"pr_id": pr_id, "pr_number": pr_number},
                  message=f"{label} {pr_number} submitted successfully.")

    except Exception as exc:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        logger.error("PR create error: %s", exc)
        fail(f"Could not create PR: {exc}", 500)


@router.get("/{pr_id}")
def get_pr(pr_id: int, user=Depends(require_login)):
    """ME53N detail — single PR header + lines."""
    cid = user["company_id"]
    pr = ErpDB.fetch_one(
        """SELECT pr.*, u.full_name AS requested_by_name
           FROM purchase_requisitions pr
           LEFT JOIN users u ON u.id = pr.requested_by
           WHERE pr.id=? AND pr.company_id=?""",
        (pr_id, cid),
    )
    if not pr:
        fail("Purchase requisition not found.", 404)
    lines = _build_pr_lines_query(pr_id)
    return ok({"pr": pr, "lines": lines})


@router.post("/{pr_id}/action")
def pr_action(pr_id: int, body: PRActionRequest, user=Depends(require_login)):
    """
    ME53N actions: approve / reject / convert_po.
    Approve/reject require PURCHASE_MANAGER or SUPER_ADMIN.
    Convert to PO requires PURCHASE_EXEC / PURCHASE_MANAGER / SUPER_ADMIN.
    """
    cid = user["company_id"]
    action = body.action.lower()

    pr = ErpDB.fetch_one(
        "SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?",
        (pr_id, cid),
    )
    if not pr:
        fail("Purchase requisition not found.", 404)

    if action == "approve":
        if not ErpAuth.has_role(["PURCHASE_MANAGER", "SUPER_ADMIN"]):
            fail("Not authorized to approve PRs.", 403)
        ErpDB.update("purchase_requisitions",
                     {"status": "APPROVED"}, "id=? AND company_id=?", (pr_id, cid))
        ErpDB.query(
            "UPDATE purchase_requisition_lines SET qty_approved=qty_requested WHERE pr_id=?",
            (pr_id,))
        # Clear any pending release entry
        ErpDB.query(
            "UPDATE document_releases SET status='RELEASED' "
            "WHERE doc_type='purchase_requisition' AND doc_id=? AND status='PENDING'",
            (pr_id,))
        ErpAuth.audit("purchase_requisitions", pr_id, "APPROVE",
                      {"status": pr["status"]}, {"status": "APPROVED"})
        return ok(message="PR approved successfully.")

    elif action == "reject":
        if not ErpAuth.has_role(["PURCHASE_MANAGER", "SUPER_ADMIN"]):
            fail("Not authorized to reject PRs.", 403)
        ErpDB.update("purchase_requisitions",
                     {"status": "REJECTED"}, "id=? AND company_id=?", (pr_id, cid))
        ErpDB.query(
            "UPDATE document_releases SET status='REJECTED' "
            "WHERE doc_type='purchase_requisition' AND doc_id=? AND status='PENDING'",
            (pr_id,))
        ErpAuth.audit("purchase_requisitions", pr_id, "REJECT",
                      {"status": pr["status"]}, {"status": "REJECTED"})
        return ok(message="PR rejected.")

    elif action == "convert_po":
        if not ErpAuth.has_role(["PURCHASE_EXEC", "PURCHASE_MANAGER", "SUPER_ADMIN"]):
            fail("Not authorized to convert PRs to POs.", 403)
        if pr["status"] != "APPROVED":
            fail("Only an APPROVED PR can be converted to a Purchase Order.", 400)
        # Return a redirect hint — the client navigates to ME21N with ?pr_id=
        return ok({
            "redirect_tcode": "ME21N",
            "pr_id": pr_id,
            "pr_number": pr["pr_number"],
        }, message=f"Redirect to ME21N for PR {pr['pr_number']}.")

    else:
        fail(f"Unknown action: {action}. Use approve | reject | convert_po.", 400)