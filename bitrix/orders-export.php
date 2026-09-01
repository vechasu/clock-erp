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
$select = array(
    'ID', 'ACCOUNT_NUMBER', 'DATE_INSERT', 'DATE_UPDATE', 'STATUS_ID',
    'PRICE', 'CURRENCY', 'USER_ID', 'CANCELED', 'PAYED', 'PAY_SYSTEM_ID',
    'DELIVERY_ID', 'DELIVERY_PRICE', 'USER_DESCRIPTION', 'COMMENTS',
    'TRACKING_NUMBER'
);
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

$basketItems = array();
if ($ids) {
    $dbBasket = CSaleBasket::GetList(
        array('ORDER_ID' => 'ASC', 'ID' => 'ASC'),
        array('ORDER_ID' => $ids),
        false,
        false,
        array(
            'ID', 'ORDER_ID', 'PRODUCT_ID', 'PRODUCT_XML_ID', 'XML_ID',
            'NAME', 'QUANTITY', 'PRICE', 'BASE_PRICE', 'DISCOUNT_PRICE'
        )
    );
    while ($basket = $dbBasket->Fetch()) {
        $orderId = (string) $basket['ORDER_ID'];
        if (!isset($basketItems[$orderId])) {
            $basketItems[$orderId] = array();
        }
        $basketItems[$orderId][] = array(
            'id' => (string) $basket['ID'],
            'product_id' => (string) $basket['PRODUCT_ID'],
            'xml_id' => (string) ($basket['PRODUCT_XML_ID'] ?: $basket['XML_ID']),
            'name' => (string) $basket['NAME'],
            'quantity' => (float) $basket['QUANTITY'],
            'price' => (float) $basket['PRICE'],
            'base_price' => (float) $basket['BASE_PRICE'],
            'discount' => (float) $basket['DISCOUNT_PRICE'],
        );
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

function resolvedCity(array $properties): string
{
    static $cache = array();
    $value = firstProperty($properties, array('CITY', 'LOCATION_NAME', 'NAME:ГОРОД'));
    if ($value === '' || !ctype_digit($value)) {
        return $value;
    }
    if (array_key_exists($value, $cache)) {
        return $cache[$value];
    }
    $location = CSaleLocation::GetByID((int) $value, LANGUAGE_ID);
    $name = is_array($location) ? trim((string) ($location['CITY_NAME'] ?? '')) : '';
    $cache[$value] = $name;
    return $name;
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
        'currency' => (string) $order['CURRENCY'],
        'cancelled' => ((string) $order['CANCELED'] === 'Y'),
        'paid' => (string) $order['PAYED'],
        'payment_system' => (string) $order['PAY_SYSTEM_ID'],
        'delivery' => (string) $order['DELIVERY_ID'],
        'delivery_price' => (float) $order['DELIVERY_PRICE'],
        'comment' => trim((string) $order['USER_DESCRIPTION']),
        'bitrix_comment' => trim((string) $order['COMMENTS']),
        'tracking' => trim((string) $order['TRACKING_NUMBER']),
        'external_customer_id' => ((int) $order['USER_ID'] > 0 ? (string) $order['USER_ID'] : ''),
        'customer' => firstProperty($props, array('FIO', 'NAME', 'CONTACT_PERSON', 'NAME:Ф.И.О.', 'NAME:ИМЯ')),
        'phone' => firstProperty($props, array('PHONE', 'MOBILE', 'NAME:ТЕЛЕФОН')),
        'email' => firstProperty($props, array('EMAIL', 'NAME:E-MAIL', 'NAME:EMAIL')),
        'city' => resolvedCity($props),
        'products' => $basketItems[$id] ?? array(),
    );
}

$nextCursor = $orders ? (int) $orders[count($orders) - 1]['id'] : null;
exportResponse(array(
    'orders' => $orders,
    'count' => count($orders),
    'has_more' => $hasMore,
    'next_cursor' => $hasMore ? $nextCursor : null,
));
