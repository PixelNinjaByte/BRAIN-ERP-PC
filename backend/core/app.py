"""
Brain ERP — App Dispatcher & Navigation Logic
Direct port of PHP core/app.php -> Python.

SCOPE NOTE — read this before extending the file:
app.php is two very different things stitched together:
  (1) T-code name/fuzzy matching, the dispatch decision logic, the nav-menu
      *data*, flash messages and dashboard KPIs — all pure, reusable logic
      with no WordPress dependency once translated. That is what this file
      ports, in full, below.
  (2) ~900 lines of literal WordPress-admin-page markup: the topbar/sidebar
      HTML, inline CSS and jQuery, wp_nonce_url()/admin_url() links, a
      dropdown "My Account" menu, etc. (msme_erp_render_layout()'s HTML
      body). That part has no Python equivalent in *this* codebase: the
      backend is FastAPI + PyQt6, there is no admin.php router, no WP
      nonces/transients, and none of modules/*.php were ported, so there is
      nothing for that markup to glue together. Translating ~900 lines of
      WP markup 1:1 would produce dead HTML no PyQt6 view can render.
      Porting the *real* equivalent (a Qt sidebar/topbar, or a web template
      if a view layer gets added later) is a UI-design task, not a
      mechanical translation — ask for it explicitly and say which UI
      (Qt widgets vs. a web/HTML view) you want it built as, so the result
      is something your app can actually render.

What IS ported here, 1:1 with the PHP:
  match_tcode_by_name()     fuzzy English-phrase -> T-code resolver + stemmer
  tcode_suggestions()       "did you mean" candidate list (data, not HTML)
  dispatch()                routing decision (which T-code/handler to open)
  set_flash/get_flash/...   one-shot flash message (in-memory, replaces
                             WP transients — there is no WP options table)
  dashboard_kpis()          the 6 dashboard KPI counts
  build_nav_tree()          module -> group -> sub-group -> T-code tree,
                            pruned to what the user can see (same algorithm
                            as the PHP $resolveGroup/$hasActive/$renderGroup
                            closures), as plain data a Qt tree widget (or a
                            future web sidebar) can consume directly
  icons_grid_data()         T-codes grouped by module, for a "\\ICONS" view
  QUICK_ACCESS_TCODES / NAV_MODULES / NAV_SUB_GROUPS
                            the same menu/tile configuration as the PHP file

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.db import ErpDB
from core.auth import ErpAuth

logger = logging.getLogger(__name__)


# ============================================================
# NAV MENU DATA  (mirrors $navModules / $navSubGroups in app.php)
# ============================================================
NAV_MODULES: dict[str, dict[str, str]] = {
    "ADMIN": {"icon": "bi-gear-fill",            "label": "Admin",                     "color": "#6c757d"},
    "HR":    {"icon": "bi-people-fill",          "label": "HR",                        "color": "#0dcaf0"},
    "MM":    {"icon": "bi-box-seam-fill",        "label": "Materials",                 "color": "#ffc107"},
    "SD":    {"icon": "bi-cart-fill",            "label": "Sales",                     "color": "#198754"},
    "INV":   {"icon": "bi-archive-fill",         "label": "Inventory",                 "color": "#0d6efd"},
    "BOM":   {"icon": "bi-diagram-3-fill",       "label": "BOM",                        "color": "#6f42c1"},
    "PROD":  {"icon": "bi-gear-wide-connected",  "label": "Production",                "color": "#fd7e14"},
    "FI":    {"icon": "bi-bank2",                "label": "Accounting",                "color": "#dc3545"},
    "QM":    {"icon": "bi-clipboard2-check-fill","label": "Quality",                   "color": "#20c997"},
    "PM":    {"icon": "bi-tools",                "label": "Plant Maint.",              "color": "#795548"},
    "PS":    {"icon": "bi-kanban-fill",          "label": "Projects & Developments",   "color": "#5b21b6"},
    "COMM":  {"icon": "bi-inbox-fill",           "label": "Communication",            "color": "#e91e63"},
}

# Second level of the left-hand tree: module -> [groups]. A group's "items"
# holds either a T-code string or a nested group dict, in display order, to
# any depth. Anything not listed here stays directly under its module.
NAV_SUB_GROUPS: dict[str, list[dict[str, Any]]] = {
    "ADMIN": [
        {"label": "Authorization", "icon": "bi-shield-lock", "items": ["SU01", "PFCG", "SU56", "SU53"]},
        {"label": "Organization", "icon": "bi-diagram-3-fill", "items": ["OS01", "OBY6", "CMPY", "ZMDC", "ZMAT"]},
        {"label": "System", "icon": "bi-hdd-network", "items": ["NRIV", "SREL", "SM21", "ZQDR"]},
        {"label": "My Workspace", "icon": "bi-person-circle", "items": ["APPROVALS", "MYACC", "SU3"]},
    ],
    "HR": [
        {"label": "Personnel Administration", "icon": "bi-person-vcard", "items": ["PA01", "PA30"]},
        {"label": "Organizational Management", "icon": "bi-diagram-2", "items": ["OM01"]},
        {"label": "Time Management", "icon": "bi-clock-history", "items": ["PT50", "PT60"]},
        {"label": "Payroll", "icon": "bi-cash-stack", "items": ["PY01", "PC00"]},
    ],
    "MM": [
        {"label": "Master Data", "icon": "bi-database", "items": [
            {"label": "Material", "icon": "bi-box-seam", "items": ["MM01", "MM02", "MM03", "OMSF", "ZMMI", "ZMRP", "VK11"]},
            {"label": "Supplier", "icon": "bi-truck", "items": ["XK01", "XK02"]},
        ]},
        {"label": "Purchasing", "icon": "bi-cart-plus", "items": [
            {"label": "Purchase Requisition", "icon": "bi-file-earmark-plus", "items": ["ME51N", "ME53N"]},
            {"label": "RFQ / Quotation", "icon": "bi-envelope-paper", "items": ["ME41", "ME47"]},
            {"label": "Bids / Tenders", "icon": "bi-clipboard2-check", "items": ["BID01", "BID02"]},
            {"label": "Purchase Order", "icon": "bi-bag", "items": ["ME21N", "ME23N", "ME2M"]},
            {"label": "Service Procurement", "icon": "bi-clipboard-check", "items": ["ML81N"]},
        ]},
        {"label": "Inventory Management", "icon": "bi-boxes", "items": [
            {"label": "Goods Movement", "icon": "bi-arrow-down-circle", "items": ["MIGO", "LT01"]},
            {"label": "Returns", "icon": "bi-arrow-return-left", "items": ["PRTN"]},
            {"label": "Stock", "icon": "bi-archive", "items": ["MMBE", "MB5L"]},
            {"label": "Physical Inventory", "icon": "bi-clipboard-data", "items": ["MI01", "MI07"]},
        ]},
        {"label": "Invoice Verification", "icon": "bi-receipt-cutoff", "items": ["MR01"]},
        {"label": "Reports", "icon": "bi-bar-chart", "items": ["ME2PN", "ZMMR"]},
    ],
    "SD": [
        {"label": "Master Data", "icon": "bi-person-lines-fill", "items": ["XD01"]},
        {"label": "Sales", "icon": "bi-cart", "items": [
            {"label": "Quotation", "icon": "bi-file-earmark-plus", "items": ["QB01"]},
            {"label": "Sales Order", "icon": "bi-bag", "items": ["VA01", "VA03"]},
        ]},
        {"label": "Shipping", "icon": "bi-truck-flatbed", "items": ["VL01N", "EWB01"]},
        {"label": "Billing", "icon": "bi-file-earmark-text", "items": ["VF01", "VF03"]},
        {"label": "Returns", "icon": "bi-arrow-return-right", "items": ["SRTN"]},
    ],
    "BOM": [
        {"label": "Bill of Material", "icon": "bi-diagram-3-fill", "items": ["CS01", "CS03"]},
        {"label": "Routing & Work Center", "icon": "bi-signpost-split", "items": ["CA01", "CR01"]},
    ],
    "PROD": [
        {"label": "Planning", "icon": "bi-calendar-week", "items": ["PP01"]},
        {"label": "Work Orders", "icon": "bi-gear", "items": ["CO01", "CO13"]},
        {"label": "Reports", "icon": "bi-bar-chart", "items": ["COOIS", "CK13N"]},
    ],
    "FI": [
        {"label": "General Account", "icon": "bi-journal-bookmark-fill",
         "items": ["FS00", "FB50", "FB03", "FBA1", "FBOB", "KS01", "KS02", "KS03"]},
        {"label": "Accounts Receivable", "icon": "bi-person-check", "items": ["F-28", "FB75", "FBL5N", "FBRA"]},
        {"label": "Accounts Payable", "icon": "bi-credit-card",
         "items": ["F-53", "F110", "FBAP", "FB65", "FBL1N", "MRBR", "ZINT"]},
        {"label": "Bank", "icon": "bi-bank", "items": ["FF67", "ZCBK"]},
        {"label": "Financial Statements", "icon": "bi-graph-up",
         "items": ["F.01", "S_ALR", "F.08", "F.CF", "FAGLL03", "ZDAY", "KSB1"]},
        {"label": "Fixed Assets", "icon": "bi-building-gear", "items": ["AW01N", "AFAB"]},
        {"label": "GST Returns", "icon": "bi-file-earmark-pdf", "items": ["GSTR1", "GSTR3B", "GSTR2B", "ZEIV"]},
        {"label": "TDS", "icon": "bi-percent", "items": ["TDS01", "TDSM"]},
        {"label": "Period-End & Configuration", "icon": "bi-sliders",
         "items": ["OB52", "FAGLGVTR", "FBPOST", "OBYC", "FINSET", "OB08"]},
    ],
    "QM": [
        {"label": "Inspection", "icon": "bi-clipboard-data", "items": ["QM01", "QA32"]},
        {"label": "Quality Notification", "icon": "bi-exclamation-triangle", "items": ["QF01"]},
    ],
    "PM": [
        {"label": "Technical Objects", "icon": "bi-cpu", "items": ["IE01"]},
        {"label": "Maintenance Processing", "icon": "bi-tools", "items": ["IW21", "IP01"]},
        {"label": "Reports", "icon": "bi-clipboard2-pulse", "items": ["IW38"]},
    ],
    "PS": [
        {"label": "Project Definition", "icon": "bi-kanban", "items": ["CJ01", "CJ02", "CJ03"]},
        {"label": "Budget & Costs", "icon": "bi-cash-coin", "items": ["CJ30", "CJI3"]},
        {"label": "Execution", "icon": "bi-diagram-2", "items": ["CN01"]},
    ],
    "COMM": [
        {"label": "Business Workplace", "icon": "bi-inbox-fill", "items": ["SBWP", "SO01"]},
    ],
}

# Dashboard "Quick Access" tiles — [tcode, label, icon, color].
QUICK_ACCESS_TCODES: list[tuple[str, str, str, str]] = [
    ("MM01", "Create Material", "bi-box-seam", "#ffc107"),
    ("ME21N", "Create PO", "bi-bag-plus", "#fd7e14"),
    ("MIGO", "Goods Receipt", "bi-arrow-down-circle", "#198754"),
    ("VA01", "Sales Order", "bi-cart-plus", "#198754"),
    ("VF01", "Create Invoice", "bi-receipt", "#0d6efd"),
    ("MMBE", "Stock Overview", "bi-boxes", "#6f42c1"),
    ("PA01", "New Employee", "bi-person-plus", "#0dcaf0"),
    ("PY01", "Payroll Run", "bi-cash-stack", "#198754"),
    ("CO01", "Work Order", "bi-gear", "#fd7e14"),
    ("CS01", "Create BOM", "bi-diagram-3", "#6f42c1"),
    ("FS00", "Chart of Accts", "bi-bank2", "#dc3545"),
    ("FB50", "Journal Entry", "bi-journal-plus", "#dc3545"),
]


# ============================================================
# NAME-BASED T-CODE LOOKUP
# ============================================================
# Lets the T-code bar accept a plain-English action name — "Create
# Purchase Request" — as well as the literal code ("ME51N"). Tried in
# order: exact description match, then a substring match, then a fuzzy
# word-overlap score against every registered T-code's description.
# min_coverage is how much of the typed phrase has to be accounted for
# (1.0 = every word); dispatch() calls this twice — once at the normal
# bar (0.6) to resolve/redirect, and again at a looser bar (0.3) only to
# build "did you mean" suggestions if that first call found nothing.
_SUFFIXES = ["isition", "ication", "tion", "sion", "ing", "ment", "ed", "es", "s"]
_STOPWORDS = {"a", "an", "the", "of", "to", "for", "on", "in", "new"}
_SYNONYMS = {
    "make": "create", "add": "create", "raise": "create", "generate": "create",
    "show": "view", "list": "view", "see": "view", "display": "view", "check": "view",
    "update": "edit", "modify": "edit", "change": "edit",
    "remove": "delete", "cancel": "delete",
    "bill": "invoice", "po": "order", "pr": "requisition", "so": "order",
    "staff": "employee", "worker": "employee", "grn": "receipt",
}


def _stem(word: str) -> str:
    w = word.lower()
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _split_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in _STOPWORDS]


def match_tcode_by_name(query: str, min_coverage: float = 0.6) -> list[dict]:
    query = (query or "").strip()
    if query == "" or len(query) < 3:
        return []

    all_rows = ErpDB.fetch_all("SELECT * FROM tcodes WHERE is_active = 1")
    if not all_rows:
        return []

    # 1) Exact description match (case-insensitive)
    for row in all_rows:
        if row["description"].casefold() == query.casefold():
            return [row]

    # 2) Substring match — query appears inside the description, or the
    # description appears inside the query (covers someone typing the
    # full official name, and someone typing a fragment of it).
    substring_hits = [
        row for row in all_rows
        if query.lower() in row["description"].lower() or row["description"].lower() in query.lower()
    ]
    if len(substring_hits) == 1:
        return substring_hits

    # 3) Fuzzy word-overlap scoring, with a tiny stemmer so "request"
    # still matches "requisition", "invoice" matches "invoicing", etc.
    query_words = [_SYNONYMS.get(w, w) for w in _split_words(query)]
    if not query_words:
        return substring_hits
    query_stems = [_stem(w) for w in query_words]

    scored = []
    for row in all_rows:
        desc_words = _split_words(row["description"])
        if not desc_words:
            continue
        desc_stems = [_stem(w) for w in desc_words]

        matched = 0
        for qs in query_stems:
            for ds in desc_stems:
                if qs == ds or (len(qs) >= 4 and len(ds) >= 4 and (ds.startswith(qs) or qs.startswith(ds))):
                    matched += 1
                    break

        coverage = matched / len(query_stems)
        if coverage >= min_coverage:
            scored.append({"row": row, "coverage": coverage, "extra": len(desc_stems) - matched})

    if not scored:
        return []

    scored.sort(key=lambda s: (-s["coverage"], s["extra"]))

    # Clear winner: only one candidate cleared the bar, or the top match
    # covers every word the user typed and is unambiguously ahead.
    if len(scored) == 1 or (scored[0]["coverage"] >= 0.999 and (len(scored) < 2 or scored[1]["coverage"] < 0.999)):
        return [scored[0]["row"]]

    # Ambiguous — return every candidate at (or near) the top score so the
    # caller can show a picker instead of guessing.
    top = scored[0]["coverage"]
    return [s["row"] for s in scored if s["coverage"] >= top - 0.001]


def tcode_suggestions(query: str, matches: list[dict], ambiguous: bool) -> dict:
    """
    "Did you mean" candidates as plain data (no HTML — there is no WP admin
    page to echo markup into here). A UI layer renders this however it
    likes: a picker dialog in Qt, a dropdown in a future web view, etc.
    """
    return {
        "query": query,
        "ambiguous": ambiguous,
        "intro": (f'"{query}" matches more than one screen — which did you mean?' if ambiguous
                  else f'T-Code or name "{query}" not found. Did you mean:'),
        "matches": [{"tcode": m["tcode"], "description": m["description"]} for m in matches],
    }


# ============================================================
# DISPATCHER
# ============================================================
@dataclass
class DispatchResult:
    kind: str  # 'tcode' | 'suggestions' | 'not_found' | 'settings' | 'module_index' | 'dashboard'
    tcode: str = ""
    description: str = ""
    handler: Optional[Callable] = None
    hint: str = ""
    error: str = ""
    suggestions: Optional[dict] = None


def dispatch(page: str, tcode: str, module_map: dict[str, Callable]) -> DispatchResult:
    """
    Routing decision only. The PHP original include()'d a module .php file
    on disk; here module_map maps a T-code (or a module-index key) straight
    to a registered Python handler, since there is no PHP file tree to walk.
    """
    from core.helpers import tcode_from_display  # local import: avoids a cycle at module load

    if tcode:
        internal = tcode_from_display(tcode)
        if internal != tcode:
            tcode = internal

        tc_row = ErpDB.fetch_one("SELECT * FROM tcodes WHERE tcode=? AND is_active=1", [tcode])

        typed_name = None
        if not tc_row:
            matches = match_tcode_by_name(tcode)
            if len(matches) == 1:
                typed_name = tcode
                tc_row = matches[0]
                tcode = tc_row["tcode"]
            elif len(matches) > 1:
                return DispatchResult(kind="suggestions", suggestions=tcode_suggestions(tcode, matches, True))

        if tc_row:
            hint = (f'Matched "{typed_name}" to {tcode} — {tc_row["description"]}' if typed_name is not None else "")
            handler = module_map.get(tcode)
            if handler is not None:
                return DispatchResult(kind="tcode", tcode=tcode, description=tc_row["description"],
                                       handler=handler, hint=hint)
            return DispatchResult(kind="tcode", tcode=tcode, description=tc_row["description"],
                                   hint=hint, error=f"Module not registered for T-Code: {tcode}")

        # T-code not in DB, and no name match cleared the normal 0.6
        # coverage bar — try once more at a looser bar just to offer
        # "did you mean" suggestions instead of a flat dead end.
        loose = match_tcode_by_name(tcode, 0.3)
        if loose:
            return DispatchResult(kind="suggestions", suggestions=tcode_suggestions(tcode, loose, False))
        return DispatchResult(kind="not_found", tcode=tcode,
                               error=f"T-Code {tcode} is not registered. Check the T-Code and try again.")

    if page == "msme-erp-settings":
        handler = module_map.get("__settings__")
        return DispatchResult(kind="settings", handler=handler, description="ERP Settings")

    if page in module_map:
        return DispatchResult(kind="module_index", handler=module_map[page], description=page.capitalize())

    return DispatchResult(kind="dashboard", description="Dashboard")


# ============================================================
# FLASH MESSAGE  — in-memory, replaces the WP transient
# (set_transient('msme_erp_flash_<wp_user_id>', ..., 60))
# ============================================================
_FLASH_TTL_SECONDS = 60
_flash_store: dict[int, tuple[dict, float]] = {}


def set_flash(message: str, flash_type: str = "success", user_id: Optional[int] = None) -> None:
    if not message:
        return
    user_id = user_id if user_id is not None else ErpAuth.user_id()
    _flash_store[user_id] = ({"message": message, "type": flash_type}, time.time() + _FLASH_TTL_SECONDS)


def get_flash(user_id: Optional[int] = None) -> Optional[dict]:
    """Read and clear, like the PHP get_transient() + delete_transient() pair."""
    user_id = user_id if user_id is not None else ErpAuth.user_id()
    entry = _flash_store.pop(user_id, None)
    if entry is None:
        return None
    payload, expires_at = entry
    return payload if time.time() <= expires_at else None


# ============================================================
# DASHBOARD
# ============================================================
def dashboard_kpis(company_id: Optional[int] = None) -> dict[str, int]:
    company_id = company_id if company_id is not None else ErpAuth.company_id()
    return {
        "open_so": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM sales_orders WHERE company_id=? AND status IN('APPROVED','PROCESSING')",
            [company_id]) or 0,
        "open_po": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM purchase_orders WHERE company_id=? AND status IN('APPROVED','PARTIAL')",
            [company_id]) or 0,
        "wip_wo": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM work_orders WHERE company_id=? AND status IN('RELEASED','IN_PROGRESS')",
            [company_id]) or 0,
        "employees": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM employees WHERE company_id=? AND status='ACTIVE'", [company_id]) or 0,
        "open_inv": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM sales_invoices WHERE company_id=? AND status='UNPAID'", [company_id]) or 0,
        "suppliers": ErpDB.fetch_value(
            "SELECT COUNT(*) FROM suppliers WHERE company_id=? AND status='ACTIVE'", [company_id]) or 0,
    }


# ============================================================
# NAV TREE — module > group > sub-group > T-code, pruned to what the
# user can see. Same algorithm as the PHP $resolveGroup / $hasActive /
# $renderGroup closures, returned as plain nested data instead of HTML.
# ============================================================
def _resolve_group(grp: dict, by_code: dict, placed: set[str]) -> Optional[dict]:
    out = []
    for it in grp["items"]:
        if isinstance(it, str):
            if it in by_code:
                out.append(by_code[it])
                placed.add(it)
        else:
            sub = _resolve_group(it, by_code, placed)
            if sub:
                out.append(sub)
    return {"label": grp["label"], "icon": grp["icon"], "items": out} if out else None


def _group_has_active(grp: dict, active_tcode: str) -> bool:
    for it in grp["items"]:
        if "tcode" in it:
            if it["tcode"] == active_tcode:
                return True
        elif _group_has_active(it, active_tcode):
            return True
    return False


def _build_group_node(grp: dict, depth: int, path: str, active_tcode: str) -> dict:
    collapsed = not _group_has_active(grp, active_tcode)
    children = []
    for it in grp["items"]:
        if "tcode" in it:
            children.append({"type": "link", "depth": depth + 1, "tcode": it["tcode"],
                              "description": it["description"], "icon": it.get("icon", "bi-app"),
                              "active": it["tcode"] == active_tcode})
        else:
            children.append(_build_group_node(it, depth + 1, f"{path}/{it['label']}", active_tcode))
    return {"type": "group", "label": grp["label"], "icon": grp["icon"], "path": path,
            "depth": depth, "collapsed": collapsed, "children": children}


def build_nav_tree(nav_tcodes_by_module: dict[str, list[dict]], active_tcode: str,
                    nav_modules: dict = NAV_MODULES, nav_sub_groups: dict = NAV_SUB_GROUPS) -> list[dict]:
    """
    nav_tcodes_by_module: {module_code: [tcode rows the current user may see]},
    already filtered by caller (SUPER_ADMIN sees all; others by user['tcodes']).
    """
    tree = []
    for mod_code, mod in nav_modules.items():
        rows = nav_tcodes_by_module.get(mod_code)
        if not rows:
            continue
        by_code = {r["tcode"]: r for r in rows}
        placed: set[str] = set()

        module_open = active_tcode != "" and any(r["tcode"] == active_tcode for r in rows)
        children = []
        for sg in nav_sub_groups.get(mod_code, []):
            resolved = _resolve_group(sg, by_code, placed)
            if resolved:
                children.append(_build_group_node(resolved, 1, f"{mod_code}/{resolved['label']}", active_tcode))
        for r in rows:
            if r["tcode"] in placed:
                continue
            children.append({"type": "link", "depth": 1, "tcode": r["tcode"], "description": r["description"],
                              "icon": r.get("icon", "bi-app"), "active": r["tcode"] == active_tcode})

        tree.append({"module": mod_code, "label": mod["label"], "icon": mod["icon"], "color": mod["color"],
                     "open": module_open, "children": children})
    return tree


# ============================================================
# \ICONS — every active T-code as a clickable tile, grouped by module.
# ============================================================
def icons_grid_data(all_tcodes: list[dict], nav_modules: dict = NAV_MODULES) -> list[dict]:
    by_module: dict[str, list[dict]] = {}
    for tc in all_tcodes:
        by_module.setdefault(tc["module"], []).append(tc)

    out = []
    for mod_code, mod in nav_modules.items():
        rows = by_module.get(mod_code)
        if not rows:
            continue
        out.append({
            "module": mod_code, "label": mod["label"], "icon": mod["icon"], "color": mod["color"],
            "tcodes": [{"tcode": r["tcode"], "description": r["description"], "icon": r.get("icon") or "bi-app"}
                       for r in rows],
        })
    return out