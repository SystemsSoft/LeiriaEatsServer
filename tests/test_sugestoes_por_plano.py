"""
Testes de "Melhores sugestões" (plano ESSENCE) e "Outras sugestões" (plano SMART) no chat de IA.

Bug (24/09/2026): "tem alguma pizza?" só devolvia pizzas do restaurante ESSENCE. A busca cortava em 6
produtos NO TOTAL (as 6 vagas iam para o plano que ranqueia melhor) e os cards eram só os produtos que a
IA citava pelo nome — as pizzas do restaurante SMART nunca chegavam ao app.

Execução:
    python3 tests/test_sugestoes_por_plano.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService


def _resultados(planos):
    """planos: lista de 'ESSENCE'/'SMART'/None na ordem de relevância. Registra o plano no AIService."""
    AIService._restaurant_plan_by_product_id = {i: p for i, p in enumerate(planos, 1) if p}
    return [{"obj": SimpleNamespace(id=i), "score": 1.0 - i / 100} for i in range(1, len(planos) + 1)]


def _ids(resultados):
    return [r["obj"].id for r in resultados]


def teste_limite_por_plano_mantem_os_dois_planos():
    # 8 do ESSENCE ranqueiam antes de 4 do SMART: o corte global em 6 perdia o SMART inteiro
    res = _resultados(["ESSENCE"] * 8 + ["SMART"] * 4)
    assert _ids(res[:6]) == [1, 2, 3, 4, 5, 6]                       # comportamento antigo: só ESSENCE
    por_plano = AIService._limitar_por_plano(res, 6)
    assert _ids(por_plano) == [1, 2, 3, 4, 5, 6, 9, 10, 11, 12], _ids(por_plano)
    print("OK  - até 6 de CADA plano: ESSENCE não ocupa mais todas as vagas")


def teste_limite_por_plano_respeita_a_ordem_e_o_teto():
    res = _resultados(["SMART", "ESSENCE", "SMART", "ESSENCE", "SMART"])
    assert _ids(AIService._limitar_por_plano(res, 2)) == [1, 2, 3, 4], "no máximo 2 de cada plano, na ordem de relevância"
    print("OK  - respeita a ordem de relevância e o teto por plano")


def teste_produto_sem_plano_tem_grupo_proprio():
    res = _resultados([None, None, "ESSENCE"])
    assert _ids(AIService._limitar_por_plano(res, 1)) == [1, 3]
    print("OK  - produto sem plano conhecido não é engolido pelos outros planos")


def _pool():
    return [
        {"id": 1, "name": "Pizza Napolitana", "restaurant_plan": "ESSENCE"},
        {"id": 2, "name": "Pizza de Calabresa", "restaurant_plan": "ESSENCE"},
        {"id": 3, "name": "Queijo", "restaurant_plan": "SMART"},
        {"id": 4, "name": "Calabresa", "restaurant_plan": "SMART"},
        {"id": 5, "name": "Taco Mexicano", "restaurant_plan": "SMART"},   # veio do catálogo, NÃO da busca
    ]


def teste_outras_sugestoes_completa_com_smart_relevante():
    citados = [p for p in _pool() if p["id"] in (1, 2)]                      # a IA só citou as ESSENCE
    r = HybridAIService._completar_com_outras_sugestoes(citados, _pool(), {1, 2, 3, 4}, "product_search")
    assert [p["id"] for p in r] == [1, 2, 3, 4], r                           # + as SMART que a busca achou
    print("OK  - 'Outras sugestões' recebe as SMART relevantes mesmo sem a IA citá-las")


def teste_nunca_traz_produto_que_a_busca_nao_achou():
    citados = [p for p in _pool() if p["id"] == 1]
    r = HybridAIService._completar_com_outras_sugestoes(citados, _pool(), {1, 3}, "product_search")
    assert 5 not in [p["id"] for p in r], "o resto do catálogo (id 5) não pode virar card"
    print("OK  - só entram produtos que vieram da busca (nunca o resto do catálogo)")


def teste_sem_cards_extras_fora_de_busca_de_produto_ou_sem_citacao():
    citados = [p for p in _pool() if p["id"] == 1]
    for intent in ("greeting", "general_question", "consultation_needed", "specific_question"):
        assert HybridAIService._completar_com_outras_sugestoes(citados, _pool(), {1, 3, 4}, intent) == citados, intent
    assert HybridAIService._completar_com_outras_sugestoes([], _pool(), {3, 4}, "product_search") == []
    print("OK  - sem extras em saudação/esclarecimento nem quando a IA não sugeriu nada")


def teste_nao_duplica_e_respeita_o_limite():
    pool = [{"id": i, "name": f"S{i}", "restaurant_plan": "SMART"} for i in range(1, 11)]
    citados = pool[:2]                                                       # a IA já citou 2 SMART
    r = HybridAIService._completar_com_outras_sugestoes(citados, pool, {p["id"] for p in pool}, "product_search")
    ids = [p["id"] for p in r]
    assert ids[:2] == [1, 2] and len(ids) == len(set(ids)) and len(ids) == 2 + 6, ids
    print("OK  - não duplica os já citados e acrescenta no máximo 6")


if __name__ == "__main__":
    teste_limite_por_plano_mantem_os_dois_planos()
    teste_limite_por_plano_respeita_a_ordem_e_o_teto()
    teste_produto_sem_plano_tem_grupo_proprio()
    teste_outras_sugestoes_completa_com_smart_relevante()
    teste_nunca_traz_produto_que_a_busca_nao_achou()
    teste_sem_cards_extras_fora_de_busca_de_produto_ou_sem_citacao()
    teste_nao_duplica_e_respeita_o_limite()
    print("\nTodos os testes de sugestões por plano passaram.")
