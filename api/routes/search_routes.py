# Arquivo: api/routes/search_routes.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from services.ai_service import AIService
from schemas.models import SearchResponse
from pydantic import BaseModel

router = APIRouter()

class SearchRequest(BaseModel):
    query: str
    scope: str = "auto"  # Valores possíveis: "auto", "product", "restaurant", "both"

@router.post("/search", response_model=SearchResponse)
def search_restaurants(request: SearchRequest, db: Session = Depends(get_db)):
    """
    Rota utilizada pelo App KMM para buscar restaurantes e produtos.
    O 'db' é injetado automaticamente e passado para o AIService.

    Args:
        request: Contém query (texto de busca) e scope (tipo de busca)
            - scope="auto": Detecta automaticamente (padrão: produtos)
            - scope="product": Busca apenas produtos
            - scope="restaurant": Busca apenas restaurantes
            - scope="both": Busca produtos e restaurantes
    """
    response = AIService.process_search(request.query, db, request.scope)
    return response

# Rota opcional para forçar atualização (ex: chamar após cadastrar produto)
@router.post("/search/reload")
def reload_ai_index(db: Session = Depends(get_db)):
    AIService.reload_data(db)
    return {"message": "Índice de IA atualizado com sucesso!"}