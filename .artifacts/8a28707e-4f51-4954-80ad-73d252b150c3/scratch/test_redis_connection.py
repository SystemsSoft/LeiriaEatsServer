import redis
import os
from dotenv import load_dotenv

load_dotenv()

USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

print(f"--- Configuração ---")
print(f"USE_REDIS: {USE_REDIS}")
print(f"REDIS_HOST: {REDIS_HOST}")
print(f"REDIS_PORT: {REDIS_PORT}")

if USE_REDIS:
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.ping()
        print("✅ Conexão com Redis estabelecida com sucesso!")
        
        # Teste de escrita/leitura
        r.set("test_key", "KomaAI_Redis_Test")
        val = r.get("test_key")
        print(f"📝 Teste de dados: {val}")
        r.delete("test_key")
        
    except Exception as e:
        print(f"❌ Erro ao conectar ao Redis: {e}")
else:
    print("ℹ️ Redis está desativado no .env")
