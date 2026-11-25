"""
backend/core/service_entry.py
------------------------------
Brain ERP — Service Entry Sheet (ML81N)
Port of PHP modules/mm/service_entry_full.php (business-logic half) -> Python / SQLite.

A service entry sheet (SES) records services rendered against a Purchase
Order (account-assignment category 'K' = cost center, by default). It starts
as DRAFT; a purchase manager then ACCEPTs it (posting an acceptance document
number and an optional vendor-evaluation score) or REJECTs it.

NOT PORTED (and why):
  - CSRF validation: dropped project-wide (FastAPI, no CSRF needed).
  - wp_redirect()/flash messages: HTTP-layer concerns, handled by the router
    returning ok()/fail() and by the UI.
  - ML81N tcode registration: handled by installer.py's TCODE_DEFINITIONS.

Original: Prigag Online Pvt. Ltd. (modules/mm/service_entry_full.php)
Port:     Python/SQLite standalone desktop version
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from core.auth import ErpAuth
from core.db import ErpDB

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_service_entry_sheet(in_data: dict[str, Any]) -> int:
    """Create a new DRAFT service entry sheet against a Purchase Order.

    Args:
        in_data: dict containing:
            - po_id: int (required - PO must belong to this company)
            - po_line_id: int | None (optional - ties the sheet to one PO line)
            - ses_date: YYYY-MM-DD (optional, defaults to today)
            - short_text: str
            - acct_assgt_cat: str (defaults 'K')
            - cost_center_id: int | None
            - external_number: str
            - service_location: str
            - period_from / period_to: YYYY-MM-DD | None
            - price_ref_date: YYYY-MM-DD | None
            - returns_indicator: bool
            - remarks: str
            - lines: list[dict] each with short_text, quantity, gross_price,
              and optionally service_item_id, uom_id, line_cost_center_id
              (lines with no short_text or quantity<=0 are dropped, matching
              the PHP array_filter())

    Returns:
        int: service_entry_sheets.id
    """
    company_id = ErpAuth.company_id()
    user_id = ErpAuth.user_id()

    po_id = int(in_data.get("po_id") or 0)
    po_line_id = int(in_data["po_line_id"]) if in_data.get("po_line_id") else None

    raw_lines = in_data.get("lines") or []
    lines = [
        l for l in raw_lines
        if isinstance(l, dict) and str(l.get("short_text") or "").strip() and float(l.get("quantity") or 0) > 0
    ]

    po = ErpDB.fetch_one(
        "SELECT * FROM purchase_orders WHERE id=? AND company_id=?", (po_id, company_id)
    ) if po_id else None

    if not po:
        raise ValueError("Please select a valid Purchase Order.")
    if not lines:
        raise ValueError("Add at least one service line with a description and quantity.")

    ses_date = str(in_data.get("ses_date") or "") or date.today().isoformat()
    total = sum(float(l["quantity"]) * float(l.get("gross_price") or 0) for l in lines)
    cost_center_id = int(in_data["cost_center_id"]) if in_data.get("cost_center_id") else None

    ErpDB.begin_transaction()
    try:
        num = ErpDB.call_procedure(
            "sp_get_next_number", [company_id, "SES", date.today().year], ["p_doc_number"]
        )
        ses_number = num.get("p_doc_number") if isinstance(num, dict) else str(num)

        ses_id = ErpDB.insert("service_entry_sheets", {
            "company_id": company_id,
            "ses_number": ses_number,
            "ses_date": ses_date,
            "po_id": po_id,
            "po_line_id": po_line_id,
            "supplier_id": po["supplier_id"],
            "short_text": in_data.get("short_text") or "",
            "acct_assgt_cat": in_data.get("acct_assgt_cat") or "K",
            "cost_center_id": cost_center_id,
            "external_number": in_data.get("external_number") or "",
            "service_location": in_data.get("service_location") or "",
            "period_from": in_data.get("period_from") or None,
            "period_to": in_data.get("period_to") or None,
            "price_ref_date": in_data.get("price_ref_date") or None,
            "currency_code": po.get("currency_code") or "INR",
            "total_amount": round(total, 2),
            "status": "DRAFT",
            "returns_indicator": 1 if in_data.get("returns_indicator") else 0,
            "remarks": in_data.get("remarks") or "",
            "created_by": user_id,
        })

        for i, l in enumerate(lines):
            qty = float(l["quantity"])
            price = float(l.get("gross_price") or 0)
            line_cc = l.get("line_cost_center_id")
            ErpDB.insert("service_entry_sheet_lines", {
                "ses_id": ses_id,
                "line_num": (i + 1) * 10,
                "service_item_id": int(l["service_item_id"]) if l.get("service_item_id") else None,
                "short_text": l["short_text"],
                "quantity": qty,
                "uom_id": int(l["uom_id"]) if l.get("uom_id") else None,
                "gross_price": price,
                "cost_center_id": int(line_cc) if line_cc else cost_center_id,
                "line_total": round(qty * price, 2),
                "status": "OPEN",
            })

        ErpAuth.audit("service_entry_sheets", ses_id, "INSERT", {}, {"ses_number": ses_number})
        ErpDB.commit()
        return int(ses_id)
    except Exception:
        ErpDB.rollback()
        raise


def accept_service_entry_sheet(
    ses_id: int, quality_of_service: int = 100, service_timeliness: int = 100
) -> Optional[str]:
    """Accept a DRAFT entry sheet: posts an acceptance document and marks its
    lines POSTED. Returns the acceptance document number, or None if the
    sheet was not found / not DRAFT (mirrors the PHP's silent no-op)."""
    company_id = ErpAuth.company_id()
    user_id = ErpAuth.user_id()

    ses = ErpDB.fetch_one(
        "SELECT * FROM service_entry_sheets WHERE id=? AND company_id=? AND status='DRAFT'",
        (ses_id, company_id),
    )
    if not ses:
        return None

    qos = max(0, min(100, int(quality_of_service)))
    timely = max(0, min(100, int(service_timeliness)))

    num = ErpDB.call_procedure("sp_get_next_number", [company_id, "SESACC", date.today().year], ["p_doc_number"])
    acc_num = num.get("p_doc_number") if isinstance(num, dict) else str(num)

    ErpDB.update("service_entry_sheets", {
        "status": "ACCEPTED",
        "acceptance_doc_number": acc_num,
        "accepted_by": user_id,
        "accepted_at": _now(),
        "quality_of_service": qos,
        "service_timeliness": timely,
    }, "id=?", (ses_id,))
    ErpDB.query("UPDATE service_entry_sheet_lines SET status='POSTED' WHERE ses_id=?", (ses_id,))

    ErpAuth.audit(
        "service_entry_sheets", ses_id, "UPDATE",
        {"status": "DRAFT"}, {"status": "ACCEPTED", "acceptance_doc_number": acc_num},
    )
    return acc_num


def reject_service_entry_sheet(ses_id: int) -> bool:
    """Reject a DRAFT entry sheet. Returns True if a row was actually updated."""
    company_id = ErpAuth.company_id()
    rows = ErpDB.update(
        "service_entry_sheets", {"status": "REJECTED"},
        "id=? AND company_id=? AND status='DRAFT'", (ses_id, company_id),
    )
    return bool(rows)