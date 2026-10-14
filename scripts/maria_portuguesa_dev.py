"""
Servidor de desenvolvimento do módulo Maria Portuguesa.

Sobe SÓ as rotas /maria-portuguesa/*, com SQLite em memória e perfis de
demonstração — não toca na base de dados de produção, no Stripe nem nos workers
do Leiria Eats. Serve para trabalhar na landing page.

Execução (a partir da raiz do servidor):
    python scripts/maria_portuguesa_dev.py --landing D:/MobileDevops/maria_portugessa

    --landing PASTA   serve também a landing page em http://localhost:8090/
    --banco-real      usa o banco configurado em modules/maria_portuguesa/database.py
                      (MySQL, ou o que estiver em MP_DATABASE_URL) em vez do SQLite em
                      memória: cria banco e tabelas se faltarem e não semeia perfis
    --voz-simulada    conversa por voz sem GEMINI_API_KEY: uma Maria de ensaio que
                      responde com um zumbido em vez de fala e faz a busca com o que
                      lhe escreverem. Serve para trabalhar o ecrã, não a conversa
    --sem-e5          arranque instantâneo com um codificador simplificado
                      (a busca deixa de ser semântica; só compara palavras)
    --porta N         porta (por omissão 8090)

A voz precisa de GEMINI_API_KEY no .env; sem ela a pesquisa escrita funciona na mesma.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# O servidor escreve emojis nos logs; a consola do Windows (cp1252) rebenta com eles.
for _saida in (sys.stdout, sys.stderr):
    _saida.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from modules import maria_portuguesa
from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa.database import Base, get_db
from modules.maria_portuguesa.live_service import AssistenteVoz
from modules.maria_portuguesa.models import MpDomesticaDB
from modules.maria_portuguesa import pagamentos as pagamentos_mp
from modules.maria_portuguesa import routes as rotas_mp
from modules.maria_portuguesa.routes import get_fabrica_db
from modules.maria_portuguesa.search_service import BuscaService
from modules.maria_portuguesa.security import gerar_hash_senha

# Conta de demonstração para entrar no editor de perfil. Só existe neste servidor
# de desenvolvimento (a base é recriada em memória a cada arranque).
EMAIL_DEMO = "demo@mariaportuguesa.test"
SENHA_DEMO = "demo-maria-2026"

# Pessoas fictícias, apenas para ter o que pesquisar em desenvolvimento.
PERFIS_DEMO = [
    dict(nome="Rosa (demo)", titulo="Limpeza e engomadoria com 15 anos de casa de família",
         distrito="Leiria", concelho="Leiria", servicos=["limpeza", "engomar", "roupa", "vidros"],
         anos_experiencia=15, preco_hora=9.5, dias=["seg", "ter", "qua", "qui", "sex"], periodos=["manha"],
         regimes=["semanal", "quinzenal"], idiomas=["Português"], tem_transporte=True, passa_recibo=True,
         tem_referencias=True,
         sobre="Trabalho há quinze anos em casas de família na zona de Leiria. Sou muito cuidadosa com a roupa: "
               "passo a ferro camisas, lençóis e toalhas de mesa. Deixo sempre a cozinha e as casas de banho a brilhar."),
    dict(nome="Fátima (demo)", titulo="Apoio a idosos e companhia durante o dia",
         distrito="Leiria", concelho="Marinha Grande", servicos=["idosos", "cozinhar", "compras", "limpeza"],
         anos_experiencia=8, preco_hora=10.0, dias=["seg", "ter", "qua", "qui", "sex"], periodos=["manha", "tarde"],
         regimes=["semanal"], idiomas=["Português", "Francês"], tem_referencias=True, passa_recibo=True,
         sobre="Fui auxiliar num lar durante oito anos. Tenho muita paciência com pessoas idosas: ajudo na higiene, "
               "preparo as refeições, controlo a medicação e faço companhia. Também trato da limpeza ligeira da casa."),
    dict(nome="Olena (demo)", titulo="Limpeza profunda e pós-obra",
         distrito="Leiria", concelho="Pombal", servicos=["limpeza_profunda", "pos_obra", "vidros", "limpeza"],
         anos_experiencia=6, preco_hora=12.0, dias=["qui", "sex", "sab"], periodos=["manha", "tarde"],
         regimes=["pontual"], idiomas=["Português", "Ucraniano", "Russo"], tem_transporte=True,
         sobre="Especializada em limpezas de fundo: fim de obra, mudanças de casa, estores, vidros e fornos. "
               "Levo os meus próprios produtos e máquinas."),
    dict(nome="Carla (demo)", titulo="Ama e ajuda com a casa ao fim do dia",
         distrito="Lisboa", concelho="Sintra", servicos=["criancas", "cozinhar", "organizacao"],
         anos_experiencia=10, preco_hora=11.0, dias=["seg", "ter", "qua", "qui", "sex"], periodos=["tarde", "noite"],
         regimes=["semanal"], idiomas=["Português", "Inglês"], aceita_animais=True, tem_referencias=True,
         sobre="Vou buscar as crianças à escola, dou o lanche, ajudo nos trabalhos de casa e deixo o jantar feito. "
               "Tenho curso de primeiros socorros pediátricos e adoro animais."),
    dict(nome="Dulce (demo)", titulo="Limpeza de alojamento local e check-out",
         distrito="Porto", concelho="Vila Nova de Gaia", servicos=["alojamento_local", "limpeza", "roupa"],
         anos_experiencia=5, preco_hora=11.5, dias=["sex", "sab", "dom"], periodos=["manha", "tarde"],
         regimes=["pontual", "semanal"], idiomas=["Português", "Inglês", "Espanhol"], tem_transporte=True,
         passa_recibo=True,
         sobre="Trato de apartamentos de alojamento local entre hóspedes: limpeza, mudança de roupa de cama, "
               "reposição de consumíveis e fotografia do estado final para o proprietário."),
    dict(nome="Ivone (demo)", titulo="Empregada interna, cozinha tradicional portuguesa",
         distrito="Lisboa", concelho="Cascais", servicos=["limpeza", "cozinhar", "engomar", "roupa", "compras"],
         anos_experiencia=22, preco_hora=None, dias=["seg", "ter", "qua", "qui", "sex", "sab"],
         periodos=["manha", "tarde", "noite"], regimes=["interna"], idiomas=["Português", "Crioulo cabo-verdiano"],
         aceita_animais=True, tem_referencias=True, passa_recibo=True,
         sobre="Mais de vinte anos como empregada interna. Cozinho comida tradicional portuguesa e cabo-verdiana, "
               "trato de toda a roupa e da organização da casa. Procuro uma família para ficar muitos anos."),
    dict(nome="Sandra (demo)", titulo="Limpeza semanal e cuidar de animais",
         distrito="Leiria", concelho="Leiria", servicos=["limpeza", "animais", "jardim", "compras"],
         anos_experiencia=4, preco_hora=8.5, dias=["ter", "qui", "sab"], periodos=["tarde"],
         regimes=["semanal", "quinzenal", "pontual"], idiomas=["Português"], aceita_animais=True,
         sobre="Faço a limpeza da casa e, se precisar, passeio o cão, dou comida aos gatos e rego as plantas "
               "quando a família está de férias."),
]


def codificador_simplificado(textos: list[str]) -> np.ndarray:
    vetores = np.zeros((len(textos), 512), dtype=np.float32)
    for i, texto in enumerate(textos):
        limpo = catalogo.normalizar(texto.split(": ", 1)[-1])
        for palavra in "".join(c if c.isalnum() else " " for c in limpo).split():
            if len(palavra) > 2:
                vetores[i, int(hashlib.md5(palavra[:6].encode()).hexdigest(), 16) % 512] += 1.0
        norma = np.linalg.norm(vetores[i])
        if norma:
            vetores[i] /= norma
    return vetores


class SessaoLiveSimulada:
    """Faz de Gemini Live para o AssistenteVoz, sem rede: apresenta-se, e a cada
    texto recebido pede uma busca e comenta o resultado. O "áudio" é um zumbido
    modulado, só para o avatar mexer a boca e a reprodução ser exercitada."""

    APRESENTACAO = (
        "Olá! Sou a Maria Portuguesa. Estou aqui para lhe trazer as melhores empregadas domésticas "
        "da sua região, de forma prática, rápida e de confiança. Diga-me, do que precisa?"
    )

    def __init__(self):
        self._fila: asyncio.Queue = asyncio.Queue()
        self._apresentada = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def send_realtime_input(self, text=None, **_):
        if text is None:
            return  # áudio do microfone: a simulação não ouve
        if not self._apresentada:
            self._apresentada = True
            await self._fila.put(("fala", self.APRESENTACAO))
        else:
            await self._fila.put(("procura", text))

    async def send_tool_response(self, function_responses):
        resultado = function_responses[0].response
        perfis = resultado.get("perfis", [])
        if not perfis:
            fala = "Ainda não encontrei ninguém com esse perfil. Quer tentar noutra zona?"
        else:
            primeiro = perfis[0]
            fala = (f"Encontrei {resultado['total_encontrado']} pessoas. Destaco a {primeiro['nome']}, "
                    f"de {primeiro['zona']}. Quer que lhe mostre o perfil?")
        await self._fila.put(("fala", fala))

    @staticmethod
    def _zumbido(segundos: float) -> bytes:
        amostras = int(24000 * segundos)
        return b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 210 * i / 24000)
                                  * (0.55 + 0.45 * math.sin(2 * math.pi * 5 * i / 24000))))
            for i in range(amostras)
        )

    async def receive(self):
        tipo, valor = await self._fila.get()
        if tipo == "procura":
            chamada = SimpleNamespace(id="sim", name="procurar_domesticas", args={"consulta": valor})
            yield SimpleNamespace(tool_call=SimpleNamespace(function_calls=[chamada]), server_content=None)
            return

        def conteudo(**campos):
            base = dict(interrupted=False, model_turn=None, input_transcription=None,
                        output_transcription=None, turn_complete=False)
            base.update(campos)
            return SimpleNamespace(tool_call=None, server_content=SimpleNamespace(**base))

        for palavra in valor.split(" "):
            audio = SimpleNamespace(inline_data=SimpleNamespace(data=self._zumbido(0.22)))
            yield conteudo(model_turn=SimpleNamespace(parts=[audio]),
                           output_transcription=SimpleNamespace(text=palavra + " "))
            await asyncio.sleep(0.2)
        yield conteudo(turn_complete=True)


def _usar_sqlite_em_memoria(app: FastAPI) -> None:
    """Base descartável com perfis de demonstração; substitui a do módulo."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Sessao = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = Sessao()
    for i, dados in enumerate(PERFIS_DEMO):
        perfil = MpDomesticaDB(
            email=EMAIL_DEMO if i == 0 else f"demo{i}@mariaportuguesa.test",
            senha_hash=gerar_hash_senha(SENHA_DEMO),
            publicado=True,
            # Metade partilha o contacto (números inventados), para se verem os dois casos.
            telemovel=f"91{i} 000 00{i}" if i % 2 == 0 else None,
            mostrar_contacto=i % 2 == 0,
            # Um perfil Premium, para se ver o destaque nas pesquisas.
            premium_ate=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30) if i == 1 else None,
            premium_renova=i == 1,
            **dados,
        )
        BuscaService.indexar(perfil)
        db.add(perfil)
    db.commit()
    db.close()
    print(f"🌱 {len(PERFIS_DEMO)} perfis de demonstração criados (conta de teste: {EMAIL_DEMO})")

    def _get_db():
        sessao = Sessao()
        try:
            yield sessao
        finally:
            sessao.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_fabrica_db] = lambda: Sessao


def criar_app(pasta_landing: str | None, sem_e5: bool, porta: int = 8090, banco_real: bool = False,
              voz_simulada: bool = False) -> FastAPI:
    if voz_simulada:
        from core.config import settings
        settings.GEMINI_API_KEYS = ["simulada"]
        AssistenteVoz._abrir_sessao_live = staticmethod(lambda chave: SessaoLiveSimulada())
        print("🎭 Voz simulada: a Maria de ensaio responde sem usar o Gemini.")

    if sem_e5:
        BuscaService.definir_codificador(codificador_simplificado)
    else:
        print("🤖 A carregar o modelo E5 (o mesmo do servidor real)...")
        from services.ai_service import AIService
        AIService.get_model()

    app = FastAPI(title="Maria Portuguesa - desenvolvimento")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(maria_portuguesa.router)

    if banco_real:
        tabelas = maria_portuguesa.criar_banco_e_tabelas()
        print(f"🗄️ Banco real pronto. Tabelas: {', '.join(tabelas)}")
    else:
        _usar_sqlite_em_memoria(app)
    # Fotografias: em desenvolvimento ficam numa pasta temporária local em vez do S3.
    pasta_fotos = tempfile.mkdtemp(prefix="maria_portuguesa_fotos_")

    def _guardar_foto_local(file_obj, filename: str, folder: str = "") -> str:
        with open(os.path.join(pasta_fotos, filename), "wb") as destino:
            shutil.copyfileobj(file_obj, destino)
        return f"http://localhost:{porta}/_fotos/{filename}"

    rotas_mp.upload_file_to_s3 = _guardar_foto_local
    app.mount("/_fotos", StaticFiles(directory=pasta_fotos), name="fotos")

    # Pagamentos: sem MP_STRIPE_SECRET_KEY no .env, a assinatura Premium é dada como
    # paga e a doméstica volta logo ao editor — serve para trabalhar o ecrã, não a Stripe.
    if not pagamentos_mp.MP_STRIPE_SECRET_KEY:
        def _assinatura_simulada(sessao_id: str):
            return pagamentos_mp.Assinatura(
                id=f"sub_{sessao_id}", cliente="cus_simulado", gid=sessao_id.removeprefix("cs_simulada_"),
                ativa=True, valida_ate=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30), cancela_no_fim=False,
            )

        pagamentos_mp.Pagamentos.definir_gateway(
            criar=lambda gid, email, cliente, retorno:
                f"{retorno}?{pagamentos_mp.PARAMETRO_SESSAO}=cs_simulada_{gid}#/domestica",
            consultar=_assinatura_simulada,
            portal=lambda cliente, retorno: f"{retorno}#/domestica",
        )
        print("💳 Pagamento simulado: sem chave da Stripe, o Premium é ativado sem cobrar.")

    if pasta_landing:
        app.mount("/", StaticFiles(directory=pasta_landing, html=True), name="landing")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Servidor de desenvolvimento da Maria Portuguesa")
    parser.add_argument("--landing", help="pasta da landing page a servir em /")
    parser.add_argument("--banco-real", action="store_true", help="usar o banco configurado no módulo")
    parser.add_argument("--voz-simulada", action="store_true", help="conversa de ensaio sem Gemini")
    parser.add_argument("--sem-e5", action="store_true", help="não carregar o modelo E5")
    parser.add_argument("--porta", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(criar_app(args.landing, args.sem_e5, args.porta, args.banco_real, args.voz_simulada), host="127.0.0.1", port=args.porta)
