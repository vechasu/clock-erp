"""Profile isolated, production-shaped orders; never reads production data.

Run from the repository root. SQL counts include explicit schema probes/PRAGMAs.
Timings include cProfile instrumentation and execute + fetchall processing.
"""
import argparse
import os,sys,time,json,sqlite3,cProfile,pstats,io
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--orders',type=int,default=16471)
parser.add_argument('--runs',type=int,default=3)
parser.add_argument('--serve',action='store_true')
parser.add_argument('--port',type=int,default=4196)
parser.add_argument('--output')
args=parser.parse_args()
project_root=Path(__file__).resolve().parents[1]
os.chdir(str(project_root))
os.environ.update(ERP_TEST_MODE='1',ERP_AUTH_ENABLED='0')
sys.path[:0]=[os.getcwd(),os.path.join(os.getcwd(),'tests')]
import stage2_preview_server as preview
from app.services.wildberries_orders import normalize_wildberries_order
web=preview.web
web.app.config['ORDERS_SNAPSHOT_TESTING']=True
store=preview.OrdersSnapshotStore(preview.PREVIEW_ROOT/'orders.db')
orders=[normalize_wildberries_order(dict(id=900000+i,supplierStatus='new',wbStatus=['waiting','sold','ready_for_pickup','canceled_by_client'][i%4],article='T137.407',name='Test watch',price=860100,createdAt='2026-09-07T05:05:00Z')) for i in range(max(0,args.orders-3))]
store.upsert_wildberries(orders)
web.ORDERS_CACHE.update(items=[],loaded_at=time.time(),error='')
web.get_order=lambda oid:store.get(oid)
import requests
requests.sessions.Session.request=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('External requests forbidden'))
statements=[]
class Cursor(sqlite3.Cursor):
 def execute(self,sql,params=()):
  self.sql=sql;self.params=params;start=time.perf_counter()
  try:return super().execute(sql,params)
  finally:statements.append({'sql':sql,'ms':(time.perf_counter()-start)*1000})
 def fetchall(self):
  start=time.perf_counter()
  try:return super().fetchall()
  finally:
   if statements:statements[-1]['ms']+=(time.perf_counter()-start)*1000
class Connection(sqlite3.Connection):
 def execute(self,sql,params=()):return self.cursor(factory=Cursor).execute(sql,params)
original_connect=sqlite3.connect
def connect(*a,**k):k['factory']=Connection;return original_connect(*a,**k)
sqlite3.connect=connect
if args.serve:
 web.app.run(host='127.0.0.1',port=args.port,use_reloader=False)
 sys.exit()
client=web.app.test_client()
output={}
for path in ['/order','/orders','/api/orders','/api/orders?status=WB_NEW','/order/wildberries/900000','/order/7001','/order/wildberries/900000#fragment','/order/7001#fragment']:
 runs=[]
 for i in range(args.runs):
  statements.clear();p=cProfile.Profile();t=time.perf_counter();p.enable();response=client.get(path.split('#')[0],headers={'X-Order-Detail':'1'} if '#fragment' in path else {});p.disable();ms=(time.perf_counter()-t)*1000
  runs.append({'ms':round(ms,2),'status':response.status_code,'bytes':len(response.data),'sql_count':len(statements),'sql_selects':sum(x['sql'].lstrip().upper().startswith('SELECT') for x in statements),'sql_ms':round(sum(x['ms'] for x in statements),2)})
  if i==args.runs-1:
   s=io.StringIO();pstats.Stats(p,stream=s).sort_stats('cumulative').print_stats(35)
   output[path]={'runs':runs,'profile':s.getvalue(),'slow_sql':sorted(statements,key=lambda x:x['ms'],reverse=True)[:10], 'order_sql':[row for row in statements if 'FROM orders_snapshot' in row['sql']]}
with store.connection() as connection:
 output['query_plans']={query:[row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+query)] for query in [
  'SELECT payload_json FROM orders_snapshot ORDER BY created_sort DESC,order_id DESC LIMIT 50',
  "SELECT payload_json FROM orders_snapshot WHERE source='wildberries' ORDER BY created_sort DESC,order_id DESC LIMIT 50",
  "SELECT order_id FROM orders_snapshot WHERE number_fold='900001' OR order_id='900001' OR external_order_id='900001'",
  'SELECT source,work_status,COUNT(*) FROM orders_snapshot GROUP BY source,work_status']}
serialized=json.dumps(output,ensure_ascii=False,indent=2)
if args.output:Path(args.output).write_text(serialized+'\n',encoding='utf-8')
else:print(serialized)
