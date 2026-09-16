# Arquivo: conectaai/api/routes/auth_routes.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.schemas.auth import AuthResponse, LoginRequest, RegisterRequest
from conectaai.services import auth_service

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register(db, data)


@router.post("/login", response_model=AuthResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login(db, data)
