"""
Brain ERP — Report Library
Direct port of PHP core/reports.php (v1.19.0) → Python + SQLite.

One data-driven registry of SAP-style standard reports, each returning
plain Python dicts/lists so the caller (FastAPI route, PyQt6 widget, or
CSV export) can consume them directly — no HTML is generated here.

Each report entry is a dict with:
  module   : str              — module code (MM / SD / FI / HR / PROD / BOM / QM / PM / PS / ADMIN)
  group    : str              — left-menu group label
  title    : str              — human-readable title
  icon     : str              — Bootstrap-icon class (kept for the UI layer)
  filters  : list[str]        — filter keys the caller should supply (see below)
  status   : list[str] | None — valid status values when 'status' is a filter
  run      : Callable[[int, dict], dict]
              company_id, filter_dict → result dict:
                cols     : dict[col_key, col_label]
                rows     : list[dict]
                qty      : list[str]   — columns to format as quantities
                money    : list[str]   — columns to format as currency
                sum      : list[str]   — columns that carry grand totals
                group_by : str | None  — column whose value changes trigger a sub-total row
                note     : str | None  — footnote for the UI

Filter keys the caller must supply (default to '' / 0 / None when unused):
  from, to       : 'YYYY-MM-DD' date strings
  as_of          : 'YYYY-MM-DD' date string (point-in-time)
  supplier       : int supplier_id (0 = all)
  customer       : int customer_id (0 = all)
  item           : int item_id     (0 = all)
  warehouse      : int warehouse_id (0 = all)
  matgroup       : int material_group_id (0 = all)
  department     : int department_id (0 = all)
  status         : str status value ('' = all)
  text           : str free-text search fragment
  year           : int calendar year (0 = current)
  month          : str 'YYYY-MM' ('' = current)
  run            : int payroll_run_id (0 = latest)
  days           : int idle-days threshold for slow-movers (0 = default 90)
  opt            : str option key (varies per report)

NOT PORTED — flagged, not translated wholesale, same convention as app.py /
finance_engine.py:
  • msme_erp_report_can_access()   — WP Auth::check() / current_user_can().
      Use ErpAuth.check_tcode(tcode) from auth.py instead.
  • modules/finance/reports.php    — that file is a *rendering* screen for
      F.01 / S_ALR / F.08 / FAGLL03 / ZDAY / ZCBK / F.CF / KSB1. All the
      business-logic data already lives in finance_engine.py (balances(),
      fin_balances(), fin_signed_balance(), etc.). The rendering loops, HTML
      echo, $bar() / $head() filter-bar closures, esc_html(), CSV streaming,
      and the POST budget-save form in KSB1 are WP-admin-UI; they have no
      equivalent in the desktop app. A FastAPI route can call finance_engine
      functions and return JSON; the PyQt6 widget renders it. The KSB1 budget-
      save POST is a small write that belongs in a router, not here.
  • msme_erp_fin_csv_export()      — HTTP CSV streaming; a FastAPI route can
      use StreamingResponse(csv_data, media_type='text/csv') instead.
  • msme_erp_fin_export_buttons()  — WP HTML snippet for export buttons; UI only.
  • $bar() / $head()               — WP HTML filter-bar closures; UI only.

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import logging
import re
from datetime import date, datetime
from typing import Any, Callable, Optional

from core.db import ErpDB
from core.auth import ErpAuth

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------

def _rp_add(where: list, args: list, cond: str, val) -> None:
    """
    Append cond + val to the where/args lists only when val is non-empty.
    Mirrors PHP msme_erp_rp_add() exactly.
    """
    if val is None or val == "" or val == 0 or val == "0":
        return
    where.append(cond)
    args.append(val)


def _d(filters: dict, key: str, default="") -> Any:
    """Safe filter value accessor with a default."""
    v = filters.get(key, default)
    return v if v is not None else default


def _today() -> str:
    return date.today().isoformat()


def _days_ago(d: str, n: int) -> str:
    """Date n days before d (YYYY-MM-DD string)."""
    from datetime import timedelta
    return (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# ACCESS CHECK
# ---------------------------------------------------------------------------

def report_can_access(tcode: str) -> bool:
    """
    Return True when the current user may run this report.
    Mirrors PHP msme_erp_report_can_access() without WP dependencies:
      - super-admin always wins
      - otherwise the user must hold the T-code
    """
    if not ErpAuth.is_logged_in():
        return False
    if ErpAuth.is_super_admin():
        return True
    return ErpAuth.check_tcode(tcode)


# ---------------------------------------------------------------------------
# REPORT TREE  (module → group → [tcodes])
# Mirrors PHP msme_erp_report_tree() exactly.
# ---------------------------------------------------------------------------

REPORT_TREE: dict[str, dict[str, list[str]]] = {
    "MM": {
        "Purchasing":          ["ME2L", "ME2M", "ME5A", "ME1P", "ME2PN", "ZMMR"],
        "Inventory":           ["MMBE", "MB5L", "MB5B", "MB51", "ZMRP", "MC46", "MC40"],
        "Invoice Verification": ["MB5S"],
    },
    "SD": {
        "Sales Documents": ["VA05", "V.15", "VL06O", "VF05"],
        "Sales Analysis":  ["MCSI", "ZSMA"],
    },
    "FI": {
        "Financial Statements":    ["F.01", "S_ALR", "F.08", "F.CF", "FS10N", "FAGLL03", "ZDAY"],
        "Receivables & Payables":  ["FBL5N", "FBL1N"],
        "Bank & Cash":             ["ZCBK"],
        "Cost Center Accounting":  ["KSB1", "KS03"],
        "Fixed Assets":            ["AW01N"],
        "GST & TDS":               ["GSTR1", "GSTR3B", "GSTR2B", "TDS01"],
    },
    "HR": {
        "Personnel":        ["S_AHR_61016362", "ZHCD"],
        "Time Management":  ["PT_BAL00", "ZATT"],
        "Payroll":          ["ZPRG"],
    },
    "PROD": {
        "Production Orders": ["COOIS", "ZPOU", "ZMIV"],
        "Costing":           ["CK13N"],
    },
    "BOM": {
        "Bill of Material": ["ZBOM"],
    },
    "QM": {
        "Inspection":            ["QA05"],
        "Quality Notification":  ["QM10"],
    },
    "PM": {
        "Equipment & Orders": ["IH08", "IW39", "IW38"],
    },
    "PS": {
        "Project Reports": ["CJ03", "CJI3", "ZPRB"],
    },
    "ADMIN": {
        "Authorization & Audit": ["SUIM", "SCDO", "SM21"],
    },
}

# Module code → sidebar label (mirrors PHP msme_erp_report_module_labels())
REPORT_MODULE_LABELS: dict[str, str] = {
    "MM":    "Materials Management",
    "SD":    "Sales & Distribution",
    "FI":    "Accounting",
    "HR":    "Human Resources",
    "PROD":  "Production",
    "BOM":   "Bill of Material",
    "QM":    "Quality",
    "PM":    "Plant Maintenance",
    "PS":    "Projects & Developments",
    "ADMIN": "Administration",
}

# ---------------------------------------------------------------------------
# EXISTING REPORTS (keep their own dedicated screens — menu / report-center only)
# Mirrors PHP msme_erp_report_existing().
# ---------------------------------------------------------------------------

REPORT_EXISTING: dict[str, str] = {
    "ME2M":   "Purchase Orders by Material",
    "ME2PN":  "MM Pending Report",
    "ZMMR":   "Procurement Follow-up Reports",
    "MMBE":   "Stock Overview",
    "ZMRP":   "Reorder Report",
    "F.01":   "Balance Sheet",
    "S_ALR":  "Profit & Loss Statement",
    "F.08":   "Trial Balance",
    "F.CF":   "Cash Flow Statement",
    "FAGLL03":"G/L Account Statement",
    "ZDAY":   "Day Book",
    "FBL5N":  "Customer Open Items & Ageing",
    "FBL1N":  "Supplier Open Items & Ageing",
    "ZCBK":   "Cash and Bank Book",
    "KSB1":   "Cost Center P&L and Budget",
    "KS03":   "Display Cost Centers",
    "AW01N":  "Asset Register and Depreciation",
    "GSTR1":  "GSTR-1 Outward Supplies",
    "GSTR3B": "GSTR-3B Summary Return",
    "GSTR2B": "GSTR-2B Reconciliation",
    "TDS01":  "TDS Register and Returns",
    "COOIS":  "Production Report",
    "CK13N":  "Production Cost Report",
    "IW38":   "Maintenance Report",
    "CJ03":   "Project List",
    "CJI3":   "Project Actual Cost Report",
    "SM21":   "System Log",
}


# ---------------------------------------------------------------------------
# REPORT DEFINITIONS
# Mirrors PHP msme_erp_report_defs().  Each entry:
#   module, group, title, icon, filters, [status], [days_default], [opt], run
# ---------------------------------------------------------------------------

def _report_defs() -> dict[str, dict]:
    """
    Build and return the full report-definition registry (lazy singleton).
    Mirrors PHP msme_erp_report_defs() — static $defs pattern.
    """
    R: dict[str, dict] = {}

    # ===================== MM =====================

    # ME2L — Purchase Orders by Supplier
    def _me2l(c: int, f: dict) -> dict:
        w = ["po.company_id=?", "COALESCE(po.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "po.po_date>=?", _d(f, "from"))
        _rp_add(w, a, "po.po_date<=?", _d(f, "to"))
        _rp_add(w, a, "po.supplier_id=?", int(_d(f, "supplier", 0)) or None)
        _rp_add(w, a, "po.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT po.po_number, po.po_date, s.supplier_code, s.name AS supplier, po.status, po.delivery_date, "
            "po.total_amount, po.tax_amount, po.grand_total, "
            "COALESCE(SUM(pl.qty_ordered),0) AS qty_ordered, COALESCE(SUM(pl.qty_received),0) AS qty_received "
            "FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id "
            "LEFT JOIN purchase_order_lines pl ON pl.po_id=po.id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY po.id, po.po_number, po.po_date, s.supplier_code, s.name, po.status, "
            "po.delivery_date, po.total_amount, po.tax_amount, po.grand_total "
            "ORDER BY s.name, po.po_date DESC, po.id DESC",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["qty_pending"] = max(0.0, float(r["qty_ordered"]) - float(r["qty_received"]))
        return {
            "cols": {
                "po_number": "PO No.", "po_date": "PO Date",
                "supplier_code": "Supplier Code", "supplier": "Supplier",
                "status": "Status", "delivery_date": "Delivery Date",
                "qty_ordered": "Qty Ordered", "qty_received": "Qty Received",
                "qty_pending": "Qty Pending",
                "total_amount": "Taxable", "tax_amount": "GST", "grand_total": "Grand Total",
            },
            "rows": rows,
            "qty": ["qty_ordered", "qty_received", "qty_pending"],
            "money": ["total_amount", "tax_amount", "grand_total"],
            "sum": ["total_amount", "tax_amount", "grand_total"],
            "group_by": "supplier",
        }

    R["ME2L"] = {
        "module": "MM", "group": "Purchasing", "title": "Purchase Orders by Supplier",
        "icon": "bi-bag",
        "filters": ["from", "to", "supplier", "status"],
        "status": ["DRAFT", "APPROVED", "PARTIAL", "RECEIVED", "CLOSED", "REJECTED", "CANCELLED"],
        "run": _me2l,
    }

    # ME5A — Purchase Requisitions List Display
    def _me5a(c: int, f: dict) -> dict:
        w = ["pr.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "pr.pr_date>=?", _d(f, "from"))
        _rp_add(w, a, "pr.pr_date<=?", _d(f, "to"))
        _rp_add(w, a, "prl.item_id=?", int(_d(f, "item", 0)) or None)
        _rp_add(w, a, "pr.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT pr.pr_number, pr.pr_date, pr.status, prl.line_num, i.item_code, i.item_name, u.code AS uom, "
            "prl.qty_requested, prl.qty_approved, prl.est_price, "
            "(prl.qty_requested * prl.est_price) AS est_value, prl.required_by, prl.status AS line_status, "
            "po.po_number "
            "FROM purchase_requisitions pr "
            "JOIN purchase_requisition_lines prl ON prl.pr_id=pr.id "
            "LEFT JOIN items i ON i.id=prl.item_id "
            "LEFT JOIN units_of_measure u ON u.id=prl.uom_id "
            "LEFT JOIN purchase_order_lines pol ON pol.pr_line_id=prl.id "
            "LEFT JOIN purchase_orders po ON po.id=pol.po_id "
            f"WHERE {' AND '.join(w)} ORDER BY pr.pr_date DESC, pr.id DESC, prl.line_num",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "pr_number": "PR No.", "pr_date": "PR Date", "status": "PR Status",
                "line_num": "Line", "item_code": "Material", "item_name": "Description",
                "uom": "UoM", "qty_requested": "Qty Requested", "qty_approved": "Qty Approved",
                "est_price": "Est. Price", "est_value": "Est. Value",
                "required_by": "Required By", "line_status": "Line Status", "po_number": "PO No.",
            },
            "rows": rows,
            "qty": ["qty_requested", "qty_approved"],
            "money": ["est_price", "est_value"],
            "sum": ["est_value"],
        }

    R["ME5A"] = {
        "module": "MM", "group": "Purchasing", "title": "Purchase Requisitions - List Display",
        "icon": "bi-file-earmark-text",
        "filters": ["from", "to", "item", "status"],
        "status": ["SUBMITTED", "APPROVED", "PO_CREATED", "REJECTED", "CANCELLED"],
        "run": _me5a,
    }

    # ME1P — Purchase Price History
    def _me1p(c: int, f: dict) -> dict:
        w = ["po.company_id=?", "COALESCE(po.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "po.po_date>=?", _d(f, "from"))
        _rp_add(w, a, "po.po_date<=?", _d(f, "to"))
        _rp_add(w, a, "pl.item_id=?", int(_d(f, "item", 0)) or None)
        _rp_add(w, a, "po.supplier_id=?", int(_d(f, "supplier", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT i.item_code, i.item_name, po.po_date, po.po_number, s.name AS supplier, "
            "pl.qty_ordered, pl.unit_price, pl.discount_pct, pl.gst_rate "
            "FROM purchase_order_lines pl "
            "JOIN purchase_orders po ON po.id=pl.po_id "
            "JOIN suppliers s ON s.id=po.supplier_id "
            "JOIN items i ON i.id=pl.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY i.item_name, po.po_date DESC, po.id DESC",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["net_rate"] = round(float(r["unit_price"]) * (1 - float(r["discount_pct"] or 0) / 100), 4)
        return {
            "cols": {
                "item_code": "Material", "item_name": "Description",
                "po_date": "PO Date", "po_number": "PO No.", "supplier": "Supplier",
                "qty_ordered": "Qty", "unit_price": "Unit Price",
                "discount_pct": "Disc %", "net_rate": "Net Rate", "gst_rate": "GST %",
            },
            "rows": rows,
            "qty": ["qty_ordered"],
            "money": ["unit_price", "net_rate"],
            "group_by": "item_name",
        }

    R["ME1P"] = {
        "module": "MM", "group": "Purchasing", "title": "Purchase Price History",
        "icon": "bi-graph-up-arrow",
        "filters": ["from", "to", "item", "supplier"],
        "run": _me1p,
    }

    # MB51 — Material Document List (stock movements)
    def _mb51(c: int, f: dict) -> dict:
        w = ["sm.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "sm.movement_date>=?", _d(f, "from"))
        _rp_add(w, a, "sm.movement_date<=?", _d(f, "to"))
        _rp_add(w, a, "sm.item_id=?", int(_d(f, "item", 0)) or None)
        _rp_add(w, a, "sm.warehouse_id=?", int(_d(f, "warehouse", 0)) or None)
        txt = _d(f, "text")
        if txt:
            w.append("(sm.movement_type LIKE ? OR sm.reference_num LIKE ? OR sm.mvt_code LIKE ?)")
            like = f"%{txt}%"
            a += [like, like, like]
        rows = ErpDB.fetch_all(
            "SELECT sm.movement_date, sm.mvt_code, sm.movement_type, sm.reference_num, "
            "i.item_code, i.item_name, wh.name AS warehouse, "
            "sm.qty_in, sm.qty_out, sm.unit_cost, sm.total_value, sm.batch_number "
            "FROM stock_movements sm "
            "LEFT JOIN items i ON i.id=sm.item_id "
            "LEFT JOIN warehouses wh ON wh.id=sm.warehouse_id "
            f"WHERE {' AND '.join(w)} ORDER BY sm.movement_date DESC, sm.id DESC LIMIT 5000",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "movement_date": "Posting Date", "mvt_code": "Mvt", "movement_type": "Movement Type",
                "reference_num": "Reference", "item_code": "Material", "item_name": "Description",
                "warehouse": "Warehouse", "qty_in": "Qty In", "qty_out": "Qty Out",
                "unit_cost": "Unit Cost", "total_value": "Value", "batch_number": "Batch",
            },
            "rows": rows,
            "qty": ["qty_in", "qty_out"],
            "money": ["unit_cost", "total_value"],
            "sum": ["qty_in", "qty_out"],
            "note": "Latest 5,000 documents matching the selection.",
        }

    R["MB51"] = {
        "module": "MM", "group": "Inventory", "title": "Material Document List",
        "icon": "bi-journal-text",
        "filters": ["from", "to", "item", "warehouse", "text"],
        "text_label": "Movement type / reference",
        "run": _mb51,
    }

    # MB5B — Stocks for Posting Date (rebuilt from movements)
    def _mb5b(c: int, f: dict) -> dict:
        as_of = _d(f, "as_of") or _today()
        w = ["sm.company_id=?", "sm.movement_date<=?"]
        a: list = [c, as_of]
        _rp_add(w, a, "sm.warehouse_id=?", int(_d(f, "warehouse", 0)) or None)
        _rp_add(w, a, "i.material_group_id=?", int(_d(f, "matgroup", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT i.item_code, i.item_name, wh.name AS warehouse, "
            "SUM(sm.qty_in) AS q_in, SUM(sm.qty_out) AS q_out, "
            "SUM(sm.qty_in * sm.unit_cost) AS v_in, SUM(sm.qty_out * sm.unit_cost) AS v_out "
            "FROM stock_movements sm "
            "JOIN items i ON i.id=sm.item_id "
            "LEFT JOIN warehouses wh ON wh.id=sm.warehouse_id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY i.id, i.item_code, i.item_name, wh.id, wh.name ORDER BY i.item_name, wh.name",
            tuple(v for v in a if v is not None),
        )
        out = []
        for r in rows:
            q = round(float(r["q_in"]) - float(r["q_out"]), 3)
            if abs(q) < 0.0005:
                continue
            v = round(float(r["v_in"]) - float(r["v_out"]), 2)
            out.append({
                "item_code": r["item_code"], "item_name": r["item_name"],
                "warehouse": r["warehouse"], "qty": q,
                "rate": round(v / q, 4) if q != 0 else 0,
                "value": v,
            })
        return {
            "cols": {
                "item_code": "Material", "item_name": "Description", "warehouse": "Warehouse",
                "qty": "Stock Qty", "rate": "Avg Rate", "value": "Stock Value",
            },
            "rows": out,
            "qty": ["qty"],
            "money": ["rate", "value"],
            "sum": ["value"],
            "note": "Rebuilt from goods movements up to the selected date.",
        }

    R["MB5B"] = {
        "module": "MM", "group": "Inventory", "title": "Stocks for Posting Date",
        "icon": "bi-calendar-check",
        "filters": ["as_of", "warehouse", "matgroup"],
        "run": _mb5b,
    }

    # MB5L — List of Stock Values (Balance Sheet Valuation)
    def _mb5l(c: int, f: dict) -> dict:
        w = ["i.company_id=?", "COALESCE(i.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "sb.warehouse_id=?", int(_d(f, "warehouse", 0)) or None)
        _rp_add(w, a, "i.material_group_id=?", int(_d(f, "matgroup", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT i.item_code, i.item_name, COALESCE(mg.name,'') AS matgroup, "
            "SUM(sb.qty_on_hand) AS qty, SUM(sb.qty_reserved) AS reserved, "
            "COALESCE(i.moving_avg_price,0) AS map_price, "
            "SUM(sb.qty_on_hand * COALESCE(NULLIF(sb.avg_cost,0), i.moving_avg_price, 0)) AS value "
            "FROM stock_balances sb "
            "JOIN items i ON i.id=sb.item_id "
            "LEFT JOIN material_groups mg ON mg.id=i.material_group_id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY i.id, i.item_code, i.item_name, mg.name, i.moving_avg_price "
            "HAVING SUM(sb.qty_on_hand) <> 0 ORDER BY i.item_name",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "item_code": "Material", "item_name": "Description", "matgroup": "Material Group",
                "qty": "Stock Qty", "reserved": "Reserved",
                "map_price": "Moving Avg Price", "value": "Stock Value",
            },
            "rows": rows,
            "qty": ["qty", "reserved"],
            "money": ["map_price", "value"],
            "sum": ["value"],
        }

    R["MB5L"] = {
        "module": "MM", "group": "Inventory", "title": "List of Stock Values (Balance Sheet Valuation)",
        "icon": "bi-cash-stack",
        "filters": ["warehouse", "matgroup"],
        "run": _mb5l,
    }

    # MC46 — Slow-Moving Items
    def _mc46(c: int, f: dict) -> dict:
        days = max(1, int(_d(f, "days", 0) or 90))
        w = ["i.company_id=?", "COALESCE(i.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "sb.warehouse_id=?", int(_d(f, "warehouse", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT i.id, i.item_code, i.item_name, SUM(sb.qty_on_hand) AS qty, "
            "COALESCE(i.moving_avg_price,0) AS map_price, "
            "(SELECT MAX(sm.movement_date) FROM stock_movements sm WHERE sm.item_id=i.id AND sm.qty_out>0) AS last_issue "
            "FROM stock_balances sb JOIN items i ON i.id=sb.item_id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY i.id, i.item_code, i.item_name, i.moving_avg_price "
            "HAVING SUM(sb.qty_on_hand) > 0 ORDER BY i.item_name",
            tuple(v for v in a if v is not None),
        )
        today = date.today()
        out = []
        for r in rows:
            if r["last_issue"]:
                li = datetime.strptime(str(r["last_issue"]), "%Y-%m-%d").date()
                age = (today - li).days
            else:
                age = None
            if age is not None and age < days:
                continue
            out.append({
                "item_code": r["item_code"],
                "item_name": r["item_name"],
                "qty": r["qty"],
                "map_price": r["map_price"],
                "value": round(float(r["qty"]) * float(r["map_price"]), 2),
                "last_issue": r["last_issue"] or "Never issued",
                "idle_days": age if age is not None else "-",
            })
        out.sort(key=lambda x: float(x["value"]) if isinstance(x["value"], (int, float)) else 0, reverse=True)
        return {
            "cols": {
                "item_code": "Material", "item_name": "Description",
                "qty": "Stock Qty", "map_price": "Moving Avg Price",
                "value": "Stock Value", "last_issue": "Last Issue", "idle_days": "Idle Days",
            },
            "rows": out,
            "qty": ["qty"],
            "money": ["map_price", "value"],
            "sum": ["value"],
            "note": f"Stock with no issue in the last {days} days.",
        }

    R["MC46"] = {
        "module": "MM", "group": "Inventory", "title": "Slow-Moving Items",
        "icon": "bi-hourglass-split",
        "filters": ["days", "warehouse"],
        "days_default": 90,
        "run": _mc46,
    }

    # MC40 — ABC Analysis
    def _mc40(c: int, f: dict) -> dict:
        basis = _d(f, "opt") or "consumption"
        if basis == "stock":
            rows = ErpDB.fetch_all(
                "SELECT i.item_code, i.item_name, "
                "SUM(sb.qty_on_hand * COALESCE(NULLIF(sb.avg_cost,0), i.moving_avg_price, 0)) AS value "
                "FROM stock_balances sb JOIN items i ON i.id=sb.item_id "
                "WHERE i.company_id=? GROUP BY i.id, i.item_code, i.item_name "
                "HAVING SUM(sb.qty_on_hand) > 0",
                (c,),
            )
        else:
            w = ["sm.company_id=?", "sm.qty_out>0"]
            a: list = [c]
            _rp_add(w, a, "sm.movement_date>=?", _d(f, "from"))
            _rp_add(w, a, "sm.movement_date<=?", _d(f, "to"))
            rows = ErpDB.fetch_all(
                "SELECT i.item_code, i.item_name, SUM(sm.qty_out * sm.unit_cost) AS value "
                "FROM stock_movements sm JOIN items i ON i.id=sm.item_id "
                f"WHERE {' AND '.join(w)} GROUP BY i.id, i.item_code, i.item_name",
                tuple(v for v in a if v is not None),
            )
        rows.sort(key=lambda x: float(x["value"] or 0), reverse=True)
        total = sum(float(r["value"] or 0) for r in rows)
        cum = 0.0
        out = []
        for idx, r in enumerate(rows):
            v = float(r["value"] or 0)
            prev_pct = (cum / total * 100) if total > 0 else 0
            cum += v
            pct = (cum / total * 100) if total > 0 else 0
            cls = "A" if prev_pct < 70 else ("B" if prev_pct < 90 else "C")
            out.append({
                "rank": idx + 1,
                "item_code": r["item_code"],
                "item_name": r["item_name"],
                "value": round(v, 2),
                "share": round(v / total * 100, 2) if total > 0 else 0,
                "cum": round(pct, 2),
                "cls": cls,
            })
        return {
            "cols": {
                "rank": "#", "item_code": "Material", "item_name": "Description",
                "value": "Value", "share": "Share %", "cum": "Cumulative %", "cls": "Class",
            },
            "rows": out,
            "money": ["value"],
            "sum": ["value"],
            "note": "Class A = first 70% of value, B = next 20%, C = last 10%.",
        }

    R["MC40"] = {
        "module": "MM", "group": "Inventory", "title": "ABC Analysis",
        "icon": "bi-bar-chart-steps",
        "filters": ["from", "to", "opt"],
        "opt": {"label": "Basis", "choices": {
            "consumption": "Consumption value (issues in period)",
            "stock": "Current stock value",
        }},
        "run": _mc40,
    }

    # MB5S — GR/IR Analysis (Received, Not Invoiced)
    def _mb5s(c: int, f: dict) -> dict:
        w = ["po.company_id=?", "COALESCE(po.is_deleted,0)=0", "pl.qty_received > pl.qty_invoiced"]
        a: list = [c]
        _rp_add(w, a, "po.po_date>=?", _d(f, "from"))
        _rp_add(w, a, "po.po_date<=?", _d(f, "to"))
        _rp_add(w, a, "po.supplier_id=?", int(_d(f, "supplier", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT po.po_number, po.po_date, s.name AS supplier, i.item_code, i.item_name, "
            "pl.qty_received, pl.qty_invoiced, "
            "(pl.qty_received - pl.qty_invoiced) AS qty_open, pl.unit_price, "
            "((pl.qty_received - pl.qty_invoiced) * pl.unit_price) AS value_open "
            "FROM purchase_order_lines pl "
            "JOIN purchase_orders po ON po.id=pl.po_id "
            "JOIN suppliers s ON s.id=po.supplier_id "
            "JOIN items i ON i.id=pl.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY s.name, po.po_date, po.id",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "po_number": "PO No.", "po_date": "PO Date", "supplier": "Supplier",
                "item_code": "Material", "item_name": "Description",
                "qty_received": "Qty Received", "qty_invoiced": "Qty Invoiced",
                "qty_open": "Qty Not Invoiced", "unit_price": "Unit Price",
                "value_open": "Value Not Invoiced",
            },
            "rows": rows,
            "qty": ["qty_received", "qty_invoiced", "qty_open"],
            "money": ["unit_price", "value_open"],
            "sum": ["value_open"],
            "group_by": "supplier",
        }

    R["MB5S"] = {
        "module": "MM", "group": "Invoice Verification", "title": "GR/IR Analysis (Received, Not Invoiced)",
        "icon": "bi-receipt",
        "filters": ["from", "to", "supplier"],
        "run": _mb5s,
    }

    # ===================== SD =====================

    # VA05 — List of Sales Orders
    def _va05(c: int, f: dict) -> dict:
        w = ["so.company_id=?", "COALESCE(so.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "so.so_date>=?", _d(f, "from"))
        _rp_add(w, a, "so.so_date<=?", _d(f, "to"))
        _rp_add(w, a, "so.customer_id=?", int(_d(f, "customer", 0)) or None)
        _rp_add(w, a, "so.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT so.so_number, so.so_date, cu.customer_code, cu.name AS customer, "
            "so.po_number AS cust_po, so.status, so.delivery_date, "
            "so.total_amount, so.tax_amount, so.grand_total, "
            "COALESCE(SUM(sl.qty_ordered),0) AS qty_ordered, "
            "COALESCE(SUM(sl.qty_delivered),0) AS qty_delivered "
            "FROM sales_orders so "
            "JOIN customers cu ON cu.id=so.customer_id "
            "LEFT JOIN sales_order_lines sl ON sl.so_id=so.id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY so.id, so.so_number, so.so_date, cu.customer_code, cu.name, so.po_number, "
            "so.status, so.delivery_date, so.total_amount, so.tax_amount, so.grand_total "
            "ORDER BY so.so_date DESC, so.id DESC",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["qty_pending"] = max(0.0, float(r["qty_ordered"]) - float(r["qty_delivered"]))
        return {
            "cols": {
                "so_number": "SO No.", "so_date": "SO Date",
                "customer_code": "Cust. Code", "customer": "Customer", "cust_po": "Cust. PO",
                "status": "Status", "delivery_date": "Delivery Date",
                "qty_ordered": "Qty Ordered", "qty_delivered": "Qty Delivered",
                "qty_pending": "Qty Pending",
                "total_amount": "Net Value", "tax_amount": "GST", "grand_total": "Total",
            },
            "rows": rows,
            "qty": ["qty_ordered", "qty_delivered", "qty_pending"],
            "money": ["total_amount", "tax_amount", "grand_total"],
            "sum": ["total_amount", "tax_amount", "grand_total"],
        }

    R["VA05"] = {
        "module": "SD", "group": "Sales Documents", "title": "List of Sales Orders",
        "icon": "bi-cart-check",
        "filters": ["from", "to", "customer", "status"],
        "status": ["DRAFT", "APPROVED", "PARTIAL", "DELIVERED", "INVOICED", "CLOSED", "REJECTED", "CANCELLED"],
        "run": _va05,
    }

    # V.15 — Backorders (Open Sales Order Items)
    def _v15(c: int, f: dict) -> dict:
        w = [
            "so.company_id=?", "COALESCE(so.is_deleted,0)=0",
            "sl.qty_ordered > sl.qty_delivered",
            "so.status NOT IN ('DRAFT','REJECTED','CANCELLED','CLOSED')",
        ]
        a: list = [c]
        _rp_add(w, a, "so.customer_id=?", int(_d(f, "customer", 0)) or None)
        _rp_add(w, a, "sl.item_id=?", int(_d(f, "item", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT so.so_number, so.so_date, cu.name AS customer, i.item_code, i.item_name, "
            "sl.qty_ordered, sl.qty_delivered, (sl.qty_ordered - sl.qty_delivered) AS qty_open, "
            "sl.unit_price, ((sl.qty_ordered - sl.qty_delivered) * sl.unit_price) AS value_open, "
            "COALESCE(sl.delivery_date, so.delivery_date) AS due "
            "FROM sales_order_lines sl "
            "JOIN sales_orders so ON so.id=sl.so_id "
            "JOIN customers cu ON cu.id=so.customer_id "
            "JOIN items i ON i.id=sl.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY cu.name, so.so_date, so.id",
            tuple(v for v in a if v is not None),
        )
        today = date.today()
        for r in rows:
            if r["due"]:
                due_d = datetime.strptime(str(r["due"]), "%Y-%m-%d").date()
                r["overdue"] = max(0, (today - due_d).days) if due_d < today else 0
            else:
                r["overdue"] = 0
        return {
            "cols": {
                "so_number": "SO No.", "so_date": "SO Date", "customer": "Customer",
                "item_code": "Material", "item_name": "Description",
                "qty_ordered": "Ordered", "qty_delivered": "Delivered",
                "qty_open": "Open Qty", "unit_price": "Unit Price",
                "value_open": "Open Value", "due": "Due Date", "overdue": "Days Overdue",
            },
            "rows": rows,
            "qty": ["qty_ordered", "qty_delivered", "qty_open"],
            "money": ["unit_price", "value_open"],
            "sum": ["value_open"],
            "group_by": "customer",
        }

    R["V.15"] = {
        "module": "SD", "group": "Sales Documents", "title": "Backorders (Open Sales Order Items)",
        "icon": "bi-hourglass",
        "filters": ["customer", "item"],
        "run": _v15,
    }

    # VL06O — Outbound Delivery Monitor
    def _vl06o(c: int, f: dict) -> dict:
        w = ["dn.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "dn.dn_date>=?", _d(f, "from"))
        _rp_add(w, a, "dn.dn_date<=?", _d(f, "to"))
        _rp_add(w, a, "dn.customer_id=?", int(_d(f, "customer", 0)) or None)
        _rp_add(w, a, "dn.warehouse_id=?", int(_d(f, "warehouse", 0)) or None)
        _rp_add(w, a, "dn.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT dn.dn_number, dn.dn_date, so.so_number, cu.name AS customer, wh.name AS warehouse, "
            "dn.vehicle_num, dn.transport, dn.lr_number, dn.status, dn.dispatched_at, "
            "COALESCE(SUM(dl.qty_packed),0) AS qty "
            "FROM delivery_notes dn "
            "LEFT JOIN sales_orders so ON so.id=dn.so_id "
            "LEFT JOIN customers cu ON cu.id=dn.customer_id "
            "LEFT JOIN warehouses wh ON wh.id=dn.warehouse_id "
            "LEFT JOIN delivery_note_lines dl ON dl.dn_id=dn.id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY dn.id, dn.dn_number, dn.dn_date, so.so_number, cu.name, wh.name, "
            "dn.vehicle_num, dn.transport, dn.lr_number, dn.status, dn.dispatched_at "
            "ORDER BY dn.dn_date DESC, dn.id DESC",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "dn_number": "Delivery No.", "dn_date": "Date", "so_number": "SO No.",
                "customer": "Customer", "warehouse": "Warehouse", "vehicle_num": "Vehicle",
                "transport": "Transporter", "lr_number": "LR No.",
                "status": "Status", "dispatched_at": "Dispatched", "qty": "Qty",
            },
            "rows": rows,
            "qty": ["qty"],
            "sum": ["qty"],
        }

    R["VL06O"] = {
        "module": "SD", "group": "Sales Documents", "title": "Outbound Delivery Monitor",
        "icon": "bi-truck",
        "filters": ["from", "to", "customer", "warehouse", "status"],
        "status": ["DRAFT", "PACKED", "DISPATCHED", "DELIVERED", "CANCELLED"],
        "run": _vl06o,
    }

    # VF05 — List of Billing Documents
    def _vf05(c: int, f: dict) -> dict:
        w = ["inv.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "inv.inv_date>=?", _d(f, "from"))
        _rp_add(w, a, "inv.inv_date<=?", _d(f, "to"))
        _rp_add(w, a, "inv.customer_id=?", int(_d(f, "customer", 0)) or None)
        _rp_add(w, a, "inv.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT inv.inv_number, inv.inv_date, cu.name AS customer, so.so_number, "
            "inv.due_date, inv.status, inv.base_amount, inv.discount_amount, "
            "inv.tax_amount, inv.grand_total, inv.paid_amount, inv.due_amount "
            "FROM sales_invoices inv "
            "JOIN customers cu ON cu.id=inv.customer_id "
            "LEFT JOIN sales_orders so ON so.id=inv.so_id "
            f"WHERE {' AND '.join(w)} ORDER BY inv.inv_date DESC, inv.id DESC",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "inv_number": "Invoice No.", "inv_date": "Date", "customer": "Customer",
                "so_number": "SO No.", "due_date": "Due Date", "status": "Status",
                "base_amount": "Taxable", "discount_amount": "Discount", "tax_amount": "GST",
                "grand_total": "Invoice Total", "paid_amount": "Received", "due_amount": "Outstanding",
            },
            "rows": rows,
            "money": ["base_amount", "discount_amount", "tax_amount", "grand_total", "paid_amount", "due_amount"],
            "sum": ["base_amount", "discount_amount", "tax_amount", "grand_total", "paid_amount", "due_amount"],
        }

    R["VF05"] = {
        "module": "SD", "group": "Sales Documents", "title": "List of Billing Documents",
        "icon": "bi-receipt-cutoff",
        "filters": ["from", "to", "customer", "status"],
        "status": ["DRAFT", "POSTED", "PARTIAL", "PAID", "CANCELLED"],
        "run": _vf05,
    }

    # MCSI — Customer Analysis (Sales by Customer)
    def _mcsi(c: int, f: dict) -> dict:
        w = ["inv.company_id=?", "inv.status<>'CANCELLED'", "inv.status<>'DRAFT'"]
        a: list = [c]
        _rp_add(w, a, "inv.inv_date>=?", _d(f, "from"))
        _rp_add(w, a, "inv.inv_date<=?", _d(f, "to"))
        rows = ErpDB.fetch_all(
            "SELECT cu.customer_code, cu.name AS customer, COUNT(inv.id) AS invoices, "
            "SUM(inv.base_amount) AS base_amount, SUM(inv.tax_amount) AS tax_amount, "
            "SUM(inv.grand_total) AS grand_total, SUM(inv.paid_amount) AS paid_amount, "
            "SUM(inv.due_amount) AS due_amount "
            "FROM sales_invoices inv JOIN customers cu ON cu.id=inv.customer_id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY cu.id, cu.customer_code, cu.name ORDER BY grand_total DESC",
            tuple(v for v in a if v is not None),
        )
        total = sum(float(r["grand_total"] or 0) for r in rows)
        for r in rows:
            r["share"] = round(float(r["grand_total"] or 0) / total * 100, 2) if total > 0 else 0
        return {
            "cols": {
                "customer_code": "Cust. Code", "customer": "Customer", "invoices": "Invoices",
                "base_amount": "Taxable Sales", "tax_amount": "GST",
                "grand_total": "Invoice Total", "paid_amount": "Received",
                "due_amount": "Outstanding", "share": "Share %",
            },
            "rows": rows,
            "money": ["base_amount", "tax_amount", "grand_total", "paid_amount", "due_amount"],
            "sum": ["invoices", "base_amount", "tax_amount", "grand_total", "paid_amount", "due_amount"],
        }

    R["MCSI"] = {
        "module": "SD", "group": "Sales Analysis", "title": "Customer Analysis (Sales by Customer)",
        "icon": "bi-people",
        "filters": ["from", "to"],
        "run": _mcsi,
    }

    # ZSMA — Material Analysis (Sales by Material)
    def _zsma(c: int, f: dict) -> dict:
        w = ["inv.company_id=?", "inv.status<>'CANCELLED'", "inv.status<>'DRAFT'"]
        a: list = [c]
        _rp_add(w, a, "inv.inv_date>=?", _d(f, "from"))
        _rp_add(w, a, "inv.inv_date<=?", _d(f, "to"))
        _rp_add(w, a, "inv.customer_id=?", int(_d(f, "customer", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT i.item_code, i.item_name, SUM(il.qty) AS qty, "
            "SUM(il.line_total) AS taxable, "
            "SUM(il.cgst_amount + il.sgst_amount + il.igst_amount) AS gst "
            "FROM sales_invoice_lines il "
            "JOIN sales_invoices inv ON inv.id=il.inv_id "
            "JOIN items i ON i.id=il.item_id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY i.id, i.item_code, i.item_name ORDER BY taxable DESC",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            qty = float(r["qty"] or 0)
            taxable = float(r["taxable"] or 0)
            r["avg_rate"] = round(taxable / qty, 2) if qty > 0 else 0
            r["total"] = round(taxable + float(r["gst"] or 0), 2)
        return {
            "cols": {
                "item_code": "Material", "item_name": "Description",
                "qty": "Qty Sold", "avg_rate": "Avg Rate",
                "taxable": "Taxable Sales", "gst": "GST", "total": "Total",
            },
            "rows": rows,
            "qty": ["qty"],
            "money": ["avg_rate", "taxable", "gst", "total"],
            "sum": ["taxable", "gst", "total"],
        }

    R["ZSMA"] = {
        "module": "SD", "group": "Sales Analysis", "title": "Material Analysis (Sales by Material)",
        "icon": "bi-box-seam",
        "filters": ["from", "to", "customer"],
        "run": _zsma,
    }

    # ===================== FI =====================

    # FS10N — G/L Account Balances
    def _fs10n(c: int, f: dict) -> dict:
        frm = _d(f, "from") or date(date.today().year, 1, 1).isoformat()
        to = _d(f, "to") or _today()
        rows = ErpDB.fetch_all(
            "SELECT coa.account_code, coa.account_name, coa.account_type, "
            "COALESCE(SUM(CASE WHEN je.entry_date < ? THEN jl.debit_amount - jl.credit_amount ELSE 0 END),0) AS opening, "
            "COALESCE(SUM(CASE WHEN je.entry_date >= ? AND je.entry_date <= ? THEN jl.debit_amount ELSE 0 END),0) AS debit, "
            "COALESCE(SUM(CASE WHEN je.entry_date >= ? AND je.entry_date <= ? THEN jl.credit_amount ELSE 0 END),0) AS credit "
            "FROM chart_of_accounts coa "
            "LEFT JOIN journal_entry_lines jl ON jl.account_id=coa.id "
            "LEFT JOIN journal_entries je ON je.id=jl.je_id AND je.status='POSTED' AND je.company_id=coa.company_id "
            "WHERE coa.company_id=? AND coa.is_group=0 "
            "GROUP BY coa.id, coa.account_code, coa.account_name, coa.account_type "
            "ORDER BY coa.account_code",
            (frm, frm, to, frm, to, c),
        )
        out = []
        for r in rows:
            opening = float(r["opening"] or 0)
            debit = float(r["debit"] or 0)
            credit = float(r["credit"] or 0)
            if abs(opening) < 0.005 and abs(debit) < 0.005 and abs(credit) < 0.005:
                continue
            out.append(dict(r, closing=round(opening + debit - credit, 2)))
        return {
            "cols": {
                "account_code": "G/L Account", "account_name": "Account Name",
                "account_type": "Type",
                "opening": "Opening Balance (Dr+/Cr-)",
                "debit": "Debit", "credit": "Credit",
                "closing": "Closing Balance (Dr+/Cr-)",
            },
            "rows": out,
            "money": ["opening", "debit", "credit", "closing"],
            "sum": ["debit", "credit"],
            "note": "Posted journal entries only. Accounts with no activity are hidden.",
        }

    R["FS10N"] = {
        "module": "FI", "group": "Financial Statements", "title": "G/L Account Balances",
        "icon": "bi-journal-check",
        "filters": ["from", "to"],
        "run": _fs10n,
    }

    # ===================== HR =====================

    # S_AHR_61016362 — Employee List
    def _s_ahr(c: int, f: dict) -> dict:
        w = ["e.company_id=?", "COALESCE(e.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "e.department_id=?", int(_d(f, "department", 0)) or None)
        _rp_add(w, a, "e.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT e.employee_code, e.first_name, e.last_name, d.name AS department, "
            "dg.name AS designation, e.joining_date, e.employment_type, e.status, e.phone, e.email "
            "FROM employees e "
            "LEFT JOIN departments d ON d.id=e.department_id "
            "LEFT JOIN designations dg ON dg.id=e.designation_id "
            f"WHERE {' AND '.join(w)} ORDER BY e.employee_code",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["name"] = (str(r["first_name"] or "") + " " + str(r["last_name"] or "")).strip()
        return {
            "cols": {
                "employee_code": "Emp. Code", "name": "Name", "department": "Department",
                "designation": "Designation", "joining_date": "Date of Joining",
                "employment_type": "Type", "status": "Status", "phone": "Phone", "email": "Email",
            },
            "rows": rows,
        }

    R["S_AHR_61016362"] = {
        "module": "HR", "group": "Personnel", "title": "Employee List",
        "icon": "bi-person-lines-fill",
        "filters": ["department", "status"],
        "status": ["ACTIVE", "INACTIVE", "RESIGNED", "TERMINATED"],
        "run": _s_ahr,
    }

    # ZHCD — Headcount by Department
    def _zhcd(c: int, f: dict) -> dict:
        rows = ErpDB.fetch_all(
            "SELECT COALESCE(d.name,'(No department)') AS department, COUNT(e.id) AS headcount, "
            "SUM(CASE WHEN e.status='ACTIVE' THEN 1 ELSE 0 END) AS active_count "
            "FROM employees e LEFT JOIN departments d ON d.id=e.department_id "
            "WHERE e.company_id=? AND COALESCE(e.is_deleted,0)=0 "
            "GROUP BY d.id, d.name ORDER BY department",
            (c,),
        )
        return {
            "cols": {
                "department": "Department",
                "headcount": "Total Employees",
                "active_count": "Active",
            },
            "rows": rows,
            "sum": ["headcount", "active_count"],
        }

    R["ZHCD"] = {
        "module": "HR", "group": "Personnel", "title": "Headcount by Department",
        "icon": "bi-diagram-3",
        "filters": [],
        "run": _zhcd,
    }

    # PT_BAL00 — Leave Balances
    def _pt_bal00(c: int, f: dict) -> dict:
        y = int(_d(f, "year", 0) or date.today().year)
        frm = f"{y}-01-01"
        to = f"{y}-12-31"
        w = [
            "e.company_id=?", "COALESCE(e.is_deleted,0)=0", "e.status='ACTIVE'",
            "lt.company_id=?", "COALESCE(lt.is_active,1)=1",
        ]
        a: list = [frm, to, c, c]
        _rp_add(w, a, "e.department_id=?", int(_d(f, "department", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT e.employee_code, e.first_name, e.last_name, lt.name AS leave_type, "
            "lt.days_per_year AS entitlement, "
            "(SELECT COALESCE(SUM(lr.num_days),0) FROM leave_requests lr "
            " WHERE lr.employee_id=e.id AND lr.leave_type_id=lt.id AND lr.status='APPROVED' "
            " AND lr.from_date>=? AND lr.from_date<=?) AS taken "
            "FROM employees e CROSS JOIN leave_types lt "
            f"WHERE {' AND '.join(w)} ORDER BY e.employee_code, lt.name",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["name"] = (str(r["first_name"] or "") + " " + str(r["last_name"] or "")).strip()
            r["balance"] = float(r["entitlement"] or 0) - float(r["taken"] or 0)
        return {
            "cols": {
                "employee_code": "Emp. Code", "name": "Name", "leave_type": "Leave Type",
                "entitlement": "Entitlement", "taken": "Taken", "balance": "Balance",
            },
            "rows": rows,
            "qty": ["entitlement", "taken", "balance"],
            "note": f"Calendar year {y}; approved leave only.",
            "group_by": "name",
        }

    R["PT_BAL00"] = {
        "module": "HR", "group": "Time Management", "title": "Leave Balances",
        "icon": "bi-calendar2-check",
        "filters": ["year", "department"],
        "run": _pt_bal00,
    }

    # ZATT — Attendance Summary (Monthly)
    def _zatt(c: int, f: dict) -> dict:
        raw_month = _d(f, "month")
        if not re.match(r"^\d{4}-\d{2}$", str(raw_month)):
            raw_month = date.today().strftime("%Y-%m")
        from calendar import monthrange
        y_m = raw_month.split("-")
        last_day = monthrange(int(y_m[0]), int(y_m[1]))[1]
        frm = f"{raw_month}-01"
        to = f"{raw_month}-{last_day:02d}"
        w = ["e.company_id=?", "COALESCE(e.is_deleted,0)=0", "e.status='ACTIVE'"]
        a: list = [frm, to, c]
        _rp_add(w, a, "e.department_id=?", int(_d(f, "department", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT e.employee_code, e.first_name, e.last_name, "
            "COALESCE(SUM(CASE WHEN at.status='PRESENT' THEN 1 ELSE 0 END),0) AS present, "
            "COALESCE(SUM(CASE WHEN at.status='ABSENT' THEN 1 ELSE 0 END),0) AS absent, "
            "COALESCE(SUM(CASE WHEN at.status='HALF_DAY' THEN 1 ELSE 0 END),0) AS half_day, "
            "COALESCE(SUM(CASE WHEN at.status='LEAVE' THEN 1 ELSE 0 END),0) AS on_leave, "
            "COALESCE(SUM(at.working_hrs),0) AS hours, "
            "COALESCE(SUM(at.overtime_hrs),0) AS overtime "
            "FROM employees e "
            "LEFT JOIN attendance at ON at.employee_id=e.id AND at.att_date>=? AND at.att_date<=? "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY e.id, e.employee_code, e.first_name, e.last_name ORDER BY e.employee_code",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["name"] = (str(r["first_name"] or "") + " " + str(r["last_name"] or "")).strip()
        return {
            "cols": {
                "employee_code": "Emp. Code", "name": "Name",
                "present": "Present", "absent": "Absent",
                "half_day": "Half Day", "on_leave": "Leave",
                "hours": "Hours", "overtime": "Overtime Hrs",
            },
            "rows": rows,
            "qty": ["hours", "overtime"],
            "sum": ["present", "absent", "half_day", "on_leave", "hours", "overtime"],
            "note": f"Month {raw_month}.",
        }

    R["ZATT"] = {
        "module": "HR", "group": "Time Management", "title": "Attendance Summary (Monthly)",
        "icon": "bi-clock-history",
        "filters": ["month", "department"],
        "run": _zatt,
    }

    # ZPRG — Payroll Register
    def _zprg(c: int, f: dict) -> dict:
        run_id = int(_d(f, "run", 0) or 0)
        if not run_id:
            run_id = int(
                ErpDB.fetch_value("SELECT id FROM payroll_runs WHERE company_id=? ORDER BY id DESC LIMIT 1", (c,)) or 0
            )
        rows = ErpDB.fetch_all(
            "SELECT e.employee_code, e.first_name, e.last_name, d.name AS department, "
            "COALESCE(SUM(CASE WHEN pd.comp_type='EARNING' THEN pd.amount ELSE 0 END),0) AS earnings, "
            "COALESCE(SUM(CASE WHEN pd.comp_type='DEDUCTION' THEN pd.amount ELSE 0 END),0) AS deductions "
            "FROM payroll_details pd "
            "JOIN payroll_runs pr ON pr.id=pd.payroll_run_id "
            "JOIN employees e ON e.id=pd.employee_id "
            "LEFT JOIN departments d ON d.id=e.department_id "
            "WHERE pr.company_id=? AND pr.id=? "
            "GROUP BY e.id, e.employee_code, e.first_name, e.last_name, d.name ORDER BY e.employee_code",
            (c, run_id),
        )
        for r in rows:
            r["name"] = (str(r["first_name"] or "") + " " + str(r["last_name"] or "")).strip()
            r["net"] = round(float(r["earnings"] or 0) - float(r["deductions"] or 0), 2)
        month_year = ErpDB.fetch_value("SELECT month_year FROM payroll_runs WHERE id=?", (run_id,))
        return {
            "cols": {
                "employee_code": "Emp. Code", "name": "Name", "department": "Department",
                "earnings": "Gross Earnings", "deductions": "Deductions", "net": "Net Pay",
            },
            "rows": rows,
            "money": ["earnings", "deductions", "net"],
            "sum": ["earnings", "deductions", "net"],
            "note": f"Payroll run {month_year}." if month_year else "No payroll run found.",
        }

    R["ZPRG"] = {
        "module": "HR", "group": "Payroll", "title": "Payroll Register",
        "icon": "bi-cash-coin",
        "filters": ["run"],
        "run": _zprg,
    }

    # ===================== PROD =====================

    # ZPOU — Production Output & Variance
    def _zpou(c: int, f: dict) -> dict:
        w = ["wo.company_id=?", "COALESCE(wo.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "wo.wo_date>=?", _d(f, "from"))
        _rp_add(w, a, "wo.wo_date<=?", _d(f, "to"))
        _rp_add(w, a, "wo.item_id=?", int(_d(f, "item", 0)) or None)
        _rp_add(w, a, "wo.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT wo.wo_number, wo.wo_date, i.item_code, i.item_name, "
            "wo.qty_planned, wo.qty_produced, wo.qty_rejected, "
            "wo.planned_end, wo.actual_end, wo.status "
            "FROM work_orders wo JOIN items i ON i.id=wo.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY wo.wo_date DESC, wo.id DESC",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["variance"] = float(r["qty_produced"] or 0) - float(r["qty_planned"] or 0)
            r["yield_pct"] = (
                round(float(r["qty_produced"] or 0) / float(r["qty_planned"]) * 100, 1)
                if float(r["qty_planned"] or 0) > 0 else 0
            )
        return {
            "cols": {
                "wo_number": "Order No.", "wo_date": "Order Date",
                "item_code": "Material", "item_name": "Description",
                "qty_planned": "Planned Qty", "qty_produced": "Produced Qty",
                "qty_rejected": "Rejected", "variance": "Variance", "yield_pct": "Yield %",
                "planned_end": "Planned End", "actual_end": "Actual End", "status": "Status",
            },
            "rows": rows,
            "qty": ["qty_planned", "qty_produced", "qty_rejected", "variance"],
            "sum": ["qty_planned", "qty_produced", "qty_rejected", "variance"],
        }

    R["ZPOU"] = {
        "module": "PROD", "group": "Production Orders", "title": "Production Output & Variance",
        "icon": "bi-gear-wide-connected",
        "filters": ["from", "to", "item", "status"],
        "status": ["DRAFT", "PLANNED", "RELEASED", "IN_PROGRESS", "COMPLETED", "CLOSED", "CANCELLED"],
        "run": _zpou,
    }

    # ZMIV — Material Consumption vs Requirement
    def _zmiv(c: int, f: dict) -> dict:
        w = ["wo.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "wo.wo_date>=?", _d(f, "from"))
        _rp_add(w, a, "wo.wo_date<=?", _d(f, "to"))
        _rp_add(w, a, "mi.item_id=?", int(_d(f, "item", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT wo.wo_number, wo.wo_date, i.item_code, i.item_name, "
            "mi.qty_required, mi.qty_issued, mi.issue_date "
            "FROM production_material_issues mi "
            "JOIN work_orders wo ON wo.id=mi.wo_id "
            "JOIN items i ON i.id=mi.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY wo.wo_date DESC, wo.id DESC, i.item_name",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["variance"] = float(r["qty_issued"] or 0) - float(r["qty_required"] or 0)
        return {
            "cols": {
                "wo_number": "Order No.", "wo_date": "Order Date",
                "item_code": "Component", "item_name": "Description",
                "qty_required": "Required", "qty_issued": "Issued",
                "variance": "Variance (Issued - Required)", "issue_date": "Issue Date",
            },
            "rows": rows,
            "qty": ["qty_required", "qty_issued", "variance"],
            "sum": ["qty_required", "qty_issued", "variance"],
        }

    R["ZMIV"] = {
        "module": "PROD", "group": "Production Orders", "title": "Material Consumption vs Requirement",
        "icon": "bi-box-arrow-in-down",
        "filters": ["from", "to", "item"],
        "run": _zmiv,
    }

    # ===================== BOM =====================

    # ZBOM — BOM Overview with Material Cost
    def _zbom(c: int, f: dict) -> dict:
        w = ["b.company_id=?", "COALESCE(b.is_deleted,0)=0"]
        a: list = [c]
        _rp_add(w, a, "b.item_id=?", int(_d(f, "item", 0)) or None)
        rows = ErpDB.fetch_all(
            "SELECT b.bom_number, b.revision, i.item_code, i.item_name, b.standard_qty, "
            "b.effective_from, b.effective_to, b.is_active, "
            "COUNT(bl.id) AS components, "
            "COALESCE(SUM(bl.qty_required * COALESCE(bl.unit_cost, 0)),0) AS material_cost "
            "FROM bom_headers b "
            "JOIN items i ON i.id=b.item_id "
            "LEFT JOIN bom_lines bl ON bl.bom_id=b.id "
            f"WHERE {' AND '.join(w)} "
            "GROUP BY b.id, b.bom_number, b.revision, i.item_code, i.item_name, "
            "b.standard_qty, b.effective_from, b.effective_to, b.is_active "
            "ORDER BY i.item_name, b.revision",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            r["is_active"] = "Active" if r["is_active"] else "Inactive"
        return {
            "cols": {
                "bom_number": "BOM No.", "revision": "Rev",
                "item_code": "Header Material", "item_name": "Description",
                "standard_qty": "Base Qty", "components": "Components",
                "material_cost": "Material Cost",
                "effective_from": "Valid From", "effective_to": "Valid To", "is_active": "Status",
            },
            "rows": rows,
            "qty": ["standard_qty"],
            "money": ["material_cost"],
        }

    R["ZBOM"] = {
        "module": "BOM", "group": "Bill of Material", "title": "BOM Overview with Material Cost",
        "icon": "bi-diagram-3-fill",
        "filters": ["item"],
        "run": _zbom,
    }

    # ===================== QM =====================

    # QA05 — List of Inspection Lots
    def _qa05(c: int, f: dict) -> dict:
        w = ["il.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "il.lot_date>=?", _d(f, "from"))
        _rp_add(w, a, "il.lot_date<=?", _d(f, "to"))
        _rp_add(w, a, "il.item_id=?", int(_d(f, "item", 0)) or None)
        _rp_add(w, a, "il.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT il.lot_number, il.lot_date, il.origin_type, i.item_code, i.item_name, "
            "il.qty, il.status, il.result, il.inspected_at "
            "FROM inspection_lots il "
            "LEFT JOIN items i ON i.id=il.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY il.lot_date DESC, il.id DESC",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "lot_number": "Lot No.", "lot_date": "Lot Date", "origin_type": "Origin",
                "item_code": "Material", "item_name": "Description",
                "qty": "Lot Qty", "status": "Status",
                "result": "Usage Decision", "inspected_at": "Inspected On",
            },
            "rows": rows,
            "qty": ["qty"],
            "sum": ["qty"],
        }

    R["QA05"] = {
        "module": "QM", "group": "Inspection", "title": "List of Inspection Lots",
        "icon": "bi-clipboard-data",
        "filters": ["from", "to", "item", "status"],
        "status": ["OPEN", "IN_PROGRESS", "COMPLETED", "CLOSED"],
        "run": _qa05,
    }

    # QM10 — List of Quality Notifications (NCR)
    def _qm10(c: int, f: dict) -> dict:
        w = ["n.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "n.ncr_date>=?", _d(f, "from"))
        _rp_add(w, a, "n.ncr_date<=?", _d(f, "to"))
        _rp_add(w, a, "n.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT n.ncr_number, n.ncr_date, i.item_code, i.item_name, n.source, "
            "n.qty_affected, n.severity, n.disposition, n.status, n.root_cause, n.corrective_action "
            "FROM non_conformances n "
            "LEFT JOIN items i ON i.id=n.item_id "
            f"WHERE {' AND '.join(w)} ORDER BY n.ncr_date DESC, n.id DESC",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "ncr_number": "NCR No.", "ncr_date": "Date",
                "item_code": "Material", "item_name": "Description",
                "source": "Source", "qty_affected": "Qty Affected",
                "severity": "Severity", "disposition": "Disposition",
                "status": "Status", "root_cause": "Root Cause",
                "corrective_action": "Corrective Action",
            },
            "rows": rows,
            "qty": ["qty_affected"],
            "sum": ["qty_affected"],
        }

    R["QM10"] = {
        "module": "QM", "group": "Quality Notification",
        "title": "List of Quality Notifications (NCR)",
        "icon": "bi-exclamation-triangle",
        "filters": ["from", "to", "status"],
        "status": ["OPEN", "IN_PROGRESS", "CLOSED"],
        "run": _qm10,
    }

    # ===================== PM =====================

    # IH08 — Equipment List
    def _ih08(c: int, f: dict) -> dict:
        w = ["company_id=?"]
        a: list = [c]
        _rp_add(w, a, "status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT asset_code, asset_name, asset_type, category, location, serial_number, "
            "manufacturer, model, purchase_date, purchase_price, current_value, warranty_expiry, status "
            "FROM assets "
            f"WHERE {' AND '.join(w)} ORDER BY asset_code",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "asset_code": "Equipment", "asset_name": "Description",
                "asset_type": "Type", "category": "Category", "location": "Location",
                "serial_number": "Serial No.", "manufacturer": "Manufacturer", "model": "Model",
                "purchase_date": "Acquired On", "purchase_price": "Acquisition Cost",
                "current_value": "Book Value", "warranty_expiry": "Warranty Until",
                "status": "Status",
            },
            "rows": rows,
            "money": ["purchase_price", "current_value"],
            "sum": ["purchase_price", "current_value"],
        }

    R["IH08"] = {
        "module": "PM", "group": "Equipment & Orders", "title": "Equipment List",
        "icon": "bi-cpu",
        "filters": ["status"],
        "status": ["ACTIVE", "UNDER_MAINTENANCE", "INACTIVE", "DISPOSED"],
        "run": _ih08,
    }

    # IW39 — List of Maintenance Orders
    def _iw39(c: int, f: dict) -> dict:
        w = ["mo.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "mo.mo_date>=?", _d(f, "from"))
        _rp_add(w, a, "mo.mo_date<=?", _d(f, "to"))
        _rp_add(w, a, "mo.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT mo.mo_number, mo.mo_date, ast.asset_code, ast.asset_name, "
            "mo.mo_type, mo.priority, mo.planned_start, mo.planned_end, mo.actual_end, mo.status, "
            "mo.material_cost, mo.labor_cost, mo.total_cost "
            "FROM maintenance_orders mo "
            "LEFT JOIN assets ast ON ast.id=mo.asset_id "
            f"WHERE {' AND '.join(w)} ORDER BY mo.mo_date DESC, mo.id DESC",
            tuple(v for v in a if v is not None),
        )
        return {
            "cols": {
                "mo_number": "Order No.", "mo_date": "Date",
                "asset_code": "Equipment", "asset_name": "Description",
                "mo_type": "Order Type", "priority": "Priority",
                "planned_start": "Planned Start", "planned_end": "Planned End",
                "actual_end": "Actual End", "status": "Status",
                "material_cost": "Material Cost", "labor_cost": "Labour Cost",
                "total_cost": "Total Cost",
            },
            "rows": rows,
            "money": ["material_cost", "labor_cost", "total_cost"],
            "sum": ["material_cost", "labor_cost", "total_cost"],
        }

    R["IW39"] = {
        "module": "PM", "group": "Equipment & Orders", "title": "List of Maintenance Orders",
        "icon": "bi-tools",
        "filters": ["from", "to", "status"],
        "status": ["PLANNED", "RELEASED", "IN_PROGRESS", "COMPLETED", "CLOSED", "CANCELLED"],
        "run": _iw39,
    }

    # ===================== PS =====================

    # ZPRB — Project Budget vs Actual
    def _zprb(c: int, f: dict) -> dict:
        w = ["p.company_id=?"]
        a: list = [c]
        _rp_add(w, a, "p.status=?", _d(f, "status"))
        rows = ErpDB.fetch_all(
            "SELECT p.project_code, p.project_name, p.status, p.start_date, p.end_date, "
            "p.planned_budget, "
            "(SELECT COALESCE(SUM(cp.amount),0) FROM project_cost_postings cp "
            " WHERE cp.project_id=p.id) AS actual "
            "FROM projects p "
            f"WHERE {' AND '.join(w)} ORDER BY p.project_code",
            tuple(v for v in a if v is not None),
        )
        for r in rows:
            budget = float(r["planned_budget"] or 0)
            actual = float(r["actual"] or 0)
            r["variance"] = budget - actual
            r["used_pct"] = round(actual / budget * 100, 1) if budget > 0 else 0
        return {
            "cols": {
                "project_code": "Project", "project_name": "Description",
                "status": "Status", "start_date": "Start", "end_date": "Finish",
                "planned_budget": "Budget", "actual": "Actual Cost",
                "variance": "Remaining", "used_pct": "Used %",
            },
            "rows": rows,
            "money": ["planned_budget", "actual", "variance"],
            "sum": ["planned_budget", "actual", "variance"],
        }

    R["ZPRB"] = {
        "module": "PS", "group": "Project Reports", "title": "Project Budget vs Actual",
        "icon": "bi-kanban",
        "filters": ["status"],
        "status": ["PLANNED", "ACTIVE", "ON_HOLD", "COMPLETED", "CLOSED", "CANCELLED"],
        "run": _zprb,
    }

    # ===================== ADMIN =====================

    # SUIM — Users with Roles (Authorization Report)
    def _suim(c: int, f: dict) -> dict:
        rows = ErpDB.fetch_all(
            "SELECT u.username, u.full_name, u.email, u.is_active, r.code AS role_code, r.name AS role_name "
            "FROM users u "
            "LEFT JOIN user_roles ur ON ur.user_id=u.id "
            "LEFT JOIN roles r ON r.id=ur.role_id "
            "WHERE u.company_id=? AND COALESCE(u.is_deleted,0)=0 ORDER BY u.username, r.code",
            (c,),
        )
        for r in rows:
            r["is_active"] = "Active" if r["is_active"] else "Locked"
        return {
            "cols": {
                "username": "User", "full_name": "Name", "email": "Email",
                "is_active": "Status", "role_code": "Role", "role_name": "Role Name",
            },
            "rows": rows,
            "group_by": "username",
        }

    R["SUIM"] = {
        "module": "ADMIN", "group": "Authorization & Audit",
        "title": "Users with Roles (Authorization Report)",
        "icon": "bi-shield-lock",
        "filters": [],
        "run": _suim,
    }

    # SCDO — Change Documents (Audit Log)
    def _scdo(c: int, f: dict) -> dict:
        w = ["company_id=?"]
        a: list = [c]
        frm = _d(f, "from")
        to = _d(f, "to")
        txt = _d(f, "text")
        if frm:
            w.append("created_at>=?")
            a.append(f"{frm} 00:00:00")
        if to:
            w.append("created_at<=?")
            a.append(f"{to} 23:59:59")
        if txt:
            w.append("(username LIKE ? OR tcode LIKE ? OR table_name LIKE ?)")
            like = f"%{txt}%"
            a += [like, like, like]
        rows = ErpDB.fetch_all(
            "SELECT created_at, username, tcode, table_name, record_id, action, ip_address "
            "FROM audit_logs "
            f"WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT 2000",
            tuple(a),
        )
        return {
            "cols": {
                "created_at": "Date / Time", "username": "User", "tcode": "T-Code",
                "table_name": "Table", "record_id": "Record",
                "action": "Action", "ip_address": "IP Address",
            },
            "rows": rows,
            "note": "Latest 2,000 entries matching the selection.",
        }

    R["SCDO"] = {
        "module": "ADMIN", "group": "Authorization & Audit", "title": "Change Documents (Audit Log)",
        "icon": "bi-clock-history",
        "filters": ["from", "to", "text"],
        "text_label": "User / T-code / table",
        "run": _scdo,
    }

    return R


# Lazy singleton — module-level cache
_DEFS: Optional[dict] = None


def report_defs() -> dict[str, dict]:
    """Return the full report-definition registry (cached after first call)."""
    global _DEFS
    if _DEFS is None:
        _DEFS = _report_defs()
    return _DEFS


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def run_report(tcode: str, company_id: Optional[int] = None, filters: Optional[dict] = None) -> dict:
    """
    Run one report by T-code.

    Args:
        tcode      : report T-code (e.g. "ME2L", "VA05").
        company_id : company to scope to; defaults to ErpAuth.company_id().
        filters    : dict of filter values (all keys optional; missing ones default to '' / 0).

    Returns:
        Result dict: { cols, rows, qty?, money?, sum?, group_by?, note? }

    Raises:
        PermissionError : caller lacks the T-code.
        KeyError        : unknown T-code.
        RuntimeError    : DB errors propagate unchanged.
    """
    if not report_can_access(tcode):
        raise PermissionError(f"Access denied: T-code {tcode}")
    defs = report_defs()
    if tcode not in defs:
        raise KeyError(f"Unknown report T-code: {tcode}")
    cid = company_id or ErpAuth.company_id()
    f = filters or {}
    return defs[tcode]["run"](cid, f)


def report_tcode_rows() -> list[tuple]:
    """
    T-code rows for the installer:  (tcode, module, description, file, icon, sort).
    Mirrors PHP msme_erp_report_tcode_rows().  'file' is set to the Python module path
    so the installer can record it; routing uses the T-code, not the file.
    """
    rows = []
    sort = 300
    for tc, d in report_defs().items():
        rows.append((tc, d["module"], d["title"], "core/reports.py", d["icon"], sort))
        sort += 1
    rows.append(("ZREP", "ADMIN", "Report Center (all reports)", "core/reports.py",
                 "bi-bar-chart-line-fill", 15))
    return rows
