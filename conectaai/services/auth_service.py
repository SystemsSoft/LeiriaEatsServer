# Arquivo: conectaai/services/auth_service.py
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from conectaai.core.security import create_access_token, verify_password
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.user_repo import UserRepository
from conectaai.schemas.auth import AuthResponse, AuthUser, LoginRequest, RegisterRequest


def _build_auth_response(db: Session, user) -> AuthResponse:
    company_id = None
    creator_id = None
    if user.role == "company":
        company = CompanyRepository.get_by_user_id(db, user.id)
        company_id = company.id if company else None
    elif user.role == "creator":
        creator = CreatorRepository.get_by_user_id(db, user.id)
        creator_id = creator.id if creator else None

    token = create_access_token(user_id=user.id, role=user.role)
    return AuthResponse(
        token=token,
        user=AuthUser(
            id=user.id,
            role=user.role,
            name=user.name,
            email=user.email,
            company_id=company_id,
            creator_id=creator_id,
        ),
    )


def register(db: Session, data: RegisterRequest) -> AuthResponse:
    if data.role not in ("company", "creator"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role deve ser 'company' ou 'creator'")

    if UserRepository.get_by_email(db, data.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    user = UserRepository.create(db, role=data.role, name=data.name, email=data.email, password=data.password)
    return _build_auth_response(db, user)


def login(db: Session, data: LoginRequest) -> AuthResponse:
    user = UserRepository.get_by_email(db, data.email)
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")

    return _build_auth_response(db, user)
