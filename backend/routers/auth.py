"""
Brain ERP — /api/auth
Login, logout, current session, company switch, CSRF token issue.

No JWT/cookies: ErpAuth keeps one process-global session (see core/auth.py's
docstring on why that's correct for a single-user desktop app). The PyQt6
client just needs to know *that* it's logged in and *what* the session
contains; it doesn't carry or refresh a token.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth import ErpAuth
from core.db import ErpDB
from .deps import require_login, ok, fail

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SwitchCompanyRequest(BaseModel):
    company_id: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/login")
def login(body: LoginRequest):
    success, error = ErpAuth.login(body.username, body.password)
    if not success:
        fail(error, status_code=401)
    return ok(_session_payload(), message="Logged in.")


@router.post("/logout")
def logout(_user=Depends(require_login)):
    ErpAuth.logout()
    return ok(message="Logged out.")


@router.get("/me")
def me(_user=Depends(require_login)):
    """Current session — call on app start to restore the logged-in state
    (e.g. after a PyQt6 relaunch while the FastAPI process is still up)."""
    return ok(_session_payload())


@router.post("/switch-company")
def switch_company(body: SwitchCompanyRequest, _user=Depends(require_login)):
    if not ErpDB.can_switch_company():
        fail("Your account is not permitted to switch companies.", status_code=403)
    if not ErpAuth.switch_company(body.company_id):
        fail("That company is not available to you.", status_code=403)
    return ok(_session_payload(), message="Company switched.")


@router.get("/csrf")
def csrf(_user=Depends(require_login)):
    """Issue a one-time CSRF token for a state-changing form that specifically
    asks for one (mirrors the old wp_create_nonce() calls). Most endpoints
    don't need this — FastAPI + localhost-only CORS is the real boundary;
    this exists for parity with flows ported from PHP that already expect it
    (e.g. core.documents' email-send form)."""
    return ok({"token": ErpAuth.csrf_token()})


# ---------------------------------------------------------------------------
def _session_payload() -> dict:
    user = ErpAuth.user()
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "email": user["email"],
        "company_id": user["company_id"],
        "branch_id": user["branch_id"],
        "roles": user["roles"],
        "tcodes": user["tcodes"],
        "theme": user["theme"],
        "is_super_admin": ErpAuth.is_super_admin(),
    }