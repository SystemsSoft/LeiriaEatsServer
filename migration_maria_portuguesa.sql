-- Maria Portuguesa: banco de dados e tabelas.
-- Gerado por: python scripts/maria_portuguesa_criar_banco.py --sql
-- O servidor executa o equivalente a isto ao arrancar (modules/maria_portuguesa/database.py).

CREATE DATABASE IF NOT EXISTS `MariaPortuguesaDB` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `MariaPortuguesaDB`;

CREATE TABLE IF NOT EXISTS domesticas (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	gid VARCHAR(32) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	senha_hash VARCHAR(255) NOT NULL, 
	firebase_uid VARCHAR(128), 
	consentimento_rgpd_em DATETIME NOT NULL, 
	nome VARCHAR(120) NOT NULL, 
	titulo VARCHAR(140), 
	sobre TEXT, 
	foto_url VARCHAR(500), 
	distrito VARCHAR(60), 
	concelho VARCHAR(80), 
	servicos JSON NOT NULL, 
	anos_experiencia INTEGER, 
	preco_hora FLOAT, 
	dias JSON NOT NULL, 
	periodos JSON NOT NULL, 
	regimes JSON NOT NULL, 
	idiomas JSON NOT NULL, 
	tem_transporte BOOL NOT NULL, 
	aceita_animais BOOL NOT NULL, 
	passa_recibo BOOL NOT NULL, 
	tem_referencias BOOL NOT NULL, 
	telemovel VARCHAR(30), 
	mostrar_contacto BOOL NOT NULL, 
	publicado BOOL NOT NULL, 
	premium_ate DATETIME, 
	premium_renova BOOL NOT NULL, 
	premium_estado VARCHAR(32), 
	stripe_cliente_id VARCHAR(64), 
	stripe_assinatura_id VARCHAR(64), 
	embedding BLOB, 
	criado_em DATETIME NOT NULL, 
	atualizado_em DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_domesticas_distrito ON domesticas (distrito);

CREATE UNIQUE INDEX ix_domesticas_email ON domesticas (email);

CREATE UNIQUE INDEX ix_domesticas_firebase_uid ON domesticas (firebase_uid);

CREATE UNIQUE INDEX ix_domesticas_gid ON domesticas (gid);

CREATE INDEX ix_domesticas_id ON domesticas (id);

CREATE INDEX ix_domesticas_publicado ON domesticas (publicado);

CREATE INDEX ix_domesticas_stripe_assinatura_id ON domesticas (stripe_assinatura_id);

CREATE TABLE IF NOT EXISTS sessoes (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	token_hash VARCHAR(64) NOT NULL, 
	domestica_id INTEGER NOT NULL, 
	expira_em DATETIME NOT NULL, 
	criado_em DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(domestica_id) REFERENCES domesticas (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessoes_domestica_id ON sessoes (domestica_id);

CREATE UNIQUE INDEX ix_sessoes_token_hash ON sessoes (token_hash);

-- Instalações anteriores: a tabela domesticas já existe sem as colunas
-- acrescentadas depois (Firebase e Premium). O servidor acrescenta-as ao arrancar
-- (_acrescentar_colunas_em_falta em database.py); à mão seria:
--   ALTER TABLE domesticas ADD COLUMN firebase_uid VARCHAR(128) NULL;
--   CREATE UNIQUE INDEX ix_domesticas_firebase_uid ON domesticas (firebase_uid);
--   ALTER TABLE domesticas ADD COLUMN premium_ate DATETIME NULL;
--   ALTER TABLE domesticas ADD COLUMN premium_renova BOOLEAN NOT NULL DEFAULT 0;
--   ALTER TABLE domesticas ADD COLUMN premium_estado VARCHAR(32) NULL;
--   ALTER TABLE domesticas ADD COLUMN stripe_cliente_id VARCHAR(64) NULL;
--   ALTER TABLE domesticas ADD COLUMN stripe_assinatura_id VARCHAR(64) NULL;
--   CREATE INDEX ix_domesticas_stripe_assinatura_id ON domesticas (stripe_assinatura_id);
--
-- A tabela desbloqueios (contacto pago, retirado) deixou de ser usada:
--   DROP TABLE IF EXISTS desbloqueios;
