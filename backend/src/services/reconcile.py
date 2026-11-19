"""签收单对账与核销。

并发模型（全部围绕 transfer_ledgers 单行行锁串行化同一申请编号的写操作）：
- 登记签收 / 新建申请 / 更正申请：先 UPSERT 账本并 SELECT ... FOR UPDATE 持锁，再入对账任务；
  与正在进行的对账互斥，争同一余额时在锁上排队，对账提交后自然触发新一轮对账。
- 对账任务：SKIP LOCKED 领取，单事务内重算。已核数量 verified_qty 单调递增、绝不回退；
  更正按 transfers.version 最新版本认账，超量签收保留原已核数量并标 quota_insufficient，
  额度腾出后下一轮自动重算。
- 重试幂等：任务入队靠 partial unique index 去重；核销结果只取决于当前行数据，重跑结果不变。
"""
from datetime import datetime, timezone
from sqlalchemy import select, text
from src.config.database import Session
from src.models.entities import Signoff, Transfer, ReconJob

LOCK_TIMEOUT_SQL = text("SET LOCAL lock_timeout = '3000ms'")
# 单轮最多扫描的签收单行数；常规申请一轮扫完。超过即落游标并由续跑任务从游标继续，
# 保证对账中断后可续、单事务短。更正/新签收事件会把游标归零触发全量重算。
BATCH_LIMIT = 500


def utcnow():
    return datetime.now(timezone.utc)


def enqueue(db, transfer_code, reason='signoff'):
    """幂等入队：同一申请存在 queued/running 任务时跳过（partial unique index 兜底）。"""
    db.execute(text(
        "INSERT INTO recon_jobs(transfer_code,reason,status,attempts,error,created_at,updated_at) "
        "VALUES (:code,:reason,'queued',0,'',:now,:now) "
        "ON CONFLICT (transfer_code) WHERE status IN ('queued','running') DO NOTHING"
    ), {'code': transfer_code, 'reason': reason, 'now': utcnow()})


def lock_ledger(db, transfer_code):
    """UPSERT 并锁住宿额账本行；调用方需已开启事务。
    新签收/申请/更正事件都会把续跑游标归零，强制下一轮全量重算，杜绝漏核。"""
    db.execute(LOCK_TIMEOUT_SQL)
    db.execute(text(
        "INSERT INTO transfer_ledgers(transfer_code,application_qty,verified_qty,"
        "version,last_cursor_id,updated_at) VALUES (:code,0,0,0,0,:now) ON CONFLICT DO NOTHING"
    ), {'code': transfer_code, 'now': utcnow()})
    db.execute(text(
        "UPDATE transfer_ledgers SET last_cursor_id=0,updated_at=:now "
        "WHERE transfer_code=:code"
    ), {'code': transfer_code, 'now': utcnow()})
    return db.execute(text(
        "SELECT transfer_code,application_qty,verified_qty,version,last_cursor_id "
        "FROM transfer_ledgers WHERE transfer_code=:code FOR UPDATE"
    ), {'code': transfer_code}).mappings().one()


def reset_running(db):
    """中断续跑：把 running 任务重置为 queued。
    worker 启动时调用自愈崩溃遗留；正常运行期间不调用（单 worker 模型，不会误杀在跑任务）。"""
    rows = db.execute(text(
        "UPDATE recon_jobs SET status='queued',error='',started_at=NULL "
        "WHERE status='running' RETURNING id"
    )).all()
    if rows:
        db.commit()
    return len(rows)


def claim_next(db):
    """领取一个排队任务，多 worker 安全（FOR UPDATE SKIP LOCKED）。"""
    job = db.execute(select(ReconJob).where(ReconJob.status == 'queued')
                     .order_by(ReconJob.id).limit(1).with_for_update(skip_locked=True)).scalar()
    if not job:
        return None
    job.status = 'running'
    job.attempts += 1
    job.error = ''
    job.started_at = utcnow()
    db.commit()
    return job


def _mark_pending(db, transfer_code, reason, exclude_id=None):
    sql = ("UPDATE signoffs SET status='pending',pending_reason=:reason,updated_at=:now "
           "WHERE transfer_code=:code AND status='pending'")
    params = {'reason': reason, 'code': transfer_code, 'now': utcnow()}
    if exclude_id is not None:
        sql += ' AND id > :exclude_id'
        params['exclude_id'] = exclude_id
    return db.execute(text(sql), params).rowcount


def run_job(job_id):
    """对单个任务执行一轮对账。任何异常都落库为 failed，不吞任务。

    游标语义：transfer_ledgers.last_cursor_id 记录本申请已扫描到的最大 signoff id。
    - 常规（行数 <= BATCH_LIMIT）：游标为 0，一轮全量重算后游标回到 0；
    - 超限：每轮从游标后取 BATCH_LIMIT 行推进，全部扫完后游标归零；
    - 更正/新签收/申请到达事件在 lock_ledger 时把游标归零，强制下一轮全量重算。
    """
    with Session() as db:
        job = db.get(ReconJob, job_id)
        if not job or job.status not in ('queued', 'running'):
            return False
        if job.status == 'queued':
            job.status = 'running'
            job.attempts += 1
            job.started_at = utcnow()
            db.commit()
        code = job.transfer_code
        try:
            db.execute(LOCK_TIMEOUT_SQL)
            db.execute(text(
                "INSERT INTO transfer_ledgers(transfer_code,application_qty,verified_qty,"
                "version,last_cursor_id,updated_at) VALUES (:code,0,0,0,0,:now) ON CONFLICT DO NOTHING"
            ), {'code': code, 'now': utcnow()})
            ledger = db.execute(text(
                "SELECT * FROM transfer_ledgers WHERE transfer_code=:code FOR UPDATE"
            ), {'code': code}).mappings().one()

            transfer = db.execute(
                select(Transfer).where(Transfer.code == code).with_for_update()
            ).scalar()

            cursor = ledger['last_cursor_id'] or 0
            # 锁本申请从游标起的一批签收单，按登记先后 FIFO 认账。
            signoffs = list(db.execute(
                select(Signoff).where(Signoff.transfer_code == code, Signoff.id > cursor)
                .order_by(Signoff.id).limit(BATCH_LIMIT).with_for_update()
            ).scalars())
            max_batch_id = signoffs[-1].id if signoffs else cursor
            truncated = db.scalar(
                select(Signoff.id).where(Signoff.transfer_code == code,
                                         Signoff.id > max_batch_id).limit(1)) is not None

            now = utcnow()
            if transfer is None:
                # 申请尚未到：签收单全部可登记、待核，原因 application_missing；额度保持 0。
                for s in signoffs:
                    if s.status == 'pending':
                        s.pending_reason = 'application_missing'
                        s.updated_at = now
                db.flush()
                db.execute(text(
                    "UPDATE transfer_ledgers SET application_qty=0,version=0,last_cursor_id=:cur,"
                    "verified_qty=COALESCE((SELECT SUM(verified_qty) FROM signoffs "
                    "WHERE transfer_code=:code),0),updated_at=:now WHERE transfer_code=:code"
                ), {'code': code, 'now': now, 'cur': max_batch_id if truncated else 0})
            else:
                quota = transfer.quantity
                # 已核数量按全表聚合（含未在本批的后续行），分批续跑时口径同样正确，且绝不回退。
                already = db.execute(text(
                    "SELECT COALESCE(SUM(verified_qty),0) FROM signoffs WHERE transfer_code=:code"
                ), {'code': code}).scalar()
                remaining = quota - already
                last_id = None
                for s in signoffs:
                    last_id = s.id
                    want = s.quantity - s.verified_qty
                    if remaining < 0:
                        # 更正缩量导致已核总量超最新额度：已核数量不回退，已核销单也转待核，
                        # 腾出额度（放量更正）后由后续轮次自动重新核销。
                        s.status = 'pending'
                        s.pending_reason = 'quota_insufficient'
                        s.updated_at = now
                    elif want <= 0:
                        # 整单已核满：放量重算时恢复为已核销。
                        s.status = 'verified'
                        s.pending_reason = None
                        s.updated_at = now
                    elif remaining >= want:
                        # 额度充足：补齐本单并核销。
                        s.verified_qty += want
                        remaining -= want
                        s.status = 'verified'
                        s.pending_reason = None
                        s.updated_at = now
                    elif remaining > 0:
                        # 额度只够部分：核掉剩余额度，本单保留为待核。
                        s.verified_qty += remaining
                        remaining = 0
                        s.status = 'pending'
                        s.pending_reason = 'quota_insufficient'
                        s.updated_at = now
                    else:
                        # 已无额度：已核数量原样保留，整单待核。
                        s.status = 'pending'
                        s.pending_reason = 'quota_insufficient'
                        s.updated_at = now
                # 本批之后仍有未扫描行（中断续跑场景）：先统一原因，收尾后由新任务续跑。
                if truncated:
                    _mark_pending(db, code, 'quota_insufficient', exclude_id=last_id)
                else:
                    _mark_pending(db, code, 'quota_insufficient')

                db.flush()  # 让聚合 SQL 读到本事务内 ORM 的核销变更
                verified_total = db.execute(text(
                    "SELECT COALESCE(SUM(verified_qty),0) FROM signoffs WHERE transfer_code=:code"
                ), {'code': code}).scalar()
                db.execute(text(
                    "UPDATE transfer_ledgers SET application_qty=:qty,version=:ver,"
                    "verified_qty=:total,last_cursor_id=:cur,updated_at=:now "
                    "WHERE transfer_code=:code"
                ), {'qty': quota, 'ver': transfer.version, 'total': verified_total,
                    'now': now, 'code': code, 'cur': max_batch_id if truncated else 0})

            db.refresh(job)
            job.status = 'done'
            job.error = ''
            job.started_at = None
            job.updated_at = now
            db.commit()
            if truncated:
                # 当前任务提交后活动槽位释放，再排续跑任务（partial unique index 才不会冲突）。
                with Session() as qdb:
                    enqueue(qdb, code, 'resume')
                    qdb.commit()
            return True
        except Exception as exc:
            db.rollback()
            with Session() as faildb:
                failed = faildb.get(ReconJob, job_id)
                if failed:
                    failed.status = 'failed'
                    failed.error = str(exc)[:500]
                    failed.started_at = None
                    failed.updated_at = utcnow()
                    faildb.commit()
            raise


def run_available(limit=5, recover=False):
    """worker 入口：可选地先自愈 running 僵尸任务，再顺序处理排队任务，返回本轮处理数。"""
    processed = 0
    if recover:
        with Session() as db:
            reset_running(db)
    for _ in range(limit):
        with Session() as db:
            job = claim_next(db)
        if job is None:
            break
        run_job(job.id)
        processed += 1
    return processed


def drain(transfer_code):
    """测试/手工入口：入队并跑到该申请没有活动任务为止，返回最终账本。"""
    with Session() as db:
        enqueue(db, transfer_code, 'manual')
        db.commit()
    while True:
        run_available(limit=10)
        with Session() as db:
            active = db.scalar(select(ReconJob.id).where(
                ReconJob.transfer_code == transfer_code,
                ReconJob.status.in_(('queued', 'running'))))
            failed = db.scalar(select(ReconJob).where(
                ReconJob.transfer_code == transfer_code,
                ReconJob.status == 'failed').order_by(ReconJob.id.desc()))
            if failed:
                raise RuntimeError(failed.error)
            if active is None:
                return dict(db.execute(text(
                    "SELECT * FROM transfer_ledgers WHERE transfer_code=:code"
                ), {'code': transfer_code}).mappings().one())
