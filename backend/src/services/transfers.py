from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Transfer,Sample,Audit
from src.utils.serialization import record
from src.constants.enums import PENDING_REASON_LABELS
from . import reconcile

def _signoff_summary(db,code):
    return db.execute(text(
        "SELECT COUNT(*) AS signoff_count,"
        "COALESCE(SUM(verified_qty),0) AS verified_qty,"
        "COALESCE(SUM(quantity),0) AS signoff_qty,"
        "COUNT(*) FILTER (WHERE status='pending') AS pending_count "
        "FROM signoffs WHERE transfer_code=:code"),{'code':code}).mappings().one()

def _pending_reasons(db,code):
    rows=db.execute(text(
        "SELECT pending_reason,COUNT(*) AS n FROM signoffs "
        "WHERE transfer_code=:code AND status='pending' GROUP BY pending_reason"
    ),{'code':code}).all()
    return [{'reason':r,'label':PENDING_REASON_LABELS.get(r,r),'count':n} for r,n in rows]

def listing(db):
    # 附带每个申请的签收/核销汇总，供移交页展示已核数量与待核原因。
    rows=[record(x) for x in db.scalars(select(Transfer).order_by(Transfer.id))]
    for row in rows:
        agg=_signoff_summary(db,row['code'])
        row.update(dict(agg))
        row['pending_reasons']=_pending_reasons(db,row['code'])
    return rows

def create(db,data,user):
    sample=db.get(Sample,data.sample_id)
    if not sample: raise HTTPException(404,'样本不存在')
    if data.quantity>sample.quantity: raise HTTPException(422,'移交量超过样本可用量')
    if db.scalar(select(Transfer.id).where(Transfer.code==data.code)):
        raise HTTPException(409,'申请编号已存在，如需调整请使用更正功能（按版本认最新）')
    obj=Transfer(**data.model_dump())
    db.add(obj)
    db.add(Audit(actor=user.email,action='创建流转申请 '+data.code))
    # 申请补齐：与对账互斥地锁账本并入队，先到的签收单将被自动核销；整段单事务原子提交。
    reconcile.lock_ledger(db,data.code)
    reconcile.enqueue(db,data.code,'transfer_arrived')
    try:
        db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'编号已存在或引用对象无效，请调整后重试')
    return record(obj)

def correct(db,ident,data,user):
    obj = ident if isinstance(ident, Transfer) else db.get(Transfer,ident)
    if not obj: raise HTTPException(404,'流转申请不存在')
    sample=db.get(Sample,obj.sample_id)
    if data.quantity>sample.quantity: raise HTTPException(422,'更正后数量超过样本可用量')
    # 持同一把账本锁：与正在进行的对账、并发新签收争同一余额时排队，绝不漏核/重核。
    reconcile.lock_ledger(db,obj.code)
    obj.quantity=data.quantity
    obj.destination=data.destination
    obj.note=data.note
    obj.version+=1
    db.add(Audit(actor=user.email,action=f'更正流转申请 {obj.code} 至 v{obj.version}（{data.quantity} 份）'))
    reconcile.enqueue(db,obj.code,'correction')
    db.commit()
    return record(obj)
