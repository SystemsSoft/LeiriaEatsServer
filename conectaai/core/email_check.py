# Arquivo: conectaai/core/email_check.py
#
# Confere se o DOMÍNIO de um e-mail existe e está configurado para receber
# e-mails (registro MX, com fallback para A/AAAA — mesma regra que qualquer
# servidor de e-mail usa para decidir se entrega). Pega erros de digitação e
# domínios inventados (ex.: "gmial.com"). NÃO confirma se a caixa específica
# existe — isso exigiria um serviço pago de verificação de e-mail (fora do
# escopo aqui, sem credencial disponível).
from typing import Optional, Tuple

from email_validator import EmailNotValidError, validate_email


def check_email_deliverable(email: str) -> Tuple[bool, Optional[str]]:
    try:
        validate_email(email, check_deliverability=True)
        return True, None
    except EmailNotValidError as e:
        return False, str(e)
