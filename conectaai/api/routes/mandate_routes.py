# Arquivo: conectaai/api/routes/mandate_routes.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from conectaai.api.deps import self_id
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user
from conectaai.repositories.mandate_repo import MandateRepository
from conectaai.schemas.mandate import MandateCreateRequest, MandateResponse

router = APIRouter(prefix="/mandates", tags=["Mandatos"])


@router.post("", response_model=MandateResponse, status_code=201)
def create_mandate(
    data: MandateCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    owner_id = self_id(db, current_user)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Perfil não encontrado para este usuário")
    payload = data.dict()
    payload["deliverables"] = [d for d in payload.get("deliverables") or []]
    return MandateRepository.create(db, owner_type=current_user.role, owner_id=owner_id, data=payload)


@router.get("/me", response_model=Optional[MandateResponse])
def get_my_mandate(
    campaign_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_id = self_id(db, current_user)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Perfil não encontrado para este usuário")
    return MandateRepository.get_active_for_owner(db, owner_type=current_user.role, owner_id=owner_id, campaign_id=campaign_id)


@router.get("", response_model=List[MandateResponse])
def list_my_mandates(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    owner_id = self_id(db, current_user)
    if not owner_id:
        return []
    return MandateRepository.get_all_for_owner(db, owner_type=current_user.role, owner_id=owner_id)


@router.get("/{mandate_id}", response_model=MandateResponse)
def get_mandate(mandate_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    mandate = MandateRepository.get_by_id(db, mandate_id)
    if not mandate:
        raise HTTPException(status_code=404, detail="Mandato não encontrado")
    if mandate.owner_id != self_id(db, current_user):
        raise HTTPException(status_code=403, detail="Esse mandato não é seu")
    return mandate


@router.delete("/{mandate_id}", status_code=204)
def deactivate_mandate(mandate_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    mandate = MandateRepository.get_by_id(db, mandate_id)
    if not mandate:
        raise HTTPException(status_code=404, detail="Mandato não encontrado")
    if mandate.owner_id != self_id(db, current_user):
        raise HTTPException(status_code=403, detail="Esse mandato não é seu")
    MandateRepository.deactivate(db, mandate)
