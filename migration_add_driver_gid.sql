-- 2026-09-04: Adiciona coluna gid à tabela drivers e driver_gid à sub_orders
-- (substitui a atribuição de estafeta por sub_orders.driver_id, que referenciava drivers.id)

-- Passo 1 (aditivo, seguro): novas colunas, nullable
ALTER TABLE drivers ADD COLUMN gid VARCHAR(255) NULL;
ALTER TABLE sub_orders ADD COLUMN driver_gid VARCHAR(255) NULL;

-- Passo 2: rodar o backfill (scripts/backfill_driver_ulids.py e
-- scripts/backfill_sub_order_driver_gid.py) antes de seguir para o passo 3.

-- Passo 3 (depois do backfill): tornar drivers.gid único e criar a FK nova
ALTER TABLE drivers ADD CONSTRAINT uq_driver_gid UNIQUE (gid);
ALTER TABLE sub_orders ADD CONSTRAINT fk_sub_orders_driver_gid FOREIGN KEY (driver_gid) REFERENCES drivers(gid) ON DELETE SET NULL;

-- ⚠️ Passo 4 — DESTRUTIVO/IRREVERSÍVEL — só executar manualmente depois de validar
-- em produção que toda a aplicação já lê/escreve driver_gid corretamente:
-- ALTER TABLE sub_orders DROP FOREIGN KEY fk_sub_orders_driver;
-- ALTER TABLE sub_orders DROP COLUMN driver_id;
