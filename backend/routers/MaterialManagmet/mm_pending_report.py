"""
MM Pending / Status Report (T-Code: ME2PN)
Converted from modules/mm/pending_report.php

One From/To + optional Category/Unit/Department filtered report: all PRs,
all POs, PRs not yet converted to a PO, POs with material not fully
received, and supplier invoices still awaiting payment. Every date range
filters on the document's own date column (pr_date/po_date/invoice_date),
never on today's date — "pending" status is evaluated independently of
whether the document itself falls inside the period.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from core.db import ErpDB
from core.auth import ErpAuth
from routers.deps import require_tcode, ok

router = APIRouter(prefix="/api/mm-pending-report", tags=["mm-pending-report"])


def _pdate(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _days_since(d_str: Optional[str], today: date) -> Optional[int]:
    d = _pdate(d_str)
    return (today - d).days if d else None


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _cc_filter(alias: str, category_id: Optional[int], unit_id: Optional[int],
               dept_id: Optional[int]):
    """Mirrors PHP's $ccFilter — appends AND <alias>.category_id=? etc. for
    whichever of the three cost-center levels is actually selected."""
    sql, params = "", []
    if category_id:
        sql += f" AND {alias}.category_id = ?"
        params.append(category_id)
    if unit_id:
        sql += f" AND {alias}.unit_id = ?"
        params.append(unit_id)
    if dept_id:
        sql += f" AND {alias}.department_id = ?"
        params.append(dept_id)
    return sql, params


@router.get("/lookups")
def cost_center_lookups(auth: dict = Depends(require_tcode("ME2PN"))):
    company_id = auth["company_id"]
    categories = ErpDB.fetch_all(
        "SELECT id, code, name FROM factory_categories WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    units = ErpDB.fetch_all(
        "SELECT id, code, name, category_id FROM plants WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    depts = ErpDB.fetch_all(
        "SELECT id, code, name, plant_id FROM departments WHERE company_id=? AND is_active=1 ORDER BY code",
        [company_id])
    return ok({"categories": categories, "units": units, "departments": depts})


@router.get("/summary")
def pending_report_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    category_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    department_id: Optional[int] = None,
    auth: dict = Depends(require_tcode("ME2PN")),
):
    company_id = auth["company_id"]
    today = date.today()

    frm = _pdate(from_) or _month_start(today)
    to = _pdate(to_) or today
    from_s, to_s = str(frm), str(to)

    cc_sql_pr, cc_params_pr = _cc_filter("pr", category_id, unit_id, department_id)
    cc_sql_po, cc_params_po = _cc_filter("po", category_id, unit_id, department_id)

    # -----------------------------------------------------------
    # 1. Purchase Requisitions raised in the period
    # -----------------------------------------------------------
    pr_list = ErpDB.fetch_all(
        f"""SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.status, pr.required_by,
                   d.name AS dept_name, u.full_name AS requester,
                   COUNT(prl.id) AS line_count,
                   COALESCE(SUM(prl.qty_requested * prl.est_price), 0) AS est_value,
                   SUM(CASE WHEN prl.status = 'OPEN' THEN 1 ELSE 0 END) AS open_lines
            FROM purchase_requisitions pr
            LEFT JOIN departments d ON d.id = pr.dept_id
            LEFT JOIN users u ON u.id = pr.requested_by
            LEFT JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
            WHERE pr.company_id = ? AND pr.pr_date BETWEEN ? AND ? AND pr.status != 'CANCELLED'{cc_sql_pr}
            GROUP BY pr.id ORDER BY pr.pr_date DESC, pr.id DESC""",
        [company_id, from_s, to_s] + cc_params_pr)

    # -----------------------------------------------------------
    # 2. Purchase Orders raised in the period
    # -----------------------------------------------------------
    po_list = ErpDB.fetch_all(
        f"""SELECT po.id, po.po_number, po.po_date, po.status, po.release_status, po.grand_total,
                   po.delivery_date, s.name AS supplier_name, s.supplier_code,
                   COALESCE(SUM(pol.qty_ordered), 0) AS qty_ordered,
                   COALESCE(SUM(pol.qty_received), 0) AS qty_received
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            LEFT JOIN purchase_order_lines pol ON pol.po_id = po.id
            WHERE po.company_id = ? AND po.po_date BETWEEN ? AND ? AND po.is_deleted = 0
              AND po.status != 'CANCELLED'{cc_sql_po}
            GROUP BY po.id ORDER BY po.po_date DESC, po.id DESC""",
        [company_id, from_s, to_s] + cc_params_po)

    # -----------------------------------------------------------
    # 3. Purchase Requisitions NOT yet processed (still have open lines)
    # -----------------------------------------------------------
    pr_pending = ErpDB.fetch_all(
        f"""SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.status, pr.required_by,
                   d.name AS dept_name, u.full_name AS requester,
                   COUNT(prl.id) AS open_line_count,
                   COALESCE(SUM(prl.qty_requested), 0) AS open_qty,
                   COALESCE(SUM(prl.qty_requested * prl.est_price), 0) AS open_value
            FROM purchase_requisitions pr
            JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id AND prl.status = 'OPEN'
            LEFT JOIN departments d ON d.id = pr.dept_id
            LEFT JOIN users u ON u.id = pr.requested_by
            WHERE pr.company_id = ? AND pr.pr_date BETWEEN ? AND ? AND pr.status NOT IN ('CANCELLED'){cc_sql_pr}
            GROUP BY pr.id ORDER BY pr.pr_date ASC, pr.id ASC""",
        [company_id, from_s, to_s] + cc_params_pr)
    for r in pr_pending:
        r["days_pending"] = _days_since(r["pr_date"], today)
    pr_pending_value = round(sum(float(r["open_value"] or 0) for r in pr_pending), 2)

    # -----------------------------------------------------------
    # 4. PO made but material not (fully) received
    # -----------------------------------------------------------
    po_pending = ErpDB.fetch_all(
        f"""SELECT po.id, po.po_number, po.po_date, po.status, po.release_status, po.delivery_date,
                   s.name AS supplier_name, s.supplier_code,
                   SUM(pol.qty_ordered) AS qty_ordered,
                   SUM(pol.qty_received) AS qty_received,
                   SUM(pol.qty_ordered - pol.qty_received) AS qty_pending,
                   SUM((pol.qty_ordered - pol.qty_received) * pol.unit_price) AS pending_value
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            JOIN purchase_order_lines pol ON pol.po_id = po.id
            WHERE po.company_id = ? AND po.po_date BETWEEN ? AND ? AND po.is_deleted = 0
              AND po.release_status = 'RELEASED' AND po.status NOT IN ('CLOSED','CANCELLED','REJECTED'){cc_sql_po}
            GROUP BY po.id HAVING SUM(pol.qty_ordered - pol.qty_received) > 0.0001
            ORDER BY po.po_date ASC, po.id ASC""",
        [company_id, from_s, to_s] + cc_params_po)
    for r in po_pending:
        r["days_pending"] = _days_since(r["po_date"], today)
        r["delivery_overdue"] = bool(r["delivery_date"] and r["delivery_date"] < str(today))
    po_pending_value = round(sum(float(r["pending_value"] or 0) for r in po_pending), 2)

    # -----------------------------------------------------------
    # 5. Payment pending (supplier invoices, unpaid balance)
    # Supplier Invoice (MR01) never saves its own cost-center columns, so a
    # cost-center filter here goes through the invoice's linked PO instead —
    # an invoice with no po_id is excluded whenever the filter is active.
    # -----------------------------------------------------------
    cc_sql_si_po, cc_params_si_po = _cc_filter("po", category_id, unit_id, department_id)
    payment_pending = ErpDB.fetch_all(
        f"""SELECT si.id, si.invoice_number, si.invoice_date, si.due_date, si.status,
                   si.total_amount, si.paid_amount, si.due_amount,
                   s.name AS supplier_name, s.supplier_code, po.po_number
            FROM supplier_invoices si
            JOIN suppliers s ON s.id = si.supplier_id
            LEFT JOIN purchase_orders po ON po.id = si.po_id
            WHERE si.company_id = ? AND si.invoice_date BETWEEN ? AND ?
              AND si.status NOT IN ('PAID','CANCELLED') AND si.due_amount > 0{cc_sql_si_po}
            ORDER BY si.due_date ASC, si.id ASC""",
        [company_id, from_s, to_s] + cc_params_si_po)
    for r in payment_pending:
        r["is_overdue"] = bool(r["due_date"] and r["due_date"] < str(today))
        r["days_overdue"] = _days_since(r["due_date"], today) if r["is_overdue"] else None
    payment_pending_value = round(sum(float(r["due_amount"] or 0) for r in payment_pending), 2)

    cards = [
        {"view": "pr", "count": len(pr_list), "label": "Purchase Requests"},
        {"view": "po", "count": len(po_list), "label": "Purchase Orders"},
        {"view": "pr_pending", "count": len(pr_pending), "label": "PR Not Processed",
         "value": pr_pending_value},
        {"view": "po_pending", "count": len(po_pending), "label": "PO — Material Not Received",
         "value": po_pending_value},
        {"view": "payment_pending", "count": len(payment_pending), "label": "Payment Pending",
         "value": payment_pending_value},
    ]

    return ok({
        "period": {"from": from_s, "to": to_s},
        "cost_center_filter": {"category_id": category_id, "unit_id": unit_id,
                                "department_id": department_id},
        "cards": cards,
        "pr": {"rows": pr_list},
        "po": {"rows": po_list},
        "pr_pending": {"rows": pr_pending, "total_value": pr_pending_value},
        "po_pending": {"rows": po_pending, "total_value": po_pending_value},
        "payment_pending": {"rows": payment_pending, "total_value": payment_pending_value},
    })