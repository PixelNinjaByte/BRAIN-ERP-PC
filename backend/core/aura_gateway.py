"""
Brain ERP — AURA 7 Gateway (v1.20.0)
Port of PHP core/aura_gateway.php → Python / SQLite.

Lets an external AI agent (AURA 7 desktop companion, or any MCP client)
talk to Brain ERP safely:

  POST /api/aura/mcp          MCP (Streamable HTTP, JSON-RPC 2.0)
  GET  /api/aura/ping         health check
  GET  /api/aura/tools        list tools (plain REST)
  POST /api/aura/tools/<name> call a tool (plain REST, JSON body = arguments)
  GET  /api/aura/events       poll the event queue (?after_id=N)
  POST /api/aura/hook/<name>  inbound custom webhook

SECURITY MODEL:
  - Every call carries a secret token (Authorization: Bearer pgb_...).
    Only the SHA-256 hash is stored; the token is shown once, when created.
  - A token is bound to ONE ERP user and ONE company. After the token
    is verified the request runs AS that user, so the ERP's own roles,
    T-codes and approval levels decide what AURA 7 may see or do.
    Nothing here bypasses ErpAuth.
  - Scopes per token: read (default), write, approve. Write/approve tools
    are refused unless the token holds that scope.
  - Per-token rate limit, failed-login throttle per IP, write actions audit-logged.

NOT PORTED (and why):
  - msme_erp_aura_ensure_schema(): DDL for aura_tokens and aura_events.
    Tables live in schema.sql / installer.py, not created lazily.
  - register_rest_route(...): WordPress REST API registration. FastAPI
    routers in backend/routers/ handle routing and HTTP responses.
  - msme_erp_aura_settings_handle(), msme_erp_aura_settings_card_html(),
    msme_erp_aura_render_tokens_table(): WP-admin settings UI panels
    (lines ~607–756), skipped per port convention.
  - add_filter('rest_allowed_cors_headers', ...), add_action('rest_api_init', ...):
    WordPress hooks.
  - apply_filters('msme_erp_aura_tools', $T): Returns tools dict directly.

Original: Prigag Online Pvt. Ltd. (v1.20.0)
Port:     Python/SQLite standalone desktop version
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from core.auth import ErpAuth
from core.db import ErpDB
from core.reports import report_can_access, report_defs, run_report

logger = logging.getLogger(__name__)

AURA_NS = "prigag-brain/v1"
AURA_SCHEMA_VERSION = "1.20.0"
SUPPORTED_MCP_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

# Context local storage for request execution
_local = threading.local()

# Rate limiting caches: {ip: (fail_count, expire_timestamp)}
_fail_lock = threading.Lock()
_fail_attempts: dict[str, tuple[int, float]] = {}

# Per-token rate counters: {f"{token_id}_{minute_bucket}": count}
_rate_lock = threading.Lock()
_rate_counters: dict[str, int] = {}


def get_ctx() -> dict[str, Any]:
    """Return the current AURA token execution context."""
    ctx = getattr(_local, "ctx", None)
    if ctx is None:
        return {
            "token_id": 0,
            "label": "internal",
            "scopes": ["read", "write", "approve"],
            "company_id": ErpAuth.company_id(),
            "user_id": ErpAuth.user_id(),
        }
    return dict(ctx)


def set_ctx(ctx: dict[str, Any]) -> None:
    """Set the current AURA token execution context."""
    _local.ctx = dict(ctx)


def is_enabled() -> bool:
    """Check whether AURA 7 gateway is enabled in system options."""
    return bool(int(ErpDB.get_option("erp_aura_enabled", 0) or 0))


def company_for_user(user_id: int, wanted_company_id: int) -> int:
    """Resolve the pinned company for a user if they cannot switch company."""
    if ErpAuth.is_super_admin() or ErpAuth.has_role("SUPER_ADMIN"):
        return wanted_company_id
    co = ErpDB.fetch_value("SELECT company_id FROM users WHERE id=?", (user_id,))
    if co and int(co) > 0:
        return int(co)
    return wanted_company_id


def create_token(
    label: str,
    user_id: int,
    company_id: int = 1,
    scopes: Optional[list[str]] = None,
    days: int = 0,
    created_by: int = 0,
) -> str:
    """Generate a new AURA token, store its SHA-256 hash, and return the plaintext token."""
    valid_scopes = [s for s in (scopes or ["read"]) if s in ("read", "write", "approve")]
    if "read" not in valid_scopes:
        valid_scopes.insert(0, "read")

    token = "pgb_" + secrets.token_hex(20)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_hint = token[:8] + "…" + token[-4:]

    expires_at = None
    if days > 0:
        expires_at = datetime.fromtimestamp(
            time.time() + days * 86400, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

    resolved_company = company_for_user(user_id, company_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ErpDB.insert("aura_tokens", {
        "company_id": resolved_company,
        "wp_user_id": user_id,
        "label": label[:100],
        "token_hash": token_hash,
        "token_hint": token_hint,
        "scopes": ",".join(valid_scopes),
        "is_active": 1,
        "expires_at": expires_at,
        "created_by": created_by or ErpAuth.user_id(),
        "created_at": now_str,
    })
    return token


def authenticate(token: str, ip: str = "") -> dict[str, Any]:
    """Validate Bearer token, enforce rate limits, and set active context.

    Returns the context dict or raises PermissionError / ValueError.
    """
    if not is_enabled():
        raise PermissionError("The AURA 7 connection is switched off in system options.")

    now = time.time()
    ip_key = hashlib.md5((ip or "127.0.0.1").encode("utf-8")).hexdigest()

    with _fail_lock:
        if ip_key in _fail_attempts:
            count, expire = _fail_attempts[ip_key]
            if expire > now and count >= 20:
                raise PermissionError("Too many invalid attempts. Try again in a few minutes.")
            elif expire <= now:
                del _fail_attempts[ip_key]

    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    row = None
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = ErpDB.fetch_one(
            "SELECT * FROM aura_tokens WHERE token_hash=? LIMIT 1",
            (token_hash,),
        )

    bad = not row or not int(row.get("is_active") or 0)
    if not bad and row.get("expires_at"):
        try:
            exp_ts = datetime.strptime(str(row["expires_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            if exp_ts < now:
                bad = True
        except Exception:
            pass

    if bad:
        with _fail_lock:
            cur_cnt = _fail_attempts.get(ip_key, (0, 0))[0]
            _fail_attempts[ip_key] = (cur_cnt + 1, now + 600)
        raise PermissionError("Invalid, expired or revoked token.")

    # Rate limiting
    limit = max(10, int(ErpDB.get_option("msme_erp_aura_rate_limit", 120) or 120))
    minute_bucket = int(now // 60)
    rl_key = f"{row['id']}_{minute_bucket}"
    with _rate_lock:
        cur_reqs = _rate_counters.get(rl_key, 0) + 1
        _rate_counters[rl_key] = cur_reqs
        # Prune old counters
        for k in list(_rate_counters.keys()):
            try:
                b = int(k.split("_")[-1])
                if b < minute_bucket - 2:
                    del _rate_counters[k]
            except Exception:
                pass

    if cur_reqs > limit:
        raise PermissionError(f"Rate limit of {limit} calls/minute reached.")

    uid = int(row.get("wp_user_id") or 0)
    user_row = ErpDB.fetch_one("SELECT * FROM users WHERE id=? AND COALESCE(is_deleted,0)=0", (uid,))
    if not user_row:
        raise PermissionError("The user this token belongs to no longer exists or is deleted.")

    company_id = int(row.get("company_id") or 1)
    ErpDB.use_company(company_id)

    # Set user session in ErpAuth
    roles = ErpDB.fetch_all(
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=?",
        (uid,),
    )
    role_codes = [r["code"] for r in roles] if roles else []
    ErpAuth.set_session({
        "user_id": uid,
        "company_id": company_id,
        "username": user_row.get("username") or "",
        "full_name": user_row.get("full_name") or user_row.get("username") or "",
        "roles": role_codes,
        "branch_id": int(user_row.get("branch_id") or 1),
    })

    now_mysql = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ErpDB.update("aura_tokens", {
        "last_used_at": now_mysql,
        "last_ip": ip[:64],
    }, "id=?", (row["id"],))

    scopes = [s.strip() for s in str(row.get("scopes") or "").split(",") if s.strip()]
    ctx = {
        "token_id": int(row["id"]),
        "label": str(row.get("label") or ""),
        "scopes": scopes,
        "company_id": company_id,
        "user_id": uid,
    }
    set_ctx(ctx)
    return ctx


# ============================================================
# EVENT QUEUE + OUTBOUND WEBHOOK
# ============================================================
def _send_webhook_async(url: str, body: str, event: str, secret: str) -> None:
    """Deliver outbound webhook payload in a daemon background thread."""
    try:
        sig = "sha256=" + hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Prigag-Event": event,
                "X-Prigag-Signature": sig,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception as exc:
        logger.warning("AURA webhook POST to %s failed: %s", url, exc)


def emit(event: str, payload: Optional[dict[str, Any]] = None, company_id: int = 0) -> None:
    """Emit an event into the AURA event queue and fire webhook if configured."""
    if not is_enabled():
        return
    try:
        cid = company_id or ErpDB.get_company_id() or ErpAuth.company_id()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload_data = payload or {}
        payload_json = json.dumps(payload_data, ensure_ascii=False)

        event_id = ErpDB.insert("aura_events", {
            "company_id": cid,
            "event": event[:60],
            "payload": payload_json,
            "created_at": now_str,
        })

        url = str(ErpDB.get_option("msme_erp_aura_webhook_url", "") or "").strip()
        if url and re.match(r"^https?://", url, re.IGNORECASE):
            secret = str(ErpDB.get_option("msme_erp_aura_webhook_secret", "") or "")
            body = json.dumps({
                "id": event_id,
                "event": event,
                "company_id": cid,
                "created_at": now_str,
                "data": payload_data,
            }, ensure_ascii=False)
            threading.Thread(
                target=_send_webhook_async,
                args=(url, body, event, secret),
                daemon=True,
            ).start()
    except Exception as exc:
        logger.error("AURA emit failed for event '%s': %s", event, exc)


def on_db_inserted(table: str, record_id: int, data: dict[str, Any]) -> None:
    """Auto-emit AURA events on record creation for tracked ERP tables."""
    if not is_enabled():
        return
    tbl = table.lower()
    mapping = {
        "document_releases": ("approval.requested", ["doc_type", "doc_number", "doc_amount"]),
        "purchase_orders": ("po.created", ["po_number", "supplier_id", "grand_total", "status"]),
        "sales_orders": ("so.created", ["so_number", "customer_id", "grand_total", "status"]),
        "sales_invoices": ("invoice.created", ["inv_number", "customer_id", "grand_total", "due_date"]),
        "payments": ("payment.recorded", ["payment_number", "payment_type", "party_type", "amount"]),
        "goods_receipts": ("grn.created", ["grn_number", "po_id", "status"]),
        "non_conformances": ("quality.ncr", ["ncr_number", "description"]),
    }
    if tbl not in mapping:
        return
    event_name, fields = mapping[tbl]
    payload: dict[str, Any] = {"record_id": int(record_id)}
    for f in fields:
        if f in data:
            payload[f] = data[f]
    emit(event_name, payload, int(data.get("company_id") or 0))


def daily_digest() -> None:
    """Generate daily digest events for active companies and prune old events."""
    if not is_enabled():
        return
    try:
        companies = ErpDB.fetch_all("SELECT id FROM companies WHERE is_active=1")
        for co in companies:
            cid = int(co["id"])
            emit("daily.digest", overview(cid), cid)
        # Prune events older than 14 days
        cutoff = datetime.fromtimestamp(time.time() - 14 * 86400).strftime("%Y-%m-%d %H:%M:%S")
        ErpDB.query("DELETE FROM aura_events WHERE created_at < ?", (cutoff,))
    except Exception as exc:
        logger.error("AURA daily digest failed: %s", exc)


# ============================================================
# TOOL HELPERS
# ============================================================
def overview(company_id: int) -> dict[str, Any]:
    """Summary overview metrics for the executive dashboard."""
    def safe(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:
            return 0

    today = date.today().isoformat()
    c = company_id
    return {
        "as_of": today,
        "open_purchase_orders": safe(lambda: int(ErpDB.fetch_value(
            "SELECT COUNT(*) FROM purchase_orders WHERE company_id=? AND COALESCE(is_deleted,0)=0 "
            "AND status NOT IN('CLOSED','CANCELLED','REJECTED','RECEIVED')",
            (c,),
        ) or 0)),
        "open_sales_orders": safe(lambda: int(ErpDB.fetch_value(
            "SELECT COUNT(*) FROM sales_orders WHERE company_id=? AND COALESCE(is_deleted,0)=0 "
            "AND status NOT IN('CLOSED','CANCELLED','REJECTED','COMPLETED')",
            (c,),
        ) or 0)),
        "pending_approvals": safe(lambda: int(ErpDB.fetch_value(
            "SELECT COUNT(*) FROM document_releases WHERE company_id=? AND status='PENDING'",
            (c,),
        ) or 0)),
        "receivables_outstanding": safe(lambda: float(ErpDB.fetch_value(
            "SELECT COALESCE(SUM(due_amount),0) FROM sales_invoices WHERE company_id=? AND status NOT IN('PAID','CANCELLED')",
            (c,),
        ) or 0.0)),
        "receivables_overdue": safe(lambda: float(ErpDB.fetch_value(
            "SELECT COALESCE(SUM(due_amount),0) FROM sales_invoices WHERE company_id=? AND status NOT IN('PAID','CANCELLED') AND due_date < ?",
            (c, today),
        ) or 0.0)),
        "payables_outstanding": safe(lambda: float(ErpDB.fetch_value(
            "SELECT COALESCE(SUM(due_amount),0) FROM supplier_invoices WHERE company_id=? AND status NOT IN('PAID','CANCELLED')",
            (c,),
        ) or 0.0)),
        "items_below_reorder": safe(lambda: len(low_stock(c, 500))),
    }


def low_stock(company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """List materials whose total on-hand stock is below their reorder point."""
    lim = max(1, min(500, limit))
    rows = ErpDB.fetch_all(
        "SELECT i.id, i.item_code, i.item_name, i.reorder_point, i.min_stock, "
        "COALESCE(SUM(sb.qty_on_hand),0) AS qty_on_hand "
        "FROM items i LEFT JOIN stock_balances sb ON sb.item_id=i.id "
        "WHERE i.company_id=? AND COALESCE(i.is_deleted,0)=0 AND i.is_active=1 AND i.reorder_point>0 "
        "GROUP BY i.id, i.item_code, i.item_name, i.reorder_point, i.min_stock "
        "HAVING COALESCE(SUM(sb.qty_on_hand),0) < i.reorder_point "
        "ORDER BY i.item_name LIMIT ?",
        (company_id, lim),
    )
    return rows or []


def _schema_obj(props: Optional[dict[str, Any]] = None, req: Optional[list[str]] = None) -> dict[str, Any]:
    s: dict[str, Any] = {"type": "object", "properties": props or {}}
    if req:
        s["required"] = req
    return s


def _like(s: Any) -> str:
    cleaned = str(s or "").strip().replace("%", "").replace("_", "")
    return f"%{cleaned}%"


def _date_str(v: Any) -> str:
    s = str(v or "").strip()
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else ""


def _lim(a: dict[str, Any], default: int = 25) -> int:
    try:
        val = int(a.get("limit") or default)
        return max(1, min(100, val))
    except Exception:
        return default


_TOOLS_CACHE: Optional[dict[str, dict[str, Any]]] = None


def get_tools() -> dict[str, dict[str, Any]]:
    """Return the dictionary of available AURA tools with schema and handlers."""
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE

    limit_prop = {"limit": {"type": "integer", "description": "Max rows (1-100, default 25)"}}
    T: dict[str, dict[str, Any]] = {}

    # 1. erp_info
    def _handle_erp_info(a: dict[str, Any]) -> dict[str, Any]:
        u = ErpAuth.user()
        ctx = get_ctx()
        co = ErpDB.fetch_one("SELECT company_name, currency FROM companies WHERE id=?", (u.get("company_id", 1),))
        co_name = co["company_name"] if co else "Default Company"
        currency = co["currency"] if co else "INR"
        return {
            "company_id": u.get("company_id", 1),
            "company": co_name,
            "currency": currency,
            "user": u.get("full_name") or u.get("username") or "",
            "roles": u.get("roles") or [],
            "token": ctx.get("label", ""),
            "scopes": ctx.get("scopes", []),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    T["erp_info"] = {
        "scope": "read",
        "title": "ERP connection info",
        "description": "Who AURA is connected as: company, currency, ERP user, roles and the scopes this token holds. Call first to know what is allowed.",
        "input": _schema_obj(),
        "handler": _handle_erp_info,
    }

    # 2. erp_overview
    T["erp_overview"] = {
        "scope": "read",
        "title": "Business overview",
        "description": "Headline numbers: open POs/SOs, pending approvals, receivables (total/overdue), payables, items below reorder level.",
        "input": _schema_obj(),
        "handler": lambda a: overview(ErpAuth.company_id()),
    }

    # 3. search_materials
    def _handle_search_materials(a: dict[str, Any]) -> list[dict[str, Any]]:
        n = _lim(a)
        q = _like(a.get("query") or "")
        return ErpDB.fetch_all(
            "SELECT id, item_code, item_name, item_category, hsn_code, gst_rate, "
            "standard_price, moving_avg_price, reorder_point, min_stock, is_active "
            "FROM items WHERE company_id=? AND COALESCE(is_deleted,0)=0 "
            "AND (item_code LIKE ? OR item_name LIKE ?) ORDER BY item_name LIMIT ?",
            (ErpAuth.company_id(), q, q, n),
        )

    T["search_materials"] = {
        "scope": "read",
        "title": "Search materials",
        "description": "Find material master records by code or name.",
        "input": _schema_obj(
            {"query": {"type": "string", "description": "Part of the item code or name"}, **limit_prop},
            ["query"],
        ),
        "handler": _handle_search_materials,
    }

    # 4. stock_levels
    def _handle_stock_levels(a: dict[str, Any]) -> list[dict[str, Any]]:
        w = ["i.company_id=?"]
        p: list[Any] = [ErpAuth.company_id()]
        n = _lim(a, 50)
        if a.get("query"):
            w.append("(i.item_code LIKE ? OR i.item_name LIKE ?)")
            q = _like(a["query"])
            p.extend([q, q])
        if a.get("warehouse_id"):
            w.append("sb.warehouse_id=?")
            p.append(int(a["warehouse_id"]))
        p.append(n)
        return ErpDB.fetch_all(
            "SELECT i.item_code, i.item_name, wh.name AS warehouse, sb.qty_on_hand, sb.qty_reserved, sb.avg_cost "
            "FROM stock_balances sb JOIN items i ON i.id=sb.item_id JOIN warehouses wh ON wh.id=sb.warehouse_id "
            f"WHERE {' AND '.join(w)} ORDER BY i.item_name, wh.name LIMIT ?",
            tuple(p),
        )

    T["stock_levels"] = {
        "scope": "read",
        "title": "Stock levels",
        "description": "On-hand and reserved quantity per warehouse. Filter by item code/name and/or warehouse id.",
        "input": _schema_obj({
            "query": {"type": "string", "description": "Item code or name (optional)"},
            "warehouse_id": {"type": "integer", "description": "Warehouse id (optional)"},
            **limit_prop,
        }),
        "handler": _handle_stock_levels,
    }

    # 5. low_stock
    T["low_stock"] = {
        "scope": "read",
        "title": "Items below reorder level",
        "description": "Materials whose total on-hand stock is under their reorder point.",
        "input": _schema_obj(limit_prop),
        "handler": lambda a: low_stock(ErpAuth.company_id(), _lim(a, 50)),
    }

    # 6. list_purchase_orders
    def _handle_list_purchase_orders(a: dict[str, Any]) -> list[dict[str, Any]]:
        w = ["po.company_id=?", "COALESCE(po.is_deleted,0)=0"]
        p: list[Any] = [ErpAuth.company_id()]
        n = _lim(a)
        if a.get("status"):
            w.append("po.status=?")
            p.append(str(a["status"]).upper())
        if a.get("supplier"):
            w.append("s.name LIKE ?")
            p.append(_like(a["supplier"]))
        f_date = _date_str(a.get("from"))
        if f_date:
            w.append("po.po_date>=?")
            p.append(f_date)
        t_date = _date_str(a.get("to"))
        if t_date:
            w.append("po.po_date<=?")
            p.append(t_date)
        p.append(n)
        return ErpDB.fetch_all(
            "SELECT po.id, po.po_number, po.po_date, s.name AS supplier, po.status, po.release_status, po.delivery_date, po.grand_total "
            f"FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE {' AND '.join(w)} ORDER BY po.id DESC LIMIT ?",
            tuple(p),
        )

    T["list_purchase_orders"] = {
        "scope": "read",
        "title": "Purchase orders",
        "description": "Recent purchase orders. Status: DRAFT, APPROVED, PARTIAL, RECEIVED, CLOSED, REJECTED, CANCELLED.",
        "input": _schema_obj({
            "status": {"type": "string", "description": "Status filter"},
            "supplier": {"type": "string", "description": "Supplier name contains"},
            "from": {"type": "string", "description": "PO date from YYYY-MM-DD"},
            "to": {"type": "string", "description": "PO date to YYYY-MM-DD"},
            **limit_prop,
        }),
        "handler": _handle_list_purchase_orders,
    }

    # 7. list_sales_orders
    def _handle_list_sales_orders(a: dict[str, Any]) -> list[dict[str, Any]]:
        w = ["so.company_id=?", "COALESCE(so.is_deleted,0)=0"]
        p: list[Any] = [ErpAuth.company_id()]
        n = _lim(a)
        if a.get("status"):
            w.append("so.status=?")
            p.append(str(a["status"]).upper())
        if a.get("customer"):
            w.append("cu.name LIKE ?")
            p.append(_like(a["customer"]))
        f_date = _date_str(a.get("from"))
        if f_date:
            w.append("so.so_date>=?")
            p.append(f_date)
        t_date = _date_str(a.get("to"))
        if t_date:
            w.append("so.so_date<=?")
            p.append(t_date)
        p.append(n)
        return ErpDB.fetch_all(
            "SELECT so.id, so.so_number, so.so_date, cu.name AS customer, so.status, so.release_status, so.delivery_date, so.grand_total "
            f"FROM sales_orders so JOIN customers cu ON cu.id=so.customer_id WHERE {' AND '.join(w)} ORDER BY so.id DESC LIMIT ?",
            tuple(p),
        )

    T["list_sales_orders"] = {
        "scope": "read",
        "title": "Sales orders",
        "description": "Recent sales orders, optionally filtered by status, customer name or date range.",
        "input": _schema_obj({
            "status": {"type": "string", "description": "Status filter"},
            "customer": {"type": "string", "description": "Customer name contains"},
            "from": {"type": "string", "description": "SO date from YYYY-MM-DD"},
            "to": {"type": "string", "description": "SO date to YYYY-MM-DD"},
            **limit_prop,
        }),
        "handler": _handle_list_sales_orders,
    }

    # 8. list_invoices
    def _handle_list_invoices(a: dict[str, Any]) -> list[dict[str, Any]]:
        sales = str(a.get("kind") or "sales") != "supplier"
        n = _lim(a)
        p: list[Any] = [ErpAuth.company_id()]
        if sales:
            w = ["si.company_id=?", "si.status<>'CANCELLED'"]
            if a.get("only_open") or a.get("overdue_only"):
                w.append("si.status<>'PAID'")
            if a.get("overdue_only"):
                w.append("si.due_date < date('now')")
            if a.get("party"):
                w.append("cu.name LIKE ?")
                p.append(_like(a["party"]))
            p.append(n)
            return ErpDB.fetch_all(
                "SELECT si.inv_number AS number, si.inv_date AS date, cu.name AS party, si.grand_total AS total, "
                "si.paid_amount, si.due_amount, si.due_date, si.status "
                f"FROM sales_invoices si JOIN customers cu ON cu.id=si.customer_id WHERE {' AND '.join(w)} "
                "ORDER BY si.due_date IS NULL, si.due_date LIMIT ?",
                tuple(p),
            )
        else:
            w = ["sv.company_id=?", "sv.status<>'CANCELLED'"]
            if a.get("only_open") or a.get("overdue_only"):
                w.append("sv.status<>'PAID'")
            if a.get("overdue_only"):
                w.append("sv.due_date < date('now')")
            if a.get("party"):
                w.append("s.name LIKE ?")
                p.append(_like(a["party"]))
            p.append(n)
            return ErpDB.fetch_all(
                "SELECT sv.invoice_number AS number, sv.invoice_date AS date, s.name AS party, sv.total_amount AS total, "
                "sv.paid_amount, sv.due_amount, sv.due_date, sv.status "
                f"FROM supplier_invoices sv JOIN suppliers s ON s.id=sv.supplier_id WHERE {' AND '.join(w)} "
                "ORDER BY sv.due_date IS NULL, sv.due_date LIMIT ?",
                tuple(p),
            )

    T["list_invoices"] = {
        "scope": "read",
        "title": "Invoices (sales or supplier)",
        "description": "kind=sales for customer invoices (receivables) or kind=supplier for supplier invoices (payables). Use only_open / overdue_only to focus on money still due.",
        "input": _schema_obj(
            {
                "kind": {"type": "string", "enum": ["sales", "supplier"], "description": "sales or supplier"},
                "only_open": {"type": "boolean"},
                "overdue_only": {"type": "boolean"},
                "party": {"type": "string", "description": "Customer/supplier name contains"},
                **limit_prop,
            },
            ["kind"],
        ),
        "handler": _handle_list_invoices,
    }

    # 9. list_parties
    def _handle_list_parties(a: dict[str, Any]) -> list[dict[str, Any]]:
        n = _lim(a)
        q = _like(a.get("query") or "") if a.get("query") else "%"
        if str(a.get("kind") or "") == "supplier":
            return ErpDB.fetch_all(
                "SELECT id, supplier_code AS code, name, contact_person, phone, email, city, state, gstin, payment_terms, status "
                "FROM suppliers WHERE company_id=? AND COALESCE(is_deleted,0)=0 AND (name LIKE ? OR supplier_code LIKE ?) ORDER BY name LIMIT ?",
                (ErpAuth.company_id(), q, q, n),
            )
        return ErpDB.fetch_all(
            "SELECT id, customer_code AS code, name, contact_person, phone, email, city, state, gstin, payment_terms, credit_days, credit_limit, status "
            "FROM customers WHERE company_id=? AND COALESCE(is_deleted,0)=0 AND (name LIKE ? OR customer_code LIKE ?) ORDER BY name LIMIT ?",
            (ErpAuth.company_id(), q, q, n),
        )

    T["list_parties"] = {
        "scope": "read",
        "title": "Customers / suppliers",
        "description": "Look up customers or suppliers by name or code (contact details, GSTIN, payment terms). Bank details are never returned.",
        "input": _schema_obj(
            {
                "kind": {"type": "string", "enum": ["customer", "supplier"]},
                "query": {"type": "string", "description": "Name or code contains"},
                **limit_prop,
            },
            ["kind"],
        ),
        "handler": _handle_list_parties,
    }

    # 10. pending_approvals
    def _handle_pending_approvals(a: dict[str, Any]) -> list[dict[str, Any]]:
        n = _lim(a, 50)
        rows = ErpDB.fetch_all(
            "SELECT id AS release_id, doc_type, doc_id, doc_number, doc_amount, current_level, requested_at "
            "FROM document_releases WHERE company_id=? AND status='PENDING' ORDER BY id DESC LIMIT ?",
            (ErpAuth.company_id(), n),
        )
        for r in rows:
            r["can_decide"] = bool(ErpAuth.can_release_document(int(r["release_id"])))
        return rows

    T["pending_approvals"] = {
        "scope": "read",
        "title": "Pending approvals",
        "description": "Documents waiting for release (PO, SO, WO, PR). can_decide tells whether the connected user may approve each one at its current level.",
        "input": _schema_obj(limit_prop),
        "handler": _handle_pending_approvals,
    }

    # 11. decide_approval
    def _handle_decide_approval(a: dict[str, Any]) -> dict[str, Any]:
        rid = int(a.get("release_id") or 0)
        dec = str(a.get("decision") or "").upper()
        if dec not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        rel = ErpDB.fetch_one(
            "SELECT * FROM document_releases WHERE id=? AND company_id=? AND status='PENDING'",
            (rid, ErpAuth.company_id()),
        )
        if not rel:
            raise ValueError("No pending release with that id in this company.")
        if not ErpAuth.can_release_document(rid):
            raise PermissionError("The connected ERP user is not authorised to decide this approval level.")
        remarks = str(a.get("remarks") or "")[:400]
        ErpDB.release_document(rid, dec, ErpAuth.user_id(), f"[via AURA 7] {remarks}")
        return {"ok": True, "release_id": rid, "doc_number": rel.get("doc_number"), "decision": dec}

    T["decide_approval"] = {
        "scope": "approve",
        "title": "Approve or reject a document",
        "description": "APPROVE or REJECT a pending release. Needs the 'approve' scope AND the connected ERP user must hold the role for the current approval level. Ask the human to confirm before calling.",
        "input": _schema_obj(
            {
                "release_id": {"type": "integer", "description": "release_id from pending_approvals"},
                "decision": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
                "remarks": {"type": "string", "description": "Reason / note"},
            },
            ["release_id", "decision"],
        ),
        "handler": _handle_decide_approval,
    }

    # 12. list_reports
    def _handle_list_reports(a: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        mod_filter = str(a.get("module") or "").strip().lower()
        for tc, d in report_defs().items():
            if mod_filter and d.get("module", "").lower() != mod_filter:
                continue
            if not report_can_access(tc):
                continue
            out.append({
                "tcode": tc,
                "module": d.get("module", ""),
                "group": d.get("group", ""),
                "title": d.get("title", ""),
                "filters": d.get("filters", []),
            })
        return out

    T["list_reports"] = {
        "scope": "read",
        "title": "List ERP reports",
        "description": "The report library (T-code, module, title, accepted filters) that the connected user may run.",
        "input": _schema_obj({"module": {"type": "string", "description": "Optional module filter, e.g. MM, SD, FI"}}),
        "handler": _handle_list_reports,
    }

    # 13. run_report
    def _handle_run_report(a: dict[str, Any]) -> dict[str, Any]:
        tc = str(a.get("tcode") or "").strip().upper()
        defs = report_defs()
        if tc not in defs:
            raise ValueError(f"Unknown report T-code {tc}. Call list_reports.")
        if not report_can_access(tc):
            raise PermissionError(f"The connected user may not run {tc}.")

        d = defs[tc]
        in_filters = a.get("filters") if isinstance(a.get("filters"), dict) else {}
        fl = d.get("filters") or []

        f: dict[str, Any] = {
            "from": "", "to": "", "as_of": "", "supplier": 0, "customer": 0, "item": 0,
            "warehouse": 0, "matgroup": 0, "department": 0, "status": "", "text": "",
            "days": int(d.get("days_default") or 0), "month": "", "year": 0, "run": 0, "opt": "",
        }
        today_date = date.today()
        f["from"] = _date_str(in_filters.get("from")) or (
            datetime(today_date.year, today_date.month, 1).strftime("%Y-%m-01") if "from" in fl else ""
        )
        f["to"] = _date_str(in_filters.get("to")) or (today_date.isoformat() if "to" in fl else "")
        f["as_of"] = _date_str(in_filters.get("as_of")) or (today_date.isoformat() if "as_of" in fl else "")
        for k in ("supplier", "customer", "item", "warehouse", "matgroup", "department", "run"):
            f[k] = int(in_filters.get(k) or 0)
        f["status"] = str(in_filters.get("status") or "").strip().upper()
        f["text"] = str(in_filters.get("text") or "").strip()
        if "days" in in_filters:
            f["days"] = max(1, int(in_filters["days"]))
        month_str = str(in_filters.get("month") or "")
        f["month"] = month_str if re.match(r"^\d{4}-\d{2}$", month_str) else (
            today_date.strftime("%Y-%m") if "month" in fl else ""
        )
        f["year"] = int(in_filters.get("year") or 0) if "year" in in_filters else (
            today_date.year if "year" in fl else 0
        )
        opt_str = re.sub(r"[^a-zA-Z_]", "", str(in_filters.get("opt") or ""))
        if not opt_str and d.get("opt") and isinstance(d["opt"].get("choices"), dict):
            choices = list(d["opt"]["choices"].keys())
            opt_str = choices[0] if choices else ""
        f["opt"] = opt_str

        res = run_report(tc, ErpAuth.company_id(), f)
        rows = res.get("rows") or []
        max_rows = max(1, min(200, int(a.get("limit") or 50)))
        totals: dict[str, float] = {}
        for col in res.get("sum") or []:
            totals[col] = round(sum(float(r.get(col) or 0.0) for r in rows), 2)

        return {
            "tcode": tc,
            "title": d.get("title", ""),
            "columns": res.get("cols") or [],
            "row_count": len(rows),
            "rows": rows[:max_rows],
            "truncated": len(rows) > max_rows,
            "totals": totals,
        }

    T["run_report"] = {
        "scope": "read",
        "title": "Run an ERP report",
        "description": "Run a report from list_reports by T-code. filters is an object using the keys listed for that report.",
        "input": _schema_obj(
            {
                "tcode": {"type": "string", "description": "Report T-code"},
                "filters": {"type": "object", "description": "Report filters"},
                "limit": {"type": "integer", "description": "Max rows, default 50, max 200"},
            },
            ["tcode"],
        ),
        "handler": _handle_run_report,
    }

    # 14. get_events
    def _handle_get_events(a: dict[str, Any]) -> dict[str, Any]:
        n = _lim(a, 50)
        after_id = max(0, int(a.get("after_id") or 0))
        rows = ErpDB.fetch_all(
            "SELECT id, event, payload, created_at FROM aura_events "
            "WHERE company_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (ErpAuth.company_id(), after_id, n),
        )
        for r in rows:
            try:
                r["payload"] = json.loads(str(r["payload"]))
            except Exception:
                pass
        latest_id = int(rows[-1]["id"]) if rows else after_id
        return {"events": rows, "latest_id": latest_id}

    T["get_events"] = {
        "scope": "read",
        "title": "Poll ERP events",
        "description": "Events the ERP produced since after_id. Poll this to let AURA react proactively; remember the highest id you saw.",
        "input": _schema_obj({"after_id": {"type": "integer", "description": "Return events with id greater than this (default 0)"}, **limit_prop}),
        "handler": _handle_get_events,
    }

    # 15. notify_user
    def _handle_notify_user(a: dict[str, Any]) -> dict[str, Any]:
        to = int(a.get("to_user_id") or 0)
        if to:
            exists = ErpDB.fetch_value(
                "SELECT id FROM users WHERE id=? AND company_id=? AND COALESCE(is_deleted,0)=0",
                (to, ErpAuth.company_id()),
            )
            if not exists:
                raise ValueError("That ERP user is not in this company.")
        target_uid = to or ErpAuth.user_id()
        title_str = "🤖 " + str(a.get("title") or "")
        msg_str = str(a.get("message") or "")[:140]
        nid = ErpDB.insert("notifications", {
            "user_id": target_uid,
            "title": title_str[:190],
            "message": msg_str,
            "type": "INFO",
        })
        return {"ok": True, "notification_id": nid}

    T["notify_user"] = {
        "scope": "write",
        "title": "Send an ERP notification",
        "description": "Drop a notification in an ERP user's bell (defaults to the connected user). Needs the 'write' scope.",
        "input": _schema_obj(
            {
                "title": {"type": "string", "description": "Short title"},
                "message": {"type": "string", "description": "Message (140 chars)"},
                "to_user_id": {"type": "integer", "description": "ERP user id (optional)"},
            },
            ["title"],
        ),
        "handler": _handle_notify_user,
    }

    _TOOLS_CACHE = T
    return T


def call_tool(name: str, args: Optional[dict[str, Any]] = None) -> Any:
    """Run one tool with scope enforcement and audit logging."""
    tools = get_tools()
    if name not in tools:
        raise ValueError(f"Unknown tool '{name}'.")

    t = tools[name]
    ctx = get_ctx()
    need = t.get("scope", "read")
    if need not in ctx.get("scopes", []):
        raise PermissionError(f"This token does not have the '{need}' scope required by {name}.")

    args_data = args or {}
    out = (t["handler"])(args_data)

    if need != "read" or name == "run_report":
        try:
            ErpAuth.audit(
                "aura_api",
                0,
                "AURA_" + name.upper(),
                {},
                {"token": ctx.get("label", ""), "args": dict(list(args_data.items())[:8])},
                "AURA7",
            )
        except Exception:
            pass

    return out


# ============================================================
# MCP (Stateless JSON-RPC 2.0 handler)
# ============================================================
def mcp_handle(msg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Process an MCP JSON-RPC 2.0 message and return response dict (or None for notifications)."""
    msg_id = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if "id" not in msg and method.startswith("notifications/"):
        return None

    if method == "initialize":
        want = str(params.get("protocolVersion") or "")
        proto_ver = want if want in SUPPORTED_MCP_VERSIONS else SUPPORTED_MCP_VERSIONS[0]
        return ok({
            "protocolVersion": proto_ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "prigag-brain", "title": "Prigag Brain ERP", "version": AURA_SCHEMA_VERSION},
            "instructions": "Prigag Brain ERP. Call erp_info first, then erp_overview. Read tools are safe; decide_approval and notify_user change data — confirm with the human first.",
        })
    elif method == "ping":
        return ok({})
    elif method == "tools/list":
        tool_list = []
        for n, t in get_tools().items():
            tool_list.append({
                "name": n,
                "title": t.get("title") or n,
                "description": t.get("description") or "",
                "inputSchema": t.get("input") or {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": t.get("scope", "read") == "read"},
            })
        return ok({"tools": tool_list})
    elif method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            data = call_tool(tool_name, arguments)
            return ok({
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "isError": False,
            })
        except Exception as exc:
            return ok({
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
    else:
        return err(-32601, f"Method not found: {method}")
