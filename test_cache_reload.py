"""
Script para testar se o restaurante ID 8 aparece nas buscas após forçar reload do cache
"""
from core.database import engine
from sqlalchemy.orm import Session
from services.ai_service import AIService

with Session(engine) as db:
    # Força reload do cache
    print("🔄 Recarregando cache do AIService...")
    AIService.reload_data(db)

    # Verifica se o restaurante 8 está no cache
    if AIService._data_cache:
        print(f"\n✅ Cache contém {len(AIService._data_cache)} restaurantes:")
        for r in AIService._data_cache:
            print(f"   ID: {r.id} | Nome: {r.name} | Status: {r.status if hasattr(r, 'status') else 'N/A'}")

        # Verifica especificamente o ID 8
        rest_8 = next((r for r in AIService._data_cache if r.id == 8), None)
        if rest_8:
            print(f"\n🎉 SUCESSO: Restaurante ID 8 '{rest_8.name}' está no cache!")
            print(f"   Categoria: {rest_8.category}")
            print(f"   Rating: {rest_8.rating}")
            print(f"   Produtos: {len(rest_8.products) if rest_8.products else 0}")
        else:
            print(f"\n❌ PROBLEMA: Restaurante ID 8 NÃO está no cache")
    else:
        print("❌ Cache está vazio!")

    # Testa a busca
    print("\n" + "="*60)
    print("🔍 Testando busca por 'Thai'...")
    result = AIService.process_search("Thai", db, scope="restaurant")
    print(f"   Reply: {result.reply}")
    print(f"   Intent: {result.intent}")
    print(f"   Restaurantes encontrados: {len(result.restaurantResults)}")
    if result.restaurantResults:
        for r in result.restaurantResults:
            print(f"      - ID: {r.id} | Nome: {r.name}")

    print("\n" + "="*60)
    print("🔍 Testando busca por 'restaurantes' (ver todos)...")
    result = AIService.process_search("restaurantes", db, scope="restaurant")
    print(f"   Reply: {result.reply}")
    print(f"   Restaurantes encontrados: {len(result.restaurantResults)}")
    if result.restaurantResults:
        for r in result.restaurantResults:
            print(f"      - ID: {r.id} | Nome: {r.name}")

