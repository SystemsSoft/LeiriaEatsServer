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

    # --- IA generativa (negociação entre agentes, draft de campanha, embeddings) ---
    # Chave PRÓPRIA do ConectaAI, criada à parte no Google AI Studio — separada da
    # GEMINI_API_KEY do Koma, para que cota e faturamento de cada produto fiquem
    # isolados. Aceita várias chaves separadas por vírgula (failover em
    # services/ai/gemini_client.py). De propósito NÃO cai de volta na
    # GEMINI_API_KEY do Koma: sem esta variável, o módulo usa o agente
    # determinístico (services/negotiation/policy.py) em vez de gastar a cota do
    # Koma sem ninguém perceber.
    GEMINI_API_KEYS: list = [k.strip() for k in os.getenv("CONECTAAI_GEMINI_API_KEY", "").split(",") if k.strip()]
    GEMINI_MODEL: str = os.getenv("CONECTAAI_GEMINI_MODEL", "gemini-flash-lite-latest")
    GEMINI_EMBEDDING_MODEL: str = os.getenv("CONECTAAI_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    # Quantos creators (pré-filtrados pela busca semântica) vão para o Gemini
    # avaliar com os dados do perfil e escrever o motivo do match — ver
    # services/ai/creator_rerank.py. Mais candidatos = melhor cobertura para
    # pedidos numéricos ("mais seguidores"), mas prompt e latência maiores.
    MATCH_RERANK_CANDIDATES: int = int(os.getenv("CONECTAAI_MATCH_RERANK_CANDIDATES", 30))

    # --- Negociação entre agentes ---
    NEGOTIATION_MAX_ROUNDS_DEFAULT: int = int(os.getenv("CONECTAAI_NEGOTIATION_MAX_ROUNDS", 4))
    NEGOTIATION_TURN_DEADLINE_S: float = float(os.getenv("CONECTAAI_NEGOTIATION_TURN_DEADLINE_S", 12))
    NEGOTIATION_LEASE_SECONDS: int = int(os.getenv("CONECTAAI_NEGOTIATION_LEASE_SECONDS", 180))
    NEGOTIATION_MAX_CONCURRENT: int = int(os.getenv("CONECTAAI_NEGOTIATION_MAX_CONCURRENT", 2))

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

    # --- Pagamentos (Stripe Connect) ---
    # Chave SECRETA própria do ConectaAI (sk_live_.../sk_test_...), separada da STRIPE_SECRET_KEY do Koma: cada
    # produto com a sua conta/plataforma. Sem ela, o Financeiro mostra "pagamentos ainda não disponíveis".
    STRIPE_API_KEY: str = os.getenv("CONECTAAI_STRIPE_SECRET_KEY", "")
    # Segredo(s) (whsec_...) dos webhooks que apontam para POST <PUBLIC_BASE_URL>/finance/stripe/webhook. São
    # DOIS endpoints na Stripe, cada um com o seu segredo — coloque os dois aqui, separados por vírgula:
    #   - "Connected accounts": account.updated (situação da conta de quem recebe);
    #   - "Your account": checkout.session.completed, checkout.session.async_payment_succeeded,
    #     checkout.session.async_payment_failed e checkout.session.expired (pagamentos dos acordos).
    STRIPE_WEBHOOK_SECRET: str = os.getenv("CONECTAAI_STRIPE_WEBHOOK_SECRET", "")
    # País das contas conectadas (ISO de 2 letras) — Portugal, onde o produto é lançado. A Stripe não deixa mudar
    # o país de uma conta depois de criada.
    STRIPE_COUNTRY: str = os.getenv("CONECTAAI_STRIPE_COUNTRY", "PT").upper()
    # Moeda das cobranças e taxa da plataforma (% do valor do acordo, descontada do que o creator recebe).
    STRIPE_CURRENCY: str = os.getenv("CONECTAAI_STRIPE_CURRENCY", "eur").lower()
    PLATFORM_FEE_PERCENT: float = float(os.getenv("CONECTAAI_PLATFORM_FEE_PERCENT", "0") or 0)
    # Endereço do app (para onde a Stripe devolve o usuário) quando o app não informa o próprio.
    APP_URL: str = os.getenv("CONECTAAI_APP_URL", "").rstrip("/")

    def __init__(self):
        if not self.DB_USER or not self.DB_PASS:
            print("⚠️ AVISO: CONECTAAI_DB_USER/CONECTAAI_DB_PASS não configurados no .env")
        if not self.JWT_SECRET:
            print("⚠️ AVISO: CONECTAAI_JWT_SECRET não configurado no .env — usando valor inseguro de desenvolvimento")
        if not self.GEMINI_API_KEYS:
            print("⚠️ AVISO: CONECTAAI_GEMINI_API_KEY não configurado no .env — IA desativada (agente determinístico e busca por palavra-chave)")


settings = ConectaAISettings()
