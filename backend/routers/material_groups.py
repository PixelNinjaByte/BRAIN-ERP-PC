"""
backend/routers/material_groups.py
------------------------------------
Brain ERP — /api/material-groups
Material Groups (OMSF) with inventory GL account.

Converted from modules/mm/material_groups.php.
A group's inventory account is where goods receipts of its materials are
debited (falls back to the company's default INVENTORY account via OBYC
when the group has none - see core/material.inventory_account_for_item()).

Endpoints (T-code OMSF)
------------------------
GET    /lookups        — posting-eligible (asset, non-group) GL accounts for the dropdown
GET    /                — all groups for this company, with GL account + material count
GET    /{group_id}      — one group
POST   /                — create a group
PUT    /{group_id}      — update a group
DELETE /{group_id}      — delete a group (blocked while it still has materials)

PHP -> Python notes
  * CSRF dropped (per project rule).
  * The PHP single page (list + inline create/edit form + delete) is split
    into the REST endpoints above; the UI decides what "new" vs "edit" looks like.
  * `msme_erp_mat_dup_uq_company_group_code` option banner (warns about a
    pre-existing duplicate-code data problem blocking a unique-key migration)
    is a one-time installer/migration concern, not ported here.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.db import ErpDB
from core.auth import ErpAuth
from .deps import require_tcode, ok, fail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/material-groups", tags=["OMSF - Material Groups"])

TCODE = "OMSF"


class MaterialGroupIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    inventory_account_id: int = 0
    is_active: bool = True


def _clean_code(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", raw or "").upper()


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
@router.get("/lookups", dependencies=[Depends(require_tcode(TCODE))])
def lookups():
    company_id = ErpAuth.company_id()
    accounts = ErpDB.fetch_all(
        "SELECT id, account_code, account_name FROM chart_of_accounts "
        "WHERE company_id=? AND COALESCE(is_active,1)=1 AND COALESCE(is_group,0)=0 AND account_type='ASSET' "
        "ORDER BY account_code",
        (company_id,),
    )
    return ok({"accounts": accounts})


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------
@router.get("/", dependencies=[Depends(require_tcode(TCODE))])
def list_groups():
    company_id = ErpAuth.company_id()
    rows = ErpDB.fetch_all(
        "SELECT g.*, a.account_code, a.account_name, "
        "(SELECT COUNT(*) FROM items i WHERE i.material_group_id=g.id AND i.is_deleted=0) AS n_items "
        "FROM material_groups g LEFT JOIN chart_of_accounts a ON a.id=g.inventory_account_id "
        "WHERE g.company_id=? ORDER BY g.code",
        (company_id,),
    )
    return ok(rows)


@router.get("/{group_id}", dependencies=[Depends(require_tcode(TCODE))])
def get_group(group_id: int):
    company_id = ErpAuth.company_id()
    g = ErpDB.fetch_one(
        "SELECT g.*, a.account_code, a.account_name "
        "FROM material_groups g LEFT JOIN chart_of_accounts a ON a.id=g.inventory_account_id "
        "WHERE g.id=? AND g.company_id=?",
        (group_id, company_id),
    )
    if not g:
        return fail("Group not found.", 404)
    return ok(g)


# ---------------------------------------------------------------------------
# Create / update (shared validation, mirrors the PHP's single save branch)
# ---------------------------------------------------------------------------
def _validate_and_build_row(body: MaterialGroupIn, company_id: int, group_id: int = 0) -> dict:
    code = _clean_code(body.code)
    if not code or len(code) > 20:
        raise ValueError("Group code is required (letters, digits, - _ ; max 20).")
    name = body.name.strip()[:100]
    if not name:
        raise ValueError("Group name is required.")

    acc = int(body.inventory_account_id or 0)
    if acc and not ErpDB.fetch_value(
        "SELECT id FROM chart_of_accounts WHERE id=? AND company_id=? AND COALESCE(is_group,0)=0",
        (acc, company_id),
    ):
        raise ValueError("Choose a posting account from the chart of accounts.")

    if ErpDB.fetch_value(
        "SELECT id FROM material_groups WHERE company_id=? AND code=? AND id<>?",
        (company_id, code, group_id),
    ):
        raise ValueError(f"Group code '{code}' already exists.")

    return {
        "code": code,
        "name": name,
        "description": (body.description or "").strip()[:255],
        "inventory_account_id": acc,
        "is_active": 1 if body.is_active else 0,
    }


@router.post("/", dependencies=[Depends(require_tcode(TCODE))])
def create_group(body: MaterialGroupIn):
    company_id = ErpAuth.company_id()
    try:
        row = _validate_and_build_row(body, company_id)
    except ValueError as e:
        return fail(str(e), 400)

    row["company_id"] = company_id
    try:
        group_id = ErpDB.insert("material_groups", row)
    except RuntimeError as e:
        if "duplicate" in str(e).lower():
            return fail("That group code already exists.", 409)
        return fail(str(e), 400)

    ErpAuth.audit("material_groups", group_id, "INSERT", {}, row)
    return ok({"group_id": group_id, "code": row["code"]}, f"Group {row['code']} created.")


@router.put("/{group_id}", dependencies=[Depends(require_tcode(TCODE))])
def update_group(group_id: int, body: MaterialGroupIn):
    company_id = ErpAuth.company_id()
    old = ErpDB.fetch_one(
        "SELECT code, name, inventory_account_id, is_active FROM material_groups WHERE id=? AND company_id=?",
        (group_id, company_id),
    )
    if not old:
        return fail("Group not found.", 404)

    try:
        row = _validate_and_build_row(body, company_id, group_id)
    except ValueError as e:
        return fail(str(e), 400)

    try:
        ErpDB.update("material_groups", row, "id=? AND company_id=?", (group_id, company_id))
    except RuntimeError as e:
        if "duplicate" in str(e).lower():
            return fail("That group code already exists.", 409)
        return fail(str(e), 400)

    ErpAuth.audit("material_groups", group_id, "UPDATE", dict(old), row)
    return ok({"group_id": group_id, "code": row["code"]}, f"Group {row['code']} updated.")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.delete("/{group_id}", dependencies=[Depends(require_tcode(TCODE))])
def delete_group(group_id: int):
    company_id = ErpAuth.company_id()
    g = ErpDB.fetch_one(
        "SELECT * FROM material_groups WHERE id=? AND company_id=?", (group_id, company_id)
    )
    if not g:
        return fail("Group not found.", 404)

    n_items = ErpDB.fetch_value(
        "SELECT COUNT(*) FROM items WHERE material_group_id=? AND is_deleted=0", (group_id,)
    )
    if int(n_items or 0):
        return fail("This group still has materials - move them first or deactivate the group.", 409)

    ErpDB.query("DELETE FROM material_groups WHERE id=? AND company_id=?", (group_id, company_id))
    ErpAuth.audit("material_groups", group_id, "DELETE", {"code": g["code"], "name": g["name"]}, {})
    return ok(None, "Group deleted.")