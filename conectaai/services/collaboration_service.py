# Arquivo: conectaai/services/collaboration_service.py
#
# Acompanhamento de um acordo (proposta aceita) até o fim da campanha: os
# conteúdos que o creator precisa entregar, o link que ele envia de cada um, e a
# aprovação (ou o pedido de ajustes) da empresa. As duas partes usam os mesmos
# dados — cada ação da uma gera um evento na linha do tempo e uma notificação
# para a outra.
#
# Conteúdos de um acordo:
#   - criados na primeira vez que o acordo é aberto (listagem ou detalhe), a
#     partir das entregas combinadas — por formato, se o acordo veio de uma
#     negociação; senão, a quantidade da proposta;
#   - status: pending -> submitted -> approved, ou submitted -> changes_requested
#     -> submitted (o creator reenvia) -> ...;
#   - approved é final.
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conectaai.models.sql_models import DeliverableDB, DeliverableEventDB, ProposalDB
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.schemas.collaboration import (
    CollaborationCounts,
    CollaborationDetail,
    CollaborationSummary,
    DeliverableResponse,
)
from conectaai.services import contract_service

MAX_DELIVERABLES = 50
_URL_MAX_CHARS = 1000
_OTHER_SCHEMES = re.compile(r"^(javascript|data|file|vbscript|mailto|tel|blob|ftp):", re.IGNORECASE)
STATUSES = ("pending", "submitted", "changes_requested", "approved")


class CollaborationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# --- Link do conteúdo ----------------------------------------------------------


def normalize_url(raw: str) -> str:
    """Link http(s) válido a partir do que a pessoa colou. Sem esquema ("drive.google.com/…")
    assume https. Recusa qualquer outro esquema (javascript:, data:, file:…): a outra parte
    vai abrir este link."""
    text = (raw or "").strip()
    if not text:
        raise CollaborationError(400, "Cole o link do conteúdo.")
    if len(text) > _URL_MAX_CHARS:
        raise CollaborationError(400, "O link é longo demais.")
    if re.search(r"\s", text):
        raise CollaborationError(400, "O link não pode ter espaços.")
    if "://" not in text:
        if _OTHER_SCHEMES.match(text):
            raise CollaborationError(400, "Use um link que comece com http:// ou https://.")
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise CollaborationError(400, "Use um link que comece com http:// ou https://.")
    if "." not in (parsed.hostname or ""):
        raise CollaborationError(400, "Esse link não parece válido.")
    return text


# --- Conteúdos de um acordo ------------------------------------------------------


def _plan_items(db: Session, proposal: ProposalDB) -> List[Dict[str, str]]:
    terms = contract_service.collect_terms(db, proposal)
    items: List[Dict[str, str]] = []
    if terms.get("deliverables"):
        for deliverable in terms["deliverables"]:
            quantity = max(1, int(deliverable["quantity"]))
            for i in range(1, quantity + 1):
                label = deliverable["content_type"] if quantity == 1 else f"{deliverable['content_type']} {i}"
                items.append({"label": label, "content_type": deliverable["content_type"]})
    else:
        quantity = max(1, int(proposal.quantity or 1))
        formats = (proposal.content_type or "").strip() or "Conteúdo"
        multiple = " + " in formats or "," in formats
        for i in range(1, quantity + 1):
            if multiple:
                label = f"Conteúdo {i}"
            else:
                label = formats if quantity == 1 else f"{formats} {i}"
            items.append({"label": label, "content_type": formats})
    return items[:MAX_DELIVERABLES]


def ensure_deliverables(db: Session, proposal: ProposalDB) -> List[DeliverableDB]:
    existing = db.query(DeliverableDB).filter(DeliverableDB.proposal_id == proposal.id).order_by(DeliverableDB.position).all()
    if existing:
        return existing
    for position, item in enumerate(_plan_items(db, proposal), start=1):
        db.add(
            DeliverableDB(
                proposal_id=proposal.id,
                company_id=proposal.company_id,
                creator_id=proposal.creator_id,
                campaign_id=proposal.campaign_id,
                position=position,
                label=item["label"],
                content_type=item["content_type"],
            )
        )
    try:
        db.commit()
    except IntegrityError:  # duas telas abriram ao mesmo tempo: a outra já criou
        db.rollback()
    CampaignRepository.mark_production_started(db, proposal.campaign_id)
    return db.query(DeliverableDB).filter(DeliverableDB.proposal_id == proposal.id).order_by(DeliverableDB.position).all()


# --- Consulta -------------------------------------------------------------------


def accepted_proposals(db: Session, *, role: str, profile_id: str) -> List[ProposalDB]:
    if not profile_id:
        return []
    query = db.query(ProposalDB).filter(ProposalDB.status == "accepted")
    query = query.filter(ProposalDB.company_id == profile_id if role == "company" else ProposalDB.creator_id == profile_id)
    return query.all()


def _counts(deliverables: List[DeliverableDB]) -> CollaborationCounts:
    counts = CollaborationCounts(total=len(deliverables))
    for d in deliverables:
        if d.status in STATUSES:
            setattr(counts, d.status, getattr(counts, d.status) + 1)
    return counts


def summary_of(db: Session, proposal: ProposalDB, deliverables: List[DeliverableDB]) -> CollaborationSummary:
    company = CompanyRepository.get_by_id(db, proposal.company_id)
    creator = CreatorRepository.get_by_id(db, proposal.creator_id)
    campaign = CampaignRepository.get_by_id(db, proposal.campaign_id) if proposal.campaign_id else None
    counts = _counts(deliverables)
    activity = [d.updated_at or d.created_at for d in deliverables if (d.updated_at or d.created_at)]
    return CollaborationSummary(
        proposal_id=proposal.id,
        campaign_id=proposal.campaign_id,
        campaign_name=(proposal.campaign_name or (campaign.name if campaign else "") or ""),
        company_id=proposal.company_id,
        company_name=(company.name if company else "") or "",
        company_avatar_url=(company.avatar_url if company else "") or "",
        creator_id=proposal.creator_id,
        creator_name=(creator.name if creator else "") or "",
        creator_avatar_url=(creator.avatar_url if creator else "") or "",
        content_type=proposal.content_type or "",
        quantity=proposal.quantity or 1,
        price=float(proposal.budget or 0),
        deadline=proposal.deadline,
        counts=counts,
        status="completed" if counts.total and counts.approved == counts.total else "in_progress",
        last_activity_at=max(activity) if activity else _now(),
    )


def detail_of(db: Session, proposal: ProposalDB, deliverables: List[DeliverableDB]) -> CollaborationDetail:
    base = summary_of(db, proposal, deliverables)
    return CollaborationDetail(**base.model_dump(), deliverables=[DeliverableResponse.model_validate(d) for d in deliverables])


def get_for_participant(db: Session, proposal_id: str, *, role: str, profile_id: str) -> ProposalDB:
    proposal = db.query(ProposalDB).filter(ProposalDB.id == proposal_id).first()
    own_id = proposal.company_id if proposal and role == "company" else (proposal.creator_id if proposal else None)
    if proposal is None or not profile_id or own_id != profile_id:
        raise CollaborationError(404, "Acordo não encontrado")
    if proposal.status != "accepted":
        raise CollaborationError(404, "Acordo não encontrado")
    return proposal


# --- Ações ----------------------------------------------------------------------


def _load_deliverable(db: Session, deliverable_id: str, *, role: str, profile_id: str) -> DeliverableDB:
    deliverable = db.query(DeliverableDB).filter(DeliverableDB.id == deliverable_id).first()
    if deliverable is None:
        raise CollaborationError(404, "Conteúdo não encontrado")
    own_id = deliverable.creator_id if role == "creator" else deliverable.company_id
    if not profile_id or own_id != profile_id:
        raise CollaborationError(403, "Esse conteúdo não é seu")
    return deliverable


def _campaign_label(db: Session, deliverable: DeliverableDB) -> str:
    proposal = db.query(ProposalDB).filter(ProposalDB.id == deliverable.proposal_id).first()
    campaign = CampaignRepository.get_by_id(db, deliverable.campaign_id) if deliverable.campaign_id else None
    return (proposal.campaign_name if proposal and proposal.campaign_name else (campaign.name if campaign else "")) or "da campanha"


def _short(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def submit(db: Session, deliverable_id: str, *, profile_id: str, url: str, note: str) -> DeliverableDB:
    deliverable = _load_deliverable(db, deliverable_id, role="creator", profile_id=profile_id)
    if deliverable.status not in ("pending", "changes_requested"):
        raise CollaborationError(
            400,
            "Esse conteúdo já foi aprovado." if deliverable.status == "approved" else "Esse conteúdo já está em análise pela empresa.",
        )
    clean_url = normalize_url(url)
    now = _now()
    note = (note or "").strip()
    deliverable.status = "submitted"
    deliverable.url = clean_url
    deliverable.note = note
    deliverable.feedback = ""
    deliverable.submitted_at = now
    db.add(DeliverableEventDB(deliverable_id=deliverable.id, actor_role="creator", kind="submitted", url=clean_url, message=note, created_at=now))
    db.commit()
    db.refresh(deliverable)

    CampaignRepository.mark_content_submitted(db, deliverable.campaign_id)
    company = CompanyRepository.get_by_id(db, deliverable.company_id)
    creator = CreatorRepository.get_by_id(db, deliverable.creator_id)
    if company:
        NotificationRepository.create(
            db,
            user_id=company.user_id,
            type_="campaignUpdate",
            title="Conteúdo enviado para aprovação",
            message=f'{creator.name if creator else "O creator"} enviou "{deliverable.label}" da campanha "{_campaign_label(db, deliverable)}". Aprove ou peça ajustes em Acordos.',
        )
    return deliverable


def review(db: Session, deliverable_id: str, *, profile_id: str, decision: str, feedback: str) -> DeliverableDB:
    deliverable = _load_deliverable(db, deliverable_id, role="company", profile_id=profile_id)
    if decision not in ("approve", "request_changes"):
        raise CollaborationError(400, "Decisão inválida.")
    if deliverable.status != "submitted":
        raise CollaborationError(
            400,
            "Esse conteúdo já foi aprovado." if deliverable.status == "approved" else "Esse conteúdo ainda não foi enviado pelo creator.",
        )
    feedback = (feedback or "").strip()
    if decision == "request_changes" and not feedback:
        raise CollaborationError(400, "Explique o que precisa ser ajustado.")

    now = _now()
    approved = decision == "approve"
    deliverable.status = "approved" if approved else "changes_requested"
    deliverable.feedback = feedback
    deliverable.reviewed_at = now
    db.add(
        DeliverableEventDB(
            deliverable_id=deliverable.id,
            actor_role="company",
            kind="approved" if approved else "changes_requested",
            url=deliverable.url,
            message=feedback,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(deliverable)

    siblings = db.query(DeliverableDB).filter(DeliverableDB.proposal_id == deliverable.proposal_id).all()
    all_approved = approved and bool(siblings) and all(d.status == "approved" for d in siblings)
    if all_approved:
        CampaignRepository.mark_content_approved(db, deliverable.campaign_id)

    company = CompanyRepository.get_by_id(db, deliverable.company_id)
    creator = CreatorRepository.get_by_id(db, deliverable.creator_id)
    campaign_name = _campaign_label(db, deliverable)
    company_name = (company.name if company else "") or "A empresa"
    if creator:
        if approved:
            NotificationRepository.create(
                db,
                user_id=creator.user_id,
                type_="campaignUpdate",
                title="Conteúdo aprovado!",
                message=f'{company_name} aprovou "{deliverable.label}" da campanha "{campaign_name}".',
            )
        else:
            NotificationRepository.create(
                db,
                user_id=creator.user_id,
                type_="campaignUpdate",
                title="Ajustes solicitados",
                message=f'{company_name} pediu ajustes em "{deliverable.label}": {_short(feedback)}',
            )
    if all_approved:
        for recipient in (company, creator):
            if recipient:
                NotificationRepository.create(
                    db,
                    user_id=recipient.user_id,
                    type_="campaignUpdate",
                    title="Todos os conteúdos aprovados",
                    message=f'Todos os conteúdos do acordo da campanha "{campaign_name}" foram aprovados.',
                )
    return deliverable
