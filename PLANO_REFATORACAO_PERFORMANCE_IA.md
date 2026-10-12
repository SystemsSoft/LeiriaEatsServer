# Planejamento — Performance da IA (Generativa + Semântica) no fluxo de pedido

**Objetivo:** reduzir o tempo de resposta da IA durante os pedidos, sem alterar nenhuma
lógica de negócio ou regra existente (limite de restaurantes, exclusividade da Caixa
Surpresa, aptidão de pagamento, contrato de tags/function calling, gates de checkout).

**Base:** telemetria real de produção coletada em 10/09/2026 via SSH na instância EC2
(`journalctl -u leiria-eats.service`), **366 turnos de chat** dos últimos 14 dias, mais
leitura de `services/hybrid_ai_service.py`, `services/gemini_sales_service.py`,
`services/ai_service.py`, `api/routes/product_routes.py` e `api/routes/company_routes.py`.

> Código proposto aqui é rascunho: pressupõe revisão, testes e aprovação antes de produção.
> Nenhuma alteração foi aplicada — este documento é só o plano.

---

## 1. Diagnóstico — o que os números dizem

### 1.1 Latência medida (366 turnos, 14 dias)

| Métrica | p50 | p90 | p95 | p99 | máx |
|---|---|---|---|---|---|
| `ms_e5` (busca semântica) | 426 ms | 673 ms | 884 ms | 4.488 ms | 6.907 ms |
| `ms_pool` (montagem do pool) | 0,3 ms | 0,4 ms | 0,6 ms | 1,6 ms | 1,9 ms |
| **`ms_ttft` (Gemini, 1º token)** | **519 ms** | **4.214 ms** | **11.224 ms** | **55.765 ms** | **110.485 ms** |
| `ms_total` (turno completo) | 1.173 ms | 6.384 ms | 11.669 ms | 55.908 ms | 110.821 ms |

### 1.2 Onde o tempo é gasto (soma acumulada dos 366 turnos)

| Etapa | Tempo acumulado | % da espera total |
|---|---|---|
| **Gemini (`ms_ttft`)** | **1.051 s** | **78 %** |
| E5 (`ms_e5`) | 195 s | 15 % |
| Montagem do pool | ~0,1 s | ~0 % |
| **Total** | 1.341 s | 100 % |

### 1.3 O problema é a cauda, não a mediana

- **A mediana está boa:** 1,17 s por turno. Em condição normal o sistema já é rápido.
- **11,5 %** dos turnos passaram de 5 s; **7,1 %** passaram de 10 s.
- **Os 26 turnos que passaram de 10 s consumiram 62 % de toda a espera acumulada.**

Ou seja: otimizar o caso médio rende pouco. **O ganho está em cortar a cauda.**

### 1.4 A cauda tem uma correlação clara: tamanho do prompt

`ms_ttft` por tamanho do pool de produtos enviado ao Gemini:

| Pool | n | p50 | p90 | p95 | máx |
|---|---|---|---|---|---|
| ≤ 2 produtos | 49 | 559 ms | 733 ms | 2.081 ms | 8.610 ms |
| ≥ 10 produtos | 313 | 512 ms | **5.427 ms** | **14.408 ms** | **110.485 ms** |

`ms_ttft` por tokens de prompt:

| Prompt | n | p50 | p90 | p95 | máx |
|---|---|---|---|---|---|
| < 500 tokens | 28 | 523 ms | 803 ms | 2.155 ms | 8.610 ms |
| 500–999 tokens | 30 | 563 ms | 683 ms | 738 ms | 5.114 ms |
| ≥ 1.000 tokens | 304 | 515 ms | **5.882 ms** | **15.468 ms** | **110.485 ms** |

**Todos os 26 turnos acima de 10 s tinham pool de 16–17 produtos.** Nenhum turno com
pool ≤ 2 passou de 8,6 s. A mediana quase não muda entre as faixas — o que muda é a
**probabilidade de cair na cauda**.

### 1.5 Instabilidade do provedor (causa direta da cauda)

Em 14 dias:

| Evento | Ocorrências |
|---|---|
| `503 UNAVAILABLE` ("high demand") por chave | 38 |
| Rodadas de retry temporal (sleep 1,5 s → 3 s) | 29 |
| **Desistências finais → resposta de fallback genérica** | **18** |
| `429 RESOURCE_EXHAUSTED` (cota) | 2 |

O código já documenta a raiz disso em `services/gemini_sales_service.py:90-100`: a conta
usa o **tier gratuito** do `gemini-flash-lite-latest`, cuja capacidade é compartilhada
entre todos os usuários gratuitos. Como já verificado numa investigação anterior, o `503`
**não** é limite por projeto — trocar de chave não ajuda, e é por isso que as 3–4 chaves
falham juntas.

Custo do retry atual (`gemini_sales_service.py:327-397`): até 3 rodadas × 4 chaves, com
`sleep(0,5)` entre chaves e `sleep(1,5 s)`/`sleep(3 s)` entre rodadas — mais o tempo de
resposta de cada 503. É isso que produz os turnos de 42 s, 55 s e 110 s.

### 1.6 Reindexação síncrona do E5 no caminho do request

`AIService.reload_data(db)` (`services/ai_service.py:421`) re-encoda **todo** o catálogo
com o `multilingual-e5-large` (`services/ai_service.py:59`).
Medido nos logs: **26–32 s por execução**, e ocorreu **11 vezes em 14 dias**.

É chamado de forma **síncrona dentro do request** em 8 pontos de produção:

| Arquivo | Linhas |
|---|---|
| `api/routes/product_routes.py` | 71, 162, 180 (criar / atualizar / deletar produto) |
| `api/routes/company_routes.py` | 51, 82, 599, 653 |
| `api/routes/search_routes.py` | 34 |

Impacto duplo: **(a)** quem cadastra/edita um produto espera ~30 s pela resposta;
**(b)** durante esses 30 s o encode consome os 2 vCPUs e **degrada qualquer chat
simultâneo** — o que explica parte da cauda de `ms_e5` (p99 = 4,5 s, máx = 6,9 s) num
serviço cuja mediana é 426 ms.

### 1.7 Recursos e concorrência

- Instância `t3.medium`: **2 vCPU / 3,8 GB RAM**.
- **1 único worker** uvicorn (nenhuma flag `--workers` em `leiria-eats.service`).
- Processo Python com **2,59 GB RSS (66 % da RAM)**, quase tudo do `multilingual-e5-large`
  carregado em memória.
- Consequência: não há folga de RAM para adicionar workers (cada worker duplicaria o
  modelo), e o encode do E5 disputa CPU com o atendimento HTTP.

### 1.8 Cache do Gemini existe, mas está desligado no caminho de produção

`GeminiCache` (`gemini_sales_service.py:14-42`) é consultado **apenas** em
`generate_response` (fluxo síncrono). O fluxo que roda em produção é
`generate_response_stream` (`gemini_sales_service.py:310`), que só verifica
`_STATIC_RESPONSES` e o limite diário — **nunca consulta o cache**. Ou seja: a
infraestrutura de cache já está escrita, testada e inutilizada no caminho que importa.

---

## 2. Plano de refatoração

Cada item declara explicitamente se **preserva a regra de negócio** (todas preservam) e se
**altera comportamento observável** (algumas alteram — marcadas para validação).

### Fase 1 — Cortar o prompt (maior ganho, risco baixo)

O prompt é hoje **96 % seção de produtos**. Medição real com 15 produtos do catálogo atual:

| Cenário | Tamanho | Tokens |
|---|---|---|
| Prompt atual | 6.749 chars | ~1.687 |
| Sem `description`/`ingredients`/`allergens`/`dietary_tags`/`recommended_for` | 2.448 chars | ~612 |
| **Redução** | — | **−64 %** |

Só o campo `description` custa **2.400 chars (~600 tokens) = 36 % do prompt inteiro**.

**1.1 — Detalhe por produto sob demanda, não para todos**
`services/gemini_sales_service.py:588-637` (`_build_prompt`; o laço por produto começa na
linha 604, `for p in products[:15]`).
Hoje todo produto do pool entra com descrição, ingredientes, alérgenos, tags dietéticas e
"recomendado para". Proposta: enviar o **bloco enxuto** (GID, nome, preço, categoria,
`serves_people`, flags `⭐ popular` / `🎁 caixa surpresa` + janela de recolha) para **todos**
os produtos, e o **bloco detalhado** apenas para:
- os **3 primeiros** produtos (os mais relevantes segundo o E5), e/ou
- quando `intent_type == "specific_question"` ou os sinais de `user_needs` indicarem
  pergunta sobre ingrediente/alérgeno/dieta.

- Regra de negócio: **preservada** — a IA continua vendo **os mesmos produtos**, com os
  mesmos GIDs e preços, e continua podendo adicionar qualquer um ao carrinho.
- Comportamento: **altera** a riqueza da resposta consultiva em turnos onde o cliente
  pergunta detalhe de um produto fora do top-3. Mitigado pela regra de intenção acima.
- Ganho esperado: prompt de ~1.687 → ~700–900 tokens na maioria dos turnos, o que move a
  distribuição da faixa "≥ 1.000 tokens" (p90 = 5.882 ms) para "500–999 tokens"
  (p90 = 683 ms). **É a alavanca mais direta sobre a cauda.**

**1.2 — Não mandar o catálogo inteiro quando não há restaurante fixo**
`services/hybrid_ai_service.py:543` e `971`:
```python
all_products = AIService._product_obj_cache if len(AIService._product_obj_cache) <= 50 else search_results.productResults
```
Com o catálogo atual (23 produtos), **todos** entram no pool, e o corte só acontece depois
(`candidate_pool[:20]` na linha 573, e `products[:15]` no prompt). O resultado é o
`pool_size: 16–17` que aparece em 85 % dos turnos e em **100 % dos turnos acima de 10 s**.
Proposta: usar sempre o ranking do E5 e limitar o pool a ~8 produtos + itens do carrinho +
últimas sugestões (que já têm prioridade na montagem, linhas 546-560).

- Regra de negócio: **preservada** — os filtros de restaurantes travados, aptidão de
  pagamento e exclusividade da Caixa Surpresa continuam rodando sobre o pool, na mesma ordem.
- Comportamento: **altera** — a IA passa a ver menos produtos por turno. Recomendo validar
  com alguns diálogos reais antes de fixar o número (8 é ponto de partida, não conclusão).
- Ganho esperado: reforça 1.1; juntas devem tirar a maioria dos turnos da faixa de risco.

**1.3 — Ligar o cache que já existe no fluxo de stream**
`services/gemini_sales_service.py:310-397` (`generate_response_stream`).
Reaproveitar `GeminiCache` como já é feito em `generate_response`, incluindo a proteção que
já está escrita lá (`gemini_sales_service.py:472-476`): **nunca cachear resposta que contenha
tag `[[...]]`**, porque uma resposta com `[[ADD_TO_CART:...]]` reexecutada em outra sessão
mexeria no carrinho errado. A chave de cache já considera carrinho e histórico
(`_generate_cache_key`, linhas 762-788).

- Regra de negócio: **preservada** (a proteção anti-tag é o que garante isso).
- Comportamento: **não altera** — resposta idêntica, só instantânea em repetição.
- Ganho esperado: elimina a chamada de rede em turnos repetidos (perguntas comuns,
  reinícios de conversa). Ganho depende do padrão de uso real; não estimo número sem medir.

### Fase 2 — Limitar o custo do pior caso

**2.1 — Orçamento de latência (deadline) no retry**
`services/gemini_sales_service.py:327-397`.
Hoje o pior caso observado é **110 s**. O fallback já existe e já é usado (18 vezes em 14
dias) — a proposta é apenas **chegar nele mais rápido**: manter um relógio no início do
turno e, se o tempo acumulado passar de um teto (ex.: 8–10 s), parar de tentar e ir direto
para `_generate_fallback_response`.

- Regra de negócio: **preservada** — o caminho de degradação já é o comportamento atual.
- Comportamento: **altera** apenas o pior caso: em vez de esperar 110 s pela IA, o cliente
  recebe o fallback em ~10 s. (Trade-off honesto: alguns turnos que hoje respondem em 40 s
  passariam a cair no fallback. Dado que 62 % de toda a espera vem desses turnos, e que
  110 s é pior que um fallback rápido, considero o troco favorável — mas é decisão de produto.)
- Ganho esperado: p99 de 55,9 s → teto configurado.

**2.2 — Reduzir rodadas e sleeps do retry**
Mesmo bloco. `max_retries = 2` (3 rodadas) × 4 chaves + `sleep(0,5)` entre chaves +
`sleep(1,5 s)`/`sleep(3 s)` entre rodadas. Como o `503` do tier gratuito **não** é por
chave (constatação já registrada nesta base de código), varrer 4 chaves em cada rodada é
custo sem retorno. Proposta: 1 chave por rodada + no máximo 2 rodadas, mantendo o
failover de chave só para o `429` (que **é** por projeto).

- Regra de negócio: **preservada**.
- Comportamento: **altera** o padrão de degradação (chega ao fallback mais cedo em
  incidente do provedor).
- Ganho esperado: corta a maior parte dos ~6,5 s de sleep puro + o tempo das chamadas 503
  redundantes.

### Fase 3 — Tirar a reindexação do caminho do request

**3.1 — Reindexar em background**
Trocar as 8 chamadas síncronas de `AIService.reload_data(db)` (lista em 1.6) por
agendamento em background (`BackgroundTasks` do FastAPI, ou uma flag "índice sujo" com
reindexação debounced).

- Regra de negócio: **preservada** — o índice continua sendo atualizado após cada mudança
  de catálogo; só deixa de bloquear a resposta HTTP.
- Comportamento: **altera** — passa a existir uma janela de alguns segundos em que a busca
  semântica ainda não reflete o produto recém-criado. Hoje essa janela é zero (à custa de
  30 s de espera no request).
- Ganho esperado: CRUD de produto/empresa de ~30 s → resposta imediata; e some a
  contenção de CPU que degradava chats simultâneos.

**3.2 — Reindexação incremental**
`services/ai_service.py:527-588` (`_index_data`) re-encoda **todos** os produtos a cada
chamada. Para criação/edição/remoção de **um** produto, dá para encodar só o item afetado
e substituí-lo nas estruturas (`_product_obj_cache`, `_product_by_id`,
`_embeddings_products`, `_restaurant_name_by_product_id`,
`_restaurant_surprise_box_window_by_product_id`), preservando o rebind atômico ao final
que já está documentado nas linhas 575-579.

- Regra de negócio: **preservada**.
- Comportamento: **não altera** (mesmo índice, mesmo resultado de busca).
- Ganho esperado: 26–32 s → ordem de ~100 ms por produto alterado. Combinado com 3.1,
  elimina essa fonte de latência.

### Fase 4 — Provedor e infraestrutura

**4.1 — Ativar faturamento no Gemini (maior alavanca sobre a cauda)**
O próprio código já aponta isso como pendência (`gemini_sales_service.py:98-99`). O tier
gratuito é a **causa raiz** dos 38 eventos de 503 e dos 18 fallbacks. Sem billing, os itens
1.x e 2.x **mitigam** a cauda; com billing, a cauda deixa de ter origem.

- Regra de negócio: **preservada**. Comportamento: **não altera** (só fica estável).
- Ganho esperado: remove a fonte dos turnos de 40–110 s. É a única medida que ataca a raiz.
- Custo: precisa de decisão comercial/financeira — fora do escopo técnico.

**4.2 — Não adicionar workers sem resolver a RAM primeiro**
Registrando para evitar a tentativa: com 2,59 GB de RSS num host de 3,8 GB, subir um
segundo worker uvicorn **duplicaria** o modelo E5 e estouraria a memória. Para ter
paralelismo real, primeiro é preciso **ou** subir a instância, **ou** mover o E5 para um
processo/serviço próprio (um encoder compartilhado, com os workers HTTP magros).

**4.3 — Avaliar modelo de embedding menor (opcional, requer validação de qualidade)**
`services/ai_service.py:59` usa `intfloat/multilingual-e5-large` (560 M parâmetros,
~2,2 GB). As variantes `-base`/`-small` reduziriam RAM e tempo de encode
significativamente, liberando espaço para 4.2.

- Regra de negócio: preservada. Comportamento: **altera a qualidade da busca semântica** —
  por isso é opcional e exige comparação lado a lado com consultas reais antes de trocar.

### Fase 5 — Observabilidade (para medir o efeito das fases acima)

**5.1** Separar, na telemetria, o tempo gasto em **retry/erro** do tempo de geração real —
hoje ambos estão embutidos em `ms_ttft`, o que impede distinguir "Gemini lento" de "Gemini
indisponível + nossos sleeps".
**5.2** Registrar `cache_hit` (após 1.3) e `prompt_tokens` antes/depois (após 1.1/1.2).
**5.3** Alarme simples quando a taxa de `motivo_fallback` passar de um limite — hoje os 18
fallbacks só apareceram porque fui ler o log manualmente.

---

## 3. Ordem sugerida e retorno esperado

| # | Item | Esforço | Risco | Alvo |
|---|---|---|---|---|
| 1 | 1.1 Prompt enxuto + detalhe sob demanda | Baixo | Baixo | Cauda (p90–p99) |
| 2 | 3.1 + 3.2 Reindexação em background e incremental | Médio | Baixo | Cauda do E5 + CRUD |
| 3 | 2.1 + 2.2 Deadline e retry mais curto | Baixo | Baixo¹ | p99 / pior caso |
| 4 | 1.3 Cache no stream | Baixo | Baixo | Turnos repetidos |
| 5 | 1.2 Pool menor | Baixo | Médio² | Cauda |
| 6 | 4.1 Billing do Gemini | — | — | **Raiz da cauda** |
| 7 | 5.x Observabilidade | Baixo | Nenhum | Medição |

¹ Risco baixo tecnicamente; a decisão de "falhar rápido em vez de esperar 110 s" é de produto.
² Requer validar em diálogos reais quantos produtos a IA precisa ver para continuar vendendo bem.

**Como medir:** a telemetria por turno já existe e foi a base deste diagnóstico. Recomendo
comparar as mesmas métricas (`ms_e5`, `ms_ttft`, `ms_total`, `pool_size`,
`tokens_prompt_estimado`, `motivo_fallback`) numa janela equivalente após cada fase, com
atenção especial a **p90/p95/p99** — a mediana já está boa e não é onde o problema vive.

---

## 4. O que eu deliberadamente não recomendo

- **Mexer na lógica de negócio para ganhar velocidade.** Os filtros de pool (restaurantes
  travados, aptidão de pagamento, exclusividade da Caixa Surpresa) custam
  `ms_pool` ≈ 0,3 ms — são irrelevantes para a latência e não devem ser tocados.
- **Paralelizar as tentativas entre as 4 chaves.** Multiplicaria o consumo de cota para
  resolver um erro que não é por chave.
- **Trocar o `max_output_tokens=250` ou a `temperature`.** Afetam a resposta ao cliente sem
  atacar a causa (o gargalo é o **primeiro** token — `ms_ttft` —, não a geração).
