"""
Teste do "Conectar Instagram" do creator (POST e DELETE /creators/me/instagram).

Confere: sem a chave secreta do app o app é avisado (503); o código devolvido pelo login do Instagram vira @,
seguidores e link confirmados no perfil; o link do Instagram digitado à mão é trocado pelo confirmado (sem mexer
nas outras redes); uma conta do Instagram confirma um perfil só (409), mas o mesmo creator pode reconectar; recusa
do Instagram vira 502 com mensagem para o usuário; só creator logado conecta; desconectar apaga do perfil o que
veio do Instagram e libera a conta; e o serviço fala com o Instagram do jeito certo (troca do código com a chave
secreta, leitura do perfil com o token, nos dois formatos de resposta).

Roda contra SQLite em memória, sem rede — o Instagram é substituído por um falso.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_instagram.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")
os.environ["CONECTAAI_UPLOAD_DIR"] = tempfile.mkdtemp()

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@event.listens_for(_engine, "connect")
def _foreign_keys_on(dbapi, _):
    dbapi.execute("PRAGMA foreign_keys=ON")


dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import conectaai.main as main  # noqa: E402
from conectaai.core.config import settings  # noqa: E402
from conectaai.core.security import create_access_token  # noqa: E402
from conectaai.models import sql_models as m  # noqa: E402
from conectaai.services import instagram_service  # noqa: E402

dbmod.Base.metadata.create_all(_engine)
_client = TestClient(main.app, raise_server_exceptions=False)
_n = 0
_REDIRECT = "https://app.conectaai.test/instagram-callback.html"
_real_fetch_profile = instagram_service.fetch_profile


class FakeInstagram:
    """Instagram falso: cada código vale um perfil; código desconhecido é recusado."""

    def __init__(self, profiles):
        self.profiles = profiles
        self.calls = []

    def fetch_profile(self, code, redirect_uri):
        self.calls.append({"code": code, "redirect_uri": redirect_uri})
        if code not in self.profiles:
            raise instagram_service.InstagramError("O Instagram não aceitou a autorização. Tente conectar de novo.")
        return self.profiles[code]


def _instalar_instagram(profiles):
    fake = FakeInstagram(profiles)
    instagram_service.fetch_profile = fake.fetch_profile
    return fake


def _usuario(role="creator", platforms=None):
    global _n
    _n += 1
    db = dbmod.SessionLocal()
    user = m.UserDB(email=f"ig{role}{_n}@teste.com", password_hash="x", role=role, name="Ana")
    db.add(user)
    db.flush()
    db.add(m.CompanyDB(user_id=user.id, name="Bela Cosméticos") if role == "company"
           else m.CreatorDB(user_id=user.id, name="Beto", platforms=platforms or []))
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(user.id, role)}"}


def _conectar(headers, code):
    return _client.post("/creators/me/instagram", json={"code": code, "redirect_uri": _REDIRECT}, headers=headers)


def teste_sem_a_chave_secreta_o_app_e_avisado():
    instagram_service.fetch_profile = _real_fetch_profile
    settings.INSTAGRAM_APP_SECRET = ""
    r = _conectar(_usuario(), "qualquer")
    assert r.status_code == 503 and "ainda não está disponível" in r.json()["detail"], r.text
    print("OK  - sem a chave secreta do app do Instagram: 503")


def teste_codigo_do_login_confirma_o_instagram_no_perfil():
    fake = _instalar_instagram({"cod1": instagram_service.InstagramProfile("1789", "beto.oficial", 12500)})
    creator = _usuario(platforms=[
        {"platform": "TikTok", "url": "https://www.tiktok.com/@beto"},
        {"platform": "Instagram", "url": "https://www.instagram.com/digitado_a_mao"},
    ])
    antes = _client.get("/creators/me", headers=creator).json()
    assert antes["instagram_username"] == "" and antes["instagram_followers"] == 0

    r = _conectar(creator, "cod1")
    assert r.status_code == 200, r.text
    assert fake.calls == [{"code": "cod1", "redirect_uri": _REDIRECT}]
    body = r.json()
    assert body["instagram_username"] == "beto.oficial" and body["instagram_followers"] == 12500
    # o link digitado à mão dá lugar ao confirmado, na mesma posição; as outras redes ficam como estavam
    assert body["platforms"] == [
        {"platform": "TikTok", "url": "https://www.tiktok.com/@beto"},
        {"platform": "Instagram", "url": "https://www.instagram.com/beto.oficial"},
    ]
    # os seguidores declarados pelo creator não são sobrescritos
    assert body["followers"] == antes["followers"]
    assert _client.get("/creators/me", headers=creator).json()["instagram_username"] == "beto.oficial"
    print("OK  - código do login vira @, seguidores e link confirmados no perfil")


def teste_creator_sem_instagram_na_lista_ganha_o_link():
    _instalar_instagram({"cod2": instagram_service.InstagramProfile("2001", "nova.conta", 300)})
    creator = _usuario(platforms=["TikTok"])  # formato antigo: lista de nomes
    body = _conectar(creator, "cod2").json()
    assert body["platforms"] == [
        {"platform": "TikTok", "url": ""},
        {"platform": "Instagram", "url": "https://www.instagram.com/nova.conta"},
    ], body
    print("OK  - creator sem Instagram na lista: o link confirmado é acrescentado")


def teste_uma_conta_do_instagram_confirma_um_perfil_so():
    _instalar_instagram({
        "a": instagram_service.InstagramProfile("3001", "disputada", 10),
        "b": instagram_service.InstagramProfile("3001", "disputada", 11),
        "c": instagram_service.InstagramProfile("3002", "outra", 5),
    })
    primeiro, segundo = _usuario(), _usuario()
    assert _conectar(primeiro, "a").status_code == 200

    r = _conectar(segundo, "b")
    assert r.status_code == 409 and "já está conectado" in r.json()["detail"], r.text
    assert _client.get("/creators/me", headers=segundo).json()["instagram_username"] == ""

    # o dono reconecta a mesma conta (atualiza os seguidores) e pode trocar por outra
    assert _conectar(primeiro, "b").json()["instagram_followers"] == 11
    assert _conectar(primeiro, "c").json()["instagram_username"] == "outra"
    # a conta antiga ficou livre para o outro creator
    assert _conectar(segundo, "a").status_code == 200
    print("OK  - uma conta do Instagram confirma um perfil só; o dono reconecta e troca")


def teste_recusa_do_instagram_e_quem_pode_conectar():
    _instalar_instagram({})
    creator = _usuario()
    r = _conectar(creator, "vencido")
    assert r.status_code == 502 and "não aceitou a autorização" in r.json()["detail"], r.text
    assert _client.get("/creators/me", headers=creator).json()["instagram_username"] == ""

    assert _client.post("/creators/me/instagram", json={"code": "x", "redirect_uri": _REDIRECT}).status_code == 401
    assert _conectar(_usuario("company"), "x").status_code == 403
    assert _client.post("/creators/me/instagram", json={"code": "x"}, headers=creator).status_code == 422
    print("OK  - recusa do Instagram vira 502; sem login 401, empresa 403, sem endereço de retorno 422")


def teste_desconectar_apaga_o_que_veio_do_instagram_e_libera_a_conta():
    _instalar_instagram({"d": instagram_service.InstagramProfile("4001", "vai.sair", 900)})
    creator = _usuario(platforms=[{"platform": "TikTok", "url": "https://www.tiktok.com/@beto"}, "Instagram"])
    assert _conectar(creator, "d").json()["instagram_username"] == "vai.sair"

    r = _client.delete("/creators/me/instagram", headers=creator)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["instagram_username"] == "" and body["instagram_followers"] == 0
    # o Instagram continua na lista de redes, mas sem o link; as outras redes ficam como estavam
    assert body["platforms"] == [
        {"platform": "TikTok", "url": "https://www.tiktok.com/@beto"},
        {"platform": "Instagram", "url": ""},
    ]
    db = dbmod.SessionLocal()
    assert db.query(m.CreatorDB).filter(m.CreatorDB.instagram_user_id == "4001").count() == 0
    db.close()

    # a conta ficou livre para outro perfil; desconectar sem nada conectado não quebra nem cria o Instagram na lista
    outro = _usuario(platforms=["TikTok"])
    assert _client.delete("/creators/me/instagram", headers=outro).json()["platforms"] == [{"platform": "TikTok", "url": ""}]
    assert _conectar(outro, "d").status_code == 200

    assert _client.delete("/creators/me/instagram").status_code == 401
    assert _client.delete("/creators/me/instagram", headers=_usuario("company")).status_code == 403
    print("OK  - desconectar apaga id, @, seguidores e link do Instagram e libera a conta; só o próprio creator")


class _Resposta:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("sem json")
        return self._body


def teste_o_servico_fala_com_o_instagram_do_jeito_certo():
    instagram_service.fetch_profile = _real_fetch_profile
    settings.INSTAGRAM_APP_ID, settings.INSTAGRAM_APP_SECRET = "111", "segredo"
    original_post, original_get = requests.post, requests.get
    pedidos = []
    respostas = {}

    def post(url, data=None, timeout=None):
        pedidos.append(("post", url, data))
        return respostas["token"]

    def get(url, params=None, timeout=None):
        pedidos.append(("get", url, params))
        return respostas["perfil"]

    requests.post, requests.get = post, get
    try:
        # resposta direta + "#_" que o Instagram cola no fim do endereço de retorno
        respostas["token"] = _Resposta(200, {"access_token": "tok1", "user_id": 1})
        respostas["perfil"] = _Resposta(200, {"user_id": "1789", "username": "beto", "followers_count": 42, "id": "9"})
        perfil = instagram_service.fetch_profile("abc#_", _REDIRECT)
        assert (perfil.user_id, perfil.username, perfil.followers) == ("1789", "beto", 42)
        assert pedidos[0] == ("post", "https://api.instagram.com/oauth/access_token", {
            "client_id": "111", "client_secret": "segredo", "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT, "code": "abc",
        })
        assert pedidos[1] == ("get", "https://graph.instagram.com/me",
                              {"fields": "user_id,username,followers_count", "access_token": "tok1"})

        # resposta dentro de {"data": [...]}; sem seguidores na resposta = 0
        respostas["token"] = _Resposta(200, {"data": [{"access_token": "tok2", "user_id": "1"}]})
        respostas["perfil"] = _Resposta(200, {"id": "55", "username": "ana"})
        perfil = instagram_service.fetch_profile("abc", _REDIRECT)
        assert (perfil.user_id, perfil.username, perfil.followers) == ("55", "ana", 0) and pedidos[-1][2]["access_token"] == "tok2"

        # código vencido/já usado, resposta que não é JSON, perfil recusado e rede fora do ar: erro para o usuário
        for token, perfil_resp in (
            (_Resposta(400, {"error_type": "OAuthException", "code": 400, "error_message": "code expirado"}), None),
            (_Resposta(500, None), None),
            (_Resposta(200, {"access_token": "tok3"}), _Resposta(400, {"error": {"message": "token inválido"}})),
        ):
            respostas["token"], respostas["perfil"] = token, perfil_resp
            try:
                instagram_service.fetch_profile("abc", _REDIRECT)
                raise AssertionError("deveria recusar")
            except instagram_service.InstagramError as exc:
                assert "não aceitou a autorização" in str(exc)

        def post_fora_do_ar(url, data=None, timeout=None):
            raise requests.ConnectionError("sem rede")

        requests.post = post_fora_do_ar
        try:
            instagram_service.fetch_profile("abc", _REDIRECT)
            raise AssertionError("deveria recusar")
        except instagram_service.InstagramError as exc:
            assert "Não consegui falar com o Instagram" in str(exc)
    finally:
        requests.post, requests.get = original_post, original_get
    print("OK  - serviço: troca o código com a chave secreta, lê o perfil com o token, trata recusas e rede")


if __name__ == "__main__":
    teste_sem_a_chave_secreta_o_app_e_avisado()
    teste_codigo_do_login_confirma_o_instagram_no_perfil()
    teste_creator_sem_instagram_na_lista_ganha_o_link()
    teste_uma_conta_do_instagram_confirma_um_perfil_so()
    teste_recusa_do_instagram_e_quem_pode_conectar()
    teste_desconectar_apaga_o_que_veio_do_instagram_e_libera_a_conta()
    teste_o_servico_fala_com_o_instagram_do_jeito_certo()
