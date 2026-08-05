#!/usr/bin/env python3
"""
Script de teste para verificar se os campos do driver são retornados corretamente
no endpoint GET /orders/{restaurant_id}
"""
import requests
import sys

# Configuração
BASE_URL = "http://localhost:8000"  # Ajuste se necessário
TEST_RESTAURANT_ID = 1  # Ajuste para um restaurante válido no seu banco

def test_get_orders_endpoint():
    """Testa o endpoint GET /orders/{restaurant_id}"""
    print(f"🧪 Testando endpoint: GET /orders/{TEST_RESTAURANT_ID}")
    print("-" * 60)

    try:
        response = requests.get(f"{BASE_URL}/orders/{TEST_RESTAURANT_ID}")

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            orders = response.json()
            print(f"✅ Sucesso! Retornou {len(orders)} pedidos\n")

            # Analisa os pedidos para verificar campos do driver
            orders_with_driver = [o for o in orders if o.get('driver_id')]
            orders_without_driver = [o for o in orders if not o.get('driver_id')]

            print(f"📊 Estatísticas:")
            print(f"   - Total de pedidos: {len(orders)}")
            print(f"   - Com driver atribuído: {len(orders_with_driver)}")
            print(f"   - Sem driver atribuído: {len(orders_without_driver)}\n")

            # Mostra um exemplo de pedido com driver
            if orders_with_driver:
                print("📦 Exemplo de pedido COM driver atribuído:")
                print("-" * 60)
                example = orders_with_driver[0]
                print(f"Order ID: {example.get('id')}")
                print(f"Status: {example.get('status')}")
                print(f"Customer: {example.get('customer_name')}")
                print(f"\n🚗 Informações do Driver:")
                print(f"   - driver_id: {example.get('driver_id')}")
                print(f"   - driver_name: {example.get('driver_name')}")
                print(f"   - driver_phone: {example.get('driver_phone')}")
                print(f"   - vehicle_type: {example.get('vehicle_type')}")
                print(f"   - vehicle_model: {example.get('vehicle_model')}")
                print(f"   - vehicle_plate: {example.get('vehicle_plate')}")
                print(f"   - vehicle_color: {example.get('vehicle_color')}")

                # Verifica se os campos obrigatórios estão presentes
                driver_fields = ['driver_phone', 'vehicle_type', 'vehicle_model',
                                'vehicle_plate', 'vehicle_color']
                missing_fields = [f for f in driver_fields if f not in example]

                if missing_fields:
                    print(f"\n⚠️  Campos ausentes na resposta: {missing_fields}")
                    return False
                else:
                    print(f"\n✅ Todos os campos do driver estão presentes!")
                    return True
            else:
                print("ℹ️  Nenhum pedido com driver atribuído para testar.")
                print("   Crie um pedido e atribua um driver para testar completamente.")

            # Mostra um exemplo de pedido sem driver
            if orders_without_driver:
                print("\n📦 Exemplo de pedido SEM driver atribuído:")
                print("-" * 60)
                example = orders_without_driver[0]
                print(f"Order ID: {example.get('id')}")
                print(f"Status: {example.get('status')}")
                print(f"driver_id: {example.get('driver_id')} (None é esperado)")
                print(f"driver_phone: {example.get('driver_phone')} (None é esperado)")

            return True
        else:
            print(f"❌ Erro: Status {response.status_code}")
            print(f"Resposta: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print("❌ Erro: Não foi possível conectar ao servidor.")
        print(f"   Certifique-se de que o servidor está rodando em {BASE_URL}")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Teste dos campos do driver no endpoint /orders/{restaurant_id}")
    print("=" * 60)
    print()

    success = test_get_orders_endpoint()

    print()
    print("=" * 60)
    if success:
        print("✅ TESTE PASSOU!")
    else:
        print("❌ TESTE FALHOU!")
    print("=" * 60)

    sys.exit(0 if success else 1)

