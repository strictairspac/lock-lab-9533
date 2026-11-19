"""验收脚本：通过真实 HTTP API + worker 演示用户点名的两个场景。
1) 新版先到：申请先连续更正到 v3，之后才到的签收按最新版本核销。
2) 更正与新签收争同一余额：持锁更正与并发签收交错，最终精确核销、不重不漏。
"""
import sys, time, json, uuid, threading, urllib.request, urllib.error
BASE='http://127.0.0.1:19242'
RUN=uuid.uuid4().hex[:8]
sys.path.insert(0,'/workspace/backend')
from scripts.seed_demo import seed
seed()

def call(method, path, token=None, body=None):
    req=urllib.request.Request(BASE+path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', **({'Authorization':'Bearer '+token} if token else {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def login():
    return call('POST','/auth/login', body={'email':'operator@qingyu.test','password':'Demo2026!'})[1]['token']

def wait_idle(token, tcode, timeout=15):
    end=time.time()+timeout
    while time.time()<end:
        _, jobs = call('GET','/recon/jobs', token)
        mine=[j for j in jobs if j['transfer_code']==tcode]
        if mine and not any(j['status'] in ('queued','running') for j in mine):
            if any(j['status']=='failed' for j in mine): raise AssertionError('存在失败任务: '+str(mine))
            return mine
        time.sleep(0.25)
    raise AssertionError('对账未在时限内结束')

def signs(token, tcode):
    _, rows = call('GET', f'/signoffs?transfer_code={tcode}', token)
    return [(s['code'], s['quantity'], s['verified_qty'], s['status'], s['pending_reason_label']) for s in rows]

tok=login()
_, samples = call('GET','/samples', token=tok); sid=samples[0]['id']

print('场景一：新版先到（先更正到 v3，签收后到）')
c1=f'MOVE-NEW-{RUN}'
st, tr = call('POST','/transfers', tok, {'code':c1,'sample_id':sid,'destination':'理化检测室','quantity':100,'note':'v1'})
tid=tr['id']
for v,q in ((2,80),(3,60)):
    st, tr = call('PUT', f'/transfers/{tid}', tok, {'destination':'理化检测室','quantity':q,'note':f'v{v}'})
    assert st==200 and tr['version']==v, tr
print(f'  申请已连续更正至 v3，最新额度 60（此时尚无任何签收单）')
call('POST','/signoffs', tok, {'code':f'SO-NEW-{RUN}-1','transfer_code':c1,'quantity':50})
call('POST','/signoffs', tok, {'code':f'SO-NEW-{RUN}-2','transfer_code':c1,'quantity':50})
wait_idle(tok, c1)
rows=signs(tok, c1)
for r in rows: print('  ', r)
assert rows[0][2]==50 and rows[0][3]=='verified'
assert rows[1][2]==10 and rows[1][3]=='pending' and '额度不足' in rows[1][4]
print('  ✓ 后到签收按最新 v3 的 60 额度核销：50 全核 + 第二单 10/50 待核')

print('\n场景二：更正与新签收争同一余额（真实并发锁竞争）')
c2=f'MOVE-RACE-{RUN}'
call('POST','/transfers', tok, {'code':c2,'sample_id':sid,'destination':'理化检测室','quantity':100,'note':''})
call('POST','/signoffs', tok, {'code':f'SO-RACE-{RUN}-1','transfer_code':c2,'quantity':100})
wait_idle(tok, c2)
print('  初始：申请 100、签收 100 已全核')
errs=[]
def hold_correction():
    # 在独立事务里先锁账本再停留，制造与签收事务的真实锁窗口
    try:
        from src.config.database import Session
        from src.models.entities import Transfer, Audit
        from src.services import reconcile
        import time
        with Session() as db:
            reconcile.lock_ledger(db, c2)
            time.sleep(1.0)
            t=db.scalar(__import__('sqlalchemy').select(Transfer).where(Transfer.code==c2))
            t.quantity=150; t.version+=1
            db.add(Audit(actor='operator@qingyu.test', action='验收：放量更正到 150'))
            reconcile.enqueue(db, c2, 'correction')
            db.commit()
    except Exception as e: errs.append(str(e))
def concurrent_signoff():
    try:
        time.sleep(0.2)  # 确保更正事务已先持锁
        st, body = call('POST','/signoffs', tok, {'code':f'SO-RACE-{RUN}-2','transfer_code':c2,'quantity':60})
        if st not in (200,201): errs.append(str(body))
    except Exception as e: errs.append(str(e))
import time
th=[threading.Thread(target=hold_correction), threading.Thread(target=concurrent_signoff)]
[t.start() for t in th]; [t.join() for t in th]
assert not errs, errs
print('  并发结束：更正（100→150）与新签收 60 争同一余额，无死锁/超时')
wait_idle(tok, c2)
rows=signs(tok, c2)
for r in rows: print('  ', r)
assert rows[0][2]==100 and rows[0][3]=='verified'
assert rows[1][2]==50 and rows[1][3]=='pending' and '额度不足' in rows[1][4]
_, transfers = call('GET','/transfers', tok)
row=[t for t in transfers if t['code']==c2][0]
print(f"  ✓ 账本：申请量 {row['quantity']}(v{row['version']})，已核 {row['verified_qty']}，待核 {row['pending_count']} 笔")
assert row['verified_qty']==150 and row['version']==2 and row['pending_count']==1
print('\n两个验收场景全部通过。')
