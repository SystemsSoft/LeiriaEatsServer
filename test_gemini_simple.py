"""
Teste simples para verificar respostas do Gemini
"""
from google import genai
from google.genai import types
from core.config import settings

try:
    print("🧪 Teste simples do Gemini")
    print("=" * 60)

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    # Teste simples
    prompt = "Você é um vendedor de pizzas. Um cliente disse 'quero pizza'. Responda de forma amigável."

    print(f"\n📝 Prompt: {prompt}\n")

    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=200,
        )
    )

    print(f"✅ Resposta completa:")
    print(f"   Texto: {response.text}")
    print(f"   Tipo: {type(response.text)}")
    print(f"   Tamanho: {len(response.text)} caracteres")

    if hasattr(response, 'candidates'):
        print(f"\n📊 Candidatos: {len(response.candidates)}")
        for i, candidate in enumerate(response.candidates):
            print(f"   Candidato {i}: {candidate}")

    if hasattr(response, 'usage_metadata'):
        print(f"\n📊 Metadata de uso:")
        print(f"   {response.usage_metadata}")

except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback
    traceback.print_exc()

