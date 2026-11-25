<?php
/**
 * MSME ERP — Manage Bids / Tenders (list + detail)
 * T-Code: BID02
 *
 * List of all Bids/Tenders created via BID01, drilling into a detail view
 * that shows the full header + Service Items and lets an authorized user
 * move the bid through its lifecycle: New -> Published -> Closed ->
 * Awarded, with Cancel available at any point before Awarded.
 *
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;

Auth::requireTcode('BID02');

$companyId = Auth::companyId();
$id        = (int)($_GET['id'] ?? 0);

// Allowed forward transitions — deliberately linear (no skipping a stage)
// so the published/submission/opening dates on the record actually mean
// something by the time a bid reaches AWARDED.
$transitions = [
    'NEW'       => ['PUBLISHED', 'CANCELLED'],
    'PUBLISHED' => ['CLOSED', 'CANCELLED'],
    'CLOSED'    => ['AWARDED', 'CANCELLED'],
];

// ---- POST: status change ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['action']) && $_POST['action'] === 'change_status') {
    try {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            throw new RuntimeException('Security check failed. Please try again.');
        }
        $bidId     = (int)($_POST['bid_id'] ?? 0);
        $newStatus = sanitize_text_field($_POST['new_status'] ?? '');
        $bid       = DB::fetchOne("SELECT * FROM bids WHERE id=? AND company_id=?", [$bidId, $companyId]);

        if (!$bid) {
            throw new RuntimeException('Bid not found.');
        }
        if (!in_array($newStatus, $transitions[$bid['status']] ?? [], true)) {
            throw new RuntimeException("Cannot move Bid from {$bid['status']} to {$newStatus}.");
        }

        $update = ['status' => $newStatus];
        if ($newStatus === 'PUBLISHED' && !$bid['bid_publish_date']) {
            $update['bid_publish_date'] = date('Y-m-d');
        }
        DB::update('bids', $update, 'id=?', [$bidId]);
        Auth::audit('bids', $bidId, 'UPDATE', ['status' => $bid['status']], ['status' => $newStatus]);
        msme_erp_set_flash("Bid {$bid['bid_number']} moved to " . ucfirst(strtolower($newStatus)) . '.', 'success');

    } catch (\Throwable $e) {
        error_log('MSME ERP BID02 status change error: ' . $e->getMessage());
        msme_erp_set_flash($e->getMessage(), 'danger');
    }
    wp_redirect(admin_url('admin.php?page=msme-erp&tcode=BID02&id=' . ($bidId ?? 0))); exit;
}

// ==================== DETAIL VIEW ====================
if ($id) {
    $bid = DB::fetchOne(
        "SELECT b.*, fc.code AS category_code, fc.name AS category_name,
                p.code AS unit_code, p.name AS unit_name,
                d.code AS dept_code, d.name AS dept_name,
                w.code AS warehouse_code, w.name AS warehouse_name,
                pr.pr_number AS src_pr_number,
                u.full_name AS created_by_name
         FROM bids b
         LEFT JOIN factory_categories fc ON fc.id = b.category_id
         LEFT JOIN plants p ON p.id = b.unit_id
         LEFT JOIN departments d ON d.id = b.department_id
         LEFT JOIN warehouses w ON w.id = b.warehouse_id
         LEFT JOIN purchase_requisitions pr ON pr.id = b.pr_id
         LEFT JOIN users u ON u.id = b.created_by
         WHERE b.id = ? AND b.company_id = ?",
        [$id, $companyId]
    );
    if (!$bid) { wp_redirect(admin_url('admin.php?page=msme-erp&tcode=BID02')); exit; }

    $items = DB::fetchAll(
        "SELECT bi.*, u.code AS uom_code FROM bid_items bi LEFT JOIN units_of_measure u ON u.id = bi.uom_id
         WHERE bi.bid_id = ? ORDER BY bi.line_num", [$id]
    );

    $pageTitle   = 'Bid / Tender: ' . $bid['bid_number'];
    $pageTcode   = 'BID02';
    $breadcrumbs = ['MM', 'Purchasing', 'Bid: ' . $bid['bid_number']];
    $pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=BID01') . '" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New Bid</a>
        <a href="javascript:window.print()" class="btn btn-sm btn-outline-secondary"><i class="bi bi-printer"></i> Print</a>
        <a href="' . admin_url('admin.php?page=msme-erp&tcode=BID02') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left"></i> Back</a>';

    erp_render_page_header();
    ?>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center py-2">
        <span class="fw-bold"><i class="bi bi-clipboard2-check me-2"></i><?= htmlspecialchars($bid['bid_number']) ?></span>
        <?= erp_status_badge($bid['status']) ?>
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-6">
            <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
              <tr><th width="160" class="text-muted">Bid Type:</th><td><?= htmlspecialchars($bid['bid_type']) ?></td></tr>
              <tr><th class="text-muted">Requirement Type:</th><td><?= htmlspecialchars($bid['requirement_type']) ?></td></tr>
              <tr><th class="text-muted">Mode of Bid:</th><td><?= htmlspecialchars($bid['mode_of_bid']) ?></td></tr>
              <tr><th class="text-muted">Sche. Delivery Days:</th><td><?= (int)$bid['sche_delivery_days'] ?></td></tr>
              <tr><th class="text-muted">Category / Unit / Dept:</th><td>
                <?= $bid['category_code'] ? htmlspecialchars($bid['category_code'] . ' — ' . $bid['category_name']) : '—' ?> /
                <?= $bid['unit_code'] ? htmlspecialchars($bid['unit_code']) : '—' ?> /
                <?= $bid['dept_code'] ? htmlspecialchars($bid['dept_code']) : '—' ?>
              </td></tr>
              <tr><th class="text-muted">Storage Location:</th><td><?= $bid['warehouse_code'] ? htmlspecialchars($bid['warehouse_code'] . ' — ' . $bid['warehouse_name']) : '—' ?></td></tr>
              <tr><th class="text-muted">Purch. Group:</th><td><?= htmlspecialchars($bid['purch_group'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Source PR:</th><td><?= htmlspecialchars($bid['src_pr_number'] ?: '—') ?></td></tr>
            </table>
          </div>
          <div class="col-md-6">
            <table class="table table-sm table-borderless mb-0" style="font-size:.83rem">
              <tr><th width="160" class="text-muted">Bid Creation Date:</th><td><?= htmlspecialchars($bid['bid_creation_date'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Bid Publish Date:</th><td><?= htmlspecialchars($bid['bid_publish_date'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Download Start:</th><td><?= htmlspecialchars($bid['download_start_dt'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Bid Subm. End:</th><td><?= htmlspecialchars($bid['bid_subm_end_dt'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Bid Open Date/Time:</th><td><?= htmlspecialchars($bid['bid_open_dt'] ?: '—') ?></td></tr>
              <tr><th class="text-muted">Created By:</th><td><?= htmlspecialchars($bid['created_by_name'] ?: '—') ?></td></tr>
            </table>
          </div>
          <div class="col-12">
            <strong class="small text-muted">Title of Bid:</strong>
            <div class="small"><?= nl2br(htmlspecialchars($bid['title_of_bid'])) ?></div>
          </div>
        </div>
      </div>
    </div>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2">Bidding Terms</div>
      <div class="card-body">
        <div class="row g-3" style="font-size:.83rem">
          <div class="col-md-3"><span class="text-muted">Bid Stage:</span> <?= htmlspecialchars($bid['bid_stage']) ?></div>
          <div class="col-md-3"><span class="text-muted">Bid Security:</span> <?= $bid['bid_security_applic'] ? 'Yes (' . msme_erp_cur() . '' . number_format($bid['bid_security_amount'], 2) . ')' : 'No' ?></div>
          <div class="col-md-3"><span class="text-muted">Bid Spec. Cost:</span> <?= $bid['bid_spec_applic'] ? '' . msme_erp_cur() . '' . number_format($bid['bid_spec_cost'], 2) : 'No' ?></div>
          <div class="col-md-3"><span class="text-muted">Tax Rate:</span> <?= number_format($bid['tax_rate'], 2) ?>%</div>
          <div class="col-md-3"><span class="text-muted">Foreign Currency:</span> <?= $bid['foreign_curr_appli'] ? 'Yes' : 'No' ?></div>
          <div class="col-md-3"><span class="text-muted">E-Proc Applicable:</span> <?= $bid['e_proc_applicable'] ? 'Yes' : 'No' ?></div>
          <div class="col-md-3"><span class="text-muted">E-Reverse Auction:</span> <?= $bid['e_reverse_auction'] ? 'Yes' : 'No' ?></div>
          <div class="col-md-3"><span class="text-muted">Bid Value:</span> <?= esc_html(msme_erp_cur()) ?><?= number_format($bid['bid_value'], 2) ?></div>
        </div>
      </div>
    </div>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list-ul me-2"></i>Service / Bid Items</div>
      <div class="card-body p-0">
        <table class="table table-sm table-hover mb-0 erp-table">
          <thead><tr><th>Line</th><th>Description</th><th>Qty</th><th>UOM</th><th>Est. Rate</th><th>Est. Value</th></tr></thead>
          <tbody>
            <?php $totalEst = 0; foreach ($items as $it): $totalEst += $it['estimated_value']; ?>
            <tr>
              <td class="text-muted small"><?= $it['line_num'] ?></td>
              <td class="small"><?= htmlspecialchars($it['description']) ?></td>
              <td class="small"><?= number_format($it['qty'], 3) ?></td>
              <td class="small"><?= htmlspecialchars($it['uom_code'] ?? '—') ?></td>
              <td class="small"><?= number_format($it['estimated_rate'], 2) ?></td>
              <td class="small fw-bold"><?= number_format($it['estimated_value'], 2) ?></td>
            </tr>
            <?php endforeach; ?>
            <?php if (!$items): ?>
            <tr><td colspan="6" class="text-center text-muted py-3">No items on this bid.</td></tr>
            <?php endif; ?>
          </tbody>
          <?php if ($items): ?>
          <tfoot><tr><th colspan="5" class="text-end">Total Estimated Value</th><th><?= number_format($totalEst, 2) ?></th></tr></tfoot>
          <?php endif; ?>
        </table>
      </div>
    </div>

    <?php
    // ---- Data prep for the analytics panel below ----
    $itemLabels = $itemValues = $itemCum = [];
    $running = 0;
    foreach ($items as $it) {
        $itemLabels[] = mb_strimwidth($it['description'], 0, 18, '…');
        $itemValues[] = round((float)$it['estimated_value'], 2);
        $running += (float)$it['estimated_value'];
        $itemCum[] = round($running, 2);
    }
    $taxAmount   = round($totalEst * ((float)$bid['tax_rate'] / 100), 2);
    $grandTotal  = round($totalEst + $taxAmount, 2);

    $now = time();
    $daysToSubmit = $bid['bid_subm_end_dt'] ? floor((strtotime($bid['bid_subm_end_dt']) - $now) / 86400) : null;

    $milestones = [
        ['label' => 'Bid Created',       'value' => $bid['bid_creation_date'],   'icon' => 'bi-flag-fill'],
        ['label' => 'Bid Published',     'value' => $bid['bid_publish_date'],    'icon' => 'bi-megaphone-fill'],
        ['label' => 'Download Opens',    'value' => $bid['download_start_dt'],   'icon' => 'bi-download'],
        ['label' => 'Submission Closes', 'value' => $bid['bid_subm_end_dt'],     'icon' => 'bi-hourglass-split'],
        ['label' => 'Bid Opens',         'value' => $bid['bid_open_dt'],         'icon' => 'bi-unlock-fill'],
    ];
    ?>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2 d-flex justify-content-between align-items-center">
        <span><i class="bi bi-graph-up-arrow me-2"></i>Bidding Graph &amp; Analytics</span>
        <?= erp_status_badge($bid['status']) ?>
      </div>
      <div class="card-body">
        <div class="row g-3">

          <!-- Left column: snapshot metric cards + timeline feed, mirroring the
               "Real-Time Metrics" / "AI Strategy" / "Market Feed" side panels -->
          <div class="col-lg-3">
            <div class="border rounded p-3 mb-3" style="background:#f8f9fb">
              <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Snapshot</div>
              <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Total Items:</span><strong><?= count($items) ?></strong></div>
              <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Est. Value:</span><strong><?= esc_html(msme_erp_cur()) ?><?= number_format($totalEst, 2) ?></strong></div>
              <div class="d-flex justify-content-between small mb-1"><span class="text-muted">Tax (<?= number_format($bid['tax_rate'], 2) ?>%):</span><strong><?= esc_html(msme_erp_cur()) ?><?= number_format($taxAmount, 2) ?></strong></div>
              <div class="d-flex justify-content-between small"><span class="text-muted">Grand Total:</span><strong class="text-primary"><?= esc_html(msme_erp_cur()) ?><?= number_format($grandTotal, 2) ?></strong></div>
            </div>

            <div class="border rounded p-3 mb-3" style="background:#f0f7ff;border-color:#bfe0ff !important">
              <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Mode: <?= htmlspecialchars($bid['mode_of_bid']) ?></div>
              <span class="badge bg-<?= in_array($bid['status'], ['NEW','PUBLISHED']) ? 'success' : 'secondary' ?>">
                <i class="bi bi-circle-fill me-1" style="font-size:.55rem"></i><?= in_array($bid['status'], ['NEW','PUBLISHED']) ? 'Active' : ucfirst(strtolower($bid['status'])) ?>
              </span>
              <?php if ($daysToSubmit !== null): ?>
              <div class="small text-muted mt-2"><?= $daysToSubmit >= 0 ? "{$daysToSubmit} day(s) to submission close" : 'Submission window closed' ?></div>
              <?php endif; ?>
            </div>

            <div class="border rounded p-3" style="background:#f8f9fb">
              <div class="text-uppercase text-muted small fw-bold mb-2" style="letter-spacing:.05em">Bid Timeline</div>
              <?php foreach ($milestones as $m): ?>
              <div class="d-flex align-items-center small mb-2">
                <i class="bi <?= $m['icon'] ?> me-2 <?= $m['value'] ? 'text-primary' : 'text-muted' ?>"></i>
                <div class="flex-grow-1"><?= $m['label'] ?></div>
                <div class="<?= $m['value'] ? 'fw-bold' : 'text-muted' ?>"><?= $m['value'] ? htmlspecialchars($m['value']) : '—' ?></div>
              </div>
              <?php endforeach; ?>
            </div>
          </div>

          <!-- Right column: main charts, mirroring the big screen + bar chart -->
          <div class="col-lg-9">
            <div class="border rounded p-3 mb-3">
              <div class="small fw-bold text-muted mb-2"><i class="bi bi-activity me-1"></i>Cumulative Estimated Value by Line</div>
              <canvas id="bidTrendChart" height="90"></canvas>
            </div>
            <div class="border rounded p-3">
              <div class="small fw-bold text-muted mb-2"><i class="bi bi-bar-chart-fill me-1"></i>Estimated Value by Item</div>
              <canvas id="bidVolumeChart" height="110"></canvas>
            </div>
          </div>

        </div>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script>
    (function(){
      var labels = <?= wp_json_encode($itemLabels) ?>;
      var values = <?= wp_json_encode($itemValues) ?>;
      var cum    = <?= wp_json_encode($itemCum) ?>;

      if (!labels.length) { return; }

      new Chart(document.getElementById('bidTrendChart'), {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Cumulative Est. Value (<?= esc_js(msme_erp_cur()) ?>)',
            data: cum,
            borderColor: '#1565c0',
            backgroundColor: 'rgba(21,101,192,0.10)',
            borderWidth: 2,
            pointBackgroundColor: '#f9a825',
            pointRadius: 4,
            tension: 0.35,
            fill: true,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { y: { ticks: { callback: v => '<?= esc_js(msme_erp_cur()) ?>' + v.toLocaleString('en-IN') } } }
        }
      });

      new Chart(document.getElementById('bidVolumeChart'), {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Est. Value (<?= esc_js(msme_erp_cur()) ?>)',
            data: values,
            backgroundColor: '#1565c0',
            hoverBackgroundColor: '#f9a825',
            borderRadius: 4,
            maxBarThickness: 40,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { y: { ticks: { callback: v => '<?= esc_js(msme_erp_cur()) ?>' + v.toLocaleString('en-IN') } } }
        }
      });
    })();
    </script>

    <?php $next = $transitions[$bid['status']] ?? []; if ($next): ?>
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2">Move Bid Status</div>
      <div class="card-body d-flex gap-2">
        <?php foreach ($next as $ns):
          $btnClass = $ns === 'CANCELLED' ? 'btn-outline-danger' : 'btn-primary';
          $label = ['PUBLISHED' => 'Publish', 'CLOSED' => 'Close Submission', 'AWARDED' => 'Mark Awarded', 'CANCELLED' => 'Cancel Bid'][$ns] ?? $ns;
        ?>
        <form method="POST" onsubmit="return confirm('<?= esc_js($label) ?> — are you sure?');">
          <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
          <input type="hidden" name="action" value="change_status">
          <input type="hidden" name="bid_id" value="<?= $bid['id'] ?>">
          <input type="hidden" name="new_status" value="<?= $ns ?>">
          <button type="submit" class="btn btn-sm <?= $btnClass ?>"><?= htmlspecialchars($label) ?></button>
        </form>
        <?php endforeach; ?>
      </div>
    </div>
    <?php endif; ?>

    <?php
    return;
}

// ==================== LIST VIEW ====================
$statusFilter = sanitize_text_field($_GET['status'] ?? '');
$where  = "WHERE b.company_id = ?";
$params = [$companyId];
if ($statusFilter !== '') { $where .= " AND b.status = ?"; $params[] = $statusFilter; }

$bids = DB::fetchAll(
    "SELECT b.*, (SELECT COUNT(*) FROM " . DB::tbl('bid_items') . " bi WHERE bi.bid_id = b.id) AS item_count
     FROM bids b {$where} ORDER BY b.id DESC",
    $params
);

$pageTitle   = 'Manage Bids / Tenders';
$pageTcode   = 'BID02';
$breadcrumbs = ['MM', 'Purchasing', 'Manage Bids / Tenders'];
$pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=BID01') . '" class="btn btn-sm btn-primary"><i class="bi bi-plus-lg"></i> New Bid</a>';

erp_render_page_header();
?>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-body py-2">
    <form method="GET" class="row g-2 align-items-end">
      <input type="hidden" name="page" value="msme-erp">
      <input type="hidden" name="tcode" value="BID02">
      <div class="col-md-3">
        <select name="status" class="form-select form-select-sm" onchange="this.form.submit()">
          <option value="">All Statuses</option>
          <?php foreach (['NEW','PUBLISHED','CLOSED','AWARDED','CANCELLED'] as $s): ?>
          <option value="<?= $s ?>" <?= $statusFilter === $s ? 'selected' : '' ?>><?= ucfirst(strtolower($s)) ?></option>
          <?php endforeach; ?>
        </select>
      </div>
    </form>
  </div>
</div>

<div class="card border-0 shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0 erp-table">
        <thead>
          <tr><th>Bid No.</th><th>Title</th><th>Type</th><th>Stage</th><th>Items</th><th>Publish Date</th><th>Subm. End</th><th>Status</th></tr>
        </thead>
        <tbody>
          <?php foreach ($bids as $b): ?>
          <tr class="cursor-pointer" onclick="window.location='<?= admin_url('admin.php?page=msme-erp&tcode=BID02&id=' . $b['id']) ?>'" style="cursor:pointer">
            <td class="small fw-bold"><?= htmlspecialchars($b['bid_number']) ?></td>
            <td class="small"><?= htmlspecialchars(mb_strimwidth($b['title_of_bid'] ?? '', 0, 60, '…')) ?></td>
            <td class="small"><?= htmlspecialchars($b['bid_type']) ?></td>
            <td class="small"><?= htmlspecialchars($b['bid_stage']) ?></td>
            <td class="small"><?= (int)$b['item_count'] ?></td>
            <td class="small"><?= htmlspecialchars($b['bid_publish_date'] ?: '—') ?></td>
            <td class="small"><?= htmlspecialchars($b['bid_subm_end_dt'] ?: '—') ?></td>
            <td><?= erp_status_badge($b['status']) ?></td>
          </tr>
          <?php endforeach; ?>
          <?php if (!$bids): ?>
          <tr><td colspan="8" class="text-center text-muted py-4">No Bids/Tenders yet. <a href="<?= admin_url('admin.php?page=msme-erp&tcode=BID01') ?>">Create one (BID01)</a>.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>
</div>
