# Arquivo: conectaai/core/uploads.py
import os
import uuid

from fastapi import HTTPException, UploadFile

from conectaai.core.config import settings

# Validação pela extensão do nome do arquivo, não pelo header Content-Type:
# esse header nem sempre chega confiável em todos os browsers/plataformas do
# cliente Flutter Web, enquanto o nome do arquivo (com extensão) é sempre
# enviado pelo seletor de imagem.
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


async def save_avatar_image(file: UploadFile) -> str:
    """Salva a imagem de avatar em disco (sob CONECTAAI_UPLOAD_DIR/avatars) e
    devolve a URL pública completa. Nome do arquivo é sempre gerado (uuid4),
    nunca reaproveita o nome original, para não colidir com o avatar de outro
    usuário nem expor o nome do arquivo enviado pelo cliente."""
    original_name = file.filename or ""
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de imagem não suportado. Use JPEG, PNG ou WebP.")

    content = await file.read()
    if len(content) > _MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Imagem muito grande (máximo 5MB).")

    avatars_dir = os.path.join(settings.UPLOAD_DIR, "avatars")
    os.makedirs(avatars_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(avatars_dir, filename), "wb") as f:
        f.write(content)

    return f"{settings.PUBLIC_BASE_URL}/uploads/avatars/{filename}"
