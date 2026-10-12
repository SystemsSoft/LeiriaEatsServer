"""
Teste da criação de campanha item por item pelo Assistente de IA (POST /ai/campaigns/intake).

O app manda as respostas até agora, cada uma marcada com o item perguntado; o servidor devolve a próxima
pergunta ou, quando nada falta, o rascunho. Confere: a ordem das perguntas, que "não sei"/"tanto faz" conta como
respondido (a pergunta não volta), a leitura por regras quando não há IA, que a IA pula itens já adiantados numa
resposta e que um ajuste depois do resumo muda o rascunho.

Roda contra um SQLite em memória, sem rede (o Gemini é substituído por um fake).

Execução (na raiz do repo do servidor):
    python3 tests/test_conectaai_campaign_intake.py
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

from conectaai.api.routes.campaign_ai_routes import campaign_intake_step  # noqa: E402
from conectaai.core.config import settings  # noqa: E402
from conectaai.core.security import CurrentUser  # noqa: E402
from conectaai.schemas.ai import CampaignIntakeRequest, IntakeAnswer  # noqa: E402
from conectaai.schemas.ai_structured import CampaignIntakeExtraction, ProposedDeliverable  # noqa: E402
from conectaai.services.ai import campaign_intake, gemini_client  # noqa: E402

dbmod.Base.metadata.create_all(_engine)

_USUARIO = CurrentUser("empresa", "company")


def _passo(respostas):
    answers = [IntakeAnswer(field=f, text=t) for f, t in respostas]
    return campaign_intake_step(CampaignIntakeRequest(answers=answers), current_user=_USUARIO, db=dbmod.SessionLocal())


def _sem_ia():
    settings.GEMINI_API_KEYS = []


def _fake_gemini(extraction):
    chamadas = []

    def fake(**kwargs):
        chamadas.append(kwargs)
        return gemini_client.GeminiCallResult(extraction, "{}", 0, 1, "ok", 10)

    gemini_client.generate_json = fake
    settings.GEMINI_API_KEYS = ["fake"]
    return chamadas


def teste_sem_respostas_pergunta_o_primeiro_item():
    _sem_ia()
    passo = _passo([])
    assert passo.next_field == "product", passo.next_field
    assert passo.question and passo.draft is None
    print("OK  - conversa vazia: primeira pergunta é o que divulgar")


def teste_fluxo_completo_sem_ia_pergunta_item_por_item_e_monta_o_rascunho():
    _sem_ia()
    respostas = [
        ("product", "Lançamento do protetor solar FPS 50"),
        ("categories", "Beleza, skincare"),
        ("city", "Qualquer lugar"),
        ("audience", "Mulheres de 25 a 40 anos"),
        ("target_count", "3"),
        ("budget_total", "4.500 €"),
        ("deliverables", "Reels e stories"),
        ("preferences", "Mais engajamento"),
    ]
    for i, (campo, _) in enumerate(respostas):
        passo = _passo(respostas[:i])
        assert passo.next_field == campo, (i, passo.next_field, campo)
        assert passo.source == "heuristic"

    final = _passo(respostas)
    assert final.next_field == "" and final.draft is not None
    d = final.draft
    assert d.objective == "Lançamento do protetor solar FPS 50"
    assert d.desired_categories == ["Beleza", "skincare"], d.desired_categories
    assert d.city == ""
    assert d.target_count == 3
    assert d.budget_total == 4500 and d.ideal_price == 1500 and d.price_ceiling == 1950, (d.budget_total, d.ideal_price, d.price_ceiling)
    assert [x.content_type for x in d.deliverables] == ["Reel", "Story"], d.deliverables
    assert final.audience == "Mulheres de 25 a 40 anos" and final.preferences == "Mais engajamento"
    print("OK  - sem IA: pergunta na ordem, lê cada resposta por regras e monta o rascunho")


def teste_nao_sei_conta_como_respondido():
    _sem_ia()
    passo = _passo([("product", "Tênis de corrida"), ("categories", "Qualquer nicho")])
    assert passo.next_field == "city", passo.next_field
    passo = _passo([("product", "Tênis"), ("categories", "fitness"), ("city", "SP"), ("audience", "sem público específico"),
                    ("target_count", "2"), ("budget_total", "Ainda não sei")])
    assert passo.next_field == "deliverables", passo.next_field
    print("OK  - 'qualquer nicho' / 'ainda não sei' avançam para o próximo item")


def teste_leitura_de_valores_em_euros():
    casos = {"2.500 €": 2500, "€ 2.500": 2500, "3 mil": 3000, "1500": 1500, "1.234,56 €": 1234.56, "5k": 5000, "uns 10 mil euros": 10000,
             "R$ 2.500": 2500}  # o formato antigo (reais) continua sendo entendido
    for texto, esperado in casos.items():
        assert campaign_intake._parse_money(texto) == esperado, (texto, campaign_intake._parse_money(texto))
    print("OK  - valores em euros ('3 mil', '2.500 €', '5k'…) são lidos certo")


def teste_ia_pula_itens_ja_adiantados_numa_resposta():
    chamadas = _fake_gemini(CampaignIntakeExtraction(
        campaign_name="Protetor FPS 50", product="Divulgar protetor solar", categories=["beleza"], city="São Paulo", target_count=3,
    ))
    passo = _passo([("product", "Quero 3 influenciadoras de beleza em SP para divulgar meu protetor solar")])
    assert passo.source == "gemini"
    assert passo.next_field == "audience", passo.next_field  # nicho, cidade e quantidade já vieram
    assert "[o que divulgar] Quero 3 influenciadoras" in chamadas[0]["user_content"]
    print("OK  - IA: itens adiantados numa resposta não são perguntados de novo")


def teste_ajuste_depois_do_resumo_muda_o_rascunho():
    _fake_gemini(CampaignIntakeExtraction(
        campaign_name="Protetor", product="Protetor solar", categories=["beleza"], any_location=True, audience="mulheres",
        target_count=5, budget_total=10000, deliverables=[ProposedDeliverable(content_type="Reel", quantity=2)], preferences="mais seguidores",
    ))
    passo = _passo([("product", "Protetor solar"), ("adjust", "na verdade quero 5 influenciadoras e orçamento de 10 mil")])
    assert passo.next_field == "" and passo.draft.target_count == 5 and passo.draft.ideal_price == 2000, passo.draft
    assert passo.draft.deliverables[0].max_qty == 2
    print("OK  - ajuste livre depois do resumo atualiza o rascunho")


def teste_campo_que_a_ia_deixou_vazio_usa_a_resposta_direta():
    _fake_gemini(CampaignIntakeExtraction(product="Protetor solar"))  # IA não entendeu o orçamento
    passo = _passo([("product", "Protetor solar"), ("categories", "beleza"), ("city", "Qualquer lugar"), ("audience", "mulheres"),
                    ("target_count", "2"), ("budget_total", "3 mil"), ("deliverables", "reels"), ("preferences", "não")])
    assert passo.next_field == "" and passo.draft.budget_total == 3000 and passo.draft.target_count == 2, passo.draft
    print("OK  - o que a IA deixa vazio é lido da resposta àquele item")


if __name__ == "__main__":
    teste_sem_respostas_pergunta_o_primeiro_item()
    teste_fluxo_completo_sem_ia_pergunta_item_por_item_e_monta_o_rascunho()
    teste_nao_sei_conta_como_respondido()
    teste_leitura_de_valores_em_euros()
    teste_ia_pula_itens_ja_adiantados_numa_resposta()
    teste_ajuste_depois_do_resumo_muda_o_rascunho()
    teste_campo_que_a_ia_deixou_vazio_usa_a_resposta_direta()
