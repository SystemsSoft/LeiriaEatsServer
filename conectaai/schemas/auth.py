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
