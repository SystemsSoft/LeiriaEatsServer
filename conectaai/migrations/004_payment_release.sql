-- Pagamento com retenção: o valor fica reservado e só é repassado ao creator quando a empresa confirma que o
-- acordo foi cumprido. Guarda o repasse (transfer da Stripe) e quando foi liberado.
ALTER TABLE payments ADD COLUMN transfer_id VARCHAR(64) DEFAULT '';
ALTER TABLE payments ADD COLUMN released_at DATETIME NULL;
