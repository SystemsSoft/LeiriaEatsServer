# Arquivo: conectaai/api/routes/auth_routes.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.schemas.auth import AuthResponse, EmailCheckResponse, GoogleAuthRequest, LoginRequest, RegisterRequest
from conectaai.services import auth_service

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.get("/check-email", response_model=EmailCheckResponse)
def check_email(email: str):
    return auth_service.check_email(email)


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register(db, data)


@router.post("/login", response_model=AuthResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login(db, data)


@router.post("/google", response_model=AuthResponse)
def google_auth(data: GoogleAuthRequest, db: Session = Depends(get_db)):
    return auth_service.google_auth(db, data)
