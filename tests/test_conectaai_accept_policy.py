"""
Teste da validação do `accept` na negociação do ConectaAI (conectaai/services/negotiation).

Problema que motivou: policy.apply só valida o que o agente PROPÕE, e um `accept` propõe quase nada.
Um agente podia então aceitar entregáveis fora do próprio catálogo, quantidade/exclusividade/prazo fora
do mandato, e o preço era reescrito em silêncio para o limite do mandato — fechando um acordo que a
contraparte nunca ofereceu (ex.: catálogos disjuntos fechavam "1x Story" com um creator que só faz Reel).

Cobre (1) as funções puras de policy.py e (2) engine.run_turn contra um SQLite em memória, com o
Gemini substituído por turnos roteirizados — sem rede e sem gastar cota.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_accept_policy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# O banco é trocado ANTES de importar o resto do módulo — nenhuma conexão com o MySQL real.
import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

from conectaai.core.config import settings  # noqa: E402
from conectaai.models import sql_models  # noqa: E402,F401
from conectaai.repositories.mandate_repo import MandateRepository  # noqa: E402
from conectaai.repositories.negotiation_repo import NegotiationRepository  # noqa: E402
from conectaai.repositories.negotiation_turn_repo import NegotiationTurnRepository  # noqa: E402
from conectaai.services.negotiation import engine, policy  # noqa: E402
from conectaai.services.negotiation.state_machine import is_terminal  # noqa: E402

settings.GEMINI_API_KEYS = []  # sem chave: o agente determinístico é o padrão; os testes de LLM roteirizam _generate_turn
dbmod.Base.metadata.create_all(_engine)

REEL = {"content_type": "Reel", "min_qty": 1, "max_qty": 3}
STORY = {"content_type": "Story", "min_qty": 1, "max_qty": 5}
POST = {"content_type": "Post", "min_qty": 1, "max_qty": 2}


def _mandato_dict(owner_type, **kw):
    base = {"owner_type": owner_type, "price_ceiling": 0, "price_floor": 0, "deliverables": [REEL], "exclusivity_allowed": False}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- policy (funções puras)


def teste_accept_com_oferta_dentro_do_mandato_e_valido():
    creator = _mandato_dict("creator", price_floor=1800, deliverables=[REEL, POST])
    oferta = {"price": 2000, "deliverables": [{"content_type": "Reel", "quantity": 2}], "exclusivity": False}
    assert policy.acceptance_violations(creator, oferta) == []
    print("OK  - oferta dentro do mandato pode ser aceita como está")


def teste_accept_de_preco_abaixo_do_piso_do_creator_e_invalido():
    creator = _mandato_dict("creator", price_floor=1800)
    assert policy.acceptance_violations(creator, {"price": 1500}) == ["price_out_of_mandate"]
    company = _mandato_dict("company", price_ceiling=3000)
    assert policy.acceptance_violations(company, {"price": 3500}) == ["price_out_of_mandate"]
    print("OK  - preço abaixo do piso (creator) / acima do teto (empresa) impede o accept")


def teste_accept_de_entregavel_fora_do_catalogo_e_invalido():
    creator = _mandato_dict("creator", price_floor=1000, deliverables=[REEL])
    oferta = {"price": 2000, "deliverables": [{"content_type": "Reel", "quantity": 1}, {"content_type": "Story", "quantity": 1}]}
    assert policy.acceptance_violations(creator, oferta) == ["deliverable_not_in_catalog:Story"]
    oferta = {"price": 2000, "deliverables": [{"content_type": "Reel", "quantity": 5}]}
    assert policy.acceptance_violations(creator, oferta) == ["deliverable_qty_out_of_mandate:Reel"]
    print("OK  - tipo fora do catálogo e quantidade fora da faixa impedem o accept")


def teste_accept_de_exclusividade_nao_permitida_e_invalido():
    creator = _mandato_dict("creator", price_floor=1000)
    assert policy.acceptance_violations(creator, {"price": 2000, "exclusivity": True}) == ["exclusivity_not_allowed"]
    creator["exclusivity_allowed"] = True
    assert policy.acceptance_violations(creator, {"price": 2000, "exclusivity": True}) == []
    print("OK  - exclusividade só é aceita se o mandato permitir")


def teste_sem_oferta_nao_ha_o_que_aceitar():
    assert policy.acceptance_violations(_mandato_dict("creator"), {}) == ["no_offer_to_accept"]
    print("OK  - accept sem oferta na mesa é inválido")


def teste_conform_offer_reduz_a_oferta_ao_mandato():
    creator = _mandato_dict("creator", price_floor=1800, deliverables=[REEL, POST])
    oferta = {
        "price": 1500,
        "deliverables": [{"content_type": "Reel", "quantity": 5}, {"content_type": "Story", "quantity": 1}],
        "exclusivity": True,
    }
    assert policy.conform_offer(creator, oferta) == {
        "price": 1800,
        "deliverables": [{"content_type": "Reel", "quantity": 3}],
        "exclusivity": False,
    }
    print("OK  - conform_offer: piso de preço, catálogo, faixa de quantidade e exclusividade")


def teste_catalogos_disjuntos():
    empresa = _mandato_dict("company", deliverables=[STORY])
    creator = _mandato_dict("creator", deliverables=[REEL])
    assert policy.deliverable_catalogs_are_disjoint(empresa, creator)
    assert not policy.deliverable_catalogs_are_disjoint(_mandato_dict("company", deliverables=[REEL, STORY]), _mandato_dict("creator", deliverables=[REEL, POST]))
    # mesmo tipo, mas faixas de quantidade que não se tocam (empresa quer 4-5, creator faz 1-3)
    empresa = _mandato_dict("company", deliverables=[{"content_type": "Reel", "min_qty": 4, "max_qty": 5}])
    assert policy.deliverable_catalogs_are_disjoint(empresa, _mandato_dict("creator", deliverables=[REEL]))
    # catálogo vazio de um lado = sem restrição, não é incompatibilidade
    assert not policy.deliverable_catalogs_are_disjoint(_mandato_dict("company", deliverables=[]), _mandato_dict("creator", deliverables=[REEL]))
    print("OK  - catálogos disjuntos: tipos sem interseção e faixas de quantidade que não se tocam")


def teste_apply_nao_publica_entregavel_herdado_fora_do_catalogo():
    creator = _mandato_dict("creator", price_floor=1000, deliverables=[REEL])
    anterior = {"price": 2000, "deliverables": [{"content_type": "Reel", "quantity": 1}, {"content_type": "Story", "quantity": 1}]}
    resultado = policy.apply(creator, previous_terms=anterior, proposed_terms={"price": 2000})
    assert resultado.terms["deliverables"] == [{"content_type": "Reel", "quantity": 1}], resultado.terms
    assert "deliverable_not_in_catalog:Story" in resultado.violations
    print("OK  - apply remove entregável herdado da contraparte que não está no catálogo próprio")


# --------------------------------------------------------------------------- engine (SQLite)


def _db():
    return dbmod.SessionLocal()


def _mandato(db, owner_type, owner_id, **kw):
    dados = dict(
        objective="teste", max_rounds=4, deliverables=[REEL], negotiable_fields=["price", "deliverables", "deadline"],
        non_negotiable_fields=[], exclusivity_allowed=False, extra_terms={},
    )
    dados.update(kw)
    return MandateRepository.create(db, owner_type=owner_type, owner_id=owner_id, data=dados)


def _negociacao(db, empresa_mandate, creator_mandate):
    return NegotiationRepository.create(
        db,
        {
            "company_id": empresa_mandate.owner_id, "creator_id": creator_mandate.owner_id,
            "company_mandate_id": empresa_mandate.id, "creator_mandate_id": creator_mandate.id,
            "state": "queued", "max_rounds": 4,
        },
    )


def _rodar(db, negociacao, max_turnos=12):
    for _ in range(max_turnos):
        negociacao = engine.run_turn(db, negociacao)
        if negociacao.state not in ("queued", "running"):
            break
    return negociacao


def _roteirizar(*turnos):
    """Substitui o Gemini por turnos fixos, na ordem em que os agentes falam."""
    fila = iter(turnos)
    engine._generate_turn = lambda **kw: (next(fila), None)


def _oferta(preco, entregaveis, **extra):
    return {
        "intent": "offer", "proposed_terms": {"price": preco, "deliverables": entregaveis, **extra},
        "message_template": "Proposta: {price}", "rationale": "teste",
    }


_ACEITO = {"intent": "accept", "proposed_terms": {}, "message_template": "Aceito {price}.", "rationale": "teste"}
_original_generate_turn = engine._generate_turn


def _turnos(db, negociacao):
    return NegotiationTurnRepository.get_all_for_negotiation(db, negociacao.id)


def teste_engine_accept_de_entregavel_fora_do_catalogo_vira_contraproposta():
    db = _db()
    empresa = _mandato(db, "company", "emp-a", ideal_price=1500, price_ceiling=3000, deliverables=[REEL, STORY])
    creator = _mandato(db, "creator", "cre-a", ideal_price=2500, price_floor=1800, deliverables=[REEL])
    n = _negociacao(db, empresa, creator)
    # empresa oferece Reel+Story; o agente do creator (catálogo só Reel) tenta "aceitar"
    _roteirizar(_oferta(2500, [{"content_type": "Reel", "quantity": 1}, {"content_type": "Story", "quantity": 1}]), _ACEITO)
    try:
        n = engine.run_turn(db, n)  # turno da empresa
        n = engine.run_turn(db, n)  # turno do creator
    finally:
        engine._generate_turn = _original_generate_turn
    turnos = _turnos(db, n)
    assert turnos[1].actor == "creator_agent"
    assert turnos[1].intent == "counter_offer", f"accept inválido deveria virar contraproposta, veio {turnos[1].intent}"
    assert "deliverable_not_in_catalog:Story" in turnos[1].policy_violations
    assert turnos[1].terms_after_policy["deliverables"] == [{"content_type": "Reel", "quantity": 1}]
    assert n.state == "running" and n.agreement is None, "não pode ter fechado acordo"
    print("OK  - engine: accept de Story por um creator que só faz Reel vira contraproposta só com Reel (sem acordo)")


def teste_engine_accept_abaixo_do_piso_nao_reescreve_o_preco_em_silencio():
    db = _db()
    empresa = _mandato(db, "company", "emp-b", ideal_price=1500, price_ceiling=3000)
    creator = _mandato(db, "creator", "cre-b", ideal_price=2500, price_floor=1800)
    n = _negociacao(db, empresa, creator)
    # agente do creator "aceita" os R$ 1.500 da empresa — abaixo do piso de R$ 1.800
    _roteirizar(_oferta(1500, [{"content_type": "Reel", "quantity": 1}]), _ACEITO, _ACEITO)
    try:
        n = _rodar(db, n)
    finally:
        engine._generate_turn = _original_generate_turn
    turnos = _turnos(db, n)
    assert turnos[1].intent == "counter_offer" and turnos[1].terms_after_policy["price"] == 1800
    assert "price_out_of_mandate" in turnos[1].policy_violations
    # a empresa então aceita os R$ 1.800 que ela MESMA viu na mesa → acordo legítimo
    assert turnos[2].actor == "company_agent" and turnos[2].intent == "accept"
    assert turnos[2].terms_after_policy["price"] == 1800
    assert n.state == "waiting_approval" and n.agreement is not None and n.agreement.total_value == 1800
    print("OK  - engine: accept abaixo do piso vira contraproposta de R$ 1.800; o acordo só fecha quando a empresa aceita esse valor")


def teste_engine_accept_valido_fecha_exatamente_a_oferta():
    db = _db()
    empresa = _mandato(db, "company", "emp-c", ideal_price=2000, price_ceiling=3000)
    creator = _mandato(db, "creator", "cre-c", ideal_price=2500, price_floor=1800)
    n = _negociacao(db, empresa, creator)
    _roteirizar(_oferta(2000, [{"content_type": "Reel", "quantity": 2}]), _ACEITO)
    try:
        n = _rodar(db, n)
    finally:
        engine._generate_turn = _original_generate_turn
    turnos = _turnos(db, n)
    assert turnos[1].intent == "accept" and turnos[1].policy_violations == []
    assert n.state == "waiting_approval"
    assert n.agreement.terms["price"] == 2000
    assert n.agreement.terms["deliverables"] == [{"content_type": "Reel", "quantity": 2}]
    print("OK  - engine: accept de oferta dentro do mandato fecha exatamente o que foi oferecido")


def teste_engine_catalogos_disjuntos_viram_impasse_sem_turnos():
    db = _db()
    empresa = _mandato(db, "company", "emp-d", ideal_price=1500, price_ceiling=3000, deliverables=[STORY])
    creator = _mandato(db, "creator", "cre-d", ideal_price=2500, price_floor=1800, deliverables=[REEL])
    n = _negociacao(db, empresa, creator)
    n = _rodar(db, n)
    assert n.state == "impasse", n.state
    assert "não se cruzam" in n.outcome_reason
    assert _turnos(db, n) == [] and n.agreement is None
    print("OK  - engine: catálogos que não se cruzam viram impasse imediato, sem turnos nem acordo")


def teste_engine_agente_deterministico_fecha_acordo_valido_nos_dois_mandatos():
    db = _db()
    empresa = _mandato(db, "company", "emp-e", ideal_price=1500, price_ceiling=3000, deliverables=[REEL, STORY])
    creator = _mandato(db, "creator", "cre-e", ideal_price=2500, price_floor=1800, deliverables=[REEL, POST])
    n = _negociacao(db, empresa, creator)
    n = _rodar(db, n)  # sem chave Gemini → fallback_agent nos dois lados
    assert n.state == "waiting_approval", (n.state, n.outcome_reason)
    termos = n.agreement.terms
    assert 1800 <= termos["price"] <= 3000
    tipos = {d["content_type"] for d in termos["deliverables"]}
    assert tipos == {"Reel"}, f"só Reel existe nos dois catálogos, veio {tipos}"
    print(f"OK  - engine: agentes determinísticos fecham R$ {termos['price']} só com o entregável comum (Reel)")


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("teste_") and callable(funcao):
            funcao()
    print("\nTodos os testes de validação do accept (ConectaAI) passaram.")
