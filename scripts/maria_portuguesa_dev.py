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
    --sem-e5          arranque instantâneo com um codificador simplificado
                      (a busca deixa de ser semântica; só compara palavras)
    --porta N         porta (por omissão 8090)

A voz precisa de GEMINI_API_KEY no .env; sem ela a pesquisa escrita funciona na mesma.
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile

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
from modules.maria_portuguesa.models import MpDomesticaDB
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


def criar_app(pasta_landing: str | None, sem_e5: bool, porta: int = 8090, banco_real: bool = False) -> FastAPI:
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

    if pasta_landing:
        app.mount("/", StaticFiles(directory=pasta_landing, html=True), name="landing")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Servidor de desenvolvimento da Maria Portuguesa")
    parser.add_argument("--landing", help="pasta da landing page a servir em /")
    parser.add_argument("--banco-real", action="store_true", help="usar o banco configurado no módulo")
    parser.add_argument("--sem-e5", action="store_true", help="não carregar o modelo E5")
    parser.add_argument("--porta", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(criar_app(args.landing, args.sem_e5, args.porta, args.banco_real), host="127.0.0.1", port=args.porta)
