# Arquivo: conectaai/api/routes/company_routes.py
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.core.uploads import save_avatar_image
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.schemas.company import CompanyResponse, CompanyUpdateRequest, company_to_response as _to_response

router = APIRouter(prefix="/companies", tags=["Empresas"])


@router.get("/me", response_model=CompanyResponse)
def get_my_company(
    current_user: CurrentUser = Depends(require_role("company")), db: Session = Depends(get_db)
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return _to_response(company)


@router.put("/me", response_model=CompanyResponse)
def update_my_company(
    data: CompanyUpdateRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return _to_response(CompanyRepository.update(db, company, data.dict(exclude_unset=True)))


@router.post("/me/avatar", response_model=CompanyResponse)
async def upload_my_avatar(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    url = await save_avatar_image(file)
    return _to_response(CompanyRepository.update(db, company, {"avatar_url": url}))


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: str, db: Session = Depends(get_db)):
    company = CompanyRepository.get_by_id(db, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return _to_response(company)
