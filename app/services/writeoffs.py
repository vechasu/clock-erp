"""Warehouse write-offs, independent of sales, using existing physical balances."""
import uuid
from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.component_inventory import balance, remember, write_balance
from app.services.inventory_lock import assert_products_unlocked
from app.services.product_bundles import physical_lines
from app.services.sales_inventory import SalesInventoryError, InsufficientStockError, now_iso

REASONS = ('Брак', 'Повреждение', 'Потеря', 'Для ремонта',
           'Внутреннее использование', 'Образец / демонстрация', 'Прочее')


def integer(value, label):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise SalesInventoryError('{} должно быть целым числом больше нуля.'.format(label))
    return int(value)


class Writeoffs:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def create(self, payload, actor, key='', failure_hook=None):
        pid = integer(payload.get('product_id'), 'ID товара')
        quantity = integer(payload.get('quantity'), 'Количество')
        reason = str(payload.get('reason') or '')
        comment = str(payload.get('comment') or '').strip()
        if reason not in REASONS:
            raise SalesInventoryError('Выберите причину списания.')
        if reason == 'Прочее' and not comment:
            raise SalesInventoryError('Укажите комментарий для причины «Прочее».')
        if len(comment) > 2000 or len(str(key or '')) > 120:
            raise SalesInventoryError('Слишком длинный комментарий или ключ операции.')
        wid, timestamp = uuid.uuid4().hex, now_iso()
        with self.database.transaction() as c:
            if key:
                old = c.execute('SELECT * FROM erp_writeoffs WHERE idempotency_key=?', (key,)).fetchone()
                if old:
                    if (old['product_id'], old['quantity'], old['reason'], old['comment']) != (pid, quantity, reason, comment):
                        raise SalesInventoryError('Ключ операции уже использован для другого списания.')
                    return dict(old)
            product = c.execute('SELECT p.*, b.name AS brand_name, k.name AS category_name FROM catalog_excel_products p LEFT JOIN erp_brands b ON b.id=p.brand_id LEFT JOIN erp_categories k ON k.id=p.category_id WHERE p.id=? AND p.active=1', (pid,)).fetchone()
            if product is None:
                raise SalesInventoryError('Товар не найден.')
            lines = physical_lines(c, pid, quantity)
            assert_products_unlocked(c, [pid] + [p for p, _ in lines], SalesInventoryError)
            available = min(balance(c, p) // (q // quantity) for p, q in lines)
            if available < quantity:
                raise InsufficientStockError(available)
            c.execute('INSERT INTO erp_writeoffs (id,product_id,quantity,reason,comment,status,created_at,created_by,created_by_name,idempotency_key,product_name,article,brand,category) VALUES (?,?,?,?,?,\'posted\',?,?,?,?,?,?,?,?)',
                      (wid,pid,quantity,reason,comment,timestamp,actor.get('actor_id',''),actor.get('actor_name',''),key or None,product['excel_name_raw'],product['excel_article'],product['brand_name'] or product['excel_brand'],product['category_name'] or product['excel_category']))
            for physical_id, amount in lines:
                c.execute('INSERT INTO erp_writeoff_items VALUES (?,?,?)', (wid,physical_id,amount))
                remember(c, physical_id, ('writeoff',wid))
                self._move(c, wid, physical_id, -amount, reason, comment, actor, timestamp, 'post')
            if failure_hook:
                failure_hook(c)
            return dict(c.execute('SELECT * FROM erp_writeoffs WHERE id=?',(wid,)).fetchone())

    def cancel(self, wid, actor, failure_hook=None):
        with self.database.transaction() as c:
            row = c.execute('SELECT * FROM erp_writeoffs WHERE id=?',(wid,)).fetchone()
            if row is None:
                raise SalesInventoryError('Списание не найдено.')
            if row['status'] == 'cancelled':
                return dict(row)
            lines = c.execute('SELECT * FROM erp_writeoff_items WHERE writeoff_id=?',(wid,)).fetchall()
            assert_products_unlocked(c, [row['product_id']] + [p['product_id'] for p in lines], SalesInventoryError)
            timestamp = now_iso()
            for line in lines:
                self._move(c,wid,line['product_id'],line['quantity'],row['reason'],row['comment'],actor,timestamp,'cancel')
            c.execute("UPDATE erp_writeoffs SET status='cancelled',cancelled_at=?,cancelled_by=?,cancelled_by_name=? WHERE id=?", (timestamp,actor.get('actor_id',''),actor.get('actor_name',''),wid))
            if failure_hook:
                failure_hook(c)
            return dict(c.execute('SELECT * FROM erp_writeoffs WHERE id=?',(wid,)).fetchone())

    def _move(self,c,wid,pid,delta,reason,comment,actor,timestamp,operation):
        document = ('writeoff',wid)
        before = balance(c,pid,document)
        after = before + delta
        write_balance(c,pid,after,'writeoff',timestamp,document)
        label = 'Списание' if operation == 'post' else 'Отмена списания'
        c.execute("INSERT INTO catalog_stock_movements (id,product_id,movement_type,quantity_delta,stock_before,stock_after,source_type,source_id,source_line_id,operation_kind,source,user_name,comment,created_at) VALUES (?,?,'manual_adjustment',?,?,?,'writeoff',?,?,?,?,?,?,?)", (uuid.uuid4().hex,pid,delta,before,after,wid,str(pid),operation,label,actor.get('actor_name',''),reason + (': '+comment if comment else ''),timestamp))
        AuditJournal(self.database).record('product',str(pid),'updated',label,reason,
            before={'stock':before},after={'stock':after},metadata={'writeoff_id':wid,'quantity_delta':delta,'comment':comment},
            source=label,connection=c,**actor)

    def list(self, filters):
        clauses, params = [], []
        for key in ('status','reason'):
            if filters.get(key):
                clauses.append('w.'+key+'=?'); params.append(filters[key])
        for key, op in (('date_from','>='),('date_to','<=')):
            if filters.get(key):
                clauses.append('substr(w.created_at,1,10)'+op+'?'); params.append(filters[key])
        if filters.get('q'):
            clauses.append("instr(casefold(w.product_name||' '||COALESCE(w.article,'')||' '||COALESCE(w.brand,'')||' '||COALESCE(w.category,'')||' '||w.created_by_name||' '||w.comment),casefold(?))>0")
            params.append(filters['q'])
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        page = max(1,int(filters.get('page') or 1))
        size = min(100,max(1,int(filters.get('per_page') or 50)))
        with self.database.connect() as c:
            c.create_function('casefold', 1, lambda value: str(value or '').casefold())
            total = c.execute('SELECT COUNT(*) FROM erp_writeoffs w'+where,params).fetchone()[0]
            page = min(page,max(1,(total+size-1)//size))
            rows = c.execute('SELECT w.* FROM erp_writeoffs w'+where+' ORDER BY w.created_at DESC,w.id DESC LIMIT ? OFFSET ?',params+[size,(page-1)*size]).fetchall()
        return {'rows':[dict(r) for r in rows], 'total':total,'page':page,'per_page':size}
