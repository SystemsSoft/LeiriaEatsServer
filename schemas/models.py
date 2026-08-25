from pydantic import BaseModel
from typing import List, Optional

# --- MODELO DO PRODUTO ---
class Product(BaseModel):
    id: int
    gid: Optional[str] = ""
    restaurant_gid: Optional[str] = ""
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
    id: int # ID interno para JOINs
    gid: Optional[str] = "" # Global ID para o Frontend
    name: str
    category: str
    rating: Optional[float] = None
    image_url: Optional[str] = None
    plan: Optional[str] = None
    is_closed: Optional[bool] = None  # Estado de encerramento do restaurante no dia/hora atual
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    has_surprise_box: bool = False
    surprise_box_qty: int = 0

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
class OrderItemRequest(BaseModel):
    product_gid: str
    quantity: int
    observation: Optional[str] = None

class SubOrderRequest(BaseModel):
    gid: str = ""
    order_gid: str = ""
    restaurant_gid: str
    restaurant_name: str
    restaurant_image_url: Optional[str] = None
    restaurant_category: str
    items: List[OrderItemRequest]
    delivery_fee: float = 0.0
    base_time: int = 0

class OrderRequest(BaseModel):
    gid: str = ""
    user_id: str
    user_name: str
    user_address: str
    user_phone: str
    save_payment_method: bool = False
    search_query: str = ""
    tracking_code: str = ""
    delivery_type: str = ""
    order_type: str = "COMMON"
    delivery_latitude: Optional[float] = None
    delivery_longitude: Optional[float] = None
    total_delivery_fee: float = 0.0
    total_service_fee: float = 0.0
    sub_orders: List[SubOrderRequest] = []


class OrderItemResponse(BaseModel):
    product_name: str
    quantity: int
    description: Optional[str] = None
    image_url: Optional[str] = None
    price: float
    observation: Optional[str] = None

    class Config:
        from_attributes = True


class SubOrderResponse(BaseModel):
    id: int
    gid: str
    restaurant_gid: str
    restaurant_name: str
    restaurant_category: str
    restaurant_image_url: Optional[str] = None
    status: str
    total: float
    delivery_fee: float
    base_time: int
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    vehicle_type: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_plate: Optional[str] = None
    vehicle_color: Optional[str] = None
    items: List[OrderItemResponse]

    class Config:
        from_attributes = True

class OrderResponse(BaseModel):
    id: int
    gid: str
    customer_name: str
    delivery_address: str
    total: float
    status: str
    tracking_code: Optional[str] = ""
    delivery_type: Optional[str] = ""
    order_type: Optional[str] = "COMMON"
    delivery_latitude: Optional[float] = None
    delivery_longitude: Optional[float] = None
    total_delivery_fee: float = 0.0
    total_service_fee: float = 0.0
    sub_orders: List[SubOrderResponse] = []

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
    product_gid: str
    rating: int  # 1–5

class RatingRequest(BaseModel):
    order_id: str
    restaurant_gid: str
    ratings: List[RatingItemRequest]

class LoginRequest(BaseModel):
        username: str  # Pode ser o email ou login
        password: str

class LoginResponse(BaseModel):
        id: int

class DeliveryFeeRequest(BaseModel):
    restaurant_gid: str
    customer_latitude: float
    customer_longitude: float
    restaurant_latitude: float
    restaurant_longitude: float
