"""
Brain ERP — Helper Functions
Converted from PHP helpers.php by Prigag Online Pvt. Ltd.
Replacement: WordPress utility functions → pure Python equivalents
"""

import re
import math
import logging
import smtplib
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from core.db import ErpDB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS  (mirrors helpers.php constants)
# ---------------------------------------------------------------------------
ERP_NAME      = "Brain ERP"
ERP_VERSION   = "1.21.0"
ERP_DEVELOPER = "Prigag Online Pvt. Ltd."
ERP_WEBSITE   = "www.prigag.com"
ERP_SUPPORT   = "support@prigag.com"

# ---------------------------------------------------------------------------
# COUNTRIES / CURRENCIES  (ported from helpers.php — identical data)
# ---------------------------------------------------------------------------
COUNTRIES: dict = {
    'IN': {'name': 'India',          'currency': 'INR', 'tax_label': 'GSTIN',              'fy': '04-01'},
    'US': {'name': 'United States',  'currency': 'USD', 'tax_label': 'EIN / Sales Tax ID', 'fy': '01-01'},
    'GB': {'name': 'United Kingdom', 'currency': 'GBP', 'tax_label': 'VAT Reg. No.',       'fy': '04-01'},
    'AU': {'name': 'Australia',      'currency': 'AUD', 'tax_label': 'ABN / GST No.',      'fy': '07-01'},
    'AE': {'name': 'UAE',            'currency': 'AED', 'tax_label': 'TRN (VAT)',           'fy': '01-01'},
    'CA': {'name': 'Canada',         'currency': 'CAD', 'tax_label': 'GST/HST No.',         'fy': '01-01'},
    'SG': {'name': 'Singapore',      'currency': 'SGD', 'tax_label': 'GST Reg. No.',        'fy': '01-01'},
    'DE': {'name': 'Germany',        'currency': 'EUR', 'tax_label': 'VAT ID',              'fy': '01-01'},
}

CURRENCIES: dict = {
    'INR': {'symbol': '₹',   'name': 'Indian Rupee',    'major': 'Rupees',   'minor': 'Paise'},
    'USD': {'symbol': '$',   'name': 'US Dollar',       'major': 'Dollars',  'minor': 'Cents'},
    'GBP': {'symbol': '£',   'name': 'British Pound',   'major': 'Pounds',   'minor': 'Pence'},
    'AUD': {'symbol': 'A$',  'name': 'Australian Dollar','major': 'Dollars', 'minor': 'Cents'},
    'AED': {'symbol': 'د.إ', 'name': 'UAE Dirham',       'major': 'Dirhams', 'minor': 'Fils'},
    'CAD': {'symbol': 'C$',  'name': 'Canadian Dollar',  'major': 'Dollars', 'minor': 'Cents'},
    'SGD': {'symbol': 'S$',  'name': 'Singapore Dollar', 'major': 'Dollars', 'minor': 'Cents'},
    'EUR': {'symbol': '€',   'name': 'Euro',             'major': 'Euros',   'minor': 'Cents'},
    'SAR': {'symbol': 'SR',  'name': 'Saudi Riyal',      'major': 'Riyals',  'minor': 'Halalas'},
}

# ---------------------------------------------------------------------------
# CURRENCY HELPERS  (mirrors msme_erp_currency_symbol, msme_erp_money, etc.)
# ---------------------------------------------------------------------------
def currency_symbol(code: Optional[str] = None) -> str:
    code = (code or 'INR').upper()
    return CURRENCIES.get(code, {}).get('symbol', code + ' ')


def money(amount, decimals: int = 2, currency_code: Optional[str] = None) -> str:
    sym = currency_symbol(currency_code) if currency_code else currency_symbol(erp_company_currency())
    return f"{sym}{float(amount):,.{decimals}f}"


def erp_company_currency(company_id: Optional[int] = None) -> str:
    cid = company_id or ErpDB.get_company_id()
    row = ErpDB.fetch_one("SELECT currency_code FROM companies WHERE id=?", [cid])
    return str(row['currency_code']).upper() if row and row.get('currency_code') else 'INR'


def erp_company(company_id: Optional[int] = None) -> dict:
    cid = company_id or ErpDB.get_company_id()
    return ErpDB.fetch_one("SELECT * FROM companies WHERE id=?", [cid]) or {}


def erp_company_name(company_id: Optional[int] = None) -> str:
    c = erp_company(company_id)
    return str(c.get('name') or 'My Company')


def tax_label(country_code: Optional[str] = None) -> str:
    cc = (country_code or 'IN').upper()
    return COUNTRIES.get(cc, {}).get('tax_label', 'Tax Reg. No.')

# ---------------------------------------------------------------------------
# INDIAN AMOUNT IN WORDS  (mirrors erp_num_to_words exactly)
# ---------------------------------------------------------------------------
def num_to_words(amount: float) -> str:
    n = int(amount)
    if n == 0:
        return "Zero Rupees Only"

    words = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
             'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
             'Seventeen', 'Eighteen', 'Nineteen']
    tens  = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def say(n: int) -> str:
        if n < 20:
            return words[n]
        if n < 100:
            return tens[n // 10] + ((' ' + words[n % 10]) if n % 10 else '')
        return words[n // 100] + ' Hundred' + ((' ' + say(n % 100)) if n % 100 else '')

    result = ''
    if n >= 10_000_000:
        result += say(n // 10_000_000) + ' Crore '
        n %= 10_000_000
    if n >= 100_000:
        result += say(n // 100_000) + ' Lakh '
        n %= 100_000
    if n >= 1_000:
        result += say(n // 1_000) + ' Thousand '
        n %= 1_000
    if n > 0:
        result += say(n)
    return result.strip() + ' Rupees Only'


# ---------------------------------------------------------------------------
# INDIAN ID VALIDATION  (mirrors PHP msme_erp_valid_pan / msme_erp_valid_aadhaar)
# ---------------------------------------------------------------------------
def normalize_pan(v: Optional[str]) -> str:
    return re.sub(r'\s+', '', str(v or '')).upper()


def valid_pan(v: str) -> bool:
    return bool(re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', v))


def normalize_aadhaar(v: Optional[str]) -> str:
    return re.sub(r'\D+', '', str(v or ''))


def valid_aadhaar(v: str) -> bool:
    if not re.match(r'^[2-9][0-9]{11}$', v):
        return False
    # Verhoeff algorithm — exact port from PHP
    d  = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
          [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
          [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
          [9,8,7,6,5,4,3,2,1,0]]
    pm = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
          [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
          [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
    c = 0
    for i, ch in enumerate(reversed(v)):
        c = d[c][pm[i % 8][int(ch)]]
    return c == 0


def mask_aadhaar(v: Optional[str]) -> str:
    v = normalize_aadhaar(v)
    return f"XXXX XXXX {v[8:]}" if len(v) == 12 else ('—' if not v else v)


# ---------------------------------------------------------------------------
# GRN DEDUCTION CALCULATION  (mirrors msme_erp_calc_deduction_amount)
# ---------------------------------------------------------------------------
def calc_deduction_amount(mode: str, value: float, base_amount: float) -> float:
    if value <= 0:
        return 0.0
    if mode.upper() == 'AMOUNT':
        return round(value, 2)
    return round(base_amount * value / 100, 2)


# ---------------------------------------------------------------------------
# STOCK HELPERS  (mirrors erp_fmt_qty / msme_erp_stock_cover)
# ---------------------------------------------------------------------------
def fmt_qty(n: float) -> str:
    formatted = f"{n:.3f}".rstrip('0').rstrip('.')
    return formatted or '0'


def stock_cover(needed: float, s: dict) -> dict:
    avail = float(s.get('available', 0))
    if needed <= 0.0005:
        return {'status': 'NA',      'label': '—',                'css': 'secondary', 'shortfall': 0.0}
    if avail >= needed:
        return {'status': 'FULL',    'label': 'In stock',          'css': 'success',   'shortfall': 0.0}
    if avail > 0.0005:
        return {'status': 'PARTIAL', 'label': 'Partly in stock',   'css': 'warning',   'shortfall': needed - avail}
    return     {'status': 'NONE',    'label': 'No stock',           'css': 'danger',    'shortfall': needed}


def item_stock_map(company_id: int, item_ids: list[int]) -> dict:
    """
    Return per-item stock position. Mirrors PHP msme_erp_item_stock_map().
    Returns dict keyed by item_id.
    """
    ids = list(set(int(i) for i in item_ids if i))
    out = {i: {'on_hand': 0.0, 'reserved': 0.0, 'available': 0.0, 'on_order': 0.0, 'wh': []} for i in ids}
    if not ids:
        return out

    placeholders = ','.join('?' for _ in ids)
    rows = ErpDB.fetch_all(
        f"SELECT sb.item_id, sb.qty_on_hand, sb.qty_reserved, w.code AS wh_code, w.name AS wh_name "
        f"FROM stock_balances sb JOIN warehouses w ON w.id=sb.warehouse_id "
        f"WHERE w.company_id=? AND sb.item_id IN ({placeholders})",
        [company_id] + ids
    )
    for r in rows:
        iid = int(r['item_id'])
        q   = float(r['qty_on_hand'])
        out[iid]['on_hand']  += q
        out[iid]['reserved'] += float(r['qty_reserved'])
        if abs(q) > 0.0005:
            out[iid]['wh'].append({'code': r['wh_code'], 'name': r['wh_name'], 'qty': q})

    # Open PO quantities
    rows2 = ErpDB.fetch_all(
        f"SELECT pol.item_id, SUM(MAX(pol.qty_ordered - COALESCE(pol.qty_received,0), 0)) AS open_qty "
        f"FROM purchase_order_lines pol JOIN purchase_orders po ON po.id=pol.po_id "
        f"WHERE po.company_id=? AND pol.item_id IN ({placeholders}) "
        f"AND po.status NOT IN ('CANCELLED','REJECTED','CLOSED','DRAFT') "
        f"GROUP BY pol.item_id",
        [company_id] + ids
    )
    for r in rows2:
        out[int(r['item_id'])]['on_order'] = float(r['open_qty'])

    for o in out.values():
        o['available'] = max(0.0, o['on_hand'] - o['reserved'])

    return out


# ---------------------------------------------------------------------------
# BOM EXPLOSION  (mirrors PHP msme_erp_bom_requirements exactly)
# ---------------------------------------------------------------------------
def bom_requirements(bom_id: int, qty: float, depth: int = 0, acc: dict = None) -> dict:
    """
    Explode a BOM into raw-material requirements.
    Returns dict: { 'item_id:uom_id' => {'item_id': int, 'uom_id': int, 'qty': float} }
    """
    if acc is None:
        acc = {}
    if qty <= 0 or depth > 5:
        return acc

    hdr = ErpDB.fetch_one("SELECT id, item_id, standard_qty FROM bom_headers WHERE id=?", [bom_id])
    if not hdr:
        return acc

    std   = float(hdr['standard_qty']) if float(hdr.get('standard_qty', 0)) > 0 else 1.0
    scale = qty / std

    lines = ErpDB.fetch_all(
        "SELECT component_id, qty_required, uom_id, scrap_pct, is_phantom FROM bom_lines WHERE bom_id=?",
        [bom_id]
    )
    for ln in lines:
        cid  = int(ln['component_id'])
        need = float(ln['qty_required']) * scale * (1 + float(ln.get('scrap_pct', 0)) / 100)
        if need <= 0:
            continue
        if ln.get('is_phantom'):
            sub = ErpDB.fetch_one(
                "SELECT id FROM bom_headers WHERE item_id=? AND is_active=1 AND COALESCE(is_deleted,0)=0 "
                "ORDER BY id DESC LIMIT 1",
                [cid]
            )
            if sub:
                bom_requirements(int(sub['id']), need, depth + 1, acc)
                continue

        uom = int(ln.get('uom_id') or 0)
        if not uom:
            uom = int(ErpDB.fetch_value("SELECT base_uom_id FROM items WHERE id=?", [cid]) or 0)
        key = f"{cid}:{uom}"
        if key not in acc:
            acc[key] = {'item_id': cid, 'uom_id': uom, 'qty': 0.0}
        acc[key]['qty'] += need

    return acc


# ---------------------------------------------------------------------------
# EMAIL  (replaces wp_mail — uses Python smtplib with SMTP settings from DB)
# ---------------------------------------------------------------------------
def send_email(to: str, subject: str, html_body: str) -> tuple[bool, str]:
    """
    Send an HTML email via SMTP. Settings are read from erp_options (same keys
    as WordPress options): erp_smtp_host, erp_smtp_port, erp_smtp_username,
    erp_smtp_password, erp_smtp_from_email, erp_smtp_from_name.
    Returns (success, error_message).
    """
    host = ErpDB.get_option('erp_smtp_host', '')
    user = ErpDB.get_option('erp_smtp_username', '')
    pw   = ErpDB.get_option('erp_smtp_password', '')
    port = int(ErpDB.get_option('erp_smtp_port', 587))
    enc  = ErpDB.get_option('erp_smtp_encryption', 'tls')
    frm  = ErpDB.get_option('erp_smtp_from_email', '') or user
    name = ErpDB.get_option('erp_smtp_from_name',  '') or erp_company_name()

    if not (host and user and pw):
        return False, "SMTP not configured. Add settings in Settings → AI & Notifications."

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"{name} <{frm}>"
    msg['To']      = to
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        if enc == 'ssl':
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, pw)
                server.sendmail(frm, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(user, pw)
                server.sendmail(frm, [to], msg.as_string())
        return True, ""
    except Exception as e:
        logger.error(f"Email send error: {e}")
        return False, str(e)


# ---------------------------------------------------------------------------
# MASTER DATA LOADERS  (mirrors erp_get_items, erp_get_suppliers, etc.)
# ---------------------------------------------------------------------------
def get_items(company_id: int, categories: list[str] = None) -> list[dict]:
    sql    = ("SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, "
              "i.moving_avg_price, i.gst_rate, i.hsn_code, i.item_category "
              "FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id "
              "WHERE i.company_id=? AND COALESCE(i.is_active,1)=1 AND COALESCE(i.is_deleted,0)=0")
    params = [company_id]
    if categories:
        sql += f" AND i.item_category IN ({','.join('?' for _ in categories)})"
        params += categories
    return ErpDB.fetch_all(sql + " ORDER BY i.item_name", params)


def get_warehouses(company_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, code, name FROM warehouses WHERE company_id=? AND COALESCE(is_active,1)=1 ORDER BY code",
        [company_id]
    )


def get_departments(company_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, code, name FROM departments WHERE company_id=? AND COALESCE(is_active,1)=1 "
        "AND COALESCE(is_deleted,0)=0 ORDER BY name",
        [company_id]
    )


def get_employees(company_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, employee_code, (first_name||' '||last_name) AS full_name "
        "FROM employees WHERE company_id=? AND status='ACTIVE' AND COALESCE(is_deleted,0)=0 ORDER BY first_name",
        [company_id]
    )


def get_customers(company_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, customer_code, name, payment_terms, credit_limit FROM customers "
        "WHERE company_id=? AND status='ACTIVE' AND COALESCE(is_deleted,0)=0 ORDER BY name",
        [company_id]
    )


def get_suppliers(company_id: int) -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, supplier_code, name, credit_days FROM suppliers "
        "WHERE company_id=? AND UPPER(COALESCE(status,'ACTIVE'))='ACTIVE' "
        "AND COALESCE(is_deleted,0)=0 ORDER BY name",
        [company_id]
    )


def get_uoms() -> list[dict]:
    return ErpDB.fetch_all(
        "SELECT id, code FROM units_of_measure WHERE COALESCE(is_active,1)=1 ORDER BY code"
    )


# ---------------------------------------------------------------------------
# STATUS CSS CLASS  (mirrors PHP erp_status_badge logic)
# ---------------------------------------------------------------------------
STATUS_CSS = {
    'DRAFT':        'secondary', 'SUBMITTED':    'info',    'APPROVED':  'success',
    'PENDING':      'warning',   'REJECTED':     'danger',  'POSTED':    'primary',
    'CLOSED':       'dark',      'RELEASED':     'success', 'ACTIVE':    'success',
    'OPEN':         'warning',   'PAID':         'success', 'UNPAID':    'danger',
    'PARTIAL':      'warning',   'CANCELLED':    'secondary','DISPATCHED': 'info',
    'IN_PROGRESS':  'warning',   'COMPLETED':    'success', 'NOT_RELEASED': 'warning',
}

def status_css(status: str) -> str:
    return STATUS_CSS.get(status.upper(), 'secondary')


# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------
def erp_today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def erp_now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def current_fiscal_year() -> int:
    now = datetime.now()
    return now.year if now.month >= 4 else now.year - 1


# ---------------------------------------------------------------------------
# NUMBER FORMAT
# ---------------------------------------------------------------------------
def fmt_number(n, decimals: int = 2) -> str:
    return f"{float(n):,.{decimals}f}"


# ---------------------------------------------------------------------------
# UTF-8 SAFETY  (mirrors PHP msme_erp_utf8)
# ---------------------------------------------------------------------------
def safe_str(v) -> str:
    if v is None:
        return ''
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8')
        except UnicodeDecodeError:
            return v.decode('windows-1252', errors='ignore')
    return str(v)


# ---------------------------------------------------------------------------
# ACTIVE-COMPANY CURRENCY / BRANDING / COLOUR  (mirrors msme_erp_cur_code,
# msme_erp_cur, msme_erp_shade, msme_erp_branding)
# ---------------------------------------------------------------------------
_DEFAULT_LOGO = "assets/img/logo.png"


def cur_code(company_id: Optional[int] = None) -> str:
    return erp_company_currency(company_id)


def cur(company_id: Optional[int] = None) -> str:
    """Symbol of the ACTIVE company's currency."""
    return currency_symbol(cur_code(company_id))


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def _sanitize_hex(v) -> str:
    v = (v or "").strip()
    return v if _HEX_RE.match(v) else ""


def shade(hex_color: str, pct: int) -> str:
    """Darken (negative) / lighten (positive) a #rrggbb colour by pct (-100..100)."""
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.match(r"^[0-9a-fA-F]{6}$", h):
        return "#1565c0"
    out = "#"
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16)
        c = round(c * (100 + pct) / 100) if pct < 0 else round(c + (255 - c) * pct / 100)
        out += format(max(0, min(255, c)), "02x")
    return out


def branding(company_id: Optional[int] = None) -> dict:
    """
    Branding of the active company. Company-level values win; anything left
    blank falls back to the bundled defaults (there is no WP Settings page
    with global fallback values in the desktop app).
    """
    c = erp_company(company_id)
    primary = _sanitize_hex(c.get("primary_color")) or "#1565c0"
    return {
        "name": c.get("name") or "My Company",
        "logo": c.get("logo_url") or _DEFAULT_LOGO,
        "has_logo": bool(c.get("logo_url")),
        "bg_color": _sanitize_hex(c.get("bg_color")),
        "bg_image": c.get("bg_image_url") or "",
        "primary": primary,
        "sidebar": _sanitize_hex(c.get("sidebar_color")),
    }


# ---------------------------------------------------------------------------
# DISPLAY T-CODES  (mirrors msme_erp_compute_display_codes / _display_code /
# _tcode_from_display). Computes a short memorable code (e.g. "CRPUR") from
# each T-code's own description, for the T-code bar / sidebar / tiles —
# routing and permissions always keep using the internal T-code (ME51N).
# ---------------------------------------------------------------------------
_display_codes_cache: Optional[dict[str, str]] = None
_WORD_RE = re.compile(r"[A-Za-z]+")

# Explicit overrides where the plain formula gives a clash or an unusable
# result: Price List / Project List would both compute to PRLI; "P&L
# Statement" and "GSTR-3B" split badly on the punctuation.
_DISPLAY_CODE_OVERRIDES = {"VK11": "PRLIM", "CJ03": "PRLIP", "S_ALR": "PLST", "GSTR3B": "GSTR3B"}


def compute_display_codes() -> dict[str, str]:
    global _display_codes_cache
    if _display_codes_cache is not None:
        return _display_codes_cache

    from core.installer import TCODE_DEFINITIONS

    computed: dict[str, Optional[str]] = {}
    counts: dict[str, int] = {}
    for tc, _mod, desc, _file, _icon, _sort in TCODE_DEFINITIONS:
        words = _WORD_RE.findall(desc)
        if len(words) < 2:
            computed[tc] = None  # fallback #1
            continue
        code = words[0][:2].upper() + words[1][:2].upper()
        if len(words) > 2:
            code += words[2][:1].upper()
        computed[tc] = code
        counts[code] = counts.get(code, 0) + 1

    for tc, code in _DISPLAY_CODE_OVERRIDES.items():
        if tc in computed:
            old = computed[tc]
            if old and old in counts:
                counts[old] -= 1
            computed[tc] = code
            counts[code] = counts.get(code, 0) + 1

    result = {}
    for tc, code in computed.items():
        result[tc] = tc if (code is None or counts.get(code, 0) > 1) else code  # fallback #2
    _display_codes_cache = result
    return result


def display_code(tcode: str) -> str:
    return compute_display_codes().get(tcode, tcode)


def tcode_from_display(code: str) -> str:
    """Reverse lookup: typed/bookmarked display code -> internal T-code."""
    rev = {disp.upper(): real for real, disp in compute_display_codes().items() if disp != real}
    return rev.get((code or "").upper(), code)