# Brain ERP — Desktop App

FastAPI backend + PyQt6 desktop frontend, SQLite storage. Ported from the `msme-erp` WordPress plugin.

## Overview & Current Scope

Brain ERP provides a desktop interface for MSME business management.

- **Authentication**: Sign-in via `/api/auth/*`
- **Dashboard (Home)**: Overview screen displaying all ERP modules as responsive cards/tiles with status badges and KPI metrics
- **MM01 — Create Material**: Improved sectioned form UI with real-time feedback, auto-numbering, duplicate detection confirmation, and lookups
- **Materials List (MM03 preview)**: Read-only live table of materials created so far (`/api/mm/materials`)
- **T-Code & Sidebar Navigation**: T-Code entry bar (`MM01`, `HOME`, etc.) and sidebar navigation routing active screens, with "Coming Soon" placeholders for roadmap modules.

All backend business logic in `core/` (finance, materials, returns, reports, aura AI gateway, etc.) is ported and unit-testable.

## Running the Application

```bash
pip install -r backend/requirements.txt
python run_app.py
```

`run_app.py` runs the FastAPI backend in a background thread and opens the PyQt6 desktop interface in the same process.

### Default Credentials
- **Username:** `admin`
- **Password:** `admin@123`

## ERP Module Status

| Module | T-Code | Status | Description |
|---|---|---|---|
| Dashboard | `HOME` | ✅ Active | Overview & module launcher |
| Materials Management | `MM01` / `MM03` | ✅ Active | Material Master create & list |
| Purchasing | `ME2L` | 🚧 Roadmap | Purchase orders & returns |
| Sales & Distribution | `VA05` | 🚧 Roadmap | Sales orders & returns |
| Finance & Accounting | `FB50` | 🚧 Roadmap | General ledger, journals, reconciliation |
| Human Resources | `PA30` | 🚧 Roadmap | Employee master, attendance, leaves |
| Production Planning | `COOIS` | 🚧 Roadmap | BOMs, work orders, shop floor |
| Quality Management | `QA32` | 🚧 Roadmap | Inspection lots, usage decisions |
| AURA 7 AI | `AURA` | 🚧 Roadmap | AI assistant integration |
| Reports | `ZREP` | 🚧 Roadmap | Analytics and export engine |
| Approvals | `APPR` | 🚧 Roadmap | Multi-level approval workflows |
| Administration | `SU01` | 🚧 Roadmap | User permissions and system configuration |

## Project Structure

```
backend/
  main.py            FastAPI application factory and middleware
  core/              Ported business logic (ErpDB, ErpAuth, material, finance_engine, reports, ...)
  routers/           HTTP endpoints (auth.py, mm.py, nav.py, deps.py)
  schema.sql         SQLite database schema
  brain_erp.db       SQLite database (seeded with admin user & initial tables)
frontend/
  app_controller.py  Lifecycle manager for LoginWindow and MainWindow
  api_client.py      HTTP client (httpx) talking to the FastAPI backend
  login_window.py    Login dialog
  main_window.py     Main application window with top-bar, T-code search, sidebar & stack
  dashboard_widget.py Responsive dashboard with module cards and KPI banner
  mm01_widget.py     MM01 Create Material form and Materials list tab
run_app.py           Application entry point
```
