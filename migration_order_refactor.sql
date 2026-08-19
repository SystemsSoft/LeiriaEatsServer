-- 2026-08-19: Refatoração para pedidos Master e Sub-Pedidos

-- 1. Criar a tabela sub_orders
CREATE TABLE IF NOT EXISTS sub_orders (
    id            INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    gid           VARCHAR(255) NULL UNIQUE,
    order_id      INT NOT NULL,
    restaurant_id INT NULL,
    restaurant_name VARCHAR(255) NULL,
    restaurant_category VARCHAR(100) NULL,
    restaurant_image_url VARCHAR(500) NULL,
    status        VARCHAR(50) DEFAULT 'Pendente',
    total         DOUBLE DEFAULT 0,
    delivery_fee  DOUBLE DEFAULT 0,
    base_time     INT DEFAULT 0,
    driver_id     INT NULL,
    driver_name   VARCHAR(255) NULL,
    driver_delivery_fee DOUBLE NULL,
    driver_payment_transfer_id VARCHAR(255) NULL,
    restaurant_latitude  DOUBLE NULL,
    restaurant_longitude DOUBLE NULL,
    CONSTRAINT fk_sub_orders_master FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CONSTRAINT fk_sub_orders_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL,
    CONSTRAINT fk_sub_orders_driver FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
);

-- 2. Alterar a tabela orders (Master)
ALTER TABLE orders ADD COLUMN gid VARCHAR(255) NULL UNIQUE;
ALTER TABLE orders ADD COLUMN total_delivery_fee DOUBLE DEFAULT 0;
ALTER TABLE orders ADD COLUMN total_service_fee DOUBLE DEFAULT 0;

-- 3. Refatorar order_items para apontar para sub_orders
ALTER TABLE order_items DROP FOREIGN KEY fk_order_items_order; -- Substitua pelo nome real da constraint se for diferente
ALTER TABLE order_items CHANGE COLUMN order_id sub_order_id INT;
ALTER TABLE order_items ADD CONSTRAINT fk_order_items_sub_order FOREIGN KEY (sub_order_id) REFERENCES sub_orders(id) ON DELETE CASCADE;

-- ⚠️ IMPORTANTE: Se houver dados existentes, é necessário um script de migração manual para mover os dados de orders para sub_orders antes de apagar as colunas antigas.
