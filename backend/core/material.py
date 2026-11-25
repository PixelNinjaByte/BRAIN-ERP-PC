"""
Brain ERP — Material Master Engine
Direct port of PHP core/material.php (v1.17.1) -> Python.

Shared by MM01/MM02/MM03 (material CRUD), OMSF (material groups), ZMMI
(import/export) and ZMRP (reorder report):
  - one validation routine used by the form AND the CSV import (same
    rules everywhere) — clean()
  - item-level weighted moving average price across all warehouses —
    refresh_map()
  - inventory GL account determination by material group —
    inventory_account_for_item() (this is what finance_engine.py's
    post_grn() imports once this file lands — see finance_engine.py's
    module docstring)
  - block status (procurement / sales / all) picker filter
  - similar-description detection, usage (where-used) lookups, change diff
  - reorder -> purchase requisition (ZMRP)

NOT PORTED — flagged instead of translated wholesale, same as app.py's
render_layout and finance_engine.py's ensure_schema():
  - msme_erp_mat_ensure_schema() — raw ALTER TABLE DDL (MySQL
    information_schema introspection) that adds items.block_status /
    items.manufacturer, material_groups.inventory_account_id /
    .description, and the item_uom_conversions table. This port's schema
    lives in schema.sql; item_uom_conversions is already in db.py's
    KNOWN_TABLES, so schema.sql must also carry those two items.* columns
    and the two material_groups.* columns for this module's queries to
    return real data (ErpDB.insert()/update() silently drop unknown
    columns rather than error — see db.py _filter_to_columns — so this
    degrades quietly if schema.sql is behind).
  - msme_erp_mat_make_unique() — one-time migration that promotes
    item_code/material_groups.code to a per-company UNIQUE key and drops
    the old global-unique key. A schema-migration concern for whoever
    maintains schema.sql, not runtime business logic.
  - The one-time "moving_avg_price was overwritten by the last
    warehouse's average" backfill (msme_erp_mat_map_fixed option guard in
    ensure_schema). A single historical data-repair step, not something
    to run on every call; if a fresh install ever needs it, loop
    refresh_map() over every item_id with stock once, from installer.py.

msme_erp_has_col() (checks whether a column exists before using it in a
query) is reimplemented locally as has_col() using ErpDB.get_columns(),
which already exists for exactly this purpose (see documents.py's
doc_ensure_col()).

similar_text() (PHP's longest-common-substring % similarity) has no
stdlib equivalent; find_similar() below uses difflib.SequenceMatcher's
ratio instead. Both are heuristics for "did someone already create this
material", not a figure anything is posted or reconciled against, so the
slightly different scoring algorithm is not a functional regression.

DEPENDENCY NOTE: create_pr() calls into two not-yet-ported helpers —
msme_erp_employee_cost_center() (the logged-in user's default cost
center) and msme_erp_get_pr_release_strategy() (approval routing for
purchase requisitions). The PHP used function_exists() guards so a
missing helper degrades gracefully instead of failing; this port keeps
that behaviour with a local lazy import + fallback (no cost center / no
release routing) until those modules exist.

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import re
import logging
import difflib
from datetime import date
from html.parser import HTMLParser
from typing import Optional

from core.db import ErpDB
from core.auth import ErpAuth

logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================
def mat_types() -> dict:
    return {"MATERIAL": "Material", "SERVICE": "Service", "ASSET": "Asset", "TRADING": "Trading Item"}


def mat_categories() -> dict:
    return {"RAW": "Raw Material", "WIP": "Work In Progress", "FG": "Finished Goods",
            "CONSUMABLE": "Consumable", "SPARE": "Spare Part", "TRADING": "Trading"}


def mat_blocks() -> dict:
    return {"": "Not blocked", "PROCUREMENT": "Blocked for procurement",
            "SALES": "Blocked for sales", "ALL": "Blocked for procurement and sales"}


def mat_gst_rates() -> list:
    """GST slabs offered on the form. Legacy 12 / 28 stay so existing items still validate."""
    return [0, 0.25, 1.5, 3, 5, 12, 18, 28, 40]


MAT_CSV_COLUMNS = [
    "item_code", "item_name", "description", "item_type", "item_category", "uom_code", "group_code",
    "hsn_code", "gst_rate", "standard_price", "min_stock", "max_stock", "reorder_point", "lead_time_days",
    "lot_size", "qc_required", "batch_managed", "shelf_life_days", "gross_weight", "manufacturer",
    "block_status", "is_active",
]


def has_col(table: str, col: str) -> bool:
    return col in ErpDB.get_columns(table)


# ============================================================
# VALUATION — item-level moving average across all warehouses
# ============================================================
def refresh_map(item_id: int) -> None:
    """items.moving_avg_price = value of all positive stock / quantity of all positive stock. Unchanged when nothing is in stock."""
    r = ErpDB.fetch_one(
        "SELECT SUM(qty_on_hand) AS q, SUM(qty_on_hand*avg_cost) AS v FROM stock_balances WHERE item_id=? AND qty_on_hand>0",
        (item_id,),
    )
    q = float((r or {}).get("q") or 0)
    if q > 0.0005:
        ErpDB.update("items", {"moving_avg_price": round(float(r["v"]) / q, 4)}, "id=?", (item_id,))


# ============================================================
# BLOCK STATUS — SQL fragment for pickers
# ============================================================
def item_block_sql(side: str, alias: str = "i") -> str:
    """side: 'PURCHASE' or 'SALES'. Returns " AND ..." (or '' when the column is not there yet)."""
    if not has_col("items", "block_status"):
        return ""
    bad = "'SALES','ALL'" if side == "SALES" else "'PROCUREMENT','ALL'"
    a = re.sub(r"[^A-Za-z0-9_]", "", alias)
    return f" AND COALESCE({a}.block_status,'') NOT IN ({bad})"


# ============================================================
# GL — inventory account by material group
# ============================================================
_inv_acct_cache: dict = {}


def inventory_account_for_item(item_id: int, company_id: int) -> int:
    """Inventory GL account for an item: its material group's account, else the company's INVENTORY determination."""
    k = f"{company_id}:{item_id}"
    if k in _inv_acct_cache:
        return _inv_acct_cache[k]
    acc = 0
    if has_col("material_groups", "inventory_account_id"):
        acc = ErpDB.fetch_value(
            "SELECT c.id FROM items i JOIN material_groups g ON g.id=i.material_group_id "
            "JOIN chart_of_accounts c ON c.id=g.inventory_account_id AND c.company_id=? AND COALESCE(c.is_active,1)=1 "
            "WHERE i.id=?", (company_id, item_id),
        )
    if not acc:
        from core.finance_engine import acct as fin_acct
        acc = fin_acct("INVENTORY", company_id)
    _inv_acct_cache[k] = int(acc)
    return int(acc)


# ============================================================
# CODES
# ============================================================
def next_code(company_id: int) -> str:
    """Next free automatic material number: MAT000001, MAT000002 ... (per company, never reused)."""
    rows = ErpDB.fetch_all(
        "SELECT item_code FROM items WHERE company_id=? AND item_code GLOB 'MAT[0-9]*'", (company_id,)
    )
    max_n = 0
    for r in rows:
        tail = r["item_code"][3:]
        if tail.isdigit():
            max_n = max(max_n, int(tail))
    while True:
        max_n += 1
        code = f"MAT{max_n:06d}"
        if not ErpDB.fetch_value("SELECT COUNT(*) FROM items WHERE company_id=? AND item_code=?", (company_id, code)):
            return code


# ============================================================
# VALIDATION — one routine for the form and the CSV import
# ============================================================
class _TagStripper(HTMLParser):
    """Minimal stand-in for WordPress's sanitize_text_field()/sanitize_textarea_field() tag-stripping."""
    def __init__(self):
        super().__init__()
        self.parts: list = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _strip_tags(s: str) -> str:
    p = _TagStripper()
    p.feed(s)
    return p.text()


def _clean_text(value: str, collapse_ws: bool = True) -> str:
    s = _strip_tags(str(value or ""))
    if collapse_ws:
        s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean(data: dict, company_id: int, existing_id: Optional[int] = None) -> tuple:
    """
    data:        raw input (form row or CSV row; CSV may carry uom_code / group_code instead of ids)
    existing_id: item being changed (code is then immutable and duplicate checks skip it)
    Returns (clean_data, errors). clean_data is only safe to save when errors is empty.
    """
    err: list = []
    d: dict = {}

    def s(key: str, max_len: int = 255) -> str:
        return _clean_text(data.get(key, ""))[:max_len]

    def n(key: str, default: float = 0.0) -> float:
        v = data.get(key)
        try:
            return float(v) if v is not None and v != "" else default
        except (TypeError, ValueError):
            return default

    # code
    if existing_id is None:
        code = s("item_code", 50).upper()
        if code == "":
            code = next_code(company_id)
        if not re.match(r"^[A-Z0-9][A-Z0-9._\-/]{0,49}$", code):
            err.append(f"Item code '{code}' may only contain letters, digits and . _ - / (max 50, no spaces).")
        elif ErpDB.fetch_value("SELECT COUNT(*) FROM items WHERE company_id=? AND item_code=?", (company_id, code)):
            err.append(f"Item code '{code}' already exists.")
        d["item_code"] = code

    # names
    d["item_name"] = s("item_name", 200)
    if d["item_name"] == "":
        err.append("Material description is required.")
    d["description"] = _clean_text(data.get("description", ""), collapse_ws=False)[:2000]
    d["manufacturer"] = s("manufacturer", 100)

    # type / category
    d["item_type"] = (s("item_type", 20).upper() or "MATERIAL")
    if d["item_type"] not in mat_types():
        err.append("Invalid material type.")
    d["item_category"] = (s("item_category", 20).upper() or "RAW")
    if d["item_category"] not in mat_categories():
        err.append("Invalid category.")

    # UOM (id, or code from CSV)
    uom = int(data.get("base_uom_id") or 0)
    if not uom and data.get("uom_code"):
        uom = int(ErpDB.fetch_value(
            "SELECT id FROM units_of_measure WHERE UPPER(code)=? AND COALESCE(is_active,1)=1 LIMIT 1",
            (str(data["uom_code"]).strip().upper(),),
        ) or 0)
    if not uom or not ErpDB.fetch_value("SELECT id FROM units_of_measure WHERE id=?", (uom,)):
        err.append("A valid Base UOM is required.")
    d["base_uom_id"] = uom

    # group (id, or code from CSV) — must belong to this company
    gid = int(data.get("material_group_id") or 0)
    if not gid and data.get("group_code"):
        gid = int(ErpDB.fetch_value(
            "SELECT id FROM material_groups WHERE company_id=? AND UPPER(code)=? LIMIT 1",
            (company_id, str(data["group_code"]).strip().upper()),
        ) or 0)
        if not gid:
            err.append(f"Material group '{data['group_code']}' does not exist (create it in OMSF).")
    if gid and not ErpDB.fetch_value("SELECT id FROM material_groups WHERE id=? AND company_id=?", (gid, company_id)):
        err.append("Material group not found.")
        gid = 0
    d["material_group_id"] = gid or None

    # tax
    hsn = re.sub(r"\s+", "", s("hsn_code", 8))
    if hsn and not re.match(r"^(\d{4}|\d{6}|\d{8})$", hsn):
        err.append("HSN / SAC must be 4, 6 or 8 digits.")
    d["hsn_code"] = hsn
    gst = n("gst_rate", 0.0)
    if round(gst, 2) not in [round(x, 2) for x in mat_gst_rates()]:
        err.append("GST rate must be one of " + "%, ".join(str(x) for x in mat_gst_rates()) + "%.")
    d["gst_rate"] = gst

    # prices and planning
    for key in ("standard_price", "min_stock", "max_stock", "reorder_point", "gross_weight"):
        v = n(key, 0.0)
        if v < 0:
            err.append(key.replace("_", " ").title() + " cannot be negative.")
            v = 0.0
        d[key] = v
    if d["max_stock"] > 0 and d["max_stock"] < d["min_stock"]:
        err.append("Max stock cannot be below Min stock.")
    d["lot_size"] = n("lot_size", 1.0)
    if d["lot_size"] <= 0:
        err.append("Lot size must be greater than zero.")
        d["lot_size"] = 1.0
    d["lead_time_days"] = max(0, int(n("lead_time_days")))
    d["shelf_life_days"] = max(0, int(n("shelf_life_days")))
    d["qc_required"] = 1 if (data.get("qc_required") and str(data["qc_required"]) != "0") else 0
    d["batch_managed"] = 1 if (data.get("batch_managed") and str(data["batch_managed"]) != "0") else 0
    if d["shelf_life_days"] > 0 and not d["batch_managed"]:
        d["batch_managed"] = 1  # expiry needs a batch

    # status
    d["block_status"] = s("block_status", 12).upper()
    if d["block_status"] not in mat_blocks():
        err.append("Invalid block status.")
        d["block_status"] = ""
    is_active_raw = data.get("is_active")
    d["is_active"] = 1 if (is_active_raw is None or (str(is_active_raw) != "0" and is_active_raw != "")) else 0

    # valuation is always moving average; legacy values on old rows are left untouched.
    # opening MAP only on creation, and only when no stock exists yet
    if existing_id is None:
        mv = data.get("moving_avg_price")
        try:
            d["moving_avg_price"] = float(mv) if mv is not None and mv != "" and float(mv) >= 0 else d["standard_price"]
        except (TypeError, ValueError):
            d["moving_avg_price"] = d["standard_price"]

    # deactivating while stock exists would hide a live balance
    if existing_id is not None and not d["is_active"] and stock_total(existing_id) > 0.0005:
        err.append("This material still has stock on hand — issue or transfer it out before deactivating.")

    # duplicate description (caller may surface this as a warning; pass confirm_dup=True to skip the check)
    similar = None
    if not err and not data.get("confirm_dup"):
        sim = find_similar(d["item_name"], company_id, existing_id)
        if sim:
            similar = sim
    if similar is not None:
        d["_similar"] = similar  # advisory only — not a column, caller pops it before saving

    return d, err


# ============================================================
# LOOKUPS
# ============================================================
def stock_total(item_id: int) -> float:
    return float(ErpDB.fetch_value("SELECT COALESCE(SUM(qty_on_hand),0) FROM stock_balances WHERE item_id=?", (item_id,)) or 0)


def find_similar(name: str, company_id: int, exclude_id: Optional[int] = None, limit: int = 5) -> list:
    """Existing materials in this company whose description is the same or very close."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    target = norm(name)
    if target == "":
        return []
    out = []
    rows = ErpDB.fetch_all(
        "SELECT id, item_code, item_name FROM items WHERE company_id=? AND is_deleted=0 AND id<>? LIMIT 5000",
        (company_id, exclude_id or 0),
    )
    for r in rows:
        candidate = norm(r["item_name"])
        if candidate == "":
            continue
        if candidate == target:
            out.append({**r, "score": 100})
            continue
        if abs(len(candidate) - len(target)) > 6:
            continue
        pct = difflib.SequenceMatcher(None, target, candidate).ratio() * 100
        if pct >= 90:
            out.append({**r, "score": round(pct)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def usage(item_id: int, company_id: int) -> dict:
    """Where a material is used. Counts drive the delete guard; lists feed the MM03 'Where used' tab."""
    def c(sql: str) -> int:
        return int(ErpDB.fetch_value(sql, (item_id, company_id)) or 0)

    return {
        "po": c("SELECT COUNT(*) FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.po_id WHERE l.item_id=? AND o.company_id=?"),
        "pr": c("SELECT COUNT(*) FROM purchase_requisition_lines l JOIN purchase_requisitions r ON r.id=l.pr_id WHERE l.item_id=? AND r.company_id=?"),
        "grn": c("SELECT COUNT(*) FROM goods_receipt_lines l JOIN goods_receipts g ON g.id=l.grn_id WHERE l.item_id=? AND g.company_id=?"),
        "so": c("SELECT COUNT(*) FROM sales_order_lines l JOIN sales_orders s ON s.id=l.so_id WHERE l.item_id=? AND s.company_id=?"),
        "bom": c("SELECT COUNT(*) FROM bom_lines l JOIN bom_headers h ON h.id=l.bom_id WHERE l.component_id=? AND h.company_id=?")
               + c("SELECT COUNT(*) FROM bom_headers WHERE item_id=? AND company_id=?"),
        "moves": c("SELECT COUNT(*) FROM stock_movements WHERE item_id=? AND company_id=?"),
    }


def to_base_qty(item_id: int, uom_id: int, qty: float) -> float:
    """Base-unit quantity for a quantity entered in an alternate unit (returns qty unchanged for the base unit)."""
    base = int(ErpDB.fetch_value("SELECT base_uom_id FROM items WHERE id=?", (item_id,)) or 0)
    if not uom_id or uom_id == base:
        return qty
    factor = float(ErpDB.fetch_value(
        "SELECT factor FROM item_uom_conversions WHERE item_id=? AND alt_uom_id=?", (item_id, uom_id)
    ) or 0)
    return round(qty * factor, 6) if factor > 0 else qty


def mat_diff(old: dict, new: dict) -> dict:
    """Changed fields only: {field: [old, new]}."""
    out = {}
    for k, v in new.items():
        if k not in old:
            continue
        o = old[k]
        is_num = isinstance(o, (int, float)) and isinstance(v, (int, float))
        changed = abs(float(o) - float(v)) > 0.00001 if is_num else str(o) != str(v)
        if changed:
            out[k] = [o, v]
    return out


# ============================================================
# REORDER -> PURCHASE REQUISITION (ZMRP)
# ============================================================
def create_pr(company_id: int, lines: dict) -> dict:
    """
    One PR for the given materials {item_id: qty} and sends it to release (same routing as the
    work-order auto-PR). Returns {ok, message, pr_id?, pr_number?, lines?}.
    """
    clean_lines = {int(k): float(v) for k, v in lines.items() if float(v or 0) > 0}
    if not clean_lines:
        return {"ok": False, "message": "Nothing selected."}

    try:
        user_id = ErpAuth.user_id()
        ErpDB.begin_transaction()
        pr_number = ErpDB.get_next_number(company_id, "PR", date.today().year)

        # msme_erp_employee_cost_center() isn't ported yet — degrade to no cost center, same as
        # PHP's function_exists() guard.
        cc = {"category_id": None, "unit_id": None, "department_id": None}
        try:
            from core.app import employee_cost_center  # type: ignore
            cc = employee_cost_center() or cc
        except ImportError:
            pass

        pr_id = ErpDB.insert("purchase_requisitions", {
            "company_id": company_id, "pr_number": pr_number, "pr_date": date.today().isoformat(),
            "pr_type": "MATERIAL", "requested_by": user_id,
            "purpose": "Reorder — stock at or below reorder point (ZMRP)", "status": "SUBMITTED", "created_by": user_id,
            "category_id": cc.get("category_id"), "unit_id": cc.get("unit_id"), "department_id": cc.get("department_id"),
        })
        n, total = 0, 0.0
        for item_id, qty in clean_lines.items():
            it = ErpDB.fetch_one(
                "SELECT id, base_uom_id, moving_avg_price, block_status FROM items WHERE id=? AND company_id=? AND is_deleted=0",
                (item_id, company_id),
            )
            if not it or str(it.get("block_status") or "") in ("PROCUREMENT", "ALL"):
                continue
            n += 1
            ErpDB.insert("purchase_requisition_lines", {
                "pr_id": pr_id, "line_num": n * 10, "item_id": item_id, "uom_id": it["base_uom_id"],
                "qty_requested": round(qty, 3), "est_price": float(it["moving_avg_price"] or 0),
                "remarks": "Reorder level", "status": "OPEN",
            })
            total += qty * float(it["moving_avg_price"] or 0)

        if n == 0:
            ErpDB.rollback()
            return {"ok": False, "message": "None of the selected materials can be purchased (blocked or missing)."}

        ErpAuth.audit("purchase_requisitions", pr_id, "INSERT", {}, {"pr_number": pr_number, "source": "ZMRP"})
        ErpDB.commit()

        # msme_erp_get_pr_release_strategy() isn't ported yet (approval-routing engine) — degrade to
        # "no release routing", same as PHP's function_exists() guard.
        released = False
        try:
            from core.app import get_pr_release_strategy  # type: ignore
            st = get_pr_release_strategy(company_id)
            if st:
                rel_id = ErpDB.insert("document_releases", {
                    "company_id": company_id, "doc_type": "purchase_requisition", "doc_id": pr_id,
                    "doc_number": pr_number, "doc_amount": round(total, 2), "strategy_id": int(st["id"]),
                    "current_level": 1, "status": "PENDING", "requested_by": user_id,
                })
                ErpDB.update("purchase_requisitions", {"release_id": rel_id}, "id=?", (pr_id,))
                released = True
        except ImportError:
            pass
        except Exception as e:
            logger.error("ZMRP release routing: %s", e)

        tail = " and sent to release." if released else " — approve it from ME53N."
        return {"ok": True, "pr_id": pr_id, "pr_number": pr_number, "lines": n,
                "message": f"Purchase request {pr_number} created with {n} line(s){tail}"}
    except Exception as e:
        try:
            ErpDB.rollback()
        except Exception:
            pass
        return {"ok": False, "message": f"Could not create the purchase request: {e}"}