#!/usr/bin/env python
"""
Script de teste para diagnosticar o erro ao criar produtos
"""
import sys
sys.path.insert(0, '.')

from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.sql_models import ProductDB, RestaurantDB

def test_product_creation():
    db: Session = SessionLocal()
    
    try:
        # Verifica se existe restaurante
        restaurant = db.query(RestaurantDB).first()
        if not restaurant:
            print("⚠️  Nenhum restaurante encontrado no banco")
            return
        
        print(f"✓ Restaurante encontrado: {restaurant.name} (ID: {restaurant.id})")
        
        # Tenta criar um produto
        new_product = ProductDB(
            name="Teste Produto",
            description="Descrição de teste",
            price=10.50,
            image_url="https://example.com/image.jpg",
            restaurant_id=restaurant.id,
            category="Teste",
            preparation_time="15 minutos"
        )
        
        print(f"📝 Objeto criado: {new_product.name}")
        print(f"   Atributos: name={new_product.name}, price={new_product.price}, preparation_time={new_product.preparation_time}")
        
        db.add(new_product)
        db.commit()
        db.refresh(new_product)
        
        print(f"✅ Produto criado com sucesso! ID: {new_product.id}")
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ Erro ao criar produto:")
        print(f"   Tipo: {type(e).__name__}")
        print(f"   Mensagem: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    print("🔍 Testando criação de produto...\n")
    test_product_creation()

