# Arquivo: conectaai/api/routes/opportunity_routes.py
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.repositories.opportunity_repo import OpportunityRepository
from conectaai.schemas.opportunity import OpportunityResponse

router = APIRouter(prefix="/opportunities", tags=["Oportunidades"])


@router.get("", response_model=List[OpportunityResponse])
def list_opportunities(db: Session = Depends(get_db)):
    return OpportunityRepository.get_all(db)
