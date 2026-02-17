# Arquivo: core/config.py
import os
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env que está na raiz
load_dotenv()


class Settings:
    PROJECT_NAME: str = "Leiria Eats"

    # Busca as chaves no arquivo .env
    STRIPE_API_KEY: str = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_PUBLIC_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY")

    def __init__(self):
        # Aviso de segurança no terminal se a chave não for achada
        if not self.STRIPE_API_KEY:
            print("⚠️ AVISO: STRIPE_SECRET_KEY não encontrada no arquivo .env")


# Cria uma instância única para ser importada
settings = Settings()