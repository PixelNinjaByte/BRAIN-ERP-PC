"""
backend/routers/migo.py
-----------------------
MIGO - Goods Movement (MSME ERP / Brain ERP, Prigag Online Pvt. Ltd.)

Converted from modules/mm/goods_movement.php.

Movement types: 101 GRN against PO | 122 Return to Vendor | 261 Issue to WO
                201 Issue to Cost Center | 501 Receipt without PO

Endpoints (all under /api/migo, T-code MIGO):
  GET  /meta?mvt=101        master data for the screen (suppliers, warehouses, UOMs,
                            items, open POs, QC config, movement types)
  GET  /po/{po_id}          "Pull from Purchase Order": PO header + open lines
  GET  /grn/{grn_id}        view a posted / held GRN (+ QC lots; deductions for SUPER_ADMIN only)
  POST /post                post a goods movement

PHP -> Python notes
  * The PHP screen was one page (create + view + POST). It is split into the four
    JSON endpoints above; HTML, JS and flash/redirect handling belong to the PyQt/web UI.
  * CSRF is dropped (per project rule). The PHP single-use form token also doubled as a
    double-click / F5 guard, so an optional `request_id` on POST /post is accepted and a
    repeat of the same id is rejected (in-process, 10 min).
  * Deduction amounts (ZQDR) are only read, written or returned for SUPER_ADMIN.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.core import stock
from backend.core.auth import ErpAuth
from backend.core import finance_engine as fe
from backend.core.db import ErpDB
from backend.core.util import currency_symbol, has_col as _has_col, opt_bool
from backend.routers.deps import fail, ok, require_tcode

log = logging.getLogger("erp.migo")

router = APIRouter(prefix="/api/migo", tags=["MIGO"])

TCODE = "MIGO"
QTY_EPS = 0.0004

MVT_LABELS = {
    "101": "Goods Receipt against PO",
    "122": "Return to Vendor",
    "261": "Goods Issue to Work Order",
    "201": "Goods Issue to Cost Center",
    "501": "Receipt without PO",
}
DED_REASONS = {
    "QUALITY": "Quality / quantity not as ordered",
    "PRICE_DIFF": "Price difference",
    "OTHER": "Other deduction",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _is_super_admin() -> bool:
    return bool(ErpAuth.has_role(["SUPER_ADMIN"]))


def _ctx() -> dict:
    u = ErpAuth.user() or {}
    return {
        "company_id": int(u.get("company_id") or 0),
        "user_id": int(u.get("id") or 0),
        "branch_id": int(u.get("branch_id") or 1),
        "currency": (u.get("currency") or "INR"),
    }


def _fmt_qty(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _qc_all_receipts() -> bool:
    """PHP get_option('msme_erp_qc_all_receipts') - company-wide "inspect every goods receipt"."""
    return opt_bool("msme_erp_qc_all_receipts", 0)


def _qc_item_ids(company_id: int) -> list[int]:
    if not _has_col("items", "qc_required"):
        return []
    rows = ErpDB.fetch_all("SELECT id FROM items WHERE company_id=? AND qc_required=1", [company_id])
    return [int(r["id"]) for r in rows]


def _qc_mandatory(company_id: int, item_ids: list[int]) -> bool:
    if _qc_all_receipts():
        return True
    flagged = set(_qc_item_ids(company_id))
    return any(int(i) in flagged for i in item_ids)


def calc_deduction_amount(mode: str, value: float, base_amount: float) -> float:
    """PHP msme_erp_calc_deduction_amount()."""
    if value <= 0:
        return 0.0
    if mode.upper() == "AMOUNT":
        return round(value, 2)
    return round(base_amount * value / 100, 2)


# ---- master data (layered so a dropdown is never empty when usable records exist) ----
def _orphan_sql(col: str = "company_id") -> str:
    return f"({col} IS NULL OR {col}=0 OR {col} NOT IN (SELECT id FROM companies))"


def master_suppliers(company_id: int, notes: list[str]) -> list[dict]:
    w = ["company_id=?"]
    if _has_col("suppliers", "is_deleted"):
        w.append("COALESCE(is_deleted,0)=0")
    if _has_col("suppliers", "status"):
        w.append("UPPER(COALESCE(NULLIF(status,''),'ACTIVE')) NOT IN ('INACTIVE','BLOCKED','DELETED')")
    rows = ErpDB.fetch_all(
        f"SELECT id, supplier_code, name FROM suppliers WHERE {' AND '.join(w)} ORDER BY name", [company_id])
    if rows:
        return rows
    rows = ErpDB.fetch_all(
        "SELECT id, supplier_code, name FROM suppliers WHERE company_id=? ORDER BY name", [company_id])
    if rows:
        notes.append("Suppliers shown regardless of their Active/Blocked/Deleted flag - none of this "
                     "company's suppliers passed the normal filter. Check their status on XK02.")
        return rows
    rows = ErpDB.fetch_all(
        f"SELECT id, supplier_code, name FROM suppliers WHERE {_orphan_sql()} ORDER BY name")
    if rows:
        notes.append("Suppliers shown here are not assigned to any company. Use ZMDC (Master Data "
                     "Link Check) to assign them to this company.")
    return rows


def master_warehouses(company_id: int) -> list[dict]:
    if _has_col("warehouses", "is_active"):
        rows = ErpDB.fetch_all(
            "SELECT id, code, name FROM warehouses WHERE company_id=? AND COALESCE(is_active,1)<>0 "
            "ORDER BY code", [company_id])
        if rows:
            return rows
    rows = ErpDB.fetch_all("SELECT id, code, name FROM warehouses WHERE company_id=? ORDER BY code", [company_id])
    if rows:
        return rows
    try:  # company has none: create the default so stock can always be posted
        ErpDB.insert("warehouses", {"company_id": company_id, "code": "WH01", "name": "Main Warehouse",
                                    "wh_type": "GENERAL", "is_active": 1})
    except Exception as e:  # noqa: BLE001
        log.error("default warehouse: %s", e)
    return ErpDB.fetch_all("SELECT id, code, name FROM warehouses WHERE company_id=? ORDER BY code", [company_id])


def default_warehouse_id(company_id: int) -> int:
    rows = master_warehouses(company_id)
    return int(rows[0]["id"]) if rows else 0


def master_items(company_id: int, notes: list[str]) -> list[dict]:
    if _has_col("items", "moving_avg_price"):
        price = "COALESCE(i.moving_avg_price,0)"
    elif _has_col("items", "standard_price"):
        price = "COALESCE(i.standard_price,0)"
    else:
        price = "0"
    gst = "COALESCE(i.gst_rate,0)" if _has_col("items", "gst_rate") else "0"
    sel = (f"SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, "
           f"{price} AS moving_avg_price, {gst} AS gst_rate "
           f"FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id")
    w_del = ["i.company_id=?"]
    if _has_col("items", "is_deleted"):
        w_del.append("COALESCE(i.is_deleted,0)=0")
    w_act = list(w_del)
    if _has_col("items", "is_active"):
        w_act.append("COALESCE(i.is_active,1)=1")

    rows = ErpDB.fetch_all(f"{sel} WHERE {' AND '.join(w_act)} ORDER BY i.item_name", [company_id])
    if rows:
        return rows
    rows = ErpDB.fetch_all(f"{sel} WHERE {' AND '.join(w_del)} ORDER BY i.item_name", [company_id])
    if rows:
        notes.append("Materials shown even though they are marked inactive or blocked - none of this "
                     "company's materials are usable. Re-activate / unblock them on MM02 or ZMAT.")
        return rows
    rows = ErpDB.fetch_all(f"{sel} WHERE {_orphan_sql('i.company_id')} ORDER BY i.item_name")
    if rows:
        notes.append("Materials shown here are not assigned to any company. Use ZMDC (Master Data "
                     "Link Check) to assign them to this company.")
    return rows


def open_pos(company_id: int, supplier_id: int = 0) -> list[dict]:
    """Approved / partial POs that still have a receivable balance (not already sitting in a GRN)."""
    sql = """
        SELECT po.id, po.po_number, po.po_date, po.supplier_id, po.status, s.name AS supplier_name,
               COUNT(pol.id) AS open_lines
        FROM purchase_orders po
        JOIN suppliers s ON s.id=po.supplier_id
        JOIN purchase_order_lines pol ON pol.po_id=po.id
        WHERE po.company_id=? AND COALESCE(po.is_deleted,0)=0 AND po.status IN ('APPROVED','PARTIAL')
          AND (pol.qty_ordered - pol.qty_received
               - COALESCE((SELECT SUM(g.qty_received) FROM goods_receipt_lines g
                           JOIN goods_receipts gr ON gr.id=g.grn_id
                           WHERE g.po_line_id=pol.id AND gr.status IN ('DRAFT','PENDING_QC')),0)) > 0
    """
    args: list = [company_id]
    if supplier_id:
        sql += " AND po.supplier_id=?"
        args.append(supplier_id)
    sql += (" GROUP BY po.id, po.po_number, po.po_date, po.supplier_id, po.status, s.name "
            "ORDER BY po.po_date DESC, po.id DESC")
    return ErpDB.fetch_all(sql, args)


def po_open_lines(po_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        """SELECT pol.*, i.item_code, i.item_name, u.code AS uom, i.gst_rate,
                  (pol.qty_ordered - pol.qty_received) AS qty_open_gross,
                  COALESCE((SELECT SUM(g.qty_received) FROM goods_receipt_lines g
                            JOIN goods_receipts gr ON gr.id=g.grn_id
                            WHERE g.po_line_id=pol.id AND gr.status IN ('DRAFT','PENDING_QC')),0) AS qty_in_qc
           FROM purchase_order_lines pol
           JOIN items i ON i.id=pol.item_id LEFT JOIN units_of_measure u ON u.id=pol.uom_id
           WHERE pol.po_id=? ORDER BY pol.id""", [po_id])


def _pullable_po_lines(po_id: int) -> list[dict]:
    out = []
    for pl in po_open_lines(po_id):
        if pl.get("status") and pl["status"] != "OPEN":
            continue
        open_q = float(pl["qty_ordered"] or 0) - float(pl["qty_received"] or 0) - float(pl["qty_in_qc"] or 0)
        if open_q <= QTY_EPS:
            continue
        pl["qty_open"] = open_q
        out.append(pl)
    return out


# ---- double-submit guard (replaces the PHP single-use form token) ----
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def _claim_request_id(rid: Optional[str]) -> bool:
    if not rid:
        return True
    now = time.time()
    with _seen_lock:
        for k in [k for k, t in _seen.items() if now - t > 600]:
            _seen.pop(k, None)
        if rid in _seen:
            return False
        _seen[rid] = now
        return True


def _release_request_id(rid: Optional[str]) -> None:
    if rid:
        with _seen_lock:
            _seen.pop(rid, None)


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------
class GrnLineIn(BaseModel):
    po_line_id: Optional[int] = None
    item_id: Optional[int] = None
    qty_ordered: Optional[float] = None
    qty_received: float = 0
    qty_accepted: Optional[float] = None      # blank => equals received
    uom_id: Optional[int] = None
    unit_price: float = 0                     # ignored when the line comes from a PO
    batch_number: Optional[str] = None
    expiry_date: Optional[str] = None


class GrnPostIn(BaseModel):
    mvt_code: str = "101"
    po_id: int = 0
    supplier_id: int = 0
    warehouse_id: int = 0                     # 0 => company default warehouse
    dc_number: str = ""
    vehicle_num: str = ""
    qc_required: int = 0
    qc_override: int = 0                      # honoured for SUPER_ADMIN only
    category_id: Optional[int] = None         # cost-centre fields (msme_erp_cost_center_from_post)
    unit_id: Optional[int] = None
    department_id: Optional[int] = None
    ded_active: bool = False                  # ZQDR deduction - SUPER_ADMIN only, else ignored
    ded_type: str = "QUALITY"
    ded_mode: str = "AMOUNT"
    ded_value: float = 0
    ded_remarks: str = ""
    request_id: Optional[str] = None
    lines: list[GrnLineIn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /meta
# ---------------------------------------------------------------------------
@router.get("/meta", dependencies=[Depends(require_tcode(TCODE))])
def migo_meta(mvt: str = Query("101")):
    c = _ctx()
    company_id = c["company_id"]
    sa = _is_super_admin()
    notes: list[str] = []
    try:
        suppliers = master_suppliers(company_id, notes)
        warehouses = master_warehouses(company_id)
        uoms = ErpDB.fetch_all("SELECT id, code FROM units_of_measure WHERE is_active=1 ORDER BY code")
        items = master_items(company_id, notes)
        pos = open_pos(company_id) if mvt == "101" else []
    except Exception as e:  # noqa: BLE001
        log.exception("MIGO master data")
        return fail(f"Could not load master data (suppliers / warehouses / items): {e}", 500)

    return ok({
        "mvt_code": mvt,
        "mvt_labels": MVT_LABELS,
        "is_super_admin": sa,
        "currency": c["currency"],
        "currency_symbol": currency_symbol(c["currency"]),
        "suppliers": suppliers,
        "warehouses": warehouses,
        "uoms": uoms,
        "items": items,
        "open_pos": pos,
        "qc": {
            "items": _qc_item_ids(company_id),
            "all": _qc_all_receipts(),
            "admin": sa,
            "on": mvt == "101",
        },
        "diagnostics": notes,
    })


# ---------------------------------------------------------------------------
# GET /po/{po_id}   (Pull from Purchase Order)
# ---------------------------------------------------------------------------
@router.get("/po/{po_id}", dependencies=[Depends(require_tcode(TCODE))])
def migo_pull_po(po_id: int):
    company_id = _ctx()["company_id"]
    po = ErpDB.fetch_one(
        "SELECT po.*, s.name AS supplier_name, s.supplier_code FROM purchase_orders po "
        "JOIN suppliers s ON s.id=po.supplier_id WHERE po.id=? AND po.company_id=?",
        [po_id, company_id])
    if not po:
        return fail("That purchase order was not found in the active company.", 404)
    if po["status"] not in ("APPROVED", "PARTIAL"):
        return fail(f"PO {po['po_number']} is {po['status']} - goods can be received only against an "
                    f"APPROVED or PARTIAL purchase order.", 409)
    lines = _pullable_po_lines(po_id)
    notice = "" if lines else (f"PO {po['po_number']} has nothing left to receive "
                               f"(fully received or waiting in QC).")
    return ok({"po": po, "lines": lines, "notice": notice})


# ---------------------------------------------------------------------------
# GET /grn/{grn_id}
# ---------------------------------------------------------------------------
@router.get("/grn/{grn_id}", dependencies=[Depends(require_tcode(TCODE))])
def migo_view_grn(grn_id: int):
    company_id = _ctx()["company_id"]
    sa = _is_super_admin()
    grn = ErpDB.fetch_one(
        "SELECT gr.*, s.name AS supplier_name, w.name AS warehouse_name "
        "FROM goods_receipts gr JOIN suppliers s ON s.id=gr.supplier_id "
        "JOIN warehouses w ON w.id=gr.warehouse_id WHERE gr.id=? AND gr.company_id=?",
        [grn_id, company_id])
    if not grn:
        return fail("GRN not found.", 404)

    lines = ErpDB.fetch_all(
        "SELECT grl.*, i.item_code, i.item_name, u.code AS uom "
        "FROM goods_receipt_lines grl JOIN items i ON i.id=grl.item_id "
        "LEFT JOIN units_of_measure u ON u.id=grl.uom_id WHERE grl.grn_id=?", [grn_id])

    po_number = None
    if grn.get("po_id"):
        po_number = ErpDB.fetch_value("SELECT po_number FROM purchase_orders WHERE id=?", [int(grn["po_id"])])

    lots = ErpDB.fetch_all(
        "SELECT il.id, il.lot_number, il.status, il.result, il.qty, i.item_code "
        "FROM inspection_lots il JOIN goods_receipt_lines g ON g.id=il.origin_id "
        "JOIN items i ON i.id=il.item_id WHERE il.origin_type='GRN_LINE' AND g.grn_id=? ORDER BY il.id",
        [grn_id])

    # Deduction figures are read ONLY for SUPER_ADMIN; everyone else gets the payload
    # exactly as if the deduction feature did not exist.
    deductions: list[dict] = []
    deduction_total = 0.0
    if sa:
        deductions = ErpDB.fetch_all("SELECT gd.* FROM grn_deductions gd WHERE gd.grn_id=? ORDER BY gd.id", [grn_id])
        deduction_total = sum(float(d.get("deducted_amount") or 0) for d in deductions)

    data = {"grn": grn, "po_number": po_number, "lines": lines, "inspection_lots": lots}
    if sa:
        data["deductions"] = deductions
        data["deduction_total"] = deduction_total
    return ok(data)


# ---------------------------------------------------------------------------
# POST /post
# ---------------------------------------------------------------------------
@router.post("/post", dependencies=[Depends(require_tcode(TCODE))])
def migo_post(body: GrnPostIn):
    c = _ctx()
    company_id, user_id = c["company_id"], c["user_id"]
    sa = _is_super_admin()

    mvt = body.mvt_code or "101"
    if mvt not in MVT_LABELS:
        return fail(f"Unknown movement type {mvt}.", 400)

    supplier_id = int(body.supplier_id or 0)
    po_id = int(body.po_id or 0)
    warehouse_id = int(body.warehouse_id or 0)
    qc_required = 1 if body.qc_required else 0

    lines = [l.model_dump() if hasattr(l, "model_dump") else l.dict()
             for l in body.lines if l.item_id and float(l.qty_received or 0) > 0]

    # QC gate: a receipt with a QC-flagged material (or any receipt when the company-wide
    # option is on) MUST go to inspection. Only a super admin can override - and it is audited.
    qc_overridden = False
    if mvt == "101" and _qc_mandatory(company_id, [l["item_id"] for l in lines]):
        if not qc_required:
            if sa and body.qc_override:
                qc_overridden = True
            else:
                qc_required = 1

    # A GRN pulled from a PO must be for that PO's supplier; a PO line can never receive more
    # than its open balance; price always comes from the PO line, never from the client.
    po_err = ""
    if po_id:
        po = ErpDB.fetch_one(
            "SELECT id, supplier_id, po_number, status FROM purchase_orders WHERE id=? AND company_id=?",
            [po_id, company_id])
        if not po or po["status"] not in ("APPROVED", "PARTIAL"):
            po_err = "The selected purchase order is not open for receipt."
        else:
            supplier_id = int(po["supplier_id"])
            open_by_line = {
                int(x["id"]): {
                    "open": float(x["qty_ordered"] or 0) - float(x["qty_received"] or 0) - float(x["qty_in_qc"] or 0),
                    "price": float(x["unit_price"] or 0),
                    "item": int(x["item_id"]),
                } for x in po_open_lines(po_id)
            }
            for l in lines:
                plid = int(l.get("po_line_id") or 0)
                if not plid:
                    continue
                ob = open_by_line.get(plid)
                if ob is None or ob["item"] != int(l["item_id"]):
                    po_err = "A line does not belong to the selected purchase order."
                    break
                if float(l["qty_received"]) > ob["open"] + QTY_EPS:
                    po_err = (f"Received quantity exceeds the open PO balance ({_fmt_qty(ob['open'])}) "
                              f"on one of the lines.")
                    break
                l["unit_price"] = ob["price"]

    # Deduction -> ZQDR. SUPER_ADMIN only; anyone else's values are ignored outright.
    ded_active = sa and body.ded_active
    ded_type = body.ded_type if body.ded_type in DED_REASONS else "QUALITY"
    ded_mode = "PERCENT" if (body.ded_mode or "").upper() == "PERCENT" else "AMOUNT"
    ded_value = float(body.ded_value or 0)
    ded_remarks = (body.ded_remarks or "").strip()

    # Warehouse is optional: blank -> company default; a chosen one must belong to the company.
    if warehouse_id and not ErpDB.fetch_value(
            "SELECT id FROM warehouses WHERE id=? AND company_id=?", [warehouse_id, company_id]):
        warehouse_id = 0
    if not warehouse_id:
        warehouse_id = default_warehouse_id(company_id)

    if po_err:
        return fail(po_err, 400)
    if not supplier_id:
        return fail("Supplier is required.", 400)
    if not warehouse_id:
        return fail("No warehouse could be found or created for this company.", 400)
    if not lines:
        return fail("Enter at least one line with received quantity.", 400)

    if not _claim_request_id(body.request_id):
        return fail("Duplicate submission ignored - this goods movement was already posted.", 409)

    try:
        ErpDB.begin_transaction()

        grn_num = ErpDB.get_next_number(company_id, "GRN", date.today().year)

        new_grn_id = ErpDB.insert("goods_receipts", {
            "company_id": company_id,
            "branch_id": c["branch_id"],
            "grn_number": grn_num,
            "grn_date": date.today().isoformat(),
            "po_id": po_id if (po_id and mvt == "101") else None,
            "supplier_id": supplier_id,
            "warehouse_id": warehouse_id,
            "movement_type": mvt,
            "dc_number": body.dc_number,
            "vehicle_num": body.vehicle_num,
            "status": "DRAFT",
            "qc_required": qc_required,
            "created_by": user_id,
            "category_id": body.category_id or None,
            "unit_id": body.unit_id or None,
            "department_id": body.department_id or None,
        })

        grn_total_base = 0.0
        for l in lines:
            qty_recv = float(l["qty_received"])
            qty_acc = float(l["qty_accepted"]) if l.get("qty_accepted") is not None else qty_recv
            qty_acc = max(0.0, min(qty_recv, qty_acc))       # accepted can never exceed received
            qty_rej = qty_recv - qty_acc
            u_price = float(l.get("unit_price") or 0)
            uom_id = int(l["uom_id"]) if l.get("uom_id") else int(
                ErpDB.fetch_value("SELECT base_uom_id FROM items WHERE id=?", [int(l["item_id"])]) or 0)

            ErpDB.insert("goods_receipt_lines", {
                "grn_id": new_grn_id,
                "po_line_id": int(l["po_line_id"]) if l.get("po_line_id") else None,
                "item_id": int(l["item_id"]),
                "uom_id": uom_id,
                "qty_received": qty_recv,
                "qty_accepted": qty_acc,
                "qty_rejected": qty_rej,
                "unit_price": u_price,
                "batch_number": l.get("batch_number") or None,
                "expiry_date": l.get("expiry_date") or None,
            })
            grn_total_base += qty_recv * u_price

        # Deduction -> ZQDR (confidential). Percentage is worked out server-side against the
        # GRN value from PO / moving-average prices; the store user never sees a price.
        if ded_active and ded_value > 0:
            ded_amt = calc_deduction_amount(ded_mode, ded_value, grn_total_base)
            if ded_amt > 0:
                ErpDB.insert("grn_deductions", {
                    "company_id": company_id,
                    "branch_id": c["branch_id"],
                    "grn_id": new_grn_id,
                    "grn_line_id": None,
                    "supplier_id": supplier_id,
                    "deduction_type": ded_type,
                    "calc_mode": ded_mode,
                    "input_value": ded_value,
                    "base_amount": grn_total_base,
                    "deducted_amount": ded_amt,
                    "reason": DED_REASONS[ded_type],
                    "remarks": ded_remarks,
                    "created_by": user_id,
                })

        # Auto-post when no QC is required (or it is a vendor return); otherwise hold the
        # stock and raise one inspection lot per line.
        lot_nums: list[str] = []
        if not qc_required or mvt == "122":
            stock.process_grn(new_grn_id, user_id)
            status = "POSTED"
            msg = (f"Return {grn_num} posted. Stock reduced." if mvt == "122"
                   else f"GRN {grn_num} posted successfully. Stock updated.")
        else:
            ErpDB.update("goods_receipts", {"status": "PENDING_QC"}, "id=?", [new_grn_id])
            lot_nums = stock.create_grn_inspection_lots(new_grn_id, company_id)
            status = "PENDING_QC"
            msg = (f"GRN {grn_num} sent to Quality Inspection"
                   + (f" - lot{'s' if len(lot_nums) > 1 else ''} {', '.join(lot_nums)}" if lot_nums else "")
                   + ". Stock is posted when QC accepts (QA32).")

        ErpAuth.audit("goods_receipts", new_grn_id, "INSERT", {}, {"grn_number": grn_num})
        if qc_overridden:
            ErpAuth.audit("goods_receipts", new_grn_id, "QC_OVERRIDE", {},
                          {"note": "QC-mandatory material posted straight to stock by a super admin"})
        ErpDB.commit()

    except Exception as e:  # noqa: BLE001
        ErpDB.rollback()
        fe.pop_warnings()
        _release_request_id(body.request_id)      # nothing was saved - let the user retry
        log.exception("MIGO post failed")
        return fail(f"Error posting GRN: {e}", 500)

    return ok({
        "grn_id": new_grn_id,
        "grn_number": grn_num,
        "status": status,
        "inspection_lots": lot_nums,
        "warnings": fe.pop_warnings(),
    }, msg)