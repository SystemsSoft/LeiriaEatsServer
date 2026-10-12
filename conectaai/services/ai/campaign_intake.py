# Arquivo: conectaai/services/ai/campaign_intake.py
#
# Criação de campanha pelo Assistente de IA, uma pergunta por vez (ver
# POST /ai/campaigns/intake em api/routes/campaign_ai_routes.py). O app manda
# todas as respostas até agora, cada uma marcada com o item que estava sendo
# perguntado; aqui:
#   1. o Gemini extrai os dados da conversa inteira — assim uma resposta que
#      já adianta outros itens ("3 influenciadoras de beleza em SP") pula as
#      perguntas correspondentes, e um "ajuste" depois do resumo corrige
#      qualquer campo;
#   2. o que o Gemini deixar vazio (ou tudo, se ele estiver indisponível) é
#      lido por regras simples da própria resposta àquele item;
#   3. o próximo item é o primeiro, na ordem de _FIELDS, que não tem valor
#      nem foi respondido. Item respondido com "não sei"/"tanto faz" conta
#      como respondido — senão a pergunta voltaria para sempre.
# Quando nada falta, devolve o rascunho da campanha (mesmo formato do
# POST /ai/campaigns/draft) para o humano revisar antes da busca.
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.schemas.ai import CampaignDraftResponse, CampaignIntakeResponse, IntakeAnswer
from conectaai.schemas.ai_structured import CampaignIntakeExtraction, ProposedDeliverable
from conectaai.schemas.mandate import DeliverableSpec
from conectaai.services.ai import gemini_client

ADJUST = "adjust"  # correção livre feita depois do resumo — pode mudar qualquer campo

# (campo, pergunta, respostas rápidas) — a ordem é a ordem das perguntas.
_FIELDS: List[Tuple[str, str, List[str]]] = [
    ("product", "Vamos lá! Primeiro: o que você quer divulgar com essa campanha? (um produto, serviço, lançamento…)", []),
    ("categories", "Em qual nicho os influenciadores devem atuar?", ["Beleza", "Moda", "Fitness", "Gastronomia", "Tecnologia", "Qualquer nicho"]),
    ("city", "Os influenciadores precisam ser de alguma cidade ou região específica?", ["Qualquer lugar"]),
    ("audience", "Qual público você quer atingir? (ex.: mulheres de 25 a 34 anos, pais, gamers)", ["Sem público específico"]),
    ("target_count", "Quantos influenciadores você quer na campanha?", ["1", "3", "5", "10"]),
    ("budget_total", "Qual o orçamento total da campanha, em euros?", ["Ainda não sei"]),
    ("deliverables", "Quais formatos de conteúdo você precisa? Pode citar mais de um.", ["Reels", "Stories", "Posts no feed", "TikTok"]),
    ("preferences", "Por último: alguma preferência sobre o perfil dos influenciadores?", ["Mais seguidores", "Mais engajamento", "Menor preço", "Sem preferência"]),
]
FIELD_NAMES = [f for f, _, _ in _FIELDS]

_FIELD_LABELS = {
    "product": "o que divulgar",
    "categories": "nicho",
    "city": "cidade/região",
    "audience": "público-alvo",
    "target_count": "quantidade de influenciadores",
    "budget_total": "orçamento total",
    "deliverables": "formatos de conteúdo",
    "preferences": "preferências de perfil",
    ADJUST: "ajuste depois do resumo",
}

_MAX_ANSWERS = 30
_MAX_ANSWER_CHARS = 500

_SYSTEM = """Você extrai os dados de uma campanha publicitária com influenciadores a partir de uma conversa em
português do Brasil, em que o assistente perguntou um item por vez e a empresa respondeu.

Cada resposta vem marcada com o item que estava sendo perguntado. Respostas marcadas como "ajuste depois do
resumo" são correções livres e podem mudar qualquer campo. Se o mesmo dado aparecer mais de uma vez, vale o MAIS
RECENTE. Uma resposta pode trazer mais de um item (ex.: "3 influenciadoras de beleza em SP") — extraia todos.

Campos:
- campaign_name: nome curto e descritivo para a campanha, criado a partir do que será divulgado
- product: o que será divulgado / objetivo da campanha, em 1 frase
- categories: nichos dos influenciadores (ex.: beleza, moda, fitness)
- city: cidade ou região exigida
- any_location: true se a empresa disse que qualquer lugar serve
- audience: público que a campanha quer atingir
- target_count: quantos influenciadores
- budget_total: orçamento total em euros ("3 mil" = 3000, "2.500 €" = 2500)
- deliverables: formatos pedidos — content_type em [Reel, Story, Post, TikTok, Vídeo] e quantity = quantos de cada
  formato por influenciador (1 se não disser)
- preferences: preferências sobre o perfil dos influenciadores (ex.: mais seguidores, mais engajamento, menor preço)

Nunca invente: item não informado fica vazio (texto ""), lista vazia ou 0. Respostas como "tanto faz",
"qualquer", "não sei", "sem preferência" deixam o campo vazio."""


# --- Leitura por regras (sem IA) -------------------------------------------

_NEGATIVE = re.compile(
    r"^\s*(n[ãa]o|nenhum[a]?|nada|tanto faz|qualquer( um| uma| nicho| lugar| p[uú]blico)?|sem (prefer[eê]ncia|p[uú]blico( espec[ií]fico)?|nicho)|ainda n[ãa]o sei|n[ãa]o sei|indiferente)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_ANY_LOCATION = re.compile(r"qualquer (lugar|cidade|regi[ãa]o)|todo o brasil|brasil inteiro|tanto faz|indiferente|n[ãa]o precisa", re.IGNORECASE)
_NUMBER_WORDS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10}
_COUNT_IN_TEXT = re.compile(r"(\d+)\s*(creators?|criador(?:es|as)?|influenciador(?:es|as)?)", re.IGNORECASE)
_MONEY = re.compile(r"(\d+(?:[.\s]\d{3})*(?:,\d+)?|\d+(?:\.\d+)?)\s*(mil|k)?", re.IGNORECASE)
_DELIVERABLE_KEYWORDS = [
    (re.compile(r"reels?", re.IGNORECASE), "Reel"),
    (re.compile(r"stor(y|ies|ys)", re.IGNORECASE), "Story"),
    (re.compile(r"\bposts?\b|feed", re.IGNORECASE), "Post"),
    (re.compile(r"tik\s?tok", re.IGNORECASE), "TikTok"),
    (re.compile(r"v[ií]deos?|youtube|shorts", re.IGNORECASE), "Vídeo"),
]


def _is_negative(text: str) -> bool:
    return bool(_NEGATIVE.match(text or ""))


def _parse_count(text: str) -> int:
    match = re.search(r"\d+", text)
    if match:
        return int(match.group(0))
    for word in re.findall(r"\w+", text.lower()):
        if word in _NUMBER_WORDS:
            return _NUMBER_WORDS[word]
    return 0


def _parse_money(text: str) -> float:
    match = _MONEY.search(text.replace("R$", " ").replace("€", " "))
    if not match:
        return 0.0
    raw, suffix = match.group(1), match.group(2)
    if "," in raw:
        value = raw.replace(".", "").replace(" ", "").replace(",", ".")
    elif re.fullmatch(r"\d+(?:[.\s]\d{3})+", raw):
        value = raw.replace(".", "").replace(" ", "")  # "2.500" = dois mil e quinhentos
    else:
        value = raw
    try:
        amount = float(value)
    except ValueError:
        return 0.0
    return amount * 1000 if suffix else amount


def _parse_deliverables(text: str) -> List[ProposedDeliverable]:
    return [ProposedDeliverable(content_type=name, quantity=1) for pattern, name in _DELIVERABLE_KEYWORDS if pattern.search(text)]


def _heuristic(answers: List[IntakeAnswer]) -> CampaignIntakeExtraction:
    """Cada resposta é lida como valor do item que estava sendo perguntado
    (a resposta mais recente de cada item vence)."""
    data = CampaignIntakeExtraction()
    for answer in answers:
        text = answer.text.strip()
        field = answer.field
        if not text:
            continue
        if field == ADJUST:
            # Sem IA, um ajuste livre só é entendido se citar quantidade ou valor.
            count = _COUNT_IN_TEXT.search(text)
            if count:
                data.target_count = int(count.group(1))
            if "€" in text or "R$" in text or re.search(r"\bor[çc]amento\b|\beuros?\b", text, re.IGNORECASE):
                data.budget_total = _parse_money(text) or data.budget_total
            continue
        if _is_negative(text) and field != "city":
            continue
        if field == "product":
            data.product = text[:255]
        elif field == "categories":
            data.categories = [c.strip() for c in re.split(r",|;|/|\se\s", text) if c.strip()]
        elif field == "city":
            if _ANY_LOCATION.search(text) or _is_negative(text):
                data.any_location, data.city = True, ""
            else:
                data.any_location, data.city = False, text[:120]
        elif field == "audience":
            data.audience = text[:255]
        elif field == "target_count":
            data.target_count = _parse_count(text)
        elif field == "budget_total":
            data.budget_total = _parse_money(text)
        elif field == "deliverables":
            data.deliverables = _parse_deliverables(text)
        elif field == "preferences":
            data.preferences = text[:255]
    return data


# --- Gemini ------------------------------------------------------------------


def _extract_with_ai(db: Session, answers: List[IntakeAnswer]) -> Optional[CampaignIntakeExtraction]:
    if not settings.GEMINI_API_KEYS:
        return None
    lines = [f"[{_FIELD_LABELS.get(a.field, 'resposta livre')}] {a.text.strip()}" for a in answers if a.text.strip()]
    user_content = "Respostas da empresa, em ordem:\n" + "\n".join(lines)
    result = gemini_client.generate_json(
        system_instruction=_SYSTEM,
        user_content=user_content,
        response_model=CampaignIntakeExtraction,
        deadline_s=settings.NEGOTIATION_TURN_DEADLINE_S,
        temperature=0.1,
    )
    if result is None:
        return None
    AiCallLogRepository.create(
        db,
        purpose="campaign_intake",
        model=settings.GEMINI_MODEL,
        key_index=result.key_index,
        attempt=result.attempt,
        status=result.status,
        latency_ms=result.latency_ms,
        prompt=f"{_SYSTEM}\n{user_content}",
        response_raw=result.raw_text,
        error=result.error,
    )
    return result.parsed


def _has_value(data: CampaignIntakeExtraction, field: str) -> bool:
    if field == "city":
        return bool(data.city.strip()) or data.any_location
    value = getattr(data, field)
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _merge(ai: Optional[CampaignIntakeExtraction], fallback: CampaignIntakeExtraction) -> CampaignIntakeExtraction:
    if ai is None:
        return fallback
    merged = ai.model_copy()
    for field in FIELD_NAMES:
        if not _has_value(merged, field) and _has_value(fallback, field):
            if field == "city":
                merged.city, merged.any_location = fallback.city, fallback.any_location
            else:
                setattr(merged, field, getattr(fallback, field))
    if not merged.campaign_name.strip():
        merged.campaign_name = fallback.campaign_name
    return merged


def _to_draft(data: CampaignIntakeExtraction, source: str) -> CampaignDraftResponse:
    target_count = max(1, min(1000, data.target_count or 1))
    budget_total = max(0.0, data.budget_total or 0.0)
    ideal_price = round(budget_total / target_count, 2) if budget_total else 0.0
    product = data.product.strip()
    deliverables = [
        DeliverableSpec(content_type=d.content_type, min_qty=1, max_qty=max(1, min(100, d.quantity)))
        for d in data.deliverables
        if d.content_type.strip()
    ] or [DeliverableSpec(content_type="Reel", min_qty=1, max_qty=1)]
    return CampaignDraftResponse(
        campaign_name=(data.campaign_name.strip() or product[:60] or "Nova campanha")[:255],
        objective=product[:255],
        target_count=target_count,
        desired_categories=[c.strip() for c in data.categories if c.strip()],
        city="" if data.any_location else data.city.strip(),
        budget_total=budget_total,
        ideal_price=ideal_price,
        price_ceiling=round(ideal_price * 1.3, 2),
        deliverables=deliverables,
        clarifying_question="",
        source=source,
    )


def next_step(db: Session, answers: List[IntakeAnswer]) -> CampaignIntakeResponse:
    answers = [
        IntakeAnswer(field=a.field if a.field in FIELD_NAMES or a.field == ADJUST else "", text=a.text.strip()[:_MAX_ANSWER_CHARS])
        for a in answers[-_MAX_ANSWERS:]
        if a.text.strip()
    ]
    fallback = _heuristic(answers)
    ai = _extract_with_ai(db, answers) if answers else None
    data = _merge(ai, fallback)
    source = "gemini" if ai is not None else "heuristic"

    answered: Dict[str, bool] = {a.field: True for a in answers}
    for field, question, quick_replies in _FIELDS:
        if not _has_value(data, field) and not answered.get(field):
            return CampaignIntakeResponse(next_field=field, question=question, quick_replies=quick_replies, source=source)

    return CampaignIntakeResponse(
        next_field="",
        question="",
        quick_replies=[],
        draft=_to_draft(data, source),
        audience=data.audience.strip(),
        preferences=data.preferences.strip(),
        source=source,
    )
