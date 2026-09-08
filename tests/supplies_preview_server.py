"""Isolated fixture server; all external integration calls are replaced."""
import os
import stage2_preview_server as fixture
from app.services.supplies import SupplyEngine
from app.services.sales_inventory import SalesInventory

web = fixture.web
products = [dict(external_product_id=str(i), name=name, external_sku='SUP-'+str(i), brand='Casio', stock=47, images=[], properties=[]) for i,name in [(90101,'Casio A168'),(90102,'Casio F91W')]]
class BitrixFixture:
    def search_products(self, query, limit=20):
        return products
    def get_product(self, product_id):
        return next((p for p in products if p['external_product_id']==str(product_id)),None)
web._bitrix_single_client=lambda:BitrixFixture()
class ForbiddenMoySklad:
    def __init__(self,*args,**kwargs):
        raise AssertionError('Supply accessed MoySklad')
web.MoySkladClient=ForbiddenMoySklad
engine=SupplyEngine(fixture.fixture_catalog_database)
engine.resolve_bitrix(products[1])
card=engine.resolve_bitrix(products[0])
with fixture.fixture_catalog_database.transaction() as c:
    c.execute('UPDATE catalog_excel_products SET stock=3 WHERE id=?',(card['id'],))
sales=SalesInventory(fixture.fixture_catalog_database)
sales.create_sale({'id':'supply-fixture-cancellation','source':'Tictactoy','order_number':'21096'},card['id'],1,100)
sales.cancel_sale('supply-fixture-cancellation',reason='duplicate',user_name='Максим')
web.app.run(host='127.0.0.1',port=int(os.environ.get('PREVIEW_PORT','4184')),debug=False,use_reloader=False)
