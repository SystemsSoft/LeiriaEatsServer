#!/usr/bin/env python
"""
Script de teste da API POST /product
"""
import json
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_create_product():
    """Testa a criação de um produto via HTTP"""
    
    # Dados do produto a criar
    payload = {
        "name": "Hambúrguer Clássico",
        "description": "Hambúrguer com pão integral",
        "price": 8.50,
        "image_url": "https://example.com/hamburger.jpg",
        "restaurant_id": 53,
        "category": "Hambúrgueres",
        "preparation_time": "10 minutos"
    }
    
    print("📤 Enviando POST /product...")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    
    response = client.post("/product", json=payload)
    
    print(f"\n📥 Status Code: {response.status_code}")
    print(f"   Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code == 201:
        print("\n✅ Sucesso! Produto criado com sucesso!")
        return True
    else:
        print("\n❌ Erro! Não foi possível criar o produto")
        return False

if __name__ == "__main__":
    print("🔍 Testando API POST /product\n")
    test_create_product()

