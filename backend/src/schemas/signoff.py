from pydantic import BaseModel, Field
class SignoffInput(BaseModel):
    code: str=Field(min_length=2,max_length=80)
    transfer_code: str=Field(min_length=2,max_length=80)
    quantity: int=Field(ge=1,le=100000)
