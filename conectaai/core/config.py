# Arquivo: conectaai/core/config.py
import os
from dotenv import load_dotenv

# Reaproveita o mesmo .env da raiz do repo (mesmo padrão do core/config.py do Koma),
# mas todas as variáveis daqui são prefixadas CONECTAAI_ para não colidir com as do Koma.
load_dotenv()


class ConectaAISettings:
    PROJECT_NAME: str = "ConectaAI Backend"

    # --- Banco de dados (schema próprio, mesma instância RDS do Koma por padrão) ---
    DB_HOST: str = os.getenv("CONECTAAI_DB_HOST", "localhost")
    DB_PORT: str = os.getenv("CONECTAAI_DB_PORT", "3306")
    DB_NAME: str = os.getenv("CONECTAAI_DB_NAME", "conectaaiDB")
    DB_USER: str = os.getenv("CONECTAAI_DB_USER", "")
    DB_PASS: str = os.getenv("CONECTAAI_DB_PASS", "")

    # --- Autenticação ---
    JWT_SECRET: str = os.getenv("CONECTAAI_JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = int(os.getenv("CONECTAAI_JWT_EXPIRES_MINUTES", 60 * 24 * 7))  # 7 dias

    # --- IA generativa (v1.1, opcional) — reaproveita a mesma chave do Koma ---
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").split(",")[0].strip()

    # --- Login com Google (Firebase Auth) ---
    # Id do projeto Firebase do app ConectaAI (não é segredo — já é público no
    # client, em lib/firebase_options.dart). Usado só para conferir o campo
    # `aud` do ID token do Google, sem precisar de credencial de service account.
    FIREBASE_PROJECT_ID: str = os.getenv("CONECTAAI_FIREBASE_PROJECT_ID", "conectaai-cc9f4")

    # --- Porta do processo (documentacional; quem define de fato é o comando uvicorn) ---
    PORT: int = int(os.getenv("CONECTAAI_PORT", 8081))

    # --- Upload de arquivos (avatar do creator) — disco local, próprio do módulo,
    # sem depender do bucket S3 do Koma (mantém o isolamento do processo). ---
    UPLOAD_DIR: str = os.getenv(
        "CONECTAAI_UPLOAD_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"),
    )
    PUBLIC_BASE_URL: str = os.getenv("CONECTAAI_PUBLIC_BASE_URL", "https://api.leiriaeats.com/conectaai")

    def __init__(self):
        if not self.DB_USER or not self.DB_PASS:
            print("⚠️ AVISO: CONECTAAI_DB_USER/CONECTAAI_DB_PASS não configurados no .env")
        if not self.JWT_SECRET:
            print("⚠️ AVISO: CONECTAAI_JWT_SECRET não configurado no .env — usando valor inseguro de desenvolvimento")


settings = ConectaAISettings()
