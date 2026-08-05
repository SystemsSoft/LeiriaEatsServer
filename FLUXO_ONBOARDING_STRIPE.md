# 📘 Fluxo de Onboarding Stripe - Leiria Eats

## 🔄 Fluxo Completo (Restaurantes e Drivers)

### 1️⃣ **Cadastro Inicial**
```
POST /companies (restaurante)
POST /drivers/register (driver)
```

**Resultado:**
- ✅ Conta criada no banco
- ✅ `status = "PENDING"`
- ✅ `license = "PENDING"` (restaurantes)
- ✅ `stripe_account_id = NULL`

---

### 2️⃣ **Criação da Conta Stripe**
```
POST /connect/onboarding/{restaurant_id} (restaurante)
// Driver já cria na hora do register
```

**Resultado:**
- ✅ Conta Stripe Express criada
- ✅ `stripe_account_id` preenchido
- ✅ `status = "STRIPE_PENDING"` ← **NORMAL!**
- ✅ `license = "PENDING"` (ainda não mudou)
- ✅ Retorna `onboarding_url` para abrir no WebView

---

### 3️⃣ **Usuário Completa Onboarding**

**No App:**
1. Abre `onboarding_url` em WebView
2. Usuário preenche formulário no Stripe:
   - Nome completo
   - Email
   - Telefone
   - NIF
   - IBAN
   - Documentos
3. Stripe valida e aprova

**Redirecionamento:**
- ✅ Sucesso → `https://api.leiriaeats.com/connect/onboarding-success`
- ❌ Expirado → `https://api.leiriaeats.com/connect/onboarding-refresh`

---

### 4️⃣ **Webhook Automático** 🎉

**Stripe dispara:**
```
POST /stripe-webhook
Event: account.updated
```

**Sistema atualiza automaticamente:**
- ✅ `status = "ACTIVE"` ← **AQUI!**
- ✅ `license = "ATIVO"` ← **AQUI!** (restaurantes)
- ✅ `stripe_onboarding_completed = true`
- ✅ Login liberado ✅

---

## 🚨 Status Esperados em Cada Etapa

| Etapa | Status | License | Login? |
|-------|--------|---------|--------|
| **Cadastro** | `PENDING` | `PENDING` | ❌ Não |
| **Conta Stripe criada** | `STRIPE_PENDING` | `PENDING` | ❌ Não |
| **Onboarding completo** | `ACTIVE` | `ATIVO` | ✅ Sim |

---

## ❓ FAQ - Problemas Comuns

### ❌ "Status ficou em STRIPE_PENDING"
**Normal!** O status só muda para `ACTIVE` **depois** que o webhook disparar.

**Quando o webhook dispara?**
- Quando o usuário **completar** o formulário no Stripe
- Quando o Stripe **aprovar** a conta

**Como testar?**
1. Complete o onboarding no link fornecido
2. Aguarde 10-30 segundos
3. Verifique o banco ou faça login

---

### ❌ "localhost:8080 refused to connect"
**Corrigido!** Os URLs agora apontam para:
- `https://api.leiriaeats.com/connect/onboarding-success`
- `https://api.leiriaeats.com/drivers/onboarding-success`

**O que fazer no app?**
Detecte o redirect e feche o WebView:
```kotlin
if (url.contains("onboarding-success")) {
    // Sucesso! Feche o WebView e recarregue dados
    webView.destroy()
    checkStatus() // Verifica se status = ACTIVE
}
```

---

### ❌ "License ainda está PENDING"
**Corrigido!** Agora o webhook atualiza tanto `status` quanto `license`:
- Webhook: `status = "ACTIVE"` + `license = "ATIVO"`
- Login só funciona quando `license = "ATIVO"`

---

## 🧪 Modo Teste vs Produção

### **Modo TESTE** (atual)
- ✅ Dados fictícios aceitos
- ✅ Aprovação instantânea
- ✅ Webhook dispara normalmente
- ✅ Perfeito para desenvolvimento

### **Modo PRODUÇÃO** (futuro)
- ⚠️ Valida documentos reais
- ⚠️ Verifica IBAN verdadeiro
- ⚠️ Pode demorar horas/dias
- ⚠️ Revisão manual do Stripe

---

## 🔗 Endpoints Adicionados

### Restaurantes
```
GET /connect/onboarding-success
GET /connect/onboarding-refresh
```

### Drivers
```
GET /drivers/onboarding-success
GET /drivers/onboarding-refresh
```

**Response:**
```json
{
  "success": true,
  "message": "Onboarding concluído!",
  "redirect": "komapartner://onboarding-success"
}
```

---

## ✅ Checklist de Teste

1. [ ] Criar restaurante → `status = "PENDING"`
2. [ ] Chamar `/connect/onboarding/{id}` → recebe URL
3. [ ] Abrir URL no navegador → preencher dados
4. [ ] Ver redirect para `/onboarding-success`
5. [ ] Aguardar 30s → verificar status no banco
6. [ ] `status = "ACTIVE"` ✅
7. [ ] `license = "ATIVO"` ✅
8. [ ] Login funciona ✅

---

**Última atualização:** 5 de Agosto de 2026

