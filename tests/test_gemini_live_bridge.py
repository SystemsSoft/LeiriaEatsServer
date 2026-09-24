"""
Testes da ponte de voz em tempo real (services/gemini_live_bridge.py) — sem rede e sem
gastar cota da Gemini: a sessão Live é um dublê que reproduz a semântica REAL do SDK
google-genai, em especial a que causou o bug "a IA não espera o usuário e faz várias
perguntas uma atrás da outra":

    AsyncSession.receive() entrega mensagens até o primeiro `turn_complete` e ENCERRA
    (google/genai/live.py: `if turn_complete: yield result; break`).

A ponte iterava receive() uma vez só; ao fim do primeiro turno da IA (a saudação) ela
achava que a sessão tinha acabado, trocava de chave e abria OUTRA sessão Live com a
mensagem de "reconexão técnica" — a IA falava de novo sozinha, depois de CADA turno,
até esgotar as chaves e derrubar a ligação (medido em teste real: 10 sessões Live em 48s
de conversa, 2 falas espontâneas, carrinho nunca montado).

Execução:
    python3 tests/test_gemini_live_bridge.py
"""
import asyncio
import contextlib
import os
import sys
import time
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")
os.environ.setdefault("USE_REDIS", "false")

from google.genai import types as gtypes
from starlette.websockets import WebSocketDisconnect, WebSocketState

import services.gemini_live_bridge as ponte
from services.ai_service import AIService
from services.gemini_sales_service import GeminiSalesAgent
from services.session_service import SessionManager

# A system instruction só existe depois de GeminiSalesAgent.initialize() (no startup do
# servidor) — aqui basta um texto qualquer.
GeminiSalesAgent._system_instruction_fc = "INSTRUCAO-FAKE"
AIService._product_obj_cache = []


# ───────────────────────── dublês ─────────────────────────

class _SessaoLiveFake:
    """Sessão Live falsa com a MESMA semântica de receive() do SDK real."""

    def __init__(self, resposta_a_mensagem_inicial=None):
        self._fila = asyncio.Queue()
        # Turno que a IA "diz" quando é provocada por send_client_content(turn_complete=True)
        self._resposta_inicial = resposta_a_mensagem_inicial
        self.client_contents = []   # [(texto, turn_complete)]
        self.frames_de_audio = 0
        # Queda do websocket: dispara receive() E um send_realtime_input em andamento na MESMA
        # volta do loop, como acontece numa queda real de conexão.
        self.travar_envio = False
        self._caiu = asyncio.get_running_loop().create_future()
        self._erro_queda = RuntimeError("websocket caiu")

    def injetar(self, mensagens):
        for m in mensagens:
            self._fila.put_nowait(m)

    def falhar(self, erro):
        self._fila.put_nowait(erro)

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.client_contents.append((turns.parts[0].text, turn_complete))
        if turn_complete and self._resposta_inicial:
            self.injetar(self._resposta_inicial)

    def derrubar_websocket(self, erro):
        self._erro_queda = erro
        self._caiu.set_result(None)

    async def send_realtime_input(self, **kwargs):
        if self.travar_envio:          # fica preso no envio até o websocket cair
            await self._caiu
            raise self._erro_queda
        self.frames_de_audio += 1

    async def send_tool_response(self, **kwargs):
        pass

    async def receive(self):
        # ↓ ESTE é o comportamento do SDK que a ponte precisa respeitar ↓
        while True:
            get = asyncio.ensure_future(self._fila.get())
            try:
                await asyncio.wait({get, self._caiu}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not get.done():
                    get.cancel()
            if self._caiu.done():
                raise self._erro_queda
            m = get.result()
            if isinstance(m, BaseException):
                raise m
            yield m
            if m.server_content and m.server_content.turn_complete:
                break


class _LiveFake:
    def __init__(self, sessoes):
        self._sessoes = list(sessoes)
        self.conexoes = 0

    @contextlib.asynccontextmanager
    async def connect(self, *, model, config):
        self.conexoes += 1
        item = self._sessoes.pop(0)
        if isinstance(item, BaseException):
            raise item
        yield item


class _WSFake:
    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self._entrada = asyncio.Queue()
        self.enviados = []

    async def accept(self):
        pass

    async def receive_json(self):
        item = await self._entrada.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    async def send_json(self, evento):
        self.enviados.append(evento)

    async def close(self):
        self.client_state = WebSocketState.DISCONNECTED

    def mandar_audio(self):
        self._entrada.put_nowait({"type": "audio", "data": "AAAA"})

    def desconectar(self):
        self._entrada.put_nowait(None)

    def tipos(self):
        return [e["type"] for e in self.enviados]


# ───────────────────────── helpers de mensagens (tipos reais do SDK) ─────────────────────────

def _fala_ia(*fragmentos, audio=True, fim=True, interrompida=False):
    """Turno da IA: fragmentos de transcrição de saída (como a Gemini entrega, palavra a
    palavra), um pedaço de áudio e, se fim=True, o turn_complete."""
    msgs = [gtypes.LiveServerMessage(server_content=gtypes.LiveServerContent(
        output_transcription=gtypes.Transcription(text=f))) for f in fragmentos]
    if audio:
        msgs.append(gtypes.LiveServerMessage(server_content=gtypes.LiveServerContent(
            model_turn=gtypes.Content(parts=[gtypes.Part(
                inline_data=gtypes.Blob(data=b"\x01\x02", mime_type="audio/pcm;rate=24000"))]))))
    if interrompida:
        msgs.append(gtypes.LiveServerMessage(server_content=gtypes.LiveServerContent(interrupted=True)))
    if fim:
        msgs.append(gtypes.LiveServerMessage(server_content=gtypes.LiveServerContent(turn_complete=True)))
    return msgs


def _fala_usuario(*fragmentos):
    return [gtypes.LiveServerMessage(server_content=gtypes.LiveServerContent(
        input_transcription=gtypes.Transcription(text=f))) for f in fragmentos]


async def _esperar(condicao, timeout=3.0, msg="condição não atendida a tempo"):
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        if condicao():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(msg)


@contextlib.contextmanager
def _ponte_com(sessoes, chaves=("k1", "k2", "k3"), idle_timeout=None):
    """Troca o cliente Gemini por um dublê que entrega `sessoes` na ordem das conexões."""
    live = _LiveFake(sessoes)
    cliente = SimpleNamespace(aio=SimpleNamespace(live=live))
    original = (ponte.genai, ponte.settings, ponte.IDLE_TIMEOUT_SECONDS)
    ponte.genai = SimpleNamespace(Client=lambda **kw: cliente)
    ponte.settings = SimpleNamespace(GEMINI_API_KEYS=list(chaves))
    if idle_timeout is not None:
        ponte.IDLE_TIMEOUT_SECONDS = idle_timeout
    try:
        yield live
    finally:
        ponte.genai, ponte.settings, ponte.IDLE_TIMEOUT_SECONDS = original


async def _iniciar(bridge, ws):
    return asyncio.create_task(bridge.run(ws))


async def _encerrar(ws, tarefa):
    ws.desconectar()
    await asyncio.wait_for(tarefa, timeout=5)


def _nova_ponte(nome="Bruno"):
    return ponte.GeminiLiveBridge(session_id=f"teste-{uuid.uuid4().hex[:8]}", db=None, nome_usuario=nome)


# ───────────────────────── testes ─────────────────────────

async def teste_uma_unica_sessao_live_atravessa_varios_turnos():
    """A CAUSA RAIZ do bug: receive() encerra a cada turn_complete, e a ponte não pode tratar
    isso como fim da sessão. Uma ligação com saudação + 2 respostas usa UMA sessão Live e não
    manda nenhuma "reconexão técnica" que faria a IA falar sozinha."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!", " Em que posso ajudar?"))
    s2 = _SessaoLiveFake(_fala_ia("NÃO deveria falar aqui"))
    ws = _WSFake()
    with _ponte_com([s1, s2]) as live:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") >= 1, msg="a saudação não chegou ao app")
        await asyncio.sleep(0.3)  # tempo de sobra para uma "reconexão" indevida acontecer
        assert live.conexoes == 1, (
            f"abriu {live.conexoes} sessões Live logo após a saudação (esperado: 1) — a ponte trocou "
            "de chave só porque receive() encerrou no turn_complete")
        assert ws.tipos().count("turn_complete") == 1, "a IA falou de novo sem o usuário ter dito nada"

        # o cliente fala e a IA responde — duas vezes, na MESMA sessão
        s1.injetar(_fala_usuario("Queria uma pizza") + _fala_ia("Para quantas pessoas seria?"))
        await _esperar(lambda: ws.tipos().count("turn_complete") == 2, msg="1ª resposta não chegou")
        s1.injetar(_fala_usuario("Somos duas") + _fala_ia("Perfeito, adicionei."))
        await _esperar(lambda: ws.tipos().count("turn_complete") == 3, msg="2ª resposta não chegou")

        assert live.conexoes == 1, f"abriu {live.conexoes} sessões Live numa ligação só (esperado: 1)"
        assert s2.client_contents == [], "a 2ª sessão (reconexão) nunca deveria ter sido usada"
        assert len(s1.client_contents) == 1, "só a mensagem de abertura deve ser enviada, uma vez"
        assert "error" not in ws.tipos()
        await _encerrar(ws, tarefa)
    print("OK  - uma única sessão Live atravessa saudação + várias respostas (receive() encerra a cada turno)")


async def teste_ia_nao_fala_sozinha_depois_da_saudacao():
    """Sem nenhuma fala do usuário, o app recebe exatamente UM turno da IA (a saudação)."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!", " Em que posso ajudar?"))
    s2 = _SessaoLiveFake(_fala_ia("Em que posso ajudar com a sua encomenda?"))
    ws = _WSFake()
    with _ponte_com([s1, s2]) as live:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") >= 1, msg="a saudação não chegou ao app")
        await asyncio.sleep(0.5)  # tempo de sobra para uma "reconexão" indevida acontecer
        assert ws.tipos().count("turn_complete") == 1, "a IA falou de novo sem o usuário ter dito nada"
        assert live.conexoes == 1
        await _encerrar(ws, tarefa)
    print("OK  - depois da saudação a IA espera: nenhum turno espontâneo")


async def teste_fragmentos_de_transcricao_viram_uma_mensagem_por_fala():
    """A Gemini transcreve palavra a palavra. Cada fragmento virava uma mensagem do histórico
    (só cabem 20): as falas do cliente eram empurradas pra fora e o resumo usado numa
    reconexão — e o chat por texto, que compartilha a sessão — ficava ilegível."""
    s1 = _SessaoLiveFake(_fala_ia("Olá", ", Bruno", "!", " Em que", " posso ajudar", "?"))
    ws = _WSFake()
    bridge = _nova_ponte()
    with _ponte_com([s1]):
        tarefa = await _iniciar(bridge, ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
        s1.injetar(_fala_usuario("Queria", " uma", " pizza", ".")
                   + _fala_ia("Para", " quantas", " pessoas", " seria", "?"))
        await _esperar(lambda: ws.tipos().count("turn_complete") == 2)
        historico = bridge.session.history
        assert historico == [
            {"role": "assistant", "content": "Olá, Bruno! Em que posso ajudar?"},
            {"role": "user", "content": "Queria uma pizza."},
            {"role": "assistant", "content": "Para quantas pessoas seria?"},
        ], historico
        await _encerrar(ws, tarefa)
    print("OK  - transcrição em fragmentos vira UMA mensagem de histórico por fala")


async def teste_reconexao_no_meio_da_chamada_espera_o_cliente():
    """Numa falha real (ex.: 1011 cota esgotada) a ponte troca de chave — e a IA NÃO deve
    falar de novo: a mensagem de retomada vai com turn_complete=False (só contexto)."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!", " Em que posso ajudar?"))
    s2 = _SessaoLiveFake(_fala_ia("NÃO deveria falar ao reconectar"))
    ws = _WSFake()
    with _ponte_com([s1, s2]) as live:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
        s1.injetar(_fala_usuario("Queria uma pizza") + _fala_ia("Para quantas pessoas seria?"))
        await _esperar(lambda: ws.tipos().count("turn_complete") == 2)

        s1.falhar(RuntimeError("1011 Resource has been exhausted (e.g. check quota)"))
        await _esperar(lambda: live.conexoes == 2, msg="não trocou de chave após a falha")
        await _esperar(lambda: len(s2.client_contents) == 1)
        await asyncio.sleep(0.3)

        texto, turn_complete = s2.client_contents[0]
        assert turn_complete is False, "reconexão não pode provocar fala da IA (turn_complete deve ser False)"
        assert "CONTEXTO DA CHAMADA EM CURSO" in texto
        assert "Queria uma pizza" in texto and "Para quantas pessoas seria?" in texto, texto
        assert ws.tipos().count("turn_complete") == 2, "a IA falou sozinha ao reconectar"

        # e quando o cliente fala, a sessão nova responde normalmente
        s2.injetar(_fala_usuario("Somos duas") + _fala_ia("Perfeito."))
        await _esperar(lambda: ws.tipos().count("turn_complete") == 3)
        await _encerrar(ws, tarefa)
    print("OK  - reconexão no meio da chamada retoma em silêncio (turn_complete=False) e com o histórico")


async def teste_reconexao_com_pedido_do_cliente_sem_resposta_responde():
    """Se a sessão cai DEPOIS de o cliente falar e ANTES de a IA responder, a retomada tem
    que provocar a resposta (turn_complete=True) — senão o cliente ficaria falando sozinho."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
    s2 = _SessaoLiveFake(_fala_ia("Claro, uma pizza de calabresa."))
    ws = _WSFake()
    with _ponte_com([s1, s2]) as live:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
        s1.injetar(_fala_usuario("Quero uma pizza de calabresa"))
        await _esperar(lambda: any(e.get("role") == "user" for e in ws.enviados))
        s1.falhar(RuntimeError("1011 Resource has been exhausted"))

        await _esperar(lambda: len(s2.client_contents) == 1)
        texto, turn_complete = s2.client_contents[0]
        assert turn_complete is True
        assert "ainda não foi respondida" in texto and "Quero uma pizza de calabresa" in texto, texto
        await _esperar(lambda: ws.tipos().count("turn_complete") == 2, msg="a IA não respondeu ao pedido pendente")
        await _encerrar(ws, tarefa)
    print("OK  - reconexão com pedido do cliente ainda sem resposta provoca a resposta")


async def teste_inicio_de_chamada_com_historico_previo_cumprimenta():
    """A sessão é compartilhada com o chat por texto e com ligações anteriores. Histórico
    existente + a IA ainda não falou NESTA ligação = início de chamada, não reconexão: a IA
    cumprimenta (turn_complete=True) em vez de ficar muda."""
    bridge = _nova_ponte(nome="Bruno")
    bridge.session.add_message("user", "quero uma pizza")
    bridge.session.add_message("assistant", "Temos calabresa e napolitana.")
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno! Quer continuar o pedido da pizza?"))
    ws = _WSFake()
    with _ponte_com([s1]):
        tarefa = await _iniciar(bridge, ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1, msg="a IA ficou muda no início da chamada")
        texto, turn_complete = s1.client_contents[0]
        assert turn_complete is True
        assert "NOVA chamada" in texto and "Bruno" in texto and "quero uma pizza" in texto, texto
        await _encerrar(ws, tarefa)
    print("OK  - início de chamada com histórico prévio (chat por texto) cumprimenta, não fica muda")


async def teste_abertura_sem_historico_cumprimenta_pelo_nome():
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
    ws = _WSFake()
    with _ponte_com([s1]):
        tarefa = await _iniciar(_nova_ponte(nome="Bruno"), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
        texto, turn_complete = s1.client_contents[0]
        assert turn_complete is True
        assert "início da chamada" in texto and "Bruno" in texto and "AGUARDE" in texto, texto
        await _encerrar(ws, tarefa)
    print("OK  - abertura sem histórico cumprimenta pelo nome e manda aguardar")


async def teste_prompt_de_voz_manda_uma_pergunta_por_vez():
    """As regras de turno de chamada de voz vão no system_instruction da sessão."""
    capturado = {}

    class _LiveCaptura(_LiveFake):
        @contextlib.asynccontextmanager
        async def connect(self, *, model, config):
            capturado["config"] = config
            async with super().connect(model=model, config=config) as s:
                yield s

    s1 = _SessaoLiveFake(_fala_ia("Olá!"))
    ws = _WSFake()
    live = _LiveCaptura([s1])
    cliente = SimpleNamespace(aio=SimpleNamespace(live=live))
    original = (ponte.genai, ponte.settings)
    ponte.genai = SimpleNamespace(Client=lambda **kw: cliente)
    ponte.settings = SimpleNamespace(GEMINI_API_KEYS=["k1"])
    try:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: "config" in capturado)
        texto = capturado["config"].system_instruction.parts[0].text
        assert texto.startswith("INSTRUCAO-FAKE")
        assert "REGRAS DA CHAMADA DE VOZ" in texto
        assert "NO MÁXIMO UMA pergunta por vez" in texto and "AGUARDE o cliente" in texto
        await _encerrar(ws, tarefa)
    finally:
        ponte.genai, ponte.settings = original
    print("OK  - system_instruction da ligação inclui as regras de turno (1 pergunta por vez, aguardar)")


async def teste_interrupcao_do_usuario_e_repassada_ao_app():
    """Quando o usuário fala por cima da IA a Gemini manda `interrupted`; o app precisa saber
    pra limpar a fila de áudio (a doc da Live API manda o cliente fazer isso)."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
    ws = _WSFake()
    with _ponte_com([s1]):
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
        s1.injetar(_fala_ia("Temos a pizza de", audio=True, fim=False, interrompida=True)
                   + _fala_ia(audio=False))
        await _esperar(lambda: "interrupted" in ws.tipos(), msg="interrupted não chegou ao app")
        await _encerrar(ws, tarefa)
    print("OK  - interrupção do usuário é repassada ao app (evento 'interrupted')")


async def teste_watchdog_so_encerra_sem_nenhum_frame_do_app():
    """Contrato de que o app depende: enquanto chegam frames de áudio (mesmo silêncio, com o
    microfone mudo) a ligação segue; só some tudo por IDLE_TIMEOUT_SECONDS ela é encerrada.
    Por isso o app manda silêncio em vez de descartar o áudio quando está mudo."""
    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
    ws = _WSFake()
    with _ponte_com([s1], idle_timeout=1.5) as live:
        tarefa = await _iniciar(_nova_ponte(), ws)
        await _esperar(lambda: ws.tipos().count("turn_complete") == 1)

        # 3s mandando "silêncio" a cada 0.2s (o dobro do timeout): a ligação NÃO pode cair
        for _ in range(15):
            ws.mandar_audio()
            await asyncio.sleep(0.2)
            assert not tarefa.done(), "a ligação caiu mesmo com frames chegando"
        assert s1.frames_de_audio >= 10

        # agora o app some de vez (nenhum frame): dentro de ~timeout+1s a ponte encerra
        await asyncio.wait_for(tarefa, timeout=4)
        assert live.conexoes == 1, "não deveria reconectar quando o cliente abandonou a ligação"
    print("OK  - watchdog: frames (mesmo de silêncio) mantêm a ligação; sem nenhum frame ela é encerrada")


async def teste_queda_dupla_do_websocket_nao_deixa_excecao_sem_recolher():
    """Quando o websocket da Gemini cai, receber E enviar falham juntas. Só a primeira exceção era
    tratada; a outra ficava sem ninguém ler e o asyncio despejava "Task exception was never
    retrieved" no journal (visto em produção). Todas as tarefas da ponte têm que ter o resultado
    recolhido quando a sessão acaba.

    Confere a propriedade em si (Task._log_traceback é o que faz o asyncio reclamar no GC), não o
    ruído: a mensagem só sai quando o GC decide destruir a tarefa, o que é imprevisível num teste.
    """
    criadas = []

    class _AsyncioEspiao:
        def __getattr__(self, nome):
            return getattr(asyncio, nome)

        def create_task(self, coro, **kw):
            t = asyncio.create_task(coro, **kw)
            criadas.append(t)
            return t

    s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
    s2 = _SessaoLiveFake(_fala_ia("Continuando."))
    ws = _WSFake()
    original = ponte.asyncio
    ponte.asyncio = _AsyncioEspiao()
    try:
        with _ponte_com([s1, s2]) as live:
            tarefa = await _iniciar(_nova_ponte(), ws)
            await _esperar(lambda: ws.tipos().count("turn_complete") >= 1)
            s1.travar_envio = True
            ws.mandar_audio()                   # app_para_gemini fica preso dentro do envio...
            await asyncio.sleep(0.05)
            # ...e cai junto com o receive(), na mesma volta do loop
            s1.derrubar_websocket(RuntimeError("1011 Resource has been exhausted"))
            await _esperar(lambda: live.conexoes == 2, msg="não trocou de chave após a queda")
            await _encerrar(ws, tarefa)
    finally:
        ponte.asyncio = original
    # ATENÇÃO à ordem: t.exception() recolhe a exceção e zera _log_traceback — olhar a flag ANTES.
    sem_recolher = [t.get_name() for t in criadas if t.done() and not t.cancelled() and t._log_traceback]
    com_erro = [t for t in criadas if t.done() and not t.cancelled() and t.exception() is not None]
    assert len(com_erro) >= 2, "o teste não reproduziu a queda dupla (as duas tarefas deveriam ter falhado)"
    assert not sem_recolher, f"tarefas com exceção que ninguém recolheu (o asyncio vai reclamar no GC): {sem_recolher}"
    print("OK  - queda dupla do websocket (receber+enviar) não deixa exceção sem recolher")


def _catalogo_de_teste():
    """Instala um catálogo pequeno no AIService e devolve o valor anterior para restaurar."""
    anterior = (AIService._product_obj_cache, AIService._restaurant_name_by_product_id,
                AIService._restaurant_plan_by_product_id)
    itens = [(1, "Pizza de Calabresa", 15.0), (2, "Pizza Napolitana", 20.0), (3, "Burrito Vegetariano", 10.0),
             (4, "Shake Crocante", 5.0)]
    AIService._product_obj_cache = [SimpleNamespace(
        id=i, gid=f"G{i}", name=n, price=p, description="", image_url=None, restaurant_gid="R1", category="x",
        is_available=True, is_surprise_box=False, serves_people=1) for i, n, p in itens]
    AIService._restaurant_name_by_product_id = {i: "Rest" for i, _, _ in itens}
    AIService._restaurant_plan_by_product_id = {}
    return anterior


def _restaurar_catalogo(anterior):
    (AIService._product_obj_cache, AIService._restaurant_name_by_product_id,
     AIService._restaurant_plan_by_product_id) = anterior


def _chamada_sugerir_produtos(*gids):
    return gtypes.LiveServerMessage(tool_call=gtypes.LiveServerToolCall(function_calls=[
        gtypes.FunctionCall(id="fc1", name="sugerir_produtos", args={"gids": list(gids)})]))


def _nomes_sugeridos(ws):
    return [[p["name"] for p in e["products"]] for e in ws.enviados if e["type"] == "products_suggested"]


async def teste_cards_sao_os_produtos_citados_na_fala_como_no_chat_de_texto():
    """Mesma regra do chat por texto (HybridAIService._filter_mentioned_products): só vira card o
    produto cujo NOME aparece no que a IA falou. Citar 2 → 2 cards; não citar nada → nenhum."""
    anterior = _catalogo_de_teste()
    try:
        s1 = _SessaoLiveFake(_fala_ia("Olá, Bruno!"))
        ws = _WSFake()
        with _ponte_com([s1]):
            tarefa = await _iniciar(_nova_ponte(), ws)
            await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
            assert _nomes_sugeridos(ws) == [], "saudação sem produtos não pode gerar cards"

            s1.injetar(_fala_ia("Temos a Pizza de Calabresa", " e a Pizza Napolitana.", " Qual prefere?"))
            await _esperar(lambda: ws.tipos().count("turn_complete") == 2)
            ultimo = _nomes_sugeridos(ws)[-1]
            assert set(ultimo) == {"Pizza de Calabresa", "Pizza Napolitana"}, ultimo
            await _encerrar(ws, tarefa)
    finally:
        _restaurar_catalogo(anterior)
    print("OK  - cards = produtos citados na fala (mesma regra do chat de texto)")


async def teste_cards_aparecem_quando_a_ia_cita_e_nao_repetem():
    anterior = _catalogo_de_teste()
    try:
        s1 = _SessaoLiveFake(_fala_ia("Olá!"))
        ws = _WSFake()
        with _ponte_com([s1]):
            tarefa = await _iniciar(_nova_ponte(), ws)
            await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
            # fala em fragmentos: o card do Shake surge ao ser citado, e fragmentos seguintes sem
            # produto novo NÃO reenviam a mesma lista
            s1.injetar(_fala_ia("Claro,", " o Shake", " Crocante", " é uma ótima escolha,", " muito refrescante."))
            await _esperar(lambda: ws.tipos().count("turn_complete") == 2)
            assert _nomes_sugeridos(ws) == [["Shake Crocante"]], _nomes_sugeridos(ws)
            await _encerrar(ws, tarefa)
    finally:
        _restaurar_catalogo(anterior)
    print("OK  - o card aparece quando a IA cita o produto e a lista não é reenviada sem mudança")


async def teste_ferramenta_sugerir_produtos_nao_gera_cards():
    """`sugerir_produtos` é respondida à Gemini mas não decide mais os cards: se a IA a chama com GIDs
    que NÃO cita na fala, nenhum card aparece (antes apareciam, sem limite)."""
    anterior = _catalogo_de_teste()
    try:
        s1 = _SessaoLiveFake(_fala_ia("Olá!"))
        ws = _WSFake()
        with _ponte_com([s1]):
            tarefa = await _iniciar(_nova_ponte(), ws)
            await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
            s1.injetar([_chamada_sugerir_produtos("G1", "G2", "G3", "G4")] + _fala_ia("Diga-me o que prefere."))
            await _esperar(lambda: ws.tipos().count("turn_complete") == 2)
            assert _nomes_sugeridos(ws) == [], f"a ferramenta não deveria gerar cards: {_nomes_sugeridos(ws)}"
            await _encerrar(ws, tarefa)
    finally:
        _restaurar_catalogo(anterior)
    print("OK  - a ferramenta sugerir_produtos não decide mais os cards")


async def teste_cards_do_turno_anterior_nao_vazam_para_o_turno_seguinte():
    anterior = _catalogo_de_teste()
    try:
        s1 = _SessaoLiveFake(_fala_ia("Olá!"))
        ws = _WSFake()
        with _ponte_com([s1]):
            tarefa = await _iniciar(_nova_ponte(), ws)
            await _esperar(lambda: ws.tipos().count("turn_complete") == 1)
            s1.injetar(_fala_ia("Temos o Burrito Vegetariano."))
            await _esperar(lambda: ws.tipos().count("turn_complete") == 2)
            s1.injetar(_fala_ia("E também o Shake Crocante."))   # 2º turno cita só o shake
            await _esperar(lambda: ws.tipos().count("turn_complete") == 3)
            assert _nomes_sugeridos(ws) == [["Burrito Vegetariano"], ["Shake Crocante"]], _nomes_sugeridos(ws)
            await _encerrar(ws, tarefa)
    finally:
        _restaurar_catalogo(anterior)
    print("OK  - cada turno mostra só os produtos citados nele")


if __name__ == "__main__":
    async def _todos():
        await teste_uma_unica_sessao_live_atravessa_varios_turnos()
        await teste_ia_nao_fala_sozinha_depois_da_saudacao()
        await teste_fragmentos_de_transcricao_viram_uma_mensagem_por_fala()
        await teste_reconexao_no_meio_da_chamada_espera_o_cliente()
        await teste_reconexao_com_pedido_do_cliente_sem_resposta_responde()
        await teste_inicio_de_chamada_com_historico_previo_cumprimenta()
        await teste_abertura_sem_historico_cumprimenta_pelo_nome()
        await teste_prompt_de_voz_manda_uma_pergunta_por_vez()
        await teste_interrupcao_do_usuario_e_repassada_ao_app()
        await teste_watchdog_so_encerra_sem_nenhum_frame_do_app()
        await teste_queda_dupla_do_websocket_nao_deixa_excecao_sem_recolher()
        await teste_cards_sao_os_produtos_citados_na_fala_como_no_chat_de_texto()
        await teste_cards_aparecem_quando_a_ia_cita_e_nao_repetem()
        await teste_ferramenta_sugerir_produtos_nao_gera_cards()
        await teste_cards_do_turno_anterior_nao_vazam_para_o_turno_seguinte()

    asyncio.run(_todos())
    print("\nTodos os testes da ponte de voz passaram (sessão Live simulada — sem rede).")
