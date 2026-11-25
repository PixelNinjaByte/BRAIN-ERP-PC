"""
Brain ERP — /api/supplier
Supplier master module (XK01 / XK02).
Port of mm/supplier_create.php → Python / FastAPI.

Endpoints
---------
GET  /lookups          — states, GST rates, payment terms (form dropdowns)
GET  /                 — paginated supplier list
POST /                 — XK01 create supplier
GET  /{sup_id}         — supplier detail
PUT  /{sup_id}         — XK02 update supplier
POST /{sup_id}/toggle  — activate / deactivate supplier
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator

from core.db import ErpDB
from core.auth import ErpAuth
from .deps import require_login, require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# GSTIN regex — 15-char Indian format
# ---------------------------------------------------------------------------
_GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SupplierUpsertRequest(BaseModel):
    supplier_name: str
    gstin: str = ""
    pan: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    country: str = "India"
    payment_terms: str = ""
    currency_code: str = "INR"
    bank_name: str = ""
    bank_account: str = ""
    bank_ifsc: str = ""
    bank_branch: str = ""
    tds_applicable: bool = False
    tds_section: str = ""
    tds_rate: float = 0.0
    msme_registered: bool = False
    msme_number: str = ""
    remarks: str = ""

    @field_validator("supplier_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Supplier name is required.")
        return v

    @field_validator("gstin")
    @classmethod
    def validate_gstin(cls, v: str) -> str:
        v = v.strip().upper()
        if v and not _GSTIN_RE.match(v):
            raise ValueError("Invalid GSTIN format.")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if v and "@" not in v:
            raise ValueError("Invalid email address.")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_supplier_code(company_id: int) -> str:
    """Auto-generate SUP00001, SUP00002, … (mirrors PHP)."""
    last = ErpDB.fetch_value(
        "SELECT supplier_code FROM master_suppliers "
        "WHERE company_id=? ORDER BY id DESC LIMIT 1",
        (company_id,),
    )
    if last and str(last).upper().startswith("SUP"):
        try:
            num = int(str(last)[3:]) + 1
            return f"SUP{num:05d}"
        except ValueError:
            pass
    # Fallback: count + 1
    count = int(ErpDB.fetch_value(
        "SELECT COUNT(*) FROM master_suppliers WHERE company_id=?",
        (company_id,),
    ) or 0)
    return f"SUP{(count + 1):05d}"


def _dup_check(company_id: int, gstin: str, name: str,
               exclude_id: Optional[int] = None) -> Optional[str]:
    """Return a warning message if a duplicate exists, else None."""
    if gstin:
        sql = "SELECT id FROM master_suppliers WHERE company_id=? AND UPPER(gstin)=?"
        params: list = [company_id, gstin.upper()]
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        if ErpDB.fetch_value(sql, params):
            return f"A supplier with GSTIN {gstin} already exists."

    sql = ("SELECT id FROM master_suppliers "
           "WHERE company_id=? AND UPPER(TRIM(supplier_name))=?")
    params = [company_id, name.strip().upper()]
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    if ErpDB.fetch_value(sql, params):
        return f"A supplier named '{name}' already exists."

    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/lookups")
def lookups(user=Depends(require_login)):
    """Static dropdown data for create/edit forms."""
    indian_states = [
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
        "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
        "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
        "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
        "West Bengal",
        "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
        "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
    ]
    tds_sections = [
        {"code": "194C", "label": "194C — Contractor (1%/2%)"},
        {"code": "194J", "label": "194J — Professional / Technical (10%)"},
        {"code": "194H", "label": "194H — Commission (5%)"},
        {"code": "194I", "label": "194I — Rent (10%)"},
        {"code": "194Q", "label": "194Q — Purchase of Goods (0.1%)"},
    ]
    payment_terms = [
        "Immediate", "7 Days", "15 Days", "30 Days", "45 Days",
        "60 Days", "90 Days", "Advance",
    ]
    return ok({
        "states": indian_states,
        "tds_sections": tds_sections,
        "payment_terms": payment_terms,
    })


@router.get("/")
def list_suppliers(
    q: str = "",
    is_active: Optional[int] = None,
    pg: int = Query(default=1, ge=1),
    user=Depends(require_login),
):
    """Paginated supplier list for this company."""
    cid = user["company_id"]
    sql = """SELECT id, supplier_code, supplier_name, gstin, email,
                    phone, city, state, payment_terms, is_active
             FROM master_suppliers
             WHERE company_id=?"""
    params: list = [cid]
    if q:
        sql += " AND (UPPER(supplier_name) LIKE ? OR UPPER(supplier_code) LIKE ? OR gstin LIKE ?)"
        like = f"%{q.upper()}%"
        params += [like, like, like]
    if is_active is not None:
        sql += " AND is_active=?"
        params.append(is_active)
    sql += " ORDER BY supplier_name"
    return ok(ErpDB.paginate(sql, params, pg))


@router.post("/", dependencies=[Depends(require_tcode("XK01"))])
def create_supplier(body: SupplierUpsertRequest, user=Depends(require_login)):
    """XK01 — Create a new supplier master record."""
    cid = user["company_id"]
    uid = user["user_id"]

    warn = _dup_check(cid, body.gstin, body.supplier_name)
    if warn:
        fail(warn, 409)

    code = _next_supplier_code(cid)

    sup_id = ErpDB.insert("master_suppliers", {
        "company_id": cid,
        "supplier_code": code,
        "supplier_name": body.supplier_name.strip(),
        "gstin": body.gstin,
        "pan": body.pan.strip().upper(),
        "email": body.email,
        "phone": body.phone.strip(),
        "address": body.address.strip(),
        "city": body.city.strip(),
        "state": body.state.strip(),
        "pincode": body.pincode.strip(),
        "country": body.country.strip() or "India",
        "payment_terms": body.payment_terms,
        "currency_code": body.currency_code or "INR",
        "bank_name": body.bank_name.strip(),
        "bank_account": body.bank_account.strip(),
        "bank_ifsc": body.bank_ifsc.strip().upper(),
        "bank_branch": body.bank_branch.strip(),
        "tds_applicable": int(body.tds_applicable),
        "tds_section": body.tds_section,
        "tds_rate": round(float(body.tds_rate), 2),
        "msme_registered": int(body.msme_registered),
        "msme_number": body.msme_number.strip(),
        "remarks": body.remarks.strip(),
        "is_active": 1,
        "created_by": uid,
        "created_at": date.today().isoformat(),
    })

    ErpAuth.audit("master_suppliers", sup_id, "INSERT", {},
                  {"supplier_code": code, "supplier_name": body.supplier_name})
    return ok(
        {"supplier_id": sup_id, "supplier_code": code},
        message=f"Supplier {code} — {body.supplier_name} created.",
    )


@router.get("/{sup_id}")
def get_supplier(sup_id: int, user=Depends(require_login)):
    """Supplier detail record."""
    cid = user["company_id"]
    row = ErpDB.fetch_one(
        "SELECT * FROM master_suppliers WHERE id=? AND company_id=?",
        (sup_id, cid),
    )
    if not row:
        fail("Supplier not found.", 404)
    return ok(row)


@router.put("/{sup_id}", dependencies=[Depends(require_tcode("XK02"))])
def update_supplier(sup_id: int, body: SupplierUpsertRequest,
                    user=Depends(require_login)):
    """XK02 — Update an existing supplier master record."""
    cid = user["company_id"]
    uid = user["user_id"]

    existing = ErpDB.fetch_one(
        "SELECT * FROM master_suppliers WHERE id=? AND company_id=?",
        (sup_id, cid),
    )
    if not existing:
        fail("Supplier not found.", 404)

    warn = _dup_check(cid, body.gstin, body.supplier_name, exclude_id=sup_id)
    if warn:
        fail(warn, 409)

    before = dict(existing)
    updates = {
        "supplier_name": body.supplier_name.strip(),
        "gstin": body.gstin,
        "pan": body.pan.strip().upper(),
        "email": body.email,
        "phone": body.phone.strip(),
        "address": body.address.strip(),
        "city": body.city.strip(),
        "state": body.state.strip(),
        "pincode": body.pincode.strip(),
        "country": body.country.strip() or "India",
        "payment_terms": body.payment_terms,
        "currency_code": body.currency_code or "INR",
        "bank_name": body.bank_name.strip(),
        "bank_account": body.bank_account.strip(),
        "bank_ifsc": body.bank_ifsc.strip().upper(),
        "bank_branch": body.bank_branch.strip(),
        "tds_applicable": int(body.tds_applicable),
        "tds_section": body.tds_section,
        "tds_rate": round(float(body.tds_rate), 2),
        "msme_registered": int(body.msme_registered),
        "msme_number": body.msme_number.strip(),
        "remarks": body.remarks.strip(),
        "updated_by": uid,
        "updated_at": date.today().isoformat(),
    }

    ErpDB.update("master_suppliers", updates, "id=? AND company_id=?", (sup_id, cid))
    ErpAuth.audit("master_suppliers", sup_id, "UPDATE", before, updates)
    return ok(message=f"Supplier {existing['supplier_code']} updated.")


@router.post("/{sup_id}/toggle")
def toggle_supplier(sup_id: int, user=Depends(require_login)):
    """Activate or deactivate a supplier (XK02 block/unblock)."""
    cid = user["company_id"]
    if not ErpAuth.has_role(["PURCHASE_MANAGER", "SUPER_ADMIN"]):
        fail("Not authorized to block/unblock suppliers.", 403)

    row = ErpDB.fetch_one(
        "SELECT id, supplier_code, is_active FROM master_suppliers "
        "WHERE id=? AND company_id=?",
        (sup_id, cid),
    )
    if not row:
        fail("Supplier not found.", 404)

    new_state = 0 if row["is_active"] else 1
    ErpDB.update("master_suppliers", {"is_active": new_state},
                 "id=? AND company_id=?", (sup_id, cid))
    label = "activated" if new_state else "deactivated"
    ErpAuth.audit("master_suppliers", sup_id, "UPDATE",
                  {"is_active": row["is_active"]}, {"is_active": new_state})
    return ok({"is_active": new_state},
              message=f"Supplier {row['supplier_code']} {label}.")