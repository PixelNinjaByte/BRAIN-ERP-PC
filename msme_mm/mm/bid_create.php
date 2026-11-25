<?php
/**
 * MSME ERP — Create Bid / Tender
 * T-Code: BID01
 *
 * A formal public tender, distinct from ME41's RFQ (a direct request to
 * named suppliers): carries its own publish/submission/opening schedule,
 * bid security & bid-document-cost terms, and optionally sources its line
 * items from an existing Purchase Requisition. Layout mirrors a typical
 * e-Procurement "Create Bid" screen — Header Details, then a
 * Bidding Details / Purchase Requisition Details / Service Items tab set.
 *
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;

Auth::requireTcode('BID01');

$companyId = Auth::companyId();
$error = $success = '';

// ---- Lookups for the header dropdowns ----
$categories = DB::fetchAll("SELECT id, code, name FROM factory_categories WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
$units      = DB::fetchAll("SELECT id, code, name, category_id FROM plants WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
$depts      = DB::fetchAll("SELECT id, code, name, plant_id FROM departments WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
$warehouses = DB::fetchAll("SELECT id, code, name FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);

$eligiblePRs = DB::fetchAll(
    "SELECT pr.id, pr.pr_number, pr.pr_date, pr.status, COUNT(prl.id) AS line_count
     FROM purchase_requisitions pr
     JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
     WHERE pr.company_id = ? AND pr.status IN ('SUBMITTED','APPROVED')
     GROUP BY pr.id ORDER BY pr.id DESC",
    [$companyId]
);

// ---- POST: create the Bid ----
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
        $error = 'Security check failed. Please try again.';
    } else {
        $prId    = (int)($_POST['pr_id'] ?? 0);
        $titleOfBid = trim($_POST['title_of_bid'] ?? '');

        $descs   = $_POST['item_description'] ?? [];
        $qtys    = $_POST['item_qty'] ?? [];
        $uoms    = $_POST['item_uom_id'] ?? [];
        $rates   = $_POST['item_rate'] ?? [];
        $prLineIds = $_POST['item_pr_line_id'] ?? [];

        $items = [];
        foreach ($descs as $i => $d) {
            $d = trim($d);
            if ($d === '') continue;
            $qty  = (float)($qtys[$i] ?? 0);
            $rate = (float)($rates[$i] ?? 0);
            $items[] = [
                'description' => $d,
                'qty'         => $qty,
                'uom_id'      => !empty($uoms[$i]) ? (int)$uoms[$i] : null,
                'rate'        => $rate,
                'value'       => round($qty * $rate, 2),
                'pr_line_id'  => !empty($prLineIds[$i]) ? (int)$prLineIds[$i] : null,
            ];
        }

        if ($titleOfBid === '') {
            $error = 'Title of Bid is required.';
        } elseif (!$items) {
            $error = 'Add at least one item/service line before saving the bid.';
        } else {
            try {
                DB::beginTransaction();

                $bidNumber = DB::getNextNumber($companyId, 'BID', (int)date('Y'));

                $header = [
                    'company_id'             => $companyId,
                    'bid_number'             => $bidNumber,
                    'bid_type'               => sanitize_text_field($_POST['bid_type'] ?? 'SERVICE'),
                    'requirement_type'       => sanitize_text_field($_POST['requirement_type'] ?? 'OTHERS'),
                    'mode_of_bid'            => sanitize_text_field($_POST['mode_of_bid'] ?? 'OPEN'),
                    'sche_delivery_days'     => (int)($_POST['sche_delivery_days'] ?? 0),
                    'bid_creation_date'      => $_POST['bid_creation_date'] ?: date('Y-m-d'),
                    'warehouse_id'           => !empty($_POST['warehouse_id']) ? (int)$_POST['warehouse_id'] : null,
                    'purch_group'            => sanitize_text_field($_POST['purch_group'] ?? ''),
                    'status'                 => 'NEW',
                    'bid_publish_date'       => $_POST['bid_publish_date'] ?: null,
                    'bid_stage'              => sanitize_text_field($_POST['bid_stage'] ?? 'SINGLE_PART'),
                    'bid_security_applic'    => !empty($_POST['bid_security_applic']) ? 1 : 0,
                    'bid_security_amount'    => (float)($_POST['bid_security_amount'] ?? 0),
                    'bid_security_amount_fc' => (float)($_POST['bid_security_amount_fc'] ?? 0),
                    'foreign_curr_appli'     => !empty($_POST['foreign_curr_appli']) ? 1 : 0,
                    'tax_rate'               => (float)($_POST['tax_rate'] ?? 0),
                    'e_proc_applicable'      => !empty($_POST['e_proc_applicable']) ? 1 : 0,
                    'bid_spec_applic'        => !empty($_POST['bid_spec_applic']) ? 1 : 0,
                    'bid_spec_cost'          => (float)($_POST['bid_spec_cost'] ?? 0),
                    'bid_spec_cost_fc'       => (float)($_POST['bid_spec_cost_fc'] ?? 0),
                    'download_start_dt'      => $_POST['download_start_dt'] ? str_replace('T', ' ', $_POST['download_start_dt']) . ':00' : null,
                    'bid_subm_end_dt'        => $_POST['bid_subm_end_dt'] ? str_replace('T', ' ', $_POST['bid_subm_end_dt']) . ':00' : null,
                    'bid_open_dt'            => $_POST['bid_open_dt'] ? str_replace('T', ' ', $_POST['bid_open_dt']) . ':00' : null,
                    'title_of_bid'           => $titleOfBid,
                    'bid_value'              => (float)($_POST['bid_value'] ?? 0),
                    'e_reverse_auction'      => !empty($_POST['e_reverse_auction']) ? 1 : 0,
                    'pr_id'                  => $prId ?: null,
                    'created_by'             => Auth::userId(),
                ] + msme_erp_cost_center_from_post($_POST);

                $bidId = DB::insert('bids', $header);

                foreach ($items as $i => $it) {
                    DB::insert('bid_items', [
                        'bid_id'          => $bidId,
                        'line_num'        => ($i + 1) * 10,
                        'pr_line_id'      => $it['pr_line_id'],
                        'item_type'       => $header['bid_type'] === 'GOODS' ? 'MATERIAL' : 'SERVICE',
                        'description'     => $it['description'],
                        'qty'             => $it['qty'],
                        'uom_id'          => $it['uom_id'],
                        'estimated_rate'  => $it['rate'],
                        'estimated_value' => $it['value'],
                    ]);
                }

                Auth::audit('bids', $bidId, 'INSERT', [], ['bid_number' => $bidNumber, 'title_of_bid' => $titleOfBid]);
                DB::commit();

                msme_erp_set_flash("Bid {$bidNumber} created.", 'success');
                wp_redirect(admin_url('admin.php?page=msme-erp&tcode=BID02&id=' . $bidId)); exit;

            } catch (\Throwable $e) {
                DB::rollback();
                error_log('MSME ERP BID01 create error: ' . $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
                $error = 'Could not create Bid: ' . $e->getMessage();
            }
        }
    }
}

// ---- GET: PR picker (Purchase Requisition Details tab) ----
$selectedPrId  = (int)($_GET['pr_id'] ?? $_POST['pr_id'] ?? 0);
$selectedPr    = null;
$prLinesToShow = [];
if ($selectedPrId) {
    $selectedPr = DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?", [$selectedPrId, $companyId]);
    if ($selectedPr) {
        $prLinesToShow = DB::fetchAll(
            "SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
             FROM purchase_requisition_lines prl
             LEFT JOIN items i ON i.id=prl.item_id LEFT JOIN units_of_measure u ON u.id=prl.uom_id
             WHERE prl.pr_id=? ORDER BY prl.line_num", [$selectedPrId]
        );
    }
}

$uomList = DB::fetchAll("SELECT id, code FROM units_of_measure WHERE is_active=1 ORDER BY code", []);

$pageTitle   = 'Create Bid / Tender';
$pageTcode   = 'BID01';
$breadcrumbs = ['MM', 'Purchasing', 'Create Bid / Tender'];
$pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=BID02') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-clipboard-data-fill me-1"></i>Manage Bids (BID02)</a>';

erp_render_page_header();
?>

<?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>

<form method="POST" id="bidForm">
  <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
  <input type="hidden" name="pr_id" id="pr_id_hidden" value="<?= (int)$selectedPrId ?>">

  <!-- ===================== HEADER DETAILS ===================== -->
  <div class="card border-0 shadow-sm mb-3">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-card-heading me-2"></i>Header Details</div>
    <div class="card-body">
      <div class="row g-3">
        <div class="col-md-3">
          <label class="form-label small mb-1">Bid No.</label>
          <input type="text" class="form-control form-control-sm" value="(auto on save)" disabled>
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Bid Type</label>
          <select name="bid_type" class="form-select form-select-sm">
            <option value="SERVICE">Service</option>
            <option value="GOODS">Goods</option>
            <option value="WORKS">Works</option>
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Requirement Type</label>
          <select name="requirement_type" class="form-select form-select-sm">
            <option value="OTHERS">Others</option>
            <option value="NEW">New Requirement</option>
            <option value="REPEAT">Repeat Order</option>
            <option value="EMERGENCY">Emergency</option>
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Mode of Bid</label>
          <select name="mode_of_bid" class="form-select form-select-sm">
            <option value="OPEN">Open</option>
            <option value="LIMITED">Limited</option>
            <option value="SINGLE">Single</option>
          </select>
        </div>

        <div class="col-md-3">
          <label class="form-label small mb-1">Sche. Delivery Days</label>
          <input type="number" name="sche_delivery_days" class="form-control form-control-sm" min="0" value="0">
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Bid Creation Date</label>
          <input type="date" name="bid_creation_date" class="form-control form-control-sm" value="<?= date('Y-m-d') ?>">
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Status</label>
          <input type="text" class="form-control form-control-sm" value="New" disabled>
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Bid Publish Date</label>
          <input type="date" name="bid_publish_date" class="form-control form-control-sm">
        </div>

        <div class="col-md-3">
          <label class="form-label small mb-1">Purch. Group</label>
          <input type="text" name="purch_group" class="form-control form-control-sm" placeholder="e.g. AEN-I(T&amp;C) RTN">
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Storage Location</label>
          <select name="warehouse_id" class="form-select form-select-sm">
            <option value="">— None —</option>
            <?php foreach ($warehouses as $w): ?>
            <option value="<?= $w['id'] ?>"><?= htmlspecialchars($w['code'] . ' — ' . $w['name']) ?></option>
            <?php endforeach; ?>
          </select>
        </div>
      </div>
      <hr>
      <?php msme_erp_render_cost_center_fields($companyId); ?>
    </div>
  </div>

  <!-- ===================== TABS ===================== -->
  <ul class="nav nav-tabs mb-3" role="tablist">
    <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-bidding" type="button">Bidding Details</button></li>
    <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-pr" type="button">Purchase Requisition Details</button></li>
    <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-items" type="button">Service Items</button></li>
  </ul>

  <div class="tab-content">

    <!-- ---- Bidding Details ---- -->
    <div class="tab-pane fade show active" id="tab-bidding">
      <div class="card border-0 shadow-sm mb-3">
        <div class="card-header bg-white fw-bold small py-2">Data</div>
        <div class="card-body">
          <div class="row g-3">
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Stage</label>
              <select name="bid_stage" class="form-select form-select-sm">
                <option value="SINGLE_PART">Single Part</option>
                <option value="TWO_PART">Two Part</option>
              </select>
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Security Applic</label>
              <select name="bid_security_applic" class="form-select form-select-sm">
                <option value="1">Yes</option>
                <option value="0" selected>No</option>
              </select>
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Spec. Applic</label>
              <select name="bid_spec_applic" class="form-select form-select-sm">
                <option value="1">Yes</option>
                <option value="0" selected>No</option>
              </select>
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Foreign curr Appli</label>
              <select name="foreign_curr_appli" class="form-select form-select-sm">
                <option value="1">Yes</option>
                <option value="0" selected>No</option>
              </select>
            </div>

            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Security Amount (INR)</label>
              <input type="number" step="0.01" name="bid_security_amount" class="form-control form-control-sm" value="0">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Secu. Amount (FC)</label>
              <input type="number" step="0.01" name="bid_security_amount_fc" class="form-control form-control-sm" value="0">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Tax Rate (%)</label>
              <input type="number" step="0.01" name="tax_rate" class="form-control form-control-sm" value="18">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">E-Proc Applicable</label>
              <select name="e_proc_applicable" class="form-select form-select-sm">
                <option value="1">Yes</option>
                <option value="0" selected>No</option>
              </select>
            </div>

            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Spec. Cost (INR)</label>
              <input type="number" step="0.01" name="bid_spec_cost" class="form-control form-control-sm" value="0">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Spec. Cost (FC)</label>
              <input type="number" step="0.01" name="bid_spec_cost_fc" class="form-control form-control-sm" value="0">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">Bid Value (INR)</label>
              <input type="number" step="0.01" name="bid_value" class="form-control form-control-sm" value="0">
            </div>
            <div class="col-md-3">
              <label class="form-label small mb-1">E-Reverse Auction</label>
              <select name="e_reverse_auction" class="form-select form-select-sm">
                <option value="1">Yes</option>
                <option value="0" selected>No</option>
              </select>
            </div>

            <div class="col-md-4">
              <label class="form-label small mb-1">Download Start Dt/Tm</label>
              <input type="datetime-local" name="download_start_dt" class="form-control form-control-sm">
            </div>
            <div class="col-md-4">
              <label class="form-label small mb-1">Bid Subm. End Dt/Tm</label>
              <input type="datetime-local" name="bid_subm_end_dt" class="form-control form-control-sm">
            </div>
            <div class="col-md-4">
              <label class="form-label small mb-1">Bid Open Date/Time</label>
              <input type="datetime-local" name="bid_open_dt" class="form-control form-control-sm">
            </div>
          </div>
        </div>
      </div>

      <div class="card border-0 shadow-sm mb-3">
        <div class="card-header bg-white fw-bold small py-2">Description</div>
        <div class="card-body">
          <label class="form-label small mb-1">Title Of Bid</label>
          <textarea name="title_of_bid" class="form-control form-control-sm" rows="4" placeholder="Full title/scope of the bid as it should appear on the public notice..." required></textarea>
        </div>
      </div>
    </div>

    <!-- ---- Purchase Requisition Details ---- -->
    <div class="tab-pane fade" id="tab-pr">
      <div class="card border-0 shadow-sm mb-3">
        <div class="card-header bg-white fw-bold small py-2">Source Purchase Requisition (optional)</div>
        <div class="card-body">
          <div class="row g-2 align-items-end mb-3">
            <div class="col-md-6">
              <label class="form-label small mb-1">Purchase Requisition</label>
              <select id="pr_picker" class="form-select form-select-sm">
                <option value="">— None / manual entry —</option>
                <?php foreach ($eligiblePRs as $p): ?>
                <option value="<?= $p['id'] ?>" <?= $selectedPrId == $p['id'] ? 'selected' : '' ?>>
                  <?= htmlspecialchars($p['pr_number']) ?> — <?= $p['pr_date'] ?> (<?= $p['line_count'] ?> lines, <?= $p['status'] ?>)
                </option>
                <?php endforeach; ?>
              </select>
            </div>
            <div class="col-auto text-muted small">Choosing a PR reloads the page and pre-fills Service Items from its lines.</div>
          </div>

          <?php if ($selectedPr): ?>
          <table class="table table-sm table-hover mb-0 erp-table">
            <thead><tr><th>Item</th><th>Description</th><th>Qty</th><th>UOM</th></tr></thead>
            <tbody>
              <?php foreach ($prLinesToShow as $l): ?>
              <tr>
                <td class="small fw-bold"><?= $l['item_code'] ? htmlspecialchars($l['item_code']) : '—' ?></td>
                <td class="small"><?= htmlspecialchars($l['item_name'] ?? '—') ?></td>
                <td class="small"><?= number_format($l['qty_requested'], 3) ?></td>
                <td class="small"><?= htmlspecialchars($l['uom_code'] ?? '—') ?></td>
              </tr>
              <?php endforeach; ?>
            </tbody>
          </table>
          <?php else: ?>
          <p class="text-muted small mb-0">No Purchase Requisition selected — add Service Items manually in the next tab.</p>
          <?php endif; ?>
        </div>
      </div>
    </div>

    <!-- ---- Service Items ---- -->
    <div class="tab-pane fade" id="tab-items">
      <div class="card border-0 shadow-sm mb-3">
        <div class="card-header bg-white fw-bold small py-2 d-flex justify-content-between align-items-center">
          <span>Service Items</span>
          <button type="button" class="btn btn-sm btn-outline-primary" id="addItemRow"><i class="bi bi-plus-lg me-1"></i>Add Row</button>
        </div>
        <div class="card-body p-0">
          <table class="table table-sm mb-0 erp-table" id="itemsTable">
            <thead>
              <tr>
                <th>Description</th><th width="110">Qty</th><th width="120">UOM</th>
                <th width="140">Est. Rate</th><th width="140">Est. Value</th><th width="40"></th>
              </tr>
            </thead>
            <tbody id="itemsBody"></tbody>
          </table>
        </div>
      </div>
    </div>

  </div>

  <button type="submit" class="btn btn-primary"><i class="bi bi-save me-1"></i>Save Bid / Tender</button>
</form>

<script>
(function(){
  var uoms = <?= wp_json_encode($uomList) ?>;
  var prLines = <?= wp_json_encode($prLinesToShow) ?>;
  var tbody = document.getElementById('itemsBody');

  function uomOptions(selectedId) {
    var html = '<option value="">—</option>';
    uoms.forEach(function(u){
      html += '<option value="' + u.id + '"' + (String(u.id) === String(selectedId) ? ' selected' : '') + '>' + u.code + '</option>';
    });
    return html;
  }

  function addRow(desc, qty, uomId, rate, prLineId) {
    var tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input type="hidden" name="item_pr_line_id[]" value="' + (prLineId || '') + '">' +
        '<input type="text" name="item_description[]" class="form-control form-control-sm" value="' + (desc ? desc.replace(/"/g,'&quot;') : '') + '" placeholder="Service / item description"></td>' +
      '<td><input type="number" step="0.001" name="item_qty[]" class="form-control form-control-sm" value="' + (qty || '') + '"></td>' +
      '<td><select name="item_uom_id[]" class="form-select form-select-sm">' + uomOptions(uomId) + '</select></td>' +
      '<td><input type="number" step="0.01" name="item_rate[]" class="form-control form-control-sm" value="' + (rate || '') + '"></td>' +
      '<td><input type="text" class="form-control form-control-sm row-value" value="0.00" disabled></td>' +
      '<td><button type="button" class="btn btn-sm btn-outline-danger removeRow"><i class="bi bi-trash"></i></button></td>';
    tbody.appendChild(tr);

    var qtyInput = tr.querySelector('input[name="item_qty[]"]');
    var rateInput = tr.querySelector('input[name="item_rate[]"]');
    var valueBox = tr.querySelector('.row-value');
    function recalc() {
      var v = (parseFloat(qtyInput.value) || 0) * (parseFloat(rateInput.value) || 0);
      valueBox.value = v.toFixed(2);
    }
    qtyInput.addEventListener('input', recalc);
    rateInput.addEventListener('input', recalc);
    recalc();

    tr.querySelector('.removeRow').addEventListener('click', function(){ tr.remove(); });
  }

  document.getElementById('addItemRow').addEventListener('click', function(){ addRow(); });

  if (prLines.length) {
    prLines.forEach(function(l){ addRow(l.item_name || l.item_code, l.qty_requested, null, 0, l.id); });
  } else {
    addRow(); // start with one blank row
  }

  document.getElementById('pr_picker').addEventListener('change', function(){
    document.getElementById('pr_id_hidden').value = this.value;
    var url = new URL(window.location.href);
    url.searchParams.set('pr_id', this.value);
    window.location.href = url.toString();
  });
})();
</script>
