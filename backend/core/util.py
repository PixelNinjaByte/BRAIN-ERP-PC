"""
backend/core/util.py
--------------------
Small shared helpers that the PHP code got from WordPress / helpers.php.

  get_option()      <- WP get_option()  (settings saved on the Settings / FINSET screens)
  opt_bool()        <- truthiness of an option the way PHP saw it ("0", "", None => False)
  has_col()         <- msme_erp_has_col()
  currency_symbol() <- msme_erp_currency_symbol()

!! ONE PLACE TO ADAPT: get_option() guesses the settings store (table `options`
!! with option_name/option_value, or `settings` with key/value). Point it at
!! wherever your Python Settings screen saves its values.
"""
from __future__ import annotations

from typing import Any

from backend.core.db import ErpDB

_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AUD": "A$", "CAD": "C$",
                     "SGD": "S$", "NZD": "NZ$", "AED": "AED "}


def get_option(name: str, default: Any = None) -> Any:
    for sql in ("SELECT option_value FROM options WHERE option_name=?",
                "SELECT value FROM settings WHERE key=?"):
        try:
            v = ErpDB.fetch_value(sql, [name])
            if v is not None:
                return v
        except Exception:  # noqa: BLE001
            continue
    return default


def opt_bool(name: str, default: Any = 0) -> bool:
    v = get_option(name, default)
    return str(v).strip().lower() not in ("", "0", "false", "no", "off", "none")


_cols: dict[str, set[str]] = {}


def has_col(table: str, col: str) -> bool:
    if table not in _cols:
        try:
            _cols[table] = {r["name"] for r in ErpDB.fetch_all(f"PRAGMA table_info({table})")}
        except Exception:  # noqa: BLE001
            return False
    return col in _cols[table]


def currency_symbol(code: str | None) -> str:
    code = (code or "INR").upper()
    return _CURRENCY_SYMBOLS.get(code, code + " ")


# ---- double-submit guard (replaces the PHP single-use form token) ----
import threading as _threading
import time as _time

_seen: dict[str, float] = {}
_seen_lock = _threading.Lock()


def claim_request_id(rid: str | None) -> bool:
    """True the first time a client-generated request_id is seen (10 min window); False on a repeat."""
    if not rid:
        return True
    now = _time.time()
    with _seen_lock:
        for k in [k for k, t in _seen.items() if now - t > 600]:
            _seen.pop(k, None)
        if rid in _seen:
            return False
        _seen[rid] = now
        return True


def release_request_id(rid: str | None) -> None:
    if rid:
        with _seen_lock:
            _seen.pop(rid, None)