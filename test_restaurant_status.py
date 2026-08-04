"""
Testa o fluxo de status do restaurante:
1. Criar restaurante → status=PENDING
2. Criar conta Stripe → status=STRIPE_PENDING
3. Completar onboarding → status=ACTIVE (via webhook)
"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_restaurant_creation_flow():
    print("🧪 Testando fluxo de criação de restaurante e status...\n")

    # 1️⃣ Criar um novo restaurante
    print("1️⃣ Criando novo restaurante...")
    restaurant_data = {
        "name": "Restaurante Teste Status",
        "category": "Pizza",
        "phone": "244123456",
        "address": "Rua Teste, Leiria",
        "image_url": "https://example.com/image.jpg",
        "latitude": 39.7436,
        "longitude": -8.8071,
        "login": f"teste_status_{int(1000 * __import__('time').time())}",
        "password": "senha123",
        "license": "LIC-12345",
        "plan": "BASIC"
    }

    try:
        response = requests.post(f"{BASE_URL}/companies", json=restaurant_data)
        if response.status_code == 201:
            restaurant = response.json()
            restaurant_id = restaurant["id"]
            print(f"✅ Restaurante criado com ID: {restaurant_id}")
            print(f"   Status: {restaurant.get('status', 'N/A')}")
            print(f"   Stripe Account ID: {restaurant.get('stripe_account_id', 'N/A')}")

            # Verifica se o status é PENDING
            if restaurant.get("status") == "PENDING":
                print("   ✅ Status inicial correto: PENDING\n")
            else:
                print(f"   ❌ Status inicial incorreto: {restaurant.get('status')}\n")
                return

            # 2️⃣ Criar conta Stripe (onboarding)
            print("2️⃣ Iniciando onboarding Stripe...")
            onboarding_response = requests.post(f"{BASE_URL}/connect/onboarding/{restaurant_id}")

            if onboarding_response.status_code == 200:
                onboarding_data = onboarding_response.json()
                print(f"✅ Link de onboarding criado")
                print(f"   URL: {onboarding_data.get('url', 'N/A')[:80]}...\n")

                # Verifica o status após criar a conta Stripe
                print("3️⃣ Verificando status após criação da conta Stripe...")
                get_response = requests.get(f"{BASE_URL}/companies/{restaurant_id}")
                if get_response.status_code == 200:
                    updated_restaurant = get_response.json()
                    print(f"   Status: {updated_restaurant.get('status', 'N/A')}")
                    print(f"   Stripe Account ID: {updated_restaurant.get('stripe_account_id', 'N/A')[:30]}...")

                    if updated_restaurant.get("status") == "STRIPE_PENDING":
                        print("   ✅ Status atualizado corretamente: STRIPE_PENDING")
                        print("\n✅ TESTE PASSOU! O fluxo está funcionando corretamente.")
                        print("\n📝 Próximos passos:")
                        print("   - Quando o restaurante completar o onboarding no Stripe,")
                        print("   - o webhook 'account.updated' atualizará o status para 'ACTIVE'")
                    else:
                        print(f"   ❌ Status incorreto após onboarding: {updated_restaurant.get('status')}")
                else:
                    print(f"❌ Erro ao buscar restaurante: {get_response.status_code}")
            else:
                print(f"❌ Erro ao criar onboarding: {onboarding_response.status_code}")
                print(f"   Resposta: {onboarding_response.text}")
        else:
            print(f"❌ Erro ao criar restaurante: {response.status_code}")
            print(f"   Resposta: {response.text}")

    except requests.exceptions.ConnectionError:
        print("❌ Erro: O servidor não está rodando em http://localhost:8000")
        print("   Execute: uvicorn main:app --reload")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")

if __name__ == "__main__":
    test_restaurant_creation_flow()

