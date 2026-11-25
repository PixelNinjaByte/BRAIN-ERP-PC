<?php
/**
 * MSME ERP — Purchase Order List & View
 * T-Code: ME23N
 * Developed by: Prigag Online Pvt. Ltd.
 */

if (empty($tcode)) $tcode = strtoupper($_GET['tcode'] ?? 'ME23N');
Auth::requireTcode(in_array($tcode, ['ME23N','ME2M']) ? $tcode : 'ME23N');

$companyId    = Auth::companyId();
$currentTcode = 'ME23N';
$id           = (int)($_GET['id'] ?? 0);

// ---- Release Approve/Reject/Return ----
// Handled in-page (same pattern as the rest of this plugin) instead of
// via admin-post.php — see /areas/msme-erp notes on why that route was
// dropped.
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['decision'])) {
    try {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            throw new RuntimeException('Security check failed. Please try again.');
        }
        $releaseId = (int)($_POST['release_id'] ?? 0);
        $decision  = sanitize_text_field($_POST['decision']);
        if (!$releaseId || !in_array($decision, ['APPROVED', 'REJECTED', 'RETURNED'], true)) {
            throw new RuntimeException('Invalid release action.');
        }
        if (!Auth::canReleaseDocument($releaseId)) {
            throw new RuntimeException('You are not authorized to release at this level.');
        }
        ErpDB::releaseDocument($releaseId, $decision, Auth::userId(), sanitize_textarea_field($_POST['remarks'] ?? ''));
        $msgs = ['APPROVED' => 'PO approved successfully.', 'REJECTED' => 'PO rejected.', 'RETURNED' => 'PO returned for revision.'];
        msme_erp_set_flash($msgs[$decision], $decision === 'APPROVED' ? 'success' : 'warning');
    } catch (Throwable $e) {
        error_log('MSME ERP PO release error: ' . $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
        msme_erp_set_flash($e->getMessage(), 'danger');
    }
    wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . $id)); exit;
}

// ---- DETAIL VIEW ----
if ($id) {
    $po = DB::fetchOne(
        "SELECT po.*, s.name AS supplier_name, s.supplier_code, s.gstin AS supplier_gstin,
                s.pan_number AS supplier_pan, s.address AS supplier_addr, s.city AS supplier_city,
                s.state AS supplier_state, s.pincode AS supplier_pincode, s.phone AS supplier_phone,
                u.full_name AS created_by_name, c.name AS company_name, c.address AS comp_addr,
                c.city AS comp_city, c.state AS comp_state, c.pincode AS comp_pincode,
                c.phone AS comp_phone, c.email AS comp_email, c.gstin AS comp_gstin, c.pan_number AS comp_pan
         FROM purchase_orders po
         JOIN suppliers s ON s.id = po.supplier_id
         JOIN companies c ON c.id = po.company_id
         LEFT JOIN users u ON u.id = po.created_by
         WHERE po.id = ? AND po.company_id = ?",
        [$id, $companyId]
    );
    if (!$po) { wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME23N')); exit; }

    $lines = DB::fetchAll(
        "SELECT pol.*, i.item_code, i.item_name, u.code AS uom_code,
                pr.id AS src_pr_id, pr.pr_number AS src_pr_number
         FROM purchase_order_lines pol
         JOIN items i ON i.id = pol.item_id
         LEFT JOIN units_of_measure u ON u.id = pol.uom_id
         LEFT JOIN purchase_requisition_lines prl ON prl.id = pol.pr_line_id
         LEFT JOIN purchase_requisitions pr ON pr.id = prl.pr_id
         WHERE pol.po_id = ? ORDER BY pol.line_num",
        [$id]
    );

    // Rate-wise CGST/SGST breakdown, same grouping logic as the sales-invoice
    // print (VF03) — a PO mixing tax rates prints one CGST+SGST pair per rate.
    $poRateGroups = [];
    foreach ($lines as $l) {
        $rate = (float)$l['gst_rate'];
        $taxableLine = $l['qty_ordered'] * $l['unit_price'] * (1 - $l['discount_pct'] / 100);
        $gstLine     = $taxableLine * $rate / 100;
        if (!isset($poRateGroups[$rate])) $poRateGroups[$rate] = ['taxable' => 0, 'gst' => 0];
        $poRateGroups[$rate]['taxable'] += $taxableLine;
        $poRateGroups[$rate]['gst']     += $gstLine;
    }
    ksort($poRateGroups);

    // Company's primary active bank account, for the "Bank Details" block
    // on the printed PO (so the supplier can verify/refund/quote against it).
    $companyBank = DB::fetchOne(
        "SELECT * FROM bank_accounts WHERE company_id=? AND is_active=1 ORDER BY id LIMIT 1",
        [$companyId]
    );

    $releases = DB::fetchAll(
        "SELECT ra.*, u.full_name AS actor_name, dr.current_level
         FROM release_actions ra
         LEFT JOIN document_releases dr ON dr.id = ra.release_id
         LEFT JOIN users u ON u.id = ra.action_by
         WHERE ra.release_id = ? ORDER BY ra.action_at",
        [$po['release_id'] ?? 0]
    );

    $releaseDoc = $po['release_id'] ? DB::fetchOne("SELECT * FROM document_releases WHERE id=?", [$po['release_id']]) : null;
    $canRelease  = $releaseDoc && $releaseDoc['status'] === 'PENDING' && Auth::canReleaseDocument($po['release_id']);

    $pageTitle   = 'Purchase Order: ' . $po['po_number'];
    $pageTcode   = 'ME23N';
    $breadcrumbs = ['MM', 'Purchasing', 'PO: ' . $po['po_number']];
    $pageActions = '<div class="d-flex gap-2">
        ' . (Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN']) && $po['status']==='DRAFT' ?
          '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ME21N') . '" class="btn btn-sm btn-primary"><i class="bi bi-bag-plus"></i> New PO</a>' : '') . '
        ' . msme_erp_doc_actions_html('po', (int)$po['id']) . '
        <a href="' . admin_url('admin.php?page=msme-erp&tcode=ME23N') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left"></i> Back</a>
      </div>';

    erp_render_page_header();

    

    $releaseColors= ['NOT_RELEASED'=>'warning','RELEASED'=>'success','REJECTED'=>'danger'];
?>

<!-- PO Status Bar (operational — not part of the printed document) -->
<div class="card border-0 shadow-sm mb-3 no-print">
  <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center py-2">
    <span class="fw-bold"><i class="bi bi-bag me-2"></i><?= htmlspecialchars($po['po_number']) ?></span>
    <div class="d-flex gap-2">
      <span class="badge bg-light text-dark"><?= htmlspecialchars($po['status']) ?></span>
      <span class="badge bg-<?= $releaseColors[$po['release_status']] ?? 'secondary' ?>"><?= $po['release_status'] ?></span>
    </div>
  </div>
  <div class="card-body py-2">
    <span class="small text-muted">Created By: <?= htmlspecialchars($po['created_by_name'] ?? '—') ?></span>
  </div>
</div>

<!-- Printable Purchase Order -->
<div class="card border shadow-sm mb-3" id="printable-po">
  <div class="card-body p-4">
    <?= msme_erp_doc_po_inner($po, $lines, $poRateGroups, $companyBank) ?>
  </div>
</div>

<!-- Line Items (internal tracking — receipt/approval status, not printed) -->
<div class="card border-0 shadow-sm mb-3 no-print">
  <div class="card-header bg-white fw-bold small py-2">
    <i class="bi bi-list-ul me-2"></i>Line Items — Receipt Status
  </div>
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0 erp-table">
        <thead>
          <tr>
            <th>Line</th><th>Material</th><th>Qty Ordered</th><th>Qty Received</th>
            <th>Unit Price</th><th>Disc%</th><th>GST%</th><th>Net Value</th><th>Status</th><th>Source PR</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($lines as $l): ?>
          <tr>
            <td class="text-muted small"><?= $l['line_num'] ?></td>
            <td>
              <div class="fw-semibold small"><?= htmlspecialchars($l['item_code']) ?></div>
              <div class="text-muted" style="font-size:.72rem"><?= htmlspecialchars($l['item_name']) ?></div>
            </td>
            <td class="small"><?= number_format($l['qty_ordered'],3) ?> <?= $l['uom_code'] ?></td>
            <td class="small <?= $l['qty_received'] >= $l['qty_ordered'] ? 'text-success fw-bold' : '' ?>">
              <?= number_format($l['qty_received'],3) ?>
            </td>
            <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($l['unit_price'],2) ?></td>
            <td class="small"><?= $l['discount_pct'] ?>%</td>
            <td class="small"><?= $l['gst_rate'] ?>%</td>
            <td class="small fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($l['line_total'],2) ?></td>
            <td><span class="badge bg-<?= $l['status']==='CLOSED'?'primary':($l['status']==='OPEN'?'warning':'secondary') ?> small"><?= $l['status'] ?></span></td>
            <td class="small">
              <?php if ($l['src_pr_number']): ?>
              <a href="?page=msme-erp&tcode=ME53N&id=<?= (int)$l['src_pr_id'] ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($l['src_pr_number']) ?></a>
              <?php else: ?>—<?php endif; ?>
            </td>
          </tr>
          <?php endforeach; ?>
        </tbody>
        <tfoot class="table-light fw-bold">
          <tr>
            <td colspan="8" class="text-end small">Taxable Amount:</td>
            <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($po['total_amount'],2) ?></td>
          </tr>
          <tr>
            <td colspan="8" class="text-end small">GST Amount:</td>
            <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($po['tax_amount'],2) ?></td>
          </tr>
          <tr class="table-success">
            <td colspan="8" class="text-end fw-bold">Grand Total:</td>
            <td class="fw-bold text-success"><?= esc_html(msme_erp_cur()) ?><?= number_format($po['grand_total'],2) ?></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</div>

<!-- Release / Approval Trail -->
<?php if ($releaseDoc): ?>
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2 d-flex justify-content-between">
    <span><i class="bi bi-diagram-3 me-2"></i>Releasing Strategy / Approval Trail</span>
    <?php if ($canRelease): ?>
    <button class="btn btn-sm btn-success" data-bs-toggle="modal" data-bs-target="#releaseModal">
      <i class="bi bi-check2-circle"></i> Take Action
    </button>
    <?php endif; ?>
  </div>
  <div class="card-body">
    <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
      <span class="badge bg-primary">Level <?= $releaseDoc['current_level'] ?></span>
      <span class="badge bg-<?= $releaseDoc['status']==='RELEASED'?'success':($releaseDoc['status']==='REJECTED'?'danger':'warning') ?>">
        <?= $releaseDoc['status'] ?>
      </span>
      <span class="text-muted small">Doc Amount: <?= esc_html(msme_erp_cur()) ?><?= number_format($releaseDoc['doc_amount'],2) ?></span>
    </div>
    <?php if ($releases): ?>
    <table class="table table-sm small" style="font-size:.78rem">
      <thead class="table-light"><tr><th>Level</th><th>Action</th><th>By</th><th>Date</th><th>Remarks</th></tr></thead>
      <tbody>
        <?php foreach ($releases as $r): ?>
        <tr>
          <td>L<?= $r['level_num'] ?></td>
          <td><span class="badge bg-<?= $r['action']==='APPROVED'?'success':($r['action']==='REJECTED'?'danger':'warning') ?>"><?= $r['action'] ?></span></td>
          <td><?= htmlspecialchars($r['actor_name'] ?? '—') ?></td>
          <td><?= $r['action_at'] ?></td>
          <td><?= htmlspecialchars($r['remarks'] ?? '') ?></td>
        </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
    <?php else: ?>
    <div class="text-muted small">Awaiting first level approval...</div>
    <?php endif; ?>
  </div>
</div>
<?php endif; ?>

<!-- Release Action Modal -->
<?php if ($canRelease): ?>
<div class="modal fade" id="releaseModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <form method="POST" action="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . $id) ?>">
      <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
      <input type="hidden" name="release_id" value="<?= $po['release_id'] ?>">
      <div class="modal-header bg-primary text-white">
        <h5 class="modal-title small"><i class="bi bi-clipboard-check me-2"></i>Release Action — <?= htmlspecialchars($po['po_number']) ?></h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p class="small text-muted">You are acting as releasing authority for Level <?= $releaseDoc['current_level'] ?>.</p>
        <div class="mb-3">
          <label class="form-label fw-semibold small">Decision <span class="text-danger">*</span></label>
          <div class="d-flex gap-3">
            <div class="form-check">
              <input class="form-check-input" type="radio" name="decision" value="APPROVED" id="act-approve" required>
              <label class="form-check-label text-success fw-semibold" for="act-approve"><i class="bi bi-check-circle"></i> Approve</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="decision" value="REJECTED" id="act-reject">
              <label class="form-check-label text-danger fw-semibold" for="act-reject"><i class="bi bi-x-circle"></i> Reject</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="decision" value="RETURNED" id="act-return">
              <label class="form-check-label text-warning fw-semibold" for="act-return"><i class="bi bi-arrow-return-left"></i> Return</label>
            </div>
          </div>
        </div>
        <div class="mb-2">
          <label class="form-label fw-semibold small">Remarks</label>
          <textarea name="remarks" class="form-control form-control-sm" rows="3" placeholder="Optional remarks..."></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button type="submit" class="btn btn-primary btn-sm"><i class="bi bi-send"></i> Submit Decision</button>
        <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Cancel</button>
      </div>
      </form>
    </div>
  </div>
</div>
<?php endif; ?>

<!-- GRN Button (if PO approved) -->
<?php if (in_array($po['status'], ['APPROVED','PARTIAL']) && Auth::hasRole(['STORE_MANAGER','SUPER_ADMIN'])): ?>
<div class="mt-3">
  <a href="<?= admin_url('admin.php?page=msme-erp&tcode=MIGO') ?>&po_id=<?= $id ?>" class="btn btn-success">
    <i class="bi bi-arrow-down-circle me-1"></i>Post Goods Receipt (MIGO)
  </a>
</div>
<?php endif; ?>

<?php
    
    return;
}

// ---- LIST VIEW ----
$pageTitle   = 'Purchase Orders';
$pageTcode   = 'ME23N';
$breadcrumbs = ['MM', 'Purchasing', 'Purchase Orders'];
$pageActions = Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN'])
    ? '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ME21N') . '" class="btn btn-primary btn-sm"><i class="bi bi-bag-plus me-1"></i>Create PO (ME21N)</a>'
    : '';

erp_render_page_header();

// Filters
$filterStatus   = $_GET['status'] ?? '';
$filterSupplier = $_GET['supplier_id'] ?? '';
$filterDate     = $_GET['from_date'] ?? '';

$sql    = "SELECT po.id, po.po_number, po.po_date, po.delivery_date, s.name AS supplier, po.grand_total, po.status, po.release_status
           FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
           WHERE po.company_id=? AND po.is_deleted=0";
$params = [$companyId];
if ($filterStatus)   { $sql .= " AND po.status=?";       $params[] = $filterStatus; }
if ($filterSupplier) { $sql .= " AND po.supplier_id=?";  $params[] = $filterSupplier; }
if ($filterDate)     { $sql .= " AND po.po_date >= ?";   $params[] = $filterDate; }
$sql .= " ORDER BY po.id DESC";

// NOTE: pagination uses 'pg', not 'page' — WordPress's own admin router
// already uses the 'page' GET param to mean the plugin page slug
// (page=msme-erp). Reading/writing a pagination page number under the
// same 'page' key overwrote that slug on every paginated or filtered
// link, dropping the site straight to a blank wp-admin screen — the
// same failure mode as the ME23N/PO26000001 hyperlink bug.
$page    = max(1, (int)($_GET['pg'] ?? 1));
$result  = DB::paginate($sql, $params, $page);
$pos     = $result['data'];
$suppliers = DB::fetchAll("SELECT id, name FROM suppliers WHERE company_id=? ORDER BY name", [$companyId]);


?>

<!-- Filters -->
<div class="card border-0 shadow-sm mb-3">
  <div class="card-body py-2">
    <form class="row g-2 align-items-end" method="GET">
      <input type="hidden" name="page" value="msme-erp">
      <input type="hidden" name="tcode" value="ME23N">
      <div class="col-md-2">
        <label class="form-label small mb-1">Status</label>
        <select name="status" class="form-select form-select-sm">
          <option value="">All</option>
          <?php foreach (['DRAFT','APPROVED','PARTIAL','CLOSED','CANCELLED','REJECTED'] as $st): ?>
          <option value="<?= $st ?>" <?= $filterStatus===$st?'selected':'' ?>><?= $st ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-md-3">
        <label class="form-label small mb-1">Supplier</label>
        <select name="supplier_id" class="form-select form-select-sm">
          <option value="">All Suppliers</option>
          <?php foreach ($suppliers as $s): ?>
          <option value="<?= $s['id'] ?>" <?= $filterSupplier==$s['id']?'selected':'' ?>><?= htmlspecialchars($s['name']) ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-md-2">
        <label class="form-label small mb-1">From Date</label>
        <input type="date" name="from_date" class="form-control form-control-sm" value="<?= $filterDate ?>">
      </div>
      <div class="col-auto">
        <button class="btn btn-primary btn-sm"><i class="bi bi-search me-1"></i>Filter</button>
        <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N') ?>" class="btn btn-outline-secondary btn-sm ms-1"><i class="bi bi-x"></i> Clear</a>
      </div>
    </form>
  </div>
</div>

<!-- Table -->
<div class="card border-0 shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-hover table-sm mb-0 erp-table">
        <thead>
          <tr>
            <th>PO Number</th><th>Date</th><th>Supplier</th>
            <th>Grand Total</th><th>Status</th><th>Release</th>
            <th>Delivery</th><th class="text-center">Action</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($pos as $po):
            $sc = ['DRAFT'=>'secondary','APPROVED'=>'success','PARTIAL'=>'info','CLOSED'=>'primary','CANCELLED'=>'danger','REJECTED'=>'danger'];
            $rc = ['NOT_RELEASED'=>'warning','RELEASED'=>'success','REJECTED'=>'danger'];
          ?>
          <tr>
            <td><a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . $po['id']) ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($po['po_number']) ?></a></td>
            <td class="small"><?= $po['po_date'] ?></td>
            <td class="small"><?= htmlspecialchars($po['supplier']) ?></td>
            <td class="small fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($po['grand_total'],2) ?></td>
            <td><span class="badge bg-<?= $sc[$po['status']]??'secondary' ?>"><?= $po['status'] ?></span></td>
            <td><span class="badge bg-<?= $rc[$po['release_status']]??'secondary' ?>"><?= $po['release_status'] ?></span></td>
            <td class="small"><?= $po['delivery_date'] ?? '—' ?></td>
            <td class="text-center">
              <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . $po['id']) ?>" class="btn btn-xs btn-outline-primary py-0 px-1 small">
                <i class="bi bi-eye"></i>
              </a>
            </td>
          </tr>
          <?php endforeach; if (!$pos): ?>
          <tr><td colspan="8" class="text-center text-muted py-4">No purchase orders found</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>
  <!-- Pagination -->
  <?php if ($result['total_pages'] > 1): ?>
  <div class="card-footer bg-white">
    <nav><ul class="pagination pagination-sm mb-0 justify-content-end">
      <?php for ($p = 1; $p <= $result['total_pages']; $p++): ?>
      <li class="page-item <?= $p===$result['current_page']?'active':'' ?>">
        <a class="page-link" href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N') . '&pg=' . $p . '&status=' . urlencode($filterStatus) . '&supplier_id=' . urlencode($filterSupplier) ?>"><?= $p ?></a>
      </li>
      <?php endfor; ?>
    </ul></nav>
  </div>
  <?php endif; ?>
</div>

<?php  ?>
