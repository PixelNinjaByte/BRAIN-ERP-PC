Continuing the Brain ERP port (WordPress plugin msme-erp → Python/SQLite
desktop app "Brain ERP", FastAPI + PyQt6). backend.zip = Python port so far,
msme-erp.zip = original PHP plugin source.

Already ported in backend/core/*.py: auth, db, helpers, installer,
invoice_scan, user_prefs, app (logic only — the WP admin HTML/CSS/JS shell
in render_layout was intentionally skipped, no UI equivalent exists yet),
documents.

Still missing: aura_gateway.php, finance_engine.php, material.php,
reports.php, returns.php — port these from msme-erp/msme-erp/core/ into
backend/backend/core/, one at a time, matching existing conventions
(ErpDB/ErpAuth classes, snake_case, SQLite instead of wp_*, no WordPress
dependency). Flag anything that's WP-admin-UI rather than business logic
before porting it wholesale, same as was done for app.php.

Next: finance_engine.php.