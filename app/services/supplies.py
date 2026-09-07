"""Local supplies backed by the existing receipt documents and stock ledger.

No remote calls. JSON metadata extends existing documents without schema changes.
All mutations share CatalogDatabase.transaction (BEGIN IMMEDIATE) with sales.
"""
import json
import math
import uuid

from app.catalog_db import CatalogDatabase
from app.services.receipt_inventory import ReceiptInventory, ReceiptInventoryError, utc_now
from app.services.bitrix_erp_product_sync import BitrixERPProductSync, enrichment_from_product
from app.services.excel_receipt_import import ExcelReceiptImportService, _load_bitrix_products
from app.services.product_reconciliation import ProductReconciler


class SupplyError(ReceiptInventoryError):
    pass


class SupplyEngine:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    @staticmethod
    def _metadata(row):
        metadata = json.loads(row['metadata_json'] or '{}')
        if not isinstance(metadata, dict):
            raise SupplyError('Метаданные документа повреждены. Требуется проверка истории.')
        return metadata

    @staticmethod
    def _row(connection, supply_id, draft=False):
        row = connection.execute('SELECT * FROM erp_receipts WHERE id = ?', (supply_id,)).fetchone()
        if row is None or SupplyEngine._metadata(row).get('source_type') != 'supply':
            raise SupplyError('Поставка не найдена.')
        if draft and row['status'] != 'draft':
            raise SupplyError('Изменять или удалять можно только черновик поставки.')
        return row

    def create(self, title, comment='', actor='', items=None, key=None):
        title = str(title or '').strip()
        comment = str(comment or '').strip()
        if not title or len(title) > 200 or len(comment) > 2000:
            raise SupplyError('Укажите название до 200 символов и комментарий до 2000 символов.')
        now = utc_now()
        with self.database.transaction() as connection:
            if key:
                previous = connection.execute('SELECT id FROM erp_receipts WHERE tenant_id = ? AND idempotency_key = ?', ('default', 'supply:' + key)).fetchone()
                if previous:
                    return self._get(connection, previous['id'])
            supply_id = 'supply:' + uuid.uuid4().hex
            metadata = {'source_type': 'supply', 'title': title, 'created_by': actor,
                        'created_at': now, 'posted_by': None, 'posted_at': None}
            ReceiptInventory._insert_draft(connection, metadata, [], supply_id,
                'supply:' + key if key else None, actor, 'default', now)
            connection.execute('UPDATE erp_receipts SET number = ?, comment = ? WHERE id = ?',
                ('П-' + supply_id.split(':')[1][:12].upper(), comment, supply_id))
            if items is not None:
                self._replace(connection, supply_id, items, now)
            return self._get(connection, supply_id)

    @staticmethod
    def _positions(items):
        if not isinstance(items, list):
            raise SupplyError('Некорректный список товаров.')
        seen = set()
        for item in items:
            if not isinstance(item, dict) or type(item.get('product_id')) is not int:
                raise SupplyError('Некорректная ссылка на товар.')
            if type(item.get('quantity')) is not int or item['quantity'] <= 0 or item['quantity'] > 2147483647:
                raise SupplyError('Количество должно быть целым положительным числом.')
            if item['product_id'] in seen:
                raise SupplyError('Товар уже есть в поставке. Измените количество существующей позиции.')
            seen.add(item['product_id'])
        return ReceiptInventory._prepare_positions(items) if items else []

    def _replace(self, connection, supply_id, items, now):
        prepared = self._positions(items)
        for item in prepared:
            row = connection.execute('SELECT bitrix_external_product_id, bitrix_catalog_product_id FROM catalog_excel_products WHERE id = ? AND active = 1 AND deleted_at IS NULL', (item['product_id'],)).fetchone()
            if row is None or not (row['bitrix_external_product_id'] or row['bitrix_catalog_product_id']):
                raise SupplyError('Выберите товар, связанный с Bitrix.')
        products = ReceiptInventory._load_products(connection, prepared) if prepared else {}
        connection.execute('UPDATE erp_receipt_items SET active = 0 WHERE receipt_id = ?', (supply_id,))
        ReceiptInventory._insert_items(connection, supply_id, prepared, products, now)

    def update(self, supply_id, title, comment, items):
        title = str(title or '').strip()
        comment = str(comment or '').strip()
        if not title or len(title) > 200 or len(comment) > 2000:
            raise SupplyError('Проверьте название и комментарий поставки.')
        with self.database.transaction() as connection:
            row = self._row(connection, supply_id, draft=True)
            meta = self._metadata(row)
            meta['title'] = title
            self._replace(connection, supply_id, items, utc_now())
            connection.execute('UPDATE erp_receipts SET comment = ?, metadata_json = ?, updated_at = ? WHERE id = ?', (comment, json.dumps(meta, ensure_ascii=False), utc_now(), supply_id))
            return self._get(connection, supply_id)

    def delete(self, supply_id):
        with self.database.transaction() as connection:
            self._row(connection, supply_id, draft=True)
            # Keep references/audit, hide the deleted draft. No stock operation.
            connection.execute("UPDATE erp_receipts SET status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE id = ?", (utc_now(), utc_now(), supply_id))

    def post(self, supply_id, actor='', failure_hook=None):
        with self.database.transaction() as connection:
            row = self._row(connection, supply_id)
            if row['status'] == 'posted':
                return self._get(connection, supply_id)
            self._row(connection, supply_id, draft=True)
            items = [dict(r) for r in connection.execute('SELECT product_id, quantity FROM erp_receipt_items WHERE receipt_id = ? AND active = 1', (supply_id,))]
            for item in items:
                value = item['quantity']
                if value != int(value):
                    raise SupplyError('Количество должно быть целым положительным числом.')
                item['quantity'] = int(value)
            self._positions(items)
            for item in items:
                stock = self._product(connection, item['product_id'])['stock']
                if not math.isfinite(stock) or stock < 0:
                    raise SupplyError('Остаток товара некорректен. Проведение остановлено.')
            now = utc_now()
            ReceiptInventory._post_draft(connection, supply_id, actor, failure_hook, now)
            meta = self._metadata(row)
            meta.update(posted_at=now, posted_by=actor)
            # Preserve historical card labels as well as actual before/after values.
            meta['snapshots'] = {str(i['product_id']): self._product(connection, i['product_id']) for i in items}
            connection.execute('UPDATE erp_receipts SET metadata_json = ? WHERE id = ?', (json.dumps(meta, ensure_ascii=False), supply_id))
            connection.execute("UPDATE catalog_stock_movements SET source_type = 'supply', source = 'Поставка', comment = ? WHERE receipt_id = ? AND operation_kind = 'post'", (row['comment'], supply_id))
            return self._get(connection, supply_id)

    @staticmethod
    def _product(connection, product_id):
        row = connection.execute("SELECT p.id, p.excel_name_raw AS name, COALESCE(p.excel_article, '') AS article, COALESCE(b.name, p.excel_brand, '') AS brand, COALESCE(c.name, p.excel_category, '') AS category, COALESCE(p.bitrix_thumbnail_url, p.bitrix_primary_image_url, '') AS image_url, p.stock FROM catalog_excel_products p LEFT JOIN erp_brands b ON b.id = p.brand_id LEFT JOIN erp_categories c ON c.id = p.category_id WHERE p.id = ?", (product_id,)).fetchone()
        if row is None:
            raise SupplyError('Товар не найден.')
        return dict(row)

    def _get(self, connection, supply_id):
        row = self._row(connection, supply_id)
        result = dict(row)
        meta = self._metadata(row)
        result.update(meta)
        result.pop('metadata_json', None)
        result['items'] = []
        for item in connection.execute('SELECT * FROM erp_receipt_items WHERE receipt_id = ? AND active = 1 ORDER BY id', (supply_id,)):
            product = (meta.get('snapshots') or {}).get(str(item['product_id'])) or self._product(connection, item['product_id'])
            movement = connection.execute("SELECT stock_before, stock_after FROM catalog_stock_movements WHERE receipt_item_id = ? AND operation_kind = 'post'", (item['id'],)).fetchone()
            before = movement['stock_before'] if movement else product['stock']
            after = movement['stock_after'] if movement else before + item['quantity']
            result['items'].append(dict(product, **dict(item), stock_before=before, stock_after=after))
        result['position_count'] = len(result['items'])
        result['total_quantity'] = sum(i['quantity'] for i in result['items'])
        return result

    def get(self, supply_id):
        with self.database.connect() as connection:
            return self._get(connection, supply_id)

    def list(self):
        with self.database.connect() as connection:
            ids = [r['id'] for r in connection.execute("SELECT id, metadata_json FROM erp_receipts WHERE status <> 'cancelled' ORDER BY created_at DESC, id DESC") if self._metadata(r).get('source_type') == 'supply']
            return [self._get(connection, i) for i in ids]

    def resolve_bitrix(self, product, connection=None):
        if connection is None:
            with self.database.transaction() as connection:
                return self.resolve_bitrix(product, connection)
        sync = BitrixERPProductSync(self.database)
        if sync._validate(product):
            raise SupplyError('Товар Bitrix не содержит ID или названия.')
        # Never pass external stock into the catalog card constructor.
        product = dict(product, stock=0)
        if sync._deleted_product(connection, product):
            raise SupplyError('Товар ERP удалён. Восстановите карточку перед добавлением.')
        rows = connection.execute('SELECT * FROM catalog_excel_products WHERE active = 1 AND (bitrix_external_product_id = ? OR bitrix_catalog_product_id IN (SELECT id FROM catalog_products WHERE external_product_id = ?))', (str(product['external_product_id']), str(product['external_product_id']))).fetchall()
        if not rows:
            rows = sync._single_match(connection, product)['products']
            if rows and any(r['bitrix_external_product_id'] not in (None, '', str(product['external_product_id'])) for r in rows):
                raise SupplyError('Артикул связан с другим товаром Bitrix. Требуется ручное сопоставление.')
        if len(rows) > 1:
            raise SupplyError('Найдено несколько карточек ERP. Требуется ручное сопоставление.')
        if rows:
            product_id = rows[0]['id']
            # Persist a proven identity only; do not overwrite the card or its stock.
            connection.execute('UPDATE catalog_excel_products SET bitrix_external_product_id = ? WHERE id = ?', (str(product['external_product_id']), product_id))
        else:
            product_id = sync._insert_card(connection, product, enrichment_from_product(product))
        return self._product(connection, product_id)

    def import_excel(self, content, filename, actor=''):
        parsed = ExcelReceiptImportService(self.database)._parse(content)
        invalid = [r for r in parsed['rows'] if r['row_status'] == 'error']
        if invalid:
            raise SupplyError('; '.join('Строка {}: {}'.format(r['excel_row'], r['error_message']) for r in invalid[:10]))
        with self.database.transaction() as connection:
            matches = ProductReconciler(_load_bitrix_products(connection)).reconcile([r['data'] for r in parsed['rows'] if r['row_status'] == 'valid'])
            items = []
            for match in matches:
                if match['match_status'] != 'exact':
                    raise SupplyError('Строка {}: нет однозначного товара Bitrix. Укажите Bitrix ID в Excel или добавьте товар вручную через поиск Bitrix.'.format(match['excel_row']))
                catalog = connection.execute('SELECT normalized_payload_json FROM catalog_products WHERE id = ?', (match['product_id'],)).fetchone()
                product = self.resolve_bitrix(json.loads(catalog['normalized_payload_json']), connection)
                items.append({'product_id': product['id'], 'quantity': int(match['stock'])})
            self._positions(items)
            now = utc_now()
            supply_id = 'supply:' + uuid.uuid4().hex
            meta = {'source_type': 'supply', 'title': filename[:200], 'created_by': actor, 'created_at': now, 'posted_by': None, 'posted_at': None, 'import_filename': filename}
            ReceiptInventory._insert_draft(connection, meta, [], supply_id, None, actor, 'default', now)
            connection.execute('UPDATE erp_receipts SET number = ? WHERE id = ?', ('П-' + supply_id.split(':')[1][:12].upper(), supply_id))
            self._replace(connection, supply_id, items, now)
            return self._get(connection, supply_id)

    def movements(self, legacy=None):
        rows = []
        represented = set()
        with self.database.connect() as connection:
            for raw in connection.execute('SELECT * FROM catalog_stock_movements WHERE quantity_delta > 0 ORDER BY created_at DESC, id DESC'):
                m = dict(raw)
                kind = 'sale_cancellation' if m['movement_type'] == 'cancellation' else (m['source_type'] or m['movement_type'])
                receipt = connection.execute('SELECT * FROM erp_receipts WHERE id = ?', (m['receipt_id'],)).fetchone() if m['receipt_id'] else None
                if kind == 'sale_cancellation':
                    receipt = connection.execute('SELECT * FROM erp_receipts WHERE id = ?', ('sale-cancellation:' + str(m['sale_id']),)).fetchone()
                sale_number = ''
                if kind == 'sale_cancellation':
                    from app.services.sales_inventory import SalesInventory
                    sale = connection.execute('SELECT * FROM erp_sales WHERE id = ?', (m['sale_id'],)).fetchone()
                    if sale is not None:
                        sale_number = SalesInventory._sale_number(sale)
                meta = self._metadata(receipt) if receipt else {}
                product = (meta.get('snapshots') or {}).get(str(m['product_id'])) or self._product(connection, m['product_id'])
                source_id = m['sale_id'] if kind == 'sale_cancellation' else (m['receipt_id'] or m['source_id'] or m['sale_id'])
                if m['receipt_id']:
                    represented.add(str(m['receipt_id']))
                if kind == 'sale_cancellation':
                    represented.add('sale-cancellation:' + str(m['sale_id']))
                rows.append(dict(product, id=m['id'], product_id=m['product_id'],
                    source_type=kind, source_id=source_id, sale_number=sale_number, source_line_id=m['source_line_id'] or m['sale_item_id'],
                    number=(receipt['number'] if receipt else m['source_number']) or '',
                    title=meta.get('title') or (receipt['number'] if receipt else m['source_number']) or str(source_id or ''),
                    comment=m['comment'] or '', created_at=m['created_at'], user_name=m['user_name'] or '',
                    stock_before=m['stock_before'] if m['stock_before'] is not None else m['stock_after']-m['quantity_delta'],
                    quantity=m['quantity_delta'], stock_after=m['stock_after']))
            # Historical Excel operations remain read-only in their original ledger.
            for raw in connection.execute('SELECT o.*, r.number FROM catalog_excel_receipt_operations o JOIN catalog_excel_receipts r ON r.id = o.receipt_id WHERE o.stock_difference > 0'):
                m = dict(raw)
                rows.append(dict(self._product(connection, m['product_id']), id='excel:'+m['id'], product_id=m['product_id'], source_type='legacy_excel', source_id=m['receipt_id'], source_line_id=m['receipt_row_id'], title=m['number'], comment='', created_at=m['created_at'], user_name='', stock_before=m['stock_before'], quantity=m['stock_difference'], stock_after=m['stock_after']))
        for receipt in legacy or []:
            if receipt.get('status') == 'draft' or str(receipt.get('id')) in represented:
                continue
            for index, item in enumerate(receipt.get('positions') or [receipt]):
                kind = 'sale_cancellation' if receipt.get('automatic_type') == 'sale_cancellation' else 'legacy'
                rows.append(dict(id='legacy:{}:{}'.format(receipt.get('id'), index), product_id=item.get('product_id'), name=item.get('product_name') or '', article=item.get('article') or '', brand=item.get('brand') or '', category=item.get('category') or '', image_url='', source_type=kind, source_id=receipt.get('source_sale_id') if kind == 'sale_cancellation' else receipt.get('id'), source_line_id=None, title=receipt.get('number') or '', comment=receipt.get('note') or receipt.get('comment') or '', created_at=receipt.get('created_at') or receipt.get('receipt_date') or '', user_name=receipt.get('user_name') or '', stock_before=None, quantity=item.get('quantity') or 0, stock_after=None))
        return sorted(rows, key=lambda r: (r['created_at'], str(r['id'])), reverse=True)
