"""
Script de teste rápido do Gemini
Execute: python test_gemini.py
"""
from services.gemini_sales_service import GeminiSalesAgent
import json


def test_gemini():
    """Testa o Gemini com exemplos reais"""

    print("🧪 TESTANDO GEMINI SALES AGENT")
    print("=" * 60)

    # Inicializar
    print("\n1️⃣ Inicializando Gemini...")
    try:
        GeminiSalesAgent.initialize()
        print("✅ Gemini pronto!")
    except Exception as e:
        print(f"❌ Erro: {e}")
        return

    # Produtos de exemplo
    products = [
        {
            "id": 1,
            "name": "Pizza Margherita",
            "price": 35.00,
            "description": "Molho de tomate, mussarela e manjericão",
            "serves_people": 2,
            "preparation_time_minutes": 30,
            "is_popular": True
        },
        {
            "id": 2,
            "name": "Pizza Calabresa",
            "price": 38.00,
            "description": "Calabresa, cebola e azeitona",
            "serves_people": 2,
            "preparation_time_minutes": 30
        }
    ]

    # Testes
    test_cases = [
        ("oi", "Saudação simples"),
        ("pizza", "Busca genérica"),
        ("quero pizza margherita", "Pedido específico"),
        ("estou entre pizza e mexicana", "Dúvida entre opções"),
        ("para quantas pessoas serve?", "Pergunta sobre porção"),
    ]

    print("\n2️⃣ Testando conversações:")
    print("-" * 60)

    for i, (message, description) in enumerate(test_cases, 1):
        print(f"\n📝 Teste {i}: {description}")
        print(f"👤 Usuário: \"{message}\"")

        context = {
            "products": products if "pizza" in message.lower() else [],
            "cart": [],
            "has_results": "pizza" in message.lower()
        }

        try:
            response = GeminiSalesAgent.generate_response(message, context)
            print(f"🤖 Gemini: \"{response}\"")
        except Exception as e:
            print(f"❌ Erro: {e}")

    # Status final
    print("\n" + "=" * 60)
    print("3️⃣ Status de uso da API:")
    usage = GeminiSalesAgent.get_usage_status()
    print(f"📊 Requisições hoje: {usage['requests_today']}/{usage['limit_daily']}")
    print(f"📊 Restantes: {usage['remaining_today']}")
    print(f"📊 Uso: {usage['percentage_used']:.1f}%")

    cache = GeminiSalesAgent.get_cache_status()
    print(f"💾 Cache: {cache['entries']} entradas")

    print("\n✅ Teste concluído!")


if __name__ == "__main__":
    test_gemini()

