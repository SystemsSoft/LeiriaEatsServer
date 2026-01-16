# Arquivo: schemas/company.py
from pydantic import BaseModel
from typing import Optional


# REQUEST: O que o app envia para criar a empresa
class CompanyCreateRequest(BaseModel):
    name: str
    category: str
    phone: str
    address: str
    image_url: Optional[str] = None

    login: str
    password: str
    license: str


# RESPONSE: O que o app recebe de volta
class CompanyResponse(BaseModel):
    id: int
    name: str
    category: str
    phone: str
    address: str
    image_url: Optional[str]

    # --- NOVOS CAMPOS ---
    login: str
    license: str

    # OBS: NUNCA retornamos o campo 'password' aqui por segurança

    class Config:
        from_attributes = True