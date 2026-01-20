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

@router.post("/search", response_model=SearchResponse)
def search_restaurants(request: SearchRequest, db: Session = Depends(get_db)):
    """
    Rota utilizada pelo App KMM para buscar restaurantes.
    O 'db' é injetado automaticamente e passado para o AIService.
    """
    response = AIService.process_search(request.query, db)
    return response

# Rota opcional para forçar atualização (ex: chamar após cadastrar produto)
@router.post("/search/reload")
def reload_ai_index(db: Session = Depends(get_db)):
    AIService.reload_data(db)
    return {"message": "Índice de IA atualizado com sucesso!"}