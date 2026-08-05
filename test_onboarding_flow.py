#!/usr/bin/env python3
"""
Script de teste do fluxo completo de onboarding Stripe.
Testa tanto restaurantes quanto drivers.
"""

import requests
import time
import json

BASE_URL = "https://api.leiriaeats.com"

def test_restaurant_onboarding():
    """Testa o fluxo completo de onboarding de restaurante"""
    print("🏢 === TESTE: RESTAURANTE ONBOARDING ===\n")

    # 1. Criar restaurante
    print("1️⃣ Criando restaurante...")
    restaurant_data = {
        "name": "Teste Restaurante Stripe",
        "category": "Teste",
        "phone": "912345678",
        "address": "Rua Teste, 123",
        "image_url": "https://example.com/test.jpg",
        "latitude": 39.7436,
        "longitude": -8.8071,
        "login": f"teste_stripe_{int(time.time())}",
        "password": "teste123",
        "license": "PENDING",
        "plan": "BASIC",
        "rating": 4.5
    }

    response = requests.post(f"{BASE_URL}/companies", json=restaurant_data)
    print(f"   Status: {response.status_code}")

    if response.status_code != 201:
        print(f"   ❌ Erro: {response.text}")
        return

    restaurant = response.json()
    restaurant_id = restaurant.get("id")
    print(f"   ✅ Restaurante criado: ID={restaurant_id}")
    print(f"   📊 Status inicial: {restaurant.get('status')}")
    print(f"   📋 License inicial: {restaurant.get('license')}")

    # 2. Criar conta Stripe e obter link de onboarding
    print("\n2️⃣ Criando conta Stripe e gerando link de onboarding...")
    response = requests.post(f"{BASE_URL}/connect/onboarding/{restaurant_id}")
    print(f"   Status: {response.status_code}")

    if response.status_code != 200:
        print(f"   ❌ Erro: {response.text}")
        return

    onboarding_data = response.json()
    print(f"   ✅ Link de onboarding gerado")
    print(f"   🔗 URL: {onboarding_data.get('url')[:80]}...")
    print(f"   💳 Stripe Account ID: {onboarding_data.get('stripe_account_id')}")
    print(f"   📊 Status atual: {onboarding_data.get('status')}")
    print(f"   💡 Mensagem: {onboarding_data.get('message')}")

    # 3. Verificar status
    print("\n3️⃣ Verificando status no banco...")
    response = requests.get(f"{BASE_URL}/companies/{restaurant_id}")
    if response.status_code == 200:
        restaurant = response.json()
        print(f"   📊 Status: {restaurant.get('status')}")
        print(f"   📋 License: {restaurant.get('license')}")
        print(f"   💳 Stripe Account ID: {restaurant.get('stripe_account_id')}")
        print(f"   ✅ Onboarding completed: {restaurant.get('stripe_onboarding_completed')}")

    print("\n📝 INSTRUÇÕES:")
    print("   1. Abra o link de onboarding no navegador")
    print("   2. Preencha os dados (modo teste aceita dados fictícios)")
    print("   3. Complete o formulário")
    print("   4. Aguarde o webhook atualizar o status")
    print("   5. Verifique se status=ACTIVE e license=ATIVO")

    return restaurant_id


def test_driver_onboarding():
    """Testa o fluxo completo de onboarding de driver"""
    print("\n\n🚴 === TESTE: DRIVER ONBOARDING ===\n")

    # 1. Registrar driver
    print("1️⃣ Registrando driver...")
    driver_data = {
        "login": f"teste_driver_{int(time.time())}",
        "password": "teste123",
        "personal_info": {
            "name": "Teste Driver Stripe",
            "phone": "912345678",
            "email": f"teste_driver_{int(time.time())}@example.com",
            "address": "Rua Teste, 456",
            "city": "Leiria",
            "postal_code": "2400-000"
        },
        "vehicle_info": {
            "type": "MOTORCYCLE",
            "plate": "AA-00-BB",
            "model": "Honda",
            "color": "Preto"
        }
    }

    response = requests.post(f"{BASE_URL}/drivers/register", json=driver_data)
    print(f"   Status: {response.status_code}")

    if response.status_code != 201:
        print(f"   ❌ Erro: {response.text}")
        return

    driver = response.json()
    driver_id = driver.get("driver_id")
    print(f"   ✅ Driver registrado: ID={driver_id}")
    print(f"   📊 Status: {driver.get('status')}")
    print(f"   💳 Stripe Account ID: {driver.get('stripe_account_id')}")
    print(f"   🔗 Onboarding URL: {driver.get('onboarding_url')[:80] if driver.get('onboarding_url') else 'N/A'}...")

    # 2. Verificar status
    print("\n2️⃣ Verificando status no banco...")
    response = requests.get(f"{BASE_URL}/drivers/{driver_id}")
    if response.status_code == 200:
        driver = response.json()
        print(f"   📊 Status: {driver.get('status')}")
        print(f"   💳 Stripe Account ID: {driver.get('stripe_account_id')}")
        print(f"   ✅ Onboarding completed: {driver.get('stripe_onboarding_completed')}")

    print("\n📝 INSTRUÇÕES:")
    print("   1. Abra o link de onboarding no navegador")
    print("   2. Preencha os dados (modo teste aceita dados fictícios)")
    print("   3. Complete o formulário")
    print("   4. Aguarde o webhook atualizar o status")
    print("   5. Verifique se status=ACTIVE")

    return driver_id


def test_endpoints():
    """Testa os novos endpoints de callback"""
    print("\n\n🔗 === TESTE: ENDPOINTS DE CALLBACK ===\n")

    # 1. Teste endpoint de sucesso de restaurante
    print("1️⃣ Testando /connect/onboarding-success...")
    response = requests.get(f"{BASE_URL}/connect/onboarding-success")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Success: {data.get('success')}")
        print(f"   💬 Message: {data.get('message')}")
        print(f"   🔄 Redirect: {data.get('redirect')}")

    # 2. Teste endpoint de refresh de restaurante
    print("\n2️⃣ Testando /connect/onboarding-refresh...")
    response = requests.get(f"{BASE_URL}/connect/onboarding-refresh")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Success: {data.get('success')}")
        print(f"   💬 Message: {data.get('message')}")
        print(f"   🔄 Redirect: {data.get('redirect')}")

    # 3. Teste endpoint de sucesso de driver
    print("\n3️⃣ Testando /drivers/onboarding-success...")
    response = requests.get(f"{BASE_URL}/drivers/onboarding-success")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Success: {data.get('success')}")
        print(f"   💬 Message: {data.get('message')}")
        print(f"   🔄 Redirect: {data.get('redirect')}")

    # 4. Teste endpoint de refresh de driver
    print("\n4️⃣ Testando /drivers/onboarding-refresh...")
    response = requests.get(f"{BASE_URL}/drivers/onboarding-refresh")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Success: {data.get('success')}")
        print(f"   💬 Message: {data.get('message')}")
        print(f"   🔄 Redirect: {data.get('redirect')}")


if __name__ == "__main__":
    print("=" * 70)
    print("🧪 TESTE COMPLETO DO FLUXO DE ONBOARDING STRIPE")
    print("=" * 70)

    # Teste os endpoints primeiro
    test_endpoints()

    # Depois teste o fluxo completo
    # restaurant_id = test_restaurant_onboarding()
    # driver_id = test_driver_onboarding()

    print("\n" + "=" * 70)
    print("✅ TESTES CONCLUÍDOS")
    print("=" * 70)
    print("\n💡 Para testar o fluxo completo, descomente as linhas:")
    print("   restaurant_id = test_restaurant_onboarding()")
    print("   driver_id = test_driver_onboarding()")
    print("\n⚠️  Lembre-se: Em modo teste, use dados fictícios no formulário Stripe")

