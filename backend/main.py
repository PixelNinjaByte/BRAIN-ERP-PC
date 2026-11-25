"""
backend/main.py  (UPDATED)
--------------------------
FastAPI app for the Brain ERP desktop build (served on 127.0.0.1:8765 for the PyQt6 shell).

Registered routers
  auth, nav, mm (MM01), pr (ME51N/ME53N), po (ME21N/ME23N), supplier (XK01/XK02), rfq (ME41/ME47)
  + migo              (MIGO goods movement)                 <- added
  + supplier_invoice  (MR01 invoice verification, LT01 stock transfer, VK11 price list)  <- added

NOTE: I could not see your current main.py, so this is rebuilt from your description. Keep whatever
you already have for DB init / lifespan / static files / error handlers and just merge the two
`include_router` lines and their imports (marked "NEW").
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import (auth, mm, nav, po, pr, rfq, supplier)
from backend.routers import migo                 # NEW  - MIGO
from backend.routers import supplier_invoice     # NEW  - MR01 / LT01 / VK11
from backend.routers import purchase_return, service_entry
from backend.routers import reorder, rfq_compare
from backend.routers import material_groups, mm_reports, bid, mm_pending_report
from backend.routers import material_import



logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Brain ERP", version="1.0.0")


# The PyQt6 shell talks to localhost only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth")
app.include_router(nav.router, prefix="/api/nav")
app.include_router(mm.router, prefix="/api/mm")
app.include_router(pr.router, prefix="/api/pr")
app.include_router(po.router, prefix="/api/po")
app.include_router(supplier.router, prefix="/api/supplier")
app.include_router(rfq.router, prefix="/api/rfq")

sub_routers = (
    migo, supplier_invoice, reorder,
    purchase_return, service_entry, rfq_compare,
    material_groups, bid, mm_reports, mm_pending_report,
    material_import,
)
for _r in sub_routers:
    app.include_router(_r.router)


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok"}


def create_app() -> FastAPI:
    try:
        from core.installer import run_install, ensure_columns
        run_install("brain_erp.db")
        ensure_columns()
    except Exception as e:
        logging.getLogger("brain_erp.main").warning("DB auto-init check: %s", e)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")