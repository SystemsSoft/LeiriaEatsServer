from pydantic import BaseModel
from typing import List, Optional


class Product(BaseModel):
    id: int
    name: str
    price: float
    description: str
    image_url: Optional[str] = None

    class Config:
        from_attributes = True



class Restaurant(BaseModel):
    id: int
    name: str
    category: str
    rating: Optional[float] = None
    image_url: Optional[str] = None

    products: List[Product] = []

    class Config:
        from_attributes = True



class UserRequest(BaseModel):
    text: str
    user_id: str = "mobile_user"


class SearchResponse(BaseModel):
    reply: str
    intent: str
    results: List[Restaurant]

class RestaurantCreate(BaseModel):
    name: str
    category: str
    image_url: str = "https://i.imgur.com/9i6w0X8.png"
    rating: float = 5.0