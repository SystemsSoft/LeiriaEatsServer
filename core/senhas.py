# Arquivo: core/senhas.py
"""Hash de senhas de restaurantes e estafetas: PBKDF2-SHA256 com salt por conta (só stdlib).

Formato guardado:  pbkdf2_sha256$<iterações>$<salt>$<hash>

Contas antigas: os estafetas guardavam sha256(senha) sem salt e os restaurantes guardavam a
senha em texto simples (revisão de segurança de 12/11/2026). As duas continuam a entrar e
são convertidas para PBKDF2 no primeiro login (ver precisa_rehash); o script
scripts/hash_senhas_restaurantes.py converte as que ainda não entraram.
"""
import hashlib
import hmac
import secrets

PBKDF2_ITERACOES = 310_000
_PREFIXO = "pbkdf2_sha256$"


def gerar_hash(senha: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), PBKDF2_ITERACOES).hex()
    return f"{_PREFIXO}{PBKDF2_ITERACOES}${salt}${digest}"


def verificar(senha: str, guardada: str, aceitar_texto_simples: bool = False) -> bool:
    """True se `senha` corresponde ao que está guardado: PBKDF2, sha256 sem salt (estafetas
    antigos) ou, com aceitar_texto_simples, a própria senha (restaurantes antigos)."""
    guardada = guardada or ""
    if guardada.startswith(_PREFIXO):
        try:
            _, iteracoes, salt, digest = guardada.split("$", 3)
            candidato = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), int(iteracoes)).hex()
        except ValueError:
            return False
        return hmac.compare_digest(candidato, digest)
    if hmac.compare_digest(hashlib.sha256(senha.encode()).hexdigest(), guardada):
        return True
    return aceitar_texto_simples and hmac.compare_digest(senha, guardada)


def precisa_rehash(guardada: str) -> bool:
    return not (guardada or "").startswith(f"{_PREFIXO}{PBKDF2_ITERACOES}$")
