"""
backend/routers/service_entry.py
---------------------------------
Brain ERP — /api/service-entry
Service Entry Sheet (ML81N) — services rendered against a PO, accepted by
a purchase manager with an optional vendor-evaluation score.

Converted from modules/mm/service_entry_full.php (and the empty-stub
modules/mm/service_entry.php dispatcher). Business logic lives in
core/service_entry.py.

Endpoints
---------
GET  /lookups              — open POs, cost centers, service-type items (create form)
GET  /po/{po_id}/lines     — PO lines for the selected PO (populates the
                              "PO Line (optional)" dropdown once a PO is chosen)
GET  /                     — paginated list (status filter, DRAFT/ACCEPTED/REJECTED counts)
POST /                     — create a new DRAFT entry sheet
GET  /{ses_id}             — header + lines detail
POST /{ses_id}/accept      — accept & post (PURCHASE_MANAGER / SUPER_ADMIN only)
POST /{ses_id}/reject      — reject (PURCHASE_MANAGER / SUPER_ADMIN only)

PHP -> Python notes
  * canCreate = hasRole([PURCHASE_EXEC, PURCHASE_MANAGER, SUPER_ADMIN]);
    canAccept = hasRole([PURCHASE_MANAGER, SUPER_ADMIN]) — enforced per-endpoint below
    instead of toggling page sections.
  * CSRF dropped (per project rule).
  * The PHP single page (list/new/detail/POST actions) is split into the
    endpoints above.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from core.service_entry import (
    create_service_entry_sheet,
    accept_service_entry_sheet,
    reject_service_entry_sheet,
)
from ..deps import require_login, require_tcode, require_role, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/service-entry", tags=["ML81N - Service Entry Sheet"])

TCODE = "ML81N"
CREATE_ROLES = ["PURCHASE_EXEC", "PURCHASE_MANAGER", "SUPER_ADMIN"]
ACCEPT_ROLES = ["PURCHASE_MANAGER", "SUPER_ADMIN"]
STATUS_BADGES = {"DRAFT": "warning", "ACCEPTED": "success", "REJECTED": "danger", "CANCELLED": "secondary"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class SesLine(BaseModel):
    short_text: str
    quantity: float = Field(gt=0)
    gross_price: float = Field(ge=0, default=0)
    service_item_id: Optional[int] = None
    uom_id: Optional[int] = None
    line_cost_center_id: Optional[int] = None


class SesCreate(BaseModel):
    po_id: int
    po_line_id: Optional[int] = None
    ses_date: Optional[str] = None
    short_text: str = ""
    acct_assgt_cat: str = "K"
    cost_center_id: Optional[int] = None
    external_number: str = ""
    service_location: str = ""
    period_from: Optional[str] = None
    period_to: Optional[str] = None
    price_ref_date: Optional[str] = None
    returns_indicator: bool = False
    remarks: str = ""
    lines: list[SesLine] = Field(default_factory=list)


class SesAccept(BaseModel):
    quality_of_service: int = Field(100, ge=0, le=100)
    service_timeliness: int = Field(100, ge=0, le=100)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
@router.get("/lookups", dependencies=[Depends(require_tcode(TCODE))])
def lookups():
    company_id = ErpAuth.company_id()
    pos = ErpDB.fetch_all(
        "SELECT po.id, po.po_number, po.currency_code, s.name AS supplier_name "
        "FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id "
        "WHERE po.company_id=? AND po.status IN ('APPROVED','PARTIAL') AND po.is_deleted=0 "
        "ORDER BY po.id DESC",
        (company_id,),
    )
    cost_centers = ErpDB.fetch_all(
        "SELECT id, code, name FROM cost_centers WHERE company_id=? AND is_active=1 ORDER BY name",
        (company_id,),
    )
    service_items = ErpDB.fetch_all(
        "SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, i.moving_avg_price "
        "FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id "
        "WHERE i.company_id=? AND i.item_type='SERVICE' AND i.is_active=1 AND i.is_deleted=0 "
        "ORDER BY i.item_name",
        (company_id,),
    )
    return ok({
        "purchase_orders": pos,
        "cost_centers": cost_centers,
        "service_items": service_items,
        "can_create": ErpAuth.has_role(CREATE_ROLES),
        "can_accept": ErpAuth.has_role(ACCEPT_ROLES),
    })


@router.get("/po/{po_id}/lines", dependencies=[Depends(require_tcode(TCODE))])
def po_lines(po_id: int):
    rows = ErpDB.fetch_all(
        "SELECT pol.id, pol.line_num, i.item_code, i.item_name, i.item_type "
        "FROM purchase_order_lines pol JOIN items i ON i.id = pol.item_id "
        "WHERE pol.po_id=? ORDER BY pol.line_num",
        (po_id,),
    )
    return ok(rows)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get("/", dependencies=[Depends(require_tcode(TCODE))])
def list_sheets(
    status: str = Query("", description="DRAFT | ACCEPTED | REJECTED | '' for all"),
    pg: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    company_id = ErpAuth.company_id()
    sql = (
        "SELECT ses.id, ses.ses_number, ses.ses_date, po.po_number, s.name AS supplier_name, "
        "ses.total_amount, ses.status, ses.acceptance_doc_number "
        "FROM service_entry_sheets ses "
        "JOIN purchase_orders po ON po.id = ses.po_id "
        "JOIN suppliers s ON s.id = ses.supplier_id "
        "WHERE ses.company_id=? AND ses.is_deleted=0"
    )
    params: list = [company_id]
    if status:
        sql += " AND ses.status=?"
        params.append(status)
    sql += " ORDER BY ses.id DESC"

    result = ErpDB.paginate(sql, tuple(params), pg, page_size)

    counts = {"DRAFT": 0, "ACCEPTED": 0, "REJECTED": 0}
    for r in ErpDB.fetch_all(
        "SELECT status, COUNT(*) c FROM service_entry_sheets WHERE company_id=? AND is_deleted=0 GROUP BY status",
        (company_id,),
    ):
        if r["status"] in counts:
            counts[r["status"]] = int(r["c"])

    result["counts"] = counts
    return ok(result)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@router.get("/{ses_id}", dependencies=[Depends(require_tcode(TCODE))])
def get_sheet(ses_id: int):
    company_id = ErpAuth.company_id()
    ses = ErpDB.fetch_one(
        "SELECT ses.*, po.po_number, s.name AS supplier_name, s.supplier_code, "
        "u.full_name AS created_by_name, acc.full_name AS accepted_by_name, "
        "cc.code AS cc_code, cc.name AS cc_name "
        "FROM service_entry_sheets ses "
        "JOIN purchase_orders po ON po.id = ses.po_id "
        "JOIN suppliers s ON s.id = ses.supplier_id "
        "LEFT JOIN users u ON u.id = ses.created_by "
        "LEFT JOIN users acc ON acc.id = ses.accepted_by "
        "LEFT JOIN cost_centers cc ON cc.id = ses.cost_center_id "
        "WHERE ses.id=? AND ses.company_id=?",
        (ses_id, company_id),
    )
    if not ses:
        return fail("Service entry sheet not found.", 404)

    if ses.get("po_line_id"):
        ses["po_line_num"] = ErpDB.fetch_value(
            "SELECT line_num FROM purchase_order_lines WHERE id=?", (ses["po_line_id"],)
        )

    lines = ErpDB.fetch_all(
        "SELECT sesl.*, i.item_code AS service_code, u.code AS uom_code, cc.code AS line_cc_code "
        "FROM service_entry_sheet_lines sesl "
        "LEFT JOIN items i ON i.id = sesl.service_item_id "
        "LEFT JOIN units_of_measure u ON u.id = sesl.uom_id "
        "LEFT JOIN cost_centers cc ON cc.id = sesl.cost_center_id "
        "WHERE sesl.ses_id=? ORDER BY sesl.line_num",
        (ses_id,),
    )
    return ok({
        "sheet": ses,
        "lines": lines,
        "status_badge": STATUS_BADGES.get(ses["status"], "secondary"),
        "can_accept": ErpAuth.has_role(ACCEPT_ROLES),
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post("/", dependencies=[Depends(require_role(*CREATE_ROLES))])
def create_sheet(body: SesCreate):
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    try:
        new_id = create_service_entry_sheet(payload)
    except ValueError as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.exception("Service entry sheet create failed")
        return fail(f"Error creating service entry sheet: {e}", 500)

    num = ErpDB.fetch_value("SELECT ses_number FROM service_entry_sheets WHERE id=?", (new_id,))
    return ok({"ses_id": new_id, "ses_number": num}, f"Service entry sheet {num} created. Ready for acceptance.")


# ---------------------------------------------------------------------------
# Accept / Reject
# ---------------------------------------------------------------------------
@router.post("/{ses_id}/accept", dependencies=[Depends(require_role(*ACCEPT_ROLES))])
def accept_sheet(ses_id: int, body: SesAccept):
    try:
        acc_num = accept_service_entry_sheet(ses_id, body.quality_of_service, body.service_timeliness)
    except Exception as e:  # noqa: BLE001
        logger.exception("Service entry sheet accept failed")
        return fail(f"Error accepting service entry sheet: {e}", 500)

    if not acc_num:
        return fail("Entry sheet not found, or it is not in DRAFT status.", 409)

    return ok({"ses_id": ses_id, "acceptance_doc_number": acc_num},
              f"Service entry sheet saved, acceptance document {acc_num} posted.")


@router.post("/{ses_id}/reject", dependencies=[Depends(require_role(*ACCEPT_ROLES))])
def reject_sheet(ses_id: int):
    ok_flag = reject_service_entry_sheet(ses_id)
    if not ok_flag:
        return fail("Entry sheet not found, or it is not in DRAFT status.", 409)
    return ok({"ses_id": ses_id}, "Service entry sheet rejected.")