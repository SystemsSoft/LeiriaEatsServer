"""
Testes do suporte a vários idiomas: a regra das instruções do chat e a escolha da língua
da voz (services/idioma.py). Sem rede.

Execução:
    python3 tests/test_idioma.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from services.idioma import codigo_voz_da_chamada, codigo_voz_para_texto, e_portugues


def teste_detecao_do_portugues():
    for texto in ["Quero uma pizza de frango, por favor", "Não tem sobremesa?", "Olá! Como posso ajudar?",
                  "Encontrei 3 opções: Pizza Margherita para o jantar"]:
        assert e_portugues(texto), texto
    for texto in ["I would like a chicken pizza, please", "Do you have something spicy?",
                  "Hello! What would you like to order today?", "Quiero una pizza, gracias",
                  "Je voudrais une pizza avec du fromage", "Haben Sie eine vegetarische Pizza?", ""]:
        assert not e_portugues(texto), texto
    print("OK  - voz: pt-PT só com sinais claros de português; qualquer outra língua fica com a deteção do Gemini")


def teste_voz_do_tts_segue_o_texto():
    assert codigo_voz_para_texto("A sua encomenda está a caminho!") == "pt-PT"
    assert codigo_voz_para_texto("Your order is on the way!") is None  # o Gemini deteta a língua
    print("OK  - TTS: pt-PT para português, sem language_code (deteção automática) nos outros")


def teste_voz_da_chamada_segue_o_telemovel():
    assert codigo_voz_da_chamada(None) == "pt-PT"
    assert codigo_voz_da_chamada("") == "pt-PT"
    assert codigo_voz_da_chamada("pt-BR") == "pt-PT"  # sotaque de Portugal sempre
    assert codigo_voz_da_chamada("en-US") == "en-US"
    assert codigo_voz_da_chamada("es") == "es"
    assert codigo_voz_da_chamada("en-US; DROP TABLE") == "pt-PT"  # valor estranho é ignorado
    print("OK  - chamada de voz: idioma do telemóvel, português e valores inválidos ficam em pt-PT")


def teste_instrucoes_do_chat_seguem_o_idioma_do_cliente():
    from services.gemini_sales_service import GeminiSalesAgent
    GeminiSalesAgent._system_instruction = None
    try:
        GeminiSalesAgent.initialize()
    except Exception:
        pass  # sem chave real o cliente falha, mas as instruções já foram montadas
    for instrucao in (GeminiSalesAgent._system_instruction, GeminiSalesAgent._system_instruction_fc):
        assert instrucao and "idioma da ÚLTIMA mensagem do cliente" in instrucao
        assert "Comunique SEMPRE em Português" not in instrucao
    print("OK  - as duas instruções do chat mandam responder no idioma do cliente (PT-PT em português)")


def teste_prompt_pede_a_resposta_no_idioma_da_mensagem():
    """Caso real de 06/11/2026: 'do you have hambúrguer?' recebeu resposta em português. O prompt
    não nomeia nenhuma língua — quem a identifica é o Gemini, para qualquer idioma."""
    from services.gemini_sales_service import GeminiSalesAgent
    for mensagem in ("do you have hambúrguer?", "Haben Sie Pizza?", "tem hambúrguer?"):
        prompt = GeminiSalesAgent._build_prompt(mensagem, products=[], context={})
        fim = prompt.split("CLIENTE DISSE AGORA")[-1]
        assert f'"{mensagem}"' in fim
        assert "Identifique o idioma em que o cliente escreveu a mensagem acima e responda NESSE idioma" in fim
        assert "inglês" not in fim and "alemão" not in fim
    print("OK  - junto da mensagem o prompt pede a resposta no idioma dela, sem impor nenhuma língua")


def teste_outro_idioma_usa_o_flash_ate_ao_fim_do_pedido():
    from services.gemini_sales_service import GeminiSalesAgent as G
    from services.hybrid_ai_service import HybridAIService as H
    from services.session_service import UserSession

    assert "[[IDIOMA:xx]]" in G._build_prompt("tem pizza?", products=[], context={})

    s = UserSession("teste-idioma")
    contexto = lambda: {"session_context": s.context}
    assert G._escolher_modelo(contexto()) == G.MODELO_CONVERSA == "gemini-flash-lite-latest"

    H._registar_idioma(s, "Temos sim! [[IDIOMA:pt]]")
    assert s.context["idioma"] == "pt" and G._escolher_modelo(contexto()) == "gemini-flash-lite-latest"

    H._registar_idioma(s, "Yes, we have the Napolitana! [[IDIOMA:en]]")
    assert G._escolher_modelo(contexto()) == G.MODELO_OUTRO_IDIOMA == "gemini-flash-latest"

    # "and the quatro queijos?" identificado como português não volta ao flash-lite
    H._registar_idioma(s, "A Quatro Queijos é... [[IDIOMA:pt]]")
    assert s.context["idioma"] == "en" and G._escolher_modelo(contexto()) == "gemini-flash-latest"

    s.reset_session()  # fim do pedido
    assert G._escolher_modelo(contexto()) == "gemini-flash-lite-latest"

    assert H._PADRAO_IDIOMA.sub("", "Hello! [[IDIOMA:en]]").strip() == "Hello!"
    print("OK  - português no flash-lite; outro idioma passa ao flash até a sessão ser limpa no fim do pedido")


def teste_saudacao_da_chamada_no_idioma_do_telemovel():
    from services.gemini_live_bridge import GeminiLiveBridge
    em_ingles = GeminiLiveBridge.__new__(GeminiLiveBridge)
    em_ingles.codigo_voz = "en-US"
    assert "en-US" in em_ingles._instrucao_idioma()
    em_portugues = GeminiLiveBridge.__new__(GeminiLiveBridge)
    em_portugues.codigo_voz = "pt-PT"
    assert em_portugues._instrucao_idioma() == ""
    print("OK  - chamada com o telemóvel em inglês acrescenta a instrução de idioma; em português não")


if __name__ == "__main__":
    teste_detecao_do_portugues()
    teste_voz_do_tts_segue_o_texto()
    teste_voz_da_chamada_segue_o_telemovel()
    teste_instrucoes_do_chat_seguem_o_idioma_do_cliente()
    teste_prompt_pede_a_resposta_no_idioma_da_mensagem()
    teste_outro_idioma_usa_o_flash_ate_ao_fim_do_pedido()
    teste_saudacao_da_chamada_no_idioma_do_telemovel()
    print("\nTodos os testes de idioma passaram.")
