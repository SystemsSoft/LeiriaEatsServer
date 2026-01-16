# Arquivo: schemas/company.py
from pydantic import BaseModel
from typing import Optional

# --- MODELO BASE (Dados comuns) ---
class CompanyBase(BaseModel):
    name: str
    phone: str
    address: str

# --- MODELO PARA RECEBER DADOS (Input / Create) ---
# Esse é exatamente o formato que o Flutter está enviando agora.
class CompanyCreateRequest(CompanyBase):
    # Como o Flutter de gestão não manda categoria nem imagem por enquanto,
    # vamos definir valores padrão aqui para o banco não reclamar.
    category: str = "Geral"
    # Uma imagem placeholder de restaurante
    image_url: str = "https://placehold.co/600x400/e94560/ffffff?text=Leiria+Eats"
    rating: float = 5.0

# --- MODELO PARA DEVOLVER DADOS (Output / Response) ---
# Usado quando a API devolve os dados da empresa para o front
class CompanyResponse(CompanyBase):
    id: int
    category: str
    image_url: str
    rating: float

    # Isso permite que o Pydantic converta automaticamente o objeto do SQLAlchemy (RestaurantDB)
    # para este formato JSON.
    class Config:
        from_attributes = True