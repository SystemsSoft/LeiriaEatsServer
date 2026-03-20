# Arquivo: core/sql_models.py
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from core.database import Base


class RestaurantDB(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    category = Column(String(100))
    rating = Column(Float)
    image_url = Column(String(500))
    login = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    license = Column(String(100), nullable=True)
    plan = Column(String(50), nullable=True)
    stripe_account_id = Column(String(255), nullable=False)
    stripe_onboarding_completed = Column(Boolean, default=False)

    products = relationship("ProductDB", back_populates="restaurant")
    hours = relationship("RestaurantHourDB", back_populates="restaurant")


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(Float)
    image_url = Column(String(500))
    category = Column(String(100), default="Geral")
    preparation_time = Column(String(100), nullable=True)
    rating = Column(Float, nullable=True, default=None)

    # ForeignKey aponta para a tabela 'restaurants', coluna 'id'
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    restaurant = relationship("RestaurantDB", back_populates="products")


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(255))
    delivery_address = Column(String(500))
    status = Column(String(50), default="Pendente")
    total = Column(Float)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    user_id = Column(String(255))
    restaurant_name = Column(String(255))
    payment_intent_id = Column(String(255))
    stripe_customer_id = Column(String(255), nullable=True)
    restaurant_category = Column(String(100))
    restaurant_image_url = Column(String(500))
    tracking_code = Column(String(100), nullable=True, default="")
    delivery_type = Column(String(50), nullable=True)

    restaurant = relationship("RestaurantDB")
    items = relationship("OrderItemDB", back_populates="order")


class OrderItemDB(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))

    observation = Column(String(500), nullable=True)
    product_name = Column(String(255))
    price = Column(Float)
    quantity = Column(Integer)
    description = Column(Text)
    image_url = Column(String(500))

    order = relationship("OrderDB", back_populates="items")


class SavedPaymentMethodDB(Base):
    __tablename__ = "saved_payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), index=True, nullable=False)
    stripe_customer_id = Column(String(255), nullable=False)
    stripe_payment_method_id = Column(String(255), unique=True, nullable=False)
    card_brand = Column(String(50), nullable=True)
    card_last4 = Column(String(4), nullable=True)
    card_exp_month = Column(Integer, nullable=True)
    card_exp_year = Column(Integer, nullable=True)


class ProductRatingDB(Base):
    __tablename__ = "product_ratings"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1–5

    order = relationship("OrderDB")
    product = relationship("ProductDB")
    restaurant = relationship("RestaurantDB")

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_product_rating"),
    )


class RestaurantHourDB(Base):
    __tablename__ = "restaurant_hours"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Domingo ... 6=Sábado
    open_time = Column(String(5), nullable=False)   # "HH:mm"
    close_time = Column(String(5), nullable=False)  # "HH:mm"
    is_closed = Column(Boolean, default=False)

    restaurant = relationship("RestaurantDB", back_populates="hours")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "day_of_week", name="uq_restaurant_day"),
    )

