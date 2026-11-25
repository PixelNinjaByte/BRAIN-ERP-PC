<?php
/**
 * MSME ERP — Create RFQ (Request for Quotation)
 * T-Code: ME41
 * Generates a public, no-login quotation form per invited supplier
 * (see modules/mm/rfq_public.php) from a Purchase Requisition, and
 * emails each supplier their unique link.
 * Developed by: Prigag Online Pvt. Ltd.
 */

$tcode = 'ME41';
Auth::requireTcode('ME41');

$companyId = Auth::companyId();
$error = $success = '';

// ---- POST: create the RFQ ----
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
        $error = 'Security check failed. Please try again.';
    } else {
        $prId       = (int)($_POST['pr_id'] ?? 0);
        $dueDate    = trim($_POST['due_date'] ?? '');
        $autoHours  = (isset($_POST['auto_hours']) && $_POST['auto_hours'] !== '') ? max(0, (int)$_POST['auto_hours']) : null;   // v1.21.0: blank = Settings default
        $notes      = sanitize_textarea_field($_POST['notes'] ?? '');
        $lineIds    = array_filter(array_map('intval', $_POST['line_ids'] ?? []));
        $supplierIn = $_POST['suppliers'] ?? []; // [supplier_id => email]

        $pr = $prId ? DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?", [$prId, $companyId]) : null;

        // v1.20.8: the email boxes are always visible/editable; a supplier is invited when its tick-box
        // (invite[id]) is ticked. (Older posts without invite[] fall back to "has a valid email".)
        $inviteIn = isset($_POST['invite']) && is_array($_POST['invite']) ? array_map('intval', array_keys($_POST['invite'])) : null;
        $supplierRows = []; $badEmail = [];
        foreach ($supplierIn as $sid => $email) {
            $sid = (int)$sid;
            if ($inviteIn !== null && !in_array($sid, $inviteIn, true)) continue;
            $email = sanitize_email(trim((string)$email));
            if ($email && is_email($email)) $supplierRows[$sid] = $email; else $badEmail[] = $sid;
        }

        if (!$pr) {
            $error = 'Select a valid Purchase Requisition.';
        } elseif (!$lineIds) {
            $error = 'Select at least one line item to quote.';
        } elseif (!$supplierRows) {
            $error = $badEmail ? 'A ticked supplier has no valid email address - type one in the Email box.' : 'Tick at least one supplier to invite.';
        } else {
            $prLines = DB::fetchAll(
                "SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
                 FROM purchase_requisition_lines prl
                 LEFT JOIN items i ON i.id=prl.item_id LEFT JOIN units_of_measure u ON u.id=prl.uom_id
                 WHERE prl.pr_id=? AND prl.id IN (" . implode(',', array_fill(0, count($lineIds), '?')) . ")",
                array_merge([$prId], $lineIds)
            );

            try {
                DB::beginTransaction();

                $result    = DB::callProcedure('sp_get_next_number', [$companyId, 'RFQ', (int)date('Y')], ['p_doc_number']);
                $rfqNumber = $result['p_doc_number'];

                $rfqId = DB::insert('rfqs', [
                    'company_id' => $companyId,
                    'rfq_number' => $rfqNumber,
                    'pr_id'      => $prId,
                    'rfq_date'   => date('Y-m-d'),
                    'due_date'   => $dueDate ?: null,
                    'auto_compare_at' => msme_erp_rfq_default_compare_at($autoHours),
                    'status'     => 'OPEN',
                    'notes'      => $notes,
                    'created_by' => Auth::userId(),
                ] + msme_erp_cost_center_from_post($_POST));

                $rfqLineByPrLine = [];
                foreach ($prLines as $i => $l) {
                    $rfqLineId = DB::insert('rfq_lines', [
                        'rfq_id'     => $rfqId,
                        'line_num'   => ($i + 1) * 10,
                        'pr_line_id' => $l['id'],
                        'item_id'    => $l['item_id'],
                        'uom_id'     => $l['uom_id'],
                        'qty'        => $l['qty_requested'],
                    ]);
                    $rfqLineByPrLine[$l['id']] = $rfqLineId;
                }

                $invitedSuppliers = []; // for emailing after commit
                foreach ($supplierRows as $supplierId => $email) {
                    $token = bin2hex(random_bytes(20));
                    DB::insert('rfq_suppliers', [
                        'rfq_id'      => $rfqId,
                        'supplier_id' => $supplierId,
                        'token'       => $token,
                        'email_to'    => $email,
                        'status'      => 'SENT',
                        'sent_at'     => current_time('mysql'),
                    ]);
                    $invitedSuppliers[] = ['email' => $email, 'token' => $token];
                }

                Auth::audit('rfqs', $rfqId, 'INSERT', [], ['rfq_number' => $rfqNumber]);
                DB::commit();

                // ---- Email each supplier their unique quotation link ----
                // Uses WordPress's own wp_mail() so it automatically goes
                // through whatever SMTP plugin (or PHP mail()) is already
                // configured on this site — no separate mail credentials
                // needed here. If nothing is configured, wp_mail() falls
                // back to PHP mail(), which many hosts throttle/block for
                // outbound mail; for reliable delivery, install any
                // "WP Mail SMTP" plugin and connect a real mailbox
                // (Gmail, SendGrid, etc.) once, and every ERP email
                // (this one included) will go through it automatically.
                $failedSends = []; $okCount = 0;
                foreach ($invitedSuppliers as $inv) {
                    $r = msme_erp_rfq_email($rfqNumber, $inv['email'], $inv['token'], $dueDate, $notes);
                    if ($r['ok']) $okCount++; else $failedSends[] = $inv['email'] . ' (' . $r['error'] . ')';
                }

                if ($failedSends) {
                    msme_erp_set_flash($rfqNumber . ' created. Emailed ' . $okCount . ' of ' . count($invitedSuppliers) . ' supplier(s). NOT sent: ' . implode('; ', $failedSends) . '. Open the "Invited Suppliers & Links" tab below to resend, fix the address, or share the link by WhatsApp.', 'warning');
                } else {
                    msme_erp_set_flash($rfqNumber . ' created and emailed to ' . count($invitedSuppliers) . ' supplier(s): ' . implode(', ', array_column($invitedSuppliers, 'email')) . '.', 'success');
                }
                wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $rfqId)); exit;

            } catch (Throwable $e) {
                DB::rollback();
                error_log('MSME ERP RFQ create error: ' . $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
                $error = 'Could not create RFQ: ' . $e->getMessage();
            }
        }
    }
}

// ---- GET: load PR + lines for the picker ----
$selectedPrId = (int)($_GET['pr_id'] ?? $_POST['pr_id'] ?? 0);
$selectedPr   = null;
$prLinesToShow = [];

// PRs eligible to source an RFQ from — SUBMITTED or APPROVED, still
// carrying at least one line. Unlike ME21N's PO-conversion picker, this
// is not restricted to APPROVED only or to lines not yet pulled into a
// PO — sourcing quotes typically starts before or during approval, and
// re-quoting an already-ordered line (e.g. next cycle) is legitimate.
$eligiblePRs = DB::fetchAll(
    "SELECT pr.id, pr.pr_number, pr.pr_date, pr.status, COUNT(prl.id) AS line_count
     FROM purchase_requisitions pr
     JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
     WHERE pr.company_id = ? AND pr.status IN ('SUBMITTED','APPROVED')
     GROUP BY pr.id ORDER BY pr.id DESC",
    [$companyId]
);

if ($selectedPrId) {
    $selectedPr = DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=?", [$selectedPrId, $companyId]);
    if ($selectedPr) {
        $prLinesToShow = DB::fetchAll(
            // LEFT JOIN, not INNER — an INNER JOIN here would silently
            // drop a PR line from the picker entirely (making it
            // unselectable) if its item_id/uom_id ever fails to resolve
            // to a live items/units_of_measure row. Showing it with a
            // fallback label is always better than hiding it.
            "SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
             FROM purchase_requisition_lines prl
             LEFT JOIN items i ON i.id=prl.item_id LEFT JOIN units_of_measure u ON u.id=prl.uom_id
             WHERE prl.pr_id=? ORDER BY prl.line_num", [$selectedPrId]
        );
    }
}

$suppliers = DB::fetchAll("SELECT id, supplier_code, name, email FROM suppliers WHERE company_id=? AND status='ACTIVE' ORDER BY name", [$companyId]);

$pageTitle   = 'Create RFQ (Request for Quotation)';
$pageTcode   = 'ME41';
$breadcrumbs = ['MM', 'Purchasing', 'Create RFQ'];
$pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ME47') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-bar-chart-steps me-1"></i>Compare Quotations (ME47)</a>';

erp_render_page_header();
?>

<?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>

<!-- Step 1: choose PR -->
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-1-circle me-2"></i>Select Purchase Requisition</div>
  <div class="card-body">
    <form method="GET" class="row g-2 align-items-end">
      <input type="hidden" name="page" value="msme-erp">
      <input type="hidden" name="tcode" value="ME41">
      <div class="col-md-6">
        <select name="pr_id" class="form-select form-select-sm" onchange="this.form.submit()">
          <option value="">— Choose a PR —</option>
          <?php foreach ($eligiblePRs as $p): ?>
          <option value="<?= $p['id'] ?>" <?= $selectedPrId==$p['id']?'selected':'' ?>>
            <?= htmlspecialchars($p['pr_number']) ?> — <?= $p['pr_date'] ?> (<?= $p['line_count'] ?> lines, <?= $p['status'] ?>)
          </option>
          <?php endforeach; ?>
        </select>
      </div>
      <?php if (!$eligiblePRs): ?>
      <div class="col-auto text-muted small">No open Purchase Requisitions found. <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME51N') ?>">Create one first (ME51N)</a>.</div>
      <?php endif; ?>
    </form>
  </div>
</div>

<?php if ($selectedPr): ?>
<form method="POST">
  <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
  <input type="hidden" name="pr_id" value="<?= $selectedPr['id'] ?>">

  <!-- Step 2: line items -->
  <div class="card border-0 shadow-sm mb-3">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-2-circle me-2"></i>Line Items to Quote — <?= htmlspecialchars($selectedPr['pr_number']) ?></div>
    <div class="card-body p-0">
      <table class="table table-sm table-hover mb-0 erp-table">
        <thead><tr><th width="30"><input type="checkbox" onclick="document.querySelectorAll('.rfq-line-chk').forEach(c=>c.checked=this.checked)" checked></th><th>Item</th><th>Description</th><th>Qty</th><th>UOM</th></tr></thead>
        <tbody>
          <?php foreach ($prLinesToShow as $l): ?>
          <tr>
            <td><input type="checkbox" class="rfq-line-chk" name="line_ids[]" value="<?= $l['id'] ?>" checked></td>
            <td class="small fw-bold"><?= $l['item_code'] ? htmlspecialchars($l['item_code']) : '<span class="text-danger">Item #' . (int)$l['item_id'] . ' not found</span>' ?></td>
            <td class="small"><?= htmlspecialchars($l['item_name'] ?? '—') ?></td>
            <td class="small"><?= number_format($l['qty_requested'], 3) ?></td>
            <td class="small"><?= htmlspecialchars($l['uom_code'] ?? '—') ?></td>
          </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Step 3: suppliers -->
  <div class="card border-0 shadow-sm mb-3">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-3-circle me-2"></i>Invite Suppliers</div>
    <div class="card-body p-0">
      <table class="table table-sm table-hover mb-0 erp-table">
        <thead><tr><th width="30"><input type="checkbox" title="Tick every supplier that has an email" onclick="document.querySelectorAll('.rfq-sup-chk').forEach(function(c){ var e=document.getElementById('sup-email-'+c.dataset.sid); c.checked = this.checked && e.value.trim()!==''; }, this)"></th><th>Supplier</th><th>Code</th><th>Email to send to (editable)</th></tr></thead>
        <tbody>
          <?php foreach ($suppliers as $s): ?>
          <tr>
            <td><input type="checkbox" class="rfq-sup-chk" name="invite[<?= $s['id'] ?>]" value="1" data-sid="<?= $s['id'] ?>" onchange="if(this.checked){var e=document.getElementById('sup-email-<?= $s['id'] ?>'); if(!e.value.trim()) e.focus();}"></td>
            <td class="small fw-bold"><?= htmlspecialchars($s['name']) ?></td>
            <td class="small text-muted"><?= htmlspecialchars($s['supplier_code']) ?></td>
            <td><input type="email" id="sup-email-<?= $s['id'] ?>" name="suppliers[<?= $s['id'] ?>]" class="form-control form-control-sm" style="max-width:260px" value="<?= htmlspecialchars($s['email'] ?? '') ?>" placeholder="no email on file - type one"><?php if (empty($s['email'])): ?><span class="badge bg-warning text-dark ms-1">no email on file</span><?php endif; ?></td>
          </tr>
          <?php endforeach; ?>
          <?php if (!$suppliers): ?>
          <tr><td colspan="4" class="text-center text-muted py-3">No active suppliers. <a href="<?= admin_url('admin.php?page=msme-erp&tcode=XK01') ?>">Create one first (XK01)</a>.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Step 4: due date + notes -->
  <div class="card border-0 shadow-sm mb-3">
    <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-4-circle me-2"></i>Quotation Due Date &amp; Notes</div>
    <div class="card-body">
      <div class="row g-3">
        <div class="col-md-3">
          <label class="form-label small mb-1">Quote Due By <span class="text-muted">(cut-off)</span></label>
          <input type="date" name="due_date" class="form-control form-control-sm">
        </div>
        <div class="col-md-3">
          <label class="form-label small mb-1">Auto-compare after (hours)</label>
          <input type="number" min="0" step="1" name="auto_hours" class="form-control form-control-sm" placeholder="Settings default: <?= (int)msme_erp_rfq_auto_settings()['hours'] ?>">
          <div class="form-text" style="font-size:.7rem">AI comparison + automatic PO run at this time or after the due date, whichever comes first. 0 = due date only.</div>
        </div>
        <div class="col-md-6">
          <label class="form-label small mb-1">Notes to Supplier (optional)</label>
          <textarea name="notes" class="form-control form-control-sm" rows="2" placeholder="Delivery location, payment terms, special instructions..."></textarea>
        </div>
      </div>
      <hr>
      <?php msme_erp_render_cost_center_fields($companyId); ?>
    </div>
  </div>

  <button type="submit" class="btn btn-primary"><i class="bi bi-send me-1"></i>Create &amp; Email RFQ to Suppliers</button>
</form>
<?php endif; ?>
