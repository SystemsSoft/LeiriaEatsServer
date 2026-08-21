# Arquivo: api/routes/order_routes.py
from typing import List, Dict, Any, Optional
import math

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from core import config
from core.database import get_db, SessionLocal
from core.sql_models import OrderDB, OrderItemDB, ProductDB, RestaurantDB, SavedPaymentMethodDB, ProductRatingDB, DeliveryZoneDB, DriverDB, SubOrderDB
from repositories.restaurant_repo import RestaurantRepository
from schemas.models import OrderRequest, OrderResponse, OrderStatusUpdate, OrderStatusResponse, RatingRequest, DeliveryFeeRequest, SubOrderResponse, OrderItemResponse

router = APIRouter()

# Garante que chamadas Stripe nesse módulo usem a chave secreta do backend
stripe.api_key = config.settings.STRIPE_API_KEY


# ─── Utilitário: Fórmula de Haversine ──────────────────────────────────────
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula a distância em km entre dois pontos geográficos (WGS-84)."""
    R = 6371.0  # Raio médio da Terra em km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ─── Helper: Resolve taxa de entrega ──────────────────────────────────────
def _resolve_delivery_fee(
    *,
    db: Session,
    restaurant: RestaurantDB,
    distance_km: float,
) -> dict:
    """
    Calcula e devolve a taxa de entrega correcta para a distância dada.

    • use_own_delivery=True  → usa as delivery_zones habilitadas do restaurante
                               (menor raio que abranja a distância).
    • use_own_delivery=False → escalões padrão da plataforma (até 6 km).

    Levanta HTTPException 400 se o endereço estiver fora da área de entrega.
    """
    if restaurant.use_own_delivery:
        zones = (
            db.query(DeliveryZoneDB)
            .filter(
                DeliveryZoneDB.restaurant_id == restaurant.id,
                DeliveryZoneDB.enabled == True,
            )
            .order_by(DeliveryZoneDB.radius_km.asc())
            .all()
        )

        matched_zone = next((z for z in zones if distance_km <= z.radius_km), None)

        if not matched_zone:
            max_radius = zones[-1].radius_km if zones else 0
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Endereço fora da área de entrega do restaurante. "
                    f"Distância calculada: {distance_km:.2f} km "
                    f"(raio máximo configurado: {max_radius:.2f} km)."
                ),
            )

        print(f"🚚 Entrega própria — Zona {matched_zone.zone}: {matched_zone.price} € (raio {matched_zone.radius_km} km)")
        return {
            "distance_km": round(distance_km, 2),
            "delivery_fee": matched_zone.price,
            "tier": matched_zone.zone,
            "zone_id": matched_zone.id,
            "use_own_delivery": True,
        }

    # ── Escalões padrão da plataforma ─────────────────────────────────────
    if distance_km <= 2.0:
        fee, tier = 1.99, 1
    elif distance_km <= 4.0:
        fee, tier = 2.99, 2
    elif distance_km <= 6.0:
        fee, tier = 3.99, 3
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Endereço fora da área de entrega. Distância calculada: {distance_km:.2f} km (máximo permitido: 6 km).",
        )

    return {
        "distance_km": round(distance_km, 2),
        "delivery_fee": fee,
        "tier": tier,
        "use_own_delivery": False,
    }


# ─── Endpoint: Calcular taxa de entrega ────────────────────────────────────
@router.post("/orders/delivery-fee")
def calculate_delivery_fee(payload: DeliveryFeeRequest, db: Session = Depends(get_db)):
    """
    Calcula a taxa de entrega com base na distância entre o cliente e o restaurante.

    Se o restaurante usar entrega própria (use_own_delivery=True), aplica o preço
    definido nas zonas de entrega (delivery_zones).
    Caso contrário, aplica os escalões padrão da plataforma.
    """
    restaurant = RestaurantRepository.get_by_gid(db, payload.restaurant_gid)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurante não encontrado.")

    distance_km = _haversine_km(
        payload.customer_latitude,
        payload.customer_longitude,
        payload.restaurant_latitude,
        payload.restaurant_longitude,
    )
    print(f"📍 Distância calculada: {distance_km:.2f} km")

    return _resolve_delivery_fee(db=db, restaurant=restaurant, distance_km=distance_km)


def get_commission_rate(plan: str | None, use_own_delivery: bool = False) -> float:
    """Retorna a taxa de comissão com base no plano do restaurante.
    - use_own_delivery=True → 15% (fixo, independente do plano)
    - ESSENCE              → 18%
    - SMART                → 21%
    """
    if use_own_delivery:
        return 0.15
    if plan and plan.upper() == "SMART":
        return 0.21
    return 0.18  # ESSENCE é o padrão


def _try_automatic_payment_with_saved_card(
    *,
    db: Session,
    new_order: OrderDB,
    saved_method: SavedPaymentMethodDB,
    restaurant: RestaurantDB,
    amount_cents: int,
    platform_fee: int,
):
    """
    Tenta cobrar off-session usando o último cartão salvo do usuário.
    Retorna um dict com dados do pagamento quando sucesso, ou None para fallback em Checkout.
    """
    if not saved_method:
        return None

    if not saved_method.stripe_customer_id or not saved_method.stripe_payment_method_id:
        return None

    try:
        # Garantir que o payment_method está anexado ao customer antes de cobrar
        try:
            stripe.PaymentMethod.attach(
                saved_method.stripe_payment_method_id,
                customer=saved_method.stripe_customer_id
            )
        except stripe.error.StripeError:
            # Já está anexado, ignora silenciosamente
            pass

        payment_intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="eur",
            customer=saved_method.stripe_customer_id,
            payment_method=saved_method.stripe_payment_method_id,
            off_session=True,
            confirm=True,
            application_fee_amount=platform_fee,
            transfer_data={"destination": restaurant.stripe_account_id},
            metadata={
                "order_id": str(new_order.id),
                "user_id": new_order.user_id,
                "payment_flow": "off_session_saved_card",
            },
        )

        new_order.payment_intent_id = payment_intent.id
        new_order.stripe_customer_id = saved_method.stripe_customer_id
        new_order.status = "Pendente"
        for sub in new_order.sub_orders:
            sub.status = "Pendente"
        db.commit()

        return {
            "url": None,
            "auto_paid": True,
            "order_id": new_order.id,
            "payment_intent_id": payment_intent.id,
            "status": new_order.status,
        }

    except stripe.error.CardError as e:
        # Falhas de autenticação/recusa caem para fluxo de Checkout com UI.
        print(f"⚠️ Falha no pagamento automático para pedido {new_order.id}: {str(e)}")
        return None
    except stripe.error.StripeError as e:
        print(f"⚠️ Erro Stripe no pagamento automático para pedido {new_order.id}: {str(e)}")
        return None


@router.post("/orders/initiate-checkout")
def initiate_order_and_create_checkout_session(order_data: OrderRequest, db: Session = Depends(get_db)):
    """
    Cria pedido Master com seus respectivos Sub-Pedidos por restaurante.
    - Suporta múltiplos restaurantes num único checkout.
    - Se houver cartão salvo e save_payment_method=true, tenta cobrança automática.
    """
    from ulid import ULID
    master_order_gid = order_data.gid if order_data.gid else str(ULID())
    
    # ── 1. Cliente Stripe ──────────────────────────────────────────────────
    # Para o SDK Nativo (PaymentSheet), precisamos SEMPRE de um Customer ID
    stripe_customer_id = None
    
    existing_saved_method = db.query(SavedPaymentMethodDB).filter(
        SavedPaymentMethodDB.user_id == order_data.user_id
    ).order_by(SavedPaymentMethodDB.id.desc()).first()

    if existing_saved_method:
        stripe_customer_id = existing_saved_method.stripe_customer_id
    else:
        try:
            customer = stripe.Customer.create(
                name=order_data.user_name,
                phone=order_data.user_phone,
                metadata={"user_id": order_data.user_id}
            )
            stripe_customer_id = customer.id
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao criar cliente Stripe: {str(e)}")

    # ── 2. Criar Master Order ───────────────────────────────────────────────
    new_master_order = OrderDB(
        gid=master_order_gid,
        customer_name=order_data.user_name,
        delivery_address=order_data.user_address,
        delivery_latitude=order_data.delivery_latitude,
        delivery_longitude=order_data.delivery_longitude,
        status="PENDING_PAYMENT",
        total=0.0,  # Será calculado abaixo
        user_id=order_data.user_id,
        stripe_customer_id=stripe_customer_id,
        tracking_code=order_data.tracking_code,
        delivery_type=order_data.delivery_type,
        total_delivery_fee=order_data.total_delivery_fee,
        total_service_fee=order_data.total_service_fee,
    )
    db.add(new_master_order)
    db.commit()
    db.refresh(new_master_order)

    total_products_price = 0.0
    
    # ── 3. Criar Sub-Pedidos (um para cada restaurante) ─────────────────────
    first_restaurant = None # Usado para o transfer_data (limitação do Stripe Checkout)

    for sub_req in order_data.sub_orders:
        restaurant = RestaurantRepository.get_by_gid(db, sub_req.restaurant_gid)
        if not restaurant:
            raise HTTPException(status_code=404, detail=f"Restaurante {sub_req.restaurant_name} não encontrado")
        
        if not first_restaurant:
            first_restaurant = restaurant

        sub_total_products = 0.0
        
        new_sub_order = SubOrderDB(
            gid=sub_req.gid if sub_req.gid else str(ULID()),
            master_order_gid=new_master_order.gid,
            restaurant_gid=restaurant.gid,
            restaurant_name=restaurant.name,
            restaurant_category=restaurant.category,
            restaurant_image_url=restaurant.image_url,
            restaurant_latitude=restaurant.latitude,
            restaurant_longitude=restaurant.longitude,
            status="PENDING_PAYMENT",
            delivery_fee=sub_req.delivery_fee,
            base_time=sub_req.base_time,
            total=0.0 # Calculado com os itens
        )
        db.add(new_sub_order)
        db.commit()
        db.refresh(new_sub_order)

        # Criar itens do sub-pedido
        for item_req in sub_req.items:
            product = db.query(ProductDB).filter(ProductDB.gid == item_req.product_gid).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Produto {item_req.product_gid} não encontrado")
            
            sub_total_products += product.price * item_req.quantity
            
            db_item = OrderItemDB(
                sub_order_gid=new_sub_order.gid,
                product_name=product.name,
                price=product.price,
                quantity=item_req.quantity,
                observation=item_req.observation,
                image_url=product.image_url,
                description=product.description
            )
            db.add(db_item)
        
        new_sub_order.total = sub_total_products + sub_req.delivery_fee
        total_products_price += sub_total_products
        db.commit()

    # Atualizar total da Master Order
    new_master_order.total = total_products_price + order_data.total_delivery_fee + order_data.total_service_fee
    db.commit()

    # ── 4. Pagamento Stripe ────────────────────────────────────────────────
    amount_cents = int(new_master_order.total * 100)
    
    # ⚠️ NOTA: Stripe Checkout só suporta 1 destino em transfer_data.
    # Em pedidos multi-restaurante, o dinheiro cai na conta da PLATAFORMA
    # e deve ser distribuído via Transfer API no webhook após o sucesso.
    
    is_multi_restaurant = len(order_data.sub_orders) > 1
    
    payment_intent_data = {}
    if not is_multi_restaurant and first_restaurant and first_restaurant.stripe_account_id:
        # Se for apenas 1 restaurante, mantemos a lógica de repasse direto
        commission_rate = get_commission_rate(first_restaurant.plan, use_own_delivery=first_restaurant.use_own_delivery)
        platform_fee = int((total_products_price * commission_rate + order_data.total_service_fee) * 100)
        
        if not first_restaurant.use_own_delivery:
            platform_fee += int(order_data.total_delivery_fee * 100)

        payment_intent_data = {
            "application_fee_amount": platform_fee,
            "transfer_data": {"destination": first_restaurant.stripe_account_id},
        }

    try:
        # 4.1 Ephemeral Key (Para exibir cartões salvos no SDK)
        ephemeral_key = stripe.EphemeralKey.create(
            customer=stripe_customer_id,
            stripe_version='2022-11-15', # Versão recomendada para o SDK
        )

        # 4.2 Criar PaymentIntent centralizado
        # Incluímos as taxas de aplicação e transferência (apenas para restaurante único)
        payment_intent_params = {
            "amount": amount_cents,
            "currency": "eur",
            "customer": stripe_customer_id,
            "automatic_payment_methods": {'enabled': True},
            "metadata": {
                "order_id": str(new_master_order.id),
                "master_gid": master_order_gid,
                "user_id": order_data.user_id,
                "is_multi_restaurant": str(is_multi_restaurant).lower(),
            }
        }

        # Adicionar taxas se houver apenas um restaurante
        if payment_intent_data:
            payment_intent_params.update(payment_intent_data)
        
        # Se o usuário quer salvar o cartão, instruímos o Stripe
        if order_data.save_payment_method:
            payment_intent_params["setup_future_usage"] = "off_session"

        payment_intent = stripe.PaymentIntent.create(**payment_intent_params)

        new_master_order.payment_intent_id = payment_intent.id
        db.commit()

        return {
            "order_id": new_master_order.id,
            "gid": master_order_gid,
            "clientSecret": payment_intent.client_secret,
            "customerId": stripe_customer_id,
            "ephemeralKey": ephemeral_key.secret,
            "publishableKey": config.settings.STRIPE_PUBLIC_KEY,
            "auto_paid": False
        }

    except Exception as e:
        print(f"❌ [Stripe SDK Error]: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/orders/customer/{user_id}", response_model=List[OrderResponse])
def get_customer_orders(user_id: str, db: Session = Depends(get_db)):
    try:
        print(f"👤 Buscando histórico de: {user_id}")

        from sqlalchemy.orm import joinedload
        master_orders = db.query(OrderDB).options(
            joinedload(OrderDB.sub_orders).joinedload(SubOrderDB.items),
            joinedload(OrderDB.sub_orders).joinedload(SubOrderDB.restaurant)
        ).filter(OrderDB.user_id == user_id).order_by(OrderDB.id.desc()).all()

        result = []
        for order in master_orders:
            sub_orders_resp = []
            for sub in order.sub_orders:
                items_resp = [
                    OrderItemResponse(
                        product_name=item.product_name if item.product_name else "Produto",
                        quantity=item.quantity if item.quantity else 1,
                        description=item.description,
                        image_url=item.image_url,
                        price=item.price if item.price else 0.0,
                        observation=item.observation
                    ) for item in sub.items
                ]
                
                sub_orders_resp.append(SubOrderResponse(
                    id=sub.id,
                    gid=sub.gid if sub.gid else "",
                    restaurant_gid=sub.restaurant_gid if sub.restaurant_gid else "",
                    restaurant_name=sub.restaurant_name if sub.restaurant_name else "",
                    restaurant_category=sub.restaurant_category if sub.restaurant_category else "",
                    restaurant_image_url=sub.restaurant_image_url,
                    status=sub.status,
                    total=sub.total if sub.total else 0.0,
                    delivery_fee=sub.delivery_fee if sub.delivery_fee else 0.0,
                    base_time=sub.base_time if sub.base_time else 0,
                    driver_name=sub.driver_name,
                    # Outros campos do driver podem ser buscados se necessário
                    items=items_resp
                ))

            result.append(OrderResponse(
                id=order.id,
                gid=order.gid if order.gid else "",
                customer_name=order.customer_name,
                delivery_address=order.delivery_address,
                total=order.total,
                status=order.status,
                tracking_code=order.tracking_code,
                delivery_type=order.delivery_type,
                delivery_latitude=order.delivery_latitude,
                delivery_longitude=order.delivery_longitude,
                total_delivery_fee=order.total_delivery_fee,
                total_service_fee=order.total_service_fee,
                sub_orders=sub_orders_resp
            ))

        return result
    except Exception as e:
        print(f"❌ Erro em get_customer_orders: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/{gid}", response_model=List[SubOrderResponse])
def get_restaurant_orders(gid: str, db: Session = Depends(get_db)):
    try:
        print(f"🔎 Buscando pedidos para o Restaurante GID {gid}")

        restaurant = RestaurantRepository.get_by_gid(db, gid)
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurante não encontrado")

        from sqlalchemy.orm import joinedload
        # Busca apenas os sub-pedidos deste restaurante
        sub_orders = db.query(SubOrderDB).options(
            joinedload(SubOrderDB.items)
        ).filter(SubOrderDB.restaurant_gid == gid).order_by(SubOrderDB.id.desc()).all()

        result = []
        for sub in sub_orders:
            items_resp = [
                OrderItemResponse(
                    product_name=item.product_name if item.product_name else "Produto",
                    quantity=item.quantity if item.quantity else 1,
                    description=item.description,
                    image_url=item.image_url,
                    price=item.price if item.price else 0.0,
                    observation=item.observation
                ) for item in sub.items
            ]
            
            result.append(SubOrderResponse(
                id=sub.id,
                gid=sub.gid if sub.gid else "",
                restaurant_gid=gid,
                restaurant_name=sub.restaurant_name if sub.restaurant_name else "",
                restaurant_category=sub.restaurant_category if sub.restaurant_category else "",
                restaurant_image_url=sub.restaurant_image_url,
                status=sub.status,
                total=sub.total if sub.total else 0.0,
                delivery_fee=sub.delivery_fee if sub.delivery_fee else 0.0,
                base_time=sub.base_time if sub.base_time else 0,
                driver_name=sub.driver_name,
                items=items_resp
            ))

        return result
    except Exception as e:
        print(f"❌ Erro em get_restaurant_orders: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders/{order_id}/cancel")
def cancel_order_and_refund(order_id: int, db: Session = Depends(get_db)):
    """
    Cancela um pedido e processa o reembolso automático ao cliente via Stripe.

    Fluxo:
    1. Se payment_intent_id já está no banco → usa direto.
    2. Se ainda é null (webhook ainda não chegou) → recupera via checkout_session_id.
    3. Verifica o status real do PaymentIntent antes de tentar o reembolso:
       - succeeded      → estorno normal com reverse_transfer
       - processing     → avisa que o reembolso será feito assim que capturado
       - não capturado  → cancela o PaymentIntent diretamente (sem cobrança)
    """
    print(f"🚫 Solicitação de cancelamento para o pedido #{order_id}")

    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    if order.status == "Cancelado":
        raise HTTPException(status_code=400, detail="Pedido já está cancelado")

    if order.status == "Entregue":
        raise HTTPException(status_code=400, detail="Não é possível cancelar um pedido já entregue")

    refund_id = None
    refund_status = None
    refund_error = None

    payment_intent_id = order.payment_intent_id

    # ─── Passo 1: webhook ainda não chegou → recupera via checkout_session_id ───
    if not payment_intent_id and order.checkout_session_id:
        try:
            print(f"🔍 payment_intent_id ausente — buscando via Session: {order.checkout_session_id}")
            session = stripe.checkout.Session.retrieve(order.checkout_session_id)
            if session.payment_intent:
                payment_intent_id = session.payment_intent
                # Persiste para não ter que buscar novamente
                order.payment_intent_id = payment_intent_id
                db.commit()
                print(f"✅ PaymentIntent recuperado: {payment_intent_id}")
        except stripe.error.StripeError as e:
            print(f"⚠️ Não foi possível recuperar a Session no Stripe: {str(e)}")

    # ─── Passo 2: processa o reembolso conforme o estado do PaymentIntent ───
    if payment_intent_id:
        try:
            pi = stripe.PaymentIntent.retrieve(payment_intent_id)
            print(f"💳 PaymentIntent status: {pi.status}")

            if pi.status == "succeeded":
                # Pagamento capturado → estorna e reverte o repasse ao restaurante
                try:
                    refund = stripe.Refund.create(
                        payment_intent=payment_intent_id,
                        reason="requested_by_customer",
                        reverse_transfer=True,       # tenta reverter repasse automático
                        refund_application_fee=True, # tenta devolver a comissão da plataforma
                        metadata={"order_id": str(order_id)},
                    )
                except stripe.error.StripeError as e:
                    # Se falhar por causa de transfer ou application_fee, tenta o estorno básico do valor
                    print(f"ℹ️ Retentando estorno simplificado devido a erro: {str(e)}")
                    refund = stripe.Refund.create(
                        payment_intent=payment_intent_id,
                        reason="requested_by_customer",
                        reverse_transfer=False,
                        refund_application_fee=False,
                        metadata={"order_id": str(order_id)},
                    )

                refund_id = refund.id
                refund_status = refund.status
                print(f"✅ Reembolso criado! ID: {refund_id}, Status: {refund_status}")

            elif pi.status == "processing":
                # Ainda em processamento — não é possível estornar agora
                refund_status = "pending_stripe_processing"
                refund_error = (
                    "Pagamento ainda em processamento pelo Stripe. "
                    "O reembolso será processado automaticamente assim que a captura for concluída."
                )
                print(f"⏳ PaymentIntent ainda em 'processing' — reembolso pendente")

            elif pi.status in ("requires_payment_method", "requires_confirmation",
                               "requires_action", "requires_capture"):
                # Nunca foi capturado → cancela diretamente, sem cobrança
                stripe.PaymentIntent.cancel(payment_intent_id)
                refund_status = "not_charged"
                print(f"✅ PaymentIntent cancelado antes de ser capturado — sem cobrança ao cliente")

            elif pi.status == "canceled":
                refund_status = "already_canceled"
                print(f"ℹ️ PaymentIntent já estava cancelado")

        except stripe.error.InvalidRequestError as e:
            print(f"⚠️ Reembolso não aplicável: {str(e)}")
            refund_error = str(e)
        except stripe.error.StripeError as e:
            print(f"❌ Erro ao processar reembolso: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Erro ao processar reembolso: {str(e)}")
    else:
        print(f"ℹ️ Pedido #{order_id} sem PaymentIntent associado — sem reembolso a processar")

    order.status = "Cancelado"
    for sub in order.sub_orders:
        sub.status = "Cancelado"
    db.commit()

    return {
        "message": "Pedido cancelado com sucesso",
        "order_id": order_id,
        "status": "Cancelado",
        "refund": {
            "processed": refund_id is not None,
            "refund_id": refund_id,
            "refund_status": refund_status,
            "error": refund_error,
        }
    }


@router.post("/orders/sub-order/{gid}/cancel")
def cancel_sub_order_and_partial_refund(gid: str, db: Session = Depends(get_db)):
    """
    Cancela apenas um sub-pedido específico de um restaurante via GID e processa o 
    reembolso parcial proporcional ao valor desse restaurante.
    """
    print(f"🚫 Solicitação de cancelamento para the sub-pedido GID: {gid}")

    sub = db.query(SubOrderDB).filter(SubOrderDB.gid == gid).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-pedido não encontrado")

    if sub.status == "Cancelado":
        raise HTTPException(status_code=400, detail="Sub-pedido já está cancelado")

    master = sub.master_order
    if not master:
        raise HTTPException(status_code=404, detail="Pedido principal não encontrado")

    refund_id = None
    refund_status = None
    refund_error = None

    payment_intent_id = master.payment_intent_id

    # 1. Recuperar PaymentIntent se necessário (fallback via session)
    if not payment_intent_id and master.checkout_session_id:
        try:
            session = stripe.checkout.Session.retrieve(master.checkout_session_id)
            if session.payment_intent:
                payment_intent_id = session.payment_intent
                master.payment_intent_id = payment_intent_id
                db.commit()
        except Exception:
            pass

    # 2. Processar reembolso parcial no Stripe
    if payment_intent_id:
        try:
            pi = stripe.PaymentIntent.retrieve(payment_intent_id)
            if pi.status == "succeeded":
                # Montante a reembolsar (total do sub-pedido em cêntimos)
                refund_amount = int(sub.total * 100)
                
                try:
                    refund = stripe.Refund.create(
                        payment_intent=payment_intent_id,
                        amount=refund_amount,
                        reason="requested_by_customer",
                        reverse_transfer=True, # Tenta reverter repasse automático
                        refund_application_fee=True, # Tenta devolver comissão
                        metadata={
                            "sub_order_id": str(sub.id), 
                            "sub_order_gid": gid,
                            "master_order_id": str(master.id),
                            "master_order_gid": master.gid
                        },
                    )
                except stripe.error.StripeError as e:
                    print(f"ℹ️ Retentando estorno parcial simplificado devido a erro: {str(e)}")
                    refund = stripe.Refund.create(
                        payment_intent=payment_intent_id,
                        amount=refund_amount,
                        reason="requested_by_customer",
                        reverse_transfer=False,
                        refund_application_fee=False,
                        metadata={
                            "sub_order_id": str(sub.id), 
                            "sub_order_gid": gid,
                            "master_order_id": str(master.id),
                            "master_order_gid": master.gid
                        },
                    )

                refund_id = refund.id
                refund_status = refund.status
                print(f"✅ Reembolso parcial criado! Valor: {sub.total} €, ID: {refund_id}")

        except stripe.error.StripeError as e:
            print(f"❌ Erro ao processar reembolso parcial: {str(e)}")
            refund_error = str(e)

    # 3. Atualizar status na base de dados
    sub.status = "Cancelado"
    
    # 4. Verificar se TODOS os sub-pedidos da master foram cancelados
    all_cancelled = all(s.status == "Cancelado" for s in master.sub_orders)
    if all_cancelled:
        master.status = "Cancelado"
        print(f"ℹ️ Todos os sub-pedidos do Master #{master.id} foram cancelados. Master atualizado.")
    
    db.commit()

    return {
        "message": "Sub-pedido cancelado com sucesso",
        "sub_order_gid": gid,
        "master_status": master.status,
        "refund": {
            "amount": sub.total,
            "processed": refund_id is not None,
            "refund_id": refund_id,
            "error": refund_error
        }
    }


@router.patch("/orders/{order_id}/base_time")
def update_base_time(order_id: int, payload: dict, db: Session = Depends(get_db)):
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    order.base_time = payload["base_time"]
    db.commit()
    return {"order_id": order_id, "base_time": order.base_time}


@router.post("/orders/{order_id}/reset-delivery")
def reset_order_delivery(order_id: int, db: Session = Depends(get_db)):
    """
    Remove o estafeta atual de um pedido e reinicia a busca.
    Limpa os campos driver_id, driver_name e informações de pagamento ao estafeta.
    """
    print(f"🔄 Reiniciando busca de estafeta para o pedido #{order_id}")
    
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    
    # Armazena o nome do driver anterior para log
    previous_driver = order.driver_name or f"ID {order.driver_id}"
    
    # Remove a atribuição do driver
    order.driver_id = None
    order.driver_name = None
    
    # Limpa os campos de pagamento ao estafeta (se houver)
    order.driver_delivery_fee = None
    order.driver_payment_transfer_id = None
    
    db.commit()
    
    print(f"✅ Estafeta {previous_driver} removido do pedido #{order_id}. Busca reiniciada.")
    
    return {
        "message": "Estafeta removido e busca reiniciada com sucesso",
        "order_id": order_id,
        "previous_driver": previous_driver,
        "status": order.status
    }


@router.put("/orders/{order_id}/status", response_model=OrderStatusResponse)
def update_order_status(order_id: int, status_data: OrderStatusUpdate, db: Session = Depends(get_db)):
    print(f"🔄 Atualizando pedido #{order_id} para: {status_data.status}")

    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    if status_data.status == "Cancelado":
        # Se cancelar o master, usa a lógica de cancelamento total
        return cancel_order_and_refund(order_id, db)

    order.status = status_data.status
    db.commit()

    return {"message": "Status atualizado", "status": order.status, "driver_name": None, "tracking_code": order.tracking_code}


@router.put("/orders/sub-order/{gid}/status", response_model=OrderStatusResponse)
def update_sub_order_status(gid: str, status_data: OrderStatusUpdate, db: Session = Depends(get_db)):
    """
    Atualiza o status de um sub-pedido específico via GID (usado pelo restaurante).
    """
    print(f"🔄 Atualizando sub-pedido GID: {gid} para: {status_data.status}")

    sub = db.query(SubOrderDB).filter(SubOrderDB.gid == gid).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Sub-pedido não encontrado")

    if status_data.status == "Cancelado":
        # Se o restaurante cancela, usa a lógica de reembolso parcial
        res = cancel_sub_order_and_partial_refund(gid, db)
        return {"message": res["message"], "status": "Cancelado", "driver_name": sub.driver_name}

    sub.status = status_data.status
    db.commit()

    return {"message": "Status do sub-pedido atualizado", "status": sub.status, "driver_name": sub.driver_name}


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()

    print(f"🔔 Webhook recebido. Signature: {stripe_signature[:20] if stripe_signature else 'None'}...")

    webhook_secret = config.settings.STRIPE_WEBHOOK_SECRET or config.settings.STRIPE_API_KEY

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Cabeçalho Stripe-Signature ausente")

    try:
        # Verifica se o evento veio realmente do Stripe
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=webhook_secret
        )
        print(f"✅ Evento validado: {event['type']}")
    except ValueError as e:
        # Payload inválido
        print(f"❌ Erro - Payload inválido: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.SignatureVerificationError as e:
        # Assinatura inválida
        print(f"❌ Erro - Assinatura inválida: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    # --- Processa o evento que nos interessa ---
    if event['type'] == 'checkout.session.completed':
        print(f"🎉 Evento checkout.session.completed recebido!")
        session = event['data']['object']

        payment_intent_id = session.get('payment_intent')
        metadata = session.get('metadata', {}) or {}
        order_id = metadata.get('order_id')
        user_id = metadata.get('user_id')
        should_save_payment_method = str(metadata.get('save_payment_method', 'false')).lower() == 'true'

        print(f"📋 Order ID: {order_id}, User ID: {user_id}, PaymentIntent: {payment_intent_id}")

        db = SessionLocal()
        try:
            if order_id and payment_intent_id:
                db_order = db.query(OrderDB).filter(OrderDB.id == int(order_id)).first()
                if db_order:
                    print(f"📦 Pedido encontrado: {db_order.id}, Status anterior: {db_order.status}")
                    db_order.payment_intent_id = payment_intent_id
                    if session.get('customer'):
                        db_order.stripe_customer_id = session.get('customer')

                    if db_order.status == "PENDING_PAYMENT":
                        db_order.status = "Pendente"
                        # Propaga o status para as sub-orders
                        for sub in db_order.sub_orders:
                            sub.status = "Pendente"
                        print(f"✅ Status atualizado para: Pendente (Master + {len(db_order.sub_orders)} Sub-orders)")
                else:
                    print(f"❌ Pedido {order_id} não encontrado no banco!")

            if should_save_payment_method and user_id and payment_intent_id:
                print(f"💾 Salvando método de pagamento para user: {user_id}")
                payment_intent = stripe.PaymentIntent.retrieve(
                    payment_intent_id,
                    expand=['payment_method']
                )
                payment_method = payment_intent.get('payment_method')

                if payment_method and payment_method.get('type') == 'card':
                    card_data = payment_method.get('card', {}) or {}
                    payment_method_id = payment_method.get('id')
                    stripe_customer_id = session.get('customer') or payment_intent.get('customer')

                    if payment_method_id and stripe_customer_id:
                        existing_method = db.query(SavedPaymentMethodDB).filter(
                            SavedPaymentMethodDB.stripe_payment_method_id == payment_method_id
                        ).first()

                        if existing_method:
                            print(f"🔄 Atualizando método existente: {payment_method_id}")
                            existing_method.user_id = user_id
                            existing_method.stripe_customer_id = stripe_customer_id
                            existing_method.card_brand = card_data.get('brand')
                            existing_method.card_last4 = card_data.get('last4')
                            existing_method.card_exp_month = card_data.get('exp_month')
                            existing_method.card_exp_year = card_data.get('exp_year')
                        else:
                            print(f"➕ Criando novo método salvo: {payment_method_id}")
                            db.add(SavedPaymentMethodDB(
                                user_id=user_id,
                                stripe_customer_id=stripe_customer_id,
                                stripe_payment_method_id=payment_method_id,
                                card_brand=card_data.get('brand'),
                                card_last4=card_data.get('last4'),
                                card_exp_month=card_data.get('exp_month'),
                                card_exp_year=card_data.get('exp_year')
                            ))

            db.commit()
            print(f"✅ Webhook processado com sucesso!")
        except Exception as e:
            print(f"❌ Erro ao processar webhook: {str(e)}")
            db.rollback()
        finally:
            db.close()

    if event['type'] == 'payment_intent.succeeded':
        pi = event['data']['object']
        payment_intent_id = pi.get('id')
        order_id = pi.get('metadata', {}).get('order_id')
        print(f"💳 Evento payment_intent.succeeded recebido: {payment_intent_id} | Order ID: {order_id}")

        db = SessionLocal()
        try:
            # 🚀 ATUALIZAÇÃO DE STATUS PARA SDK NATIVO
            # Quando usa o SDK, o evento principal é este succeeded, não o checkout.session
            if order_id:
                db_order = db.query(OrderDB).filter(OrderDB.id == int(order_id)).first()
                if db_order and db_order.status == "PENDING_PAYMENT":
                    print(f"✅ [SDK SDK] Atualizando pedido #{db_order.id} para Pendente via PaymentIntent")
                    db_order.status = "Pendente"
                    db_order.payment_intent_id = payment_intent_id
                    for sub in db_order.sub_orders:
                        sub.status = "Pendente"
                    db.commit()

            # Verifica se o pedido associado foi cancelado enquanto o pagamento estava em "processing" (estorno tardio)
            order = db.query(OrderDB).filter(
                OrderDB.payment_intent_id == payment_intent_id,
                OrderDB.status == "Cancelado"
            ).first()

            if order:
                print(f"⚠️ Pedido #{order.id} estava cancelado enquanto o pagamento processava — iniciando reembolso tardio")
                stripe.Refund.create(
                    payment_intent=payment_intent_id,
                    reason="requested_by_customer",
                    reverse_transfer=True,
                    refund_application_fee=True,
                    metadata={
                        "order_id": str(order.id),
                        "late_refund": "true",
                    }
                )
                print(f"✅ Reembolso tardio processado com sucesso para o pedido #{order.id}")
            else:
                print(f"ℹ️ Nenhum pedido cancelado encontrado para PaymentIntent {payment_intent_id}")

        except stripe.error.StripeError as e:
            print(f"❌ Erro ao processar reembolso tardio: {str(e)}")
        except Exception as e:
            print(f"❌ Erro inesperado no reembolso tardio: {str(e)}")
        finally:
            db.close()

    # --- Eventos de Conta Stripe Connect (Restaurantes e Drivers) ---
    if event['type'] == 'account.updated':
        account_data = event['data']['object']
        account_id = account_data.get('id')
        is_complete = (
            account_data.get('details_submitted', False) and
            account_data.get('charges_enabled', False) and
            account_data.get('payouts_enabled', False)
        )

        print(f"🔄 Evento account.updated recebido para conta: {account_id}")
        print(f"   details_submitted={account_data.get('details_submitted')}, "
              f"charges_enabled={account_data.get('charges_enabled')}, "
              f"payouts_enabled={account_data.get('payouts_enabled')}")

        db = SessionLocal()
        try:
            # Verifica se é uma conta de restaurante
            restaurant = db.query(RestaurantDB).filter(
                RestaurantDB.stripe_account_id == account_id
            ).first()

            if restaurant:
                restaurant.stripe_onboarding_completed = is_complete
                if is_complete and restaurant.status != "ACTIVE":
                    restaurant.status = "ACTIVE"
                    restaurant.license = "ATIVO"  # Sincroniza o campo license
                    print(f"✅ Restaurante {restaurant.id} ({restaurant.name}) → status=ACTIVE, license=ATIVO")
                db.commit()

            # Verifica se é uma conta de driver
            driver = db.query(DriverDB).filter(
                DriverDB.stripe_account_id == account_id
            ).first()

            if driver:
                driver.stripe_onboarding_completed = is_complete
                if is_complete and driver.status not in ["ACTIVE", "INACTIVE"]:
                    driver.status = "ACTIVE"
                    print(f"✅ Driver {driver.id} ({driver.login}) → status=ACTIVE")
                db.commit()

            if not restaurant and not driver:
                print(f"⚠️ Nenhum restaurante ou driver encontrado com stripe_account_id={account_id}")

        except Exception as e:
            print(f"❌ Erro ao processar account.updated: {str(e)}")
            db.rollback()
        finally:
            db.close()

    # Avisa ao Stripe que recebemos o evento com sucesso
    return {"status": "success"}


@router.get("/users/{user_id}/saved-payment-methods")
def get_user_saved_payment_methods(user_id: str, db: Session = Depends(get_db)):
    """
    Retorna todos os métodos de pagamento salvos do usuário.
    Útil para o app verificar antes de tentar pagamento automático.
    """
    print(f"💳 Buscando métodos de pagamento salvos para user: {user_id}")

    saved_methods = db.query(SavedPaymentMethodDB).filter(
        SavedPaymentMethodDB.user_id == user_id
    ).all()

    if not saved_methods:
        return {
            "has_saved_methods": False,
            "methods": []
        }

    methods_data = [
        {
            "id": method.id,
            "brand": method.card_brand,
            "last4": method.card_last4,
            "exp_month": method.card_exp_month,
            "exp_year": method.card_exp_year,
            "stripe_payment_method_id": method.stripe_payment_method_id,
        }
        for method in saved_methods
    ]

    return {
        "has_saved_methods": True,
        "methods": methods_data
    }


@router.delete("/users/{user_id}/saved-payment-methods/{method_id}")
def delete_saved_payment_method(user_id: str, method_id: int, db: Session = Depends(get_db)):
    """
    Deleta um método de pagamento salvo do usuário.
    """
    print(f"🗑️ Deletando método {method_id} do user: {user_id}")

    saved_method = db.query(SavedPaymentMethodDB).filter(
        SavedPaymentMethodDB.id == method_id,
        SavedPaymentMethodDB.user_id == user_id
    ).first()

    if not saved_method:
        raise HTTPException(status_code=404, detail="Método de pagamento não encontrado")

    try:
        # Detacha o método de pagamento do Stripe
        stripe.PaymentMethod.detach(saved_method.stripe_payment_method_id)
        print(f"✅ PaymentMethod {saved_method.stripe_payment_method_id} desanexado do Stripe")
    except stripe.error.StripeError as e:
        print(f"⚠️ Aviso ao desanexar no Stripe: {str(e)}")
        # Continua mesmo se falhar no Stripe, pois pode já estar deletado

    db.delete(saved_method)
    db.commit()

    return {"message": "Método de pagamento deletado com sucesso"}


@router.get("/restaurant/{gid}/finance-summary")
def get_restaurant_finance_summary(gid: str, db: Session = Depends(get_db)):
    print(f"💰 Buscando resumo financeiro para o Restaurante GID: {gid}")

    restaurant = RestaurantRepository.get_by_gid(db, gid)

    if not restaurant or not restaurant.stripe_account_id:
        raise HTTPException(status_code=404, detail="Restaurante não configurado para pagamentos.")

    try:
        # 1. Busca os saldos atuais
        balance = stripe.Balance.retrieve(stripe_account=restaurant.stripe_account_id)

        # 2. Busca os repasses futuros/pendentes (limitamos a 3 para mostrar na lista)
        upcoming_payouts = stripe.Payout.list(
            limit=3,
            status="pending",  # Traz apenas os que ainda vão cair
            stripe_account=restaurant.stripe_account_id
        )

        # 3. NOVO: Busca os repasses que JÁ FORAM PAGOS (Já caíram no banco)
        paid_payouts = stripe.Payout.list(
            limit=100,  # Puxa até os últimos 100 repasses realizados
            status="paid",
            stripe_account=restaurant.stripe_account_id
        )

        # 4. Faz as somas convertendo de centavos para Euros
        available = sum(b.amount for b in balance.available) / 100.0
        pending = sum(b.amount for b in balance.pending) / 100.0

        # Faz a soma de todo o dinheiro que já foi transferido para o banco
        total_ja_repassado = sum(p.amount for p in paid_payouts.data) / 100.0

        # Formata a lista dos próximos repasses para o Flutter
        upcoming_list = [
            {
                "amount": p.amount / 100.0,
                "status": p.status,
                "expected_arrival_date": p.arrival_date
            } for p in upcoming_payouts.data
        ]

        return {
            "saldo_disponivel_eur": available,
            "saldo_pendente_eur": pending,
            "total_ja_repassado_eur": total_ja_repassado,  # <--- NOVO CAMPO AQUI
            "proximos_repasses": upcoming_list
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/orders/{order_id}/check-payment-status")
def check_order_payment_status(order_id: int, db: Session = Depends(get_db)):
    """
    Verifica o status real do pagamento no Stripe e atualiza o pedido no banco.
    Útil como fallback caso o webhook não seja processado imediatamente.
    """
    print(f"🔍 Verificando status de pagamento para pedido #{order_id}")

    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    # Se já foi processado, retorna o status atual
    if order.status != "PENDING_PAYMENT":
        return {
            "order_id": order_id,
            "status": order.status,
            "payment_confirmed": True,
            "already_processed": True
        }

    # Tenta buscar o status no Stripe
    try:
        payment_intent_id = order.payment_intent_id

        # Se não tem payment_intent_id ainda, tenta recuperar via checkout_session_id
        if not payment_intent_id and order.checkout_session_id:
            print(f"🔍 Buscando PaymentIntent via Session: {order.checkout_session_id}")
            session = stripe.checkout.Session.retrieve(order.checkout_session_id)

            if session.payment_intent:
                payment_intent_id = session.payment_intent
                order.payment_intent_id = payment_intent_id

                # Captura o customer_id se disponível
                if session.customer:
                    order.stripe_customer_id = session.customer

                db.commit()
                print(f"✅ PaymentIntent recuperado: {payment_intent_id}")

        # Verifica o status do pagamento
        if payment_intent_id:
            pi = stripe.PaymentIntent.retrieve(payment_intent_id)
            print(f"💳 PaymentIntent status: {pi.status}")

            if pi.status == "succeeded":
                # Pagamento confirmado → atualiza para Pendente
                order.status = "Pendente"
                for sub in order.sub_orders:
                    sub.status = "Pendente"
                db.commit()
                print(f"✅ Pedido #{order_id} atualizado: PENDING_PAYMENT → Pendente (com sub-orders)")

                return {
                    "order_id": order_id,
                    "status": "Pendente",
                    "payment_confirmed": True,
                    "updated": True
                }
            else:
                # Pagamento ainda não foi completado
                return {
                    "order_id": order_id,
                    "status": "PENDING_PAYMENT",
                    "payment_confirmed": False,
                    "payment_status": pi.status
                }
        else:
            # Sem payment_intent ainda
            return {
                "order_id": order_id,
                "status": "PENDING_PAYMENT",
                "payment_confirmed": False,
                "message": "Pagamento ainda não foi processado"
            }

    except stripe.error.StripeError as e:
        print(f"❌ Erro ao verificar status no Stripe: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro ao verificar pagamento: {str(e)}")


@router.get("/payment-success", response_class=HTMLResponse)
def payment_success(order_id: Optional[int] = None):
    """
    Página de sucesso após pagamento completado no Stripe.
    Atualiza automaticamente o status do pedido via JavaScript.
    """
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pagamento Confirmado - Koma Ai</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #D4D7DD 0%, #A8ADB7 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            
            .container {{
                background: white;
                border-radius: 20px;
                padding: 60px 40px;
                max-width: 500px;
                width: 100%;
                text-align: center;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                animation: slideUp 0.5s ease-out;
            }}
            
            @keyframes slideUp {{
                from {{
                    opacity: 0;
                    transform: translateY(30px);
                }}
                to {{
                    opacity: 1;
                    transform: translateY(0);
                }}
            }}
            
            .success-icon {{
                width: 80px;
                height: 80px;
                background: linear-gradient(135deg, #D4D7DD 0%, #A8ADB7 100%);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 30px;
                animation: scaleIn 0.5s ease-out 0.2s both;
            }}
            
            @keyframes scaleIn {{
                from {{
                    transform: scale(0);
                }}
                to {{
                    transform: scale(1);
                }}
            }}
            
            .checkmark {{
                width: 40px;
                height: 40px;
                border: 4px solid white;
                border-top: none;
                border-right: none;
                transform: rotate(-45deg);
                margin-top: -10px;
            }}
            
            h1 {{
                color: #2d3748;
                font-size: 32px;
                font-weight: 700;
                margin-bottom: 20px;
            }}
            
            .message {{
                color: #4a5568;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 30px;
            }}
            
            .status-badge {{
                display: inline-block;
                background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                color: white;
                padding: 12px 30px;
                border-radius: 25px;
                font-weight: 600;
                font-size: 16px;
                margin-bottom: 30px;
                animation: pulse 2s ease-in-out infinite;
            }}
            
            @keyframes pulse {{
                0%, 100% {{
                    transform: scale(1);
                }}
                50% {{
                    transform: scale(1.05);
                }}
            }}
            
            .info-text {{
                color: #718096;
                font-size: 14px;
                line-height: 1.6;
                margin-bottom: 30px;
            }}
            
            .close-button {{
                background: linear-gradient(135deg, #D4D7DD 0%, #A8ADB7 100%);
                color: white;
                border: none;
                padding: 16px 40px;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                box-shadow: 0 4px 15px rgba(168, 173, 183, 0.4);
            }}
            
            .close-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(168, 173, 183, 0.6);
            }}
            
            .close-button:active {{
                transform: translateY(0);
            }}
            
            .footer {{
                margin-top: 30px;
                color: #a0aec0;
                font-size: 12px;
            }}
        </style>
        <script>
            // Função para atualizar o status do pedido automaticamente
            async function updateOrderStatus() {{
                const orderId = '{order_id}';
                if (!orderId || orderId === 'None') {{
                    console.error('Order ID não fornecido na URL');
                    return;
                }}
                
                try {{
                    const response = await fetch(`/orders/${{orderId}}/check-payment-status`, {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }}
                    }});
                    
                    const data = await response.json();
                    console.log('Status atualizado:', data);
                    
                    if (data.payment_confirmed) {{
                        console.log('✅ Pagamento confirmado! Status:', data.status);
                    }}
                }} catch (error) {{
                    console.error('Erro ao atualizar status:', error);
                }}
            }}
            
            // Chama a atualização assim que a página carrega
            window.addEventListener('load', updateOrderStatus);
            
            // Auto-redirect depois de 3 segundos
            setTimeout(function() {{
                window.location.href = 'https://komaapp.netlify.app/';
            }}, 3000);
            
            function goHome() {{
                window.location.href = 'https://komaapp.netlify.app/';
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">
                <div class="checkmark"></div>
            </div>
            
            <h1>💳 Pagamento Confirmado!</h1>
            
            <div class="status-badge">
                ✅ Pedido Realizado com Sucesso
            </div>
            
            <p class="message">
                Obrigado! Seu pagamento foi processado com sucesso.<br>
                Seu pedido já está sendo preparado.
            </p>
            
            <p class="info-text">
                Você receberá atualizações em tempo real sobre o status do seu pedido.
            </p>
            
            <button class="close-button" onclick="goHome()">
                Voltar ao Início
            </button>
            
            <p class="footer">
                Redirecionando automaticamente em 3 segundos...
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/orders/ratings")
def submit_order_ratings(payload: RatingRequest, db: Session = Depends(get_db)):
    """
    Recebe as avaliações dos produtos de um pedido.
    Calcula e atualiza o rating médio de cada produto avaliado.
    """
    order_id_int = int(payload.order_id)

    # Valida o pedido
    order = db.query(OrderDB).filter(OrderDB.id == order_id_int).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    restaurant = RestaurantRepository.get_by_gid(db, payload.restaurant_gid)
    # Verifica se este restaurante faz parte do pedido Master através de uma Sub-Order
    is_part_of_order = any(sub.restaurant_gid == payload.restaurant_gid for sub in order.sub_orders)
    
    if not restaurant or not is_part_of_order:
        raise HTTPException(status_code=400, detail="restaurant_gid não corresponde a nenhum sub-pedido desta encomenda")

    saved_ratings = []
    for item in payload.ratings:
        if not (1 <= item.rating <= 5):
            raise HTTPException(
                status_code=422,
                detail=f"Rating inválido ({item.rating}) para produto {item.product_gid}. Deve ser entre 1 e 5."
            )

        product = db.query(ProductDB).filter(ProductDB.gid == item.product_gid).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Produto {item.product_gid} não encontrado")

        # Evita duplicatas por pedido+produto (upsert manual)
        existing = db.query(ProductRatingDB).filter(
            ProductRatingDB.order_id == order_id_int,
            ProductRatingDB.product_id == product.id,
        ).first()

        if existing:
            existing.rating = item.rating
        else:
            new_rating = ProductRatingDB(
                order_id=order_id_int,
                product_id=product.id,
                restaurant_id=restaurant.id,
                rating=item.rating,
            )
            db.add(new_rating)

        saved_ratings.append(product.id)

    db.commit()

    # Recalcula o rating médio de cada produto avaliado e persiste no ProductDB
    for product_id in saved_ratings:
        all_ratings = db.query(ProductRatingDB).filter(
            ProductRatingDB.product_id == product_id,
            ProductRatingDB.restaurant_id == restaurant.id
        ).all()
        if all_ratings:
            avg = sum(r.rating for r in all_ratings) / len(all_ratings)
            product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
            if product:
                product.rating = round(avg, 2)

    db.commit()

    print(f"✅ Avaliações registadas para o pedido {order_id_int}: produtos {saved_ratings}")
    return {"message": "Avaliações registadas com sucesso", "rated_products": saved_ratings}
