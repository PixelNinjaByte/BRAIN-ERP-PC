"""
backend/routers/rfq_compare.py
--------------------------------
Brain ERP — /api/rfq-compare
Compare Quotations (ME47) — the RFQ comparison grid, negotiation record-
keeping, and the manual split-award "Create PO" action.

Converted from modules/mm/rfq_compare.php. Business logic lives in
core/rfq_compare.py — see that module's docstring for what was NOT ported
(the Gemini AI comparison, the automated cut-off/auto-award engine, and the
RFQ-invite resend email all depend on an AI-gateway / mail layer that isn't
wired up yet).

Note: this is a separate router from the existing routers/rfq.py (ME41 create
+ a simpler bid/compare flow against a different table set - rfq_bids /
rfq_bid_lines / master_suppliers). rfq_compare.py here matches the actual
schema rfq_create.php writes to (rfq_suppliers with a token-based public
quote form, rfq_quote_lines, rfq_negotiations) — register BOTH routers; they
serve different screens (ME41 create vs. ME47 compare/negotiate/award).

Endpoints (T-code ME47)
------------------------
GET  /{rfq_id}                        — comparison grid (lines x suppliers,
                                         lowest price/line & total, each
                                         supplier's effective/last terms,
                                         PO-award status)
POST /{rfq_id}/negotiation/final       — set the buyer's FINAL discount for
                                         one supplier ({rfq_supplier_id, mode,
                                         value, remarks?, channel?})
DELETE /{rfq_id}/negotiation/final     — clear a previously-set final
                                         discount ({rfq_supplier_id, remarks?,
                                         channel?})
POST /{rfq_id}/negotiation/log         — log an off-system negotiation round
POST /{rfq_id}/award                   — split-award: one PO per supplier
                                         with the lowest quote on each still-
                                         open line (requires T-code ME21N)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth import ErpAuth
from core.rfq_compare import (
    get_comparison,
    set_final_discount,
    clear_final_discount,
    log_negotiation_round,
    create_po_from_comparison,
)
from .deps import require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rfq-compare", tags=["ME47 - Compare Quotations"])

TCODE = "ME47"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class SetFinalDiscount(BaseModel):
    rfq_supplier_id: int
    mode: str = Field("PERCENT", description="PERCENT or NET")
    value: float
    remarks: str = ""
    channel: Optional[str] = None  # PHONE | EMAIL | MEETING | PORTAL | OTHER


class ClearFinalDiscount(BaseModel):
    rfq_supplier_id: int
    remarks: str = ""
    channel: Optional[str] = None


class LogNegotiationRound(BaseModel):
    rfq_supplier_id: int
    actor: str = "BUYER"              # BUYER | SUPPLIER
    action_type: str = "NOTE"         # OFFER | COUNTER | NOTE
    discount_pct: float = 0.0
    total_amount: float = 0.0
    channel: Optional[str] = None
    remarks: str = ""


# ---------------------------------------------------------------------------
# Comparison grid
# ---------------------------------------------------------------------------
@router.get("/{rfq_id}", dependencies=[Depends(require_tcode(TCODE))])
def comparison(rfq_id: int):
    company_id = ErpAuth.company_id()
    try:
        data = get_comparison(rfq_id, company_id)
    except ValueError as e:
        return fail(str(e), 404)
    return ok(data)


# ---------------------------------------------------------------------------
# Negotiation tab
# ---------------------------------------------------------------------------
@router.post("/{rfq_id}/negotiation/final", dependencies=[Depends(require_tcode(TCODE))])
def post_final_discount(rfq_id: int, body: SetFinalDiscount):
    try:
        msg = set_final_discount(
            rfq_id, body.rfq_supplier_id, body.mode, body.value, body.remarks, body.channel
        )
    except ValueError as e:
        return fail(str(e), 400)
    return ok(None, msg)


@router.delete("/{rfq_id}/negotiation/final", dependencies=[Depends(require_tcode(TCODE))])
def delete_final_discount(rfq_id: int, body: ClearFinalDiscount):
    try:
        msg = clear_final_discount(rfq_id, body.rfq_supplier_id, body.remarks, body.channel)
    except ValueError as e:
        return fail(str(e), 400)
    return ok(None, msg)


@router.post("/{rfq_id}/negotiation/log", dependencies=[Depends(require_tcode(TCODE))])
def post_negotiation_log(rfq_id: int, body: LogNegotiationRound):
    try:
        msg = log_negotiation_round(
            rfq_id, body.rfq_supplier_id, body.actor, body.action_type,
            body.discount_pct, body.total_amount, body.channel, body.remarks,
        )
    except ValueError as e:
        return fail(str(e), 400)
    return ok(None, msg)


# ---------------------------------------------------------------------------
# Award: split-create PO(s) from the comparison
# ---------------------------------------------------------------------------
@router.post("/{rfq_id}/award", dependencies=[Depends(require_tcode(TCODE))])
def award(rfq_id: int):
    company_id = ErpAuth.company_id()
    try:
        result = create_po_from_comparison(rfq_id, company_id)
    except PermissionError as e:
        return fail(str(e), 403)
    except ValueError as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.exception("RFQ award failed")
        return fail(f"Error creating PO(s): {e}", 500)

    return ok(result, result["message"])