from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint, Index, text
from datetime import datetime, timezone
from src.config.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True);email=Column(String(120),unique=True,nullable=False)
    password=Column(String(300),nullable=False);role=Column(String(20),nullable=False)
class Sample(Base):
    __tablename__='samples'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    title=Column(String(160),nullable=False);quantity=Column(Integer,nullable=False);location=Column(String(120),nullable=False)
class Transfer(Base):
    """移交申请。同一编号的更正不新增行，只递增 version 并覆盖数量等字段；对账按最新版本计算。"""
    __tablename__='transfers'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False);destination=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);note=Column(String(300),default='')
    version=Column(Integer,nullable=False,default=1,server_default='1')
class Trial(Base):
    __tablename__='trials'
    id=Column(Integer,primary_key=True);name=Column(String(120),nullable=False)
    transfer_id=Column(Integer,ForeignKey('transfers.id'),nullable=False);timeout_ms=Column(Integer,nullable=False)
    status=Column(String(20),default='queued',nullable=False);result=Column(JSON,nullable=True)
    created_at=Column(DateTime(timezone=True),default=utcnow);error=Column(Text,default='')
class TrialBalance(Base):
    __tablename__='trial_balances'
    trial_id=Column(Integer,ForeignKey('trials.id'),primary_key=True);remaining=Column(Integer,nullable=False);received=Column(Integer,nullable=False,default=0)
class Receipt(Base):
    __tablename__='receipts'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False)
    actor=Column(String(20),nullable=False);quantity=Column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('trial_id','actor'),)
class Signoff(Base):
    """签收单。以 transfer_code 字符串关联申请，允许申请尚未入库时先行登记（乱序到达）。
    verified_qty 已核数量不回退；status=verified 表示整单核销完毕，pending 表示待核（含部分核销）。"""
    __tablename__='signoffs'
    id=Column(Integer,primary_key=True)
    code=Column(String(80),unique=True,nullable=False)
    transfer_code=Column(String(80),nullable=False,index=True)
    quantity=Column(Integer,nullable=False)
    verified_qty=Column(Integer,nullable=False,default=0,server_default='0')
    status=Column(String(20),nullable=False,default='pending',server_default='pending')
    pending_reason=Column(String(40),nullable=True)
    created_at=Column(DateTime(timezone=True),default=utcnow)
    updated_at=Column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)
    __table_args__=(
        Index('ix_signoffs_transfer_order','transfer_code','id'),
        Index('ix_signoffs_pending','transfer_code',postgresql_where=text("status = 'pending'")),
    )
class TransferLedger(Base):
    """每个申请编号一行的核销额度账本。worker 在行锁内重算，并发争同一余额时在此排队。"""
    __tablename__='transfer_ledgers'
    transfer_code=Column(String(80),primary_key=True)
    application_qty=Column(Integer,nullable=False,default=0,server_default='0')
    verified_qty=Column(Integer,nullable=False,default=0,server_default='0')
    version=Column(Integer,nullable=False,default=0,server_default='0')
    # 分批续跑游标：已扫描到的最大 signoff id；新签收/更正事件归零，触发全量重算。
    last_cursor_id=Column(Integer,nullable=False,default=0,server_default='0')
    updated_at=Column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)
class ReconJob(Base):
    """对账任务。partial unique index 保证同一申请同时只有一个活动任务；中断后 running 可被重置续跑。"""
    __tablename__='recon_jobs'
    id=Column(Integer,primary_key=True)
    transfer_code=Column(String(80),nullable=False,index=True)
    reason=Column(String(40),nullable=False,default='signoff')
    status=Column(String(20),nullable=False,default='queued',server_default='queued')
    attempts=Column(Integer,nullable=False,default=0,server_default='0')
    error=Column(Text,nullable=False,default='')
    started_at=Column(DateTime(timezone=True),nullable=True)
    created_at=Column(DateTime(timezone=True),default=utcnow)
    updated_at=Column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)
    __table_args__=(
        Index('uq_recon_active_per_transfer','transfer_code',unique=True,postgresql_where=text("status IN ('queued','running')")),
        Index('ix_recon_pick','status','id'),
    )
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);actor=Column(String(120),nullable=False);action=Column(String(160),nullable=False)
    created_at=Column(DateTime(timezone=True),default=utcnow)
