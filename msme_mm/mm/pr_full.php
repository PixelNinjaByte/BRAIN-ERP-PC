<?php
/**
 * MSME ERP — Purchase Requisition (ME51N / ME53N)
 * Full implementation with approval workflow
 * Developed by: Prigag Online Pvt. Ltd.
 */

$tcode = $_GET['tcode'] ?? 'ME51N';
if (!in_array($tcode, ['ME51N','ME53N'])) $tcode = 'ME53N';
// v1.17.3: the global super admin (prigag.group@gmail.com) may always raise and
// view purchase requisitions, whatever the T-code / role tables say.
if (Auth::isGlobalSuperAdmin()) { Auth::requireLogin(); } else { Auth::requireTcode($tcode); }

$companyId = Auth::companyId();
$id        = (int)($_GET['id'] ?? 0);
$error = $success = '';

// Request Type: MATERIAL (default) or SERVICE ("Service Request").
// Same ME51N/ME53N screens and the same purchase_requisitions /
// purchase_requisition_lines tables handle both — a Service Request is
// simply a PR with pr_type='SERVICE', whose lines carry a service (typed
// freely or picked from existing SERVICE-type items) instead of a
// stock material. It flows through the existing PR→PO→(now)
// ML81N Service Entry Sheet chain unchanged.
$prType = (($_POST['pr_type'] ?? $_GET['type'] ?? '') === 'service') ? 'SERVICE' : 'MATERIAL';

// ---- Requester's cost center (v1.14.0) ----
// The logged-in user's employee record (PA01/PA30) carries Category > Unit >
// Department. When all three are set, every PR/Service Request this person
// raises is booked to that cost center automatically — it is applied on the
// server, not just pre-filled, so it can't be changed by editing the form.
// WP administrators / SUPER_ADMIN keep the selectors editable (they raise
// requests on behalf of other units). A user whose employee record has no
// (or only a partial) assignment gets the normal editable selectors, pre-
// filled with whatever part is known, plus a pointer to fix their profile.
$myEmp      = msme_erp_current_employee();
$myCc       = msme_erp_employee_cost_center($myEmp);
$ccOverride = current_user_can('manage_options') || in_array('SUPER_ADMIN', (array)(Auth::user()['roles'] ?? []), true);
$ccLocked   = $myCc['complete'] && !$ccOverride;

// ---- POST: Create PR ----
if ($tcode === 'ME51N' && $_SERVER['REQUEST_METHOD'] === 'POST' && empty($_POST['pr_action'])) {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
    $d = $_POST;

    if ($prType === 'SERVICE') {
        $lines = array_filter($d['lines'] ?? [], fn($l) => !empty(trim($l['service_name'] ?? '')) && (float)($l['qty_requested'] ?? 0) > 0);
    } else {
        $lines = array_filter($d['lines'] ?? [], fn($l) => !empty($l['item_id']) && (float)($l['qty_requested'] ?? 0) > 0);
    }

    if (!$lines) { $error = $prType === 'SERVICE' ? 'Add at least one service with a description and quantity.' : 'Add at least one item with quantity.'; }
    else {
        try {
            DB::beginTransaction();
            // Cost center: locked to the requester's employee profile, or what was
            // chosen on the form — and if nothing at all was chosen, fall back to
            // the profile so the PR is never left without a cost center when one is known.
            $prCc = msme_erp_cost_center_from_post($d);
            if ($ccLocked) {
                $prCc = ['category_id' => $myCc['category_id'], 'unit_id' => $myCc['unit_id'], 'department_id' => $myCc['department_id']];
            } elseif (!$prCc['category_id'] && !$prCc['unit_id'] && !$prCc['department_id']) {
                $prCc = ['category_id' => $myCc['category_id'], 'unit_id' => $myCc['unit_id'], 'department_id' => $myCc['department_id']];
            }
            $prDeptId = $ccLocked ? $prCc['department_id']
                                  : (!empty($d['dept_id']) ? (int)$d['dept_id'] : ($prCc['department_id'] ?: null));
            $result   = DB::callProcedure('sp_get_next_number', [$companyId, 'PR', date('Y')], ['p_doc_number']);
            $prNumber = $result['p_doc_number'];

            $prId = DB::insert('purchase_requisitions', [
                'company_id'   => $companyId,
                'branch_id'    => Auth::branchId(),
                'pr_number'    => $prNumber,
                'pr_date'      => $d['pr_date'] ?? date('Y-m-d'),
                'pr_type'      => $prType,
                'dept_id'      => $prDeptId,
                'requested_by' => Auth::userId(),
                'required_by'  => $d['required_by'] ?: null,
                'purpose'      => $d['purpose'] ?? '',
                'status'       => 'SUBMITTED',
                'created_by'   => Auth::userId(),
            ] + $prCc);

            $defaultUomId = null; // resolved lazily, only if a new service item needs creating

            foreach ($lines as $i => $ln) {
                if ($prType === 'SERVICE') {
                    $itemId = !empty($ln['item_id']) ? (int)$ln['item_id'] : 0;
                    $uomId  = !empty($ln['uom_id']) ? (int)$ln['uom_id'] : 0;

                    if (!$itemId) {
                        // Typed a service name that doesn't match an existing
                        // SERVICE item — create a lightweight service master
                        // on the fly so PR/PO/Service-Entry-Sheet lines all
                        // keep referencing a real item record.
                        if (!$defaultUomId) $defaultUomId = (int) DB::fetchValue("SELECT id FROM units_of_measure WHERE code='NOS'");
                        $svcResult = DB::callProcedure('sp_get_next_number', [$companyId, 'SRVITEM', date('Y')], ['p_doc_number']);
                        $itemId = DB::insert('items', [
                            'company_id'       => $companyId,
                            'item_code'        => $svcResult['p_doc_number'],
                            'item_name'        => trim($ln['service_name']),
                            'item_type'        => 'SERVICE',
                            'item_category'    => 'SERVICE',
                            'base_uom_id'      => $defaultUomId,
                            'moving_avg_price' => (float)($ln['est_price'] ?? 0),
                            'created_by'       => Auth::userId(),
                            'is_active'        => 1,
                        ]);
                        $uomId = $defaultUomId;
                    }

                    DB::insert('purchase_requisition_lines', [
                        'pr_id'         => $prId,
                        'line_num'      => ($i + 1) * 10,
                        'item_id'       => $itemId,
                        'uom_id'        => $uomId,
                        'qty_requested' => (float)$ln['qty_requested'],
                        'est_price'     => (float)($ln['est_price'] ?? 0),
                        'required_by'   => $d['required_by'] ?: null,
                        'remarks'       => $ln['remarks'] ?? '',
                        'status'        => 'OPEN',
                    ]);
                } else {
                    $lnUom = !empty($ln['uom_id']) ? (int)$ln['uom_id']
                           : (int) DB::fetchValue("SELECT base_uom_id FROM items WHERE id=?", [(int)$ln['item_id']]);
                    DB::insert('purchase_requisition_lines', [
                        'pr_id'         => $prId,
                        'line_num'      => ($i + 1) * 10,
                        'item_id'       => (int)$ln['item_id'],
                        'uom_id'        => $lnUom,
                        'qty_requested' => (float)$ln['qty_requested'],
                        'est_price'     => (float)($ln['est_price'] ?? 0),
                        'required_by'   => $d['required_by'] ?: null,
                        'remarks'       => $ln['remarks'] ?? '',
                        'status'        => 'OPEN',
                    ]);
                }
            }

            Auth::audit('purchase_requisitions', $prId, 'INSERT', [], ['pr_number' => $prNumber, 'pr_type' => $prType]);
            DB::commit();
            $label = $prType === 'SERVICE' ? 'Service Request' : 'PR';
            msme_erp_set_flash("{$label} {$prNumber} submitted successfully.", 'success');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N')); exit;
            exit;
        } catch (Exception $e) { DB::rollback(); $error = $e->getMessage(); }
    }
}

// ---- POST: Approve / Convert to PO ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['pr_action'])) {
    if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
    $prId   = (int)$_POST['pr_id'];
    $action = $_POST['pr_action'];

    if ($action === 'approve' && Auth::hasRole(['PURCHASE_MANAGER','SUPER_ADMIN'])) {
        DB::update('purchase_requisitions', ['status' => 'APPROVED'], 'id=? AND company_id=?', [$prId, $companyId]);
        DB::query("UPDATE purchase_requisition_lines SET qty_approved=qty_requested WHERE pr_id=?", [$prId]);
        // Keep the release inbox in step: a PR approved here must not stay "pending" in My Approvals.
        DB::query("UPDATE document_releases SET status='RELEASED', fully_released_at=NOW() WHERE doc_type='purchase_requisition' AND doc_id=? AND status='PENDING'", [$prId]);
        $success = 'PR approved successfully.';
    } elseif ($action === 'reject' && Auth::hasRole(['PURCHASE_MANAGER','SUPER_ADMIN'])) {
        DB::update('purchase_requisitions', ['status' => 'REJECTED'], 'id=? AND company_id=?', [$prId, $companyId]);
        DB::query("UPDATE document_releases SET status='REJECTED' WHERE doc_type='purchase_requisition' AND doc_id=? AND status='PENDING'", [$prId]);
        $success = 'PR rejected.';
    } elseif ($action === 'convert_po' && Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN'])) {
        // Convert approved PR to Draft PO
        $pr = DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=? AND status='APPROVED'", [$prId, $companyId]);
        if ($pr) {
            msme_erp_set_flash("Redirect to create PO from PR {$pr['pr_number']}", 'success');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME21N') . '&pr_id=' . $prId); exit;
            exit;
        }
    } elseif ($action === 'automate' && Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN'])) {
        // ============================================================
        // "AUTOMATE PURCHASE PROCESS" — one click from an APPROVED PR
        // straight to an RFQ sent to suppliers, skipping the manual
        // ME41 form (line/supplier picking). AI comparison, supplier
        // selection and PO creation already happen on the RFQ screen
        // (ME47 — see rfq_compare.php's "Generate AI Comparison" and
        // auto-award-on-lowest-price "Create PO" button) once quotes
        // come in; the final leg — emailing the released PO to the
        // winning supplier — happens automatically too, from
        // ErpDB::releaseDocument() via msme_erp_email_po_to_supplier().
        // ============================================================
        $pr = DB::fetchOne("SELECT * FROM purchase_requisitions WHERE id=? AND company_id=? AND status='APPROVED'", [$prId, $companyId]);
        if (!$pr) {
            msme_erp_set_flash('This PR must be fully APPROVED before the purchase process can be automated.', 'danger');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . $prId)); exit;
        }

        $prLinesFull = DB::fetchAll(
            "SELECT prl.*, i.item_code, i.item_name, u.code AS uom_code
             FROM purchase_requisition_lines prl
             LEFT JOIN items i ON i.id=prl.item_id LEFT JOIN units_of_measure u ON u.id=prl.uom_id
             WHERE prl.pr_id=? AND prl.status='OPEN'", [$prId]
        );
        if (!$prLinesFull) {
            msme_erp_set_flash('Every line on this PR already has a Purchase Order — nothing left to automate.', 'warning');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . $prId)); exit;
        }

        // Auto-pick suppliers: prefer whoever has actually supplied any
        // of these items before (ranked by how often, from PO history),
        // so the RFQ goes to relevant suppliers rather than everyone on
        // file. Falls back to every ACTIVE supplier with a usable email
        // (capped at 10) only when none of these items has any purchase
        // history yet.
        $itemIds = array_values(array_unique(array_filter(array_column($prLinesFull, 'item_id'))));
        $supplierRows = []; // supplier_id => email
        if ($itemIds) {
            $placeholders = implode(',', array_fill(0, count($itemIds), '?'));
            $history = DB::fetchAll(
                "SELECT po.supplier_id, s.email, COUNT(*) AS times_used
                 FROM purchase_order_lines pol
                 JOIN purchase_orders po ON po.id = pol.po_id
                 JOIN suppliers s ON s.id = po.supplier_id
                 WHERE pol.item_id IN ($placeholders) AND s.status='ACTIVE'
                       AND s.email IS NOT NULL AND s.email != '' AND po.company_id=?
                 GROUP BY po.supplier_id, s.email
                 ORDER BY times_used DESC
                 LIMIT 5",
                array_merge($itemIds, [$companyId])
            );
            foreach ($history as $h) {
                if (is_email($h['email'])) $supplierRows[(int)$h['supplier_id']] = $h['email'];
            }
        }
        if (!$supplierRows) {
            $fallback = DB::fetchAll(
                "SELECT id, email FROM suppliers WHERE company_id=? AND status='ACTIVE' AND email IS NOT NULL AND email != '' ORDER BY id LIMIT 10",
                [$companyId]
            );
            foreach ($fallback as $f) {
                if (is_email($f['email'])) $supplierRows[(int)$f['id']] = $f['email'];
            }
        }

        if (!$supplierRows) {
            msme_erp_set_flash('No suppliers with a usable email address on file — add supplier email addresses first (MM > Suppliers), then try again.', 'danger');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . $prId)); exit;
        }

        try {
            DB::beginTransaction();

            $result    = DB::callProcedure('sp_get_next_number', [$companyId, 'RFQ', (int)date('Y')], ['p_doc_number']);
            $rfqNumber = $result['p_doc_number'];

            $rfqId = DB::insert('rfqs', [
                'company_id' => $companyId,
                'rfq_number' => $rfqNumber,
                'pr_id'      => $prId,
                'rfq_date'   => date('Y-m-d'),
                'due_date'   => null,
                'auto_compare_at' => msme_erp_rfq_default_compare_at(),
                'status'     => 'OPEN',
                'notes'      => 'Auto-created by "Automate Purchase Process" from PR ' . $pr['pr_number'] . '.',
                'created_by' => Auth::userId(),
            ]);

            foreach ($prLinesFull as $i => $l) {
                DB::insert('rfq_lines', [
                    'rfq_id'     => $rfqId,
                    'line_num'   => ($i + 1) * 10,
                    'pr_line_id' => $l['id'],
                    'item_id'    => $l['item_id'],
                    'uom_id'     => $l['uom_id'],
                    'qty'        => $l['qty_requested'],
                ]);
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

            Auth::audit('rfqs', $rfqId, 'INSERT', [], ['rfq_number' => $rfqNumber, 'auto_from_pr' => $pr['pr_number']]);
            DB::commit();

            // Same mailer as the manual ME41 form (v1.20.8: reports the real failure reason per supplier).
            $failedSends = []; $okMails = [];
            foreach ($invitedSuppliers as $inv) {
                $r = msme_erp_rfq_email($rfqNumber, $inv['email'], $inv['token']);
                if ($r['ok']) $okMails[] = $inv['email']; else $failedSends[] = $inv['email'] . ' (' . $r['error'] . ')';
            }

            if ($failedSends) {
                msme_erp_set_flash($rfqNumber . ' created automatically for ' . count($invitedSuppliers) . ' supplier(s). Emailed: ' . ($okMails ? implode(', ', $okMails) : 'none') . '. NOT sent: ' . implode('; ', $failedSends) . '. Use the "Invited Suppliers & Links" tab below to resend, fix an address or share the link by WhatsApp.', 'warning');
            } else {
                msme_erp_set_flash('Purchase process automated: ' . $rfqNumber . ' created and emailed to ' . count($invitedSuppliers) . ' supplier(s) (' . implode(', ', $okMails) . '). Once they respond, use "Generate AI Comparison" then "Create PO" on this RFQ — the PO will auto-email to the winning supplier once fully released.', 'success');
            }
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $rfqId)); exit;

        } catch (Exception $e) {
            DB::rollback();
            msme_erp_set_flash('Error automating purchase process: ' . $e->getMessage(), 'danger');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . $prId)); exit;
        }
    }
}

// ---- Data for forms ----
$items       = msme_erp_master_items($companyId, 'PURCHASE', 'MATERIAL');   // v1.17.3: tolerant lookup (NULL type / inactive flag / orphan company / super admin)
$__stk = msme_erp_item_stock_map($companyId, array_column($items, 'id'));
foreach ($items as &$__it) {
    $__x = $__stk[(int)$__it['id']] ?? ['on_hand'=>0,'available'=>0,'on_order'=>0];
    $__it['on_hand'] = $__x['on_hand']; $__it['available'] = $__x['available']; $__it['on_order'] = $__x['on_order'];
}
unset($__it);
$uoms = msme_erp_master_uoms();
if (!$uoms) {   // v1.17.6: brand-new database - seed the standard units so a material can be created right here
    global $wpdb; msme_erp_ensure_default_uoms($wpdb, $wpdb->prefix . get_option('msme_erp_db_prefix', 'erp_'));
    $uoms = msme_erp_master_uoms();
}
$canNewMat = $prType !== 'SERVICE' && Auth::checkTcode('MM01');
$uomOptsHtml = '<option value="">UOM</option>';
foreach ($uoms as $__u) { $uomOptsHtml .= '<option value="' . (int)$__u['id'] . '">' . htmlspecialchars($__u['code']) . '</option>'; }
$serviceItems = $prType === 'SERVICE' ? msme_erp_master_items($companyId, 'PURCHASE', 'SERVICE') : [];
$departments = DB::fetchAll("SELECT id, code, name FROM departments WHERE company_id=? AND is_active=1 ORDER BY name", [$companyId]);
$currentUserName = Auth::user()['full_name'] ?? '';

// ---- PR Detail View ----
if ($id && $tcode === 'ME53N') {
    $pr = DB::fetchOne(
        "SELECT pr.*, d.name AS dept_name, u.full_name AS requester,
                fc.code AS cc_cat, pl.code AS cc_unit, cd.code AS cc_dept, cd.name AS cc_dept_name
         FROM purchase_requisitions pr
         LEFT JOIN departments d ON d.id=pr.dept_id LEFT JOIN users u ON u.id=pr.requested_by
         LEFT JOIN factory_categories fc ON fc.id=pr.category_id
         LEFT JOIN plants pl ON pl.id=pr.unit_id
         LEFT JOIN departments cd ON cd.id=pr.department_id
         WHERE pr.id=? AND pr.company_id=?", [$id, $companyId]
    );
    $prLines = DB::fetchAll(
        "SELECT prl.*, i.item_code, i.item_name, u.code AS uom, po.id AS po_id, po.po_number
         FROM purchase_requisition_lines prl
         JOIN items i ON i.id=prl.item_id LEFT JOIN units_of_measure u ON u.id=prl.uom_id
         LEFT JOIN purchase_order_lines pol ON pol.pr_line_id = prl.id
         LEFT JOIN purchase_orders po ON po.id = pol.po_id
         WHERE prl.pr_id=? ORDER BY prl.line_num", [$id]
    );
    $canApprove  = Auth::hasRole(['PURCHASE_MANAGER','SUPER_ADMIN']) && $pr && $pr['status'] === 'SUBMITTED';
    $canConvert  = Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN']) && $pr && $pr['status'] === 'APPROVED';
    $totalValue  = array_sum(array_map(fn($l) => $l['qty_requested'] * $l['est_price'], $prLines));
    $isService   = ($pr['pr_type'] ?? 'MATERIAL') === 'SERVICE';
    $docLabel    = $isService ? 'Service Request' : 'PR';
    // Existing stock for every requested material (shown to whoever releases / approves).
    $stockMap = $isService ? [] : msme_erp_item_stock_map($companyId, array_column($prLines, 'item_id'));
    $stockBox = $isService ? '' : msme_erp_pr_stock_table((int)$pr['id'], $companyId);

    $pageTitle   = $docLabel . ': ' . ($pr['pr_number'] ?? ''); $pageTcode = 'ME53N';
    $breadcrumbs = ['MM','Purchasing',$docLabel.': '.($pr['pr_number'] ?? '')];
    $pageActions = '<div class="d-flex gap-2">
            <a href="?page=msme-erp&tcode=ME51N" class="btn btn-sm btn-primary"><i class="bi bi-cart-plus me-1"></i>New PR</a>
            <a href="?page=msme-erp&tcode=ME51N&type=service" class="btn btn-sm btn-outline-primary"><i class="bi bi-tools me-1"></i>New Service Request</a>
            <a href="?page=msme-erp&tcode=ME53N" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left me-1"></i>Back</a>
        </div>';

    erp_render_page_header();
    $sc = ['DRAFT'=>'secondary','SUBMITTED'=>'info','APPROVED'=>'success','REJECTED'=>'danger','PO_CREATED'=>'primary','CANCELLED'=>'secondary'];
    ?>
    <?php if ($success): ?><div class="alert alert-success small py-2"><?= htmlspecialchars($success) ?></div><?php endif; ?>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-warning text-dark d-flex justify-content-between py-2">
        <span class="fw-bold"><i class="bi bi-<?= $isService ? 'tools' : 'cart-check' ?> me-2"></i><?= htmlspecialchars($pr['pr_number']) ?> <span class="badge bg-dark ms-1"><?= strtoupper($docLabel) ?></span></span>
        <span class="badge bg-<?= $sc[$pr['status']] ?? 'secondary' ?> fs-6"><?= $pr['status'] ?></span>
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-6">
            <table class="table table-sm table-borderless small mb-0">
              <tr><th class="text-muted" width="130">PR Date:</th><td><?= $pr['pr_date'] ?></td></tr>
              <tr><th class="text-muted">Required By:</th><td><?= $pr['required_by'] ?? '—' ?></td></tr>
              <tr><th class="text-muted">Department:</th><td><?= htmlspecialchars($pr['dept_name'] ?? '—') ?></td></tr>
              <tr><th class="text-muted">Cost Center:</th><td class="fw-semibold"><?= htmlspecialchars(implode(' / ', array_filter([$pr['cc_cat'] ?? '', $pr['cc_unit'] ?? '', $pr['cc_dept'] ?? ''])) ?: '—') ?></td></tr>
            </table>
          </div>
          <div class="col-md-6">
            <table class="table table-sm table-borderless small mb-0">
              <tr><th class="text-muted" width="130">Requested By:</th><td class="fw-bold"><?= htmlspecialchars($pr['requester'] ?? '—') ?></td></tr>
              <tr><th class="text-muted">Est. Value:</th><td class="fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($totalValue, 2) ?></td></tr>
              <tr><th class="text-muted">Purpose:</th><td><?= htmlspecialchars($pr['purpose'] ?? '—') ?></td></tr>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list-ul me-2"></i><?= $isService ? 'Service Lines' : 'PR Line Items' ?></div>
      <?php if ($stockBox): ?><div class="alert alert-info border-0 rounded-0 mb-0 py-2"><?= $stockBox ?></div><?php endif; ?>
      <div class="card-body p-0">
        <table class="table table-sm table-hover mb-0 erp-table">
          <thead><tr><th>Line</th><th><?= $isService ? 'Service Code' : 'Item Code' ?></th><th><?= $isService ? 'Service Description' : 'Description' ?></th><th>Qty Requested</th><th>Qty Approved</th><th>UOM</th><?php if (!$isService): ?><th class="text-end">In Stock</th><th class="text-end">Available</th><th class="text-end">On Order</th><th>Stock Cover</th><?php endif; ?><th>Est. Price</th><th>Est. Value</th><th>Required By</th><th>Status</th><th>PO Number</th></tr></thead>
          <tbody>
            <?php foreach ($prLines as $l): ?>
            <tr>
              <td class="text-muted small"><?= $l['line_num'] ?></td>
              <td class="fw-bold small"><?= htmlspecialchars($l['item_code']) ?></td>
              <td class="small"><?= htmlspecialchars($l['item_name']) ?></td>
              <td class="small fw-bold"><?= number_format($l['qty_requested'], 3) ?></td>
              <td class="small <?= $l['qty_approved'] ? 'text-success fw-bold' : 'text-muted' ?>">
                <?= $l['qty_approved'] ? number_format($l['qty_approved'], 3) : '—' ?>
              </td>
              <td class="small"><?= $l['uom'] ?></td>
              <?php if (!$isService):
                $__s  = $stockMap[(int)$l['item_id']] ?? ['on_hand'=>0,'available'=>0,'on_order'=>0,'wh'=>[]];
                $__cv = msme_erp_stock_cover((float)$l['qty_requested'], $__s);
                $__wt = $__s['wh'] ? implode(', ', array_map(fn($w) => $w['code'].': '.msme_erp_fmt_qty($w['qty']), $__s['wh'])) : 'No stock in any warehouse';
              ?>
              <td class="small text-end" title="<?= esc_attr($__wt) ?>"><?= esc_html(msme_erp_fmt_qty($__s['on_hand'])) ?></td>
              <td class="small text-end fw-bold"><?= esc_html(msme_erp_fmt_qty($__s['available'])) ?></td>
              <td class="small text-end"><?= $__s['on_order'] > 0 ? esc_html(msme_erp_fmt_qty($__s['on_order'])) : '<span class="text-muted">0</span>' ?></td>
              <td><span class="badge bg-<?= $__cv['class'] ?><?= $__cv['class']==='warning' ? ' text-dark' : '' ?>"><?= esc_html($__cv['label']) ?></span><?php if ($__cv['shortfall'] > 0): ?><div class="text-danger" style="font-size:.7rem">short <?= esc_html(msme_erp_fmt_qty($__cv['shortfall'])) ?></div><?php endif; ?></td>
              <?php endif; ?>
              <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($l['est_price'], 2) ?></td>
              <td class="small fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($l['qty_requested'] * $l['est_price'], 2) ?></td>
              <td class="small"><?= $l['required_by'] ?? '—' ?></td>
              <td><span class="badge bg-<?= $l['status']==='OPEN'?'warning':($l['status']==='PO_CREATED'?'success':'secondary') ?>"><?= $l['status'] ?></span></td>
              <td class="small">
                <?php if ($l['po_number']): ?>
                <a href="?page=msme-erp&tcode=ME23N&id=<?= (int)$l['po_id'] ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($l['po_number']) ?></a>
                <?php else: ?>—<?php endif; ?>
              </td>
            </tr>
            <?php endforeach; ?>
          </tbody>
          <tfoot class="table-light fw-bold">
            <tr><td colspan="<?= $isService ? 7 : 11 ?>" class="text-end small">Total Estimated Value:</td><td class="small text-primary"><?= esc_html(msme_erp_cur()) ?><?= number_format($totalValue, 2) ?></td><td colspan="3"></td></tr>
          </tfoot>
        </table>
      </div>
    </div>

    <!-- Approval Actions -->
    <?php $canRfq = in_array($pr['status'], ['SUBMITTED','APPROVED']) && Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN']); ?>
    <?php $canAutomate = $pr['status'] === 'APPROVED' && Auth::hasRole(['PURCHASE_EXEC','PURCHASE_MANAGER','SUPER_ADMIN']); ?>
    <?php if ($canApprove || $canConvert || $canRfq || $canAutomate): ?>
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-clipboard-check me-2"></i>Actions</div>
      <div class="card-body d-flex gap-3 align-items-center">
        <?php if ($canApprove): ?>
        <form method="POST">
          <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
          <input type="hidden" name="pr_id" value="<?= $id ?>">
          <input type="hidden" name="pr_action" value="approve">
          <button class="btn btn-success btn-sm" onclick="return confirm('Approve PR <?= htmlspecialchars($pr['pr_number']) ?>?')">
            <i class="bi bi-check-circle me-1"></i>Approve PR
          </button>
        </form>
        <form method="POST">
          <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
          <input type="hidden" name="pr_id" value="<?= $id ?>">
          <input type="hidden" name="pr_action" value="reject">
          <button class="btn btn-danger btn-sm" onclick="return confirm('Reject this PR?')">
            <i class="bi bi-x-circle me-1"></i>Reject PR
          </button>
        </form>
        <?php endif; ?>
        <?php if ($canConvert): ?>
        <form method="POST">
          <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
          <input type="hidden" name="pr_id" value="<?= $id ?>">
          <input type="hidden" name="pr_action" value="convert_po">
          <button class="btn btn-primary btn-sm">
            <i class="bi bi-bag-plus me-1"></i>Convert to Purchase Order (ME21N)
          </button>
        </form>
        <?php endif; ?>
        <?php if ($canRfq): ?>
        <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME41&pr_id=' . $id) ?>" class="btn btn-outline-primary btn-sm">
          <i class="bi bi-envelope-paper me-1"></i>Create RFQ — Request Supplier Quotes (ME41)
        </a>
        <?php endif; ?>
        <?php if ($canAutomate): ?>
        <form method="POST">
          <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
          <input type="hidden" name="pr_id" value="<?= $id ?>">
          <input type="hidden" name="pr_action" value="automate">
          <button class="btn btn-primary btn-sm" onclick="return confirm('This will automatically create an RFQ from every open line on this PR, pick suppliers (based on past purchase history, or all active suppliers if none), and email them for quotes right away. Continue?')" title="Sends an RFQ to suppliers now. Once they quote, use Generate AI Comparison + Create PO on the RFQ — the PO auto-emails to the winning supplier when fully released.">
            <i class="bi bi-magic me-1"></i>Automate Purchase Process
          </button>
        </form>
        <?php endif; ?>
        <a href="javascript:window.print()" class="btn btn-outline-secondary btn-sm ms-auto no-print">
          <i class="bi bi-printer me-1"></i>Print
        </a>
      </div>
    </div>
    <?php endif; ?>
    <?php
    
    return;
}

// ---- PR List / Create ----
$docLabel    = $prType === 'SERVICE' ? 'Service Request' : 'Purchase Requisition';
$pageTitle   = $tcode === 'ME51N' ? "Create {$docLabel}" : 'Purchase Requisitions';
$pageTcode   = $tcode;
$breadcrumbs = ['MM', 'Purchasing', $pageTitle];
$pageActions = '<div class="d-flex gap-2">
        <a href="?page=msme-erp&tcode=ME51N" class="btn btn-sm btn-primary"><i class="bi bi-cart-plus me-1"></i>Create PR (ME51N)</a>
        <a href="?page=msme-erp&tcode=ME51N&type=service" class="btn btn-sm btn-outline-primary"><i class="bi bi-tools me-1"></i>Create Service Request</a>
        <a href="?page=msme-erp&tcode=ME53N" class="btn btn-sm btn-outline-secondary"><i class="bi bi-list me-1"></i>PR List</a>
    </div>';

erp_render_page_header();

$prList = DB::fetchAll(
    "SELECT pr.id, pr.pr_number, pr.pr_date, pr.pr_type, pr.required_by, pr.status, d.name AS dept,
            u.full_name AS requester,
            COUNT(prl.id) AS line_count,
            COALESCE(SUM(prl.qty_requested * prl.est_price), 0) AS est_value
     FROM purchase_requisitions pr
     LEFT JOIN departments d ON d.id = pr.dept_id
     LEFT JOIN users u ON u.id = pr.requested_by
     LEFT JOIN purchase_requisition_lines prl ON prl.pr_id = pr.id
     WHERE pr.company_id = ? AND pr.status != 'CANCELLED'
     GROUP BY pr.id ORDER BY pr.id DESC LIMIT 50",
    [$companyId]
);


if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>
<?php if ($success): ?><div class="alert alert-success small py-2"><?= htmlspecialchars($success) ?></div><?php endif; ?>

<?php if ($tcode === 'ME51N'): ?>
<!-- ======= CREATE PR / SERVICE REQUEST FORM ======= -->
<?php if ($prType !== 'SERVICE') { $GLOBALS['msme_erp_master_notes'] = array_unique($GLOBALS['msme_erp_master_notes'] ?? []); msme_erp_master_diagnostic($companyId, [], $items, [], false); } ?>
<form method="POST" id="pr-form">
<input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
<input type="hidden" name="pr_type" value="<?= $prType === 'SERVICE' ? 'service' : 'material' ?>">
<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-warning text-dark fw-bold small py-2 d-flex justify-content-between align-items-center">
    <span><i class="bi bi-<?= $prType === 'SERVICE' ? 'tools' : 'cart-plus' ?> me-2"></i>Create <?= htmlspecialchars($docLabel) ?> — ME51N</span>
    <div class="btn-group btn-group-sm" role="group">
      <a href="?page=msme-erp&tcode=ME51N" class="btn btn-<?= $prType==='MATERIAL' ? 'dark' : 'outline-dark' ?>">Material</a>
      <a href="?page=msme-erp&tcode=ME51N&type=service" class="btn btn-<?= $prType==='SERVICE' ? 'dark' : 'outline-dark' ?>">Service</a>
    </div>
  </div>
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-3">
        <label class="form-label fw-semibold small">Requested By</label>
        <input type="text" class="form-control form-control-sm bg-light" value="<?= htmlspecialchars($currentUserName) ?>" readonly>
      </div>
      <div class="col-md-3">
        <label class="form-label fw-semibold small"><?= $prType === 'SERVICE' ? 'Request Date' : 'PR Date' ?></label>
        <input type="date" name="pr_date" class="form-control form-control-sm" value="<?= date('Y-m-d') ?>">
      </div>
      <div class="col-md-3">
        <label class="form-label fw-semibold small">Required By</label>
        <input type="date" name="required_by" class="form-control form-control-sm" value="<?= date('Y-m-d', strtotime('+14 days')) ?>">
      </div>
      <div class="col-md-3">
        <label class="form-label fw-semibold small">Department</label>
        <select name="dept_id" class="form-select form-select-sm" <?= $ccLocked ? 'disabled title="Set automatically from your employee profile"' : '' ?>>
          <option value="">— Select —</option>
          <?php foreach ($departments as $d): ?>
          <option value="<?= $d['id'] ?>" <?= (int)$myCc['department_id'] === (int)$d['id'] ? 'selected' : '' ?>><?= htmlspecialchars($d['code'] . ' — ' . $d['name']) ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div class="col-md-12">
        <label class="form-label fw-semibold small">Purpose</label>
        <input type="text" name="purpose" class="form-control form-control-sm" placeholder="Brief description of need">
      </div>
    </div>
    <hr>
    <?php if ($ccLocked): ?>
    <div class="alert alert-success small py-2 mb-0">
      <i class="bi bi-diagram-3 me-1"></i><strong>Cost center (automatic):</strong>
      <?= esc_html($myCc['label']) ?><?= $myEmp && !empty($myEmp['dept_name']) ? ' — ' . esc_html($myEmp['dept_name']) : '' ?>
      <?= $myCc['cost_center_code'] !== '' ? ' &nbsp;<span class="badge bg-success">' . esc_html($myCc['cost_center_code']) . '</span>' : '' ?>
      <div class="text-muted" style="font-size:.75rem">Taken from your employee record (PA30). This request will be booked to it.</div>
    </div>
    <?php else: ?>
    <?php if (!$myCc['complete']): ?>
    <div class="alert alert-warning small py-2">
      <i class="bi bi-exclamation-triangle me-1"></i>
      <?= $myEmp
          ? 'Your employee record has no complete Category / Unit / Department, so the cost center could not be set automatically. Please choose it below, and ask HR to complete your record on PA30.'
          : 'Your login is not linked to an employee record, so the cost center could not be set automatically. Please choose it below, and ask HR to link your login (SU01) or e-mail on PA30.' ?>
    </div>
    <?php endif; ?>
    <?php msme_erp_render_cost_center_fields($companyId, ['category_id' => $myCc['category_id'], 'unit_id' => $myCc['unit_id'], 'department_id' => $myCc['department_id']]); ?>
    <?php endif; ?>
  </div>
</div>

<div class="card border-0 shadow-sm mb-3">
  <div class="card-header bg-white d-flex justify-content-between py-2">
    <span class="fw-bold small"><i class="bi bi-list-ul me-2"></i><?= $prType === 'SERVICE' ? 'Services Required' : 'Items Required' ?></span>
    <span class="d-flex gap-2">
    <?php if ($canNewMat): ?>
    <button type="button" class="btn btn-sm btn-success" onclick="var b=document.getElementById('quickMat');b.style.display=b.style.display==='none'?'':'none';"><i class="bi bi-box-seam me-1"></i>+ New Material</button>
    <?php endif; ?>
    <button type="button" class="btn btn-sm btn-outline-warning" onclick="<?= $prType === 'SERVICE' ? 'addSrvLine()' : 'addPrLine()' ?>">
      <i class="bi bi-plus-circle me-1"></i>Add <?= $prType === 'SERVICE' ? 'Service' : 'Item' ?>
    </button>
    </span>
  </div>
  <?php if ($canNewMat): ?>
  <div id="quickMat" class="p-3 border-bottom bg-light" style="<?= $items ? 'display:none' : '' ?>">
    <div class="fw-bold small mb-2"><i class="bi bi-box-seam me-1"></i>Create a material (Material Master) and add it to this request
      <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=MM01')) ?>" target="_blank" class="ms-2 fw-normal">open full MM01 form</a></div>
    <?php if (!$items): ?><div class="small text-muted mb-2">There are no materials yet - create the first one here.</div><?php endif; ?>
    <div class="row g-2">
      <div class="col-md-4"><label class="form-label small mb-0">Material description *</label><input type="text" id="qmName" class="form-control form-control-sm" maxlength="200" placeholder="e.g. MS Plate 10mm"></div>
      <div class="col-md-2"><label class="form-label small mb-0">Item code <span class="text-muted">(auto)</span></label><input type="text" id="qmCode" class="form-control form-control-sm" maxlength="50" placeholder="MAT000001"></div>
      <div class="col-md-2"><label class="form-label small mb-0">Unit *</label>
        <select id="qmUom" class="form-select form-select-sm"><option value="">Select</option>
        <?php foreach ($uoms as $__qu): ?><option value="<?= (int)$__qu['id'] ?>" <?= strtoupper($__qu['code']) === 'NOS' ? 'selected' : '' ?>><?= esc_html($__qu['code']) ?></option><?php endforeach; ?></select></div>
      <div class="col-md-1"><label class="form-label small mb-0">Price</label><input type="number" id="qmPrice" class="form-control form-control-sm" value="0" min="0" step="0.01"></div>
      <div class="col-md-1"><label class="form-label small mb-0">GST %</label>
        <select id="qmGst" class="form-select form-select-sm"><?php foreach (msme_erp_mat_gst_rates() as $__g): ?><option value="<?= $__g ?>" <?= (float)$__g === 18.0 ? 'selected' : '' ?>><?= $__g ?></option><?php endforeach; ?></select></div>
      <div class="col-md-2"><label class="form-label small mb-0">HSN <span class="text-muted">(optional)</span></label><input type="text" id="qmHsn" class="form-control form-control-sm" maxlength="8"></div>
    </div>
    <div class="d-flex align-items-center gap-3 mt-2">
      <button type="button" id="qmSave" class="btn btn-sm btn-success"><i class="bi bi-check2-circle me-1"></i>Save material &amp; add to list</button>
      <label class="small mb-0"><input type="checkbox" id="qmDup"> Create anyway if a similar description exists</label>
    </div>
    <div id="qmMsg" class="small mt-2"></div>
  </div>
  <?php endif; ?>
  <?php if ($prType !== 'SERVICE'):
      $__tbl = ErpDB::tbl('items'); $__all = (int) ErpDB::wpdb()->get_var("SELECT COUNT(*) FROM `{$__tbl}`");
      $__co = (string) ErpDB::fetchValue("SELECT name FROM companies WHERE id=?", [$companyId]); ?>
  <div id="prStatus" class="px-3 py-2 small border-bottom bg-warning-subtle" data-server-count="<?= count($items) ?>">
    <span id="prStatusMain"><i class="bi bi-exclamation-triangle me-1"></i><strong>Page script has not started</strong> &mdash; press F12 &rarr; Console and send a screenshot of any red error.</span>
    <span class="text-muted ms-2">Server: <?= count($items) ?> material(s) for company &ldquo;<?= esc_html($__co) ?>&rdquo; (id <?= (int)$companyId ?>) &middot; table <code><?= esc_html($__tbl) ?></code> holds <?= $__all ?> row(s) in total.</span>
    <button type="button" id="prPing" class="btn btn-sm btn-outline-secondary py-0 ms-2">Test connection</button>
    <a class="ms-2" href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMDC')) ?>">Link check</a>
    <?php $__mis = msme_erp_misfiled_items((int)$companyId); if ($__mis): ?>
    <div class="text-danger fw-semibold mt-1"><i class="bi bi-exclamation-octagon me-1"></i><?= count($__mis) ?> entr<?= count($__mis) === 1 ? 'y' : 'ies' ?> in this list are really Departments/Units/Categories, not materials (imported by mistake).
      <a href="<?= esc_url(admin_url('admin.php?page=msme-erp&tcode=ZMAT')) ?>">Open ZMAT &rarr; Remove them from Materials</a></div>
    <?php endif; ?>
    <div id="prStatusErr" class="text-danger mt-1"></div><div id="prPingOut" class="mt-1"></div>
  </div>
  <script>
  window.addEventListener('error', function(e){ var x=document.getElementById('prStatusErr'); if(x) x.textContent += 'JS error: ' + (e.message||'') + (e.filename?(' ('+e.filename.split('/').pop()+':'+e.lineno+')'):'') + '  '; });
  </script>
  <?php endif; ?>
  <div class="card-body p-0">
    <div class="table-responsive">
    <?php if ($prType === 'SERVICE'): ?>
      <datalist id="service-datalist">
        <?php foreach ($serviceItems as $si): ?>
        <option value="<?= htmlspecialchars($si['item_name']) ?>">
        <?php endforeach; ?>
      </datalist>
      <table class="table table-sm table-bordered mb-0" style="font-size:.8rem">
        <thead class="table-warning">
          <tr>
            <th>#</th>
            <th width="280">Service (select existing or type new)</th>
            <th width="110">Qty</th>
            <th width="130">Estimated Cost/Price</th>
            <th width="120">Est. Value</th>
            <th>Remarks</th>
            <th width="30"></th>
          </tr>
        </thead>
        <tbody id="srv-lines-body">
          <?php for ($i = 0; $i < 3; $i++): ?>
          <tr class="srv-line">
            <td class="text-muted small text-center srv-line-num"><?= $i + 1 ?></td>
            <td>
              <input type="text" name="lines[<?= $i ?>][service_name]" class="form-control form-control-sm srv-name" list="service-datalist" placeholder="Type or pick a service...">
              <input type="hidden" name="lines[<?= $i ?>][item_id]" class="srv-item-id">
              <input type="hidden" name="lines[<?= $i ?>][uom_id]" class="srv-uom-id">
            </td>
            <td><input type="number" name="lines[<?= $i ?>][qty_requested]" class="form-control form-control-sm srv-qty" value="1" min="0.001" step="0.001"></td>
            <td><input type="number" name="lines[<?= $i ?>][est_price]" class="form-control form-control-sm srv-price" value="0" min="0" step="0.01"></td>
            <td><input type="text" class="form-control form-control-sm srv-value fw-bold text-end" readonly value="0.00"></td>
            <td><input type="text" name="lines[<?= $i ?>][remarks]" class="form-control form-control-sm" placeholder="Optional..."></td>
            <td>
              <button type="button" class="btn btn-xs btn-outline-danger py-0 px-1" onclick="removeSrvLine(this)">
                <i class="bi bi-x"></i>
              </button>
            </td>
          </tr>
          <?php endfor; ?>
        </tbody>
        <tfoot>
          <tr class="table-light fw-bold">
            <td colspan="4" class="text-end small pe-2">Total Estimated Value:</td>
            <td><input type="text" id="srv-total" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
            <td colspan="2"></td>
          </tr>
        </tfoot>
      </table>
      <div class="form-text px-2 pb-2">Pick a service already on file, or just type a new one — it's added to the service catalog automatically when this request is submitted.</div>
    <?php else: ?>
      <table class="table table-sm table-bordered mb-0" style="font-size:.8rem">
        <thead class="table-warning">
          <tr>
            <th>#</th>
            <th width="250">Material / Item</th>
            <th width="120">Existing Stock</th>
            <th width="70">UOM</th>
            <th width="110">Qty Required</th>
            <th width="120">Est. Unit Price</th>
            <th width="120">Est. Value</th>
            <th>Remarks</th>
            <th width="30"></th>
          </tr>
        </thead>
        <tbody id="pr-lines-body">
          <?php for ($i = 0; $i < 3; $i++): ?>
          <tr class="pr-line">
            <td class="text-muted small text-center pr-line-num"><?= $i + 1 ?></td>
            <td>
              <select name="lines[<?= $i ?>][item_id]" class="form-select form-select-sm select2 pr-item-sel" style="min-width:200px">
                <option value="">— Select Item —</option>
                <?php foreach ($items as $it): ?>
                <option value="<?= $it['id'] ?>"
                  data-uom="<?= htmlspecialchars((string)($it['uom'] ?? '')) ?>"
                  data-uomid="<?= (int)($it['uom_id'] ?? 0) ?>"
                  data-price="<?= $it['moving_avg_price'] ?>">
                  <?= htmlspecialchars($it['item_code'] . ' — ' . $it['item_name']) ?>
                </option>
                <?php endforeach; ?>
              </select>
            </td>
            <td class="pr-stock small text-muted">—</td>
            <td><select name="lines[<?= $i ?>][uom_id]" class="form-select form-select-sm pr-uom-sel" style="min-width:80px"><?= $uomOptsHtml ?></select></td>
            <td><input type="number" name="lines[<?= $i ?>][qty_requested]" class="form-control form-control-sm pr-qty" value="1" min="0.001" step="0.001"></td>
            <td><input type="number" name="lines[<?= $i ?>][est_price]" class="form-control form-control-sm pr-price" value="0" min="0" step="0.01"></td>
            <td><input type="text" class="form-control form-control-sm pr-value fw-bold text-end" readonly value="0.00"></td>
            <td><input type="text" name="lines[<?= $i ?>][remarks]" class="form-control form-control-sm" placeholder="Optional..."></td>
            <td>
              <button type="button" class="btn btn-xs btn-outline-danger py-0 px-1" onclick="removePrLine(this)">
                <i class="bi bi-x"></i>
              </button>
            </td>
          </tr>
          <?php endfor; ?>
        </tbody>
        <tfoot>
          <tr class="table-light fw-bold">
            <td colspan="6" class="text-end small pe-2">Total Estimated Value:</td>
            <td><input type="text" id="pr-total" class="form-control form-control-sm text-end fw-bold" readonly value="0.00"></td>
            <td colspan="2"></td>
          </tr>
        </tfoot>
      </table>
    <?php endif; ?>
    </div>
  </div>
</div>

<div class="d-flex gap-2">
  <button type="submit" class="btn btn-warning text-dark">
    <i class="bi bi-send me-1"></i>Submit <?= htmlspecialchars($docLabel) ?>
  </button>
  <a href="?page=msme-erp&tcode=ME53N" class="btn btn-outline-secondary">
    <i class="bi bi-x-circle me-1"></i>Cancel
  </a>
</div>
</form>

<?php else: ?>
<!-- ======= PR LIST ======= -->
<div class="row g-3 mb-3">
  <?php
  $counts = ['SUBMITTED'=>0,'APPROVED'=>0,'REJECTED'=>0,'PO_CREATED'=>0];
  foreach ($prList as $p) { if (isset($counts[$p['status']])) $counts[$p['status']]++; }
  $statCards = [['Pending Approval','SUBMITTED','warning',$counts['SUBMITTED']],['Approved','APPROVED','success',$counts['APPROVED']],['Rejected','REJECTED','danger',$counts['REJECTED']],['PO Created','PO_CREATED','primary',$counts['PO_CREATED']]];
  foreach ($statCards as [$label,$stat,$color,$cnt]):
  ?>
  <div class="col-6 col-md-3">
    <div class="card border-0 shadow-sm text-center p-3">
      <div class="fs-4 fw-bold text-<?= $color ?>"><?= $cnt ?></div>
      <div class="small text-muted"><?= $label ?></div>
    </div>
  </div>
  <?php endforeach; ?>
</div>

<div class="card border-0 shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-hover table-sm mb-0 erp-table erp-datatable">
        <thead>
          <tr><th>PR Number</th><th>Type</th><th>Date</th><th>Requester</th><th>Department</th><th>Lines</th><th>Est. Value</th><th>Req'd By</th><th>Status</th><th>Action</th></tr>
        </thead>
        <tbody>
          <?php foreach ($prList as $pr):
            $sc = ['DRAFT'=>'secondary','SUBMITTED'=>'info','APPROVED'=>'success','REJECTED'=>'danger','PO_CREATED'=>'primary','CANCELLED'=>'secondary'];
            $rowIsService = ($pr['pr_type'] ?? 'MATERIAL') === 'SERVICE';
          ?>
          <tr>
            <td>
              <a href="?page=msme-erp&tcode=ME53N&id=<?= $pr['id'] ?>" class="fw-bold text-decoration-none">
                <?= htmlspecialchars($pr['pr_number']) ?>
              </a>
            </td>
            <td class="small"><span class="badge bg-<?= $rowIsService ? 'info text-dark' : 'light text-dark border' ?>"><i class="bi bi-<?= $rowIsService ? 'tools' : 'box-seam' ?> me-1"></i><?= $rowIsService ? 'Service' : 'Material' ?></span></td>
            <td class="small"><?= $pr['pr_date'] ?></td>
            <td class="small"><?= htmlspecialchars($pr['requester'] ?? '—') ?></td>
            <td class="small"><?= htmlspecialchars($pr['dept'] ?? '—') ?></td>
            <td class="small text-center"><?= $pr['line_count'] ?></td>
            <td class="small fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($pr['est_value'], 0) ?></td>
            <td class="small"><?= $pr['required_by'] ?? '—' ?></td>
            <td><span class="badge bg-<?= $sc[$pr['status']] ?? 'secondary' ?>"><?= $pr['status'] ?></span></td>
            <td>
              <a href="?page=msme-erp&tcode=ME53N&id=<?= $pr['id'] ?>" class="btn btn-xs btn-outline-primary py-0 px-1 small">
                <i class="bi bi-eye"></i>
              </a>
              <?php if ($pr['status'] === 'SUBMITTED' && Auth::hasRole(['PURCHASE_MANAGER','SUPER_ADMIN'])): ?>
              <form method="POST" class="d-inline">
                <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
                <input type="hidden" name="pr_id" value="<?= $pr['id'] ?>">
                <input type="hidden" name="pr_action" value="approve">
                <button class="btn btn-xs btn-outline-success py-0 px-1 small" type="submit" title="Quick Approve">
                  <i class="bi bi-check2"></i>
                </button>
              </form>
              <?php endif; ?>
            </td>
          </tr>
          <?php endforeach; if (!$prList): ?>
          <tr><td colspan="10" class="text-center text-muted py-4">No purchase requisitions found.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>
</div>
<?php endif; ?>

<?php
$itemsJson = json_encode(array_column($items, null, 'id'));
$extraScripts = <<<'JS'
<script>
let prLineIdx = 3;
const prItems = Object.assign({}, __ITEMS_JSON__);
function escHtml(t){ return String(t==null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
// v1.17.7: every item dropdown (the 3 printed rows, rows added later, and after a quick-create) is built
// from prItems by this one routine, so they can never disagree with each other.
function itemOptionsHtml(){
  return '<option value="">\u2014 Select Item \u2014</option>' + Object.values(prItems).map(function(it){
    return '<option value="'+it.id+'" data-uom="'+escHtml(it.uom)+'" data-uomid="'+(it.uom_id||0)+'" data-price="'+(it.moving_avg_price||0)+'">'+escHtml(it.item_code)+' \u2014 '+escHtml(it.item_name)+'</option>';
  }).join('');
}
function fillItemSelect(sel){
  var keep = sel.value, $s = $(sel);
  if ($s.hasClass('select2-hidden-accessible')) $s.select2('destroy');
  sel.innerHTML = itemOptionsHtml();
  if (keep && prItems[keep]) sel.value = keep;
  $s.select2(prSelect2Opts());
}
// v1.20.1: an empty search no longer ends in a dead "No results found" - it offers to create the material.
function prSelect2Opts(){
  return {theme: 'bootstrap-5', width: '100%', language: { noResults: function(){
    return $('<span>No material found. <a href="#" class="pr-create-from-search fw-bold">+ Create it as a new material</a></span>');
  }}};
}
document.addEventListener('mousedown', function(e){
  var a = e.target.closest && e.target.closest('.pr-create-from-search'); if (!a) return;
  e.preventDefault();
  var term = ($('.select2-search__field').last().val() || '').trim();
  var box = document.getElementById('quickMat'); if (!box) { window.open(window.MSME_ERP.adminUrl + '?page=msme-erp&tcode=MM01', '_blank'); return; }
  $('select.pr-item-sel').select2('close');
  box.style.display = ''; var n = document.getElementById('qmName'); if (term) n.value = term; n.focus();
  box.scrollIntoView({behavior: 'smooth', block: 'center'});
});

function addPrLine() {
  const tbody = document.getElementById('pr-lines-body');
  const i = prLineIdx++;
  tbody.insertAdjacentHTML('beforeend', `
    <tr class="pr-line">
      <td class="text-muted small text-center pr-line-num">${i + 1}</td>
      <td>
        <select name="lines[${i}][item_id]" class="form-select form-select-sm select2 pr-item-sel" style="min-width:200px">
          <option value="">— Select Item —</option>
        </select>
      </td>
      <td class="pr-stock small text-muted">—</td>
      <td><select name="lines[${i}][uom_id]" class="form-select form-select-sm pr-uom-sel" style="min-width:80px">__UOM_OPTS__</select></td>
      <td><input type="number" name="lines[${i}][qty_requested]" class="form-control form-control-sm pr-qty" value="1" min="0.001" step="0.001"></td>
      <td><input type="number" name="lines[${i}][est_price]" class="form-control form-control-sm pr-price" value="0" step="0.01"></td>
      <td><input type="text" class="form-control form-control-sm pr-value fw-bold text-end" readonly value="0.00"></td>
      <td><input type="text" name="lines[${i}][remarks]" class="form-control form-control-sm" placeholder="Optional..."></td>
      <td><button type="button" class="btn btn-xs btn-outline-danger py-0 px-1" onclick="removePrLine(this)"><i class="bi bi-x"></i></button></td>
    </tr>`);
  const row = tbody.lastElementChild;
  initPrRow(row);
}

function removePrLine(btn) {
  const rows = document.querySelectorAll('.pr-line');
  if (rows.length <= 1) return;
  btn.closest('tr').remove();
  reindexPrLines();
  recalcPrTotal();
}

function reindexPrLines() {
  document.querySelectorAll('.pr-line-num').forEach((el, idx) => el.textContent = idx + 1);
}

function calcPrRow(row) {
  const qty   = parseFloat(row.querySelector('.pr-qty').value) || 0;
  const price = parseFloat(row.querySelector('.pr-price').value) || 0;
  row.querySelector('.pr-value').value = (qty * price).toFixed(2);
  recalcPrTotal();
}

function recalcPrTotal() {
  const totalEl = document.getElementById('pr-total');
  if (!totalEl) return;
  let total = 0;
  document.querySelectorAll('.pr-value').forEach(el => total += parseFloat(el.value) || 0);
  totalEl.value = total.toFixed(2);
}

// Existing stock of the chosen material, shown right on the request line.
function showPrStock(row, d) {
  const c = row.querySelector('.pr-stock');
  if (!c) return;
  if (!d) { c.textContent = '—'; c.className = 'pr-stock small text-muted'; return; }
  const f = n => (Math.round((parseFloat(n) || 0) * 1000) / 1000).toString();
  const av = parseFloat(d.available) || 0;
  c.className = 'pr-stock small ' + (av > 0 ? 'text-success fw-semibold' : 'text-danger fw-semibold');
  c.textContent = f(d.available) + ' ' + (d.uom || '') + ' available';
  c.title = 'On hand: ' + f(d.on_hand) + ' | On order: ' + f(d.on_order);
}

function initPrRow(row) {
  var sel = row.querySelector('.pr-item-sel');
  if (sel) fillItemSelect(sel);
  row.querySelector('.pr-qty').addEventListener('input', () => calcPrRow(row));
  row.querySelector('.pr-price').addEventListener('input', () => calcPrRow(row));
}
// one delegated handler for every item dropdown, old or new
$(document).on('change', '.pr-item-sel', function() {
  const row = this.closest('tr'), d = prItems[this.value];
  showPrStock(row, d);
  if (!d) return;
  row.querySelector('.pr-uom-sel').value = d.uom_id;   // default from item master; user may change
  row.querySelector('.pr-price').value   = parseFloat(d.moving_avg_price || 0).toFixed(2);
  calcPrRow(row);
});

document.querySelectorAll('.pr-line').forEach(initPrRow);
recalcPrTotal();

// ---- Service Request lines (Request Type = Service) ----
let srvLineIdx = 3;
const srvServices = __SERVICE_ITEMS_JSON__; // keyed by lowercase item_name -> {id, uom_id, moving_avg_price}

function calcSrvRow(row) {
  const qty   = parseFloat(row.querySelector('.srv-qty').value)   || 0;
  const price = parseFloat(row.querySelector('.srv-price').value) || 0;
  row.querySelector('.srv-value').value = (qty * price).toFixed(2);
  recalcSrvTotal();
}

function recalcSrvTotal() {
  const totalEl = document.getElementById('srv-total');
  if (!totalEl) return;
  let total = 0;
  document.querySelectorAll('.srv-value').forEach(el => total += parseFloat(el.value) || 0);
  totalEl.value = total.toFixed(2);
}

function initSrvRow(row) {
  const nameInput = row.querySelector('.srv-name');
  nameInput.addEventListener('change', function() {
    const match = srvServices[this.value.trim().toLowerCase()];
    const idField   = row.querySelector('.srv-item-id');
    const uomField  = row.querySelector('.srv-uom-id');
    if (match) {
      // Matches an existing service master — link to it directly and
      // suggest its last known price; typing a name that ISN'T in the
      // catalog leaves item_id blank, so the server creates a new
      // service master for it automatically on submit.
      idField.value  = match.id;
      uomField.value = match.uom_id;
      if (!parseFloat(row.querySelector('.srv-price').value)) {
        row.querySelector('.srv-price').value = parseFloat(match.moving_avg_price || 0).toFixed(2);
      }
    } else {
      idField.value  = '';
      uomField.value = '';
    }
    calcSrvRow(row);
  });
  row.querySelector('.srv-qty').addEventListener('input', () => calcSrvRow(row));
  row.querySelector('.srv-price').addEventListener('input', () => calcSrvRow(row));
}

function addSrvLine() {
  const tbody = document.getElementById('srv-lines-body');
  if (!tbody) return;
  const i = srvLineIdx++;
  tbody.insertAdjacentHTML('beforeend', `
    <tr class="srv-line">
      <td class="text-muted small text-center srv-line-num">${i + 1}</td>
      <td>
        <input type="text" name="lines[${i}][service_name]" class="form-control form-control-sm srv-name" list="service-datalist" placeholder="Type or pick a service...">
        <input type="hidden" name="lines[${i}][item_id]" class="srv-item-id">
        <input type="hidden" name="lines[${i}][uom_id]" class="srv-uom-id">
      </td>
      <td><input type="number" name="lines[${i}][qty_requested]" class="form-control form-control-sm srv-qty" value="1" min="0.001" step="0.001"></td>
      <td><input type="number" name="lines[${i}][est_price]" class="form-control form-control-sm srv-price" value="0" min="0" step="0.01"></td>
      <td><input type="text" class="form-control form-control-sm srv-value fw-bold text-end" readonly value="0.00"></td>
      <td><input type="text" name="lines[${i}][remarks]" class="form-control form-control-sm" placeholder="Optional..."></td>
      <td><button type="button" class="btn btn-xs btn-outline-danger py-0 px-1" onclick="removeSrvLine(this)"><i class="bi bi-x"></i></button></td>
    </tr>`);
  initSrvRow(tbody.lastElementChild);
}

function removeSrvLine(btn) {
  const rows = document.querySelectorAll('.srv-line');
  if (rows.length <= 1) return;
  btn.closest('tr').remove();
  document.querySelectorAll('.srv-line-num').forEach((el, idx) => el.textContent = idx + 1);
  recalcSrvTotal();
}

document.querySelectorAll('.srv-line').forEach(initSrvRow);
recalcSrvTotal();

// ---- v1.17.6: create a material from this screen ----
(function(){
  var btn = document.getElementById('qmSave'); if (!btn) return;
  var msg = document.getElementById('qmMsg');
  function say(cls, t){ msg.className = 'small mt-2 ' + cls; msg.textContent = t; }
  btn.addEventListener('click', function(){
    var name = document.getElementById('qmName').value.trim();
    if (!name) return say('text-danger', 'Enter the material description.');
    if (!document.getElementById('qmUom').value) return say('text-danger', 'Choose a unit.');
    btn.disabled = true; say('text-muted', 'Saving...');
    var fd = new FormData();
    fd.append('action', 'msme_erp_quick_material');
    fd.append('nonce', window.MSME_ERP.nonce);
    fd.append('item_name', name);
    fd.append('item_code', document.getElementById('qmCode').value);
    fd.append('base_uom_id', document.getElementById('qmUom').value);
    fd.append('standard_price', document.getElementById('qmPrice').value);
    fd.append('gst_rate', document.getElementById('qmGst').value);
    fd.append('hsn_code', document.getElementById('qmHsn').value);
    fd.append('confirm_dup', document.getElementById('qmDup').checked ? '1' : '');
    fetch(window.MSME_ERP.ajaxUrl, { method: 'POST', body: fd }).then(function(r){ return r.json(); }).then(function(j){
      btn.disabled = false;
      if (!j || !j.success) return say('text-danger', (j && j.data && j.data.message) || 'The material could not be saved.');
      var it = j.data; prItems[it.id] = it;
      document.querySelectorAll('.pr-item-sel').forEach(fillItemSelect);
      var sels = document.querySelectorAll('.pr-item-sel');
      for (var i = 0; i < sels.length; i++) { if (!sels[i].value) { $(sels[i]).val(String(it.id)).trigger('change'); break; } }
      say('text-success', 'Material ' + it.item_code + ' created and added to the list. You can create another.');
      document.getElementById('qmName').value = ''; document.getElementById('qmCode').value = ''; document.getElementById('qmHsn').value = ''; document.getElementById('qmDup').checked = false;
    }).catch(function(){ btn.disabled = false; say('text-danger', 'Network error - please try again.'); });
  });
})();
// ---- v1.20.3: status strip + connection test ----
(function(){
  var main = document.getElementById('prStatusMain'); if (!main) return;
  var bar = document.getElementById('prStatus');
  var n = document.querySelector('.pr-item-sel') ? document.querySelector('.pr-item-sel').options.length - 1 : 0;
  var s2 = !!($.fn && $.fn.select2);
  bar.className = bar.className.replace('bg-warning-subtle', n > 0 ? 'bg-success-subtle' : 'bg-danger-subtle');
  main.innerHTML = '<strong>Page script OK</strong> &middot; jQuery ' + $.fn.jquery + ' &middot; select2 ' + (s2 ? 'loaded' : '<b class="text-danger">NOT loaded</b>') + ' &middot; dropdown has <strong>' + n + '</strong> material(s)'
    + (n === 0 ? ' &mdash; <b>no material exists for this company yet: use + New Material, then Save material &amp; add to list</b>' : '');
  // v1.20.8: report what the browser really loaded, so a stale cached file is visible at a glance.
  var sels = document.querySelectorAll('select.pr-item-sel'), dup = 0;
  sels.forEach(function(x){ var c = 0, e = x.nextElementSibling; while (e && e.classList.contains('select2-container')) { c++; e = e.nextElementSibling; } if (c !== 1) dup++; });
  main.innerHTML += ' &middot; erp.js ' + (window.MSME_ERP_JS ? 'v' + window.MSME_ERP_JS : '<b class="text-danger">old/cached &mdash; press Ctrl+F5</b>')
    + ' &middot; widgets ' + (dup ? '<b class="text-danger">' + dup + ' row(s) wrong</b>' : 'OK');
  document.getElementById('prPing').addEventListener('click', function(){
    var out = document.getElementById('prPingOut'); out.className = 'mt-1 text-muted'; out.textContent = 'Testing...';
    var fd = new FormData(); fd.append('action', 'msme_erp_link_ping'); fd.append('nonce', window.MSME_ERP.nonce);
    fetch(window.MSME_ERP.ajaxUrl, {method: 'POST', body: fd, credentials: 'same-origin'}).then(function(r){
      return r.text().then(function(t){ var j; try { j = JSON.parse(t); } catch(e) {}
        if (!j) { out.className = 'mt-1 text-danger'; out.textContent = 'admin-ajax.php answered HTTP ' + r.status + ' but not JSON (blocked by a firewall/security plugin or a PHP error?): ' + t.replace(/<[^>]+>/g,' ').slice(0,160); return; }
        if (!j.success) { out.className = 'mt-1 text-danger'; out.textContent = 'Server refused: ' + ((j.data && j.data.message) || 'unknown'); return; }
        var d = j.data; out.className = 'mt-1 text-success';
        out.textContent = 'Connected. Plugin v' + d.version + ' \u00b7 company id ' + d.company_id + ' \u00b7 table ' + d.table + (d.table_exists ? '' : ' (MISSING)') + ' \u00b7 rows in table (all companies): ' + d.items_all_companies + ' \u00b7 this company: ' + d.items_this_company + ' \u00b7 listed in dropdown: ' + d.items_in_dropdown;
      });
    }).catch(function(e){ out.className = 'mt-1 text-danger'; out.textContent = 'Could not reach admin-ajax.php: ' + e; });
  });
})();
</script>
JS;

$serviceItemsJson = json_encode(array_combine(
    array_map(fn($s) => strtolower($s['item_name']), $serviceItems),
    array_map(fn($s) => ['id' => $s['id'], 'uom_id' => $s['uom_id'], 'moving_avg_price' => $s['moving_avg_price']], $serviceItems)
));

echo str_replace(
    ['__ITEMS_JSON__', '__SERVICE_ITEMS_JSON__', '__UOM_OPTS__'],
    [$itemsJson, $serviceItemsJson, $uomOptsHtml],
    $extraScripts
);

?>
