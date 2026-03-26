from pydantic import BaseModel
from typing import List, Optional

# --- MODELO DO PRODUTO ---
class Product(BaseModel):
    id: int
    restaurant_id: int
    name: str
    price: float
    description: str
    category: str
    image_url: Optional[str] = None
    preparation_time: Optional[str] = "20-30 min"
    quantity: Optional[int] = 1  # Quantidade detectada pela IA (padrão: 1)
    rating: Optional[float] = None  # Rating médio do produto

    class Config:
        from_attributes = True

# --- MODELO DO RESTAURANTE ---
class Restaurant(BaseModel):
    id: int
    name: str
    category: str
    rating: Optional[float] = None
    image_url: Optional[str] = None
    is_closed: Optional[bool] = None  # Estado de encerramento do restaurante no dia/hora atual
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # O nome aqui deve ser 'products' para bater com o banco de dados
    products: List[Product] = []

    class Config:
        from_attributes = True

# --- MODELOS DE INTERAÇÃO (CHAT/BUSCA) ---
class UserRequest(BaseModel):
    text: str
    user_id: str = "mobile_user"

class SearchResponse(BaseModel):
    reply: str
    intent: str
    restaurantResults: List[Restaurant]
    productResults: List[Product]

# --- MODELOS DE GESTÃO ---
class RestaurantCreate(BaseModel):
    name: str
    category: str
    image_url: str = "https://i.imgur.com/9i6w0X8.png"
    rating: float = 5.0

# --- MODELOS DE PEDIDO (ORDER) ---
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int
    observation: Optional[str] = None

class OrderCreate(BaseModel):
    user_id: str
    user_name: str
    user_address: str
    user_phone: str
    delivery_latitude:  Optional[float] = None   # coordenadas do endereço de entrega
    delivery_longitude: Optional[float] = None
    restaurant_id: int
    restaurant_name: str
    restaurant_category: str
    restaurant_image_url: Optional[str] = None
    payment_intent_id: Optional[str] = None
    save_payment_method: bool = False
    search_query: str = ""
    tracking_code: str = ""
    delivery_type: Optional[str] = None
    base_time: int = 0        # opcional — restaurante define depois via PATCH
    items: List[OrderItemCreate]


class OrderItemResponse(BaseModel):
    product_name: str
    quantity: int
    description: str
    image_url: str
    price: float
    observation: Optional[str] = None

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    customer_name: str
    delivery_address: str
    total: float
    status: str
    restaurant_name: str
    restaurant_category: str
    restaurant_image_url: Optional[str] = None
    tracking_code: Optional[str] = ""
    delivery_type: Optional[str] = ""
    base_time: Optional[int] = None
    delivery_latitude: Optional[float] = None
    delivery_longitude: Optional[float] = None
    restaurant_latitude: Optional[float] = None
    restaurant_longitude: Optional[float] = None
    items: List[OrderItemResponse]

    class Config:
        from_attributes = True

class OrderStatusUpdate(BaseModel):
    status: str

class OrderStatusResponse(BaseModel):
    message: str
    status: str
    driver_name: Optional[str] = None
    tracking_code: Optional[str] = None

class RatingItemRequest(BaseModel):
    product_id: int
    rating: int  # 1–5

class RatingRequest(BaseModel):
    order_id: str
    restaurant_id: int
    ratings: List[RatingItemRequest]

class LoginRequest(BaseModel):
        username: str  # Pode ser o email ou login
        password: str

class LoginResponse(BaseModel):
        id: int

class DeliveryFeeRequest(BaseModel):
    customer_latitude: float
    customer_longitude: float
    restaurant_latitude: float
    restaurant_longitude: float
