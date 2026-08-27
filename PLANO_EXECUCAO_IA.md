# Plano de Execução — IA Generativa e Busca Semântica na Construção do Pedido

**Escopo:** apenas o que afeta (a) a IA entender o pedido e agir corretamente sobre o carrinho e
(b) a busca semântica encontrar o produto certo, rápido. Tudo que é infraestrutura, pagamento,
entrega ou conformidade fica **fora** deste plano — inclusive itens legítimos apontados na
[análise](ANALISE_IA_PEDIDO.md), como trava de sessão no Redis e PII em log, que devem virar
tarefas próprias.

**Base:** [ANALISE_IA_PEDIDO.md](ANALISE_IA_PEDIDO.md). Este documento é o *como* e o *em que ordem*.

**Esforço total:** ~19 dias de desenvolvimento, em 6 fases. As fases 1 e 2 concentram o ganho por
esforço; a fase 3 é a única mudança estrutural.

> Código proposto aqui é rascunho: pressupõe revisão, teste e aprovação antes de produção.

---

## Princípio que ordena o plano

Três coisas, nesta ordem, antes de qualquer refatoração:

1. **Medir** — o repositório não tem suíte de testes nem CI, e os quatro `⏱️` que já existem só
   vão para o stdout. Sem baseline, nenhuma das fases seguintes é comprovável.
2. **Ligar o que já foi construído e está desligado** — `intent_type`, `user_needs`,
   `session.context` e cinco colunas de produto são calculados/populados e descartados antes de
   chegar ao modelo. É a maior distância entre esforço e resultado em todo o projeto.
3. **Só então trocar o contrato** (function calling) e o motor de busca.

Inverter essa ordem é o erro clássico: migrar para function calling primeiro dá trabalho grande,
resolve uma classe real de bugs, e mesmo assim a IA continua sem receber o contexto que o sistema
já calculou.

---

## Grafo de dependências

```
F0 Baseline ──┬─────────────────────────────────────────────► (valida todas as fases)
              │
F1 Destravar ─┼── 1.1 índice enriquecido ──────────┐
              ├── 1.2 ligar contexto ──────────────┤
              ├── 1.3 cache (crítico) ─────────────┤
              └── 1.4 gid obrigatório ──────┐      │
                                            │      │
F2 Latência ──┬── 2.1 pular E5 ─────────────┼──────┤
              ├── 2.2 dict O(n²) ───────────┼──────┤
              ├── 2.3 roteamento modelo ────┼──────┤
              └── 2.4 retry 429 ────────────┼──────┤
                                            ▼      ▼
F3 Function calling ◄───── requer 1.4 e 2.2 ──────────► habilita 4.1, 4.2, 5.1
                                            │
F4 Fidelidade ──┬── 4.1 session.context estruturado
                ├── 4.2 sugestões por GID
                └── 4.3 sem preços no texto
                                            │
F5 Recuperação ─┬── 5.2 corte relativo    (independente de F3)
                ├── 5.3 busca híbrida RRF (independente de F3)
                └── 5.4 índice incremental (independente de F3)
```

**Paralelizável:** F5 (busca) não depende de F3 (contrato). Com dois desenvolvedores, um toca
F3+F4 e o outro F5 a partir da semana 3.

---

# Fase 0 — Linha de base mensurável

**2 dias. Bloqueia a validação de todo o resto.**

### 0.1 Telemetria estruturada por turno

Os quatro timings já existem ([hybrid_ai_service.py:177](services/hybrid_ai_service.py:177),
[:255](services/hybrid_ai_service.py:255), [:268](services/hybrid_ai_service.py:268),
[:294](services/hybrid_ai_service.py:294)) mas vão para `print`. Trocar por um registro
estruturado, uma linha JSON por turno:

```python
# rascunho — services/telemetry.py
import json, logging
_log = logging.getLogger("chat.turn")

def registrar_turno(**campos):
    _log.info(json.dumps(campos, ensure_ascii=False))

# no fim de process_sales_chat_stream
registrar_turno(
    session_id=session.session_id[:8],
    restaurant_gid=restaurant_gid,
    modelo="gemini-flash-lite-latest",
    ms_e5=round(t_e5 * 1000),
    ms_pool=round(t_pool * 1000),
    ms_ttft=round(t_ttft * 1000),
    ms_total=round(t_total * 1000),
    pool_size=len(found_products),
    tokens_prompt_estimado=len(prompt) // 4,
    tags_emitidas=len(add_to_cart_matches),
    tags_aplicadas=n_aplicadas,          # ← novo contador
    tags_descartadas_motivo=motivos,     # ← ["GID_FORA_DO_POOL", ...]
    carrinho_mudou=bool(n_aplicadas),
    promessa_de_acao=_texto_promete_adicao(clean_response),
)
```

`tags_emitidas` vs `tags_aplicadas` é a métrica central do projeto: é ela que quantifica
"a IA mentiu".

**Critério de aceite:** uma linha JSON por turno em produção, com os 12 campos, sem conteúdo de
mensagem do usuário.

### 0.2 Conjunto de avaliação — construído sozinho

Não invente um dataset. Registre, por turno, `(consulta, pool_de_gids, gid_que_entrou_no_carrinho)`.
Depois de ~1 semana de tráfego você tem um conjunto rotulado de graça: **o produto que o usuário
acabou pedindo é o rótulo de relevância**.

Com isso calcule `recall@6`, `MRR` e `posicao_do_item_escolhido` — os três números que dizem se a
busca melhorou ou piorou nas fases 1 e 5.

**Critério de aceite:** script que lê os logs e emite as três métricas; primeira execução com no
mínimo 200 turnos.

### 0.3 Coletar baseline

Rodar 0.1 e 0.2 em produção por **3 a 5 dias** antes de mexer em qualquer coisa.

**Critério de saída da fase:** tabela com p50/p95 de `ms_e5`, `ms_pool`, `ms_ttft`, `ms_total`, mais
`taxa_acao_prometida_x_executada`, `recall@6` e `MRR`. Estes são os números contra os quais todas as
fases seguintes se comparam.

---

# Fase 1 — Destravar o que já existe

**3 dias. Maior retorno por esforço do plano.**

### 1.1 Enriquecer o texto indexado — *0,5 dia*

Hoje o índice usa só `name + description` ([ai_service.py:421](services/ai_service.py:421)),
ignorando cinco colunas criadas exatamente para a IA via
`migration_add_product_ai_columns.sql`. Consultas como "sem lactose", "picante", "leve",
"para o jantar" não têm nenhum sinal no vetor.

```python
# rascunho — ai_service.py, _index_data
def _texto_para_indice(p, categoria_restaurante: str) -> str:
    partes = [p.name, p.description, p.category, categoria_restaurante,
              p.ingredients, p.dietary_tags, p.search_tags,
              p.recommended_for, p.portion_size]
    if p.spice_level and p.spice_level != "não picante":
        partes.append(p.spice_level)
    return "passage: " + " | ".join(x for x in partes if x)
```

**Aceite:** `recall@6` do conjunto 0.2 sobe frente ao baseline. Se não subir, investigar
qualidade do preenchimento das colunas antes de seguir — colunas vazias não geram sinal.

**Risco:** baixo. Rollback = reverter uma função.

### 1.2 Ligar o contexto que é calculado e jogado fora — *1 dia*

`_detect_intent_type` produz `intent_type`, `user_needs`, `is_greeting`, `consultation_mode`,
`specific_question`; o `context` ainda carrega `has_results` e `user_query`. **Nenhum é lido por
`_build_prompt`** (verificado por `grep`). E `session.context` (`pessoas`, `categoria_atual`,
`aguardando`) é lido no prompt ([gemini_sales_service.py:365](services/gemini_sales_service.py:365))
mas nunca escrito.

Duas decisões, nesta tarefa:

**a)** Injetar `intent_type` e `user_needs` no prompt como uma seção curta e factual:

```python
# rascunho — _build_prompt
sinais = []
if context.get("user_needs", {}).get("has_doubt"):        sinais.append("cliente indeciso")
if context.get("user_needs", {}).get("wants_recommendation"): sinais.append("pediu recomendação")
if context.get("user_needs", {}).get("mentions_drink"):   sinais.append("mencionou bebida")
if context.get("user_needs", {}).get("mentions_dessert"): sinais.append("mencionou sobremesa")
sinais_section = f"\n\n🔎 SINAIS DETECTADOS: {', '.join(sinais)}" if sinais else ""
```

**b)** Apagar o que continuar sem uso. `_generate_consultation_response`
([hybrid_ai_service.py:789](services/hybrid_ai_service.py:789)) e `_generate_specific_answer`
([:866](services/hybrid_ai_service.py:866)) somam ~140 linhas mortas, em PT-BR com "R$", num
sistema PT-PT com "€". Não deixe código morto como documentação de intenção.

O preenchimento de `session.context` fica para **4.1**, que depende de structured output.

**Aceite:** `grep` confirma que toda chave posta em `context` é lida em `_build_prompt`, ou foi
removida. Nenhuma função privada sem chamador.

### 1.3 Cache que corrompe carrinho entre sessões — *0,5 dia · CRÍTICO*

A chave é `mensagem + 3 IDs de produto` ([gemini_sales_service.py:400](services/gemini_sales_service.py:400))
e o valor guardado ainda contém as tags `[[ADD_TO_CART:...]]`. Um "sim" do usuário A pode executar
a tag de A no carrinho de B.

Correção mínima, imediata:

```python
# rascunho — generate_response, antes de cachear
if "[[" in ai_response:
    return ai_response          # nunca cachear resposta com ação
cls._cache.set(cache_key, ai_response)
```

E na chave, incluir o estado que a resposta pressupõe:

```python
# rascunho
import hashlib
def _generate_cache_key(cls, user_message, context):
    carrinho = ",".join(f"{i['product_id']}x{i['quantity']}"
                        for i in sorted(context.get("cart", []), key=lambda i: i["product_id"]))
    ultimas = context.get("history_text", "")[-300:]
    gids = ",".join(sorted(p.get("gid", "") for p in context.get("products", [])[:5]))
    bruto = f"{user_message.lower().strip()}|{carrinho}|{ultimas}|{gids}"
    return hashlib.sha256(bruto.encode()).hexdigest()
```

**Aceite:** teste que simula duas sessões com carrinhos diferentes e a mesma mensagem curta
("sim", "quero 2", "pode ser") e verifica que o carrinho de B não é alterado pela resposta de A.
Este teste deve existir e passar antes de qualquer outra tarefa da fase.

**Prioridade:** faça **isolado e primeiro**, num commit só. É o único item do plano com potencial
de misturar pedidos entre clientes.

### 1.4 `products.gid` obrigatório — *0,5 dia*

`ProductDB.gid` é `nullable=True`. Produto sem GID entra no prompt como `[CÓDIGO: ]` — a IA não
tem como referenciá-lo, e a tentativa de adicionar vira uma tag descartada em silêncio. É causa
direta de "disse que adicionou e não adicionou".

Sequência, nesta ordem:

```bash
# 1. Diagnóstico — quantos estão sem GID
psql "$DATABASE_URL" -c "SELECT count(*) FROM products WHERE gid IS NULL OR gid = '';"
```

2. Rodar `scripts/backfill_product_ulids.py` em **homologação** primeiro.
3. Conferir que o count voltou a zero.
4. Só então avaliar a constraint `NOT NULL`.

> ⚠️ **Alteração de schema.** `ALTER TABLE ... SET NOT NULL` é operação de migração: valide em
> homologação, tenha backup verificado e uma janela definida. Se houver dúvida sobre volume ou
> lock, faça só o backfill nesta fase e deixe a constraint para depois — o ganho de IA vem do
> backfill, não da constraint.

Independentemente do schema, blindar o pool no código:

```python
# rascunho — ao montar found_products
if not p_data["gid"]:
    registrar_turno(evento="produto_sem_gid_excluido", product_id=product.id)
    continue          # não oferecer o que a IA não consegue adicionar
```

**Aceite:** zero produtos sem GID no pool; contador `produto_sem_gid_excluido` em zero após o backfill.

---

# Fase 2 — Latência do turno

**2 dias. Ataca TTFT, que é a única latência que o usuário sente no streaming.**

### 2.1 Pular o E5 quando ele não muda nada — *0,5 dia*

Com `restaurant_gid` definido e cardápio menor que o corte do pool (15–20), **todos** os produtos
vão para o prompt de qualquer forma — e a ordenação do E5 é desfeita logo depois pela prioridade
carrinho → últimas sugestões → resto. Paga-se o encode em CPU por um resultado descartado.

```python
# rascunho — process_sales_chat_stream, antes do process_search
usar_e5 = True
if restaurant_id:
    n = db.query(ProductDBModel).filter(ProductDBModel.restaurant_id == restaurant_id).count()
    usar_e5 = n > LIMITE_POOL          # LIMITE_POOL = 20
search_results = AIService.process_search(...) if usar_e5 else _vazio()
```

**Aceite:** `ms_e5` = 0 nos turnos com restaurante fixo e menu pequeno; `recall@6` inalterado
(é o mesmo conjunto de produtos indo ao prompt).

### 2.2 Eliminar o O(n²) do preparo de contexto — *0,5 dia*

O padrão `next((p for p in AIService._product_obj_cache if p.id == ...), None)` aparece em pelo
menos cinco pontos ([hybrid_ai_service.py:192](services/hybrid_ai_service.py:192),
[:486](services/hybrid_ai_service.py:486) e nos laços de carrinho, `cartProducts` e sugestões),
sempre dentro de um laço.

```python
# rascunho — ai_service.py, manter junto do cache
_product_by_id: dict[int, Product] = {}
# em _index_data:  cls._product_by_id = {p.id: p for p in cls._product_obj_cache}
```

Trocar todos os `next(...)` por `AIService._product_by_id.get(id)`.

**Aceite:** `ms_pool` p95 cai; comportamento idêntico (é substituição mecânica).

### 2.3 Roteamento de modelo por turno — *1 dia*

`gemini-flash-lite-latest` é a variante mais fraca em seguir instrução, e o system prompt pede
aritmética de carrinho incremental e decisão condicional. Mas Lite é o mais rápido — subir tudo
para Flash piora TTFT sem necessidade em saudação e conversa fiada.

```python
# rascunho
MODELO_CONVERSA = "gemini-flash-lite-latest"
MODELO_ACAO     = "gemini-flash-latest"   # confirmar nome na documentação oficial

def _escolher_modelo(context: dict) -> str:
    # turno que pode mexer no carrinho merece o modelo melhor
    if context.get("cart") or context.get("intent_type") in {"product_search", "specific_question"}:
        return MODELO_ACAO
    return MODELO_CONVERSA
```

> Confirme os identificadores de modelo, limites e preços na documentação oficial do Gemini antes
> de fixar constantes — mudam com frequência e não devem ser assumidos de memória.

**Aceite:** `taxa_acao_prometida_x_executada` sobe frente ao baseline; `ms_ttft` p95 sobe menos de
30% (se subir mais, reavaliar o critério de roteamento).

### 2.4 Tratar 429 no retry — *0,5 dia*

```python
is_unavailable = "503" in str(e) or "UNAVAILABLE" in str(e)
```

[gemini_sales_service.py:202](services/gemini_sales_service.py:202) só faz retry de **503**. Um
**429** (`RESOURCE_EXHAUSTED`, cota estourada) não casa e cai direto no fallback genérico — o
cliente, com carrinho montado, recebe "Temos disponível: X, Y, Z. Qual prefere?".

Incluir 429 com backoff exponencial e teto, e registrar o motivo do fallback na telemetria para
saber com que frequência isso acontece.

**Aceite:** `motivo_fallback` na telemetria; taxa de fallback visível no baseline.

---

# Fase 3 — Contrato de ação: function calling

**5 dias. Única mudança estrutural. Atrás de flag, com o caminho antigo intacto.**

Elimina de uma vez os oito modos de falha silenciosa da tabela 3.4 da análise: regex que não casa,
tag truncada por `max_output_tokens`, `re.sub` que apaga tag malformada sem log, quantidade sem
teto, ação aplicada só depois do stream terminar.

### 3.1 Definir as ferramentas — *1 dia*

```python
# rascunho — validar contra a documentação atual do SDK google-genai
FERRAMENTAS = [
    {
        "name": "adicionar_ao_carrinho",
        "description": "Adiciona, remove ou ajusta a quantidade de um produto. Delta negativo remove.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_gid": {"type": "string", "description": "GID exato listado no catálogo"},
                "delta_quantidade": {"type": "integer"},
            },
            "required": ["product_gid", "delta_quantidade"],
        },
    },
    {
        "name": "sugerir_produtos",
        "description": "Declara quais produtos serão mostrados como cartão ao cliente.",
        "parameters": {
            "type": "object",
            "properties": {"gids": {"type": "array", "items": {"type": "string"}}},
            "required": ["gids"],
        },
    },
    {"name": "mostrar_sacola", "description": "Abre a sacola para o cliente confirmar.",
     "parameters": {"type": "object", "properties": {}}},
]
```

### 3.2 Executor validado — *1 dia*

```python
# rascunho
MAX_QTD_ITEM = 20

def executar_adicionar(session, gid, delta, pool_por_gid) -> dict:
    produto = pool_por_gid.get(gid)
    if produto is None:
        return {"ok": False, "erro": "GID_FORA_DO_CATALOGO",
                "dica": "Use apenas GIDs da lista PRODUTOS DISPONÍVEIS."}
    if not produto.get("is_available", True):
        return {"ok": False, "erro": "PRODUTO_INDISPONIVEL", "produto": produto["name"]}
    atual = next((i.quantity for i in session.cart if i.product_id == produto["id"]), 0)
    alvo  = max(0, min(atual + delta, MAX_QTD_ITEM))
    if alvo == atual:
        return {"ok": False, "erro": "SEM_EFEITO", "quantidade_atual": atual}
    session.add_to_cart(product_id=produto["id"], name=produto["name"],
                        price=produto["price"], restaurant_gid=produto["restaurant_gid"],
                        quantity=alvo - atual,
                        serves_people=produto.get("serves_people") or 1)
    return {"ok": True, "produto": produto["name"], "quantidade_final": alvo}
```

O ponto decisivo é o **retorno**: o resultado volta ao modelo *antes* de ele redigir a frase final.
Se o GID não existir, ele reformula em vez de mentir. Essa realimentação é o que hoje não existe
em ponto nenhum do fluxo.

### 3.3 Loop de tool-use com streaming — *1,5 dia*

Cuidado de projeto: hoje a ação só é aplicada depois de todo o texto sair — se a conexão cair no
meio, o texto foi entregue e a ação nunca ocorreu. Na nova ordem, **executar a ferramenta antes de
emitir a redação final** resolve isso por construção: quando o primeiro caractere chega ao usuário,
o carrinho já mudou.

Some junto: o filtro caractere a caractere de `[[...]]`
([hybrid_ai_service.py:260](services/hybrid_ai_service.py:260)) deixa de ser necessário.

### 3.4 Flag, sombra e rollout — *1,5 dia*

```python
USAR_FUNCTION_CALLING = os.getenv("IA_FUNCTION_CALLING", "false").lower() == "true"
```

1. **Sombra (3 dias):** flag desligada; rodar o caminho novo em paralelo, registrar o que ele
   *teria* feito, comparar com o que o caminho de tags fez. Sem afetar o usuário.
2. **Rollout:** ligar para 10% das sessões, depois 50%, depois 100%, observando
   `taxa_acao_prometida_x_executada` a cada degrau.
3. **Remoção:** apagar o caminho de tags só depois de a métrica ficar acima de **99%** por uma
   semana inteira em 100%.

**Aceite da fase:** `tags_descartadas_motivo` some da telemetria;
`taxa_acao_prometida_x_executada` ≥ 99%.

**Rollback:** variável de ambiente, sem deploy.

---

# Fase 4 — Fidelidade da resposta

**3 dias. Depende de F3.**

### 4.1 `session.context` preenchido de verdade — *1,5 dia*

Com structured output, extrair a cada turno e persistir na sessão:

```python
# rascunho
ESQUEMA_CONTEXTO = {
    "type": "object",
    "properties": {
        "pessoas": {"type": ["integer", "null"]},
        "categoria_atual": {"type": ["string", "null"]},
        "aguardando": {"type": ["string", "null"],
                       "description": "decisão pendente, ex: 'escolha do sabor'"},
        "restricoes": {"type": "array", "items": {"type": "string"}},
    },
}
```

Fecha o ciclo aberto em 1.2 e é o que dá memória entre turnos — o modelo para de perguntar duas
vezes quantas pessoas são.

**Aceite:** a seção "🧠 CONTEXTO EXTRA" aparece no prompt em conversas com mais de dois turnos;
queda mensurável em perguntas repetidas (amostragem manual de 20 conversas).

### 4.2 Sugestões por GID, aposentando o casamento por nome — *0,5 dia*

`_filter_mentioned_products` ([hybrid_ai_service.py:753](services/hybrid_ai_service.py:753))
adivinha quais cards mostrar procurando o nome no texto. Erra nos dois sentidos: "a Margherita"
não casa com "Pizza Margherita Grande" (nenhum card); "Coca-Cola" casa também em "Coca-Cola Zero"
(card duplicado). A ferramenta `sugerir_produtos` de 3.1 devolve a lista explícita — apagar a
heurística.

### 4.3 Tirar os números da boca do modelo — *1 dia*

O prompt escreve preços como texto ([gemini_sales_service.py:300](services/gemini_sales_service.py:300))
e nada confere a resposta contra o catálogo. Preço, caloria ou **alérgeno** alucinado passa direto.
Em alérgeno isso deixa de ser questão de experiência.

O payload já devolve `products` e `cartProducts` completos. Instrução de sistema: *nunca escreva
preços, calorias ou alérgenos no texto; refira-se ao cartão do produto*. Mais uma validação de
rede: extrair `€ x,yz` da resposta, comparar com os produtos citados, registrar
`divergencia_de_preco` na telemetria.

**Aceite:** `divergencia_de_preco` = 0 numa amostra de 200 turnos.

---

# Fase 5 — Qualidade da recuperação

**4 dias. Independente da F3 — pode correr em paralelo.**

### 5.1 Corte relativo no lugar dos thresholds fixos — *1 dia*

Os valores `0.45`, `0.60` e `0.65` ([ai_service.py:511](services/ai_service.py:511),
[:253](services/ai_service.py:253), [:298](services/ai_service.py:298)) foram escolhidos em
momentos diferentes. Com E5, o cosseno é comprimido numa faixa alta — `0.45` na prática não filtra
nada, e o corte real acaba sendo o `[:6]` de [ai_service.py:533](services/ai_service.py:533).

```python
# rascunho
if prod_results:
    topo = prod_results[0]["score"]
    prod_results = [r for r in prod_results if r["score"] >= topo * FATOR_CORTE]  # 0.97 inicial
```

**Aceite:** `recall@6` mantido ou melhor, com pool médio menor (menos ruído no prompt = menos
tokens e menos distração para o modelo).

### 5.2 Busca híbrida lexical + vetorial — *2 dias*

Nome próprio de prato ("Francesinha", "Bitoque", "Combo do Chefe") é onde o embedding multilingual
é fraco e onde `LIKE`/trigrama acerta trivialmente. Fundir os dois rankings por RRF:

```python
# rascunho
def rrf(rank_lex: int | None, rank_vet: int | None, k: int = 60) -> float:
    s = 0.0
    if rank_lex is not None: s += 1.0 / (k + rank_lex)
    if rank_vet is not None: s += 1.0 / (k + rank_vet)
    return s
```

Lado lexical com `pg_trgm` no Postgres, sobre `name` e `search_tags`.

**Aceite:** `recall@6` sobe especificamente no subconjunto de consultas que são nome de prato —
separe essa fatia do conjunto 0.2 para medir.

### 5.3 Índice incremental — *1 dia*

`reload_data` ([ai_service.py:412](services/ai_service.py:412)) recodifica **todo** o catálogo e é
chamada em oito pontos. Salvar um produto reindexa tudo, de forma síncrona, dentro do request.

Pior: a reatribuição de `_product_obj_cache`, `_embeddings_products` e `_product_owner_name` não é
atômica. Durante a janela, um lookup falha e **a ação do modelo é descartada em silêncio** — mais
uma origem de "disse que adicionou e não adicionou".

Duas correções na mesma tarefa: *upsert* por produto (embedding só do que mudou, chaveado por hash
do texto) e **rebind único** de uma estrutura nova ao final, nunca campo a campo.

**Aceite:** salvar um produto não altera `ms_e5` dos turnos concorrentes; zero
`GID_FORA_DO_POOL` atribuível a reindexação.

---

# Fase 6 — Custo e escala

**2 dias. Fazer depois que F3 estabilizar o formato do prompt.**

### 6.1 Context caching — *1,5 dia*

Numa conversa de 8 mensagens, o mesmo cardápio é retransmitido 8 vezes. Pela leitura de
`_build_prompt`, são ~2.000–3.000 tokens de entrada por turno contra 150–250 de saída — carga
dominada por input numa razão de ~10:1.

Duas medidas: mover o bloco de catálogo para o prefixo estável do prompt (deixando em `contents`
só carrinho + histórico + mensagem) e usar o context caching do Gemini. Reduz custo **e** TTFT,
porque é menos prompt para processar.

> Não fixe preços nem parâmetros de cache por memória — confira a documentação e a tabela oficiais.

**Aceite:** `tokens_prompt_estimado` por turno cai; `ms_ttft` p50 cai.

### 6.2 ANN — *condicional, não fazer agora*

`util.cos_sim` sobre o tensor inteiro é O(n) por consulta. Com o catálogo atual, irrelevante.
**Gatilho para reavaliar:** `ms_e5` p95 acima de 300 ms com 5.1–5.3 já aplicados. Aí sim,
`pgvector` ou FAISS.

---

## Cronograma

| Semana | Frente A | Frente B (se houver 2º dev) |
|---|---|---|
| 1 | F0 baseline · **1.3 cache primeiro, isolado** | — |
| 2 | F1 restante (1.1, 1.2, 1.4) + F2 | — |
| 3 | F3.1–3.3 function calling | F5.1 corte relativo |
| 4 | F3.4 sombra + rollout 10% | F5.2 busca híbrida |
| 5 | F3.4 rollout 100% · F4 | F5.3 índice incremental |
| 6 | F4 restante · F6.1 context caching | folga / medição |

**Com um só desenvolvedor:** ~19 dias úteis, mesma ordem, sem a coluna B.

---

## Portões de decisão

| Momento | Pergunta | Se a resposta for "não" |
|---|---|---|
| Fim da F0 | O baseline tem ≥200 turnos e as 7 métricas? | Não começar a F1 — não haverá como provar ganho |
| Após 1.1 | `recall@6` subiu? | Auditar preenchimento das colunas de IA antes da F5 |
| Após 2.3 | `ms_ttft` p95 subiu mais de 30%? | Estreitar o critério de roteamento para Flash |
| Fim da sombra F3.4 | O caminho novo bate ou supera o antigo? | Não ligar a flag; corrigir o executor primeiro |
| Rollout 100% | `taxa_acao_prometida_x_executada` ≥ 99% por 7 dias? | Não apagar o caminho de tags |

---

## Riscos

| Risco | Fase | Mitigação |
|---|---|---|
| Cache corrompendo carrinho continua em produção durante o plano | F1 | 1.3 vai primeiro, sozinho, com teste dedicado |
| Backfill de `gid` em produção sem validação | F1 | Homologação primeiro; backup verificado; constraint separada do backfill |
| Function calling regride comportamento hoje aceitável | F3 | Modo sombra 3 dias + rollout gradual + flag de rollback sem deploy |
| Trocar modelo e não conseguir provar melhora | F2 | F0 é pré-requisito bloqueante |
| Otimizar busca sem conjunto de avaliação | F5 | 0.2 gera o conjunto a partir do tráfego real |
| Sem CI, uma regressão passa despercebida | todas | Mínimo: testes de 1.3 e do executor 3.2 rodando antes de cada deploy |

---

## O que este plano deliberadamente não cobre

Itens reais da análise, fora do escopo de "IA e busca" — devem virar tarefas próprias:

- **Trava de sessão no Redis** (leitura-modificação-escrita sem lock). Toca a integridade do
  carrinho e vale prioridade alta, mas é infraestrutura, não IA.
- **Conteúdo de mensagem em log claro** (nome, morada, restrição alimentar — dado sensível sob a
  LGPD). Levar à área de privacidade; não é decisão técnica isolada.
- **Sanitização de injeção de prompt via descrição do produto** preenchida pelo lojista. A F3
  reduz muito a superfície, mas a sanitização é tarefa de segurança à parte.
- **Tier pago da API e contador de uso em Redis.** Decisão comercial e de conformidade, discutida
  à parte; o roteamento de 2.3 assume que ela já foi tomada.
