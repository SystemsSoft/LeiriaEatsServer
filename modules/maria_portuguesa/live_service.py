# Arquivo: modules/maria_portuguesa/live_service.py
#
# Assistente de voz do contratante (Gemini Live).
#
#   navegador  ⇄  WebSocket /maria-portuguesa/voz  ⇄  Gemini Live
#
# O servidor fica no meio por dois motivos: a GEMINI_API_KEY nunca chega ao
# navegador, e a ferramenta de busca corre aqui, ao lado da base de dados e do
# modelo de embeddings. O navegador só envia áudio do microfone e recebe áudio,
# transcrições e os perfis encontrados.
#
# Protocolo com o navegador
#   → binário                       PCM 16 bit, 16 kHz, mono (microfone)
#   → {"type": "text", "text"}      pedido escrito, para quem não quer falar
#   ← binário                       PCM 16 bit, 24 kHz, mono (voz da assistente)
#   ← {"type": "ready"}             sessão aberta, pode falar
#   ← {"type": "transcript", "role": "user" | "assistant", "text"}
#   ← {"type": "searching", "consulta"}
#   ← {"type": "results", ...}      mesmo formato de POST /maria-portuguesa/busca
#   ← {"type": "open_profile", "gid"}
#   ← {"type": "interrupted"}       a pessoa falou por cima: parar a reprodução
#   ← {"type": "turn_complete"}
#   ← {"type": "error", "message"}
import asyncio
import json
import os
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from core.config import settings
from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa.search_service import BuscaService

MODELO_LIVE = os.getenv("MP_GEMINI_LIVE_MODEL", "gemini-3.8-live")

# A rota é pública e cada sessão consome quota da API: há um teto de duração por
# sessão e de sessões em simultâneo.
MAX_SEGUNDOS_SESSAO = int(os.getenv("MP_VOZ_MAX_SEGUNDOS", 300))
MAX_SESSOES_SIMULTANEAS = int(os.getenv("MP_VOZ_MAX_SESSOES", 20))

_INSTRUCOES = f"""És a Maria, a assistente de voz da Maria Portuguesa, um serviço em Portugal que ajuda famílias a encontrar empregadas domésticas de confiança.

COMO FALAS
- Sempre em português de Portugal: "telemóvel", "passar a ferro", "está a procurar", tratamento por "você" implícito. Nunca uses gerúndio brasileiro.
- Calorosa e direta, como uma vizinha prestável. Frases curtas: isto é uma conversa falada.
- Começa por cumprimentar e perguntar em que podes ajudar, numa só frase.

O QUE FAZES
- Percebes o que a pessoa precisa: que tarefas, em que zona, em que dias, e quanto quer pagar à hora. Não faças um interrogatório: se já tens a tarefa e a zona, procura logo.
- Para procurar usas SEMPRE a ferramenta procurar_domesticas. Nunca inventes pessoas, nomes, preços ou contactos: só existe quem a ferramenta devolver.
- Os perfis encontrados aparecem no ecrã da pessoa. Não os leias todos: diz quantos encontraste e destaca no máximo dois, com o nome, a zona e o que os torna adequados ao pedido.
- Se a ferramenta indicar filtros relaxados, diz com franqueza o que não foi possível cumprir (por exemplo, o dia ou o preço).
- Se não houver resultados numa localidade pequena, tenta de novo com o concelho ou o distrito e explica que alargaste a zona. Se mesmo assim não houver ninguém, diz que ainda não há perfis nessa zona.
- Quando a pessoa mostrar interesse em alguém em concreto, usa abrir_perfil para lhe mostrar o perfil completo.
- Só falas de procura de ajuda doméstica. A outros assuntos respondes com simpatia que só podes ajudar nisso.

SERVIÇOS QUE EXISTEM (usa estes identificadores no parâmetro servicos)
{chr(10).join(f"- {chave}: {nome}" for chave, nome in catalogo.SERVICOS.items())}
"""

_FERRAMENTAS = [{
    "function_declarations": [
        {
            "name": "procurar_domesticas",
            "description": "Procura empregadas domésticas com perfil publicado. Os resultados aparecem no ecrã do utilizador.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "consulta": {
                        "type": "STRING",
                        "description": "Descrição completa, em linguagem natural, do que a pessoa procura, incluindo pormenores que não cabem nos outros parâmetros (ex.: 'alguém paciente para ajudar a minha mãe de 80 anos e fazer limpeza ligeira').",
                    },
                    "localidade": {
                        "type": "STRING",
                        "description": "Concelho, cidade ou distrito em Portugal (ex.: 'Leiria', 'Marinha Grande', 'Porto').",
                    },
                    "servicos": {
                        "type": "ARRAY",
                        "items": {"type": "STRING", "enum": list(catalogo.SERVICOS)},
                        "description": "Só os serviços indispensáveis: quem não os oferecer todos é excluído.",
                    },
                    "dias": {
                        "type": "ARRAY",
                        "items": {"type": "STRING", "enum": list(catalogo.DIAS)},
                        "description": "Dias da semana pretendidos.",
                    },
                    "preco_max": {
                        "type": "NUMBER",
                        "description": "Valor máximo por hora, em euros.",
                    },
                },
                "required": ["consulta"],
            },
        },
        {
            "name": "abrir_perfil",
            "description": "Abre no ecrã do utilizador o perfil completo de uma pessoa devolvida por procurar_domesticas.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "gid": {"type": "STRING", "description": "Identificador gid do perfil."},
                },
                "required": ["gid"],
            },
        },
    ],
}]


def configuracao_live() -> dict:
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": _INSTRUCOES,
        "tools": _FERRAMENTAS,
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


def _resumo_para_o_modelo(resultado: dict) -> dict:
    """Versão enxuta do resultado para devolver ao Gemini: o suficiente para ele
    falar sobre as pessoas, sem fotos nem contactos (esses ficam no ecrã)."""
    perfis = []
    for p in resultado["resultados"]:
        perfis.append({
            "gid": p["gid"],
            "nome": p["nome"],
            "titulo": p["titulo"],
            "zona": ", ".join(z for z in (p["concelho"], p["distrito"]) if z),
            "servicos": [catalogo.SERVICOS[s] for s in p["servicos"] if s in catalogo.SERVICOS],
            "preco_hora_euros": p["preco_hora"],
            "anos_experiencia": p["anos_experiencia"],
            "dias": [catalogo.DIAS[x] for x in p["dias"] if x in catalogo.DIAS],
            "apresentacao": (p["sobre"] or "")[:240],
        })
    return {
        "total_encontrado": resultado["total"],
        "mostrados_no_ecra": len(perfis),
        "filtros_relaxados": resultado["filtros_relaxados"],
        "perfis": perfis,
    }


class AssistenteVoz:
    _sessoes_ativas = 0

    # Ponto de substituição para testes: devolve o gestor de contexto assíncrono da
    # sessão Live para uma dada chave de API.
    _abrir_sessao_live: Callable = staticmethod(
        lambda chave: genai.Client(api_key=chave).aio.live.connect(model=MODELO_LIVE, config=configuracao_live())
    )

    @classmethod
    async def atender(cls, websocket: WebSocket, fabrica_db: Callable) -> None:
        await websocket.accept()

        if not settings.GEMINI_API_KEYS:
            await cls._recusar(websocket, "A assistente de voz não está configurada neste servidor.")
            return
        if cls._sessoes_ativas >= MAX_SESSOES_SIMULTANEAS:
            await cls._recusar(websocket, "A Maria está a atender muitas pessoas. Tente de novo dentro de instantes.")
            return

        cls._sessoes_ativas += 1
        try:
            await cls._conversar(websocket, fabrica_db)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"❌ [MariaPortuguesa] Sessão de voz terminou com erro: {e}")
            await cls._recusar(websocket, "A ligação à assistente falhou. Pode continuar a pesquisar por escrito.")
        finally:
            cls._sessoes_ativas -= 1

    @staticmethod
    async def _recusar(websocket: WebSocket, mensagem: str) -> None:
        try:
            await websocket.send_json({"type": "error", "message": mensagem})
            await websocket.close()
        except Exception:
            pass  # o navegador já tinha fechado a ligação

    @classmethod
    async def _conversar(cls, websocket: WebSocket, fabrica_db: Callable) -> None:
        # Failover entre chaves, como no GeminiSalesAgent: só passa à seguinte se a
        # ligação nem chegar a abrir.
        ultimo_erro = None
        for chave in settings.GEMINI_API_KEYS:
            try:
                gestor = cls._abrir_sessao_live(chave)
                sessao = await gestor.__aenter__()
            except Exception as e:
                ultimo_erro = e
                print(f"⚠️ [MariaPortuguesa] Falha ao abrir o Gemini Live com uma das chaves: {e}")
                continue
            try:
                await cls._ponte(websocket, sessao, fabrica_db)
            finally:
                await gestor.__aexit__(None, None, None)
            return
        raise RuntimeError(f"Nenhuma chave conseguiu abrir o Gemini Live: {ultimo_erro}")

    @classmethod
    async def _ponte(cls, websocket: WebSocket, sessao, fabrica_db: Callable) -> None:
        await websocket.send_json({"type": "ready"})
        # A Maria fala primeiro: sem isto a sessão fica muda até a pessoa dizer algo.
        await sessao.send_realtime_input(text="Olá")

        tarefas = [
            asyncio.create_task(cls._navegador_para_gemini(websocket, sessao)),
            asyncio.create_task(cls._gemini_para_navegador(websocket, sessao, fabrica_db)),
        ]
        try:
            concluidas, _ = await asyncio.wait(
                tarefas, timeout=MAX_SEGUNDOS_SESSAO, return_when=asyncio.FIRST_COMPLETED,
            )
            if not concluidas:
                await cls._recusar(websocket, "A conversa atingiu o tempo máximo. Pode iniciar outra quando quiser.")
            for tarefa in concluidas:
                tarefa.result()  # propaga a exceção, se houve
        finally:
            for tarefa in tarefas:
                tarefa.cancel()
            await asyncio.gather(*tarefas, return_exceptions=True)

    @staticmethod
    async def _navegador_para_gemini(websocket: WebSocket, sessao) -> None:
        while True:
            mensagem = await websocket.receive()
            if mensagem["type"] == "websocket.disconnect":
                return
            audio = mensagem.get("bytes")
            if audio:
                await sessao.send_realtime_input(
                    audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                )
                continue
            texto = mensagem.get("text")
            if texto:
                try:
                    dados = json.loads(texto)
                except ValueError:
                    continue
                if isinstance(dados, dict) and dados.get("type") == "text" and str(dados.get("text", "")).strip():
                    await sessao.send_realtime_input(text=str(dados["text"])[:500])

    @classmethod
    async def _gemini_para_navegador(cls, websocket: WebSocket, sessao, fabrica_db: Callable) -> None:
        # receive() termina no fim de cada turno do modelo; a conversa continua.
        while True:
            recebeu_algo = False
            async for resposta in sessao.receive():
                recebeu_algo = True
                if resposta.tool_call:
                    await cls._executar_ferramentas(websocket, sessao, resposta.tool_call, fabrica_db)

                conteudo = resposta.server_content
                if conteudo is None:
                    continue
                if conteudo.interrupted:
                    await websocket.send_json({"type": "interrupted"})
                if conteudo.model_turn:
                    for parte in conteudo.model_turn.parts or []:
                        if parte.inline_data and parte.inline_data.data:
                            await websocket.send_bytes(parte.inline_data.data)
                if conteudo.input_transcription and conteudo.input_transcription.text:
                    await websocket.send_json(
                        {"type": "transcript", "role": "user", "text": conteudo.input_transcription.text})
                if conteudo.output_transcription and conteudo.output_transcription.text:
                    await websocket.send_json(
                        {"type": "transcript", "role": "assistant", "text": conteudo.output_transcription.text})
                if conteudo.turn_complete:
                    await websocket.send_json({"type": "turn_complete"})
            if not recebeu_algo:
                return  # o Gemini fechou a sessão

    @classmethod
    async def _executar_ferramentas(cls, websocket: WebSocket, sessao, tool_call, fabrica_db: Callable) -> None:
        respostas = []
        for chamada in tool_call.function_calls or []:
            argumentos = dict(chamada.args or {})
            if chamada.name == "procurar_domesticas":
                await websocket.send_json({"type": "searching", "consulta": argumentos.get("consulta", "")})
                # A codificação E5 é CPU síncrona: fora do event loop, para não
                # prender o áudio das outras sessões.
                resultado = await asyncio.to_thread(cls._procurar, fabrica_db, argumentos)
                await websocket.send_json({"type": "results", **resultado})
                conteudo = _resumo_para_o_modelo(resultado)
            elif chamada.name == "abrir_perfil":
                await websocket.send_json({"type": "open_profile", "gid": str(argumentos.get("gid", ""))})
                conteudo = {"resultado": "perfil aberto no ecrã"}
            else:
                conteudo = {"erro": f"ferramenta desconhecida: {chamada.name}"}
            respostas.append(types.FunctionResponse(id=chamada.id, name=chamada.name, response=conteudo))
        if respostas:
            await sessao.send_tool_response(function_responses=respostas)

    @staticmethod
    def _procurar(fabrica_db: Callable, argumentos: dict) -> dict:
        preco_max = argumentos.get("preco_max")
        db = fabrica_db()
        try:
            return BuscaService.procurar(
                db,
                consulta=str(argumentos.get("consulta", "")),
                localidade=argumentos.get("localidade"),
                servicos=list(argumentos.get("servicos") or []),
                dias=list(argumentos.get("dias") or []),
                preco_max=float(preco_max) if preco_max is not None else None,
            )
        finally:
            db.close()
