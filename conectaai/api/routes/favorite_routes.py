# Arquivo: conectaai/api/routes/favorite_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user
from conectaai.repositories.favorite_repo import FavoriteRepository
from conectaai.schemas.favorite import FavoriteCreateRequest, FavoriteResponse

router = APIRouter(prefix="/favorites", tags=["Favoritos"])


@router.get("", response_model=List[FavoriteResponse])
def list_favorites(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return FavoriteRepository.get_all_for_user(db, current_user.user_id)


@router.post("", response_model=FavoriteResponse, status_code=201)
def add_favorite(
    data: FavoriteCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = FavoriteRepository.find(db, current_user.user_id, data.target_type, data.target_id)
    if existing:
        return existing
    return FavoriteRepository.create(db, current_user.user_id, data.target_type, data.target_id)


@router.delete("/{target_type}/{target_id}", status_code=204)
def remove_favorite(
    target_type: str,
    target_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    favorite = FavoriteRepository.find(db, current_user.user_id, target_type, target_id)
    if not favorite:
        raise HTTPException(status_code=404, detail="Favorito não encontrado")
    FavoriteRepository.delete(db, favorite)
