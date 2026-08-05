# Solução: Status de Pedido Não Atualiza Após Pagamento

## 🐛 Problema Relatado

Após o pedido ser realizado com sucesso no Stripe (modo teste):
- ✅ `stripe_customer_id` é capturado
- ✅ `checkout_session_id` é capturado
- ❌ `status` continua `PENDING_PAYMENT` (deveria ser `Pendente`)

## 🔍 Causa Raiz

O webhook do Stripe (`checkout.session.completed`) **estava configurado corretamente**, mas:

1. **Webhooks podem demorar** - O Stripe pode levar alguns segundos para disparar
2. **Webhooks podem falhar** - Problemas de rede, validação de assinatura, etc.
3. **Dependência total do webhook** - Nenhum fallback caso o webhook falhasse

## ✅ Solução Implementada

### 1. Endpoint de Verificação Manual

**Novo endpoint:** `POST /orders/{order_id}/check-payment-status`

**Funcionalidades:**
- Consulta o Stripe diretamente para obter o status real do pagamento
- Recupera o `payment_intent_id` via `checkout_session_id` se necessário
- Atualiza o status do pedido de `PENDING_PAYMENT` → `Pendente` se o pagamento foi confirmado
- Retorna o status atualizado em tempo real

**Uso no App:**
```kotlin
// Após o WebView detectar sucesso
val response = api.checkPaymentStatus(orderId)
if (response.payment_confirmed) {
    // Status atualizado! Navegue para tela de acompanhamento
    navigateToOrderTracking(orderId)
}
```

---

### 2. Página HTML de Sucesso Automática

**Nova página:** `GET /payment-success?order_id={order_id}`

**Funcionalidades:**
- Página moderna com feedback visual de sucesso
- **JavaScript automático** que chama `/orders/{order_id}/check-payment-status`
- Atualiza o status **imediatamente** quando a página carrega
- Redireciona automaticamente para o app após 3 segundos

**URL configurada no Stripe:**
```python
success_url=f"https://api.leiriaeats.com/payment-success?order_id={new_order.id}"
```

**Vantagem:** O status é atualizado **automaticamente**, sem necessidade de código adicional no app!

---

## 🎯 Fluxo Completo Agora

```
1. Cliente paga no Stripe WebView
   ↓
2. Stripe redireciona para /payment-success?order_id=123
   ↓
3. Página HTML carrega + JavaScript chama /check-payment-status automaticamente
   ↓
4. API consulta Stripe → status=succeeded
   ↓
5. Banco atualizado: status = "Pendente" ✅
   ↓
6. (Em paralelo) Webhook do Stripe também processa (redundância)
   ↓
7. Após 3s → Redirect automático para o app
```

---

## 🔧 Endpoints Criados

### POST /orders/{order_id}/check-payment-status

**Request:**
```bash
curl -X POST https://api.leiriaeats.com/orders/123/check-payment-status
```

**Response (Pagamento Confirmado):**
```json
{
  "order_id": 123,
  "status": "Pendente",
  "payment_confirmed": true,
  "updated": true
}
```

**Response (Já Processado):**
```json
{
  "order_id": 123,
  "status": "Pendente",
  "payment_confirmed": true,
  "already_processed": true
}
```

**Response (Pagamento Pendente):**
```json
{
  "order_id": 123,
  "status": "PENDING_PAYMENT",
  "payment_confirmed": false,
  "payment_status": "processing"
}
```

---

### GET /payment-success?order_id={order_id}

**URL:** `https://api.leiriaeats.com/payment-success?order_id=123`

**Resultado:**
- Página HTML moderna com feedback visual
- Atualização automática do status via JavaScript
- Redirect automático para `https://komaapp.netlify.app/` após 3 segundos

---

## 📱 Integração no App (Opcional)

Se preferir não depender do redirect automático, o app pode chamar manualmente:

```kotlin
// Após detectar sucesso no WebView
lifecycleScope.launch {
    delay(2000) // Aguarda 2s para garantir que Stripe processou
    
    val response = orderApi.checkPaymentStatus(orderId)
    
    if (response.payment_confirmed) {
        showSuccess("Pedido confirmado!")
        navigateToOrderTracking(orderId)
    } else {
        // Tenta novamente após alguns segundos
        delay(3000)
        checkPaymentStatusAgain(orderId)
    }
}
```

---

## ✅ Vantagens da Solução

1. **Redundância** - Webhook + Verificação Manual
2. **Confiável** - Funciona mesmo se o webhook falhar
3. **Rápido** - Atualização instantânea via JavaScript
4. **Transparente** - Usuário não percebe a verificação
5. **Automático** - Nenhuma mudança necessária no app

---

## 🧪 Como Testar

### Teste 1: Verificação Manual
```bash
curl -X POST https://api.leiriaeats.com/orders/1/check-payment-status | jq
```

### Teste 2: Página HTML
Abra no navegador:
```
https://api.leiriaeats.com/payment-success?order_id=1
```

Observe:
- ✅ Página carrega com feedback visual
- ✅ Console do navegador mostra "Status atualizado: {...}"
- ✅ Após 3s → redirect para o app

### Teste 3: Fluxo Completo
1. Crie um pedido via app
2. Pague no WebView com cartão de teste: `4242 4242 4242 4242`
3. Observe o redirect para a página de sucesso
4. Verifique no banco se o status mudou para "Pendente"

---

## 📊 Status do Deploy

- ✅ Código atualizado em produção
- ✅ Endpoint `/orders/{order_id}/check-payment-status` ativo
- ✅ Página `/payment-success` funcionando
- ✅ Webhook do Stripe mantido (redundância)
- ✅ URL de sucesso do checkout atualizada

---

## 🎉 Problema Resolvido!

O status do pedido agora é atualizado **automaticamente e de forma confiável**, independente de:
- Webhooks lentos do Stripe
- Problemas de rede
- Falhas de validação de assinatura

**A solução é robusta, transparente e não requer mudanças no app!** 🚀

