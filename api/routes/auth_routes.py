# Arquivo: api/routes/auth_routes.py
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from starlette import status

from core.database import get_db
from repositories.restaurant_repo import RestaurantRepository
from schemas.auth import LoginRequest, LoginResponse
from schemas.company import CompanyCreateRequest

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    print(f"🔐 Tentativa de login: {request.login}")

    user = RestaurantRepository.check_credentials(db, request.login, request.password)

    if not user:
        raise HTTPException(status_code=401, detail="Login ou senha incorretos")


    if user.license is None or user.license.upper() != "ATIVO":
        print(f"🚫 Acesso negado para {user.name}: Licença '{user.license}'")

        return {
            "authenticated": False,
            "restaurant_id": 0,
            "name": "",
            "message": "Sua licença não está ATIVA. Contate o suporte."
        }

    print(f"✅ Acesso permitido para: {user.name}")
    return {
        "authenticated": True,
        "restaurant_id": user.id,
        "name": user.name,
        "message": "Login realizado com sucesso"
    }


@router.post("/company", status_code=status.HTTP_201_CREATED)
@router.post("/register", status_code=status.HTTP_201_CREATED)
def create_company(company: CompanyCreateRequest, db: Session = Depends(get_db)):
    print(f"🏭 Criando nova empresa: {company.name}")

    # Verifica duplicidade (opcional, mas bom ter)
    existing = RestaurantRepository.check_credentials(db, company.login, "dummy")
    # Nota: O check_credentials retorna None se a senha não bater, então não serve bem para checar existência pelo login.
    # O ideal seria um método 'find_by_login', mas vamos confiar no try/catch do banco (unique constraint)

    try:
        new_restaurant = RestaurantRepository.create_company(db, company)
        return {
            "message": "Restaurante criado com sucesso!",
            "id": new_restaurant.id,
            "login": new_restaurant.login
        }
    except Exception as e:
        print(f"❌ Erro ao criar empresa: {e}")
        # Retorna erro 400 se já existir o login
        raise HTTPException(status_code=400, detail="Erro ao criar empresa. Login pode já existir.")