<?php

declare(strict_types=1);

const CATALOG_EXPORT_API_VERSION = '1.0';
const CATALOG_EXPORT_IBLOCK_ID = 5;
const CATALOG_EXPORT_DEFAULT_LIMIT = 100;
const CATALOG_EXPORT_MAX_LIMIT = 200;
const CATALOG_EXPORT_BASE_URL = 'https://www.tictactoy.ru';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Pragma: no-cache');

function exportResponse(array $payload, int $status = 200): void
{
    http_response_code($status);
    echo json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ), "\n";
    exit;
}
function exportError(string $code, int $status): void
{
    exportResponse(array('error' => $code), $status);
}

function bearerToken(): string
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

function configuredToken(): string
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

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    header('Allow: GET');
    exportError('method_not_allowed', 405);
}

$expectedToken = configuredToken();
$providedToken = bearerToken();
if ($expectedToken === '' || $providedToken === '' || !hash_equals($expectedToken, $providedToken)) {
    exportError('unauthorized', 401);
}

define('NO_KEEP_STATISTIC', true);
define('NOT_CHECK_PERMISSIONS', true);
define('BX_NO_ACCELERATOR_RESET', true);

require $_SERVER['DOCUMENT_ROOT'] . '/bitrix/modules/main/include/prolog_before.php';

use Bitrix\Iblock\InheritedProperty\ElementValues;
use Bitrix\Main\FileTable;
use Bitrix\Main\Loader;

foreach (array('iblock', 'catalog', 'currency') as $requiredModule) {
    if (!Loader::includeModule($requiredModule)) {
        error_log('catalog-export: required Bitrix module is unavailable');
        exportError('service_unavailable', 503);
    }
}

function positiveIntegerParameter(string $name, int $default, int $maximum = PHP_INT_MAX): int
{
    if (!isset($_GET[$name]) || $_GET[$name] === '') {
        return $default;
    }
    $raw = (string) $_GET[$name];
    if (!preg_match('/^[0-9]+$/', $raw)) {
        exportError('invalid_' . $name, 400);
    }
    $value = (int) $raw;
    if ($value < 1 || $value > $maximum) {
        exportError('invalid_' . $name, 400);
    }
    return $value;
}

function booleanParameter(string $name): bool
{
    if (!isset($_GET[$name]) || $_GET[$name] === '') {
        return false;
    }
    if (!in_array((string) $_GET[$name], array('0', '1'), true)) {
        exportError('invalid_' . $name, 400);
    }
    return (string) $_GET[$name] === '1';
}

function updatedFromParameter(): ?DateTimeImmutable
{
    if (!isset($_GET['updated_from']) || $_GET['updated_from'] === '') {
        return null;
    }
    try {
        return new DateTimeImmutable((string) $_GET['updated_from']);
    } catch (Throwable $error) {
        exportError('invalid_updated_from', 400);
    }
    return null;
}

function isoDate($value): ?string
{
    if ($value instanceof DateTimeInterface) {
        return $value->format(DATE_ATOM);
    }
    $text = trim((string) $value);
    if ($text === '') {
        return null;
    }
    foreach (array('d.m.Y H:i:s', 'Y-m-d H:i:s', DATE_ATOM) as $format) {
        $date = DateTimeImmutable::createFromFormat($format, $text);
        if ($date instanceof DateTimeImmutable) {
            return $date->format(DATE_ATOM);
        }
    }
    try {
        return (new DateTimeImmutable($text))->format(DATE_ATOM);
    } catch (Throwable $error) {
        return null;
    }
}

function absoluteUrl(string $path): string
{
    if ($path === '') {
        return '';
    }
    if (preg_match('#^https?://#i', $path)) {
        return preg_replace('#^http://#i', 'https://', $path);
    }
    return CATALOG_EXPORT_BASE_URL . '/' . ltrim($path, '/');
}

function propertyType(array $property): string
{
    $type = (string) ($property['PROPERTY_TYPE'] ?? '');
    $userType = (string) ($property['USER_TYPE'] ?? '');
    if ($type === 'S' && strcasecmp($userType, 'DateTime') === 0) {
        return 'date';
    }
    if ($type === 'S' && strcasecmp($userType, 'HTML') === 0) {
        return 'html';
    }
    return array(
        'S' => 'string',
        'N' => 'number',
        'L' => 'list',
        'F' => 'file',
        'E' => 'element_link',
        'G' => 'section_link',
    )[$type] ?? 'unknown';
}

function propertyDefinitions(): array
{
    $result = array();
    $cursor = CIBlockProperty::GetList(
        array('SORT' => 'ASC', 'ID' => 'ASC'),
        array('IBLOCK_ID' => CATALOG_EXPORT_IBLOCK_ID, 'CHECK_PERMISSIONS' => 'N')
    );
    while ($property = $cursor->Fetch()) {
        $property['NORMALIZED_TYPE'] = propertyType($property);
        $result[(int) $property['ID']] = $property;
    }
    return $result;
}

function categoryMap(): array
{
    $sections = array();
    $cursor = CIBlockSection::GetList(
        array('LEFT_MARGIN' => 'ASC'),
        array('IBLOCK_ID' => CATALOG_EXPORT_IBLOCK_ID, 'CHECK_PERMISSIONS' => 'N'),
        false,
        array(
            'ID', 'XML_ID', 'CODE', 'NAME', 'IBLOCK_SECTION_ID', 'SORT', 'ACTIVE',
            'DEPTH_LEVEL', 'SECTION_PAGE_URL',
        )
    );
    while ($section = $cursor->Fetch()) {
        $sections[(int) $section['ID']] = $section;
    }
    foreach ($sections as $sectionId => $section) {
        $path = array();
        $current = $section;
        while ($current) {
            array_unshift($path, array(
                'id' => (string) $current['ID'],
                'name' => trim((string) $current['NAME']),
            ));
            $parentId = (int) ($current['IBLOCK_SECTION_ID'] ?? 0);
            $current = $parentId > 0 ? ($sections[$parentId] ?? null) : null;
        }
        $sections[$sectionId]['NORMALIZED'] = array(
            'id' => (string) $sectionId,
            'xml_id' => (string) ($section['XML_ID'] ?? ''),
            'code' => (string) $section['CODE'],
            'name' => trim((string) $section['NAME']),
            'parent_id' => (string) ($section['IBLOCK_SECTION_ID'] ?? ''),
            'sort' => (int) $section['SORT'],
            'active' => ($section['ACTIVE'] === 'Y'),
            'path' => $path,
        );
    }
    return $sections;
}

function fileMap(array $fileIds): array
{
    $fileIds = array_values(array_unique(array_filter(array_map('intval', $fileIds))));
    if ($fileIds === array()) {
        return array();
    }
    $result = array();
    $cursor = FileTable::getList(array(
        'filter' => array('@ID' => $fileIds),
        'select' => array(
            'ID', 'TIMESTAMP_X', 'MODULE_ID', 'HEIGHT', 'WIDTH', 'FILE_SIZE',
            'CONTENT_TYPE', 'SUBDIR', 'FILE_NAME', 'ORIGINAL_NAME',
        ),
    ));
    while ($file = $cursor->fetch()) {
        $path = CFile::GetFileSRC($file);
        $result[(int) $file['ID']] = array(
            'id' => (string) $file['ID'],
            'url' => absoluteUrl((string) $path),
            'filename' => (string) ($file['ORIGINAL_NAME'] ?: $file['FILE_NAME']),
            'mime_type' => (string) $file['CONTENT_TYPE'],
            'width' => (int) $file['WIDTH'],
            'height' => (int) $file['HEIGHT'],
            'file_size' => (int) $file['FILE_SIZE'],
        );
    }
    return $result;
}

function imageRecord(array $file, string $type, int $sort, bool $isPrimary): array
{
    return $file + array(
        'type' => $type,
        'sort' => $sort,
        'is_primary' => $isPrimary,
    );
}

function deduplicateImages(array $images): array
{
    $seenIds = array();
    $seenUrls = array();
    $result = array();
    foreach ($images as $image) {
        $id = (string) ($image['id'] ?? '');
        $url = strtolower(preg_replace('/[?#].*$/', '', (string) ($image['url'] ?? '')));
        if (($id !== '' && isset($seenIds[$id])) || ($url !== '' && isset($seenUrls[$url]))) {
            continue;
        }
        if ($id !== '') {
            $seenIds[$id] = true;
        }
        if ($url !== '') {
            $seenUrls[$url] = true;
        }
        $result[] = $image;
    }
    return $result;
}

function scalarValues($value): array
{
    if (is_array($value)) {
        return array_values($value);
    }
    return $value === null || $value === '' ? array() : array($value);
}

function linkedElementNames(array $ids): array
{
    $ids = array_values(array_unique(array_filter(array_map('intval', $ids))));
    if ($ids === array()) {
        return array();
    }
    $result = array();
    $cursor = CIBlockElement::GetList(
        array('ID' => 'ASC'),
        array('ID' => $ids, 'CHECK_PERMISSIONS' => 'N'),
        false,
        false,
        array('ID', 'NAME')
    );
    while ($row = $cursor->Fetch()) {
        $result[(int) $row['ID']] = (string) $row['NAME'];
    }
    return $result;
}

function linkedSectionNames(array $ids): array
{
    $ids = array_values(array_unique(array_filter(array_map('intval', $ids))));
    if ($ids === array()) {
        return array();
    }
    $result = array();
    $cursor = CIBlockSection::GetList(
        array('ID' => 'ASC'),
        array('ID' => $ids, 'CHECK_PERMISSIONS' => 'N'),
        false,
        array('ID', 'NAME')
    );
    while ($row = $cursor->Fetch()) {
        $result[(int) $row['ID']] = (string) $row['NAME'];
    }
    return $result;
}

function metaResponse(array $properties, array $categories): array
{
    $all = (int) CIBlockElement::GetList(
        array(),
        array('IBLOCK_ID' => CATALOG_EXPORT_IBLOCK_ID, 'CHECK_PERMISSIONS' => 'N'),
        array(),
        false
    );
    $active = (int) CIBlockElement::GetList(
        array(),
        array('IBLOCK_ID' => CATALOG_EXPORT_IBLOCK_ID, 'ACTIVE' => 'Y', 'CHECK_PERMISSIONS' => 'N'),
        array(),
        false
    );
    $priceTypes = array();
    $priceCursor = CCatalogGroup::GetList(array('SORT' => 'ASC', 'ID' => 'ASC'));
    while ($priceType = $priceCursor->Fetch()) {
        $priceTypes[] = array(
            'id' => (string) $priceType['ID'],
            'type' => (string) $priceType['NAME'],
            'name' => (string) ($priceType['NAME_LANG'] ?? $priceType['NAME']),
            'is_base' => ($priceType['BASE'] === 'Y'),
        );
    }
    $currencies = array();
    $currencyCursor = CCurrency::GetList($by = 'sort', $order = 'asc');
    while ($currency = $currencyCursor->Fetch()) {
        $currencies[] = (string) $currency['CURRENCY'];
    }
    $propertyList = array();
    foreach ($properties as $property) {
        $propertyList[] = array(
            'id' => (string) $property['ID'],
            'code' => (string) $property['CODE'],
            'name' => (string) $property['NAME'],
            'type' => (string) $property['NORMALIZED_TYPE'],
            'multiple' => ($property['MULTIPLE'] === 'Y'),
            'sort' => (int) $property['SORT'],
        );
    }
    return array(
        'api_version' => CATALOG_EXPORT_API_VERSION,
        'generated_at' => date(DATE_ATOM),
        'product_iblock_id' => (string) CATALOG_EXPORT_IBLOCK_ID,
        'offers_iblock_id' => null,
        'products_active' => $active,
        'products_inactive' => $all - $active,
        'products_total' => $all,
        'offers_total' => 0,
        'sections_total' => count($categories),
        'properties' => $propertyList,
        'price_types' => $priceTypes,
        'currencies' => $currencies,
        'updated_from_scope' => 'element_timestamp_only; periodic full export required for price-only changes',
    );
}

try {
    $properties = propertyDefinitions();
    $categories = categoryMap();
    if (isset($_GET['mode']) && (string) $_GET['mode'] !== 'meta') {
        exportError('invalid_mode', 400);
    }
    if (isset($_GET['mode']) && (string) $_GET['mode'] === 'meta') {
        exportResponse(metaResponse($properties, $categories));
    }

    $page = positiveIntegerParameter('page', 1);
    $limit = positiveIntegerParameter('limit', CATALOG_EXPORT_DEFAULT_LIMIT, CATALOG_EXPORT_MAX_LIMIT);
    $includeInactive = booleanParameter('include_inactive');
    $updatedFrom = updatedFromParameter();

    $filter = array(
        'IBLOCK_ID' => CATALOG_EXPORT_IBLOCK_ID,
        'CHECK_PERMISSIONS' => 'N',
    );
    if (!$includeInactive) {
        $filter['ACTIVE'] = 'Y';
    }
    if ($updatedFrom instanceof DateTimeImmutable) {
        $filter['>=TIMESTAMP_X'] = $updatedFrom->format('d.m.Y H:i:s');
    }

    $total = (int) CIBlockElement::GetList(array(), $filter, array(), false);
    $totalPages = $total > 0 ? (int) ceil($total / $limit) : 0;
    if ($totalPages > 0 && $page > $totalPages) {
        exportError('page_out_of_range', 400);
    }

    $rawProducts = array();
    $cursor = CIBlockElement::GetList(
        array('ID' => 'ASC'),
        $filter,
        false,
        array('iNumPage' => $page, 'nPageSize' => $limit),
        array(
            'ID', 'IBLOCK_ID', 'XML_ID', 'CODE', 'NAME', 'ACTIVE', 'SORT',
            'DATE_CREATE', 'TIMESTAMP_X', 'PREVIEW_TEXT', 'PREVIEW_TEXT_TYPE',
            'DETAIL_TEXT', 'DETAIL_TEXT_TYPE', 'PREVIEW_PICTURE', 'DETAIL_PICTURE',
            'DETAIL_PAGE_URL', 'IBLOCK_SECTION_ID',
        )
    );
    while ($row = $cursor->GetNext()) {
        $rawProducts[(int) $row['ID']] = $row;
    }
    $productIds = array_keys($rawProducts);

    $propertyValues = array();
    foreach ($productIds as $productId) {
        $propertyValues[$productId] = array();
    }
    if ($productIds !== array()) {
        CIBlockElement::GetPropertyValuesArray(
            $propertyValues,
            CATALOG_EXPORT_IBLOCK_ID,
            array('ID' => $productIds, 'CHECK_PERMISSIONS' => 'N')
        );
    }

    $categoryIdsByProduct = array_fill_keys($productIds, array());
    if ($productIds !== array()) {
        $groupCursor = CIBlockElement::GetElementGroups(
            $productIds,
            true,
            array('ID', 'IBLOCK_ELEMENT_ID')
        );
        while ($group = $groupCursor->Fetch()) {
            $productId = (int) ($group['IBLOCK_ELEMENT_ID'] ?? 0);
            if (isset($categoryIdsByProduct[$productId])) {
                $categoryIdsByProduct[$productId][] = (int) $group['ID'];
            }
        }
    }

    $catalogRows = array();
    if ($productIds !== array()) {
        $catalogCursor = CCatalogProduct::GetList(
            array('ID' => 'ASC'),
            array('ID' => $productIds),
            false,
            false,
            array(
                'ID', 'QUANTITY', 'QUANTITY_RESERVED', 'AVAILABLE', 'MEASURE',
                'WEIGHT', 'WIDTH', 'LENGTH', 'HEIGHT',
            )
        );
        while ($catalogRow = $catalogCursor->Fetch()) {
            $catalogRows[(int) $catalogRow['ID']] = $catalogRow;
        }
    }

    $measureNames = array();
    $measureCursor = CCatalogMeasure::getList(
        array(), array(), false, false, array('ID', 'MEASURE_TITLE', 'SYMBOL_RUS')
    );
    while ($measure = $measureCursor->Fetch()) {
        $measureNames[(int) $measure['ID']] = (string) ($measure['SYMBOL_RUS'] ?: $measure['MEASURE_TITLE']);
    }

    $pricesByProduct = array_fill_keys($productIds, array());
    if ($productIds !== array()) {
        $priceCursor = CPrice::GetList(
            array('CATALOG_GROUP_ID' => 'ASC', 'ID' => 'ASC'),
            array('PRODUCT_ID' => $productIds)
        );
        while ($price = $priceCursor->Fetch()) {
            $productId = (int) $price['PRODUCT_ID'];
            if (!isset($pricesByProduct[$productId])) {
                continue;
            }
            $pricesByProduct[$productId][] = array(
                'id' => (string) $price['ID'],
                'type' => (string) ($price['CATALOG_GROUP_NAME'] ?? $price['CATALOG_GROUP_ID']),
                'name' => (string) ($price['CATALOG_GROUP_NAME_LANG'] ?? 'Розничная цена'),
                'amount' => number_format((float) $price['PRICE'], 2, '.', ''),
                'currency' => (string) $price['CURRENCY'],
                'is_base' => ((string) ($price['CATALOG_GROUP_NAME'] ?? '') === 'BASE'
                    || (int) $price['CATALOG_GROUP_ID'] === 1),
                'old_amount' => null,
                'old_amount_source' => null,
            );
        }
    }

    $barcodesByProduct = array_fill_keys($productIds, array());
    if ($productIds !== array() && class_exists('CCatalogStoreBarCode')) {
        $barcodeCursor = CCatalogStoreBarCode::getList(
            array('ID' => 'ASC'),
            array('PRODUCT_ID' => $productIds),
            false,
            false,
            array('ID', 'PRODUCT_ID', 'BARCODE')
        );
        while ($barcode = $barcodeCursor->Fetch()) {
            $productId = (int) $barcode['PRODUCT_ID'];
            if (isset($barcodesByProduct[$productId])) {
                $barcodesByProduct[$productId][] = (string) $barcode['BARCODE'];
            }
        }
    }

    $fileIds = array();
    $elementLinkIds = array();
    $sectionLinkIds = array();
    foreach ($rawProducts as $productId => $rawProduct) {
        $fileIds[] = (int) $rawProduct['PREVIEW_PICTURE'];
        $fileIds[] = (int) $rawProduct['DETAIL_PICTURE'];
        foreach ($properties as $propertyId => $definition) {
            $valueRow = $propertyValues[$productId][(string) $definition['CODE']]
                ?? $propertyValues[$productId][$propertyId]
                ?? null;
            $values = scalarValues(is_array($valueRow) ? ($valueRow['VALUE'] ?? null) : null);
            if ($definition['PROPERTY_TYPE'] === 'F') {
                $fileIds = array_merge($fileIds, array_map('intval', $values));
            } elseif ($definition['PROPERTY_TYPE'] === 'E') {
                $elementLinkIds = array_merge($elementLinkIds, array_map('intval', $values));
            } elseif ($definition['PROPERTY_TYPE'] === 'G') {
                $sectionLinkIds = array_merge($sectionLinkIds, array_map('intval', $values));
            }
        }
    }
    $files = fileMap($fileIds);
    $elementNames = linkedElementNames($elementLinkIds);
    $sectionNames = linkedSectionNames($sectionLinkIds);

    $products = array();
    foreach ($rawProducts as $productId => $rawProduct) {
        $normalizedProperties = array();
        $images = array();
        $imageSort = 0;
        foreach (array('PREVIEW_PICTURE' => 'preview', 'DETAIL_PICTURE' => 'detail') as $field => $type) {
            $fileId = (int) $rawProduct[$field];
            if ($fileId > 0 && isset($files[$fileId])) {
                $images[] = imageRecord($files[$fileId], $type, $imageSort++, $type === 'detail');
            }
        }

        $brand = '';
        foreach ($properties as $propertyId => $definition) {
            $valueRow = $propertyValues[$productId][(string) $definition['CODE']]
                ?? $propertyValues[$productId][$propertyId]
                ?? array();
            $rawValue = is_array($valueRow) ? ($valueRow['VALUE'] ?? null) : null;
            $values = scalarValues($rawValue);
            $displayRaw = is_array($valueRow) ? ($valueRow['VALUE_ENUM'] ?? $rawValue) : $rawValue;
            $displayValues = scalarValues($displayRaw);
            $enumIds = scalarValues(is_array($valueRow) ? ($valueRow['VALUE_ENUM_ID'] ?? null) : null);

            if ($definition['PROPERTY_TYPE'] === 'F') {
                $fileValues = array();
                foreach ($values as $position => $value) {
                    $fileId = (int) $value;
                    if (!isset($files[$fileId])) {
                        continue;
                    }
                    $fileValue = imageRecord(
                        $files[$fileId],
                        strtolower((string) $definition['CODE']) === 'gallery' ? 'gallery' : 'property_file',
                        $imageSort++,
                        false
                    );
                    $fileValues[] = $fileValue;
                    $images[] = $fileValue;
                }
                $values = $fileValues;
                $displayValues = $fileValues;
            } elseif ($definition['PROPERTY_TYPE'] === 'E') {
                $displayValues = array_map(
                    static function ($value) use ($elementNames) {
                        return $elementNames[(int) $value] ?? (string) $value;
                    },
                    $values
                );
            } elseif ($definition['PROPERTY_TYPE'] === 'G') {
                $displayValues = array_map(
                    static function ($value) use ($sectionNames) {
                        return $sectionNames[(int) $value] ?? (string) $value;
                    },
                    $values
                );
            }

            $multiple = ($definition['MULTIPLE'] === 'Y');
            $propertyValue = $multiple ? $values : ($values[0] ?? null);
            $displayValue = $multiple ? $displayValues : ($displayValues[0] ?? null);
            $enumId = $multiple ? $enumIds : ($enumIds[0] ?? null);
            $normalizedProperties[] = array(
                'id' => (string) $propertyId,
                'code' => (string) $definition['CODE'],
                'name' => (string) $definition['NAME'],
                'type' => (string) $definition['NORMALIZED_TYPE'],
                'multiple' => $multiple,
                'value' => $propertyValue,
                'display_value' => $displayValue,
                'enum_id' => $enumId,
                'sort' => (int) $definition['SORT'],
            );
            if ((string) $definition['CODE'] === 'BRAND_MODEL') {
                $brand = trim((string) ($displayValue ?? ''));
            }
        }

        $productCategories = array();
        foreach (array_unique($categoryIdsByProduct[$productId] ?? array()) as $categoryId) {
            if (isset($categories[$categoryId]['NORMALIZED'])) {
                $productCategories[] = $categories[$categoryId]['NORMALIZED'];
            }
        }
        $primaryCategoryId = (int) ($rawProduct['IBLOCK_SECTION_ID'] ?? 0);
        if ($productCategories === array() && isset($categories[$primaryCategoryId]['NORMALIZED'])) {
            $productCategories[] = $categories[$primaryCategoryId]['NORMALIZED'];
        }

        $seo = array();
        try {
            $seo = (new ElementValues(CATALOG_EXPORT_IBLOCK_ID, $productId))->getValues();
        } catch (Throwable $error) {
            error_log('catalog-export: inherited SEO lookup failed for product ' . $productId);
        }
        $catalogRow = $catalogRows[$productId] ?? array();
        $barcodes = $barcodesByProduct[$productId] ?? array();
        $products[] = array(
            'id' => (string) $productId,
            'iblock_id' => (string) CATALOG_EXPORT_IBLOCK_ID,
            'xml_id' => (string) $rawProduct['XML_ID'],
            'code' => (string) $rawProduct['CODE'],
            'name' => (string) $rawProduct['NAME'],
            'active' => ($rawProduct['ACTIVE'] === 'Y'),
            'sort' => (int) $rawProduct['SORT'],
            'created_at' => isoDate($rawProduct['DATE_CREATE']),
            'updated_at' => isoDate($rawProduct['TIMESTAMP_X']),
            'preview_text' => (string) $rawProduct['PREVIEW_TEXT'],
            'preview_text_type' => strtolower((string) $rawProduct['PREVIEW_TEXT_TYPE']),
            'detail_text' => (string) $rawProduct['DETAIL_TEXT'],
            'detail_text_type' => strtolower((string) $rawProduct['DETAIL_TEXT_TYPE']),
            'article' => '',
            'barcode' => (string) ($barcodes[0] ?? ''),
            'barcodes' => $barcodes,
            'brand' => $brand,
            'unit' => (string) ($measureNames[(int) ($catalogRow['MEASURE'] ?? 0)] ?? ''),
            'available_quantity' => isset($catalogRow['QUANTITY']) ? (float) $catalogRow['QUANTITY'] : null,
            'reserved_quantity' => isset($catalogRow['QUANTITY_RESERVED']) ? (float) $catalogRow['QUANTITY_RESERVED'] : null,
            'available' => (($catalogRow['AVAILABLE'] ?? 'N') === 'Y'),
            'source_url' => absoluteUrl((string) $rawProduct['DETAIL_PAGE_URL']),
            'primary_category_id' => $primaryCategoryId > 0 ? (string) $primaryCategoryId : null,
            'categories' => $productCategories,
            'properties' => $normalizedProperties,
            'images' => deduplicateImages($images),
            'prices' => $pricesByProduct[$productId] ?? array(),
            'offers' => array(),
            'seo' => $seo,
        );
    }

    $hasMore = $totalPages > 0 && $page < $totalPages;
    exportResponse(array(
        'api_version' => CATALOG_EXPORT_API_VERSION,
        'generated_at' => date(DATE_ATOM),
        'page' => $page,
        'limit' => $limit,
        'total' => $total,
        'total_pages' => $totalPages,
        'has_more' => $hasMore,
        'next_page' => $hasMore ? $page + 1 : null,
        'products' => $products,
    ));
} catch (Throwable $error) {
    error_log('catalog-export: ' . get_class($error) . ' at export processing stage');
    exportError('internal_error', 500);
}
