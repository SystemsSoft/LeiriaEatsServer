# Arquivo: core/sql_models.py
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, Boolean, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from core.database import Base

LISBON_TZ = ZoneInfo("Europe/Lisbon")


class RestaurantDB(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    category = Column(String(100))
    rating = Column(Float)
    image_url = Column(String(500))
    login = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    license = Column(String(100), nullable=True)
    plan = Column(String(50), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    stripe_account_id = Column(String(255), nullable=True)
    stripe_onboarding_completed = Column(Boolean, default=False)
    use_own_delivery = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), default="PENDING")  # PENDING | STRIPE_PENDING | ACTIVE | INACTIVE
    gid = Column(String(255), nullable=True, unique=True)
    
    # --- SURPRISE BOX ---
    has_surprise_box = Column(Boolean, default=False, nullable=False)
    surprise_box_qty = Column(Integer, default=0, nullable=False)
    # Faixa de horário em que a caixa surpresa pode ser recolhida, formato "HH:mm"
    # (mesmo padrão de RestaurantHourDB.open_time/close_time)
    surprise_box_pickup_start = Column(String(5), nullable=True)
    surprise_box_pickup_end = Column(String(5), nullable=True)

    products = relationship("ProductDB", back_populates="restaurant")
    hours = relationship("RestaurantHourDB", back_populates="restaurant")
    delivery_zones = relationship("DeliveryZoneDB", back_populates="restaurant", cascade="all, delete-orphan")


class ProductDB(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    gid = Column(String(255), nullable=True, unique=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(Float)
    image_url = Column(String(500))
    category = Column(String(100), default="Geral")
    preparation_time = Column(String(100), nullable=True)
    rating = Column(Float, nullable=True, default=None)

    # ForeignKey aponta para a tabela 'restaurants', coluna 'id'
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)

    # ══════════════════════════════════════════════════════════════
    # Novas colunas para melhorar recomendações da IA
    # ══════════════════════════════════════════════════════════════
    
    # Ingredientes e composição
    ingredients = Column(Text, nullable=True)  # Lista de ingredientes separados por vírgula
    allergens = Column(String(500), nullable=True)  # Ex: "glúten, lactose, amendoim"
    
    # Tags dietéticas (separadas por vírgula)
    # Ex: "vegetariano, sem glúten, low carb"
    dietary_tags = Column(String(500), nullable=True)
    
    # Nível de picância
    # Valores: "não picante", "levemente picante", "picante", "muito picante"
    spice_level = Column(String(50), nullable=True, default="não picante")
    
    # Informações de porção
    serves_people = Column(Integer, nullable=True)  # Quantas pessoas serve (1, 2, 4, etc)
    portion_size = Column(String(50), nullable=True)  # "individual", "pequeno", "médio", "grande", "família"
    
    # Informações nutricionais básicas
    calories = Column(Integer, nullable=True)  # Calorias aproximadas
    
    # Status e popularidade
    is_popular = Column(Boolean, default=False)  # Se é item destaque/popular
    is_available = Column(Boolean, default=True)  # Se está disponível no momento
    is_surprise_box = Column(Boolean, default=False)  # Se é um item de Caixa Surpresa
    
    # Tempo de preparo estruturado
    preparation_time_minutes = Column(Integer, nullable=True)  # Tempo em minutos (ex: 30)

    # Minutos que o prato aguenta em trânsito mantendo qualidade (gelado derrete, chocolate
    # quente esfria) — usado pelo sequenciador de recolhas multi-restaurante para não recolher
    # um perecível cedo demais. Obrigatório no formulário/API de criação (ver
    # PLANO_RECOLHA_MULTI_RESTAURANTE.md, 4.4/5.5); nullable aqui porque o histórico de
    # produtos já cadastrados não tem esse valor — ver scripts/backfill_transit_tolerance.py.
    transit_tolerance_minutes = Column(Integer, nullable=True)

    # Recomendação por horário (separado por vírgula)
    # Ex: "café da manhã, almoço", "jantar, lanche", "sobremesa"
    recommended_for = Column(String(200), nullable=True)
    
    # Tags adicionais para busca (separadas por vírgula)
    # Ex: "rápido, leve, gourmet, tradicional, kids"
    search_tags = Column(String(500), nullable=True)

    restaurant = relationship("RestaurantDB", back_populates="products")

    @property
    def restaurant_gid(self) -> str:
        if self.restaurant and self.restaurant.gid:
            return self.restaurant.gid
        return ""


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    gid = Column(String(255), nullable=True, unique=True) # Master Order GID
    customer_name = Column(String(255))
    delivery_address = Column(String(500))
    status = Column(String(50), default="Pendente")
    total = Column(Float)
    user_id = Column(String(255))
    payment_intent_id = Column(String(255), nullable=True)
    checkout_session_id = Column(String(255), nullable=True)
    stripe_customer_id = Column(String(255), nullable=True)
    tracking_code = Column(String(100), nullable=True, default="")
    delivery_type = Column(String(50), nullable=True)
    order_type = Column(String(50), nullable=True, default="COMMON") # COMMON | SURPRISE
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # ── Coordenadas do endereço de entrega ─────────
    delivery_latitude  = Column(Float, nullable=True)
    delivery_longitude = Column(Float, nullable=True)

    # ── Taxas Totais ─────────────────────────────────────────────────────────
    total_delivery_fee = Column(Float, nullable=True, default=0.0)
    total_service_fee  = Column(Float, nullable=True, default=0.0)

    # ── Pagamento em 2 etapas (PLANO_PAGAMENTO_2_ETAPAS.md) ────────────────
    # payment_flow distingue pedidos criados antes/depois deste fluxo: pedidos antigos
    # foram cobrados na hora (AUTO_CAPTURE) e devem seguir o cancelamento/estorno de
    # sempre; só MANUAL_CAPTURE passa pelo caminho novo de autorização → aceite → captura.
    # Sem essa coluna, um pedido antigo cancelado depois do deploy seguiria a lógica errada.
    payment_flow = Column(String(20), nullable=True, default="AUTO_CAPTURE")  # AUTO_CAPTURE | MANUAL_CAPTURE
    # REQUIRES_PAYMENT | AUTHORIZED | CAPTURED | CANCELED | FAILED
    payment_status = Column(String(50), nullable=True, default=None)
    authorized_amount = Column(Float, nullable=True, default=None)
    captured_amount = Column(Float, nullable=True, default=None)
    authorization_expires_at = Column(DateTime(timezone=True), nullable=True, default=None)

    sub_orders = relationship("SubOrderDB", back_populates="master_order", cascade="all, delete-orphan", foreign_keys="SubOrderDB.master_order_gid", primaryjoin="OrderDB.gid == SubOrderDB.master_order_gid")


class SubOrderDB(Base):
    __tablename__ = "sub_orders"

    id = Column(Integer, primary_key=True, index=True)
    gid = Column(String(255), nullable=True, unique=True)
    master_order_gid = Column(String(255), ForeignKey("orders.gid"), nullable=False, index=True)

    restaurant_gid = Column(String(255), ForeignKey("restaurants.gid"))
    restaurant_name = Column(String(255))
    restaurant_category = Column(String(100))
    restaurant_image_url = Column(String(500))

    status = Column(String(50), default="Pendente", index=True)
    total = Column(Float)
    delivery_fee = Column(Float, default=0.0)
    base_time = Column(Integer, default=0)

    # PLANO_RECOLHA_MULTI_RESTAURANTE.md, Fase 2 — sinal real de prontidão, gravado pelo
    # botão "Pedido pronto" do KomaRestaurant. Não substitui `status` (ver secção 6 do
    # plano: trocar o vocabulário de status mexe na guarda de captura de pagamento, risco
    # à parte); é um timestamp paralelo que o worker de despacho passa a preferir à
    # estimativa por base_time quando presente.
    ready_at = Column(DateTime(timezone=True), nullable=True, default=None)

    # ── Estafeta atribuído (específico por sub-pedido/restaurante) ──────────
    driver_gid  = Column(String(255), ForeignKey("drivers.gid"), nullable=True)
    driver_name = Column(String(255), nullable=True)
    driver_delivery_fee = Column(Float, nullable=True, default=None)
    driver_payment_transfer_id = Column(String(255), nullable=True, default=None)

    # ── Coordenadas do restaurante ───
    restaurant_latitude  = Column(Float, nullable=True)
    restaurant_longitude = Column(Float, nullable=True)

    # ── Aceite e repasse (PLANO_PAGAMENTO_2_ETAPAS.md, Fases 2 e 4) ─────────
    accepted_at  = Column(DateTime(timezone=True), nullable=True, default=None)
    declined_at  = Column(DateTime(timezone=True), nullable=True, default=None)
    decline_reason = Column(String(255), nullable=True, default=None)
    # Idempotência do repasse: sem isto, um webhook reentregue pagaria o restaurante 2x.
    stripe_transfer_id = Column(String(255), nullable=True, default=None)
    stripe_transfer_amount = Column(Float, nullable=True, default=None)
    stripe_transfer_reversed = Column(Float, nullable=True, default=0.0)

    master_order = relationship("OrderDB", back_populates="sub_orders", foreign_keys=[master_order_gid], primaryjoin="SubOrderDB.master_order_gid == OrderDB.gid")
    restaurant = relationship("RestaurantDB", foreign_keys=[restaurant_gid], primaryjoin="SubOrderDB.restaurant_gid == RestaurantDB.gid")
    items = relationship("OrderItemDB", back_populates="sub_order", cascade="all, delete-orphan", foreign_keys="OrderItemDB.sub_order_gid", primaryjoin="SubOrderDB.gid == OrderItemDB.sub_order_gid")


class OrderItemDB(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    sub_order_gid = Column(String(255), ForeignKey("sub_orders.gid"))

    observation = Column(String(500), nullable=True)
    product_name = Column(String(255))
    price = Column(Float)
    quantity = Column(Integer)
    description = Column(Text)
    image_url = Column(String(500))

    sub_order = relationship("SubOrderDB", back_populates="items", foreign_keys=[sub_order_gid], primaryjoin="OrderItemDB.sub_order_gid == SubOrderDB.gid")


class DeliveryRouteDB(Base):
    """
    PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 5.1 e Fase 3 — a unidade que o estafeta
    aceita passa a ser a rota (N paragens de recolha + 1 entrega), não o sub-pedido
    isolado. Uma rota agrupa os sub-pedidos de um único master_order (Opção B, secção 3);
    não ser filha do pedido no desenho (é uma entidade própria) deixa a Opção C
    (agrupar entre pedidos distintos, hoje fora de escopo) possível sem nova migração.
    """
    __tablename__ = "delivery_routes"

    id = Column(Integer, primary_key=True, index=True)
    gid = Column(String(255), unique=True, nullable=False)
    # Sem ForeignKey de verdade: orders.gid não tem chave única no banco de produção
    # (mesma lacuna pré-existente de OrderItemDB.sub_order_gid — confirmado via
    # information_schema, não é regressão desta migração). Índice normal basta para as
    # queries que o worker de despacho faz.
    master_order_gid = Column(String(255), nullable=False, index=True)
    driver_gid = Column(String(255), ForeignKey("drivers.gid"), nullable=True, index=True)

    # OFFERED | ACCEPTED | IN_PROGRESS | COMPLETED | EXPIRED | CANCELLED
    status = Column(String(30), nullable=False, default="OFFERED", index=True)

    offered_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_at  = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    estimated_delivery_at = Column(DateTime(timezone=True), nullable=True)
    # Taxa estimada da rota inteira, calculada no momento da oferta (mesma fórmula
    # _calculate_delivery_fee já usada no fluxo antigo de sub-pedido único) — sem isto o
    # app do estafeta (Fase 4) não tem o que mostrar como ganho antes de aceitar.
    estimated_fee = Column(Float, nullable=True)
    sequence_version = Column(Integer, nullable=False, default=1)  # incrementa a cada recálculo

    master_order = relationship("OrderDB", foreign_keys=[master_order_gid], primaryjoin="DeliveryRouteDB.master_order_gid == OrderDB.gid")
    driver = relationship("DriverDB", foreign_keys=[driver_gid], primaryjoin="DeliveryRouteDB.driver_gid == DriverDB.gid")
    stops = relationship("RouteStopDB", back_populates="route", cascade="all, delete-orphan", foreign_keys="RouteStopDB.route_gid", primaryjoin="DeliveryRouteDB.gid == RouteStopDB.route_gid", order_by="RouteStopDB.sequence")


class RouteStopDB(Base):
    """
    PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 5.2 — uma paragem por sub-pedido dentro de
    uma rota. É aqui que vive a ordem de recolha (campo `sequence`), que antes desta fase
    não existia em lugar nenhum do schema.
    """
    __tablename__ = "route_stops"

    id = Column(Integer, primary_key=True, index=True)
    route_gid = Column(String(255), ForeignKey("delivery_routes.gid"), nullable=False, index=True)
    # Sem ForeignKey de verdade contra sub_orders.gid — mesma lacuna de master_order_gid
    # acima (sub_orders.gid também não tem chave única em produção).
    sub_order_gid = Column(String(255), nullable=False, index=True)

    sequence = Column(Integer, nullable=False)  # 1, 2, 3 — ordem de recolha decidida pelo RouteSequencer
    ready_at_estimated = Column(DateTime(timezone=True), nullable=True)
    ready_at_confirmed = Column(DateTime(timezone=True), nullable=True)  # espelha SubOrderDB.ready_at no momento do cálculo
    arrived_at   = Column(DateTime(timezone=True), nullable=True)
    picked_up_at = Column(DateTime(timezone=True), nullable=True)

    route = relationship("DeliveryRouteDB", back_populates="stops", foreign_keys=[route_gid], primaryjoin="RouteStopDB.route_gid == DeliveryRouteDB.gid")
    sub_order = relationship("SubOrderDB", foreign_keys=[sub_order_gid], primaryjoin="RouteStopDB.sub_order_gid == SubOrderDB.gid")

    __table_args__ = (UniqueConstraint("route_gid", "sub_order_gid", name="uq_route_sub_order"),)


class SavedPaymentMethodDB(Base):
    __tablename__ = "saved_payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), index=True, nullable=False)
    stripe_customer_id = Column(String(255), nullable=False)
    stripe_payment_method_id = Column(String(255), unique=True, nullable=False)
    card_brand = Column(String(50), nullable=True)
    card_last4 = Column(String(4), nullable=True)
    card_exp_month = Column(Integer, nullable=True)
    card_exp_year = Column(Integer, nullable=True)


class ProductRatingDB(Base):
    __tablename__ = "product_ratings"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1–5

    order = relationship("OrderDB")
    product = relationship("ProductDB")
    restaurant = relationship("RestaurantDB")

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_product_rating"),
    )


class DriverDB(Base):
    __tablename__ = "drivers"

    id       = Column(Integer, primary_key=True, index=True)
    gid      = Column(String(255), nullable=True, unique=True)
    login    = Column(String(100), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    status   = Column(String(50), default="PENDING")   # PENDING | ACTIVE | INACTIVE

    # ── Informação pessoal ──────────────────────────────────────
    name        = Column(String(255), nullable=True)
    phone       = Column(String(50),  nullable=True)
    email       = Column(String(255), nullable=True)
    birth_date  = Column(String(20),  nullable=True)
    address     = Column(Text,        nullable=True)
    city        = Column(String(100), nullable=True)
    postal_code = Column(String(20),  nullable=True)
    cc          = Column(String(50),  nullable=True)   # Cartão de Cidadão

    # ── Informação fiscal ───────────────────────────────────────
    nif  = Column(String(20),  nullable=True)
    niss = Column(String(20),  nullable=True)
    iban = Column(String(50),  nullable=True)
    stripe_account_id        = Column(String(255), nullable=True)
    stripe_onboarding_completed = Column(Boolean, default=False)

    # ── Informação do veículo ───────────────────────────────────
    vehicle_type             = Column(String(50),  nullable=True)   # MOTORCYCLE, BICYCLE, etc.
    vehicle_plate            = Column(String(20),  nullable=True)
    vehicle_model            = Column(String(100), nullable=True)
    vehicle_color            = Column(String(50),  nullable=True)
    carta_conducao           = Column(String(100), nullable=True)
    carta_conducao_categoria = Column(String(50),  nullable=True)

    # ── Localização (actualizada via polling pelo app do estafeta) ──────────
    latitude   = Column(Float,    nullable=True)   # última latitude conhecida
    longitude  = Column(Float,    nullable=True)   # última longitude conhecida
    last_seen  = Column(DateTime(timezone=True), nullable=True)  # timestamp do último update

    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))


class RestaurantHourDB(Base):
    __tablename__ = "restaurant_hours"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Domingo ... 6=Sábado
    open_time = Column(String(5), nullable=False)   # "HH:mm"
    close_time = Column(String(5), nullable=False)  # "HH:mm"
    is_closed = Column(Boolean, default=False)

    restaurant = relationship("RestaurantDB", back_populates="hours")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "day_of_week", name="uq_restaurant_day"),
    )


class DeliveryZoneDB(Base):
    __tablename__ = "delivery_zones"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    zone          = Column(Integer, nullable=False)          # número da zona (1, 2, 3…)
    radius_km     = Column(Float,   nullable=False)
    price         = Column(Float,   nullable=False)
    enabled       = Column(Boolean, nullable=False, default=True)
    center_lat    = Column(Float,   nullable=True)
    center_lng    = Column(Float,   nullable=True)

    restaurant = relationship("RestaurantDB", back_populates="delivery_zones")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "zone", name="uq_restaurant_zone"),
    )

