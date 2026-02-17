# Arquivo: schemas/company.py (ou onde você guarda os schemas)
from pydantic import BaseModel

# Adicione esta classe
class PaymentIntentRequest(BaseModel):
    amount_euros: float
    restaurant_id: int