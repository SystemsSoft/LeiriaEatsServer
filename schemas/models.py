from pydantic import BaseModel
from typing import List

# --- MODELOS ---
class Product(BaseModel):
    name: str
    price: float
    description: str

class Restaurant(BaseModel):
    id: int
    name: str
    category: str
    rating: float
    image_url: str
    menu: List[Product]

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
        image_url: str = "https://i.imgur.com/9i6w0X8.png"  # Imagem padrão se não enviar
        rating: float = 5.0