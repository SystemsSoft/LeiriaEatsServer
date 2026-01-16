from fastapi import APIRouter
from schemas.models import UserRequest, SearchResponse
from services.ai_service import AIService

router = APIRouter()

@router.post("/chat", response_model=SearchResponse)
def semantic_search(request: UserRequest):
    user_query = request.text.strip()
    return AIService.process_search(user_query)