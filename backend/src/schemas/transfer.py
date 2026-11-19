from pydantic import BaseModel, Field
class TransferInput(BaseModel):
    code: str=Field(min_length=2,max_length=80)
    sample_id: int=Field(gt=0)
    destination: str=Field(min_length=2,max_length=120)
    quantity: int=Field(ge=1,le=100000)
    note: str=Field(default='',max_length=300)
class TransferCorrection(BaseModel):
    # 更正按编号认最新版本：数量与接收方可改，样本不可跨批次替换
    destination: str=Field(min_length=2,max_length=120)
    quantity: int=Field(ge=1,le=100000)
    note: str=Field(default='',max_length=300)
