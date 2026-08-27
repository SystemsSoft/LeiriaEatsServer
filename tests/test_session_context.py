"""
Testes de session.context (F4.1 do PLANO_EXECUCAO_IA.md).

Antes, intent_type/user_needs eram calculados e session.context era lido pelo prompt do
Gemini, mas nunca escrito em nenhum ponto do código — a seção "CONTEXTO EXTRA" do prompt
nunca aparecia de fato. Aqui validamos a extração heurística (pessoas/categoria_atual/
aguardando) que substitui, por ora, a extração via structured output do Gemini prevista
no plano original — trocada por não ser possível validar contra a API viva nesta sessão
(créditos esgotados) e por não fazer sentido dobrar chamadas por turno durante o incidente.

Execução:
    python3 tests/test_session_context.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.hybrid_ai_service import HybridAIService
from services.session_service import UserSession


# ── _extrair_pessoas ─────────────────────────────────────────────────────────

def teste_extrai_pessoas_numero_digitado():
    assert HybridAIService._extrair_pessoas("é para 4 pessoas") == 4
    assert HybridAIService._extrair_pessoas("somos 3") == 3
    print("OK  - extrai número de pessoas quando a frase menciona 'pessoas'/'somos'")


def teste_extrai_pessoas_numero_por_extenso():
    assert HybridAIService._extrair_pessoas("para duas pessoas, por favor") == 2
    assert HybridAIService._extrair_pessoas("seremos quatro") == 4
    print("OK  - extrai número de pessoas por extenso (duas, quatro)")


def teste_nao_confunde_quantidade_de_produto_com_pessoas():
    """'quero 2 pizzas' NÃO deve virar 'pedido para 2 pessoas' — é exatamente o tipo de
    falso positivo que uma extração ingênua por regex de dígito cometeria."""
    assert HybridAIService._extrair_pessoas("quero 2 pizzas grandes") is None
    assert HybridAIService._extrair_pessoas("me traz 3 refrigerantes") is None
    print("OK  - não confunde quantidade de produto com número de pessoas")


def teste_extrai_pessoas_retorna_none_sem_sinal():
    assert HybridAIService._extrair_pessoas("quero uma pizza margherita") is None
    print("OK  - retorna None quando não há menção a pessoas")


# ── _extrair_categoria_dominante ─────────────────────────────────────────────

def teste_categoria_dominante_quando_pool_e_uniforme():
    pool = [{"category": "Pizza"}, {"category": "Pizza"}, {"category": "Pizza"}]
    assert HybridAIService._extrair_categoria_dominante(pool) == "Pizza"
    print("OK  - detecta categoria dominante quando todo o pool é da mesma categoria")


def teste_categoria_dominante_none_quando_pool_variado():
    pool = [{"category": "Pizza"}, {"category": "Sobremesa"}]
    assert HybridAIService._extrair_categoria_dominante(pool) is None
    print("OK  - não define categoria quando o pool tem categorias variadas")


def teste_categoria_dominante_none_com_pool_vazio():
    assert HybridAIService._extrair_categoria_dominante([]) is None
    print("OK  - não quebra com pool vazio")


# ── _extrair_aguardando ──────────────────────────────────────────────────────

def teste_aguardando_none_quando_resposta_nao_e_pergunta():
    intent_info = {"type": "product_search", "details": {}}
    assert HybridAIService._extrair_aguardando("Prontinho, pizza adicionada!", intent_info) is None
    print("OK  - aguardando fica None quando a resposta não termina em pergunta")


def teste_aguardando_quantidade_quando_pergunta_sobre_quantidade():
    intent_info = {"type": "specific_question", "details": {"asks_quantity": True}}
    resultado = HybridAIService._extrair_aguardando("Quantas pizzas você quer?", intent_info)
    assert resultado == "quantidade"
    print("OK  - detecta 'aguardando quantidade' quando a pergunta é sobre quantidade")


# ── _atualizar_contexto_sessao (integração dos três extratores) ────────────

def teste_atualizar_contexto_nao_apaga_valor_ja_conhecido():
    """Se 'pessoas' já foi definido numa mensagem anterior, uma mensagem posterior sem
    menção a pessoas NÃO deve apagar o valor — é exatamente o bug que o prompt original
    tentava evitar ('não pergunte de novo se já disse antes')."""
    session = UserSession(session_id="s1")
    session.context["pessoas"] = 4

    intent_info = {"type": "product_search", "details": {}}
    HybridAIService._atualizar_contexto_sessao(
        session, user_message="quero uma pizza", ai_response_limpa="Adicionei a pizza!",
        intent_info=intent_info, found_products=[{"category": "Pizza"}],
    )

    assert session.context["pessoas"] == 4, "não deveria ter apagado o valor já conhecido"
    assert session.context["categoria_atual"] == "Pizza"
    assert session.context["aguardando"] is None
    print("OK  - _atualizar_contexto_sessao preserva 'pessoas' já conhecido quando a "
          "mensagem atual não repete a informação")


def teste_atualizar_contexto_completo():
    session = UserSession(session_id="s2")
    intent_info = {"type": "specific_question", "details": {"asks_quantity": True}}
    HybridAIService._atualizar_contexto_sessao(
        session, user_message="somos 3 pessoas", ai_response_limpa="Perfeito! Quantas pizzas?",
        intent_info=intent_info, found_products=[{"category": "Pizza"}, {"category": "Pizza"}],
    )
    assert session.context["pessoas"] == 3
    assert session.context["categoria_atual"] == "Pizza"
    assert session.context["aguardando"] == "quantidade"
    print("OK  - _atualizar_contexto_sessao preenche os três campos num turno completo")


if __name__ == "__main__":
    teste_extrai_pessoas_numero_digitado()
    teste_extrai_pessoas_numero_por_extenso()
    teste_nao_confunde_quantidade_de_produto_com_pessoas()
    teste_extrai_pessoas_retorna_none_sem_sinal()
    teste_categoria_dominante_quando_pool_e_uniforme()
    teste_categoria_dominante_none_quando_pool_variado()
    teste_categoria_dominante_none_com_pool_vazio()
    teste_aguardando_none_quando_resposta_nao_e_pergunta()
    teste_aguardando_quantidade_quando_pergunta_sobre_quantidade()
    teste_atualizar_contexto_nao_apaga_valor_ja_conhecido()
    teste_atualizar_contexto_completo()
    print("\nTodos os testes de session.context (F4.1) passaram.")
