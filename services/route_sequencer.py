# Arquivo: services/route_sequencer.py
"""
PLANO_RECOLHA_MULTI_RESTAURANTE.md, secção 4 — RouteSequencer.

Sequenciamento por enumeração exaustiva. Com o limite de MAX_RESTAURANTES_POR_PEDIDO = 3
(PLANO_LIMITE_RESTAURANTES.md), o espaço de soluções é 3! = 6 permutações — não é um
problema de TSP, é busca completa e ótima em microssegundos. Não introduzir solver,
heurística nem API de routing externa (ver secção 10 do plano, "O que NÃO fazer").

Módulo puro: sem SQLAlchemy, sem I/O, sem chamada externa — testável sem banco
(ver tests/test_route_sequencer.py, que reproduz os exemplos numéricos das secções
4.1, 4.2 e 4.4 do plano).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import permutations
from typing import Callable, Optional, Sequence, Tuple

TravelFn = Callable[["Point", "Point"], float]

# Pesos da função de custo (secção 4) — constantes ajustáveis sem deploy de schema,
# candidatas a virar configuração depois de calibradas com dados reais (Fase 1).
ALPHA = 0.3  # peso de "não arrefecer" (soma do tempo total em trânsito)
BETA = 0.5   # peso de "não estragar" (excesso sobre a tolerância, quadrático de propósito)

ROAD_FACTOR = 1.35        # estrada real vs. linha reta (Haversine subestima sempre)
AVERAGE_SPEED_KMH = 22.0  # velocidade média urbana
MIN_TRAVEL_MINUTES = 2.0  # piso de viagem — duas paragens muito próximas não custam ~0


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Stop:
    """Uma paragem de recolha — um sub-pedido dentro da rota."""
    stop_id: str
    location: Point
    ready_at: datetime        # a partir de quando pode ser recolhido (SubOrderDB.ready_at ou estimativa)
    tolerance_minutes: float  # quanto tempo aguenta em trânsito depois de recolhido (secção 4.4)


@dataclass(frozen=True)
class StopTiming:
    stop_id: str
    arrival: datetime   # quando o estafeta chega à porta
    pickup: datetime    # = max(arrival, ready_at) — quando de facto recolhe
    wait_minutes: float # tempo parado à espera (arrival -> pickup)


@dataclass(frozen=True)
class RouteResult:
    order: Tuple[str, ...]                 # stop_id na ordem de recolha escolhida
    stop_timings: Tuple[StopTiming, ...]   # timings na mesma ordem
    delivery_at: datetime
    cost: float


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def travel_minutes(a: Point, b: Point) -> float:
    """viagem(a → b) da secção 4 — determinístico, sem chamada externa."""
    straight_km = _haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
    road_km = straight_km * ROAD_FACTOR
    minutes = (road_km / AVERAGE_SPEED_KMH) * 60
    return max(minutes, MIN_TRAVEL_MINUTES)


def _evaluate(
    order: Sequence[Stop],
    driver_location: Point,
    customer_location: Point,
    now: datetime,
    travel_fn: TravelFn,
) -> RouteResult:
    t = now
    position = driver_location
    timings: list[StopTiming] = []

    for stop in order:
        arrival = t + timedelta(minutes=travel_fn(position, stop.location))
        pickup = max(arrival, stop.ready_at)
        wait_minutes = max(0.0, (pickup - arrival).total_seconds() / 60)
        timings.append(StopTiming(stop_id=stop.stop_id, arrival=arrival, pickup=pickup, wait_minutes=wait_minutes))
        t = pickup
        position = stop.location

    delivery_at = t + timedelta(minutes=travel_fn(position, customer_location))

    total_transit = 0.0
    total_excess_sq = 0.0
    for stop, timing in zip(order, timings):
        transito_minutos = (delivery_at - timing.pickup).total_seconds() / 60
        excesso = max(0.0, transito_minutos - stop.tolerance_minutes)
        total_transit += transito_minutos
        total_excess_sq += excesso ** 2

    minutos_ate_entrega = (delivery_at - now).total_seconds() / 60
    cost = minutos_ate_entrega + ALPHA * total_transit + BETA * total_excess_sq

    return RouteResult(
        order=tuple(s.stop_id for s in order),
        stop_timings=tuple(timings),
        delivery_at=delivery_at,
        cost=cost,
    )


def sequence_stops(
    stops: Sequence[Stop],
    driver_location: Point,
    customer_location: Point,
    now: Optional[datetime] = None,
    travel_fn: TravelFn = travel_minutes,
) -> RouteResult:
    """
    Enumera todas as permutações das paragens e devolve a de menor custo (secção 4).

    `travel_fn` é o ponto de extensão citado na secção 4 ("trocar por uma Distance Matrix
    real é um ponto de extensão isolado numa função — não uma reescrita"): por omissão usa
    Haversine + fator de estrada (`travel_minutes`), mas testes e uma futura integração de
    routing real podem injetar outra função com a mesma assinatura.

    Em caso de empate exato de custo, fica com a primeira permutação de menor custo
    encontrada na ordem de itertools.permutations sobre `stops` — determinístico para o
    mesmo input, sem significado especial atribuído à ordem de desempate.
    """
    if not stops:
        raise ValueError("sequence_stops precisa de ao menos uma paragem")

    reference_now = now if now is not None else datetime.now(stops[0].ready_at.tzinfo)

    best: Optional[RouteResult] = None
    for perm in permutations(stops):
        result = _evaluate(perm, driver_location, customer_location, reference_now, travel_fn)
        if best is None or result.cost < best.cost:
            best = result
    assert best is not None
    return best
