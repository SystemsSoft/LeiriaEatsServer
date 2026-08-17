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

    # Google Gemini API Key
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")

    # Redis Config
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    USE_REDIS: bool = os.getenv("USE_REDIS", "false").lower() == "true"

    def __init__(self):
        # Aviso de segurança no terminal se a chave não for achada
        if not self.STRIPE_API_KEY:
            print("⚠️ AVISO: STRIPE_SECRET_KEY não encontrada no arquivo .env")
        
        if not self.GEMINI_API_KEY:
            print("⚠️ AVISO: GEMINI_API_KEY não encontrada no arquivo .env")
        elif self.GEMINI_API_KEY:
            print("✅ Gemini API Key configurada")


settings = Settings()