# Arquivo: api/routes/auth_routes.py
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from repositories.restaurant_repo import RestaurantRepository
from schemas.auth import LoginRequest, LoginResponse

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    print(f"🔐 Tentativa de login: {request.login}")

    user = RestaurantRepository.check_credentials(db, request.login, request.password)

    if not user:
        # Retorna erro 401 (Não Autorizado) se falhar
        raise HTTPException(status_code=401, detail="Login ou senha incorretos")

    print(f"✅ Acesso permitido para: {user.name}")
    return {
        "authenticated": True,
        "restaurant_id": user.id,
        "name": user.name,
        "message": "Login realizado com sucesso"
    }