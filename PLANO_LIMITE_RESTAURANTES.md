# Planejamento — Limite de 3 Restaurantes por Pedido

**Objetivo:** limitar um pedido a no máximo 3 restaurantes distintos e, ao atingir esse
limite, fazer a IA sugerir apenas produtos dos restaurantes já escolhidos — até o pedido
ser finalizado ou algum restaurante sair do carrinho.

**Base:** leitura do código atual em `services/hybrid_ai_service.py`,
`services/session_service.py`, `services/gemini_sales_service.py` e
`api/routes/order_routes.py`.

> Código proposto aqui é rascunho: pressupõe revisão, testes e aprovação antes de produção.

---

## ⚠️ Bloqueador de negócio — ler antes de começar

Ao investigar como os sub-pedidos são pagos, encontrei o seguinte:

`initiate-checkout` ([order_routes.py:353-357](api/routes/order_routes.py:353)) tem esta nota:

```python
# ⚠️ NOTA: Stripe Checkout só suporta 1 destino em transfer_data.
# Em pedidos multi-restaurante, o dinheiro cai na conta da PLATAFORMA
# e deve ser distribuído via Transfer API no webhook após o sucesso.
is_multi_restaurant = len(order_data.sub_orders) > 1
```

O `is_multi_restaurant` é gravado no metadata do PaymentIntent
([order_routes.py:391](api/routes/order_routes.py:391)) — presumivelmente para o webhook
agir sobre ele. **Mas o webhook nunca lê esse campo, e não existe uma única chamada
`stripe.Transfer.create` em todo o projeto** (confirmado por busca em todos os `.py`).

Consequência prática: **hoje, num pedido com mais de um restaurante, o dinheiro entra na
conta da plataforma e nunca é repassado aos restaurantes automaticamente.** Com 1
restaurante funciona (`transfer_data` com destino direto, linha 370); com 2+ o repasse
simplesmente não acontece.

Isto não é causado pela feature pedida — já é o comportamento atual. Mas a feature
**transforma o multi-restaurante de exceção em caso de uso incentivado**, multiplicando o
problema. Duas opções antes de liberar:

| Opção | O que envolve |
|---|---|
| **A. Implementar o repasse** | `stripe.Transfer.create` por sub-pedido no webhook de `payment_intent.succeeded`, com idempotência (evitar transferir duas vezes se o webhook reenviar), cálculo de comissão por restaurante e tratamento de falha parcial. Estimativa: 3–5 dias, e precisa de validação financeira. |
| **B. Liberar assim mesmo, com repasse manual** | Aceitável só se o volume for baixo e alguém concilia manualmente. Precisa de acordo explícito com a área financeira e de um relatório de "sub-pedidos pendentes de repasse". |

**Recomendação:** decidir isto antes da Fase 1. Se for a opção A, ela vira pré-requisito
do plano. Não é decisão técnica isolada — envolve o financeiro e os contratos com os
restaurantes; recomendo confirmar com a área responsável.

---

## 1. Como funciona hoje

- O carrinho da sessão de chat (`UserSession.cart`) é uma lista de `CartItem`, e **cada
  item já carrega `restaurant_gid`** ([session_service.py:15](services/session_service.py:15)).
  Não há nenhuma contagem ou limite de restaurantes distintos.
- O pool de produtos oferecido à IA é montado por turno em `process_sales_chat` e
  `process_sales_chat_stream`. Hoje ele tem dois caminhos: restaurante fixo
  (`restaurant_id`) ou busca global — nenhum dos dois entende "conjunto de restaurantes".
- **O carrinho do chat e o payload do checkout são desacoplados:** `initiate-checkout`
  recebe `sub_orders` do app ([schemas/models.py:92](schemas/models.py:92)), não da sessão.
  O servidor não valida quantidade de restaurantes em lugar nenhum.

Esse desacoplamento é o ponto mais importante do desenho: **qualquer limite aplicado só na
IA é cosmético**, porque o app pode montar o payload de checkout como quiser.

---

## 2. Desenho: estado derivado, não armazenado

A lista de restaurantes do pedido **não deve virar um campo novo na sessão**. Deve ser
sempre derivada do carrinho:

```python
# rascunho — services/session_service.py, em UserSession
def restaurantes_no_carrinho(self) -> set:
    """GIDs distintos de restaurantes com item no carrinho."""
    return {item.restaurant_gid for item in self.cart if item.restaurant_gid}
```

Por quê: o requisito "destravar quando um restaurante for retirado" acontece **de graça**
com estado derivado. Se guardássemos uma lista separada, remover o último item de um
restaurante exigiria lembrar de atualizar a lista também — e todo caminho que esquecesse
disso deixaria um slot ocupado por um restaurante sem itens. Estado derivado não
dessincroniza.

---

## 3. Quatro camadas de defesa

| # | Camada | Onde | Papel |
|---|---|---|---|
| 1 | **Gate de checkout** | `initiate-checkout` | Rejeita >3 restaurantes com 400. **Única inviolável.** |
| 2 | **Executor / parser de tags** | `_executar_ferramenta`, laço de `ADD_TO_CART` | Recusa incluir o 4º restaurante no carrinho |
| 3 | **Pool de produtos** | montagem de `found_products` | Com 3 travados, a IA só *vê* produtos desses 3 |
| 4 | **Prompt** | system instruction + secção nova | A IA entende a regra e explica bem ao cliente |

A camada 1 é obrigatória e sozinha já garante a regra de negócio. As camadas 2–4 existem
para que o cliente nunca chegue ao checkout e leve um erro depois de montar o pedido
inteiro — são experiência, não correção.

---

## Fase 1 — Fundação e gate de checkout *(1 dia)*

### 1.1 Contagem derivada na sessão
Adicionar `restaurantes_no_carrinho()` a `UserSession` (código acima) e uma constante
única compartilhada:

```python
# rascunho — core/config.py (para app e server lerem o mesmo número)
MAX_RESTAURANTES_POR_PEDIDO: int = int(os.getenv("MAX_RESTAURANTES_POR_PEDIDO", 3))
```

### 1.2 Gate no checkout — o item que realmente protege

```python
# rascunho — order_routes.py, início de initiate_order_and_create_checkout_session
gids_distintos = {s.restaurant_gid for s in order_data.sub_orders if s.restaurant_gid}

if not order_data.sub_orders:
    raise HTTPException(status_code=400, detail="O pedido não contém nenhum item.")

if len(gids_distintos) > settings.MAX_RESTAURANTES_POR_PEDIDO:
    raise HTTPException(
        status_code=400,
        detail=(f"Um pedido pode incluir no máximo {settings.MAX_RESTAURANTES_POR_PEDIDO} "
                f"restaurantes diferentes (recebidos: {len(gids_distintos)})."),
    )
```

> **Nota:** a validação de `sub_orders` vazio resolve de quebra um problema separado que
> encontramos antes — `sub_orders: List[...] = []` passa pelo Pydantic sem erro e hoje
> chega ao Stripe com valor zero, produzindo um 400 genérico e confuso.

**Aceite:** requisição com 4 restaurantes recebe 400 com mensagem clara; com 3, passa.

### 1.3 Alinhar o app
O limite precisa aparecer na interface antes do checkout, senão o usuário só descobre no
fim. Combinar com o time do app: ler o limite do servidor (ou espelhar a constante) e
bloquear a adição do 4º restaurante na sacola. **Tarefa fora deste repositório**, mas
sem ela a experiência fica ruim mesmo com tudo o resto pronto.

---

## Fase 2 — Impedir o carrinho de chegar a 4 *(1 dia)*

### 2.1 Verificação no executor (caminho de function calling)

```python
# rascunho — HybridAIService._executar_ferramenta, antes de session.add_to_cart
restaurantes = session.restaurantes_no_carrinho()
gid_do_produto = produto["restaurant_gid"]
e_restaurante_novo = gid_do_produto and gid_do_produto not in restaurantes

if delta > 0 and e_restaurante_novo and len(restaurantes) >= MAX_RESTAURANTES_POR_PEDIDO:
    return {
        "ok": False,
        "erro": "LIMITE_DE_RESTAURANTES_ATINGIDO",
        "limite": MAX_RESTAURANTES_POR_PEDIDO,
        "restaurantes_atuais": nomes_dos_restaurantes,   # ver 2.3
        "dica": ("Explique o limite ao cliente e ofereça remover os itens de um dos "
                 "restaurantes atuais para abrir espaço."),
    }
```

Três detalhes que é fácil errar:
- **Só bloqueia `delta > 0`.** Remover (`delta < 0`) tem de funcionar sempre — é justamente
  o caminho de destravamento.
- **Só bloqueia restaurante *novo*.** Adicionar outro item de um dos 3 já escolhidos é
  sempre permitido.
- **O erro volta ao modelo** antes da resposta final (o loop de function calling já faz
  isso), então a IA explica o limite em vez de prometer o que não aconteceu.

### 2.2 Mesma verificação no caminho de tags
O function calling ainda está atrás da flag `IA_FUNCTION_CALLING` (desligada por padrão),
então o caminho de tags `[[ADD_TO_CART:...]]` é o que roda em produção hoje. Ele precisa
do mesmo bloqueio, registrando `LIMITE_DE_RESTAURANTES` em `tags_descartadas_motivo` na
telemetria já existente.

### 2.3 `restaurant_name` no CartItem
Para a IA dizer *"você já tem itens da Pizzaria X, do Sushi Y e do Café Z — quer tirar
algum?"*, o carrinho precisa do nome, não só do GID. Adicionar `restaurant_name` a
`CartItem`, com `to_dict`/`from_dict` tolerantes (`data.get("restaurant_name", "")`) para
sessões antigas que ainda estão no Redis não quebrarem.

---

## Fase 3 — A IA para de oferecer o que não pode vender *(1,5 dia)*

### 3.1 Filtrar o pool num único ponto

Hoje o pool é montado por dois caminhos diferentes (restaurante fixo / busca global), nos
dois métodos (`process_sales_chat` e `process_sales_chat_stream`) — quatro lugares. Em vez
de alterar os quatro, aplicar o filtro **depois** de `candidate_pool` estar montado e
**antes** do corte `[:20]`:

```python
# rascunho — após montar candidate_pool, nos dois métodos
restaurantes = session.restaurantes_no_carrinho()
if len(restaurantes) >= MAX_RESTAURANTES_POR_PEDIDO:
    ids_no_carrinho = {i.product_id for i in session.cart}
    candidate_pool = [
        p for p in candidate_pool
        if (getattr(p, "restaurant_gid", "") in restaurantes)
           or (p.id in ids_no_carrinho)   # ← ver nota
    ]
```

> **A condição `or p.id in ids_no_carrinho` não é redundante.** Se por qualquer
> inconsistência de dados um item do carrinho tiver `restaurant_gid` divergente, sem essa
> cláusula ele sumiria do pool — e a IA perderia a capacidade de removê-lo, deixando o
> cliente preso com um item que não consegue tirar pela conversa. Itens do carrinho são
> sempre visíveis.

### 3.2 Contexto no prompt

Nova secção em `_build_prompt`, sempre presente quando há carrinho:

```
🏪 RESTAURANTES NO PEDIDO (2/3): Pizzaria X | Sushi Y
```

E quando estiver no limite:

```
🏪 RESTAURANTES NO PEDIDO (3/3 — LIMITE ATINGIDO): Pizzaria X | Sushi Y | Café Z
   Ofereça APENAS produtos destes 3 restaurantes.
```

### 3.3 Regra na system instruction

```
8. Limite de Restaurantes: um pedido pode incluir no máximo 3 restaurantes diferentes.
   - A secção "RESTAURANTES NO PEDIDO" mostra quantos já estão no carrinho.
   - Ao atingir 3, ofereça apenas produtos desses 3 restaurantes.
   - Se o cliente pedir algo de um 4º restaurante, explique o limite com naturalidade e
     ofereça remover os itens de um dos restaurantes atuais para abrir espaço.
   - NUNCA prometa adicionar um produto de um 4º restaurante.
```

Aplicar nas **duas** system instructions (`_system_instruction` e `_system_instruction_fc`).

---

## Fase 4 — Destravamento e observabilidade *(0,5 dia)*

### 4.1 Destravamento
Não exige código novo: com o estado derivado da Fase 1, remover o último item de um
restaurante já libera o slot no turno seguinte. O que **falta** é a IA perceber e avisar —
como a secção do prompt é recalculada a cada turno, ela passa de `3/3 — LIMITE ATINGIDO`
para `2/3` sozinha. Vale um teste de conversa cobrindo esse ciclo completo.

### 4.2 Telemetria
Somar ao `registrar_turno` já existente:
- `restaurantes_no_carrinho` (int)
- `limite_restaurantes_atingido` (bool)
- `LIMITE_DE_RESTAURANTES` como motivo em `tags_descartadas_motivo`

Serve para responder depois: com que frequência os clientes esbarram no limite? 3 é o
número certo, ou está atrapalhando venda?

---

## Testes a escrever

| # | Teste | Fase |
|---|---|---|
| 1 | `restaurantes_no_carrinho()` conta distintos e ignora GID vazio | 1 |
| 2 | Checkout rejeita 4 restaurantes com 400 | 1 |
| 3 | Checkout aceita exatamente 3 | 1 |
| 4 | Checkout rejeita `sub_orders` vazio com mensagem clara | 1 |
| 5 | Executor recusa produto de 4º restaurante e **não** altera o carrinho | 2 |
| 6 | Executor **permite** mais itens de um dos 3 já escolhidos | 2 |
| 7 | Executor **permite** remoção (delta < 0) mesmo no limite | 2 |
| 8 | Pool filtra para os 3 restaurantes quando travado | 3 |
| 9 | Pool mantém item do carrinho visível mesmo com `restaurant_gid` divergente | 3 |
| 10 | Ciclo completo: 3 restaurantes → remove o último item de um → 4º passa a ser aceito | 4 |

Os testes 5–10 podem reaproveitar os dublês já existentes em
`tests/test_function_calling.py` e `tests/test_pipeline_integration.py`.

---

## Riscos e pontos de atenção

| Risco | Impacto | Mitigação |
|---|---|---|
| **Repasse multi-restaurante inexistente** | Restaurantes não recebem | Ver bloqueador no topo — decidir antes da Fase 1 |
| `restaurant_gid` vazio em `CartItem` | Item não conta para o limite; brecha silenciosa | Logar ocorrência; investigar origem (`CartItem.from_dict` aceita `""`) |
| Fallback `getattr(p,"restaurant_gid","") or restaurant_gid` no pool | Produto sem GID herda o restaurante *da sessão*, podendo ser mal atribuído e furar a contagem | Remover o fallback no caminho de contagem; usar só o GID real do produto |
| 3 restaurantes = 3 taxas de entrega | Pedido pode ficar caro e o cliente se surpreender no fim | Decisão de produto: mostrar as taxas acumuladas durante a conversa |
| Carrinhos legados no Redis com >3 restaurantes | Checkout passa a rejeitar pedido já montado | TTL de 30 min resolve sozinho; garantir que a mensagem de erro oriente o cliente a remover itens |
| `session.restaurant_gid` (singular) vs. multi-restaurante | Semântica conflitante: a sessão "pertence" a um restaurante mas o carrinho tem 3 | Tratar como "restaurante em navegação", não como escopo do pedido; documentar no código |

---

## Sequência sugerida

```
Decisão de negócio sobre repasse  ← bloqueante
        │
        ▼
Fase 1 (fundação + gate)  ──────► já garante a regra, mesmo sem as outras
        │
        ├──► Fase 2 (executor + tags)     ─┐
        │                                   ├─► experiência completa
        └──► Fase 3 (pool + prompt)       ─┘
                    │
                    ▼
             Fase 4 (telemetria)
```

**Esforço total: ~4 dias** de desenvolvimento no servidor, mais a tarefa do app (1.3) e,
se for a opção A, os 3–5 dias do repasse via Transfer API.

A Fase 1 sozinha já cumpre o requisito de negócio ("limitado a 3 restaurantes") e pode ir
para produção antes das demais — as Fases 2–4 são o que faz a IA se comportar bem em vez
de o cliente levar um erro no fim.
