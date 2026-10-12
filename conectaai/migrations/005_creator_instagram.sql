-- "Conectar Instagram": conta do Instagram que o creator provou ser dele pelo login oficial do Instagram
-- (POST /creators/me/instagram). Guarda só o id da conta, o @ e os seguidores lidos na hora — nem senha nem token.
ALTER TABLE creators ADD COLUMN instagram_user_id VARCHAR(40) DEFAULT '';
ALTER TABLE creators ADD COLUMN instagram_username VARCHAR(60) DEFAULT '';
ALTER TABLE creators ADD COLUMN instagram_followers INT DEFAULT 0;
