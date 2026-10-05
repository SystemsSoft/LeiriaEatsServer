"""
Teste do contrato de uma proposta aceita, com assinatura eletrônica das duas partes
(POST /contracts, GET /contracts, POST /contracts/{id}/sign).

Confere: só empresa dona gera, e só de proposta aceita; os termos acordados (valor, entregas, prazo — da proposta
ou do acordo de negociação) entram no texto por regra, nunca pela IA; a IA só escreve cláusulas gerais e qualquer
dígito nelas faz o contrato cair no texto-padrão; assinatura das duas partes com hash, trava de versão alterada,
duplicidade e integridade; notificações.

Roda contra SQLite em memória (FK ativa, como o InnoDB), sem rede — o Gemini é substituído por um fake.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_contracts.py
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
from conectaai.core.config import settings  # noqa: E402
from conectaai.core.security import create_access_token  # noqa: E402
from conectaai.models import sql_models as m  # noqa: E402
from conectaai.schemas.ai_structured import ContractClause, ContractClauses  # noqa: E402
from conectaai.services import contract_service  # noqa: E402
from conectaai.services.ai import gemini_client  # noqa: E402

dbmod.Base.metadata.create_all(_engine)
_client = TestClient(main.app, raise_server_exceptions=False)
_n = 0


def _cenario(status="accepted", with_agreement=False):
    """Empresa, creator e uma proposta — devolve (headers_empresa, headers_creator, proposal_id, ids)."""
    global _n
    _n += 1
    db = dbmod.SessionLocal()
    empresa_user = m.UserDB(email=f"empresa{_n}@teste.com", password_hash="x", role="company", name="Ana Diretora")
    creator_user = m.UserDB(email=f"creator{_n}@teste.com", password_hash="x", role="creator", name="Beto Creator")
    db.add_all([empresa_user, creator_user])
    db.flush()
    empresa = m.CompanyDB(user_id=empresa_user.id, name="Bela Cosméticos", segment="Beleza", city="São Paulo")
    creator = m.CreatorDB(user_id=creator_user.id, name="Beto Creator", username="@beto", city="Santos")
    db.add_all([empresa, creator])
    db.flush()
    campanha = m.CampaignDB(company_id=empresa.id, name="Verão 2026", status="active")
    db.add(campanha)
    db.flush()
    proposta = m.ProposalDB(
        company_id=empresa.id, creator_id=creator.id, campaign_id=campanha.id, campaign_name="Verão 2026",
        content_type="Reels + Stories", quantity=3, deadline=datetime(2026, 12, 15), description="Mostrar o protetor solar na praia",
        budget=1500, status=status, message="oi",
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
            terms={"price": 2400, "deliverables": [{"content_type": "Reel", "quantity": 2}, {"content_type": "Story", "quantity": 4}],
                   "deadline_days": 10, "exclusivity": True},
            total_value=2400, proposal_id=proposta.id,
        ))
    db.commit()
    h = lambda user, role: {"Authorization": f"Bearer {create_access_token(user.id, role)}", "X-Forwarded-For": "203.0.113.7, 10.0.0.1"}  # noqa: E731
    ids = {"company": empresa.id, "creator": creator.id, "company_user": empresa_user.id, "creator_user": creator_user.id}
    return h(empresa_user, "company"), h(creator_user, "creator"), proposta.id, ids


def _sem_ia():
    settings.GEMINI_API_KEYS = []


def _fake_gemini(clauses):
    chamadas = []

    def fake(**kwargs):
        chamadas.append(kwargs)
        parsed = ContractClauses(clauses=[ContractClause(heading=h, text=t) for h, t in clauses]) if clauses is not None else None
        return gemini_client.GeminiCallResult(parsed, "{}", 0, 1, "ok" if parsed else "invalid_json", 10)

    gemini_client.generate_json = fake
    settings.GEMINI_API_KEYS = ["fake"]
    return chamadas


_CLAUSULAS_OK = [
    ("DAS OBRIGAÇÕES DO CONTRATADO", "O CONTRATADO(A) produzirá os Reels e Stories da campanha com cuidado."),
    ("DAS OBRIGAÇÕES DO CONTRATANTE", "O CONTRATANTE fornecerá os produtos e o briefing."),
    ("DA APROVAÇÃO E DOS AJUSTES DO CONTEÚDO", "O conteúdo será aprovado antes de ir ao ar."),
    ("DOS DIREITOS DE IMAGEM E DA PROPRIEDADE INTELECTUAL", "O uso do conteúdo segue o acordado."),
    ("DA IDENTIFICAÇÃO PUBLICITÁRIA", "O conteúdo deve indicar que é publicidade."),
    ("DA RESCISÃO", "Qualquer parte pode rescindir em caso de descumprimento."),
]


def _gerar(headers, proposal_id):
    return _client.post("/contracts", json={"proposal_id": proposal_id}, headers=headers)


def teste_so_a_empresa_dona_gera_e_so_de_proposta_aceita():
    _sem_ia()
    empresa, creator, proposta, _ = _cenario()
    assert _gerar(creator, proposta).status_code == 403, "creator não gera"
    assert _gerar(empresa, "nao-existe").status_code == 404

    outra_empresa, _, _, _ = _cenario()
    assert _gerar(outra_empresa, proposta).status_code == 404, "empresa que não é dona não gera"

    empresa2, _, pendente, _ = _cenario(status="pending")
    r = _gerar(empresa2, pendente)
    assert r.status_code == 400 and "aceita" in r.json()["detail"], r.text
    print("OK  - só a empresa dona gera, e só depois de a proposta ser aceita")


def teste_gera_com_os_termos_da_proposta_e_notifica_o_creator():
    _sem_ia()
    empresa, creator, proposta, ids = _cenario()
    r = _gerar(empresa, proposta)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "awaiting_signatures" and c["source"] == "template" and c["integrity_ok"] is True
    texto = "\n".join(s["heading"] + "\n" + s["text"] for s in c["content"]["sections"])
    assert "R$ 1.500,00" in texto, "valor formatado em reais"
    assert "Verão 2026" in texto and "Bela Cosméticos" in texto and "@beto" in texto
    assert "3 conteúdo(s) no(s) formato(s): Reels + Stories" in texto
    assert "até 15/12/2026" in texto
    assert "Mostrar o protetor solar na praia" in texto
    assert [s["heading"].split(" – ")[0] for s in c["content"]["sections"]][:3] == ["CLÁUSULA 1ª", "CLÁUSULA 2ª", "CLÁUSULA 3ª"]
    assert "DA ASSINATURA ELETRÔNICA" in c["content"]["sections"][-1]["heading"]
    assert len(c["content_hash"]) == 64

    db = dbmod.SessionLocal()
    notas = db.query(m.NotificationDB).filter_by(user_id=ids["creator_user"]).all()
    assert len(notas) == 1 and "Contrato disponível" in notas[0].title

    # Gerar de novo devolve o mesmo contrato, sem duplicar nem notificar outra vez.
    r2 = _gerar(empresa, proposta)
    assert r2.json()["id"] == c["id"] and db.query(m.ContractDB).filter_by(proposal_id=proposta).count() == 1
    assert db.query(m.NotificationDB).filter_by(user_id=ids["creator_user"]).count() == 1
    print("OK  - termos da proposta entram no texto; creator é notificado; gerar de novo não duplica")


def teste_proposta_vinda_de_negociacao_usa_os_termos_do_acordo():
    _sem_ia()
    empresa, _, proposta, _ = _cenario(with_agreement=True)
    c = _gerar(empresa, proposta).json()
    texto = "\n".join(s["text"] for s in c["content"]["sections"])
    assert "R$ 2.400,00" in texto and "R$ 1.500,00" not in texto
    assert "2x Reel, 4x Story" in texto
    assert "em até 10 dias corridos, contados da assinatura" in texto
    assert "Exclusividade: acordada" in texto
    assert c["agreement_id"]
    print("OK  - proposta nascida de negociação usa preço, entregas, prazo e exclusividade do acordo")


def teste_ia_escreve_so_as_clausulas_gerais():
    empresa, _, proposta, _ = _cenario()
    chamadas = _fake_gemini(_CLAUSULAS_OK)
    c = _gerar(empresa, proposta).json()
    assert c["source"] == "gemini"
    textos = [s["text"] for s in c["content"]["sections"]]
    assert any("produzirá os Reels e Stories" in t for t in textos)
    assert any("R$ 1.500,00" in t for t in textos), "o valor continua vindo do servidor"
    assert "R$" not in chamadas[0]["system_instruction"].split("Regras invioláveis")[0]
    print("OK  - IA escreve as cláusulas gerais; valor, partes e prazo continuam vindo do servidor")


def teste_numero_nas_clausulas_da_ia_derruba_para_o_texto_padrao():
    for clausulas in [
        [(h, t) for h, t in _CLAUSULAS_OK[:-1]] + [("DA RESCISÃO", "Quem rescindir paga multa de 50% do valor.")],
        [("DAS OBRIGAÇÕES DO CONTRATADO", "Entregar em 5 dias.")] + _CLAUSULAS_OK[1:],
        [(h, t) for h, t in _CLAUSULAS_OK[:3]],  # poucas cláusulas
        [("", "texto sem título")] + _CLAUSULAS_OK[1:],
        None,  # JSON inválido
    ]:
        empresa, _, proposta, _ = _cenario()
        _fake_gemini(clausulas)
        c = _gerar(empresa, proposta).json()
        assert c["source"] == "template", clausulas
        texto = "\n".join(s["text"] for s in c["content"]["sections"])
        assert "multa de 50%" not in texto and "Entregar em 5 dias" not in texto
    print("OK  - dígito/multa inventada, poucas cláusulas, título vazio ou JSON inválido: cai no texto-padrão")


def teste_assinatura_das_duas_partes():
    _sem_ia()
    empresa, creator, proposta, ids = _cenario()
    c = _gerar(empresa, proposta).json()
    url = f"/contracts/{c['id']}/sign"

    r = _client.post(url, json={"content_hash": "0" * 64}, headers=empresa)
    assert r.status_code == 409 and "alterado" in r.json()["detail"], "hash de outra versão é recusado"

    r = _client.post(url, json={"content_hash": c["content_hash"]}, headers=empresa)
    assert r.status_code == 200, r.text
    parcial = r.json()
    assert parcial["status"] == "awaiting_signatures" and [s["role"] for s in parcial["signatures"]] == ["company"]
    sig = parcial["signatures"][0]
    assert sig["signer_name"] == "Ana Diretora" and sig["signer_email"].startswith("empresa")
    assert sig["ip_address"] == "203.0.113.7", "IP real vem do X-Forwarded-For"
    assert sig["content_hash"] == c["content_hash"] and len(sig["signature_code"]) == 32

    r = _client.post(url, json={"content_hash": c["content_hash"]}, headers=empresa)
    assert r.status_code == 400 and "já assinou" in r.json()["detail"]

    db = dbmod.SessionLocal()
    avisos = db.query(m.NotificationDB).filter_by(user_id=ids["creator_user"]).all()
    assert any("Falta a sua assinatura" in n.title for n in avisos)

    r = _client.post(url, json={"content_hash": c["content_hash"]}, headers=creator)
    assert r.status_code == 200, r.text
    final = r.json()
    assert final["status"] == "signed" and sorted(s["role"] for s in final["signatures"]) == ["company", "creator"]
    assert final["integrity_ok"] is True
    assert db.query(m.NotificationDB).filter_by(user_id=ids["company_user"], title="Contrato assinado!").count() == 1
    assert db.query(m.NotificationDB).filter_by(user_id=ids["creator_user"], title="Contrato assinado!").count() == 1

    r = _client.post(url, json={"content_hash": c["content_hash"]}, headers=creator)
    assert r.status_code == 400 and "já foi assinado" in r.json()["detail"]
    print("OK  - as duas partes assinam; hash errado, assinatura repetida e IP/e-mail/código são tratados; ambos notificados")


def teste_so_os_participantes_veem_e_assinam():
    _sem_ia()
    empresa, creator, proposta, _ = _cenario()
    c = _gerar(empresa, proposta).json()
    intruso_empresa, intruso_creator, _, _ = _cenario()
    for intruso in (intruso_empresa, intruso_creator):
        assert _client.get(f"/contracts/{c['id']}", headers=intruso).status_code == 403
        assert _client.post(f"/contracts/{c['id']}/sign", json={"content_hash": c["content_hash"]}, headers=intruso).status_code == 403
    assert _client.get(f"/contracts/{c['id']}", headers=creator).status_code == 200
    assert _client.get("/contracts/nao-existe", headers=empresa).status_code == 404
    assert [x["id"] for x in _client.get("/contracts", headers=creator).json()] == [c["id"]]
    assert _client.get("/contracts", headers=intruso_creator).json() == []
    assert _client.get("/contracts").status_code == 401
    print("OK  - só empresa e creator do contrato leem e assinam; listas são isoladas")


def teste_adulteracao_do_texto_e_detectada_e_bloqueia_a_assinatura():
    _sem_ia()
    empresa, creator, proposta, _ = _cenario()
    c = _gerar(empresa, proposta).json()
    _client.post(f"/contracts/{c['id']}/sign", json={"content_hash": c["content_hash"]}, headers=empresa)

    db = dbmod.SessionLocal()
    contrato = db.query(m.ContractDB).filter_by(id=c["id"]).first()
    adulterado = dict(contrato.content)
    adulterado["sections"] = [dict(s) for s in adulterado["sections"]]
    adulterado["sections"][2]["text"] = adulterado["sections"][2]["text"].replace("1.500,00", "150,00")
    contrato.content = adulterado
    db.commit()

    lido = _client.get(f"/contracts/{c['id']}", headers=creator).json()
    assert lido["integrity_ok"] is False
    r = _client.post(f"/contracts/{c['id']}/sign", json={"content_hash": c["content_hash"]}, headers=creator)
    assert r.status_code == 409 and "integridade" in r.json()["detail"]
    print("OK  - texto adulterado no banco: integrity_ok=false e a assinatura é recusada")


if __name__ == "__main__":
    teste_so_a_empresa_dona_gera_e_so_de_proposta_aceita()
    teste_gera_com_os_termos_da_proposta_e_notifica_o_creator()
    teste_proposta_vinda_de_negociacao_usa_os_termos_do_acordo()
    teste_ia_escreve_so_as_clausulas_gerais()
    teste_numero_nas_clausulas_da_ia_derruba_para_o_texto_padrao()
    teste_assinatura_das_duas_partes()
    teste_so_os_participantes_veem_e_assinam()
    teste_adulteracao_do_texto_e_detectada_e_bloqueia_a_assinatura()
