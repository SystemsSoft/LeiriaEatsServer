from fastapi import APIRouter, UploadFile, File, HTTPException
from services.s3_service import upload_file_to_s3

router = APIRouter()


@router.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    print(f"📸 Recebendo imagem para upload: {file.filename}")

    image_url = upload_file_to_s3(file.file, file.filename)

    if not image_url:
        raise HTTPException(status_code=500, detail="Falha ao fazer upload da imagem")

    return {"url": image_url}