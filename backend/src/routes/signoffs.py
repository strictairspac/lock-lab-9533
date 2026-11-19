from fastapi import APIRouter,Depends, Response
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.signoff import SignoffInput
from src.services import signoffs as service
router=APIRouter()
@router.get('/signoffs')
def listing(transfer_code:str|None=None,db=Depends(db_session),user=Depends(current)):
    return service.listing(db,transfer_code)
@router.post('/signoffs')
def register(data:SignoffInput,response:Response,db=Depends(db_session),user=Depends(writer)):
    obj, created = service.register(db,data,user)
    response.status_code = 201 if created else 200  # 幂等重试返回 200
    return obj
