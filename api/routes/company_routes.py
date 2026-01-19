from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyResponse, CompanyCreateRequest

router = APIRouter()


@router.get("/company/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    db_company = RestaurantRepository.get_by_id(db, company_id)
    if db_company is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return db_company


@router.post("/company", response_model=CompanyResponse, status_code=201)
def register_company(
    company_data: CompanyCreateRequest,
    db: Session = Depends(get_db)
):
    print(f"🏢 Recebendo cadastro de empresa: {company_data.name}")
    try:
        new_company = RestaurantRepository.create_company(db, company_data)
        print(f"✅ Empresa criada com ID: {new_company.id}")
        return new_company
    except Exception as e:
        print(f"❌ Erro ao criar empresa: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao salvar empresa.")