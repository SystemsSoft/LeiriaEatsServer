# Arquivo: services/s3_service.py
import boto3
import mimetypes
import os
import re
import uuid
from botocore.exceptions import NoCredentialsError

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("AWS_S3_BUCKET", "koma-repo")
REGION_NAME = os.getenv("AWS_REGION", "us-east-2")


def nome_unico_de_arquivo(filename: str) -> str:
    """Nome único para o objeto no S3: 12 hex aleatórios + nome original higienizado + extensão.

    O upload usava o nome ORIGINAL do arquivo como chave (`Restaurants/{filename}`). Dois cadastros que
    subiam um arquivo com o mesmo nome — "images.jpeg" é o nome padrão de download do Google — caíam na
    MESMA chave, e o segundo upload sobrescrevia a imagem do primeiro: em 24/09/2026 a Mexicana passou a
    exibir o logo da Domino's, e os dois cadastros ficaram apontando para o mesmo arquivo. Também tira
    espaços e caracteres especiais (as URLs antigas tinham espaço, ex.: "pizza logo 1.jpg")."""
    base, ext = os.path.splitext(os.path.basename(filename or ""))
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")[:40] or "imagem"
    ext = ext.lower() if re.fullmatch(r"\.[a-z0-9]{1,5}", ext.lower()) else ".jpg"
    return f"{uuid.uuid4().hex[:12]}-{base}{ext}"


# ALTERAÇÃO: Adicionado parâmetro 'folder' com valor padrão
def upload_file_to_s3(file_obj, filename: str, folder: str = "Restaurants") -> str:
    client_config = {"region_name": REGION_NAME}
    if AWS_ACCESS_KEY and AWS_SECRET_KEY:
        client_config.update(
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
        )

    # Sem variáveis de ambiente, o boto3 usa a cadeia padrão de credenciais,
    # incluindo a IAM Role atribuída à instância de produção.
    s3_client = boto3.client("s3", **client_config)

    try:
        # Chave ÚNICA por upload (ver nome_unico_de_arquivo) — nunca sobrescreve a imagem de outro cadastro.
        key = f"{folder}/{nome_unico_de_arquivo(filename)}"

        tipo, _ = mimetypes.guess_type(key)
        s3_client.upload_fileobj(
            file_obj,
            BUCKET_NAME,
            key,
            ExtraArgs={'ContentType': tipo if tipo and tipo.startswith("image/") else 'image/jpeg'}
        )
        # -------------------------------------

        url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{key}"
        return url

    except NoCredentialsError:
        print("Erro: Credenciais AWS não encontradas")
        return None
    except Exception as e:
        print(f"Erro no upload S3: {e}")
        return None
