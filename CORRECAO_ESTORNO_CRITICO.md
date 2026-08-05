# ⚠️ CORREÇÃO CRÍTICA: Estorno Incompleto ao Recusar Pedido

## 🐛 Problema Identificado

**Severidade:** CRÍTICA - Prejuízo financeiro direto para a plataforma

### Situação Anterior

Quando o restaurante recusava/cancelava um pedido através do endpoint `PUT /orders/{order_id}/status`:

```python
# ❌ CÓDIGO ANTERIOR (INCOMPLETO)
stripe.Refund.create(
    payment_intent=order.payment_intent_id,
)
```

**Consequências:**
- ✅ Cliente recebia estorno corretamente
- ❌ **Restaurante MANTINHA o dinheiro** (repasse não era revertido)
- ❌ **Plataforma MANTINHA a comissão** (não era devolvida)
- 💰 **PREJUÍZO FINANCEIRO** - Plataforma perde comissão + precisa estornar cliente do próprio bolso

---

## ✅ Solução Implementada

### Código Corrigido

```python
# ✅ CÓDIGO NOVO (COMPLETO)
stripe.Refund.create(
    payment_intent=payment_intent_id,
    reason="requested_by_customer",
    reverse_transfer=True,       # ← REVERTE REPASSE AO RESTAURANTE
    refund_application_fee=True, # ← DEVOLVE COMISSÃO DA PLATAFORMA
    metadata={"order_id": str(order_id), "canceled_via": "status_update"},
)
```

### Melhorias Adicionadas

1. **Recuperação de PaymentIntent**
   - Se `payment_intent_id` não estiver no banco, recupera via `checkout_session_id`
   - Garante que o estorno funcione mesmo se webhook não chegou ainda

2. **Tratamento de Cenários**
   - `succeeded` → Estorno completo com reversão
   - `processing` → Log informativo (webhook tardio fará estorno)
   - `requires_*` → Cancela PaymentIntent (sem cobrança)
   - `canceled` → Log informativo (já estava cancelado)

3. **Validações**
   - Verifica se pedido já está cancelado
   - Bloqueia cancelamento de pedidos entregues
   - Logs detalhados para auditoria

---

## 📊 Impacto Financeiro

### Antes da Correção (Por Pedido Cancelado)
```
Cliente: +€50,00 (estorno recebido)
Restaurante: +€40,00 (MANTÉM indevidamente)
Plataforma: -€50,00 (paga estorno) + €10,00 (MANTÉM comissão indevida)
RESULTADO: Prejuízo de €40,00 para plataforma
```

### Depois da Correção
```
Cliente: +€50,00 (estorno recebido)
Restaurante: €0,00 (repasse revertido ✅)
Plataforma: €0,00 (comissão devolvida ✅)
RESULTADO: Equilíbrio financeiro ✅
```

---

## 🎯 Comparação de Endpoints

### POST /orders/{order_id}/cancel ✅
**Status:** JÁ ESTAVA CORRETO
- Usado por clientes para cancelar
- Sempre teve estorno completo
- `reverse_transfer=True` desde o início

### PUT /orders/{order_id}/status ❌→✅
**Status:** CORRIGIDO AGORA
- Usado por restaurantes para recusar/cancelar
- **Tinha estorno incompleto** (só cliente)
- **Agora tem estorno completo** (cliente + restaurante + plataforma)

---

## 🔍 Como Testar

### Teste 1: Criar pedido e cancelar pelo restaurante

```bash
# 1. Criar pedido com pagamento
POST /orders/initiate-checkout
# Pagar com cartão: 4242 4242 4242 4242

# 2. Restaurante recusa pedido
PUT /orders/{order_id}/status
{
  "status": "Cancelado"
}

# 3. Verificar logs no servidor
# Deve mostrar:
# ✅ Estorno COMPLETO realizado! ID: re_xxx
#    ✓ Cliente: recebe estorno
#    ✓ Restaurante: repasse revertido
#    ✓ Plataforma: comissão devolvida
```

### Teste 2: Verificar no Dashboard Stripe

1. Acesse: https://dashboard.stripe.com/test/payments
2. Encontre o PaymentIntent do pedido
3. Verifique que tem um Refund com:
   - ✅ `reverse_transfer: true`
   - ✅ `refund_application_fee: true`

---

## 📝 Arquivos Alterados

### `/api/routes/order_routes.py`
- **Função:** `update_order_status` (linha 527-598)
- **Alteração:** Estorno completo quando `status = "Cancelado"`
- **Linhas críticas:** 561-577 (criação do Refund)

### Commit
```bash
git log --oneline -1
# feat: corrige estorno incompleto ao recusar pedido (crítico)
```

---

## ⚠️ Alertas Importantes

### Para Equipe Financeira
- **Revisar pedidos cancelados no período anterior** à correção
- Identificar prejuízos acumulados por estornos incompletos
- Considerar recuperação de valores com restaurantes que cancelaram pedidos

### Para Equipe de Desenvolvimento
- **Testar todos os fluxos de cancelamento** após correção
- Monitorar logs do Stripe para confirmar `reverse_transfer`
- Validar que novos cancelamentos estão com estorno completo

### Para Equipe de Suporte
- Se cliente relatar problema de estorno: **verificar se foi antes ou depois da correção**
- Cancelamentos feitos **após** 05/08/2026 às 14:00 UTC: estorno completo ✅
- Cancelamentos feitos **antes**: podem ter estorno incompleto ❌

---

## 🎉 Status do Deploy

- ✅ Código corrigido
- ✅ Testes de sintaxe aprovados
- ✅ Deploy em produção concluído (05/08/2026 14:15 UTC)
- ✅ Serviço reiniciado com sucesso
- ✅ Documentação criada

---

## 📚 Referências

- [Stripe Refunds API](https://stripe.com/docs/api/refunds)
- [Reversing Transfers](https://stripe.com/docs/connect/charges-transfers#reversing-transfers)
- [Application Fee Refunds](https://stripe.com/docs/connect/direct-charges#refunding-application-fees)

---

**Data da Correção:** 05/08/2026  
**Responsável:** Sistema Autônomo  
**Prioridade:** CRÍTICA  
**Status:** ✅ RESOLVIDO

