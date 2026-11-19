from sqlalchemy import select
from src.models.entities import Signoff, Transfer, Audit
from src.utils.serialization import record
from src.constants.enums import PENDING_REASON_LABELS
from . import reconcile

def listing(db,transfer_code=None):
    stmt=select(Signoff).order_by(Signoff.id)
    if transfer_code:
        stmt=stmt.where(Signoff.transfer_code==transfer_code)
    rows=[record(x) for x in db.scalars(stmt)]
    for row in rows:
        row['pending_reason_label']=PENDING_REASON_LABELS.get(row['pending_reason'],'') if row['pending_reason'] else ''
        row['has_transfer']=db.scalar(select(Transfer.id).where(Transfer.code==row['transfer_code'])) is not None
    return rows

def register(db,data,user):
    # 幂等登记：同一签收单编号重试直接返回原单，不重复入队、不重复核销。
    existing=db.scalar(select(Signoff).where(Signoff.code==data.code))
    if existing:
        return record(existing), False
    obj=Signoff(code=data.code,transfer_code=data.transfer_code,
                quantity=data.quantity,status='pending',pending_reason='queued',verified_qty=0)
    db.add(obj)
    db.add(Audit(actor=user.email,action=f'登记签收单 {data.code}（{data.transfer_code} × {data.quantity}）'))
    # 申请可能尚未到：先 UPSERT 账本占位并持锁；申请补齐后的对账任务会自动核销本单。
    reconcile.lock_ledger(db,data.transfer_code)
    reconcile.enqueue(db,data.transfer_code,'signoff')
    try:
        db.commit()
    except Exception:
        db.rollback();raise
    return record(obj), True
