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
#
# Se a sessão do Gemini falhar a meio (quota da chave, ligação caída), o servidor
# abre outra com a chave seguinte e a conversa continua no mesmo WebSocket: o
# navegador só recebe um "interrupted" para calar o áudio que ficou a meio.
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

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

_INSTRUCOES = f"""És a Maria Portuguesa, a anfitriã de um serviço em Portugal que ajuda famílias a encontrar empregadas domésticas de confiança. A pessoa está a ver-te no ecrã e a falar contigo por voz, como numa conversa entre duas pessoas.

QUEM ÉS
- Uma mulher portuguesa, calorosa, prática e bem-disposta. Falas como uma pessoa real, nunca como um sistema: nada de "como assistente" nem de listas faladas.
- Sempre em português de Portugal: "telemóvel", "passar a ferro", "está a procurar". Nunca uses gerúndio brasileiro.
- Frases curtas e naturais. Deixas a pessoa falar e não a interrompes com perguntas em série.

COMO COMEÇAS
- Na primeira fala apresentas-te em duas ou três frases: dizes que és a Maria Portuguesa, que estás ali para lhe trazer as melhores empregadas domésticas da região de forma prática, rápida e de confiança, e perguntas do que precisa.
- Só te apresentas uma vez.

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


# Quanto da conversa segue para a sessão nova quando se troca de chave.
_MAX_FALAS_NA_RETOMA = 24
_MAX_CARACTERES_POR_FALA = 600


@dataclass
class _Conversa:
    """O que sobrevive à troca de sessão do Gemini dentro do mesmo WebSocket."""
    # Sessão Live atual; None enquanto se abre outra.
    sessao: Optional[object] = None
    # Acorda a conversa quando um envio para o Gemini falha.
    avaria: asyncio.Event = field(default_factory=asyncio.Event)
    # (quem, texto) — as transcrições que o próprio Gemini vai devolvendo.
    falas: list = field(default_factory=list)
    # Resumo da última busca, para a sessão nova saber quem está no ecrã.
    ultima_busca: Optional[dict] = None
    # Pedidos escritos que chegaram sem sessão aberta: seguem na retoma.
    textos_pendentes: list = field(default_factory=list)
    # A sessão atual já entregou alguma coisa ao navegador?
    progrediu: bool = False

    def anotar(self, quem: str, texto: str) -> None:
        # As transcrições chegam aos bocados: os seguidos da mesma pessoa juntam-se.
        if self.falas and self.falas[-1][0] == quem:
            self.falas[-1] = (quem, (self.falas[-1][1] + texto)[-_MAX_CARACTERES_POR_FALA:])
        else:
            self.falas.append((quem, texto[-_MAX_CARACTERES_POR_FALA:]))
        del self.falas[:-_MAX_FALAS_NA_RETOMA]

    def texto_de_retoma(self) -> str:
        linhas = [
            "[Nota do sistema — não a leias nem a comentes] A ligação teve uma falha técnica e foi "
            "retomada agora. Já te apresentaste: não voltes a apresentar-te nem a cumprimentar.",
        ]
        if self.falas:
            linhas.append("Conversa até aqui:")
            linhas += [f"{quem}: {texto.strip()}" for quem, texto in self.falas]
        if self.ultima_busca:
            linhas.append(
                "Resultado da última busca, que continua no ecrã da pessoa: "
                + json.dumps(self.ultima_busca, ensure_ascii=False)
            )
        linhas.append(
            "Continua com naturalidade a partir daqui. Se a última fala da pessoa ficou sem resposta, "
            "responde-lhe agora; se não, diz só numa frase curta que já a ouves outra vez."
        )
        return "\n".join(linhas)


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
            try:
                await websocket.close()
            except Exception:
                pass  # o navegador já tinha fechado a ligação
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
        """Mantém a conversa com o navegador enquanto houver uma chave que funcione.

        Uma chave que falhe — ao abrir ou a meio da conversa — é trocada pela
        seguinte sem fechar o WebSocket. Desiste quando todas falham de seguida,
        sem que nenhuma chegue a entregar nada ao navegador.
        """
        chaves = list(settings.GEMINI_API_KEYS)
        conversa = _Conversa()
        prazo = asyncio.get_running_loop().time() + MAX_SEGUNDOS_SESSAO
        # Um só leitor do navegador para a conversa inteira, seja qual for a chave.
        leitor = asyncio.create_task(cls._navegador_para_gemini(websocket, conversa))
        indice, falhas_seguidas, ultimo_erro, retoma = 0, 0, None, False
        try:
            while falhas_seguidas < len(chaves):
                chave = chaves[indice % len(chaves)]
                indice += 1
                try:
                    gestor = cls._abrir_sessao_live(chave)
                    sessao = await gestor.__aenter__()
                except Exception as e:
                    falhas_seguidas, ultimo_erro = falhas_seguidas + 1, e
                    print(f"⚠️ [MariaPortuguesa] Falha ao abrir o Gemini Live com uma das chaves: {e}")
                    continue
                try:
                    terminou, erro = await cls._ponte(websocket, sessao, fabrica_db, conversa, leitor, prazo, retoma)
                finally:
                    conversa.sessao = None
                    try:
                        await gestor.__aexit__(None, None, None)
                    except Exception:
                        pass  # a sessão já estava morta
                if terminou:
                    return
                falhas_seguidas = 0 if conversa.progrediu else falhas_seguidas + 1
                ultimo_erro, retoma = erro, True
                print(f"⚠️ [MariaPortuguesa] O Gemini Live falhou a meio da conversa; a retomar com outra chave: {erro}")
                # Cala no navegador o áudio que ficou a meio da frase.
                await websocket.send_json({"type": "interrupted"})
            raise RuntimeError(f"Nenhuma chave conseguiu manter o Gemini Live: {ultimo_erro}")
        finally:
            leitor.cancel()
            await asyncio.gather(leitor, return_exceptions=True)

    @classmethod
    async def _ponte(cls, websocket: WebSocket, sessao, fabrica_db: Callable, conversa: _Conversa,
                     leitor: asyncio.Task, prazo: float, retoma: bool) -> tuple[bool, Optional[BaseException]]:
        """Liga o navegador a uma sessão do Gemini. Devolve (True, None) quando a
        conversa acabou e (False, erro) quando foi esta sessão que falhou."""
        conversa.progrediu = False
        conversa.avaria = asyncio.Event()
        try:
            if retoma:
                await sessao.send_realtime_input(text=conversa.texto_de_retoma())
                pendentes, conversa.textos_pendentes = conversa.textos_pendentes, []
                for texto in pendentes:
                    await sessao.send_realtime_input(text=texto)
            else:
                await websocket.send_json({"type": "ready"})
                # A Maria fala primeiro: sem isto a sessão fica muda até a pessoa dizer algo.
                await sessao.send_realtime_input(text="Olá")
        except WebSocketDisconnect:
            raise
        except Exception as e:
            return False, e
        conversa.sessao = sessao

        gemini = asyncio.create_task(cls._gemini_para_navegador(websocket, sessao, fabrica_db, conversa))
        avaria = asyncio.create_task(conversa.avaria.wait())
        try:
            concluidas, _ = await asyncio.wait(
                {leitor, gemini, avaria},
                timeout=max(0.0, prazo - asyncio.get_running_loop().time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            conversa.sessao = None
            for tarefa in (gemini, avaria):
                tarefa.cancel()
            resultados = await asyncio.gather(gemini, avaria, return_exceptions=True)

        if not concluidas:
            await cls._recusar(websocket, "A conversa atingiu o tempo máximo. Pode iniciar outra quando quiser.")
            return True, None
        if leitor in concluidas:
            leitor.result()  # o navegador saiu; propaga a exceção, se houve
            return True, None
        erro_gemini = resultados[0] if gemini in concluidas else None
        if isinstance(erro_gemini, WebSocketDisconnect):
            raise erro_gemini
        if isinstance(erro_gemini, Exception):
            return False, erro_gemini
        if avaria in concluidas:
            return False, RuntimeError("o envio para o Gemini falhou")
        return True, None  # o Gemini fechou a sessão sem erro

    @staticmethod
    async def _navegador_para_gemini(websocket: WebSocket, conversa: _Conversa) -> None:
        while True:
            mensagem = await websocket.receive()
            if mensagem["type"] == "websocket.disconnect":
                return
            sessao = conversa.sessao
            audio = mensagem.get("bytes")
            if audio:
                # Sem sessão (a trocar de chave) o áudio desses instantes perde-se.
                if sessao is not None:
                    try:
                        await sessao.send_realtime_input(
                            audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                        )
                    except Exception:
                        conversa.avaria.set()
                continue
            texto = mensagem.get("text")
            if texto:
                try:
                    dados = json.loads(texto)
                except ValueError:
                    continue
                if isinstance(dados, dict) and dados.get("type") == "text" and str(dados.get("text", "")).strip():
                    pedido = str(dados["text"])[:500]
                    try:
                        if sessao is None:
                            raise RuntimeError("sem sessão aberta")
                        await sessao.send_realtime_input(text=pedido)
                        conversa.anotar("Pessoa", pedido)
                    except Exception:
                        # Um pedido escrito não se perde: segue quando a sessão voltar.
                        conversa.textos_pendentes.append(pedido)
                        conversa.avaria.set()

    @classmethod
    async def _gemini_para_navegador(cls, websocket: WebSocket, sessao, fabrica_db: Callable,
                                     conversa: _Conversa) -> None:
        # receive() termina no fim de cada turno do modelo; a conversa continua.
        while True:
            recebeu_algo = False
            async for resposta in sessao.receive():
                recebeu_algo = True
                if resposta.tool_call:
                    await cls._executar_ferramentas(websocket, sessao, resposta.tool_call, fabrica_db, conversa)

                conteudo = resposta.server_content
                if conteudo is None:
                    continue
                if conteudo.interrupted:
                    await websocket.send_json({"type": "interrupted"})
                if conteudo.model_turn:
                    for parte in conteudo.model_turn.parts or []:
                        if parte.inline_data and parte.inline_data.data:
                            await websocket.send_bytes(parte.inline_data.data)
                            conversa.progrediu = True
                if conteudo.input_transcription and conteudo.input_transcription.text:
                    conversa.anotar("Pessoa", conteudo.input_transcription.text)
                    await websocket.send_json(
                        {"type": "transcript", "role": "user", "text": conteudo.input_transcription.text})
                if conteudo.output_transcription and conteudo.output_transcription.text:
                    conversa.anotar("Maria", conteudo.output_transcription.text)
                    conversa.progrediu = True
                    await websocket.send_json(
                        {"type": "transcript", "role": "assistant", "text": conteudo.output_transcription.text})
                if conteudo.turn_complete:
                    await websocket.send_json({"type": "turn_complete"})
            if not recebeu_algo:
                return  # o Gemini fechou a sessão

    @classmethod
    async def _executar_ferramentas(cls, websocket: WebSocket, sessao, tool_call, fabrica_db: Callable,
                                    conversa: _Conversa) -> None:
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
                conversa.ultima_busca = conteudo
                conversa.progrediu = True
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
