import os, sqlite3, sys, re

backend_dir = os.path.dirname(os.path.abspath(__file__))
schema_file = os.path.join(backend_dir, "schema.sql")

# Read existing schema
with open(schema_file, "r", encoding="utf-8") as f:
    orig_sql = f.read()

conn = sqlite3.connect(":memory:")
conn.executescript(orig_sql)
cur = conn.cursor()

from test_additions import additions_sql
conn.executescript(additions_sql)

column_patches = [
    # suppliers
    ("suppliers", "supplier_name", "varchar(200) DEFAULT ''"),
    ("suppliers", "pan", "varchar(20) DEFAULT ''"),
    ("suppliers", "bank_branch", "varchar(100) DEFAULT ''"),
    ("suppliers", "tds_applicable", "INTEGER DEFAULT 0"),
    ("suppliers", "tds_section", "varchar(20) DEFAULT ''"),
    ("suppliers", "tds_rate", "REAL DEFAULT 0.0"),
    ("suppliers", "msme_registered", "INTEGER DEFAULT 0"),
    ("suppliers", "msme_number", "varchar(50) DEFAULT ''"),
    ("suppliers", "remarks", "TEXT DEFAULT ''"),
    ("suppliers", "is_active", "INTEGER DEFAULT 1"),
    ("suppliers", "updated_by", "INTEGER DEFAULT NULL"),
    ("suppliers", "updated_at", "TEXT DEFAULT NULL"),

    # master_suppliers
    ("master_suppliers", "name", "varchar(200) DEFAULT ''"),
    ("master_suppliers", "pan_number", "varchar(20) DEFAULT ''"),
    ("master_suppliers", "status", "varchar(20) DEFAULT 'ACTIVE'"),

    # rfqs
    ("rfqs", "remarks", "TEXT DEFAULT ''"),
    ("rfqs", "is_deleted", "INTEGER DEFAULT 0"),
    ("rfqs", "branch_id", "INTEGER DEFAULT NULL"),
    ("rfqs", "quotes_finalized_at", "TEXT DEFAULT NULL"),
    ("rfqs", "quotes_finalized_by", "INTEGER DEFAULT NULL"),
    ("rfqs", "auto_compare_at", "TEXT DEFAULT NULL"),
    ("rfqs", "auto_award_status", "TEXT DEFAULT NULL"),
    ("rfqs", "auto_award_at", "TEXT DEFAULT NULL"),
    ("rfqs", "auto_award_trigger", "TEXT DEFAULT NULL"),
    ("rfqs", "auto_award_supplier_id", "INTEGER DEFAULT NULL"),
    ("rfqs", "auto_award_po_id", "INTEGER DEFAULT NULL"),

    # rfq_lines
    ("rfq_lines", "qty_required", "decimal(15,3) DEFAULT NULL"),
    ("rfq_lines", "remarks", "TEXT DEFAULT ''"),

    # rfq_suppliers
    ("rfq_suppliers", "invited_on", "TEXT DEFAULT NULL"),
    ("rfq_suppliers", "payment_terms", "varchar(200) DEFAULT ''"),
    ("rfq_suppliers", "delivery_period", "varchar(100) DEFAULT ''"),
    ("rfq_suppliers", "incoterms", "varchar(20) DEFAULT ''"),
    ("rfq_suppliers", "certifications", "TEXT DEFAULT ''"),
    ("rfq_suppliers", "negotiation_status", "varchar(20) DEFAULT 'NONE'"),
    ("rfq_suppliers", "negotiation_round", "INTEGER DEFAULT 0"),
    ("rfq_suppliers", "negotiation_discount_pct", "decimal(5,2) DEFAULT 0.0"),
    ("rfq_suppliers", "negotiated_po_id", "INTEGER DEFAULT NULL"),
    ("rfq_suppliers", "final_discount_pct", "decimal(5,2) DEFAULT NULL"),
    ("rfq_suppliers", "final_amount", "decimal(18,2) DEFAULT NULL"),
    ("rfq_suppliers", "final_remarks", "varchar(255) DEFAULT ''"),
    ("rfq_suppliers", "final_set_by", "INTEGER DEFAULT NULL"),
    ("rfq_suppliers", "final_set_at", "TEXT DEFAULT NULL"),

    # rfq_negotiations
    ("rfq_negotiations", "channel", "varchar(20) DEFAULT NULL"),
    ("rfq_negotiations", "remarks", "varchar(255) DEFAULT ''"),
    ("rfq_negotiations", "created_by", "INTEGER DEFAULT NULL"),

    # rfq_bids & rfq_bid_lines
    ("rfq_bids", "validity_days", "INTEGER DEFAULT 30"),
    ("rfq_bid_lines", "gst_rate", "decimal(5,2) DEFAULT 0.0"),
    ("rfq_bid_lines", "discount_pct", "decimal(5,2) DEFAULT 0.0"),
    ("rfq_bid_lines", "base_price", "decimal(18,4) DEFAULT 0.0"),
    ("rfq_bid_lines", "gst_amount", "decimal(18,2) DEFAULT 0.0"),
    ("rfq_bid_lines", "line_total", "decimal(18,2) DEFAULT 0.0"),

    # purchase_requisitions
    ("purchase_requisitions", "is_deleted", "INTEGER DEFAULT 0"),
    ("purchase_requisitions", "branch_id", "INTEGER DEFAULT NULL"),
    ("purchase_requisitions", "source_wo_id", "INTEGER DEFAULT NULL"),
    ("purchase_requisitions", "project_id", "INTEGER DEFAULT NULL"),
    ("purchase_requisitions", "wbs_element_id", "INTEGER DEFAULT NULL"),

    # purchase_orders
    ("purchase_orders", "created_by", "INTEGER DEFAULT NULL"),
    ("purchase_orders", "taxable_total", "decimal(18,2) DEFAULT 0.0"),
    ("purchase_orders", "gst_total", "decimal(18,2) DEFAULT 0.0"),
    ("purchase_orders", "discount_amount", "decimal(18,2) DEFAULT 0.0"),
    ("purchase_orders", "round_off", "decimal(10,2) DEFAULT 0.0"),
    ("purchase_orders", "supplier_notified_at", "TEXT DEFAULT NULL"),
    ("purchase_orders", "doc_token", "varchar(64) DEFAULT NULL"),
    ("purchase_orders", "branch_id", "INTEGER DEFAULT NULL"),
    ("purchase_orders", "project_id", "INTEGER DEFAULT NULL"),
    ("purchase_orders", "wbs_element_id", "INTEGER DEFAULT NULL"),

    # purchase_order_lines
    ("purchase_order_lines", "base_price", "decimal(18,4) DEFAULT 0.0"),
    ("purchase_order_lines", "gst_amount", "decimal(18,2) DEFAULT 0.0"),
    ("purchase_order_lines", "remarks", "TEXT DEFAULT ''"),
    ("purchase_order_lines", "qty_pending", "decimal(15,3) DEFAULT NULL"),
    ("purchase_order_lines", "pr_line_id", "INTEGER DEFAULT NULL"),
    ("purchase_order_lines", "supplier_notified_at", "TEXT DEFAULT NULL"),

    # supplier_invoices
    ("supplier_invoices", "block_reason", "TEXT DEFAULT ''"),
    ("supplier_invoices", "match_expected", "decimal(18,2) DEFAULT 0.0"),
    ("supplier_invoices", "cgst_amount", "decimal(18,2) DEFAULT 0.0"),
    ("supplier_invoices", "sgst_amount", "decimal(18,2) DEFAULT 0.0"),
    ("supplier_invoices", "igst_amount", "decimal(18,2) DEFAULT 0.0"),
    ("supplier_invoices", "itc_eligible", "INTEGER DEFAULT 1"),
    ("supplier_invoices", "reverse_charge", "INTEGER DEFAULT 0"),
    ("supplier_invoices", "tds_section", "varchar(20) DEFAULT ''"),
    ("supplier_invoices", "attachment_file", "varchar(255) DEFAULT NULL"),
    ("supplier_invoices", "ai_extracted", "INTEGER DEFAULT 0"),
    ("supplier_invoices", "created_by", "INTEGER DEFAULT NULL"),

    # goods_receipts
    ("goods_receipts", "branch_id", "INTEGER DEFAULT NULL"),
    ("goods_receipts", "inventory_account_id", "INTEGER DEFAULT NULL"),

    # goods_receipt_lines
    ("goods_receipt_lines", "qty", "decimal(15,3) DEFAULT 0.0"),

    # grn_deductions
    ("grn_deductions", "account_id", "INTEGER DEFAULT NULL"),
    ("grn_deductions", "key_code", "varchar(50) DEFAULT ''"),
    ("grn_deductions", "branch_id", "INTEGER DEFAULT NULL"),
    ("grn_deductions", "grn_line_id", "INTEGER DEFAULT NULL"),
    ("grn_deductions", "supplier_id", "INTEGER DEFAULT NULL"),
    ("grn_deductions", "input_value", "decimal(15,3) DEFAULT 0.0"),
    ("grn_deductions", "base_amount", "decimal(18,2) DEFAULT 0.0"),
    ("grn_deductions", "remarks", "TEXT DEFAULT ''"),
    ("grn_deductions", "created_by", "INTEGER DEFAULT NULL"),

    # tds_deductions
    ("tds_deductions", "source_type", "varchar(20) DEFAULT 'INVOICE'"),
    ("tds_deductions", "source_id", "INTEGER DEFAULT 0"),
    ("tds_deductions", "deduct_date", "date DEFAULT NULL"),
    ("tds_deductions", "supplier_id", "INTEGER DEFAULT 0"),
    ("tds_deductions", "gross_amount", "decimal(18,2) DEFAULT 0.0"),

    # units_of_measure
    ("units_of_measure", "full_name", "varchar(100) DEFAULT ''"),

    # projects
    ("projects", "project_code", "varchar(30) DEFAULT ''"),
    ("projects", "project_name", "varchar(200) DEFAULT ''"),
    ("projects", "planned_budget", "decimal(18,2) DEFAULT 0.0"),

    # erp_csrf_tokens
    ("erp_csrf_tokens", "used", "INTEGER DEFAULT 0"),

    # sales_return_lines
    ("sales_return_lines", "grn_line_id", "INTEGER DEFAULT NULL"),

    # items
    ("items", "block_status", "varchar(30) DEFAULT ''"),
    ("items", "item_id", "INTEGER DEFAULT NULL"),
    ("items", "unit_price", "decimal(18,4) DEFAULT 0.0"),
    ("items", "discount_pct", "decimal(5,2) DEFAULT 0.0"),
    ("items", "lot_size", "decimal(15,3) DEFAULT 1.0"),
    ("items", "qc_required", "INTEGER DEFAULT 0"),
    ("items", "batch_managed", "INTEGER DEFAULT 0"),
    ("items", "shelf_life_days", "INTEGER DEFAULT 0"),
    ("items", "gross_weight", "decimal(15,3) DEFAULT 0.0"),
    ("items", "manufacturer", "varchar(100) DEFAULT ''"),
    ("items", "moving_avg_price", "decimal(18,4) DEFAULT 0.0"),
    ("items", "gst_rate", "decimal(5,2) DEFAULT 0.0"),

    # material_groups
    ("material_groups", "inventory_account_id", "INTEGER DEFAULT NULL"),

    # stock_balances
    ("stock_balances", "company_id", "INTEGER DEFAULT 1"),

    # journal_entries
    ("journal_entries", "created_by", "INTEGER DEFAULT NULL"),
    ("journal_entries", "fiscal_year_id", "INTEGER DEFAULT NULL"),
    ("journal_entries", "project_id", "INTEGER DEFAULT NULL"),
    ("journal_entries", "wbs_element_id", "INTEGER DEFAULT NULL"),

    # journal_entry_lines
    ("journal_entry_lines", "party_type", "varchar(20) DEFAULT ''"),
    ("journal_entry_lines", "party_id", "INTEGER DEFAULT NULL"),

    # gst_transactions
    ("gst_transactions", "source_key", "varchar(100) DEFAULT ''"),
    ("gst_transactions", "direction", "varchar(20) DEFAULT 'INWARD'"),

    # release_actions
    ("release_actions", "doc_type", "varchar(20) DEFAULT ''"),
    ("release_actions", "doc_id", "INTEGER DEFAULT NULL"),
    ("release_actions", "action_date", "TEXT DEFAULT NULL"),

    # release_strategies
    ("release_strategies", "amount_from", "decimal(18,2) DEFAULT 0.0"),
    ("release_strategies", "amount_to", "decimal(18,2) DEFAULT 0.0"),

    # release_levels
    ("release_levels", "sequence_no", "INTEGER DEFAULT 1"),

    # document_releases
    ("document_releases", "created_by", "INTEGER DEFAULT NULL"),

    # companies
    ("companies", "company_name", "varchar(200) DEFAULT ''"),
    ("companies", "country_code", "CHAR(2) DEFAULT 'IN'"),
    ("companies", "logo_url", "varchar(500) DEFAULT NULL"),
    ("companies", "bg_color", "varchar(9) DEFAULT NULL"),
    ("companies", "primary_color", "varchar(9) DEFAULT NULL"),
    ("companies", "sidebar_color", "varchar(9) DEFAULT NULL"),
    ("companies", "bg_image_url", "varchar(500) DEFAULT NULL"),
    ("companies", "created_by", "INTEGER DEFAULT NULL"),

    # plants & departments & cost_centers
    ("plants", "category_id", "INTEGER DEFAULT NULL"),
    ("departments", "plant_id", "INTEGER DEFAULT NULL"),
    ("cost_centers", "cc_type", "varchar(20) DEFAULT 'OTHER'"),
    ("cost_centers", "plant_id", "INTEGER DEFAULT NULL"),
    ("cost_centers", "description", "varchar(255) DEFAULT ''"),

    # employees & designations
    ("employees", "category_id", "INTEGER DEFAULT NULL"),
    ("employees", "unit_id", "INTEGER DEFAULT NULL"),
    ("employees", "created_by", "INTEGER DEFAULT NULL"),
    ("employees", "updated_by", "INTEGER DEFAULT NULL"),
    ("designations", "created_by", "INTEGER DEFAULT NULL"),
    ("designations", "is_deleted", "INTEGER DEFAULT 0"),

    # inspection_lots
    ("inspection_lots", "remarks", "TEXT DEFAULT ''"),
    ("inspection_lots", "reject_reason", "TEXT DEFAULT ''"),

    # work_orders
    ("work_orders", "uom_id", "INTEGER DEFAULT NULL"),
    ("work_orders", "pr_id", "INTEGER DEFAULT NULL"),
    ("work_orders", "project_id", "INTEGER DEFAULT NULL"),
    ("work_orders", "wbs_element_id", "INTEGER DEFAULT NULL"),

    # quotations
    ("quotations", "public_token", "varchar(64) DEFAULT NULL"),
    ("quotations", "sent_at", "TEXT DEFAULT NULL"),
    ("quotations", "accepted_at", "TEXT DEFAULT NULL"),
    ("quotations", "negotiation_round", "INTEGER DEFAULT 0"),
    ("quotations", "total_amount", "decimal(18,2) DEFAULT 0.0"),
    ("quotations", "tax_amount", "decimal(18,2) DEFAULT 0.0"),
    ("quotations", "converted_so_id", "INTEGER DEFAULT NULL"),
    ("quotations", "customer_email", "varchar(200) DEFAULT NULL"),
    ("quotations", "source", "varchar(20) DEFAULT 'MANUAL'"),

    # sales_orders & sales_invoices
    ("sales_orders", "doc_token", "varchar(64) DEFAULT NULL"),
    ("sales_invoices", "round_off", "decimal(10,2) DEFAULT 0.0"),
    ("sales_invoice_lines", "batch_num", "varchar(50) DEFAULT ''"),
    ("sales_invoice_lines", "mfg_date", "date DEFAULT NULL"),
    ("sales_invoice_lines", "exp_date", "date DEFAULT NULL"),
    ("sales_invoice_lines", "manufacturer", "varchar(100) DEFAULT ''"),

    # payments
    ("payments", "bank_account_id", "INTEGER DEFAULT NULL"),

    # service_entry_sheets
    ("service_entry_sheets", "quality_of_service", "INTEGER DEFAULT NULL"),
    ("service_entry_sheets", "service_timeliness", "INTEGER DEFAULT NULL"),

    # non_conformances
    ("non_conformances", "project_id", "INTEGER DEFAULT NULL"),
    ("non_conformances", "wbs_element_id", "INTEGER DEFAULT NULL"),
]

for tbl, col, col_def in column_patches:
    existing = [r[1] for r in cur.execute(f"PRAGMA table_info({tbl})").fetchall()]
    if col not in existing:
        cur.execute(f"ALTER TABLE `{tbl}` ADD COLUMN `{col}` {col_def}")

cost_center_tables = [
    'purchase_requisitions', 'purchase_orders', 'rfqs', 'goods_receipts',
    'supplier_invoices', 'service_entry_sheets', 'quotations', 'sales_orders',
    'delivery_notes', 'sales_invoices', 'payments', 'journal_entries',
    'stock_transfers', 'physical_inventory', 'work_orders',
    'production_material_issues', 'production_receipts', 'maintenance_orders',
    'inspection_lots', 'non_conformances', 'assets', 'projects'
]
for cct in cost_center_tables:
    existing = [r[1] for r in cur.execute(f"PRAGMA table_info({cct})").fetchall()]
    for col, cdef in [('category_id', 'INTEGER DEFAULT NULL'),
                      ('unit_id', 'INTEGER DEFAULT NULL'),
                      ('department_id', 'INTEGER DEFAULT NULL')]:
        if col not in existing:
            cur.execute(f"ALTER TABLE `{cct}` ADD COLUMN `{col}` {cdef}")

# Also ensure options table exists for util.py get_option
cur.execute("CREATE TABLE IF NOT EXISTS `options` (`option_name` varchar(191) PRIMARY KEY, `option_value` longtext);")
cur.execute("CREATE TABLE IF NOT EXISTS `settings` (`key` varchar(191) PRIMARY KEY, `value` longtext);")

conn.commit()

dump_sql = []
dump_sql.append("-- ============================================================================")
dump_sql.append("-- Brain ERP — Complete SQLite Database Schema")
dump_sql.append("-- Comprehensive rewrite based on all backend modules, core engines, and routers")
dump_sql.append("-- ============================================================================")
dump_sql.append("PRAGMA foreign_keys = OFF;\n")

all_tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name").fetchall()]

for t in all_tables:
    ddl = cur.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
    # Ensure IF NOT EXISTS on all tables
    if not re.search(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS', ddl, re.IGNORECASE):
        ddl = re.sub(r'CREATE\s+TABLE\s+', 'CREATE TABLE IF NOT EXISTS ', ddl, count=1, flags=re.IGNORECASE)
    dump_sql.append(f"{ddl};")
    idx_rows = cur.execute(f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (t,)).fetchall()
    for ir in idx_rows:
        isql = ir[0]
        if not re.search(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS', isql, re.IGNORECASE):
            isql = re.sub(r'CREATE\s+(UNIQUE\s+)?INDEX\s+', r'CREATE \1INDEX IF NOT EXISTS ', isql, count=1, flags=re.IGNORECASE)
        dump_sql.append(f"{isql};")
    dump_sql.append("")

full_schema_content = "\n".join(dump_sql)

with open(schema_file, "w", encoding="utf-8") as f:
    f.write(full_schema_content)

print(f"Successfully wrote {len(all_tables)} tables to {schema_file} ({len(full_schema_content)} bytes).")
conn.close()
