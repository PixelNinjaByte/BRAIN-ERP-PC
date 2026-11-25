# Brain ERP — Materials Management (MM) Conversion Checklist
> **Porting Status**: WordPress PHP plugin (`msme-erp/modules/mm`) → Python / FastAPI / SQLite (`Brain ERP`)

---

## Executive Summary
- **Total PHP Source Files**: 27
- **Converted & Implemented**: **26 / 27** (96.3%)
- **Pending Conversion**: **1 / 27** (3.7%) — `material_import.php` (ZMMI)

---

## Detailed Conversion Status by File

| # | Original PHP File (`modules/mm/`) | T-Code | Python Implementation | Status | Description & Capabilities |
|:---:|---|:---:|---|:---:|---|
| 1 | `material_master.php` | `MM01`<br>`MM02`<br>`MM03` | `backend/routers/mm.py`<br>`backend/core/material.py` | [x] **Converted** | Material Master CRUD: auto-numbering, validation, stock breakdown by warehouse, where-used, alternate UOMs, audit change history |
| 2 | `material_create.php` | `MM01` | `backend/routers/mm.py` | [x] **Converted** | Router shim for creation; mapped to `POST /api/mm/materials` |
| 3 | `material_view.php` | `MM03` | `backend/routers/mm.py` | [x] **Converted** | Router shim for display; mapped to `GET /api/mm/materials` & `GET /api/mm/materials/{id}` |
| 4 | `goods_movement.php` | `MIGO` | `backend/routers/migo.py`<br>`backend/core/stock.py` | [x] **Converted** | Goods movements: 101 (GRN against PO), 122 (Return to Vendor), 261 (Issue to WO), 201, 501, moving average cost recalculation |
| 5 | `supplier_invoice.php` | `MR01`<br>`LT01`<br>`VK11` | `backend/routers/supplier_invoice.py` | [x] **Converted** | Supplier invoice verification (MIRO), stock transfer orders (LT01), and condition price lists (VK11) |
| 6 | `material_price.php` | `VK11` | `backend/routers/supplier_invoice.py` | [x] **Converted** | Router shim delegating to `supplier_invoice.py` |
| 7 | `purchase_return.php` | `PRTN` | `backend/routers/purchase_return.py`<br>`backend/core/returns.py` | [x] **Converted** | Return goods to vendor against GRN, inventory reversal, and debit note generation |
| 8 | `po_create.php` | `ME21N` | `backend/routers/po.py` | [x] **Converted** | Create purchase orders against PR or standalone, pricing conditions, delivery schedule |
| 9 | `po_view.php` | `ME23N` | `backend/routers/po.py` | [x] **Converted** | Purchase order list, document display, multi-level release & approval workflow |
| 10 | `po_by_material.php` | `ME2M` | `backend/routers/po.py` | [x] **Converted** | Router shim for purchase orders filtered by material |
| 11 | `pr_full.php` | `ME51N`<br>`ME53N` | `backend/routers/pr.py` | [x] **Converted** | Purchase requisition creation, line approvals, and release strategy workflows |
| 12 | `pr_create.php` | `ME51N` | `backend/routers/pr.py` | [x] **Converted** | Router shim delegating to `pr_full.php` |
| 13 | `pr_view.php` | `ME53N` | `backend/routers/pr.py` | [x] **Converted** | Router shim delegating to `pr_full.php` |
| 14 | `rfq_create.php` | `ME41` | `backend/routers/rfq.py` | [x] **Converted** | Request for Quotation creation from PR / standalone |
| 15 | `rfq_compare.php` | `ME47` | `backend/routers/rfq_compare.py`<br>`backend/routers/rfq.py` | [x] **Converted** | Supplier quotation comparison matrix and evaluation grid |
| 16 | `rfq_public.php` | Portal | `backend/routers/rfq.py` | [x] **Converted** | Token-based public portal for supplier quotation submissions without ERP login |
| 17 | `supplier_create.php` | `XK01`<br>`XK02` | `backend/routers/supplier.py` | [x] **Converted** | Supplier master maintenance with banking details, GSTIN, PAN, and contacts |
| 18 | `supplier_edit.php` | `XK02` | `backend/routers/supplier.py` | [x] **Converted** | Router shim delegating to `supplier_create.php` |
| 19 | `service_entry_full.php` | `ML81N` | `backend/routers/service_entry.py`<br>`backend/core/service_entry.py` | [x] **Converted** | Service Entry Sheet (SES) against service POs with acceptance & GRN posting |
| 20 | `service_entry.php` | `ML81N` | `backend/routers/service_entry.py` | [x] **Converted** | Router shim delegating to `service_entry_full.php` |
| 21 | `material_groups.php` | `OMSF` | `backend/routers/material_groups.py`<br>`backend/routers/mm.py` | [x] **Converted** | Material Groups master (OMSF) with Inventory GL account mappings |
| 22 | `material_reorder.php` | `ZMRP` | `backend/routers/reorder.py`<br>`backend/core/material.py` | [x] **Converted** | Reorder Point stock monitoring report (ZMRP) & automated PR creation |
| 23 | `bid_create.php` | `BID01` | `backend/routers/bid.py` | [x] **Converted** | Formal public tender / bid creation |
| 24 | `bid_view.php` | `BID02` | `backend/routers/bid.py` | [x] **Converted** | Public tender management, submission tracking, and status drilldown |
| 25 | `mm_reports.php` | `ZMMR` | `backend/routers/mm_reports.py` | [x] **Converted** | Procurement follow-up reports, analytics, and supplier delivery tracking |
| 26 | `pending_report.php` | `ME2PN` | `backend/routers/mm_pending_report.py` | [x] **Converted** | MM status report (PR pending PO, PO pending GRN, GRN pending invoice/payment) |
| 27 | `material_import.php` | `ZMMI` | — | [ ] **Pending** | Two-step CSV batch preview & import/export for materials |

---

## Supporting Core Python Modules
- **`backend/core/material.py`**: Field cleaning, duplicate description matching, diff generation, reorder thresholds.
- **`backend/core/stock.py`**: Inventory movements (`post_stock_movement`), GRN processing, moving average price adjustments, inspection lot creation.
- **`backend/core/service_entry.py`**: Service entry sheet validation, line items, and GRN linkage.
- **`backend/core/returns.py`**: Purchase return calculations, stock reversals, and debit note management.
- **`backend/core/finance_engine.py`**: Double-entry journal posting, note creation (`create_note_split`), GST splitting, accounting period verification.
- **`backend/core/util.py`**: Double-submit request guards (`claim_request_id`), currency symbol formatting, system settings lookup.
- **`backend/core/db.py`**: SQLite database abstraction layer (`ErpDB`).
- **`backend/core/auth.py`**: Authentication, session management, and RBAC authorization (`ErpAuth`).
