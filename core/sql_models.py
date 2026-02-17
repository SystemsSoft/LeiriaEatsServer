# Arquivo: core/sql_models.py
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, Boolean
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
    stripe_account_id = Column(String, nullable=False)
    stripe_onboarding_completed = Column(Boolean, default=False)

    products = relationship("ProductDB", back_populates="restaurant")


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(Float)
    image_url = Column(String(500))
    category = Column(String, default="Geral")
    preparation_time = Column(String(100), nullable=True)

    # ForeignKey aponta para a tabela 'restaurants', coluna 'id'
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    restaurant = relationship("RestaurantDB", back_populates="products")


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String)
    delivery_address = Column(String)
    status = Column(String, default="Pendente")
    total = Column(Float)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    user_id = Column(String)
    restaurant_name = Column(String)

    restaurant = relationship("RestaurantDB")
    items = relationship("OrderItemDB", back_populates="order")


class OrderItemDB(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))

    observation = Column(String, nullable=True)
    product_name = Column(String)
    price = Column(Float)
    quantity = Column(Integer)

    order = relationship("OrderDB", back_populates="items")