from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from core.database import get_db
from schemas.models import UserRequest, SearchResponse
from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService
from services.session_service import SessionManager

router = APIRouter()


# Schema para chat com IA conversacional
class ChatRequest(BaseModel):
    message: str
    restaurant_gid: Optional[str] = None
    session_id: Optional[str] = None


class ProductItem(BaseModel):
    id: int
    name: str
    price: float
    image_url: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[float] = None
    is_popular: Optional[bool] = False
    is_available: Optional[bool] = True
    serves_people: Optional[int] = None
    portion_size: Optional[str] = None
    preparation_time_minutes: Optional[int] = None
    preparation_time: Optional[str] = None
    ingredients: Optional[str] = None
    allergens: Optional[str] = None
    dietary_tags: Optional[str] = None
    spice_level: Optional[str] = None
    calories: Optional[int] = None
    recommended_for: Optional[str] = None
    search_tags: Optional[str] = None
    restaurant_gid: Optional[str] = None
    quantity: int = 0


class ChatResponse(BaseModel):
    response: str
    products: List[ProductItem]
    intent: str
    session_id: str
    cart: dict
    order_confirmed: bool = False


# Rota antiga (mantida para compatibilidade)
@router.post("/chat", response_model=SearchResponse)
def semantic_search(request: UserRequest, db: Session = Depends(get_db)):
    user_query = request.text.strip()
    return AIService.process_search(user_query, db=db)


# Nova rota com IA conversacional (Gemini) + sessão
@router.post("/chat/sales", response_model=ChatResponse)
def chat_sales(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Chat conversacional com agente de vendas inteligente
    Usa E5 (busca semântica) + Gemini (conversação) + Sessão (carrinho e histórico)
    """
    result = HybridAIService.process_sales_chat(
        user_message=request.message,
        restaurant_gid=request.restaurant_gid,
        cart=[],
        db=db,
        session_id=request.session_id
    )

    return ChatResponse(
        response=result["response"],
        products=result["products"],
        intent=result["intent"],
        session_id=result["session_id"],
        cart=result["cart"],
        order_confirmed=result.get("order_confirmed", False)
    )


# Endpoint para consultar o carrinho da sessão
@router.get("/chat/cart/{session_id}")
def get_cart(session_id: str):
    """Retorna o carrinho atual de uma sessão"""
    session = SessionManager.get(session_id)
    if not session:
        return {"error": "Sessão não encontrada ou expirada", "cart": None}
    return {"session_id": session_id, "cart": session.get_cart_summary()}


# Endpoint para limpar o carrinho
@router.delete("/chat/cart/{session_id}")
def clear_cart(session_id: str):
    """Limpa o carrinho de uma sessão"""
    session = SessionManager.get(session_id)
    if not session:
        return {"error": "Sessão não encontrada ou expirada"}
    session.clear_cart()
    SessionManager.save(session)
    return {"message": "Carrinho limpo com sucesso", "session_id": session_id}


# Endpoint para encerrar sessão
@router.delete("/chat/session/{session_id}")
def end_session(session_id: str):
    """Encerra uma sessão (ex: após confirmar pedido)"""
    SessionManager.delete(session_id)
    return {"message": "Sessão encerrada", "session_id": session_id}


# Rota de status dos modelos
@router.get("/chat/status")
def chat_status():
    """Status dos modelos + uso Gemini + sessões ativas"""
    status = HybridAIService.get_status()
    session_stats = SessionManager.get_stats()

    response = {
        "status": "ready" if status["system_ready"] else "loading",
        "e5_status": "loaded" if status["e5_loaded"] else "not_loaded",
        "gemini_status": "ready" if status["gemini_ready"] else "not_ready",
        "active_sessions": session_stats["active_sessions"],
        "details": status
    }

    if status.get("gemini_usage"):
        usage = status["gemini_usage"]
        response["gemini_daily_usage"] = {
            "used": usage["requests_today"],
            "limit": usage["limit_daily"],
            "remaining": usage["remaining_today"],
            "percentage": f"{usage['percentage_used']:.1f}%"
        }

    if status.get("gemini_cache"):
        response["cache_info"] = status["gemini_cache"]

    return response
