"""
Ponte WebSocket para conversa de voz em tempo real com a Gemini Live API.

Replica a arquitetura da GeminiLiveBridge.kt do AthennaServer (assistente "Megan"):
app <-> este servidor <-> Gemini Live, com failover entre chaves e reaproveitando a
MESMA validação/mutação de carrinho já usada pelo chat por texto
(HybridAIService._executar_ferramenta) — um pedido montado por voz passa pelas
mesmas regras de limite de restaurantes e exclusividade de Caixa Surpresa.

Achados validados nesta sessão (com scripts isolados, antes de escrever este
arquivo) — não repetir esses testes sem necessidade:
- A chave PAGA do Koma estava SEM acesso à Live API (erro 1008 "Your project has been
  denied access", testado de 3 formas) — era bloqueio do projeto no Google, não do código:
  em 24/09/2026 o acesso foi liberado e ela passou a funcionar (Live, texto e TTS). Por isso
  ela é a última da fila de failover: as gratuitas primeiro, a paga como rede de segurança.
  As 4 chaves GRATUITAS têm acesso confirmado. `settings.GEMINI_API_KEYS` já lista
  as gratuitas antes da paga, então o failover abaixo já tenta na ordem certa.
- O modelo "gemini-2.5-flash-native-audio-latest" (o mesmo que a Megan usa) só
  "pensa" em chamar a ferramenta (campo `thought`) mas não emite a function-call de
  verdade neste projeto — carrinho nunca mudava de fato.
  "gemini-3.1-flash-live-preview" chama a ferramenta corretamente (validado de
  ponta a ponta com o system_instruction real de produção) — por isso é o modelo
  usado aqui, não o mesmo da Megan.
- input_audio_transcription/output_audio_transcription funcionam e dão o texto
  transcrito dos dois lados — usados para popular o histórico e o chat do app.
- A API Live não tem "turnos" com prompt reconstruído como o chat por texto (que
  refaz busca E5 a cada mensagem) — o catálogo inteiro é embutido uma vez no
  system_instruction do `setup`. Catálogo do Koma é pequeno (~23 produtos), cabe
  tranquilo; se crescer muito, revisitar com uma ferramenta de busca dedicada.

NÃO reintroduzir o modelo/; confirmar contra a API real antes de mudar model/voz —
nomes e disponibilidade de modelos Live mudam com frequência.
"""
import asyncio
import base64
import time
from typing import Dict, List, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
from google import genai
from google.genai import types as gtypes
from sqlalchemy.orm import Session

from core.config import settings
from services.gemini_sales_service import GeminiSalesAgent
from services.hybrid_ai_service import HybridAIService
from services.session_service import SessionManager, UserSession
from services.ai_service import AIService

LIVE_MODEL = "models/gemini-3.1-flash-live-preview"
LIVE_VOICE = "Aoede"

# A system_instruction do modo function-calling (GeminiSalesAgent._system_instruction_fc)
# foi escrita para o chat por TEXTO: uma resposta por mensagem, "máximo 100 palavras".
# Numa chamada de voz isso não basta — nada ali diz que, depois de perguntar, a IA deve
# PARAR e esperar o cliente falar. Estas regras são anexadas a ela só na ligação de voz.
INSTRUCAO_CHAMADA_DE_VOZ = """

REGRAS DA CHAMADA DE VOZ (prevalecem sobre as regras acima em caso de conflito):
- Isto é uma chamada por VOZ, em tempo real, com o cliente a ouvir. Fale como ao telefone: frases
  curtas, no máximo 2 por vez (o limite de 100 palavras acima não se aplica).
- Faça NO MÁXIMO UMA pergunta por vez. Depois de perguntar, PARE de falar e AGUARDE o cliente
  responder: nunca junte duas perguntas na mesma fala, nunca responda à sua própria pergunta e
  nunca volte a falar sem que o cliente tenha dito alguma coisa.
- Ao oferecer opções, cite até 6 produtos só pelo nome, numa frase curta ("Temos A, B e C."), sem
  descrever cada um — os produtos citados aparecem como cartões na tela.
- Se o cliente ficar em silêncio, fique em silêncio também: não repita a pergunta, não pergunte se
  ele ainda está na linha nem puxe conversa.
- Cumprimente só uma vez, na primeira fala da chamada, e apenas com "Olá" (e o nome do cliente, se
  souber). NÃO se apresente: nunca diga que é consultor de vendas, assistente, IA ou qualquer título —
  nem nesta fala nem depois."""

# Sem NENHUM frame de nenhum lado por esse tempo — sessão morta de vez, encerra tudo.
# (Não existe mais um timeout de "sem resposta da Gemini": um usuário em silêncio
# normal — pensando, ainda não respondeu — também fica sem receber nada da Gemini
# por vários segundos, e isso é esperado, não indica chave travada. Um watchdog
# baseado só nesse tempo confundia as duas coisas e ficava reconectando sozinho a
# cada estouro, reenviando a saudação inicial e gerando perguntas repetidas do
# nada — bug relatado em produção. A troca de chave por falha real continua
# acontecendo normalmente via exceção, capturada em run().)
IDLE_TIMEOUT_SECONDS = 45.0
# Quantas voltas completas pela lista de chaves antes de desistir e avisar o app.
MAX_VOLTAS_PELAS_CHAVES = 2


def _catalogo_para_prompt(found_products: List[Dict]) -> str:
    """Mesmo espírito da seção 'PRODUTOS DISPONÍVEIS' de
    GeminiSalesAgent._build_prompt, mas com o catálogo inteiro de uma vez só — ver
    nota no topo do arquivo sobre a Live API não ter prompt reconstruído por turno.
    """
    if not found_products:
        return "\n\n⚠️ NENHUM PRODUTO DISPONÍVEL NO MOMENTO. Informe o cliente educadamente."
    linhas = ["\n\n📦 PRODUTOS DISPONÍVEIS (use o CÓDIGO GID para adicionar ao carrinho):"]
    for p in found_products:
        linha = f"• [CÓDIGO: {p['gid']}] {p['name']} - € {p['price']:.2f}"
        extras = []
        if p.get("category"):
            extras.append(f"categoria: {p['category']}")
        if p.get("restaurant_name"):
            extras.append(f"restaurante: {p['restaurant_name']}")
        if p.get("serves_people") and p["serves_people"] > 1:
            extras.append(f"serve {p['serves_people']}p")
        if p.get("is_surprise_box"):
            extras.append("🎁 caixa surpresa")
        if extras:
            linha += f" ({', '.join(extras)})"
        linhas.append(linha)
    return "\n".join(linhas)


class GeminiLiveBridge:
    """Uma instância por sessão de voz — guarda estado da conversa (pool de
    produtos do catálogo). O carrinho/histórico de verdade fica no UserSession
    (SessionManager), compartilhado com o chat por texto pelo mesmo session_id.
    """

    def __init__(self, session_id: str, db: Session, nome_usuario: Optional[str] = None):
        self.session_id = session_id
        self.db = db
        self.session: UserSession = SessionManager.get_or_create(session_id)
        self.found_products: List[Dict] = []
        self._client_ws: Optional[WebSocket] = None
        # Nome do perfil cadastrado no app (vem do query param ?nome= da conexão
        # WebSocket) — usado só na abertura da chamada, pra IA já cumprimentar o
        # cliente pelo nome em vez de um "Olá!" genérico. Ausente/vazio = sem nome
        # cadastrado ainda (ex.: onboarding), cai no genérico.
        self.nome_usuario = (nome_usuario or "").strip()
        # Fala em andamento de cada lado, ainda não gravada no histórico — ver _acumular_fala.
        self._fala_em_curso: Dict[str, str] = {"user": "", "assistant": ""}
        # A IA já falou nesta ligação? Distingue INÍCIO de chamada (cumprimenta) de
        # RECONEXÃO no meio dela (retoma em silêncio) — ver _attempt_session.
        self._ia_ja_falou = False
        # Texto que a IA já falou NO TURNO ATUAL e os produtos que ele já citou (ids, na ordem) — base
        # dos cards da tela de ligação; ver _enviar_sugestoes_citadas.
        self._texto_ia_turno = ""
        self._ids_citados_enviados: List[int] = []

    def _acumular_fala(self, papel: str, texto: str):
        """A Gemini entrega a transcrição em FRAGMENTOS (palavra a palavra na fala da IA).
        Gravar cada fragmento como uma mensagem enchia as 20 vagas de UserSession.history
        com pedaços da última frase: as falas do cliente eram empurradas pra fora e o
        resumo usado numa reconexão (e o chat por texto, que compartilha esta sessão)
        ficava ilegível — a IA chegava a perguntar de novo o que o cliente já tinha dito.
        Aqui os fragmentos se juntam por quem fala, e a mensagem só entra no histórico
        quando a fala termina (troca de quem fala, turn_complete, interrupção ou fim).
        """
        self._fechar_fala("assistant" if papel == "user" else "user")
        self._fala_em_curso[papel] += texto

    def _fechar_fala(self, papel: Optional[str] = None):
        for p in ([papel] if papel else ["user", "assistant"]):
            texto = self._fala_em_curso[p].strip()
            if texto:
                self.session.add_message(p, texto)
            self._fala_em_curso[p] = ""

    async def _enviar_sugestoes_citadas(self):
        """Cards da tela de ligação = produtos cujo NOME aparece no que a IA falou no turno — a MESMA
        regra do chat por texto (HybridAIService._filter_mentioned_products sobre a resposta), aplicada
        à transcrição da fala. Antes os cards vinham da ferramenta `sugerir_produtos`, em que o modelo
        escolhia livremente os GIDs (sem limite e sem relação com o que dizia). Roda a cada fragmento
        de fala, então o card aparece assim que a IA cita o produto; só reenvia quando o conjunto muda,
        e nunca envia lista vazia (os cards anteriores ficam até a IA citar outros)."""
        citados = HybridAIService._filter_mentioned_products(self._texto_ia_turno, self.found_products)
        ids = [p["id"] for p in citados]
        if not ids or ids == self._ids_citados_enviados:
            return
        self._ids_citados_enviados = ids
        await self._enviar_ao_app({
            "type": "products_suggested",
            "products": self._produtos_sugeridos_para_app([p["gid"] for p in citados]),
        })

    def _reiniciar_turno_da_ia(self):
        self._texto_ia_turno = ""
        self._ids_citados_enviados = []

    def _montar_pool_produtos(self) -> List[Dict]:
        """Todo o catálogo (AIService._product_obj_cache) no mesmo formato de dict
        que HybridAIService usa em found_products — reaproveita os mapas auxiliares
        de nome/plano de restaurante já carregados pelo AIService.
        """
        produtos = []
        for p in AIService._product_obj_cache:
            gid = getattr(p, "gid", "") or ""
            if not gid:
                # Sem GID a IA não tem como referenciar o produto na chamada da
                # ferramenta — mesma regra do fluxo por texto (F1.4).
                continue
            produtos.append({
                "id": p.id,
                "gid": gid,
                "name": p.name,
                "price": float(p.price),
                "description": getattr(p, "description", "") or "",
                "image_url": getattr(p, "image_url", None),
                "restaurant_gid": getattr(p, "restaurant_gid", "") or "",
                "restaurant_name": AIService._restaurant_name_by_product_id.get(p.id, ""),
                "restaurant_plan": AIService._restaurant_plan_by_product_id.get(p.id, ""),
                "category": getattr(p, "category", ""),
                "is_available": getattr(p, "is_available", True),
                "is_surprise_box": getattr(p, "is_surprise_box", False),
                "serves_people": getattr(p, "serves_people", 1) or 1,
            })
        return produtos

    def _carrinho_para_app(self) -> List[Dict]:
        """Reformata session.cart (CartItem, chave por product_id inteiro) para o
        mesmo formato JSON do modelo `Product` do app (chave por `gid` string) —
        cruza com self.found_products pra achar o gid/descrição/imagem de cada item.
        Sem isso, o app não conseguiria casar os itens do carrinho de voz com o
        resto da UI (cartões, checkout), que só conhece produtos pelo GID.
        """
        pool_por_id = {p["id"]: p for p in self.found_products}
        itens = []
        for item in self.session.cart:
            info = pool_por_id.get(item.product_id, {})
            itens.append({
                "gid": info.get("gid", ""),
                "name": item.name,
                "description": info.get("description", ""),
                "price": item.price,
                "image_url": info.get("image_url"),
                "restaurant_gid": item.restaurant_gid,
                "restaurant_name": item.restaurant_name,
                "restaurant_plan": info.get("restaurant_plan"),
                "category": item.category,
                "quantity": item.quantity,
                "is_surprise_box": item.is_surprise_box,
            })
        return itens

    def _produtos_sugeridos_para_app(self, gids: List[str]) -> List[Dict]:
        """Mesmo formato de _carrinho_para_app, mas para os GIDs dos produtos que a IA CITOU na fala
        (ver _enviar_sugestoes_citadas) — produtos que ela está sugerindo, não
        necessariamente no carrinho ainda. Sem quantidade de carrinho (sempre 1),
        já que aqui é só "isto pode te interessar".
        """
        pool_por_gid = {p["gid"]: p for p in self.found_products}
        itens = []
        for gid in gids:
            p = pool_por_gid.get(gid)
            if not p:
                continue
            itens.append({
                "gid": p["gid"],
                "name": p["name"],
                "description": p.get("description", ""),
                "price": p["price"],
                "image_url": p.get("image_url"),
                "restaurant_gid": p.get("restaurant_gid", ""),
                "restaurant_name": p.get("restaurant_name", ""),
                "restaurant_plan": p.get("restaurant_plan"),
                "category": p.get("category", ""),
                "quantity": 1,
                "is_surprise_box": p.get("is_surprise_box", False),
            })
        return itens

    async def _enviar_ao_app(self, evento: Dict):
        if self._client_ws is not None and self._client_ws.client_state == WebSocketState.CONNECTED:
            await self._client_ws.send_json(evento)

    async def _processar_tool_call(self, tool_call, estado_turno: Dict) -> List[gtypes.FunctionResponse]:
        """Reaproveita HybridAIService._executar_ferramenta (a mesma validação de
        GID/limite de restaurantes/exclusividade de Caixa Surpresa do chat por
        texto) — só aqui a ação é de fato aplicada ao carrinho da sessão real.
        """
        respostas = []
        for fc in tool_call.function_calls:
            resultado = HybridAIService._executar_ferramenta(
                fc.name, dict(fc.args or {}), self.session, self.found_products, estado_turno
            )
            respostas.append(gtypes.FunctionResponse(id=fc.id, name=fc.name, response=resultado))
        SessionManager.save(self.session)
        return respostas

    async def _attempt_session(self, client: genai.Client, key_label: str) -> str:
        """Abre UMA sessão Live com uma chave e faz o relay bidirecional até algo
        encerrar a ligação. Retorna 'client_disconnected' ou 'switch_key'.
        """
        config = gtypes.LiveConnectConfig(
            response_modalities=["AUDIO"],
            # Temperatura baixa (não o 0.7 do chat por texto): aqui o risco maior não
            # é soar repetitivo, é escolher o GID errado entre produtos parecidos
            # (ex.: confundir "Pizza Napolitana" com "Pizza de Calabresa") — visto
            # acontecer em teste manual com o valor default. Precisão > variedade.
            temperature=0.2,
            input_audio_transcription={},
            output_audio_transcription={},
            speech_config=gtypes.SpeechConfig(
                voice_config=gtypes.VoiceConfig(
                    prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(voice_name=LIVE_VOICE)
                ),
                # Sotaque de Portugal, não do Brasil — mesmo language_code aplicado
                # no TTS avulso (synthesize_speech, gemini_sales_service.py).
                language_code="pt-PT",
            ),
            system_instruction=gtypes.Content(parts=[gtypes.Part(
                text=(GeminiSalesAgent._system_instruction_fc + INSTRUCAO_CHAMADA_DE_VOZ
                      + _catalogo_para_prompt(self.found_products))
            )]),
            tools=GeminiSalesAgent._TOOLS,
            # ATENÇÃO: a doc da Live API diz que proactive_audio NÃO é suportado por
            # gemini-3.1-flash-live-preview (só pelos modelos native-audio) — a API aceita o
            # campo em v1alpha (ver run()), mas não há prova de que faça algo aqui. Ele foi
            # posto quando se acreditava ser a causa da IA "falar sozinha"; a causa real era
            # outra: receive() do SDK encerra a cada turn_complete e a ponte trocava de
            # chave (abrindo outra sessão com a mensagem de "reconexão") a cada turno — ver
            # mensagens_da_gemini() e tests/test_gemini_live_bridge.py. Mantido só porque
            # ainda não foi testado sem ele numa conversa longa; quando a cota da Live API
            # permitir, testar sem (e em v1beta) e remover se nada mudar.
            proactivity=gtypes.ProactivityConfig(proactive_audio=True),
            realtime_input_config=gtypes.RealtimeInputConfig(
                automatic_activity_detection=gtypes.AutomaticActivityDetection(
                    disabled=False,
                    # Silêncio precisa durar mais pra "fechar" a fala do usuário —
                    # evita o modelo interpretar uma pausa curta pra respirar/pensar
                    # como "o usuário terminou, hora de responder de novo".
                    silence_duration_ms=800,
                ),
                turn_coverage=gtypes.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
            ),
        )

        last_any_at = time.time()
        estado_turno: Dict = {}

        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as live_session:
            # Numa troca de chave no meio da conversa (reconexão), retoma com o
            # histórico em vez de reabrir do zero — mesmo espírito do
            # buildPrimingMessage da Megan (GeminiLiveBridge.kt).
            self._fechar_fala()  # o resumo abaixo precisa incluir o que já foi dito até a queda
            historico = self.session.get_history_text()
            # Só é RECONEXÃO no meio da chamada se a IA já chegou a falar nesta ligação.
            # Histórico sem isso (o cliente conversou por texto antes, ou numa ligação
            # anterior — a sessão é a mesma) é INÍCIO de chamada: a IA cumprimenta.
            reconexao = self._ia_ja_falou and bool(historico)
            # turn_complete=True FAZ a IA falar na hora; False só entrega o contexto e ela
            # espera o cliente (send_client_content, doc da Live API). Só a abertura da
            # chamada e um pedido do cliente ainda sem resposta devem provocar fala.
            fala_agora = True
            if reconexao:
                if self.session.history[-1]["role"] == "user":
                    abertura = (
                        "IMPORTANTE — isto é uma reconexão técnica no meio da chamada, não uma "
                        "chamada nova: não se apresente nem cumprimente de novo. A última coisa "
                        "dita foi do Cliente e ainda não foi respondida: responda-lhe agora, sem "
                        f"repetir perguntas que você já fez. Conversa até agora:\n{historico}"
                    )
                else:
                    fala_agora = False
                    abertura = (
                        "[CONTEXTO DA CHAMADA EM CURSO — reconexão técnica, não é uma chamada nova. "
                        "NÃO responda a esta nota, não se apresente nem cumprimente de novo: fique "
                        "em silêncio e aguarde o cliente falar; quando ele falar, continue a partir "
                        f"daqui sem repetir perguntas que você já fez. Conversa até agora:\n{historico}]"
                    )
            elif historico:
                abertura = (
                    "IMPORTANTE — isto é o início de uma NOVA chamada de voz, mas este cliente já "
                    "conversou connosco antes (resumo abaixo). Cumprimente o cliente apenas com "
                    + (f"\"Olá, {self.nome_usuario}!\"" if self.nome_usuario else "\"Olá!\"")
                    + " — NÃO se apresente nem diga que é consultor de vendas ou assistente. Depois pergunte no que pode ajudar (ou se quer "
                    "continuar o pedido) e AGUARDE a resposta dele — não continue falando sozinho "
                    "nem faça mais perguntas antes disso. Não repita perguntas que o cliente já "
                    f"respondeu. Conversa anterior:\n{historico}"
                )
            elif self.nome_usuario:
                abertura = (
                    "IMPORTANTE — isto é o início da chamada. Cumprimente o cliente apenas com "
                    f"\"Olá, {self.nome_usuario}!\" já nesta primeira fala — NÃO se apresente e "
                    "NÃO diga que é consultor de vendas nem assistente. Depois pergunte no que pode ajudar e AGUARDE "
                    "a resposta dele — não continue falando sozinho nem faça mais "
                    "perguntas antes disso."
                )
            else:
                abertura = (
                    "IMPORTANTE — isto é o início da chamada. Cumprimente o cliente apenas com "
                    "\"Olá!\" — NÃO se apresente e NÃO diga que é consultor de vendas nem assistente. "
                    "Depois pergunte no que pode ajudar e AGUARDE a resposta dele."
                )
            await live_session.send_client_content(
                turns=gtypes.Content(role="user", parts=[gtypes.Part(text=abertura)]),
                turn_complete=fala_agora,
            )

            async def mensagens_da_gemini():
                # O SDK google-genai documenta que live_session.receive() entrega "um
                # turno completo do modelo" e ENCERRA sozinho no primeiro turn_complete
                # (ver AsyncSession.receive: `if turn_complete: yield; break`). Iterar
                # uma vez só (como era antes) fazia esta tarefa terminar logo depois da
                # PRIMEIRA fala da IA — a saudação —, e _attempt_session tratava o fim da
                # tarefa como "trocar de chave": derrubava a sessão e abria outra, com a
                # mensagem de "reconexão técnica" logo abaixo. A IA então falava de novo
                # sozinha, e de novo depois de CADA turno, até esgotar as chaves e
                # derrubar a ligação; a fala do usuário em andamento se perdia a cada
                # troca e o carrinho nunca era montado (bug relatado: "a IA faz várias
                # perguntas uma atrás da outra sem esperar o usuário"). Uma única sessão
                # Live deve durar a ligação inteira: reabre o receive() a cada turno, e
                # só uma FALHA REAL (exceção do websocket) troca de chave.
                while True:
                    async for message in live_session.receive():
                        yield message

            async def gemini_para_app():
                nonlocal last_any_at
                async for message in mensagens_da_gemini():
                    last_any_at = time.time()

                    if message.tool_call:
                        respostas = await self._processar_tool_call(message.tool_call, estado_turno)
                        await live_session.send_tool_response(function_responses=respostas)
                        if estado_turno.get("carrinho_mudou"):
                            await self._enviar_ao_app({
                                "type": "cart_updated",
                                "cart": self._carrinho_para_app(),
                            })
                            estado_turno["carrinho_mudou"] = False
                        if estado_turno.get("show_cart"):
                            await self._enviar_ao_app({"type": "show_cart"})
                            estado_turno["show_cart"] = False
                        # `sugerir_produtos` continua sendo respondida à Gemini (função chamada precisa de
                        # resposta), mas NÃO gera mais cards: eles vêm dos produtos citados na fala
                        # (_enviar_sugestoes_citadas), como no chat por texto.
                        estado_turno["gids_sugeridos"] = None

                    sc = message.server_content
                    if sc:
                        if sc.input_transcription and sc.input_transcription.text:
                            texto = sc.input_transcription.text
                            self._acumular_fala("user", texto)
                            await self._enviar_ao_app({"type": "transcript", "role": "user", "text": texto})
                        if sc.output_transcription and sc.output_transcription.text:
                            texto = sc.output_transcription.text
                            self._acumular_fala("assistant", texto)
                            await self._enviar_ao_app({"type": "transcript", "role": "ai", "text": texto})
                            self._texto_ia_turno += texto
                            await self._enviar_sugestoes_citadas()
                        if sc.model_turn and sc.model_turn.parts:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    self._ia_ja_falou = True
                                    await self._enviar_ao_app({
                                        "type": "audio",
                                        "mime_type": part.inline_data.mime_type,
                                        "data": base64.b64encode(part.inline_data.data).decode("ascii"),
                                    })
                        if getattr(sc, "interrupted", False):
                            # O usuário falou por cima da IA: a Gemini já parou de gerar,
                            # mas o app ainda tem na fila o áudio que recebeu mais rápido
                            # que o tempo real — avisa pra ele limpar a fila (app antigo
                            # ignora este tipo de evento desconhecido, sem quebrar).
                            self._fechar_fala()
                            self._reiniciar_turno_da_ia()
                            await self._enviar_ao_app({"type": "interrupted"})
                        if sc.turn_complete:
                            self._fechar_fala()
                            self._reiniciar_turno_da_ia()
                            SessionManager.save(self.session)
                            await self._enviar_ao_app({"type": "turn_complete"})

            async def app_para_gemini():
                nonlocal last_any_at
                assert self._client_ws is not None
                while True:
                    frame = await self._client_ws.receive_json()
                    last_any_at = time.time()
                    tipo = frame.get("type")
                    if tipo == "audio":
                        pcm = base64.b64decode(frame["data"])
                        await live_session.send_realtime_input(
                            audio=gtypes.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                        )
                    elif tipo == "text":
                        # Fallback de depuração/teste — o app de produção manda áudio,
                        # mas um cliente de texto (nosso script de validação da Fase 1,
                        # por exemplo) pode mandar isto diretamente.
                        await live_session.send_client_content(
                            turns=gtypes.Content(role="user", parts=[gtypes.Part(text=frame["text"])]),
                            turn_complete=True,
                        )

            async def watchdog():
                # Só verifica abandono de verdade (nem áudio do usuário chegando,
                # nem nada da Gemini) — ver nota junto de IDLE_TIMEOUT_SECONDS
                # sobre por que não existe mais um timeout de "sem resposta".
                while True:
                    await asyncio.sleep(1)
                    if time.time() - last_any_at > IDLE_TIMEOUT_SECONDS:
                        print(f"⏱️ [Voice Live] {key_label} sem nenhuma atividade há {IDLE_TIMEOUT_SECONDS}s — encerrando sessão.")
                        return "client_disconnected"

            tarefas = [
                asyncio.create_task(gemini_para_app(), name="gemini_para_app"),
                asyncio.create_task(app_para_gemini(), name="app_para_gemini"),
                asyncio.create_task(watchdog(), name="watchdog"),
            ]
            try:
                done, pending = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    exc = t.exception()
                    if exc:
                        if isinstance(exc, WebSocketDisconnect):
                            return "client_disconnected"
                        raise exc
                    resultado = t.result()
                    if resultado in ("client_disconnected", "switch_key"):
                        return resultado
                return "switch_key"
            finally:
                for t in tarefas:
                    if not t.done():
                        t.cancel()
                # Recolhe o resultado/exceção de TODAS as tarefas. Quando o websocket da Gemini
                # cai, gemini_para_app e app_para_gemini costumam falhar juntas: só a primeira é
                # tratada acima, e a outra ficava com a exceção sem ninguém ler — o asyncio
                # despejava "Task exception was never retrieved" no journal (visto em produção).
                await asyncio.gather(*tarefas, return_exceptions=True)

    async def run(self, client_ws: WebSocket):
        """Ponto de entrada — chamado pela rota WebSocket em chat_routes.py."""
        await client_ws.accept()
        self._client_ws = client_ws
        self.found_products = self._montar_pool_produtos()

        chaves = list(settings.GEMINI_API_KEYS)
        # Mesma ordem do chat de texto: 1ª e 2ª gratuitas, PAGA em terceiro, demais gratuitas no fim
        # (a paga é sempre a ÚLTIMA de settings.GEMINI_API_KEYS). O índice original vai no log.
        ordem = GeminiSalesAgent.ordem_base_das_chaves(len(chaves))
        ultimo_erro = None
        try:
            for _volta in range(MAX_VOLTAS_PELAS_CHAVES):
                for idx in ordem:
                    key = chaves[idx]
                    label = f"chave[{idx}]" + (" (paga)" if idx == len(chaves) - 1 else " (gratuita)")
                    # v1alpha é exigido pela Live API para reconhecer campos de
                    # "setup" ainda em preview, como ProactivityConfig — sem isto a
                    # API rejeita a sessão com "Unknown name 'proactivity' at
                    # 'setup': Cannot find field." em TODAS as chaves (não é
                    # problema de cota/autenticação, é versão da API).
                    client = genai.Client(api_key=key, http_options=gtypes.HttpOptions(api_version="v1alpha"))
                    try:
                        resultado = await self._attempt_session(client, label)
                    except WebSocketDisconnect:
                        return
                    except Exception as e:
                        ultimo_erro = f"{type(e).__name__}: {e}"
                        print(f"⚠️ [Voice Live] {label} falhou: {ultimo_erro}")
                        continue
                    if resultado == "client_disconnected":
                        return
                    # 'switch_key': continua o loop pra próxima chave da lista.
                await asyncio.sleep(0.5)

            await self._enviar_ao_app({
                "type": "error",
                "message": "Não foi possível manter a ligação de voz agora. Tente novamente.",
                "debug": ultimo_erro,
            })
        finally:
            self._fechar_fala()  # grava a fala que estava em andamento quando a ligação acabou
            SessionManager.save(self.session)
            if self._client_ws is not None and self._client_ws.client_state == WebSocketState.CONNECTED:
                await self._client_ws.close()
