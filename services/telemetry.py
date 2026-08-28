"""
Telemetria estruturada do fluxo de chat (F0 do PLANO_EXECUCAO_IA.md).

Emite uma linha JSON por turno de conversa, com os tempos de cada etapa e os sinais de
fidelidade (a IA fez o que disse que fez?). É a base de comparação de todas as fases
seguintes do plano — sem isto não há como provar que uma mudança melhorou algo.

Propositalmente NÃO inclui o conteúdo da mensagem do usuário nem da resposta da IA:
mensagens de pedido contêm nome, morada e por vezes restrição alimentar (dado pessoal
sensível sob a LGPD). Loga apenas metadados e métricas.
"""
import json
import logging
import re
from typing import Dict, List, Optional

_logger = logging.getLogger("chat.turn")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


# Padrões simples de "a resposta promete uma ação de carrinho" — usados só para a métrica
# de fidelidade (promessa vs. execução), não para decidir nada no fluxo de negócio.
_PADROES_PROMESSA = [
    re.compile(r"\bvou (adicionar|colocar|incluir|acrescentar)\b", re.IGNORECASE),
    re.compile(r"\bj[áa] (adicionei|coloquei|inclu[íi])\b", re.IGNORECASE),
    re.compile(r"\badicionado\b", re.IGNORECASE),
    re.compile(r"\bao carrinho\b", re.IGNORECASE),
]


def texto_promete_acao_carrinho(texto_limpo: str) -> bool:
    """Detecta, por heurística textual, se a resposta (já sem tags) sugere ao usuário
    que uma ação de carrinho foi executada. Usado só para telemetria."""
    if not texto_limpo:
        return False
    return any(p.search(texto_limpo) for p in _PADROES_PROMESSA)


# F4.3 do PLANO_EXECUCAO_IA.md: mesmo instruindo o modelo a não escrever preços no texto
# (system instruction), nada validava se ele obedeceu — um preço alucinado passava direto
# para o cliente. `divergencia_de_preco` é a rede de segurança: não corrige a resposta,
# só torna visível com que frequência isso acontece de fato.
_PADRAO_PRECO_EUR = re.compile(r"€\s*(\d+[.,]\d{2})")


def _extrair_precos_mencionados(texto: str) -> List[float]:
    if not texto:
        return []
    valores = []
    for m in _PADRAO_PRECO_EUR.finditer(texto):
        try:
            valores.append(float(m.group(1).replace(",", ".")))
        except ValueError:
            continue
    return valores


def detectar_divergencia_de_preco(texto_limpo: str, produtos_no_pool: List[Dict],
                                   tolerancia: float = 0.01) -> bool:
    """True se a resposta menciona um valor em euros que não corresponde ao preço de
    NENHUM produto do pool oferecido neste turno — sinal de preço inventado/desatualizado."""
    precos_mencionados = _extrair_precos_mencionados(texto_limpo)
    if not precos_mencionados:
        return False
    precos_catalogo = {round(float(p.get("price", -1)), 2) for p in produtos_no_pool if p.get("price") is not None}
    if not precos_catalogo:
        return bool(precos_mencionados)
    for preco in precos_mencionados:
        if not any(abs(preco - pc) <= tolerancia for pc in precos_catalogo):
            return True
    return False


def registrar_turno(
    *,
    session_id: str,
    restaurant_gid: Optional[str],
    modelo: str,
    ms_e5: float,
    ms_pool: float,
    ms_ttft: Optional[float],
    ms_total: float,
    pool_size: int,
    tokens_prompt_estimado: int,
    tags_emitidas: int,
    tags_aplicadas: int,
    tags_descartadas_motivo: Optional[List[str]] = None,
    promessa_de_acao: bool = False,
    divergencia_de_preco: bool = False,
    motivo_fallback: Optional[str] = None,
    intent_type: Optional[str] = None,
    origem: str = "stream",
    restaurantes_no_carrinho: int = 0,
    limite_restaurantes_atingido: bool = False,
) -> None:
    """
    Registra um turno completo de conversa. Chamar sempre no caminho de sucesso E no de
    erro/fallback, para a taxa de fallback ficar visível na métrica, não escondida.
    """
    carrinho_mudou = tags_aplicadas > 0
    # Fidelidade: se prometeu ação e o carrinho não mudou, é o sintoma central do projeto
    # ("a IA disse que adicionou e não adicionou").
    promessa_sem_execucao = promessa_de_acao and not carrinho_mudou

    evento = {
        "session_id": (session_id or "")[:8],
        "restaurant_gid": restaurant_gid,
        "modelo": modelo,
        "origem": origem,
        "intent_type": intent_type,
        "ms_e5": round(ms_e5, 1) if ms_e5 is not None else None,
        "ms_pool": round(ms_pool, 1) if ms_pool is not None else None,
        "ms_ttft": round(ms_ttft, 1) if ms_ttft is not None else None,
        "ms_total": round(ms_total, 1) if ms_total is not None else None,
        "pool_size": pool_size,
        "tokens_prompt_estimado": tokens_prompt_estimado,
        "tags_emitidas": tags_emitidas,
        "tags_aplicadas": tags_aplicadas,
        "tags_descartadas_motivo": tags_descartadas_motivo or [],
        "carrinho_mudou": carrinho_mudou,
        "promessa_de_acao": promessa_de_acao,
        "promessa_sem_execucao": promessa_sem_execucao,
        "divergencia_de_preco": divergencia_de_preco,
        "motivo_fallback": motivo_fallback,
        # PLANO_LIMITE_RESTAURANTES.md, Fase 4.2 — dá para responder depois: com que
        # frequência os clientes esbarram no limite, e 3 é o número certo?
        "restaurantes_no_carrinho": restaurantes_no_carrinho,
        "limite_restaurantes_atingido": limite_restaurantes_atingido,
    }
    _logger.info(json.dumps(evento, ensure_ascii=False))


def registrar_amostra_relevancia(
    *,
    session_id: str,
    consulta: str,
    pool_gids: List[str],
    gid_escolhido: Optional[str],
) -> None:
    """
    Registra (consulta, pool de GIDs oferecidos, GID que efetivamente entrou no carrinho)
    para construir o conjunto de avaliação de busca (F0.2) sem precisar rotular nada à mão:
    o produto que o usuário escolheu É o rótulo de relevância.

    Não loga o texto completo da consulta em produção por padrão — ver nota de privacidade
    no topo do módulo. Mantido aqui como função de log estruturado; a decisão de habilitar
    ou truncar `consulta` em produção deve ser tomada junto da área de privacidade.
    """
    evento = {
        "tipo": "amostra_relevancia",
        "session_id": (session_id or "")[:8],
        "pool_gids": pool_gids,
        "gid_escolhido": gid_escolhido,
        "posicao_escolhida": (pool_gids.index(gid_escolhido) if gid_escolhido in pool_gids else None),
    }
    _logger.info(json.dumps(evento, ensure_ascii=False))
