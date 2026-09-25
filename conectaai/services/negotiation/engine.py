# Arquivo: conectaai/services/negotiation/engine.py
#
# Executa UM turno de uma negociação por chamada (run_turn). Quem repete a
# chamada até o fim é services/negotiation/runner.py — essa separação é o
# que permite persistir cada turno como uma transação isolada (ver
# comentário em runner.py sobre por que isso importa sem fila/Celery).
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.models.sql_models import CommercialMandateDB, NegotiationDB, NegotiationTurnDB
from conectaai.repositories.agreement_repo import AgreementRepository
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.repositories.conversation_repo import ConversationRepository
from conectaai.repositories.mandate_repo import MandateRepository
from conectaai.repositories.negotiation_repo import NegotiationRepository
from conectaai.repositories.negotiation_turn_repo import NegotiationTurnRepository
from conectaai.schemas.ai_structured import NegotiationTurnOutput
from conectaai.services.ai import gemini_client, prompts
from conectaai.services.negotiation import fallback_agent, policy
from conectaai.services.negotiation.state_machine import can_transition

_OTHER_ACTOR = {"company_agent": "creator_agent", "creator_agent": "company_agent"}
_SIDE_OF_ACTOR = {"company_agent": "company", "creator_agent": "creator"}
# Quem responde a cada jogada: sempre o AGENTE do lado oposto — inclusive quando
# a jogada foi uma contraproposta de uma pessoa (human.py).
_RESPONDER = {
    "company_agent": "creator_agent",
    "creator_agent": "company_agent",
    "human_company": "creator_agent",
    "human_creator": "company_agent",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mandate_to_dict(mandate: CommercialMandateDB) -> Dict[str, Any]:
    return {
        "id": mandate.id,
        "owner_type": mandate.owner_type,
        "ideal_price": mandate.ideal_price,
        "price_floor": mandate.price_floor,
        "price_ceiling": mandate.price_ceiling,
        "auto_approve_limit": mandate.auto_approve_limit,
        "deliverables": mandate.deliverables or [],
        "negotiable_fields": mandate.negotiable_fields or [],
        "non_negotiable_fields": mandate.non_negotiable_fields or [],
        "deadline_earliest": mandate.deadline_earliest,
        "deadline_latest": mandate.deadline_latest,
        "exclusivity_allowed": mandate.exclusivity_allowed,
    }


def _side_of(actor: str) -> str:
    """'company_agent' e 'human_company' -> 'company'; 'creator_agent' e 'human_creator' -> 'creator'."""
    return "company" if "company" in actor else "creator"


def _counterpart_text(db: Session, negotiation: NegotiationDB, turns: List[NegotiationTurnDB], side: str) -> str:
    """O que o lado oposto disse por último: o texto da última jogada dele (agente OU pessoa)
    mais as mensagens que a pessoa dele mandou no chat depois dessa jogada. Chega ao prompt
    dentro do bloco de "texto da contraparte" (dado, nunca instrução — ver prompts.py)."""
    other_side = "creator" if side == "company" else "company"
    other_turns = sorted((t for t in turns if _side_of(t.actor) == other_side), key=lambda t: t.created_at)
    last_turn = other_turns[-1] if other_turns else None
    parts = [last_turn.message_text] if last_turn and last_turn.message_text else []

    if negotiation.conversation_id:
        conversation = ConversationRepository.get_by_id(db, negotiation.conversation_id)
        other_sender = negotiation.creator_id if other_side == "creator" else negotiation.company_id
        if conversation is not None:
            since = last_turn.created_at if last_turn else None
            chat = [
                m.text
                for m in conversation.messages
                if m.sender_id == other_sender and m.text and (since is None or _naive(m.timestamp) > _naive(since))
            ]
            # o texto da própria contraproposta já vem espelhado no chat: não repete
            parts.extend(t for t in chat[-3:] if t not in parts)
    return "\n".join(parts)


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


def _transition(db: Session, negotiation: NegotiationDB, to_state: str, *, reason: str = "") -> NegotiationDB:
    if not can_transition(negotiation.state, to_state):
        # Não devia acontecer com o motor chamando certo, mas nunca gravamos
        # uma transição fora da tabela — mais seguro falhar visível do que
        # deixar o estado inconsistente silenciosamente.
        raise ValueError(f"Transição inválida: {negotiation.state} -> {to_state}")
    data = {"state": to_state}
    if reason:
        data["outcome_reason"] = reason
    if to_state in ("impasse", "rejected", "expired", "failed"):
        data["outcome"] = to_state
    return NegotiationRepository.update(db, negotiation, data)


def _generate_turn(*, mandate_dict: Dict[str, Any], side: str, round_no: int, counterpart_text: str, current_offer: Dict[str, Any], negotiation_id: str):
    """Tenta o Gemini (se houver chave configurada); em qualquer falha —
    inclusive ausência de chave — cai para o agente determinístico. Devolve
    (raw_dict, ai_call_id_ou_None)."""
    if settings.GEMINI_API_KEYS:
        system_instruction, user_content = prompts.build_negotiation_turn_prompt(
            side=side, mandate=mandate_dict, round_no=round_no, counterpart_text=counterpart_text, current_offer=current_offer
        )
        result = gemini_client.generate_json(
            system_instruction=system_instruction,
            user_content=user_content,
            response_model=NegotiationTurnOutput,
            deadline_s=settings.NEGOTIATION_TURN_DEADLINE_S,
        )
        if result is not None:
            from conectaai.core.database import SessionLocal

            log_db = SessionLocal()
            try:
                log = AiCallLogRepository.create(
                    log_db,
                    negotiation_id=negotiation_id,
                    purpose="negotiation_turn",
                    model=settings.GEMINI_MODEL,
                    key_index=result.key_index,
                    attempt=result.attempt,
                    status=result.status,
                    latency_ms=result.latency_ms,
                    prompt=f"{system_instruction}\n{user_content}",
                    response_raw=result.raw_text,
                    error=result.error,
                )
                ai_call_id = log.id
            finally:
                log_db.close()

            if result.parsed is not None:
                return result.parsed.model_dump(), ai_call_id

    my_last_terms = current_offer if current_offer else {}
    fallback = fallback_agent.propose_turn(
        mandate=mandate_dict,
        side=side,
        my_last_price=my_last_terms.get("price") if my_last_terms.get("price") is not None else None,
        other_last_price=current_offer.get("price") if current_offer else None,
    )
    return fallback, None


def _accept_as_counter_offer(mandate_dict: Dict[str, Any], current_offer: Dict[str, Any], problems: List[str]) -> Dict[str, Any]:
    """Turno (no formato de NegotiationTurnOutput) que substitui um accept
    inválido: a mesma oferta, reduzida ao que o mandato de quem responde permite."""
    proposed = policy.conform_offer(mandate_dict, current_offer)
    template = "Não consigo fechar exatamente assim. Posso fazer {price}" + (" com {deliverables}" if proposed.get("deliverables") else "") + "."
    return {
        "intent": "counter_offer" if current_offer.get("price") is not None else "offer",
        "proposed_terms": proposed,
        "message_template": template,
        "rationale": f"Accept convertido em contraproposta: a oferta atual não cabe no mandato ({', '.join(problems)}).",
    }


def run_turn(db: Session, negotiation: NegotiationDB) -> NegotiationDB:
    if negotiation.state not in ("queued", "running"):
        return negotiation
    if negotiation.state == "queued":
        # Normalmente quem faz essa transição é NegotiationRepository.acquire_lease
        # (chamado pelo runner em background). Fazemos isso aqui também para
        # que run_turn funcione corretamente mesmo chamado direto — como no
        # modo síncrono usado em testes, sem passar pelo runner.
        negotiation = _transition(db, negotiation, "running")

    company_mandate = MandateRepository.get_by_id(db, negotiation.company_mandate_id)
    creator_mandate = (
        MandateRepository.get_by_id(db, negotiation.creator_mandate_id) if negotiation.creator_mandate_id else None
    )
    if not company_mandate or not creator_mandate:
        return _transition(db, negotiation, "waiting_human_creator", reason="Mandato ausente para um dos lados")

    if negotiation.deadline_at and _now() > negotiation.deadline_at:
        return _transition(db, negotiation, "expired", reason="Prazo da negociação expirado")

    # Busca os turnos direto do banco em vez de usar negotiation.turns: essa
    # coleção (relationship) pode ficar desatualizada quando a mesma sessão é
    # reutilizada em chamadas sucessivas de run_turn (ex.: driver síncrono de
    # teste, ou várias rodadas dentro do mesmo processo) — commits feitos por
    # NegotiationTurnRepository.create() em objetos separados não atualizam
    # automaticamente uma coleção já carregada em memória.
    all_turns = NegotiationTurnRepository.get_all_for_negotiation(db, negotiation.id)
    turns_this_round: List[NegotiationTurnDB] = [t for t in all_turns if t.round_no == negotiation.round_no]

    if not all_turns:
        actor = "company_agent"
        # Checagem de impasse óbvio ANTES de qualquer chamada de IA — custo
        # zero, evita gastar uma chamada de LLM (e a latência de até
        # dezenas de segundos) para descobrir que os mandatos não se cruzam.
        if negotiation.round_no == 0:
            company_dict, creator_dict = _mandate_to_dict(company_mandate), _mandate_to_dict(creator_mandate)
            if policy.counterparty_gap_is_unbridgeable(company_dict, creator_dict):
                return _transition(db, negotiation, "impasse", reason="Teto da empresa é menor que o piso do creator")
            if policy.deliverable_catalogs_are_disjoint(company_dict, creator_dict):
                return _transition(
                    db, negotiation, "impasse", reason="Os entregáveis dos dois mandatos não se cruzam (tipo ou quantidade)"
                )
    else:
        if len(turns_this_round) >= 2:  # rodada completa: abre a próxima (ou encerra por limite)
            if negotiation.round_no + 1 >= negotiation.max_rounds:
                return _transition(db, negotiation, "impasse", reason=f"Limite de {negotiation.max_rounds} rodadas atingido sem acordo")
            negotiation = NegotiationRepository.update(db, negotiation, {"round_no": negotiation.round_no + 1})
        # Fala o agente do lado oposto ao da última jogada. Isso vale também depois de uma
        # contraproposta humana (human.py), que não segue o "empresa abre, creator responde".
        actor = _RESPONDER[all_turns[-1].actor]

    side = _SIDE_OF_ACTOR[actor]
    mandate = company_mandate if side == "company" else creator_mandate
    mandate_dict = _mandate_to_dict(mandate)

    counterpart_text = _counterpart_text(db, negotiation, all_turns, side)
    current_offer = negotiation.current_offer or {}

    raw_turn, ai_call_id = _generate_turn(
        mandate_dict=mandate_dict,
        side=side,
        round_no=negotiation.round_no,
        counterpart_text=counterpart_text,
        current_offer=current_offer,
        negotiation_id=negotiation.id,
    )

    proposed_terms = {k: v for k, v in (raw_turn.get("proposed_terms") or {}).items() if v is not None}
    intent = raw_turn.get("intent", "counter_offer")
    waiting_state = "waiting_human_company" if side == "company" else "waiting_human_creator"

    # Aceitar = fechar EXATAMENTE a oferta corrente da contraparte. Se ela não
    # cabe no mandato de quem aceita, o accept é inválido e vira contraproposta
    # (policy.apply só valida o que o agente propõe — um accept propõe quase
    # nada, então sem isto passavam entregáveis/exclusividade/prazo fora do
    # mandato e o preço era reescrito em silêncio, fechando um acordo que a
    # contraparte nunca ofereceu).
    conversion_notes: List[str] = []
    if intent == "accept":
        conversion_notes = policy.acceptance_violations(mandate_dict, current_offer)
        if conversion_notes:
            raw_turn = _accept_as_counter_offer(mandate_dict, current_offer, conversion_notes)
            proposed_terms = raw_turn["proposed_terms"]
            intent = raw_turn["intent"]

    if intent == "accept":
        terms = dict(current_offer)
        turn_violations: List[str] = []
    else:
        policy_result = policy.apply(mandate_dict, previous_terms=current_offer, proposed_terms=proposed_terms)

        if policy_result.is_degenerate:
            return _transition(
                db,
                negotiation,
                waiting_state,
                reason=f"Proposta do agente {side} ficou fora do mandato: {', '.join(policy_result.violations) or 'sem termos válidos'}",
            )

        terms = policy_result.terms
        turn_violations = conversion_notes + policy_result.violations
        if conversion_notes and policy.acceptance_violations(mandate_dict, terms):
            # Nem a contraproposta ajustada cabe no mandato (ex.: campo não
            # negociável) — não publica uma oferta que o próprio lado não pode
            # cumprir; devolve a decisão ao humano.
            return _transition(
                db,
                negotiation,
                waiting_state,
                reason=f"O agente {side} não conseguiu montar uma contraproposta dentro do mandato: {', '.join(conversion_notes)}",
            )

    price = terms.get("price")
    auto_limit = mandate_dict.get("auto_approve_limit") or 0
    needs_human_review = intent == "accept" and auto_limit > 0 and price is not None and price > auto_limit

    message_text = prompts.render_message(raw_turn.get("message_template", ""), terms)

    NegotiationTurnRepository.create(
        db,
        {
            "negotiation_id": negotiation.id,
            "round_no": negotiation.round_no,
            "actor": actor,
            "intent": intent,
            "proposed_terms": proposed_terms,
            "terms_after_policy": terms,
            "policy_violations": turn_violations,
            "rationale": raw_turn.get("rationale", ""),
            "message_text": message_text,
            "ai_call_id": ai_call_id,
        },
    )

    negotiation = NegotiationRepository.update(
        db, negotiation, {"current_offer": terms, "last_actor": actor}
    )

    if intent == "reject":
        return _transition(db, negotiation, "rejected", reason=f"Recusado pelo agente {side}")

    if intent == "accept":
        if needs_human_review:
            waiting_state = "waiting_human_company" if side == "company" else "waiting_human_creator"
            return _transition(
                db, negotiation, waiting_state, reason=f"Valor acordado (R$ {price}) acima do limite de aprovação automática"
            )

        agreement_data = {
            "negotiation_id": negotiation.id,
            "company_id": negotiation.company_id,
            "creator_id": negotiation.creator_id,
            "campaign_id": negotiation.campaign_id,
            "terms": terms,
            "total_value": price or 0,
            "status": "awaiting_approval",
            "company_approved_at": None,
            "creator_approved_at": None,
        }
        # Há um acordo por negociação (unique). Se uma contraproposta humana substituiu o
        # anterior, o mesmo registro volta a valer com os termos novos.
        existing = AgreementRepository.get_by_negotiation_id(db, negotiation.id)
        agreement = (
            AgreementRepository.update(db, existing, {**agreement_data, "rejected_by": "", "reject_reason": ""})
            if existing is not None
            else AgreementRepository.create(db, agreement_data)
        )
        negotiation = _transition(db, negotiation, "waiting_approval", reason="Acordo estruturado, aguardando aprovação dos dois lados")
        negotiation.agreement = agreement
        return negotiation

    # offer / counter_offer: segue rodando — o runner chama run_turn de novo.
    return negotiation
