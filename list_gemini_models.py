"""
Script para listar modelos Gemini disponíveis
"""
from google import genai
from core.config import settings

try:
    print("🔍 Listando modelos Gemini disponíveis...")
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    # Listar modelos
    models = client.models.list()

    print("\n✅ Modelos disponíveis:\n")
    for model in models:
        print(f"📦 {model.name}")
        if hasattr(model, 'display_name'):
            print(f"   Nome: {model.display_name}")
        if hasattr(model, 'description'):
            print(f"   Descrição: {model.description}")
        print()

except Exception as e:
    print(f"❌ Erro: {e}")

