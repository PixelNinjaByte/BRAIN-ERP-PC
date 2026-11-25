"""
Brain ERP — /api/nav
Dashboard KPIs, sidebar tree, T-code-bar resolve, the \\ICONS grid.

Everything here wraps core.app's pure-logic functions (see that file's
SCOPE NOTE) and adds the one thing core.app deliberately leaves to a
caller: which T-codes the CURRENT user may see. That's session-dependent,
so it belongs at the router layer, not in core.app itself.

Not wired to core.app.dispatch() yet: dispatch() resolves a typed T-code
to a registered Python *handler* (module_map), and module_map only makes
sense once the MM/SD/FI/... routers exist to register into it. Until
then, /resolve answers "which T-code (if any) matches what was typed" and
leaves navigating to it to the client — once a module router exists it
registers its T-codes into a shared module_map (see TODO below) and
/resolve switches to calling dispatch() directly.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query

from core.auth import ErpAuth
from core.db import ErpDB
from core import app as nav_logic  # core/app.py — named nav_logic here to avoid
                                    # clashing with the FastAPI `app` instance
from .deps import require_login, ok

router = APIRouter()

# Screens every logged-in user may reach regardless of their T-code grants
# (kept identical to ErpAuth.check_tcode's own whitelist).
_SELF_SERVICE_TCODES = {"MYACC", "SU3", "SU53", "SBWP", "SO01"}


# ---------------------------------------------------------------------------
# Visibility — which tcodes rows the current user is allowed to see at all
# ---------------------------------------------------------------------------
def _visible_tcodes(user: dict) -> list[dict]:
    if ErpAuth.is_super_admin():
        return ErpDB.fetch_all("SELECT * FROM tcodes WHERE is_active=1 ORDER BY sort_order")

    allowed = set(t.upper() for t in user.get("tcodes", [])) | _SELF_SERVICE_TCODES
    if not allowed:
        return []
    placeholders = ",".join("?" * len(allowed))
    return ErpDB.fetch_all(
        f"SELECT * FROM tcodes WHERE is_active=1 AND UPPER(tcode) IN ({placeholders}) ORDER BY sort_order",
        list(allowed),
    )


def _visible_by_module(user: dict) -> dict[str, list[dict]]:
    by_module: dict[str, list[dict]] = {}
    for row in _visible_tcodes(user):
        by_module.setdefault(row["module"], []).append(row)
    return by_module


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/modules")
def modules(_user=Depends(require_login)):
    """Module metadata (icon/label/color) for building the top-level sidebar
    groups — independent of which T-codes the user can see inside them."""
    return ok(nav_logic.NAV_MODULES)


@router.get("/tree")
def tree(active_tcode: str = "", user=Depends(require_login)):
    """Sidebar tree: module -> group -> sub-group -> T-code, pruned to what
    this user can see. Pass the currently-open T-code so the matching
    branch comes back expanded."""
    return ok(nav_logic.build_nav_tree(_visible_by_module(user), active_tcode.upper()))


@router.get("/icons")
def icons(user=Depends(require_login)):
    """The \\ICONS view: every T-code this user can see, grouped by module."""
    return ok(nav_logic.icons_grid_data(_visible_tcodes(user)))


@router.get("/dashboard")
def dashboard(user=Depends(require_login)):
    """KPI counters + quick-access tiles the user is actually allowed to open."""
    tiles = [
        {"tcode": tc, "label": label, "icon": icon, "color": color}
        for tc, label, icon, color in nav_logic.QUICK_ACCESS_TCODES
        if ErpAuth.check_tcode(tc)
    ]
    return ok({"kpis": nav_logic.dashboard_kpis(user["company_id"]), "quick_access": tiles})


@router.get("/resolve")
def resolve(q: str = Query(min_length=1), user=Depends(require_login)):
    """
    T-code bar: `q` is whatever the user typed, a literal T-code ("ME21N"),
    a bookmarked display code, or a plain-English action name ("create
    purchase order"). Returns one of:
      {"kind": "tcode",        "tcode": "...", "description": "..."}
      {"kind": "suggestions",  "suggestions": {...}}   — "did you mean"
      {"kind": "not_found",    "query": "..."}
    A T-code the user isn't allowed to open comes back as "not_found" with
    no description leaked, same as PHP's requireTcode() behaviour.
    """
    from core.helpers import tcode_from_display

    query = tcode_from_display(q.strip())
    row = ErpDB.fetch_one("SELECT * FROM tcodes WHERE tcode=? AND is_active=1", [query])

    typed_name = None
    if not row:
        matches = nav_logic.match_tcode_by_name(query)
        if len(matches) == 1:
            typed_name, row = query, matches[0]
            query = row["tcode"]
        elif len(matches) > 1:
            return ok({"kind": "suggestions",
                       "suggestions": nav_logic.tcode_suggestions(query, matches, True)})

    if row:
        if not ErpAuth.check_tcode(row["tcode"]):
            return ok({"kind": "not_found", "query": q})
        hint = f'Matched "{typed_name}" to {row["tcode"]} — {row["description"]}' if typed_name else ""
        return ok({"kind": "tcode", "tcode": row["tcode"], "description": row["description"], "hint": hint})

    loose = nav_logic.match_tcode_by_name(query, 0.3)
    if loose:
        return ok({"kind": "suggestions", "suggestions": nav_logic.tcode_suggestions(query, loose, False)})
    return ok({"kind": "not_found", "query": q})


@router.get("/flash")
def flash(user=Depends(require_login)):
    """One-shot message left by a previous action (e.g. after a redirect-style
    flow). Returns null once already read — same semantics as the old
    WP transient: read it once and it's gone."""
    return ok(nav_logic.get_flash(user["user_id"]))