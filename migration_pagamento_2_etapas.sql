-- 2026-08-27: Pagamento em 2 etapas (PLANO_PAGAMENTO_2_ETAPAS.md)
-- Todas as colunas são NULL ou têm DEFAULT — aditivo, sem reescrita de dados.
--
-- ⚠️ NÃO EXECUTAR EM PRODUÇÃO SEM: validar em homologação, backup verificado,
-- janela definida. Ver PLANO_PAGAMENTO_2_ETAPAS.md, seção 5, para o motivo de
-- cada coluna.

ALTER TABLE orders ADD COLUMN payment_flow VARCHAR(20) NULL DEFAULT 'AUTO_CAPTURE';
ALTER TABLE orders ADD COLUMN payment_status VARCHAR(50) NULL;
ALTER TABLE orders ADD COLUMN authorized_amount DOUBLE NULL;
ALTER TABLE orders ADD COLUMN captured_amount DOUBLE NULL;
ALTER TABLE orders ADD COLUMN authorization_expires_at DATETIME NULL;

-- Backfill: pedidos existentes são todos do fluxo antigo (cobrança imediata).
UPDATE orders SET payment_flow = 'AUTO_CAPTURE' WHERE payment_flow IS NULL;

ALTER TABLE sub_orders ADD COLUMN accepted_at DATETIME NULL;
ALTER TABLE sub_orders ADD COLUMN declined_at DATETIME NULL;
ALTER TABLE sub_orders ADD COLUMN decline_reason VARCHAR(255) NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_id VARCHAR(255) NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_amount DOUBLE NULL;
ALTER TABLE sub_orders ADD COLUMN stripe_transfer_reversed DOUBLE NULL DEFAULT 0;
