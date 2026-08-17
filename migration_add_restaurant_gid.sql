-- 2026-08-17: Adiciona coluna gid à tabela restaurants
ALTER TABLE restaurants ADD COLUMN gid VARCHAR(255) NULL;
ALTER TABLE restaurants ADD CONSTRAINT uq_restaurant_gid UNIQUE (gid);
