# Arquivo: conectaai/services/ai/prompts.py
#
# Monta o prompt de geração que sobra no módulo depois que a negociação
# virou 100% humana (services/negotiation/human.py): o rascunho de campanha
# por IA. `render_message` continua em uso — é quem monta o texto de uma
# contraproposta humana a partir dos termos já validados (nunca de texto
# livre digitado por alguém, ver human.py:_summarize).
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
