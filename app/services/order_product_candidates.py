"""Bounded catalog candidate lookup shared by order lists and cards."""


def _text(value):
    return str(value or '').strip()


def _lower(value):
    # Match SQLite's built-in lower(), including on the production 3.7 runtime.
    return _text(value).translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))


def batch_order_candidates(connection, products, identities, saved_rows):
    attempts = []
    values = {'article': set(), 'barcode': set(), 'nm': set(), 'bitrix': set(), 'xml': set()}
    for product, identity, saved in zip(products, identities, saved_rows):
        rules = []
        if not (isinstance(saved, dict) and saved.get('product_id')):
            if identity['source'] == 'wildberries':
                article = _text(product.get('article') or product.get('supplierArticle') or product.get('vendorCode'))
                if article:
                    rules.append(('article', _lower(article), 'vendor_code'))
                for barcode in dict.fromkeys([product.get('barcode'), product.get('sku')] + list(product.get('skus') or [])):
                    if _text(barcode):
                        rules.append(('barcode', _lower(barcode), 'barcode'))
                nm = product.get('nm_id') or product.get('nmId') or product.get('nmID')
                if nm:
                    rules.append(('nm', _text(nm), 'nm_id'))
            else:
                if identity['bitrix_product_id']:
                    rules.append(('bitrix', identity['bitrix_product_id'], 'bitrix_product_id'))
                if identity['bitrix_xml_id']:
                    rules.append(('xml', _lower(identity['bitrix_xml_id']), 'bitrix_product_id'))
        attempts.append(rules)
        for kind, value, _method in rules:
            values[kind].add(value)
    expressions = {
        'article': "lower(trim(COALESCE(p.excel_article,'')))",
        'barcode': "lower(trim(COALESCE(cp.barcode,'')))",
        'nm': "CASE WHEN lower(trim(COALESCE(cp.external_source,'')))='wildberries' THEN trim(COALESCE(cp.external_product_id,'')) ELSE '' END",
        'bitrix': "trim(COALESCE(p.bitrix_external_product_id,''))",
        'xml': "lower(trim(COALESCE(p.bitrix_xml_id,'')))",
    }
    candidates = {}
    # Chunking is bounded by SQLite's 999-variable limit, not one query per item.
    pairs = [(kind, value) for kind in values for value in sorted(values[kind])]
    for offset in range(0, len(pairs), 400):
        chunk = pairs[offset:offset + 400]
        clauses, params = [], []
        for kind in values:
            subset = [value for field, value in chunk if field == kind]
            if not subset:
                continue
            placeholders = ','.join('?' for _ in subset)
            clauses.append(expressions[kind] + ' IN (' + placeholders + ')')
            params.extend(subset)
            if kind == 'bitrix':
                clauses.append('CAST(p.bitrix_catalog_product_id AS TEXT) IN (' + placeholders + ')')
                params.extend(subset)
        rows = connection.execute(
            'SELECT p.id, CAST(p.bitrix_catalog_product_id AS TEXT) AS catalog_id, ' +
            ', '.join(expression + ' AS ' + key for key, expression in expressions.items()) +
            ' FROM catalog_excel_products p LEFT JOIN catalog_products cp ON cp.id=p.bitrix_catalog_product_id '
            'WHERE p.deleted_at IS NULL AND (' + ' OR '.join(clauses) + ') ORDER BY p.id', params,
        ).fetchall()
        for row in rows:
            for kind in values:
                for value in ([row[kind], row['catalog_id']] if kind == 'bitrix' else [row[kind]]):
                    if value in values[kind]:
                        found = candidates.setdefault((kind, value), set())
                        found.add(row['id'])
    result = []
    for rules in attempts:
        found, method = set(), ''
        for kind, value, match_method in rules:
            ids = sorted(candidates.get((kind, value), ()))[:2]
            if ids and not method:
                method = match_method
            found.update(ids)
        result.append((next(iter(found)), method) if len(found) == 1 else (None, ''))
    return result
