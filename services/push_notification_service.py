# Arquivo: services/push_notification_service.py
"""
PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 5 — envio de push via Firebase Cloud Messaging.

Substitui o antigo "notificação = logger.info" em courier_notification_service.py por
um push de verdade, quando configurado. Deliberadamente degradado: sem credencial
(FIREBASE_SERVICE_ACCOUNT_PATH, ver core/config.py) ou sem token do estafeta, esta
função só loga e retorna — o despacho por polling (GET /drivers/routes a cada 10s)
continua sendo a fonte de verdade e nunca depende de o push ter funcionado.

Mensagem "data-only" (sem payload de notificação nativa): o app, ao recebê-la, só
dispara uma nova checagem de GET /drivers/routes — nunca duplica os dados da rota no
corpo do push, para não arriscar mostrar estado obsoleto se a rota mudar entre o envio
e a app processar a mensagem.
"""
import logging

from core.config import settings
from core.sql_models import DriverDB, DeliveryRouteDB

logger = logging.getLogger("push_notification")

_firebase_app = None
_init_attempted = False
_init_warning_logged = False


def _get_firebase_app():
    """Inicializa o Firebase Admin uma única vez (lazy). Devolve None se não configurado
    ou se a inicialização falhar — nunca lança exceção para quem chama."""
    global _firebase_app, _init_attempted, _init_warning_logged
    if _firebase_app is not None:
        return _firebase_app
    if _init_attempted:
        return None
    _init_attempted = True

    if not settings.FIREBASE_SERVICE_ACCOUNT_PATH:
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("✅ Firebase Admin inicializado — push de novas rotas ativo.")
        return _firebase_app
    except Exception as exc:
        if not _init_warning_logged:
            logger.warning(f"⚠️ Falha ao inicializar Firebase Admin, push ficará desativado: {exc}")
            _init_warning_logged = True
        return None


def send_route_offer(driver: DriverDB, route: DeliveryRouteDB) -> None:
    """
    Notifica o estafeta de que uma nova rota foi oferecida. Nunca lança exceção: uma
    falha de push não pode derrubar o worker de despacho, que já persistiu a rota em
    DeliveryRouteDB e continuará servindo-a via GET /drivers/routes de qualquer forma.
    """
    app = _get_firebase_app()
    if app is None:
        return
    if not driver.fcm_token:
        return

    try:
        from firebase_admin import messaging
        message = messaging.Message(
            data={"type": "route_offered", "route_gid": route.gid},
            token=driver.fcm_token,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(aps=messaging.Aps(content_available=True)),
            ),
        )
        messaging.send(message, app=app)
        logger.info(f"📲 Push enviado a {driver.name or driver.id} para a rota {route.gid}.")
    except Exception as exc:
        logger.warning(f"⚠️ Falha ao enviar push para {driver.name or driver.id} (rota {route.gid}): {exc}")
