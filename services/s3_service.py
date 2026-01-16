# Arquivo: services/s3_service.py
import boto3
from botocore.exceptions import NoCredentialsError

# Configure suas chaves aqui (ou use variáveis de ambiente .env em produção)
AWS_ACCESS_KEY = "AKIAWOUMWSBT2TD4ZV7E"
AWS_SECRET_KEY = "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"
BUCKET_NAME = "leiria-eats-repo"
REGION_NAME = "us-east-2"


def upload_file_to_s3(file_obj, filename: str) -> str:
    """
    Recebe o arquivo em memória e envia para o S3.
    Retorna a URL pública.
    """
    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=REGION_NAME
    )

    try:
        # Caminho onde vai salvar: Restaurants/nome_do_arquivo
        key = f"Restaurants/{filename}"

        # Faz o upload
        s3_client.upload_fileobj(
            file_obj,
            BUCKET_NAME,
            key,
            ExtraArgs={'ACL': 'public-read', 'ContentType': 'image/jpeg'}  # Deixa público
        )

        # Monta a URL final
        url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{key}"
        return url

    except NoCredentialsError:
        print("Erro: Credenciais AWS não encontradas")
        return None
    except Exception as e:
        print(f"Erro no upload S3: {e}")
        return None