-- Arquivo: conectaai/migrations/003_proposal_sender_role.sql
--
-- Propostas agora podem partir do creator para a empresa (candidatura a uma campanha), além do
-- caminho original empresa -> creator. `sender_role` guarda quem enviou e define quem pode
-- aceitar/recusar (sempre o outro lado). Não existe Alembic neste módulo (só create_all, que nunca
-- altera tabela existente): em qualquer banco onde `proposals` já existe, rodar este ALTER
-- manualmente ANTES de subir esta versão — senão a API quebra ao ler/gravar a coluna.
--
-- As propostas já existentes eram todas da empresa; o DEFAULT cobre isso sem backfill.
--
-- Revisar e rodar manualmente (ex.: `mysql conectaaiDB < 003_proposal_sender_role.sql`) fora de
-- horário de pico — mesmo cuidado de 002 (DDL em MySQL pode travar a tabela durante o ALTER).

ALTER TABLE proposals
  ADD COLUMN sender_role VARCHAR(20) NOT NULL DEFAULT 'company';
