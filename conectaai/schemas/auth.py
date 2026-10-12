# Arquivo: conectaai/schemas/auth.py
from typing import Optional

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    role: str  # "company" | "creator"
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EmailCheckResponse(BaseModel):
    deliverable: bool
    reason: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    id_token: str
    # Só é usado quando ainda não existe conta com o email do Google — define
    # o papel da conta nova. Ausente = só tenta logar numa conta já existente.
    role: Optional[str] = None
    name: Optional[str] = None


class AuthUser(BaseModel):
    id: str
    role: str
    name: str
    email: str
    company_id: Optional[str] = None
    creator_id: Optional[str] = None


class AuthResponse(BaseModel):
    token: str
    user: AuthUser
