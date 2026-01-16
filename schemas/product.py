from pydantic import BaseModel
from typing import Optional

class ProductCreateRequest(BaseModel):
    name: str
    description: str
    price: float
    image_url: Optional[str] = None
    restaurant_id: int

class ProductResponse(BaseModel):
    id: int
    name: str
    description: str
    price: float
    image_url: Optional[str]
    restaurant_id: int

    class Config:
        from_attributes = True