"""
MM Procurement Follow-up Reports (T-Code: ZMMR)
Converted from modules/mm/mm_reports.php

Read-only. Five tabs, computed in Python from two base queries
(PR lines + PO lines w/ GRN aggregates) exactly as the PHP did it —
SQLite has no DATEDIFF(), so all "days since / days late" math moved
to Python, and the information_schema.COLUMNS probe for
purchase_order_lines.supplier_notified_at became a PRAGMA check.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.core.db import ErpDB
from backend.core.auth import ErpAuth
from backend.routers.deps import require_tcode, ok

router = APIRouter(prefix="/api/mm-reports", tags=["mm-reports"])

TOL = 0.0005


def _pdate(s: Optional[str]) -> Optional[date]:
    """Parse the first 10 chars of a stored date/datetime string -> date, or None."""
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _days_between(a: date, b: date) -> int:
    return (b - a).days


def _column_exists(table: str, column: str) -> bool:
    rows = ErpDB.fetch_all(f"PRAGMA table_info({table})", [])
    return any(r["name"] == column for r in rows)


def _age_badge_level(d: int) -> str:
    return "danger" if d > 15 else ("warning" if d > 7 else "light")


def _grade(score: Optional[float]):
    if score is None:
        return None, None
    if score >= 85:
        return "A", "success"
    if score >= 70:
        return "B", "primary"
    if score >= 50:
        return "C", "warning"
    return "D", "danger"


@router.get("/summary")
def mm_reports_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    supplier_id: Optional[int] = None,
    late_mode: str = Query("pending", pattern="^(pending|received_late)$"),
    w_d: int = 40,
    w_q: int = 35,
    w_t: int = 25,
    auth: ErpAuth = Depends(require_tcode("ZMMR")),
):
    company_id = ErpAuth.company_id()
    today = date.today()

    # ---------------- Filters ----------------
    frm = _pdate(from_) or (today - timedelta(days=90))
    to = _pdate(to_) or today
    if frm > to:
        frm, to = to, frm
    from_s, to_s = str(frm), str(to)

    w_d = max(0, min(100, w_d))
    w_q = max(0, min(100, w_q))
    w_t = max(0, min(100, w_t))
    if w_d + w_q + w_t == 0:
        w_d, w_q, w_t = 40, 35, 25

    suppliers = ErpDB.fetch_all(
        "SELECT id, supplier_code, name FROM suppliers WHERE company_id=? AND is_deleted=0 ORDER BY name",
        [company_id])

    has_notified = _column_exists("purchase_orders", "supplier_notified_at")
    notified_sel = "po.supplier_notified_at" if has_notified else "NULL"

    # ---------------------------------------------------------------
    # Shared base query: one row per released PO line w/ GRN aggregates
    # ---------------------------------------------------------------
    po_line_sql = f"""
        SELECT po.id AS po_id, po.po_number, po.po_date, po.delivery_date AS po_delivery_date,
               {notified_sel} AS notified_at, po.payment_terms,
               s.id AS supplier_id, s.supplier_code, s.name AS supplier_name,
               pol.id AS pol_id, pol.line_num, pol.qty_ordered, pol.unit_price,
               COALESCE(pol.delivery_date, po.delivery_date) AS due_date,
               i.item_code, i.item_name, un.code AS uom,
               COALESCE(gr.recvd, 0)       AS qty_recvd,
               COALESCE(gr.qc_hold, 0)     AS qty_qc_hold,
               COALESCE(gr.recv_posted, 0) AS recv_posted,
               COALESCE(gr.acc_posted, 0)  AS acc_posted,
               COALESCE(gr.price_dev, 0)   AS price_dev,
               COALESCE(gr.grn_lines, 0)   AS grn_lines,
               gr.last_grn, gr.first_grn
        FROM purchase_orders po
        JOIN suppliers s ON s.id = po.supplier_id
        JOIN purchase_order_lines pol ON pol.po_id = po.id
        JOIN items i ON i.id = pol.item_id
        LEFT JOIN units_of_measure un ON un.id = pol.uom_id
        LEFT JOIN (
            SELECT grl.po_line_id,
                   SUM(grl.qty_received) AS recvd,
                   SUM(CASE WHEN g.status = 'PENDING_QC' THEN grl.qty_received ELSE 0 END) AS qc_hold,
                   SUM(CASE WHEN g.status = 'POSTED' THEN grl.qty_received ELSE 0 END) AS recv_posted,
                   SUM(CASE WHEN g.status = 'POSTED' THEN grl.qty_accepted ELSE 0 END) AS acc_posted,
                   SUM(CASE WHEN COALESCE(grl.unit_price,0) > 0 AND grl.unit_price > pl2.unit_price * 1.005 THEN 1 ELSE 0 END) AS price_dev,
                   COUNT(grl.id) AS grn_lines,
                   MAX(g.grn_date) AS last_grn, MIN(g.grn_date) AS first_grn
            FROM goods_receipt_lines grl
            JOIN goods_receipts g ON g.id = grl.grn_id AND g.status IN ('POSTED','PENDING_QC')
            JOIN purchase_order_lines pl2 ON pl2.id = grl.po_line_id
            GROUP BY grl.po_line_id
        ) gr ON gr.po_line_id = pol.id
        WHERE po.company_id = ? AND po.is_deleted = 0 AND po.release_status = 'RELEASED'
          AND po.status NOT IN ('CANCELLED','REJECTED','DRAFT')
          AND pol.status NOT IN ('CANCELLED')
          AND po.po_date BETWEEN ? AND ?
    """
    po_line_params = [company_id, from_s, to_s]
    if supplier_id:
        po_line_sql += " AND po.supplier_id = ?"
        po_line_params.append(supplier_id)
    po_line_sql += " ORDER BY po.po_date ASC, po.id ASC, pol.line_num ASC"

    po_lines = ErpDB.fetch_all(po_line_sql, po_line_params)

    # -----------------------------------------------------------
    # Tab 1 — PR released, PO not created
    # -----------------------------------------------------------
    pr_open = ErpDB.fetch_all(
        """SELECT pr.id AS pr_id, pr.pr_number, pr.pr_date, pr.required_by AS pr_required_by,
                  d.name AS dept_name, u.full_name AS requester,
                  prl.line_num, prl.est_price, prl.required_by AS line_required_by,
                  COALESCE(prl.qty_approved, prl.qty_requested) AS qty,
                  i.item_code, i.item_name, un.code AS uom,
                  (SELECT COUNT(*) FROM rfqs rq WHERE rq.pr_id = pr.id) AS rfq_count
           FROM purchase_requisitions pr
           JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id AND prl.status = 'OPEN'
           JOIN items i ON i.id = prl.item_id
           LEFT JOIN units_of_measure un ON un.id = prl.uom_id
           LEFT JOIN departments d ON d.id = pr.dept_id
           LEFT JOIN users u ON u.id = pr.requested_by
           WHERE pr.company_id = ? AND pr.status = 'APPROVED' AND pr.pr_date BETWEEN ? AND ?
           ORDER BY pr.pr_date ASC, pr.id ASC, prl.line_num ASC""",
        [company_id, from_s, to_s])

    pr_open_value = 0.0
    pr_open_docs = set()
    for r in pr_open:
        qty = float(r["qty"] or 0)
        price = float(r["est_price"] or 0)
        r["est_value"] = round(qty * price, 2)
        pr_open_value += qty * price
        pr_open_docs.add(r["pr_id"])
        req = r["line_required_by"] or r["pr_required_by"]
        r["overdue"] = bool(req and req < str(today))
        r["age_days"] = _days_between(_pdate(r["pr_date"]), today)
    pr_open_value = round(pr_open_value, 2)

    # -----------------------------------------------------------
    # Tab 2 / 3 — derived from po_lines
    # -----------------------------------------------------------
    po_open, po_late, po_late_received = [], [], []
    no_due_date = 0
    for r in po_lines:
        r = dict(r)
        ordered = float(r["qty_ordered"] or 0)
        recvd = float(r["qty_recvd"] or 0)
        pend = ordered - recvd
        due = r["due_date"]
        if pend > TOL:
            r["qty_pending"] = pend
            r["pending_value"] = pend * float(r["unit_price"] or 0)
            r["days_since_po"] = _days_between(_pdate(r["po_date"]), today)
            po_open.append(r)
            if not due:
                no_due_date += 1
            elif due < str(today):
                r["days_late"] = _days_between(_pdate(due), today)
                po_late.append(r)
        elif due and r["last_grn"] and r["last_grn"] > due:
            r["days_late"] = _days_between(_pdate(due), _pdate(r["last_grn"]))
            po_late_received.append(r)

    po_late.sort(key=lambda x: x["days_late"], reverse=True)
    po_late_received.sort(key=lambda x: x["days_late"], reverse=True)
    po_open_value = round(sum(r["pending_value"] for r in po_open), 2)
    po_open_docs = len({r["po_id"] for r in po_open})
    po_late_value = round(sum(r["pending_value"] for r in po_late), 2)
    po_late_docs = len({r["po_id"] for r in po_late})
    late_rows = po_late_received if late_mode == "received_late" else po_late

    # -----------------------------------------------------------
    # Tab 5 — GRN posted, stock not transferred onward (FIFO match)
    # -----------------------------------------------------------
    grn_rows = ErpDB.fetch_all(
        """SELECT g.id AS grn_id, g.grn_number, g.grn_date, g.status AS grn_status, g.warehouse_id,
                  w.name AS wh_name, grl.id AS grl_id, grl.item_id, grl.qty_received, grl.qty_accepted,
                  i.item_code, i.item_name, un.code AS uom,
                  po.po_number, s.name AS supplier_name, d.name AS req_dept
           FROM goods_receipts g
           JOIN goods_receipt_lines grl ON grl.grn_id = g.id
           JOIN items i ON i.id = grl.item_id
           LEFT JOIN units_of_measure un ON un.id = grl.uom_id
           LEFT JOIN warehouses w ON w.id = g.warehouse_id
           LEFT JOIN suppliers s ON s.id = g.supplier_id
           LEFT JOIN purchase_orders po ON po.id = g.po_id
           LEFT JOIN purchase_order_lines pol ON pol.id = grl.po_line_id
           LEFT JOIN purchase_requisition_lines prl ON prl.id = pol.pr_line_id
           LEFT JOIN purchase_requisitions pr ON pr.id = prl.pr_id
           LEFT JOIN departments d ON d.id = pr.dept_id
           WHERE g.company_id = ? AND g.status IN ('POSTED','PENDING_QC')
           ORDER BY g.grn_date ASC, g.id ASC, grl.id ASC""",
        [company_id])
    grn_rows = [dict(r) for r in grn_rows]

    xfers = ErpDB.fetch_all(
        """SELECT st.from_wh_id, stl.item_id, st.transfer_date, SUM(stl.qty_transfer) AS qty
           FROM stock_transfers st
           JOIN stock_transfer_lines stl ON stl.sto_id = st.id
           WHERE st.company_id = ? AND st.status = 'TRANSFERRED'
           GROUP BY st.from_wh_id, stl.item_id, st.transfer_date
           ORDER BY st.transfer_date ASC""",
        [company_id])

    grn_pool: dict[str, list[int]] = {}
    for k, r in enumerate(grn_rows):
        r["xfer_qty"] = 0.0
        r["last_xfer"] = None
        if r["grn_status"] == "POSTED":
            grn_pool.setdefault(f'{r["warehouse_id"]}|{r["item_id"]}', []).append(k)

    for x in xfers:
        key = f'{x["from_wh_id"]}|{x["item_id"]}'
        left = float(x["qty"] or 0)
        for k in grn_pool.get(key, []):
            if left <= TOL:
                break
            if grn_rows[k]["grn_date"] > x["transfer_date"]:
                continue
            room = float(grn_rows[k]["qty_accepted"] or 0) - grn_rows[k]["xfer_qty"]
            if room <= TOL:
                continue
            take = min(room, left)
            grn_rows[k]["xfer_qty"] += take
            grn_rows[k]["last_xfer"] = x["transfer_date"]
            left -= take

    grn_pending = []
    for r in grn_rows:
        if r["grn_date"] < from_s or r["grn_date"] > to_s:
            continue
        if r["grn_status"] == "PENDING_QC":
            r["pending"] = float(r["qty_received"] or 0)
            r["state"] = "QC"
        else:
            r["pending"] = float(r["qty_accepted"] or 0) - r["xfer_qty"]
            if r["pending"] <= TOL:
                continue
            r["state"] = "PARTIAL" if r["xfer_qty"] > TOL else "NONE"
        r["days_since"] = _days_between(_pdate(r["grn_date"]), today)
        grn_pending.append(r)
    grn_pending_docs = len({r["grn_id"] for r in grn_pending})

    # -----------------------------------------------------------
    # Tab 4 — Supplier ranking (computed live from po_lines)
    # -----------------------------------------------------------
    agg: dict[int, dict] = {}
    for r in po_lines:
        sid = r["supplier_id"]
        a = agg.setdefault(sid, {
            "name": r["supplier_name"], "code": r["supplier_code"], "pos": set(),
            "eval": 0, "ontime": 0, "delay_sum": 0, "late_lines": 0,
            "ord_due": 0.0, "got_due": 0.0,
            "recv_posted": 0.0, "acc_posted": 0.0, "grn_lines": 0, "price_dev": 0,
        })
        a["pos"].add(r["po_id"])
        ordered = float(r["qty_ordered"] or 0)
        recvd = float(r["qty_recvd"] or 0)
        due = r["due_date"]
        full = (ordered - recvd) <= TOL

        if due and ordered > TOL and (full or due < str(today)):
            a["eval"] += 1
            a["ord_due"] += ordered
            a["got_due"] += min(recvd, ordered)
            if full and r["last_grn"] and r["last_grn"] <= due:
                a["ontime"] += 1
            else:
                a["late_lines"] += 1
                end_date = r["last_grn"] if (full and r["last_grn"]) else str(today)
                a["delay_sum"] += max(0, _days_between(_pdate(due), _pdate(end_date)))
        a["recv_posted"] += float(r["recv_posted"] or 0)
        a["acc_posted"] += float(r["acc_posted"] or 0)
        a["grn_lines"] += int(r["grn_lines"] or 0)
        a["price_dev"] += int(r["price_dev"] or 0)

    ranked, unranked = [], []
    for sid, a in agg.items():
        d_score = 100 * a["ontime"] / a["eval"] if a["eval"] > 0 else None
        q_score = 100 * a["acc_posted"] / a["recv_posted"] if a["recv_posted"] > 0 else None
        fill = 100 * a["got_due"] / a["ord_due"] if a["ord_due"] > 0 else None
        price = 100 * (a["grn_lines"] - a["price_dev"]) / a["grn_lines"] if a["grn_lines"] > 0 else None
        if fill is not None and price is not None:
            t_score = 0.6 * fill + 0.4 * price
        else:
            t_score = fill if fill is not None else price

        parts = [(d_score, w_d), (q_score, w_q), (t_score, w_t)]
        s, wsum = 0.0, 0
        for sc, w in parts:
            if sc is not None and w > 0:
                s += sc * w
                wsum += w
        overall = s / wsum if wsum > 0 else None
        grade, grade_color = _grade(overall)

        row = {
            **{k: v for k, v in a.items() if k != "pos"},
            "pos_count": len(a["pos"]), "sid": sid,
            "d": d_score, "q": q_score, "t": t_score, "fill": fill, "price": price,
            "overall": round(overall, 1) if overall is not None else None,
            "avg_delay": round(a["delay_sum"] / a["late_lines"], 1) if a["late_lines"] > 0 else 0,
            "grade": grade, "grade_color": grade_color,
        }
        (unranked if overall is None else ranked).append(row)

    ranked.sort(key=lambda x: (x["overall"], x["eval"]), reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    # ---------------- Summary cards ----------------
    cards = [
        {"view": "pr_open", "count": len(pr_open_docs), "label": "PR Released — PO Not Created",
         "sub": f"{len(pr_open)} lines", "value": pr_open_value, "color": "warning"},
        {"view": "po_open", "count": po_open_docs, "label": "PO Released — Material Not Received",
         "sub": "pending", "value": po_open_value, "color": "danger"},
        {"view": "po_late", "count": po_late_docs, "label": "PO Sent — Not Came In Time",
         "sub": f"{len(po_late)} overdue lines", "value": po_late_value, "color": "danger"},
        {"view": "ranking", "count": len(ranked), "label": "Suppliers Ranked",
         "sub": "delivery · quality · PO terms", "color": "primary"},
        {"view": "grn_transfer", "count": grn_pending_docs, "label": "GRN — Not Transferred to Dept",
         "sub": f"{len(grn_pending)} lines", "color": "warning"},
    ]

    return ok({
        "period": {"from": from_s, "to": to_s},
        "suppliers": suppliers,
        "weights": {"w_d": w_d, "w_q": w_q, "w_t": w_t},
        "cards": cards,
        "pr_open": {"rows": pr_open, "total_value": pr_open_value, "doc_count": len(pr_open_docs)},
        "po_open": {"rows": po_open, "total_value": po_open_value, "doc_count": po_open_docs,
                    "no_due_date_count": no_due_date},
        "po_late": {
            "late_mode": late_mode,
            "rows": late_rows,
            "pending_count": len(po_late), "received_late_count": len(po_late_received),
            "overdue_value": po_late_value,
        },
        "ranking": {"ranked": ranked, "unranked": unranked},
        "grn_transfer": {"rows": grn_pending, "doc_count": grn_pending_docs},
    })