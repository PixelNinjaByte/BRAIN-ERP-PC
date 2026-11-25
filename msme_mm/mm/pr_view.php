<?php
/**
 * MSME ERP — Router: ME53N → mm/pr_full.php
 * Developed by: Prigag Online Pvt. Ltd.
 */
defined('ABSPATH') || exit;
if (empty($_GET['tcode'])) $_GET['tcode'] = 'ME53N';
$tcode = $_GET['tcode'];
require_once __DIR__ . '/pr_full.php';
