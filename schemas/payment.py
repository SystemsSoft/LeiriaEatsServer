# Arquivo: schemas/payment.py
from pydantic import BaseModel

class PaymentIntentRequest(BaseModel):
    amount_euros: float
    restaurant_gid: str
