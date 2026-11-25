<?php
/**
 * MSME ERP — Public Supplier Quotation Form
 * Reached via a plain query var (?msme_rfq=<token>) handled on WP's
 * 'init' hook in msme-erp.php — NOT through the ERP's admin.php
 * dispatcher, since suppliers never get a WordPress login. The random
 * token in rfq_suppliers.token IS the access credential; possession of
 * the emailed link is what authorizes viewing/submitting this form.
 * Developed by: Prigag Online Pvt. Ltd.
 */

defined('ABSPATH') || exit;

$token = sanitize_text_field($_GET['msme_rfq'] ?? '');

function msme_rfq_public_page(string $title, string $bodyHtml): void {
    ?><!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= esc_html($title) ?></title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<style>
  body { background:#f0f2f5; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; }
  .rfq-wrap { max-width: 900px; margin: 30px auto; padding: 0 15px 40px; }
  .rfq-header { background:#1565c0; color:#fff; border-radius:10px 10px 0 0; padding: 18px 24px; }
  .rfq-card { border:0; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); }
</style>
</head>
<body>
<div class="rfq-wrap">
  <?= $bodyHtml ?>
</div>
</body>
</html><?php
    exit;
}

if (!$token) {
    msme_rfq_public_page('Invalid Link', '<div class="alert alert-danger mt-4">Invalid quotation link.</div>');
}

$rs = DB::fetchOne(
    "SELECT rs.*, r.rfq_number, r.due_date, r.notes, r.status AS rfq_status, r.auto_award_status, r.company_id, s.name AS supplier_name
     FROM rfq_suppliers rs
     JOIN rfqs r ON r.id = rs.rfq_id
     JOIN suppliers s ON s.id = rs.supplier_id
     WHERE rs.token = ?", [$token]
);

if (!$rs) {
    msme_rfq_public_page('Invalid Link', '<div class="alert alert-danger mt-4"><i class="bi bi-exclamation-triangle me-2"></i>This quotation link is invalid or has expired. Please contact the buyer for a new link.</div>');
}

// Public token page: no logged-in user, so pin this request to the RFQ's own company.
ErpDB::useCompany((int)($rs['company_id'] ?? 0));
$companyName = msme_erp_company_name();

// Mark as viewed (first open only)
if ($rs['status'] === 'SENT') {
    DB::update('rfq_suppliers', ['status' => 'VIEWED', 'viewed_at' => current_time('mysql')], 'id=?', [$rs['id']]);
    $rs['status'] = 'VIEWED';
}

$lines = DB::fetchAll(
    "SELECT rl.*, i.item_code, i.item_name, u.code AS uom_code
     FROM rfq_lines rl LEFT JOIN items i ON i.id=rl.item_id LEFT JOIN units_of_measure u ON u.id=rl.uom_id
     WHERE rl.rfq_id = ? ORDER BY rl.line_num", [$rs['rfq_id']]
);
$existingQuotes = [];
foreach (DB::fetchAll("SELECT * FROM rfq_quote_lines WHERE rfq_supplier_id=?", [$rs['id']]) as $ql) {
    $existingQuotes[$ql['rfq_line_id']] = $ql;
}

$error = '';
$submitted = false;
$negoNotice = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!wp_verify_nonce($_POST['_wpnonce'] ?? '', 'msme_rfq_' . $token)) {
        $error = 'Security check failed — please reload the page and try again.';
    } elseif (!empty($rs['auto_award_status']) && ($_POST['do'] ?? '') === 'submit_quote') {
        $error = 'This RFQ has closed and the quotations have been evaluated, so new quotations can no longer be submitted.';
    } elseif (($_POST['do'] ?? '') === 'negotiation_accept') {
        $result = msme_erp_accept_rfq_negotiation($rs['id']);
        if ($result['ok']) {
            $rs = DB::fetchOne("SELECT rs.*, r.rfq_number, r.due_date, r.notes, r.status AS rfq_status, r.auto_award_status, r.company_id, s.name AS supplier_name FROM rfq_suppliers rs JOIN rfqs r ON r.id = rs.rfq_id JOIN suppliers s ON s.id = rs.supplier_id WHERE rs.id = ?", [$rs['id']]);
            $negoNotice = 'accepted';
        } else {
            $error = $result['error'];
        }
    } elseif (($_POST['do'] ?? '') === 'negotiation_decline') {
        $outcome = msme_erp_decline_rfq_negotiation($rs['id']);
        $rs = DB::fetchOne("SELECT rs.*, r.rfq_number, r.due_date, r.notes, r.status AS rfq_status, r.auto_award_status, r.company_id, s.name AS supplier_name FROM rfq_suppliers rs JOIN rfqs r ON r.id = rs.rfq_id JOIN suppliers s ON s.id = rs.supplier_id WHERE rs.id = ?", [$rs['id']]);
        $negoNotice = $outcome === 'EXHAUSTED' ? 'exhausted' : 'declined';
    } elseif (($rs['negotiation_status'] ?? 'NONE') !== 'NONE') {
        // A negotiation round is already live (or finished) on this
        // supplier's original submission — the base quote is locked from
        // here on so it can't shift out from under an in-flight discount
        // ask; the supplier can only Accept/Decline below.
        $error = 'A discount negotiation is already underway on your submitted quotation — please respond to the offer below instead of resubmitting.';
    } else {
        $postLines = $_POST['lines'] ?? [];
        $anyPrice  = false;
        foreach ($postLines as $rlId => $vals) {
            if ((float)($vals['unit_price'] ?? 0) > 0) $anyPrice = true;
        }
        if (!$anyPrice) {
            $error = 'Enter a unit price for at least one line item.';
        } else {
            try {
                DB::beginTransaction();
                $total = 0.0;
                foreach ($lines as $l) {
                    $vals        = $postLines[$l['id']] ?? [];
                    $unitPrice   = (float)($vals['unit_price'] ?? 0);
                    $deliveryDays= $vals['delivery_days'] !== '' ? (int)($vals['delivery_days'] ?? 0) : null;
                    $lineRemarks = sanitize_text_field($vals['remarks'] ?? '');
                    // Replace-on-submit: simplest correct way to keep this
                    // upsert-able across repeat/edited submissions without
                    // relying on database-specific ON DUPLICATE KEY syntax.
                    DB::query("DELETE FROM rfq_quote_lines WHERE rfq_supplier_id=? AND rfq_line_id=?", [$rs['id'], $l['id']]);
                    DB::insert('rfq_quote_lines', [
                        'rfq_supplier_id' => $rs['id'],
                        'rfq_line_id'     => $l['id'],
                        'unit_price'      => $unitPrice,
                        'delivery_days'   => $deliveryDays,
                        'remarks'         => $lineRemarks,
                    ]);
                    $total += $unitPrice * (float)$l['qty'];
                }
                DB::update('rfq_suppliers', [
                    'status'          => 'SUBMITTED',
                    'submitted_at'    => current_time('mysql'),
                    'total_quoted'    => round($total, 2),
                    'payment_terms'   => sanitize_text_field($_POST['payment_terms'] ?? ''),
                    'delivery_period' => sanitize_text_field($_POST['delivery_period'] ?? ''),
                    'incoterms'       => sanitize_text_field($_POST['incoterms'] ?? ''),
                    'certifications'  => sanitize_textarea_field($_POST['certifications'] ?? ''),
                    'remarks'         => sanitize_textarea_field($_POST['overall_remarks'] ?? ''),
                ], 'id=?', [$rs['id']]);
                DB::commit();
                // First-ever submission auto-kicks off the reverse
                // negotiation (buyer counter-offers a discount, emailed
                // to this same link) — no-op if one already exists.
                msme_erp_start_rfq_negotiation($rs['id']);
                $rs = DB::fetchOne("SELECT rs.*, r.rfq_number, r.due_date, r.notes, r.status AS rfq_status, r.auto_award_status, r.company_id, s.name AS supplier_name FROM rfq_suppliers rs JOIN rfqs r ON r.id = rs.rfq_id JOIN suppliers s ON s.id = rs.supplier_id WHERE rs.id = ?", [$rs['id']]);
                $submitted = true;
                $rs['status']          = 'SUBMITTED';
                $rs['payment_terms']   = sanitize_text_field($_POST['payment_terms'] ?? '');
                $rs['delivery_period'] = sanitize_text_field($_POST['delivery_period'] ?? '');
                $rs['incoterms']       = sanitize_text_field($_POST['incoterms'] ?? '');
                $rs['certifications']  = sanitize_textarea_field($_POST['certifications'] ?? '');
                foreach ($lines as $l) {
                    $existingQuotes[$l['id']] = [
                        'unit_price'    => $postLines[$l['id']]['unit_price'] ?? 0,
                        'delivery_days' => $postLines[$l['id']]['delivery_days'] ?? '',
                        'remarks'       => $postLines[$l['id']]['remarks'] ?? '',
                    ];
                }
            } catch (Throwable $e) {
                DB::rollback();
                error_log('MSME ERP RFQ public submit error: ' . $e->getMessage());
                $error = 'Something went wrong submitting your quotation. Please try again.';
            }
        }
    }
}

ob_start();
?>
<div class="card rfq-card mt-4 mb-4">
  <div class="rfq-header d-flex justify-content-between align-items-center flex-wrap gap-2">
    <div>
      <div class="fw-bold fs-5"><i class="bi bi-envelope-paper me-2"></i>Request for Quotation</div>
      <div class="small opacity-75"><?= esc_html($companyName) ?> — <?= esc_html($rs['rfq_number']) ?></div>
    </div>
    <div class="text-end small">
      <div>Supplier: <strong><?= esc_html($rs['supplier_name']) ?></strong></div>
      <?php if ($rs['due_date']): ?><div>Quote due by: <strong><?= esc_html($rs['due_date']) ?></strong></div><?php endif; ?>
    </div>
  </div>
  <div class="card-body">
    <?php if ($rs['notes']): ?><p class="text-muted small"><?= nl2br(esc_html($rs['notes'])) ?></p><?php endif; ?>

    <?php if ($submitted): ?>
    <div class="alert alert-success"><i class="bi bi-check-circle me-2"></i>Thank you — your quotation has been submitted.</div>
    <?php elseif ($negoNotice === 'accepted'): ?>
    <div class="alert alert-success"><i class="bi bi-check-circle me-2"></i>Offer accepted — thank you! Your price is recorded. All quotations are compared at the cut-off and the order goes to the best overall offer; we will contact you if you are selected.</div>
    <?php elseif ($negoNotice === 'declined'): ?>
    <div class="alert alert-info"><i class="bi bi-arrow-repeat me-2"></i>Thanks for letting us know — here's a revised offer below.</div>
    <?php elseif ($negoNotice === 'exhausted'): ?>
    <div class="alert alert-secondary"><i class="bi bi-info-circle me-2"></i>Thank you — we've noted that the discounted offers didn't work for you. Someone from our purchasing team will follow up with you directly.</div>
    <?php elseif ($error): ?>
    <div class="alert alert-danger small"><?= esc_html($error) ?></div>
    <?php endif; ?>

    <?php if ($rs['status'] === 'SUBMITTED' && !$submitted && ($rs['negotiation_status'] ?? 'NONE') === 'NONE'): ?>
    <div class="alert alert-info small"><i class="bi bi-info-circle me-2"></i>You already submitted a quotation on <?= esc_html($rs['submitted_at']) ?>. You can update it below and resubmit.</div>
    <?php endif; ?>

    <?php
    $negoStatus = $rs['negotiation_status'] ?? 'NONE';
    $negoTotal  = $negoStatus !== 'NONE' ? round((float)$rs['total_quoted'] * (1 - (float)$rs['negotiation_discount_pct'] / 100), 2) : 0;
    ?>

    <?php if ($negoStatus === 'IN_PROGRESS'): ?>
    <!-- Live counter-offer — the supplier can only Accept or Decline; the
         base quote below this is locked while a round is open. -->
    <div class="card border-0 mb-4" style="background:#fff8e6;border:1px solid #ffe08a !important;">
      <div class="card-body">
        <div class="fw-bold mb-2"><i class="bi bi-tags me-2"></i>Counter-Offer — Round <?= (int)$rs['negotiation_round'] ?></div>
        <p class="mb-1 small">Your quoted total: <strong><?= esc_html(msme_erp_cur()) ?><?= number_format((float)$rs['total_quoted'], 2) ?></strong></p>
        <p class="mb-2">We'd like to request an additional <strong><?= number_format((float)$rs['negotiation_discount_pct'], 2) ?>% discount</strong> — new total: <strong class="text-success"><?= esc_html(msme_erp_cur()) ?><?= number_format($negoTotal, 2) ?></strong></p>
        <div class="d-flex gap-2">
          <form method="POST" onsubmit="return confirm('Accept this offer at <?= esc_html(msme_erp_cur()) ?><?= number_format($negoTotal, 2) ?>? Your price is then considered with the other quotations at the cut-off; an order is placed only if you are selected.');">
            <input type="hidden" name="_wpnonce" value="<?= wp_create_nonce('msme_rfq_' . $token) ?>">
            <input type="hidden" name="do" value="negotiation_accept">
            <button type="submit" class="btn btn-success"><i class="bi bi-check-circle me-1"></i>Accept This Price</button>
          </form>
          <form method="POST" onsubmit="return confirm('Decline this offer? We will follow up with a revised discount request.');">
            <input type="hidden" name="_wpnonce" value="<?= wp_create_nonce('msme_rfq_' . $token) ?>">
            <input type="hidden" name="do" value="negotiation_decline">
            <button type="submit" class="btn btn-outline-secondary"><i class="bi bi-x-circle me-1"></i>Decline</button>
          </form>
        </div>
      </div>
    </div>
    <?php elseif ($negoStatus === 'ACCEPTED'): ?>
    <div class="alert alert-success small"><i class="bi bi-check-circle me-2"></i>You accepted a <?= number_format((float)$rs['negotiation_discount_pct'], 2) ?>% discount (round <?= (int)$rs['negotiation_round'] ?>) — your price of <?= esc_html(msme_erp_cur()) ?><?= number_format($negoTotal, 2) ?> is recorded. All quotations are compared at the cut-off and the order goes to the best overall offer; you will be contacted if you are selected.</div>
    <?php elseif ($negoStatus === 'CLOSED'): ?>
    <div class="alert alert-secondary small"><i class="bi bi-lock me-2"></i>This RFQ has reached its cut-off and the open offer was closed. Our purchasing team will contact you if you are selected.</div>
    <?php elseif ($negoStatus === 'EXHAUSTED' && $negoNotice !== 'exhausted'): ?>
    <div class="alert alert-secondary small"><i class="bi bi-info-circle me-2"></i>Our purchasing team will follow up with you directly about this quotation.</div>
    <?php endif; ?>

    <?php if ($negoStatus === 'NONE'): ?>
    <form method="POST">
      <input type="hidden" name="_wpnonce" value="<?= wp_create_nonce('msme_rfq_' . $token) ?>">
      <input type="hidden" name="do" value="submit_quote">
      <div class="table-responsive">
        <table class="table table-sm table-bordered align-middle">
          <thead class="table-light">
            <tr><th>Item</th><th>Qty</th><th style="width:140px">Unit Price (<?= esc_html(msme_erp_cur()) ?>)</th><th style="width:130px">Delivery (days)</th><th>Remarks</th></tr>
          </thead>
          <tbody>
            <?php foreach ($lines as $l): $ex = $existingQuotes[$l['id']] ?? null; ?>
            <tr>
              <td class="small"><div class="fw-bold"><?= $l['item_code'] ? esc_html($l['item_code']) : 'Item #'.(int)$l['item_id'] ?></div><div class="text-muted" style="font-size:.75rem"><?= esc_html($l['item_name'] ?? '—') ?></div></td>
              <td class="small"><?= number_format($l['qty'],3) ?> <?= esc_html($l['uom_code'] ?? '—') ?></td>
              <td><input type="number" step="0.01" min="0" class="form-control form-control-sm" name="lines[<?= $l['id'] ?>][unit_price]" value="<?= $ex ? esc_attr($ex['unit_price']) : '' ?>"></td>
              <td><input type="number" min="0" class="form-control form-control-sm" name="lines[<?= $l['id'] ?>][delivery_days]" value="<?= $ex && $ex['delivery_days'] !== null ? esc_attr($ex['delivery_days']) : '' ?>"></td>
              <td><input type="text" class="form-control form-control-sm" name="lines[<?= $l['id'] ?>][remarks]" value="<?= $ex ? esc_attr($ex['remarks']) : '' ?>" placeholder="e.g. brand, alt. spec"></td>
            </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <div class="row g-3 mt-1 mb-2">
        <div class="col-12"><h6 class="fw-bold small text-muted mb-0">COMMERCIAL TERMS</h6></div>
        <div class="col-md-4">
          <label class="form-label small">Payment Terms</label>
          <input type="text" name="payment_terms" class="form-control form-control-sm" value="<?= esc_attr($rs['payment_terms'] ?? '') ?>" placeholder="e.g. 50% advance, balance on delivery">
        </div>
        <div class="col-md-4">
          <label class="form-label small">Delivery Period</label>
          <input type="text" name="delivery_period" class="form-control form-control-sm" value="<?= esc_attr($rs['delivery_period'] ?? '') ?>" placeholder="e.g. 2 weeks from PO date">
        </div>
        <div class="col-md-4">
          <label class="form-label small">Incoterms <span class="text-muted fw-normal">(for imports)</span></label>
          <select name="incoterms" class="form-select form-select-sm">
            <?php $incotermOptions = ['' => 'N/A — domestic', 'EXW'=>'EXW','FOB'=>'FOB','CIF'=>'CIF','CFR'=>'CFR','FCA'=>'FCA','CPT'=>'CPT','CIP'=>'CIP','DAP'=>'DAP','DPU'=>'DPU','DDP'=>'DDP'];
            $curIncoterm = $rs['incoterms'] ?? ''; ?>
            <?php foreach ($incotermOptions as $val => $label): ?>
            <option value="<?= esc_attr($val) ?>" <?= $curIncoterm === $val ? 'selected' : '' ?>><?= esc_html($label) ?></option>
            <?php endforeach; ?>
          </select>
        </div>
        <div class="col-12">
          <label class="form-label small">Certificates / Awards <span class="text-muted fw-normal">(optional)</span></label>
          <textarea name="certifications" class="form-control form-control-sm" rows="2" placeholder="e.g. ISO 9001:2015, CE Certified, MSME Registered, any quality awards"><?= esc_textarea($rs['certifications'] ?? '') ?></textarea>
        </div>
      </div>

      <div class="mb-3">
        <label class="form-label small">Overall Remarks (optional)</label>
        <textarea name="overall_remarks" class="form-control form-control-sm" rows="2"><?= esc_textarea($rs['remarks'] ?? '') ?></textarea>
      </div>
      <button type="submit" class="btn btn-primary"><i class="bi bi-send me-1"></i><?= $rs['status'] === 'SUBMITTED' ? 'Update Quotation' : 'Submit Quotation' ?></button>
    </form>
    <?php endif; ?>
  </div>
</div>
<?php
$body = ob_get_clean();
msme_rfq_public_page('RFQ ' . $rs['rfq_number'] . ' — ' . $companyName, $body);
