-- 2026-08-18: Adiciona coluna gid à tabela products
ALTER TABLE products ADD COLUMN gid VARCHAR(255) NULL;
ALTER TABLE products ADD CONSTRAINT uq_product_gid UNIQUE (gid);
