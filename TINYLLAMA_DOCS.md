# Sistema Híbrido de IA - E5 + TinyLlama

## 📋 Visão Geral

Este servidor agora possui um **sistema híbrido de IA** que combina:

1. **E5 (Sentence Transformer)** - Busca semântica inteligente
2. **TinyLlama 1.1B** - Conversação natural

### Pipeline de Funcionamento

```
Usuário: "Quero uma pizza"
    ↓
[E5] Busca semântica → Encontra produtos relevantes
    ↓
[TinyLlama] Conversação → Gera resposta natural
    ↓
Resposta: "Ótimo! Temos pizzas: Margherita (R$ 32), 
           Calabresa (R$ 35)..."
```

## 🚀 Rotas Disponíveis

### 1. Chat Conversacional (NOVO)

**Endpoint:** `POST /chat/sales`

**Descrição:** Chat com agente de vendas usando IA generativa

**Request:**
```json
{
  "message": "Quero uma pizza",
  "restaurant_id": 123,
  "session_id": "abc-123"
}
```

**Response:**
```json
{
  "response": "Ótimo! Temos 3 pizzas disponíveis: • Margherita (R$ 32.00) • Calabresa (R$ 35.00) • Portuguesa (R$ 38.00). Qual você prefere?",
  "products": [
    {
      "id": 1,
      "name": "Pizza Margherita",
      "price": 32.0,
      "description": "Molho, mussarela, tomate...",
      "category": "Pizza",
      "quantity": 1
    }
  ],
  "intent": "product_search"
}
```

### 2. Busca Semântica Tradicional

**Endpoint:** `POST /chat`

**Descrição:** Busca semântica usando apenas E5 (compatibilidade)

**Request:**
```json
{
  "text": "pizza calabresa"
}
```

**Response:**
```json
{
  "reply": "Encontrei o prato: 1x Pizza Calabresa (R$ 35.00).",
  "intent": "product_search",
  "restaurantResults": [],
  "productResults": [...]
}
```

### 3. Status do Sistema

**Endpoint:** `GET /chat/status`

**Descrição:** Verifica se os modelos de IA estão carregados

**Response:**
```json
{
  "status": "ready",
  "details": {
    "e5_loaded": true,
    "tinyllama_loaded": true,
    "system_ready": true
  }
}
```

## 💡 Exemplos de Uso

### Exemplo 1: Busca Simples
```bash
curl -X POST http://localhost:8000/chat/sales \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Quero uma pizza",
    "restaurant_id": 1
  }'
```

### Exemplo 2: Busca com Contexto
```bash
curl -X POST http://localhost:8000/chat/sales \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Algo light para dieta",
    "restaurant_id": 1
  }'
```

### Exemplo 3: Verificar Status
```bash
curl http://localhost:8000/chat/status
```

## 🔧 Configuração e Inicialização

### Primeira Execução

Na primeira execução, o servidor irá:
1. Baixar o modelo TinyLlama (~2.2GB) - pode levar 2-5 minutos
2. Carregar o modelo E5 (já existente)
3. Indexar os dados do banco

**Logs esperados:**
```
🚀 Iniciando Koma AI Server...
==================================================
🤖 CARREGANDO MODELOS DE INTELIGÊNCIA ARTIFICIAL
==================================================
📡 Carregando E5 (Sentence Transformer)...
✅ E5 carregado!
🤖 Carregando TinyLlama...
📚 [TinyLlama] Carregando tokenizer...
🧠 [TinyLlama] Carregando modelo (isso pode levar 1-2 minutos)...
✅ [TinyLlama] Modelo carregado com sucesso!
🎉 Todos os modelos foram carregados com sucesso!
==================================================
```

### Execuções Posteriores

Após a primeira execução, o modelo já está em cache local e carrega rapidamente (~10-30 segundos).

## 📊 Requisitos de Sistema

### Recursos Necessários
- **RAM:** 4GB (TinyLlama ~2GB + E5 ~1.5GB + FastAPI ~0.5GB)
- **Storage:** ~5GB para modelos
- **CPU:** Qualquer processador moderno (GPU opcional, mas não necessário)

### Dependências Python
```
transformers>=4.35.0
torch>=2.0.0
sentence-transformers
accelerate>=0.25.0
bitsandbytes>=0.41.0
```

## 🧪 Testes

### Teste Manual do Sistema
```bash
python test_tinyllama_integration.py
```

Este script testa:
- ✅ Download e inicialização do TinyLlama
- ✅ Geração de resposta
- ✅ Status do E5
- ✅ Status do sistema completo

## 🎯 Casos de Uso

### 1. Cliente Busca Produto
```
Usuário: "Quero uma pizza"
Sistema: "Ótimo! Temos pizzas: Margherita (R$ 32), 
         Calabresa (R$ 35)... Qual você prefere?"
```

### 2. Cliente Busca com Filtro
```
Usuário: "Algo light para 2 pessoas"
Sistema: "Perfeito! Opções saudáveis que servem 2:
         • Salada Caesar (R$ 22)
         • Frango Grelhado (R$ 25)"
```

### 3. Cliente Pede Sugestão
```
Usuário: "E pra beber?"
Sistema: "Para acompanhar:
         • Refrigerante 2L (R$ 8)
         • Suco Natural (R$ 6)"
```

## 🔮 Próximos Passos

### Funcionalidades a Implementar

1. **Gestão de Carrinho por Sessão**
   - Armazenar itens do carrinho em Redis/Sessão
   - Contexto persistente durante a conversa

2. **Histórico de Conversação**
   - Manter contexto das últimas 5-10 mensagens
   - Referências a mensagens anteriores

3. **Sistema de ML (Recomendações)**
   - Banco ML separado
   - Aprendizado de padrões de compra
   - Sugestões baseadas em co-ocorrência

4. **Fine-tuning do TinyLlama**
   - Treinar com conversas reais
   - Especialização no domínio de delivery

## 📝 Notas Importantes

### Performance
- **Latência E5:** ~100-300ms
- **Latência TinyLlama:** ~500-1000ms
- **Latência Total:** ~1-2 segundos

### Limitações Atuais
- TinyLlama tem capacidade limitada vs GPT-4
- Respostas podem ser menos "criativas"
- Não mantém contexto entre sessões (ainda)

### Vantagens
- ✅ 100% local - sem custos de API
- ✅ Privacidade total dos dados
- ✅ Baixa latência
- ✅ Escalável sem custos adicionais
- ✅ Controle total do modelo

## 🆘 Troubleshooting

### Modelo não carrega
```
Erro: "Out of memory"
Solução: Verificar se tem 4GB RAM disponíveis
```

### Download falha
```
Erro: "Connection timeout"
Solução: Verificar conexão internet e tentar novamente
```

### Respostas estranhas
```
Problema: TinyLlama gera texto sem sentido
Solução: Ajustar temperatura e max_tokens no código
```

## 📧 Suporte

Para problemas ou dúvidas:
1. Verificar logs do servidor
2. Executar `test_tinyllama_integration.py`
3. Checar `/chat/status`
4. Verificar uso de RAM com `htop` ou similar

---

**Versão:** 1.0.0  
**Última Atualização:** Agosto 2026  
**Status:** ✅ Produção

