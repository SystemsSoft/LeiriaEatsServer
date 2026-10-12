"""
Testes do RouteSequencer (PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 4).

Reproduz os exemplos numéricos do próprio plano (4.1, 4.2, 4.4) usando um `travel_fn`
injetado — os testes fixam os tempos de viagem exatamente como o plano os enuncia, em vez
de tentar reverter coordenadas geográficas que produzissem esses minutos via Haversine.
Isso isola o teste da lógica de sequenciamento e da fórmula de custo, sem depender da
implementação de `travel_minutes` (que tem seu próprio ponto de extensão, ver o módulo).

Execução:
    python3 tests/test_route_sequencer.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.route_sequencer import Point, Stop, sequence_stops, _evaluate

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

DRIVER = Point(0.0, 0.0)
CUSTOMER = Point(1.0, 1.0)
POINT_A = Point(2.0, 2.0)   # pizzaria
POINT_B = Point(3.0, 3.0)   # sushi
POINT_C = Point(4.0, 4.0)   # gelataria
EM_TRANSITO = Point(5.0, 5.0)  # posição do estafeta a caminho de B, no recálculo de 4.2

NO_TOLERANCE = 10_000.0  # tolerância "infinita" — isola o termo de excesso nos testes que não o testam


def _make_travel_fn(times: dict[tuple[str, str], float]):
    labels = {
        DRIVER: "estafeta", CUSTOMER: "cliente",
        POINT_A: "A", POINT_B: "B", POINT_C: "C",
        EM_TRANSITO: "em_transito",
    }
    symmetric = dict(times)
    for (a, b), v in times.items():
        symmetric.setdefault((b, a), v)

    def travel_fn(p1: Point, p2: Point) -> float:
        return float(symmetric[(labels[p1], labels[p2])])

    return travel_fn


# Tempos de viagem do exemplo 4.1 (estafeta->A=5, estafeta->B=8, estafeta->C=12;
# A-B=6, B-C=5, A-C=9; A->cliente=7, B->cliente=8, C->cliente=10).
TRAVEL_4_1 = _make_travel_fn({
    ("estafeta", "A"): 5, ("estafeta", "B"): 8, ("estafeta", "C"): 12,
    ("A", "B"): 6, ("B", "C"): 5, ("A", "C"): 9,
    ("A", "cliente"): 7, ("B", "cliente"): 8, ("C", "cliente"): 10,
})

# Mesmos tempos, mas com as distâncias do estafeta às paragens invertidas
# (estafeta->A passa a 12, estafeta->C passa a 5) — "prova de que a distância não é o motivo".
TRAVEL_4_1_INVERTIDO = _make_travel_fn({
    ("estafeta", "A"): 12, ("estafeta", "B"): 8, ("estafeta", "C"): 5,
    ("A", "B"): 6, ("B", "C"): 5, ("A", "C"): 9,
    ("A", "cliente"): 7, ("B", "cliente"): 8, ("C", "cliente"): 10,
})

# Tempos do recálculo de 4.2: o estafeta já recolheu em C e está "em_transito" para B.
TRAVEL_4_2 = _make_travel_fn({
    ("em_transito", "B"): 3, ("em_transito", "A"): 7,
    ("A", "B"): 6,
    ("A", "cliente"): 7, ("B", "cliente"): 8,
})


def teste_4_1_prontidao_vence_proximidade():
    """C→B→A (32 min) vence a ordem ingénua por proximidade A→B→C (46 min)."""
    stop_a = Stop("A", POINT_A, NOW + timedelta(minutes=25), NO_TOLERANCE)
    stop_b = Stop("B", POINT_B, NOW + timedelta(minutes=12), NO_TOLERANCE)
    stop_c = Stop("C", POINT_C, NOW + timedelta(minutes=8), NO_TOLERANCE)

    best = sequence_stops([stop_a, stop_b, stop_c], DRIVER, CUSTOMER, now=NOW, travel_fn=TRAVEL_4_1)
    assert best.order == ("C", "B", "A"), best.order
    assert best.delivery_at == NOW + timedelta(minutes=32), best.delivery_at

    naive = _evaluate((stop_a, stop_b, stop_c), DRIVER, CUSTOMER, NOW, TRAVEL_4_1)
    assert naive.delivery_at == NOW + timedelta(minutes=46), naive.delivery_at
    assert best.cost < naive.cost

    print("OK  - 4.1: prontidão vence proximidade (C->B->A, 32min < A->B->C, 46min)")


def teste_4_1_distancia_so_desempata():
    """
    Invertendo as distâncias do estafeta às paragens (A passa a 12min, C a 5min), a ordem
    por prontidão (C->B->A) continua a entregar em 32min contra 46min da ingénua
    A->B->C — a mesma prova do plano de que a distância ao estafeta não é o que decide.

    Não afirma que C->B->A seja o argmin global aqui: com as novas distâncias, o termo
    alfa (soma do tempo em trânsito) passa a preferir B->C->A por uma margem de 1 min,
    o que é o comportamento correto da função de custo — só não é o ponto que este
    parágrafo do plano estava a ilustrar (ver teste_4_4 para o efeito do termo de excesso).
    """
    stop_a = Stop("A", POINT_A, NOW + timedelta(minutes=25), NO_TOLERANCE)
    stop_b = Stop("B", POINT_B, NOW + timedelta(minutes=12), NO_TOLERANCE)
    stop_c = Stop("C", POINT_C, NOW + timedelta(minutes=8), NO_TOLERANCE)

    prontidao = _evaluate((stop_c, stop_b, stop_a), DRIVER, CUSTOMER, NOW, TRAVEL_4_1_INVERTIDO)
    ingenua = _evaluate((stop_a, stop_b, stop_c), DRIVER, CUSTOMER, NOW, TRAVEL_4_1_INVERTIDO)
    assert prontidao.delivery_at == NOW + timedelta(minutes=32), prontidao.delivery_at
    assert ingenua.delivery_at == NOW + timedelta(minutes=46), ingenua.delivery_at
    assert prontidao.cost < ingenua.cost

    print("OK  - 4.1: distância invertida não muda a comparação (C->B->A, 32min < A->B->C, 46min)")


def teste_4_2_recalculo_so_sobre_o_que_falta():
    """
    t=14: B atrasa para pronto aos 30. O estafeta já recolheu C e está a caminho de B.
    Recalculando só sobre {A, B}: A->B (entrega 39) vence sobre manter B->A (entrega 43).
    """
    now_recalculo = NOW + timedelta(minutes=14)
    stop_a = Stop("A", POINT_A, NOW + timedelta(minutes=25), NO_TOLERANCE)  # ready_at original, inalterado
    stop_b = Stop("B", POINT_B, NOW + timedelta(minutes=30), NO_TOLERANCE)  # atrasou de 12 para 30

    best = sequence_stops([stop_a, stop_b], EM_TRANSITO, CUSTOMER, now=now_recalculo, travel_fn=TRAVEL_4_2)
    assert best.order == ("A", "B"), best.order
    assert best.delivery_at == NOW + timedelta(minutes=39), best.delivery_at

    manter = _evaluate((stop_b, stop_a), EM_TRANSITO, CUSTOMER, now_recalculo, TRAVEL_4_2)
    assert manter.delivery_at == NOW + timedelta(minutes=43), manter.delivery_at
    assert best.cost < manter.cost

    print("OK  - 4.2: recálculo troca para A->B (39min) quando B atrasa, em vez de manter B->A (43min)")


def teste_4_4_tolerancia_muda_o_vencedor():
    """
    Com tolerância (A=35 pizzaria, B=30 sushi, C=12 gelataria), o vencedor deixa de ser
    C->B->A (76.6) e passa a ser B->C->A (54.2) — 1 min mais lento na entrega (33 vs 32),
    mas o gelado sai de 20 min de trânsito para 16.
    """
    stop_a = Stop("A", POINT_A, NOW + timedelta(minutes=25), tolerance_minutes=35)
    stop_b = Stop("B", POINT_B, NOW + timedelta(minutes=12), tolerance_minutes=30)
    stop_c = Stop("C", POINT_C, NOW + timedelta(minutes=8), tolerance_minutes=12)

    best = sequence_stops([stop_a, stop_b, stop_c], DRIVER, CUSTOMER, now=NOW, travel_fn=TRAVEL_4_1)
    assert best.order == ("B", "C", "A"), best.order
    assert best.delivery_at == NOW + timedelta(minutes=33), best.delivery_at
    assert abs(best.cost - 54.2) < 1e-9, best.cost

    sem_prioridade_perecivel = _evaluate((stop_c, stop_b, stop_a), DRIVER, CUSTOMER, NOW, TRAVEL_4_1)
    assert sem_prioridade_perecivel.delivery_at == NOW + timedelta(minutes=32)
    assert abs(sem_prioridade_perecivel.cost - 76.6) < 1e-9, sem_prioridade_perecivel.cost
    assert best.cost < sem_prioridade_perecivel.cost

    # Trânsito do gelado (C) cai de 20 min (recolhido primeiro) para 16 min (recolhido 2º)
    gelado_no_vencedor = next(t for t in best.stop_timings if t.stop_id == "C")
    gelado_transito = (best.delivery_at - gelado_no_vencedor.pickup).total_seconds() / 60
    assert abs(gelado_transito - 16) < 1e-9, gelado_transito

    print("OK  - 4.4: tolerância de trânsito muda o vencedor para B->C->A (custo 54.2 < 76.6)")


def teste_ordem_unica_nao_precisa_de_permutar():
    """Uma paragem só tem uma ordem possível — sanity check do caso trivial."""
    stop_a = Stop("A", POINT_A, NOW + timedelta(minutes=5), NO_TOLERANCE)
    best = sequence_stops([stop_a], DRIVER, CUSTOMER, now=NOW, travel_fn=TRAVEL_4_1)
    assert best.order == ("A",)

    print("OK  - paragem única não quebra (permutação trivial)")


if __name__ == "__main__":
    teste_4_1_prontidao_vence_proximidade()
    teste_4_1_distancia_so_desempata()
    teste_4_2_recalculo_so_sobre_o_que_falta()
    teste_4_4_tolerancia_muda_o_vencedor()
    teste_ordem_unica_nao_precisa_de_permutar()
    print("\nTodos os testes do RouteSequencer (secção 4 do plano) passaram.")
