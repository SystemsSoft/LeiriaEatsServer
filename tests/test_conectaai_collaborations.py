"""
Teste de "Acordos": o acompanhamento de uma proposta aceita até o fim da campanha
(GET /collaborations, GET /collaborations/{proposal_id}, POST /deliverables/{id}/submit,
POST /deliverables/{id}/review).

Confere: só propostas aceitas viram acordo, e cada lado vê só os seus; a lista de conteúdos nasce das entregas
combinadas (da proposta ou do acordo de negociação) e não duplica; o ciclo enviar -> pedir ajustes -> reenviar ->
aprovar com a linha do tempo de cada passo; validação do link (só http/https); quem pode fazer o quê; notificações
para a outra parte; etapas da campanha (Produção -> Aprovação -> Publicação) que só avançam.

Roda contra SQLite em memória (FK ativa, como o InnoDB), sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_collaborations.py
"""
import os
import sys
import tempfile
from datetime import datetime

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

from fastapi.testclient import TestClient  # noqa: E402

import conectaai.main as main  # noqa: E402
from conectaai.core.security import create_access_token  # noqa: E402
from conectaai.models import sql_models as m  # noqa: E402

dbmod.Base.metadata.create_all(_engine)
_client = TestClient(main.app, raise_server_exceptions=False)
_n = 0


def _cenario(*, status="accepted", content_type="Reels", quantity=3, with_agreement=False):
    """Empresa, creator e uma proposta — devolve (headers_empresa, headers_creator, proposal_id, ids)."""
    global _n
    _n += 1
    db = dbmod.SessionLocal()
    empresa_user = m.UserDB(email=f"empresa{_n}@teste.com", password_hash="x", role="company", name="Ana Diretora")
    creator_user = m.UserDB(email=f"creator{_n}@teste.com", password_hash="x", role="creator", name="Beto Creator")
    db.add_all([empresa_user, creator_user])
    db.flush()
    empresa = m.CompanyDB(user_id=empresa_user.id, name="Bela Cosméticos")
    creator = m.CreatorDB(user_id=creator_user.id, name="Beto Creator", username="@beto")
    db.add_all([empresa, creator])
    db.flush()
    campanha = m.CampaignDB(company_id=empresa.id, name="Verão 2026", status="active", current_stage="contract")
    db.add(campanha)
    db.flush()
    proposta = m.ProposalDB(
        company_id=empresa.id, creator_id=creator.id, campaign_id=campanha.id, campaign_name="Verão 2026",
        content_type=content_type, quantity=quantity, deadline=datetime(2026, 12, 15), description="", budget=1500, status=status, message="",
    )
    db.add(proposta)
    db.flush()
    if with_agreement:
        mandato = m.CommercialMandateDB(owner_type="company", owner_id=empresa.id, campaign_id=campanha.id)
        db.add(mandato)
        db.flush()
        negociacao = m.NegotiationDB(company_id=empresa.id, creator_id=creator.id, campaign_id=campanha.id, company_mandate_id=mandato.id)
        db.add(negociacao)
        db.flush()
        db.add(m.AgreementDB(
            negotiation_id=negociacao.id, company_id=empresa.id, creator_id=creator.id, campaign_id=campanha.id, status="approved",
            terms={"price": 2400, "deliverables": [{"content_type": "Reel", "quantity": 2}, {"content_type": "Story", "quantity": 1}]},
            total_value=2400, proposal_id=proposta.id,
        ))
    db.commit()
    h = lambda user, role: {"Authorization": f"Bearer {create_access_token(user.id, role)}"}  # noqa: E731
    ids = {"company_user": empresa_user.id, "creator_user": creator_user.id, "campaign": campanha.id}
    return h(empresa_user, "company"), h(creator_user, "creator"), proposta.id, ids


def _detalhe(headers, proposal_id):
    return _client.get(f"/collaborations/{proposal_id}", headers=headers)


def _enviar(headers, deliverable_id, url="https://drive.google.com/file/d/abc", note=""):
    return _client.post(f"/deliverables/{deliverable_id}/submit", json={"url": url, "note": note}, headers=headers)


def _revisar(headers, deliverable_id, decision, feedback=""):
    return _client.post(f"/deliverables/{deliverable_id}/review", json={"decision": decision, "feedback": feedback}, headers=headers)


def teste_so_proposta_aceita_vira_acordo_e_cada_lado_ve_so_os_seus():
    empresa, creator, proposta, _ = _cenario()
    _, _, pendente, _ = _cenario(status="pending")
    outra_empresa, outro_creator, _, _ = _cenario()

    for h in (empresa, creator):
        lista = _client.get("/collaborations", headers=h).json()
        assert [c["proposal_id"] for c in lista] == [proposta], lista
        assert lista[0]["campaign_name"] == "Verão 2026" and lista[0]["company_name"] == "Bela Cosméticos" and lista[0]["creator_name"] == "Beto Creator"
        assert lista[0]["counts"]["total"] == 3 and lista[0]["counts"]["pending"] == 3 and lista[0]["status"] == "in_progress"

    for intruso in (outra_empresa, outro_creator):
        assert _detalhe(intruso, proposta).status_code == 404
    assert _detalhe(empresa, pendente).status_code == 404, "proposta pendente não é acordo"
    assert _client.get("/collaborations").status_code == 401
    print("OK  - só proposta aceita vira acordo; empresa e creator veem só os seus")


def teste_conteudos_nascem_das_entregas_combinadas_e_nao_duplicam():
    empresa, creator, proposta, _ = _cenario(quantity=3)
    d = _detalhe(empresa, proposta).json()
    assert [x["label"] for x in d["deliverables"]] == ["Reels 1", "Reels 2", "Reels 3"]
    assert all(x["status"] == "pending" for x in d["deliverables"])
    again = _detalhe(creator, proposta).json()
    assert [x["id"] for x in again["deliverables"]] == [x["id"] for x in d["deliverables"]], "não duplica"
    assert len(_client.get("/collaborations", headers=empresa).json()) == 1

    h, _, um_id, _ = _cenario(content_type="Reels", quantity=1)
    assert [x["label"] for x in _detalhe(h, um_id).json()["deliverables"]] == ["Reels"]

    h2, _, multi, _ = _cenario(content_type="Reels + Stories", quantity=2)
    items = _detalhe(h2, multi).json()["deliverables"]
    assert [x["label"] for x in items] == ["Conteúdo 1", "Conteúdo 2"] and items[0]["content_type"] == "Reels + Stories"

    h3, _, ac, _ = _cenario(with_agreement=True)
    items = _detalhe(h3, ac).json()["deliverables"]
    assert [x["label"] for x in items] == ["Reel 1", "Reel 2", "Story"], "proposta de negociação usa as entregas por formato do acordo"

    h4, _, grande, _ = _cenario(quantity=500)
    assert len(_detalhe(h4, grande).json()["deliverables"]) == 50, "limite de conteúdos por acordo"
    print("OK  - conteúdos criados das entregas (proposta ou acordo), sem duplicar, com limite")


def teste_ciclo_enviar_pedir_ajustes_reenviar_aprovar_com_linha_do_tempo():
    empresa, creator, proposta, ids = _cenario(quantity=2)
    a, b = _detalhe(empresa, proposta).json()["deliverables"]

    # empresa não envia; creator não revisa; revisar o que não foi enviado é recusado
    assert _enviar(empresa, a["id"]).status_code == 403
    assert _revisar(creator, a["id"], "approve").status_code == 403
    r = _revisar(empresa, a["id"], "approve")
    assert r.status_code == 400 and "ainda não foi enviado" in r.json()["detail"]

    r = _enviar(creator, a["id"], url="drive.google.com/file/d/v1", note="Primeira versão")
    assert r.status_code == 200, r.text
    enviado = r.json()
    assert enviado["status"] == "submitted" and enviado["url"] == "https://drive.google.com/file/d/v1" and enviado["note"] == "Primeira versão"
    assert [e["kind"] for e in enviado["events"]] == ["submitted"]

    r = _enviar(creator, a["id"])
    assert r.status_code == 400 and "em análise" in r.json()["detail"], "não reenvia enquanto a empresa analisa"

    r = _revisar(empresa, a["id"], "request_changes", "")
    assert r.status_code == 400 and "o que precisa ser ajustado" in r.json()["detail"], "pedir ajustes exige explicar"
    r = _revisar(empresa, a["id"], "request_changes", "Troque a música e corte o final")
    assert r.status_code == 200 and r.json()["status"] == "changes_requested" and r.json()["feedback"] == "Troque a música e corte o final"

    r = _enviar(creator, a["id"], url="https://drive.google.com/file/d/v2", note="Ajustado")
    assert r.json()["status"] == "submitted" and r.json()["feedback"] == "", "o reenvio limpa o pedido anterior"

    r = _revisar(empresa, a["id"], "approve", "Ficou ótimo")
    aprovado = r.json()
    assert aprovado["status"] == "approved" and aprovado["reviewed_at"]
    assert [(e["actor_role"], e["kind"]) for e in aprovado["events"]] == [
        ("creator", "submitted"), ("company", "changes_requested"), ("creator", "submitted"), ("company", "approved"),
    ]
    assert aprovado["events"][0]["url"].endswith("/v1") and aprovado["events"][2]["url"].endswith("/v2"), "cada versão guarda o seu link"

    r = _enviar(creator, a["id"])
    assert r.status_code == 400 and "já foi aprovado" in r.json()["detail"]
    assert _revisar(empresa, a["id"], "approve").status_code == 400

    d = _detalhe(creator, proposta).json()
    assert d["counts"] == {"total": 2, "pending": 1, "submitted": 0, "changes_requested": 0, "approved": 1} and d["status"] == "in_progress"

    _enviar(creator, b["id"], url="https://www.instagram.com/reel/Cxyz/")
    _revisar(empresa, b["id"], "approve")
    final = _detalhe(empresa, proposta).json()
    assert final["status"] == "completed" and final["counts"]["approved"] == 2
    print("OK  - ciclo completo com linha do tempo; acordo conclui quando todos os conteúdos são aprovados")


def teste_so_o_creator_dono_envia_e_so_a_empresa_dona_revisa():
    empresa, creator, proposta, _ = _cenario(quantity=1)
    outra_empresa, outro_creator, _, _ = _cenario()
    item = _detalhe(empresa, proposta).json()["deliverables"][0]

    assert _enviar(outro_creator, item["id"]).status_code == 403, "outro creator não envia"
    assert _enviar(creator, item["id"]).status_code == 200
    assert _revisar(outra_empresa, item["id"], "approve").status_code == 403, "outra empresa não revisa"
    assert _enviar(creator, "nao-existe").status_code == 404
    assert _revisar(empresa, item["id"], "talvez").status_code == 400
    assert _revisar(empresa, item["id"], "approve").status_code == 200
    print("OK  - só o creator dono envia e só a empresa dona revisa")


def teste_link_so_http_https():
    empresa, creator, proposta, _ = _cenario(quantity=1)
    item = _detalhe(empresa, proposta).json()["deliverables"][0]
    for ruim in ["javascript:alert(1)", "data:text/html,oi", "ftp://x.com/a", "localhost", "meu video.com/a", "   "]:
        r = _enviar(creator, item["id"], url=ruim)
        assert r.status_code in (400, 422), (ruim, r.status_code, r.text)
    assert _detalhe(creator, proposta).json()["deliverables"][0]["status"] == "pending", "link inválido não muda nada"
    r = _enviar(creator, item["id"], url="  www.tiktok.com/@beto/video/123  ")
    assert r.status_code == 200 and r.json()["url"] == "https://www.tiktok.com/@beto/video/123"
    print("OK  - só links http/https (sem esquema vira https); javascript:, data:, ftp:, espaços e vazio são recusados")


def teste_notificacoes_para_a_outra_parte():
    empresa, creator, proposta, ids = _cenario(quantity=1)
    item = _detalhe(empresa, proposta).json()["deliverables"][0]
    db = dbmod.SessionLocal()

    def titulos(user_id):
        return [n.title for n in db.query(m.NotificationDB).filter_by(user_id=user_id).order_by(m.NotificationDB.timestamp).all()]

    _enviar(creator, item["id"])
    assert titulos(ids["company_user"]) == ["Conteúdo enviado para aprovação"] and titulos(ids["creator_user"]) == []
    _revisar(empresa, item["id"], "request_changes", "Refazer a abertura")
    assert titulos(ids["creator_user"]) == ["Ajustes solicitados"]
    nota = db.query(m.NotificationDB).filter_by(user_id=ids["creator_user"]).first()
    assert "Refazer a abertura" in nota.message and nota.type == "campaignUpdate"
    _enviar(creator, item["id"])
    _revisar(empresa, item["id"], "approve")
    assert titulos(ids["creator_user"]) == ["Ajustes solicitados", "Conteúdo aprovado!", "Todos os conteúdos aprovados"]
    assert titulos(ids["company_user"])[-1] == "Todos os conteúdos aprovados"
    print("OK  - cada ação notifica a outra parte (tipo campaignUpdate); a conclusão notifica as duas")


def teste_etapas_da_campanha_so_avancam():
    empresa, creator, proposta, ids = _cenario(quantity=2)
    db = dbmod.SessionLocal()

    def etapa():
        db.expire_all()
        return db.query(m.CampaignDB).filter_by(id=ids["campaign"]).first().current_stage

    assert etapa() == "contract"
    a, b = _detalhe(empresa, proposta).json()["deliverables"]
    assert etapa() == "production", "abrir o acordo põe a campanha em Produção"
    _enviar(creator, a["id"])
    assert etapa() == "approval"
    _revisar(empresa, a["id"], "approve")
    assert etapa() == "approval", "ainda falta um conteúdo"
    _enviar(creator, b["id"])
    _revisar(empresa, b["id"], "approve")
    assert etapa() == "publication"
    _detalhe(empresa, proposta)  # reabrir não faz a etapa voltar
    assert etapa() == "publication"
    print("OK  - campanha: Produção ao abrir, Aprovação no 1º envio, Publicação quando tudo é aprovado — sem regredir")


if __name__ == "__main__":
    teste_so_proposta_aceita_vira_acordo_e_cada_lado_ve_so_os_seus()
    teste_conteudos_nascem_das_entregas_combinadas_e_nao_duplicam()
    teste_ciclo_enviar_pedir_ajustes_reenviar_aprovar_com_linha_do_tempo()
    teste_so_o_creator_dono_envia_e_so_a_empresa_dona_revisa()
    teste_link_so_http_https()
    teste_notificacoes_para_a_outra_parte()
    teste_etapas_da_campanha_so_avancam()
