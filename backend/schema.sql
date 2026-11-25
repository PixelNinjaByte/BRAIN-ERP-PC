-- ============================================================================
-- Brain ERP — Complete SQLite Database Schema
-- Comprehensive rewrite based on all backend modules, core engines, and routers
-- ============================================================================
PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS `accounting_periods` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `fiscal_year_id` INTEGER NOT NULL,
  `period_number` INTEGER NOT NULL,
  `name` varchar(50) DEFAULT NULL,
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `is_closed` INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS `app_options` (
  `option_name` TEXT PRIMARY KEY,
  `option_value` TEXT
);

CREATE TABLE IF NOT EXISTS `asset_depreciation_entries` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `asset_id` INTEGER NOT NULL,
  `fiscal_year_id` INTEGER NOT NULL,
  `posting_date` date NOT NULL,
  `depreciation_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `accumulated_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `assets` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `asset_code` varchar(30) NOT NULL,
  `asset_name` varchar(200) NOT NULL,
  `asset_type` varchar(30) DEFAULT NULL,
  `category` varchar(50) DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `location` varchar(100) DEFAULT NULL,
  `purchase_date` date DEFAULT NULL,
  `purchase_price` decimal(18,2) DEFAULT NULL,
  `useful_life_yrs` INTEGER DEFAULT NULL,
  `depreciation_method` varchar(20) DEFAULT 'SLM',
  `salvage_value` decimal(18,2) DEFAULT 0.00,
  `current_value` decimal(18,2) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'ACTIVE',
  `serial_number` varchar(100) DEFAULT NULL,
  `manufacturer` varchar(100) DEFAULT NULL,
  `model` varchar(100) DEFAULT NULL,
  `warranty_expiry` date DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `assets__asset_code` ON `assets` (`asset_code`);

CREATE TABLE IF NOT EXISTS `attendance` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `employee_id` INTEGER NOT NULL,
  `att_date` date NOT NULL,
  `check_in` TEXT DEFAULT NULL,
  `check_out` TEXT DEFAULT NULL,
  `status` varchar(20) DEFAULT 'PRESENT',
  `working_hrs` decimal(5,2) DEFAULT NULL,
  `overtime_hrs` decimal(5,2) DEFAULT 0.00
);
CREATE UNIQUE INDEX IF NOT EXISTS `attendance__emp_date` ON `attendance` (`employee_id`, `att_date`);

CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER DEFAULT NULL,
  `user_id` INTEGER DEFAULT NULL,
  `username` varchar(100) DEFAULT NULL,
  `tcode` varchar(20) DEFAULT NULL,
  `table_name` varchar(100) DEFAULT NULL,
  `record_id` INTEGER DEFAULT NULL,
  `action` varchar(20) DEFAULT NULL,
  `old_data` longtext DEFAULT NULL,
  `new_data` longtext DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `audit_logs__idx_table` ON `audit_logs` (`table_name`, `record_id`);
CREATE INDEX IF NOT EXISTS `audit_logs__idx_user` ON `audit_logs` (`user_id`);
CREATE INDEX IF NOT EXISTS `audit_logs__idx_date` ON `audit_logs` (`created_at`);

CREATE TABLE IF NOT EXISTS `aura_events` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `event` varchar(60) NOT NULL,
  `payload` text DEFAULT NULL,
  `created_at` TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS `aura_events__company_id` ON `aura_events` (`company_id`, `id`);

CREATE TABLE IF NOT EXISTS `aura_tokens` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `wp_user_id` INTEGER NOT NULL,
  `label` varchar(100) NOT NULL,
  `token_hash` char(64) NOT NULL,
  `token_hint` varchar(16) NOT NULL DEFAULT '',
  `scopes` varchar(100) NOT NULL DEFAULT 'read',
  `is_active` INTEGER NOT NULL DEFAULT 1,
  `expires_at` TEXT DEFAULT NULL,
  `last_used_at` TEXT DEFAULT NULL,
  `last_ip` varchar(64) DEFAULT NULL,
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `aura_tokens__token_hash` ON `aura_tokens` (`token_hash`);

CREATE TABLE IF NOT EXISTS `bank_accounts` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `account_name` varchar(100) DEFAULT NULL,
  `bank_name` varchar(100) DEFAULT NULL,
  `account_num` varchar(50) DEFAULT NULL,
  `ifsc_code` varchar(20) DEFAULT NULL,
  `upi_id` varchar(100) DEFAULT NULL,
  `account_type` varchar(20) DEFAULT NULL,
  `opening_bal` decimal(18,2) DEFAULT 0.00,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `bank_statement_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `bank_account_id` INTEGER NOT NULL,
  `stmt_date` date NOT NULL,
  `description` varchar(255) DEFAULT '',
  `ref_no` varchar(100) DEFAULT '',
  `debit` decimal(18,2) DEFAULT 0.0,
  `credit` decimal(18,2) DEFAULT 0.0,
  `reconciled` INTEGER DEFAULT 0,
  `matched_payment_id` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `bid_items` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `bid_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `pr_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER DEFAULT NULL,
  `item_type` varchar(20) NOT NULL DEFAULT 'SERVICE',
  `description` varchar(255) DEFAULT NULL,
  `qty` decimal(15,3) DEFAULT 0.000,
  `uom_id` INTEGER DEFAULT NULL,
  `estimated_rate` decimal(18,2) DEFAULT 0.00,
  `estimated_value` decimal(18,2) DEFAULT 0.00
);
CREATE INDEX IF NOT EXISTS `bid_items__idx_bid` ON `bid_items` (`bid_id`);

CREATE TABLE IF NOT EXISTS `bids` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL,
  `bid_number` varchar(30) NOT NULL,
  `bid_type` varchar(20) NOT NULL DEFAULT 'SERVICE',
  `requirement_type` varchar(30) NOT NULL DEFAULT 'OTHERS',
  `mode_of_bid` varchar(20) NOT NULL DEFAULT 'OPEN',
  `sche_delivery_days` INTEGER DEFAULT 0,
  `bid_creation_date` date DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `warehouse_id` INTEGER DEFAULT NULL,
  `purch_group` varchar(50) DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'NEW',
  `bid_publish_date` date DEFAULT NULL,
  `bid_stage` varchar(20) NOT NULL DEFAULT 'SINGLE_PART',
  `bid_security_applic` INTEGER DEFAULT 0,
  `bid_security_amount` decimal(18,2) DEFAULT 0.00,
  `bid_security_amount_fc` decimal(18,2) DEFAULT 0.00,
  `foreign_curr_appli` INTEGER DEFAULT 0,
  `tax_rate` decimal(5,2) DEFAULT 0.00,
  `e_proc_applicable` INTEGER DEFAULT 0,
  `bid_spec_applic` INTEGER DEFAULT 0,
  `bid_spec_cost` decimal(18,2) DEFAULT 0.00,
  `bid_spec_cost_fc` decimal(18,2) DEFAULT 0.00,
  `download_start_dt` TEXT DEFAULT NULL,
  `bid_subm_end_dt` TEXT DEFAULT NULL,
  `bid_open_dt` TEXT DEFAULT NULL,
  `title_of_bid` text DEFAULT NULL,
  `bid_value` decimal(18,2) DEFAULT 0.00,
  `e_reverse_auction` INTEGER DEFAULT 0,
  `pr_id` INTEGER DEFAULT NULL,
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `bids__uq_company_bidno` ON `bids` (`company_id`, `bid_number`);
CREATE INDEX IF NOT EXISTS `bids__company_id` ON `bids` (`company_id`);
CREATE INDEX IF NOT EXISTS `bids__idx_pr` ON `bids` (`pr_id`);

CREATE TABLE IF NOT EXISTS `bom_headers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `item_id` INTEGER NOT NULL,
  `bom_number` varchar(30) NOT NULL,
  `revision` varchar(10) DEFAULT 'A',
  `effective_from` date NOT NULL,
  `effective_to` date DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `is_locked` INTEGER DEFAULT 0,
  `standard_qty` decimal(15,3) DEFAULT 1.000,
  `uom_id` INTEGER NOT NULL,
  `notes` text DEFAULT NULL,
  `approved_by` INTEGER DEFAULT NULL,
  `approved_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS `bom_headers__bom_number` ON `bom_headers` (`bom_number`);
CREATE INDEX IF NOT EXISTS `bom_headers__idx_item` ON `bom_headers` (`item_id`);

CREATE TABLE IF NOT EXISTS `bom_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `bom_id` INTEGER NOT NULL,
  `sequence` INTEGER NOT NULL,
  `component_id` INTEGER NOT NULL,
  `qty_required` decimal(15,6) NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `scrap_pct` decimal(5,2) DEFAULT 0.00,
  `is_phantom` INTEGER DEFAULT 0,
  `sub_bom_id` INTEGER DEFAULT NULL,
  `unit_cost` decimal(18,4) DEFAULT 0.0000,
  `notes` text DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `branches` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL,
  `plant_id` INTEGER DEFAULT NULL,
  `code` varchar(20) NOT NULL,
  `name` varchar(200) NOT NULL,
  `address` text DEFAULT NULL,
  `gstin` varchar(20) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `branches__company_id` ON `branches` (`company_id`);

CREATE TABLE IF NOT EXISTS `chart_of_accounts` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `account_code` varchar(20) NOT NULL,
  `account_name` varchar(200) NOT NULL,
  `account_type` varchar(20) NOT NULL,
  `account_group` varchar(50) DEFAULT NULL,
  `parent_id` INTEGER DEFAULT NULL,
  `level` INTEGER DEFAULT 1,
  `is_group` INTEGER DEFAULT 0,
  `normal_balance` varchar(6) DEFAULT 'DEBIT',
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `chart_of_accounts__account_code` ON `chart_of_accounts` (`account_code`);

CREATE TABLE IF NOT EXISTS `companies` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `code` varchar(20) NOT NULL,
  `name` varchar(200) NOT NULL,
  `legal_name` varchar(200) DEFAULT NULL,
  `address` text DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `state` varchar(100) DEFAULT NULL,
  `country` varchar(100) DEFAULT 'India',
  `country_code` char(2) DEFAULT 'IN',
  `pincode` varchar(10) DEFAULT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `email` varchar(200) DEFAULT NULL,
  `gstin` varchar(20) DEFAULT NULL,
  `pan_number` varchar(20) DEFAULT NULL,
  `msme_reg_number` varchar(50) DEFAULT NULL,
  `currency_code` char(3) DEFAULT 'INR',
  `fiscal_year_start` date DEFAULT NULL,
  `logo_url` varchar(500) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `company_name` varchar(200) DEFAULT '', `bg_color` varchar(9) DEFAULT NULL, `primary_color` varchar(9) DEFAULT NULL, `sidebar_color` varchar(9) DEFAULT NULL, `bg_image_url` varchar(500) DEFAULT NULL, `created_by` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `companies__code` ON `companies` (`code`);

CREATE TABLE IF NOT EXISTS `cost_center_budgets` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `cost_center_id` INTEGER NOT NULL,
  `fiscal_year_id` INTEGER NOT NULL,
  `allocated_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `consumed_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `cost_centers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
, `cc_type` varchar(20) DEFAULT 'OTHER', `plant_id` INTEGER DEFAULT NULL, `description` varchar(255) DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS `cost_centers__code` ON `cost_centers` (`code`);

CREATE TABLE IF NOT EXISTS `customers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `customer_code` varchar(30) NOT NULL,
  `name` varchar(200) NOT NULL,
  `contact_person` varchar(100) DEFAULT NULL,
  `email` varchar(200) DEFAULT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `billing_address` text DEFAULT NULL,
  `shipping_address` text DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `state` varchar(100) DEFAULT NULL,
  `country` varchar(100) DEFAULT 'India',
  `pincode` varchar(10) DEFAULT NULL,
  `gstin` varchar(20) DEFAULT NULL,
  `pan_number` varchar(20) DEFAULT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `credit_days` INTEGER DEFAULT 30,
  `credit_limit` decimal(18,2) DEFAULT 0.00,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `status` varchar(20) DEFAULT 'ACTIVE',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS `customers__customer_code` ON `customers` (`customer_code`);

CREATE TABLE IF NOT EXISTS `delivery_note_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `dn_id` INTEGER NOT NULL,
  `so_line_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_packed` decimal(15,3) NOT NULL,
  `batch_num` varchar(50) DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `delivery_notes` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `dn_number` varchar(30) NOT NULL,
  `dn_date` date NOT NULL,
  `so_id` INTEGER NOT NULL,
  `customer_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `vehicle_num` varchar(20) DEFAULT NULL,
  `driver_name` varchar(100) DEFAULT NULL,
  `transport` varchar(100) DEFAULT NULL,
  `lr_number` varchar(50) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `dispatched_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `delivery_notes__dn_number` ON `delivery_notes` (`dn_number`);

CREATE TABLE IF NOT EXISTS `departments` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `branch_id` INTEGER DEFAULT NULL,
  `plant_id` INTEGER DEFAULT NULL,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `parent_id` INTEGER DEFAULT NULL,
  `manager_id` INTEGER DEFAULT NULL,
  `cost_center` varchar(20) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS `designations` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `name` varchar(100) NOT NULL,
  `grade` varchar(20) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
, `created_by` INTEGER DEFAULT NULL, `is_deleted` INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS `document_releases` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `doc_type` varchar(30) NOT NULL,
  `doc_id` INTEGER NOT NULL,
  `doc_number` varchar(30) DEFAULT NULL,
  `doc_amount` decimal(18,2) DEFAULT NULL,
  `strategy_id` INTEGER NOT NULL,
  `current_level` INTEGER DEFAULT 1,
  `status` varchar(20) DEFAULT 'PENDING',
  `requested_by` INTEGER NOT NULL,
  `requested_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `fully_released_at` TEXT DEFAULT NULL
, `created_by` INTEGER DEFAULT NULL);
CREATE INDEX IF NOT EXISTS `document_releases__doc_type_id` ON `document_releases` (`doc_type`, `doc_id`);

CREATE TABLE IF NOT EXISTS `employee_salaries` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `employee_id` INTEGER NOT NULL,
  `structure_id` INTEGER NOT NULL,
  `basic_salary` decimal(15,2) NOT NULL,
  `effective_from` date NOT NULL,
  `effective_to` date DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `employees` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `branch_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `designation_id` INTEGER DEFAULT NULL,
  `employee_code` varchar(30) NOT NULL,
  `first_name` varchar(100) NOT NULL,
  `last_name` varchar(100) NOT NULL,
  `date_of_birth` date DEFAULT NULL,
  `gender` char(1) DEFAULT NULL,
  `email` varchar(200) DEFAULT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `pan_number` varchar(20) DEFAULT NULL,
  `aadhaar_number` varchar(20) DEFAULT NULL,
  `joining_date` date NOT NULL,
  `employment_type` varchar(30) DEFAULT 'PERMANENT',
  `status` varchar(20) DEFAULT 'ACTIVE',
  `bank_account` varchar(50) DEFAULT NULL,
  `bank_ifsc` varchar(20) DEFAULT NULL,
  `bank_name` varchar(100) DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
, `category_id` INTEGER DEFAULT NULL, `unit_id` INTEGER DEFAULT NULL, `created_by` INTEGER DEFAULT NULL, `updated_by` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `employees__employee_code` ON `employees` (`employee_code`);
CREATE INDEX IF NOT EXISTS `employees__idx_status` ON `employees` (`status`);
CREATE INDEX IF NOT EXISTS `employees__idx_dept` ON `employees` (`department_id`);

CREATE TABLE IF NOT EXISTS erp_csrf_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
, `used` INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS `eway_bills` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `doc_type` varchar(20) NOT NULL,
  `doc_id` INTEGER NOT NULL,
  `eway_bill_no` varchar(50) NOT NULL,
  `eway_bill_date` TEXT DEFAULT NULL,
  `valid_upto` TEXT DEFAULT NULL,
  `status` varchar(20) DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS `factory_categories` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `factory_categories__uq_company_code` ON `factory_categories` (`company_id`, `code`);
CREATE INDEX IF NOT EXISTS `factory_categories__company_id` ON `factory_categories` (`company_id`);

CREATE TABLE IF NOT EXISTS fin_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INT NOT NULL DEFAULT 1,
    note_number VARCHAR(30) NOT NULL,
    note_type VARCHAR(2) NOT NULL,
    note_date date NOT NULL,
    party_id INT NOT NULL,
    ref_inv_id INT NULL,
    ref_inv_number VARCHAR(40) NULL,
    reason VARCHAR(255),
    base_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
    cgst_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
    sgst_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
    igst_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
    total_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
    gst_rate DECIMAL(5,2) DEFAULT 0,
    hsn_code VARCHAR(20) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'POSTED',
    je_id INT NULL,
    offset_key VARCHAR(20) NULL,
    ret_type VARCHAR(20) NULL,
    ret_id INT NULL,
    created_by INT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_fin_note ON fin_notes (company_id, note_number);
CREATE INDEX IF NOT EXISTS idx_fin_note_party ON fin_notes (party_id);

CREATE TABLE IF NOT EXISTS `fiscal_years` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `name` varchar(50) NOT NULL,
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `is_closed` INTEGER DEFAULT 0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `fx_rates` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `from_currency` varchar(5) NOT NULL,
  `to_currency` varchar(5) NOT NULL,
  `rate_date` date NOT NULL,
  `rate` decimal(12,6) NOT NULL DEFAULT 1.0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `gl_determination` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `event_type` varchar(50) NOT NULL,
  `account_id` INTEGER NOT NULL,
  `sub_key` varchar(50) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS `gl_determination__event` ON `gl_determination` (`company_id`, `event_type`);

CREATE TABLE IF NOT EXISTS `goods_receipt_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `grn_id` INTEGER NOT NULL,
  `po_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_received` decimal(15,3) NOT NULL,
  `qty_accepted` decimal(15,3) DEFAULT 0.000,
  `qty_rejected` decimal(15,3) DEFAULT 0.000,
  `unit_price` decimal(18,4) DEFAULT NULL,
  `batch_number` varchar(50) DEFAULT NULL,
  `expiry_date` date DEFAULT NULL
, `qty` decimal(15,3) DEFAULT 0.0);

CREATE TABLE IF NOT EXISTS `goods_receipts` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `grn_number` varchar(30) NOT NULL,
  `grn_date` date NOT NULL,
  `po_id` INTEGER DEFAULT NULL,
  `supplier_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `movement_type` varchar(10) DEFAULT '101',
  `dc_number` varchar(50) DEFAULT NULL,
  `vehicle_num` varchar(20) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `qc_required` INTEGER DEFAULT 0,
  `posted_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `branch_id` INTEGER DEFAULT NULL, `inventory_account_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `goods_receipts__grn_number` ON `goods_receipts` (`grn_number`);

CREATE TABLE IF NOT EXISTS `grn_deductions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `grn_id` INTEGER NOT NULL,
  `item_id` INTEGER DEFAULT NULL,
  `deduction_type` varchar(50) NOT NULL,
  `calc_mode` varchar(20) DEFAULT 'AMOUNT',
  `qty_received` decimal(15,3) DEFAULT NULL,
  `deducted_qty` decimal(15,3) DEFAULT 0.0,
  `deducted_amount` decimal(18,2) DEFAULT 0.0,
  `reason` TEXT DEFAULT '',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `account_id` INTEGER DEFAULT NULL, `key_code` varchar(50) DEFAULT '', `branch_id` INTEGER DEFAULT NULL, `grn_line_id` INTEGER DEFAULT NULL, `supplier_id` INTEGER DEFAULT NULL, `input_value` decimal(15,3) DEFAULT 0.0, `base_amount` decimal(18,2) DEFAULT 0.0, `remarks` TEXT DEFAULT '', `created_by` INTEGER DEFAULT NULL);
CREATE INDEX IF NOT EXISTS `grn_deductions__grn` ON `grn_deductions` (`grn_id`);

CREATE TABLE IF NOT EXISTS `gst_transactions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `trans_type` varchar(20) DEFAULT NULL,
  `doc_type` varchar(20) DEFAULT NULL,
  `doc_id` INTEGER DEFAULT NULL,
  `doc_number` varchar(30) DEFAULT NULL,
  `doc_date` date DEFAULT NULL,
  `party_gstin` varchar(20) DEFAULT NULL,
  `taxable_amt` decimal(18,2) DEFAULT NULL,
  `cgst` decimal(18,2) DEFAULT 0.00,
  `sgst` decimal(18,2) DEFAULT 0.00,
  `igst` decimal(18,2) DEFAULT 0.00,
  `total_tax` decimal(18,2) DEFAULT 0.00
, `source_key` varchar(100) DEFAULT '', `direction` varchar(20) DEFAULT 'INWARD');

CREATE TABLE IF NOT EXISTS `gstr2b_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `return_period` varchar(10) NOT NULL,
  `supplier_gstin` varchar(20) NOT NULL,
  `invoice_num` varchar(50) NOT NULL,
  `invoice_date` date NOT NULL,
  `invoice_type` varchar(20) DEFAULT 'R',
  `taxable_val` decimal(18,2) DEFAULT 0.0,
  `igst` decimal(18,2) DEFAULT 0.0,
  `cgst` decimal(18,2) DEFAULT 0.0,
  `sgst` decimal(18,2) DEFAULT 0.0,
  `itc_available` varchar(1) DEFAULT 'Y',
  `matched_invoice_id` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `inspection_characteristics` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `plan_id` INTEGER NOT NULL,
  `char_name` varchar(100) NOT NULL,
  `char_type` varchar(20) DEFAULT 'QUANTITATIVE',
  `uom_id` INTEGER DEFAULT NULL,
  `lower_limit` decimal(15,4) DEFAULT NULL,
  `upper_limit` decimal(15,4) DEFAULT NULL,
  `target_val` decimal(15,4) DEFAULT NULL,
  `is_critical` INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS `inspection_lots` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `lot_number` varchar(30) NOT NULL,
  `lot_date` date NOT NULL,
  `origin_type` varchar(30) DEFAULT NULL,
  `origin_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `plan_id` INTEGER DEFAULT NULL,
  `qty` decimal(15,3) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN',
  `result` varchar(20) DEFAULT NULL,
  `inspector_id` INTEGER DEFAULT NULL,
  `inspected_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `remarks` TEXT DEFAULT '', `reject_reason` TEXT DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS `inspection_lots__lot_number` ON `inspection_lots` (`lot_number`);

CREATE TABLE IF NOT EXISTS `inspection_plans` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `item_id` INTEGER NOT NULL,
  `plan_number` varchar(30) NOT NULL,
  `description` varchar(200) DEFAULT '',
  `usage` varchar(30) DEFAULT '',
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `inspection_plans__num` ON `inspection_plans` (`plan_number`);

CREATE TABLE IF NOT EXISTS `inspection_results` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `lot_id` INTEGER NOT NULL,
  `char_id` INTEGER NOT NULL,
  `result_val` decimal(15,4) DEFAULT NULL,
  `result_text` varchar(100) DEFAULT NULL,
  `is_pass` INTEGER DEFAULT NULL,
  `remarks` text DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `internal_message_recipients` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `message_id` INTEGER NOT NULL,
  `recipient_id` INTEGER NOT NULL,
  `is_read` INTEGER DEFAULT 0,
  `read_at` TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `internal_messages` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `sender_id` INTEGER NOT NULL,
  `subject` varchar(200) DEFAULT '',
  `body` TEXT DEFAULT '',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS item_uom_conversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    alt_uom_id INTEGER NOT NULL,
    factor REAL NOT NULL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(item_id, alt_uom_id)
);

CREATE TABLE IF NOT EXISTS `items` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `material_group_id` INTEGER DEFAULT NULL,
  `item_code` varchar(50) NOT NULL,
  `item_name` varchar(200) NOT NULL,
  `description` text DEFAULT NULL,
  `item_type` varchar(20) DEFAULT 'MATERIAL',
  `item_category` varchar(20) DEFAULT 'RAW',
  `base_uom_id` INTEGER NOT NULL,
  `hsn_code` varchar(20) DEFAULT NULL,
  `gst_rate` decimal(5,2) DEFAULT 0.00,
  `valuation_method` varchar(20) DEFAULT 'MOVING_AVG',
  `standard_price` decimal(18,4) DEFAULT 0.0000,
  `moving_avg_price` decimal(18,4) DEFAULT 0.0000,
  `min_stock` decimal(15,3) DEFAULT 0.000,
  `max_stock` decimal(15,3) DEFAULT 0.000,
  `reorder_point` decimal(15,3) DEFAULT 0.000,
  `lead_time_days` INTEGER DEFAULT 0,
  `lot_size` decimal(15,3) DEFAULT 1.000,
  `qc_required` INTEGER DEFAULT 0,
  `batch_managed` INTEGER DEFAULT 0,
  `shelf_life_days` INTEGER DEFAULT 0,
  `gross_weight` decimal(15,3) DEFAULT 0.000,
  `manufacturer` varchar(100) DEFAULT NULL,
  `created_by` INTEGER DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
, `block_status` varchar(30) DEFAULT '', `item_id` INTEGER DEFAULT NULL, `unit_price` decimal(18,4) DEFAULT 0.0, `discount_pct` decimal(5,2) DEFAULT 0.0);
CREATE UNIQUE INDEX IF NOT EXISTS `items__item_code` ON `items` (`item_code`);
CREATE INDEX IF NOT EXISTS `items__idx_code` ON `items` (`item_code`);
CREATE INDEX IF NOT EXISTS `items__idx_cat` ON `items` (`item_category`);

CREATE TABLE IF NOT EXISTS `journal_entries` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `entry_number` varchar(30) NOT NULL,
  `entry_date` date NOT NULL,
  `entry_type` varchar(20) DEFAULT 'JOURNAL',
  `source_module` varchar(30) DEFAULT NULL,
  `source_id` INTEGER DEFAULT NULL,
  `narration` text DEFAULT NULL,
  `total_debit` decimal(18,2) DEFAULT 0.00,
  `total_credit` decimal(18,2) DEFAULT 0.00,
  `status` varchar(20) DEFAULT 'POSTED',
  `posted_by` INTEGER DEFAULT NULL,
  `posted_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `created_by` INTEGER DEFAULT NULL, `fiscal_year_id` INTEGER DEFAULT NULL, `project_id` INTEGER DEFAULT NULL, `wbs_element_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `journal_entries__entry_number` ON `journal_entries` (`entry_number`);
CREATE INDEX IF NOT EXISTS `journal_entries__idx_date` ON `journal_entries` (`entry_date`);

CREATE TABLE IF NOT EXISTS `journal_entry_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `je_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `account_id` INTEGER NOT NULL,
  `debit_amount` decimal(18,2) DEFAULT 0.00,
  `credit_amount` decimal(18,2) DEFAULT 0.00,
  `narration` varchar(300) DEFAULT NULL
, `party_type` varchar(20) DEFAULT '', `party_id` INTEGER DEFAULT NULL);
CREATE INDEX IF NOT EXISTS `journal_entry_lines__idx_account` ON `journal_entry_lines` (`account_id`);

CREATE TABLE IF NOT EXISTS `leave_requests` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `employee_id` INTEGER NOT NULL,
  `leave_type_id` INTEGER NOT NULL,
  `from_date` date NOT NULL,
  `to_date` date NOT NULL,
  `num_days` decimal(5,1) DEFAULT NULL,
  `reason` text DEFAULT NULL,
  `status` varchar(20) DEFAULT 'PENDING',
  `approved_by` INTEGER DEFAULT NULL,
  `approved_at` TEXT DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `leave_types` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `days_per_year` decimal(5,1) DEFAULT NULL,
  `is_paid` INTEGER DEFAULT 1,
  `is_carry_forward` INTEGER DEFAULT 0,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `maintenance_orders` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `mo_number` varchar(30) NOT NULL,
  `mo_date` date NOT NULL,
  `asset_id` INTEGER NOT NULL,
  `mo_type` varchar(20) DEFAULT 'CM',
  `priority` varchar(20) DEFAULT 'MEDIUM',
  `description` text DEFAULT NULL,
  `planned_start` date DEFAULT NULL,
  `planned_end` date DEFAULT NULL,
  `actual_start` TEXT DEFAULT NULL,
  `actual_end` TEXT DEFAULT NULL,
  `assigned_to` INTEGER DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN',
  `material_cost` decimal(18,2) DEFAULT 0.00,
  `labor_cost` decimal(18,2) DEFAULT 0.00,
  `total_cost` decimal(18,2) DEFAULT 0.00,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `maintenance_orders__mo_number` ON `maintenance_orders` (`mo_number`);

CREATE TABLE IF NOT EXISTS `maintenance_plans` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `asset_id` INTEGER NOT NULL,
  `plan_name` varchar(100) DEFAULT NULL,
  `frequency_type` varchar(20) DEFAULT NULL,
  `frequency` INTEGER DEFAULT 1,
  `last_done` date DEFAULT NULL,
  `next_due` date DEFAULT NULL,
  `assigned_to` INTEGER DEFAULT NULL,
  `checklist` text DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `master_suppliers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `supplier_code` varchar(30) DEFAULT '',
  `supplier_name` varchar(200) NOT NULL,
  `name` varchar(200) DEFAULT '',
  `gstin` varchar(20) DEFAULT '',
  `pan` varchar(20) DEFAULT '',
  `pan_number` varchar(20) DEFAULT '',
  `email` varchar(200) DEFAULT '',
  `phone` varchar(30) DEFAULT '',
  `address` TEXT DEFAULT '',
  `city` varchar(100) DEFAULT '',
  `state` varchar(100) DEFAULT '',
  `pincode` varchar(10) DEFAULT '',
  `country` varchar(100) DEFAULT 'India',
  `payment_terms` varchar(50) DEFAULT '',
  `currency_code` varchar(10) DEFAULT 'INR',
  `credit_days` INTEGER DEFAULT 30,
  `bank_name` varchar(100) DEFAULT '',
  `bank_account` varchar(50) DEFAULT '',
  `bank_ifsc` varchar(20) DEFAULT '',
  `bank_branch` varchar(100) DEFAULT '',
  `tds_applicable` INTEGER DEFAULT 0,
  `tds_section` varchar(20) DEFAULT '',
  `tds_rate` REAL DEFAULT 0.0,
  `msme_registered` INTEGER DEFAULT 0,
  `msme_number` varchar(50) DEFAULT '',
  `remarks` TEXT DEFAULT '',
  `status` varchar(20) DEFAULT 'ACTIVE',
  `is_active` INTEGER DEFAULT 1,
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `updated_by` INTEGER DEFAULT NULL,
  `updated_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS `master_suppliers__code` ON `master_suppliers` (`supplier_code`);
CREATE INDEX IF NOT EXISTS `master_suppliers__company` ON `master_suppliers` (`company_id`);

CREATE TABLE IF NOT EXISTS `material_groups` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `is_active` INTEGER DEFAULT 1
, `inventory_account_id` INTEGER DEFAULT NULL);

CREATE TABLE IF NOT EXISTS `non_conformances` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `ncr_number` varchar(30) NOT NULL,
  `ncr_date` date NOT NULL,
  `lot_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `source` varchar(30) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `qty_affected` decimal(15,3) DEFAULT NULL,
  `severity` varchar(20) DEFAULT 'MINOR',
  `disposition` varchar(30) DEFAULT NULL,
  `root_cause` text DEFAULT NULL,
  `corrective_action` text DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN',
  `closed_by` INTEGER DEFAULT NULL,
  `closed_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `project_id` INTEGER DEFAULT NULL, `wbs_element_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `non_conformances__ncr_number` ON `non_conformances` (`ncr_number`);

CREATE TABLE IF NOT EXISTS `notifications` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `user_id` INTEGER NOT NULL,
  `title` varchar(200) DEFAULT NULL,
  `message` text DEFAULT NULL,
  `type` varchar(20) DEFAULT 'INFO',
  `link` varchar(300) DEFAULT NULL,
  `is_read` INTEGER DEFAULT 0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `notifications__idx_user_read` ON `notifications` (`user_id`, `is_read`);

CREATE TABLE IF NOT EXISTS `number_series` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `doc_type` varchar(30) NOT NULL,
  `prefix` varchar(10) DEFAULT NULL,
  `current_num` INTEGER DEFAULT 0,
  `padding` INTEGER DEFAULT 7,
  `fiscal_year` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `number_series__uniq_series` ON `number_series` (`company_id`, `doc_type`, `fiscal_year`);

CREATE TABLE IF NOT EXISTS `options` (`option_name` varchar(191) PRIMARY KEY, `option_value` longtext);

CREATE TABLE IF NOT EXISTS `payment_allocations` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `payment_id` INTEGER NOT NULL,
  `inv_type` varchar(20) DEFAULT NULL,
  `inv_id` INTEGER DEFAULT NULL,
  `inv_number` varchar(30) DEFAULT NULL,
  `allocated_amt` decimal(18,2) DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `payments` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `payment_number` varchar(30) NOT NULL,
  `payment_date` date NOT NULL,
  `payment_type` varchar(30) NOT NULL,
  `party_type` varchar(20) DEFAULT NULL,
  `party_id` INTEGER DEFAULT NULL,
  `payment_mode` varchar(20) DEFAULT NULL,
  `cheque_number` varchar(30) DEFAULT NULL,
  `cheque_date` date DEFAULT NULL,
  `amount` decimal(18,2) NOT NULL,
  `bank_charges` decimal(18,2) DEFAULT 0.00,
  `tds_amount` decimal(18,2) DEFAULT 0.00,
  `net_amount` decimal(18,2) DEFAULT NULL,
  `reference` varchar(100) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'CONFIRMED',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `bank_account_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `payments__payment_number` ON `payments` (`payment_number`);

CREATE TABLE IF NOT EXISTS `payroll_details` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `payroll_run_id` INTEGER NOT NULL,
  `employee_id` INTEGER NOT NULL,
  `component_id` INTEGER NOT NULL,
  `amount` decimal(15,2) NOT NULL,
  `comp_type` varchar(20) DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `payroll_runs` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `month_year` varchar(7) NOT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `processed_by` INTEGER DEFAULT NULL,
  `processed_at` TEXT DEFAULT NULL,
  `total_gross` decimal(15,2) DEFAULT 0.00,
  `total_deductions` decimal(15,2) DEFAULT 0.00,
  `total_net` decimal(15,2) DEFAULT 0.00
);
CREATE UNIQUE INDEX IF NOT EXISTS `payroll_runs__uniq_run` ON `payroll_runs` (`company_id`, `month_year`);

CREATE TABLE IF NOT EXISTS `physical_inventory` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `pi_number` varchar(30) NOT NULL,
  `pi_date` date NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `status` varchar(20) DEFAULT 'OPEN',
  `counted_by` INTEGER DEFAULT NULL,
  `approved_by` INTEGER DEFAULT NULL,
  `posted_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `physical_inventory__pi_number` ON `physical_inventory` (`pi_number`);

CREATE TABLE IF NOT EXISTS `physical_inventory_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `pi_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `book_qty` decimal(15,3) DEFAULT 0.000,
  `counted_qty` decimal(15,3) DEFAULT NULL,
  `variance` decimal(15,3) DEFAULT NULL,
  `unit_cost` decimal(18,4) DEFAULT NULL,
  `variance_value` decimal(18,2) DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `plants` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `code` varchar(10) NOT NULL,
  `name` varchar(100) NOT NULL,
  `address` text DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `plants__company_id` ON `plants` (`company_id`);

CREATE TABLE IF NOT EXISTS `price_list_items` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `price_list_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `customer_id` INTEGER DEFAULT NULL,
  `unit_price` decimal(18,4) NOT NULL,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `valid_from` date DEFAULT NULL,
  `valid_to` date DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `price_lists` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `name` varchar(100) NOT NULL,
  `list_type` varchar(10) DEFAULT 'SALES',
  `currency` char(3) DEFAULT 'INR',
  `valid_from` date DEFAULT NULL,
  `valid_to` date DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `production_material_issues` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `wo_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `qty_required` decimal(15,3) DEFAULT NULL,
  `qty_issued` decimal(15,3) NOT NULL,
  `issue_date` date NOT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `production_receipts` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `wo_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `qty_received` decimal(15,3) NOT NULL,
  `qty_rejected` decimal(15,3) DEFAULT 0.000,
  `receipt_date` date NOT NULL,
  `batch_number` varchar(50) DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `project_activities` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `project_id` INTEGER NOT NULL,
  `name` varchar(200) NOT NULL,
  `status` varchar(20) DEFAULT 'PENDING'
);

CREATE TABLE IF NOT EXISTS `project_budget_log` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `project_id` INTEGER NOT NULL,
  `old_budget` decimal(18,2) DEFAULT 0.0,
  `new_budget` decimal(18,2) DEFAULT 0.0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `project_cost_postings` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `project_id` INTEGER NOT NULL,
  `wbs_element_id` INTEGER DEFAULT NULL,
  `doc_type` varchar(20) NOT NULL,
  `doc_id` INTEGER NOT NULL,
  `posting_date` date NOT NULL,
  `amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `description` varchar(255) DEFAULT '',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `project_milestones` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `project_id` INTEGER NOT NULL,
  `name` varchar(200) NOT NULL,
  `due_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'PENDING'
);

CREATE TABLE IF NOT EXISTS `project_team_members` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `project_id` INTEGER NOT NULL,
  `user_id` INTEGER NOT NULL,
  `role` varchar(50) DEFAULT 'MEMBER'
);

CREATE TABLE IF NOT EXISTS `project_wbs_elements` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `project_id` INTEGER NOT NULL,
  `code` varchar(30) NOT NULL,
  `name` varchar(200) NOT NULL,
  `budget` decimal(18,2) DEFAULT 0.0,
  `status` varchar(20) DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS `projects` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(30) NOT NULL,
  `name` varchar(200) NOT NULL,
  `description` TEXT DEFAULT '',
  `start_date` date DEFAULT NULL,
  `end_date` date DEFAULT NULL,
  `budget` decimal(18,2) DEFAULT 0.0,
  `status` varchar(20) DEFAULT 'PLANNED',
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `project_code` varchar(30) DEFAULT '', `project_name` varchar(200) DEFAULT '', `planned_budget` decimal(18,2) DEFAULT 0.0);
CREATE UNIQUE INDEX IF NOT EXISTS `projects__code` ON `projects` (`company_id`, `code`);

CREATE TABLE IF NOT EXISTS `purchase_order_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `po_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `pr_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_ordered` decimal(15,3) NOT NULL,
  `qty_received` decimal(15,3) DEFAULT 0.000,
  `qty_invoiced` decimal(15,3) DEFAULT 0.000,
  `unit_price` decimal(18,4) NOT NULL,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `gst_rate` decimal(5,2) DEFAULT 0.00,
  `line_total` decimal(18,2) NOT NULL,
  `delivery_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN'
, `base_price` decimal(18,4) DEFAULT 0.0, `gst_amount` decimal(18,2) DEFAULT 0.0, `remarks` TEXT DEFAULT '', `qty_pending` decimal(15,3) DEFAULT NULL, `supplier_notified_at` TEXT DEFAULT NULL);

CREATE TABLE IF NOT EXISTS `purchase_orders` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `po_number` varchar(30) NOT NULL,
  `po_date` date NOT NULL,
  `supplier_id` INTEGER NOT NULL,
  `currency_code` char(3) DEFAULT 'INR',
  `payment_terms` varchar(50) DEFAULT NULL,
  `delivery_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `release_status` varchar(20) DEFAULT 'NOT_RELEASED',
  `supplier_notified_at` TEXT DEFAULT NULL,
  `release_id` INTEGER DEFAULT NULL,
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `discount_amount` decimal(18,2) DEFAULT 0.00,
  `round_off` decimal(10,2) DEFAULT 0.00,
  `grand_total` decimal(18,2) DEFAULT 0.00,
  `remarks` text DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `created_by` INTEGER DEFAULT NULL, `taxable_total` decimal(18,2) DEFAULT 0.0, `gst_total` decimal(18,2) DEFAULT 0.0, `doc_token` varchar(64) DEFAULT NULL, `branch_id` INTEGER DEFAULT NULL, `project_id` INTEGER DEFAULT NULL, `wbs_element_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `purchase_orders__po_number` ON `purchase_orders` (`po_number`);
CREATE INDEX IF NOT EXISTS `purchase_orders__idx_status` ON `purchase_orders` (`status`);
CREATE INDEX IF NOT EXISTS `purchase_orders__idx_supplier` ON `purchase_orders` (`supplier_id`);

CREATE TABLE IF NOT EXISTS `purchase_requisition_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `pr_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_requested` decimal(15,3) NOT NULL,
  `qty_approved` decimal(15,3) DEFAULT NULL,
  `est_price` decimal(18,4) DEFAULT 0.0000,
  `required_by` date DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS `purchase_requisitions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `pr_number` varchar(30) NOT NULL,
  `pr_date` date NOT NULL,
  `pr_type` varchar(10) DEFAULT 'MATERIAL',
  `dept_id` INTEGER DEFAULT NULL,
  `requested_by` INTEGER NOT NULL,
  `required_by` date DEFAULT NULL,
  `purpose` text DEFAULT NULL,
  `status` varchar(20) DEFAULT 'SUBMITTED',
  `release_id` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `is_deleted` INTEGER DEFAULT 0, `branch_id` INTEGER DEFAULT NULL, `source_wo_id` INTEGER DEFAULT NULL, `project_id` INTEGER DEFAULT NULL, `wbs_element_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `purchase_requisitions__pr_number` ON `purchase_requisitions` (`pr_number`);

CREATE TABLE IF NOT EXISTS `purchase_return_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `return_id` INTEGER NOT NULL,
  `grn_line_id` INTEGER NOT NULL,
  `po_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty` decimal(15,3) NOT NULL,
  `unit_price` decimal(18,4) NOT NULL DEFAULT 0,
  `gst_rate` decimal(5,2) NOT NULL DEFAULT 0,
  `line_value` decimal(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS `purchase_return_lines__idx_ret` ON `purchase_return_lines` (`return_id`);
CREATE INDEX IF NOT EXISTS `purchase_return_lines__idx_grnline` ON `purchase_return_lines` (`grn_line_id`);

CREATE TABLE IF NOT EXISTS `purchase_returns` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `return_number` varchar(30) NOT NULL,
  `return_date` date NOT NULL,
  `grn_id` INTEGER NOT NULL,
  `po_id` INTEGER DEFAULT NULL,
  `supplier_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `stock_doc_id` INTEGER DEFAULT NULL,
  `reason_code` varchar(20) NOT NULL DEFAULT 'DEFECTIVE',
  `reason` varchar(255) DEFAULT NULL,
  `base_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `tax_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `total_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `debit_note_id` INTEGER DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'POSTED',
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `purchase_returns__uniq_prn` ON `purchase_returns` (`company_id`, `return_number`);
CREATE INDEX IF NOT EXISTS `purchase_returns__idx_grn` ON `purchase_returns` (`grn_id`);
CREATE INDEX IF NOT EXISTS `purchase_returns__idx_sup` ON `purchase_returns` (`supplier_id`);

CREATE TABLE IF NOT EXISTS `quotation_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `quot_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty` decimal(15,3) NOT NULL,
  `unit_price` decimal(18,4) NOT NULL,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `gst_rate` decimal(5,2) DEFAULT 0.00,
  `line_total` decimal(18,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS `quotation_negotiations` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `quot_id` INTEGER NOT NULL,
  `round_no` INTEGER NOT NULL,
  `actor` varchar(10) NOT NULL,
  `action_type` varchar(20) NOT NULL DEFAULT 'PROPOSE',
  `lines_json` longtext DEFAULT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `valid_until` date DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `grand_total` decimal(18,2) DEFAULT 0.00,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `quotation_negotiations__idx_quot` ON `quotation_negotiations` (`quot_id`);

CREATE TABLE IF NOT EXISTS `quotations` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `quot_number` varchar(30) NOT NULL,
  `public_token` varchar(64) DEFAULT NULL,
  `quot_date` date NOT NULL,
  `valid_until` date DEFAULT NULL,
  `customer_id` INTEGER NOT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `sent_at` TEXT DEFAULT NULL,
  `accepted_at` TEXT DEFAULT NULL,
  `negotiation_round` INTEGER DEFAULT 0,
  `grand_total` decimal(18,2) DEFAULT 0.00,
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `converted_so_id` INTEGER DEFAULT NULL,
  `customer_email` varchar(200) DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `source` varchar(20) DEFAULT 'MANUAL');
CREATE UNIQUE INDEX IF NOT EXISTS `quotations__quot_number` ON `quotations` (`quot_number`);
CREATE UNIQUE INDEX IF NOT EXISTS `quotations__uq_quot_public_token` ON `quotations` (`public_token`);

CREATE TABLE IF NOT EXISTS `release_actions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `release_id` INTEGER NOT NULL,
  `level_num` INTEGER NOT NULL,
  `action` varchar(20) NOT NULL,
  `action_by` INTEGER NOT NULL,
  `action_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `remarks` text DEFAULT NULL
, `doc_type` varchar(20) DEFAULT '', `doc_id` INTEGER DEFAULT NULL, `action_date` TEXT DEFAULT NULL);

CREATE TABLE IF NOT EXISTS `release_levels` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `strategy_id` INTEGER NOT NULL,
  `level_num` INTEGER NOT NULL,
  `level_code` varchar(10) DEFAULT NULL,
  `description` varchar(100) DEFAULT NULL,
  `role_id` INTEGER NOT NULL,
  `amount_from` decimal(18,2) DEFAULT 0.00,
  `amount_to` decimal(18,2) DEFAULT 99999999.00
, `sequence_no` INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS `release_strategies` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `doc_type` varchar(30) NOT NULL,
  `strategy_code` varchar(20) NOT NULL,
  `description` varchar(200) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
, `amount_from` decimal(18,2) DEFAULT 0.0, `amount_to` decimal(18,2) DEFAULT 0.0);

CREATE TABLE IF NOT EXISTS `rfq_bid_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `bid_id` INTEGER NOT NULL,
  `rfq_line_id` INTEGER NOT NULL,
  `unit_price` decimal(18,4) NOT NULL DEFAULT 0.0,
  `tax_rate` decimal(5,2) DEFAULT 0.0,
  `delivery_days` INTEGER DEFAULT 0,
  `remarks` TEXT DEFAULT '',
  `rank_position` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `gst_rate` decimal(5,2) DEFAULT 0.0, `discount_pct` decimal(5,2) DEFAULT 0.0, `base_price` decimal(18,4) DEFAULT 0.0, `gst_amount` decimal(18,2) DEFAULT 0.0, `line_total` decimal(18,2) DEFAULT 0.0);
CREATE INDEX IF NOT EXISTS `rfq_bid_lines__bid` ON `rfq_bid_lines` (`bid_id`);

CREATE TABLE IF NOT EXISTS `rfq_bids` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `rfq_id` INTEGER NOT NULL,
  `supplier_id` INTEGER NOT NULL,
  `bid_date` date DEFAULT NULL,
  `currency_code` varchar(10) DEFAULT 'INR',
  `delivery_days` INTEGER DEFAULT 0,
  `payment_terms` varchar(100) DEFAULT '',
  `remarks` TEXT DEFAULT '',
  `bid_total` decimal(18,2) DEFAULT 0.0,
  `status` varchar(20) DEFAULT 'SUBMITTED',
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `validity_days` INTEGER DEFAULT 30);
CREATE INDEX IF NOT EXISTS `rfq_bids__rfq_supp` ON `rfq_bids` (`rfq_id`, `supplier_id`);

CREATE TABLE IF NOT EXISTS `rfq_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `rfq_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `pr_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty` decimal(15,3) NOT NULL
, `qty_required` decimal(15,3) DEFAULT NULL, `remarks` TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS `rfq_negotiations` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `rfq_supplier_id` INTEGER NOT NULL,
  `round_no` INTEGER NOT NULL,
  `actor` varchar(10) NOT NULL,
  `action_type` varchar(20) NOT NULL DEFAULT 'OFFER',
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `channel` varchar(20) DEFAULT NULL, `remarks` varchar(255) DEFAULT '', `created_by` INTEGER DEFAULT NULL);
CREATE INDEX IF NOT EXISTS `rfq_negotiations__idx_rfq_supplier` ON `rfq_negotiations` (`rfq_supplier_id`);

CREATE TABLE IF NOT EXISTS `rfq_quote_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `rfq_supplier_id` INTEGER NOT NULL,
  `rfq_line_id` INTEGER NOT NULL,
  `unit_price` decimal(18,4) DEFAULT 0.0000,
  `delivery_days` INTEGER DEFAULT NULL,
  `remarks` varchar(255) DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `rfq_quote_lines__uq_rfq_supplier_line` ON `rfq_quote_lines` (`rfq_supplier_id`, `rfq_line_id`);

CREATE TABLE IF NOT EXISTS `rfq_suppliers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `rfq_id` INTEGER NOT NULL,
  `supplier_id` INTEGER NOT NULL,
  `token` varchar(64) NOT NULL,
  `email_to` varchar(200) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'SENT',
  `sent_at` TEXT DEFAULT NULL,
  `viewed_at` TEXT DEFAULT NULL,
  `submitted_at` TEXT DEFAULT NULL,
  `total_quoted` decimal(18,2) DEFAULT 0.00,
  `payment_terms` varchar(200) DEFAULT NULL,
  `delivery_period` varchar(100) DEFAULT NULL,
  `incoterms` varchar(20) DEFAULT NULL,
  `certifications` text DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `negotiation_status` varchar(20) DEFAULT 'NONE',
  `negotiation_round` INTEGER DEFAULT 0,
  `negotiation_discount_pct` decimal(5,2) DEFAULT 0.00,
  `negotiated_po_id` INTEGER DEFAULT NULL
, `invited_on` TEXT DEFAULT NULL, `final_discount_pct` decimal(5,2) DEFAULT NULL, `final_amount` decimal(18,2) DEFAULT NULL, `final_remarks` varchar(255) DEFAULT '', `final_set_by` INTEGER DEFAULT NULL, `final_set_at` TEXT DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `rfq_suppliers__token` ON `rfq_suppliers` (`token`);

CREATE TABLE IF NOT EXISTS `rfqs` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `rfq_number` varchar(30) NOT NULL,
  `pr_id` INTEGER NOT NULL,
  `rfq_date` date NOT NULL,
  `due_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN',
  `notes` text DEFAULT NULL,
  `ai_summary` text DEFAULT NULL,
  `ai_summary_at` TEXT DEFAULT NULL,
  `quotes_finalized_at` TEXT DEFAULT NULL,
  `quotes_finalized_by` INTEGER DEFAULT NULL,
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `remarks` TEXT DEFAULT '', `is_deleted` INTEGER DEFAULT 0, `branch_id` INTEGER DEFAULT NULL, `auto_compare_at` TEXT DEFAULT NULL, `auto_award_status` TEXT DEFAULT NULL, `auto_award_at` TEXT DEFAULT NULL, `auto_award_trigger` TEXT DEFAULT NULL, `auto_award_supplier_id` INTEGER DEFAULT NULL, `auto_award_po_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `rfqs__rfq_number` ON `rfqs` (`rfq_number`);

CREATE TABLE IF NOT EXISTS `role_tcodes` (
  `role_id` INTEGER NOT NULL,
  `tcode_id` INTEGER NOT NULL,
  PRIMARY KEY (`role_id`, `tcode_id`)
);

CREATE TABLE IF NOT EXISTS `roles` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(50) NOT NULL,
  `name` varchar(100) NOT NULL,
  `description` text DEFAULT NULL,
  `role_type` varchar(20) DEFAULT 'SINGLE',
  `parent_id` INTEGER DEFAULT NULL,
  `is_system` INTEGER DEFAULT 0,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS `roles__company_id` ON `roles` (`company_id`);

CREATE TABLE IF NOT EXISTS `routings` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `bom_id` INTEGER NOT NULL,
  `sequence` INTEGER NOT NULL,
  `operation` varchar(100) NOT NULL,
  `work_center_id` INTEGER NOT NULL,
  `setup_time_hrs` decimal(8,2) DEFAULT 0.00,
  `run_time_hrs` decimal(8,2) DEFAULT 0.00,
  `labor_count` INTEGER DEFAULT 1,
  `notes` text DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `salary_components` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(30) NOT NULL,
  `name` varchar(100) NOT NULL,
  `comp_type` varchar(20) NOT NULL,
  `is_taxable` INTEGER DEFAULT 1,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `salary_structure_components` (
  `structure_id` INTEGER NOT NULL,
  `component_id` INTEGER NOT NULL,
  `calculation` varchar(30) NOT NULL,
  `amount` decimal(15,2) DEFAULT NULL,
  `percentage` decimal(5,2) DEFAULT NULL,
  PRIMARY KEY (`structure_id`, `component_id`)
);

CREATE TABLE IF NOT EXISTS `salary_structures` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `name` varchar(100) NOT NULL,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `sales_invoice_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `inv_id` INTEGER NOT NULL,
  `so_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty` decimal(15,3) NOT NULL,
  `unit_price` decimal(18,4) NOT NULL,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `hsn_code` varchar(20) DEFAULT NULL,
  `batch_num` varchar(50) DEFAULT NULL,
  `mfg_date` date DEFAULT NULL,
  `exp_date` date DEFAULT NULL,
  `manufacturer` varchar(100) DEFAULT NULL,
  `gst_rate` decimal(5,2) DEFAULT 0.00,
  `cgst_amount` decimal(18,2) DEFAULT 0.00,
  `sgst_amount` decimal(18,2) DEFAULT 0.00,
  `igst_amount` decimal(18,2) DEFAULT 0.00,
  `line_total` decimal(18,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS `sales_invoices` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `inv_number` varchar(30) NOT NULL,
  `inv_date` date NOT NULL,
  `so_id` INTEGER DEFAULT NULL,
  `dn_id` INTEGER DEFAULT NULL,
  `customer_id` INTEGER NOT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `due_date` date DEFAULT NULL,
  `inv_type` varchar(20) DEFAULT 'TAX',
  `base_amount` decimal(18,2) DEFAULT 0.00,
  `discount_amount` decimal(18,2) DEFAULT 0.00,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `round_off` decimal(10,2) DEFAULT 0.00,
  `grand_total` decimal(18,2) DEFAULT 0.00,
  `paid_amount` decimal(18,2) DEFAULT 0.00,
  `due_amount` decimal(18,2) DEFAULT 0.00,
  `status` varchar(20) DEFAULT 'UNPAID',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `sales_invoices__inv_number` ON `sales_invoices` (`inv_number`);
CREATE INDEX IF NOT EXISTS `sales_invoices__idx_status` ON `sales_invoices` (`status`);
CREATE INDEX IF NOT EXISTS `sales_invoices__idx_cust` ON `sales_invoices` (`customer_id`);

CREATE TABLE IF NOT EXISTS `sales_order_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `so_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_ordered` decimal(15,3) NOT NULL,
  `qty_delivered` decimal(15,3) DEFAULT 0.000,
  `qty_invoiced` decimal(15,3) DEFAULT 0.000,
  `unit_price` decimal(18,4) NOT NULL,
  `discount_pct` decimal(5,2) DEFAULT 0.00,
  `gst_rate` decimal(5,2) DEFAULT 0.00,
  `line_total` decimal(18,2) NOT NULL,
  `delivery_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS `sales_orders` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `so_number` varchar(30) NOT NULL,
  `so_date` date NOT NULL,
  `quot_id` INTEGER DEFAULT NULL,
  `customer_id` INTEGER NOT NULL,
  `po_number` varchar(50) DEFAULT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `delivery_date` date DEFAULT NULL,
  `ship_to_addr` text DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `release_status` varchar(20) DEFAULT 'NOT_RELEASED',
  `release_id` INTEGER DEFAULT NULL,
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `grand_total` decimal(18,2) DEFAULT 0.00,
  `remarks` text DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `doc_token` varchar(64) DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `sales_orders__so_number` ON `sales_orders` (`so_number`);
CREATE INDEX IF NOT EXISTS `sales_orders__idx_status` ON `sales_orders` (`status`);
CREATE INDEX IF NOT EXISTS `sales_orders__idx_cust` ON `sales_orders` (`customer_id`);

CREATE TABLE IF NOT EXISTS `sales_return_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `return_id` INTEGER NOT NULL,
  `inv_line_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty` decimal(15,3) NOT NULL,
  `unit_price` decimal(18,4) NOT NULL DEFAULT 0,
  `discount_pct` decimal(5,2) NOT NULL DEFAULT 0,
  `gst_rate` decimal(5,2) NOT NULL DEFAULT 0,
  `cgst_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `sgst_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `igst_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `line_base` decimal(18,2) NOT NULL DEFAULT 0,
  `unit_cost` decimal(18,4) NOT NULL DEFAULT 0
, `grn_line_id` INTEGER DEFAULT NULL);
CREATE INDEX IF NOT EXISTS `sales_return_lines__idx_ret` ON `sales_return_lines` (`return_id`);
CREATE INDEX IF NOT EXISTS `sales_return_lines__idx_invline` ON `sales_return_lines` (`inv_line_id`);

CREATE TABLE IF NOT EXISTS `sales_returns` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `return_number` varchar(30) NOT NULL,
  `return_date` date NOT NULL,
  `invoice_id` INTEGER NOT NULL,
  `customer_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER DEFAULT NULL,
  `disposition` varchar(10) NOT NULL DEFAULT 'RESTOCK',
  `reason_code` varchar(20) NOT NULL DEFAULT 'DEFECTIVE',
  `reason` varchar(255) DEFAULT NULL,
  `base_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `tax_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `total_amount` decimal(18,2) NOT NULL DEFAULT 0,
  `credit_note_id` INTEGER DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'POSTED',
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `sales_returns__uniq_srn` ON `sales_returns` (`company_id`, `return_number`);
CREATE INDEX IF NOT EXISTS `sales_returns__idx_inv` ON `sales_returns` (`invoice_id`);
CREATE INDEX IF NOT EXISTS `sales_returns__idx_cust` ON `sales_returns` (`customer_id`);

CREATE TABLE IF NOT EXISTS `service_entry_sheet_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `ses_id` INTEGER NOT NULL,
  `line_num` INTEGER NOT NULL,
  `service_item_id` INTEGER DEFAULT NULL,
  `short_text` varchar(200) NOT NULL,
  `quantity` decimal(15,3) NOT NULL DEFAULT 1.000,
  `uom_id` INTEGER DEFAULT NULL,
  `gross_price` decimal(18,4) NOT NULL DEFAULT 0.0000,
  `cost_center_id` INTEGER DEFAULT NULL,
  `line_total` decimal(18,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS `service_entry_sheets` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `ses_number` varchar(30) NOT NULL,
  `ses_date` date NOT NULL,
  `po_id` INTEGER NOT NULL,
  `po_line_id` INTEGER DEFAULT NULL,
  `supplier_id` INTEGER NOT NULL,
  `short_text` varchar(200) DEFAULT NULL,
  `acct_assgt_cat` varchar(5) DEFAULT 'K',
  `cost_center_id` INTEGER DEFAULT NULL,
  `external_number` varchar(50) DEFAULT NULL,
  `service_location` varchar(100) DEFAULT NULL,
  `period_from` date DEFAULT NULL,
  `period_to` date DEFAULT NULL,
  `price_ref_date` date DEFAULT NULL,
  `currency_code` char(3) DEFAULT 'INR',
  `total_amount` decimal(18,2) DEFAULT 0.00,
  `status` varchar(20) DEFAULT 'DRAFT',
  `returns_indicator` INTEGER DEFAULT 0,
  `acceptance_doc_number` varchar(30) DEFAULT NULL,
  `accepted_by` INTEGER DEFAULT NULL,
  `accepted_at` TEXT DEFAULT NULL,
  `quality_of_service` INTEGER DEFAULT NULL,
  `service_timeliness` INTEGER DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `created_by` INTEGER DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `service_entry_sheets__ses_number` ON `service_entry_sheets` (`ses_number`);
CREATE INDEX IF NOT EXISTS `service_entry_sheets__idx_status` ON `service_entry_sheets` (`status`);
CREATE INDEX IF NOT EXISTS `service_entry_sheets__idx_po` ON `service_entry_sheets` (`po_id`);

CREATE TABLE IF NOT EXISTS `settings` (`key` varchar(191) PRIMARY KEY, `value` longtext);

CREATE TABLE IF NOT EXISTS `so_deductions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `so_id` INTEGER NOT NULL,
  `deduction_type` varchar(50) NOT NULL,
  `calc_mode` varchar(20) DEFAULT 'AMOUNT',
  `deducted_amount` decimal(18,2) DEFAULT 0.0,
  `reason` TEXT DEFAULT '',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `stock_balances` (
  `item_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `qty_on_hand` decimal(15,3) DEFAULT 0.000,
  `qty_reserved` decimal(15,3) DEFAULT 0.000,
  `avg_cost` decimal(18,4) DEFAULT 0.0000,
  `last_updated` TEXT DEFAULT CURRENT_TIMESTAMP, `company_id` INTEGER DEFAULT 1,
  PRIMARY KEY (`item_id`, `warehouse_id`)
);

CREATE TABLE IF NOT EXISTS `stock_movements` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `movement_date` date NOT NULL,
  `movement_type` varchar(30) NOT NULL,
  `mvt_code` varchar(10) DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER NOT NULL,
  `reference_type` varchar(30) DEFAULT NULL,
  `reference_id` INTEGER DEFAULT NULL,
  `reference_num` varchar(30) DEFAULT NULL,
  `qty_in` decimal(15,3) DEFAULT 0.000,
  `qty_out` decimal(15,3) DEFAULT 0.000,
  `unit_cost` decimal(18,4) DEFAULT NULL,
  `total_value` decimal(18,2) DEFAULT NULL,
  `running_balance` decimal(15,3) DEFAULT NULL,
  `batch_number` varchar(50) DEFAULT NULL,
  `remarks` varchar(300) DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS `stock_movements__idx_item` ON `stock_movements` (`item_id`);
CREATE INDEX IF NOT EXISTS `stock_movements__idx_date` ON `stock_movements` (`movement_date`);

CREATE TABLE IF NOT EXISTS `stock_transfer_lines` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `sto_id` INTEGER NOT NULL,
  `item_id` INTEGER NOT NULL,
  `uom_id` INTEGER NOT NULL,
  `qty_transfer` decimal(15,3) NOT NULL,
  `qty_received` decimal(15,3) DEFAULT 0.000
);

CREATE TABLE IF NOT EXISTS `stock_transfers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `sto_number` varchar(30) NOT NULL,
  `transfer_date` date NOT NULL,
  `from_wh_id` INTEGER NOT NULL,
  `to_wh_id` INTEGER NOT NULL,
  `status` varchar(20) DEFAULT 'DRAFT',
  `transferred_at` TEXT DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `created_by` INTEGER DEFAULT NULL,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `stock_transfers__sto_number` ON `stock_transfers` (`sto_number`);

CREATE TABLE IF NOT EXISTS `supplier_invoices` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `invoice_number` varchar(30) NOT NULL,
  `invoice_date` date NOT NULL,
  `supplier_id` INTEGER NOT NULL,
  `po_id` INTEGER DEFAULT NULL,
  `ref_invoice_num` varchar(50) DEFAULT NULL,
  `base_amount` decimal(18,2) DEFAULT NULL,
  `tax_amount` decimal(18,2) DEFAULT 0.00,
  `tds_amount` decimal(18,2) DEFAULT 0.00,
  `total_amount` decimal(18,2) DEFAULT NULL,
  `paid_amount` decimal(18,2) DEFAULT 0.00,
  `due_amount` decimal(18,2) DEFAULT NULL,
  `due_date` date DEFAULT NULL,
  `status` varchar(20) DEFAULT 'PENDING',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `block_reason` TEXT DEFAULT '', `match_expected` decimal(18,2) DEFAULT 0.0, `cgst_amount` decimal(18,2) DEFAULT 0.0, `sgst_amount` decimal(18,2) DEFAULT 0.0, `igst_amount` decimal(18,2) DEFAULT 0.0, `itc_eligible` INTEGER DEFAULT 1, `reverse_charge` INTEGER DEFAULT 0, `tds_section` varchar(20) DEFAULT '', `attachment_file` varchar(255) DEFAULT NULL, `ai_extracted` INTEGER DEFAULT 0, `created_by` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `supplier_invoices__invoice_number` ON `supplier_invoices` (`invoice_number`);

CREATE TABLE IF NOT EXISTS `suppliers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `supplier_code` varchar(30) NOT NULL,
  `name` varchar(200) NOT NULL,
  `contact_person` varchar(100) DEFAULT NULL,
  `email` varchar(200) DEFAULT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `address` text DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `state` varchar(100) DEFAULT NULL,
  `country` varchar(100) DEFAULT 'India',
  `pincode` varchar(10) DEFAULT NULL,
  `gstin` varchar(20) DEFAULT NULL,
  `pan_number` varchar(20) DEFAULT NULL,
  `payment_terms` varchar(50) DEFAULT NULL,
  `credit_days` INTEGER DEFAULT 30,
  `bank_account` varchar(50) DEFAULT NULL,
  `bank_ifsc` varchar(20) DEFAULT NULL,
  `bank_name` varchar(100) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'ACTIVE',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
, `supplier_name` varchar(200) DEFAULT '', `pan` varchar(20) DEFAULT '', `bank_branch` varchar(100) DEFAULT '', `tds_applicable` INTEGER DEFAULT 0, `tds_section` varchar(20) DEFAULT '', `tds_rate` REAL DEFAULT 0.0, `msme_registered` INTEGER DEFAULT 0, `msme_number` varchar(50) DEFAULT '', `remarks` TEXT DEFAULT '', `is_active` INTEGER DEFAULT 1, `updated_by` INTEGER DEFAULT NULL, `updated_at` TEXT DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `suppliers__supplier_code` ON `suppliers` (`supplier_code`);

CREATE TABLE IF NOT EXISTS `tcode_requests` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `user_id` INTEGER NOT NULL,
  `tcode` varchar(20) NOT NULL,
  `reason` TEXT DEFAULT '',
  `status` varchar(20) DEFAULT 'PENDING',
  `requested_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `reviewed_by` INTEGER DEFAULT NULL,
  `reviewed_at` TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS `tcodes` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `tcode` varchar(20) NOT NULL,
  `module` varchar(30) NOT NULL,
  `description` varchar(200) NOT NULL,
  `php_file` varchar(300) NOT NULL,
  `icon` varchar(50) DEFAULT 'bi-grid',
  `menu_path` varchar(300) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `sort_order` INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS `tcodes__tcode` ON `tcodes` (`tcode`);

CREATE TABLE IF NOT EXISTS `tds_challans` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `challan_num` varchar(50) DEFAULT NULL,
  `bsr_code` varchar(20) DEFAULT NULL,
  `deposit_date` date DEFAULT NULL,
  `total_tds` decimal(18,2) DEFAULT 0.0,
  `interest` decimal(18,2) DEFAULT 0.0,
  `fee` decimal(18,2) DEFAULT 0.0,
  `status` varchar(20) DEFAULT 'DEPOSITED',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `tds_deductions` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `section` varchar(20) NOT NULL,
  `voucher_type` varchar(20) NOT NULL,
  `voucher_id` INTEGER NOT NULL,
  `deductee_type` varchar(20) DEFAULT 'SUPPLIER',
  `deductee_id` INTEGER NOT NULL,
  `pan_number` varchar(20) DEFAULT '',
  `payment_date` date NOT NULL,
  `base_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `tds_rate` decimal(5,2) NOT NULL DEFAULT 0.0,
  `tds_amount` decimal(18,2) NOT NULL DEFAULT 0.0,
  `challan_id` INTEGER DEFAULT NULL,
  `status` varchar(20) DEFAULT 'DEDUCTED',
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
, `source_type` varchar(20) DEFAULT 'INVOICE', `source_id` INTEGER DEFAULT 0, `deduct_date` date DEFAULT NULL, `supplier_id` INTEGER DEFAULT 0, `gross_amount` decimal(18,2) DEFAULT 0.0);

CREATE TABLE IF NOT EXISTS `tds_sections` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `section` varchar(20) NOT NULL,
  `description` varchar(200) DEFAULT '',
  `rate_pct` REAL DEFAULT 0.0,
  `rate_no_pan_pct` REAL DEFAULT 20.0,
  `threshold_amt` REAL DEFAULT 0.0,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `tds_sections__comp_sec` ON `tds_sections` (`company_id`, `section`);

CREATE TABLE IF NOT EXISTS `units_of_measure` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `code` varchar(10) NOT NULL,
  `name` varchar(50) NOT NULL,
  `uom_type` varchar(20) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
, `full_name` varchar(100) DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS `units_of_measure__code` ON `units_of_measure` (`code`);

CREATE TABLE IF NOT EXISTS user_passwords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS user_passwords__idx_user ON user_passwords (user_id);

CREATE TABLE IF NOT EXISTS `user_roles` (
  `user_id` INTEGER NOT NULL,
  `role_id` INTEGER NOT NULL,
  `valid_from` date DEFAULT NULL,
  `valid_to` date DEFAULT NULL,
  `assigned_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `assigned_by` INTEGER DEFAULT NULL,
  PRIMARY KEY (`user_id`, `role_id`)
);

CREATE TABLE IF NOT EXISTS `user_tcode_grants` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `user_id` INTEGER NOT NULL,
  `tcode` varchar(20) NOT NULL,
  `granted_by` INTEGER DEFAULT NULL,
  `granted_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `valid_to` date DEFAULT NULL,
  `request_id` INTEGER DEFAULT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS `user_tcode_grants__uq` ON `user_tcode_grants` (`user_id`, `tcode`);

CREATE TABLE IF NOT EXISTS `users` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `branch_id` INTEGER DEFAULT NULL,
  `employee_id` INTEGER DEFAULT NULL,
  `wp_user_id` INTEGER DEFAULT NULL,
  `username` varchar(100) NOT NULL,
  `email` varchar(200) DEFAULT NULL,
  `full_name` varchar(200) DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS `users__username` ON `users` (`username`);
CREATE INDEX IF NOT EXISTS `users__company_id` ON `users` (`company_id`);
CREATE INDEX IF NOT EXISTS `users__wp_user_id` ON `users` (`wp_user_id`);

CREATE TABLE IF NOT EXISTS `warehouses` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `wh_type` varchar(20) DEFAULT 'GENERAL',
  `address` text DEFAULT NULL,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `work_centers` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `code` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `department_id` INTEGER DEFAULT NULL,
  `capacity_per_hour` decimal(10,2) DEFAULT NULL,
  `cost_per_hour` decimal(10,2) DEFAULT NULL,
  `efficiency_pct` decimal(5,2) DEFAULT 100.00,
  `is_active` INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS `work_orders` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `company_id` INTEGER NOT NULL DEFAULT 1,
  `wo_number` varchar(30) NOT NULL,
  `wo_date` date NOT NULL,
  `so_line_id` INTEGER DEFAULT NULL,
  `item_id` INTEGER NOT NULL,
  `bom_id` INTEGER NOT NULL,
  `warehouse_id` INTEGER DEFAULT NULL,
  `qty_planned` decimal(15,3) NOT NULL,
  `qty_produced` decimal(15,3) DEFAULT 0.000,
  `qty_rejected` decimal(15,3) DEFAULT 0.000,
  `planned_start` date DEFAULT NULL,
  `planned_end` date DEFAULT NULL,
  `actual_start` TEXT DEFAULT NULL,
  `actual_end` TEXT DEFAULT NULL,
  `priority` INTEGER DEFAULT 5,
  `status` varchar(20) DEFAULT 'DRAFT',
  `release_status` varchar(20) DEFAULT 'NOT_RELEASED',
  `release_id` INTEGER DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `created_at` TEXT DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` INTEGER DEFAULT 0,
  `category_id` INTEGER DEFAULT NULL,
  `unit_id` INTEGER DEFAULT NULL,
  `department_id` INTEGER DEFAULT NULL
, `uom_id` INTEGER DEFAULT NULL, `pr_id` INTEGER DEFAULT NULL, `project_id` INTEGER DEFAULT NULL, `wbs_element_id` INTEGER DEFAULT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS `work_orders__wo_number` ON `work_orders` (`wo_number`);
CREATE INDEX IF NOT EXISTS `work_orders__idx_status` ON `work_orders` (`status`);
