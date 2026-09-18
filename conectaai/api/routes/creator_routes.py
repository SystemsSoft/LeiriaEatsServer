# Arquivo: conectaai/api/routes/creator_routes.py
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.core.uploads import save_avatar_image
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.repositories.rating_repo import RatingRepository
from conectaai.schemas.creator import CreatorResponse, CreatorUpdateRequest, creator_to_response as _to_response
from conectaai.schemas.rating import RatingRequest, RatingResponse

router = APIRouter(prefix="/creators", tags=["Creators"])


@router.get("", response_model=List[CreatorResponse])
def list_creators(db: Session = Depends(get_db)):
    return [_to_response(c) for c in CreatorRepository.get_all(db)]


@router.get("/me", response_model=CreatorResponse)
def get_my_profile(
    current_user: CurrentUser = Depends(require_role("creator")), db: Session = Depends(get_db)
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")
    return _to_response(creator)


@router.put("/me", response_model=CreatorResponse)
def update_my_profile(
    data: CreatorUpdateRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    update_data = data.dict(exclude_unset=True)
    if "audience" in update_data:
        update_data["audience_info"] = update_data.pop("audience")
    creator = CreatorRepository.update(db, creator, update_data)
    return _to_response(creator)


@router.post("/me/avatar", response_model=CreatorResponse)
async def upload_my_avatar(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    url = await save_avatar_image(file)
    creator = CreatorRepository.update(db, creator, {"avatar_url": url})
    return _to_response(creator)


@router.post("/{creator_id}/view", response_model=CreatorResponse)
def record_profile_view(
    creator_id: str,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Chamado quando uma empresa abre o perfil de um creator — cada chamada
    soma 1 na contagem de visualizações (sem deduplicar por empresa/dia:
    reflete literalmente as vezes que o perfil foi aberto)."""
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    creator = CreatorRepository.update(db, creator, {"profile_views": (creator.profile_views or 0) + 1})
    return _to_response(creator)


@router.post("/{creator_id}/rating", response_model=CreatorResponse)
def rate_creator(
    creator_id: str,
    data: RatingRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Só permite avaliar depois de uma parceria de verdade (proposta aceita)
    entre a empresa e o creator — evita nota avulsa sem nenhuma relação real."""
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if not ProposalRepository.has_accepted_between(db, company.id, creator_id):
        raise HTTPException(
            status_code=403,
            detail="Só é possível avaliar creators com quem você já fechou uma parceria.",
        )

    RatingRepository.upsert(db, company.id, creator_id, data.score)
    creator = CreatorRepository.update(db, creator, {"rating": RatingRepository.average_for_creator(db, creator_id)})
    return _to_response(creator)


@router.get("/{creator_id}/rating/me", response_model=RatingResponse)
def get_my_rating(
    creator_id: str,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    rating = RatingRepository.get_by_company_and_creator(db, company.id, creator_id)
    return RatingResponse(score=rating.score if rating else None)


@router.get("/{creator_id}", response_model=CreatorResponse)
def get_creator(creator_id: str, db: Session = Depends(get_db)):
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    return _to_response(creator)
