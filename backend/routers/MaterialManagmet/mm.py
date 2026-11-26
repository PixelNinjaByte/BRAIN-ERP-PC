"""
Brain ERP — /api/mm
Material Master router implementing MM01 (Create), MM02 (Change), and MM03 (Display).
Ported from msme-erp/modules/mm/material_master.php.

Endpoints:
  GET    /lookups                 — dropdown data (UOMs, material groups with GL, types, categories, GST slabs, block statuses)
  GET    /materials               — list / search materials with category statistics, stock totals, and status filters (MM03)
  GET    /materials/{id}          — full material details with stock, purchases, where-used, alt units, and audit history (MM03 detail)
  POST   /materials               — create material (MM01), gated on MM01
  PUT    /materials/{id}          — update material (MM02), locks code & base UOM if used, records audit diff, gated on MM02
  DELETE /materials/{id}          — safe delete material if unused, frees item code with ~DEL{id}, gated on MM02
  POST   /materials/{id}/uom      — add or update alternate UOM conversion factor (MM02)
  DELETE /materials/{id}/uom/{c}  — remove alternate UOM conversion factor (MM02)
"""

from __future__ import annotations

import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.db import ErpDB
from core import material
from ..deps import require_login, require_tcode, ok, fail

router = APIRouter()


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class MaterialCreateRequest(BaseModel):
    item_code: str = ""                     # blank -> auto-numbered MAT000001...
    item_name: str = Field(min_length=1)
    description: str = ""
    item_type: str = "MATERIAL"
    item_category: str = "RAW"
    base_uom_id: int
    material_group_id: Optional[int] = None
    hsn_code: str = ""
    gst_rate: float = 0.0
    standard_price: float = 0.0
    moving_avg_price: float = 0.0
    min_stock: float = 0.0
    max_stock: float = 0.0
    reorder_point: float = 0.0
    lead_time_days: int = 0
    lot_size: float = 1.0
    qc_required: bool = False
    batch_managed: bool = False
    shelf_life_days: int = 0
    gross_weight: float = 0.0
    manufacturer: str = ""
    block_status: str = ""
    is_active: bool = True
    confirm_dup: bool = False


class MaterialUpdateRequest(BaseModel):
    item_name: str = Field(min_length=1)
    description: str = ""
    item_type: str = "MATERIAL"
    item_category: str = "RAW"
    base_uom_id: Optional[int] = None
    material_group_id: Optional[int] = None
    hsn_code: str = ""
    gst_rate: float = 0.0
    standard_price: float = 0.0
    moving_avg_price: float = 0.0
    min_stock: float = 0.0
    max_stock: float = 0.0
    reorder_point: float = 0.0
    lead_time_days: int = 0
    lot_size: float = 1.0
    qc_required: bool = False
    batch_managed: bool = False
    shelf_life_days: int = 0
    gross_weight: float = 0.0
    manufacturer: str = ""
    block_status: str = ""
    is_active: bool = True
    confirm_dup: bool = False


class UomConversionRequest(BaseModel):
    alt_uom_id: int
    factor: float = Field(gt=0, description="1 alternate unit = factor base units")


# ---------------------------------------------------------------------------
# Audit Log Helper
# ---------------------------------------------------------------------------

def _log_audit(
    company_id: int,
    table_name: str,
    record_id: int,
    action: str,
    old_data: Optional[dict],
    new_data: Optional[dict],
    user: dict,
) -> None:
    try:
        ErpDB.insert(
            "audit_logs",
            {
                "company_id": company_id,
                "table_name": table_name,
                "record_id": record_id,
                "action": action,
                "old_data": json.dumps(old_data) if old_data else None,
                "new_data": json.dumps(new_data) if new_data else None,
                "user_id": user.get("user_id"),
                "username": user.get("username") or user.get("full_name") or "system",
            },
        )
    except Exception:
        pass  # non-fatal


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/lookups")
def lookups(_user=Depends(require_login)):
    """Dropdown data for MM01/MM02/MM03 forms."""
    company_id = _user["company_id"]
    uoms = ErpDB.fetch_all(
        "SELECT id, code, name FROM units_of_measure WHERE COALESCE(is_active, 1)=1 ORDER BY code"
    )
    groups = ErpDB.fetch_all(
        """SELECT g.id, g.code, g.name, a.account_code, a.account_name
           FROM material_groups g
           LEFT JOIN chart_of_accounts a ON a.id = g.inventory_account_id
           WHERE g.company_id=? AND COALESCE(g.is_active, 1)=1
           ORDER BY g.name""",
        [company_id],
    )
    return ok({
        "uoms": uoms,
        "material_groups": groups,
        "item_types": material.mat_types(),
        "item_categories": material.mat_categories(),
        "gst_rates": material.mat_gst_rates(),
        "block_statuses": material.mat_blocks(),
    })


@router.get("/materials")
def list_materials(
    search: Optional[str] = None,
    category: Optional[str] = None,
    item_type: Optional[str] = None,
    status: Optional[str] = "active",
    sort: str = "code",
    dir: str = "asc",
    user=Depends(require_login),
):
    """
    List materials with search, filtering, sorting, and category KPI counts.
    Mirrors MM03 list display in msme-erp.
    """
    company_id = user["company_id"]
    stock_subquery = "(SELECT COALESCE(SUM(sb.qty_on_hand), 0) FROM stock_balances sb WHERE sb.item_id = i.id)"

    sql = f"""
        SELECT i.id, i.item_code, i.item_name, i.item_type, i.item_category,
               i.hsn_code, i.gst_rate, i.standard_price, i.moving_avg_price,
               i.min_stock, i.reorder_point, i.lead_time_days, i.is_active,
               COALESCE(i.block_status, '') AS block_status,
               u.code AS uom_code, u.name AS uom_name,
               g.name AS group_name, g.code AS group_code,
               {stock_subquery} AS stock_total
        FROM items i
        LEFT JOIN units_of_measure u ON u.id = i.base_uom_id
        LEFT JOIN material_groups g ON g.id = i.material_group_id
        WHERE i.company_id = ? AND i.is_deleted = 0
    """
    params: list[Any] = [company_id]

    if category:
        sql += " AND i.item_category = ?"
        params.append(category)

    if item_type:
        sql += " AND i.item_type = ?"
        params.append(item_type)

    if search:
        like = f"%{search.strip()}%"
        sql += " AND (i.item_code LIKE ? OR i.item_name LIKE ? OR i.hsn_code LIKE ? OR i.manufacturer LIKE ?)"
        params.extend([like, like, like, like])

    st = (status or "active").lower()
    if st == "active":
        sql += " AND i.is_active = 1"
    elif st == "inactive":
        sql += " AND i.is_active = 0"
    elif st == "blocked":
        sql += " AND COALESCE(i.block_status, '') <> ''"
    elif st == "low":
        sql += f" AND i.is_active = 1 AND i.reorder_point > 0 AND {stock_subquery} <= i.reorder_point"

    # Sorting
    direction = "DESC" if dir.lower() == "desc" else "ASC"
    sort_cols = {
        "code": "i.item_code",
        "name": "i.item_name",
        "cat": "i.item_category",
        "gst": "i.gst_rate",
        "price": "i.standard_price",
        "map": "i.moving_avg_price",
        "stock": stock_subquery,
    }
    order_col = sort_cols.get(sort.lower(), "i.item_code")
    sql += f" ORDER BY {order_col} {direction}, i.id DESC LIMIT 500"

    rows = ErpDB.fetch_all(sql, params)

    # Category and status statistics
    cat_rows = ErpDB.fetch_all(
        "SELECT item_category, COUNT(*) AS cnt FROM items WHERE company_id=? AND is_deleted=0 AND is_active=1 GROUP BY item_category",
        [company_id],
    )
    stat_map = {r["item_category"]: r["cnt"] for r in cat_rows}
    n_inactive = ErpDB.fetch_value(
        "SELECT COUNT(*) FROM items WHERE company_id=? AND is_deleted=0 AND is_active=0",
        [company_id],
    ) or 0
    n_blocked = ErpDB.fetch_value(
        "SELECT COUNT(*) FROM items WHERE company_id=? AND is_deleted=0 AND COALESCE(block_status, '') <> ''",
        [company_id],
    ) or 0
    total_active = sum(stat_map.values())

    stats = {
        "categories": stat_map,
        "total_active": total_active,
        "n_inactive": n_inactive,
        "n_blocked": n_blocked,
    }

    return ok({"rows": rows, "stats": stats})


@router.get("/materials/{item_id}")
def get_material_detail(item_id: int, user=Depends(require_login)):
    """
    Detailed material view for MM03 detail panel:
    Returns item data, stock by warehouse, recent purchases, where-used in BOM/PR/PO/SO,
    alternate UOMs, and change history.
    """
    company_id = user["company_id"]
    item = ErpDB.fetch_one(
        """SELECT i.*, u.code AS uom_code, u.name AS uom_name,
                  mg.name AS mg_name, mg.code AS mg_code,
                  a.account_code, a.account_name
           FROM items i
           LEFT JOIN units_of_measure u ON u.id = i.base_uom_id
           LEFT JOIN material_groups mg ON mg.id = i.material_group_id
           LEFT JOIN chart_of_accounts a ON a.id = mg.inventory_account_id
           WHERE i.id = ? AND i.company_id = ? AND i.is_deleted = 0""",
        [item_id, company_id],
    )
    if not item:
        fail("Material not found.", status_code=404)

    # 1. Stock by warehouse
    stock_rows = ErpDB.fetch_all(
        """SELECT sb.qty_on_hand, w.code AS warehouse_code, w.name AS warehouse_name
           FROM stock_balances sb
           JOIN warehouses w ON w.id = sb.warehouse_id
           WHERE sb.item_id = ? AND sb.company_id = ? AND sb.qty_on_hand > 0.0001
           ORDER BY w.code""",
        [item_id, company_id],
    )
    stock_total = material.stock_total(item_id)

    # 2. Recent purchases
    pos = ErpDB.fetch_all(
        """SELECT po.id, po.po_number, po.po_date,
                  COALESCE(s.name, 'Supplier') AS supplier,
                  pol.qty_ordered, pol.unit_price
           FROM purchase_order_lines pol
           JOIN purchase_orders po ON po.id = pol.po_id
           LEFT JOIN suppliers s ON s.id = po.supplier_id
           WHERE pol.item_id = ? AND po.company_id = ?
           ORDER BY po.id DESC LIMIT 10""",
        [item_id, company_id],
    )

    # 3. Where-used
    # BOMs where this item is a component
    boms = ErpDB.fetch_all(
        """SELECT bh.id, bh.bom_number, i2.item_code, i2.item_name
           FROM bom_lines bl
           JOIN bom_headers bh ON bh.id = bl.bom_id
           JOIN items i2 ON i2.id = bh.item_id
           WHERE bl.component_id = ? AND bh.company_id = ?
           ORDER BY bh.bom_number LIMIT 20""",
        [item_id, company_id],
    )

    # Open PRs
    open_prs = ErpDB.fetch_all(
        """SELECT pr.id, pr.pr_number, prl.qty_requested
           FROM purchase_requisition_lines prl
           JOIN purchase_requisitions pr ON pr.id = prl.pr_id
           WHERE prl.item_id = ? AND pr.company_id = ? AND pr.status IN ('PENDING', 'APPROVED')
           ORDER BY pr.id DESC LIMIT 10""",
        [item_id, company_id],
    )

    # Open POs (to receive)
    open_pos = ErpDB.fetch_all(
        """SELECT po.id, po.po_number, (pol.qty_ordered - COALESCE(pol.qty_received, 0)) AS open_qty
           FROM purchase_order_lines pol
           JOIN purchase_orders po ON po.id = pol.po_id
           WHERE pol.item_id = ? AND po.company_id = ? AND (pol.qty_ordered - COALESCE(pol.qty_received, 0)) > 0.0001
           ORDER BY po.id DESC LIMIT 10""",
        [item_id, company_id],
    )

    # Open SOs (to deliver)
    open_sos = ErpDB.fetch_all(
        """SELECT so.id, so.so_number, (sol.qty_ordered - COALESCE(sol.qty_delivered, 0)) AS open_qty
           FROM sales_order_lines sol
           JOIN sales_orders so ON so.id = sol.so_id
           WHERE sol.item_id = ? AND so.company_id = ? AND (sol.qty_ordered - COALESCE(sol.qty_delivered, 0)) > 0.0001
           ORDER BY so.id DESC LIMIT 10""",
        [item_id, company_id],
    )

    usage_counts = material.usage(item_id, company_id)
    uom_locked = (usage_counts.get("moves", 0) > 0 or abs(stock_total) > 0.0005)

    # 4. Alternate units of measure
    alts = ErpDB.fetch_all(
        """SELECT c.id, c.alt_uom_id, c.factor, u.code, u.name
           FROM item_uom_conversions c
           JOIN units_of_measure u ON u.id = c.alt_uom_id
           WHERE c.item_id = ?
           ORDER BY u.code""",
        [item_id],
    )

    # 5. Audit history
    hist = ErpDB.fetch_all(
        """SELECT created_at, username, action, old_data, new_data
           FROM audit_logs
           WHERE table_name = 'items' AND record_id = ?
           ORDER BY id DESC LIMIT 50""",
        [item_id],
    )

    return ok({
        "item": item,
        "stock_total": stock_total,
        "stock": stock_rows,
        "pos": pos,
        "boms": boms,
        "open_prs": open_prs,
        "open_pos": open_pos,
        "open_sos": open_sos,
        "usage": usage_counts,
        "uom_locked": uom_locked,
        "alts": alts,
        "history": hist,
    })


@router.post("/materials", dependencies=[Depends(require_tcode("MM01"))])
def create_material(body: MaterialCreateRequest, user=Depends(require_login)):
    """MM01: Create material master."""
    company_id = user["company_id"]
    data = body.model_dump()
    clean_data, errors = material.clean(data, company_id)
    if errors:
        fail(" ".join(errors), status_code=400)

    similar = clean_data.pop("_similar", None)
    if similar and not body.confirm_dup:
        return ok(
            {"needs_confirmation": True, "similar": similar},
            message="A very similar material already exists — confirm duplicate to proceed anyway.",
        )

    clean_data["company_id"] = company_id
    clean_data["created_by"] = user["user_id"]
    clean_data["is_deleted"] = 0

    item_id = ErpDB.insert("items", clean_data)
    row = ErpDB.fetch_one("SELECT * FROM items WHERE id=?", [item_id])

    _log_audit(
        company_id,
        "items",
        item_id,
        "INSERT",
        None,
        {"item_code": row["item_code"], "item_name": row["item_name"]},
        user,
    )

    return ok(row, message=f"Material {row['item_code']} created successfully.")


@router.put("/materials/{item_id}", dependencies=[Depends(require_tcode("MM02"))])
def update_material(item_id: int, body: MaterialUpdateRequest, user=Depends(require_login)):
    """MM02: Change material master."""
    company_id = user["company_id"]
    existing = ErpDB.fetch_one("SELECT * FROM items WHERE id=? AND company_id=? AND is_deleted=0", [item_id, company_id])
    if not existing:
        fail("Material not found.", status_code=404)

    usage_data = material.usage(item_id, company_id)
    stock_tot = material.stock_total(item_id)
    uom_locked = (usage_data.get("moves", 0) > 0 or abs(stock_tot) > 0.0005)

    data = body.model_dump()
    # Code is immutable on MM02
    data["item_code"] = existing["item_code"]
    if uom_locked:
        data["base_uom_id"] = existing["base_uom_id"]

    clean_data, errors = material.clean(data, company_id, existing_id=item_id)
    if errors:
        fail(" ".join(errors), status_code=400)

    clean_data.pop("_similar", None)

    # Compute diff for audit log
    diff = material.mat_diff(existing, clean_data)
    if diff:
        old_vals = {k: v[0] for k, v in diff.items()}
        new_vals = {k: v[1] for k, v in diff.items()}
        _log_audit(company_id, "items", item_id, "UPDATE", old_vals, new_vals, user)

    # Perform DB update
    set_clauses = [f"{k} = ?" for k in clean_data.keys()]
    values = list(clean_data.values()) + [item_id, company_id]
    ErpDB.query(f"UPDATE items SET {', '.join(set_clauses)} WHERE id = ? AND company_id = ?", values)

    updated = ErpDB.fetch_one("SELECT * FROM items WHERE id=?", [item_id])
    diff_count = len(diff)
    msg = f"Material {existing['item_code']} updated ({diff_count} field(s) changed)." if diff else "No changes to save."
    return ok(updated, message=msg)


@router.delete("/materials/{item_id}", dependencies=[Depends(require_tcode("MM02"))])
def delete_material(item_id: int, user=Depends(require_login)):
    """MM02: Delete material (only permitted if never used in any transactions or movements)."""
    company_id = user["company_id"]
    item = ErpDB.fetch_one("SELECT * FROM items WHERE id=? AND company_id=? AND is_deleted=0", [item_id, company_id])
    if not item:
        fail("Material not found.", status_code=404)

    usage_data = material.usage(item_id, company_id)
    stock_tot = material.stock_total(item_id)
    used = (sum(usage_data.values()) > 0 or abs(stock_tot) > 0.0005)

    if used:
        fail(
            f"Cannot delete material: used in {usage_data.get('po', 0)} PO, "
            f"{usage_data.get('pr', 0)} PR, {usage_data.get('so', 0)} SO, "
            f"{usage_data.get('bom', 0)} BOM, {usage_data.get('moves', 0)} stock movements. "
            "Deactivate or block it instead.",
            status_code=400,
        )

    # Free the code for reuse
    freed_code = f"{item['item_code'][:38]}~DEL{item_id}"
    ErpDB.query(
        "UPDATE items SET is_deleted=1, is_active=0, item_code=? WHERE id=? AND company_id=?",
        [freed_code, item_id, company_id],
    )

    _log_audit(
        company_id,
        "items",
        item_id,
        "DELETE",
        {"item_code": item["item_code"], "item_name": item["item_name"]},
        {"status": "deleted"},
        user,
    )

    return ok({"deleted_id": item_id}, message=f"Material {item['item_code']} deleted successfully.")


# ---------------------------------------------------------------------------
# Alternate Units of Measure Endpoints
# ---------------------------------------------------------------------------

@router.post("/materials/{item_id}/uom", dependencies=[Depends(require_tcode("MM02"))])
def add_alternate_uom(item_id: int, body: UomConversionRequest, user=Depends(require_login)):
    """Add or update an alternate unit conversion factor (e.g. 1 BOX = 24 PCS)."""
    company_id = user["company_id"]
    item = ErpDB.fetch_one("SELECT id, base_uom_id FROM items WHERE id=? AND company_id=? AND is_deleted=0", [item_id, company_id])
    if not item:
        fail("Material not found.", status_code=404)

    if body.alt_uom_id == item["base_uom_id"]:
        fail("Alternate unit must be different from the base unit.", status_code=400)

    if not ErpDB.fetch_value("SELECT id FROM units_of_measure WHERE id=?", [body.alt_uom_id]):
        fail("Unknown unit of measure.", status_code=400)

    existing_conv_id = ErpDB.fetch_value(
        "SELECT id FROM item_uom_conversions WHERE item_id=? AND alt_uom_id=?",
        [item_id, body.alt_uom_id],
    )

    if existing_conv_id:
        ErpDB.query(
            "UPDATE item_uom_conversions SET factor=? WHERE id=? AND item_id=?",
            [body.factor, existing_conv_id, item_id],
        )
    else:
        ErpDB.insert(
            "item_uom_conversions",
            {
                "company_id": company_id,
                "item_id": item_id,
                "alt_uom_id": body.alt_uom_id,
                "factor": body.factor,
            },
        )

    _log_audit(
        company_id,
        "items",
        item_id,
        "UPDATE",
        {},
        {"alt_uom_id": body.alt_uom_id, "factor": body.factor},
        user,
    )

    return ok({}, message="Alternate unit saved successfully.")


@router.delete("/materials/{item_id}/uom/{conv_id}", dependencies=[Depends(require_tcode("MM02"))])
def delete_alternate_uom(item_id: int, conv_id: int, user=Depends(require_login)):
    """Remove an alternate unit conversion."""
    company_id = user["company_id"]
    ErpDB.query("DELETE FROM item_uom_conversions WHERE id=? AND item_id=?", [conv_id, item_id])
    _log_audit(
        company_id,
        "items",
        item_id,
        "UPDATE",
        {"removed_conv_id": conv_id},
        {},
        user,
    )
    return ok({}, message="Alternate unit removed.")
