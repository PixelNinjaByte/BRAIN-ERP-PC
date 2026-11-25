"""
Brain ERP — Shared router dependencies.

Every module router (mm.py, sd.py, finance.py, ...) imports from here instead
of touching core.auth.ErpAuth directly in each endpoint, so the login/T-code
checks, error shape, and response envelope stay identical across modules.

Single-user model: ErpAuth holds one process-global session (there is
exactly one logged-in user in a desktop app), so these are plain functions,
not per-request token decoding — but they're still ordinary FastAPI
dependencies so endpoints declare what they need and FastAPI enforces it
before the handler body runs.
"""

from typing import Callable
from fastapi import Depends, HTTPException

from core.auth import ErpAuth


# ---------------------------------------------------------------------------
# Login guard — use on every endpoint except auth.login and the public
# documents.* routes.
# ---------------------------------------------------------------------------
def require_login() -> dict:
    if not ErpAuth.check():
        raise HTTPException(status_code=401, detail="Not logged in.")
    return ErpAuth.user()


# ---------------------------------------------------------------------------
# T-code guard — use when an endpoint maps to one screen/T-code, e.g.
#     @router.post("/materials", dependencies=[Depends(require_tcode("MM01"))])
# Keeps the same permission model as the T-code bar / sidebar (core.app),
# just enforced at the API boundary instead of (only) in the UI.
# ---------------------------------------------------------------------------
def require_tcode(tcode: str) -> Callable[[], dict]:
    def _check() -> dict:
        user = require_login()
        if not ErpAuth.check_tcode(tcode):
            raise HTTPException(status_code=403, detail=f"Not authorized for {tcode}.")
        return user
    return _check


def require_role(*roles: str) -> Callable[[], dict]:
    def _check() -> dict:
        user = require_login()
        if not ErpAuth.has_role(list(roles)):
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}.")
        return user
    return _check


# ---------------------------------------------------------------------------
# Response envelope — matches the shape the old WP AJAX endpoints used
# (wp_send_json_success / wp_send_json_error), so the PyQt6 httpx client
# code already written against that shape keeps working unchanged.
# ---------------------------------------------------------------------------
def ok(data=None, message: str = "") -> dict:
    out = {"success": True}
    if data is not None:
        out["data"] = data
    if message:
        out["message"] = message
    return out


def fail(message: str, status_code: int = 400):
    raise HTTPException(status_code=status_code, detail=message)