<?php

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function exportResponse(array $payload, int $status = 200): void
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
    exit;
}

$remote = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
if (!in_array($remote, array('127.0.0.1', '::1'), true)) {
    exportResponse(array('error' => 'forbidden'), 403);
}
if ((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    exportResponse(array('error' => 'method_not_allowed'), 405);
}

define('NO_KEEP_STATISTIC', true);
define('NOT_CHECK_PERMISSIONS', true);
define('BX_NO_ACCELERATOR_RESET', true);
require $_SERVER['DOCUMENT_ROOT'] . '/bitrix/modules/main/include/prolog_before.php';

if (!CModule::IncludeModule('sale')) {
    exportResponse(array('error' => 'sale_module_unavailable'), 503);
}

$limit = max(1, min(200, (int) ($_GET['limit'] ?? 100)));
$cursor = (int) ($_GET['cursor'] ?? 0);
$filter = $cursor > 0 ? array('<ID' => $cursor) : array();
$select = array('ID', 'ACCOUNT_NUMBER', 'DATE_INSERT', 'DATE_UPDATE', 'STATUS_ID', 'PRICE', 'USER_ID', 'CANCELED');
$dbOrders = CSaleOrder::GetList(array('ID' => 'DESC'), $filter, false, array('nTopCount' => $limit + 1), $select);
$rawOrders = array();
$ids = array();
while ($order = $dbOrders->Fetch()) {
    $rawOrders[] = $order;
    $ids[] = (int) $order['ID'];
}
$hasMore = count($rawOrders) > $limit;
if ($hasMore) {
    array_pop($rawOrders);
    array_pop($ids);
}

$properties = array();
if ($ids) {
    $dbProps = CSaleOrderPropsValue::GetList(array('ORDER_ID' => 'ASC'), array('ORDER_ID' => $ids));
    while ($property = $dbProps->Fetch()) {
        $orderId = (string) $property['ORDER_ID'];
        $code = strtoupper(trim((string) ($property['CODE'] ?? '')));
        $name = strtoupper(trim((string) ($property['NAME'] ?? '')));
        $value = trim((string) ($property['VALUE'] ?? ''));
        if ($value === '') {
            continue;
        }
        if (!isset($properties[$orderId])) {
            $properties[$orderId] = array();
        }
        if ($code !== '') {
            $properties[$orderId][$code] = $value;
        }
        if ($name !== '') {
            $properties[$orderId]['NAME:' . $name] = $value;
        }
    }
}

function firstProperty(array $properties, array $keys): string
{
    foreach ($keys as $key) {
        if (isset($properties[$key]) && trim((string) $properties[$key]) !== '') {
            return trim((string) $properties[$key]);
        }
    }
    return '';
}

$orders = array();
foreach ($rawOrders as $order) {
    $id = (string) $order['ID'];
    $props = $properties[$id] ?? array();
    $orders[] = array(
        'id' => $id,
        'number' => (string) $order['ACCOUNT_NUMBER'],
        'date' => (string) $order['DATE_INSERT'],
        'updated_at' => (string) $order['DATE_UPDATE'],
        'status' => (string) $order['STATUS_ID'],
        'price' => (float) $order['PRICE'],
        'cancelled' => ((string) $order['CANCELED'] === 'Y'),
        'external_customer_id' => ((int) $order['USER_ID'] > 0 ? (string) $order['USER_ID'] : ''),
        'customer' => firstProperty($props, array('FIO', 'NAME', 'CONTACT_PERSON', 'NAME:Ф.И.О.', 'NAME:ИМЯ')),
        'phone' => firstProperty($props, array('PHONE', 'MOBILE', 'NAME:ТЕЛЕФОН')),
        'email' => firstProperty($props, array('EMAIL', 'NAME:E-MAIL', 'NAME:EMAIL')),
        'city' => firstProperty($props, array('CITY', 'LOCATION_NAME', 'NAME:ГОРОД')),
    );
}

$nextCursor = $orders ? (int) $orders[count($orders) - 1]['id'] : null;
exportResponse(array(
    'orders' => $orders,
    'count' => count($orders),
    'has_more' => $hasMore,
    'next_cursor' => $hasMore ? $nextCursor : null,
));
