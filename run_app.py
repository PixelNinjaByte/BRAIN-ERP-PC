"""
Brain ERP — Desktop launcher.

This is the one file to run (`python run_app.py`). It starts the FastAPI
backend in a background thread and then shows the PyQt6 login screen in
the main thread.

Same process, not a subprocess, on purpose: this project is already set
up to be packaged with PyInstaller (see backend/requirements.txt), and a
single-process desktop app packages as one .exe — a subprocess backend
would need its own frozen entry point and a way to locate it at runtime.
uvicorn.Server (unlike the uvicorn.run() shortcut) is safe to call from a
background thread: Server.install_signal_handlers() no-ops when it isn't
running on the main thread, so it never tries to register Ctrl+C handlers
off-thread.

Two import roots are needed on sys.path, matching how the existing code
already imports itself:
  - PROJECT ROOT, for `backend.main` / `backend.routers.*` (main.py does
    `from backend.routers import auth, nav, mm`).
  - PROJECT ROOT / backend, for `core.*` and the routers' own `from
    core.db import ErpDB` / `from .deps import ...` — those resolve `core`
    and `routers` as top-level packages rooted at backend/.
"""

import os
import sys
import time
import logging
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT, "backend")

sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND_DIR)

HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"

logger = logging.getLogger("brain_erp.launcher")


def _start_backend() -> None:
    import uvicorn

    # brain_erp.db / schema.sql are read by core/db.py and installer.py relative to
    # the backend/ directory (ErpDB's default db_path is the bare filename).
    os.chdir(BACKEND_DIR)
    from backend.main import create_app

    config = uvicorn.Config(create_app(), host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def _wait_until_ready(timeout: float = 15.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    threading.Thread(target=_start_backend, daemon=True, name="brain-erp-backend").start()

    if not _wait_until_ready():
        print(f"Could not reach the Brain ERP backend at {BASE_URL}.\n"
              f"Check the console above for a startup error (e.g. a missing dependency "
              f"or schema.sql problem) and try again.")
        return 1

    from frontend.app_controller import run_frontend
    return run_frontend(BASE_URL)


if __name__ == "__main__":
    sys.exit(main())
