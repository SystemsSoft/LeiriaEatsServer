# Arquivo: schemas/auth.py
from pydantic import BaseModel

class LoginRequest(BaseModel):
    login: str
    password: str

class LoginResponse(BaseModel):
    authenticated: bool
    restaurant_id: int
    gid: str
    name: str
    message: str
    # True quando o restaurante ainda não concluiu o cadastro na Stripe: não entra no painel
    # (licença por ativar), mas a gestão oferece "Concluir cadastro na Stripe" com o `gid`.
    stripe_pending: bool = False