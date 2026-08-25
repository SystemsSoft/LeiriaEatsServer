
import os
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Carregar API Key
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

def test_latency():
    if not api_key:
        print("❌ Erro: GEMINI_API_KEY não encontrada.")
        return

    client = genai.Client(api_key=api_key)
    
    print("🤖 Testando latência da API do Gemini (1.5 Flash)...")
    
    # Teste 1: Resposta Simples
    start = time.time()
    try:
        response = client.models.generate_content(
            model='gemini-flash-lite-latest',
            contents="Diga 'Olá mundo' em português de Portugal."
        )
        latency = time.time() - start
        print(f"✅ Teste 1 (Simples): {latency:.4f}s")
        print(f"   Resposta: {response.text.strip()}")
    except Exception as e:
        print(f"❌ Erro no Teste 1: {e}")

    # Teste 2: Resposta com Instrução de Sistema (como no App)
    print("\n🤖 Testando com instruções de sistema...")
    start = time.time()
    try:
        response = client.models.generate_content(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction="Seja um consultor de vendas de comida em Portugal.",
                max_output_tokens=100
            ),
            contents="O que você sugere para almoço?"
        )
        latency = time.time() - start
        print(f"✅ Teste 2 (Consultivo): {latency:.4f}s")
    except Exception as e:
        print(f"❌ Erro no Teste 2: {e}")

if __name__ == "__main__":
    test_latency()
