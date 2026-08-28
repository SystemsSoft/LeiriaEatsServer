# Arquivo: core/config.py
import os
from typing import Optional
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env que está na raiz
load_dotenv()


class Settings:
    PROJECT_NAME: str = "KOMA AI"

    # Busca as chaves no arquivo .env
    STRIPE_API_KEY: str = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_PUBLIC_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY")
    # Fallback temporário para facilitar teste online do webhook.
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET")

    # Google Gemini API Keys (Suporta múltiplas chaves separadas por vírgula para failover)
    GEMINI_API_KEYS: list[str] = [k.strip() for k in os.getenv("GEMINI_API_KEY", "").split(",") if k.strip()]
    # Mantido por compatibilidade
    GEMINI_API_KEY: str = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

    # Redis Config
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    USE_REDIS: bool = os.getenv("USE_REDIS", "false").lower() == "true"

    # Limite de restaurantes distintos por pedido (PLANO_LIMITE_RESTAURANTES.md).
    # Único lugar de leitura tanto pelo checkout quanto pela IA — mudar aqui muda os dois.
    MAX_RESTAURANTES_POR_PEDIDO: int = int(os.getenv("MAX_RESTAURANTES_POR_PEDIDO", 3))

    # PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.1 — cinto de segurança do pagamento em 2
    # etapas. Independente do prazo de aceite normal (15 min, PRAZO_ACEITE_MINUTOS em
    # payment_reconciliation_service.py): se por qualquer motivo um pedido ficar preso
    # em AUTHORIZED além deste prazo (worker fora do ar, bug, pedido que escapou da
    # busca), esta é a última rede — cancela a autorização sozinha, sem depender do
    # fluxo normal de aceite/recusa.
    #
    # 1 hora foi escolhido por estar bem acima do fluxo normal (15 min) e bem abaixo de
    # qualquer janela real de expiração de autorização do Stripe (medida em dias, não
    # horas) — mas o valor exato dessa janela ainda não foi confirmado na documentação
    # oficial do Stripe (ver PLANO_PAGAMENTO_2_ETAPAS.md, Fase 6.1). Reavaliar esta
    # margem quando o número real for confirmado.
    PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS: int = int(os.getenv("PRAZO_SEGURANCA_AUTORIZACAO_MINUTOS", 60))

    def __init__(self):
        # Aviso de segurança no terminal se a chave não for achada
        if not self.STRIPE_API_KEY:
            print("⚠️ AVISO: STRIPE_SECRET_KEY não encontrada no arquivo .env")
        
        if not self.GEMINI_API_KEY:
            print("⚠️ AVISO: GEMINI_API_KEY não encontrada no arquivo .env")
        elif self.GEMINI_API_KEY:
            print("✅ Gemini API Key configurada")
            
        if not self.STRIPE_WEBHOOK_SECRET:
            print("⚠️ AVISO: STRIPE_WEBHOOK_SECRET não encontrada no arquivo .env")
        else:
            print(f"✅ Stripe Webhook configurado (Secret: {self.STRIPE_WEBHOOK_SECRET[:8]}...)")


settings = Settings()