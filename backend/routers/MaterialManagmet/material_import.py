"""
backend/routers/material_import.py
----------------------------------
Material Import & Export (ZMMI)
Converts: msme-erp/modules/mm/material_import.php
Provides:
  - GET  /api/mm/import/template  - Download CSV template for materials
  - GET  /api/mm/export           - Export material master catalog as CSV
  - POST /api/mm/import/preview   - Preview and validate CSV data before committing
  - POST /api/mm/import/confirm   - Execute import for validated preview
"""

from __future__ import annotations

import csv
import io
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.core.auth import ErpAuth
from backend.core.db import ErpDB
from backend.core import material
from backend.routers.deps import require_login, require_tcode

router = APIRouter(prefix="/api/mm", tags=["material_import"])

# In-memory storage for import previews (token -> {created_at, company_id, user_id, update, rows})
_PREVIEW_STORE: dict[str, dict[str, Any]] = {}
PREVIEW_EXPIRY_SECONDS = 1800  # 30 minutes


def _cleanup_previews() -> None:
    now = time.time()
    expired = [k for k, v in _PREVIEW_STORE.items() if now - v["created_at"] > PREVIEW_EXPIRY_SECONDS]
    for k in expired:
        _PREVIEW_STORE.pop(k, None)


class PreviewRequest(BaseModel):
    csv_content: str
    update_existing: bool = False


class ConfirmRequest(BaseModel):
    token: str


@router.get("/import/template", dependencies=[Depends(require_tcode("ZMMI"))])
@router.get("/template", dependencies=[Depends(require_tcode("ZMMI"))])
def download_template():
    """Download material master CSV template with sample row (ZMMI)."""
    cols = material.MAT_CSV_COLUMNS
    sample_row = [
        "RAW-STEEL-001", "MS Plate 10mm", "IS 2062 E250", "MATERIAL", "RAW", "KG", "STEEL",
        "7208", "18", "62.5", "100", "1000", "250", "7", "1", "0", "0", "0", "0", "Tata Steel",
        "", "1"
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(cols)
    writer.writerow(sample_row)

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="material_import_template.csv"'}
    )


@router.get("/export", dependencies=[Depends(require_tcode("ZMMI"))])
def export_materials(
    cat: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    st: Optional[str] = Query("all"),
    user=Depends(require_login),
):
    """Export materials master to CSV."""
    company_id = user["company_id"]
    cols = material.MAT_CSV_COLUMNS

    where = ["i.company_id=?", "i.is_deleted=0"]
    params: list[Any] = [company_id]

    if cat:
        where.append("i.item_category=?")
        params.append(cat)
    if type:
        where.append("i.item_type=?")
        params.append(type)
    if search:
        like = f"%{search}%"
        where.append("(i.item_code LIKE ? OR i.item_name LIKE ? OR i.hsn_code LIKE ?)")
        params.extend([like, like, like])
    if st == "active":
        where.append("i.is_active=1")
    elif st == "inactive":
        where.append("i.is_active=0")
    elif st == "blocked":
        where.append("COALESCE(i.block_status,'')<>''")

    sql = (
        "SELECT i.*, u.code AS uom_code, g.code AS group_code "
        "FROM items i "
        "LEFT JOIN units_of_measure u ON u.id=i.base_uom_id "
        "LEFT JOIN material_groups g ON g.id=i.material_group_id "
        f"WHERE {' AND '.join(where)} ORDER BY i.item_code"
    )
    items = ErpDB.fetch_all(sql, params)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(cols)

    for item in items:
        row = []
        for c in cols:
            row.append(item.get(c, ""))
        writer.writerow(row)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="materials_export.csv"'}
    )


def _parse_csv_text(content: str) -> list[dict[str, str]]:
    """Parse CSV text with comma/semicolon/tab delimiter auto-detection."""
    content = content.lstrip("\ufeff")  # strip UTF-8 BOM
    lines = content.splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="The CSV file is empty.")

    first_line = lines[0]
    delim = ","
    for d in (";", "\t"):
        if first_line.count(d) > first_line.count(delim):
            delim = d

    reader = csv.reader(lines, delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="No header row found.")

    header = [h.strip().lower() for h in header]
    if "item_name" not in header:
        raise HTTPException(status_code=400, detail="The header row must contain at least 'item_name'.")

    known = set(material.MAT_CSV_COLUMNS)
    rows = []
    line_num = 1
    for r in reader:
        line_num += 1
        if not r or not any(field.strip() for field in r):
            continue
        if len(rows) > 5000:
            raise HTTPException(status_code=400, detail="Maximum 5000 rows per import.")
        row_dict = {}
        for idx, col in enumerate(header):
            if col in known and idx < len(r):
                row_dict[col] = r[idx].strip()
        rows.append({"_line": line_num, **row_dict})
    return rows


def _check_row(row: dict[str, Any], company_id: int, update: bool, seen: dict[str, bool]) -> tuple[str, dict, list[str], int]:
    code = str(row.get("item_code") or "").strip().upper()
    ex = None
    if code:
        ex = ErpDB.fetch_one(
            "SELECT * FROM items WHERE company_id=? AND item_code=? AND is_deleted=0",
            [company_id, code]
        )
        if code in seen:
            return "ERROR", {}, [f"Code '{code}' appears multiple times in the file."], 0
        seen[code] = True

    if ex and not update:
        return "SKIP", {}, ["Item already exists. Check 'Update Existing' to overwrite."], int(ex["id"])

    in_data = {}
    if ex:
        # Pre-fill with existing item fields
        for c in material.MAT_CSV_COLUMNS:
            if c in ex:
                in_data[c] = ex[c]
        in_data["base_uom_id"] = ex.get("base_uom_id")
        in_data["material_group_id"] = ex.get("material_group_id")
        # Overwrite with non-empty CSV values
        for k, v in row.items():
            if k.startswith("_"):
                continue
            if v != "":
                in_data[k] = v
        if row.get("uom_code"):
            in_data.pop("base_uom_id", None)
            in_data["uom_code"] = row["uom_code"]
        if row.get("group_code"):
            in_data.pop("material_group_id", None)
            in_data["group_code"] = row["group_code"]
    else:
        in_data = {k: v for k, v in row.items() if not k.startswith("_") and v != ""}

    in_data["confirm_dup"] = 1
    clean_data, errs = material.clean(in_data, company_id, existing_id=int(ex["id"]) if ex else None)

    if ex and "base_uom_id" in clean_data and ex.get("base_uom_id"):
        if int(clean_data["base_uom_id"]) != int(ex["base_uom_id"]):
            usage_info = material.usage(int(ex["id"]), company_id)
            if usage_info.get("grn", 0) > 0 or usage_info.get("po", 0) > 0:
                errs.append("Base UOM cannot be changed — stock movements or purchase documents exist.")

    status = "ERROR" if errs else ("UPDATE" if ex else "NEW")
    return status, clean_data, errs, (int(ex["id"]) if ex else 0)


@router.post("/import/preview", dependencies=[Depends(require_tcode("ZMMI"))])
def preview_import(
    req: Optional[PreviewRequest] = None,
    csv_file: Optional[UploadFile] = File(None),
    update_existing: bool = Form(False),
    user=Depends(require_login),
):
    """Parse CSV, validate each line, and provide a preview token before committing (ZMMI)."""
    _cleanup_previews()
    company_id = user["company_id"]
    update = update_existing

    content = ""
    if csv_file:
        raw = csv_file.file.read()
        content = raw.decode("utf-8", errors="replace")
    elif req and req.csv_content:
        content = req.csv_content
        update = req.update_existing
    else:
        raise HTTPException(status_code=400, detail="No CSV data or file provided.")

    rows = _parse_csv_text(content)
    seen: dict[str, bool] = {}
    counts = {"NEW": 0, "UPDATE": 0, "SKIP": 0, "ERROR": 0}
    preview_items = []

    for r in rows:
        status, clean_d, errs, ex_id = _check_row(r, company_id, update, seen)
        counts[status] += 1
        preview_items.append({
            "line": r["_line"],
            "item_code": clean_d.get("item_code") or r.get("item_code", ""),
            "item_name": clean_d.get("item_name") or r.get("item_name", ""),
            "status": status,
            "errors": errs,
            "existing_id": ex_id,
        })

    token = str(uuid.uuid4()).replace("-", "")
    _PREVIEW_STORE[token] = {
        "created_at": time.time(),
        "company_id": company_id,
        "user_id": user["id"],
        "update": update,
        "rows": rows,
    }

    return {
        "token": token,
        "counts": counts,
        "total_rows": len(rows),
        "preview": preview_items[:100],  # sample first 100
    }


@router.post("/import/confirm", dependencies=[Depends(require_tcode("ZMMI"))])
def confirm_import(req: ConfirmRequest, user=Depends(require_login)):
    """Commit previously previewed material master CSV import (ZMMI)."""
    _cleanup_previews()
    company_id = user["company_id"]
    token = req.token.strip()

    st = _PREVIEW_STORE.pop(token, None)
    if not st or st["company_id"] != company_id or st["user_id"] != user["id"]:
        raise HTTPException(status_code=400, detail="Preview expired or invalid. Please preview the CSV again.")

    update = st["update"]
    rows = st["rows"]
    seen: dict[str, bool] = {}
    counts = {"NEW": 0, "UPDATE": 0, "SKIP": 0, "ERROR": 0}
    messages = []

    for r in rows:
        line_num = r["_line"]
        status, clean_d, errs, ex_id = _check_row(r, company_id, update, seen)
        try:
            if status == "NEW":
                clean_d["company_id"] = company_id
                clean_d["created_by"] = user["id"]
                clean_d["is_deleted"] = 0
                new_id = ErpDB.insert("items", clean_d)
                ErpAuth.audit(
                    "items",
                    new_id,
                    "INSERT",
                    {},
                    {"item_code": clean_d.get("item_code"), "item_name": clean_d.get("item_name"), "source": "ZMMI"},
                )
            elif status == "UPDATE":
                old = ErpDB.fetch_one("SELECT * FROM items WHERE id=? AND company_id=?", [ex_id, company_id])
                if clean_d.get("material_group_id") is None:
                    clean_d["material_group_id"] = 0
                if old:
                    old["material_group_id"] = int(old.get("material_group_id") or 0)
                    diff = material.mat_diff(old, clean_d)
                    if diff:
                        ErpDB.update("items", clean_d, "id=? AND company_id=?", [ex_id, company_id])
                        ErpAuth.audit(
                            "items",
                            ex_id,
                            "UPDATE",
                            {k: v[0] for k, v in diff.items()},
                            {k: v[1] for k, v in diff.items()},
                        )
                    else:
                        status = "SKIP"
        except Exception as e:
            status = "ERROR"
            messages.append(f"Line {line_num}: {e}")

        counts[status] += 1
        if status == "ERROR" and errs:
            messages.append(f"Line {line_num}: {' '.join(errs)}")

    return {
        "status": "success",
        "counts": counts,
        "messages": messages[:50],
    }
