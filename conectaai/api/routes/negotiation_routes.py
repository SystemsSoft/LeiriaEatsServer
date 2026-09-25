# Arquivo: conectaai/api/routes/negotiation_routes.py
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.api.deps import self_id
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.conversation_repo import ConversationRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.mandate_repo import MandateRepository
from conectaai.repositories.negotiation_repo import NegotiationRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.schemas.negotiation import (
    AiCallSummary,
    AuditTurnResponse,
    CounterProposalRequest,
    HumanMessageRequest,
    NegotiationAuditResponse,
    NegotiationMessageResponse,
    NegotiationResponse,
    RaiseAutoLimitRequest,
    StartNegotiationAsCreatorRequest,
    StartNegotiationRequest,
)
from conectaai.services.negotiation import human
from conectaai.services.negotiation.runner import run_negotiation
from conectaai.services.negotiation.state_machine import is_terminal

router = APIRouter(prefix="/negotiations", tags=["Negociações"])


def _require_participant(negotiation, current_user: CurrentUser, my_id: str) -> None:
    own_id = negotiation.creator_id if current_user.role == "creator" else negotiation.company_id
    if own_id != my_id:
        raise HTTPException(status_code=403, detail="Você não faz parte dessa negociação")


def _messages_of(db: Session, negotiation) -> List[NegotiationMessageResponse]:
    """As mensagens de chat entre a empresa e o creator desta negociação."""
    if not negotiation.conversation_id:
        return []
    conversation = ConversationRepository.get_by_id(db, negotiation.conversation_id)
    if conversation is None:
        return []
    return [
        NegotiationMessageResponse(
            id=m.id,
            sender_role="company" if m.sender_id == negotiation.company_id else "creator",
            text=m.text,
            created_at=m.timestamp,
        )
        for m in conversation.messages
    ]


def _to_response(negotiation, messages=None) -> NegotiationResponse:
    return NegotiationResponse(
        id=negotiation.id,
        company_id=negotiation.company_id,
        creator_id=negotiation.creator_id,
        campaign_id=negotiation.campaign_id,
        conversation_id=negotiation.conversation_id,
        company_mandate_id=negotiation.company_mandate_id,
        creator_mandate_id=negotiation.creator_mandate_id,
        state=negotiation.state,
        round_no=negotiation.round_no,
        max_rounds=negotiation.max_rounds,
        current_offer=negotiation.current_offer or {},
        last_actor=negotiation.last_actor or "",
        outcome=negotiation.outcome or "",
        outcome_reason=negotiation.outcome_reason or "",
        agreement_id=negotiation.agreement.id if negotiation.agreement else None,
        turns=list(negotiation.turns),
        messages=messages or [],
        created_at=negotiation.created_at,
        updated_at=negotiation.updated_at,
    )


@router.post("", response_model=NegotiationResponse, status_code=201)
def create_negotiation(
    data: StartNegotiationRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    company_mandate = MandateRepository.get_by_id(db, data.company_mandate_id)
    if not company_mandate or company_mandate.owner_id != company.id or not company_mandate.active:
        raise HTTPException(status_code=400, detail="Mandato da empresa inválido ou inativo")

    creator = CreatorRepository.get_by_id(db, data.creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")

    conversation = ConversationRepository.get_or_create(
        db,
        creator_id=creator.id,
        company_id=company.id,
        campaign_id=data.campaign_id,
        campaign_name=company_mandate.objective or "",
    )

    creator_mandate = MandateRepository.get_active_for_owner(db, owner_type="creator", owner_id=creator.id, campaign_id=None)

    if creator_mandate is None:
        # Creator sem opt-in: cai no fluxo manual de hoje — mensagem +
        # notificação, sem negociação automática nenhuma.
        ConversationRepository.add_message(
            db, conversation, company.id, f"Olá! Temos uma oportunidade: {company_mandate.objective or 'uma campanha'}."
        )
        NotificationRepository.create(
            db,
            user_id=creator.user_id,
            type_="proposal",
            title="Nova oportunidade",
            message=f"{company.name} quer falar sobre {company_mandate.objective or 'uma campanha'}.",
        )
        negotiation = NegotiationRepository.create(
            db,
            {
                "company_id": company.id,
                "creator_id": creator.id,
                "campaign_id": data.campaign_id,
                "conversation_id": conversation.id,
                "company_mandate_id": company_mandate.id,
                "creator_mandate_id": None,
                "state": "waiting_human_creator",
                "max_rounds": company_mandate.max_rounds or 4,
                "outcome_reason": "Creator não tem um agente ativo — negociação segue manual pela conversa.",
            },
        )
        return _to_response(negotiation)

    negotiation = NegotiationRepository.create(
        db,
        {
            "company_id": company.id,
            "creator_id": creator.id,
            "campaign_id": data.campaign_id,
            "conversation_id": conversation.id,
            "company_mandate_id": company_mandate.id,
            "creator_mandate_id": creator_mandate.id,
            "state": "draft",
            "max_rounds": min(company_mandate.max_rounds or 4, creator_mandate.max_rounds or 4),
        },
    )
    return _to_response(negotiation)


@router.post("/creator-start", response_model=NegotiationResponse, status_code=201)
def create_negotiation_as_creator(
    data: StartNegotiationAsCreatorRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    """Caminho inverso de `create_negotiation`: o creator encontrou uma
    oportunidade pela busca semântica (`/ai/match/opportunities`, que
    devolve `mandate_id`) e quer negociar a partir dela. Diferente do lado
    da empresa, aqui o mandato do creator é obrigatório — é ele quem está
    tomando a iniciativa, então precisa ter um agente ativo representando-o
    (sem fallback manual: se não tem mandato, a orientação é ativar o
    agente primeiro em /creator/agent, não abrir uma negociação capenga)."""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")

    creator_mandate = MandateRepository.get_active_for_owner(db, owner_type="creator", owner_id=creator.id, campaign_id=None)
    if not creator_mandate:
        raise HTTPException(status_code=400, detail="Ative seu agente (com um mandato) antes de negociar — veja /creator/agent")

    company_mandate = MandateRepository.get_by_id(db, data.company_mandate_id)
    if not company_mandate or not company_mandate.active or company_mandate.owner_type != "company":
        raise HTTPException(status_code=400, detail="Mandato da empresa inválido ou inativo")

    company = CompanyRepository.get_by_id(db, company_mandate.owner_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    conversation = ConversationRepository.get_or_create(
        db,
        creator_id=creator.id,
        company_id=company.id,
        campaign_id=company_mandate.campaign_id,
        campaign_name=company_mandate.objective or "",
    )

    negotiation = NegotiationRepository.create(
        db,
        {
            "company_id": company.id,
            "creator_id": creator.id,
            "campaign_id": company_mandate.campaign_id,
            "conversation_id": conversation.id,
            "company_mandate_id": company_mandate.id,
            "creator_mandate_id": creator_mandate.id,
            "state": "draft",
            "max_rounds": min(company_mandate.max_rounds or 4, creator_mandate.max_rounds or 4),
        },
    )
    return _to_response(negotiation)


@router.post("/{negotiation_id}/start", response_model=NegotiationResponse, status_code=202)
def start_negotiation(
    negotiation_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)
    if negotiation.state != "draft" or not negotiation.creator_mandate_id:
        raise HTTPException(status_code=400, detail="Essa negociação não pode ser iniciada agora")

    negotiation = NegotiationRepository.update(db, negotiation, {"state": "queued"})
    CampaignRepository.mark_negotiation_started(db, negotiation.campaign_id)
    background_tasks.add_task(run_negotiation, negotiation.id)
    return _to_response(negotiation)


@router.post("/{negotiation_id}/resume", response_model=NegotiationResponse, status_code=202)
def resume_negotiation(
    negotiation_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)
    if is_terminal(negotiation.state):
        return _to_response(negotiation)

    if negotiation.state in ("waiting_human_company", "waiting_human_creator"):
        negotiation = NegotiationRepository.update(db, negotiation, {"state": "queued", "error_count": 0})
    background_tasks.add_task(run_negotiation, negotiation.id)
    return _to_response(negotiation)


@router.post("/{negotiation_id}/raise-limit", response_model=NegotiationResponse)
def raise_auto_limit(
    negotiation_id: str,
    data: RaiseAutoLimitRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Endpoint da tela 'Fora do mandato — precisa de você': o humano
    autoriza um limite novo e a negociação é retomada."""
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)

    mandate_id = negotiation.company_mandate_id if current_user.role == "company" else negotiation.creator_mandate_id
    mandate = MandateRepository.get_by_id(db, mandate_id) if mandate_id else None
    if not mandate:
        raise HTTPException(status_code=400, detail="Mandato não encontrado para esse lado")
    mandate.auto_approve_limit = data.new_auto_limit
    db.commit()

    negotiation = NegotiationRepository.update(db, negotiation, {"state": "queued", "error_count": 0})
    background_tasks.add_task(run_negotiation, negotiation.id)
    return _to_response(negotiation)


@router.post("/{negotiation_id}/cancel", response_model=NegotiationResponse)
def cancel_negotiation(negotiation_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)
    if is_terminal(negotiation.state):
        return _to_response(negotiation)
    negotiation = NegotiationRepository.update(
        db, negotiation, {"state": "rejected", "outcome": "rejected", "outcome_reason": "Cancelada por um dos participantes"}
    )
    return _to_response(negotiation)


@router.get("", response_model=List[NegotiationResponse])
def list_negotiations(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    my_id = self_id(db, current_user)
    company_id = my_id if current_user.role == "company" else None
    creator_id = my_id if current_user.role == "creator" else None
    negotiations = NegotiationRepository.get_all_for_user(db, company_id=company_id, creator_id=creator_id)
    return [_to_response(n) for n in negotiations]


@router.get("/{negotiation_id}", response_model=NegotiationResponse)
def get_negotiation(negotiation_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)
    return _to_response(negotiation, _messages_of(db, negotiation))


@router.post("/{negotiation_id}/messages", response_model=NegotiationResponse)
def send_negotiation_message(
    negotiation_id: str,
    data: HumanMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A pessoa escreve para o outro usuário: a mensagem vai para a conversa entre os dois
    (a de "Conversas"), avisa o outro lado e o agente dele a lê no próximo turno. Vale em
    qualquer estado — é só conversa, não altera a negociação."""
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    _require_participant(negotiation, current_user, self_id(db, current_user))
    try:
        human.post_message(db, negotiation, sender_side=current_user.role, text=data.text)
    except human.HumanActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    db.expire_all()
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    return _to_response(negotiation, _messages_of(db, negotiation))


@router.post("/{negotiation_id}/counter", response_model=NegotiationResponse)
def send_counter_proposal(
    negotiation_id: str,
    data: CounterProposalRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Contraproposta da pessoa: vira um turno humano e reabre a conversa dos agentes — o
    agente do OUTRO lado responde, dentro do mandato dele. Recusada (409) enquanto os
    agentes estão falando."""
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    _require_participant(negotiation, current_user, self_id(db, current_user))
    terms = data.model_dump(exclude={"text"}, exclude_none=True)
    try:
        negotiation = human.post_counter(db, negotiation, sender_side=current_user.role, text=data.text, terms=terms)
    except human.HumanActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if negotiation.state == "queued":
        background_tasks.add_task(run_negotiation, negotiation.id)
    db.expire_all()
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    return _to_response(negotiation, _messages_of(db, negotiation))


@router.get("/{negotiation_id}/audit", response_model=NegotiationAuditResponse)
def get_negotiation_audit(negotiation_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    negotiation = NegotiationRepository.get_by_id(db, negotiation_id)
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negociação não encontrada")
    my_id = self_id(db, current_user)
    _require_participant(negotiation, current_user, my_id)

    turns = []
    for t in sorted(negotiation.turns, key=lambda x: x.created_at):
        ai_call = AiCallLogRepository.get_by_id(db, t.ai_call_id) if t.ai_call_id else None
        turns.append(
            AuditTurnResponse(
                id=t.id,
                round_no=t.round_no,
                actor=t.actor,
                intent=t.intent,
                proposed_terms=t.proposed_terms or {},
                terms_after_policy=t.terms_after_policy or {},
                policy_violations=t.policy_violations or [],
                rationale=t.rationale or "",
                message_text=t.message_text,
                ai_call=AiCallSummary.model_validate(ai_call) if ai_call else None,
                created_at=t.created_at,
            )
        )
    return NegotiationAuditResponse(
        negotiation_id=negotiation.id,
        state=negotiation.state,
        outcome=negotiation.outcome or "",
        outcome_reason=negotiation.outcome_reason or "",
        turns=turns,
    )
