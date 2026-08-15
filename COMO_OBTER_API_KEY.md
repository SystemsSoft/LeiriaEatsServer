# 🔑 COMO OBTER SUA API KEY DO GEMINI (GRÁTIS)

## 📝 PASSO A PASSO (2 minutos)

### **1. Acessar Google AI Studio**
```
https://aistudio.google.com/
```

### **2. Fazer Login**
- Use sua conta Google (Gmail)
- Se não tiver, crie uma grátis

### **3. Criar API Key**
1. No menu lateral, clique em **"Get API Key"**
2. Ou acesse direto: https://aistudio.google.com/app/apikey
3. Clique em **"Create API Key"**
4. Escolha um projeto (ou crie novo)
5. Copie a chave gerada (começa com `AIza...`)

### **4. Configurar no Projeto**

#### **Opção A: Variável de Ambiente (RECOMENDADO)**
```bash
# Criar/editar arquivo .env na raiz do projeto
echo 'GEMINI_API_KEY=AIza...' >> .env
```

#### **Opção B: Hardcoded (apenas para testes)**
```python
# core/config.py (linha 21)
GEMINI_API_KEY: str = os.getenv(
    "GEMINI_API_KEY",
    "AIza..."  # ← Cole sua key aqui
)
```

### **5. Testar**
```bash
python test_gemini.py
```

---

## 🆓 LIMITES GRATUITOS

| Métrica | Limite |
|---------|--------|
| **Requisições por dia** | 1.500 |
| **Requisições por minuto** | 15 |
| **Tokens por minuto** | 1 milhão |
| **Custo** | **GRATUITO** |

---

## ⚠️ SE A KEY NÃO FUNCIONAR

### **Problema: "API key not valid"**

1. **Verificar se a key está completa:**
   - Deve ter ~39 caracteres
   - Começa com `AIza`
   - Sem espaços ou quebras de linha

2. **Verificar se a API está habilitada:**
   - Acesse: https://console.cloud.google.com/apis
   - Procure por "Generative Language API"
   - Clique em "ENABLE" se estiver desabilitada

3. **Criar nova key:**
   - Às vezes a key leva alguns minutos para ativar
   - Tente criar uma nova

4. **Verificar região:**
   - O Gemini pode não estar disponível em alguns países
   - Use VPN se necessário

---

## 🧪 TESTE RÁPIDO (terminal)

```bash
# Testar se a key funciona
python -c "
from google import genai
client = genai.Client(api_key='SUA_KEY_AQUI')
response = client.models.generate_content(
    model='gemini-2.0-flash-exp',
    contents='oi'
)
print(response.text)
"
```

**Resposta esperada:**
```
Olá! Como posso ajudar você hoje?
```

---

## 📞 SUPORTE

Se continuar com problemas:
1. Documentação oficial: https://ai.google.dev/gemini-api/docs
2. Fórum: https://discuss.ai.google.dev/
3. Ou use o fallback (sistema funciona sem Gemini)

---

## 💡 DICAS

- ✅ **Não compartilhe** sua API key publicamente
- ✅ **Use .env** para manter segura
- ✅ **Monitore o uso** via `/chat/status`
- ✅ **Cache está ativo** - respostas repetidas são grátis!

