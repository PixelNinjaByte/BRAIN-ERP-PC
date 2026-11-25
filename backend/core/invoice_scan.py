"""
Brain ERP — Supplier Invoice Scan / Upload (MR01)
Direct port of PHP core/invoice_scan.php → Python.

Take a photo or upload an image / PDF of a supplier invoice, have Google
Gemini (the key saved via ErpDB.get_option) read it, and pre-fill the MR01
form. NOTHING is posted automatically: the user reviews and posts.

Replaces:
  wp_upload_dir()       → BASE_DIR/app data folder on disk
  move_uploaded_file()   → shutil.copy from the temp path the UI hands us
  wp_remote_post()       → httpx.post
  admin_url()/nonce      → no WP AJAX layer; the desktop app calls these
                           functions directly, so no URL/nonce helpers exist

Functions (same names, snake_case):
  invoice_store_file()      save the upload in a protected app-data folder
  invoice_extract()         Gemini vision call -> raw dict
  invoice_normalize()       clean numbers / dates / GSTIN, sanity checks
  invoice_match()           find supplier + PO in the ERP, duplicate check
  invoice_valid_file()      validates a stored-file reference
  invoice_serve_file()      read back the raw bytes of a stored file
  invoice_find_duplicate()  same supplier + same invoice number check

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import os
import re
import json
import base64
import logging
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Optional

from core.db import ErpDB
from core.helpers import erp_company, erp_company_currency, money

logger = logging.getLogger(__name__)

INV_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

# App-data folder for stored invoice scans (mirrors wp-uploads/msme-erp-invoices).
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVOICE_DIR = os.path.join(APP_DIR, "app_data", "invoice_scans")

_FILE_RE = re.compile(r"^(\d{1,6})/(\d{4})/([a-f0-9]{32})\.(jpg|png|webp|pdf)$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATE_DMY_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$")


def invoice_company() -> int:
    return int(ErpDB.get_company_id())


def invoice_dir() -> str:
    return INVOICE_DIR


def _sniff_mime(path: str) -> str:
    """Best-effort content sniff (no browser-supplied type is ever trusted)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return ""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return ""


# ---------------------------------------------------------------------------
# STORE — returns {'ok': bool, 'error': ?, 'file': '2026/abc.jpg', 'path': abs, 'mime': ..}
# ---------------------------------------------------------------------------
def invoice_store_file(tmp_path: str, declared_size: int) -> dict[str, Any]:
    if not tmp_path or not os.path.isfile(tmp_path):
        return {"ok": False, "error": "Upload failed. Please try again."}
    if declared_size > INV_MAX_BYTES:
        return {"ok": False, "error": "File is larger than 10 MB."}

    # Trust the file's real content, never the caller-supplied type or name.
    mime = _sniff_mime(tmp_path)
    if mime not in ALLOWED_TYPES:
        return {"ok": False, "error": "Only JPG, PNG, WebP or PDF invoices are accepted."}

    sub = f"{invoice_company()}/{date.today():%Y}"
    out_dir = os.path.join(invoice_dir(), sub)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        return {"ok": False, "error": "Could not create the invoice folder in app data."}

    name = f"{secrets.token_hex(16)}.{ALLOWED_TYPES[mime]}"
    out_path = os.path.join(out_dir, name)
    try:
        with open(tmp_path, "rb") as src, open(out_path, "wb") as dst:
            dst.write(src.read())
    except OSError:
        return {"ok": False, "error": "Could not save the uploaded file."}

    rel = f"{sub}/{name}"
    return {"ok": True, "file": rel, "path": out_path, "mime": mime}


# A reference the UI hands back: must match our own naming pattern, belong to the
# ACTIVE company (a file from another company is never accepted or served) and exist.
def invoice_valid_file(rel: Optional[str]) -> str:
    rel = rel or ""
    m = _FILE_RE.match(rel)
    if not m:
        return ""
    if int(m.group(1)) != invoice_company():
        return ""
    return rel if os.path.isfile(os.path.join(invoice_dir(), rel)) else ""


def invoice_serve_file(rel: str) -> dict[str, Any]:
    """Return the raw bytes + content type for a stored scan, or an error."""
    valid = invoice_valid_file(rel)
    if not valid:
        return {"ok": False, "error": "Not found"}
    path = os.path.join(invoice_dir(), valid)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}[ext]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return {"ok": False, "error": "Not found"}
    return {"ok": True, "mime": mime, "bytes": data, "filename": f"supplier-invoice.{ext}"}


# ---------------------------------------------------------------------------
# EXTRACT — Gemini vision. Returns {'ok': bool, 'error': ?, 'data': dict}
# ---------------------------------------------------------------------------
def invoice_extract(path: str, mime: str) -> dict[str, Any]:
    api_key = str(ErpDB.get_option("gemini_api_key", "")).strip()
    if api_key == "":
        return {
            "ok": False,
            "error": "No Google Gemini API key is configured (Settings → AI). "
                     "The file is attached — fill the fields by hand.",
        }
    model = ErpDB.get_option("gemini_model", "gemini-2.5-flash")

    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
    except OSError:
        return {"ok": False, "error": "Could not read the saved file."}
    if not raw_bytes:
        return {"ok": False, "error": "Could not read the saved file."}

    prompt = (
        "You are reading a supplier TAX INVOICE (for India this is a GST invoice; elsewhere it may "
        "carry VAT/sales tax). Extract the fields below and answer with ONE JSON object and nothing else.\n"
        "Keys: supplier_name (the SELLER issuing the invoice, not the buyer), supplier_gstin (the seller's "
        "tax registration number: 15-character GSTIN in India, otherwise VAT/tax ID), "
        "invoice_number, invoice_date (YYYY-MM-DD), due_date (YYYY-MM-DD or null), po_number (buyer's "
        "purchase order reference or null), taxable_amount (total taxable value before tax), cgst, sgst, "
        "igst (amounts, 0 if absent or not applicable), total_tax (sum of ALL tax on the invoice), "
        "total_amount (final invoice total payable, after round-off), tds_amount (only if printed, else 0).\n"
        "Rules: numbers are plain decimals with no commas or currency symbols; use null for anything you "
        "cannot read; never guess or invent values; dates written dd/mm/yyyy are day-first."
    )

    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw_bytes).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }

    try:
        import httpx
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            content=json.dumps(body),
            timeout=60,
        )
    except Exception as exc:  # network / timeout / DNS etc.
        return {"ok": False, "error": f"AI request failed: {exc}"}

    if resp.status_code != 200:
        try:
            err_json = resp.json()
            msg = err_json.get("error", {}).get("message", "Unknown error")
        except Exception:
            msg = "Unknown error"
        return {"ok": False, "error": f"AI request failed (HTTP {resp.status_code}): {msg}"}

    try:
        payload = resp.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError):
        text = ""

    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": "The AI could not read this invoice clearly. Try a sharper, well-lit photo "
                     "— or fill the fields by hand.",
        }
    return {"ok": True, "data": data}


# ---------------------------------------------------------------------------
# NORMALISE — clean values and flag anything that does not add up
# ---------------------------------------------------------------------------
def invoice_num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
    try:
        return round(float(s), 2) if s not in ("", "-", ".", "-.") else None
    except ValueError:
        return None


def invoice_date(v: Any) -> Optional[str]:
    v = str(v or "").strip()
    if v == "":
        return None
    m = _DATE_ISO_RE.match(v)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            date(y, mo, d)
            return v
        except ValueError:
            return None
    m = _DATE_DMY_RE.match(v)  # day-first
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            date(y, mo, d)
            return f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            return None
    return None


def invoice_normalize(raw: dict) -> dict:
    warn: list[str] = []
    gstin = re.sub(r"\s+", "", str(raw.get("supplier_gstin") or "")).upper()
    is_india = str(erp_company().get("country_code") or "IN").upper() == "IN"
    if is_india and gstin != "" and not _GSTIN_RE.match(gstin):
        warn.append(f"The supplier GSTIN read from the invoice ({gstin}) is not a valid GSTIN format — check it.")
        gstin = ""

    cg = invoice_num(raw.get("cgst")) or 0.0
    sg = invoice_num(raw.get("sgst")) or 0.0
    ig = invoice_num(raw.get("igst")) or 0.0
    tax = invoice_num(raw.get("total_tax"))
    base = invoice_num(raw.get("taxable_amount"))
    total = invoice_num(raw.get("total_amount"))

    tax_parts = round(cg + sg + ig, 2)
    if tax is None:
        tax = tax_parts if tax_parts > 0 else None
    if tax is not None and tax_parts > 0 and abs(tax - tax_parts) > 1.0:
        warn.append("Total GST on the invoice does not equal CGST + SGST + IGST — check the tax amount.")

    if base is None and total is not None and tax is not None:
        base = round(total - tax, 2)
    if tax is None and total is not None and base is not None:
        tax = round(total - base, 2)
    if total is None and base is not None and tax is not None:
        total = round(base + tax, 2)

    # Round-off on real invoices is a few rupees at most.
    if base is not None and tax is not None and total is not None and abs((base + tax) - total) > 5.0:
        diff = money(abs((base + tax) - total), currency_code=erp_company_currency())
        warn.append(f"Taxable value + GST does not match the invoice total (difference {diff}) "
                    "— please verify the amounts against the paper invoice.")

    inv_date = invoice_date(raw.get("invoice_date"))
    due_date = invoice_date(raw.get("due_date"))
    if raw.get("invoice_date") and not inv_date:
        warn.append("The invoice date could not be read reliably — enter it manually.")
    if inv_date and inv_date > (date.today() + timedelta(days=1)).isoformat():
        warn.append("The invoice date is in the future — check it.")

    return {
        "supplier_name": str(raw.get("supplier_name") or "").strip(),
        "supplier_gstin": gstin,
        "invoice_number": str(raw.get("invoice_number") or "").strip()[:50],
        "invoice_date": inv_date,
        "due_date": due_date,
        "po_number": str(raw.get("po_number") or "").strip(),
        "base_amount": base,
        "tax_amount": tax,
        "total_amount": total,
        "tds_amount": invoice_num(raw.get("tds_amount")) or 0.0,
        "warnings": warn,
    }


# ---------------------------------------------------------------------------
# MATCH — supplier (GSTIN first, then name), PO, credit days, duplicate
# ---------------------------------------------------------------------------
def _similar_pct(a: str, b: str) -> float:
    """Port of PHP similar_text() percentage (longest-common-substring recursion)."""
    def common(s1: str, s2: str) -> int:
        if not s1 or not s2:
            return 0
        best_len = best_i = best_j = 0
        for i in range(len(s1)):
            for j in range(len(s2)):
                k = 0
                while i + k < len(s1) and j + k < len(s2) and s1[i + k] == s2[j + k]:
                    k += 1
                if k > best_len:
                    best_len, best_i, best_j = k, i, j
        if best_len == 0:
            return 0
        return (best_len
                + common(s1[:best_i], s2[:best_j])
                + common(s1[best_i + best_len:], s2[best_j + best_len:]))

    total = len(a) + len(b)
    if total == 0:
        return 0.0
    return (common(a, b) * 2 * 100.0) / total


def invoice_match(d: dict, company_id: int) -> dict:
    supplier = None
    how = ""

    if d.get("supplier_gstin"):
        supplier = ErpDB.fetch_one(
            "SELECT id, name, credit_days FROM suppliers WHERE company_id=? AND UPPER(gstin)=?",
            [company_id, d["supplier_gstin"]],
        )
        if supplier:
            how = "GSTIN"

    if not supplier and d.get("supplier_name"):
        def norm(s: str) -> str:
            s = re.sub(r"\b(pvt|private|ltd|limited|llp|co|company|and|&|m/s|ms)\b|[^a-z0-9 ]", " ", s.lower())
            return re.sub(r"\s+", " ", s).strip()

        want = norm(d["supplier_name"])
        best = 0.0
        best_row = None
        for s in ErpDB.fetch_all("SELECT id, name, credit_days FROM suppliers WHERE company_id=?", [company_id]):
            have = norm(s["name"])
            if not have or not want:
                continue
            pct = 100.0 if have == want else _similar_pct(want, have)
            if pct > best:
                best, best_row = pct, s
        if best_row and best >= 75:
            supplier = best_row
            how = f"name ({round(best)}% match)"

    po = None
    if d.get("po_number"):
        po = ErpDB.fetch_one(
            "SELECT id, po_number FROM purchase_orders WHERE company_id=? AND po_number=? AND is_deleted=0",
            [company_id, d["po_number"]],
        )
        if not po:  # the invoice may print the PO number with extra text around it
            po = ErpDB.fetch_one(
                "SELECT id, po_number FROM purchase_orders "
                "WHERE company_id=? AND is_deleted=0 AND ? LIKE ('%' || po_number || '%') "
                "ORDER BY id DESC LIMIT 1",
                [company_id, d["po_number"]],
            )

    days = None
    if d.get("invoice_date") and d.get("due_date") and d["due_date"] >= d["invoice_date"]:
        dt1 = datetime.strptime(d["invoice_date"], "%Y-%m-%d")
        dt2 = datetime.strptime(d["due_date"], "%Y-%m-%d")
        days = (dt2 - dt1).days
    elif supplier:
        days = int(supplier["credit_days"])

    dup = None
    if supplier and d.get("invoice_number"):
        dup = invoice_find_duplicate(company_id, int(supplier["id"]), d["invoice_number"])

    return {
        "supplier_id": int(supplier["id"]) if supplier else None,
        "supplier_label": supplier["name"] if supplier else None,
        "supplier_how": how,
        "po_id": int(po["id"]) if po else None,
        "po_label": po["po_number"] if po else None,
        "credit_days": days,
        "duplicate": dup,
    }


# Same supplier + same supplier invoice number (ignoring case/spaces), not cancelled.
def invoice_find_duplicate(company_id: int, supplier_id: int, ref_no: str, exclude_id: int = 0) -> Optional[dict]:
    key = re.sub(r"\s+", "", ref_no).upper()
    if key == "":
        return None
    return ErpDB.fetch_one(
        "SELECT id, invoice_number, invoice_date FROM supplier_invoices "
        "WHERE company_id=? AND supplier_id=? AND status<>'CANCELLED' AND id<>? "
        "AND UPPER(REPLACE(ref_invoice_num,' ',''))=? LIMIT 1",
        [company_id, supplier_id, exclude_id, key],
    )