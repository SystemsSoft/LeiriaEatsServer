# Arquivo: schemas/company.py
from pydantic import BaseModel
from typing import Optional

from schemas.models import Product
from schemas.product import ProductCreateRequest


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

class CompanyUpdateRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    image_url: Optional[str] = None

# RESPONSE: O que o app recebe de volta
class CompanyResponse(BaseModel):
    id: int
    name: str
    category: str
    phone: str
    address: str
    image_url: Optional[str]
    products: list[Product]

    # --- NOVOS CAMPOS ---
    login: str
    license: str

    # OBS: NUNCA retornamos o campo 'password' aqui por segurança

    class Config:
        from_attributes = True