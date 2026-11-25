<?php
/**
 * MSME ERP — Compare Quotations (RFQ list + comparison grid)
 * T-Code: ME47
 * Developed by: Prigag Online Pvt. Ltd.
 */

$tcode = 'ME47';
Auth::requireTcode('ME47');

$companyId = Auth::companyId();
$id        = (int)($_GET['id'] ?? 0);
$error     = '';

// ============================================================
// DETAIL / COMPARISON VIEW
// ============================================================
if ($id) {
    $rfq = DB::fetchOne(
        "SELECT r.*, pr.pr_number FROM rfqs r JOIN purchase_requisitions pr ON pr.id=r.pr_id
         WHERE r.id=? AND r.company_id=?", [$id, $companyId]
    );
    if (!$rfq) { wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47')); exit; }

    // ---- v1.21.0: MANUAL CUT-OFF. "Close Quotations & Evaluate" ends the RFQ now: open negotiations
    // are closed, the AI comparison runs, the best supplier is selected and ONE Purchase Order is
    // created and sent to the releasing authority (the supplier is emailed after final release).
    // Same routine the scheduler runs at the auto-compare time / RFQ due date.
    // "retry_award" re-runs it for an RFQ whose automatic evaluation stopped on an error.
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && in_array($_POST['ai_action'] ?? '', ['finalize_quotes', 'retry_award'], true)) {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            $error = 'Security check failed. Please try again.';
        } elseif (!Auth::checkTcode('ME21N')) {
            $error = 'Closing the RFQ places the order, so T-Code ME21N (create Purchase Orders) is required.';
        } else {
            if (($_POST['ai_action'] ?? '') === 'retry_award' && in_array($rfq['auto_award_status'] ?? '', ['PO_FAILED', 'ERROR'], true)) {
                DB::query("UPDATE rfqs SET auto_award_status = NULL, auto_award_trigger = NULL WHERE id=?", [$id]);
            }
            $res = msme_erp_rfq_run_evaluation($id, 'MANUAL', Auth::userId());
            ErpDB::useCompany(null);
            msme_erp_set_flash($res['msg'], $res['ok'] ? 'success' : 'danger');
            wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $id)); exit;
        }
    }

    // ---- v1.20.8: (re)send the RFQ link to one invited supplier, with an editable address ----
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['rfq_action'] ?? '') === 'send_invite') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            $error = 'Security check failed. Please try again.';
        } else {
            $rsId  = (int)($_POST['rfq_supplier_id'] ?? 0);
            $rsRow = $rsId ? DB::fetchOne("SELECT * FROM rfq_suppliers WHERE id=? AND rfq_id=?", [$rsId, $id]) : null;
            $email = sanitize_email(trim((string)($_POST['email_to'] ?? '')));
            if (!$rsRow)                          $error = 'Supplier not found on this RFQ.';
            elseif (!$email || !is_email($email)) $error = 'Enter a valid email address to send to.';
            else {
                $r = msme_erp_rfq_email($rfq['rfq_number'], $email, $rsRow['token'], (string)($rfq['due_date'] ?? ''), (string)($rfq['notes'] ?? ''));
                DB::update('rfq_suppliers', ['email_to' => $email, 'sent_at' => current_time('mysql')], 'id=?', [$rsId]);
                Auth::audit('rfq_suppliers', $rsId, 'UPDATE', ['email_to' => $rsRow['email_to']], ['email_to' => $email, 'resent' => $r['ok'] ? 1 : 0]);
                msme_erp_set_flash($r['ok'] ? ('RFQ link emailed to ' . $email . '.') : ('Email to ' . $email . ' was NOT sent: ' . $r['error'] . '. Use "Copy link" or WhatsApp below to share it manually.'), $r['ok'] ? 'success' : 'warning');
                wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $id)); exit;
            }
        }
    }

    // ---- Negotiation record-keeping (ME47 "Negotiation" tab + Final Discount) ----
    //   set_final   : buyer records the FINAL agreed discount (as % off, or as a
    //                 final net amount) for one supplier
    //   clear_final : removes that final discount again
    //   log_round   : manual entry for a negotiation that happened off-system
    //                 (phone / email / meeting) so the whole trail lives in one place
    // Every action also appends a row to rfq_negotiations, so the Negotiation tab
    // is a complete audit trail. None of this touches the automated counter-offer
    // engine (negotiation_status / round / discount_pct) or any Purchase Order.
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && in_array($_POST['nego_action'] ?? '', ['set_final', 'clear_final', 'log_round'], true)) {
        $negoAction = $_POST['nego_action'];
        $rsId  = (int)($_POST['rfq_supplier_id'] ?? 0);
        $rsRow = $rsId ? DB::fetchOne("SELECT * FROM rfq_suppliers WHERE id=? AND rfq_id=?", [$rsId, $id]) : null;
        $channelChoices = ['PHONE', 'EMAIL', 'MEETING', 'PORTAL', 'OTHER'];
        $channel = in_array($_POST['channel'] ?? '', $channelChoices, true) ? $_POST['channel'] : null;
        $remarks = mb_substr(sanitize_text_field($_POST['remarks'] ?? ''), 0, 255);

        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            $error = 'Security check failed. Please try again.';
        } elseif (!$rsRow) {
            $error = 'Supplier not found on this RFQ.';
        } else {
            $quoted   = (float)$rsRow['total_quoted'];
            $maxRound = (int)DB::fetchValue("SELECT COALESCE(MAX(round_no),0) FROM rfq_negotiations WHERE rfq_supplier_id=?", [$rsId]);
            $roundNo  = max(1, $maxRound);
            $flash    = '';

            if ($negoAction === 'set_final') {
                $mode = ($_POST['final_mode'] ?? 'PERCENT') === 'NET' ? 'NET' : 'PERCENT';
                $val  = (float)($_POST['final_value'] ?? 0);
                if ($rsRow['status'] !== 'SUBMITTED' || $quoted <= 0) {
                    $error = 'This supplier has not submitted a quotation yet, so there is nothing to discount.';
                } elseif ($mode === 'PERCENT' && ($val < 0 || $val >= 100)) {
                    $error = 'Final discount % must be between 0 and 99.99.';
                } elseif ($mode === 'NET' && ($val <= 0 || $val > $quoted)) {
                    $error = 'Final net amount must be more than 0 and not above the total quoted.';
                } else {
                    $pct = $mode === 'PERCENT' ? round($val, 2) : round((1 - $val / $quoted) * 100, 2);
                    $net = $mode === 'PERCENT' ? round($quoted * (1 - $val / 100), 2) : round($val, 2);
                    DB::update('rfq_suppliers', [
                        'final_discount_pct' => $pct, 'final_amount' => $net, 'final_remarks' => $remarks,
                        'final_set_by' => Auth::userId(), 'final_set_at' => current_time('mysql'),
                    ], 'id=?', [$rsId]);
                    DB::insert('rfq_negotiations', [
                        'rfq_supplier_id' => $rsId, 'round_no' => $roundNo, 'actor' => 'BUYER', 'action_type' => 'FINAL',
                        'discount_pct' => $pct, 'total_amount' => $net, 'channel' => $channel, 'remarks' => $remarks,
                        'created_by' => Auth::userId(),
                    ]);
                    Auth::audit('rfq_suppliers', $rsId, 'UPDATE', ['final_discount_pct' => $rsRow['final_discount_pct'] ?? null], ['final_discount_pct' => $pct, 'final_amount' => $net]);
                    $flash = 'Final discount saved: ' . number_format($pct, 2) . '% off (net INR ' . number_format($net, 2) . ').';
                }
            } elseif ($negoAction === 'clear_final') {
                // DB::update() can't write NULL, so clear with a direct query.
                $wdb = DB::wpdb();
                $wdb->query($wdb->prepare(
                    "UPDATE " . DB::tbl('rfq_suppliers') . " SET final_discount_pct=NULL, final_amount=NULL, final_remarks=NULL, final_set_by=NULL, final_set_at=NULL WHERE id=%d AND rfq_id=%d",
                    $rsId, $id
                ));
                DB::insert('rfq_negotiations', [
                    'rfq_supplier_id' => $rsId, 'round_no' => $roundNo, 'actor' => 'BUYER', 'action_type' => 'FINAL_CLEARED',
                    'discount_pct' => 0, 'total_amount' => 0, 'remarks' => $remarks ?: 'Final discount cleared', 'created_by' => Auth::userId(),
                ]);
                Auth::audit('rfq_suppliers', $rsId, 'UPDATE', ['final_discount_pct' => $rsRow['final_discount_pct'] ?? null], ['final_discount_pct' => null]);
                $flash = 'Final discount cleared.';
            } else { // log_round
                $actor = in_array($_POST['actor'] ?? '', ['BUYER', 'SUPPLIER'], true) ? $_POST['actor'] : 'BUYER';
                $type  = in_array($_POST['action_type'] ?? '', ['OFFER', 'COUNTER', 'NOTE'], true) ? $_POST['action_type'] : 'NOTE';
                $pct   = ($_POST['discount_pct'] ?? '') !== '' ? (float)$_POST['discount_pct'] : 0.0;
                $amt   = ($_POST['total_amount'] ?? '') !== '' ? (float)$_POST['total_amount'] : 0.0;
                if ($pct < 0 || $pct >= 100 || $amt < 0) {
                    $error = 'Discount % must be between 0 and 99.99 and the amount cannot be negative.';
                } elseif ($remarks === '' && $pct == 0 && $amt == 0) {
                    $error = 'Enter a discount, an amount or a remark to log.';
                } else {
                    DB::insert('rfq_negotiations', [
                        'rfq_supplier_id' => $rsId, 'round_no' => $roundNo, 'actor' => $actor, 'action_type' => $type,
                        'discount_pct' => $pct, 'total_amount' => $amt, 'channel' => $channel, 'remarks' => $remarks,
                        'created_by' => Auth::userId(),
                    ]);
                    $flash = 'Negotiation entry logged.';
                }
            }

            if ($flash) {
                msme_erp_set_flash($flash, 'success');
                wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $id . '&tab=nego')); exit;
            }
        }
    }

    // ---- AI comparison (Google Gemini) ----
    // v1.21.0: the prompt/Gemini call lives in msme_erp_rfq_ai_compare() (core/helpers.php) so this
    // button and the automatic cut-off evaluation use exactly the same comparison.
    // Regenerating only refreshes the written summary; it never changes an order already placed.
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['ai_action'] ?? '') === 'generate') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            $error = 'Security check failed. Please try again.';
        } else {
            $anyInProgress = (int)DB::fetchValue("SELECT COUNT(*) FROM rfq_suppliers WHERE rfq_id=? AND status='SUBMITTED' AND negotiation_status='IN_PROGRESS'", [$id]) > 0;
            if ($anyInProgress && !$rfq['quotes_finalized_at']) {
                $error = 'One or more suppliers are still in active price negotiation. Wait for the cut-off, or click "Close Quotations & Evaluate".';
            } else {
                $ai = msme_erp_rfq_ai_compare($id);
                if (!$ai['ok']) {
                    $error = $ai['error'];
                } else {
                    DB::update('rfqs', ['ai_summary' => $ai['text'], 'ai_summary_at' => current_time('mysql')], 'id=?', [$id]);
                    msme_erp_set_flash('AI comparison generated.', 'success');
                    wp_redirect(admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $id)); exit;
                }
            }
        }
    }

    $lines = DB::fetchAll(
        "SELECT rl.*, i.item_code, i.item_name, u.code AS uom_code
         FROM rfq_lines rl LEFT JOIN items i ON i.id=rl.item_id LEFT JOIN units_of_measure u ON u.id=rl.uom_id
         WHERE rl.rfq_id=? ORDER BY rl.line_num", [$id]
    );
    $subs = DB::fetchAll(
        "SELECT rs.*, s.name AS supplier_name, s.supplier_code, s.phone AS supplier_phone, s.email AS supplier_master_email FROM rfq_suppliers rs
         JOIN suppliers s ON s.id=rs.supplier_id WHERE rs.rfq_id=? ORDER BY rs.id", [$id]
    );
    // Build a [supplier_id][rfq_line_id] => quote row matrix
    $matrix = [];
    foreach ($subs as $s) {
        $qls = DB::fetchAll("SELECT * FROM rfq_quote_lines WHERE rfq_supplier_id=?", [$s['id']]);
        foreach ($qls as $ql) $matrix[$s['id']][$ql['rfq_line_id']] = $ql;
    }
    // Lowest price per line, for highlighting
    $lowestPerLine = [];
    foreach ($lines as $l) {
        $min = null;
        foreach ($subs as $s) {
            $p = $matrix[$s['id']][$l['id']]['unit_price'] ?? null;
            if ($p !== null && ($min === null || (float)$p < $min)) $min = (float)$p;
        }
        $lowestPerLine[$l['id']] = $min;
    }
    $submittedTotals = array_filter(array_column($subs, 'total_quoted'), fn($v, $k) => $subs[$k]['status'] === 'SUBMITTED', ARRAY_FILTER_USE_BOTH);
    $lowestTotal = $submittedTotals ? min($submittedTotals) : null;
    $subsBySupplierId = array_column($subs, null, 'supplier_id');   // keyed by the REAL suppliers.id — for anything written to purchase_orders.supplier_id
    $submittedSubs = array_filter($subs, fn($s) => $s['status'] === 'SUBMITTED');

    // Effective discount for a supplier: the buyer's FINAL discount if one has
    // been set, otherwise the accepted automated negotiation, otherwise none.
    // Returns ['pct'=>float,'net'=>float|null,'source'=>'FINAL'|'NEGOTIATION'|null].
    $effectiveDiscount = function (array $s): array {
        if ($s['status'] !== 'SUBMITTED') return ['pct' => 0.0, 'net' => null, 'source' => null];
        if (!empty($s['final_set_at']) && $s['final_discount_pct'] !== null) {
            return ['pct' => (float)$s['final_discount_pct'], 'net' => (float)$s['final_amount'], 'source' => 'FINAL'];
        }
        if (($s['negotiation_status'] ?? 'NONE') === 'ACCEPTED') {
            $pct = (float)$s['negotiation_discount_pct'];
            return ['pct' => $pct, 'net' => round((float)$s['total_quoted'] * (1 - $pct / 100), 2), 'source' => 'NEGOTIATION'];
        }
        return ['pct' => 0.0, 'net' => null, 'source' => null];
    };

    // v1.20.8: what to SHOW for every supplier in the comparison. effectiveDiscount() above only knows a FINAL
    // discount or an ACCEPTED negotiation (it also drives PO pricing and the AI prompt, so it is left alone); a
    // supplier who was merely negotiated with showed "--". This closure always gives a last discount / price:
    //   FINAL  -> buyer's final discount      ACCEPTED -> accepted negotiation
    //   LAST OFFER -> newest logged round with a discount or amount (any status)
    //   QUOTED -> nothing negotiated: the supplier's own quoted total stands (0% off)
    $lastNegoBySub = [];
    if ($subs) {
        $__ids = array_map('intval', array_column($subs, 'id'));
        foreach (DB::fetchAll("SELECT * FROM rfq_negotiations WHERE rfq_supplier_id IN (" . implode(',', array_fill(0, count($__ids), '?')) . ") ORDER BY id ASC", $__ids) as $__n) {
            if ((float)$__n['discount_pct'] > 0 || (float)$__n['total_amount'] > 0) $lastNegoBySub[$__n['rfq_supplier_id']] = $__n;   // ascending => last write wins
        }
    }
    $lastTerms = function (array $s) use ($effectiveDiscount, $lastNegoBySub): array {
        if ($s['status'] !== 'SUBMITTED') return ['pct' => null, 'net' => null, 'basis' => null, 'note' => ''];
        $q  = (float)$s['total_quoted'];
        $ed = $effectiveDiscount($s);
        if ($ed['source'] === 'FINAL')       return ['pct' => $ed['pct'], 'net' => (float)$ed['net'], 'basis' => 'FINAL', 'note' => 'buyer\'s final discount'];
        if ($ed['source'] === 'NEGOTIATION') return ['pct' => $ed['pct'], 'net' => (float)$ed['net'], 'basis' => 'ACCEPTED', 'note' => 'accepted in negotiation'];
        $n = $lastNegoBySub[$s['id']] ?? null;
        if ($n && $q > 0) {
            $amt = (float)$n['total_amount']; $pct = (float)$n['discount_pct'];
            $net = $amt > 0 ? $amt : round($q * (1 - $pct / 100), 2);
            $pct = $pct > 0 ? $pct : max(0.0, round((1 - $net / $q) * 100, 2));
            return ['pct' => $pct, 'net' => $net, 'basis' => 'LAST OFFER', 'note' => 'round ' . (int)$n['round_no'] . ' &middot; ' . esc_html(strtolower($n['actor'])) . ' ' . esc_html(strtolower($n['action_type']))];
        }
        return ['pct' => 0.0, 'net' => $q, 'basis' => 'QUOTED', 'note' => 'no discount negotiated'];
    };
    $lowestNet = null;
    foreach ($subs as $__s) { $__t = $lastTerms($__s); if ($__t['net'] !== null && $__t['net'] > 0 && ($lowestNet === null || $__t['net'] < $lowestNet)) $lowestNet = $__t['net']; }
    $basisBadge = ['FINAL' => 'success', 'ACCEPTED' => 'success', 'LAST OFFER' => 'primary', 'QUOTED' => 'secondary'];

    // Which RFQ lines already have a Purchase Order line raised against them?
    // Keyed by pr_line_id (every RFQ line carries the PR line it came from —
    // see rfq_create.php — and that's the same link PO creation writes to
    // purchase_order_lines.pr_line_id), so this is true regardless of
    // whether the PO was made via this auto-award button or the manual
    // ME21N form / PR conversion.
    $orderedPrLineIds = [];
    $linkedPOsByPoId  = [];
    $rfqPrLineIds = array_filter(array_column($lines, 'pr_line_id'));
    if ($rfqPrLineIds) {
        $poLineRows = DB::fetchAll(
            "SELECT pol.pr_line_id, po.id AS po_id, po.po_number, po.grand_total, po.status, s.name AS supplier_name
             FROM purchase_order_lines pol
             JOIN purchase_orders po ON po.id = pol.po_id
             JOIN suppliers s ON s.id = po.supplier_id
             WHERE pol.pr_line_id IN (" . implode(',', array_fill(0, count($rfqPrLineIds), '?')) . ")",
            array_values($rfqPrLineIds)
        );
        foreach ($poLineRows as $r) {
            $orderedPrLineIds[$r['pr_line_id']] = true;
            $linkedPOsByPoId[$r['po_id']] = $r; // one row per PO for display, de-duplicated
        }
    }
    $linkedPOs  = array_values($linkedPOsByPoId);
    $isLineOrdered = fn($l) => $l['pr_line_id'] && isset($orderedPrLineIds[$l['pr_line_id']]);
    $openLines  = array_values(array_filter($lines, fn($l) => !$isLineOrdered($l)));

    // ============================================================
    // AUTO-CREATE PURCHASE ORDER(S) DIRECTLY FROM THE COMPARISON —
    // no manual PO form, no human line-by-line entry. Each STILL-OPEN
    // line (one with no Purchase Order raised against it yet — see
    // $orderedPrLineIds above) is awarded to whichever SUBMITTED
    // supplier quoted the lowest unit price for it (same rule already
    // used to highlight "lowest" cells above), mirroring the split-award
    // logic the AI summary already calls out. If different suppliers win
    // different lines, one PO is created per winning supplier. A line
    // that already has a PO is never re-ordered; a line with no submitted
    // quote at all is skipped and reported — it can't be auto-ordered
    // without a price.
    // ============================================================
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['po_action'] ?? '') === 'create_po') {
        if (!Auth::validateCsrf($_POST['_csrf'] ?? '')) {
            $error = 'Security check failed. Please try again.';
        } elseif (!Auth::checkTcode('ME21N')) {
            $error = 'You are not authorized to create Purchase Orders (T-Code ME21N is required).';
        } elseif (!$openLines) {
            $error = 'Every item on this RFQ already has a Purchase Order — nothing left to award.';
        } else {
            $award = [];        // REAL suppliers.id => [ ['line'=>rfq_line row, 'quote'=>quote_line row], ... ]
            $unquotedLines = [];
            foreach ($openLines as $l) {
                $bestSupplierId = null; $bestPrice = null; $bestQuote = null;
                foreach ($subs as $s) {
                    if ($s['status'] !== 'SUBMITTED') continue;
                    $ql = $matrix[$s['id']][$l['id']] ?? null; // $matrix is keyed by rfq_suppliers.id
                    if (!$ql) continue;
                    $p = (float)$ql['unit_price'];
                    if ($bestPrice === null || $p < $bestPrice) {
                        // Award against the REAL supplier id (rs.supplier_id), never
                        // rs.id (the rfq_suppliers row id) — purchase_orders.supplier_id
                        // is a foreign key into the suppliers table, not rfq_suppliers.
                        $bestPrice = $p; $bestSupplierId = $s['supplier_id']; $bestQuote = $ql;
                    }
                }
                if ($bestSupplierId === null) { $unquotedLines[] = $l; continue; }
                $award[$bestSupplierId][] = ['line' => $l, 'quote' => $bestQuote];
            }

            if (!$award) {
                $error = 'No submitted quotations for the remaining item(s) — nothing to order.';
            } else {
                try {
                    DB::beginTransaction();

                    $companyCurrency = DB::fetchValue("SELECT currency_code FROM companies WHERE id=?", [$companyId]) ?: 'INR';
                    $createdPos = []; // po_number => po_id
                    $convertedPrIds = [];

                    foreach ($award as $supplierId => $awardedLines) {
                        // Guard against a duplicate submit racing in between the
                        // page load and this click: re-check right before insert
                        // that none of these lines picked up a PO in the meantime.
                        $awardedLines = array_values(array_filter(
                            $awardedLines,
                            fn($al) => !($al['line']['pr_line_id'] && DB::fetchValue(
                                "SELECT id FROM purchase_order_lines WHERE pr_line_id=?", [$al['line']['pr_line_id']]
                            ))
                        ));
                        if (!$awardedLines) continue;

                        $result   = DB::callProcedure('sp_get_next_number', [$companyId, 'PO', (int)date('Y')], ['p_doc_number']);
                        $poNumber = $result['p_doc_number'];

                        // Delivery date: earliest PO date + the longest delivery_days
                        // quoted among this supplier's awarded lines (if given).
                        $maxDeliveryDays = null;
                        foreach ($awardedLines as $al) {
                            $d = $al['quote']['delivery_days'] ?? null;
                            if ($d !== null && ($maxDeliveryDays === null || (int)$d > $maxDeliveryDays)) $maxDeliveryDays = (int)$d;
                        }
                        $deliveryDate = $maxDeliveryDays !== null ? date('Y-m-d', strtotime("+{$maxDeliveryDays} days")) : null;

                        $poId = DB::insert('purchase_orders', [
                            'company_id'     => $companyId,
                            'branch_id'      => Auth::branchId(),
                            'po_number'      => $poNumber,
                            'po_date'        => date('Y-m-d'),
                            'supplier_id'    => $supplierId,
                            'currency_code'  => $companyCurrency,
                            'payment_terms'  => $subsBySupplierId[$supplierId]['payment_terms'] ?? '',
                            'delivery_date'  => $deliveryDate,
                            'status'         => 'DRAFT',
                            'release_status' => 'NOT_RELEASED',
                            'remarks'        => 'Auto-created from RFQ ' . $rfq['rfq_number'] . ' (Quotation Comparison — lowest price per line).',
                            'created_by'     => Auth::userId(),
                        ]);

                        $taxableTotal = 0.0; $gstTotal = 0.0; $lineNum = 10;
                        foreach ($awardedLines as $al) {
                            $l  = $al['line'];
                            $ql = $al['quote'];
                            $gstRate = (float) DB::fetchValue("SELECT gst_rate FROM items WHERE id=?", [$l['item_id']]);
                            $qty   = (float)$l['qty'];
                            $price = (float)$ql['unit_price'];
                            $base  = $qty * $price;
                            $gstAmt = $base * $gstRate / 100;

                            DB::insert('purchase_order_lines', [
                                'po_id'         => $poId,
                                'line_num'      => $lineNum,
                                'pr_line_id'    => $l['pr_line_id'],
                                'item_id'       => $l['item_id'],
                                'uom_id'        => $l['uom_id'],
                                'qty_ordered'   => $qty,
                                'unit_price'    => $price,
                                'discount_pct'  => 0,
                                'gst_rate'      => $gstRate,
                                'line_total'    => round($base, 2),
                                'delivery_date' => $deliveryDate,
                                'status'        => 'OPEN',
                            ]);
                            $lineNum += 10;
                            $taxableTotal += $base;
                            $gstTotal     += $gstAmt;

                            if ($l['pr_line_id']) {
                                DB::update('purchase_requisition_lines', ['status' => 'PO_CREATED'], 'id=?', [$l['pr_line_id']]);
                                $prIdForLine = DB::fetchValue("SELECT pr_id FROM purchase_requisition_lines WHERE id=?", [$l['pr_line_id']]);
                                if ($prIdForLine) $convertedPrIds[$prIdForLine] = true;
                            }
                        }

                        $taxableTotal = round($taxableTotal, 2);
                        $gstTotal     = round($gstTotal, 2);
                        DB::update('purchase_orders', [
                            'total_amount' => $taxableTotal,
                            'tax_amount'   => $gstTotal,
                            'grand_total'  => round($taxableTotal + $gstTotal, 2),
                        ], 'id=?', [$poId]);

                        Auth::audit('purchase_orders', $poId, 'INSERT', [], ['po_number' => $poNumber, 'auto_from_rfq' => $rfq['rfq_number']]);

                        // Auto-trigger release if configured — same as the manual ME21N flow.
                        $strategy = DB::fetchOne(
                            "SELECT id FROM release_strategies WHERE company_id=? AND doc_type='purchase_order' AND is_active=1 LIMIT 1",
                            [$companyId]
                        );
                        if ($strategy) {
                            $poTotal   = DB::fetchValue("SELECT grand_total FROM purchase_orders WHERE id=?", [$poId]);
                            $releaseId = DB::insert('document_releases', [
                                'company_id'    => $companyId,
                                'doc_type'      => 'purchase_order',
                                'doc_id'        => $poId,
                                'doc_number'    => $poNumber,
                                'doc_amount'    => $poTotal,
                                'strategy_id'   => $strategy['id'],
                                'current_level' => 1,
                                'status'        => 'PENDING',
                                'requested_by'  => Auth::userId(),
                            ]);
                            DB::update('purchase_orders', ['release_id' => $releaseId], 'id=?', [$poId]);
                        }

                        $createdPos[$poNumber] = $poId;
                    }

                    if (!$createdPos) {
                        DB::rollback();
                        $error = 'These item(s) already picked up a Purchase Order just now — nothing left to award. Refresh to see it.';
                    } else {
                        // A PR header only flips to PO_CREATED once every one of its lines is ordered.
                        foreach (array_keys($convertedPrIds) as $prIdDone) {
                            $stillOpen = DB::fetchValue("SELECT COUNT(*) FROM purchase_requisition_lines WHERE pr_id=? AND status='OPEN'", [$prIdDone]);
                            if ((int)$stillOpen === 0) {
                                DB::update('purchase_requisitions', ['status' => 'PO_CREATED'], 'id=?', [$prIdDone]);
                            }
                        }

                        // Mark the RFQ PO_CREATED only once EVERY line has a PO —
                        // a partial award (some items still open) is flagged
                        // PARTIALLY_ORDERED so the button correctly reappears
                        // for the remaining items.
                        $anyStillOpen = false;
                        foreach ($lines as $l) {
                            if ($l['pr_line_id']) {
                                $has = DB::fetchValue("SELECT id FROM purchase_order_lines WHERE pr_line_id=?", [$l['pr_line_id']]);
                                if (!$has) { $anyStillOpen = true; break; }
                            } else {
                                $anyStillOpen = true; break;
                            }
                        }
                        DB::update('rfqs', ['status' => $anyStillOpen ? 'PARTIALLY_ORDERED' : 'PO_CREATED'], 'id=?', [$id]);
                        Auth::audit('rfqs', $id, 'UPDATE', ['status' => $rfq['status']], ['status' => $anyStillOpen ? 'PARTIALLY_ORDERED' : 'PO_CREATED']);

                        DB::commit();

                        $poNumbers = array_keys($createdPos);
                        $msg = count($poNumbers) > 1
                            ? 'Purchase Orders created automatically (split award across suppliers): ' . implode(', ', $poNumbers) . '.'
                            : 'Purchase Order ' . $poNumbers[0] . ' created automatically.';
                        if ($unquotedLines) {
                            $msg .= ' ' . count($unquotedLines) . ' remaining line item(s) had no submitted quote and were skipped — order those separately.';
                        }
                        msme_erp_set_flash($msg, 'success');

                        // Land on the PO if only one was created, otherwise back on this RFQ.
                        $redirectTo = count($createdPos) === 1
                            ? admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . reset($createdPos))
                            : admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $id);
                        wp_redirect($redirectTo); exit;
                    }

                } catch (Exception $e) {
                    DB::rollback();
                    $error = 'Error creating PO(s): ' . $e->getMessage();
                }
            }
        }
    }

    $pageTitle   = 'RFQ: ' . $rfq['rfq_number'];
    $pageTcode   = 'ME47';
    $breadcrumbs = ['MM', 'Purchasing', 'RFQ: ' . $rfq['rfq_number']];
    $pageActions = '<div class="d-flex gap-2">
        <a href="' . admin_url('admin.php?page=msme-erp&tcode=ME41') . '" class="btn btn-sm btn-primary"><i class="bi bi-envelope-paper me-1"></i>New RFQ</a>
        <a href="' . admin_url('admin.php?page=msme-erp&tcode=ME47') . '" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-left me-1"></i>Back to List</a>
      </div>';
    erp_render_page_header();
    $statusColors = ['SENT'=>'secondary','VIEWED'=>'info','SUBMITTED'=>'success'];
    ?>
    <?php if ($error): ?><div class="alert alert-danger small py-2"><?= htmlspecialchars($error) ?></div><?php endif; ?>

    <div class="card border-0 shadow-sm mb-3">
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-3"><span class="text-muted small">Source PR:</span> <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME53N&id=' . $rfq['pr_id']) ?>" class="fw-bold"><?= htmlspecialchars($rfq['pr_number']) ?></a></div>
          <div class="col-md-3"><span class="text-muted small">RFQ Date:</span> <span class="fw-bold"><?= $rfq['rfq_date'] ?></span></div>
          <div class="col-md-3"><span class="text-muted small">Due Date:</span> <span class="fw-bold"><?= $rfq['due_date'] ?? '—' ?></span></div>
          <div class="col-md-3"><span class="text-muted small">Status:</span> <span class="badge bg-<?= $rfq['status']==='OPEN'?'warning':'secondary' ?>"><?= $rfq['status'] ?></span></div>
          <?php
            $aas = (string)($rfq['auto_award_status'] ?? '');
            $autoSettings = msme_erp_rfq_auto_settings();
            $trigLabel = ['AUTO_TIME' => 'auto-compare time', 'AUTO_CUTOFF' => 'RFQ cut-off date', 'MANUAL' => 'manual cut-off'];
            $awSupplier = null; $awPo = null;
            if (!empty($rfq['auto_award_supplier_id'])) $awSupplier = DB::fetchOne("SELECT s.name FROM rfq_suppliers rs JOIN suppliers s ON s.id=rs.supplier_id WHERE rs.id=?", [(int)$rfq['auto_award_supplier_id']]);
            if (!empty($rfq['auto_award_po_id']))       $awPo       = DB::fetchOne("SELECT id, po_number, status, release_status FROM purchase_orders WHERE id=?", [(int)$rfq['auto_award_po_id']]);
          ?>
          <div class="col-12">
            <span class="text-muted small">Cut-off &amp; auto-order:</span>
            <?php if ($aas === 'DONE'): ?>
              <span class="badge bg-success">EVALUATED</span>
              <span class="small">on <?= esc_html($rfq['auto_award_at']) ?> (<?= esc_html($trigLabel[$rfq['auto_award_trigger']] ?? $rfq['auto_award_trigger']) ?>) &rarr; <strong><?= esc_html($awSupplier['name'] ?? '—') ?></strong> selected<?php if ($awPo): ?>, PO <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . (int)$awPo['id']) ?>" class="fw-bold"><?= esc_html($awPo['po_number']) ?></a> &mdash; <?= esc_html($awPo['release_status'] === 'RELEASED' ? 'released and sent to supplier' : 'awaiting release by the releasing authority') ?><?php endif; ?></span>
            <?php elseif ($aas === 'RUNNING'): ?>
              <span class="badge bg-info">EVALUATING</span> <span class="small">started <?= esc_html($rfq['auto_award_at']) ?> &mdash; refresh in a moment.</span>
            <?php elseif ($aas === 'PO_FAILED' || $aas === 'ERROR'): ?>
              <span class="badge bg-danger"><?= esc_html($aas) ?></span> <span class="small">The automatic evaluation stopped (see the comparison below). Use <em>Retry evaluation &amp; PO</em>, or order manually.</span>
            <?php elseif ($autoSettings['enabled']): ?>
              <span class="badge bg-warning text-dark">PENDING</span>
              <span class="small">The AI comparison runs and the PO is raised automatically
                <?php $__w = []; if (!empty($rfq['auto_compare_at'])) $__w[] = 'at <strong>' . esc_html($rfq['auto_compare_at']) . '</strong>'; if (!empty($rfq['due_date'])) $__w[] = 'after the cut-off date <strong>' . esc_html($rfq['due_date']) . '</strong>'; ?>
                <?= $__w ? implode(' or ', $__w) : 'when you click <em>Close Quotations &amp; Evaluate</em> (no cut-off date or auto-compare time was set)' ?>.
                A supplier accepting an offer does not place the order.</span>
            <?php else: ?>
              <span class="badge bg-secondary">OFF</span> <span class="small">Automatic evaluation is switched off in Settings.</span>
            <?php endif; ?>
          </div>
          <?php if ($rfq['notes']): ?><div class="col-12"><span class="text-muted small">Notes:</span> <?= nl2br(htmlspecialchars($rfq['notes'])) ?></div><?php endif; ?>
        </div>
      </div>
    </div>

    <style>
      .rfq-blue thead th { background:#1565c0 !important; color:#fff !important; font-weight:600; font-size:.78rem; border-color:#1565c0 !important; }
      .rfq-tabs .nav-link { font-size:.85rem; }
      .rfq-tabs .nav-link.active { font-weight:600; }
    </style>

    <!-- Comparison grid -->
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-bar-chart-steps me-2"></i>Quotation Comparison</div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table table-sm table-bordered mb-0 erp-table rfq-blue">
            <thead>
              <tr>
                <th>Item</th>
                <th>Qty</th>
                <?php foreach ($subs as $s): ?>
                <th class="text-center">
                  <?= htmlspecialchars($s['supplier_name']) ?><br>
                  <span class="badge bg-<?= $statusColors[$s['status']] ?? 'secondary' ?>"><?= $s['status'] ?></span>
                </th>
                <?php endforeach; ?>
              </tr>
            </thead>
            <tbody>
              <?php foreach ($lines as $l): ?>
              <tr>
                <td class="small"><div class="fw-bold"><?= $l['item_code'] ? htmlspecialchars($l['item_code']) : 'Item #'.(int)$l['item_id'] ?></div><div class="text-muted" style="font-size:.72rem"><?= htmlspecialchars($l['item_name'] ?? '—') ?></div></td>
                <td class="small"><?= number_format($l['qty'],3) ?> <?= htmlspecialchars($l['uom_code'] ?? '—') ?></td>
                <?php foreach ($subs as $s):
                  $ql = $matrix[$s['id']][$l['id']] ?? null;
                  $isLowest = $ql && $lowestPerLine[$l['id']] !== null && (float)$ql['unit_price'] === (float)$lowestPerLine[$l['id']];
                ?>
                <td class="text-center small <?= $isLowest ? 'table-success fw-bold' : '' ?>">
                  <?php if ($ql): ?>
                    <?= esc_html(msme_erp_cur()) ?><?= number_format($ql['unit_price'],2) ?><br>
                    <span class="text-muted" style="font-size:.7rem"><?= $ql['delivery_days'] !== null ? $ql['delivery_days'].'d delivery' : '' ?></span>
                    <?php if ($ql['remarks']): ?><br><span class="text-muted fst-italic" style="font-size:.68rem"><?= htmlspecialchars($ql['remarks']) ?></span><?php endif; ?>
                  <?php else: ?>—<?php endif; ?>
                </td>
                <?php endforeach; ?>
              </tr>
              <?php endforeach; ?>
            </tbody>
            <tfoot class="table-light fw-bold">
              <tr>
                <td colspan="2" class="text-end small text-muted">Last discount %:</td>
                <?php foreach ($subs as $s): $lt = $lastTerms($s); ?>
                <td class="text-center small text-muted">
                  <?php if ($lt['basis'] === null): ?>--<?php else: ?>
                    <?= number_format($lt['pct'], 2) ?>%
                    <span class="badge bg-<?= $basisBadge[$lt['basis']] ?? 'secondary' ?>" style="font-size:.6rem"><?= esc_html($lt['basis']) ?></span>
                    <div style="font-size:.68rem"><?= $lt['note'] ?></div>
                  <?php endif; ?>
                </td>
                <?php endforeach; ?>
              </tr>
              <tr>
                <td colspan="2" class="text-end small text-muted">Discount Amount:</td>
                <?php foreach ($subs as $s): $lt = $lastTerms($s); ?>
                <td class="text-center small text-muted">
                  <?= $lt['basis'] === null ? '--' : msme_erp_cur_code() . ' ' . number_format(max(0, (float)$s['total_quoted'] - (float)$lt['net']), 2) ?>
                </td>
                <?php endforeach; ?>
              </tr>
              <tr>
                <td colspan="2" class="text-end small">Total Quoted:</td>
                <?php foreach ($subs as $s):
                  $ed = $effectiveDiscount($s);
                  $isLowestTotal = $s['status']==='SUBMITTED' && $lowestTotal !== null && (float)$s['total_quoted'] === (float)$lowestTotal;
                ?>
                <td class="text-center small <?= $isLowestTotal ? 'table-success text-success' : '' ?>">
                  <?php if ($s['status']==='SUBMITTED'): ?>
                    INR<?= number_format($s['total_quoted'],2) ?>
                    <?php if ($ed['net'] !== null): ?>
                    <?php endif; ?>
                  <?php else: ?>--<?php endif; ?>
                </td>
                <?php endforeach; ?>
              </tr>
              <tr>
                <td colspan="2" class="text-end small">Last price / Net payable:</td>
                <?php foreach ($subs as $s): $lt = $lastTerms($s);
                  $isLowestNet = $lt['net'] !== null && $lowestNet !== null && abs($lt['net'] - $lowestNet) < 0.005; ?>
                <td class="text-center small <?= $isLowestNet ? 'table-success text-success' : '' ?>">
                  <?php if ($lt['basis'] === null): ?>--<?php else: ?>
                    INR<?= number_format($lt['net'], 2) ?>
                    <div class="fw-normal" style="font-size:.68rem"><?= esc_html($lt['basis']) ?><?= $isLowestNet ? ' &middot; lowest' : '' ?></div>
                  <?php endif; ?>
                </td>
                <?php endforeach; ?>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>

    <!-- Commercial terms — payment/delivery/incoterms/certifications, -->
    <!-- as captured on the supplier's quote form, for submitted quotes -->
    <?php // $submittedSubs already computed above (line ~143) ?>
    <?php if ($submittedSubs): ?>
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-file-earmark-text me-2"></i>Commercial Terms</div>
      <div class="card-body p-0">
        <table class="table table-sm mb-0 erp-table rfq-blue">
          <thead><tr><th>Supplier</th><th>Payment Terms</th><th>Delivery Period</th><th>Incoterms</th><th>Certifications / Awards</th></tr></thead>
          <tbody>
            <?php foreach ($submittedSubs as $s): ?>
            <tr>
              <td class="small fw-bold"><?= htmlspecialchars($s['supplier_name']) ?></td>
              <td class="small"><?= htmlspecialchars($s['payment_terms'] ?: '—') ?></td>
              <td class="small"><?= htmlspecialchars($s['delivery_period'] ?: '—') ?></td>
              <td class="small"><?= htmlspecialchars($s['incoterms'] ?: '—') ?></td>
              <td class="small"><?= nl2br(htmlspecialchars($s['certifications'] ?: '—')) ?></td>
            </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>
    </div>
    <?php endif; ?>

    <!-- Create Purchase Order directly from the comparison — no manual PO form -->
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2"><i class="bi bi-bag-check me-2"></i>Purchase Order</div>
      <div class="card-body">
        <?php if ($linkedPOs): ?>
          <div class="small mb-2 fw-bold text-success"><i class="bi bi-check-circle me-1"></i>Purchase Order(s) already created from this RFQ (<?= count($lines) - count($openLines) ?> of <?= count($lines) ?> item<?= count($lines)==1?'':'s' ?> already ordered — those items are locked and won't be re-ordered):</div>
          <table class="table table-sm mb-0 erp-table rfq-blue">
            <thead><tr><th>PO Number</th><th>Supplier</th><th>Status</th><th class="text-end">Grand Total</th><th></th></tr></thead>
            <tbody>
              <?php foreach ($linkedPOs as $po): ?>
              <tr>
                <td class="fw-bold"><?= htmlspecialchars($po['po_number']) ?></td>
                <td class="small"><?= htmlspecialchars($po['supplier_name']) ?></td>
                <td><span class="badge bg-secondary"><?= htmlspecialchars($po['status']) ?></span></td>
                <td class="text-end small"><?= esc_html(msme_erp_cur()) ?><?= number_format($po['grand_total'], 2) ?></td>
                <td><a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME23N&id=' . $po['id']) ?>" class="btn btn-xs btn-outline-primary py-0 px-1 small">View</a></td>
              </tr>
              <?php endforeach; ?>
            </tbody>
          </table>
        <?php endif; ?>

        <?php
        $openSubmittedExist = false;
        foreach ($openLines as $ol) {
            foreach ($submittedSubs as $s) { if (!empty($matrix[$s['id']][$ol['id']])) { $openSubmittedExist = true; break 2; } }
        }
        ?>
        <?php if (!$openLines): ?>
          <div class="text-muted small<?= $linkedPOs ? ' mt-3' : '' ?>">Every item on this RFQ already has a Purchase Order. Nothing left to award.</div>
        <?php elseif (!$submittedSubs || !$openSubmittedExist): ?>
          <div class="text-muted small<?= $linkedPOs ? ' mt-3' : '' ?>">The remaining <?= count($openLines) ?> item<?= count($openLines)==1?'':'s' ?> have no submitted quotation yet — a Purchase Order can be created automatically once at least one supplier submits a price for <?= count($openLines)==1?'it':'them' ?>.</div>
        <?php elseif (!Auth::checkTcode('ME21N')): ?>
          <div class="text-muted small<?= $linkedPOs ? ' mt-3' : '' ?>">Your account isn't authorized for T-Code ME21N, so it can't create Purchase Orders from here.</div>
        <?php else: ?>
          <div class="small text-muted mb-2<?= $linkedPOs ? ' mt-3' : '' ?>">
            Awards each <?= $linkedPOs ? 'remaining' : '' ?> item to whichever submitted supplier quoted the lowest price for it — highlighted in green above.
            If different suppliers win different items, one PO is created per winning supplier automatically.
            <?= $linkedPOs ? 'Items that already have a PO (above) are skipped — they will never be re-ordered.' : 'No manual entry needed.' ?>
          </div>
          <form method="POST" onsubmit="return confirm('Create Purchase Order(s) automatically using the lowest-priced quote for each remaining item? This cannot be undone from here.');">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="po_action" value="create_po">
            <button type="submit" class="btn btn-success btn-sm">
              <i class="bi bi-bag-check me-1"></i>Create Purchase Order(s) <?= $linkedPOs ? 'for Remaining Item(s)' : 'Automatically' ?>
            </button>
          </form>
        <?php endif; ?>
      </div>
    </div>

    <!-- Supplier links / negotiation (tabbed) -->
    <?php
    $negoBadge   = ['NONE'=>'secondary','IN_PROGRESS'=>'warning','ACCEPTED'=>'success','EXHAUSTED'=>'danger'];
    $actionBadge = ['OFFER'=>'primary','COUNTER'=>'info','DECLINE'=>'danger','ACCEPT'=>'success','FINAL'=>'success','FINAL_CLEARED'=>'secondary','NOTE'=>'secondary'];
    $activeTab   = ($_GET['tab'] ?? '') === 'nego' ? 'nego' : 'links';

    $negoBySupplier = [];
    if ($subs) {
        $rsIds = array_map('intval', array_column($subs, 'id'));
        $negoRowsAll = DB::fetchAll(
            "SELECT n.*, u.full_name AS by_name FROM rfq_negotiations n
             LEFT JOIN users u ON u.id = n.created_by
             WHERE n.rfq_supplier_id IN (" . implode(',', array_fill(0, count($rsIds), '?')) . ")
             ORDER BY n.id ASC", $rsIds
        );
        foreach ($negoRowsAll as $nr) $negoBySupplier[$nr['rfq_supplier_id']][] = $nr;
    }
    ?>
    <ul class="nav nav-tabs rfq-tabs" role="tablist">
      <li class="nav-item" role="presentation">
        <button class="nav-link <?= $activeTab === 'links' ? 'active' : '' ?>" data-bs-toggle="tab" data-bs-target="#tab-links" type="button" role="tab"><i class="bi bi-link-45deg me-1"></i>Invited Suppliers &amp; Links</button>
      </li>
      <li class="nav-item" role="presentation">
        <button class="nav-link <?= $activeTab === 'nego' ? 'active' : '' ?>" data-bs-toggle="tab" data-bs-target="#tab-nego" type="button" role="tab"><i class="bi bi-chat-left-dots me-1"></i>Negotiation</button>
      </li>
    </ul>
    <div class="tab-content mb-3">
      <!-- ===== Tab 1: Invited suppliers & links ===== -->
      <div class="tab-pane fade <?= $activeTab === 'links' ? 'show active' : '' ?>" id="tab-links" role="tabpanel">
        <div class="card border-0 shadow-sm" style="border-top-left-radius:0">
          <div class="card-body p-0">
            <div class="table-responsive">
            <table class="table table-sm mb-0 erp-table rfq-blue">
              <thead><tr><th>Supplier</th><th>Email</th><th>Status</th><th>Sent</th><th>Viewed</th><th>Submitted</th><th>Negotiation</th><th>Final Discount</th><th>Link</th></tr></thead>
              <tbody>
                <?php foreach ($subs as $s): $link = home_url('/?msme_rfq=' . $s['token']);
                  $ns = $s['negotiation_status'] ?? 'NONE';
                  $finalSet = !empty($s['final_set_at']) && $s['final_discount_pct'] !== null;
                ?>
                <tr>
                  <td class="small fw-bold"><?= htmlspecialchars($s['supplier_name']) ?></td>
                  <td class="small" style="min-width:300px">
                    <form method="POST" class="d-flex gap-1 align-items-center">
                      <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
                      <input type="hidden" name="rfq_action" value="send_invite">
                      <input type="hidden" name="rfq_supplier_id" value="<?= (int)$s['id'] ?>">
                      <input type="email" name="email_to" class="form-control form-control-sm" style="min-width:190px" required
                             value="<?= esc_attr($s['email_to'] ?: ($s['supplier_master_email'] ?? '')) ?>" placeholder="supplier@example.com">
                      <button type="submit" class="btn btn-sm btn-primary py-0 px-2" title="Email the quotation link to this address (also saves the address)"><i class="bi bi-send me-1"></i><?= $s['sent_at'] ? 'Resend' : 'Send' ?></button>
                    </form>
                    <?php $__ph = preg_replace('/\D+/', '', (string)($s['supplier_phone'] ?? '')); if (strlen($__ph) === 10) $__ph = '91' . $__ph;
                      if (strlen($__ph) >= 11): ?>
                    <a class="small" target="_blank" rel="noopener" href="https://wa.me/<?= esc_attr($__ph) ?>?text=<?= rawurlencode('Request for Quotation ' . $rfq['rfq_number'] . ' - please fill your quotation here: ' . $link) ?>"><i class="bi bi-whatsapp text-success me-1"></i>Send by WhatsApp</a>
                    <?php endif; ?>
                  </td>
                  <td><span class="badge bg-<?= $statusColors[$s['status']] ?? 'secondary' ?>"><?= $s['status'] ?></span></td>
                  <td class="small"><?= $s['sent_at'] ?? '--' ?></td>
                  <td class="small"><?= $s['viewed_at'] ?? '--' ?></td>
                  <td class="small"><?= $s['submitted_at'] ?? '--' ?></td>
                  <td class="small">
                    <?php if ($ns === 'NONE'): ?>
                      <span class="badge bg-secondary">--</span>
                    <?php else: ?>
                      <span class="badge bg-<?= $negoBadge[$ns] ?? 'secondary' ?>"><?= $ns ?></span>
                      <div class="text-muted" style="font-size:.7rem">Round <?= (int)$s['negotiation_round'] ?> -- asking <?= number_format((float)$s['negotiation_discount_pct'], 2) ?>% off<?= $ns === 'ACCEPTED' ? ' (accepted)' : '' ?></div>
                    <?php endif; ?>
                  </td>
                  <td class="small">
                    <?php if ($s['status'] !== 'SUBMITTED'): ?>
                      <span class="text-muted">--</span>
                    <?php else: ?>
                      <?php if ($finalSet): ?>
                        <div class="fw-bold text-success"><?= number_format((float)$s['final_discount_pct'], 2) ?>% off</div>
                        <div class="text-muted" style="font-size:.7rem">Net INR<?= number_format((float)$s['final_amount'], 2) ?></div>
                      <?php else: $lt = $lastTerms($s); if ($lt['basis'] === 'LAST OFFER' || $lt['basis'] === 'ACCEPTED'): ?>
                        <div class="text-muted" style="font-size:.72rem">Last: <?= number_format($lt['pct'], 2) ?>% &rarr; INR<?= number_format($lt['net'], 2) ?> (<?= esc_html(strtolower($lt['basis'])) ?>)</div>
                      <?php endif; endif; ?>
                      <button type="button" class="btn btn-outline-primary py-0 px-1 js-final-btn" style="font-size:.75rem"
                              data-bs-toggle="modal" data-bs-target="#finalDiscModal"
                              data-rs="<?= (int)$s['id'] ?>" data-name="<?= esc_attr($s['supplier_name']) ?>"
                              data-quoted="<?= esc_attr(number_format((float)$s['total_quoted'], 2, '.', '')) ?>"
                              data-pct="<?= $finalSet ? esc_attr($s['final_discount_pct']) : '' ?>"
                              data-remarks="<?= esc_attr($s['final_remarks'] ?? '') ?>"
                              data-set="<?= $finalSet ? '1' : '0' ?>">
                        <i class="bi bi-pencil-square me-1"></i><?= $finalSet ? 'Edit' : 'Set' ?>
                      </button>
                    <?php endif; ?>
                  </td>
                  <td class="small">
                    <input type="text" readonly value="<?= esc_attr($link) ?>" class="form-control form-control-sm d-inline-block" style="width:220px" onclick="this.select()">
                  </td>
                </tr>
                <?php endforeach; ?>
              </tbody>
            </table>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== Tab 2: Negotiation history ===== -->
      <div class="tab-pane fade <?= $activeTab === 'nego' ? 'show active' : '' ?>" id="tab-nego" role="tabpanel">
        <?php foreach ($subs as $s):
          $rows = $negoBySupplier[$s['id']] ?? [];
          $ns = $s['negotiation_status'] ?? 'NONE';
          $finalSet = !empty($s['final_set_at']) && $s['final_discount_pct'] !== null;
        ?>
        <div class="card border-0 shadow-sm mb-3" style="border-top-left-radius:0">
          <div class="card-header bg-white py-2 d-flex justify-content-between align-items-center flex-wrap gap-2">
            <div class="small">
              <span class="fw-bold"><?= htmlspecialchars($s['supplier_name']) ?></span>
              <span class="badge bg-<?= $statusColors[$s['status']] ?? 'secondary' ?> ms-1"><?= $s['status'] ?></span>
              <?php if ($ns !== 'NONE'): ?><span class="badge bg-<?= $negoBadge[$ns] ?? 'secondary' ?> ms-1"><?= $ns ?></span><?php endif; ?>
              <?php if ($s['status'] === 'SUBMITTED'): ?>
                <span class="text-muted ms-2">Quoted INR<?= number_format((float)$s['total_quoted'], 2) ?></span>
              <?php endif; ?>
              <?php if ($finalSet): ?>
                <span class="text-success fw-bold ms-2"><i class="bi bi-check2-circle"></i> Final: <?= number_format((float)$s['final_discount_pct'], 2) ?>% off &rarr; INR<?= number_format((float)$s['final_amount'], 2) ?></span>
              <?php endif; ?>
            </div>
            <button type="button" class="btn btn-sm btn-outline-secondary py-0 js-log-btn" data-bs-toggle="modal" data-bs-target="#logRoundModal"
                    data-rs="<?= (int)$s['id'] ?>" data-name="<?= esc_attr($s['supplier_name']) ?>">
              <i class="bi bi-plus-circle me-1"></i>Log entry
            </button>
          </div>
          <div class="card-body p-0">
            <?php if (!$rows): ?>
              <div class="text-muted small p-3">No negotiation activity recorded for this supplier yet.</div>
            <?php else: ?>
            <div class="table-responsive">
            <table class="table table-sm mb-0 erp-table rfq-blue">
              <thead><tr><th>Round</th><th>When</th><th>By</th><th>Action</th><th>Channel</th><th class="text-end">Discount %</th><th class="text-end">Amount</th><th>Remarks</th></tr></thead>
              <tbody>
                <?php foreach ($rows as $nr): ?>
                <tr>
                  <td class="small"><?= (int)$nr['round_no'] ?></td>
                  <td class="small"><?= esc_html($nr['created_at']) ?></td>
                  <td class="small"><?= esc_html($nr['actor']) ?><?= !empty($nr['by_name']) ? '<div class="text-muted" style="font-size:.7rem">' . esc_html($nr['by_name']) . '</div>' : '' ?></td>
                  <td><span class="badge bg-<?= $actionBadge[$nr['action_type']] ?? 'secondary' ?>"><?= esc_html($nr['action_type']) ?></span></td>
                  <td class="small"><?= esc_html($nr['channel'] ?? '--') ?: '--' ?></td>
                  <td class="small text-end"><?= (float)$nr['discount_pct'] > 0 ? number_format((float)$nr['discount_pct'], 2) . '%' : '--' ?></td>
                  <td class="small text-end"><?= (float)$nr['total_amount'] > 0 ? msme_erp_cur_code() . ' ' . number_format((float)$nr['total_amount'], 2) : '--' ?></td>
                  <td class="small"><?= esc_html($nr['remarks'] ?? '') ?></td>
                </tr>
                <?php endforeach; ?>
              </tbody>
            </table>
            </div>
            <?php endif; ?>
          </div>
        </div>
        <?php endforeach; ?>
      </div>
    </div>

    <!-- Final Discount modal -->
    <div class="modal fade" id="finalDiscModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <form method="POST" id="finalDiscForm">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="nego_action" value="set_final">
            <input type="hidden" name="rfq_supplier_id" id="fd-rs" value="">
            <div class="modal-header py-2">
              <h6 class="modal-title"><i class="bi bi-percent me-1"></i>Final Discount &mdash; <span id="fd-name"></span></h6>
              <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
              <div class="small text-muted mb-2">Total quoted: <strong>INR<span id="fd-quoted"></span></strong></div>
              <div class="row g-2">
                <div class="col-6">
                  <label class="form-label small">Enter as</label>
                  <select name="final_mode" id="fd-mode" class="form-select form-select-sm">
                    <option value="PERCENT">Discount %</option>
                    <option value="NET">Final net amount</option>
                  </select>
                </div>
                <div class="col-6">
                  <label class="form-label small">Value</label>
                  <input type="number" name="final_value" id="fd-value" class="form-control form-control-sm" min="0" step="0.01" required>
                </div>
                <div class="col-12 small text-success" id="fd-preview"></div>
                <div class="col-6">
                  <label class="form-label small">Agreed via</label>
                  <select name="channel" class="form-select form-select-sm">
                    <option value="">&mdash;</option><option>PHONE</option><option>EMAIL</option><option>MEETING</option><option>PORTAL</option><option>OTHER</option>
                  </select>
                </div>
                <div class="col-12">
                  <label class="form-label small">Remarks</label>
                  <input type="text" name="remarks" id="fd-remarks" class="form-control form-control-sm" maxlength="255" placeholder="e.g. Agreed on call with sales head">
                </div>
              </div>
              <div class="small text-muted mt-2">This records the agreed discount and shows it on the comparison. It does not change any Purchase Order.</div>
            </div>
            <div class="modal-footer py-2">
              <button type="submit" class="btn btn-sm btn-primary"><i class="bi bi-check2 me-1"></i>Save Final Discount</button>
              <button type="submit" form="finalClearForm" class="btn btn-sm btn-outline-danger" id="fd-clear" style="display:none" onclick="return confirm('Remove the final discount for this supplier?');">Clear</button>
            </div>
          </form>
          <form method="POST" id="finalClearForm">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="nego_action" value="clear_final">
            <input type="hidden" name="rfq_supplier_id" id="fd-rs2" value="">
          </form>
        </div>
      </div>
    </div>

    <!-- Log negotiation entry modal -->
    <div class="modal fade" id="logRoundModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog">
        <div class="modal-content">
          <form method="POST">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="nego_action" value="log_round">
            <input type="hidden" name="rfq_supplier_id" id="lr-rs" value="">
            <div class="modal-header py-2">
              <h6 class="modal-title"><i class="bi bi-chat-left-text me-1"></i>Log negotiation &mdash; <span id="lr-name"></span></h6>
              <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
              <div class="row g-2">
                <div class="col-4"><label class="form-label small">Who</label>
                  <select name="actor" class="form-select form-select-sm"><option value="BUYER">Buyer (us)</option><option value="SUPPLIER">Supplier</option></select></div>
                <div class="col-4"><label class="form-label small">Type</label>
                  <select name="action_type" class="form-select form-select-sm"><option value="OFFER">Offer</option><option value="COUNTER">Counter</option><option value="NOTE">Note</option></select></div>
                <div class="col-4"><label class="form-label small">Channel</label>
                  <select name="channel" class="form-select form-select-sm"><option value="">&mdash;</option><option>PHONE</option><option>EMAIL</option><option>MEETING</option><option>PORTAL</option><option>OTHER</option></select></div>
                <div class="col-6"><label class="form-label small">Discount %</label>
                  <input type="number" name="discount_pct" class="form-control form-control-sm" min="0" max="99.99" step="0.01"></div>
                <div class="col-6"><label class="form-label small">Amount (INR)</label>
                  <input type="number" name="total_amount" class="form-control form-control-sm" min="0" step="0.01"></div>
                <div class="col-12"><label class="form-label small">Remarks</label>
                  <input type="text" name="remarks" class="form-control form-control-sm" maxlength="255" placeholder="What was discussed / agreed"></div>
              </div>
            </div>
            <div class="modal-footer py-2"><button type="submit" class="btn btn-sm btn-primary">Save entry</button></div>
          </form>
        </div>
      </div>
    </div>

    <script>
    (function () {
      var quoted = 0;
      function preview() {
        var mode = document.getElementById('fd-mode').value, v = parseFloat(document.getElementById('fd-value').value), el = document.getElementById('fd-preview');
        if (!quoted || isNaN(v)) { el.textContent = ''; return; }
        var net = mode === 'PERCENT' ? quoted * (1 - v / 100) : v;
        var pct = mode === 'PERCENT' ? v : (1 - v / quoted) * 100;
        el.textContent = 'Net after discount: INR ' + net.toFixed(2) + '  (' + pct.toFixed(2) + '% off)';
      }
      document.addEventListener('click', function (e) {
        var b = e.target.closest('.js-final-btn');
        if (b) {
          quoted = parseFloat(b.dataset.quoted) || 0;
          document.getElementById('fd-rs').value = b.dataset.rs;
          document.getElementById('fd-rs2').value = b.dataset.rs;
          document.getElementById('fd-name').textContent = b.dataset.name;
          document.getElementById('fd-quoted').textContent = b.dataset.quoted;
          document.getElementById('fd-mode').value = 'PERCENT';
          document.getElementById('fd-value').value = b.dataset.pct || '';
          document.getElementById('fd-remarks').value = b.dataset.remarks || '';
          document.getElementById('fd-clear').style.display = b.dataset.set === '1' ? '' : 'none';
          preview();
        }
        var l = e.target.closest('.js-log-btn');
        if (l) {
          document.getElementById('lr-rs').value = l.dataset.rs;
          document.getElementById('lr-name').textContent = l.dataset.name;
        }
      });
      ['fd-mode', 'fd-value'].forEach(function (id) { document.getElementById(id).addEventListener('input', preview); });
    })();
    </script>

    <!-- AI comparison -->
    <?php
      // Final quotations = every SUBMITTED supplier is done negotiating
      // (negotiation_status is NONE, ACCEPTED, or EXHAUSTED — never
      // IN_PROGRESS) — OR the buyer has manually marked quotes final
      // (quotes_finalized_at), which overrides a straggling negotiation
      // that never reaches a terminal state on its own.
      $anyNegotiationInProgress = false;
      foreach ($submittedSubs as $s) {
          if (($s['negotiation_status'] ?? 'NONE') === 'IN_PROGRESS') { $anyNegotiationInProgress = true; break; }
      }
      $manuallyFinalized = (bool)$rfq['quotes_finalized_at'];
      $canGenerateAi = (bool)$submittedSubs && (!$anyNegotiationInProgress || $manuallyFinalized);
    ?>
    <div class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-white fw-bold small py-2 d-flex justify-content-between align-items-center">
        <span><i class="bi bi-stars me-2"></i>AI Comparison</span>
        <div class="d-flex gap-2">
          <?php if ($submittedSubs && empty($rfq['auto_award_status']) && ($rfq['status'] ?? '') === 'OPEN'): ?>
          <form method="POST" onsubmit="return confirm('Close the quotations now? Open negotiations are closed, the AI comparison runs, the best supplier is selected and a Purchase Order is created and sent for release.');">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="ai_action" value="finalize_quotes">
            <button class="btn btn-sm btn-outline-success" type="submit">
              <i class="bi bi-check2-circle me-1"></i>Close Quotations &amp; Evaluate
            </button>
          </form>
          <?php elseif (in_array($rfq['auto_award_status'] ?? '', ['PO_FAILED', 'ERROR'], true)): ?>
          <form method="POST" onsubmit="return confirm('Re-run the comparison and create the Purchase Order?');">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="ai_action" value="retry_award">
            <button class="btn btn-sm btn-outline-danger" type="submit"><i class="bi bi-arrow-repeat me-1"></i>Retry evaluation &amp; PO</button>
          </form>
          <?php endif; ?>
          <form method="POST">
            <input type="hidden" name="_csrf" value="<?= Auth::csrfToken() ?>">
            <input type="hidden" name="ai_action" value="generate">
            <button class="btn btn-sm btn-outline-primary" type="submit" <?= $canGenerateAi ? '' : 'disabled' ?>>
              <i class="bi bi-stars me-1"></i><?= $rfq['ai_summary'] ? 'Regenerate' : 'Generate' ?> AI Comparison
            </button>
          </form>
        </div>
      </div>
      <?php if ($anyNegotiationInProgress && !$manuallyFinalized): ?>
      <div class="card-body text-muted small">Negotiation is still open with one or more suppliers. The AI comparison and the order follow automatically at the cut-off, or click "Close Quotations &amp; Evaluate" to do it now.</div>
      <?php elseif ($rfq['ai_summary']): ?>
      <div class="card-body">
        <?php if ($manuallyFinalized): ?>
        <div class="text-success mb-2" style="font-size:.72rem"><i class="bi bi-check2-circle me-1"></i>Quotations closed<?= $rfq['quotes_finalized_at'] ? ' on ' . $rfq['quotes_finalized_at'] : '' ?>; any negotiation still open at that time was closed.</div>
        <?php endif; ?>
        <div style="white-space:pre-wrap" class="small"><?= htmlspecialchars($rfq['ai_summary']) ?></div>
        <div class="text-muted mt-2" style="font-size:.72rem">Generated <?= $rfq['ai_summary_at'] ?> — weighs price, delivery, and commercial terms (payment terms, delivery period, incoterms, certifications). AI-generated, always verify before deciding.</div>
      </div>
      <?php else: ?>
      <div class="card-body text-muted small">No AI comparison yet. It runs automatically at the cut-off, or click "Generate AI Comparison" for a preview.</div>
      <?php endif; ?>
    </div>

    <?php
    return;
}

// ============================================================
// LIST VIEW
// ============================================================
$rfqs = DB::fetchAll(
    "SELECT r.*, pr.pr_number,
            COUNT(DISTINCT rs.id) AS invited,
            SUM(CASE WHEN rs.status='SUBMITTED' THEN 1 ELSE 0 END) AS submitted
     FROM rfqs r
     JOIN purchase_requisitions pr ON pr.id=r.pr_id
     LEFT JOIN rfq_suppliers rs ON rs.rfq_id=r.id
     WHERE r.company_id=? GROUP BY r.id ORDER BY r.id DESC",
    [$companyId]
);

$pageTitle   = 'Compare Quotations';
$pageTcode   = 'ME47';
$breadcrumbs = ['MM', 'Purchasing', 'Compare Quotations'];
$pageActions = '<a href="' . admin_url('admin.php?page=msme-erp&tcode=ME41') . '" class="btn btn-sm btn-primary"><i class="bi bi-envelope-paper me-1"></i>New RFQ (ME41)</a>';
erp_render_page_header();
?>

<div class="card border-0 shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-hover table-sm mb-0 erp-table">
        <thead><tr><th>RFQ Number</th><th>Source PR</th><th>RFQ Date</th><th>Due Date</th><th>Status</th><th>Suppliers</th><th>Submitted</th><th>Action</th></tr></thead>
        <tbody>
          <?php foreach ($rfqs as $r): ?>
          <tr>
            <td><a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $r['id']) ?>" class="fw-bold text-decoration-none"><?= htmlspecialchars($r['rfq_number']) ?></a></td>
            <td class="small"><?= htmlspecialchars($r['pr_number']) ?></td>
            <td class="small"><?= $r['rfq_date'] ?></td>
            <td class="small"><?= $r['due_date'] ?? '—' ?></td>
            <td><span class="badge bg-<?= $r['status']==='OPEN'?'warning':'secondary' ?>"><?= $r['status'] ?></span></td>
            <td class="small text-center"><?= (int)$r['invited'] ?></td>
            <td class="small text-center"><span class="fw-bold text-<?= $r['submitted']>0?'success':'muted' ?>"><?= (int)$r['submitted'] ?></span> / <?= (int)$r['invited'] ?></td>
            <td>
              <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME47&id=' . $r['id']) ?>" class="btn btn-xs btn-outline-primary py-0 px-1 small"><i class="bi bi-eye"></i></a>
            </td>
          </tr>
          <?php endforeach; if (!$rfqs): ?>
          <tr><td colspan="8" class="text-center text-muted py-4">No RFQs yet. <a href="<?= admin_url('admin.php?page=msme-erp&tcode=ME41') ?>">Create one (ME41)</a>.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>
</div>
