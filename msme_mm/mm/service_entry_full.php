<?php
/**
 * MSME ERP — Service Entry Sheet
 * T-Code: ML81N
 * Create/display service entry sheets against a Purchase Order,
 * capturing services rendered (qty/gross price/cost center) and
 * posting an acceptance document when accepted.
 * Developed by: Prigag Online Pvt. Ltd.
 */

if (empty($tcode)) $tcode = 'ML81N';
Auth::requireTcode('ML81N');

$companyId = Auth::companyId();
$error = $success = '';

$canCreate = Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN']);
$canAccept = Auth::hasRole(['PURCHASE_MANAGER','SUPER_ADMIN']);

// ---- POST: Accept / Reject an existing entry sheet ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['ses_action'])) {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
    $sesId  = (int)$_POST['ses_id'];
    $action = $_POST['ses_action'];

    if ($action === 'accept' && $canAccept) {
        $ses = DB::fetchOne("SELECT * FROM service_entry_sheets WHERE id=? AND company_id=? AND status='DRAFT'", [$sesId, $companyId]);
        if ($ses) {
            $result  = DB::callProcedure('sp_get_next_number', [$companyId, 'SESACC', date('Y')], ['p_doc_number']);
            $accNum  = $result['p_doc_number'];
            $qos     = max(0, min(100, (int)($_POST['quality_of_service'] ?? 100)));
            $timely  = max(0, min(100, (int)($_POST['service_timeliness'] ?? 100)));
            DB::update('service_entry_sheets', [
                'status'                => 'ACCEPTED',
                'acceptance_doc_number' => $accNum,
                'accepted_by'           => Auth::userId(),
                'accepted_at'           => current_time('mysql'),
                'quality_of_service'    => $qos,
                'service_timeliness'    => $timely,
            ], 'id=?', [$sesId]);
            DB::query("UPDATE service_entry_sheet_lines SET status='POSTED' WHERE ses_id=?", [$sesId]);
            Auth::audit('service_entry_sheets', $sesId, 'UPDATE', ['status'=>'DRAFT'], ['status'=>'ACCEPTED','acceptance_doc_number'=>$accNum]);
            msme_erp_set_flash("Service entry sheet saved, acceptance document {$accNum} posted.", 'success');
        }
    } elseif ($action === 'reject' && $canAccept) {
        DB::update('service_entry_sheets', ['status' => 'REJECTED'], 'id=? AND company_id=? AND status=\'DRAFT\'', [$sesId, $companyId]);
        msme_erp_set_flash('Service entry sheet rejected.', 'success');
    }
    wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ML81N') . '&id=' . $sesId); exit;
}

// ---- POST: Create a new entry sheet ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['create_ses']) && $canCreate) {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
    $d      = $_POST;
    $poId   = (int)($d['po_id'] ?? 0);
    $poLine = !empty($d['po_line_id']) ? (int)$d['po_line_id'] : null;
    $lines  = array_filter($d['lines'] ?? [], fn($l) => !empty($l['short_text']) && (float)($l['quantity'] ?? 0) > 0);

    $po = $poId ? DB::fetchOne("SELECT * FROM purchase_orders WHERE id=? AND company_id=?", [$poId, $companyId]) : null;

    if (!$po)   $error = 'Please select a valid Purchase Order.';
    elseif (!$lines) $error = 'Add at least one service line with a description and quantity.';
    else {
        try {
            DB::beginTransaction();

            $result   = DB::callProcedure('sp_get_next_number', [$companyId, 'SES', date('Y')], ['p_doc_number']);
            $sesNumber = $result['p_doc_number'];

            $total = 0;
            foreach ($lines as $l) { $total += (float)$l['quantity'] * (float)$l['gross_price']; }

            $sesId = DB::insert('service_entry_sheets', [
                'company_id'       => $companyId,
                'ses_number'       => $sesNumber,
                'ses_date'         => $d['ses_date'] ?: date('Y-m-d'),
                'po_id'            => $poId,
                'po_line_id'       => $poLine,
                'supplier_id'      => $po['supplier_id'],
                'short_text'       => $d['short_text'] ?? '',
                'acct_assgt_cat'   => $d['acct_assgt_cat'] ?: 'K',
                'cost_center_id'   => !empty($d['cost_center_id']) ? (int)$d['cost_center_id'] : null,
                'external_number'  => $d['external_number'] ?? '',
                'service_location' => $d['service_location'] ?? '',
                'period_from'      => $d['period_from'] ?: null,
                'period_to'        => $d['period_to'] ?: null,
                'price_ref_date'   => $d['price_ref_date'] ?: null,
                'currency_code'    => $po['currency_code'] ?: 'INR',
                'total_amount'     => $total,
                'status'           => 'DRAFT',
                'returns_indicator'=> !empty($d['returns_indicator']) ? 1 : 0,
                'remarks'          => $d['remarks'] ?? '',
                'created_by'       => Auth::userId(),
            ]);

            foreach ($lines as $i => $l) {
                $qty   = (float)$l['quantity'];
                $price = (float)$l['gross_price'];
                DB::insert('service_entry_sheet_lines', [
                    'ses_id'          => $sesId,
                    'line_num'        => ($i + 1) * 10,
                    'service_item_id' => !empty($l['service_item_id']) ? (int)$l['service_item_id'] : null,
                    'short_text'      => $l['short_text'],
                    'quantity'        => $qty,
                    'uom_id'          => !empty($l['uom_id']) ? (int)$l['uom_id'] : null,
                    'gross_price'     => $price,
                    'cost_center_id'  => !empty($l['line_cost_center_id']) ? (int)$l['line_cost_center_id'] : (!empty($d['cost_center_id']) ? (int)$d['cost_center_id'] : null),
                    'line_total'      => $qty * $price,
                    'status'          => 'OPEN',
                ]);
            }

            Auth::audit('service_entry_sheets', $sesId, 'INSERT', [], ['ses_number' => $sesNumber]);
            DB::commit();

            msme_erp_set_flash("Service entry sheet {$sesNumber} created. Ready for acceptance.", 'success');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ML81N') . '&id=' . $sesId); exit;

        } catch (Exception $e) {
            DB::rollback();
            $error = 'Error creating service entry sheet: ' . $e->getMessage();
        }
    }
}

$id  = (int)($_GET['id'] ?? 0);
$new = isset($_GET['new']);

// =========================================================
// DETAIL VIEW
// =========================================================
if ($id) {
    $ses = DB::fetchOne(
        "SELECT ses.*, po.po_number, s.name AS supplier_name, s.supplier_code, u.full_name AS created_by_name, acc.full_name AS accepted_by_name, cc.code AS cc_code, cc.name AS cc_name
         FROM service_entry_sheets ses
         JOIN purchase_orders po ON po.id = ses.po_id
         JOIN suppliers s ON s.id = ses.supplier_id
         LEFT JOIN users u ON u.id = ses.created_by
         LEFT JOIN users acc ON acc.id = ses.accepted_by
         LEFT JOIN cost_centers cc ON cc.id = ses.cost_center_id
         WHERE ses.id=? AND ses.company_id=?", [$id, $companyId]
    );
    if (!$ses) { wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ML81N')); exit; }

    $lines = DB::fetchAll(
        "SELECT sesl.*, i.item_code AS service_code, u.code AS uom_code, cc.code AS line_cc_code
         FROM service_entry_sheet_lines sesl
         LEFT JOIN items i ON i.id = sesl.service_item_id
         LEFT JOIN units_of_measure u ON u.id = sesl.uom_id
         LEFT JOIN cost_centers cc ON cc.id = sesl.cost_center_id
         WHERE sesl.ses_id=? ORDER BY sesl.line_num", [$id]
    );

    $pageTitle   = $ses['ses_number'] . ' Display Entry Sheet';
    $pageTcode   = 'ML81N';
    $breadcrumbs = ['MM', 'Purchasing', 'Service Entry Sheet'];
    $pageActions = '<div class="d-flex gap-2">'
        . ($canCreate ? '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ML81N&new=1') . '" class="btn btn-sm btn-primary"><i class="bi bi-plus-circle me-1"></i>New Entry Sheet</a>' : '')
        . '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ML81N') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left me-1"></i>Back</a>
        </div>';

    erp_render_page_header();

    $sc = ['DRAFT'=>'warning','ACCEPTED'=>'success','REJECTED'=>'danger','CANCELLED'=>'secondary'];
?>
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center py-2">
    <span class="fw-bold"><i class="bi bi-clipboard-check me-2"></i><?= htmlspecialchars($ses['ses_number']) ?> — Display Entry Sheet</span>
    <div class="d-flex gap-2">
      <span class="badge bg-light text-dark"><?= htmlspecialchars($ses['status']) ?></span>
      <?php if ($ses['returns_indicator']): ?><span class="badge bg-warning text-dark">Returns</span><?php endif; ?>
    </div>
  </div>
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-6">
        <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
          <tr><th width="150" class="text-muted">Entry Sheet:</th><td class="fw-bold"><?= htmlspecialchars($ses['ses_number']) ?></td></tr>
          <tr><th class="text-muted">For Purchase Order:</th><td><a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$ses['po_id'] ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($ses['po_number']) ?></a><?= $ses['po_line_id'] ? ' — Line ' . (int)DB::fetchValue("SELECT line_num FROM purchase_order_lines WHERE id=?", [$ses['po_line_id']]) : '' ?></td></tr>
          <tr><th class="text-muted">Short Text:</th><td><?= htmlspecialchars($ses['short_text'] ?? '—') ?></td></tr>
          <tr><th class="text-muted">Supplier:</th><td><?= htmlspecialchars($ses['supplier_code'] . ' — ' . $ses['supplier_name']) ?></td></tr>
        </table>
      </div>
      <div class="col-md-6">
        <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
          <tr><th width="150" class="text-muted">Acct Assgt Cat.:</th><td><?= htmlspecialchars($ses['acct_assgt_cat']) ?><?= $ses['cc_code'] ? ' — ' . htmlspecialchars($ses['cc_code'] . ' ' . $ses['cc_name']) : '' ?></td></tr>
          <tr><th class="text-muted">External Number:</th><td><?= htmlspecialchars($ses['external_number'] ?? '—') ?></td></tr>
          <tr><th class="text-muted">Service Loc.:</th><td><?= htmlspecialchars($ses['service_location'] ?? '—') ?></td></tr>
          <tr><th class="text-muted">Period:</th><td><?= $ses['period_from'] ? htmlspecialchars($ses['period_from'] . ' – ' . $ses['period_to']) : '—' ?></td></tr>
          <tr><th class="text-muted">Price Ref.:</th><td><?= $ses['price_ref_date'] ?? '—' ?></td></tr>
        </table>
      </div>
    </div>
  </div>
</div>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list-ul me-2"></i>Service Lines</div>
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0 erp-table">
        <thead><tr><th>Line</th><th>Service No.</th><th>Short Text</th><th>Quantity</th><th>Un</th><th>Gross Price</th><th>Crcy</th><th>Cost Center</th><th>Status</th></tr></thead>
        <tbody>
          <?php foreach ($lines as $l): ?>
          <tr>
            <td class="text-muted small"><?= $l['line_num'] ?></td>
            <td class="small"><?= htmlspecialchars($l['service_code'] ?? '—') ?></td>
            <td class="small"><?= htmlspecialchars($l['short_text']) ?></td>
            <td class="small fw-bold"><?= number_format($l['quantity'], 3) ?></td>
            <td class="small"><?= htmlspecialchars($l['uom_code'] ?? 'PC') ?></td>
            <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($l['gross_price'], 2) ?></td>
            <td class="small"><?= htmlspecialchars($ses['currency_code']) ?></td>
            <td class="small"><?= htmlspecialchars($l['line_cc_code'] ?? '—') ?></td>
            <td><span class="badge bg-<?= $l['status']==='POSTED'?'success':'warning' ?> small"><?= $l['status'] ?></span></td>
          </tr>
          <?php endforeach; if (!$lines): ?>
          <tr><td colspan="9" class="text-center text-muted py-3">No service lines.</td></tr>
          <?php endif; ?>
        </tbody>
        <tfoot class="table-light fw-bold">
          <tr><td colspan="8" class="text-end small">Total Value:</td><td class="small text-primary"><?= esc_html(msme_erp_cur()) ?><?= number_format($ses['total_amount'], 2) ?></td></tr>
        </tfoot>
      </table>
    </div>
  </div>
</div>

<?php if ($ses['status'] === 'ACCEPTED'): ?>
<div class="alert alert-success small py-2 d-flex align-items-center">
  <i class="bi bi-check-circle-fill me-2"></i>
  Service entry sheet saved, acceptance document <strong><?= htmlspecialchars($ses['acceptance_doc_number']) ?></strong> posted
  <?= $ses['accepted_by_name'] ? ' by ' . htmlspecialchars($ses['accepted_by_name']) : '' ?>
  <?= $ses['accepted_at'] ? ' on ' . htmlspecialchars($ses['accepted_at']) : '' ?>.
</div>
<?php if ($ses['quality_of_service'] !== null || $ses['service_timeliness'] !== null): ?>
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-star-fill me-2 text-warning"></i>Vendor Evaluation</div>
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-6">
        <label class="form-label small text-muted mb-1">Quality of Service</label>
        <div class="progress" style="height:20px">
          <div class="progress-bar bg-success" style="width:<?= (int)$ses['quality_of_service'] ?>%"><?= (int)$ses['quality_of_service'] ?></div>
        </div>
      </div>
      <div class="col-md-6">
        <label class="form-label small text-muted mb-1">Service Timeliness</label>
        <div class="progress" style="height:20px">
          <div class="progress-bar bg-info" style="width:<?= (int)$ses['service_timeliness'] ?>%"><?= (int)$ses['service_timeliness'] ?></div>
        </div>
      </div>
    </div>
  </div>
</div>
<?php endif; ?>
<?php elseif ($ses['status'] === 'REJECTED'): ?>
<div class="alert alert-danger small py-2"><i class="bi bi-x-circle-fill me-2"></i>This entry sheet was rejected.</div>
<?php elseif ($canAccept): ?>
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-clipboard-check me-2"></i>Actions</div>
  <div class="card-body">
    <form method="POST" class="mb-3 pb-3 border-bottom">
      <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
      <input type="hidden" name="ses_id" value="<?= $id ?>">
      <input type="hidden" name="ses_action" value="accept">
      <div class="row g-3 align-items-end">
        <div class="col-md-4">
          <label class="form-label small fw-semibold">Quality of Service <span class="text-muted">(0-100)</span></label>
          <input type="number" name="quality_of_service" class="form-control form-control-sm" min="0" max="100" value="100">
        </div>
        <div class="col-md-4">
          <label class="form-label small fw-semibold">Service Timeliness <span class="text-muted">(0-100)</span></label>
          <input type="number" name="service_timeliness" class="form-control form-control-sm" min="0" max="100" value="100">
        </div>
        <div class="col-md-4">
          <button class="btn btn-success btn-sm" onclick="return confirm('Accept and post entry sheet <?= htmlspecialchars($ses['ses_number']) ?>?')">
            <i class="bi bi-check-circle me-1"></i>Accept &amp; Post
          </button>
        </div>
      </div>
    </form>
    <form method="POST">
      <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
      <input type="hidden" name="ses_id" value="<?= $id ?>">
      <input type="hidden" name="ses_action" value="reject">
      <button class="btn btn-outline-danger btn-sm" onclick="return confirm('Reject this entry sheet?')">
        <i class="bi bi-x-circle me-1"></i>Reject
      </button>
    </form>
  </div>
</div>
<?php endif; ?>

<?php
    return;
}

// =========================================================
// CREATE VIEW
// =========================================================
if ($new && $canCreate) {
    $pageTitle   = 'Create Entry Sheet';
    $pageTcode   = 'ML81N';
    $breadcrumbs = ['MM', 'Purchasing', 'Create Service Entry Sheet'];
    erp_render_page_header();

    $pos = DB::fetchAll(
        "SELECT po.id, po.po_number, po.currency_code, s.name AS supplier_name
         FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
         WHERE po.company_id=? AND po.status IN ('APPROVED','PARTIAL') AND po.is_deleted=0
         ORDER BY po.id DESC", [$companyId]
    );
    $selectedPoId = (int)($_GET['po_id'] ?? $_POST['po_id'] ?? 0);
    $poLines = [];
    if ($selectedPoId) {
        $poLines = DB::fetchAll(
            "SELECT pol.id, pol.line_num, i.item_code, i.item_name, i.item_type
             FROM purchase_order_lines pol JOIN items i ON i.id = pol.item_id
             WHERE pol.po_id=? ORDER BY pol.line_num", [$selectedPoId]
        );
    }
    $costCenters   = DB::fetchAll("SELECT id, code, name FROM cost_centers WHERE company_id=? AND is_active=1 ORDER BY name", [$companyId]);
    $serviceItems  = DB::fetchAll("SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, i.moving_avg_price FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id WHERE i.company_id=? AND i.item_type='SERVICE' AND i.is_active=1 AND i.is_deleted=0 ORDER BY i.item_name", [$companyId]);
?>
<?php if ($error): ?><div class="alert alert-danger"><i class="bi bi-exclamation-triangle me-2"></i><?= htmlspecialchars($error) ?></div><?php endif; ?>

<form method="POST" id="ses-form">
<input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
<input type="hidden" name="create_ses" value="1">

<div class="row g-3 mb-4">
  <div class="col-12">
    <div class="card border-0 shadow-sm">
      <div class="card-header bg-primary text-white fw-bold small py-2"><i class="bi bi-clipboard-check me-2"></i>Entry Sheet Header — ML81N</div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-4">
            <label class="form-label fw-semibold small">For Purchase Order <span class="text-danger">*</span></label>
            <select name="po_id" id="ses-po" class="form-select form-select-sm select2" required>
              <option value="">— Select Purchase Order —</option>
              <?php foreach ($pos as $po): ?>
              <option value="<?= $po['id'] ?>" <?= $selectedPoId===(int)$po['id']?'selected':'' ?>><?= htmlspecialchars($po['po_number'] . ' — ' . $po['supplier_name']) ?></option>
              <?php endforeach; ?>
            </select>
            <div class="form-text">Only APPROVED / PARTIAL purchase orders are listed.</div>
          </div>
          <?php if ($selectedPoId): ?>
          <div class="col-md-4">
            <label class="form-label fw-semibold small">PO Line (optional)</label>
            <select name="po_line_id" class="form-select form-select-sm select2">
              <option value="">— Whole PO / not line-specific —</option>
              <?php foreach ($poLines as $pl): ?>
              <option value="<?= $pl['id'] ?>"><?= $pl['line_num'] ?> — <?= htmlspecialchars($pl['item_code'] . ' – ' . $pl['item_name']) ?><?= $pl['item_type']==='SERVICE'?' (Service)':'' ?></option>
              <?php endforeach; ?>
            </select>
          </div>
          <?php endif; ?>
          <div class="col-md-4">
            <label class="form-label fw-semibold small">Entry Sheet Date <span class="text-danger">*</span></label>
            <input type="date" name="ses_date" class="form-control form-control-sm" value="<?= date('Y-m-d') ?>" required>
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold small">Short Text</label>
            <input type="text" name="short_text" class="form-control form-control-sm" placeholder="e.g. Consulting service — Aug 2026">
          </div>

          <div class="col-md-3">
            <label class="form-label fw-semibold small">Acct Assgt Cat.</label>
            <select name="acct_assgt_cat" class="form-select form-select-sm">
              <option value="K" selected>K — Cost Center</option>
              <option value="F">F — Order</option>
              <option value="P">P — Project</option>
              <option value="U">U — Unknown</option>
            </select>
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">Cost Center</label>
            <select name="cost_center_id" class="form-select form-select-sm select2">
              <option value="">— Select —</option>
              <?php foreach ($costCenters as $cc): ?>
              <option value="<?= $cc['id'] ?>"><?= htmlspecialchars($cc['code'] . ' — ' . $cc['name']) ?></option>
              <?php endforeach; ?>
            </select>
            <div class="form-text">Used as the default for lines below.</div>
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">External Number</label>
            <input type="text" name="external_number" class="form-control form-control-sm">
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">Service Loc.</label>
            <input type="text" name="service_location" class="form-control form-control-sm">
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">Period From</label>
            <input type="date" name="period_from" class="form-control form-control-sm">
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">Period To</label>
            <input type="date" name="period_to" class="form-control form-control-sm">
          </div>
          <div class="col-md-3">
            <label class="form-label fw-semibold small">Price Ref. Date</label>
            <input type="date" name="price_ref_date" class="form-control form-control-sm" value="<?= date('Y-m-d') ?>">
          </div>
          <div class="col-md-3 d-flex align-items-end">
            <div class="form-check">
              <input class="form-check-input" type="checkbox" name="returns_indicator" value="1" id="ses-returns">
              <label class="form-check-label small" for="ses-returns">Returns Indicator</label>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="col-12">
    <div class="card border-0 shadow-sm">
      <div class="card-header bg-white d-flex justify-content-between align-items-center py-2">
        <span class="fw-bold small"><i class="bi bi-list-ul me-2"></i>Service Lines</span>
        <button type="button" class="btn btn-sm btn-outline-primary" onclick="addSesLine()"><i class="bi bi-plus-circle"></i> Add Line</button>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm table-bordered mb-0" id="ses-lines-table">
            <thead class="table-primary text-white" style="font-size:.75rem">
              <tr>
                <th width="30">#</th><th width="200">Service No. (optional)</th><th>Short Text</th>
                <th width="90">Quantity</th><th width="70">Un</th><th width="110">Gross Price</th>
                <th width="160">Cost Center</th><th width="110">Line Total</th><th width="30"></th>
              </tr>
            </thead>
            <tbody id="ses-lines-body">
              <tr class="ses-line" id="ses-line-template">
                <td class="text-center small text-muted line-num">1</td>
                <td>
                  <select name="lines[0][service_item_id]" class="form-select form-select-sm select2 service-select">
                    <option value="">— Free text —</option>
                    <?php foreach ($serviceItems as $si): ?>
                    <option value="<?= $si['id'] ?>" data-price="<?= $si['moving_avg_price'] ?>" data-uom="<?= $si['uom_id'] ?>" data-uomcode="<?= $si['uom'] ?>" data-name="<?= htmlspecialchars($si['item_name']) ?>"><?= htmlspecialchars($si['item_code'] . ' – ' . $si['item_name']) ?></option>
                    <?php endforeach; ?>
                  </select>
                  <input type="hidden" name="lines[0][uom_id]" class="line-uom-id" value="">
                </td>
                <td><input type="text" name="lines[0][short_text]" class="form-control form-control-sm" placeholder="Describe the service..."></td>
                <td><input type="number" name="lines[0][quantity]" class="form-control form-control-sm line-qty" min="0.001" step="0.001" value="1"></td>
                <td><input type="text" class="form-control form-control-sm line-uom" readonly value="PC"></td>
                <td><input type="number" name="lines[0][gross_price]" class="form-control form-control-sm line-price" min="0" step="0.01" value="0"></td>
                <td>
                  <select name="lines[0][line_cost_center_id]" class="form-select form-select-sm select2">
                    <option value="">(use header default)</option>
                    <?php foreach ($costCenters as $cc): ?>
                    <option value="<?= $cc['id'] ?>"><?= htmlspecialchars($cc['code']) ?></option>
                    <?php endforeach; ?>
                  </select>
                </td>
                <td><input type="text" class="form-control form-control-sm line-total text-end fw-bold" readonly value="0.00"></td>
                <td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1" onclick="removeSesLine(this)"><i class="bi bi-x"></i></button></td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="table-success">
                <td colspan="7" class="text-end fw-bold small pe-2">Total Value:</td>
                <td colspan="2"><input type="text" id="ses-total" class="form-control form-control-sm text-end fw-bold text-success" readonly value="0.00"></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="d-flex gap-2">
  <button type="submit" class="btn btn-primary"><i class="bi bi-save me-1"></i>Save Entry Sheet</button>
  <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ML81N') ?>" class="btn btn-outline-secondary"><i class="bi bi-x-circle me-1"></i>Cancel</a>
</div>
</form>

<?php
$extraScripts = <<<'JS'
<script>
let sesLineIdx = 1;

function bindSesLine(row) {
  const svc = row.querySelector('.service-select');
  if (svc) svc.addEventListener('change', function() {
    const opt = this.options[this.selectedIndex];
    const textInput = row.querySelector('input[name$="[short_text]"]');
    if (opt && opt.value) {
      if (textInput && !textInput.value) textInput.value = opt.dataset.name || '';
      row.querySelector('.line-price').value = parseFloat(opt.dataset.price || 0).toFixed(2);
      row.querySelector('.line-uom-id').value = opt.dataset.uom || '';
      row.querySelector('.line-uom').value = opt.dataset.uomcode || 'PC';
    }
    calcSesRow(row);
  });
  ['line-qty','line-price'].forEach(cls => {
    const el = row.querySelector('.' + cls);
    if (el) el.addEventListener('input', () => calcSesRow(row));
  });
}

function calcSesRow(row) {
  const qty   = parseFloat(row.querySelector('.line-qty').value)   || 0;
  const price = parseFloat(row.querySelector('.line-price').value) || 0;
  row.querySelector('.line-total').value = (qty * price).toFixed(2);
  recalcSesTotal();
}

function recalcSesTotal() {
  let total = 0;
  document.querySelectorAll('.ses-line').forEach(row => {
    total += parseFloat(row.querySelector('.line-total').value) || 0;
  });
  document.getElementById('ses-total').value = total.toFixed(2);
}

function addSesLine() {
  const tbody = document.getElementById('ses-lines-body');
  const idx = sesLineIdx++;
  const template = document.getElementById('ses-line-template');
  const row = template.cloneNode(true);
  row.removeAttribute('id');
  row.querySelectorAll('[name]').forEach(el => {
    el.name = el.name.replace(/\[\d+\]/, '[' + idx + ']');
    if (el.tagName === 'SELECT') el.value = '';
    if (el.classList.contains('line-qty')) el.value = 1;
    if (el.classList.contains('line-price')) el.value = 0;
    if (el.classList.contains('line-uom-id')) el.value = '';
    if (el.type === 'text' && !el.classList.contains('line-uom')) el.value = '';
  });
  row.querySelector('.line-uom').value = 'PC';
  row.querySelector('.line-total').value = '0.00';
  row.querySelector('.line-num').textContent = idx + 1;
  tbody.appendChild(row);
  $(row).find('select.select2').select2({theme:'bootstrap-5', width:'100%'});
  bindSesLine(row);
}

function removeSesLine(btn) {
  const rows = document.querySelectorAll('.ses-line');
  if (rows.length <= 1) { alert('At least one line is required'); return; }
  btn.closest('tr').remove();
  recalcSesTotal();
}

document.querySelectorAll('.ses-line').forEach(bindSesLine);

// PO picker reloads the page with ?new=1&po_id=... so the server can
// pull that PO's lines for the "PO Line (optional)" dropdown — same
// pattern used by the PR picker on ME21N (Create PO).
(function() {
  const picker = document.getElementById('ses-po');
  if (!picker) return;
  $(picker).on('select2:select', function() {
    const val = this.value;
    const url = new URL(window.location.href);
    url.searchParams.set('new', '1');
    if (val) url.searchParams.set('po_id', val); else url.searchParams.delete('po_id');
    window.location.href = url.toString();
  });
})();
</script>
JS;
echo $extraScripts;
?>
<?php
    return;
}

// =========================================================
// LIST VIEW
// =========================================================
$pageTitle   = 'Service Entry Sheets';
$pageTcode   = 'ML81N';
$breadcrumbs = ['MM', 'Purchasing', 'Service Entry Sheets'];
$pageActions = $canCreate
    ? '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ML81N&new=1') . '" class="btn btn-primary btn-sm"><i class="bi bi-plus-circle me-1"></i>Create Entry Sheet (ML81N)</a>'
    : '';

erp_render_page_header();

$filterStatus = $_GET['status'] ?? '';
$sql = "SELECT ses.id, ses.ses_number, ses.ses_date, po.po_number, s.name AS supplier_name, ses.total_amount, ses.status, ses.acceptance_doc_number
        FROM service_entry_sheets ses
        JOIN purchase_orders po ON po.id = ses.po_id
        JOIN suppliers s ON s.id = ses.supplier_id
        WHERE ses.company_id=? AND ses.is_deleted=0";
$params = [$companyId];
if ($filterStatus) { $sql .= " AND ses.status=?"; $params[] = $filterStatus; }
$sql .= " ORDER BY ses.id DESC";

$page   = max(1, (int)($_GET['pg'] ?? 1));
$result = DB::paginate($sql, $params, $page);
$list   = $result['data'];

$counts = ['DRAFT'=>0,'ACCEPTED'=>0,'REJECTED'=>0];
foreach (DB::fetchAll("SELECT status, COUNT(*) c FROM service_entry_sheets WHERE company_id=? AND is_deleted=0 GROUP BY status", [$companyId]) as $r) {
    if (isset($counts[$r['status']])) $counts[$r['status']] = (int)$r['c'];
}
?>
<div class="row g-3 mb-3">
  <?php foreach ([['Draft','DRAFT','warning',$counts['DRAFT']],['Accepted','ACCEPTED','success',$counts['ACCEPTED']],['Rejected','REJECTED','danger',$counts['REJECTED']]] as [$label,$stat,$color,$cnt]): ?>
  <div class="col-6 col-md-4">
    <div class="card border-0 shadow-sm text-center p-3">
      <div class="fs-4 fw-bold text-<?= $color ?>"><?= $cnt ?></div>
      <div class="small text-muted"><?= $label ?></div>
    </div>
  </div>
  <?php endforeach; ?>
</div>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-body py-2">
    <form class="row g-2 align-items-end" method="GET">
      <input type="hidden" name="page" value="msme-erp"><input type="hidden" name="tcode" value="ML81N">
      <div class="col-md-3">
        <label class="form-label small mb-1">Status</label>
        <select name="status" class="form-select form-select-sm">
          <option value="">All</option>
          <?php foreach (['DRAFT','ACCEPTED','REJECTED'] as $st): ?>
          <option value="<?= $st ?>" <?= $filterStatus===$st?'selected':'' ?>><?= $st ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-auto">
        <button class="btn btn-primary btn-sm"><i class="bi bi-search me-1"></i>Filter</button>
        <a href="?page=msme-erp&tcode=ML81N" class="btn btn-outline-secondary btn-sm ms-1"><i class="bi bi-x"></i> Clear</a>
      </div>
    </form>
  </div>
</div>

<div class="card border-0 shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-hover table-sm mb-0 erp-table">
        <thead><tr><th>Entry Sheet#</th><th>Date</th><th>PO</th><th>Supplier</th><th>Value</th><th>Status</th><th>Acceptance Doc.</th><th class="text-center">Action</th></tr></thead>
        <tbody>
          <?php foreach ($list as $r): $sc = ['DRAFT'=>'warning','ACCEPTED'=>'success','REJECTED'=>'danger']; ?>
          <tr>
            <td><a href="?page=msme-erp&tcode=ML81N&id=<?= $r['id'] ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($r['ses_number']) ?></a></td>
            <td class="small"><?= $r['ses_date'] ?></td>
            <td class="small"><?= htmlspecialchars($r['po_number']) ?></td>
            <td class="small"><?= htmlspecialchars($r['supplier_name']) ?></td>
            <td class="small fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($r['total_amount'], 2) ?></td>
            <td><span class="badge bg-<?= $sc[$r['status']] ?? 'secondary' ?>"><?= $r['status'] ?></span></td>
            <td class="small"><?= $r['acceptance_doc_number'] ? htmlspecialchars($r['acceptance_doc_number']) : '—' ?></td>
            <td class="text-center">
              <a href="?page=msme-erp&tcode=ML81N&id=<?= $r['id'] ?>" class="btn btn-xs btn-outline-primary py-0 px-1 small"><i class="bi bi-eye"></i></a>
            </td>
          </tr>
          <?php endforeach; if (!$list): ?>
          <tr><td colspan="8" class="text-center text-muted py-4">No service entry sheets found. <?= $canCreate ? '<a href="?page=msme-erp&tcode=ML81N&new=1">Create one →</a>' : '' ?></td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>
  <?php if ($result['total_pages'] > 1): ?>
  <div class="card-footer bg-white">
    <nav><ul class="pagination pagination-sm mb-0 justify-content-end">
      <?php for ($p = 1; $p <= $result['total_pages']; $p++): ?>
      <li class="page-item <?= $p===$result['current_page']?'active':'' ?>"><a class="page-link" href="?page=msme-erp&tcode=ML81N&pg=<?= $p ?>&status=<?= $filterStatus ?>"><?= $p ?></a></li>
      <?php endfor; ?>
    </ul></nav>
  </div>
  <?php endif; ?>
</div>
