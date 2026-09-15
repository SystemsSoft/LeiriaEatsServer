# Plano — Recolha Multi-Restaurante Sequenciada por Prontidão

**Objetivo:** num pedido com vários restaurantes, **um único estafeta** recolhe em todos eles,
na **ordem em que ficam prontos** (ponderada pela distância), e entrega ao cliente sem esperas
mortas e sem comida a arrefecer.

> Código proposto aqui é rascunho: pressupõe revisão, testes e aprovação antes de produção.
> As alterações de schema (secção 5) são migrações de banco — exigem backup e janela definida.

---

## 1. Descoberta que muda o tamanho do trabalho

O pedido era "melhorar a priorização". A investigação mostrou outra coisa:
**o despacho automático ao estafeta não funciona hoje — falha em 4 pontos independentes,
em série.** Qualquer um deles sozinho já basta para nenhum estafeta receber nada.

| # | Falha | Evidência |
|---|---|---|
| 1 | `PATCH /orders/{id}/base_time` grava num campo que não existe no modelo `OrderDB`. SQLAlchemy cria um atributo Python solto, o `commit()` não gera UPDATE, e a rota devolve **200** | [order_routes.py:1030](api/routes/order_routes.py:1030). Banco: **17/17** pedidos com `base_time = 0` |
| 2 | O worker exige `base_time > 0` para despachar | [courier_notification_service.py:118](services/courier_notification_service.py:118). Banco: **23/25** subpedidos com `base_time = 0` |
| 3 | O worker filtra `status == "Em preparo"`; o app do restaurante grava `"Em Preparo"` (P maiúsculo) | [courier_notification_service.py:18](services/courier_notification_service.py:18) vs `OrdersScreen.dart:1864`. Query `BINARY` no banco: **0 linhas** passam no filtro |
| 4 | O app do estafeta chama rotas que não existem na API | `GET /drivers/{id}/orders/pending` → **404**; `/accept` e `/reject` nesse formato → **405** (testado contra `api.leiriaeats.com`) |

Consequência: **a priorização não é uma melhoria de algo que funciona mal — é a primeira vez
que o caminho vai funcionar.** Isso é uma boa notícia para o custo: não há comportamento
legado em produção para preservar, porque não há comportamento nenhum.

### 1.1 E há um bloqueio estrutural por cima

Mesmo corrigidos os 4 pontos acima, o que foi pedido continua impossível:

```python
# services/courier_notification_service.py:46-51
busy_driver_gids = db.query(SubOrderDB.driver_gid).filter(
    SubOrderDB.driver_gid.isnot(None),
    SubOrderDB.status.in_(["Oferta enviada", "A aguardar estafeta", "A caminho"]),
)
candidates = db.query(DriverDB).filter(..., DriverDB.gid.notin_(busy_driver_gids))
```

O estafeta que já tem um subpedido ativo é **excluído dos candidatos**. O modelo é
`1 estafeta : 1 subpedido` por construção. Hoje, um pedido de 2 restaurantes gera
**2 ofertas independentes a 2 estafetas diferentes**, que fazem **2 viagens ao mesmo cliente**.

O app do estafeta reforça isso do outro lado: `Order` tem um único `restaurantName` e um único
`restaurantLocation`, `DeliveryStatus` é uma fila linear com um só `AT_RESTAURANT`, e o mapa
aceita exatamente um marcador de restaurante. E `HomeViewModel.kt:191-195` descarta ofertas
concorrentes enquanto uma estiver no ecrã — ou seja, mesmo que o servidor enviasse as duas,
a segunda seria silenciosamente perdida.

**Isto é a refatoração real.** O resto é consequência.

---

## 2. O que falta para conseguir ordenar por prontidão

Três dados que hoje não existem em lado nenhum:

**(a) Quando cada restaurante fica pronto.** Não existe evento de prontidão. No fluxo de
entrega o restaurante vai de `Em Preparo` direto para `Saiu para Entrega`
(`OrdersScreen.dart:1594-1605`) — nunca diz "acabei". A única noção de prontidão é derivada:

```python
# services/courier_notification_service.py:74-83
def _compute_ready_at(sub_order):
    return sub_order.master_order.created_at + timedelta(minutes=sub_order.base_time)
```

O relógio parte de `master_order.created_at` — o momento do **checkout**, não do aceite do
restaurante nem do início do preparo. Com captura manual, o intervalo de aceite entra no
cálculo como se fosse tempo de cozinha. E o resultado nunca é persistido: não há como
ordenar por prontidão em SQL.

**(b) Histórico temporal.** `SubOrderDB` não tem `created_at`, `ready_at`, `picked_up_at` nem
`delivered_at` — só `accepted_at`/`declined_at`, do aceite do restaurante. Sem timestamps
não há como medir estimado × real, e sem isso o `base_time` nunca melhora.

**(c) Distância entre restaurantes.** Existem três cópias de Haversine no backend
([order_routes.py:25](api/routes/order_routes.py:25), [drivers.py:41](api/routes/drivers.py:41),
[courier_notification_service.py:23](services/courier_notification_service.py:23)), todas
usadas só para `restaurante → cliente` ou `estafeta → restaurante`. Nenhuma calcula
`restaurante A → restaurante B`, que é exatamente a aresta que falta para sequenciar.

Nota sobre tempo de preparo por produto: `ProductDB.preparation_time_minutes` existe e está
preenchido, mas **não é recuperável a partir de um pedido** — `OrderItemDB` guarda
`product_name` e não guarda `product_gid` ([sql_models.py:192](core/sql_models.py:192)).
Derivar o tempo de preparo dos itens exige antes ligar o item ao produto (Fase 1).

---

## 3. Decisão de arquitetura

### Opção A — Manter 1:1 e só ordenar melhor a lista ❌

Corrigir os 4 bugs, ordenar `GET /drivers/orders` por prontidão em vez de `id DESC`.

**A favor:** barato, uma semana.
**Contra:** não resolve o pedido. Dois restaurantes continuam a ser dois estafetas e duas
viagens ao mesmo cliente. O cliente recebe a refeição em dois momentos, paga duas taxas
(ou a plataforma absorve), e a comida do primeiro restaurante espera a segunda entrega.

### Opção B — Rota agrupada: 1 estafeta recolhe o pedido inteiro ✅ recomendada

O pedido master passa a ser a unidade de despacho. O estafeta recebe **uma oferta** com
N paragens de recolha + 1 entrega, sequenciadas por prontidão e distância.

**A favor:** é o que foi pedido; uma viagem por pedido; taxa única; o cliente recebe tudo junto.
**Contra:** toca nos três apps e no schema. É a maior parte do esforço.

### Opção C — Rota agrupada + agrupamento entre pedidos diferentes (batching) ❌ agora

Juntar recolhas de pedidos distintos que partilhem restaurante ou rota.

**Contra:** com 4 estafetas registados e o volume atual, otimizar entre pedidos não tem
massa crítica; e multiplica o risco de atraso. **Deixar para depois** — a modelagem da
Opção B já abre caminho (a rota deixa de estar acoplada ao pedido).

**Recomendação: Opção B.** O resto do plano assume-a.

---

## 4. O algoritmo de sequenciamento

Com o limite existente de **3 restaurantes por pedido** (`MAX_RESTAURANTES_POR_PEDIDO`,
`PLANO_LIMITE_RESTAURANTES.md`), o espaço de soluções é 3! = **6 permutações**.
Não é um problema de TSP — é enumeração exaustiva, ótima, em microssegundos.
**Não introduzir solver, nem heurística, nem API de routing externa.**

### Função de custo

Para uma permutação π de paragens, partindo do estafeta em `t = agora`:

```
para cada paragem i em π:
    chegada_i  = t + viagem(anterior → i)
    recolha_i  = max(chegada_i, ready_at_i)     ← se chegar antes, espera
    t          = recolha_i

entrega = t + viagem(última → cliente)

transito_i = entrega - recolha_i                ← tempo na mochila
excesso_i  = max(0, transito_i - tolerancia_i)  ← quanto passou do que o prato aguenta

custo(π) = entrega  +  α · Σ transito_i  +  β · Σ excesso_i²
           └ não atrasar  └ não arrefecer        └ não estragar
```

O primeiro termo é "não atrasar" (minimiza a hora de entrega). O segundo é "não arrefecer"
(penaliza recolher muito cedo algo que só será entregue no fim). `α` arranca em `0.3` e é
ajustável sem deploy (constante no serviço, depois configuração).

O terceiro termo é a **tolerância de trânsito** — ver 4.4. É quadrático de propósito:
ultrapassar em 2 minutos é quase irrelevante, em 15 minutos torna a permutação inviável na
prática, sem nunca ser infinito (o que tornaria o problema insolúvel quando nenhuma ordem
respeita todas as tolerâncias). `β` arranca em `0.5`.

O `max(chegada, ready_at)` é o que torna isto sensível ao tempo de preparo: uma paragem que
fica pronta tarde é empurrada para o fim mesmo que seja a mais próxima, e uma que já está
pronta é recolhida primeiro mesmo estando mais longe — que é exatamente o comportamento pedido.

`viagem(a → b)` = Haversine × fator de estrada (`1.35`) ÷ velocidade média urbana (`22 km/h`),
com piso de 2 min. Sem chamada externa: mantém o cálculo determinístico e sem latência de rede
no caminho crítico do despacho. Trocar por uma Distance Matrix real é um ponto de extensão
isolado numa função — não uma reescrita.

### Recálculo

A sequência **não é decidida uma vez**. É recalculada quando:
- o restaurante confirma prontidão (sinal novo, Fase 2) — o caso principal;
- um `ready_at` estimado é ultrapassado sem confirmação (atraso detetado);
- o estafeta conclui uma recolha.

Só as paragens **ainda não recolhidas** entram no recálculo. Se a nova ordem diferir da atual,
o app é notificado e reordena — é isto que faz o sistema reagir "conforme vão ficando prontos"
em vez de seguir um plano feito no início.

### 4.1 Exemplo — pedido com 3 restaurantes

Pedido criado em `t=0`. Tempos de viagem em minutos, prontidão estimada a partir do
`base_time` de cada restaurante:

| Restaurante | Viagem do estafeta | Pronto em | Viagem até ao cliente |
|---|---|---|---|
| **A** — pizzaria | 5 | **25 min** | 7 |
| **B** — sushi | 8 | **12 min** | 8 |
| **C** — gelataria | 12 | **8 min** | 10 |

Entre paragens: A↔B = 6, B↔C = 5, A↔C = 9.

**Ordem ingénua, por proximidade (A → B → C):**

| Passo | Chega | Pronto | Recolhe | Nota |
|---|---|---|---|---|
| A | 5 | 25 | **25** | 20 min parado à porta |
| B | 31 | 12 | 31 | arrefeceu 19 min à espera |
| C | 36 | 8 | 36 | arrefeceu 28 min |
| Cliente | **46** | | | pizza há 21 min na mochila |

**Ordem que o algoritmo escolhe (C → B → A):**

| Passo | Chega | Pronto | Recolhe | Nota |
|---|---|---|---|---|
| C | 12 | 8 | 12 | já estava pronto |
| B | 17 | 12 | 17 | já estava pronto |
| A | 23 | 25 | **25** | 2 min de espera |
| Cliente | **32** | | | pizza há 7 min na mochila |

**14 minutos mais cedo, com o prato quente recolhido por último.** É a inversão que importa:
o restaurante mais próximo é visitado em último porque é o mais lento a ficar pronto.

O termo `α` da função de custo é o que garante o desempate na direção certa quando duas
permutações entregam à mesma hora — escolhe a que deixa a comida menos tempo em trânsito.

> **A regra, em uma frase:** recolhe na ordem em que fica pronto — **quem demora mais na
> cozinha fica para o fim**. A distância não manda; só desempata.

Isto costuma ser mal lido nos dois sentidos, por isso vale ser explícito:

| Leitura errada | Porquê está errada |
|---|---|
| "vai sempre ao mais perto" | No exemplo acima, o mais perto (pizzaria, 5 min) é o **último**. Ir lá primeiro custa 20 min parado à porta e 14 min de atraso na entrega |
| "vai sempre ao mais longe" | Foi coincidência do exemplo: a gelataria era ao mesmo tempo a mais longe **e** a mais rápida a ficar pronta |
| "vai ao que demora mais" | É o **inverso**. Quem demora mais vai por último — senão espera-se à porta *e* a comida dos outros arrefece |

Prova de que a distância não é o motivo: invertendo as distâncias do exemplo (A passa a 12 min,
C a 5 min) e mantendo os tempos de preparo, a ordem vencedora **continua a ser C → B → A**,
com os mesmos 32 min contra 46 min.

Quando a distância *passa* a mandar: quando tudo já está pronto (o `max` deixa de ter efeito e
o custo vira tempo de viagem puro → caminho mais curto), ou quando a viagem é longa ao ponto de
não compensar ir buscar algo que já está pronto mas muito longe.

### 4.2 Quando algo atrasa

`t=14`: B avisa que só fica pronto aos 30 (em vez de 12). O estafeta já recolheu em C e
está a caminho de B.

O recálculo corre só sobre o que falta (**B** e **A**, ambos ainda não recolhidos):

- Manter `B → A`: chega a B aos 17, espera até 30, chega a A aos 36, entrega aos 43.
- Trocar para `A → B`: chega a A aos 21, espera até 25, chega a B aos 31, entrega aos 39.

Nova sequência: **A → B**. O app do estafeta é notificado e reordena a rota enquanto ele
ainda está em movimento. Sem este recálculo, ficariam 13 minutos parados à porta de B.

### 4.3 Múltiplos pedidos em simultâneo

São dois níveis diferentes, e o plano trata-os de forma diferente:

**Dentro de um pedido (multi-restaurante)** — é a secção 4: um estafeta, uma rota,
N paragens sequenciadas. É o que o plano resolve.

**Entre pedidos distintos** — um estafeta a transportar pedidos de clientes diferentes ao
mesmo tempo. **Fica de fora por ora** (Opção C, secção 3), mas a modelagem já não o impede:
`DeliveryRouteDB` não é filha do pedido, é uma entidade própria com N paragens. Juntar dois
pedidos numa rota é acrescentar paragens e reordenar — o `RouteSequencer` e o app já saberão
lidar, porque tratam a rota como lista genérica de paragens, não como "o pedido X".

**O que muda hoje na prática:** enquanto tem uma rota ativa, o estafeta deixa de receber
ofertas novas. A regra `notin_(busy_driver_gids)` ([courier_notification_service.py:50](services/courier_notification_service.py:50))
passa a ser avaliada **por rota**, não por subpedido — que é precisamente o que hoje impede
o mesmo estafeta de pegar dois restaurantes do mesmo pedido.

E há um limite deliberado: **começar com 2 paragens por rota**, subir para 3 só com dados de
atraso real da Fase 1. Três restaurantes numa rota significa que um atraso na cozinha de um
deles segura os outros dois — o risco cresce mais depressa que o ganho de eficiência.

### 4.4 Tolerância de trânsito — o gelado que derrete

Há um segundo eixo temporal que não é o tempo de preparo: **quanto tempo o prato aguenta
depois de sair do restaurante**. Um gelado derrete, um chocolate quente arrefece, uma sopa
perde o ponto; uma garrafa de vinho é indiferente.

Isto importa porque a rota agrupada **aumenta estruturalmente** o tempo em trânsito face a uma
entrega direta. No exemplo de 4.1, a gelataria é recolhida aos 12 min e entregue aos 32 —
**20 minutos na mochila**, contra os ~10 que teria numa entrega só dela. O algoritmo, como
descrito até aqui, faria exatamente a pior escolha possível para o gelado: como ele fica
pronto primeiro, seria recolhido primeiro.

Um `α` global não resolve, porque trata todos os produtos como igualmente frágeis.

#### A ideia: cada paragem tem uma janela de recolha

Passam a existir dois limites por paragem, e não um:

| Limite | Significado | De onde vem |
|---|---|---|
| `ready_at` | **a partir de quando posso** recolher | tempo de preparo (Fase 2) |
| `entrega − tolerância` | **até quando devo** recolher | tolerância do item mais frágil |

A recolha ideal cai dentro de `[ready_at, entrega − tolerância]`. O `ready_at` empurra para
mais tarde; a tolerância também. Não há conflito entre os dois — pelo contrário, alinham-se.

E há uma consequência prática que sai de graça: **o melhor sítio para um gelado que fica pronto
aos 8 min, num pedido que só será entregue aos 32, é a arca do restaurante.** Recolher por
último não é adiar trabalho — é conservação. O `max(chegada, ready_at)` já permite recolher a
qualquer momento depois de pronto, portanto nada no modelo precisa de mudar para isto funcionar;
basta o custo saber que recolher cedo um perecível é caro.

#### De onde vem o número

**Decisão de produto: é campo obrigatório no cadastro e na edição de produto, preenchido pelo
restaurante** (KomaRestaurant). Quem conhece o prato é quem o faz — inferir por categoria seria
sempre uma aproximação, e as categorias no banco nem sequer são normalizadas (ver abaixo).

```python
# ProductDB — ver 5.5 para o motivo de continuar nullable no banco
transit_tolerance_minutes = Column(Integer, nullable=True)
```

A tolerância de uma **paragem** é o mínimo das tolerâncias dos seus itens — o prato mais frágil
manda no subpedido inteiro.

O default por categoria **não desaparece**, mas muda de papel: deixa de ser a fonte do dado e
passa a ser (a) o valor de arranque do backfill dos produtos já existentes e (b) rede de
segurança para quando o campo vier vazio na mesma — produto antigo ainda não editado, ou
pedido cujo item não consegue resolver o produto.

```python
# Fallback, não fonte primária. Matching normalizado (minúsculas, sem acentos).
TOLERANCIA_FALLBACK_CATEGORIA = {
    "gelataria": 12, "sobremesa": 25, "cafe": 15, "bebidas": 20,
    "sushi": 30, "japonesa": 30, "pizza": 35, "pizzaria": 35,
    "hamburguer": 30, "hamburgueria": 30, "marisqueira": 25, ...
}
TOLERANCIA_FALLBACK = 40
```

Nota de dependência: derivar isto dos itens de um pedido exige o `product_gid` em `OrderItemDB`
(secção 5.4), que hoje não existe. Até lá, a tolerância resolve-se pela categoria do
**restaurante**, que já está desnormalizada em `SubOrderDB.restaurant_category` — menos preciso,
mas suficiente para arrancar e sem migração adicional.

#### O backfill dos produtos existentes

São **523 produtos** em produção, e o campo obrigatório no formulário **não os preenche** —
só passa a valer para cadastros e edições futuras. Sem backfill, todos entram no fallback.

E as categorias existentes não ajudam: há **23 grafias distintas**, inconsistentes entre si —
`japonesa` (53 produtos) e `Sushi` (1), `Pizzaria` (50) e `Pizza` (2), `Hambúrgueria` (50) e
`Hambúrguer` (2). Qualquer mapa por categoria precisa de normalizar antes de comparar, e ainda
assim erra nos casos genéricos (`Outros`, `Combo`, `Combos`).

Sequência segura da migração — a ordem importa:

1. Adicionar a coluna **nullable**. Torná-la `NOT NULL` de imediato faz a migração falhar, porque
   as 523 linhas existentes não têm valor.
2. **Backfill** por categoria normalizada, com o fallback de 40 min para o que não mapear.
3. Tornar o campo **obrigatório na API de escrita** (`ProductCreateRequest`) e **no formulário**.
4. Só depois de confirmado que não há nulos é que se pode avaliar `NOT NULL` no banco — e é
   opcional: o fallback do servidor já cobre, e a constraint rígida só cria risco de escrita.

O backfill é uma estimativa, não a verdade. Vale sinalizar no app do restaurante quais produtos
ainda estão com o valor herdado do backfill, para o restaurante corrigir os que importam — os
perecíveis são uma minoria do catálogo, e são exatamente os que o default erra.

#### Especificação do campo no KomaRestaurant

Em `lib/screen/ProductRegistrationScreen.dart`, ao lado dos campos de tempo já existentes
(`_prepTimeController`, linha 440, e `_preparationTimeMinutesController`, linha 548):

```dart
_buildLabel("Tolerância em viagem (min)", Icons.ac_unit_outlined),
_buildTextField(
  controller: _transitToleranceController,
  hint: "ex: 15",
  keyboardType: TextInputType.number,
  validator: (v) {                       // obrigatório — mesmo padrão do campo "Preparo"
    if (v == null || v.isEmpty) return 'Informe a tolerância';
    final n = int.tryParse(v);
    if (n == null || n < 5 || n > 120) return 'Entre 5 e 120 minutos';
    return null;
  },
)
```

O rótulo importa: "tolerância de trânsito" é vocabulário nosso, não do restaurante. A pergunta
que ele sabe responder é **"quanto tempo este prato aguenta na mochila até perder qualidade?"** —
convém estar como texto de ajuda por baixo do campo, com exemplos (gelado ~12, pizza ~35).

Sugestão de UX que reduz o atrito e melhora o dado: chips de preset (`10 · 15 · 20 · 30 · 45`),
como o diálogo de aceite de pedido já faz com o tempo de preparo (`OrdersScreen.dart:1690-1726`),
com o valor sugerido pela categoria pré-selecionado — o restaurante confirma ou corrige, em vez
de escrever do zero em cada produto.

#### Quando nenhuma ordem serve

Se a melhor permutação ainda deixa um item muito acima da tolerância, a conclusão não é
"entregar mal" — é **não agrupar**. A tolerância passa a ser o critério objetivo do *split* que
a secção 9 já previa de forma vaga:

> Se `min(excesso)` sobre todas as permutações > limite (arrancar em **10 min**), a paragem
> perecível sai da rota e é despachada em separado.

Isto é o que impede o caso patológico: um pedido com gelado + um restaurante lento nunca deve
virar uma rota única, por mais eficiente que pareça no papel.

#### Efeito no exemplo de 4.1

Aplicando `tolerância` = 12 (gelataria), 30 (sushi), 35 (pizzaria) às 6 permutações,
com `α = 0.3` e `β = 0.5`:

| Ordem | Entrega | Σ trânsito | Σ excesso | **Custo** |
|---|---|---|---|---|
| **B → C → A** | 33 min | 44 | **4** | **54.2** ✅ |
| A → B → C | 46 min | 46 | 0 | 59.8 |
| A → C → B | 47 min | 43 | 1 | 60.4 |
| B → A → C | 44 min | 61 | 2 | 64.3 |
| C → B → A | **32 min** | 42 | 8 | 76.6 |
| C → A → B | 39 min | 49 | 15 | 166.2 |

A vencedora deixa de ser `C → B → A` e passa a ser **`B → C → A`**. E o mais interessante é o
preço disso: **1 minuto**. A entrega passa de 32 para 33 min, e em troca o gelado sai de
20 min de trânsito para 16.

Repare que as ordens que respeitam *totalmente* a tolerância do gelado (`A → B → C` e
`B → A → C`, ambas com o gelado recolhido por último e só 10 min em trânsito) custam 44–46 min
de entrega. O algoritmo julgou que 11 a 13 minutos de atraso para todo o pedido não compensam
os 4 minutos de excesso — e essa é precisamente a decisão de negócio que o `β` controla. Se a
política for "perecível nunca excede", sobe-se o `β`; se for "entregar depressa acima de tudo",
desce-se. O valor certo sai dos dados da Fase 1, não de um palpite agora.

Neste caso o excesso mínimo possível é 4 min — abaixo do limite de 10 min, portanto **não há
split**: a rota agrupada continua a ser a decisão certa. É assim que os dois mecanismos se
articulam.

E nada disto exigiu uma regra especial para gelados. Só um número por categoria.

---

## 5. Modelo de dados

### 5.1 Nova entidade: `DeliveryRouteDB`

A unidade que o estafeta aceita passa a ser a rota, não o subpedido. Isto desacopla o
despacho do pedido e deixa a Opção C (batching) possível no futuro sem nova migração.

```python
class DeliveryRouteDB(Base):
    __tablename__ = "delivery_routes"
    id = Column(Integer, primary_key=True, index=True)
    gid = Column(String(255), unique=True, nullable=False)
    master_order_gid = Column(String(255), ForeignKey("orders.gid"), nullable=False, index=True)
    driver_gid = Column(String(255), ForeignKey("drivers.gid"), nullable=True, index=True)

    # OFFERED | ACCEPTED | IN_PROGRESS | COMPLETED | EXPIRED | CANCELLED
    status = Column(String(30), nullable=False, default="OFFERED", index=True)

    offered_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_at  = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    estimated_delivery_at = Column(DateTime(timezone=True), nullable=True)
    sequence_version = Column(Integer, nullable=False, default=1)  # incrementa a cada recálculo
```

### 5.2 Nova entidade: `RouteStopDB`

Uma paragem por subpedido. É aqui que vive a ordem de recolha — o campo que hoje não existe.

```python
class RouteStopDB(Base):
    __tablename__ = "route_stops"
    id = Column(Integer, primary_key=True, index=True)
    route_gid = Column(String(255), ForeignKey("delivery_routes.gid"), nullable=False, index=True)
    sub_order_gid = Column(String(255), ForeignKey("sub_orders.gid"), nullable=False, index=True)

    sequence = Column(Integer, nullable=False)           # 1, 2, 3 — ordem de recolha
    ready_at_estimated = Column(DateTime(timezone=True), nullable=True)
    ready_at_confirmed = Column(DateTime(timezone=True), nullable=True)  # sinal real do restaurante
    arrived_at   = Column(DateTime(timezone=True), nullable=True)
    picked_up_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("route_gid", "sub_order_gid", name="uq_route_sub_order"),)
```

### 5.3 Alterações em `SubOrderDB`

```python
prep_started_at = Column(DateTime(timezone=True), nullable=True)  # entrou em preparo
ready_at        = Column(DateTime(timezone=True), nullable=True)  # restaurante confirmou pronto
```

Os campos `driver_gid`/`driver_name` no subpedido ficam como **leitura legada** durante a
transição (o app do restaurante mostra-os) e são preenchidos por espelho a partir da rota.
Removê-los é trabalho de limpeza posterior, não deste plano.

### 5.4 Alteração em `OrderItemDB`

```python
product_gid = Column(String(255), ForeignKey("products.gid"), nullable=True, index=True)
```

Sem isto não há como derivar tempo de preparo **nem tolerância de trânsito** a partir dos itens
(secções 2c e 4.4). Nullable, para não invalidar os pedidos existentes.

### 5.5 Alteração em `ProductDB`

```python
# Minutos que o prato aguenta em trânsito mantendo qualidade (ver 4.4).
# Obrigatório no formulário e na API de escrita; nullable no banco por causa do histórico.
transit_tolerance_minutes = Column(Integer, nullable=True)
```

**Obrigatório na entrada, nullable no banco** — não é contradição, é a única sequência que
funciona: `nullable=False` faria a migração falhar nas 523 linhas existentes, e mesmo depois do
backfill a constraint rígida só acrescenta risco de escrita sem benefício (o fallback do
servidor já cobre o caso de vir vazio). A obrigatoriedade vive onde é útil — no formulário
(`ProductRegistrationScreen.dart`) e no `ProductCreateRequest` do backend.

Ver 4.4 para a sequência de migração e o backfill.

### 5.6 Índices

O `sub_orders` só tem PK e a FK de `driver_gid`. As queries de despacho filtram por
`status` e juntam por `master_order_gid`, ambos sem índice:

```sql
CREATE INDEX idx_sub_orders_status ON sub_orders(status);
CREATE INDEX idx_sub_orders_master ON sub_orders(master_order_gid);
```

---

## 6. Máquina de estados

Hoje os status são strings livres, sem Enum, escritas pelo app e aceites pelo backend sem
validação ([order_routes.py:1354](api/routes/order_routes.py:1354) grava o que vier). É isso
que permitiu o bug de `"Em preparo"` vs `"Em Preparo"` passar despercebido.

A validação que existe é **ad-hoc, caso a caso, nascida de bugs de produção**: há uma guarda
anti-retrocesso escrita só para `"Pendente"` ([order_routes.py:1348](api/routes/order_routes.py:1348),
bug de 2026-08-28) e um "aceite implícito" que trata *qualquer* status de progresso como aceite
do restaurante no fluxo de captura manual ([order_routes.py:1328-1339](api/routes/order_routes.py:1328)).
São exatamente os remendos que um Enum com transições declaradas torna desnecessários.

**Cuidado na implementação:** esse aceite implícito depende do vocabulário atual —
`status_data.status not in ("Pendente", "Cancelado")`. Introduzir `PRONTO`/`RECOLHIDO` sem
rever essa condição mantém o comportamento correto (são status de progresso), mas qualquer
renomeação de `"Pendente"`/`"Cancelado"` parte o aceite e, com ele, a captura do pagamento.
Esta é a dependência mais perigosa do plano — mexe em dinheiro.

**Introduzir um Enum único e partilhado**, com validação na escrita:

```
PENDING_PAYMENT → PENDENTE → ACEITO → EM_PREPARO → PRONTO → RECOLHIDO → A_CAMINHO → ENTREGUE
                                   ↘ RECUSADO        ↘ CANCELADO
```

`PRONTO` e `RECOLHIDO` são os estados novos. `PRONTO` é o sinal que falta hoje —
e é o gatilho do recálculo de sequência.

**Compatibilidade:** a rota de escrita passa a normalizar o valor recebido (case-insensitive,
sem acentos) e a rejeitar o que não mapear, com 422. Uma tabela de tradução mantém os
valores antigos a funcionar durante a transição, para não partir os apps já instalados.

---

## 7. Fases

Cada fase é entregável e testável isoladamente. As Fases 0 e 1 têm valor mesmo que o
resto pare.

### Fase 0 — Desbloquear o que já existe (sem schema)
1. Corrigir `PATCH /orders/{id}/base_time` para escrever em `SubOrderDB` pelo `gid` do
   subpedido — hoje escreve num campo inexistente de `OrderDB` e devolve 200.
2. Normalizar status na escrita (resolve `"Em preparo"` vs `"Em Preparo"`).
3. Alinhar o contrato do app do estafeta com a API real (404/405 da secção 1) — decidir
   num sentido só: **adaptar o app à API existente**, que é o lado com dados em produção.
4. Corrigir `POST /orders/{id}/reset-delivery` ([order_routes.py:1040](api/routes/order_routes.py:1040)),
   que acede a `order.driver_name`/`driver_id` inexistentes em `OrderDB` → `AttributeError` → 500.

**Resultado:** o despacho 1:1 que existe hoje passa efetivamente a funcionar. É a base de
comparação para medir o ganho das fases seguintes.

### Fase 1 — Dados e observabilidade
1. Migrações da secção 5 (`delivery_routes`, `route_stops`, timestamps, `product_gid`,
   `transit_tolerance_minutes`, índices).
1b. **Tolerância de trânsito** (4.4), na ordem: coluna nullable → backfill dos 523 produtos por
   categoria normalizada → campo obrigatório no `ProductCreateRequest` e no formulário do
   KomaRestaurant. Entregável isolado: não depende de nada do resto da fase.
2. Preencher `prep_started_at` e `ready_at` nas transições.
3. `base_time` sugerido automaticamente a partir de `max(preparation_time_minutes)` dos itens,
   com o restaurante a poder ajustar — hoje o default é um `20` fixo no app
   (`OrdersScreen.dart:1627`) e só é enviado se o restaurante **alterar** o valor.
4. Telemetria: estimado × real por restaurante (o projeto já tem o padrão em `services/telemetry`).

### Fase 2 — Sinal de prontidão
0. **Primeiro, o app do cliente** (secção 8): ensinar a ler o vocabulário novo — cor, ícone e,
   sobretudo, a lista `successStatuses` da confirmação de pagamento. Publicar e esperar
   adoção **antes** de o servidor começar a escrever os status novos.
1. Botão **"Pedido pronto"** no app do restaurante, no ramo de entrega — hoje inexistente.
2. Endpoint que grava `ready_at` e dispara o recálculo.
3. Sem este sinal, o sistema funciona na estimativa; com ele, reage ao real.

### Fase 3 — Rota agrupada (o núcleo)
1. `RouteSequencer`: a função de custo da secção 4, pura e testável sem banco.
2. Substituir `_assign_nearest_driver` por despacho por **rota**: remover o
   `notin_(busy_driver_gids)` e passar a ofertar o pedido master inteiro a um estafeta.
3. Momento da oferta: `min(ready_at) − viagem(estafeta → 1ª paragem) − buffer`, em vez do
   `NOTIFY_BEFORE_MINUTES = 15` fixo atual.
4. Mover o estado do despacho (`_notified_sub_order_ids`, `_pending_acceptance`, hoje
   dicionários em memória) para o banco — perde-se a cada restart e não sobrevive a
   mais de um processo.
5. Endpoints novos: aceitar rota, marcar chegada/recolha por paragem, concluir entrega.

### Fase 4 — App do estafeta
1. `Order` plano → rota com `List<Stop>`; `DeliveryStatus` linear → estado por paragem +
   estado agregado.
2. `DeliveryScreen.kt:85-90`: o `isGoingToRestaurant` binário é o bloqueio central da UI.
3. `PlatformMapView`: aceitar N marcadores.
4. `HomeViewModel.kt:191-195`: deixar de descartar ofertas concorrentes.
5. Estado partilhado: hoje cada ViewModel cria a sua própria instância de repositório,
   logo `currentOrder` nunca é partilhado entre ecrãs.

### Fase 5 — Tempo real
Ligar o FCM que **já está implementado e desligado** nos dois lados (`data/notifications/*`
no app, sem qualquer chamada; e no backend a "notificação" é um `logger.info`,
[courier_notification_service.py:70](services/courier_notification_service.py:70)).
Elimina a janela de 10 s do polling — que hoje é crítica, porque a oferta expira em 60 s.

---

## 8. Impacto por componente

Quatro componentes, não três — o **app do cliente** também é afetado, por dependências de
status que não são cosméticas.

| Componente | Peso | O que muda |
|---|---|---|
| **KomaServer** | 🔴 Grande | Onde está quase todo o trabalho: schema novo, `RouteSequencer`, reescrita do despacho, Enum de status, endpoints por paragem |
| **KomaPartner** (estafeta) | 🔴 Grande | Modelo plano → rota com N paragens; máquina de estados linear → por paragem; mapa 1 marcador → N; e alinhar o contrato hoje em 404/405 |
| **KomaRestaurant** | 🟢 Pequeno | Botão "Pedido pronto" no ramo de entrega + campo obrigatório de tolerância de trânsito no cadastro/edição de produto (4.4). E corrigir o envio de `base_time`, que hoje só vai se o restaurante alterar o valor |
| **Koma** (cliente) | 🟡 Médio | Ver abaixo |

### Porque o app do cliente entra

Não é só exibir status novo — há **lógica** dependente de string literal:

- [SearchViewModel.kt:1315](../../Koma/Leiria_Eats/composeApp/src/commonMain/kotlin/org/leria/eats/project/presentation/viewmodel/SearchViewModel.kt:1315) —
  a confirmação de pagamento faz polling até o status cair em
  `listOf("Em Preparo", "Confirmado", "Pago", ..., "Entregue")`. Vocabulário novo que não
  esteja nessa lista = o cliente fica a rodar até o timeout depois de pagar.
- [OrdersScreen.kt:546](../../Koma/Leiria_Eats/composeApp/src/commonMain/kotlin/org/leria/eats/project/presentation/OrdersScreen.kt:546) —
  a avaliação do produto só aparece com `subOrder.status == "Entregue"`.
- [OrdersScreen.kt:744-760](../../Koma/Leiria_Eats/composeApp/src/commonMain/kotlin/org/leria/eats/project/presentation/OrdersScreen.kt:744) —
  cor e ícone por string, com `else` genérico.

Hoje o cliente **já** cai no `else` genérico (cinza + ícone de info) para todos os status do
estafeta: `"Oferta enviada"`, `"A aguardar estafeta"`, `"A caminho"`. Ou seja, o vocabulário
do estafeta nunca foi desenhado para ser visto pelo cliente — e é visto.

Com a rota agrupada isto fica pior, não melhor: o cliente passa a ter um pedido cujos
restaurantes estão em estados diferentes ao mesmo tempo (um `PRONTO`, outro `EM_PREPARO`).
Vale aproveitar para mostrar o progresso real da recolha — "1 de 2 recolhidos" — em vez de
um status agregado que não descreve nada.

**Ordem segura:** o cliente tem de saber ler o vocabulário novo **antes** de o backend passar
a escrevê-lo. Na prática: publicar a versão do cliente que trata os status novos, esperar a
adoção, e só então ligar a escrita no servidor. A tabela de tradução da secção 6 existe
exatamente para cobrir a janela em que há apps antigos em campo.

---

## 9. Riscos

| Risco | Mitigação |
|---|---|
| **Um estafeta com 3 paragens atrasa mais que 3 estafetas com 1** — se um restaurante atrasar, arrasta o pedido todo | Limite de paragens por rota (começar em 2, subir para 3 com dados); regra de *split*: se `ready_at` de uma paragem exceder um limite, retira-se a paragem da rota e despacha-se em separado |
| Comida a arrefecer na primeira recolha | É o termo `α` da função de custo (secção 4); medir com os timestamps da Fase 1 antes de calibrar |
| Estimativa de viagem em linha reta subestima o tempo real | Fator de estrada `1.35` e piso de 2 min; a função é ponto de extensão isolado para uma Distance Matrix real |
| Apps instalados a falar o contrato antigo | Tabela de tradução de status (secção 6) e manter as rotas antigas a responder durante a transição |
| Migração de schema em produção | Campos nullable, sem backfill destrutivo; as tabelas novas não têm leitores até a Fase 3 |
| Poucos estafetas (4 registados) para validar | As Fases 0–2 são testáveis sem volume; a Fase 3 precisa de ensaio controlado antes de ligar a todos |

---

## 10. O que NÃO fazer

- **Solver de rotas / TSP / VRP.** Com ≤3 paragens, 6 permutações resolvem otimamente.
- **API de routing externa no caminho crítico.** Latência e custo por despacho, para um ganho
  marginal face ao Haversine com fator de estrada. Deixar como extensão.
- **Batching entre pedidos (Opção C).** Sem massa crítica; a modelagem já deixa a porta aberta.
- **Reescrever o app do restaurante.** Precisa de um botão e de um endpoint, não de refatoração.
