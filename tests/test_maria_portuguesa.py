"""
Testes do módulo Maria Portuguesa (modules/maria_portuguesa).

Não tocam na base de dados real nem no Gemini: SQLite em memória, um codificador
de texto falso (saco de palavras) e uma sessão Live falsa.

Execução:
    python3 tests/test_maria_portuguesa.py
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
# O banco do módulo aponta para um ficheiro SQLite temporário: nenhum teste chega ao MySQL.
_FICHEIRO_BD = os.path.join(tempfile.mkdtemp(prefix="mp_testes_"), "maria_portuguesa.db")
os.environ["MP_DATABASE_URL"] = "sqlite:///" + _FICHEIRO_BD.replace("\\", "/")

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from modules import maria_portuguesa
from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa import database as banco_mp
from modules.maria_portuguesa.database import Base, get_db
from modules.maria_portuguesa.firebase_auth import ContaFirebase, TokenFirebaseInvalido, VerificadorFirebase
from modules.maria_portuguesa.live_service import AssistenteVoz, configuracao_live
from modules.maria_portuguesa.models import MpDomesticaDB, MpSessaoDB
from modules.maria_portuguesa.routes import get_fabrica_db
from modules.maria_portuguesa.search_service import BuscaService
from modules.maria_portuguesa.security import gerar_hash_senha, verificar_senha


# ── Infraestrutura de teste ──────────────────────────────────────────────────

def _codificador_falso(textos: list[str]) -> np.ndarray:
    """Saco de palavras com hashing: textos que partilham palavras ficam próximos."""
    vetores = np.zeros((len(textos), 256), dtype=np.float32)
    for i, texto in enumerate(textos):
        for palavra in catalogo.normalizar(texto.split(": ", 1)[-1]).replace(".", " ").replace(",", " ").split():
            if len(palavra) > 2:
                vetores[i, int(hashlib.md5(palavra.encode()).hexdigest(), 16) % 256] += 1.0
        norma = np.linalg.norm(vetores[i])
        if norma:
            vetores[i] /= norma
    return vetores


def _novo_cliente() -> tuple[TestClient, sessionmaker]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Sessao = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _get_db():
        db = Sessao()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(maria_portuguesa.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_fabrica_db] = lambda: Sessao
    BuscaService.definir_codificador(_codificador_falso)
    return TestClient(app), Sessao


def _registar(cliente: TestClient, email: str, nome: str = "Ana Teste") -> dict:
    r = cliente.post("/maria-portuguesa/domesticas/registo", json={
        "nome": nome, "email": email, "senha": "segredo-de-teste", "consentimento_rgpd": True,
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _publicar(cliente: TestClient, email: str, nome: str, **perfil) -> str:
    cabecalhos = _registar(cliente, email, nome)
    dados = {
        "distrito": "Leiria", "concelho": "Leiria", "servicos": ["limpeza"],
        "sobre": "Faço limpeza de casas com cuidado e pontualidade.", "publicado": True,
    }
    dados.update(perfil)
    r = cliente.put("/maria-portuguesa/domesticas/eu", json=dados, headers=cabecalhos)
    assert r.status_code == 200, r.text
    return r.json()["gid"]


# ── Banco de dados ───────────────────────────────────────────────────────────

def teste_banco_cria_tabelas_e_e_idempotente():
    from sqlalchemy import inspect
    assert banco_mp.criar_banco_e_tabelas() == ["domesticas", "sessoes"]
    assert banco_mp.criar_banco_e_tabelas() == ["domesticas", "sessoes"]   # segunda vez não falha

    colunas = {c["name"] for c in inspect(banco_mp.engine).get_columns("domesticas")}
    assert {"gid", "email", "senha_hash", "firebase_uid", "servicos", "embedding", "publicado"} <= colunas
    print("OK  - banco: cria as tabelas domesticas e sessoes, e pode correr várias vezes")


def teste_banco_acrescenta_firebase_uid_a_tabela_antiga():
    from sqlalchemy import inspect, text
    banco_mp.criar_banco_e_tabelas()
    # Simula uma instalação anterior ao Firebase: a tabela existe sem a coluna.
    with banco_mp.engine.begin() as ligacao:
        ligacao.execute(text("DROP INDEX ix_domesticas_firebase_uid"))
        ligacao.execute(text("ALTER TABLE domesticas DROP COLUMN firebase_uid"))
    assert "firebase_uid" not in {c["name"] for c in inspect(banco_mp.engine).get_columns("domesticas")}
    banco_mp.criar_banco_e_tabelas()
    banco_mp.criar_banco_e_tabelas()
    assert "firebase_uid" in {c["name"] for c in inspect(banco_mp.engine).get_columns("domesticas")}
    print("OK  - banco: acrescenta firebase_uid a uma tabela domesticas que já existia")


def teste_banco_e_separado_do_leiria_eats():
    from core.database import Base as BaseLeiriaEats
    assert set(Base.metadata.tables) == {"domesticas", "sessoes"}
    assert not set(Base.metadata.tables) & set(BaseLeiriaEats.metadata.tables)
    print("OK  - banco: as tabelas do módulo não se misturam com as do Leiria Eats")


def teste_ddl_mysql_e_valido():
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable
    ddl = {t.name: str(CreateTable(t).compile(dialect=mysql.dialect())) for t in Base.metadata.sorted_tables}
    assert list(ddl) == ["domesticas", "sessoes"]          # a tabela referenciada vem primeiro
    assert "email VARCHAR(255) NOT NULL" in ddl["domesticas"]
    assert "servicos JSON NOT NULL" in ddl["domesticas"]
    assert "embedding BLOB" in ddl["domesticas"]
    assert "FOREIGN KEY(domestica_id) REFERENCES domesticas (id) ON DELETE CASCADE" in ddl["sessoes"]
    print("OK  - banco: o SQL gerado para MySQL tem os tipos e a chave estrangeira esperados")


# ── Conta e perfil ───────────────────────────────────────────────────────────

def teste_hash_de_senha():
    h = gerar_hash_senha("segredo-de-teste")
    assert "segredo-de-teste" not in h
    assert verificar_senha("segredo-de-teste", h)
    assert not verificar_senha("outra-coisa", h)
    assert not verificar_senha("segredo-de-teste", "lixo")
    print("OK  - palavra-passe é guardada com scrypt e verificada corretamente")


def teste_registo_exige_consentimento_e_email_unico():
    cliente, _ = _novo_cliente()
    base = {"nome": "Ana", "email": "ana@exemplo.pt", "senha": "segredo-de-teste"}
    assert cliente.post("/maria-portuguesa/domesticas/registo",
                        json={**base, "consentimento_rgpd": False}).status_code == 422
    _registar(cliente, "ana@exemplo.pt")
    r = cliente.post("/maria-portuguesa/domesticas/registo", json={**base, "email": "ANA@exemplo.pt ", "consentimento_rgpd": True})
    assert r.status_code == 409
    print("OK  - registo exige consentimento RGPD e recusa email repetido")


def teste_login_e_sessao():
    cliente, _ = _novo_cliente()
    cabecalhos = _registar(cliente, "ana@exemplo.pt")
    assert cliente.get("/maria-portuguesa/domesticas/eu").status_code == 401
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers={"Authorization": "Bearer inventado"}).status_code == 401
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).json()["email"] == "ana@exemplo.pt"

    assert cliente.post("/maria-portuguesa/domesticas/login",
                        json={"email": "ana@exemplo.pt", "senha": "errada-errada"}).status_code == 401
    r = cliente.post("/maria-portuguesa/domesticas/login", json={"email": "ana@exemplo.pt", "senha": "segredo-de-teste"})
    assert r.status_code == 200
    novos = {"Authorization": f"Bearer {r.json()['token']}"}

    assert cliente.post("/maria-portuguesa/domesticas/sair", headers=novos).status_code == 204
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers=novos).status_code == 401
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).status_code == 200
    print("OK  - login, sessão por token e terminar sessão")


def _verificador_falso(id_token: str) -> ContaFirebase:
    """Tokens de teste no formato «uid|email|verificado»."""
    try:
        uid, email, verificado = id_token.split("|")
    except ValueError:
        raise TokenFirebaseInvalido("formato de teste inválido")
    return ContaFirebase(uid=uid, email=email, email_verificado=verificado == "sim")


def teste_criar_perfil_e_entrar_com_firebase():
    cliente, Sessao = _novo_cliente()
    rota = "/maria-portuguesa/domesticas/firebase"
    VerificadorFirebase.definir_verificador(_verificador_falso)
    try:
        assert cliente.post(rota, json={"id_token": "lixo"}).status_code == 401
        # Conta Firebase sem perfil: entrar não cria nada; criar exige nome e consentimento.
        assert cliente.post(rota, json={"id_token": "u1|ana@exemplo.pt|nao"}).status_code == 404
        assert cliente.post(rota, json={"id_token": "u1|ana@exemplo.pt|nao", "nome": "Ana"}).status_code == 422

        r = cliente.post(rota, json={"id_token": "u1|ana@exemplo.pt|nao", "nome": " Ana ", "consentimento_rgpd": True})
        assert r.status_code == 200, r.text
        assert r.json()["criado"] is True
        assert r.json()["perfil"]["nome"] == "Ana" and r.json()["perfil"]["email"] == "ana@exemplo.pt"
        cabecalhos = {"Authorization": f"Bearer {r.json()['token']}"}
        assert cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).status_code == 200

        # Entrar de novo devolve o mesmo perfil, com ou sem os dados de registo.
        r2 = cliente.post(rota, json={"id_token": "u1|ana@exemplo.pt|nao"})
        assert r2.status_code == 200 and r2.json()["criado"] is False
        assert r2.json()["perfil"]["gid"] == r.json()["perfil"]["gid"]
        with Sessao() as db:
            assert db.query(MpDomesticaDB).count() == 1

        # A conta não tem palavra-passe neste servidor: o login antigo não entra.
        for senha in ("firebase", "segredo-de-teste"):
            assert cliente.post("/maria-portuguesa/domesticas/login",
                                json={"email": "ana@exemplo.pt", "senha": senha}).status_code == 401
        # Outro utilizador Firebase com o mesmo email não fica com o perfil.
        assert cliente.post(rota, json={"id_token": "u2|ana@exemplo.pt|sim"}).status_code == 409
    finally:
        VerificadorFirebase.definir_verificador(None)
    print("OK  - Firebase: cria o perfil com nome e consentimento e entra com o ID token")


def teste_firebase_so_liga_conta_antiga_com_email_verificado():
    cliente, Sessao = _novo_cliente()
    _registar(cliente, "ana@exemplo.pt")          # conta anterior, com palavra-passe no servidor
    rota = "/maria-portuguesa/domesticas/firebase"
    VerificadorFirebase.definir_verificador(_verificador_falso)
    try:
        pedido = {"nome": "Intrusa", "consentimento_rgpd": True}
        assert cliente.post(rota, json={**pedido, "id_token": "u9|ana@exemplo.pt|nao"}).status_code == 409
        r = cliente.post(rota, json={**pedido, "id_token": "u9|ana@exemplo.pt|sim"})
        assert r.status_code == 200 and r.json()["criado"] is False
        assert r.json()["perfil"]["nome"] == "Ana Teste"      # o perfil que já existia, intacto
        with Sessao() as db:
            assert db.query(MpDomesticaDB).one().firebase_uid == "u9"
    finally:
        VerificadorFirebase.definir_verificador(None)
    print("OK  - Firebase: conta antiga só é ligada quando o email está verificado")


def teste_token_firebase_verdadeiro_e_validado():
    # Sem verificador de teste: um token inventado tem de ser recusado pela verificação real.
    cliente, _ = _novo_cliente()
    r = cliente.post("/maria-portuguesa/domesticas/firebase", json={"id_token": "a.b.c"})
    assert r.status_code == 401, r.text
    print("OK  - Firebase: um ID token inventado é recusado pela verificação real")


def teste_publicar_exige_perfil_minimo():
    cliente, _ = _novo_cliente()
    cabecalhos = _registar(cliente, "ana@exemplo.pt")
    r = cliente.put("/maria-portuguesa/domesticas/eu", json={"publicado": True}, headers=cabecalhos)
    assert r.status_code == 422 and "distrito" in r.json()["detail"]
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).json()["publicado"] is False

    r = cliente.put("/maria-portuguesa/domesticas/eu", headers=cabecalhos, json={
        "distrito": "leiria", "concelho": "Pombal", "servicos": ["engomar", "limpeza", "engomar"],
        "sobre": "Trabalho há muitos anos em casas de família.", "publicado": True,
    })
    assert r.status_code == 200, r.text
    perfil = r.json()
    assert perfil["publicado"] is True and perfil["em_falta"] == []
    assert perfil["distrito"] == "Leiria"                 # nome canónico
    assert perfil["servicos"] == ["limpeza", "engomar"]   # sem repetidos, ordem do catálogo

    r = cliente.put("/maria-portuguesa/domesticas/eu", json={"servicos": ["astronauta"]}, headers=cabecalhos)
    assert r.status_code == 422
    print("OK  - publicar exige zona, serviços e apresentação; valores são normalizados")


def teste_perfil_publico_protege_dados():
    cliente, _ = _novo_cliente()
    cabecalhos = _registar(cliente, "ana@exemplo.pt")
    assert cliente.put("/maria-portuguesa/domesticas/eu", headers=cabecalhos, json={
        "distrito": "Leiria", "concelho": "Leiria", "servicos": ["limpeza"], "sobre": "Olá, sou a Ana.",
        "telemovel": "912 345 678",
    }).status_code == 200
    gid = cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).json()["gid"]

    # Rascunho não é visível para ninguém.
    assert cliente.get(f"/maria-portuguesa/domesticas/{gid}").status_code == 404

    cliente.put("/maria-portuguesa/domesticas/eu", json={"publicado": True}, headers=cabecalhos)
    publico = cliente.get(f"/maria-portuguesa/domesticas/{gid}").json()
    assert "email" not in publico and "senha_hash" not in publico and "embedding" not in publico
    assert publico["telemovel"] is None

    cliente.put("/maria-portuguesa/domesticas/eu", json={"mostrar_contacto": True}, headers=cabecalhos)
    assert cliente.get(f"/maria-portuguesa/domesticas/{gid}").json()["telemovel"] == "912 345 678"
    print("OK  - rascunho fica oculto; email nunca sai; telemóvel só com autorização")


def teste_apagar_conta():
    cliente, Sessao = _novo_cliente()
    cabecalhos = _registar(cliente, "ana@exemplo.pt")
    assert cliente.delete("/maria-portuguesa/domesticas/eu", headers=cabecalhos).status_code == 204
    assert cliente.get("/maria-portuguesa/domesticas/eu", headers=cabecalhos).status_code == 401
    db = Sessao()
    assert db.query(MpDomesticaDB).count() == 0 and db.query(MpSessaoDB).count() == 0
    db.close()
    print("OK  - apagar conta remove perfil e sessões")


# ── Busca ────────────────────────────────────────────────────────────────────

def _busca(cliente: TestClient, **pedido) -> dict:
    r = cliente.post("/maria-portuguesa/busca", json=pedido)
    assert r.status_code == 200, r.text
    return r.json()


def teste_busca_ordena_por_afinidade():
    cliente, _ = _novo_cliente()
    _publicar(cliente, "a@exemplo.pt", "Ana", servicos=["limpeza", "engomar"],
              sobre="Gosto de passar a ferro camisas e deixar a roupa impecável.")
    idosos = _publicar(cliente, "b@exemplo.pt", "Beatriz", servicos=["idosos"],
                       sobre="Acompanho idosos com paciência, ajudo nas refeições e na medicação.")
    _registar(cliente, "rascunho@exemplo.pt", "Rascunho")  # não publicado

    resultado = _busca(cliente, consulta="alguém com paciência para acompanhar idosos")
    assert resultado["total"] == 2
    assert resultado["resultados"][0]["gid"] == idosos
    assert resultado["resultados"][0]["afinidade"] > resultado["resultados"][1]["afinidade"]
    print("OK  - busca ordena por afinidade semântica e ignora rascunhos")


def teste_busca_por_localidade():
    cliente, _ = _novo_cliente()
    leiria = _publicar(cliente, "a@exemplo.pt", "Ana", concelho="Leiria")
    pombal = _publicar(cliente, "b@exemplo.pt", "Beatriz", concelho="Pombal")
    _publicar(cliente, "c@exemplo.pt", "Carla", distrito="Porto", concelho="Porto")

    # Nome de distrito: aceita o distrito todo, com preferência pelo concelho homónimo.
    resultado = _busca(cliente, consulta="limpeza de casas", localidade="leiria")
    assert [p["gid"] for p in resultado["resultados"]] == [leiria, pombal]

    # Nome de concelho: só esse concelho.
    resultado = _busca(cliente, consulta="limpeza de casas", localidade="Pombal")
    assert [p["gid"] for p in resultado["resultados"]] == [pombal]

    # Zona sem ninguém não é relaxada.
    assert _busca(cliente, consulta="limpeza de casas", localidade="Faro")["total"] == 0
    print("OK  - localidade filtra por concelho ou distrito e nunca é relaxada")


def teste_busca_relaxa_dia_e_preco_mas_nao_servico():
    cliente, _ = _novo_cliente()
    gid = _publicar(cliente, "a@exemplo.pt", "Ana", dias=["seg", "qua"], preco_hora=12.0)

    resultado = _busca(cliente, consulta="limpeza", dias=["qua"], preco_max=15)
    assert resultado["filtros_relaxados"] == [] and resultado["resultados"][0]["gid"] == gid

    resultado = _busca(cliente, consulta="limpeza", dias=["sab"], preco_max=8)
    assert resultado["filtros_relaxados"] == ["dias", "preco_max"] and resultado["total"] == 1

    resultado = _busca(cliente, consulta="limpeza", servicos=["idosos"])
    assert resultado["total"] == 0
    print("OK  - dia e preço são relaxados (e assinalados); serviço indispensável não")


def teste_busca_indexa_perfil_sem_vetor():
    cliente, Sessao = _novo_cliente()
    gid = _publicar(cliente, "a@exemplo.pt", "Ana")
    db = Sessao()
    db.query(MpDomesticaDB).update({MpDomesticaDB.embedding: None})
    db.commit()
    db.close()

    assert _busca(cliente, consulta="limpeza de casas")["resultados"][0]["gid"] == gid
    db = Sessao()
    assert db.query(MpDomesticaDB).first().embedding is not None
    db.close()
    print("OK  - perfil sem vetor é indexado na primeira pesquisa")


def teste_listagem_sem_consulta():
    cliente, _ = _novo_cliente()
    _publicar(cliente, "a@exemplo.pt", "Ana")
    porto = _publicar(cliente, "b@exemplo.pt", "Beatriz", distrito="Porto", concelho="Gaia")
    assert cliente.get("/maria-portuguesa/domesticas").json()["total"] == 2
    r = cliente.get("/maria-portuguesa/domesticas", params={"distrito": "Porto"}).json()
    assert [p["gid"] for p in r["resultados"]] == [porto]
    print("OK  - listagem pública filtra por distrito")


# ── Assistente de voz ────────────────────────────────────────────────────────

def teste_configuracao_live_e_valida():
    from google.genai import types
    config = types.LiveConnectConfig(**configuracao_live())
    nomes = [f.name for f in config.tools[0].function_declarations]
    assert nomes == ["procurar_domesticas", "abrir_perfil"]
    print("OK  - configuração do Gemini Live é aceite pelo SDK")


class _SessaoLiveFalsa:
    """Imita o que o AssistenteVoz usa de google.genai.live.AsyncSession: um turno
    em que o modelo pede uma busca e depois fala, e a seguir fecha."""

    def __init__(self, turnos=None, falha_no_fim: bool = False):
        self.enviado = []
        self.respostas_ferramenta = []
        self._turnos = turnos if turnos is not None else [self._turno_com_busca(), self._turno_falado()]
        # Em vez de fechar no fim dos turnos, a sessão rebenta (quota, ligação caída).
        self._falha_no_fim = falha_no_fim

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def send_realtime_input(self, **kwargs):
        self.enviado.append(kwargs)

    async def send_tool_response(self, function_responses):
        self.respostas_ferramenta.extend(function_responses)

    @staticmethod
    def _turno_com_busca():
        chamada = SimpleNamespace(id="c1", name="procurar_domesticas", args={
            "consulta": "limpeza de casas", "localidade": "Leiria", "preco_max": 20,
        })
        return [SimpleNamespace(tool_call=SimpleNamespace(function_calls=[chamada]), server_content=None)]

    @staticmethod
    def _turno_falado(texto: str = "Encontrei uma pessoa.", audio: bytes = b"\x01\x02"):
        conteudo = SimpleNamespace(
            interrupted=False,
            model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=audio))]),
            input_transcription=None,
            output_transcription=SimpleNamespace(text=texto),
            turn_complete=True,
        )
        return [SimpleNamespace(tool_call=None, server_content=conteudo)]

    async def receive(self):
        if not self._turnos:
            if self._falha_no_fim:
                raise RuntimeError("1011 quota esgotada")
            await asyncio.sleep(0.2)   # dá tempo ao teste de ler tudo antes de fechar
            return
        for mensagem in self._turnos.pop(0):
            yield mensagem


def _esperar_fecho(ws) -> list[dict]:
    """Lê até o servidor fechar a ligação e devolve o que ainda chegou antes. Sair do
    bloco `with` com a rota ainda a correr faz o TestClient cancelá-la a meio."""
    mensagens = []
    while True:
        mensagem = ws.receive()
        if mensagem["type"] == "websocket.close":
            return mensagens
        mensagens.append(json.loads(mensagem["text"]) if mensagem.get("text") else mensagem["bytes"])


def _com_sessoes_live(sessoes: dict):
    """Uma chave de API por sessão falsa; uma exceção no lugar da sessão = chave que nem abre."""
    from core.config import settings

    def _abrir(chave: str):
        if isinstance(sessoes[chave], Exception):
            raise sessoes[chave]
        return sessoes[chave]

    originais = settings.GEMINI_API_KEYS, AssistenteVoz._abrir_sessao_live
    settings.GEMINI_API_KEYS = list(sessoes)
    AssistenteVoz._abrir_sessao_live = staticmethod(_abrir)

    def _repor():
        settings.GEMINI_API_KEYS, AssistenteVoz._abrir_sessao_live = originais
    return _repor


def teste_voz_troca_de_chave_a_meio_sem_cair():
    cliente, _ = _novo_cliente()
    _publicar(cliente, "a@exemplo.pt", "Ana")
    Falsa = _SessaoLiveFalsa
    primeira = Falsa(falha_no_fim=True)                       # procura, fala e depois rebenta
    terceira = Falsa(turnos=[Falsa._turno_falado("Já a ouço outra vez.", b"\x03")])
    repor = _com_sessoes_live({"k1": primeira, "k2": RuntimeError("chave sem acesso"), "k3": terceira})
    try:
        with cliente.websocket_connect("/maria-portuguesa/voz") as ws:
            assert ws.receive_json() == {"type": "ready"}
            recebido = _esperar_fecho(ws)
    finally:
        repor()

    tipos = [m if isinstance(m, bytes) else m["type"] for m in recebido]
    # A mesma ligação: a conversa da 1.ª chave, o corte do áudio e a continuação com a 3.ª.
    assert tipos == ["searching", "results", b"\x01\x02", "transcript", "turn_complete",
                     "interrupted", b"\x03", "transcript", "turn_complete"], tipos
    assert "error" not in tipos and "ready" not in tipos

    # A sessão nova não se apresenta de novo: recebe a conversa e o que está no ecrã.
    retoma = terceira.enviado[0]["text"]
    assert retoma != "Olá" and "não voltes a apresentar-te" in retoma
    assert "Maria: Encontrei uma pessoa." in retoma and '"nome": "Ana"' in retoma
    assert AssistenteVoz._sessoes_ativas == 0
    print("OK  - voz: a chave falha a meio e a conversa continua com outra na mesma ligação")


def teste_voz_desiste_quando_todas_as_chaves_falham():
    cliente, _ = _novo_cliente()
    # Abrem, mas rebentam logo sem entregar nada: uma volta completa e desiste.
    sessoes = {"k1": _SessaoLiveFalsa(turnos=[], falha_no_fim=True),
               "k2": _SessaoLiveFalsa(turnos=[], falha_no_fim=True)}
    repor = _com_sessoes_live(sessoes)
    try:
        with cliente.websocket_connect("/maria-portuguesa/voz") as ws:
            assert ws.receive_json() == {"type": "ready"}
            recebido = _esperar_fecho(ws)
    finally:
        repor()
    assert [m["type"] for m in recebido] == ["interrupted", "interrupted", "error"], recebido
    assert sessoes["k1"].enviado[0] == {"text": "Olá"} and len(sessoes["k2"].enviado) == 1
    assert AssistenteVoz._sessoes_ativas == 0
    print("OK  - voz: quando nenhuma chave aguenta, avisa uma vez e fecha")


def teste_voz_faz_ponte_e_executa_busca():
    cliente, _ = _novo_cliente()
    gid = _publicar(cliente, "a@exemplo.pt", "Ana")
    falsa = _SessaoLiveFalsa()
    original = AssistenteVoz._abrir_sessao_live
    AssistenteVoz._abrir_sessao_live = staticmethod(lambda chave: falsa)
    try:
        with cliente.websocket_connect("/maria-portuguesa/voz") as ws:
            assert ws.receive_json() == {"type": "ready"}
            ws.send_bytes(b"\x00\x00" * 160)
            assert ws.receive_json() == {"type": "searching", "consulta": "limpeza de casas"}
            resultados = ws.receive_json()
            assert resultados["type"] == "results" and resultados["resultados"][0]["gid"] == gid
            assert ws.receive_bytes() == b"\x01\x02"
            assert ws.receive_json() == {"type": "transcript", "role": "assistant", "text": "Encontrei uma pessoa."}
            assert ws.receive_json() == {"type": "turn_complete"}
            assert _esperar_fecho(ws) == []
    finally:
        AssistenteVoz._abrir_sessao_live = original

    resposta = falsa.respostas_ferramenta[0]
    assert resposta.id == "c1" and resposta.response["perfis"][0]["nome"] == "Ana"
    assert "telemovel" not in resposta.response["perfis"][0]
    audios = [e["audio"] for e in falsa.enviado if "audio" in e]
    assert audios and audios[0].mime_type == "audio/pcm;rate=16000"
    assert AssistenteVoz._sessoes_ativas == 0
    print("OK  - voz: áudio nos dois sentidos, busca executada e resultados enviados ao ecrã")


def teste_voz_sem_chave_avisa():
    from core.config import settings
    cliente, _ = _novo_cliente()
    chaves = settings.GEMINI_API_KEYS
    settings.GEMINI_API_KEYS = []
    try:
        with cliente.websocket_connect("/maria-portuguesa/voz") as ws:
            assert ws.receive_json()["type"] == "error"
            assert _esperar_fecho(ws) == []
    finally:
        settings.GEMINI_API_KEYS = chaves
    print("OK  - voz: sem GEMINI_API_KEY devolve erro legível em vez de falhar")


if __name__ == "__main__":
    testes = [f for nome, f in sorted(globals().items()) if nome.startswith("teste_") and callable(f)]
    for teste in testes:
        teste()
    print(f"\n✅ {len(testes)} testes do módulo Maria Portuguesa passaram")
