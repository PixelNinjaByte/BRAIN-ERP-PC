"""
Brain ERP — Purchase Return (PRTN) and Sales Return (SRTN)
Port of PHP core/returns.php → Python / SQLite.

PURCHASE RETURN (goods back to the supplier, against a posted GRN):
  - Stock out through a movement-122 goods document (stock_movements,
    stock_balances, GR/IR journal), via ErpDB.process_grn().
  - The PO gets the returned quantity back (qty_received down, PO re-opened).
  - Optional supplier DEBIT NOTE (GST reversed, GR/IR offset) against the
    booked supplier invoice.

SALES RETURN (goods back from the customer, against a sales invoice):
  - Stock in (movement 651) at the current average cost, or scrap (no stock).
  - CREDIT NOTE with the exact GST of the returned goods, applied to the
    invoice (open note if the invoice is settled).

Both documents block over-returns (cumulative returned qty can never exceed
the original quantity).

NOT PORTED (and why):
  - msme_erp_returns_ensure_schema(): DDL for purchase_returns,
    purchase_return_lines, sales_returns, sales_return_lines, and the
    fin_notes columns. In Brain ERP, tables are created up front by
    installer.py / schema.
  - msme_erp_returns_register_tcodes(): PRTN and SRTN are registered in
    installer.py's TCODE_DEFINITIONS list.
  - add_action('admin_init', ...): WordPress lifecycle hook.

Original: Prigag Online Pvt. Ltd. (v1.18.0)
Port:     Python/SQLite standalone desktop version
"""

import logging
import re
from datetime import date
from typing import Any, Optional

from core.auth import ErpAuth
from core.db import ErpDB
from core.finance_engine import assert_period_open, create_note_split, gst_split, _cc

logger = logging.getLogger(__name__)

RETURNS_SCHEMA_VERSION = "1.18.0"

RETURN_REASONS: dict[str, str] = {
    "DEFECTIVE": "Defective / damaged",
    "QUALITY": "Quality not acceptable",
    "WRONG_ITEM": "Wrong item supplied",
    "EXCESS": "Excess quantity",
    "LATE": "Late delivery",
    "OTHER": "Other",
}


def pr_returned_qty(grn_line_id: int) -> float:
    """Quantity already returned against one GRN line (posted purchase returns only)."""
    val = ErpDB.fetch_value(
        "SELECT COALESCE(SUM(l.qty),0) FROM purchase_return_lines l "
        "JOIN purchase_returns r ON r.id=l.return_id "
        "WHERE l.grn_line_id=? AND r.status='POSTED'",
        (int(grn_line_id),),
    )
    return float(val or 0.0)


def sr_returned_qty(inv_line_id: int) -> float:
    """Quantity already returned against one sales-invoice line (posted sales returns only)."""
    val = ErpDB.fetch_value(
        "SELECT COALESCE(SUM(l.qty),0) FROM sales_return_lines l "
        "JOIN sales_returns r ON r.id=l.return_id "
        "WHERE l.inv_line_id=? AND r.status='POSTED'",
        (int(inv_line_id),),
    )
    return float(val or 0.0)


def purchase_return_create(in_data: dict[str, Any]) -> int:
    """Create a purchase return against a posted GRN.

    Args:
        in_data: dict containing:
            - grn_id: int
            - return_date: YYYY-MM-DD (optional, defaults to today)
            - reason_code: DEFECTIVE | QUALITY | WRONG_ITEM | EXCESS | LATE | OTHER
            - reason: str (required description)
            - qty: dict[grn_line_id, float_qty]
            - debit_note: '0' (none) | '-1' (open note) | invoice_id (link to supplier invoice)

    Returns:
        int: purchase_returns.id
    """
    company_id = ErpAuth.company_id()
    user_id = ErpAuth.user_id()
    grn_id = int(in_data.get("grn_id") or 0)

    date_str = str(in_data.get("return_date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        date_str = date.today().isoformat()

    reason_code = str(in_data.get("reason_code") or "")
    if reason_code not in RETURN_REASONS:
        reason_code = "OTHER"

    reason = str(in_data.get("reason") or "").strip()
    if not reason:
        raise ValueError("Enter the reason for the return.")

    grn = ErpDB.fetch_one(
        "SELECT * FROM goods_receipts WHERE id=? AND company_id=?",
        (grn_id, company_id),
    )
    if not grn:
        raise ValueError("Goods receipt not found.")
    if grn.get("status") != "POSTED":
        raise ValueError(
            f"GRN {grn.get('grn_number')} is not posted (status {grn.get('status')}); "
            "only posted receipts can be returned."
        )
    if str(grn.get("movement_type") or "101") == "122":
        raise ValueError("That document is itself a return; choose the original goods receipt.")

    assert_period_open(date_str, company_id)

    supplier = ErpDB.fetch_one(
        "SELECT id, gstin FROM suppliers WHERE id=?",
        (int(grn["supplier_id"]),),
    )

    rows: list[dict[str, Any]] = []
    base = 0.0
    tax = 0.0
    qty_dict = in_data.get("qty") or {}
    if isinstance(qty_dict, dict):
        items_iter = list(qty_dict.items())
    elif isinstance(qty_dict, list):
        items_iter = [(d.get("grn_line_id"), d.get("qty")) for d in qty_dict if isinstance(d, dict)]
    else:
        items_iter = []

    for gl_id, q_val in items_iter:
        q = round(float(q_val or 0.0), 3)
        if q <= 0:
            continue
        gl = ErpDB.fetch_one(
            "SELECT gl.*, i.item_name, COALESCE(pol.gst_rate, i.gst_rate, 0) AS gst "
            "FROM goods_receipt_lines gl "
            "JOIN items i ON i.id=gl.item_id "
            "LEFT JOIN purchase_order_lines pol ON pol.id=gl.po_line_id "
            "WHERE gl.id=? AND gl.grn_id=?",
            (int(gl_id), grn_id),
        )
        if not gl:
            raise ValueError("A line does not belong to this goods receipt.")
        open_qty = round(float(gl.get("qty_accepted") or 0.0) - pr_returned_qty(int(gl_id)), 3)
        if q > open_qty + 0.0005:
            raise ValueError(
                f"{gl['item_name']}: you can return at most {open_qty:g} "
                "(accepted quantity less earlier returns)."
            )
        price = float(gl.get("unit_price") or 0.0)
        val = round(q * price, 2)
        rate = float(gl.get("gst") or 0.0)
        rows.append({"gl": gl, "qty": q, "price": price, "val": val, "rate": rate})
        base += val
        tax += round(val * rate / 100.0, 2)

    if not rows:
        raise ValueError("Enter a return quantity on at least one line.")

    base = round(base, 2)
    tax = round(tax, 2)

    # Debit note handling
    dn = str(in_data.get("debit_note") or "0")
    inv_id = 0
    make_note = False
    if dn not in ("0", ""):
        make_note = True
        if int(dn) > 0:
            inv_id = int(dn)
            ok = ErpDB.fetch_value(
                "SELECT id FROM supplier_invoices WHERE id=? AND company_id=? AND supplier_id=?",
                (inv_id, company_id, int(grn["supplier_id"])),
            )
            if not ok:
                raise ValueError("The selected supplier invoice does not belong to this supplier.")
        if base <= 0:
            make_note = False

    ErpDB.begin_transaction()
    try:
        year = int(date_str[:4])
        # 1. Stock document (movement 122) - posts stock out + GR/IR journal & returns qty to PO
        gr = ErpDB.call_procedure("sp_get_next_number", [company_id, "GRN", year], ["p_doc_number"])
        grn_doc_number = gr.get("p_doc_number") if isinstance(gr, dict) else str(gr)

        grn_data: dict[str, Any] = {
            "company_id": company_id,
            "branch_id": ErpAuth.branch_id(),
            "grn_number": grn_doc_number,
            "grn_date": date_str,
            "po_id": grn.get("po_id") or None,
            "supplier_id": int(grn["supplier_id"]),
            "warehouse_id": int(grn["warehouse_id"]),
            "movement_type": "122",
            "dc_number": f"Return vs {grn.get('grn_number')}"[:50],
            "status": "DRAFT",
            "qc_required": 0,
            "created_by": user_id,
        }
        grn_data.update(_cc(grn))
        stock_id = ErpDB.insert("goods_receipts", grn_data)

        for r in rows:
            ErpDB.insert("goods_receipt_lines", {
                "grn_id": stock_id,
                "po_line_id": r["gl"].get("po_line_id") or None,
                "item_id": int(r["gl"]["item_id"]),
                "uom_id": int(r["gl"]["uom_id"]),
                "qty_received": r["qty"],
                "qty_accepted": r["qty"],
                "qty_rejected": 0,
                "unit_price": r["price"],
                "batch_number": r["gl"].get("batch_number") or None,
            })
        ErpDB.process_grn(int(stock_id), user_id)

        # 2. Return document
        num = ErpDB.call_procedure("sp_get_next_number", [company_id, "PRTN", year], ["p_doc_number"])
        prtn_doc_number = num.get("p_doc_number") if isinstance(num, dict) else str(num)
        ret_id = ErpDB.insert("purchase_returns", {
            "company_id": company_id,
            "return_number": prtn_doc_number,
            "return_date": date_str,
            "grn_id": grn_id,
            "po_id": grn.get("po_id") or None,
            "supplier_id": int(grn["supplier_id"]),
            "warehouse_id": int(grn["warehouse_id"]),
            "stock_doc_id": stock_id,
            "reason_code": reason_code,
            "reason": reason[:255],
            "base_amount": base,
            "tax_amount": tax,
            "total_amount": round(base + tax, 2),
            "status": "POSTED",
            "created_by": user_id,
        })

        for r in rows:
            ErpDB.insert("purchase_return_lines", {
                "return_id": ret_id,
                "grn_line_id": int(r["gl"]["id"]),
                "po_line_id": r["gl"].get("po_line_id") or None,
                "item_id": int(r["gl"]["item_id"]),
                "uom_id": int(r["gl"]["uom_id"]),
                "qty": r["qty"],
                "unit_price": r["price"],
                "gst_rate": r["rate"],
                "line_value": r["val"],
            })

        # 3. Supplier debit note (GST reversed; offset GR/IR)
        note_id = None
        if make_note and tax >= 0:
            sp = gst_split(tax, supplier.get("gstin") if supplier else None, company_id)
            narration = f"Purchase return {prtn_doc_number} vs GRN {grn.get('grn_number')} - {reason}"
            note_id = create_note_split(
                "DN",
                int(grn["supplier_id"]),
                date_str,
                base,
                float(sp["cgst"]),
                float(sp["sgst"]),
                float(sp["igst"]),
                narration,
                inv_id,
                "GRIR",
                "PURCHASE_RETURN",
                int(ret_id),
            )
            ErpDB.update("purchase_returns", {"debit_note_id": note_id}, "id=?", (ret_id,))

        ErpAuth.audit(
            "purchase_returns",
            int(ret_id),
            "INSERT",
            {},
            {
                "return_number": prtn_doc_number,
                "grn": grn.get("grn_number"),
                "total": round(base + tax, 2),
                "debit_note_id": note_id,
            },
        )
        ErpDB.commit()
        return int(ret_id)
    except Exception:
        ErpDB.rollback()
        raise


def sales_return_create(in_data: dict[str, Any]) -> int:
    """Create a sales return against an invoice.

    Args:
        in_data: dict containing:
            - invoice_id: int
            - return_date: YYYY-MM-DD (optional, defaults to today)
            - reason_code: DEFECTIVE | QUALITY | WRONG_ITEM | EXCESS | LATE | OTHER
            - reason: str (required description)
            - disposition: RESTOCK | SCRAP
            - warehouse_id: int (required if disposition == RESTOCK)
            - qty: dict[inv_line_id, float_qty]

    Returns:
        int: sales_returns.id
    """
    company_id = ErpAuth.company_id()
    user_id = ErpAuth.user_id()
    inv_id = int(in_data.get("invoice_id") or 0)

    date_str = str(in_data.get("return_date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        date_str = date.today().isoformat()

    reason_code = str(in_data.get("reason_code") or "")
    if reason_code not in RETURN_REASONS:
        reason_code = "OTHER"

    reason = str(in_data.get("reason") or "").strip()
    if not reason:
        raise ValueError("Enter the reason for the return.")

    disp = "SCRAP" if str(in_data.get("disposition") or "RESTOCK").upper() == "SCRAP" else "RESTOCK"

    inv = ErpDB.fetch_one(
        "SELECT * FROM sales_invoices WHERE id=? AND company_id=?",
        (inv_id, company_id),
    )
    if not inv:
        raise ValueError("Sales invoice not found.")
    if inv.get("status") == "CANCELLED":
        raise ValueError(f"Invoice {inv.get('inv_number')} is cancelled.")

    assert_period_open(date_str, company_id)

    wh_id = int(in_data.get("warehouse_id") or 0)
    if disp == "RESTOCK":
        if not wh_id or not ErpDB.fetch_value(
            "SELECT id FROM warehouses WHERE id=? AND company_id=?", (wh_id, company_id)
        ):
            raise ValueError("Choose the warehouse the goods are received into.")

    rows: list[dict[str, Any]] = []
    base = 0.0
    cg = 0.0
    sg = 0.0
    ig = 0.0

    qty_dict = in_data.get("qty") or {}
    if isinstance(qty_dict, dict):
        items_iter = list(qty_dict.items())
    elif isinstance(qty_dict, list):
        items_iter = [(d.get("inv_line_id"), d.get("qty")) for d in qty_dict if isinstance(d, dict)]
    else:
        items_iter = []

    for lid, q_val in items_iter:
        q = round(float(q_val or 0.0), 3)
        if q <= 0:
            continue
        l = ErpDB.fetch_one(
            "SELECT sl.*, i.item_name FROM sales_invoice_lines sl "
            "JOIN items i ON i.id=sl.item_id WHERE sl.id=? AND sl.inv_id=?",
            (int(lid), inv_id),
        )
        if not l:
            raise ValueError("A line does not belong to this invoice.")
        open_qty = round(float(l.get("qty") or 0.0) - sr_returned_qty(int(lid)), 3)
        if q > open_qty + 0.0005:
            raise ValueError(
                f"{l['item_name']}: you can return at most {open_qty:g} "
                "(invoiced quantity less earlier returns)."
            )
        f = (q / float(l["qty"])) if float(l.get("qty") or 0.0) > 0 else 0.0
        lb = round(q * float(l.get("unit_price") or 0.0) * (1.0 - float(l.get("discount_pct") or 0.0) / 100.0), 2)
        lcg = round(float(l.get("cgst_amount") or 0.0) * f, 2)
        lsg = round(float(l.get("sgst_amount") or 0.0) * f, 2)
        lig = round(float(l.get("igst_amount") or 0.0) * f, 2)
        rows.append({"l": l, "qty": q, "base": lb, "cg": lcg, "sg": lsg, "ig": lig})
        base += lb
        cg += lcg
        sg += lsg
        ig += lig

    if not rows:
        raise ValueError("Enter a return quantity on at least one line.")

    base = round(base, 2)
    cg = round(cg, 2)
    sg = round(sg, 2)
    ig = round(ig, 2)

    ErpDB.begin_transaction()
    try:
        year = int(date_str[:4])
        num = ErpDB.call_procedure("sp_get_next_number", [company_id, "SRTN", year], ["p_doc_number"])
        srtn_doc_number = num.get("p_doc_number") if isinstance(num, dict) else str(num)

        ret_id = ErpDB.insert("sales_returns", {
            "company_id": company_id,
            "return_number": srtn_doc_number,
            "return_date": date_str,
            "invoice_id": inv_id,
            "customer_id": int(inv["customer_id"]),
            "warehouse_id": wh_id if disp == "RESTOCK" else None,
            "disposition": disp,
            "reason_code": reason_code,
            "reason": reason[:255],
            "base_amount": base,
            "tax_amount": round(cg + sg + ig, 2),
            "total_amount": round(base + cg + sg + ig, 2),
            "status": "POSTED",
            "created_by": user_id,
        })

        for r in rows:
            cost = 0.0
            item_id = int(r["l"]["item_id"])
            if disp == "RESTOCK":
                cost_val = ErpDB.fetch_value(
                    "SELECT avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
                    (item_id, wh_id),
                )
                cost = float(cost_val or 0.0)
                if cost <= 0:
                    map_val = ErpDB.fetch_value(
                        "SELECT moving_avg_price FROM items WHERE id=?",
                        (item_id,),
                    )
                    cost = float(map_val or 0.0)
                # Stock back in
                ErpDB.post_stock_movement(
                    company_id,
                    item_id,
                    wh_id,
                    "SALES_RETURN",
                    "651",
                    r["qty"],
                    0.0,
                    cost,
                    "sales_returns",
                    int(ret_id),
                    srtn_doc_number,
                    user_id,
                )

            ErpDB.insert("sales_return_lines", {
                "return_id": ret_id,
                "inv_line_id": int(r["l"]["id"]),
                "item_id": item_id,
                "uom_id": int(r["l"]["uom_id"]),
                "qty": r["qty"],
                "unit_price": float(r["l"].get("unit_price") or 0.0),
                "discount_pct": float(r["l"].get("discount_pct") or 0.0),
                "gst_rate": float(r["l"].get("gst_rate") or 0.0),
                "cgst_amount": r["cg"],
                "sgst_amount": r["sg"],
                "igst_amount": r["ig"],
                "line_base": r["base"],
                "unit_cost": cost,
            })

        # Credit note: reverses revenue + output GST, reduces customer receivable
        narration = f"Sales return {srtn_doc_number} vs invoice {inv.get('inv_number')} - {reason}"
        note_id = create_note_split(
            "CN",
            int(inv["customer_id"]),
            date_str,
            base,
            cg,
            sg,
            ig,
            narration,
            inv_id,
            "",
            "SALES_RETURN",
            int(ret_id),
        )
        ErpDB.update("sales_returns", {"credit_note_id": note_id}, "id=?", (ret_id,))

        ErpAuth.audit(
            "sales_returns",
            int(ret_id),
            "INSERT",
            {},
            {
                "return_number": srtn_doc_number,
                "invoice": inv.get("inv_number"),
                "total": round(base + cg + sg + ig, 2),
                "credit_note_id": note_id,
                "disposition": disp,
            },
        )
        ErpDB.commit()
        return int(ret_id)
    except Exception:
        ErpDB.rollback()
        raise
