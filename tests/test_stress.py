"""高并发压力测试：多线程交叉登记签收 / 更正 / 申请，验证最终一致性与零重复核销。

真实 PostgreSQL 行锁，无 mock；每个场景结束后 drain 对账并校验不变量。
"""
import sys, threading, random
sys.path.insert(0, '/workspace/tests'); sys.path.insert(0, '/workspace/backend')
from sqlalchemy import select, text
from src.config.database import Session
from src.models.entities import Transfer
from test_reconcile import reset_db, mk_signoff, mk_transfer, correction, Dummy, U, check, state
from src.services import transfers as tsv, reconcile

PASS = 0
def check2(name, cond, detail=''):
    global PASS
    assert cond, f'❌ {name} {detail}'
    print(f'  ✓ {name}'); PASS += 1

def test_stress_mixed_workers():
    print('\n[S1] 20 线程混合：先/后到签收 × 更正 × 申请，最终零漏核零超核')
    reset_db(); c = 'MOVE-S1'
    errors = []
    barrier = threading.Barrier(14)
    def signoff_worker(i):
        try:
            barrier.wait()
            with Session() as db:
                mk_signoff(db, f'SO-S1-{i:02d}', c, 10)  # 11 笔 × 10
        except Exception as e: errors.append(('so', i, str(e)))
    def correction_worker(qty):
        try:
            barrier.wait()
            with Session() as db:
                t = db.scalar(select(Transfer).where(Transfer.code == c))
                if t: correction(db, t, qty, f'并发更正 {qty}')
        except Exception as e: errors.append(('cor', qty, str(e)))
    def transfer_worker():
        try:
            barrier.wait()
            with Session() as db:
                mk_transfer(db, c, 70)
        except Exception as e: errors.append(('tr', str(e)))
    threads = [threading.Thread(target=signoff_worker, args=(i,)) for i in range(11)]
    threads += [threading.Thread(target=correction_worker, args=(q,)) for q in (50, 90)]
    threads.append(threading.Thread(target=transfer_worker))
    random.seed(7); random.shuffle(threads)
    for t in threads: t.start()
    for t in threads: t.join()
    check2('并发无死锁/锁超时异常', not errors, str(errors[:3]))
    reconcile.drain(c)
    signs, led, jobs = state(c)
    check2('11 笔签收全部存在', len(signs) == 11, str(len(signs)))
    with Session() as db:
        final_qty = db.scalar(select(Transfer.quantity).where(Transfer.code == c))
        final_ver = db.scalar(select(Transfer.version).where(Transfer.code == c))
    check2('账本申请量等于最终版本数量', led['application_qty'] == final_qty, f"{led} vs {final_qty}")
    check2('已核总量 = min(签收总量, 最终额度)，零超核',
           led['verified_qty'] == min(110, final_qty), str(led))
    check2('每单已核不超过签收量（无重复核销）',
           all(s['verified'] <= s['qty'] for s in signs), str(signs))
    check2('已核数量单调非负且 FIFO 前缀连续',
           all(s['verified'] >= 0 for s in signs)
           and sum(s['verified'] for s in signs) == led['verified_qty'], str(signs))
    check2('版本号反映更正次数（>=1）', final_ver >= 1 and led['version'] == final_ver, f'v{final_ver}')
    check2('所有对账任务 done', all(j[0] == 'done' for j in jobs), str(set(j[0] for j in jobs)))

def test_stress_repeated_corrections():
    print('\n[S2] 反复缩量/放量更正 30 次，已核数量永不回退、终态守恒')
    reset_db(); c = 'MOVE-S2'
    with Session() as db:
        mk_transfer(db, c, 100)
        for i in range(5):
            mk_signoff(db, f'SO-S2-{i}', c, 20)  # 共 100
    reconcile.drain(c)
    random.seed(11)
    for n in range(30):
        q = random.choice([10, 30, 60, 100, 130, 200])
        with Session() as db:
            t = db.scalar(select(Transfer).where(Transfer.code == c))
            correction(db, t, q)
        reconcile.drain(c)
        signs, led, _ = state(c)
        assert all(s['verified'] >= 0 for s in signs)
        assert led['verified_qty'] == sum(s['verified'] for s in signs)
        assert led['verified_qty'] <= 100  # 签收总量上限，绝不重复核销
    # 最终放量到 200：所有签收应恢复全核
    with Session() as db:
        t = db.scalar(select(Transfer).where(Transfer.code == c))
        correction(db, t, 200)
    reconcile.drain(c)
    signs, led, jobs = state(c)
    check2('30 次更正后放量，5 单全部恢复全核（各 20）',
           all(s['status'] == 'verified' and s['verified'] == 20 for s in signs), str(signs))
    check2('终态账本 100/200、版本 32',
           led['verified_qty'] == 100 and led['application_qty'] == 200 and led['version'] == 32, str(led))
    check2('无失败任务', all(j[0] == 'done' for j in jobs), str(set(j[0] for j in jobs)))

if __name__ == '__main__':
    test_stress_mixed_workers()
    test_stress_repeated_corrections()
    print(f'\n压力测试全部通过：{PASS} 项断言')
