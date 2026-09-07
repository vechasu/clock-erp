"""Receipt workspace: no MoySklad dependency; legacy writes are retired."""
import sqlite3
from functools import wraps
from flask import request, jsonify, render_template, abort, redirect
from app.auth import current_auth_user, auth_is_enabled, require_csrf_when_authenticated
from app.services.supplies import SupplyEngine, SupplyError
from app.services.receipt_inventory import ReceiptInventoryError
from app.services.excel_receipt_import import ExcelDraftError, MAX_EXCEL_FILE_SIZE
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyError


def register_supply_routes(w):
    app = w.app
    original_collection = app.view_functions["api_receipts_collection"]
    original_resource = app.view_functions["api_receipt_resource"]

    def actor():
        user = current_auth_user() or {}
        return ' '.join(filter(None, [user.get('first_name'), user.get('last_name')])) or user.get('email') or 'system'

    def writable():
        require_csrf_when_authenticated()
        user = current_auth_user() or {}
        if auth_is_enabled() and user.get('role') in ('viewer', 'readonly', 'read_only'):
            abort(403)

    def guarded(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                if request.method != 'GET':
                    writable()
                return fn(*args, **kwargs)
            except (SupplyError, ReceiptInventoryError, ExcelDraftError, ValueError) as error:
                return jsonify(ok=False, message=str(error)), 422
            except BitrixCatalogReadOnlyError:
                return jsonify(ok=False, message='Bitrix недоступен. Сохранённые поставки можно проводить без Bitrix.'), 503
            except sqlite3.Error:
                app.logger.exception('Local supply database operation failed')
                return jsonify(ok=False, message='Не удалось сохранить поставку. Изменение остатка не выполнено.'), 503
        return wrapped

    @guarded
    def page():
        return render_template('supplies.html')

    @app.route('/api/v1/receipts/supplies', methods=['GET', 'POST'])
    @guarded
    def supplies():
        engine = SupplyEngine()
        if request.method == 'POST':
            p = request.get_json(silent=True) or {}
            if not isinstance(p, dict):
                raise SupplyError('Некорректные данные поставки.')
            return jsonify(ok=True, data=engine.create(p.get('title'), p.get('comment'), actor(), key=request.headers.get('Idempotency-Key'))), 201
        return jsonify(ok=True, data=engine.list())

    @app.route('/api/v1/receipts/supplies/<supply_id>', methods=['GET', 'PATCH', 'DELETE'])
    @guarded
    def supply(supply_id):
        engine = SupplyEngine()
        if request.method == 'DELETE':
            engine.delete(supply_id)
            return jsonify(ok=True)
        if request.method == 'PATCH':
            p = request.get_json(silent=True) or {}
            if not isinstance(p, dict):
                raise SupplyError('Некорректные данные поставки.')
            return jsonify(ok=True, data=engine.update(supply_id, p.get('title'), p.get('comment'), p.get('items')))
        return jsonify(ok=True, data=engine.get(supply_id))

    @app.route('/api/v1/receipts/supplies/<supply_id>/post', methods=['POST'])
    @guarded
    def post(supply_id):
        return jsonify(ok=True, data=SupplyEngine().post(supply_id, actor()))

    @app.route('/api/v1/receipts/bitrix', methods=['GET'])
    @guarded
    def search():
        query = (request.args.get('q') or '').strip()
        if len(query) < 2:
            return jsonify(ok=True, data=[])
        products = w._bitrix_single_client().search_products(query, limit=20)
        rows = [w._bitrix_single_source_payload(p) for p in products]
        for row in rows:
            row.pop('stock', None)
        return jsonify(ok=True, data=rows)

    @app.route('/api/v1/receipts/bitrix/<int:bitrix_id>', methods=['POST'])
    @guarded
    def resolve(bitrix_id):
        product = w._bitrix_single_client().get_product(bitrix_id)
        if not product:
            raise SupplyError('Товар не найден в Bitrix.')
        return jsonify(ok=True, data=SupplyEngine().resolve_bitrix(product))

    @app.route('/api/v1/receipts/excel', methods=['POST'])
    @guarded
    def excel():
        upload = request.files.get('file')
        if not upload:
            raise SupplyError('Выберите Excel-файл.')
        content = upload.read(MAX_EXCEL_FILE_SIZE + 1)
        if len(content) > MAX_EXCEL_FILE_SIZE:
            raise SupplyError('Файл превышает 15 МБ.')
        return jsonify(ok=True, data=SupplyEngine().import_excel(content, upload.filename, actor())), 201

    @app.route('/api/v1/receipts/movements', methods=['GET'])
    @guarded
    def movements():
        return jsonify(ok=True, data=SupplyEngine().movements(w.load_receipts()))

    @guarded
    def retired(*args, **kwargs):
        return jsonify(ok=False, message='Старый способ изменения прихода отключён. Используйте поставку ERP: /app/receipts.'), 410

    @guarded
    def legacy_collection():
        if request.method != 'GET':
            return retired()
        return original_collection()

    @guarded
    def legacy_resource(receipt_id):
        if request.method != 'GET':
            return retired()
        return original_resource(receipt_id)

    app.view_functions['receipts_page'] = page
    # Retire ALL old mutation entry points, including both Excel implementations.
    for name in ('receipt_catalog_create', 'receipt_create', 'receipt_update', 'receipt_delete',
                 'receipts_import_preview', 'excel_receipt_post'):
        app.view_functions[name] = retired
    app.view_functions['api_receipts_collection'] = legacy_collection
    app.view_functions['api_receipt_resource'] = legacy_resource
    app.view_functions['excel_receipt_preview'] = excel
    app.view_functions['excel_receipt_new'] = lambda: redirect('/app/receipts?tab=supplies')
