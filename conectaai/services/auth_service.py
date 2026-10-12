# Arquivo: conectaai/services/auth_service.py
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from conectaai.core.email_check import check_email_deliverable
from conectaai.core.firebase import verify_google_id_token
from conectaai.core.security import create_access_token, verify_password
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.user_repo import UserRepository
from conectaai.schemas.auth import AuthResponse, AuthUser, EmailCheckResponse, GoogleAuthRequest, LoginRequest, RegisterRequest


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


def check_email(email: str) -> EmailCheckResponse:
    deliverable, reason = check_email_deliverable(email)
    return EmailCheckResponse(deliverable=deliverable, reason=reason)


def register(db: Session, data: RegisterRequest) -> AuthResponse:
    if data.role not in ("company", "creator"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role deve ser 'company' ou 'creator'")

    if UserRepository.get_by_email(db, data.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    deliverable, reason = check_email_deliverable(data.email)
    if not deliverable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Esse domínio de e-mail não existe ou não recebe e-mails ({reason}).",
        )

    user = UserRepository.create(db, role=data.role, name=data.name, email=data.email, password=data.password)
    return _build_auth_response(db, user)


def login(db: Session, data: LoginRequest) -> AuthResponse:
    user = UserRepository.get_by_email(db, data.email)
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")

    return _build_auth_response(db, user)


def google_auth(db: Session, data: GoogleAuthRequest) -> AuthResponse:
    claims = verify_google_id_token(data.id_token)
    email = claims["email"]

    user = UserRepository.get_by_email(db, email)
    if user:
        return _build_auth_response(db, user)

    if not data.role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada. Crie uma conta primeiro.",
        )
    if data.role not in ("company", "creator"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role deve ser 'company' ou 'creator'")

    name = data.name or claims.get("name") or email.split("@")[0]
    user = UserRepository.create(db, role=data.role, name=name, email=email)
    return _build_auth_response(db, user)
