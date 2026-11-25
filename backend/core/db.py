"""
Brain ERP — Database Layer
Direct port of PHP ErpDB class (core/db.php) → Python + SQLAlchemy + SQLite.
Preserves all method names, transaction depth tracking, stock movement logic,
GRN processing, payroll, work order completion, and document release workflow.

Original: Prigag Online Pvt. Ltd.
Port:     Python/SQLite standalone desktop version
"""

import sqlite3
import threading
import logging
import json
import re
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known ERP table names (no prefix). Same list as $knownTables in db.php.
# ---------------------------------------------------------------------------
KNOWN_TABLES = {
    "companies", "plants", "branches", "factory_categories", "tcodes", "roles",
    "role_tcodes", "users", "user_roles", "fiscal_years", "accounting_periods",
    "number_series", "release_strategies", "release_levels", "document_releases",
    "release_actions", "audit_logs", "notifications", "units_of_measure",
    "uom_conversions", "departments", "designations", "employees",
    "salary_components", "salary_structures", "salary_structure_components",
    "employee_salaries", "leave_types", "leave_balances", "leave_requests",
    "attendance", "payroll_runs", "payroll_details", "material_groups", "items",
    "suppliers", "supplier_items", "purchase_requisitions",
    "purchase_requisition_lines", "purchase_orders", "purchase_order_lines",
    "rfqs", "rfq_lines", "rfq_suppliers", "rfq_quote_lines", "rfq_negotiations",
    "bids", "bid_items", "service_entry_sheets", "service_entry_sheet_lines",
    "goods_receipts", "goods_receipt_lines", "grn_deductions", "so_deductions",
    "supplier_invoices", "customers", "customer_groups", "quotations",
    "quotation_lines", "quotation_negotiations", "sales_orders",
    "sales_order_lines", "delivery_notes", "delivery_note_lines",
    "sales_invoices", "sales_invoice_lines", "payments", "payment_allocations",
    "price_lists", "price_list_items", "bank_accounts", "gst_transactions",
    "warehouses", "warehouse_locations", "stock_balances", "stock_movements",
    "stock_transfers", "stock_transfer_lines", "physical_inventory",
    "physical_inventory_lines", "bom_headers", "bom_lines", "bom_revisions",
    "work_centers", "routings", "work_orders", "production_material_issues",
    "production_receipts", "production_labor", "downtime_logs",
    "chart_of_accounts", "cost_centers", "journal_entries",
    "journal_entry_lines", "inspection_plans", "inspection_characteristics",
    "inspection_lots", "inspection_results", "non_conformances", "assets",
    "maintenance_orders", "maintenance_plans", "profiles", "profile_roles",
    "projects", "project_wbs_elements", "project_team_members",
    "project_activities", "project_milestones", "project_budget_log",
    "project_cost_postings", "internal_messages", "internal_message_recipients",
    "eway_bills", "user_tcode_grants", "tcode_requests", "gl_determination",
    "fin_notes", "tds_sections", "tds_deductions", "tds_challans",
    "bank_statement_lines", "gstr2b_lines", "cost_center_budgets", "fx_rates",
    "asset_depreciation_entries", "item_uom_conversions", "purchase_returns",
    "purchase_return_lines", "sales_returns", "sales_return_lines",
    "aura_tokens", "aura_events",
    "user_passwords", "erp_csrf_tokens",
    "app_options",
}


class ErpDB:
    """
    Standalone SQLite database layer.
    Mirrors PHP ErpDB exactly: same method names, same transaction depth
    guard, same stock-movement / GRN / payroll / work-order business logic.
    Thread-safe: each thread gets its own SQLite connection via threading.local().
    """

    _db_path: str = "brain_erp.db"
    _local: threading.local = threading.local()
    _column_cache: dict = {}                 # {table_name: [col, ...]}
    _company_override: Optional[int] = None
    _company_cache: dict = {}
    _tx_depth: int = 0                       # nested transaction depth (same guard as PHP)
    _current_user_id: int = 0
    _current_username: str = ""

    # -----------------------------------------------------------------------
    # INIT
    # -----------------------------------------------------------------------
    @classmethod
    def init(cls, db_path: str = "brain_erp.db") -> None:
        cls._db_path = db_path
        logger.info("ErpDB initialised → %s", db_path)

    @classmethod
    def _conn(cls) -> sqlite3.Connection:
        """Per-thread SQLite connection with WAL mode and FK support."""
        if not hasattr(cls._local, "conn") or cls._local.conn is None:
            conn = sqlite3.connect(cls._db_path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            cls._local.conn = conn
        return cls._local.conn

    @classmethod
    def close(cls) -> None:
        if hasattr(cls._local, "conn") and cls._local.conn:
            cls._local.conn.close()
            cls._local.conn = None

    # -----------------------------------------------------------------------
    # TABLE NAME  (no prefix in standalone — tables are created as-is)
    # -----------------------------------------------------------------------
    @classmethod
    def tbl(cls, name: str) -> str:
        """Return the full table name. Standalone version: no prefix needed."""
        return name

    # -----------------------------------------------------------------------
    # QUERY METHODS
    # -----------------------------------------------------------------------
    @classmethod
    def fetch_all(cls, sql: str, params: tuple = ()) -> list[dict]:
        try:
            cur = cls._conn().execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("ErpDB fetch_all error: %s | SQL: %s", e, sql)
            raise

    @classmethod
    def fetch_one(cls, sql: str, params: tuple = ()) -> Optional[dict]:
        try:
            cur = cls._conn().execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error("ErpDB fetch_one error: %s | SQL: %s", e, sql)
            raise

    @classmethod
    def fetch_value(cls, sql: str, params: tuple = ()) -> Any:
        row = cls.fetch_one(sql, params)
        if row is None:
            return None
        return next(iter(row.values()))

    @classmethod
    def query(cls, sql: str, params: tuple = ()) -> int:
        """Execute a non-SELECT statement. Returns rows affected."""
        try:
            cur = cls._conn().execute(sql, params)
            if cls._tx_depth == 0:
                cls._conn().commit()
            return cur.rowcount
        except Exception as e:
            logger.error("ErpDB query error: %s | SQL: %s", e, sql)
            raise RuntimeError(f"DB error: {e}") from e

    # -----------------------------------------------------------------------
    # COLUMN INTROSPECTION — mirrors filterToColumns() in PHP
    # -----------------------------------------------------------------------
    @classmethod
    def get_columns(cls, table: str) -> list[str]:
        tbl = cls.tbl(table)
        if tbl in cls._column_cache:
            return cls._column_cache[tbl]
        try:
            rows = cls._conn().execute(f"PRAGMA table_info(`{tbl}`)").fetchall()
            cols = [row[1] for row in rows]
            cls._column_cache[tbl] = cols
            return cols
        except Exception:
            return []

    @classmethod
    def _filter_to_columns(cls, table: str, data: dict) -> dict:
        cols = cls.get_columns(table)
        if not cols:
            return data
        clean = {}
        dropped = []
        for col, val in data.items():
            if not re.match(r'^[A-Za-z0-9_]+$', str(col)):
                dropped.append(col)
                continue
            if col not in cols:
                dropped.append(col)
                continue
            clean[col] = val
        if dropped:
            logger.debug("ErpDB: ignored unknown column(s) %s for table %s", dropped, table)
        return clean

    # -----------------------------------------------------------------------
    # INSERT
    # -----------------------------------------------------------------------
    @classmethod
    def insert(cls, table: str, data: dict) -> int:
        tbl = cls.tbl(table)
        data = cls._filter_to_columns(table, data)
        # Remove None values so DB defaults apply
        clean = {k: v for k, v in data.items() if v is not None}
        if not clean:
            raise ValueError(f"No valid columns to insert into {table}")
        cols = ", ".join(f"`{c}`" for c in clean)
        placeholders = ", ".join("?" for _ in clean)
        sql = f"INSERT INTO `{tbl}` ({cols}) VALUES ({placeholders})"
        try:
            cur = cls._conn().execute(sql, tuple(clean.values()))
            if cls._tx_depth == 0:
                cls._conn().commit()
            return cur.lastrowid
        except Exception as e:
            logger.error("ErpDB insert error on %s: %s", tbl, e)
            raise RuntimeError(f"DB insert error on {table}: {e}") from e

    # -----------------------------------------------------------------------
    # UPDATE
    # -----------------------------------------------------------------------
    @classmethod
    def update(cls, table: str, data: dict, where: str, where_vals: tuple = ()) -> int:
        tbl = cls.tbl(table)
        data = cls._filter_to_columns(table, data)
        if not data:
            return 0
        set_clause = ", ".join(f"`{c}` = ?" for c in data)
        sql = f"UPDATE `{tbl}` SET {set_clause} WHERE {where}"
        params = tuple(data.values()) + tuple(where_vals)
        try:
            cur = cls._conn().execute(sql, params)
            if cls._tx_depth == 0:
                cls._conn().commit()
            return cur.rowcount
        except Exception as e:
            logger.error("ErpDB update error on %s: %s", tbl, e)
            raise RuntimeError(f"DB update error on {table}: {e}") from e

    # -----------------------------------------------------------------------
    # SOFT DELETE  (sets is_deleted = 1)
    # -----------------------------------------------------------------------
    @classmethod
    def delete(cls, table: str, where: str, params: tuple = ()) -> int:
        return cls.update(table, {"is_deleted": 1}, where, params)

    # -----------------------------------------------------------------------
    # TRANSACTIONS  — depth-aware, same guard as PHP
    # -----------------------------------------------------------------------
    @classmethod
    def begin_transaction(cls) -> None:
        if cls._tx_depth == 0:
            cls._conn().execute("BEGIN")
        cls._tx_depth += 1

    @classmethod
    def commit(cls) -> None:
        if cls._tx_depth > 0:
            cls._tx_depth -= 1
        if cls._tx_depth == 0:
            cls._conn().commit()

    @classmethod
    def rollback(cls) -> None:
        cls._tx_depth = 0
        try:
            cls._conn().rollback()
        except Exception:
            pass

    @classmethod
    def in_transaction(cls) -> bool:
        return cls._tx_depth > 0

    # -----------------------------------------------------------------------
    # GET NEXT DOCUMENT NUMBER  — thread-safe, mirrors sp_get_next_number
    # -----------------------------------------------------------------------
    @classmethod
    def get_next_number(cls, company_id: int, doc_type: str, fiscal_year: int) -> str:
        """
        Atomically increment and return the next document number.
        Mirrors PHP ErpDB::getNextNumber() exactly, using SQLite's
        exclusive connection lock instead of FOR UPDATE.
        """
        own = not cls.in_transaction()
        if own:
            cls._conn().execute("BEGIN EXCLUSIVE")
            cls._tx_depth += 1

        row = cls.fetch_one(
            "SELECT * FROM number_series WHERE company_id=? AND doc_type=? "
            "AND (fiscal_year=? OR fiscal_year IS NULL) LIMIT 1",
            (company_id, doc_type, fiscal_year),
        )
        if not row:
            # Auto-create missing series
            cls._conn().execute("ROLLBACK") if own else None
            cls._tx_depth = max(0, cls._tx_depth - 1)
            prefix = doc_type[:5].upper()
            new_id = cls.insert("number_series", {
                "company_id": company_id, "doc_type": doc_type,
                "prefix": prefix, "current_num": 0, "padding": 6, "fiscal_year": fiscal_year,
            })
            row = {"id": new_id, "prefix": prefix, "current_num": 0, "padding": 6}
            if own:
                cls._conn().execute("BEGIN EXCLUSIVE")
                cls._tx_depth += 1

        new_num = int(row["current_num"]) + 1
        year_short = str(fiscal_year)[-2:]
        padded = str(new_num).zfill(int(row["padding"]))
        doc_number = f"{row['prefix'] or ''}{year_short}{padded}"

        cls._conn().execute(
            "UPDATE number_series SET current_num=? WHERE id=?",
            (new_num, row["id"]),
        )
        if own:
            cls._conn().commit()
            cls._tx_depth = max(0, cls._tx_depth - 1)

        return doc_number

    # -----------------------------------------------------------------------
    # POST STOCK MOVEMENT  — mirrors PHP postStockMovement() exactly
    # -----------------------------------------------------------------------
    @classmethod
    def post_stock_movement(
        cls,
        company_id: int, item_id: int, warehouse_id: int,
        mvt_type: str, mvt_code: str,
        qty_in: float, qty_out: float, unit_cost: float,
        ref_type: str, ref_id: int, ref_num: str, user_id: int,
    ) -> int:
        cls.begin_transaction()
        try:
            balance = cls.fetch_one(
                "SELECT qty_on_hand, avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
                (item_id, warehouse_id),
            )
            current_qty = float(balance["qty_on_hand"]) if balance else 0.0
            current_avg = float(balance["avg_cost"]) if balance else 0.0

            if not balance:
                cls.insert("stock_balances", {
                    "item_id": item_id, "warehouse_id": warehouse_id,
                    "qty_on_hand": 0.0, "avg_cost": 0.0,
                })

            if qty_out > 0 and (current_qty - qty_out) < -0.001:
                raise RuntimeError(
                    f"Insufficient stock for item #{item_id}. "
                    f"Available: {current_qty}, Needed: {qty_out}"
                )

            new_qty = current_qty + qty_in - qty_out
            if qty_in > 0 and unit_cost > 0:
                new_avg = (
                    (current_qty * current_avg) + (qty_in * unit_cost)
                ) / max(0.001, current_qty + qty_in)
            else:
                new_avg = current_avg

            tot_val = (qty_in * unit_cost) if qty_in > 0 else (qty_out * current_avg)

            cls._conn().execute(
                "UPDATE stock_balances SET qty_on_hand=?, avg_cost=?, "
                "last_updated=datetime('now') WHERE item_id=? AND warehouse_id=?",
                (new_qty, new_avg, item_id, warehouse_id),
            )
            # Update item-level moving average price
            cls._conn().execute(
                "UPDATE items SET moving_avg_price=? WHERE id=?",
                (new_avg, item_id),
            )

            cls.insert("stock_movements", {
                "company_id": company_id,
                "movement_date": date.today().isoformat(),
                "movement_type": mvt_type,
                "mvt_code": mvt_code,
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "reference_type": ref_type,
                "reference_id": ref_id,
                "reference_num": ref_num,
                "qty_in": qty_in,
                "qty_out": qty_out,
                "unit_cost": unit_cost,
                "total_value": round(tot_val, 2),
                "running_balance": new_qty,
                "created_by": user_id,
            })
            mvt_id = cls.fetch_value("SELECT last_insert_rowid()")
            cls.commit()
            return int(mvt_id)
        except Exception:
            cls.rollback()
            raise

    # -----------------------------------------------------------------------
    # PROCESS GRN  — mirrors PHP processGRN() exactly
    # -----------------------------------------------------------------------
    @classmethod
    def process_grn(cls, grn_id: int, user_id: int) -> None:
        grn = cls.fetch_one(
            "SELECT * FROM goods_receipts WHERE id=? AND status IN ('DRAFT','PENDING_QC')",
            (grn_id,),
        )
        if not grn:
            raise RuntimeError("GRN not found or already posted.")

        is_return = str(grn.get("movement_type", "101")) == "122"

        cls.begin_transaction()
        try:
            lines = cls.fetch_all(
                "SELECT * FROM goods_receipt_lines WHERE grn_id=?", (grn_id,)
            )
            for line in lines:
                if float(line.get("qty_accepted") or 0) <= 0:
                    continue
                qty_accepted = float(line["qty_accepted"])
                cls.post_stock_movement(
                    int(grn["company_id"]),
                    int(line["item_id"]),
                    int(grn["warehouse_id"]),
                    "RTV" if is_return else "GRN",
                    "122" if is_return else "101",
                    0.0 if is_return else qty_accepted,
                    qty_accepted if is_return else 0.0,
                    float(line.get("unit_price") or 0),
                    "goods_receipts", grn_id, grn["grn_number"], user_id,
                )
                if line.get("po_line_id"):
                    if is_return:
                        cls._conn().execute(
                            "UPDATE purchase_order_lines SET qty_received = MAX(0, qty_received - ?) WHERE id=?",
                            (qty_accepted, int(line["po_line_id"])),
                        )
                    else:
                        cls._conn().execute(
                            "UPDATE purchase_order_lines SET qty_received = qty_received + ? WHERE id=?",
                            (qty_accepted, int(line["po_line_id"])),
                        )

            cls.update(
                "goods_receipts",
                {"status": "POSTED", "posted_at": datetime.now().isoformat()},
                "id=?", (grn_id,),
            )

            if grn.get("po_id") and not is_return:
                open_qty = cls.fetch_value(
                    "SELECT SUM(qty_ordered - qty_received) FROM purchase_order_lines WHERE po_id=?",
                    (int(grn["po_id"]),),
                )
                new_status = "CLOSED" if (open_qty or 0) <= 0 else "PARTIAL"
                cls.update("purchase_orders", {"status": new_status}, "id=?", (int(grn["po_id"]),))
            elif grn.get("po_id") and is_return:
                recv = float(cls.fetch_value(
                    "SELECT COALESCE(SUM(qty_received),0) FROM purchase_order_lines WHERE po_id=?",
                    (int(grn["po_id"]),),
                ) or 0)
                cls.update(
                    "purchase_orders",
                    {"status": "PARTIAL" if recv > 0 else "APPROVED"},
                    "id=?", (int(grn["po_id"]),),
                )
            cls.commit()
        except Exception:
            cls.rollback()
            raise

    # -----------------------------------------------------------------------
    # RELEASE DOCUMENT  — mirrors PHP releaseDocument() exactly
    # -----------------------------------------------------------------------
    @classmethod
    def release_document(
        cls, release_id: int, action: str, user_id: int, remarks: str
    ) -> dict:
        rel = cls.fetch_one(
            "SELECT * FROM document_releases WHERE id=? AND status='PENDING'",
            (release_id,),
        )
        if not rel:
            raise RuntimeError("Release request not found or already processed.")

        cls.begin_transaction()
        try:
            cls.insert("release_actions", {
                "release_id": release_id,
                "level_num": rel["current_level"],
                "action": action,
                "action_by": user_id,
                "action_at": datetime.now().isoformat(),
                "remarks": remarks,
            })

            po_released_id = None
            tbl_map = {
                "purchase_order": "purchase_orders",
                "sales_order": "sales_orders",
                "work_order": "work_orders",
            }

            if action == "APPROVED":
                max_level = int(cls.fetch_value(
                    "SELECT COUNT(*) FROM release_levels WHERE strategy_id=?",
                    (rel["strategy_id"],),
                ) or 0)
                if int(rel["current_level"]) >= max_level:
                    cls.update("document_releases", {
                        "status": "RELEASED",
                        "fully_released_at": datetime.now().isoformat(),
                    }, "id=?", (release_id,))
                    if rel["doc_type"] in tbl_map:
                        cls.update(
                            tbl_map[rel["doc_type"]],
                            {"status": "APPROVED", "release_status": "RELEASED"},
                            "id=?", (rel["doc_id"],),
                        )
                        if rel["doc_type"] == "purchase_order":
                            po_released_id = int(rel["doc_id"])
                    if rel["doc_type"] == "purchase_requisition":
                        cls.update("purchase_requisitions", {"status": "APPROVED"}, "id=?", (rel["doc_id"],))
                        cls._conn().execute(
                            "UPDATE purchase_requisition_lines SET qty_approved=qty_requested WHERE pr_id=?",
                            (int(rel["doc_id"]),),
                        )
                else:
                    cls.update("document_releases", {
                        "current_level": int(rel["current_level"]) + 1,
                    }, "id=?", (release_id,))

            elif action == "REJECTED":
                cls.update("document_releases", {"status": "REJECTED"}, "id=?", (release_id,))
                if rel["doc_type"] in tbl_map:
                    cls.update(
                        tbl_map[rel["doc_type"]],
                        {"status": "REJECTED", "release_status": "REJECTED"},
                        "id=?", (rel["doc_id"],),
                    )
                if rel["doc_type"] == "purchase_requisition":
                    cls.update("purchase_requisitions", {"status": "REJECTED"}, "id=?", (rel["doc_id"],))

            cls.commit()
            return {"po_released_id": po_released_id}
        except Exception:
            cls.rollback()
            raise

    # -----------------------------------------------------------------------
    # PROCESS PAYROLL  — mirrors PHP processPayroll() exactly
    # -----------------------------------------------------------------------
    @classmethod
    def process_payroll(cls, run_id: int, user_id: int) -> None:
        run = cls.fetch_one(
            "SELECT * FROM payroll_runs WHERE id=? AND status='DRAFT'", (run_id,)
        )
        if not run:
            raise RuntimeError("Payroll run not found or already processed.")

        cls.begin_transaction()
        try:
            cls._conn().execute(
                "DELETE FROM payroll_details WHERE payroll_run_id=?", (run_id,)
            )
            employees = cls.fetch_all(
                """SELECT e.id, es.basic_salary, es.structure_id
                   FROM employees e
                   JOIN employee_salaries es ON es.employee_id=e.id
                     AND es.effective_from <= date('now')
                     AND (es.effective_to IS NULL OR es.effective_to >= date('now'))
                   WHERE e.company_id=? AND e.status='ACTIVE'""",
                (run["company_id"],),
            )
            total_gross = 0.0
            total_ded = 0.0
            for emp in employees:
                components = cls.fetch_all(
                    """SELECT ssc.component_id, ssc.calculation, ssc.amount,
                              ssc.percentage, sc.comp_type
                       FROM salary_structure_components ssc
                       JOIN salary_components sc ON sc.id=ssc.component_id
                       WHERE ssc.structure_id=?""",
                    (emp["structure_id"],),
                )
                for comp in components:
                    calc = comp["calculation"]
                    if calc == "FIXED":
                        amount = float(comp["amount"] or 0)
                    elif calc == "PERCENTAGE_BASIC":
                        amount = round(float(emp["basic_salary"]) * float(comp["percentage"] or 0) / 100, 2)
                    elif calc == "PERCENTAGE_GROSS":
                        amount = round(float(emp["basic_salary"]) * 1.5 * float(comp["percentage"] or 0) / 100, 2)
                    else:
                        amount = float(comp["amount"] or 0)

                    cls.insert("payroll_details", {
                        "payroll_run_id": run_id,
                        "employee_id": emp["id"],
                        "component_id": comp["component_id"],
                        "amount": amount,
                        "comp_type": comp["comp_type"],
                    })
                    if comp["comp_type"] == "EARNING":
                        total_gross += amount
                    else:
                        total_ded += amount

            cls.update("payroll_runs", {
                "total_gross": total_gross,
                "total_deductions": total_ded,
                "total_net": total_gross - total_ded,
                "status": "PROCESSED",
                "processed_by": user_id,
                "processed_at": datetime.now().isoformat(),
            }, "id=?", (run_id,))
            cls.commit()
        except Exception:
            cls.rollback()
            raise

    # -----------------------------------------------------------------------
    # COMPLETE WORK ORDER  — mirrors PHP completeWorkOrder() exactly
    # -----------------------------------------------------------------------
    @classmethod
    def complete_work_order(
        cls,
        wo_id: int, qty_produced: float, qty_rejected: float,
        warehouse_id: int, user_id: int,
    ) -> None:
        wo = cls.fetch_one(
            "SELECT * FROM work_orders WHERE id=? AND status IN ('RELEASED','IN_PROGRESS')",
            (wo_id,),
        )
        if not wo:
            raise RuntimeError("Work order not found or not in correct status.")

        cls.begin_transaction()
        try:
            bom_lines = cls.fetch_all(
                "SELECT * FROM bom_lines WHERE bom_id=?", (wo["bom_id"],)
            )
            for bl in bom_lines:
                qty_needed = (
                    float(bl["qty_required"]) * qty_produced
                    * (1 + float(bl.get("scrap_pct") or 0) / 100)
                )
                avg_cost = float(cls.fetch_value(
                    "SELECT avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
                    (bl["component_id"], warehouse_id),
                ) or 0)
                cls.post_stock_movement(
                    int(wo["company_id"]), int(bl["component_id"]), warehouse_id,
                    "PROD_ISSUE", "261",
                    0.0, qty_needed, avg_cost,
                    "work_orders", wo_id, wo["wo_number"], user_id,
                )
                cls.insert("production_material_issues", {
                    "wo_id": wo_id, "item_id": bl["component_id"],
                    "warehouse_id": warehouse_id,
                    "qty_required": qty_needed, "qty_issued": qty_needed,
                    "issue_date": date.today().isoformat(), "created_by": user_id,
                })

            avg_fg = float(cls.fetch_value(
                "SELECT avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?",
                (wo["item_id"], warehouse_id),
            ) or 0)
            cls.post_stock_movement(
                int(wo["company_id"]), int(wo["item_id"]), warehouse_id,
                "PROD_RCPT", "101",
                qty_produced, 0.0, avg_fg,
                "work_orders", wo_id, wo["wo_number"], user_id,
            )
            cls.insert("production_receipts", {
                "wo_id": wo_id, "item_id": wo["item_id"],
                "warehouse_id": warehouse_id,
                "qty_received": qty_produced, "qty_rejected": qty_rejected,
                "receipt_date": date.today().isoformat(), "created_by": user_id,
            })
            cls.update("work_orders", {
                "qty_produced": float(wo.get("qty_produced") or 0) + qty_produced,
                "qty_rejected": float(wo.get("qty_rejected") or 0) + qty_rejected,
                "status": "COMPLETED",
                "actual_end": datetime.now().isoformat(),
            }, "id=?", (wo_id,))
            cls.commit()
        except Exception:
            cls.rollback()
            raise

    # -----------------------------------------------------------------------
    # STORED PROCEDURE ROUTER  — mirrors callProcedure() in PHP
    # -----------------------------------------------------------------------
    @classmethod
    def call_procedure(cls, proc: str, in_params: list, out_params: list = None) -> dict:
        if proc == "sp_get_next_number":
            company_id, doc_type, fiscal_year = in_params
            num = cls.get_next_number(int(company_id), str(doc_type), int(fiscal_year))
            key = out_params[0] if out_params else "p_doc_number"
            return {key: num}
        elif proc == "sp_post_stock_movement":
            mvt_id = cls.post_stock_movement(*in_params)
            return {"p_mvt_id": mvt_id}
        elif proc == "sp_process_grn":
            cls.process_grn(int(in_params[0]), int(in_params[1]))
            return {}
        elif proc == "sp_release_document":
            result = cls.release_document(
                int(in_params[0]), str(in_params[1]),
                int(in_params[2]), str(in_params[3] if len(in_params) > 3 else "")
            )
            return result
        elif proc == "sp_process_payroll":
            cls.process_payroll(int(in_params[0]), int(in_params[1]))
            return {}
        elif proc == "sp_complete_work_order":
            cls.complete_work_order(
                int(in_params[0]), float(in_params[1]), float(in_params[2]),
                int(in_params[3]), int(in_params[4]),
            )
            return {}
        else:
            logger.warning("Unknown procedure: %s", proc)
            return {}

    # -----------------------------------------------------------------------
    # PAGINATION  — mirrors PHP paginate()
    # -----------------------------------------------------------------------
    @classmethod
    def paginate(cls, sql: str, params: tuple, page: int, page_size: int = 20) -> dict:
        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS cnt_tbl"
        total_rows = int(cls.fetch_value(count_sql, params) or 0)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        offset = (page - 1) * page_size
        rows = cls.fetch_all(f"{sql} LIMIT {page_size} OFFSET {offset}", params)
        return {
            "data": rows,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "current_page": page,
            "page_size": page_size,
        }

    # -----------------------------------------------------------------------
    # MULTI-COMPANY HELPERS
    # -----------------------------------------------------------------------
    @classmethod
    def use_company(cls, company_id: Optional[int]) -> None:
        cls._company_override = company_id
        cls._company_cache = {}

    @classmethod
    def clear_company_cache(cls) -> None:
        cls._company_cache = {}

    @classmethod
    def get_company_id(cls) -> int:
        if cls._company_override:
            return cls._company_override
        uid = cls._current_user_id
        if uid not in cls._company_cache:
            cls._company_cache[uid] = cls._resolve_company_id(uid)
        return cls._company_cache[uid]

    @classmethod
    def _resolve_company_id(cls, user_id: int) -> int:
        if user_id:
            row = cls.fetch_one(
                "SELECT company_id FROM users WHERE id=? AND is_deleted=0", (user_id,)
            )
            if row and row.get("company_id"):
                return int(row["company_id"])
        # Fall back to first active company
        first = cls.fetch_value("SELECT id FROM companies WHERE is_active=1 ORDER BY id LIMIT 1")
        return int(first) if first else 1

    @classmethod
    def can_switch_company(cls) -> bool:
        """In standalone mode, admins can always switch companies."""
        from core.auth import ErpAuth
        return ErpAuth.has_role("SUPER_ADMIN")

    @classmethod
    def get_current_fiscal_year(cls) -> int:
        """Indian default: fiscal year starts April 1."""
        today = date.today()
        return today.year if today.month >= 4 else today.year - 1

    # -----------------------------------------------------------------------
    # AUDIT LOG
    # -----------------------------------------------------------------------
    @classmethod
    def audit(
        cls,
        table: str, record_id: int, action: str,
        old_data: dict = None, new_data: dict = None,
        user_id: int = 0, tcode: str = "",
    ) -> None:
        try:
            cls.insert("audit_logs", {
                "company_id": cls.get_company_id(),
                "user_id": user_id or cls._current_user_id,
                "username": cls._current_username,
                "tcode": tcode,
                "table_name": table,
                "record_id": record_id,
                "action": action,
                "old_data": json.dumps(old_data) if old_data else None,
                "new_data": json.dumps(new_data) if new_data else None,
                "ip_address": "127.0.0.1",
                "created_at": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.error("ErpDB audit error: %s", e)

    # -----------------------------------------------------------------------
    # KEY/VALUE OPTIONS  — mirrors WordPress get_option()/update_option().
    # NOTE: called throughout the port (documents.py, helpers.py,
    # invoice_scan.py) but never previously implemented here — added now
    # since finance_engine.py depends on it heavily (msme_erp_fin_opt()).
    # Self-healing: creates app_options on first use, same pattern as
    # documents.py's doc_ensure_col().
    # -----------------------------------------------------------------------
    _options_ready: bool = False

    @classmethod
    def _ensure_options_table(cls) -> None:
        if cls._options_ready:
            return
        cls._conn().execute(
            "CREATE TABLE IF NOT EXISTS app_options "
            "(option_name TEXT PRIMARY KEY, option_value TEXT)"
        )
        cls._conn().commit()
        cls._options_ready = True

    @classmethod
    def get_option(cls, name: str, default: Any = "") -> Any:
        cls._ensure_options_table()
        row = cls._conn().execute(
            "SELECT option_value FROM app_options WHERE option_name=?", (name,)
        ).fetchone()
        if row is None:
            return default
        val = row[0]
        try:
            return json.loads(val)
        except (TypeError, ValueError):
            return val

    @classmethod
    def set_option(cls, name: str, value: Any) -> None:
        cls._ensure_options_table()
        stored = value if isinstance(value, str) else json.dumps(value)
        cls._conn().execute(
            "INSERT INTO app_options (option_name, option_value) VALUES (?, ?) "
            "ON CONFLICT(option_name) DO UPDATE SET option_value=excluded.option_value",
            (name, stored),
        )
        cls._conn().commit()


# Convenience alias — mirrors PHP "class_alias('ErpDB', 'DB')"
DB = ErpDB