"""
Testes das funções de fidelidade em services/telemetry.py (F0 e F4.3 do PLANO_EXECUCAO_IA.md).

Execução:
    python3 tests/test_telemetry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")

from services import telemetry


# ── texto_promete_acao_carrinho ─────────────────────────────────────────────

def teste_promessa_detecta_frases_de_confirmacao():
    assert telemetry.texto_promete_acao_carrinho("Já adicionei a pizza ao carrinho!") is True
    assert telemetry.texto_promete_acao_carrinho("Vou adicionar 2 pizzas para você.") is True
    print("OK  - detecta frases que prometem ação de carrinho")


def teste_promessa_nao_dispara_em_texto_neutro():
    assert telemetry.texto_promete_acao_carrinho("Quantas pizzas você quer?") is False
    assert telemetry.texto_promete_acao_carrinho("") is False
    print("OK  - não dispara falso positivo em texto neutro")


# ── detectar_divergencia_de_preco (F4.3) ────────────────────────────────────

def teste_divergencia_preco_ausente_quando_texto_nao_menciona_preco():
    pool = [{"price": 12.50}]
    assert telemetry.detectar_divergencia_de_preco("Prontinho! Já adicionei a pizza.", pool) is False
    print("OK  - sem menção a preço no texto, não há divergência")


def teste_divergencia_preco_ausente_quando_preco_bate_com_catalogo():
    pool = [{"price": 12.50}, {"price": 8.90}]
    assert telemetry.detectar_divergencia_de_preco("A pizza custa € 12,50.", pool) is False
    print("OK  - preço mencionado que bate com o catálogo não é divergência")


def teste_divergencia_preco_detectada_quando_valor_nao_existe_no_pool():
    """O caso central do F4.3: a IA foi instruída a não escrever preços, mas se
    escrever um valor que não corresponde a nada no pool, isso precisa ficar visível."""
    pool = [{"price": 12.50}, {"price": 8.90}]
    assert telemetry.detectar_divergencia_de_preco("A pizza sai por apenas € 9,99!", pool) is True
    print("OK  - detecta preço mencionado que não corresponde a nenhum produto do pool")


def teste_divergencia_preco_tolerante_a_arredondamento():
    pool = [{"price": 12.5}]  # 12.5 == 12.50, sem diferença real
    assert telemetry.detectar_divergencia_de_preco("Fica em € 12,50.", pool) is False
    print("OK  - tolera diferença de representação decimal (12.5 vs 12.50)")


if __name__ == "__main__":
    teste_promessa_detecta_frases_de_confirmacao()
    teste_promessa_nao_dispara_em_texto_neutro()
    teste_divergencia_preco_ausente_quando_texto_nao_menciona_preco()
    teste_divergencia_preco_ausente_quando_preco_bate_com_catalogo()
    teste_divergencia_preco_detectada_quando_valor_nao_existe_no_pool()
    teste_divergencia_preco_tolerante_a_arredondamento()
    print("\nTodos os testes de telemetria (fidelidade) passaram.")
