-- Arquivo: conectaai/migrations/002_company_profile_fields.sql
--
-- Não existe Alembic neste módulo (só Base.metadata.create_all, que cria
-- tabela ausente mas nunca altera uma existente — ver comentário em
-- models/sql_models.py). Estas colunas foram adicionadas em `CompanyDB`
-- (bio, avatar_url, portfolio) para espelhar o que já existe em `CreatorDB`
-- e permitir editar o perfil público da empresa. Em qualquer banco onde a
-- tabela `companies` já existe, rodar este ALTER manualmente — senão a API
-- vai quebrar ao tentar ler/gravar essas colunas.
--
-- Revisar e rodar manualmente (ex.: `mysql conectaaiDB < 002_company_profile_fields.sql`)
-- fora de horário de pico. DDL em MySQL trava a tabela durante o ALTER (a
-- depender do engine/versão) — não é uma operação "grátis", mas para colunas
-- novas com valor padrão simples (sem backfill) o impacto tende a ser baixo
-- mesmo em tabelas grandes, no InnoDB/MySQL 8+ (INSTANT ALTER). Confirmar a
-- versão do MySQL em uso antes de assumir isso.

ALTER TABLE companies
  ADD COLUMN bio TEXT NULL,
  ADD COLUMN avatar_url VARCHAR(500) NULL DEFAULT '',
  ADD COLUMN portfolio JSON NULL;
