from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from src.config.database import db_session
from src.utils.security import current,writer
from src.models.entities import ReconJob
from src.utils.serialization import record
from src.services import reconcile
router=APIRouter()
@router.get('/recon/jobs')
def listing(db=Depends(db_session),user=Depends(current)):
    return [record(x) for x in db.scalars(select(ReconJob).order_by(ReconJob.id.desc()).limit(100))]
@router.post('/recon/jobs/{ident}/retry')
def retry(ident:int,db=Depends(db_session),user=Depends(writer)):
    job=db.get(ReconJob,ident)
    if not job:
        raise HTTPException(404,'对账任务不存在')
    if job.status in ('queued','running'):
        return record(job)
    # 重试不重复核销：重置任务后由幂等的对账重算，结果只取决于当前行数据。
    job.status='queued';job.error='';job.updated_at=reconcile.utcnow()
    db.commit()
    return record(job)
