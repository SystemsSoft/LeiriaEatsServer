# Migrações do ConectaAI

Aplicadas **automaticamente** pelo `deploy_conectaai.sh`, antes de reiniciar o serviço
(`python -m conectaai.scripts.migrate`). O que já rodou fica registrado na tabela `schema_migrations`.

Para mudar o schema de uma tabela que já existe (coluna nova, índice), crie `NNN_descricao.sql` com o próximo
número. Regras e detalhes no cabeçalho de `conectaai/scripts/migrate.py`, principalmente:

- nunca edite um arquivo já aplicado; crie outro;
- uma coluna/índice por comando (um `ALTER TABLE ... ADD COLUMN` por coluna).

Os comentários "rodar manualmente" em 001–003 são de antes do aplicador automático. Essas três já estão
registradas como aplicadas em produção.
