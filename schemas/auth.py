# Arquivo: schemas/auth.py
from pydantic import BaseModel

class LoginRequest(BaseModel):
    login: str
    password: str

class LoginResponse(BaseModel):
    authenticated: bool
    restaurant_id: int
    name: str
    message: str