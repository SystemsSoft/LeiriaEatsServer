"""
Teste direto da API Gemini para verificar se está funcionando
"""
from google import genai
from google.genai import types

import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

print("🔑 Testando API Key Gemini...")

try:
    client = genai.Client(api_key=API_KEY)
    print("✅ Cliente criado com sucesso")

    models_to_test = ['gemini-2.5-flash-lite', 'gemini-flash-lite-latest', 'gemini-flash-latest']

    for model_name in models_to_test:
        print(f"\n📝 Testando modelo: {model_name}")
        try:
            response = client.models.generate_content(
                model=model_name,
                contents="Responda em português com 1 frase curta: O que é uma pizza?",
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=100
                )
            )
            print(f"   response.text = {repr(response.text)}")
            if response.candidates:
                for c in response.candidates:
                    print(f"   candidate finish_reason: {c.finish_reason}")
                    if c.content and c.content.parts:
                        for p in c.content.parts:
                            print(f"   part.text = {repr(p.text)}")
            print(f"✅ Modelo {model_name} funcionou!")
            break
        except Exception as e:
            print(f"❌ {model_name}: {e}")

except Exception as e:
    print(f"❌ Erro geral: {e}")
