from fastapi import APIRouter, HTTPException
from fastapi.params import Depends
from sqlalchemy.orm import Session
from core.database import get_db
from repositories.restaurant_repo import RestaurantRepository
from schemas.company import CompanyResponse, CompanyCreateRequest
from schemas.models import UserRequest, SearchResponse
from services.ai_service import AIService
from fastapi import UploadFile, File, HTTPException
from services.s3_service import upload_file_to_s3


router = APIRouter()

@router.post("/chat", response_model=SearchResponse)
def semantic_search(request: UserRequest):
    # O Router não pensa, ele apenas delega para o AIService
    user_query = request.text.strip()
    return AIService.process_search(user_query)


@router.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    print(f"📸 Recebendo imagem para upload: {file.filename}")

    # Envia para o S3 usando nosso serviço
    image_url = upload_file_to_s3(file.file, file.filename)

    if not image_url:
        raise HTTPException(status_code=500, detail="Falha ao fazer upload da imagem")

    return {"url": image_url}


@router.post("/company", response_model=CompanyResponse, status_code=201)
def register_company(
    company_data: CompanyCreateRequest, # O FastAPI valida o JSON de entrada aqui
    db: Session = Depends(get_db)
):
    """
    Recebe dados básicos da empresa (nome, telefone, endereço) e salva no banco.
    Retorna o objeto criado com o ID gerado.
    """
    print(f"🏢 Recebendo cadastro de empresa: {company_data.name}")

    try:
        new_company = RestaurantRepository.create_company(db, company_data)
        print(f"✅ Empresa criada com ID: {new_company.id}")
        # O FastAPI converte automaticamente o modelo do banco (RestaurantDB)
        # para o schema de resposta (CompanyResponse) graças ao `from_attributes = True`
        return new_company
    except Exception as e:
        print(f"❌ Erro ao criar empresa: {e}")
        # Em um caso real, trataríamos erros específicos (ex: banco fora do ar)
        raise HTTPException(status_code=500, detail="Erro interno ao salvar empresa.")

