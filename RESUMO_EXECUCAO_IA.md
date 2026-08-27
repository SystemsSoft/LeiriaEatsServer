# Resumo da Execução — IA Generativa e Busca Semântica

Companheiro de [ANALISE_IA_PEDIDO.md](ANALISE_IA_PEDIDO.md) (diagnóstico) e
[PLANO_EXECUCAO_IA.md](PLANO_EXECUCAO_IA.md) (plano em 6 fases). Este documento registra
**o que foi de fato implementado** nesta sessão, o que ficou de fora e por quê.

**Números:** 3 arquivos de serviço alterados (+814/−198 linhas), 1 arquivo novo
(`services/telemetry.py`), 8 arquivos de teste novos (43 testes, 1207 linhas), todos
passando. Nenhum deploy foi feito — tudo local, revisão e teste em produção ficam por sua conta.

---

## Achados urgentes (fora do escopo de código)

1. **`GEMINI_API_KEY` do `.env` está com créditos esgotados.** Confirmado com uma chamada
   real mínima: `429 RESOURCE_EXHAUSTED — Your prepayment credits are depleted`. Se for a
   chave de produção, todo pedido no chat está caindo no fallback genérico agora. Recarregar
   em https://ai.studio/projects. Isso bloqueou a validação ao vivo de tudo relacionado a
   function calling nesta sessão (testado só contra dublês do SDK).
2. **Bug latente de schema:** [schemas/models.py:11](schemas/models.py:11) exige
   `description: str` obrigatório, mas `products.description` é `nullable=True` no banco.
   Um produto com descrição `NULL` faz a busca (`AIService.process_search`) retornar 500 em
   vez de resultados. As rotas de criação/edição já exigem a descrição, então é provável que
   seja só uma armadilha latente — vale rodar `SELECT count(*) FROM products WHERE description IS NULL`.
3. **Correção ao próprio plano:** o banco real é **MySQL** (`mysql+pymysql` em
   `core/database.py`), não Postgres. O rascunho original de busca híbrida (F5.2) previa
   `pg_trgm`; a implementação final não depende de nenhum recurso de dialeto.

---

## F0 — Telemetria (feito)

Novo módulo [services/telemetry.py](services/telemetry.py): uma linha JSON estruturada por
turno de conversa, sem conteúdo de mensagem (LGPD). Campos centrais:
`tags_emitidas` vs. `tags_aplicadas` (a métrica de "a IA mentiu"), `promessa_sem_execucao`,
`divergencia_de_preco`, tempos de cada etapa (`ms_e5`, `ms_pool`, `ms_ttft`, `ms_total`).
Conectado nos dois caminhos (`process_sales_chat` e `process_sales_chat_stream`), inclusive
no ramo de erro/fallback.

## F1 — Destravar o que já existia (feito)

- **1.1** Índice de busca agora usa `ingredients`, `dietary_tags`, `search_tags`,
  `recommended_for`, `spice_level` — antes só `name + description`.
- **1.2** `intent_type`/`user_needs`, calculados e descartados antes, agora chegam ao
  prompt do Gemini como "🔎 SINAIS DETECTADOS". ~120 linhas de código morto removidas
  (`_generate_consultation_response`, `_generate_specific_answer`).
- **1.3 (crítico, feito isolado primeiro)** Cache do Gemini não mistura mais carrinho
  entre sessões: chave agora inclui hash do carrinho + histórico; resposta com
  `[[ADD_TO_CART...]]` nunca é cacheada.
- **1.4** Produto sem GID é excluído do pool antes de chegar à IA (ela não conseguiria
  referenciá-lo de qualquer forma).

## F2 — Latência (feito)

- **2.1** E5 pulado quando o restaurante é fixo e o menu é pequeno (o resultado seria
  descartado de qualquer forma pela priorização carrinho → sugestões → resto).
- **2.2** Os 8 pontos de `next((p for p in cache...))` O(n²) viraram `dict.get` O(1).
- **2.3** Roteamento de modelo por turno: Flash-Lite para conversa fiada, Flash para
  qualquer turno que possa alterar o carrinho.
- **2.4** Retry agora cobre `429` (cota estourada), não só `503`.

## F3 — Function calling (feito no caminho síncrono; streaming fica para depois)

Migrou o contrato de tags `[[ADD_TO_CART:...]]` para 3 ferramentas nativas do Gemini
(`adicionar_ao_carrinho`, `mostrar_sacola`, `sugerir_produtos`), **atrás da flag
`IA_FUNCTION_CALLING`** (default desligada). Formato validado por introspecção do SDK
realmente instalado (`google-genai 2.17.0`), não por suposição. Executor validado em
`HybridAIService._executar_ferramenta`: rejeita GID fora do catálogo, aplica teto de
quantidade (20), rejeita produto indisponível — e o resultado volta ao modelo *antes* da
resposta final, o que impede a IA de afirmar uma ação que não ocorreu.

**Não testado contra a API viva** (créditos esgotados) — só contra dublê do cliente que
replica o formato introspectado. **Streaming tool-calling (F3.3) não foi implementado** —
mais arriscado de acertar sem poder validar ao vivo; o caminho síncrono (`/chat/sales`)
está pronto e testado, o de streaming (`/chat/sales/stream`, o que o app usa) continua no
contrato de tags até isso ser feito.

## F4 — Fidelidade da resposta (feito)

- **4.1** `session.context` (pessoas/categoria_atual/aguardando) agora é escrito de
  verdade — por heurística em Python (não via structured output do Gemini como o plano
  original previa, para não dobrar chamadas de API durante o incidente de créditos).
  Cuidado testado: "quero 2 pizzas" não vira "pedido para 2 pessoas".
- **4.2** Sugestões de produto (quais cards mostrar) vêm de GIDs declarados
  explicitamente pelo modelo via `sugerir_produtos`, quando function calling está ligado
  — substitui a heurística de casar nome no texto (`_filter_mentioned_products`), que
  errava nos dois sentidos.
- **4.3** Instrução "nunca escreva preços/calorias/alérgenos" adicionada também ao prompt
  original (só estava no de function calling). Nova função
  `telemetry.detectar_divergencia_de_preco`: sinaliza quando o texto menciona um valor em
  € que não bate com nenhum produto do pool.

## F5 — Qualidade da recuperação (5.1 e 5.2 feitos; 5.3 parcial)

- **5.1** Corte relativo ao top-1 (score ≥ 97% do melhor) no lugar do threshold fixo
  `0.45`, que na prática não filtrava quase nada com embeddings E5.
- **5.2** Busca híbrida: sinal léxico em Python (substring + overlap de tokens, com lista
  de stopwords para não deixar "quero um produto qualquer" casar com qualquer produto),
  fundido com o score vetorial via Reciprocal Rank Fusion. Resgata nomes próprios de
  prato ("Francesinha") que o embedding multilingual subestima.
- **5.3 parcial** — rebind atômico do índice feito (elimina lookup falhando em silêncio
  durante reindexação). Upsert incremental por produto (só reencodar o que mudou) **não
  foi feito**: exigiria manipular o tensor de embeddings por fatias mantendo várias
  estruturas em sincronia, e não dava para validar contra o banco real nesta sessão.

## F6 — Custo e escala (não feito)

Context caching do Gemini não implementado — depende de validação ao vivo, bloqueada
pelos créditos esgotados.

---

## Testes

| Arquivo | Cobre |
|---|---|
| `tests/test_cache_isolation.py` | F1.3 — cache não mistura carrinho entre sessões |
| `tests/test_ai_service_indexing.py` | F1.1, F1.4, F2.2 — índice enriquecido, dict O(1), rebind atômico |
| `tests/test_pipeline_integration.py` | Ponta a ponta: sessão → pool → Gemini → tag → carrinho, contra SQLite em memória |
| `tests/test_function_calling.py` | F3, F4.2 — schema das ferramentas, executor validado, loop completo, flag liga/desliga sem regressão |
| `tests/test_session_context.py` | F4.1 — extração de pessoas/categoria/aguardando |
| `tests/test_telemetry.py` | F0, F4.3 — promessa de ação, divergência de preço |
| `tests/test_relative_cutoff.py` | F5.1 — corte relativo descarta candidato fraco que passaria no piso fixo antigo |
| `tests/test_hybrid_search.py` | F5.2 — nome próprio de prato resgatado mesmo com score vetorial mediano |

Rodar tudo:
```bash
for f in tests/test_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```

---

## Antes de ligar `IA_FUNCTION_CALLING=true` em produção

1. Recarregar créditos da API.
2. Rodar pelo menos uma conversa real (não dublê) exercitando as 3 ferramentas.
3. Ligar a flag em ambiente de teste, observar `tags_emitidas` vs. `tags_aplicadas` na
   telemetria por alguns dias antes de qualquer rollout em produção (a métrica que decide
   isso é `taxa_acao_prometida_x_executada` — ver PLANO_EXECUCAO_IA.md, fase 3.4).
4. Reversão, se algo der errado: só a variável de ambiente, sem deploy de código.

## O que ficou para depois

F3.3 (streaming tool-calling), F5.3 completo (upsert incremental), F6 (context caching) —
todos por tamanho de escopo real ou por dependerem de validação contra a API viva/banco
real, indisponíveis nesta sessão.
