-- Migrações manuais da base de dados LeiriaEatsDB
-- Executar apenas uma vez por ambiente (produção / staging)

-- 2026-03-23: Adiciona coluna stripe_onboarding_completed à tabela drivers
ALTER TABLE drivers ADD COLUMN stripe_onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE;

-- 2026-03-25: Localização do estafeta (polling GPS via app)
ALTER TABLE drivers ADD COLUMN latitude  DOUBLE NULL;
ALTER TABLE drivers ADD COLUMN longitude DOUBLE NULL;
ALTER TABLE drivers ADD COLUMN last_seen DATETIME NULL;

-- 2026-03-25: Estafeta atribuído ao pedido
ALTER TABLE orders ADD COLUMN driver_id   INT          NULL;
ALTER TABLE orders ADD COLUMN driver_name VARCHAR(255) NULL;

-- 2026-03-25: Coordenadas do endereço de entrega (preenchidas na criação do pedido)
ALTER TABLE orders ADD COLUMN delivery_latitude  DOUBLE NULL;
ALTER TABLE orders ADD COLUMN delivery_longitude DOUBLE NULL;

-- 2026-03-25: Coordenadas do restaurante (copiadas na criação para evitar JOIN)
ALTER TABLE orders ADD COLUMN restaurant_latitude  DOUBLE NULL;
ALTER TABLE orders ADD COLUMN restaurant_longitude DOUBLE NULL;

-- 2026-03-26: Taxas de entrega e de serviço
ALTER TABLE orders ADD COLUMN delivery_fee DOUBLE NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN service_fee  DOUBLE NOT NULL DEFAULT 0;

-- 2026-03-26: Valor a pagar ao estafeta pela entrega (calculado no momento em que aceita o pedido)
ALTER TABLE orders ADD COLUMN driver_delivery_fee DOUBLE NULL;

-- 2026-03-26: ID do Transfer Stripe gerado quando o estafeta marca o pedido como entregue
ALTER TABLE orders ADD COLUMN driver_payment_transfer_id VARCHAR(255) NULL;

-- 2026-03-27: Indica se o restaurante utiliza estafeta próprio (TRUE) ou da plataforma (FALSE)
ALTER TABLE restaurants ADD COLUMN use_own_delivery BOOLEAN NOT NULL DEFAULT FALSE;
-- 2026-03-27: Renomeia coluna uses_own_courier → use_own_delivery (caso tenha sido criada antes)
-- ALTER TABLE restaurants RENAME COLUMN uses_own_courier TO use_own_delivery;

-- 2026-03-27: Zonas de entrega por restaurante
CREATE TABLE IF NOT EXISTS delivery_zones (
    id            INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    restaurant_id INT          NOT NULL,
    zone          INT          NOT NULL,
    radius_km     DOUBLE       NOT NULL,
    price         DOUBLE       NOT NULL,
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    center_lat    DOUBLE       NULL,
    center_lng    DOUBLE       NULL,
    CONSTRAINT uq_restaurant_zone UNIQUE (restaurant_id, zone),
    CONSTRAINT fk_delivery_zones_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
);

-- 2026-04-05: Permite NULL na coluna rating da tabela products (para concordar com o modelo)
ALTER TABLE products MODIFY COLUMN rating DOUBLE NULL DEFAULT NULL;

