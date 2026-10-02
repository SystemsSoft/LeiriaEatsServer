# Arquivo: conectaai/api/routes/campaign_ai_routes.py
#
# Criação de campanha por IA: o Assistente pergunta item por item (/intake)
# — ou, no fluxo antigo, a empresa descreve tudo em texto livre (/draft) —,
# a IA propõe campanha + mandato (preview, nada persiste), o humano
# revisa/edita e só então confirma — momento em que CampaignDB e
# CommercialMandateDB são criados de verdade. Mesmo princípio do resto do módulo: o LLM só propõe,
# a confirmação humana é quem decide o que vira dado real.
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.models.sql_models import CampaignDB
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.mandate_repo import MandateRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.schemas.ai import (
    CampaignDraftRequest,
    CampaignDraftResponse,
    CampaignIntakeRequest,
    CampaignIntakeResponse,
    ConfirmCampaignDraftRequest,
    ConfirmCampaignDraftResponse,
)
from conectaai.schemas.ai_structured import CampaignMandateDraft
from conectaai.schemas.campaign import CampaignResponse
from conectaai.schemas.mandate import DeliverableSpec, MandateResponse
from conectaai.services.ai import campaign_intake, gemini_client, prompts
from conectaai.services.ai.campaign_draft_fallback import heuristic_draft

router = APIRouter(prefix="/ai/campaigns", tags=["IA — Campanhas"])


def _campaign_to_response(campaign: CampaignDB) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        company_id=campaign.company_id,
        creator_ids=CampaignRepository.creator_ids_of(campaign),
        budget=campaign.budget,
        status=campaign.status,
        deadline=campaign.deadline,
        description=campaign.description,
        progress=campaign.progress,
        current_stage=campaign.current_stage,
        content_types=campaign.content_types or [],
    )


def _invite_creators(db: Session, company, campaign: CampaignDB, data: ConfirmCampaignDraftRequest, deliverables, creators) -> None:
    if not creators:
        return
    content_type = ", ".join(d["content_type"] for d in deliverables)
    quantity = sum(d.get("min_qty", 1) for d in deliverables) or 1
    # Valor de referência que a empresa definiu (o ideal; na falta dele, o teto por creator).
    budget = data.ideal_price if data.ideal_price > 0 else data.price_ceiling
    deadline = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)
    for creator in creators:
        ProposalRepository.create(
            db,
            company.id,
            {
                "creator_id": creator.id,
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "content_type": content_type,
                "quantity": quantity,
                "deadline": deadline,
                "description": data.objective,
                "budget": budget,
                "message": f"{company.name} escolheu você para a campanha \"{campaign.name}\".",
            },
        )
        NotificationRepository.create(
            db,
            user_id=creator.user_id,
            type_="proposal",
            title="Nova proposta de campanha",
            message=f'{company.name} te convidou para a campanha "{campaign.name}".',
        )


@router.post("/intake", response_model=CampaignIntakeResponse)
def campaign_intake_step(
    data: CampaignIntakeRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Próxima pergunta da criação de campanha item por item, ou o rascunho
    quando todos os itens já foram respondidos (ver services/ai/campaign_intake.py)."""
    return campaign_intake.next_step(db, data.answers)


@router.post("/draft", response_model=CampaignDraftResponse)
def draft_campaign(
    data: CampaignDraftRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Descreva o que você precisa")

    if settings.GEMINI_API_KEYS:
        system_instruction, user_content = prompts.build_campaign_draft_prompt(text)
        result = gemini_client.generate_json(
            system_instruction=system_instruction,
            user_content=user_content,
            response_model=CampaignMandateDraft,
            deadline_s=settings.NEGOTIATION_TURN_DEADLINE_S,
        )
        if result is not None:
            AiCallLogRepository.create(
                db,
                purpose="campaign_draft",
                model=settings.GEMINI_MODEL,
                key_index=result.key_index,
                attempt=result.attempt,
                status=result.status,
                latency_ms=result.latency_ms,
                prompt=f"{system_instruction}\n{user_content}",
                response_raw=result.raw_text,
                error=result.error,
            )
            if result.parsed is not None:
                draft = result.parsed
                return CampaignDraftResponse(
                    campaign_name=draft.campaign_name,
                    objective=draft.objective,
                    target_count=draft.target_count,
                    desired_categories=draft.desired_categories,
                    city=draft.city,
                    budget_total=draft.budget_total,
                    ideal_price=draft.ideal_price,
                    price_ceiling=draft.price_ceiling,
                    deliverables=[
                        DeliverableSpec(content_type=d.content_type, min_qty=1, max_qty=max(1, d.quantity))
                        for d in draft.deliverables
                    ]
                    or [DeliverableSpec(content_type="Reel", min_qty=1, max_qty=max(1, draft.target_count))],
                    clarifying_question=draft.clarifying_question,
                    source="gemini",
                )

    return heuristic_draft(text)


@router.post("/confirm", response_model=ConfirmCampaignDraftResponse, status_code=201)
def confirm_campaign_draft(
    data: ConfirmCampaignDraftRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if not data.campaign_name.strip():
        raise HTTPException(status_code=400, detail="Nome da campanha é obrigatório")

    deliverables = [d.dict() for d in data.deliverables] or [
        DeliverableSpec(content_type="Reel", min_qty=1, max_qty=max(1, data.target_count)).dict()
    ]

    # Sem duplicatas (a chave de campaign_creators é campanha+creator) e só
    # creators que existem — id desconhecido é erro de quem chamou, não algo
    # a descartar em silêncio e deixar a empresa achando que o creator entrou.
    creator_ids = list(dict.fromkeys(data.creator_ids))
    found = {c.id: c for c in CreatorRepository.get_many_by_ids(db, creator_ids)}
    unknown = [cid for cid in creator_ids if cid not in found]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Creator não encontrado: {', '.join(unknown)}")

    campaign = CampaignRepository.create(
        db,
        company.id,
        {
            "name": data.campaign_name,
            "budget": data.budget_total,
            # Criada pela empresa e já confirmada na prévia: nasce ativa (aba "Ativas"), na etapa de
            # Briefing — a etapa acompanha a negociação e o contrato depois.
            "status": "active",
            "description": data.objective,
            "content_types": [d["content_type"] for d in deliverables],
        },
        creator_ids,
    )

    mandate = MandateRepository.create(
        db,
        owner_type="company",
        owner_id=company.id,
        data={
            "campaign_id": campaign.id,
            "objective": data.objective,
            "target_count": data.target_count,
            "budget_total": data.budget_total,
            "ideal_price": data.ideal_price,
            "price_floor": 0,
            "price_ceiling": data.price_ceiling,
            "auto_approve_limit": data.auto_approve_limit,
            "currency": "BRL",
            "max_rounds": data.max_rounds,
            "deliverables": deliverables,
            "negotiable_fields": ["price", "deliverables", "deadline"],
            "non_negotiable_fields": [],
            "deadline_earliest": None,
            "deadline_latest": None,
            "exclusivity_allowed": False,
            "extra_terms": {},
            "expires_at": None,
        },
    )

    # Cada influenciador escolhido recebe a campanha como PROPOSTA (aparece em "Propostas de
    # campanha" do lado dele, para aceitar ou recusar) e uma notificação. Sem isso o vínculo
    # existia só no banco e o creator nunca ficava sabendo.
    _invite_creators(db, company, campaign, data, deliverables, [found[cid] for cid in creator_ids])

    return ConfirmCampaignDraftResponse(
        campaign=_campaign_to_response(campaign),
        mandate=MandateResponse.model_validate(mandate),
    )
