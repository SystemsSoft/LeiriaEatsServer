# Arquivo: services/s3_service.py
import boto3
import os
from botocore.exceptions import NoCredentialsError

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("AWS_S3_BUCKET", "koma-repo")
REGION_NAME = os.getenv("AWS_REGION", "us-east-2")


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
        key = f"{folder}/{filename}"


        s3_client.upload_fileobj(
            file_obj,
            BUCKET_NAME,
            key,
            ExtraArgs={'ContentType': 'image/jpeg'}
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
