# Arquivo: modules/maria_portuguesa/search_service.py
#
# Busca semântica de domésticas. Usa o mesmo modelo E5 do AIService (já em memória
# por causa do Leiria Eats) — cada perfil publicado tem o seu vetor guardado na
# própria linha, e a pesquisa é uma similaridade de cosseno sobre os candidatos que
# passam nos filtros estruturados.
#
# Não há índice em memória de propósito: os perfis são lidos da base a cada busca,
# por isso funciona igual com vários workers e nunca fica desatualizado. Com alguns
# milhares de perfis (4 KB de vetor cada) continua a ser uma leitura barata; se o
# volume crescer muito além disso, é aqui que entra um índice vetorial.
from typing import Callable, Optional

import numpy as np
from sqlalchemy import or_
from sqlalchemy.orm import Session

from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa.models import MpDomesticaDB, premium_ativo
from modules.maria_portuguesa.schemas import perfil_publico

# Filtros que podem ser abandonados quando não há ninguém que os cumpra todos, pela
# ordem em que são largados. Zona e serviços nunca são relaxados: mostrar alguém de
# outro distrito, ou que não faz o que foi pedido, não ajuda quem procura.
_ORDEM_RELAXAMENTO = ("dias", "preco_max")

# Quem está no concelho pedido sobe face a quem só partilha o distrito.
_BONUS_MESMO_CONCELHO = 0.05
# Perfis Premium sobem nas pesquisas. É um empurrão e não um salto para o topo:
# um perfil em destaque pouco relacionado com o pedido não passa à frente de quem
# corresponde bem a ele.
_BONUS_DESTAQUE = 0.04


class BuscaService:
    # Ponto de substituição para testes e para o servidor de desenvolvimento:
    # função (list[str]) -> matriz float32 com vetores normalizados.
    _codificador: Optional[Callable[[list[str]], np.ndarray]] = None

    @classmethod
    def definir_codificador(cls, codificador: Optional[Callable[[list[str]], np.ndarray]]) -> None:
        cls._codificador = codificador

    @classmethod
    def _codificar(cls, textos: list[str]) -> np.ndarray:
        if cls._codificador is not None:
            return np.asarray(cls._codificador(textos), dtype=np.float32)
        # Import tardio: carregar o AIService puxa torch + sentence-transformers.
        from services.ai_service import AIService
        modelo = AIService.get_model()
        vetores = modelo.encode(textos, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vetores, dtype=np.float32)

    # ── Indexação ────────────────────────────────────────────────────────────

    @staticmethod
    def texto_do_perfil(d: MpDomesticaDB) -> str:
        """Texto que representa o perfil no espaço vetorial. Escrito em frases, como
        alguém descreveria a pessoa — é assim que as pesquisas por voz chegam."""
        partes = []
        if d.titulo:
            partes.append(d.titulo.rstrip(".") + ".")
        servicos = [catalogo.SERVICOS[s] for s in (d.servicos or []) if s in catalogo.SERVICOS]
        if servicos:
            partes.append("Serviços: " + ", ".join(servicos).lower() + ".")
        if d.concelho or d.distrito:
            zona = ", distrito de ".join(z for z in (d.concelho, d.distrito) if z)
            partes.append(f"Trabalha em {zona}.")
        if d.anos_experiencia:
            partes.append(f"{d.anos_experiencia} anos de experiência.")
        dias = [catalogo.DIAS[x] for x in (d.dias or []) if x in catalogo.DIAS]
        periodos = [catalogo.PERIODOS[x] for x in (d.periodos or []) if x in catalogo.PERIODOS]
        if dias or periodos:
            partes.append("Disponível: " + ", ".join(dias + periodos).lower() + ".")
        regimes = [catalogo.REGIMES[x] for x in (d.regimes or []) if x in catalogo.REGIMES]
        if regimes:
            partes.append("Regime: " + ", ".join(regimes).lower() + ".")
        if d.idiomas:
            partes.append("Fala " + ", ".join(d.idiomas).lower() + ".")
        extras = []
        if d.tem_transporte:
            extras.append("tem transporte próprio")
        if d.aceita_animais:
            extras.append("está à vontade com animais de estimação")
        if d.passa_recibo:
            extras.append("passa recibo")
        if d.tem_referencias:
            extras.append("tem referências")
        if extras:
            partes.append(", ".join(extras).capitalize() + ".")
        if d.sobre:
            partes.append(d.sobre)
        # Prefixo exigido pela família E5 para documentos.
        return "passage: " + " ".join(partes)

    @classmethod
    def indexar(cls, d: MpDomesticaDB) -> None:
        """Recalcula o vetor do perfil. Quem chama faz o commit."""
        d.embedding = cls._codificar([cls.texto_do_perfil(d)])[0].tobytes()

    # ── Pesquisa ─────────────────────────────────────────────────────────────

    @classmethod
    def procurar(
            cls,
            db: Session,
            consulta: str = "",
            localidade: Optional[str] = None,
            distrito: Optional[str] = None,
            servicos: Optional[list[str]] = None,
            dias: Optional[list[str]] = None,
            preco_max: Optional[float] = None,
            limite: int = 6,
    ) -> dict:
        consulta = (consulta or "").strip()
        servicos = [s for s in (servicos or []) if s in catalogo.SERVICOS]
        dias = [x for x in (dias or []) if x in catalogo.DIAS]

        # "Leiria" tanto pode ser o concelho como o distrito. Se o que foi dito é o
        # nome de um distrito, aceita-se qualquer pessoa desse distrito e dá-se
        # preferência a quem está no concelho com o mesmo nome.
        distrito = catalogo.resolver_distrito(distrito)
        chave_local = catalogo.normalizar(localidade)
        distrito_da_localidade = catalogo.resolver_distrito(localidade) if chave_local else None

        query = db.query(MpDomesticaDB).filter(MpDomesticaDB.publicado.is_(True))
        if distrito:
            query = query.filter(MpDomesticaDB.distrito == distrito)
        if distrito_da_localidade:
            query = query.filter(or_(
                MpDomesticaDB.distrito == distrito_da_localidade,
                MpDomesticaDB.concelho == localidade,
            ))
        candidatos = query.all()

        if chave_local and not distrito_da_localidade:
            candidatos = [d for d in candidatos if catalogo.normalizar(d.concelho) == chave_local]
        if servicos:
            candidatos = [d for d in candidatos if set(servicos) <= set(d.servicos or [])]

        filtros = {
            "dias": (lambda d: bool(set(dias) & set(d.dias or []))) if dias else None,
            "preco_max": (lambda d: d.preco_hora is None or d.preco_hora <= preco_max) if preco_max is not None else None,
        }
        ativos = [nome for nome, f in filtros.items() if f is not None]
        relaxados: list[str] = []
        while True:
            filtrados = [d for d in candidatos if all(filtros[nome](d) for nome in ativos)]
            if filtrados or not ativos:
                break
            largar = next(nome for nome in _ORDEM_RELAXAMENTO if nome in ativos)
            ativos.remove(largar)
            relaxados.append(largar)

        if not filtrados:
            return {"consulta": consulta, "total": 0, "filtros_relaxados": [], "resultados": []}

        if consulta:
            ordenados = cls._ordenar_por_afinidade(db, consulta, filtrados, chave_local)
        else:
            # Sem pedido para comparar: primeiro os perfis em destaque, depois os mais recentes.
            recentes = sorted(filtrados, key=lambda d: (premium_ativo(d), d.atualizado_em), reverse=True)
            ordenados = [(d, None) for d in recentes]

        resultados = []
        for d, afinidade in ordenados[:limite]:
            perfil = perfil_publico(d)
            perfil["afinidade"] = afinidade
            resultados.append(perfil)

        return {
            "consulta": consulta,
            "total": len(ordenados),
            "filtros_relaxados": relaxados,
            "resultados": resultados,
        }

    @classmethod
    def _ordenar_por_afinidade(
            cls, db: Session, consulta: str, perfis: list[MpDomesticaDB], chave_local: str,
    ) -> list[tuple[MpDomesticaDB, float]]:
        vetor_consulta = cls._codificar(["query: " + consulta])[0]
        tamanho = vetor_consulta.nbytes

        # Perfis sem vetor, ou com vetor de outro modelo, são indexados na hora.
        por_indexar = [d for d in perfis if d.embedding is None or len(d.embedding) != tamanho]
        if por_indexar:
            vetores = cls._codificar([cls.texto_do_perfil(d) for d in por_indexar])
            for d, vetor in zip(por_indexar, vetores):
                d.embedding = vetor.tobytes()
            db.commit()

        matriz = np.stack([np.frombuffer(d.embedding, dtype=np.float32) for d in perfis])
        pontuacoes = matriz @ vetor_consulta
        if chave_local:
            mesmo_concelho = np.array([catalogo.normalizar(d.concelho) == chave_local for d in perfis])
            pontuacoes = pontuacoes + _BONUS_MESMO_CONCELHO * mesmo_concelho
        em_destaque = np.array([premium_ativo(d) for d in perfis])
        pontuacoes = pontuacoes + _BONUS_DESTAQUE * em_destaque

        ordem = np.argsort(-pontuacoes)
        return [(perfis[i], round(float(pontuacoes[i]), 4)) for i in ordem]
