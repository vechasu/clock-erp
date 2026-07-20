<?php

declare(strict_types=1);

/**
 * Read-only Bitrix catalog structure diagnostic.
 *
 * Usage on the Bitrix server:
 *   BITRIX_DOCUMENT_ROOT=/path/to/site php bitrix_catalog_server_diagnostic.php
 */

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "This script may only be run from CLI.\n");
    exit(2);
}

$documentRoot = getenv('BITRIX_DOCUMENT_ROOT');
if (!is_string($documentRoot) || $documentRoot === '') {
    fwrite(STDERR, "BITRIX_DOCUMENT_ROOT is required.\n");
    exit(2);
}

$documentRoot = rtrim($documentRoot, '/');
$prolog = $documentRoot . '/bitrix/modules/main/include/prolog_before.php';
if (!is_file($prolog)) {
    fwrite(STDERR, "Bitrix prolog was not found.\n");
    exit(2);
}

$_SERVER['DOCUMENT_ROOT'] = $documentRoot;
define('NO_KEEP_STATISTIC', true);
define('NOT_CHECK_PERMISSIONS', true);
define('BX_NO_ACCELERATOR_RESET', true);

require $prolog;

use Bitrix\Main\Loader;

foreach (array('iblock', 'catalog', 'currency') as $module) {
    if (!Loader::includeModule($module)) {
        fwrite(STDERR, "Required Bitrix module is unavailable: {$module}\n");
        exit(3);
    }
}

function scalarCount($value): int
{
    return is_numeric($value) ? (int) $value : 0;
}

function elementCount(int $iblockId, ?string $active = null): int
{
    $filter = array('IBLOCK_ID' => $iblockId, 'CHECK_PERMISSIONS' => 'N');
    if ($active !== null) {
        $filter['ACTIVE'] = $active;
    }
    return scalarCount(CIBlockElement::GetList(array(), $filter, array(), false));
}

function sectionCount(int $iblockId): int
{
    return scalarCount(CIBlockSection::GetCount(array(
        'IBLOCK_ID' => $iblockId,
        'CHECK_PERMISSIONS' => 'N',
    )));
}

function propertyTypeLabel(array $property): string
{
    $type = (string) ($property['PROPERTY_TYPE'] ?? '');
    $userType = (string) ($property['USER_TYPE'] ?? '');
    $labels = array(
        'S' => 'string',
        'N' => 'number',
        'L' => 'list',
        'F' => 'file',
        'E' => 'element_link',
        'G' => 'section_link',
    );
    if ($userType !== '') {
        return ($labels[$type] ?? $type) . ':' . $userType;
    }
    return $labels[$type] ?? $type;
}

$result = array(
    'generated_at' => date(DATE_ATOM),
    'php_version' => PHP_VERSION,
    'bitrix_version' => defined('SM_VERSION') ? SM_VERSION : null,
    'iblocks' => array(),
    'catalogs' => array(),
    'price_types' => array(),
    'currencies' => array(),
    'measures' => array(),
    'catalog_storage' => array(),
);

$catalogByIblock = array();
$catalogCursor = CCatalog::GetList(array('IBLOCK_ID' => 'ASC'));
while ($catalog = $catalogCursor->Fetch()) {
    $iblockId = (int) $catalog['IBLOCK_ID'];
    $catalogByIblock[$iblockId] = $catalog;
    $result['catalogs'][] = array(
        'iblock_id' => (string) $iblockId,
        'product_iblock_id' => (string) ($catalog['PRODUCT_IBLOCK_ID'] ?? ''),
        'sku_property_id' => (string) ($catalog['SKU_PROPERTY_ID'] ?? ''),
    );
}

$iblockCursor = CIBlock::GetList(array('ID' => 'ASC'), array('CHECK_PERMISSIONS' => 'N'));
while ($iblock = $iblockCursor->Fetch()) {
    $iblockId = (int) $iblock['ID'];
    $properties = array();
    $propertyIds = array();
    $propertyCursor = CIBlockProperty::GetList(
        array('SORT' => 'ASC', 'ID' => 'ASC'),
        array('IBLOCK_ID' => $iblockId, 'CHECK_PERMISSIONS' => 'N')
    );
    while ($property = $propertyCursor->Fetch()) {
        $properties[] = array(
            'id' => (string) $property['ID'],
            'code' => (string) $property['CODE'],
            'name' => (string) $property['NAME'],
            'type' => propertyTypeLabel($property),
            'property_type' => (string) $property['PROPERTY_TYPE'],
            'user_type' => (string) ($property['USER_TYPE'] ?? ''),
            'multiple' => ($property['MULTIPLE'] === 'Y'),
            'required' => ($property['IS_REQUIRED'] === 'Y'),
            'sort' => (int) $property['SORT'],
            'linked_iblock_id' => (string) ($property['LINK_IBLOCK_ID'] ?? ''),
        );
        $propertyIds[] = (int) $property['ID'];
    }

    $sectionDepths = array();
    $sections = array();
    $sectionCursor = CIBlockSection::GetList(
        array('LEFT_MARGIN' => 'ASC'),
        array('IBLOCK_ID' => $iblockId, 'CHECK_PERMISSIONS' => 'N'),
        false,
        array(
            'ID', 'XML_ID', 'CODE', 'NAME', 'IBLOCK_SECTION_ID', 'DEPTH_LEVEL',
            'SORT', 'ACTIVE', 'SECTION_PAGE_URL', 'LEFT_MARGIN', 'RIGHT_MARGIN',
        )
    );
    while ($section = $sectionCursor->Fetch()) {
        $depth = (string) ((int) $section['DEPTH_LEVEL']);
        $sectionDepths[$depth] = ($sectionDepths[$depth] ?? 0) + 1;
        $path = array();
        $pathCursor = CIBlockSection::GetNavChain(
            $iblockId,
            (int) $section['ID'],
            array('ID', 'NAME')
        );
        while ($pathPart = $pathCursor->Fetch()) {
            $path[] = array(
                'id' => (string) $pathPart['ID'],
                'name' => (string) $pathPart['NAME'],
            );
        }
        $sections[] = array(
            'id' => (string) $section['ID'],
            'xml_id' => (string) ($section['XML_ID'] ?? ''),
            'code' => (string) $section['CODE'],
            'name' => (string) $section['NAME'],
            'parent_id' => (string) ($section['IBLOCK_SECTION_ID'] ?? ''),
            'depth' => (int) $section['DEPTH_LEVEL'],
            'sort' => (int) $section['SORT'],
            'active' => ($section['ACTIVE'] === 'Y'),
            'path' => $path,
        );
    }

    $fieldCoverage = array(
        'xml_id' => 0,
        'code' => 0,
        'preview_text' => 0,
        'detail_text' => 0,
        'preview_picture' => 0,
        'detail_picture' => 0,
    );
    if (isset($catalogByIblock[$iblockId])) {
        $elementCursor = CIBlockElement::GetList(
            array('ID' => 'ASC'),
            array('IBLOCK_ID' => $iblockId, 'CHECK_PERMISSIONS' => 'N'),
            false,
            false,
            array(
                'ID', 'XML_ID', 'CODE', 'PREVIEW_TEXT', 'DETAIL_TEXT',
                'PREVIEW_PICTURE', 'DETAIL_PICTURE',
            )
        );
        while ($element = $elementCursor->Fetch()) {
            foreach (array(
                'XML_ID' => 'xml_id',
                'CODE' => 'code',
                'PREVIEW_TEXT' => 'preview_text',
                'DETAIL_TEXT' => 'detail_text',
                'PREVIEW_PICTURE' => 'preview_picture',
                'DETAIL_PICTURE' => 'detail_picture',
            ) as $sourceField => $coverageField) {
                if (($element[$sourceField] ?? '') !== '') {
                    $fieldCoverage[$coverageField]++;
                }
            }
        }
    }

    $propertyCoverage = array();
    foreach ($propertyIds as $propertyId) {
        $propertyCoverage[(string) $propertyId] = array(
            'elements_with_value' => 0,
            'values_total' => 0,
        );
    }
    if (isset($catalogByIblock[$iblockId]) && $propertyIds !== array()) {
        $valueCursor = CIBlockElement::GetPropertyValues(
            $iblockId,
            array('CHECK_PERMISSIONS' => 'N'),
            false,
            array('ID' => $propertyIds)
        );
        while ($values = $valueCursor->Fetch()) {
            foreach ($propertyIds as $propertyId) {
                $value = $values[$propertyId] ?? null;
                $nonEmptyValues = array();
                foreach (is_array($value) ? $value : array($value) as $singleValue) {
                    if ($singleValue !== null && $singleValue !== '' && $singleValue !== false) {
                        $nonEmptyValues[] = $singleValue;
                    }
                }
                if ($nonEmptyValues !== array()) {
                    $propertyCoverage[(string) $propertyId]['elements_with_value']++;
                    $propertyCoverage[(string) $propertyId]['values_total'] += count($nonEmptyValues);
                }
            }
        }
    }

    $skuInfo = CCatalogSKU::GetInfoByProductIBlock($iblockId);
    $offerInfo = CCatalogSKU::GetInfoByOfferIBlock($iblockId);
    $result['iblocks'][] = array(
        'id' => (string) $iblockId,
        'type' => (string) $iblock['IBLOCK_TYPE_ID'],
        'code' => (string) $iblock['CODE'],
        'name' => (string) $iblock['NAME'],
        'active' => ($iblock['ACTIVE'] === 'Y'),
        'is_catalog' => isset($catalogByIblock[$iblockId]),
        'elements_total' => elementCount($iblockId),
        'elements_active' => elementCount($iblockId, 'Y'),
        'elements_inactive' => elementCount($iblockId, 'N'),
        'sections_total' => sectionCount($iblockId),
        'section_depth_counts' => $sectionDepths,
        'sections' => $sections,
        'field_coverage' => $fieldCoverage,
        'property_coverage' => $propertyCoverage,
        'sku_info_for_product_iblock' => is_array($skuInfo) ? array(
            'product_iblock_id' => (string) ($skuInfo['PRODUCT_IBLOCK_ID'] ?? $iblockId),
            'iblock_id' => (string) ($skuInfo['IBLOCK_ID'] ?? ''),
            'sku_property_id' => (string) ($skuInfo['SKU_PROPERTY_ID'] ?? ''),
        ) : null,
        'sku_info_for_offer_iblock' => is_array($offerInfo) ? array(
            'product_iblock_id' => (string) ($offerInfo['PRODUCT_IBLOCK_ID'] ?? ''),
            'iblock_id' => (string) ($offerInfo['IBLOCK_ID'] ?? $iblockId),
            'sku_property_id' => (string) ($offerInfo['SKU_PROPERTY_ID'] ?? ''),
        ) : null,
        'properties' => $properties,
    );
}

$priceCursor = CCatalogGroup::GetList(array('SORT' => 'ASC', 'ID' => 'ASC'));
while ($priceType = $priceCursor->Fetch()) {
    $result['price_types'][] = array(
        'id' => (string) $priceType['ID'],
        'name' => (string) $priceType['NAME'],
        'name_lang' => (string) ($priceType['NAME_LANG'] ?? ''),
        'base' => ($priceType['BASE'] === 'Y'),
        'sort' => (int) $priceType['SORT'],
    );
}

$currencyCursor = CCurrency::GetList($currencyBy = 'sort', $currencyOrder = 'asc');
while ($currency = $currencyCursor->Fetch()) {
    $result['currencies'][] = array(
        'code' => (string) $currency['CURRENCY'],
        'base' => ((string) ($currency['BASE'] ?? 'N') === 'Y'),
        'sort' => (int) $currency['SORT'],
    );
}

if (class_exists('CCatalogMeasure')) {
    $measureCursor = CCatalogMeasure::getList(array(), array(), false, false, array(
        'ID', 'CODE', 'MEASURE_TITLE', 'SYMBOL_RUS', 'IS_DEFAULT',
    ));
    while ($measure = $measureCursor->Fetch()) {
        $result['measures'][] = array(
            'id' => (string) $measure['ID'],
            'code' => (string) $measure['CODE'],
            'title' => (string) $measure['MEASURE_TITLE'],
            'symbol' => (string) $measure['SYMBOL_RUS'],
            'default' => ($measure['IS_DEFAULT'] === 'Y'),
        );
    }
}

$catalogElementOwners = array();
foreach (array_keys($catalogByIblock) as $catalogIblockId) {
    $catalogElementOwners[(string) $catalogIblockId] = array();
    $catalogElementCursor = CIBlockElement::GetList(
        array('ID' => 'ASC'),
        array('IBLOCK_ID' => $catalogIblockId, 'CHECK_PERMISSIONS' => 'N'),
        false,
        false,
        array('ID')
    );
    while ($catalogElement = $catalogElementCursor->Fetch()) {
        $catalogElementOwners[(string) $catalogIblockId][(string) $catalogElement['ID']] = true;
    }
    $result['catalog_storage'][(string) $catalogIblockId] = array(
        'catalog_product_rows' => 0,
        'with_quantity' => 0,
        'with_reserved_quantity' => 0,
        'with_weight' => 0,
        'with_dimensions' => 0,
        'with_measure' => 0,
        'barcode_rows' => 0,
        'products_with_barcode' => 0,
    );
}

$catalogProductCursor = CCatalogProduct::GetList(
    array('ID' => 'ASC'),
    array(),
    false,
    false,
    array(
        'ID', 'QUANTITY', 'QUANTITY_RESERVED', 'WEIGHT', 'WIDTH', 'LENGTH',
        'HEIGHT', 'MEASURE', 'AVAILABLE',
    )
);
while ($catalogProduct = $catalogProductCursor->Fetch()) {
    $productId = (string) $catalogProduct['ID'];
    foreach ($catalogElementOwners as $catalogIblockId => $elementIds) {
        if (!isset($elementIds[$productId])) {
            continue;
        }
        $storage =& $result['catalog_storage'][$catalogIblockId];
        $storage['catalog_product_rows']++;
        if ((float) $catalogProduct['QUANTITY'] !== 0.0) {
            $storage['with_quantity']++;
        }
        if ((float) $catalogProduct['QUANTITY_RESERVED'] !== 0.0) {
            $storage['with_reserved_quantity']++;
        }
        if ((float) $catalogProduct['WEIGHT'] !== 0.0) {
            $storage['with_weight']++;
        }
        if (
            (float) $catalogProduct['WIDTH'] !== 0.0
            || (float) $catalogProduct['LENGTH'] !== 0.0
            || (float) $catalogProduct['HEIGHT'] !== 0.0
        ) {
            $storage['with_dimensions']++;
        }
        if ((int) $catalogProduct['MEASURE'] !== 0) {
            $storage['with_measure']++;
        }
        unset($storage);
        break;
    }
}

if (class_exists('CCatalogStoreBarCode')) {
    $productsWithBarcode = array();
    $barcodeCursor = CCatalogStoreBarCode::getList(
        array('ID' => 'ASC'),
        array(),
        false,
        false,
        array('ID', 'PRODUCT_ID')
    );
    while ($barcode = $barcodeCursor->Fetch()) {
        $productId = (string) $barcode['PRODUCT_ID'];
        foreach ($catalogElementOwners as $catalogIblockId => $elementIds) {
            if (!isset($elementIds[$productId])) {
                continue;
            }
            $result['catalog_storage'][$catalogIblockId]['barcode_rows']++;
            $productsWithBarcode[$catalogIblockId][$productId] = true;
            break;
        }
    }
    foreach ($productsWithBarcode as $catalogIblockId => $productIds) {
        $result['catalog_storage'][$catalogIblockId]['products_with_barcode'] = count($productIds);
    }
}

echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), "\n";
