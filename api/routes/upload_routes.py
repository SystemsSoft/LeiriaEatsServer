# Arquivo: api/routes/upload_routes.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from services.s3_service import upload_file_to_s3

router = APIRouter()


@router.post("/upload/image")
async def upload_image(
        file: UploadFile = File(...),
        # NOVO: Recebe o tipo de upload (padrão é 'company')
        type: str = Query("company", enum=["company", "product"])
):
    print(f"📸 Recebendo imagem ({type}) para upload: {file.filename}")

    # Define a pasta com base no tipo
    target_folder = "Restaurants"
    if type == "product":
        target_folder = "Cardapio"

    # Passa a pasta correta para o serviço
    image_url = upload_file_to_s3(file.file, file.filename, folder=target_folder)

    if not image_url:
        raise HTTPException(status_code=500, detail="Falha ao fazer upload da imagem")

    return {"url": image_url}