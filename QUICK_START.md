# 🚀 Guia Rápido - Sistema de IA Conversacional

## ✅ Implementação Concluída!

O sistema híbrido **E5 + TinyLlama** está pronto e funcionando!

### O que foi implementado:

1. ✅ **TinyLlama 1.1B** baixado e configurado
2. ✅ **Serviço de conversação** criado (`tinyllama_sales_service.py`)
3. ✅ **Serviço híbrido** E5 + TinyLlama (`hybrid_ai_service.py`)
4. ✅ **Rota de chat** `/chat/sales` implementada
5. ✅ **Documentação completa** (`TINYLLAMA_DOCS.md`)
6. ✅ **Testes** funcionando

---

## 🎯 Como Usar

### 1. Iniciar o Servidor

```bash
cd /Users/bruno/Documents/Athenna/Koma/KomaServer/LeiriaEatsServer
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Aguarde os logs:**
```
🤖 CARREGANDO MODELOS DE INTELIGÊNCIA ARTIFICIAL
📡 Carregando E5 (Sentence Transformer)...
✅ E5 carregado!
🤖 Carregando TinyLlama...
✅ TinyLlama carregado com sucesso!
🎉 Todos os modelos foram carregados com sucesso!
```

### 2. Testar a API

#### Verificar Status
```bash
curl http://localhost:8000/chat/status
```

**Resposta esperada:**
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

#### Chat com Agente de Vendas
```bash
curl -X POST http://localhost:8000/chat/sales \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Quero uma pizza",
    "restaurant_id": 1
  }'
```

**Resposta esperada:**
```json
{
  "response": "Ótimo! Temos pizzas disponíveis: • Margherita (R$ 32.00) • Calabresa (R$ 35.00). Qual você prefere?",
  "products": [
    {
      "id": 1,
      "name": "Pizza Margherita",
      "price": 32.0,
      ...
    }
  ],
  "intent": "product_search"
}
```

---

## 📱 Integração com App Flutter

### Exemplo de Código

```dart
// Função para chat com agente de vendas
Future<ChatResponse> chatWithAgent(String message, int restaurantId) async {
  final response = await http.post(
    Uri.parse('$baseUrl/chat/sales'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({
      'message': message,
      'restaurant_id': restaurantId,
      'session_id': sessionId, // Opcional
    }),
  );
  
  if (response.statusCode == 200) {
    return ChatResponse.fromJson(jsonDecode(response.body));
  } else {
    throw Exception('Erro ao conversar com agente');
  }
}

// Modelo de resposta
class ChatResponse {
  final String response;
  final List<Product> products;
  final String intent;
  
  ChatResponse({
    required this.response,
    required this.products,
    required this.intent,
  });
  
  factory ChatResponse.fromJson(Map<String, dynamic> json) {
    return ChatResponse(
      response: json['response'],
      products: (json['products'] as List)
          .map((p) => Product.fromJson(p))
          .toList(),
      intent: json['intent'],
    );
  }
}
```

### UI Sugerida

```dart
// Widget de chat
class ChatWidget extends StatefulWidget {
  @override
  _ChatWidgetState createState() => _ChatWidgetState();
}

class _ChatWidgetState extends State<ChatWidget> {
  final TextEditingController _controller = TextEditingController();
  final List<ChatMessage> _messages = [];
  
  void _sendMessage() async {
    final message = _controller.text;
    if (message.isEmpty) return;
    
    // Adicionar mensagem do usuário
    setState(() {
      _messages.add(ChatMessage(text: message, isUser: true));
    });
    _controller.clear();
    
    // Enviar para API
    try {
      final response = await chatWithAgent(message, currentRestaurantId);
      
      // Adicionar resposta do agente
      setState(() {
        _messages.add(ChatMessage(
          text: response.response,
          isUser: false,
          products: response.products,
        ));
      });
    } catch (e) {
      print('Erro: $e');
    }
  }
  
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Expanded(
          child: ListView.builder(
            itemCount: _messages.length,
            itemBuilder: (context, index) {
              return ChatBubble(message: _messages[index]);
            },
          ),
        ),
        ChatInput(
          controller: _controller,
          onSend: _sendMessage,
        ),
      ],
    );
  }
}
```

---

## 🔧 Configurações Avançadas

### Ajustar Temperatura (Criatividade)

Edite `services/tinyllama_sales_service.py`:

```python
outputs = cls._model.generate(
    **inputs,
    max_new_tokens=100,
    temperature=0.7,  # ⬅️ Ajuste aqui (0.1-1.0)
    top_p=0.9,
    do_sample=True,
    ...
)
```

- **0.1-0.3:** Mais conservador, respostas previsíveis
- **0.7:** Balanceado (padrão)
- **0.9-1.0:** Mais criativo, pode ser inconsistente

### Ajustar Tamanho da Resposta

```python
max_new_tokens=100,  # ⬅️ Ajuste aqui (50-200)
```

- **50:** Respostas curtas
- **100:** Balanceado (padrão)
- **200:** Respostas longas

---

## 📊 Monitoramento

### Logs do Sistema

O servidor imprime logs detalhados:

```
🔍 [E5] Analisando: 'Quero uma pizza'
✅ [E5] Encontrou 3 produtos relevantes
💬 [TinyLlama] Gerando resposta natural...
✅ [TinyLlama] Resposta gerada
```

### Verificar Uso de Memória

```bash
# No Mac
top -l 1 | grep "PhysMem"

# No Linux
free -h
```

**Uso esperado:**
- E5: ~1.5GB
- TinyLlama: ~2-2.5GB
- FastAPI: ~0.5GB
- **Total: ~4-4.5GB**

---

## 🐛 Problemas Comuns

### 1. "TinyLlama não carrega"
```bash
# Limpar cache e tentar novamente
rm -rf ~/.cache/huggingface/
python test_tinyllama_simple.py
```

### 2. "Out of memory"
```bash
# Verificar RAM disponível
# Fechar outros programas
# Reiniciar servidor
```

### 3. "Respostas estranhas"
- Ajustar temperatura (menor = mais consistente)
- Melhorar prompt no código
- Considerar usar modelo maior (Phi-3) no futuro

---

## 📈 Próximos Passos

### Curto Prazo (1-2 semanas)
- [ ] Implementar gestão de carrinho por sessão
- [ ] Adicionar histórico de conversa
- [ ] Melhorar prompts do TinyLlama

### Médio Prazo (1-3 meses)
- [ ] Sistema de ML para recomendações
- [ ] Banco de dados separado para analytics
- [ ] A/B testing de respostas

### Longo Prazo (3-6 meses)
- [ ] Fine-tuning do TinyLlama com conversas reais
- [ ] Upgrade para Phi-3 Mini (melhor qualidade)
- [ ] Sistema de feedback dos usuários

---

## 📞 Suporte

### Comandos Úteis

```bash
# Testar sistema completo
python test_tinyllama_integration.py

# Testar apenas TinyLlama
python test_tinyllama_simple.py

# Ver logs do servidor
tail -f logs/server.log  # Se tiver logs configurados

# Verificar status via API
curl http://localhost:8000/chat/status
```

### Arquivos Importantes

- `services/tinyllama_sales_service.py` - Serviço TinyLlama
- `services/hybrid_ai_service.py` - Integração E5 + TinyLlama
- `api/routes/chat_routes.py` - Rotas de chat
- `TINYLLAMA_DOCS.md` - Documentação completa

---

## ✨ Conclusão

Você agora tem um **sistema de IA conversacional completo** rodando localmente!

**Características:**
- ✅ 100% local (sem custos de API)
- ✅ Busca semântica inteligente (E5)
- ✅ Conversação natural (TinyLlama)
- ✅ Roda em 4GB RAM
- ✅ Latência ~1-2 segundos

**Comece testando:**
```bash
uvicorn main:app --reload
```

🎉 **Boa sorte com seu projeto!**

