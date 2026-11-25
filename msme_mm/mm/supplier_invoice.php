<?php
/**
 * MSME ERP — Remaining T-Code Handlers
 * MR01 ML81N F-28 F-53 MI01 MI07 LT01 VK11 MM60 ME2M PR53N
 * Developed by: Prigag Online Pvt. Ltd.
 */

$tcode = $_GET['tcode'] ?? 'MR01';
Auth::requireTcode($tcode);
$companyId = Auth::companyId();
$error = $success = '';

// =========================================================
// MR01 — Supplier Invoice Verification (MIRO)
// =========================================================
if ($tcode === 'MR01') {
    $pageTitle = 'Supplier Invoice (MIRO)'; $pageTcode = 'MR01';
    $breadcrumbs = ['MM','Invoice Verification (MR01)'];
    erp_render_page_header();

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
        $d = $_POST;
        try {
            $supplierId = (int)($d['supplier_id'] ?? 0);
            $refNo      = trim((string)($d['ref_invoice_num'] ?? ''));
            $invDate    = (string)($d['invoice_date'] ?? '');
            $base       = round((float)($d['base_amount'] ?? 0), 2);
            $tax        = round((float)($d['tax_amount'] ?? 0), 2);
            $tds        = round((float)($d['tds_amount'] ?? 0), 2);
            $total      = round($base + $tax, 2);
            $net        = round($total - $tds, 2);

            if (!$supplierId)                                   throw new Exception('Select a supplier.');
            if ($refNo === '')                                  throw new Exception("Enter the supplier's invoice number.");
            if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $invDate)) throw new Exception('Enter a valid invoice date.');
            if ($base < 0 || $tax < 0 || $tds < 0)              throw new Exception('Amounts cannot be negative.');
            if ($total <= 0)                                    throw new Exception('Invoice amount must be greater than zero.');
            if ($tds > $total)                                  throw new Exception('TDS cannot exceed the invoice total.');

            // v1.17.0 — GST split, reverse charge / ITC flags, TDS section
            $rcm    = !empty($d['reverse_charge']) ? 1 : 0;
            $itcOk  = !empty($d['itc_ineligible']) ? 0 : 1;
            $tdsSec = trim((string)($d['tds_section'] ?? ''));
            if ($tds > 0 && $tdsSec === '') throw new Exception('Select the TDS section when TDS is deducted.');
            // Reverse charge: the tax is paid to the government, not to the supplier, so the supplier is owed the base only.
            if ($rcm) { $total = $base; $net = round($base - $tds, 2); if ($net < 0) throw new Exception('TDS cannot exceed the invoice base on a reverse-charge invoice.'); }
            $supRow = DB::fetchOne("SELECT gstin FROM suppliers WHERE id=? AND company_id=?", [$supplierId, $companyId]);
            if (!$supRow) throw new Exception('Supplier not found.');
            $gstSplit = msme_erp_gst_split($tax, $supRow['gstin'], $companyId);
            // three-way match: PO price x goods received (accepted) vs what is being billed
            [$matchExpected, $matchVar, $matchBlocked, $matchMsg] = msme_erp_fin_three_way_match((int)($d['po_id'] ?? 0), $supplierId, $base);

            // Same supplier + same invoice number can only be booked once.
            $dup = msme_erp_invoice_find_duplicate($companyId, $supplierId, $refNo);
            if ($dup) throw new Exception("Supplier invoice {$refNo} is already posted for this supplier as {$dup['invoice_number']} (dated {$dup['invoice_date']}). Duplicate not allowed.");

            // v1.16.2 GRN-based check: no payment liability for goods still on QC hold
            // (or never received against the chosen PO).
            $grnErr = msme_erp_invoice_grn_check($companyId, $supplierId, (int)($d['po_id'] ?? 0));
            if ($grnErr !== '') throw new Exception($grnErr);

            // The scanned / uploaded file, if any — only our own validated token is accepted.
            $attach = msme_erp_invoice_valid_file($d['attachment_file'] ?? '');
            $aiRead = $attach && !empty($d['ai_extracted']) ? 1 : 0;

            DB::beginTransaction();
            $result = DB::callProcedure('sp_get_next_number', [$companyId, 'SINV', date('Y')], ['p_doc_number']);
            $invNum = $result['p_doc_number'];
            $due    = date('Y-m-d', strtotime($invDate . ' +' . max(0, (int)($d['credit_days'] ?? 30)) . ' days'));

            $siId = DB::insert('supplier_invoices', [
                'company_id'     => $companyId,
                'invoice_number' => $invNum,
                'invoice_date'   => $invDate,
                'supplier_id'    => $supplierId,
                'po_id'          => !empty($d['po_id']) ? (int)$d['po_id'] : null,
                'ref_invoice_num'=> $refNo,
                'base_amount'    => $base,
                'tax_amount'     => $tax,
                'tds_amount'     => $tds,
                'total_amount'   => $total,
                'paid_amount'    => 0,
                'due_amount'     => $net,
                'due_date'       => $due,
                'status'         => $matchBlocked ? 'BLOCKED' : 'PENDING',
                'block_reason'   => $matchBlocked ? $matchMsg : null,
                'match_expected' => $matchExpected,
                'cgst_amount'    => $gstSplit['cgst'], 'sgst_amount' => $gstSplit['sgst'], 'igst_amount' => $gstSplit['igst'],
                'itc_eligible'   => $itcOk, 'reverse_charge' => $rcm, 'tds_section' => $tdsSec !== '' ? $tdsSec : null,
                'attachment_file'=> $attach ?: null,
                'ai_extracted'   => $aiRead,
                'created_by'     => Auth::userId(),
            ]);
            Auth::audit('supplier_invoices', (int)$siId, 'INSERT', [], ['invoice_number' => $invNum, 'ref' => $refNo, 'scan' => $attach ? 'yes' : 'no']);
            // v1.17.0 — the invoice and its ledger entry are one unit: a closed period or a missing GL mapping stops the whole posting.
            msme_erp_fin_post_supplier_invoice((int)$siId);
            DB::commit();
            $success = "Supplier Invoice {$invNum} created. Due: " . msme_erp_cur() . number_format($net,2)." by {$due}"
                . ($matchBlocked ? ' — BLOCKED for payment: ' . $matchMsg . ' Release it in MRBR once checked.' : '');
        } catch (Throwable $e) { try { DB::rollback(); } catch (Throwable $e2) {} $error = $e->getMessage(); }
        if ($success !== '' && $error === '') erp_post_redirect($success);   // PRG: refresh can't re-post
    }

    $suppliers  = DB::fetchAll("SELECT id, supplier_code, name, credit_days FROM suppliers WHERE company_id=? ORDER BY name", [$companyId]);
    $openPOs    = DB::fetchAll("SELECT id, po_number, grand_total FROM purchase_orders WHERE company_id=? AND status IN('APPROVED','PARTIAL','CLOSED') ORDER BY id DESC LIMIT 50", [$companyId]);
    $invHistory = DB::fetchAll("SELECT si.*, s.name AS supp FROM supplier_invoices si JOIN suppliers s ON s.id=si.supplier_id WHERE si.company_id=? ORDER BY si.id DESC LIMIT 20", [$companyId]);
    $hasKey     = trim((string)get_option('msme_erp_gemini_api_key')) !== '';

     ?>
    <?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>
    <?php if ($success): ?><div class="alert alert-success small py-2"><?= htmlspecialchars($success) ?></div><?php endif; ?>
    <div class="row g-3">
      <div class="col-md-5">

        <!-- Scan / upload -->
        <div class="card border-0 shadow-sm mb-3">
          <div class="card-header bg-info text-dark fw-bold small py-2"><i class="bi bi-camera me-2"></i>Scan / Upload Invoice</div>
          <div class="card-body py-3">
            <div class="d-flex gap-2 flex-wrap">
              <label class="btn btn-sm btn-outline-primary mb-0"><i class="bi bi-camera-fill me-1"></i>Take Photo
                <input type="file" id="scan-cam" accept="image/*" capture="environment" hidden></label>
              <label class="btn btn-sm btn-outline-secondary mb-0"><i class="bi bi-upload me-1"></i>Upload Image / PDF
                <input type="file" id="scan-file" accept="image/jpeg,image/png,image/webp,application/pdf" hidden></label>
            </div>
            <div class="text-muted mt-2" style="font-size:.72rem">
              The invoice is read automatically and the form below is filled in. Nothing is posted until you check it and press <strong>Post Invoice</strong>.
              <?php if (!$hasKey): ?><span class="text-danger d-block">No Gemini API key saved (Settings → AI &amp; Notifications) — the file will be attached but fields must be typed in.</span><?php endif; ?>
            </div>
            <div id="scan-status" class="small mt-2" style="display:none"></div>
            <div id="scan-warn" class="mt-2"></div>
          </div>
        </div>

        <div class="card border-0 shadow-sm">
          <div class="card-header bg-warning text-dark fw-bold small py-2"><i class="bi bi-receipt me-2"></i>Post Supplier Invoice — MR01</div>
          <div class="card-body">
            <form method="POST" id="miro-form">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
              <input type="hidden" name="attachment_file" id="miro-attach" value="">
              <input type="hidden" name="ai_extracted" id="miro-ai" value="0">
              <div class="mb-2"><label class="form-label fw-semibold small">Supplier *</label>
                <select name="supplier_id" class="form-select form-select-sm select2" required id="miro-supp">
                  <option value="">— Select —</option>
                  <?php foreach ($suppliers as $s): ?><option value="<?= $s['id'] ?>" data-days="<?= $s['credit_days'] ?>" <?= (int)($_POST['supplier_id'] ?? 0) === (int)$s['id'] && $error ? 'selected' : '' ?>><?= htmlspecialchars($s['supplier_code'].' — '.$s['name']) ?></option><?php endforeach; ?>
                </select>
              </div>
              <div class="mb-2"><label class="form-label fw-semibold small">Ref PO (optional)</label>
                <select name="po_id" class="form-select form-select-sm select2" id="miro-po">
                  <option value="">— None —</option>
                  <?php foreach ($openPOs as $po): ?><option value="<?= $po['id'] ?>"><?= htmlspecialchars($po['po_number']) ?> (<?= esc_html(msme_erp_cur()) ?><?= number_format($po['grand_total'],0) ?>)</option><?php endforeach; ?>
                </select>
              </div>
              <div class="mb-2"><label class="form-label fw-semibold small">Supplier Invoice# *</label>
                <input type="text" name="ref_invoice_num" id="miro-ref" class="form-control form-control-sm" required maxlength="50" placeholder="Supplier's invoice number" value="<?= $error ? htmlspecialchars($_POST['ref_invoice_num'] ?? '') : '' ?>"></div>
              <div class="mb-2"><label class="form-label fw-semibold small">Invoice Date *</label>
                <input type="date" name="invoice_date" id="miro-date" class="form-control form-control-sm" value="<?= $error ? htmlspecialchars($_POST['invoice_date'] ?? date('Y-m-d')) : date('Y-m-d') ?>" required></div>
              <div class="row g-2 mb-2">
                <div class="col-6"><label class="form-label small mb-0">Base Amount</label><input type="number" name="base_amount" class="form-control form-control-sm" value="<?= $error ? htmlspecialchars($_POST['base_amount'] ?? '0') : '0' ?>" step="0.01" min="0" id="miro-base"></div>
                <div class="col-6"><label class="form-label small mb-0">GST Amount</label><input type="number" name="tax_amount" class="form-control form-control-sm" value="<?= $error ? htmlspecialchars($_POST['tax_amount'] ?? '0') : '0' ?>" step="0.01" min="0" id="miro-tax"></div>
              </div>
              <div class="row g-2 mb-2">
                <div class="col-6"><label class="form-label small mb-0">TDS Amount</label><input type="number" name="tds_amount" id="miro-tds" class="form-control form-control-sm" value="<?= $error ? htmlspecialchars($_POST['tds_amount'] ?? '0') : '0' ?>" step="0.01" min="0"></div>
                <div class="col-6"><label class="form-label small mb-0">Credit Days</label><input type="number" name="credit_days" class="form-control form-control-sm" value="<?= $error ? (int)($_POST['credit_days'] ?? 30) : 30 ?>" min="0" id="miro-days"></div>
              </div>
              <?php $tdsSecs = DB::fetchAll("SELECT section, description, rate_pct FROM tds_sections WHERE company_id=? AND is_active=1 ORDER BY section", [$companyId]); ?>
              <div class="mb-2"><label class="form-label small mb-0">TDS Section <span class="text-muted">(needed if TDS &gt; 0)</span></label>
                <select name="tds_section" class="form-select form-select-sm"><option value="">— none —</option>
                  <?php foreach ($tdsSecs as $ts): ?><option value="<?= esc_attr($ts['section']) ?>"<?= $error && ($_POST['tds_section'] ?? '') === $ts['section'] ? ' selected' : '' ?>><?= esc_html($ts['section'] . ' — ' . $ts['description'] . ' (' . rtrim(rtrim($ts['rate_pct'], '0'), '.') . '%)') ?></option><?php endforeach; ?>
                </select></div>
              <div class="d-flex gap-3 mb-2 small">
                <label class="form-check"><input type="checkbox" class="form-check-input" name="reverse_charge" value="1"<?= $error && !empty($_POST['reverse_charge']) ? ' checked' : '' ?>> <span class="form-check-label">Reverse charge (RCM)</span></label>
                <label class="form-check"><input type="checkbox" class="form-check-input" name="itc_ineligible" value="1"<?= $error && !empty($_POST['itc_ineligible']) ? ' checked' : '' ?>> <span class="form-check-label">ITC not eligible (blocked credit)</span></label>
              </div>
              <div class="alert alert-info small py-1 mt-2">
                Grand Total: <strong id="miro-total"><?= esc_html(msme_erp_cur()) ?>0.00</strong>
                <span class="ms-3">Net payable (after TDS): <strong id="miro-net"><?= esc_html(msme_erp_cur()) ?>0.00</strong></span>
              </div>
              <div id="miro-attach-note" class="small text-success mb-2" style="display:none"></div>
              <button type="submit" class="btn btn-warning text-dark btn-sm w-100"><i class="bi bi-save me-1"></i>Post Invoice</button>
            </form>
          </div>
        </div>
      </div>
      <div class="col-md-7">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-list me-2"></i>Recent Supplier Invoices</div>
          <div class="card-body p-0">
            <table class="table table-sm table-hover mb-0 erp-table">
              <thead><tr><th>Invoice#</th><th>Supplier</th><th>Date</th><th>Total</th><th>Due Amt</th><th>Status</th><th>Copy</th></tr></thead>
              <tbody>
                <?php foreach ($invHistory as $si):
                  $sc = ['PENDING'=>'warning','PARTIAL'=>'info','PAID'=>'success','CANCELLED'=>'secondary','BLOCKED'=>'danger'];
                  $att = msme_erp_invoice_valid_file($si['attachment_file'] ?? '');
                ?>
                <tr>
                  <td class="fw-bold small"><?= htmlspecialchars($si['invoice_number']) ?><?php if (!empty($si['ref_invoice_num'])): ?><div class="text-muted fw-normal" style="font-size:.68rem"><?= htmlspecialchars($si['ref_invoice_num']) ?></div><?php endif; ?></td>
                  <td class="small"><?= htmlspecialchars($si['supp']) ?></td>
                  <td class="small"><?= $si['invoice_date'] ?></td>
                  <td class="small"><?= esc_html(msme_erp_cur()) ?><?= number_format($si['total_amount'],2) ?></td>
                  <td class="small text-danger fw-bold"><?= esc_html(msme_erp_cur()) ?><?= number_format($si['due_amount'],2) ?></td>
                  <td><span class="badge bg-<?= $sc[$si['status']] ?? 'secondary' ?>"><?= $si['status'] ?></span></td>
                  <td class="small"><?php if ($att): ?><a href="<?= esc_url(msme_erp_invoice_file_url($att)) ?>" target="_blank" rel="noopener" title="View scanned invoice"><i class="bi bi-paperclip"></i></a><?php if (!empty($si['ai_extracted'])): ?> <span class="badge bg-info text-dark" title="Read automatically from the scan">AI</span><?php endif; ?><?php else: ?><span class="text-muted">—</span><?php endif; ?></td>
                </tr>
                <?php endforeach; ?>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    <?php
    $miroJs = <<<'JS'
<script>
(function () {
  var AJAX = __AJAX__, NONCE = __NONCE__, CUR = __CUR__;
  function $(id) { return document.getElementById(id); }
  function fire(el) { el.dispatchEvent(new Event('change', { bubbles: true })); }
  function mark(el) { if (el) { el.classList.add('border-info'); el.style.backgroundColor = '#eef9fd'; } }
  function setVal(id, v) {
    var el = $(id); if (!el || v === null || v === undefined || v === '') return false;
    el.value = v; fire(el); mark(el.classList.contains('select2') ? (el.nextElementSibling || el) : el); return true;
  }
  function inr(n) { return CUR + (Math.round((n || 0) * 100) / 100).toFixed(2); }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

  function recalc() {
    var b = parseFloat($('miro-base').value) || 0, t = parseFloat($('miro-tax').value) || 0, d = parseFloat($('miro-tds').value) || 0;
    $('miro-total').textContent = inr(b + t); $('miro-net').textContent = inr(b + t - d);
  }
  ['miro-base', 'miro-tax', 'miro-tds'].forEach(function (id) { var e = $(id); if (e) e.addEventListener('input', recalc); });
  var sup = $('miro-supp');
  if (sup) sup.addEventListener('change', function () {
    var d = this.options[this.selectedIndex] && this.options[this.selectedIndex].dataset;
    if (d && d.days && !$('miro-days').dataset.scanned) $('miro-days').value = d.days;
  });
  recalc();

  // Shrink big phone photos before upload (faster, and well under the size limit). PDFs go as-is.
  function shrink(file) {
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 1500000) return resolve(file);
      var img = new Image(), url = URL.createObjectURL(file);
      img.onload = function () {
        var max = 2200, r = Math.min(1, max / Math.max(img.width, img.height));
        var c = document.createElement('canvas'); c.width = Math.round(img.width * r); c.height = Math.round(img.height * r);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function (b) { resolve(b ? new File([b], 'invoice.jpg', { type: 'image/jpeg' }) : file); }, 'image/jpeg', 0.88);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  function status(html, cls) { var s = $('scan-status'); s.style.display = ''; s.className = 'small mt-2 ' + (cls || ''); s.innerHTML = html; }
  function warnBox(list, cls) {
    $('scan-warn').innerHTML = (list || []).map(function (w) { return '<div class="alert alert-' + (cls || 'warning') + ' py-1 px-2 small mb-1">' + w + '</div>'; }).join('');
  }

  function handle(file) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024 && !/^image\//.test(file.type)) { status('PDF is larger than 10 MB.', 'text-danger'); return; }
    status('<span class="spinner-border spinner-border-sm me-2"></span>Reading invoice\u2026 this can take up to a minute.', 'text-muted');
    warnBox([]);
    shrink(file).then(function (f) {
      var fd = new FormData();
      fd.append('action', 'msme_erp_invoice_scan'); fd.append('nonce', NONCE); fd.append('invoice', f, f.name || 'invoice');
      return fetch(AJAX, { method: 'POST', body: fd, credentials: 'same-origin' });
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || !j.success) { status(esc((j && j.data && j.data.message) || 'Upload failed.'), 'text-danger'); return; }
      var o = j.data;
      $('miro-attach').value = o.file; $('miro-ai').value = o.ai_ok ? '1' : '0';
      var n = $('miro-attach-note'); n.style.display = '';
      n.innerHTML = '<i class="bi bi-paperclip"></i> Invoice copy attached \u2014 <a href="' + esc(o.file_url) + '" target="_blank" rel="noopener">view</a>';
      if (!o.ai_ok) { status(esc(o.message || 'Could not read the invoice.'), 'text-warning'); return; }

      var d = o.data, m = o.match, warns = (d.warnings || []).map(esc), notes = [];
      if (m.supplier_id) { setVal('miro-supp', m.supplier_id); notes.push('Supplier: ' + esc(m.supplier_label) + ' <span class="text-muted">(matched by ' + esc(m.supplier_how) + ')</span>'); }
      else warns.push('Supplier' + (d.supplier_name ? ' \u201C' + esc(d.supplier_name) + '\u201D' : '') + ' was not found in your supplier master' + (d.supplier_gstin ? ' (GSTIN ' + esc(d.supplier_gstin) + ')' : '') + ' \u2014 select it manually, or create it in the supplier master first.');
      if (m.po_id) { setVal('miro-po', m.po_id); notes.push('PO: ' + esc(m.po_label)); }
      else if (d.po_number) warns.push('PO reference \u201C' + esc(d.po_number) + '\u201D on the invoice is not an open PO in the system.');
      setVal('miro-ref', d.invoice_number); setVal('miro-date', d.invoice_date);
      if (d.base_amount !== null) setVal('miro-base', d.base_amount);
      if (d.tax_amount !== null)  setVal('miro-tax', d.tax_amount);
      if (d.tds_amount)           setVal('miro-tds', d.tds_amount);
      if (m.credit_days !== null) { $('miro-days').value = m.credit_days; $('miro-days').dataset.scanned = '1'; mark($('miro-days')); }
      recalc();
      if (!d.invoice_number) warns.push('The supplier invoice number could not be read \u2014 enter it.');
      if (m.duplicate) warns.unshift('<strong>Duplicate:</strong> this supplier\u2019s invoice ' + esc(d.invoice_number) + ' is already posted as ' + esc(m.duplicate.invoice_number) + ' (' + esc(m.duplicate.invoice_date) + '). Posting will be blocked.');
      status('<i class="bi bi-check-circle-fill text-success me-1"></i>Invoice read \u2014 <strong>check the highlighted fields against the paper invoice</strong>, then post.' + (notes.length ? '<div class="text-muted mt-1">' + notes.join(' &middot; ') + '</div>' : ''), '');
      warnBox(warns, m.duplicate ? 'danger' : 'warning');
    }).catch(function () { status('Could not reach the server. Check your connection and try again.', 'text-danger'); });
  }
  ['scan-cam', 'scan-file'].forEach(function (id) {
    var e = $(id); if (e) e.addEventListener('change', function () { handle(this.files[0]); this.value = ''; });
  });
})();
</script>
JS;
    echo str_replace(['__AJAX__', '__NONCE__', '__CUR__'], [wp_json_encode(admin_url('admin-ajax.php')), wp_json_encode(wp_create_nonce('msme_erp_nonce')), wp_json_encode(msme_erp_cur())], $miroJs);
}


// =========================================================
// LT01 — Stock Transfer Order
// =========================================================
elseif ($tcode === 'LT01') {
    $pageTitle = 'Stock Transfer Order'; $pageTcode = 'LT01';
    $breadcrumbs = ['Inventory','Transfer (LT01)'];
    erp_render_page_header();

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
        $d = $_POST;
        try {
            DB::beginTransaction();
            $result = DB::callProcedure('sp_get_next_number', [$companyId, 'STO', date('Y')], ['p_doc_number']);
            $stoNum = $result['p_doc_number'];
            $stoId  = DB::insert('stock_transfers', [
                'company_id'   => $companyId,
                'sto_number'   => $stoNum,
                'transfer_date'=> date('Y-m-d'),
                'from_wh_id'   => (int)$d['from_wh_id'],
                'to_wh_id'     => (int)$d['to_wh_id'],
                'status'       => 'DRAFT',
                'created_by'   => Auth::userId(),
            ]);
            foreach ($d['lines'] ?? [] as $ln) {
                if (empty($ln['item_id'])) continue;
                $qty = (float)$ln['qty'];
                DB::insert('stock_transfer_lines', ['sto_id'=>$stoId,'item_id'=>(int)$ln['item_id'],'uom_id'=>(int)$ln['uom_id'],'qty_transfer'=>$qty]);
                // Post outward from source WH
                DB::callProcedure('sp_post_stock_movement', [
                    $companyId, (int)$ln['item_id'], (int)$d['from_wh_id'],
                    'TRANSFER', '311', 0, $qty, 0,
                    'stock_transfers', $stoId, $stoNum, Auth::userId()
                ], ['p_mvt_id']);
                // Post inward to destination WH
                $avgCost = DB::fetchValue("SELECT avg_cost FROM stock_balances WHERE item_id=? AND warehouse_id=?", [(int)$ln['item_id'], (int)$d['to_wh_id']]);
                DB::callProcedure('sp_post_stock_movement', [
                    $companyId, (int)$ln['item_id'], (int)$d['to_wh_id'],
                    'TRANSFER', '311', $qty, 0, (float)($avgCost ?? 0),
                    'stock_transfers', $stoId, $stoNum, Auth::userId()
                ], ['p_mvt_id']);
            }
            DB::update('stock_transfers', ['status'=>'TRANSFERRED','transferred_at'=>date('Y-m-d H:i:s')], 'id=?', [$stoId]);
            DB::commit();
            $success = "Stock Transfer {$stoNum} completed.";
        } catch (Exception $e) { DB::rollback(); $error = $e->getMessage(); }
        if ($success !== '' && $error === '') erp_post_redirect($success);   // PRG: refresh can't re-post
    }

    $warehouses = DB::fetchAll("SELECT id, code, name FROM warehouses WHERE company_id=? AND is_active=1 ORDER BY code", [$companyId]);
    $items      = DB::fetchAll("SELECT i.id, i.item_code, i.item_name, u.code AS uom, u.id AS uom_id, sb.qty_on_hand FROM items i LEFT JOIN units_of_measure u ON u.id=i.base_uom_id LEFT JOIN stock_balances sb ON sb.item_id=i.id WHERE i.company_id=? AND i.is_active=1 ORDER BY i.item_code", [$companyId]);
    $stHistory  = DB::fetchAll("SELECT st.*, fw.name AS from_wh, tw.name AS to_wh FROM stock_transfers st JOIN warehouses fw ON fw.id=st.from_wh_id JOIN warehouses tw ON tw.id=st.to_wh_id WHERE st.company_id=? ORDER BY st.id DESC LIMIT 20", [$companyId]);

     ?>
    <?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>
    <?php if ($success): ?><div class="alert alert-success small py-2"><?= htmlspecialchars($success) ?></div><?php endif; ?>
    <div class="row g-3">
      <div class="col-md-4">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-primary text-white fw-bold small py-2"><i class="bi bi-arrow-left-right me-2"></i>Transfer Stock — LT01</div>
          <div class="card-body">
            <form method="POST">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
              <div class="mb-2"><label class="form-label fw-semibold small">From Warehouse *</label>
                <select name="from_wh_id" class="form-select form-select-sm" required>
                  <option value="">—</option>
                  <?php foreach ($warehouses as $w): ?><option value="<?= $w['id'] ?>"><?= htmlspecialchars($w['code'].' — '.$w['name']) ?></option><?php endforeach; ?>
                </select>
              </div>
              <div class="mb-3"><label class="form-label fw-semibold small">To Warehouse *</label>
                <select name="to_wh_id" class="form-select form-select-sm" required>
                  <option value="">—</option>
                  <?php foreach ($warehouses as $w): ?><option value="<?= $w['id'] ?>"><?= htmlspecialchars($w['code'].' — '.$w['name']) ?></option><?php endforeach; ?>
                </select>
              </div>
              <div class="fw-semibold small mb-2">Items to Transfer</div>
              <?php foreach (range(0, 2) as $i): ?>
              <div class="row g-1 mb-1 align-items-center">
                <div class="col-7">
                  <select name="lines[<?= $i ?>][item_id]" class="form-select form-select-sm select2">
                    <option value="">— Item —</option>
                    <?php foreach ($items as $it): ?><option value="<?= $it['id'] ?>" data-uomid="<?= $it['uom_id'] ?>"><?= htmlspecialchars($it['item_code'].' – '.$it['item_name']) ?>(<?= number_format($it['qty_on_hand']??0,2) ?>)</option><?php endforeach; ?>
                  </select>
                  <input type="hidden" name="lines[<?= $i ?>][uom_id]" class="sto-uid-<?= $i ?>">
                </div>
                <div class="col-5"><input type="number" name="lines[<?= $i ?>][qty]" class="form-control form-control-sm" value="0" step="0.001" placeholder="Qty"></div>
              </div>
              <?php endforeach; ?>
              <button type="submit" class="btn btn-primary btn-sm mt-2 w-100"><i class="bi bi-send me-1"></i>Execute Transfer</button>
            </form>
          </div>
        </div>
      </div>
      <div class="col-md-8">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white fw-bold small py-2">Transfer History</div>
          <div class="card-body p-0">
            <table class="table table-sm table-hover mb-0 erp-table">
              <thead><tr><th>STO#</th><th>Date</th><th>From</th><th>To</th><th>Status</th></tr></thead>
              <tbody>
                <?php foreach ($stHistory as $st): ?>
                <tr>
                  <td class="fw-bold small"><?= htmlspecialchars($st['sto_number']) ?></td>
                  <td class="small"><?= $st['transfer_date'] ?></td>
                  <td class="small"><?= htmlspecialchars($st['from_wh']) ?></td>
                  <td class="small"><?= htmlspecialchars($st['to_wh']) ?></td>
                  <td><span class="badge bg-<?= $st['status']==='TRANSFERRED'?'success':'secondary' ?>"><?= $st['status'] ?></span></td>
                </tr>
                <?php endforeach; ?>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    <?php
}

// =========================================================
// VK11 — Price List / Condition Records
// =========================================================
elseif ($tcode === 'VK11') {
    $pageTitle = 'Price List / Condition Records'; $pageTcode = 'VK11';
    $breadcrumbs = ['SD','Pricing','Price List (VK11)'];
    erp_render_page_header();

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) { msme_erp_set_flash('Security check failed. Please try again.', 'danger'); wp_redirect(wp_get_referer() ?: admin_url('admin.php?page=msme-erp')); exit; }
        $d = $_POST;
        try {
            if (empty($d['price_list_id'])) {
                $plId = DB::insert('price_lists', ['company_id'=>$companyId,'name'=>$d['name'],'currency'=>DB::fetchValue("SELECT currency_code FROM companies WHERE id=?", [$companyId]) ?: 'INR','valid_from'=>$d['valid_from']??null,'valid_to'=>$d['valid_to']??null,'is_active'=>1]);
            } else {
                $plId = (int)$d['price_list_id'];
            }
            foreach ($d['items'] ?? [] as $itm) {
                if (empty($itm['item_id'])) continue;
                DB::insert('price_list_items', ['price_list_id'=>$plId,'item_id'=>(int)$itm['item_id'],'unit_price'=>(float)$itm['unit_price'],'discount_pct'=>(float)($itm['discount_pct']??0),'valid_from'=>$d['valid_from']??null,'valid_to'=>$d['valid_to']??null]);
            }
            $success = 'Price list saved.';
        } catch (Exception $e) { $error = $e->getMessage(); }
        if ($success !== '' && $error === '') erp_post_redirect($success);   // PRG: refresh can't re-post
    }

    $priceLists = DB::fetchAll("SELECT pl.*, COUNT(pli.id) AS item_count FROM price_lists pl LEFT JOIN price_list_items pli ON pli.price_list_id=pl.id WHERE pl.company_id=? GROUP BY pl.id ORDER BY pl.id DESC", [$companyId]);
    $items      = DB::fetchAll("SELECT id, item_code, item_name, moving_avg_price FROM items WHERE company_id=? AND is_active=1 ORDER BY item_code", [$companyId]);
     ?>
    <?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>
    <?php if ($success): ?><div class="alert alert-success small py-2"><?= htmlspecialchars($success) ?></div><?php endif; ?>
    <div class="row g-3">
      <div class="col-md-5">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-success text-white fw-bold small py-2"><i class="bi bi-tags me-2"></i>Create Price List — VK11</div>
          <div class="card-body">
            <form method="POST">
              <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
              <div class="mb-2"><label class="form-label fw-semibold small">Price List Name *</label>
                <input type="text" name="name" class="form-control form-control-sm" required placeholder="e.g. Standard 2024"></div>
              <div class="row g-2 mb-3">
                <div class="col-6"><label class="form-label small mb-0">Valid From</label><input type="date" name="valid_from" class="form-control form-control-sm" value="<?= date('Y-01-01') ?>"></div>
                <div class="col-6"><label class="form-label small mb-0">Valid To</label><input type="date" name="valid_to" class="form-control form-control-sm" value="<?= date('Y-12-31') ?>"></div>
              </div>
              <div class="fw-semibold small mb-2">Item Prices</div>
              <?php foreach ($items as $ai => $it): ?>
              <div class="row g-1 mb-1 align-items-center" style="font-size:.78rem">
                <input type="hidden" name="items[<?= $ai ?>][item_id]" value="<?= $it['id'] ?>">
                <div class="col-5 text-truncate" title="<?= htmlspecialchars($it['item_name']) ?>"><?= htmlspecialchars($it['item_code']) ?></div>
                <div class="col-4"><input type="number" name="items[<?= $ai ?>][unit_price]" class="form-control form-control-sm" value="<?= $it['moving_avg_price'] ?>" step="0.01" placeholder="Price"></div>
                <div class="col-3"><input type="number" name="items[<?= $ai ?>][discount_pct]" class="form-control form-control-sm" value="0" step="0.01" placeholder="Disc%"></div>
              </div>
              <?php endforeach; ?>
              <button type="submit" class="btn btn-success btn-sm mt-2 w-100"><i class="bi bi-save me-1"></i>Save Price List</button>
            </form>
          </div>
        </div>
      </div>
      <div class="col-md-7">
        <div class="card border-0 shadow-sm">
          <div class="card-header bg-white fw-bold small py-2">Existing Price Lists</div>
          <div class="card-body p-0">
            <table class="table table-sm table-hover mb-0 erp-table">
              <thead><tr><th>Name</th><th>Currency</th><th>Valid From</th><th>Valid To</th><th>Items</th><th>Active</th></tr></thead>
              <tbody>
                <?php foreach ($priceLists as $pl): ?>
                <tr>
                  <td class="fw-bold small"><?= htmlspecialchars($pl['name']) ?></td>
                  <td class="small"><?= $pl['currency'] ?></td>
                  <td class="small"><?= $pl['valid_from'] ?? '—' ?></td>
                  <td class="small"><?= $pl['valid_to'] ?? '—' ?></td>
                  <td class="small text-center"><?= $pl['item_count'] ?></td>
                  <td><?= $pl['is_active'] ? '<i class="bi bi-check-circle-fill text-success"></i>' : '' ?></td>
                </tr>
                <?php endforeach; if (!$priceLists): ?><tr><td colspan="6" class="text-center text-muted py-3">No price lists yet.</td></tr><?php endif; ?>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    <?php
}

// Generic fallback for mapped but not fully implemented tcodes
else {
    $pageTitle = "T-Code: {$tcode}"; $pageTcode = $tcode;
    $breadcrumbs = ['ERP', $tcode];

erp_render_page_header();
     ?>
    <div class="alert alert-info">
      <i class="bi bi-info-circle me-2"></i>
      T-Code <strong><?= htmlspecialchars($tcode) ?></strong> is registered and authorized. Full UI is available — use related modules via the sidebar.
    </div>
    <?php
}


?>
