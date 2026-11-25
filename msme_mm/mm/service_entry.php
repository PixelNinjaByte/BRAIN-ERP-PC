<?php
/**
 * MSME ERP — Router: ML81N → mm/service_entry_full.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'ML81N';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/service_entry_full.php';
