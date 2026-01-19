# Arquivo: core/sql_models.py
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
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

    products = relationship("ProductDB", back_populates="restaurant")


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(Float)
    image_url = Column(String(500))
    category = Column(String, default="Geral")

    # ForeignKey aponta para a tabela 'restaurants', coluna 'id'
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    restaurant = relationship("RestaurantDB", back_populates="products")