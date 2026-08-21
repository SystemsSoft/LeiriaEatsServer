
import os
import stripe
from dotenv import load_dotenv

# Carrega .env
load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PI_ID = "pi_3U6zMnEdmjsrvrGs05kD03sC"

try:
    print(f"🔍 Buscando PaymentIntent: {PI_ID}")
    pi = stripe.PaymentIntent.retrieve(PI_ID)
    print(f"✅ Status: {pi.status}")
    print(f"📋 Metadados: {pi.metadata}")
    print(f"💰 Valor: {pi.amount} {pi.currency}")
    
    if pi.status == "succeeded":
        print("💡 O pagamento FOI realizado com sucesso no Stripe.")
        order_id = pi.metadata.get("order_id")
        print(f"🛒 Order ID mapeado: {order_id}")
    else:
        print(f"⚠️ O pagamento ainda está em estado: {pi.status}")

except Exception as e:
    print(f"❌ Erro ao buscar dados na Stripe: {e}")
