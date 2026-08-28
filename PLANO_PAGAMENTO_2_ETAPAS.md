# Plano — Pagamento em 2 Etapas (Autorização + Captura) e Repasse Multi-Restaurante

**Objetivo:** o cartão do cliente é **autorizado** no momento em que ele finaliza o pedido
(insere o cartão e confirma), mas o valor só é **efetivamente cobrado** quando o
restaurante aceita. Se o restaurante recusar, a autorização é liberada — sem cobrança e,
portanto, sem reembolso nem espera de estorno.

**Escopo adicional:** corrigir o repasse aos restaurantes em pedidos multi-restaurante,
hoje inexistente.

> Código proposto aqui é rascunho: pressupõe revisão, testes em ambiente Stripe de teste e
> aprovação antes de qualquer uso em produção. Alterações que movem dinheiro exigem
> validação da área financeira antes do deploy.

---

## 1. Descoberta que muda o tamanho do trabalho

As duas mudanças pedidas **não são independentes — são a mesma mudança.**

Hoje o pagamento usa *Destination Charge*: o `PaymentIntent` é criado com
`transfer_data={"destination": ...}` ([order_routes.py:370](api/routes/order_routes.py:370)),
que repassa automaticamente para **um** restaurante. Isso tem duas limitações que se
combinam:

- `transfer_data` aceita **um único destino** — por isso multi-restaurante nunca repassou.
- Captura parcial (necessária quando um dos restaurantes recusa) com `transfer_data` +
  `application_fee_amount` obriga a recalcular a comissão no momento da captura, e continua
  sem resolver os outros destinos.

O modelo que resolve os dois é o mesmo: **Separate Charges and Transfers** — o valor é
capturado inteiro na conta da plataforma e depois distribuído com `stripe.Transfer.create`
por restaurante. Ou seja, fazer as duas juntas custa menos do que fazer cada uma
separadamente.

## 2. Segunda descoberta: o "aceite do restaurante" não existe hoje

Os status usados no projeto são `PENDING_PAYMENT`, `Pendente`, `Em preparo`, `A caminho`,
`Entregue` e `Cancelado`. Não há `Aceito` nem `Recusado`: o restaurante simplesmente chama
`PUT /orders/sub-order/{gid}/status` ([order_routes.py:863](api/routes/order_routes.py:863))
e move o pedido para "Em preparo".

Isso significa que este plano **não é só mover o momento da cobrança** — é criar o conceito
de aceite/recusa, que hoje não está modelado. É a maior parte do esforço.

---

## 3. A restrição central: só se captura UMA vez

Um `PaymentIntent` do Stripe admite **uma única captura**. Ela pode ser parcial (captura-se
menos do que foi autorizado, e o restante é liberado), mas **não é possível capturar em
parcelas conforme cada restaurante for aceitando**.

Com até 3 restaurantes por pedido e aceite independente, isso força uma escolha:

### Opção A — 1 autorização para o pedido inteiro, captura parcial única ✅ recomendada

- Autoriza o valor total no checkout.
- Cada restaurante aceita ou recusa o seu sub-pedido.
- Quando **todos** responderem (ou o prazo expirar), captura-se
  `soma dos sub-pedidos aceitos`. Os recusados simplesmente não entram na captura.
- Se todos recusarem: `PaymentIntent.cancel()` — nenhuma cobrança.

**A favor:** uma autorização só no extrato do cliente; a autenticação forte (SCA/3DS,
obrigatória na UE) acontece uma vez, on-session, no checkout — que é exatamente onde o
cliente está presente para autenticar.

**Contra:** a captura espera o restaurante mais lento. Precisa de um **prazo de resposta**
com recusa automática (ver 6.3).

### Opção B — 1 autorização por sub-pedido

Cada sub-pedido teria o seu próprio `PaymentIntent`, capturado quando aquele restaurante
aceita. Dá independência real, mas: o cliente vê N autorizações pendentes no cartão, e as
autorizações a partir da segunda teriam de ser criadas *off-session* — o que, sob PSD2 na
Europa, pode falhar com `authentication_required` justamente quando o cliente já saiu da
tela. Recomendo não seguir por aqui.

**Recomendação: Opção A.** O resto do plano assume essa escolha.

---

## 4. Fluxo proposto

```
Cliente confirma o pedido e o cartão
        │
        ▼
  PaymentIntent (capture_method="manual", SEM transfer_data)
  → SCA/3DS aqui, com o cliente presente
        │
        ▼
  webhook: payment_intent.amount_capturable_updated
  → pedido = AGUARDANDO_ACEITE, dinheiro reservado mas NÃO cobrado
        │
        ├──► restaurante aceita   → sub-pedido = Aceito
        ├──► restaurante recusa   → sub-pedido = Recusado
        └──► prazo expira         → sub-pedido = Recusado (automático)
        │
        ▼
  todos os sub-pedidos responderam?
        │
        ├── nenhum aceito ──► PaymentIntent.cancel()
        │                     Sem cobrança. Sem reembolso. Pedido cancelado.
        │
        └── ao menos um ───► PaymentIntent.capture(
                              amount_to_capture = soma dos aceitos)
                                    │
                                    ▼
                            webhook: payment_intent.succeeded
                                    │
                                    ▼
                            Transfer.create por restaurante aceito
                            (source_transaction = charge da captura)
```

O ganho pedido está no ramo do meio: **recusa não gera reembolso**, porque nunca houve
cobrança — apenas uma reserva liberada.

---

## 5. Mudanças de schema

> ⚠️ **Migração de banco.** Todas as colunas abaixo são `NULL`/com default, portanto
> aditivas e sem reescrita de dados. Ainda assim: validar em homologação, ter backup
> verificado e janela definida antes de aplicar em produção.

```sql
-- migration_pagamento_2_etapas.sql  (rascunho)

-- Estado do pagamento, separado do estado logístico do pedido
ALTER TABLE orders ADD COLUMN payment_status VARCHAR(50) NULL;
    -- REQUIRES_PAYMENT | AUTHORIZED | CAPTURED | CANCELED | FAILED
ALTER TABLE orders ADD COLUMN authorized_amount DOUBLE NULL;
ALTER TABLE orders ADD COLUMN captured_amount DOUBLE NULL;
ALTER TABLE orders ADD COLUMN authorization_expires_at DATETIME NULL;
ALTER TABLE orders ADD COLUMN payment_flow VARCHAR(20) NULL DEFAULT 'AUTO_CAPTURE';
    -- AUTO_CAPTURE (pedidos antigos) | MANUAL_CAPTURE (novo fluxo)

-- Aceite e repasse por restaurante
ALTER TABLE sub_orders ADD COLUMN accepted_at DATETIME NULL;
ALTER TABLE sub_orders ADD COLUMN declined_at DATETIME NULL;
ALTER TABLE sub_orders ADD COLUMN decline_reason VARCHAR(255) NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_id VARCHAR(255) NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_amount DOUBLE NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_reversed DOUBLE NULL DEFAULT 0;
```

**Por que `payment_flow`:** pedidos criados antes do deploy foram cobrados na hora
(auto-captura). Os endpoints de cancelamento precisam saber qual caminho seguir — estorno
(antigo) ou liberação de autorização (novo). Sem essa coluna, um pedido antigo cancelado
depois do deploy seguiria a lógica errada.

**Por que `stripe_transfer_id` e `stripe_transfer_reversed`:** idempotência. O webhook do
Stripe pode ser reentregue; sem registro do que já foi transferido, um reenvio pagaria o
restaurante duas vezes. E reversões parciais precisam saber quanto já foi revertido.

### Novos status

| Entidade | Status novo | Significado |
|---|---|---|
| `OrderDB` | `AGUARDANDO_ACEITE` | Autorizado, esperando resposta dos restaurantes |
| `SubOrderDB` | `AGUARDANDO_ACEITE` | Aguardando este restaurante responder |
| `SubOrderDB` | `Aceito` | Restaurante aceitou; entra na captura |
| `SubOrderDB` | `Recusado` | Recusado (pelo restaurante ou por prazo); fora da captura |

---

## Fase 0 — Filtro de aptidão de pagamento na IA *(1 dia — independente, entregar primeiro)*

Bloquear no checkout é a rede de segurança — mas sozinha faz o cliente descobrir o problema
só no fim, depois de montar o pedido inteiro conversando. Como o carrinho é construído
**exclusivamente via chat**, a correção certa é a IA nunca oferecer um produto de
restaurante inapto a receber pagamento.

Com as duas camadas juntas, o 400 do checkout passa a ser um caso raro e legítimo: só
dispara se a situação do restaurante mudar **entre** a conversa e o pagamento (conta Stripe
desativada no meio do pedido, por exemplo). É essa a relação correta entre as camadas.

> **Esta fase pode ir para produção sozinha, antes de todo o resto do plano.** Não
> depende da captura em duas etapas nem do Transfer. Hoje, um pedido multi-restaurante que inclua um
> restaurante sem conta Stripe simplesmente nunca repassa o dinheiro dele — o filtro já
> evita isso sozinho. Recomendo tratá-la como entrega independente, antes da Fase 1.

### Definição de "apto a receber pagamento"

```python
# rascunho
def _restaurante_apto_a_receber(restaurante) -> bool:
    return bool(
        restaurante
        and restaurante.stripe_account_id
        and restaurante.stripe_onboarding_completed
    )
```

Usa `stripe_onboarding_completed` (e não `status == "ACTIVE"`) porque a pergunta aqui é
estritamente "esta conta consegue receber dinheiro?". O `status` é a situação comercial do
restaurante, um conceito mais amplo — e já é mantido em sincronia pelo webhook
`account.updated` ([order_routes.py:1041](api/routes/order_routes.py:1041)), que atualiza
os dois campos juntos.

### Onde aplicar o filtro — e por que não no índice

O caminho tentador seria não indexar os produtos de restaurantes inaptos em
`AIService._index_data`. **Não fazer isso:** a reindexação só acontece em operações de CRUD
de produto/restaurante, então um restaurante que concluísse o onboarding do Stripe ficaria
invisível para a IA até alguém salvar um produto — podendo demorar dias.

O filtro deve ficar na montagem do pool, com o conjunto de aptos em cache junto do índice:

```python
# rascunho — services/ai_service.py
_restaurantes_aptos: set = set()      # GIDs aptos a receber pagamento

# dentro de _index_data, no mesmo rebind atômico dos outros caches:
restaurantes_aptos = {
    r.gid for r in restaurants
    if r.gid and r.stripe_account_id and r.stripe_onboarding_completed
}
...
cls._restaurantes_aptos = restaurantes_aptos
```

```python
# rascunho — services/hybrid_ai_service.py, junto de
# _filtrar_pool_por_restaurantes_travados (mesmo ponto do pipeline)
@staticmethod
def _filtrar_pool_por_aptidao_de_pagamento(candidate_pool: list, session: UserSession) -> list:
    """Remove do pool produtos de restaurante que não consegue receber pagamento.
    Itens já no carrinho continuam visíveis — senão o cliente ficaria preso com um
    item que a conversa não consegue mais remover (mesma razão do filtro de limite
    de restaurantes)."""
    aptos = AIService._restaurantes_aptos
    if not aptos:
        return candidate_pool          # cache ainda não carregado: não filtra nada
    ids_no_carrinho = {item.product_id for item in session.cart}
    return [
        p for p in candidate_pool
        if getattr(p, "restaurant_gid", "") in aptos or p.id in ids_no_carrinho
    ]
```

Aplicar **nos dois** métodos (`process_sales_chat` e `process_sales_chat_stream`), no mesmo
ponto onde já roda o filtro de limite de restaurantes.

### Detalhes que é fácil errar

- **Cache vazio ≠ ninguém apto.** Se `_restaurantes_aptos` estiver vazio porque o índice
  ainda não carregou, filtrar deixaria o pool vazio e a IA sem nada a oferecer. O
  `if not aptos: return candidate_pool` acima trata isso — falha para o lado permissivo,
  com o checkout ainda protegendo.
- **Itens do carrinho continuam visíveis**, como no filtro de limite de restaurantes: se um
  restaurante ficar inapto no meio da conversa, o cliente precisa conseguir remover o item.
- **`restaurantResults` também.** O payload de resposta devolve restaurantes mencionados;
  aplicar o mesmo filtro para não exibir cartão de restaurante que não pode vender.
- **Atualizar o cache quando o onboarding concluir.** O webhook `account.updated` já
  atualiza o banco; acrescentar ali uma chamada de `AIService.reload_data(db)` (ou um
  refresh só do conjunto de aptos) para o restaurante passar a aparecer sem esperar o
  próximo CRUD.

### Testes

| # | Cenário | Esperado |
|---|---|---|
| 1 | Restaurante sem `stripe_account_id` | Produtos fora do pool da IA |
| 2 | Restaurante com conta mas `stripe_onboarding_completed=False` | Produtos fora do pool |
| 3 | Restaurante apto | Produtos no pool normalmente |
| 4 | Item de restaurante inapto já no carrinho | Continua visível (removível pela conversa) |
| 5 | Cache de aptos vazio | Pool não é filtrado (falha permissiva) |
| 6 | Onboarding concluído via webhook | Restaurante volta a aparecer sem novo CRUD |

As decisões financeiras que sustentam esta fase estão registadas no fim do documento
(decisão 3: bloquear restaurante sem conta Stripe apta).

---

## Fase 1 — Autorizar em vez de cobrar *(2 dias)*

### 1.1 `initiate-checkout` passa a autorizar

```python
# rascunho — order_routes.py, na criação do PaymentIntent
payment_intent_params = {
    "amount": amount_cents,
    "currency": "eur",
    "customer": stripe_customer_id,
    "capture_method": "manual",          # ← autoriza, não cobra
    "automatic_payment_methods": {"enabled": True},
    "metadata": {
        "order_id": str(new_master_order.id),
        "master_gid": master_order_gid,
        "user_id": order_data.user_id,
        "payment_flow": "MANUAL_CAPTURE",
    },
}
# transfer_data e application_fee_amount são REMOVIDOS:
# o valor fica na conta da plataforma e é distribuído por Transfer na Fase 4.
```

`new_master_order.payment_flow = "MANUAL_CAPTURE"` e `payment_status = "REQUIRES_PAYMENT"`.

### 1.2 O mesmo para o cartão salvo

`_try_automatic_payment_with_saved_card` ([order_routes.py:141](api/routes/order_routes.py:141))
também cria `PaymentIntent` com `transfer_data`. Precisa de `capture_method="manual"` e da
remoção do `transfer_data`/`application_fee_amount`. O fallback para o fluxo com UI, quando
o cartão exige autenticação, deve ser preservado.

### 1.3 Webhook: o evento muda

Este é o ponto mais fácil de errar. Com captura manual, `payment_intent.succeeded`
**deixa de disparar na autorização** e passa a disparar só depois da captura. O webhook
hoje ([order_routes.py:991](api/routes/order_routes.py:991)) usa esse evento para marcar o
pedido como pago — se nada mudar, o pedido fica preso em `PENDING_PAYMENT` para sempre.

| Evento | Quando dispara | O que fazer |
|---|---|---|
| `payment_intent.amount_capturable_updated` | Autorização aprovada | `payment_status=AUTHORIZED`, pedido → `AGUARDANDO_ACEITE`, sub-pedidos → `AGUARDANDO_ACEITE` |
| `payment_intent.succeeded` | Após a captura | `payment_status=CAPTURED` → dispara os repasses (Fase 4) |
| `payment_intent.canceled` | Autorização liberada | `payment_status=CANCELED` |
| `payment_intent.payment_failed` | Autorização recusada | `payment_status=FAILED`, pedido cancelado |

> Confirmar os nomes e o comportamento destes eventos na documentação oficial do Stripe
> antes de implementar — não assumir a partir deste documento.

---

## Fase 2 — Aceite e recusa por restaurante *(2 dias)*

### 2.1 Endpoints novos

```
POST /orders/sub-order/{gid}/accept    → sub.status = "Aceito",   accepted_at = agora
POST /orders/sub-order/{gid}/decline   → sub.status = "Recusado", declined_at = agora
                                          body opcional: {"reason": "..."}
```

Ambos, ao final, chamam `_liquidar_pedido_se_todos_responderam(master, db)` (Fase 3).

**Compatibilidade:** manter `PUT /orders/sub-order/{gid}/status` funcionando. Se o app do
restaurante mandar "Em preparo" num sub-pedido ainda em `AGUARDANDO_ACEITE`, tratar como
aceite implícito — senão o app antigo trava o pedido até o prazo expirar. Ponto a alinhar
com o time do app do restaurante.

### 2.2 Recusa não é cancelamento

Hoje, quando o restaurante recusa, cai em `cancel_sub_order_and_partial_refund`, que
processa um **reembolso**. No fluxo novo, antes da captura, recusar não deve tocar em
`stripe.Refund` — não há o que reembolsar. É preciso separar os dois caminhos:

```python
# rascunho
if master.payment_flow == "MANUAL_CAPTURE" and master.payment_status == "AUTHORIZED":
    sub.status = "Recusado"          # nenhuma chamada de reembolso
    sub.declined_at = agora_utc()
else:
    ...  # caminho antigo, com Refund (pedidos AUTO_CAPTURE ou já capturados)
```

---

## Fase 3 — Liquidação: capturar ou cancelar *(2 dias)*

```python
# rascunho — o coração do fluxo
def _liquidar_pedido_se_todos_responderam(master, db) -> None:
    pendentes = [s for s in master.sub_orders if s.status == "AGUARDANDO_ACEITE"]
    if pendentes:
        return  # ainda falta alguém responder

    aceitos = [s for s in master.sub_orders if s.status == "Aceito"]

    if not aceitos:
        # Nenhum restaurante aceitou → libera a reserva. Sem cobrança, sem reembolso.
        stripe.PaymentIntent.cancel(master.payment_intent_id)
        master.payment_status = "CANCELED"
        master.status = "Cancelado"
        db.commit()
        return

    valor_cents = _calcular_valor_a_capturar(master, aceitos)
    stripe.PaymentIntent.capture(
        master.payment_intent_id,
        amount_to_capture=valor_cents,
        # chave de idempotência evita captura dupla se dois restaurantes
        # responderem ao mesmo tempo e as duas requisições chegarem juntas
        idempotency_key=f"capture_order_{master.gid}",
    )
    master.captured_amount = valor_cents / 100
    master.payment_status = "CAPTURED"
    db.commit()
    # Os repasses acontecem no webhook payment_intent.succeeded (Fase 4)
```

### 3.1 Quanto capturar — decisão de negócio pendente

`sub.total` já inclui a taxa de entrega daquele restaurante
([order_routes.py:341](order_routes.py:341)), e `master.total` soma produtos + taxa de
entrega total + taxa de serviço. Então:

```
valor_a_capturar = Σ(sub.total dos aceitos) + taxa_de_serviço_ajustada
```

**A definir com o financeiro:** quando um dos três restaurantes recusa, a taxa de serviço
cai proporcionalmente, permanece integral, ou é recalculada? Não implementar antes dessa
resposta — é dinheiro cobrado do cliente.

### 3.2 Corrida entre dois restaurantes respondendo ao mesmo tempo

Se dois restaurantes responderem simultaneamente, as duas requisições podem ver "todos
responderam" e tentar capturar. Duas defesas, ambas necessárias:

- `idempotency_key` na chamada de captura (acima) — o Stripe devolve a mesma captura em
  vez de cobrar duas vezes;
- `SELECT ... FOR UPDATE` na linha do pedido antes de liquidar, para serializar no banco.

---

## Fase 4 — Repasse via Transfer API *(2 dias)*

Disparado pelo webhook `payment_intent.succeeded` (agora = captura confirmada).

```python
# rascunho
def _repassar_para_restaurantes(master, db) -> None:
    pi = stripe.PaymentIntent.retrieve(master.payment_intent_id)
    charge_id = pi.latest_charge          # necessário para source_transaction
    if not charge_id:
        return

    for sub in master.sub_orders:
        if sub.status != "Aceito":
            continue
        if sub.stripe_transfer_id:        # idempotência: já repassado
            continue

        restaurante = sub.restaurant
        if not restaurante or not restaurante.stripe_account_id:
            print(f"⚠️ Sub-pedido {sub.gid} sem conta Stripe — repasse pendente")
            continue

        valor_cents = _calcular_repasse_restaurante(sub, restaurante)
        transfer = stripe.Transfer.create(
            amount=valor_cents,
            currency="eur",
            destination=restaurante.stripe_account_id,
            source_transaction=charge_id,   # vincula o repasse à cobrança
            metadata={"sub_order_gid": sub.gid, "master_order_gid": master.gid},
            idempotency_key=f"transfer_sub_{sub.gid}",
        )
        sub.stripe_transfer_id = transfer.id
        sub.stripe_transfer_amount = valor_cents / 100
        db.commit()
```

`source_transaction` amarra a transferência à cobrança específica, para o Stripe só liberar
o dinheiro quando os fundos daquela cobrança estiverem disponíveis.

### 4.1 Cálculo do repasse — validar com o financeiro

Espelhando a lógica atual de `platform_fee`
([order_routes.py:362](api/routes/order_routes.py:362)) e `get_commission_rate`:

```
repasse = produtos_do_sub × (1 − comissão)
        + taxa_de_entrega_do_sub  (somente se restaurante.use_own_delivery)
```

Comissões hoje: 15% (entrega própria), 18% (ESSENCE), 21% (SMART). **Confirmar com o
financeiro antes de implementar** — é o valor que o restaurante recebe.

### 4.2 Consequência a assinalar

Sair de *Destination Charge* para *Separate Charges and Transfers* muda quem é o titular da
cobrança perante o Stripe: passa a ser a plataforma. Isso afeta quem arca com as taxas do
Stripe e com a responsabilidade por chargebacks, e muda o que o restaurante vê no painel
dele. **Não é uma decisão técnica** — precisa de validação financeira e, possivelmente,
revisão dos contratos com os restaurantes. Recomendo consultar a área responsável.

---

## Fase 5 — Cancelamento e estorno reescritos *(2 dias)*

O caminho correto passa a depender do estado do pagamento:

| Estado | Ação | Reembolso? |
|---|---|---|
| `AUTHORIZED` (antes da captura) | `PaymentIntent.cancel()` | Não — só libera a reserva |
| `CAPTURED`, sem repasse feito | `Refund.create()` | Sim |
| `CAPTURED`, já repassado | `Refund.create()` + `Transfer.create_reversal()` | Sim, com reversão |
| `payment_flow == AUTO_CAPTURE` (legado) | caminho atual, inalterado | Sim |

### 5.1 O `reverse_transfer=True` atual deixa de funcionar

`cancel_order_and_refund` ([:628](api/routes/order_routes.py:628)) e
`cancel_sub_order_and_partial_refund` ([:743](api/routes/order_routes.py:743)) usam
`reverse_transfer=True`. Esse parâmetro reverte apenas o repasse **automático** de um
Destination Charge — não tem efeito sobre Transfers criados separadamente. Com o modelo
novo, é preciso reverter explicitamente:

```python
# rascunho — reversão de um repasse já feito
if sub.stripe_transfer_id:
    reversao = stripe.Transfer.create_reversal(
        sub.stripe_transfer_id,
        amount=int(sub.stripe_transfer_amount * 100),
        idempotency_key=f"reversal_sub_{sub.gid}",
    )
    sub.stripe_transfer_reversed = (sub.stripe_transfer_reversed or 0) + reversao.amount / 100
```

> Se este item não for feito junto da Fase 4, cancelar um pedido já repassado devolve o
> dinheiro ao cliente **sem** retirá-lo do restaurante — prejuízo direto da plataforma.
> Fases 4 e 5 devem ir para produção juntas.

---

## Fase 6 — Prazo de resposta e reconciliação *(1,5 dia)*

### 6.1 Por que existe um prazo

Uma autorização de cartão não dura para sempre — o Stripe cancela `PaymentIntents` não
capturados após cerca de 7 dias, e a rede do cartão pode liberar a reserva antes disso.
Para delivery, o prazo útil é de minutos. **Confirmar o prazo exato de expiração na
documentação do Stripe** e definir o prazo de negócio bem abaixo dele.

### 6.2 Sugestão de prazo

`PRAZO_ACEITE_MINUTOS = 15` (configurável por variável de ambiente). Um restaurante que não
responder em 15 minutos tem o sub-pedido recusado automaticamente. Valor a validar com a
operação.

### 6.3 Extensão do worker existente

`services/payment_reconciliation_service.py` já roda a cada 30s. Acrescentar:

1. Sub-pedidos em `AGUARDANDO_ACEITE` há mais de `PRAZO_ACEITE_MINUTOS` → recusar
   automaticamente e liquidar o pedido.
2. Pedidos `CAPTURED` com sub-pedidos `Aceito` sem `stripe_transfer_id` → repassar
   (cobre webhook perdido; é idempotente).
3. Pedidos `AUTHORIZED` próximos da expiração da autorização → cancelar e avisar.

### 6.4 Risco a assinalar

Na Opção A, um restaurante pode aceitar e começar a preparar enquanto outro ainda não
respondeu. Se a captura falhar depois disso (cartão sem saldo no momento da captura,
autorização expirada), a comida já está sendo feita e não há cobrança. O prazo curto reduz
a janela, mas não a elimina. Vale definir com a operação o que fazer nesse caso — é raro,
mas acontece.

---

## Fase 7 — Rollout *(1,5 dia)*

1. **Flag** `PAGAMENTO_CAPTURA_MANUAL` (padrão **ligada** — decisão de 2026-08-28: o
   projeto ainda não lançou, não há pedidos antigos em produção para manter
   compatibilidade, então a captura manual passou a ser o comportamento padrão sem
   precisar configurar nada. Setar a variável como `false` explicitamente volta ao
   comportamento antigo, só para depuração pontual).
2. **Coexistência:** pedidos antigos têm `payment_flow='AUTO_CAPTURE'` e continuam no
   caminho antigo de cancelamento. Nenhum pedido em andamento muda de regra no meio.
3. **Homologação:** rodar os cenários da tabela abaixo em modo de teste do Stripe.
4. **Rollout gradual**, acompanhando: taxa de autorização recusada, tempo médio até o
   aceite, repasses pendentes, capturas falhadas.
5. **Reversão:** desligar a flag. Pedidos já autorizados sob o fluxo novo precisam ser
   liquidados pelo worker antes de desativar por completo — não basta desligar a flag e
   fazer deploy.

### Cenários de teste obrigatórios

| # | Cenário | Resultado esperado |
|---|---|---|
| 1 | 1 restaurante aceita | Captura total; 1 transfer |
| 2 | 1 restaurante recusa | `PaymentIntent.cancel()`; **sem** reembolso; pedido cancelado |
| 3 | 3 restaurantes, todos aceitam | Captura total; 3 transfers |
| 4 | 3 restaurantes, 1 recusa | Captura parcial (2 sub-pedidos); 2 transfers; nada cobrado do recusado |
| 5 | 3 restaurantes, todos recusam | Cancelamento; sem cobrança |
| 6 | 1 não responde no prazo | Recusa automática; liquidação com os demais |
| 7 | Cliente cancela antes do aceite | Liberação da reserva; sem reembolso |
| 8 | Cliente cancela depois do repasse | Refund + reversal |
| 9 | Webhook reentregue | Sem captura dupla, sem transfer duplo |
| 10 | Dois restaurantes respondem ao mesmo tempo | Uma única captura |
| 11 | Cartão exige 3DS | Autenticação no checkout; captura posterior sem nova autenticação |
| 12 | Pedido legado (`AUTO_CAPTURE`) cancelado após o deploy | Segue o caminho antigo com reembolso |

---

## Resumo de esforço

| Fase | Dias |
|---|---|
| **0 — Filtro de aptidão na IA** *(independente, vai primeiro)* | **1** |
| 1 — Autorização + webhook | 2 |
| 2 — Aceite/recusa | 2 |
| 3 — Liquidação | 2 |
| 4 — Repasse (Transfer) | 2 |
| 5 — Cancelamento/estorno | 2 |
| 6 — Prazo + reconciliação | 1,5 |
| 7 — Rollout e testes | 1,5 |
| **Total** | **~14 dias** |

Migração de schema, app do restaurante (botões de aceitar/recusar) e app do cliente
(mostrar "aguardando confirmação") são trabalhos adicionais fora deste repositório.

---

## Decisões financeiras — FECHADAS

Confirmadas com o responsável em 2026-08-27. As Fases 3 e 4 estão desbloqueadas.

| # | Decisão | Resposta |
|---|---|---|
| 1 | Taxa de serviço quando um restaurante recusa | **Integral.** O cliente paga a taxa cheia, independente de quantos aceitaram. |
| 2 | Taxa do Stripe (Separate Charges) | **Plataforma absorve.** A comissão de 15–21% já cobre esse custo; o restaurante recebe o repasse integral, sem dedução extra. |
| 3 | Restaurante sem conta Stripe apta | **Bloquear no checkout** com 400. Nunca aceitar pedido cujo valor não tenha para onde ser repassado. |
| 4 | Comissão em pedido multi-restaurante | **Por restaurante**, sobre os produtos dele, usando o plano dele. |

### Fórmulas resultantes (implementar exatamente assim)

```python
# rascunho — valor a capturar quando parte dos restaurantes recusa
valor_a_capturar = soma(sub.total dos ACEITOS) + master.total_service_fee
#                                                 ^ integral, decisão 1

# rascunho — repasse por restaurante aceito (decisões 2 e 4)
comissao = get_commission_rate(restaurante.plan, restaurante.use_own_delivery)
repasse   = produtos_do_sub * (1 - comissao)
if restaurante.use_own_delivery:
    repasse += sub.delivery_fee
# Nenhuma dedução de taxa do Stripe: a plataforma absorve (decisão 2).
```

Conferência com o exemplo do documento (A e B aceitam, C recusa):

| | Valor |
|---|---|
| Capturado do cliente | 35 (produtos) + 6 (entrega) + 2,00 (serviço integral) = **€43,00** |
| Repasse Rest. A (ESSENCE 18%) | 20,00 × 0,82 = **€16,40** |
| Repasse Rest. B (SMART 21%) | 15,00 × 0,79 = **€11,85** |
| Margem bruta da plataforma | 43,00 − 28,25 = **€14,75** |
| Taxa do Stripe (absorvida, ~1,5% + €0,25) | ≈ −€0,90 |
| **Margem líquida** | **≈ €13,85** |

### Consequência da decisão 3 na camada de IA

Bloquear no checkout é a rede de segurança, mas sozinha faz o cliente descobrir o problema
só no fim. Como o carrinho é construído exclusivamente via chat, a IA também precisa deixar
de oferecer produtos de restaurante inapto. Isso virou a **Fase 0** deste plano — é
independente do resto e deve ir para produção antes.

---

## Decisões ainda pendentes (não bloqueantes)

| # | Decisão | Quem decide |
|---|---|---|
| 5 | Opção A (uma autorização) vs. B (uma por restaurante) — recomendação: A | Produto + tecnologia |
| 6 | Prazo de aceite (sugestão: 15 min) | Operação |
| 7 | O que fazer se a captura falhar após o restaurante já ter começado a preparar | Operação |
| 8 | App do restaurante ganha botões explícitos de aceitar/recusar | Produto |

Nenhuma delas impede começar a Fase 1. As decisões 6 e 7 são necessárias antes da Fase 6.
