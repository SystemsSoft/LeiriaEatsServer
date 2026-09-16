"""
Testes de services/push_notification_service.py (PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 5).

O ponto central a garantir: send_route_offer NUNCA lança exceção e NUNCA bloqueia o
despacho, em nenhum dos três cenários possíveis em produção antes de uma credencial
real existir — (1) sem FIREBASE_SERVICE_ACCOUNT_PATH configurado, (2) configurado mas
apontando para um caminho/arquivo inválido, (3) estafeta sem fcm_token salvo.

Execução:
    python3 tests/test_push_notification_service.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

import services.push_notification_service as push_service
from core.sql_models import DriverDB, DeliveryRouteDB


def _driver(fcm_token=None):
    return DriverDB(id=1, gid="01DRV", login="teste", password="x", name="Estafeta Teste", fcm_token=fcm_token)


def _route():
    return DeliveryRouteDB(gid="01ROUTE_TESTE", master_order_gid="01ORD", status="OFFERED")


def _reset_module_state():
    push_service._firebase_app = None
    push_service._init_attempted = False
    push_service._init_warning_logged = False


def teste_sem_credencial_configurada_nao_faz_nada():
    _reset_module_state()
    push_service.settings.FIREBASE_SERVICE_ACCOUNT_PATH = None
    # Não deve lançar, mesmo com estafeta tendo token
    push_service.send_route_offer(_driver(fcm_token="tok-123"), _route())
    assert push_service._get_firebase_app() is None
    print("OK  - sem FIREBASE_SERVICE_ACCOUNT_PATH, send_route_offer é no-op seguro")


def teste_credencial_invalida_nao_lanca_excecao():
    _reset_module_state()
    push_service.settings.FIREBASE_SERVICE_ACCOUNT_PATH = "/caminho/que/nao/existe.json"
    # A inicialização vai falhar internamente — não deve propagar exceção
    push_service.send_route_offer(_driver(fcm_token="tok-123"), _route())
    assert push_service._get_firebase_app() is None
    push_service.settings.FIREBASE_SERVICE_ACCOUNT_PATH = None
    print("OK  - credencial inválida não derruba o chamador (apenas loga e desativa push)")


def teste_estafeta_sem_token_nao_tenta_enviar():
    _reset_module_state()
    push_service.settings.FIREBASE_SERVICE_ACCOUNT_PATH = None
    # fcm_token=None — mesmo se a credencial existisse, não haveria para onde enviar
    push_service.send_route_offer(_driver(fcm_token=None), _route())
    print("OK  - estafeta sem fcm_token não tenta enviar (nada para lançar)")


if __name__ == "__main__":
    teste_sem_credencial_configurada_nao_faz_nada()
    teste_credencial_invalida_nao_lanca_excecao()
    teste_estafeta_sem_token_nao_tenta_enviar()
    print("\nTodos os testes do push_notification_service (Fase 5) passaram.")
