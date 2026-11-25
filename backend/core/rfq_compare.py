"""
backend/core/rfq_compare.py
-----------------------------
Brain ERP — Compare Quotations (ME47)
Port of modules/mm/rfq_compare.php -> Python / SQLite.

Covers the parts of the PHP file that are pure data/DB logic:
  - get_comparison()            the comparison grid: lines x suppliers quote
                                 matrix, lowest price per line/total, each
                                 supplier's "last terms" (final discount ->
                                 accepted negotiation -> last offer -> quoted),
                                 and which RFQ lines already have a PO.
  - set_final_discount() / clear_final_discount() / log_negotiation_round()
                                 the ME47 "Negotiation" tab + Final Discount
                                 record-keeping (audit trail in rfq_negotiations).
  - create_po_from_comparison() "Create PO" split-award: every still-open
                                 RFQ line is awarded to whichever SUBMITTED
                                 supplier quoted the lowest unit price for
                                 it; one PO per winning supplier, release
                                 routing triggered the same way ME21N does.

NOT PORTED (and why):
  - msme_erp_rfq_ai_compare() / "Close Quotations & Evaluate" / "retry_award"
    (msme_erp_rfq_run_evaluation): the Gemini AI comparison + the scheduled
    auto-cut-off/auto-award engine. These depend on the AI gateway
    (core/aura_gateway.py only has the MCP/webhook side so far, not the
    Gemini RFQ-comparison prompt) and are out of scope for this pass.
  - msme_erp_rfq_email() (resend the RFQ invite link to a supplier): depends
    on an outbound-mail layer not yet ported.
  - CSRF validation: dropped project-wide (FastAPI, no CSRF needed).
  Both AI compare and the automated award can be added later as their own
  core module once the AI-gateway / mail layer exist; the manual "Create PO"
  award path below does not depend on either.

Original: Prigag Online Pvt. Ltd. (modules/mm/rfq_compare.php)
Port:     Python/SQLite standalone desktop version
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from core.auth import ErpAuth
from core.db import ErpDB

logger = logging.getLogger(__name__)

CHANNEL_CHOICES = ("PHONE", "EMAIL", "MEETING", "PORTAL", "OTHER")
ACTOR_CHOICES = ("BUYER", "SUPPLIER")
ACTION_TYPE_CHOICES = ("OFFER", "COUNTER", "NOTE")
BASIS_BADGE = {"FINAL": "success", "ACCEPTED": "success", "LAST OFFER": "primary", "QUOTED": "secondary"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_rfq_supplier(rfq_id: int, rfq_supplier_id: int) -> Optional[dict]:
    return ErpDB.fetch_one(
        "SELECT * FROM rfq_suppliers WHERE id=? AND rfq_id=?", (rfq_supplier_id, rfq_id)
    )


def _effective_discount(s: dict) -> dict:
    """Buyer's FINAL discount if set, else an ACCEPTED automated negotiation, else none.
    This is what prices a PO / the AI prompt - never the merely-last-offered terms."""
    if s.get("status") != "SUBMITTED":
        return {"pct": 0.0, "net": None, "source": None}
    if s.get("final_set_at") and s.get("final_discount_pct") is not None:
        return {"pct": float(s["final_discount_pct"]), "net": float(s["final_amount"]), "source": "FINAL"}
    if (s.get("negotiation_status") or "NONE") == "ACCEPTED":
        pct = float(s.get("negotiation_discount_pct") or 0)
        return {"pct": pct, "net": round(float(s["total_quoted"]) * (1 - pct / 100), 2), "source": "NEGOTIATION"}
    return {"pct": 0.0, "net": None, "source": None}


def _last_terms(s: dict, last_nego_by_sub: dict[int, dict]) -> dict:
    """What to SHOW for a supplier: FINAL -> ACCEPTED negotiation -> newest logged
    round with a discount/amount -> the supplier's own quoted total (0% off)."""
    if s.get("status") != "SUBMITTED":
        return {"pct": None, "net": None, "basis": None, "note": ""}
    q = float(s.get("total_quoted") or 0)
    ed = _effective_discount(s)
    if ed["source"] == "FINAL":
        return {"pct": ed["pct"], "net": float(ed["net"]), "basis": "FINAL", "note": "buyer's final discount"}
    if ed["source"] == "NEGOTIATION":
        return {"pct": ed["pct"], "net": float(ed["net"]), "basis": "ACCEPTED", "note": "accepted in negotiation"}
    n = last_nego_by_sub.get(s["id"])
    if n and q > 0:
        amt = float(n.get("total_amount") or 0)
        pct = float(n.get("discount_pct") or 0)
        net = amt if amt > 0 else round(q * (1 - pct / 100), 2)
        pct = pct if pct > 0 else max(0.0, round((1 - net / q) * 100, 2))
        note = f"round {int(n['round_no'])} \u00b7 {str(n['actor']).lower()} {str(n['action_type']).lower()}"
        return {"pct": pct, "net": net, "basis": "LAST OFFER", "note": note}
    return {"pct": 0.0, "net": q, "basis": "QUOTED", "note": "no discount negotiated"}


def get_comparison(rfq_id: int, company_id: int) -> dict:
    """The ME47 comparison grid: lines, suppliers/quotes, lowest price per
    line/total, each supplier's effective/last terms, and PO-award status.
    Raises ValueError if the RFQ (with its source PR) isn't found."""
    rfq = ErpDB.fetch_one(
        "SELECT r.*, pr.pr_number FROM rfqs r "
        "JOIN purchase_requisitions pr ON pr.id=r.pr_id "
        "WHERE r.id=? AND r.company_id=?",
        (rfq_id, company_id),
    )
    if not rfq:
        raise ValueError("RFQ not found.")

    lines = ErpDB.fetch_all(
        "SELECT rl.*, i.item_code, i.item_name, u.code AS uom_code "
        "FROM rfq_lines rl "
        "LEFT JOIN items i ON i.id=rl.item_id "
        "LEFT JOIN units_of_measure u ON u.id=rl.uom_id "
        "WHERE rl.rfq_id=? ORDER BY rl.line_num",
        (rfq_id,),
    )
    subs = ErpDB.fetch_all(
        "SELECT rs.*, s.name AS supplier_name, s.supplier_code, s.phone AS supplier_phone, "
        "s.email AS supplier_master_email "
        "FROM rfq_suppliers rs JOIN suppliers s ON s.id=rs.supplier_id "
        "WHERE rs.rfq_id=? ORDER BY rs.id",
        (rfq_id,),
    )

    # Matrix: [rfq_supplier_id][rfq_line_id] -> quote line row
    matrix: dict[int, dict[int, dict]] = {}
    for s in subs:
        qls = ErpDB.fetch_all("SELECT * FROM rfq_quote_lines WHERE rfq_supplier_id=?", (s["id"],))
        matrix[s["id"]] = {ql["rfq_line_id"]: ql for ql in qls}

    # Lowest price per line, for highlighting
    lowest_per_line: dict[int, Optional[float]] = {}
    for l in lines:
        best = None
        for s in subs:
            p = matrix.get(s["id"], {}).get(l["id"], {}).get("unit_price")
            if p is not None and (best is None or float(p) < best):
                best = float(p)
        lowest_per_line[l["id"]] = best

    submitted_totals = [float(s["total_quoted"]) for s in subs if s.get("status") == "SUBMITTED"]
    lowest_total = min(submitted_totals) if submitted_totals else None
    subs_by_supplier_id = {s["supplier_id"]: s for s in subs}  # REAL suppliers.id -> row

    # Last-negotiation-per-supplier (ascending id -> last write wins)
    last_nego_by_sub: dict[int, dict] = {}
    if subs:
        ids = [int(s["id"]) for s in subs]
        placeholders = ",".join("?" for _ in ids)
        for n in ErpDB.fetch_all(
            f"SELECT * FROM rfq_negotiations WHERE rfq_supplier_id IN ({placeholders}) ORDER BY id ASC", ids
        ):
            if float(n.get("discount_pct") or 0) > 0 or float(n.get("total_amount") or 0) > 0:
                last_nego_by_sub[n["rfq_supplier_id"]] = n

    lowest_net = None
    subs_out = []
    for s in subs:
        terms = _last_terms(s, last_nego_by_sub)
        if terms["net"] is not None and terms["net"] > 0 and (lowest_net is None or terms["net"] < lowest_net):
            lowest_net = terms["net"]
        row = dict(s)
        row["effective_discount"] = _effective_discount(s)
        row["last_terms"] = terms
        row["last_terms_badge"] = BASIS_BADGE.get(terms["basis"], "secondary")
        subs_out.append(row)

    # Which RFQ lines already have a PO line against them (any route: this
    # award button, manual ME21N, or direct PR conversion)?
    ordered_pr_line_ids: set[int] = set()
    linked_pos_by_po_id: dict[int, dict] = {}
    rfq_pr_line_ids = [l["pr_line_id"] for l in lines if l.get("pr_line_id")]
    if rfq_pr_line_ids:
        placeholders = ",".join("?" for _ in rfq_pr_line_ids)
        po_line_rows = ErpDB.fetch_all(
            f"SELECT pol.pr_line_id, po.id AS po_id, po.po_number, po.grand_total, po.status, "
            f"s.name AS supplier_name "
            f"FROM purchase_order_lines pol "
            f"JOIN purchase_orders po ON po.id = pol.po_id "
            f"JOIN suppliers s ON s.id = po.supplier_id "
            f"WHERE pol.pr_line_id IN ({placeholders})",
            rfq_pr_line_ids,
        )
        for r in po_line_rows:
            ordered_pr_line_ids.add(r["pr_line_id"])
            linked_pos_by_po_id[r["po_id"]] = r

    def is_line_ordered(l: dict) -> bool:
        return bool(l.get("pr_line_id")) and l["pr_line_id"] in ordered_pr_line_ids

    open_lines = [l for l in lines if not is_line_ordered(l)]

    return {
        "rfq": rfq,
        "lines": lines,
        "suppliers": subs_out,
        "matrix": matrix,
        "lowest_per_line": lowest_per_line,
        "lowest_total": lowest_total,
        "lowest_net": lowest_net,
        "linked_pos": list(linked_pos_by_po_id.values()),
        "open_line_ids": [l["id"] for l in open_lines],
        "fully_awarded": len(open_lines) == 0,
    }


# ---------------------------------------------------------------------------
# Negotiation tab: Final Discount + manual round logging
# ---------------------------------------------------------------------------
def set_final_discount(
    rfq_id: int, rfq_supplier_id: int, mode: str, value: float,
    remarks: str = "", channel: Optional[str] = None,
) -> str:
    """Buyer records the FINAL agreed discount for one supplier (as % off, or
    as a final net amount). Returns a confirmation message."""
    user_id = ErpAuth.user_id()
    rs = _get_rfq_supplier(rfq_id, rfq_supplier_id)
    if not rs:
        raise ValueError("Supplier not found on this RFQ.")

    quoted = float(rs.get("total_quoted") or 0)
    mode = "NET" if mode == "NET" else "PERCENT"
    channel = channel if channel in CHANNEL_CHOICES else None
    remarks = (remarks or "").strip()[:255]

    if rs.get("status") != "SUBMITTED" or quoted <= 0:
        raise ValueError("This supplier has not submitted a quotation yet, so there is nothing to discount.")
    if mode == "PERCENT" and (value < 0 or value >= 100):
        raise ValueError("Final discount % must be between 0 and 99.99.")
    if mode == "NET" and (value <= 0 or value > quoted):
        raise ValueError("Final net amount must be more than 0 and not above the total quoted.")

    pct = round(value, 2) if mode == "PERCENT" else round((1 - value / quoted) * 100, 2)
    net = round(quoted * (1 - value / 100), 2) if mode == "PERCENT" else round(value, 2)

    max_round = int(ErpDB.fetch_value(
        "SELECT COALESCE(MAX(round_no),0) FROM rfq_negotiations WHERE rfq_supplier_id=?", (rfq_supplier_id,)
    ) or 0)
    round_no = max(1, max_round)

    ErpDB.update("rfq_suppliers", {
        "final_discount_pct": pct, "final_amount": net, "final_remarks": remarks,
        "final_set_by": user_id, "final_set_at": _now(),
    }, "id=?", (rfq_supplier_id,))
    ErpDB.insert("rfq_negotiations", {
        "rfq_supplier_id": rfq_supplier_id, "round_no": round_no, "actor": "BUYER", "action_type": "FINAL",
        "discount_pct": pct, "total_amount": net, "channel": channel, "remarks": remarks, "created_by": user_id,
    })
    ErpAuth.audit(
        "rfq_suppliers", rfq_supplier_id, "UPDATE",
        {"final_discount_pct": rs.get("final_discount_pct")}, {"final_discount_pct": pct, "final_amount": net},
    )
    return f"Final discount saved: {pct:.2f}% off (net INR {net:,.2f})."


def clear_final_discount(rfq_id: int, rfq_supplier_id: int, remarks: str = "", channel: Optional[str] = None) -> str:
    """Removes a previously-set final discount again."""
    user_id = ErpAuth.user_id()
    rs = _get_rfq_supplier(rfq_id, rfq_supplier_id)
    if not rs:
        raise ValueError("Supplier not found on this RFQ.")

    channel = channel if channel in CHANNEL_CHOICES else None
    remarks = (remarks or "").strip()[:255]

    max_round = int(ErpDB.fetch_value(
        "SELECT COALESCE(MAX(round_no),0) FROM rfq_negotiations WHERE rfq_supplier_id=?", (rfq_supplier_id,)
    ) or 0)
    round_no = max(1, max_round)

    ErpDB.update("rfq_suppliers", {
        "final_discount_pct": None, "final_amount": None, "final_remarks": None,
        "final_set_by": None, "final_set_at": None,
    }, "id=? AND rfq_id=?", (rfq_supplier_id, rfq_id))
    ErpDB.insert("rfq_negotiations", {
        "rfq_supplier_id": rfq_supplier_id, "round_no": round_no, "actor": "BUYER", "action_type": "FINAL_CLEARED",
        "discount_pct": 0, "total_amount": 0, "channel": channel,
        "remarks": remarks or "Final discount cleared", "created_by": user_id,
    })
    ErpAuth.audit(
        "rfq_suppliers", rfq_supplier_id, "UPDATE",
        {"final_discount_pct": rs.get("final_discount_pct")}, {"final_discount_pct": None},
    )
    return "Final discount cleared."


def log_negotiation_round(
    rfq_id: int, rfq_supplier_id: int, actor: str = "BUYER", action_type: str = "NOTE",
    discount_pct: float = 0.0, total_amount: float = 0.0, channel: Optional[str] = None, remarks: str = "",
) -> str:
    """Manual entry for a negotiation that happened off-system (phone / email /
    meeting), so the whole trail lives in one place. Never touches the
    automated counter-offer engine or any Purchase Order."""
    user_id = ErpAuth.user_id()
    rs = _get_rfq_supplier(rfq_id, rfq_supplier_id)
    if not rs:
        raise ValueError("Supplier not found on this RFQ.")

    actor = actor if actor in ACTOR_CHOICES else "BUYER"
    action_type = action_type if action_type in ACTION_TYPE_CHOICES else "NOTE"
    channel = channel if channel in CHANNEL_CHOICES else None
    remarks = (remarks or "").strip()[:255]
    pct = float(discount_pct or 0)
    amt = float(total_amount or 0)

    if pct < 0 or pct >= 100 or amt < 0:
        raise ValueError("Discount % must be between 0 and 99.99 and the amount cannot be negative.")
    if remarks == "" and pct == 0 and amt == 0:
        raise ValueError("Enter a discount, an amount or a remark to log.")

    max_round = int(ErpDB.fetch_value(
        "SELECT COALESCE(MAX(round_no),0) FROM rfq_negotiations WHERE rfq_supplier_id=?", (rfq_supplier_id,)
    ) or 0)
    round_no = max(1, max_round)

    ErpDB.insert("rfq_negotiations", {
        "rfq_supplier_id": rfq_supplier_id, "round_no": round_no, "actor": actor, "action_type": action_type,
        "discount_pct": pct, "total_amount": amt, "channel": channel, "remarks": remarks, "created_by": user_id,
    })
    return "Negotiation entry logged."


# ---------------------------------------------------------------------------
# Manual split-award: Create PO(s) directly from the comparison
# ---------------------------------------------------------------------------
def create_po_from_comparison(rfq_id: int, company_id: int) -> dict:
    """Award every still-open RFQ line to whichever SUBMITTED supplier quoted
    the lowest unit price for it (one PO per winning supplier; a line with no
    submitted quote is skipped and reported). Returns
    {po_ids, po_numbers, unquoted_lines, message}. Raises ValueError for
    "nothing to do" conditions the PHP treated as a user-facing error."""
    user_id = ErpAuth.user_id()
    branch_id = ErpAuth.branch_id()

    if not ErpAuth.check_tcode("ME21N"):
        raise PermissionError("You are not authorized to create Purchase Orders (T-Code ME21N is required).")

    rfq = ErpDB.fetch_one(
        "SELECT r.*, pr.pr_number FROM rfqs r "
        "JOIN purchase_requisitions pr ON pr.id=r.pr_id "
        "WHERE r.id=? AND r.company_id=?",
        (rfq_id, company_id),
    )
    if not rfq:
        raise ValueError("RFQ not found.")

    cmp = get_comparison(rfq_id, company_id)
    lines_by_id = {l["id"]: l for l in cmp["lines"]}
    open_lines = [lines_by_id[i] for i in cmp["open_line_ids"]]
    if not open_lines:
        raise ValueError("Every item on this RFQ already has a Purchase Order - nothing left to award.")

    subs = cmp["suppliers"]
    matrix = cmp["matrix"]
    subs_by_supplier_id = {s["supplier_id"]: s for s in subs}

    award: dict[int, list[dict]] = {}
    unquoted_lines: list[dict] = []
    for l in open_lines:
        best_supplier_id = None
        best_price = None
        best_quote = None
        for s in subs:
            if s.get("status") != "SUBMITTED":
                continue
            ql = matrix.get(s["id"], {}).get(l["id"])
            if not ql:
                continue
            p = float(ql["unit_price"])
            if best_price is None or p < best_price:
                best_price = p
                best_supplier_id = s["supplier_id"]   # REAL suppliers.id, not rfq_suppliers.id
                best_quote = ql
        if best_supplier_id is None:
            unquoted_lines.append(l)
            continue
        award.setdefault(best_supplier_id, []).append({"line": l, "quote": best_quote})

    if not award:
        raise ValueError("No submitted quotations for the remaining item(s) - nothing to order.")

    company_currency = ErpDB.fetch_value("SELECT currency_code FROM companies WHERE id=?", (company_id,)) or "INR"
    created_pos: dict[str, int] = {}
    converted_pr_ids: set[int] = set()

    ErpDB.begin_transaction()
    try:
        for supplier_id, awarded_lines in award.items():
            # Re-check right before insert - a duplicate submit could have raced in.
            awarded_lines = [
                al for al in awarded_lines
                if not (al["line"].get("pr_line_id") and ErpDB.fetch_value(
                    "SELECT id FROM purchase_order_lines WHERE pr_line_id=?", (al["line"]["pr_line_id"],)
                ))
            ]
            if not awarded_lines:
                continue

            num = ErpDB.call_procedure("sp_get_next_number", [company_id, "PO", date.today().year], ["p_doc_number"])
            po_number = num.get("p_doc_number") if isinstance(num, dict) else str(num)

            max_delivery_days = None
            for al in awarded_lines:
                d = al["quote"].get("delivery_days")
                if d is not None and (max_delivery_days is None or int(d) > max_delivery_days):
                    max_delivery_days = int(d)
            delivery_date = (
                (date.today() + timedelta(days=max_delivery_days)).isoformat()
                if max_delivery_days is not None else None
            )

            po_id = ErpDB.insert("purchase_orders", {
                "company_id": company_id,
                "branch_id": branch_id,
                "po_number": po_number,
                "po_date": date.today().isoformat(),
                "supplier_id": supplier_id,
                "currency_code": company_currency,
                "payment_terms": subs_by_supplier_id.get(supplier_id, {}).get("payment_terms") or "",
                "delivery_date": delivery_date,
                "status": "DRAFT",
                "release_status": "NOT_RELEASED",
                "remarks": f"Auto-created from RFQ {rfq['rfq_number']} (Quotation Comparison - lowest price per line).",
                "created_by": user_id,
            })

            taxable_total = 0.0
            gst_total = 0.0
            line_num = 10
            for al in awarded_lines:
                l = al["line"]
                ql = al["quote"]
                gst_rate = float(ErpDB.fetch_value("SELECT gst_rate FROM items WHERE id=?", (l["item_id"],)) or 0)
                qty = float(l["qty"])
                price = float(ql["unit_price"])
                base = qty * price
                gst_amt = base * gst_rate / 100

                ErpDB.insert("purchase_order_lines", {
                    "po_id": po_id, "line_num": line_num, "pr_line_id": l.get("pr_line_id") or None,
                    "item_id": l["item_id"], "uom_id": l["uom_id"], "qty_ordered": qty, "unit_price": price,
                    "discount_pct": 0, "gst_rate": gst_rate, "line_total": round(base, 2),
                    "delivery_date": delivery_date, "status": "OPEN",
                })
                line_num += 10
                taxable_total += base
                gst_total += gst_amt

                if l.get("pr_line_id"):
                    ErpDB.update("purchase_requisition_lines", {"status": "PO_CREATED"}, "id=?", (l["pr_line_id"],))
                    pr_id_for_line = ErpDB.fetch_value(
                        "SELECT pr_id FROM purchase_requisition_lines WHERE id=?", (l["pr_line_id"],)
                    )
                    if pr_id_for_line:
                        converted_pr_ids.add(pr_id_for_line)

            taxable_total = round(taxable_total, 2)
            gst_total = round(gst_total, 2)
            ErpDB.update("purchase_orders", {
                "total_amount": taxable_total, "tax_amount": gst_total,
                "grand_total": round(taxable_total + gst_total, 2),
            }, "id=?", (po_id,))

            ErpAuth.audit("purchase_orders", po_id, "INSERT", {}, {"po_number": po_number, "auto_from_rfq": rfq["rfq_number"]})

            # Auto-trigger release if configured - same as the manual ME21N flow.
            strategy = ErpDB.fetch_one(
                "SELECT id FROM release_strategies WHERE company_id=? AND doc_type='purchase_order' AND is_active=1 LIMIT 1",
                (company_id,),
            )
            if strategy:
                po_total = ErpDB.fetch_value("SELECT grand_total FROM purchase_orders WHERE id=?", (po_id,))
                release_id = ErpDB.insert("document_releases", {
                    "company_id": company_id, "doc_type": "purchase_order", "doc_id": po_id,
                    "doc_number": po_number, "doc_amount": po_total, "strategy_id": strategy["id"],
                    "current_level": 1, "status": "PENDING", "requested_by": user_id,
                })
                ErpDB.update("purchase_orders", {"release_id": release_id}, "id=?", (po_id,))

            created_pos[po_number] = po_id

        if not created_pos:
            ErpDB.rollback()
            raise ValueError(
                "These item(s) already picked up a Purchase Order just now - nothing left to award. Refresh to see it."
            )

        # A PR header only flips to PO_CREATED once every one of its lines is ordered.
        for pr_id_done in converted_pr_ids:
            still_open = ErpDB.fetch_value(
                "SELECT COUNT(*) FROM purchase_requisition_lines WHERE pr_id=? AND status='OPEN'", (pr_id_done,)
            )
            if int(still_open or 0) == 0:
                ErpDB.update("purchase_requisitions", {"status": "PO_CREATED"}, "id=?", (pr_id_done,))

        # Mark the RFQ PO_CREATED only once EVERY line has a PO; a partial
        # award is PARTIALLY_ORDERED so the award button reappears for the rest.
        any_still_open = False
        for l in cmp["lines"]:
            if l.get("pr_line_id"):
                has = ErpDB.fetch_value("SELECT id FROM purchase_order_lines WHERE pr_line_id=?", (l["pr_line_id"],))
                if not has:
                    any_still_open = True
                    break
            else:
                any_still_open = True
                break
        new_status = "PARTIALLY_ORDERED" if any_still_open else "PO_CREATED"
        ErpDB.update("rfqs", {"status": new_status}, "id=?", (rfq_id,))
        ErpAuth.audit("rfqs", rfq_id, "UPDATE", {"status": rfq["status"]}, {"status": new_status})

        ErpDB.commit()
    except ValueError:
        raise
    except Exception:
        ErpDB.rollback()
        raise

    po_numbers = list(created_pos.keys())
    msg = (
        f"Purchase Orders created automatically (split award across suppliers): {', '.join(po_numbers)}."
        if len(po_numbers) > 1 else f"Purchase Order {po_numbers[0]} created automatically."
    )
    if unquoted_lines:
        msg += f" {len(unquoted_lines)} remaining line item(s) had no submitted quote and were skipped - order those separately."

    return {
        "po_ids": list(created_pos.values()),
        "po_numbers": po_numbers,
        "unquoted_line_ids": [l["id"] for l in unquoted_lines],
        "message": msg,
    }