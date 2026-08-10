from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from core.database import get_db
from schemas.models import UserRequest, SearchResponse
from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService

router = APIRouter()


# Schema para chat com IA conversacional
class ChatRequest(BaseModel):
    message: str
    restaurant_id: Optional[int] = None
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str  # Resposta conversacional do Phi-3
    products: List[dict]  # Produtos encontrados pelo E5
    intent: str  # Intenção detectada


# Rota antiga (mantida para compatibilidade)
@router.post("/chat", response_model=SearchResponse)
def semantic_search(request: UserRequest, db: Session = Depends(get_db)):
    """
    Busca semântica tradicional (apenas E5)
    Mantida para compatibilidade com código existente
    """
    user_query = request.text.strip()
    return AIService.process_search(user_query, db=db)


# Nova rota com IA conversacional
@router.post("/chat/sales", response_model=ChatResponse)
def chat_sales(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Chat conversacional com agente de vendas
    Usa E5 (busca semântica) + Phi-3 (conversação consultiva)

    Exemplo de uso:
    POST /chat/sales
    {
        "message": "Quero uma pizza",
        "restaurant_id": 123,
        "session_id": "abc-123"
    }
    """
    # TODO: Implementar gestão de carrinho por sessão
    # Por enquanto, carrinho vazio
    cart = []

    # Pipeline: E5 busca + Phi-3 conversa
    result = HybridAIService.process_sales_chat(
        user_message=request.message,
        restaurant_id=request.restaurant_id,
        cart=cart,
        db=db
    )

    return ChatResponse(
        response=result["response"],
        products=result["products"],
        intent=result["intent"]
    )


# Rota de status dos modelos
@router.get("/chat/status")
def chat_status():
    """
    Verifica se os modelos de IA estão carregados e prontos
    """
    status = HybridAIService.get_status()
    return {
        "status": "ready" if status["system_ready"] else "loading",
        "details": status
    }
