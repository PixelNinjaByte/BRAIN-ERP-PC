"""
Brain ERP — Authentication & Authorization Layer
Direct port of PHP ErpAuth class (core/auth.php) → Python.

Replaces:
  is_user_logged_in()     → session dict check
  wp_create_nonce()       → secrets.token_hex()
  get_user_meta()         → SQLite query on users table
  current_user_can()      → role check in ERP roles table
  wp_options (CSRF store) → SQLite erp_csrf_tokens table

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import secrets
import hashlib
import logging
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session — set at login, cleared at logout.
# In a desktop app there is exactly one user at a time, so a module-level
# dict is the right tool (no Flask session, no JWT needed).
# ---------------------------------------------------------------------------
_session: dict = {
    "logged_in": False,
    "user_id": 0,          # ERP users.id
    "wp_user_id": 0,       # kept for data-compatibility (same column name)
    "username": "",
    "full_name": "",
    "email": "",
    "company_id": 1,
    "branch_id": 1,
    "roles": [],           # list of role codes e.g. ['SUPER_ADMIN']
    "tcodes": [],          # list of T-code strings e.g. ['MM01', 'ME21N']
    "theme": "light",
}

# ---------------------------------------------------------------------------
# Global Super Admin e-mails — mirrors $superAdminEmails in PHP.
# In standalone mode there's no network, so we keep it as a local setting.
# ---------------------------------------------------------------------------
SUPER_ADMIN_EMAILS: list[str] = []


class ErpAuth:
    """
    Standalone authentication and authorization.
    API mirrors PHP ErpAuth exactly so all module code ports 1:1.
    """

    # -----------------------------------------------------------------------
    # LOGIN / LOGOUT
    # -----------------------------------------------------------------------
    @classmethod
    def login(cls, username: str, password: str) -> tuple[bool, str]:
        """
        Verify credentials against the ERP users + user_passwords table.
        Returns (success, error_message).
        """
        from core.db import ErpDB
        row = ErpDB.fetch_one(
            "SELECT u.*, up.password_hash FROM users u "
            "LEFT JOIN user_passwords up ON up.user_id=u.id "
            "WHERE u.username=? AND u.is_active=1 AND COALESCE(u.is_deleted,0)=0",
            (username,),
        )
        if not row:
            return False, "Invalid username or password."

        stored = row.get("password_hash") or ""
        if not stored:
            return False, "No password set for this account. Contact your administrator."

        ok = False
        if stored.startswith("pbkdf2:"):
            try:
                import hashlib, hmac
                parts = stored.split("$")
                if len(parts) == 3:
                    header, salt, expected_hash = parts
                    _, algo, iters = header.split(":")
                    calc = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iters)).hex()
                    ok = hmac.compare_digest(calc, expected_hash)
            except Exception:
                ok = False
        else:
            try:
                import bcrypt
                ok = bcrypt.checkpw(password.encode(), stored.encode())
            except Exception:
                ok = False
        if not ok:
            return False, "Invalid username or password."

        roles = cls._get_user_roles(int(row["id"]))
        tcodes = cls._get_user_tcodes(int(row["id"]), roles)

        _session.update({
            "logged_in": True,
            "user_id": int(row["id"]),
            "wp_user_id": int(row.get("wp_user_id") or row["id"]),
            "username": row["username"],
            "full_name": row.get("full_name") or "",
            "email": row.get("email") or "",
            "company_id": int(row.get("company_id") or 1),
            "branch_id": int(row.get("branch_id") or 1),
            "roles": roles,
            "tcodes": tcodes,
            "theme": "light",
        })
        # Keep ErpDB in sync with current user
        from core.db import ErpDB
        ErpDB._current_user_id = _session["user_id"]
        ErpDB._current_username = _session["username"]
        ErpDB.use_company(_session["company_id"])

        logger.info("Login: %s (roles: %s)", username, roles)
        return True, ""

    @classmethod
    def logout(cls) -> None:
        _session.update({
            "logged_in": False, "user_id": 0, "wp_user_id": 0,
            "username": "", "full_name": "", "email": "",
            "company_id": 1, "branch_id": 1,
            "roles": [], "tcodes": [], "theme": "light",
        })
        from core.db import ErpDB
        ErpDB._current_user_id = 0
        ErpDB._current_username = ""

    # -----------------------------------------------------------------------
    # CHECK LOGIN
    # -----------------------------------------------------------------------
    @classmethod
    def check(cls) -> bool:
        return bool(_session.get("logged_in"))

    @classmethod
    def is_logged_in(cls) -> bool:
        return cls.check()

    @classmethod
    def is_super_admin(cls) -> bool:
        return cls.is_global_super_admin() or cls.has_role("SUPER_ADMIN")

    @classmethod
    def require_login(cls) -> None:
        if not cls.check():
            raise PermissionError("Not logged in.")

    # -----------------------------------------------------------------------
    # USER INFO ACCESSORS  — mirrors PHP static helpers
    # -----------------------------------------------------------------------
    @classmethod
    def user(cls) -> dict:
        return dict(_session)

    @classmethod
    def user_id(cls) -> int:
        return int(_session.get("user_id") or 0)

    @classmethod
    def company_id(cls) -> int:
        return int(_session.get("company_id") or 1)

    @classmethod
    def branch_id(cls) -> int:
        return int(_session.get("branch_id") or 1)

    @classmethod
    def get_erp_user_id(cls, wp_user_id: int) -> int:
        """Compatibility shim — in standalone, ERP user id IS the wp_user_id."""
        return wp_user_id

    # -----------------------------------------------------------------------
    # T-CODE AUTHORIZATION  — mirrors checkTcode() / requireTcode()
    # -----------------------------------------------------------------------
    @classmethod
    def check_tcode(cls, tcode: str) -> bool:
        if not cls.check():
            return False
        if "SUPER_ADMIN" in _session.get("roles", []):
            return True
        # Self-service screens every ERP user can reach (same whitelist as PHP)
        if tcode.upper() in {"MYACC", "SU3", "SU53", "SBWP", "SO01"}:
            return True
        return tcode.upper() in [t.upper() for t in _session.get("tcodes", [])]

    @classmethod
    def require_tcode(cls, tcode: str) -> None:
        cls.require_login()
        if not cls.check_tcode(tcode):
            raise PermissionError(f"Not authorized for T-Code {tcode}")

    # -----------------------------------------------------------------------
    # ROLE CHECK  — mirrors hasRole()
    # -----------------------------------------------------------------------
    @classmethod
    def has_role(cls, roles) -> bool:
        if isinstance(roles, str):
            roles = [roles]
        user_roles = _session.get("roles", [])
        if "SUPER_ADMIN" in user_roles:
            return True
        return bool(set(roles) & set(user_roles))

    # -----------------------------------------------------------------------
    # GLOBAL SUPER ADMIN
    # -----------------------------------------------------------------------
    @classmethod
    def is_global_super_admin(cls, user_id: int = 0) -> bool:
        from core.db import ErpDB
        uid = user_id or cls.user_id()
        if not uid:
            return False
        row = ErpDB.fetch_one("SELECT email FROM users WHERE id=?", (uid,))
        if not row:
            return False
        email = str(row.get("email") or "").lower()
        return email in [e.lower() for e in SUPER_ADMIN_EMAILS]

    # -----------------------------------------------------------------------
    # CSRF TOKEN — mirrors csrfToken() / validateCsrf()
    # Stored in SQLite table erp_csrf_tokens (created by schema.sql).
    # -----------------------------------------------------------------------
    @classmethod
    def csrf_token(cls) -> str:
        token = secrets.token_hex(16)
        from core.db import ErpDB
        try:
            ErpDB.insert("erp_csrf_tokens", {
                "token": token,
                "user_id": cls.user_id(),
                "created_at": datetime.now().isoformat(),
                "used": 0,
            })
        except Exception:
            pass  # table may not exist yet; degrade gracefully
        return token

    @classmethod
    def validate_csrf(cls, token: Optional[str]) -> bool:
        if not token:
            return False
        from core.db import ErpDB
        try:
            row = ErpDB.fetch_one(
                "SELECT id, used, created_at FROM erp_csrf_tokens "
                "WHERE token=? AND user_id=?",
                (token, cls.user_id()),
            )
            if not row:
                return False
            if int(row.get("used") or 0):
                return False  # single-use: already claimed
            # Expire tokens older than 24 hours
            created = datetime.fromisoformat(str(row["created_at"]))
            if (datetime.now() - created) > timedelta(hours=24):
                return False
            # Claim it
            ErpDB.update("erp_csrf_tokens", {"used": 1}, "id=?", (row["id"],))
            return True
        except Exception:
            return True  # degrade gracefully if table missing

    # -----------------------------------------------------------------------
    # RELEASING AUTHORITY  — mirrors canReleaseDocument()
    # -----------------------------------------------------------------------
    @classmethod
    def can_release_document(cls, release_id: int) -> bool:
        if cls.has_role("SUPER_ADMIN"):
            return True
        from core.db import ErpDB
        user_id = cls.user_id()
        rel = ErpDB.fetch_one(
            "SELECT strategy_id, current_level FROM document_releases WHERE id=? AND status='PENDING'",
            (release_id,),
        )
        if not rel:
            return False
        role_req = ErpDB.fetch_value(
            "SELECT role_id FROM release_levels WHERE strategy_id=? AND level_num=?",
            (rel["strategy_id"], rel["current_level"]),
        )
        if not role_req:
            return False
        return bool(ErpDB.fetch_value(
            "SELECT COUNT(*) FROM user_roles WHERE user_id=? AND role_id=? "
            "AND (valid_to IS NULL OR valid_to >= date('now'))",
            (user_id, int(role_req)),
        ))

    # -----------------------------------------------------------------------
    # SET / CHANGE PASSWORD
    # -----------------------------------------------------------------------
    @classmethod
    def set_password(cls, user_id: int, new_password: str) -> None:
        """Hash and store a new password for an ERP user."""
        try:
            import bcrypt
            hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(rounds=10)).decode()
        except ImportError:
            import hashlib, secrets
            salt = secrets.token_hex(16)
            h = hashlib.pbkdf2_hmac("sha256", new_password.encode(), salt.encode(), 100000).hex()
            hashed = f"pbkdf2:sha256:100000${salt}${h}"
        from core.db import ErpDB
        existing = ErpDB.fetch_one("SELECT id FROM user_passwords WHERE user_id=?", (user_id,))
        if existing:
            ErpDB.update("user_passwords", {"password_hash": hashed}, "user_id=?", (user_id,))
        else:
            ErpDB.insert("user_passwords", {"user_id": user_id, "password_hash": hashed})

    # -----------------------------------------------------------------------
    # AUDIT SHORTCUT  — mirrors PHP ErpAuth::audit()
    # -----------------------------------------------------------------------
    @classmethod
    def audit(
        cls, table: str, record_id: int, action: str,
        old: dict = None, new: dict = None, tcode: str = "",
    ) -> None:
        from core.db import ErpDB
        ErpDB.audit(table, record_id, action, old, new, cls.user_id(), tcode)

    # -----------------------------------------------------------------------
    # INTERNAL HELPERS
    # -----------------------------------------------------------------------
    @classmethod
    def _get_user_roles(cls, erp_user_id: int) -> list[str]:
        from core.db import ErpDB
        rows = ErpDB.fetch_all(
            """SELECT r.code FROM user_roles ur
               JOIN roles r ON r.id=ur.role_id
               WHERE ur.user_id=?
                 AND (ur.valid_to IS NULL OR ur.valid_to >= date('now'))
                 AND COALESCE(r.is_deleted,0)=0""",
            (erp_user_id,),
        )
        return [row["code"] for row in rows]

    @classmethod
    def _get_user_tcodes(cls, erp_user_id: int, roles: list[str]) -> list[str]:
        from core.db import ErpDB
        if "SUPER_ADMIN" in roles:
            rows = ErpDB.fetch_all("SELECT tcode FROM tcodes WHERE is_active=1")
            return [r["tcode"] for r in rows]
        if not erp_user_id:
            return []
        rows = ErpDB.fetch_all(
            """SELECT DISTINCT t.tcode FROM user_roles ur
               JOIN role_tcodes rt ON rt.role_id=ur.role_id
               JOIN tcodes t ON t.id=rt.tcode_id AND t.is_active=1
               WHERE ur.user_id=?
                 AND (ur.valid_to IS NULL OR ur.valid_to >= date('now'))""",
            (erp_user_id,),
        )
        codes = [r["tcode"] for r in rows]
        # Individual grants
        grants = ErpDB.fetch_all(
            """SELECT DISTINCT t.tcode FROM user_tcode_grants g
               JOIN tcodes t ON t.tcode=g.tcode AND t.is_active=1
               WHERE g.user_id=?
                 AND (g.valid_to IS NULL OR g.valid_to >= date('now'))""",
            (erp_user_id,),
        )
        codes += [r["tcode"] for r in grants]
        return list(set(codes))

    # -----------------------------------------------------------------------
    # COMPANY SWITCH
    # -----------------------------------------------------------------------
    @classmethod
    def switch_company(cls, company_id: int) -> bool:
        """Switch the active company for the current session."""
        from core.db import ErpDB
        row = ErpDB.fetch_one(
            "SELECT id, name FROM companies WHERE id=? AND is_active=1", (company_id,)
        )
        if not row:
            return False
        _session["company_id"] = company_id
        ErpDB.use_company(company_id)
        logger.info("Switched to company: %s (#%d)", row["name"], company_id)
        return True


# Convenience alias — mirrors PHP "class_alias('ErpAuth', 'Auth')"
Auth = ErpAuth