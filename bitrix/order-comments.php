<?php

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Pragma: no-cache');

function commentResponse(array $payload, int $status = 200): void
{
    http_response_code($status);
    echo json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ), "\n";
    exit;
}

function commentError(string $code, int $status, array $extra = array()): void
{
    commentResponse(array_merge(array('error' => $code), $extra), $status);
}

function commentBearerToken(): string
{
    $header = (string) ($_SERVER['HTTP_AUTHORIZATION'] ?? '');
    if ($header === '' && function_exists('getallheaders')) {
        $headers = getallheaders();
        if (is_array($headers)) {
            $header = (string) ($headers['Authorization'] ?? $headers['authorization'] ?? '');
        }
    }
    if (!preg_match('/^Bearer\s+(.+)$/i', trim($header), $matches)) {
        return '';
    }
    return trim((string) $matches[1]);
}

function commentConfiguredToken(): string
{
    $environmentToken = getenv('BITRIX_CATALOG_TOKEN');
    if (is_string($environmentToken) && $environmentToken !== '') {
        return $environmentToken;
    }
    $configPath = dirname((string) $_SERVER['DOCUMENT_ROOT'], 2)
        . '/.config/tictactoy/catalog_export_token.php';
    if (!is_file($configPath) || !is_readable($configPath)) {
        return '';
    }
    $configToken = require $configPath;
    return is_string($configToken) ? $configToken : '';
}

function commentHash(string $text): string
{
    return hash('sha256', $text);
}

function commentDate($value): ?string
{
    if ($value instanceof DateTimeInterface) {
        return $value->format(DATE_ATOM);
    }
    if (is_object($value) && method_exists($value, 'format')) {
        return $value->format(DATE_ATOM);
    }
    $text = trim((string) $value);
    return $text === '' ? null : $text;
}

function commentPayload($order): array
{
    $text = trim((string) $order->getField('COMMENTS'));
    return array(
        'order_id' => (string) $order->getId(),
        'field' => 'COMMENTS',
        'text' => $text,
        'hash' => commentHash($text),
        'updated_at' => commentDate($order->getField('DATE_UPDATE')),
        'author' => null,
        'history_supported' => false,
        'entity_id_supported' => false,
    );
}

$method = (string) ($_SERVER['REQUEST_METHOD'] ?? 'GET');
if (!in_array($method, array('GET', 'POST'), true)) {
    header('Allow: GET, POST');
    commentError('method_not_allowed', 405);
}

$expectedToken = commentConfiguredToken();
$providedToken = commentBearerToken();
if ($expectedToken === '' || $providedToken === '' || !hash_equals($expectedToken, $providedToken)) {
    commentError('unauthorized', 401);
}

define('NO_KEEP_STATISTIC', true);
define('NOT_CHECK_PERMISSIONS', true);
define('BX_NO_ACCELERATOR_RESET', true);

require $_SERVER['DOCUMENT_ROOT'] . '/bitrix/modules/main/include/prolog_before.php';

if (!\Bitrix\Main\Loader::includeModule('sale')) {
    error_log('order-comments: sale module is unavailable');
    commentError('service_unavailable', 503);
}

$input = $_GET;
if ($method === 'POST') {
    $raw = file_get_contents('php://input');
    $decoded = json_decode(is_string($raw) ? $raw : '', true);
    $input = is_array($decoded) ? $decoded : $_POST;
}
$orderId = (string) ($input['order_id'] ?? '');
if (!preg_match('/^[1-9][0-9]*$/', $orderId)) {
    commentError('invalid_order_id', 400);
}

$order = \Bitrix\Sale\Order::load((int) $orderId);
if (!$order) {
    commentError('order_not_found', 404);
}

if ($method === 'GET') {
    commentResponse(array('comment' => commentPayload($order)));
}

$text = trim((string) ($input['text'] ?? ''));
if ($text === '') {
    commentError('empty_comment', 400);
}
if (mb_strlen($text) > 2000) {
    commentError('comment_too_long', 400);
}

$current = commentPayload($order);
$expectedHash = trim((string) ($input['expected_hash'] ?? ''));
if ($expectedHash !== '' && !hash_equals($expectedHash, (string) $current['hash'])) {
    commentError('comment_conflict', 409, array('current' => $current));
}
if (hash_equals(commentHash($text), (string) $current['hash'])) {
    commentResponse(array('comment' => $current, 'changed' => false));
}

$order->setField('COMMENTS', $text);
$saveResult = $order->save();
if (!$saveResult->isSuccess()) {
    error_log('order-comments: Bitrix order save failed order_id=' . $orderId);
    commentError('save_failed', 500);
}

$savedOrder = \Bitrix\Sale\Order::load((int) $orderId);
commentResponse(array(
    'comment' => commentPayload($savedOrder ?: $order),
    'changed' => true,
));
