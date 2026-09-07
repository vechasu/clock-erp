"""Orders-only fixtures on the existing isolated preview database."""
import os
import stage2_preview_server as preview
from app.services.wildberries_orders import normalize_wildberries_order

web = preview.web
orders = [normalize_wildberries_order(dict(id=9000+i, supplierStatus='complete' if i%2 else 'new',
          wbStatus=['waiting','sold','ready_for_pickup','canceled_by_client'][i%4],
          article='WATCH-'+str(i), name='Часы Wildberries '+str(i), price=860100,
          createdAt='2026-09-07T05:05:00Z')) for i in range(125)]
orders[1].update(recovered_from_wb=True, recovered_at='2026-09-07T06:10:00Z', recovery_supply_id='WB-GI-TEST')
preview.OrdersSnapshotStore(preview.PREVIEW_ROOT/'orders.db').upsert_wildberries(orders)
web.get_orders = lambda *args, **kwargs: preview.preview_orders + orders
web.get_order = lambda oid: next((dict(row) for row in preview.preview_orders if row['id']==str(oid)), None)
web.schedule_order_comment_sync = lambda *args, **kwargs: None
web.wb_diagnostics = lambda *args: dict(last_success_at='2026-09-07T06:10:00Z',attention=1,errors=[{'error':'Тестовая ошибка API'}],supplies=[],pending=[])
# Resolve synthetic UI photos without external requests; transactional posting has separate backend tests.
def mapping(products, **kwargs):
    return {web.order_product_mapping_key(p): {'state':'mapped', 'mapping_method':'article', 'product':dict(id='1',name=p.get('name','Часы'),article=p.get('article','WATCH'),stock=10,image_url='/__orders-photo')} for p in products if int(p.get('order_item_id') or 0)%2}
web.build_order_product_mapping_context = mapping
web.WildberriesSales.find_sale = lambda self, oid: {'id':'test-sale-9001'} if str(oid)=='9001' else None
original_bulk = web.bulk_conducted_order_sales
def bulk(ids, **kwargs):
    result = original_bulk(ids, **kwargs)
    if 'wb:9001' in ids:
        result['wb:9001'] = 'test-sale-9001'
    return result
web.bulk_conducted_order_sales = bulk
@web.app.get('/__orders-photo')
def photo():
    return '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect x="30" y="2" width="20" height="76" rx="5" fill="#334155"/><circle cx="40" cy="40" r="23" fill="#eff6ff" stroke="#2563eb" stroke-width="4"/><path d="M40 23V40L52 47" fill="none" stroke="#334155" stroke-width="3"/></svg>', 200, {'Content-Type':'image/svg+xml'}
web.app.config["TEMPLATES_AUTO_RELOAD"] = True
web.app.run(host='127.0.0.1',port=int(os.environ.get('PREVIEW_PORT','4188')),debug=False,use_reloader=False)
