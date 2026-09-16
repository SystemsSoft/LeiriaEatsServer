# Arquivo: conectaai/api/routes/creator_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.schemas.creator import CreatorResponse, CreatorUpdateRequest, creator_to_response as _to_response

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


@router.get("/{creator_id}", response_model=CreatorResponse)
def get_creator(creator_id: str, db: Session = Depends(get_db)):
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    return _to_response(creator)
