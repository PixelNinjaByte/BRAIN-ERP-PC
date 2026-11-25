"""
Brain ERP — HTTP client the PyQt6 screens use to talk to the FastAPI backend.

Provides typed convenience methods for all backend modules:
  - Authentication & Navigation
  - Materials Management (MM01, MM02, MM03)
  - Goods Movements (MIGO)
  - Purchase Orders (ME21N, ME23N)
  - Purchase Requisitions (ME51N, ME53N)
  - Supplier Invoices & Pricing (MR01, LT01, VK11)
  - Suppliers Master (XK01, XK02)
  - Purchase Returns (PRTN)
  - RFQs & Comparison (ME41, ME47)
  - Service Entry Sheets (ML81N)
  - Material Groups (OMSF)
  - Planning & Reports (ZMRP, ME2PN, ZMMR)
  - Bids & Tenders (BID01, BID02)
  - Material CSV Import/Export (ZMMI)
"""

from typing import Any, Callable, Optional
import httpx


class ApiError(Exception):
    """Raised for a network failure, a non-2xx response, or success:false."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# A 401 on these means "wrong credentials" / "already logged out", not "session expired".
_AUTH_PATHS = ("/api/auth/login", "/api/auth/logout")


def _error_message(body: Any, status: int) -> str:
    """Turn an error body into a readable string.

    FastAPI returns `detail` as a *list of dicts* for 422 validation errors; passing
    that straight to ApiError showed users a raw Python list.
    """
    fallback = f"Request failed ({status})."
    if not isinstance(body, dict):
        return fallback
    detail = body.get("detail") or body.get("error")
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, list):
        parts = []
        for d in detail:
            if isinstance(d, dict):
                loc = ".".join(str(x) for x in d.get("loc", []) if x not in ("body", "query"))
                msg = d.get("msg", "Invalid value")
                parts.append(f"{loc}: {msg}" if loc else str(msg))
            else:
                parts.append(str(d))
        if parts:
            return "; ".join(parts)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return fallback


def _data(res: dict) -> Any:
    """Return res['data'], or raise ApiError (never KeyError) if it is missing."""
    if isinstance(res, dict) and "data" in res:
        return res["data"]
    raise ApiError("Unexpected response from the server (no 'data' field).")


def _extract_list(res: Any) -> list:
    """Normalize any backend response into a list of record dicts.

    Handles:
      - raw list: [ {...}, ... ]
      - ok envelope: {"data": [ {...}, ... ]}
      - ErpDB.paginate envelope: {"data": {"data": [ {...}, ... ], "total_rows": ...}}
      - dictionary with rows/items: {"data": {"rows": [ {...}, ... ]}} or {"rows": [ {...}, ... ]}
    """
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        d = res.get("data", res)
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for k in ("data", "rows", "items"):
                if k in d and isinstance(d[k], list):
                    return d[k]
    return []


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self._base = base_url.rstrip("/")
        # follow_redirects: FastAPI answers 307 when a route's trailing slash differs
        # ("/api/bid" vs "/api/bid/"). Without this the 307 looked like a success
        # with an empty body and screens silently showed nothing.
        self._client = httpx.Client(base_url=self._base, timeout=timeout, follow_redirects=True)
        # Set by AppController: called when an authenticated call gets 401 (session expired).
        self.on_unauthorized: Optional[Callable[[], None]] = None

    def close(self) -> None:
        self._client.close()

    def clear_session(self) -> None:
        """Forget the session cookie (used on logout / expiry so it can't leak into the next login)."""
        self._client.cookies.clear()

    @property
    def base_url(self) -> str:
        return self._base

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            r = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ApiError(f"Could not reach the Brain ERP server: {e}") from e

        try:
            body = r.json()
        except ValueError:
            body = {}

        if r.status_code >= 400:
            if r.status_code == 401 and self.on_unauthorized and path not in _AUTH_PATHS:
                self.on_unauthorized()
            raise ApiError(_error_message(body, r.status_code), r.status_code)
        if isinstance(body, dict) and body.get("success") is False:
            raise ApiError(body.get("error") or "Request failed.", r.status_code)
        return body if isinstance(body, dict) else {"success": True, "data": body}

    # ------------------------------------------------------------------ auth & nav
    def login(self, username: str, password: str) -> dict:
        return _data(self._request("POST", "/api/auth/login", json={"username": username, "password": password}))

    def logout(self) -> None:
        self._request("POST", "/api/auth/logout", timeout=3.0)  # short: runs while closing the window

    def me(self) -> dict:
        return _data(self._request("GET", "/api/auth/me"))

    def ping(self) -> bool:
        """Cheap authenticated liveness check with a short timeout (raises ApiError)."""
        self._request("GET", "/api/auth/me", timeout=3.0)
        return True

    def switch_company(self, company_id: int) -> dict:
        return _data(self._request("POST", "/api/auth/switch-company", json={"company_id": company_id}))

    def nav_dashboard(self) -> dict:
        res = self._request("GET", "/api/nav/dashboard")
        return res.get("data", res)

    def nav_modules(self) -> list:
        res = self._request("GET", "/api/nav/modules")
        return res.get("data", res) if isinstance(res, dict) else res

    # ------------------------------------------------------------------ MM01/02/03
    def mm_lookups(self) -> dict:
        return _data(self._request("GET", "/api/mm/lookups"))

    def mm_list(self, search: str = "", cat: str = "", type_: str = "", st: str = "active") -> list:
        params = {k: v for k, v in {
            "search": search or None,
            "category": cat or None,
            "item_type": type_ or None,
            "status": st or "active",
        }.items() if v is not None}
        return _extract_list(self._request("GET", "/api/mm/materials", params=params))

    def mm_get(self, item_id: int) -> dict:
        return _data(self._request("GET", f"/api/mm/materials/{item_id}"))

    def mm_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/mm/materials", json=payload)

    def mm_update(self, item_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/api/mm/materials/{item_id}", json=payload)

    def mm_delete(self, item_id: int) -> dict:
        return self._request("DELETE", f"/api/mm/materials/{item_id}")

    def mm_add_uom(self, item_id: int, payload: dict) -> dict:
        return self._request("POST", f"/api/mm/materials/{item_id}/uom", json=payload)

    def mm_delete_uom(self, item_id: int, conv_id: int) -> dict:
        return self._request("DELETE", f"/api/mm/materials/{item_id}/uom/{conv_id}")

    # ------------------------------------------------------------------ MIGO
    def migo_meta(self) -> dict:
        res = self._request("GET", "/api/migo/meta")
        return res.get("data", res)

    def migo_po_lines(self, po_id: int) -> dict:
        res = self._request("GET", f"/api/migo/po/{po_id}")
        return res.get("data", res)

    def migo_post(self, payload: dict) -> dict:
        return self._request("POST", "/api/migo/post", json=payload)

    def migo_get_doc(self, grn_id: int) -> dict:
        res = self._request("GET", f"/api/migo/grn/{grn_id}")
        return res.get("data", res)

    # ------------------------------------------------------------------ PO (ME21N / ME23N)
    def po_lookups(self) -> dict:
        res = self._request("GET", "/api/po/lookups")
        return res.get("data", res)

    def po_list(self, status: str = "", search: str = "") -> list:
        params = {}
        if status:
            params["status"] = status
        if search:
            params["search"] = search
        return _extract_list(self._request("GET", "/api/po/", params=params))

    def po_get(self, po_id: int) -> dict:
        res = self._request("GET", f"/api/po/{po_id}")
        return res.get("data", res)

    def po_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/po/", json=payload)

    def po_release(self, po_id: int) -> dict:
        return self._request("POST", f"/api/po/{po_id}/release")

    # ------------------------------------------------------------------ PR (ME51N / ME53N)
    def pr_lookups(self) -> dict:
        res = self._request("GET", "/api/pr/lookups")
        return res.get("data", res)

    def pr_list(self, status: str = "") -> list:
        params = {"status": status} if status else {}
        return _extract_list(self._request("GET", "/api/pr/", params=params))

    def pr_get(self, pr_id: int) -> dict:
        res = self._request("GET", f"/api/pr/{pr_id}")
        return res.get("data", res)

    def pr_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/pr/", json=payload)

    def pr_action(self, pr_id: int, action: str, reason: str = "") -> dict:
        return self._request("POST", f"/api/pr/{pr_id}/action", json={"action": action, "reason": reason})

    # ------------------------------------------------------------------ Suppliers (XK01 / XK02)
    def supplier_lookups(self) -> dict:
        res = self._request("GET", "/api/supplier/lookups")
        return res.get("data", res)

    def supplier_list(self, search: str = "") -> list:
        params = {"q": search} if search else {}
        return _extract_list(self._request("GET", "/api/supplier/", params=params))

    def supplier_get(self, sup_id: int) -> dict:
        res = self._request("GET", f"/api/supplier/{sup_id}")
        return res.get("data", res)

    def supplier_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/supplier/", json=payload)

    def supplier_update(self, sup_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/api/supplier/{sup_id}", json=payload)

    def supplier_toggle(self, sup_id: int) -> dict:
        return self._request("POST", f"/api/supplier/{sup_id}/toggle")

    # ------------------------------------------------------------------ Invoices (MR01 / LT01 / VK11)
    def mr01_meta(self, po_id: Optional[int] = None) -> dict:
        params = {"po_id": po_id} if po_id else {}
        res = self._request("GET", "/api/mr01/meta", params=params)
        return res.get("data", res)

    def mr01_post(self, payload: dict) -> dict:
        return self._request("POST", "/api/mr01/post", json=payload)

    def lt01_meta(self) -> dict:
        res = self._request("GET", "/api/lt01/meta")
        return res.get("data", res)

    def lt01_stock(self, item_id: int, warehouse_id: int) -> dict:
        res = self._request("GET", "/api/lt01/stock", params={"item_id": item_id, "warehouse_id": warehouse_id})
        return res.get("data", res)

    def lt01_post(self, payload: dict) -> dict:
        return self._request("POST", "/api/lt01/post", json=payload)

    def vk11_meta(self) -> dict:
        res = self._request("GET", "/api/vk11/meta")
        return res.get("data", res)

    def vk11_save(self, payload: dict) -> dict:
        return self._request("POST", "/api/vk11/save", json=payload)

    # ------------------------------------------------------------------ Purchase Returns (PRTN)
    def purchase_return_reasons(self) -> dict:
        res = self._request("GET", "/api/purchase-return/reasons")
        return res.get("data", res)

    def purchase_return_open_grns(self) -> list:
        return _extract_list(self._request("GET", "/api/purchase-return/open-grns"))

    def purchase_return_grn(self, grn_id: int) -> dict:
        res = self._request("GET", f"/api/purchase-return/grn/{grn_id}")
        return res.get("data", res)

    def purchase_return_list(self) -> list:
        return _extract_list(self._request("GET", "/api/purchase-return/"))

    def purchase_return_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/purchase-return/", json=payload)

    def purchase_return_get(self, return_id: int) -> dict:
        res = self._request("GET", f"/api/purchase-return/{return_id}")
        return res.get("data", res)

    # ------------------------------------------------------------------ RFQ & Compare (ME41 / ME47)
    def rfq_lookups(self) -> dict:
        res = self._request("GET", "/api/rfq/lookups")
        return res.get("data", res)

    def rfq_list(self) -> list:
        return _extract_list(self._request("GET", "/api/rfq/"))

    def rfq_get(self, rfq_id: int) -> dict:
        res = self._request("GET", f"/api/rfq/{rfq_id}")
        return res.get("data", res)

    def rfq_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/rfq/", json=payload)

    def rfq_compare(self, rfq_id: int) -> dict:
        res = self._request("GET", f"/api/rfq-compare/{rfq_id}")
        return res.get("data", res)

    def rfq_award(self, rfq_id: int, payload: dict) -> dict:
        return self._request("POST", f"/api/rfq-compare/{rfq_id}/award", json=payload)

    # ------------------------------------------------------------------ Service Entry (ML81N)
    def ses_lookups(self) -> dict:
        res = self._request("GET", "/api/service-entry/lookups")
        return res.get("data", res)

    def ses_list(self) -> list:
        return _extract_list(self._request("GET", "/api/service-entry/"))

    def ses_po_lines(self, po_id: int) -> list:
        return _extract_list(self._request("GET", f"/api/service-entry/po/{po_id}/lines"))

    def ses_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/service-entry/", json=payload)

    def ses_get(self, ses_id: int) -> dict:
        res = self._request("GET", f"/api/service-entry/{ses_id}")
        return res.get("data", res)

    def ses_accept(self, ses_id: int) -> dict:
        return self._request("POST", f"/api/service-entry/{ses_id}/accept")

    def ses_reject(self, ses_id: int, reason: str) -> dict:
        return self._request("POST", f"/api/service-entry/{ses_id}/reject", json={"reason": reason})

    # ------------------------------------------------------------------ Material Groups (OMSF)
    def mat_groups_lookups(self) -> dict:
        res = self._request("GET", "/api/material-groups/lookups")
        return res.get("data", res)

    def mat_groups_list(self) -> list:
        return _extract_list(self._request("GET", "/api/material-groups/"))

    def mat_group_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/material-groups/", json=payload)

    def mat_group_update(self, group_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/api/material-groups/{group_id}", json=payload)

    # ------------------------------------------------------------------ Reports & Planning (ZMRP / ME2PN / ZMMR)
    def reorder_report(self) -> list:
        return _extract_list(self._request("GET", "/api/reorder/"))

    def reorder_create_pr(self, items: list[dict]) -> dict:
        return self._request("POST", "/api/reorder/create-pr", json={"items": items})

    def pending_report(self) -> dict:
        res = self._request("GET", "/api/mm-pending-report/summary")
        return res.get("data", res)

    def mm_reports_summary(self) -> dict:
        res = self._request("GET", "/api/mm-reports/summary")
        return res.get("data", res)

    # ------------------------------------------------------------------ Bids & Tenders (BID01 / BID02)
    def bid_lookups(self) -> dict:
        res = self._request("GET", "/api/bid/lookups")
        return res.get("data", res)

    def bid_list(self) -> list:
        return _extract_list(self._request("GET", "/api/bid"))

    def bid_get(self, bid_id: int) -> dict:
        res = self._request("GET", f"/api/bid/{bid_id}")
        return res.get("data", res)

    def bid_create(self, payload: dict) -> dict:
        return self._request("POST", "/api/bid", json=payload)

    def bid_update_status(self, bid_id: int, status: str) -> dict:
        return self._request("POST", f"/api/bid/{bid_id}/status", json={"status": status})

    # ------------------------------------------------------------------ Material CSV Import/Export (ZMMI)
    def material_import_preview(self, csv_content: str, update_existing: bool = False) -> dict:
        return self._request(
            "POST",
            "/api/mm/import/preview",
            json={"csv_content": csv_content, "update_existing": update_existing},
        )

    def material_import_confirm(self, token: str) -> dict:
        return self._request("POST", "/api/mm/import/confirm", json={"token": token})