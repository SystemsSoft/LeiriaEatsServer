"""
Teste da intervenção humana na negociação do ConectaAI (conectaai/services/negotiation/human.py).

Antes, empresa e creator só podiam autorizar um limite ou cancelar: não havia como escrever para o outro
usuário nem responder a uma jogada dos agentes com uma contraproposta própria.

- MENSAGEM (texto livre): vai para a conversa entre os dois usuários (a de "Conversas"), avisa o outro lado,
  e o agente do outro lado a lê como contexto no próximo turno. Vale em qualquer estado.
- CONTRAPROPOSTA (preço/prazo/exclusividade/entregáveis + texto): vira um turno humano, reabre a conversa dos
  agentes e o agente do outro lado responde DENTRO DO MANDATO DELE. Se havia um acordo aguardando aprovação,
  ele é substituído. Não vale enquanto os agentes estão falando (evita corrida com o runner).

Roda contra um SQLite em memória, sem rede.

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_human_turns.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONECTAAI_GEMINI_API_KEY", "")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import conectaai.core.database as dbmod

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.engine = _engine
dbmod.SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

from conectaai.core.config import settings  # noqa: E402
from conectaai.models import sql_models  # noqa: E402
from conectaai.repositories.agreement_repo import AgreementRepository  # noqa: E402
from conectaai.repositories.conversation_repo import ConversationRepository  # noqa: E402
from conectaai.repositories.mandate_repo import MandateRepository  # noqa: E402
from conectaai.repositories.negotiation_repo import NegotiationRepository  # noqa: E402
from conectaai.repositories.negotiation_turn_repo import NegotiationTurnRepository  # noqa: E402
from conectaai.services.negotiation import engine, human  # noqa: E402

settings.GEMINI_API_KEYS = []  # agente determinístico por padrão; os testes de contexto roteirizam _generate_turn
dbmod.Base.metadata.create_all(_engine)

REEL = {"content_type": "Reel", "min_qty": 1, "max_qty": 3}
_original_generate_turn = engine._generate_turn


def _mandato(db, owner_type, owner_id, **kw):
    dados = dict(
        objective="teste", max_rounds=4, deliverables=[REEL], negotiable_fields=["price", "deliverables", "deadline"],
        non_negotiable_fields=[], exclusivity_allowed=False, extra_terms={},
    )
    dados.update(kw)
    return MandateRepository.create(db, owner_type=owner_type, owner_id=owner_id, data=dados)


def _cenario(db, sufixo, *, com_agente_creator=True, empresa=None, creator=None):
    """Empresa e creator com um agente cada; devolve a negociação `queued` e a conversa entre eles."""
    emp_id, cre_id = f"emp-{sufixo}", f"cre-{sufixo}"
    m_emp = _mandato(db, "company", emp_id, **(empresa or dict(ideal_price=1500, price_ceiling=3000)))
    m_cre = _mandato(db, "creator", cre_id, **(creator or dict(ideal_price=2500, price_floor=1800))) if com_agente_creator else None
    conversa = ConversationRepository.get_or_create(db, creator_id=cre_id, company_id=emp_id, campaign_id=None, campaign_name="teste")
    n = NegotiationRepository.create(
        db,
        {
            "company_id": emp_id, "creator_id": cre_id, "company_mandate_id": m_emp.id,
            "creator_mandate_id": m_cre.id if m_cre else None, "conversation_id": conversa.id,
            "state": "queued" if m_cre else "waiting_human_creator", "max_rounds": 4,
        },
    )
    return n, conversa


def _rodar(db, n, max_turnos=14):
    for _ in range(max_turnos):
        n = engine.run_turn(db, n)
        if n.state not in ("queued", "running"):
            break
    return n


def _erro(func):
    try:
        func()
    except human.HumanActionError as e:
        return e
    raise AssertionError("deveria ter sido recusado")


# --------------------------------------------------------------------------- mensagem (chat entre os usuários)


def teste_mensagem_vai_para_a_conversa_e_nao_mexe_na_negociacao():
    db = dbmod.SessionLocal()
    n, conversa = _cenario(db, "m1")
    n = _rodar(db, n)
    assert n.state == "waiting_approval"
    antes = (n.state, n.round_no, dict(n.current_offer))
    msg = human.post_message(db, n, sender_side="company", text="Oi! Podemos incluir 1 story no pacote?")
    db.expire_all()
    n = NegotiationRepository.get_by_id(db, n.id)
    assert (n.state, n.round_no, dict(n.current_offer)) == antes, "mensagem não pode alterar a negociação"
    conversa = ConversationRepository.get_by_id(db, conversa.id)
    assert [(m.sender_id, m.text) for m in conversa.messages][-1] == (n.company_id, "Oi! Podemos incluir 1 story no pacote?")
    assert msg.id == conversa.messages[-1].id
    print("OK  - mensagem: vai para a conversa entre os usuários e não altera estado/oferta/rodada")


def teste_mensagem_vale_ate_com_a_negociacao_encerrada_e_recusa_texto_vazio():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "m2", empresa=dict(ideal_price=500, price_ceiling=900), creator=dict(ideal_price=2500, price_floor=2000))
    n = _rodar(db, n)
    assert n.state == "impasse"
    human.post_message(db, n, sender_side="creator", text="Que pena! Vamos tentar de novo depois?")
    assert _erro(lambda: human.post_message(db, n, sender_side="creator", text="   ")).status_code == 400
    print("OK  - mensagem: continua possível após impasse; texto vazio é recusado")


# --------------------------------------------------------------------------- contraproposta


def teste_contraproposta_substitui_o_acordo_e_o_agente_do_outro_lado_responde():
    db = dbmod.SessionLocal()
    n, conversa = _cenario(db, "c1")
    n = _rodar(db, n)
    assert n.state == "waiting_approval"
    acordo_id = n.agreement.id
    assert n.agreement.total_value == 1800
    max_antes, rodada_antes = n.max_rounds, n.round_no

    # a empresa (pessoa) acha R$ 1.800 caro e contrapropõe R$ 1.700
    n = human.post_counter(db, n, sender_side="company", text="Consigo fechar hoje por esse valor.", terms={"price": 1700})
    assert n.state == "queued" and n.last_actor == "human_company"
    assert n.round_no == rodada_antes + 1 and n.max_rounds == max_antes + 1, "a rodada humana não consome as dos agentes"
    assert n.current_offer["price"] == 1700
    assert AgreementRepository.get_by_id(db, acordo_id).status == "superseded"

    turnos = NegotiationTurnRepository.get_all_for_negotiation(db, n.id)
    assert turnos[-1].actor == "human_company" and turnos[-1].intent == "counter_offer"
    assert "R$ 1.700,00" in turnos[-1].message_text and "Consigo fechar hoje" in turnos[-1].message_text
    assert turnos[-1].message_id, "espelhado na conversa entre os usuários"

    # o agente do creator responde dentro do mandato dele (piso R$ 1.800) e a empresa fecha
    n = _rodar(db, n)
    turnos = NegotiationTurnRepository.get_all_for_negotiation(db, n.id)
    resposta = turnos[[t.actor for t in turnos].index("human_company") + 1]
    assert resposta.actor == "creator_agent", f"quem responde à empresa é o agente do creator, não {resposta.actor}"
    assert resposta.terms_after_policy["price"] >= 1800, "o agente do creator não pode ficar abaixo do piso dele"
    assert n.state == "waiting_approval" and n.agreement.id == acordo_id, "o mesmo registro de acordo é reaproveitado"
    assert n.agreement.status == "awaiting_approval" and n.agreement.total_value == 1800
    assert n.agreement.company_approved_at is None and n.agreement.creator_approved_at is None
    conversa = ConversationRepository.get_by_id(db, conversa.id)
    assert any("Contraproposta" in m.text for m in conversa.messages)
    print("OK  - contraproposta: substitui o acordo, o agente do creator responde no mandato dele e um novo acordo é gerado")


def teste_contraproposta_do_creator_e_respondida_pelo_agente_da_empresa():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "c2", empresa=dict(ideal_price=1000, price_ceiling=2600), creator=dict(ideal_price=2500, price_floor=1800))
    n = _rodar(db, n)
    assert n.state == "waiting_approval"
    n = human.post_counter(db, n, sender_side="creator", text="", terms={"price": 2400, "exclusivity": False})
    turnos = NegotiationTurnRepository.get_all_for_negotiation(db, n.id)
    assert turnos[-1].actor == "human_creator" and turnos[-1].message_text.startswith("Contraproposta")
    n = _rodar(db, n)
    turnos = NegotiationTurnRepository.get_all_for_negotiation(db, n.id)
    seguintes = [t.actor for t in turnos[[t.actor for t in turnos].index("human_creator") + 1:]]
    assert seguintes[0] == "company_agent", f"depois do creator (pessoa) fala o agente da empresa, veio {seguintes}"
    if len(seguintes) > 1:
        assert seguintes[1] == "creator_agent", "e depois volta a alternar com o agente do creator"
    print("OK  - contraproposta do creator: responde o agente da empresa e a alternância entre os lados se mantém")


def teste_contraproposta_e_recusada_enquanto_os_agentes_negociam_ou_apos_o_acordo_aprovado():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "c3")
    n = NegotiationRepository.update(db, n, {"state": "running"})
    assert _erro(lambda: human.post_counter(db, n, sender_side="company", text="", terms={"price": 1600})).status_code == 409
    n = NegotiationRepository.update(db, n, {"state": "queued"})
    n = _rodar(db, n)
    assert n.state == "waiting_approval"
    AgreementRepository.update(db, n.agreement, {"status": "approved"})
    assert _erro(lambda: human.post_counter(db, n, sender_side="company", text="", terms={"price": 1600})).status_code == 400
    n2, _ = _cenario(db, "c3b", empresa=dict(ideal_price=500, price_ceiling=900), creator=dict(ideal_price=2500, price_floor=2000))
    n2 = _rodar(db, n2)
    assert n2.state == "impasse"
    assert _erro(lambda: human.post_counter(db, n2, sender_side="company", text="", terms={"price": 1600})).status_code == 400
    print("OK  - contraproposta: recusada durante o turno dos agentes (409), após acordo aprovado e após negociação encerrada")


def teste_contraproposta_valida_os_termos():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "c4")
    n = _rodar(db, n)
    for termos in ({}, {"price": 0}, {"price": -5}, {"price": 99_999_999}, {"deadline_days": 0}, {"deliverables": [{"content_type": "Reel", "quantity": 0}]}):
        assert _erro(lambda: human.post_counter(db, n, sender_side="company", text="x", terms=termos)).status_code == 400, termos
    print("OK  - contraproposta: sem termos, preço inválido, prazo ou quantidade inválidos são recusados")


def teste_creator_sem_agente_contraproposta_fica_manual():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "c5", com_agente_creator=False)
    assert n.state == "waiting_human_creator"
    n = human.post_counter(db, n, sender_side="company", text="Proposta direta.", terms={"price": 2000})
    assert n.state == "waiting_human_creator", "sem agente do outro lado não há quem rodar — fica esperando a pessoa"
    assert n.current_offer["price"] == 2000
    turnos = NegotiationTurnRepository.get_all_for_negotiation(db, n.id)
    assert [t.actor for t in turnos] == ["human_company"]
    print("OK  - creator sem agente: a contraproposta é registrada e fica aguardando a pessoa (nada é enfileirado)")


# --------------------------------------------------------------------------- contexto do agente


def teste_agente_le_o_texto_da_contraproposta_e_as_mensagens_do_chat():
    db = dbmod.SessionLocal()
    n, _ = _cenario(db, "x1")
    n = _rodar(db, n)
    n = human.post_counter(db, n, sender_side="company", text="Fecho por 1700 se vier junto um story extra.", terms={"price": 1700})
    human.post_message(db, n, sender_side="company", text="Aliás, a entrega pode ser na próxima semana.")
    vistos = {}

    def espiao(**kw):
        vistos.update(kw)
        return _original_generate_turn(**kw)

    engine._generate_turn = espiao
    try:
        engine.run_turn(db, n)  # turno do agente do creator, respondendo à pessoa da empresa
    finally:
        engine._generate_turn = _original_generate_turn
    assert vistos["side"] == "creator"
    assert "story extra" in vistos["counterpart_text"], "o agente precisa ver o texto da contraproposta"
    assert "próxima semana" in vistos["counterpart_text"], "e as mensagens do chat enviadas depois"
    assert vistos["current_offer"]["price"] == 1700
    print("OK  - o agente do outro lado recebe como contexto o texto da contraproposta e as mensagens do chat")


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("teste_") and callable(funcao):
            funcao()
    print("\nTodos os testes de intervenção humana (ConectaAI) passaram.")
