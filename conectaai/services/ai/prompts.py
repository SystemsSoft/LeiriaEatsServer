# Arquivo: conectaai/services/ai/prompts.py
#
# Monta os prompts de geração do módulo (a negociação é 100% humana, ver
# services/negotiation/human.py): o rascunho de campanha por IA e o
# re-ranqueamento de creators da busca (services/ai/creator_rerank.py). `render_message` continua em uso — é quem monta o texto de uma
# contraproposta humana a partir dos termos já validados (nunca de texto
# livre digitado por alguém, ver human.py:_summarize).
import json
import re

_CAMPAIGN_DRAFT_SYSTEM = """Você ajuda uma empresa a estruturar uma campanha publicitária com creators a partir
de uma descrição em linguagem natural, em português do Brasil.

Extraia da descrição:
- campaign_name: nome curto e descritivo para a campanha
- objective: o objetivo resumido em 1 frase
- target_count: quantos creators a empresa quer (se não disser, assuma 1)
- desired_categories: nicho/categoria (ex.: beleza, fitness, moda) — lista vazia se não houver pista
- city: cidade ou região mencionada, ou vazio
- budget_total: orçamento total em reais mencionado no texto
- ideal_price: quanto pagaria por creator idealmente (budget_total / target_count, salvo se o texto disser outro valor)
- price_ceiling: teto por creator (um pouco acima do ideal_price, ex. +30%, salvo se o texto der outro número)
- deliverables: tipos de conteúdo pedidos (Reel, Story, Post etc.) com quantidade mínima e máxima
- clarifying_question: se faltar informação crítica (principalmente orçamento OU quantidade de creators),
  uma pergunta curta e específica para pedir isso ao usuário; caso contrário, string vazia

Nunca invente um valor de orçamento sem nenhuma base no texto — nesse caso, deixe budget_total em 0 e
use clarifying_question para pedir o orçamento."""


def build_campaign_draft_prompt(text: str) -> tuple[str, str]:
    truncated = (text or "")[:2000]
    return _CAMPAIGN_DRAFT_SYSTEM, f"Descrição da empresa:\n{truncated}"


_CREATOR_RERANK_SYSTEM = """Você é o assistente de busca de influenciadores (creators) de uma plataforma que conecta
empresas a creators, em português do Brasil. A empresa descreve quem procura; você recebe uma lista de
creators candidatos com os dados reais do perfil de cada um (JSON) e decide quais atendem melhor ao pedido.

Como avaliar:
- Interprete critérios explícitos e implícitos do pedido: nicho/categoria, cidade/região, plataforma, formato de
  conteúdo, número de seguidores, preço, engajamento, perfil do público (gênero, idade, localização, interesses)
  e avaliação.
- Termos comuns: "micro"/"microinfluenciador" = até 100 mil seguidores; "nano" = até 10 mil; "grande"/"mais
  seguidores" = quanto mais seguidores, melhor; "mais barato"/"menor orçamento"/"menor preço" = quanto menor o
  preco_min, melhor; "até R$ X"/"orçamento de R$ X" = preco_min precisa ser no máximo X; "público feminino" =
  audiencia.feminino_pct alto; "mais engajamento" = engajamento_pct maior.
- Quando o pedido pede uma ordenação (ex.: "com mais seguidores", "mais barato"), compare os números entre os
  candidatos e faça a nota refletir essa ordem entre os que atendem aos demais critérios.
- Um critério explícito NÃO atendido (preço acima do limite, cidade diferente da exigida, plataforma ausente)
  deve derrubar a nota bastante.
- Um dado zerado ou ausente no perfil significa "não informado" — não trate como valor real; trate como
  incerteza.

O que devolver:
- results: um item por candidato que tenha alguma relação com o pedido (pode omitir os que não têm nenhuma),
  com ref (exatamente como veio na lista), score de 0 a 100 (quão bem atende ao pedido) e reason.
- reason: 1 ou 2 frases curtas, em português, explicando a nota com os NÚMEROS e dados reais do perfil
  (ex.: "Tem 85 mil seguidores e cobra a partir de R$ 300, dentro do orçamento; nicho de beleza em São Paulo.").
  Cite também o ponto fraco quando houver. Nunca invente dado que não esteja no perfil.
- summary: 1 frase dizendo como você interpretou o pedido (ex.: "Priorizei creators de beleza em SP, com mais
  seguidores e preço até R$ 500.").

Se houver pedidos anteriores da conversa, o pedido atual pode ser um refinamento deles (ex.: "agora só os mais
baratos") — nesse caso combine os critérios. Se o pedido atual for uma busca nova e independente, ignore os
anteriores. Use somente refs que estão na lista de candidatos."""


def build_creator_rerank_prompt(text: str, previous_queries: list[str], candidates: list[dict], max_results: int) -> tuple[str, str]:
    """`candidates` já vem no formato enxuto de creator_rerank._profile
    (com o `ref` curto no lugar do id)."""
    parts = []
    previous = [q.strip()[:300] for q in previous_queries if q and q.strip()]
    if previous:
        parts.append("Pedidos anteriores da conversa (do mais antigo ao mais recente):\n" + "\n".join(f"- {q}" for q in previous))
    parts.append(f"Pedido atual da empresa:\n{(text or '')[:1000]}")
    parts.append("Creators candidatos:\n" + json.dumps(candidates, ensure_ascii=False, separators=(",", ":")))
    parts.append(f"Devolva no máximo {max_results} itens em results — os que melhor atendem ao pedido.")
    return _CREATOR_RERANK_SYSTEM, "\n\n".join(parts)


def render_message(template: str, terms: dict) -> str:
    """Preenche o template com os valores JÁ validados pelo policy.apply —
    nunca com o que o LLM escreveu livremente. Se sobrar um placeholder sem
    valor correspondente, ele é removido em vez de vazar `{price}` cru para
    o usuário final."""
    deliverables_txt = ", ".join(
        f"{d['quantity']}x {d['content_type']}" for d in (terms.get("deliverables") or [])
    )
    price = terms.get("price")
    price_txt = f"R$ {price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") if price is not None else ""
    values = {
        "price": price_txt,
        "deadline_days": str(terms.get("deadline_days") or ""),
        "deliverables": deliverables_txt,
    }
    try:
        # {price} já vem com "R$"; se o modelo escreveu "R$ {price}" mesmo assim, não deixa duplicar.
        return re.sub(r"R\$\s*R\$", "R$", template.format(**values))
    except (KeyError, IndexError):
        # Placeholder desconhecido no template do modelo — melhor um texto
        # genérico do que expor `{campo_invalido}` cru na tela do usuário.
        return "Nova proposta enviada."
