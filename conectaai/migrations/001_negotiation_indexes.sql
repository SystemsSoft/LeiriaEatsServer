-- Arquivo: conectaai/migrations/001_negotiation_indexes.sql
--
-- Não existe Alembic neste módulo (só Base.metadata.create_all, que cria
-- tabela ausente mas nunca altera uma existente). Os índices de coluna única
-- (owner_id, state, negotiation_id, lease_expires_at, created_at) já nascem
-- automaticamente com `index=True` nos models, na criação da tabela. Este
-- arquivo é só para os índices COMPOSTOS, que create_all não cobre e que só
-- importam em volume real de dados — aplicar manualmente depois do primeiro
-- deploy que cria as tabelas novas.
--
-- Revisar e rodar manualmente (ex.: `mysql conectaaiDB < 001_negotiation_indexes.sql`)
-- fora de horário de pico. DDL em MySQL trava a tabela durante a criação do
-- índice (a depender do engine/versão) — não é uma operação "grátis".

CREATE INDEX idx_negotiations_company_state ON negotiations (company_id, state);
CREATE INDEX idx_negotiations_creator_state ON negotiations (creator_id, state);

-- Usado pelo sweep de startup (main.py) para achar negociações com lease
-- vencida sem varrer a tabela inteira.
CREATE INDEX idx_negotiations_state_lease ON negotiations (state, lease_expires_at);

CREATE INDEX idx_negotiation_turns_negotiation_round ON negotiation_turns (negotiation_id, round_no);

CREATE INDEX idx_commercial_mandates_owner_active ON commercial_mandates (owner_type, owner_id, active);

CREATE INDEX idx_agreements_company ON agreements (company_id, status);
CREATE INDEX idx_agreements_creator ON agreements (creator_id, status);

CREATE INDEX idx_entity_embeddings_type ON entity_embeddings (entity_type, entity_id);
