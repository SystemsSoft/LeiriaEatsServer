# Arquivo: conectaai/services/negotiation/human.py
#
# Intervenção HUMANA na negociação. Até aqui empresa e creator só podiam
# autorizar um limite ou cancelar; agora podem escrever um para o outro:
#
# - MENSAGEM (texto livre) -> vai para a conversa entre os dois usuários (a de
#   "Conversas"), avisa o outro lado, e o agente do outro lado a lê como
#   contexto no próximo turno (engine._counterpart_text). Vale em qualquer
#   estado — é só conversa, não muda a negociação.
#
# - CONTRAPROPOSTA (preço/prazo/exclusividade/entregáveis + texto opcional) ->
#   turno humano (`human_company` / `human_creator`) que reabre a conversa dos
#   agentes: o agente do OUTRO lado responde, dentro do mandato dele. A pessoa
#   tem autoridade sobre o próprio lado, então o mandato do agente dela NÃO
#   limita a contraproposta (o mandato do outro lado continua limitando a
#   resposta do agente adversário, e engine.py só aceita o que cabe no mandato
#   de quem aceita). Não vale enquanto os agentes estão falando: o runner em
#   background e uma escrita humana no mesmo instante disputariam a mesma linha.
#
# Este módulo só grava; quem enfileira o runner é a rota
# (negotiation_routes.py), quando a negociação sai daqui em `queued`.
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import MessageDB, NegotiationDB
from conectaai.repositories.agreement_repo import AgreementRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.conversation_repo import ConversationRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.negotiation_repo import NegotiationRepository
from conectaai.repositories.negotiation_turn_repo import NegotiationTurnRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.services.ai import prompts
from conectaai.services.negotiation.state_machine import can_transition, is_terminal

HUMAN_ACTOR = {"company": "human_company", "creator": "human_creator"}
MAX_TEXT_CHARS = 1000


class HumanActionError(Exception):
    """Ação recusada — a rota converte em HTTPException com este status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _clean_text(text: Optional[str]) -> str:
    return (text or "").strip()[:MAX_TEXT_CHARS]


def _clean_terms(terms: Dict[str, Any]) -> Dict[str, Any]:
    """Só os termos que a pessoa realmente preencheu, já validados."""
    clean: Dict[str, Any] = {}

    price = terms.get("price")
    if price is not None:
        if not (0 < float(price) <= 1_000_000):
            raise HumanActionError(400, "O preço precisa ser maior que zero e até R$ 1.000.000.")
        clean["price"] = round(float(price), 2)

    days = terms.get("deadline_days")
    if days is not None:
        if not (1 <= int(days) <= 365):
            raise HumanActionError(400, "O prazo precisa ser de 1 a 365 dias.")
        clean["deadline_days"] = int(days)

    if terms.get("exclusivity") is not None:
        clean["exclusivity"] = bool(terms["exclusivity"])

    deliverables = terms.get("deliverables")
    if deliverables:
        items = []
        for item in deliverables:
            content_type = str(item.get("content_type", "")).strip()[:50]
            quantity = int(item.get("quantity", 0))
            if not content_type or not (1 <= quantity <= 100):
                raise HumanActionError(400, "Cada entregável precisa de um tipo e de uma quantidade de 1 a 100.")
            items.append({"content_type": content_type, "quantity": quantity})
        clean["deliverables"] = items

    if not clean:
        raise HumanActionError(400, "Informe ao menos um termo da contraproposta (preço, prazo, exclusividade ou entregáveis).")
    return clean


def _summarize(offer: Dict[str, Any]) -> str:
    template = "Contraproposta: {price}"
    if offer.get("deliverables"):
        template += " — {deliverables}"
    if offer.get("deadline_days"):
        template += ", prazo de {deadline_days} dias"
    text = prompts.render_message(template, offer)
    if offer.get("exclusivity"):
        text += ", com exclusividade"
    return text


def _sender_and_other(db: Session, negotiation: NegotiationDB, side: str):
    """(id do perfil de quem escreve, usuário do outro lado, nome de quem escreve)."""
    company = CompanyRepository.get_by_id(db, negotiation.company_id)
    creator = CreatorRepository.get_by_id(db, negotiation.creator_id)
    if side == "company":
        return negotiation.company_id, creator, (company.name if company else "A empresa")
    return negotiation.creator_id, company, (creator.name if creator else "O creator")


def _mirror_to_conversation(db: Session, negotiation: NegotiationDB, sender_profile_id: str, text: str) -> MessageDB:
    conversation = ConversationRepository.get_by_id(db, negotiation.conversation_id) if negotiation.conversation_id else None
    if conversation is None:
        conversation = ConversationRepository.get_or_create(
            db,
            creator_id=negotiation.creator_id,
            company_id=negotiation.company_id,
            campaign_id=negotiation.campaign_id,
            campaign_name="",
        )
        NegotiationRepository.update(db, negotiation, {"conversation_id": conversation.id})
    ConversationRepository.add_message(db, conversation, sender_profile_id, text)
    return conversation.messages[-1]


def _notify(db: Session, recipient, *, title: str, message: str) -> None:
    if recipient is not None:
        NotificationRepository.create(db, user_id=recipient.user_id, type_="proposal", title=title, message=message)


def post_message(db: Session, negotiation: NegotiationDB, *, sender_side: str, text: str) -> MessageDB:
    text = _clean_text(text)
    if not text:
        raise HumanActionError(400, "Escreva uma mensagem.")
    sender_profile_id, other, sender_name = _sender_and_other(db, negotiation, sender_side)
    message = _mirror_to_conversation(db, negotiation, sender_profile_id, text)
    _notify(db, other, title="Nova mensagem na negociação", message=f"{sender_name}: {text[:140]}")
    return message


def post_counter(
    db: Session, negotiation: NegotiationDB, *, sender_side: str, text: Optional[str], terms: Dict[str, Any]
) -> NegotiationDB:
    if is_terminal(negotiation.state):
        raise HumanActionError(400, "Essa negociação já terminou — não dá mais para fazer contraproposta.")
    if negotiation.state == "draft":
        raise HumanActionError(400, "Essa negociação ainda não foi iniciada.")
    if negotiation.state in ("running", "queued"):
        raise HumanActionError(409, "Os agentes estão negociando agora — aguarde o turno terminar para fazer a sua contraproposta.")

    agreement = negotiation.agreement
    if agreement is not None and agreement.status == "approved":
        raise HumanActionError(400, "O acordo já foi aprovado pelos dois lados.")

    clean = _clean_terms(terms)
    offer = {**(negotiation.current_offer or {}), **clean}
    message_text = _summarize(offer)
    custom = _clean_text(text)
    if custom:
        message_text = f"{message_text}. {custom}"

    sender_profile_id, other, sender_name = _sender_and_other(db, negotiation, sender_side)
    mirrored = _mirror_to_conversation(db, negotiation, sender_profile_id, message_text)

    has_turns = bool(NegotiationTurnRepository.get_all_for_negotiation(db, negotiation.id))
    round_no = negotiation.round_no + 1 if has_turns else negotiation.round_no
    NegotiationTurnRepository.create(
        db,
        {
            "negotiation_id": negotiation.id,
            "round_no": round_no,
            "actor": HUMAN_ACTOR[sender_side],
            "intent": "counter_offer",
            "proposed_terms": clean,
            "terms_after_policy": offer,
            "policy_violations": [],
            "rationale": "Contraproposta enviada pela própria pessoa.",
            "message_text": message_text,
            "message_id": mirrored.id,
        },
    )

    # Um acordo que aguardava aprovação deixa de valer: a mesa mudou.
    if agreement is not None and agreement.status == "awaiting_approval":
        AgreementRepository.update(
            db, agreement, {"status": "superseded", "company_approved_at": None, "creator_approved_at": None}
        )

    # Só há quem responda se o outro lado tem agente. Sem agente do creator
    # (fluxo manual), a contraproposta fica registrada e o creator responde
    # pessoalmente — enfileirar aqui só faria o motor devolver "mandato ausente".
    agents_on_both_sides = negotiation.creator_mandate_id is not None
    update: Dict[str, Any] = {
        "current_offer": offer,
        "last_actor": HUMAN_ACTOR[sender_side],
        "round_no": round_no,
        "max_rounds": (negotiation.max_rounds or 4) + 1,  # a rodada humana não consome as dos agentes
        "error_count": 0,
    }
    if agents_on_both_sides and can_transition(negotiation.state, "queued"):
        update.update({"state": "queued", "lease_owner": None, "lease_expires_at": None})
    negotiation = NegotiationRepository.update(db, negotiation, update)

    _notify(db, other, title="Nova contraproposta", message=f"{sender_name}: {message_text[:140]}")
    return negotiation
