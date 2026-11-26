"""
backend/core/stock.py
---------------------
Shared stock / GRN posting logic.

Ported from PHP:
  DB::postStockMovement()                 -> post_stock_movement()
  DB::processGRN()  (sp_process_grn)      -> process_grn()
  msme_erp_mat_refresh_map()              -> refresh_item_map()
  msme_erp_create_grn_inspection_lots()   -> create_grn_inspection_lots()

Used by routers/migo.py now, and later by QA32 (quality result entry posts the
held GRN), sales returns, etc.

Assumes the same nesting semantics as the PHP ErpDB: begin_transaction() /
commit() keep a depth counter and only the outermost pair really begins /
commits; rollback() always rolls the whole thing back.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from backend.core.db import ErpDB

log = logging.getLogger("erp.stock")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Item-level moving average price
# ---------------------------------------------------------------------------
def refresh_item_map(item_id: int) -> None:
    """Item MAP = weighted average cost over ALL warehouses with stock on hand."""
    r = ErpDB.fetch_one(
        "SELECT SUM(qty_on_hand) AS q, SUM(qty_on_hand*avg_cost) AS v "
        "FROM stock_balances WHERE item_id=? AND qty_on_hand>0",
        [item_id],
    ) or {}
    q = float(r.get("q") or 0)
    if q > 0.0005:
        ErpDB.update(
            "items",
            {"moving_avg_price": round(float(r.get("v") or 0) / q, 4)},
            "id=?",
            [item_id],
        )


# ---------------------------------------------------------------------------
# Stock movement
# ---------------------------------------------------------------------------
def post_stock_movement(
    company_id: int, item_id: int, warehouse_id: int,
    mvt_type: str, mvt_code: str,
    qty_in: float, qty_out: float, unit_cost: float,
    ref_type: str, ref_id: int, ref_num: str, user_id: int,
) -> int:
    """Update stock_balances (weighted-average cost) and write a stock_movements row."""
    ErpDB.begin_transaction()
    try:
        bal = ErpDB.fetch_one(
            "SELECT qty_on_hand, avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
            [item_id, warehouse_id],
        )
        cur_qty = float((bal or {}).get("qty_on_hand") or 0)
        cur_avg = float((bal or {}).get("avg_cost") or 0)

        if not bal:
            ErpDB.insert("stock_balances", {
                "item_id": item_id, "warehouse_id": warehouse_id,
                "qty_on_hand": 0, "avg_cost": 0,
            })

        if qty_out > 0 and (cur_qty - qty_out) < -0.001:
            raise RuntimeError(
                f"Insufficient stock for item #{item_id}. Available: {cur_qty}, Needed: {qty_out}"
            )

        new_qty = cur_qty + qty_in - qty_out
        if qty_in > 0 and unit_cost > 0:
            new_avg = ((cur_qty * cur_avg) + (qty_in * unit_cost)) / max(0.001, cur_qty + qty_in)
        else:
            new_avg = cur_avg
        tot_val = qty_in * unit_cost if qty_in > 0 else qty_out * cur_avg

        ErpDB.update(
            "stock_balances",
            {"qty_on_hand": new_qty, "avg_cost": new_avg, "last_updated": _now()},
            "item_id=? AND warehouse_id=?",
            [item_id, warehouse_id],
        )
        refresh_item_map(item_id)

        mvt_id = ErpDB.insert("stock_movements", {
            "company_id": company_id,
            "movement_date": date.today().isoformat(),
            "movement_type": mvt_type,
            "mvt_code": mvt_code,
            "item_id": item_id,
            "warehouse_id": warehouse_id,
            "reference_type": ref_type,
            "reference_id": ref_id,
            "reference_num": ref_num,
            "qty_in": qty_in,
            "qty_out": qty_out,
            "unit_cost": unit_cost,
            "total_value": tot_val,
            "running_balance": new_qty,
            "created_by": user_id,
        })
        ErpDB.commit()
        return int(mvt_id)
    except Exception:
        ErpDB.rollback()
        raise


# ---------------------------------------------------------------------------
# GRN posting
# ---------------------------------------------------------------------------
def _try_post_grn_to_gl(grn_id: int, grn_number: str) -> None:
    """
    PHP: msme_erp_fin_try_post(fn() => msme_erp_fin_post_grn($grnId), ...)
    Dr Inventory / Cr GR-IR. Must NEVER block the physical receipt.

    finance_engine has not been ported yet, so this is a no-op until
    backend/core/finance_engine.py exposes fin_post_grn(grn_id). When you port it,
    make it savepoint-safe like the PHP version (a failed journal must not leave
    half-written rows inside the caller's transaction).
    """
    try:
        from backend.core import finance_engine as fe  # type: ignore
    except ImportError:
        log.info("finance_engine not available - GRN %s not posted to GL (run FBPOST later)", grn_number)
        return
    fn = getattr(fe, "fin_post_grn", None)
    if fn is None:
        return
    try:
        fn(grn_id)
    except Exception as e:  # noqa: BLE001
        log.error("GL auto-post failed for GRN %s: %s - fix the cause and run FBPOST", grn_number, e)


def process_grn(grn_id: int, user_id: int) -> None:
    """
    Post a DRAFT / PENDING_QC goods receipt to stock.
      * MVT 122 (return to vendor) takes stock OUT and gives the quantity back to the PO.
      * Every other movement type brings accepted quantity IN.
      * Only the ACCEPTED quantity counts as received on the PO line.
    """
    grn = ErpDB.fetch_one(
        "SELECT * FROM goods_receipts WHERE id=? AND status IN ('DRAFT','PENDING_QC')", [grn_id]
    )
    if not grn:
        raise RuntimeError("GRN not found or already posted.")

    is_return = str(grn.get("movement_type") or "101") == "122"

    ErpDB.begin_transaction()
    try:
        lines = ErpDB.fetch_all("SELECT * FROM goods_receipt_lines WHERE grn_id=?", [grn_id])
        for l in lines:
            acc = float(l.get("qty_accepted") or 0)
            if acc <= 0:
                continue
            post_stock_movement(
                int(grn["company_id"]), int(l["item_id"]), int(grn["warehouse_id"]),
                "RTV" if is_return else "GRN", "122" if is_return else "101",
                0.0 if is_return else acc, acc if is_return else 0.0,
                float(l.get("unit_price") or 0),
                "goods_receipts", grn_id, grn["grn_number"], user_id,
            )
            pol_id = l.get("po_line_id")
            if pol_id:
                cur = float(ErpDB.fetch_value(
                    "SELECT qty_received FROM purchase_order_lines WHERE id=?", [int(pol_id)]) or 0)
                new = max(0.0, cur - acc) if is_return else cur + acc
                ErpDB.update("purchase_order_lines", {"qty_received": new}, "id=?", [int(pol_id)])

        ErpDB.update("goods_receipts", {"status": "POSTED", "posted_at": _now()}, "id=?", [grn_id])

        _try_post_grn_to_gl(grn_id, grn["grn_number"])

        po_id = grn.get("po_id")
        if po_id and not is_return:
            open_qty = ErpDB.fetch_value(
                "SELECT SUM(qty_ordered - qty_received) FROM purchase_order_lines WHERE po_id=?", [po_id])
            ErpDB.update("purchase_orders",
                         {"status": "CLOSED" if float(open_qty or 0) <= 0 else "PARTIAL"},
                         "id=?", [po_id])
        elif po_id and is_return:
            recv = float(ErpDB.fetch_value(
                "SELECT COALESCE(SUM(qty_received),0) FROM purchase_order_lines WHERE po_id=?", [po_id]) or 0)
            ErpDB.update("purchase_orders",
                         {"status": "PARTIAL" if recv > 0 else "APPROVED"},
                         "id=?", [po_id])
        ErpDB.commit()
    except Exception:
        ErpDB.rollback()
        raise


# ---------------------------------------------------------------------------
# Quality inspection lots for a GRN held in QC
# ---------------------------------------------------------------------------
def create_grn_inspection_lots(grn_id: int, company_id: int) -> list[str]:
    """One inspection lot per GRN line (origin_type GRN_LINE). Idempotent per line."""
    nums: list[str] = []
    lines = ErpDB.fetch_all(
        "SELECT id, item_id, qty_received FROM goods_receipt_lines WHERE grn_id=?", [grn_id])
    for l in lines:
        if ErpDB.fetch_value(
            "SELECT COUNT(*) FROM inspection_lots WHERE origin_type='GRN_LINE' AND origin_id=?",
            [int(l["id"])],
        ):
            continue
        plan = ErpDB.fetch_value(
            "SELECT id FROM inspection_plans WHERE company_id=? AND item_id=? AND is_active=1 "
            "ORDER BY id DESC LIMIT 1", [company_id, int(l["item_id"])])
        num = ErpDB.get_next_number(company_id, "QLOT", date.today().year)
        ErpDB.insert("inspection_lots", {
            "company_id": company_id, "lot_number": num, "lot_date": date.today().isoformat(),
            "origin_type": "GRN_LINE", "origin_id": int(l["id"]),
            "item_id": int(l["item_id"]), "plan_id": int(plan) if plan else None,
            "qty": float(l["qty_received"]), "status": "OPEN",
        })
        nums.append(num)
    return nums