"""
backend/routers/reorder.py
----------------------------
Brain ERP — /api/reorder
Reorder Report (ZMRP) — materials whose TOTAL stock (all warehouses, less
reserved) is at or below the reorder point, with open-PO cover and a
suggested order quantity. Selected lines are turned into one Purchase
Requisition in a single click.

Converted from modules/mm/material_reorder.php.
Business logic already ported: core/material.py (item_block_sql, create_pr)
and core/helpers.py (item_stock_map) — this router only adds the HTTP surface
and reproduces the PHP screen's row-building/sorting logic.

Endpoints (T-code ZMRP)
-----------------------
GET  /                — the reorder list (same rows as the PHP page body)
POST /create-pr        — {"lines": {"<item_id>": qty, ...}} -> one Purchase
                          Requisition for the selected, positive-qty lines

PHP -> Python notes
  * CSRF dropped (per project rule).
  * The PHP form posted `sel[id]` (checkbox) + `qty[id]` (number) together;
    the API only needs the quantities a line is "selected" by having qty>0,
    exactly like `msme_erp_mat_create_pr()` already filters (`float(v) > 0`).
  * Sorting (`critical` first, uncovered before covered) is preserved here
    so a thin client can render the list as-is.
"""
from __future__ import annotations

import logging
import math
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from core.material import item_block_sql, create_pr
from core.helpers import item_stock_map
from .deps import require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reorder", tags=["ZMRP - Reorder Report"])

TCODE = "ZMRP"


class CreatePrFromReorder(BaseModel):
    lines: Dict[int, float] = Field(..., description="{item_id: order_qty}")


def _fmt(n: float) -> str:
    s = f"{float(n or 0):.3f}".rstrip("0").rstrip(".")
    return s or "0"


# ---------------------------------------------------------------------------
# GET / — the reorder list
# ---------------------------------------------------------------------------
@router.get("/", dependencies=[Depends(require_tcode(TCODE))])
def reorder_list():
    company_id = ErpAuth.company_id()

    items = ErpDB.fetch_all(
        "SELECT i.id, i.item_code, i.item_name, i.min_stock, i.max_stock, i.reorder_point, "
        "i.lead_time_days, i.lot_size, u.code AS uom "
        "FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id "
        "WHERE i.company_id=? AND i.is_deleted=0 AND i.is_active=1 AND i.item_type<>'SERVICE' "
        "AND i.reorder_point>0" + item_block_sql("PURCHASE") + " ORDER BY i.item_code",
        (company_id,),
    )
    stock_map = item_stock_map(company_id, [int(it["id"]) for it in items]) if items else {}

    rows = []
    for it in items:
        s = stock_map.get(int(it["id"]), {"on_hand": 0.0, "reserved": 0.0, "available": 0.0, "on_order": 0.0})
        avail = float(s["available"])
        rp = float(it["reorder_point"] or 0)
        if avail > rp:
            continue  # stock is healthy

        on_order = float(s["on_order"])
        position = avail + on_order
        target = float(it["max_stock"]) if it.get("max_stock") else rp * 2
        covered = position > rp  # open POs already bring it back above the reorder point

        suggest = 0.0
        if not covered:
            suggest = max(0.0, target - position)
            lot = float(it.get("lot_size") or 0)
            if lot > 0:
                suggest = math.ceil(suggest / lot - 1e-9) * lot

        row = dict(it)
        row.update({
            "on_hand": float(s["on_hand"]),
            "available": avail,
            "on_order": on_order,
            "suggest": round(suggest, 3),
            "covered": covered,
            "critical": avail < float(it["min_stock"] or 0),
            "available_fmt": _fmt(avail),
            "reorder_point_fmt": _fmt(rp),
            "min_stock_fmt": _fmt(it.get("min_stock")),
            "on_order_fmt": _fmt(on_order),
            "suggest_fmt": _fmt(suggest),
        })
        rows.append(row)

    # Critical first, then uncovered-before-covered (mirrors the PHP usort)
    rows.sort(key=lambda r: (not r["critical"], r["covered"]))

    return ok({
        "rows": rows,
        "count": len(rows),
        "note": ("Available = on hand - reserved, all warehouses. Suggestion tops up to Max stock "
                 "(or 2x reorder point), counting open POs, in whole lots."),
    })


# ---------------------------------------------------------------------------
# POST /create-pr — turn a selection into a Purchase Requisition
# ---------------------------------------------------------------------------
@router.post("/create-pr", dependencies=[Depends(require_tcode(TCODE))])
def create_pr_from_reorder(body: CreatePrFromReorder):
    company_id = ErpAuth.company_id()
    result = create_pr(company_id, body.lines)
    if not result.get("ok"):
        return fail(result.get("message") or "Could not create the purchase requisition.", 400)
    return ok(
        {"pr_id": result["pr_id"], "pr_number": result["pr_number"], "lines": result["lines"]},
        result["message"],
    )