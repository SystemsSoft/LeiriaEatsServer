# Arquivo: conectaai/api/routes/collaboration_routes.py
#
# "Acordos": o acompanhamento de uma proposta aceita até o fim da campanha — a
# lista de conteúdos, o link que o creator envia de cada um e a aprovação da
# empresa. A regra de negócio fica em services/collaboration_service.py.
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.api.deps import self_id
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.schemas.collaboration import (
    CollaborationDetail,
    CollaborationSummary,
    DeliverableResponse,
    ReviewDeliverableRequest,
    SubmitDeliverableRequest,
)
from conectaai.services import collaboration_service as service

router = APIRouter(prefix="/collaborations", tags=["Acordos"])
deliverables_router = APIRouter(prefix="/deliverables", tags=["Acordos"])


def _raise(error: service.CollaborationError):
    raise HTTPException(status_code=error.status_code, detail=error.detail)


@router.get("", response_model=List[CollaborationSummary])
def list_collaborations(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Os acordos (propostas aceitas) do usuário, com o andamento dos conteúdos. Na primeira
    vez que um acordo aparece, a lista de conteúdos dele é criada."""
    proposals = service.accepted_proposals(db, role=current_user.role, profile_id=self_id(db, current_user))
    summaries = [service.summary_of(db, p, service.ensure_deliverables(db, p)) for p in proposals]
    return sorted(summaries, key=lambda s: s.last_activity_at, reverse=True)


@router.get("/{proposal_id}", response_model=CollaborationDetail)
def get_collaboration(proposal_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        proposal = service.get_for_participant(db, proposal_id, role=current_user.role, profile_id=self_id(db, current_user))
    except service.CollaborationError as error:
        _raise(error)
    return service.detail_of(db, proposal, service.ensure_deliverables(db, proposal))


@deliverables_router.post("/{deliverable_id}/submit", response_model=DeliverableResponse)
def submit_deliverable(
    deliverable_id: str,
    data: SubmitDeliverableRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    """O creator envia (ou reenvia, depois de um pedido de ajustes) o link do conteúdo."""
    try:
        deliverable = service.submit(db, deliverable_id, profile_id=self_id(db, current_user), url=data.url, note=data.note)
    except service.CollaborationError as error:
        _raise(error)
    return DeliverableResponse.model_validate(deliverable)


@deliverables_router.post("/{deliverable_id}/review", response_model=DeliverableResponse)
def review_deliverable(
    deliverable_id: str,
    data: ReviewDeliverableRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """A empresa aprova o conteúdo ou pede ajustes (explicando o que ajustar)."""
    try:
        deliverable = service.review(
            db, deliverable_id, profile_id=self_id(db, current_user), decision=data.decision, feedback=data.feedback
        )
    except service.CollaborationError as error:
        _raise(error)
    return DeliverableResponse.model_validate(deliverable)
