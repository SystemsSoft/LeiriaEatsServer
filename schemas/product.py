# Arquivo: schemas/product.py
from pydantic import BaseModel
from typing import Optional

class ProductCreateRequest(BaseModel):
    name: str
    description: str
    price: float
    image_url: Optional[str] = None
    restaurant_gid: Optional[str] = None
    restaurant_id: Optional[str] = None # Fallback para apps antigos que enviam restaurant_id com GID
    category: str
    preparation_time: Optional[str] = None

    # Novas colunas para IA
    ingredients: Optional[str] = None
    allergens: Optional[str] = None
    dietary_tags: Optional[str] = None
    spice_level: Optional[str] = "não picante"
    serves_people: Optional[int] = None
    portion_size: Optional[str] = None
    calories: Optional[int] = None
    is_popular: Optional[bool] = False
    is_available: Optional[bool] = True
    preparation_time_minutes: Optional[int] = None
    recommended_for: Optional[str] = None
    search_tags: Optional[str] = None

class ProductResponse(BaseModel):
    id: int
    name: str
    gid: Optional[str] = ""
    description: str
    price: float
    image_url: Optional[str]
    restaurant_gid: Optional[str] = ""
    category: str
    preparation_time: Optional[str]
    rating: Optional[float] = None

    # Novas colunas para IA
    ingredients: Optional[str] = None
    allergens: Optional[str] = None
    dietary_tags: Optional[str] = None
    spice_level: Optional[str] = None
    serves_people: Optional[int] = None
    portion_size: Optional[str] = None
    calories: Optional[int] = None
    is_popular: Optional[bool] = None
    is_available: Optional[bool] = None
    preparation_time_minutes: Optional[int] = None
    recommended_for: Optional[str] = None
    search_tags: Optional[str] = None

    class Config:
        from_attributes = True