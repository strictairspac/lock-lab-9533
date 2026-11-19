"""签收/核销业务端到端测试（真实 PostgreSQL 行锁，无 mock）。

运行（仓库外目录）：
  source ~/run/env.sh
  cd /workspace/backend && PYTHONPATH=. python /workspace/tests/test_reconcile.py

覆盖需求：
1. 签收单先于移交申请乱序到达可登记，申请补齐后自动核销
2. 同一申请多笔签收乱序到达的 FIFO 核销与数量守恒
3. 更正按版本认最新；超量保留原已核数量并标待核；腾出额度自动重算
4. 签收登记与申请入库并发不漏核
5. 对账中断可续跑（BATCH_LIMIT 截断 + running 僵尸任务恢复）
6. 重试不重复核销（任务重试 / 签收幂等重试）
7. 新版先到（先更正再登记）
8. 更正与新签收争同一余额的并发正确性
"""
import os, sys, threading, time, uuid
sys.path.insert(0, '/workspace/backend')

from sqlalchemy import select, text
from src.config.database import Session, Base, engine
from src.models import entities
from src.models.entities import User, Sample, Transfer, Signoff, TransferLedger, ReconJob
from src.services import transfers as tsv, signoffs as ssv, reconcile

PASS = 0
def check(name, cond, detail=''):
    global PASS
    assert cond, f'❌ {name} {detail}'
    print(f'  ✓ {name} {detail}')
    PASS += 1

class U:
    email='tester@qingyu.test'; role='admin'

def reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        db.add(User(email=U.email, password='x', role='admin'))
        db.add(Sample(code='S-1', title='测试样本', quantity=1000, location='L1'))
        db.commit()

class Dummy:
    def __init__(self, **kw): self.__dict__.update(kw)
    def model_dump(self): return dict(self.__dict__)

def mk_transfer(db, code, qty, note=''):
    return tsv.create(db, Dummy(code=code, sample_id=1, destination='理化室',
                                quantity=qty, note=note), U)

def mk_signoff(db, code, tcode, qty):
    obj, _ = ssv.register(db, Dummy(code=code, transfer_code=tcode, quantity=qty), U)
    return obj

def correction(db, t, qty, note='更正'):
    return tsv.correct(db, t, Dummy(destination='理化室', quantity=qty, note=note), U)

def state(tcode):
    with Session() as db:
        signs = [dict(code=s.code, qty=s.quantity, verified=s.verified_qty,
                      status=s.status, reason=s.pending_reason)
                 for s in db.scalars(select(Signoff).where(Signoff.transfer_code==tcode).order_by(Signoff.id))]
        led = db.execute(text("SELECT * FROM transfer_ledgers WHERE transfer_code=:c"), {'c': tcode}).mappings().first()
        jobs = db.scalars(select(ReconJob).where(ReconJob.transfer_code==tcode).order_by(ReconJob.id)).all()
        return signs, (dict(led) if led else None), [(j.status, j.reason, j.attempts, j.error) for j in jobs]

def test_1_signoff_before_transfer():
    print('\n[1] 签收先于申请乱序到达 → 申请补齐自动核销')
    reset_db(); c='MOVE-T1'
    with Session() as db:
        mk_signoff(db, 'SO-T1-1', c, 30)
        mk_signoff(db, 'SO-T1-2', c, 20)
    signs, led, jobs = state(c)
    check('申请未到：两笔均登记为待核（application_missing 由对账标记）',
          all(s['status']=='pending' for s in signs) and led['application_qty']==0,
          str([s['reason'] for s in signs]))
    with Session() as db:
        mk_transfer(db, c, 40)
        reconcile.drain(c)
    signs, led, jobs = state(c)
    check('申请补齐后自动核销：30 全核 + 20 中 10 核、10 待核',
          signs[0]['verified']==30 and signs[0]['status']=='verified'
          and signs[1]['verified']==10 and signs[1]['status']=='pending'
          and signs[1]['reason']=='quota_insufficient', str(signs))
    check('账本守恒 verified=40=application', led['verified_qty']==40 and led['application_qty']==40, str(led))

def test_2_out_of_order_and_fifo():
    print('\n[2] 多笔签收乱序到达 + FIFO + 守恒')
    reset_db(); c='MOVE-T2'
    with Session() as db:
        mk_transfer(db, c, 50)
    with Session() as db:  # 乱序：先到一笔大的
        mk_signoff(db, 'SO-T2-9', c, 40)
    reconcile.drain(c)
    with Session() as db:
        mk_signoff(db, 'SO-T2-1', c, 20)
    reconcile.drain(c)
    signs, led, _ = state(c)
    check('先到 40 全核，后到 20 仅核 10（按登记先后 FIFO）',
          signs[0]['code']=='SO-T2-9' and signs[0]['verified']==40 and signs[0]['status']=='verified'
          and signs[1]['verified']==10 and signs[1]['reason']=='quota_insufficient', str(signs))
    check('总核销量不超过申请量', led['verified_qty']==50 and sum(s['verified'] for s in signs)==50)

def test_3_correction_versions():
    print('\n[3] 更正按版本认最新：缩量保留已核、放量自动重算')
    reset_db(); c='MOVE-T3'
    with Session() as db:
        mk_transfer(db, c, 100)
        mk_signoff(db, 'SO-T3-1', c, 60)
        mk_signoff(db, 'SO-T3-2', c, 60)
    reconcile.drain(c)
    signs, led, _ = state(c)
    check('100 额度：60 全核 + 40/60 待核', signs[0]['verified']==60 and signs[1]['verified']==40, str(signs))
    with Session() as db:  # 更正缩量到 50（小于已核 100）
        t = db.scalar(select(Transfer).where(Transfer.code==c))
        correction(db, t, 50, '缩量')
    reconcile.drain(c)
    signs, led, jobs = state(c)
    with Session() as db:
        ver = db.scalar(select(Transfer.version).where(Transfer.code==c))
    check('版本递增到 v2', ver==2, f'version={ver}')
    check('缩量不回退已核数量（仍 60+40=100）',
          signs[0]['verified']==60 and signs[1]['verified']==40, str(signs))
    check('两单标待核 quota_insufficient（额度 50 < 已核 100）',
          all(s['reason']=='quota_insufficient' and s['status']=='pending' for s in signs), str(signs))
    check('账本 application_qty 认最新版本=50，verified 保留=100',
          led['application_qty']==50 and led['verified_qty']==100 and led['version']==2, str(led))
    with Session() as db:  # 更正放量到 120，腾出 20 额度
        t = db.scalar(select(Transfer).where(Transfer.code==c))
        correction(db, t, 120, '放量')
    reconcile.drain(c)
    signs, led, _ = state(c)
    check('腾出额度自动重算：第二单补齐到 60 并全核',
          signs[1]['verified']==60 and signs[1]['status']=='verified', str(signs))
    check('最终两单全核、账本 120/120 v3',
          all(s['status']=='verified' for s in signs) and led['verified_qty']==120
          and led['application_qty']==120 and led['version']==3, str(led))

def test_4_concurrent_signoff_and_transfer():
    print('\n[4] 申请与签收并发入库不漏核')
    reset_db(); c='MOVE-T4'
    errors=[]
    def worker(fn):
        try:
            with Session() as db: fn(db)
        except Exception as e: errors.append(str(e))
    for i in range(6):  # 6 笔 × 20 = 120，申请 100
        t=threading.Thread(target=worker, args=(lambda db, i=i: mk_signoff(db, f'SO-T4-{i}', c, 20),))
        t.start()
    time.sleep(0.05)
    t2=threading.Thread(target=worker, args=(lambda db: mk_transfer(db, c, 100),))
    t2.start(); t2.join()
    for t in threading.enumerate():
        if t is not threading.main_thread(): t.join()
    check('并发写入无异常', not errors, str(errors))
    reconcile.drain(c)
    signs, led, jobs = state(c)
    check('6 笔签收全部登记', len(signs)==6, str(len(signs)))
    check('核销总额精确等于申请量 100，无漏核/超核',
          led['verified_qty']==100 and sum(s['verified'] for s in signs)==100
          and sum(1 for s in signs if s['status']=='verified')==5, str(signs))
    check('仅 1 笔待核且原因正确', signs[-1]['verified']==0 and signs[-1]['reason']=='quota_insufficient', str(signs[-1]))
    check('无僵尸/失败任务', all(j[0] in ('done',) for j in jobs), str(jobs))

def test_5_resume_and_stale_recovery():
    print('\n[5] 对账中断续跑（分批截断 + 僵尸 running 恢复）')
    reset_db(); c='MOVE-T5'
    # 强制小批次，让 45 笔签收触发多轮续跑（生产默认 BATCH_LIMIT=500）。
    old_batch = reconcile.BATCH_LIMIT
    reconcile.BATCH_LIMIT = 20
    try:
        with Session() as db:
            mk_transfer(db, c, 1000)
            for i in range(45):
                mk_signoff(db, f'SO-T5-{i:02d}', c, 10)
        # 手动模拟中断：制造一个 running 僵尸任务（worker 崩溃形态，started_at 为空）
        with Session() as db:
            db.execute(text("UPDATE recon_jobs SET status='running' WHERE transfer_code=:c"), {'c': c})
            db.commit()
        # 先自愈僵尸，再处理排队（limit 不足以一轮跑完，验证续跑链不丢任务）
        with Session() as db:
            reconcile.reset_running(db)
        reconcile.run_available(limit=2)
        reconcile.run_available(limit=10)
        signs, led, jobs = state(c)
        check('45 笔分批续跑后全部核销（每笔 10，额度 1000 → 全核）',
              len(signs)==45 and all(s['status']=='verified' and s['verified']==10 for s in signs),
              f"verified={sum(s['verified'] for s in signs)}")
        check('账本最终 450/1000 守恒、游标归零',
              led['verified_qty']==450 and led['last_cursor_id']==0, str(led))
        check('存在多轮 resume 任务且最终无活动任务',
              len(jobs)>=3 and all(j[0]=='done' for j in jobs), str(jobs))
    finally:
        reconcile.BATCH_LIMIT = old_batch

def test_6_retry_idempotent():
    print('\n[6] 重试不重复核销（任务重试 + 签收幂等）')
    reset_db(); c='MOVE-T6'
    with Session() as db:
        mk_transfer(db, c, 30)
        mk_signoff(db, 'SO-T6-1', c, 20)
        mk_signoff(db, 'SO-T6-2', c, 20)
    reconcile.drain(c)
    before, led1, _ = state(c)
    # 手工把任务置失败再重试
    with Session() as db:
        jid = db.scalar(select(ReconJob.id).where(ReconJob.transfer_code==c))
        db.get(ReconJob, jid).status='failed'; db.get(ReconJob, jid).error='模拟崩溃'; db.commit()
    with Session() as db:
        job = db.get(ReconJob, jid); job.status='queued'; job.error=''; db.commit()
    reconcile.drain(c)
    after, led2, jobs = state(c)
    ledger_key = lambda l: (l['application_qty'], l['verified_qty'], l['version'], l['last_cursor_id'])
    check('失败任务重试后核销数量不变（幂等）', before==after and ledger_key(led1)==ledger_key(led2), str(after))
    # 同编号签收单重复提交
    with Session() as db:
        ret = mk_signoff(db, 'SO-T6-1', c, 999)  # 数量不同也应返回原单
    check('签收编号幂等：重复登记返回原单且不新增', ret['quantity']==20, str(ret))
    after2, _, _ = state(c)
    check('签收总数仍为 2、已核不变', len(after2)==2 and after2==after, str(after2))

def test_7_new_version_first():
    print('\n[7] 新版先到：先更正到 v3，后到的签收按最新版本核销')
    reset_db(); c='MOVE-T7'
    with Session() as db:
        mk_transfer(db, c, 100)  # v1
        t = db.scalar(select(Transfer).where(Transfer.code==c))
        correction(db, t, 80)
        t = db.scalar(select(Transfer).where(Transfer.code==c))
        correction(db, t, 60)
    with Session() as db:
        ver = db.scalar(select(Transfer.version).where(Transfer.code==c))
    check('连续更正后版本 v3、额度 60', ver==3, f'v{ver}')
    with Session() as db:
        mk_signoff(db, 'SO-T7-1', c, 50)
        mk_signoff(db, 'SO-T7-2', c, 50)
    reconcile.drain(c)
    signs, led, _ = state(c)
    check('后到签收按最新版 60 核销：50 全核 + 10/50 待核',
          signs[0]['verified']==50 and signs[1]['verified']==10
          and signs[1]['reason']=='quota_insufficient', str(signs))
    check('账本版本=3', led['version']==3 and led['application_qty']==60, str(led))

def test_8_correction_vs_signoff_same_balance():
    print('\n[8] 更正与新签收争同一余额（真实行锁竞争）')
    reset_db(); c='MOVE-T8'
    with Session() as db:
        mk_transfer(db, c, 100)
        mk_signoff(db, 'SO-T8-1', c, 100)
    reconcile.drain(c)
    barrier = threading.Event()
    errors=[]
    def correction():
        try:
            with Session() as db:
                # 先拿锁并持有，制造真实锁竞争窗口
                reconcile.lock_ledger(db, c)
                barrier.set()
                time.sleep(0.8)
                t = db.scalar(select(Transfer).where(Transfer.code==c))
                t.quantity=150; t.version+=1
                db.add(Audit_safe('更正放量到 150'))
                reconcile.enqueue(db, c, 'correction')
                db.commit()
        except Exception as e: errors.append(('correction', str(e)))
    def new_signoff():
        try:
            barrier.wait(3)
            time.sleep(0.1)
            with Session() as db:  # 将在 ledger 行锁上排队，等更正提交
                mk_signoff(db, 'SO-T8-2', c, 60)
        except Exception as e: errors.append(('signoff', str(e)))
    th=[threading.Thread(target=correction), threading.Thread(target=new_signoff)]
    [t.start() for t in th]; [t.join() for t in th]
    check('锁竞争无死锁/超时错误', not errors, str(errors))
    reconcile.drain(c)
    signs, led, jobs = state(c)
    check('放量 150 后：原 100 全核 + 新签收 50/60 核（精确占用腾出额度）',
          signs[0]['verified']==100 and signs[1]['verified']==50
          and signs[1]['status']=='pending' and signs[1]['reason']=='quota_insufficient', str(signs))
    check('账本 150 额度、150 已核、版本 v2',
          led['application_qty']==150 and led['verified_qty']==150 and led['version']==2, str(led))
    check('所有任务最终 done，无重复核销', all(j[0]=='done' for j in jobs), str(jobs))

def Audit_safe(action):
    from src.models.entities import Audit
    return Audit(actor=U.email, action=action)

if __name__ == '__main__':
    tests=[v for k,v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests: t()
    print(f'\n全部通过：{PASS} 项断言')
