"""HTTP 端到端：真实 FastAPI + Postgres + Redis worker，验证 API 与自动对账。"""
import sys, time, json, uuid, urllib.request, urllib.error
BASE='http://127.0.0.1:19242'
RUN=uuid.uuid4().hex[:8]
# 单元测试会 drop_all 清库；HTTP 套件前先确保演示账号与样本存在（seed 按自然键只补缺失）。
sys.path.insert(0, '/workspace/backend')
from scripts.seed_demo import seed
seed()

def call(method, path, token=None, body=None):
    req=urllib.request.Request(BASE+path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', **({'Authorization':'Bearer '+token} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def wait_jobs(token, tcode, timeout=10):
    deadline=time.time()+timeout
    while time.time()<deadline:
        _, jobs = call('GET','/recon/jobs',token)
        mine=[j for j in jobs if j['transfer_code']==tcode]
        if mine and all(j['status']=='done' for j in mine):
            return mine
        time.sleep(0.3)
    raise AssertionError(f'对账任务未按时完成: {mine}')

ok=0
def check(name, cond, detail=''):
    global ok
    assert cond, f'❌ {name} {detail}'
    print(f'  ✓ {name}'); ok+=1

_, auth = call('POST','/auth/login', body={'email':'operator@qingyu.test','password':'Demo2026!'})
token=auth['token']
print('\n[A] 乱序：先登记签收，申请后到，worker 自动核销')
tcode=f'MOVE-HTTP-{RUN}'
st, so1 = call('POST','/signoffs', token, {'code':f'SO-HTTP-{RUN}-1','transfer_code':tcode,'quantity':30})
check('签收先于申请登记成功 201', st==201 and so1['status']=='pending', str(st))
st, so2 = call('POST','/signoffs', token, {'code':f'SO-HTTP-{RUN}-2','transfer_code':tcode,'quantity':20})
check('第二笔签收登记成功', st==201)
_, samples = call('GET','/samples', token)
sid=samples[0]['id']
st, tr = call('POST','/transfers', token, {'code':tcode,'sample_id':sid,'destination':'理化室','quantity':40,'note':'http'})
check('申请补齐入库 201 且 version=1', st==201 and tr['version']==1, str(st))
jobs=wait_jobs(token, tcode)
check('worker 自动完成对账，无失败任务', all(j['status']=='done' for j in jobs), str([(j['status'],j['reason']) for j in jobs]))
_, signs = call('GET',f'/signoffs?transfer_code={tcode}', token)
check('30 全核、20 中 10 核 10 待核',
      signs[0]['verified_qty']==30 and signs[0]['status']=='verified'
      and signs[1]['verified_qty']==10 and signs[1]['pending_reason']=='quota_insufficient'
      and signs[1]['pending_reason_label'], str(signs))
_, transfers = call('GET','/transfers', token)
row=[t for t in transfers if t['code']==tcode][0]
check('申请列表带已核数量 40 与待核原因汇总',
      row['verified_qty']==40 and row['pending_count']==1
      and row['pending_reasons'][0]['reason']=='quota_insufficient', str(row))

print('\n[B] 更正按版本认最新 + 幂等重试')
tid=row['id']
# 先放量到 60，让第二单（20）也全核；再缩量到 25 制造已核超量，验证已核不回退且转待核。
st, tr2 = call('PUT',f'/transfers/{tid}', token, {'destination':'金属室','quantity':60,'note':'放量'})
check('放量更正成功 version=2', st==200 and tr2['version']==2, str(tr2))
wait_jobs(token, tcode)
_, signs = call('GET',f'/signoffs?transfer_code={tcode}', token)
check('放量后两单全核（30+20=50）', all(s['status']=='verified' for s in signs), str(signs))
st, tr3 = call('PUT',f'/transfers/{tid}', token, {'destination':'金属室','quantity':25,'note':'缩量'})
check('缩量更正成功 version=3', st==200 and tr3['version']==3, str(tr3))
wait_jobs(token, tcode)
_, signs = call('GET',f'/signoffs?transfer_code={tcode}', token)
check('缩量到 25：已核数量保留（30+20=50，不回退），两单均转待核',
      signs[0]['verified_qty']==30 and signs[0]['status']=='pending'
      and signs[1]['verified_qty']==20 and signs[1]['status']=='pending'
      and all(s['pending_reason']=='quota_insufficient' for s in signs), str(signs))
st, dup = call('POST','/signoffs', token, {'code':f'SO-HTTP-{RUN}-1','transfer_code':tcode,'quantity':999})
check('签收编号幂等：重复提交返回原单 200、数量不变', st==200 and dup['quantity']==30, str(st))
st, dup2 = call('POST','/transfers', token, {'code':tcode,'sample_id':sid,'destination':'复核室','quantity':1,'note':''})
check('重复申请编号被拒 409，提示走更正', st==409, str(st))

print('\n[C] 权限：viewer 不可写')
_, vauth = call('POST','/auth/login', body={'email':'viewer@qingyu.test','password':'Demo2026!'})
vt=vauth['token']
st, _ = call('POST','/signoffs', vt, {'code':'X','transfer_code':tcode,'quantity':1})
check('viewer 写签收返回 403', st==403, str(st))
st, _ = call('PUT',f'/transfers/{tid}', vt, {'destination':'金属室','quantity':50,'note':''})
check('viewer 更正返回 403', st==403, str(st))

print('\n[D] 失败任务重试接口（重试不重复核销）')
# 通过 SQL 人为制造一个 failed 任务，再调重试接口，验证幂等重算。
sys.path.insert(0, '/workspace/backend')
from sqlalchemy import text
from src.config.database import Session
with Session() as db:
    db.execute(text(
        "INSERT INTO recon_jobs(transfer_code,reason,status,attempts,error,started_at,created_at,updated_at) "
        "VALUES (:c,'manual','failed',1,'模拟中断',NULL,now(),now())"), {'c': tcode})
    db.commit()
_, jobs_before = call('GET','/recon/jobs', token)
failed_id = [j for j in jobs_before if j['status']=='failed'][0]['id']
st, retried = call('POST', f'/recon/jobs/{failed_id}/retry', token)
check('重试接口把 failed 任务重置为 queued', st==200 and retried['status']=='queued', str(retried))
wait_jobs(token, tcode)
_, signs_after = call('GET',f'/signoffs?transfer_code={tcode}', token)
check('重试后核销数量与重试前一致（不重复核销）',
      [(s['code'],s['verified_qty'],s['status']) for s in signs_after]
      == [(s['code'],s['verified_qty'],s['status']) for s in signs], str(signs_after))
st, _ = call('POST', f'/recon/jobs/{999999}/retry', token)
check('重试不存在的任务返回 404', st==404, str(st))

print(f'\nHTTP 端到端全部通过：{ok} 项断言')
