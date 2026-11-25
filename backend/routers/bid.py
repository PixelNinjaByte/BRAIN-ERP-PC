"""
Bid / Tender (BID01 create, BID02 manage/list/view + status workflow)
Converted from modules/mm/bid_create.php and modules/mm/bid_view.php
"""
from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.core.db import ErpDB
from backend.core.auth import ErpAuth
from backend.routers.deps import require_login, require_tcode, ok, fail

router = APIRouter(prefix="/api/bid", tags=["bid"])

# Linear forward transitions — no skipping a stage, mirrors bid_view.php
TRANSITIONS = {
    "NEW": ["PUBLISHED", "CANCELLED"],
    "PUBLISHED": ["CLOSED", "CANCELLED"],
    "CLOSED": ["AWARDED", "CANCELLED"],
}

STATUS_LABELS = {
    "PUBLISHED": "Publish",
    "CLOSED": "Close Submission",
    "AWARDED": "Mark Awarded",
    "CANCELLED": "Cancel Bid",
}


# ---------------------------------------------------------------- schemas --
class BidItemIn(BaseModel):
    description: str
    qty: float = 0
    uom_id: Optional[int] = None
    rate: float = 0
    pr_line_id: Optional[int] = None


class BidCreateIn(BaseModel):
    bid_type: str = "SERVICE"                 # SERVICE / GOODS / WORKS
    requirement_type: str = "OTHERS"
    mode_of_bid: str = "OPEN"
    sche_delivery_days: int = 0
    bid_creation_date: Optional[date] = None
    warehouse_id: Optional[int] = None
    purch_group: Optional[str] = ""
    bid_publish_date: Optional[date] = None
    bid_stage: str = "SINGLE_PART"             # SINGLE_PART / TWO_PART
    bid_security_applic: bool = False
    bid_security_amount: float = 0
    bid_security_amount_fc: float = 0
    foreign_curr_appli: bool = False
    tax_rate: float = 0
    e_proc_applicable: bool = False
    bid_spec_applic: bool = False
    bid_spec_cost: float = 0
    bid_spec_cost_fc: float = 0
    download_start_dt: Optional[str] = None    # "YYYY-MM-DDTHH:MM" (datetime-local)
    bid_subm_end_dt: Optional[str] = None
    bid_open_dt: Optional[str] = None
    title_of_bid: str = Field(..., min_length=1)
    bid_value: float = 0
    e_reverse_auction: bool = False
    pr_id: Optional[int] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    department_id: Optional[int] = None
    items: List[BidItemIn] = []


class StatusChangeIn(BaseModel):
    new_status: str


def _dt_local(val: Optional[str]) -> Optional[str]:
    """'YYYY-MM-DDTHH:MM' -> 'YYYY-MM-DD HH:MM:00' (same rule as PHP's str_replace)."""
    if not val:
        return None
    return val.replace("T", " ") + ":00" if "T" in val else val


# ------------------------------------------------------------- lookups ----
@router.get("/lookups")
def bid_lookups(pr_id: Optional[int] = None,
                 auth: ErpAuth = Depends(require_tcode("BID01"))):
    company_id = auth.company_id()

    categories = ErpDB.fetch_all(
        "SELECT id, code, name FROM factory_categories WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    units = ErpDB.fetch_all(
        "SELECT id, code, name, category_id FROM plants WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    depts = ErpDB.fetch_all(
        "SELECT id, code, name, plant_id FROM departments WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    warehouses = ErpDB.fetch_all(
        "SELECT id, code, name FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    uoms = ErpDB.fetch_all(
        "SELECT id, code FROM units_of_measure WHERE is_active=1 ORDER BY code", [])
    eligible_prs = ErpDB.fetch_all(
        """SELECT pr.id, pr.pr_number, pr.pr_date, pr.status, COUNT(prl.id) AS line_count
           FROM purchase_requisitions pr
           JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
           WHERE pr.company_id = ? AND pr.status IN ('SUBMITTED','APPROVED')
           GROUP BY pr.id ORDER BY pr.id DESC""",
        [company_id])

    selected_pr = None
    pr_lines = []
    if pr_id:
        selected_pr = ErpDB.fetch_one(
            "SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?",
            [pr_id, company_id])
        if selected_pr:
            pr_lines = ErpDB.fetch_all(
                """SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
                   FROM purchase_requisition_lines prl
                   LEFT JOIN items i ON i.id = prl.item_id
                   LEFT JOIN units_of_measure u ON u.id = prl.uom_id
                   WHERE prl.pr_id = ? ORDER BY prl.line_num""",
                [pr_id])

    return ok({
        "categories": categories, "units": units, "departments": depts,
        "warehouses": warehouses, "uoms": uoms, "eligible_prs": eligible_prs,
        "selected_pr": selected_pr, "pr_lines": pr_lines,
    })


# -------------------------------------------------------------- create ----
@router.post("")
def create_bid(body: BidCreateIn, auth: ErpAuth = Depends(require_tcode("BID01"))):
    company_id = auth.company_id()

    items = []
    for it in body.items:
        desc = (it.description or "").strip()
        if not desc:
            continue
        value = round(it.qty * it.rate, 2)
        items.append({**it.model_dump(), "description": desc, "value": value})

    if not body.title_of_bid.strip():
        return fail("Title of Bid is required.")
    if not items:
        return fail("Add at least one item/service line before saving the bid.")

    try:
        ErpDB.begin_transaction()

        bid_number = ErpDB.get_next_number(company_id, "BID", datetime.now().year)

        header = {
            "company_id": company_id,
            "bid_number": bid_number,
            "bid_type": body.bid_type,
            "requirement_type": body.requirement_type,
            "mode_of_bid": body.mode_of_bid,
            "sche_delivery_days": body.sche_delivery_days,
            "bid_creation_date": str(body.bid_creation_date or date.today()),
            "warehouse_id": body.warehouse_id,
            "purch_group": body.purch_group or "",
            "status": "NEW",
            "bid_publish_date": str(body.bid_publish_date) if body.bid_publish_date else None,
            "bid_stage": body.bid_stage,
            "bid_security_applic": int(body.bid_security_applic),
            "bid_security_amount": body.bid_security_amount,
            "bid_security_amount_fc": body.bid_security_amount_fc,
            "foreign_curr_appli": int(body.foreign_curr_appli),
            "tax_rate": body.tax_rate,
            "e_proc_applicable": int(body.e_proc_applicable),
            "bid_spec_applic": int(body.bid_spec_applic),
            "bid_spec_cost": body.bid_spec_cost,
            "bid_spec_cost_fc": body.bid_spec_cost_fc,
            "download_start_dt": _dt_local(body.download_start_dt),
            "bid_subm_end_dt": _dt_local(body.bid_subm_end_dt),
            "bid_open_dt": _dt_local(body.bid_open_dt),
            "title_of_bid": body.title_of_bid.strip(),
            "bid_value": body.bid_value,
            "e_reverse_auction": int(body.e_reverse_auction),
            "pr_id": body.pr_id,
            "category_id": body.category_id,
            "unit_id": body.unit_id,
            "department_id": body.department_id,
            "created_by": auth.user()["id"],
        }

        bid_id = ErpDB.insert("bids", header)

        for i, it in enumerate(items):
            ErpDB.insert("bid_items", {
                "bid_id": bid_id,
                "line_num": (i + 1) * 10,
                "pr_line_id": it["pr_line_id"],
                "item_type": "MATERIAL" if header["bid_type"] == "GOODS" else "SERVICE",
                "description": it["description"],
                "qty": it["qty"],
                "uom_id": it["uom_id"],
                "estimated_rate": it["rate"],
                "estimated_value": it["value"],
            })

        auth.audit("bids", bid_id, "INSERT", {}, {"bid_number": bid_number, "title_of_bid": header["title_of_bid"]})
        ErpDB.commit()

        return ok({"id": bid_id, "bid_number": bid_number}, f"Bid {bid_number} created.")

    except Exception as e:
        ErpDB.rollback()
        raise HTTPException(status_code=500, detail=f"Could not create Bid: {e}")


# ---------------------------------------------------------------- list ----
@router.get("")
def list_bids(status: Optional[str] = None, auth: ErpAuth = Depends(require_tcode("BID02"))):
    company_id = auth.company_id()
    where = "WHERE b.company_id = ?"
    params = [company_id]
    if status:
        where += " AND b.status = ?"
        params.append(status)

    bids = ErpDB.fetch_all(
        f"""SELECT b.*, (SELECT COUNT(*) FROM bid_items bi WHERE bi.bid_id = b.id) AS item_count
            FROM bids b {where} ORDER BY b.id DESC""",
        params)
    return ok({"bids": bids})


# -------------------------------------------------------------- detail ----
@router.get("/{bid_id}")
def get_bid(bid_id: int, auth: ErpAuth = Depends(require_tcode("BID02"))):
    company_id = auth.company_id()
    bid = ErpDB.fetch_one(
        """SELECT b.*, fc.code AS category_code, fc.name AS category_name,
                  p.code AS unit_code, p.name AS unit_name,
                  d.code AS dept_code, d.name AS dept_name,
                  w.code AS warehouse_code, w.name AS warehouse_name,
                  pr.pr_number AS src_pr_number,
                  u.full_name AS created_by_name
           FROM bids b
           LEFT JOIN factory_categories fc ON fc.id = b.category_id
           LEFT JOIN plants p ON p.id = b.unit_id
           LEFT JOIN departments d ON d.id = b.department_id
           LEFT JOIN warehouses w ON w.id = b.warehouse_id
           LEFT JOIN purchase_requisitions pr ON pr.id = b.pr_id
           LEFT JOIN users u ON u.id = b.created_by
           WHERE b.id = ? AND b.company_id = ?""",
        [bid_id, company_id])
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found.")

    items = ErpDB.fetch_all(
        """SELECT bi.*, u.code AS uom_code FROM bid_items bi
           LEFT JOIN units_of_measure u ON u.id = bi.uom_id
           WHERE bi.bid_id = ? ORDER BY bi.line_num""",
        [bid_id])

    # ---- analytics panel (ported from the cumulative-value / bar chart block) ----
    item_labels, item_values, item_cum = [], [], []
    running = 0.0
    for it in items:
        item_labels.append((it["description"] or "")[:18])
        v = round(float(it["estimated_value"] or 0), 2)
        item_values.append(v)
        running = round(running + v, 2)
        item_cum.append(running)

    total_est = round(sum(item_values), 2)
    tax_amount = round(total_est * (float(bid["tax_rate"] or 0) / 100), 2)
    grand_total = round(total_est + tax_amount, 2)

    days_to_submit = None
    if bid["bid_subm_end_dt"]:
        delta = datetime.strptime(bid["bid_subm_end_dt"], "%Y-%m-%d %H:%M:%S") - datetime.now()
        days_to_submit = delta.days

    milestones = [
        {"label": "Bid Created", "value": bid["bid_creation_date"], "icon": "bi-flag-fill"},
        {"label": "Bid Published", "value": bid["bid_publish_date"], "icon": "bi-megaphone-fill"},
        {"label": "Download Opens", "value": bid["download_start_dt"], "icon": "bi-download"},
        {"label": "Submission Closes", "value": bid["bid_subm_end_dt"], "icon": "bi-hourglass-split"},
        {"label": "Bid Opens", "value": bid["bid_open_dt"], "icon": "bi-unlock-fill"},
    ]

    return ok({
        "bid": bid,
        "items": items,
        "analytics": {
            "item_labels": item_labels, "item_values": item_values, "item_cum": item_cum,
            "total_estimated_value": total_est, "tax_amount": tax_amount, "grand_total": grand_total,
            "days_to_submit": days_to_submit,
        },
        "milestones": milestones,
        "next_transitions": [
            {"status": ns, "label": STATUS_LABELS.get(ns, ns)}
            for ns in TRANSITIONS.get(bid["status"], [])
        ],
    })


# ---------------------------------------------------------- status change --
@router.post("/{bid_id}/status")
def change_status(bid_id: int, body: StatusChangeIn, auth: ErpAuth = Depends(require_tcode("BID02"))):
    company_id = auth.company_id()
    bid = ErpDB.fetch_one("SELECT * FROM bids WHERE id=? AND company_id=?", [bid_id, company_id])
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found.")

    if body.new_status not in TRANSITIONS.get(bid["status"], []):
        return fail(f"Cannot move Bid from {bid['status']} to {body.new_status}.")

    update = {"status": body.new_status}
    if body.new_status == "PUBLISHED" and not bid["bid_publish_date"]:
        update["bid_publish_date"] = str(date.today())

    ErpDB.update("bids", update, "id=?", [bid_id])
    auth.audit("bids", bid_id, "UPDATE", {"status": bid["status"]}, {"status": body.new_status})

    label = STATUS_LABELS.get(body.new_status, body.new_status).rstrip("Bid ").strip() or body.new_status.title()
    return ok({"id": bid_id, "status": body.new_status},
              f"Bid {bid['bid_number']} moved to {body.new_status.title()}.")