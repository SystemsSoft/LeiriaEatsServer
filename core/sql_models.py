# Arquivo: core/sql_models.py
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from sqlalchemy.orm import relationship
from core.database import Base

# --- MODELO DO RESTAURANTE/EMPRESA ---
class RestaurantDB(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    # Campos novos que adicionamos recentemente
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    # Campos antigos
    category = Column(String(100))
    rating = Column(Float)
    image_url = Column(String(500))

    # RELACIONAMENTO (Simplificado):
    # Dizemos apenas: "Meu filho é ProductDB, e ele me conhece como 'restaurant'"
    products = relationship("ProductDB", back_populates="restaurant")


# --- MODELO DO PRODUTO ---
class ProductDB(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(Float)
    image_url = Column(String(500))

    # CHAVE ESTRANGEIRA (O ponto crucial do erro):
    # Esta linha diz explicitamente: "Esta coluna guarda o ID da tabela 'restaurants'"
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    # RELACIONAMENTO INVERSO:
    # Dizemos: "Meu pai é RestaurantDB, e ele me conhece como 'products'"
    restaurant = relationship("RestaurantDB", back_populates="products")