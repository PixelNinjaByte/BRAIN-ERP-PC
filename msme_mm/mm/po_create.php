<?php
/**
 * MSME ERP — Create Purchase Order
 * T-Code: ME21N
 * Developed by: Prigag Online Pvt. Ltd.
 */

if (empty($tcode)) $tcode = strtoupper($_GET['tcode'] ?? 'ME21N');
Auth::requireTcode(in_array($tcode, ['ME21N','ME51N']) ? $tcode : 'ME21N');

$companyId   = Auth::companyId();
$currentTcode = 'ME21N';
$pageTitle    = 'Create Purchase Order';
$pageTcode    = 'ME21N';
$breadcrumbs  = ['MM', 'Purchasing', 'Create Purchase Order'];

erp_render_page_header();

// Multi-currency: PO currency follows the registered company's currency
// (set on OBY6), not a hardcoded 'INR'.
$companyCurrency = DB::fetchValue("SELECT currency_code FROM companies WHERE id=?", [$companyId]) ?: 'INR';

$error   = '';
$success = '';

// ---- Suppliers, materials, UOMs, pending PRs: same tolerant lookups as MIGO ----
// (helpers.php, v1.15.2). Each one checks the live table columns first and any
// SQL error is shown on screen by the master-data diagnostic banner rather than
// silently producing an empty "No results found" dropdown.
$suppliers = $items = $warehouses = $uoms = $eligiblePRs = [];
$awaitingApprovalPRs = 0; $__otherCoPrs = 0; $__canOrderAhead = false; $__showAllPrs = false;
$supplierLastPrice = [];
try {
    $suppliers  = msme_erp_master_suppliers($companyId);
    $items      = msme_erp_master_items($companyId, 'PURCHASE');
    $uoms       = msme_erp_master_uoms();
    $warehouses = msme_erp_master_warehouses($companyId);
    $__canOrderAhead = Auth::hasRole(['SUPER_ADMIN','PURCHASE_MANAGER']) || current_user_can('manage_options');
    $__showAllPrs    = $__canOrderAhead && !empty($_GET['show_all']);
    $pend       = msme_erp_pending_prs($companyId, $__showAllPrs);
    $eligiblePRs         = $pend['rows'];
    $awaitingApprovalPRs = $pend['awaiting_approval'];
    $__otherCoPrs        = $pend['other_company'];
    $supplierLastPrice   = msme_erp_supplier_last_prices($companyId);
} catch (Throwable $e) {
    error_log('ME21N master data: ' . $e->getMessage());
    $error = 'Could not load master data (suppliers / materials / PRs): ' . $e->getMessage();
}
$uomOptsHtml = '<option value="">UOM</option>';
foreach ($uoms as $__u) { $uomOptsHtml .= '<option value="' . (int)$__u['id'] . '">' . htmlspecialchars((string)$__u['code']) . '</option>'; }
$__coName = (string) DB::fetchValue("SELECT name FROM companies WHERE id=?", [$companyId]);

// If arriving via the "Convert to Purchase Order" button on ME53N, or via
// the PR picker on this page, load that PR's still-open (not yet ordered) lines.
//
// LEFT JOIN (not INNER) to items/units_of_measure: if a PR line's item_id
// or uom_id somehow doesn't resolve (e.g. the item was later deleted, or
// — the one path that creates items outside of MM01 — a Service Request
// line whose auto-created service master failed to attach correctly),
// the line must still show up here for the user to fix, rather than
// silently vanishing while the picker dropdown above still counts it as
// "pending" (that count has no items/uom join at all, so it doesn't
// notice a broken reference the way this query used to).
$selectedPrId = (int)($_GET['pr_id'] ?? $_POST['pr_id'] ?? 0);
$prLinesToShow = [];
$selectedPr = null;
if ($selectedPrId) {
    $selectedPr = DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?", [$selectedPrId, $companyId]);
    if ($selectedPr) {
        $prLinesToShow = DB::fetchAll(
            "SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code,
                    (i.id IS NULL OR u.id IS NULL) AS is_broken
             FROM purchase_requisition_lines prl
             LEFT JOIN items i ON i.id = prl.item_id
             LEFT JOIN units_of_measure u ON u.id = prl.uom_id
             LEFT JOIN purchase_order_lines pol ON pol.pr_line_id = prl.id
             WHERE prl.pr_id = ? AND pol.id IS NULL
             ORDER BY prl.line_num",
            [$selectedPrId]
        );
    }
}

// ---- Handle form submission ----
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) die('CSRF failed');

    $supplierId   = (int)($_POST['supplier_id'] ?? 0);
    $poDate       = $_POST['po_date'] ?? date('Y-m-d');
    $deliveryDate = $_POST['delivery_date'] ?? null;
    $payTerms     = $_POST['payment_terms'] ?? '';
    $remarks      = $_POST['remarks'] ?? '';
    $discountAmt  = (float)($_POST['discount_amount'] ?? 0);
    $lineItems    = $_POST['lines'] ?? [];

    $lineItems = array_filter($lineItems, fn($l) => !empty($l['item_id']) && (float)($l['qty']) > 0);

    if (!$supplierId)       $error = 'Please select a supplier.';
    elseif (!$lineItems)    $error = 'Add at least one line item.';
    else {
        try {
            DB::beginTransaction();

            // Get next PO number
            $result = DB::callProcedure('sp_get_next_number', [$companyId, 'PO', date('Y')], ['p_doc_number']);
            $poNumber = $result['p_doc_number'];

            $poId = DB::insert('purchase_orders', [
                'company_id'     => $companyId,
                'branch_id'      => Auth::branchId(),
                'po_number'      => $poNumber,
                'po_date'        => $poDate,
                'supplier_id'    => $supplierId,
                'currency_code'  => $companyCurrency,
                'payment_terms'  => $payTerms,
                'delivery_date'  => $deliveryDate ?: null,
                'status'         => 'DRAFT',
                'release_status' => 'NOT_RELEASED',
                'remarks'        => $remarks,
                'created_by'     => Auth::userId(),
            ] + msme_erp_cost_center_from_post($_POST));

            $convertedPrIds  = [];
            $taxableTotal    = 0.0;
            $gstTotal        = 0.0;
            foreach ($lineItems as $i => $line) {
                $qty   = (float)$line['qty'];
                $price = (float)$line['unit_price'];
                $disc  = (float)($line['discount_pct'] ?? 0);
                $gst   = (float)($line['gst_rate'] ?? 0);
                $base  = $qty * $price * (1 - $disc / 100);
                $total = $base; // line_total stores the taxable (pre-GST) value
                $gstAmt = $base * $gst / 100;
                $prLineId = !empty($line['pr_line_id']) ? (int)$line['pr_line_id'] : null;

                DB::insert('purchase_order_lines', [
                    'po_id'        => $poId,
                    'line_num'     => ($i + 1) * 10,
                    'pr_line_id'   => $prLineId,
                    'item_id'      => (int)$line['item_id'],
                    'uom_id'       => !empty($line['uom_id']) ? (int)$line['uom_id']
                                      : (int) DB::fetchValue("SELECT base_uom_id FROM items WHERE id=?", [(int)$line['item_id']]),
                    'qty_ordered'  => $qty,
                    'unit_price'   => $price,
                    'discount_pct' => $disc,
                    'gst_rate'     => $gst,
                    'line_total'   => $total,
                    'delivery_date'=> $deliveryDate ?: null,
                    'status'       => 'OPEN',
                ]);

                $taxableTotal += $base;
                $gstTotal     += $gstAmt;

                if ($prLineId) {
                    DB::update('purchase_requisition_lines', ['status' => 'PO_CREATED'], 'id=?', [$prLineId]);
                    $prIdForLine = DB::fetchValue("SELECT pr_id FROM purchase_requisition_lines WHERE id=?", [$prLineId]);
                    if ($prIdForLine) $convertedPrIds[$prIdForLine] = true;
                }
            }

            // Roll the line-level taxable/GST amounts up into the PO header —
            // previously these header columns were left at their DEFAULT 0
            // and never updated, so ME23N always displayed Taxable/GST/Grand
            // Total as ₹0.00 regardless of the line items.
            $taxableTotal = round($taxableTotal, 2);
            $gstTotal     = round($gstTotal, 2);
            $discountAmt  = min($discountAmt, $taxableTotal + $gstTotal); // never let discount push the total negative
            $preRound     = $taxableTotal + $gstTotal - $discountAmt;
            $roundOff     = round(round($preRound) - $preRound, 2);
            $grandTotal   = round($preRound + $roundOff, 2);
            DB::update('purchase_orders', [
                'total_amount'    => $taxableTotal,
                'tax_amount'      => $gstTotal,
                'discount_amount' => $discountAmt,
                'round_off'       => $roundOff,
                'grand_total'     => $grandTotal,
            ], 'id=?', [$poId]);

            // A PR header only flips to PO_CREATED once every one of its lines
            // has been pulled into a PO — a partially-converted PR stays
            // APPROVED so its remaining open lines still show up to convert.
            foreach (array_keys($convertedPrIds) as $prIdDone) {
                $stillOpen = DB::fetchValue("SELECT COUNT(*) FROM purchase_requisition_lines WHERE pr_id=? AND status='OPEN'", [$prIdDone]);
                if ((int)$stillOpen === 0) {
                    DB::update('purchase_requisitions', ['status' => 'PO_CREATED'], 'id=?', [$prIdDone]);
                }
            }

            Auth::audit('purchase_orders', $poId, 'INSERT', [], ['po_number' => $poNumber]);
            DB::commit();

            // Auto-trigger release if configured
            $strategy = DB::fetchOne(
                "SELECT id FROM release_strategies WHERE company_id=? AND doc_type='purchase_order' AND is_active=1 LIMIT 1",
                [$companyId]
            );
            if ($strategy) {
                $poTotal = DB::fetchValue("SELECT grand_total FROM purchase_orders WHERE id=?", [$poId]);
                $releaseId = DB::insert('document_releases', [
                    'company_id'   => $companyId,
                    'doc_type'     => 'purchase_order',
                    'doc_id'       => $poId,
                    'doc_number'   => $poNumber,
                    'doc_amount'   => $poTotal,
                    'strategy_id'  => $strategy['id'],
                    'current_level'=> 1,
                    'status'       => 'PENDING',
                    'requested_by' => Auth::userId(),
                ]);
                DB::update('purchase_orders', ['release_id' => $releaseId], 'id=?', [$poId]);
            }

            msme_erp_set_flash("PO {$poNumber} created successfully. Sent for approval.", 'success');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME23N') . '&id=' . $poId); exit;
            exit;

        } catch (Exception $e) {
            DB::rollback();
            $error = 'Error creating PO: ' . $e->getMessage();
        }
    }
}


?>

<?php if ($error): ?>
<div class="alert alert-danger"><i class="bi bi-exclamation-triangle me-2"></i><?= htmlspecialchars($error) ?></div>
<?php endif; ?>
<?php msme_erp_master_diagnostic($companyId, $suppliers, $items, $warehouses); ?>
<div class="small text-muted mb-2"><i class="bi bi-database-check me-1"></i>Loaded for <strong><?= htmlspecialchars($__coName ?: ('Company #' . $companyId)) ?></strong>:
  <?= count($suppliers) ?> supplier<?= count($suppliers)==1?'':'s' ?>, <?= count($items) ?> material<?= count($items)==1?'':'s' ?>,
  <?= count($eligiblePRs) ?> approved PR<?= count($eligiblePRs)==1?'':'s' ?> pending PO.</div>

<!-- Purchase Requisition picker — deliberately its own plain GET form,
     kept OUTSIDE the PO form below (nesting two <form> elements is
     invalid HTML and browsers will misbehave), so clicking ADD ITEM
     always does a real page reload with ?pr_id=... set — no dependency
     on a select2 change/select JS event actually firing. -->
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-cart-check me-2"></i>Create from Purchase Requisition (optional)</div>
  <div class="card-body">
    <form method="GET" class="row g-3 align-items-end">
      <input type="hidden" name="page" value="msme-erp">
      <input type="hidden" name="tcode" value="ME21N">
      <?php if ($__showAllPrs): ?><input type="hidden" name="show_all" value="1"><?php endif; ?>
      <div class="col-md-8">
        <label class="form-label fw-semibold small">Purchase Requisition</label>
        <select name="pr_id" id="pr-picker" class="form-select form-select-sm select2">
          <option value="">— Select PR to pull items from —</option>
          <?php foreach ($eligiblePRs as $pr): ?>
          <option value="<?= $pr['id'] ?>" <?= $selectedPrId === (int)$pr['id'] ? 'selected' : '' ?>>
            <?= htmlspecialchars($pr['pr_number']) ?> — <?= $pr['pr_date'] ?> (<?= $pr['open_lines'] ?> item<?= $pr['open_lines']==1?'':'s' ?> pending)<?= ($pr['status'] ?? 'APPROVED') !== 'APPROVED' ? ' [NOT YET APPROVED: ' . htmlspecialchars($pr['status']) . ']' : '' ?>
          </option>
          <?php endforeach; ?>
        </select>
        <?php if (!$eligiblePRs): ?>
        <div class="form-text text-warning-emphasis">No approved purchase requisition is waiting for a PO in this company.
          <?php if ($awaitingApprovalPRs): ?><?= (int)$awaitingApprovalPRs ?> PR<?= $awaitingApprovalPRs==1?' is':'s are' ?> still awaiting approval &mdash; approve it on
          <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ME53N')) ?>">ME53N</a> first.<?php endif; ?>
          <?php if ($__otherCoPrs): ?> <?= (int)$__otherCoPrs ?> PR(s) exist under another company &mdash; see <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMDC')) ?>">ZMDC</a>.<?php endif; ?></div>
        <?php endif; ?>
        <?php if ($__canOrderAhead): ?>
        <div class="form-text"><?php if ($__showAllPrs): ?>Showing PRs that are not yet approved too. <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ME21N')) ?>">Show approved only</a>
          <?php else: ?><a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ME21N&show_all=1')) ?>">Also list PRs not yet approved</a><?php endif; ?></div>
        <?php endif; ?>
      </div>
      <div class="col-md-4">
        <button type="submit" class="btn btn-primary btn-sm w-100"><i class="bi bi-plus-circle me-1"></i>ADD ITEM</button>
      </div>
    </form>
  </div>
</div>

<form method="POST" id="po-form">
<input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">


<div class="row g-3 mb-4">
  <!-- Header Card -->
  <div class="col-12">
    <div class="card border-0 shadow-sm">
      <div class="card-header bg-primary text-white fw-bold small py-2">
        <i class="bi bi-bag-plus me-2"></i>Purchase Order Header — ME21N
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-4">
            <label class="form-label fw-semibold small">Supplier <span class="text-danger">*</span></label>
            <select name="supplier_id" class="form-select form-select-sm select2" required id="sel-supplier">
              <option value="">— Select Supplier —</option>
              <?php foreach ($suppliers as $s): ?>
              <option value="<?= $s['id'] ?>"><?= htmlspecialchars($s['supplier_code'] . ' — ' . $s['name']) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
          <div class="col-md-2">
            <label class="form-label fw-semibold small">PO Date <span class="text-danger">*</span></label>
            <input type="date" name="po_date" class="form-control form-control-sm" value="<?= date('Y-m-d') ?>" required>
          </div>
          <div class="col-md-2">
            <label class="form-label fw-semibold small">Delivery Date</label>
            <input type="date" name="delivery_date" class="form-control form-control-sm">
          </div>
          <div class="col-md-2">
            <label class="form-label fw-semibold small">Payment Terms</label>
            <select name="payment_terms" class="form-select form-select-sm">
              <option>Net 30</option><option>Net 45</option><option>Net 60</option>
              <option>Immediate</option><option>Advance</option>
            </select>
          </div>
          <div class="col-md-2">
            <label class="form-label fw-semibold small">Currency</label>
            <input type="text" value="<?= htmlspecialchars($companyCurrency) ?>" class="form-control form-control-sm" readonly>
          </div>
          <div class="col-md-2">
            <label class="form-label fw-semibold small">Discount (<?= esc_html(msme_erp_cur()) ?>)</label>
            <input type="number" name="discount_amount" id="po-discount" class="form-control form-control-sm" min="0" step="0.01" value="0">
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold small">Notes</label>
            <input type="text" name="remarks" class="form-control form-control-sm" placeholder="Notes printed on the PO...">
          </div>
        </div>
        <hr>
        <?php msme_erp_render_cost_center_fields($companyId); ?>
      </div>
    </div>
  </div>

  <?php if ($selectedPrId): ?><input type="hidden" name="pr_id" value="<?= $selectedPrId ?>"><?php endif; ?>

  <!-- Line Items -->
  <div class="col-12">
    <div class="card border-0 shadow-sm">
      <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
        <span class="fw-bold small"><i class="bi bi-list-ul me-2"></i>Line Items</span>
        <button type="button" class="btn btn-sm btn-outline-primary" onclick="addLine()">
          <i class="bi bi-plus-circle"></i> Add Line
        </button>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm table-bordered mb-0" id="lines-table">
            <thead class="table-primary text-white" style="font-size:.75rem">
              <tr>
                <th width="30">#</th>
                <th width="220">Material</th>
                <th width="80">UOM</th>
                <th width="90">Qty</th>
                <th width="110">Unit Price</th>
                <th width="70">Disc%</th>
                <th width="70">GST%</th>
                <th width="110">Net Value</th>
                <th width="30"></th>
              </tr>
            </thead>
            <tbody id="lines-body">
              <?php $lineIdx = 0; foreach ($prLinesToShow as $pl): $estPrice = $pl['est_price'] > 0 ? $pl['est_price'] : 0; ?>
              <?php if (empty($pl['is_broken'])): ?>
              <tr class="line-row table-warning-subtle">
                <td class="text-center line-num small text-muted"><?= $lineIdx + 1 ?></td>
                <td>
                  <div class="small fw-bold"><?= htmlspecialchars($pl['item_code'] . ' – ' . $pl['item_name']) ?></div>
                  <div class="small text-muted">from PR line (qty requested: <?= number_format($pl['qty_requested'], 3) ?>)</div>
                  <input type="hidden" name="lines[<?= $lineIdx ?>][item_id]" value="<?= $pl['item_id'] ?>">
                  <input type="hidden" name="lines[<?= $lineIdx ?>][pr_line_id]" value="<?= $pl['id'] ?>">
                </td>
                <td>
                  <select name="lines[<?= $lineIdx ?>][uom_id]" class="form-select form-select-sm line-uom-sel" style="min-width:80px"><?= str_replace('value="' . (int)$pl['uom_id'] . '"', 'value="' . (int)$pl['uom_id'] . '" selected', $uomOptsHtml) ?></select>
                </td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][qty]" class="form-control form-control-sm line-qty" min="0.001" step="0.001" value="<?= (float)($pl['qty_approved'] ?: $pl['qty_requested']) ?>"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][unit_price]" class="form-control form-control-sm line-price" min="0" step="0.01" value="<?= $estPrice ?>"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][discount_pct]" class="form-control form-control-sm line-disc" min="0" max="100" step="0.01" value="0"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][gst_rate]" class="form-control form-control-sm line-tax" min="0" max="28" step="0.01" value="18"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][line_total]" class="form-control form-control-sm line-total text-end fw-bold" readonly value="0"></td>
                <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeLine(this)" title="Don't include this PR item in this PO"><i class="bi bi-x"></i></button></td>
              </tr>
              <?php else: ?>
              <!-- This PR line's item/UOM reference didn't resolve (e.g. the
                   item was removed after the PR was raised). Still linked to
                   the PR via the hidden pr_line_id below, but the material
                   needs to be reselected before this can be ordered. -->
              <tr class="line-row table-danger-subtle">
                <td class="text-center line-num small text-muted"><?= $lineIdx + 1 ?></td>
                <td>
                  <div class="small text-danger mb-1"><i class="bi bi-exclamation-triangle me-1"></i>Original item on PR line <?= (int)$pl['line_num'] ?> is missing — please reselect:</div>
                  <select name="lines[<?= $lineIdx ?>][item_id]" class="form-select form-select-sm select2 item-select" style="min-width:180px">
                    <option value="">— Item —</option>
                    <?php foreach ($items as $it): ?>
                    <option value="<?= $it['id'] ?>" data-price="<?= $it['moving_avg_price'] ?>" data-gst="<?= $it['gst_rate'] ?>" data-uom="<?= $it['uom_id'] ?>" data-uomcode="<?= $it['uom'] ?>"><?= htmlspecialchars($it['item_code'] . ' – ' . $it['item_name']) ?></option>
                    <?php endforeach; ?>
                  </select>
                  <input type="hidden" name="lines[<?= $lineIdx ?>][pr_line_id]" value="<?= $pl['id'] ?>">
                </td>
                <td>
                  <select name="lines[<?= $lineIdx ?>][uom_id]" class="form-select form-select-sm line-uom-sel" style="min-width:80px"><?= $uomOptsHtml ?></select>
                </td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][qty]" class="form-control form-control-sm line-qty" min="0.001" step="0.001" value="<?= (float)($pl['qty_approved'] ?: $pl['qty_requested']) ?>"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][unit_price]" class="form-control form-control-sm line-price" min="0" step="0.01" value="<?= $estPrice ?>"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][discount_pct]" class="form-control form-control-sm line-disc" min="0" max="100" step="0.01" value="0"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][gst_rate]" class="form-control form-control-sm line-tax" min="0" max="28" step="0.01" value="18"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][line_total]" class="form-control form-control-sm line-total text-end fw-bold" readonly value="0"></td>
                <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeLine(this)" title="Don't include this PR item in this PO"><i class="bi bi-x"></i></button></td>
              </tr>
              <?php endif; ?>
              <?php $lineIdx++; endforeach; ?>
              <tr id="line-template-row" class="line-row">
                <td class="text-center line-num small text-muted"><?= $lineIdx + 1 ?></td>
                <td>
                  <select name="lines[<?= $lineIdx ?>][item_id]" class="form-select form-select-sm select2 item-select" style="min-width:180px">
                    <option value="">— Item —</option>
                    <?php foreach ($items as $it): ?>
                    <option value="<?= $it['id'] ?>" data-price="<?= $it['moving_avg_price'] ?>" data-gst="<?= $it['gst_rate'] ?>" data-uom="<?= $it['uom_id'] ?>" data-uomcode="<?= $it['uom'] ?>"><?= htmlspecialchars($it['item_code'] . ' – ' . $it['item_name']) ?></option>
                    <?php endforeach; ?>
                  </select>
                </td>
                <td>
                  <select name="lines[<?= $lineIdx ?>][uom_id]" class="form-select form-select-sm line-uom-sel" style="min-width:80px"><?= $uomOptsHtml ?></select>
                </td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][qty]" class="form-control form-control-sm line-qty" min="0.001" step="0.001" value="1"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][unit_price]" class="form-control form-control-sm line-price" min="0" step="0.01" value="0"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][discount_pct]" class="form-control form-control-sm line-disc" min="0" max="100" step="0.01" value="0"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][gst_rate]" class="form-control form-control-sm line-tax" min="0" max="28" step="0.01" value="18"></td>
                <td><input type="number" name="lines[<?= $lineIdx ?>][line_total]" class="form-control form-control-sm line-total text-end fw-bold" readonly value="0"></td>
                <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeLine(this)"><i class="bi bi-x"></i></button></td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="table-light">
                <td colspan="6" class="text-end fw-bold small pe-2">Taxable Amount:</td>
                <td colspan="2"><input type="text" id="total-base" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
                <td></td>
              </tr>
              <tr class="table-light">
                <td colspan="6" class="text-end fw-bold small pe-2">GST Amount:</td>
                <td colspan="2"><input type="text" id="total-tax" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
                <td></td>
              </tr>
              <tr class="table-light">
                <td colspan="6" class="text-end fw-bold small pe-2">Discount:</td>
                <td colspan="2"><input type="text" id="total-discount" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
                <td></td>
              </tr>
              <tr class="table-light">
                <td colspan="6" class="text-end fw-bold small pe-2">Round Off:</td>
                <td colspan="2"><input type="text" id="total-roundoff" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
                <td></td>
              </tr>
              <tr class="table-success">
                <td colspan="6" class="text-end fw-bold small pe-2">Grand Total:</td>
                <td colspan="2"><input type="text" id="total-grand" class="form-control form-control-sm text-end fw-bold text-success" readonly value="0.00"></td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Actions -->
<div class="d-flex gap-2">
  <button type="submit" class="btn btn-primary">
    <i class="bi bi-save me-1"></i>Save & Send for Approval
  </button>
  <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N') ?>" class="btn btn-outline-secondary">
    <i class="bi bi-x-circle me-1"></i>Cancel
  </a>
</div>
</form>

<?php
$__eh_140149390970688_1 = json_encode(array_column($items, null, 'id'));
$__eh_lastPrices = json_encode((object)$supplierLastPrice);
$__eh_lineStartIdx = json_encode($lineIdx + 1); // next free index after all server-rendered rows (PR rows + the template row)
$extraScripts = <<<'JS'
<script>
let lineCount = __PH___eh_lineStartIdx__;
const itemsData = __PH___eh_140149390970688_1__;
const lastPrices = __PH___eh_lastPrices__;   // supplier_id -> item_id -> {price, disc, gst}

function addLine() {
  const tbody = document.getElementById('lines-body');
  const idx   = lineCount++;
  const template = document.getElementById('line-template-row');
  const first = template.cloneNode(true);
  first.removeAttribute('id');
  // The template row may already be select2-initialised; drop that markup so
  // the clone gets one clean dropdown instead of a dead duplicate.
  first.querySelectorAll('.select2-container').forEach(el => el.remove());
  first.querySelectorAll('select').forEach(el => {
    el.classList.remove('select2-hidden-accessible');
    el.removeAttribute('data-select2-id'); el.removeAttribute('aria-hidden'); el.removeAttribute('tabindex');
    el.querySelectorAll('option').forEach(o => o.removeAttribute('data-select2-id'));
  });
  first.querySelectorAll('input.line-price').forEach(el => delete el.dataset.touched);
  first.querySelectorAll('[name]').forEach(el => {
    el.name = el.name.replace(/\[\d+\]/, '[' + idx + ']');
    if (el.classList.contains('line-qty'))  el.value = 1;
    if (el.classList.contains('line-price')) el.value = 0;
    if (el.classList.contains('line-disc'))  el.value = 0;
    if (el.classList.contains('line-tax'))   el.value = 18;
    if (el.classList.contains('line-total')) el.value = '0.00';
    if (el.tagName === 'SELECT') el.value = '';
  });
  const uomSel = first.querySelector('.line-uom-sel'); if (uomSel) uomSel.value = '';
  first.querySelector('.line-num').textContent = idx + 1;
  tbody.appendChild(first);
  if (window.jQuery && window.jQuery.fn.select2) { window.jQuery(first).find('select.select2').select2({theme:'bootstrap-5', width:'100%'}); }
  bindItemSelect(first);
  bindCalcEvents(first);
}

function removeLine(btn) {
  const rows = document.querySelectorAll('.line-row');
  if (rows.length <= 1) { alert('At least one line is required'); return; }
  btn.closest('tr').remove();
  recalcAll();
}

// Item-select -> auto-fill price/GST/UOM. Only manual rows have an
// .item-select — PR-sourced rows already carry fixed item/UOM hidden
// fields, so this only ever runs where it's relevant.
function bindItemSelect(row) {
  const sel = row.querySelector('.item-select');
  if (!sel) return;
  const onItemChange = function() {
    const id   = this.value;
    const data = itemsData[id];
    if (!data) return;
    const hist = supplierHistory(id);
    row.querySelector('.line-price').value  = parseFloat(hist ? hist.price : (data.moving_avg_price || 0)).toFixed(2);
    row.querySelector('.line-tax').value    = parseFloat(hist && hist.gst ? hist.gst : (data.gst_rate || 18)).toFixed(2);
    if (hist && row.querySelector('.line-disc')) row.querySelector('.line-disc').value = parseFloat(hist.disc || 0).toFixed(2);
    delete row.querySelector('.line-price').dataset.touched;
    const uomSel = row.querySelector('.line-uom-sel');   // default from item master; user may change
    if (uomSel) uomSel.value = data.uom_id;
    calcRow(row);
  };
  if (window.jQuery) { window.jQuery(sel).on('change', onItemChange); }
  else { sel.addEventListener('change', onItemChange); }
}

// Qty/Price/Disc/GST -> row & grand totals. Applies to every row,
// PR-sourced or manual, since all of them expose these editable fields.
function bindCalcEvents(row) {
  ['line-qty','line-price','line-disc','line-tax'].forEach(cls => {
    const el = row.querySelector('.' + cls);
    if (el) el.addEventListener('input', () => { if (cls === 'line-price') el.dataset.touched = '1'; calcRow(row); });
  });
}

// Last price this supplier charged for a material (from earlier POs).
function supplierHistory(itemId) {
  const sup = document.getElementById('sel-supplier');
  const sid = sup ? sup.value : '';
  return (sid && lastPrices[sid] && lastPrices[sid][itemId]) ? lastPrices[sid][itemId] : null;
}

// Changing the supplier re-prices manual rows whose price the user has not typed over.
(function() {
  const sup = document.getElementById('sel-supplier');
  if (!sup) return;
  const onSup = function() {
    document.querySelectorAll('.line-row').forEach(row => {
      const sel = row.querySelector('.item-select'); const pr = row.querySelector('.line-price');
      if (!sel || !sel.value || !pr || pr.dataset.touched === '1') return;
      const data = itemsData[sel.value]; if (!data) return;
      const hist = supplierHistory(sel.value);
      pr.value = parseFloat(hist ? hist.price : (data.moving_avg_price || 0)).toFixed(2);
      calcRow(row);
    });
  };
  if (window.jQuery) window.jQuery(sup).on('change', onSup); else sup.addEventListener('change', onSup);
})();

function calcRow(row) {
  const qty   = parseFloat(row.querySelector('.line-qty').value)   || 0;
  const price = parseFloat(row.querySelector('.line-price').value)  || 0;
  const disc  = parseFloat(row.querySelector('.line-disc').value)   || 0;
  const gst   = parseFloat(row.querySelector('.line-tax').value)    || 0;
  const base  = qty * price * (1 - disc / 100);
  row.querySelector('.line-total').value = base.toFixed(2);
  recalcAll();
}

function recalcAll() {
  let base = 0, tax = 0;
  document.querySelectorAll('.line-row').forEach(row => {
    const b = parseFloat(row.querySelector('.line-total').value) || 0;
    const t = parseFloat(row.querySelector('.line-tax').value)   || 0;
    base += b;
    tax  += b * t / 100;
  });
  const discount   = parseFloat(document.getElementById('po-discount')?.value) || 0;
  const preRound   = base + tax - discount;
  const roundOff   = Math.round(preRound) - preRound;
  document.getElementById('total-base').value     = base.toFixed(2);
  document.getElementById('total-tax').value      = tax.toFixed(2);
  document.getElementById('total-discount').value = discount.toFixed(2);
  document.getElementById('total-roundoff').value = roundOff.toFixed(2);
  document.getElementById('total-grand').value     = (preRound + roundOff).toFixed(2);
}

document.getElementById('po-discount')?.addEventListener('input', recalcAll);

// Init every server-rendered row (PR-sourced rows + the manual template row)
document.querySelectorAll('.line-row').forEach(row => {
  bindItemSelect(row);
  bindCalcEvents(row);
  calcRow(row);
});
</script>
JS;
$extraScripts = str_replace('__PH___eh_140149390970688_1__', $__eh_140149390970688_1, $extraScripts);
$extraScripts = str_replace('__PH___eh_lastPrices__', $__eh_lastPrices, $extraScripts);
$extraScripts = str_replace('__PH___eh_lineStartIdx__', $__eh_lineStartIdx, $extraScripts);

echo $extraScripts;

?>

