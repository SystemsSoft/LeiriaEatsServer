# Arquivo: conectaai/services/ai/creator_rerank.py
#
# Segunda etapa da busca de creators pela empresa (ver services/match_search.py).
# A busca semântica (embeddings) só pré-seleciona candidatos pelo TEXTO do
# perfil — não enxerga números, então não sabe o que é "com mais seguidores"
# ou "até R$ 500". Aqui o Gemini recebe esses candidatos com os dados do
# perfil (seguidores, preço, engajamento, público…) e o pedido da empresa, e
# devolve uma nota e um motivo por creator, citando os dados reais de cada um.
#
# Falha (sem chave, timeout, JSON inválido) devolve None — quem chama mantém
# o ranking semântico e o motivo genérico; nunca quebra a busca.
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.models.sql_models import CreatorDB
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.schemas.ai_structured import CreatorRerankResult
from conectaai.services.ai import gemini_client, prompts

_MAX_REASON_CHARS = 300
_MAX_SUMMARY_CHARS = 300
_FALLBACK_REASON = "Selecionado pela IA como compatível com o seu pedido."


def _platform_names(platforms) -> List[str]:
    # Linhas antigas guardam só o nome ("Instagram"); as novas, {"platform", "url"}.
    names = []
    for p in platforms or []:
        name = p.get("platform") if isinstance(p, dict) else p
        if name:
            names.append(str(name))
    return names


def _top(values, n: int) -> dict:
    if not isinstance(values, dict):
        return {}
    numeric = [(k, v) for k, v in values.items() if isinstance(v, (int, float))]
    return dict(sorted(numeric, key=lambda kv: kv[1], reverse=True)[:n])


def _profile(ref: str, creator: CreatorDB) -> dict:
    """Perfil enxuto que vai no prompt — só o que ajuda a decidir o match
    (sem id real, e-mail, avatar ou histórico)."""
    profile = {
        "ref": ref,
        "nome": creator.name,
        "cidade": creator.city or "",
        "categorias": creator.categories or [],
        "plataformas": _platform_names(creator.platforms),
        "formatos": creator.content_types or [],
        "seguidores": creator.followers or 0,
        "engajamento_pct": round(creator.engagement_rate or 0, 2),
        "preco_min": creator.price_min or 0,
        "preco_max": creator.price_max or 0,
        "avaliacao": round(creator.rating or 0, 1),
        "bio": (creator.bio or "")[:200],
    }
    audience = creator.audience_info or {}
    if audience:
        profile["audiencia"] = {
            "feminino_pct": audience.get("female_percent", 0),
            "masculino_pct": audience.get("male_percent", 0),
            "idades_pct": _top(audience.get("age_ranges"), 6),
            "locais_pct": _top(audience.get("top_locations"), 3),
            "interesses": (audience.get("interests") or [])[:5],
        }
    return profile


def _clean(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def rerank(
    db: Session,
    text: str,
    previous_queries: List[str],
    candidates: List[CreatorDB],
    max_results: int,
) -> Optional[Tuple[str, List[Tuple[CreatorDB, int, str]]]]:
    """Devolve (resumo de como a IA interpretou o pedido, [(creator, nota
    0-100, motivo), ...] da melhor para a pior nota), ou None se a IA não
    estiver disponível agora. Lista vazia é resposta válida: a IA concluiu
    que nenhum candidato atende ao pedido."""
    if not candidates or not settings.GEMINI_API_KEYS:
        return None

    by_ref = {f"c{i}": creator for i, creator in enumerate(candidates, start=1)}
    system_instruction, user_content = prompts.build_creator_rerank_prompt(
        text,
        previous_queries,
        [_profile(ref, creator) for ref, creator in by_ref.items()],
        max_results,
    )
    result = gemini_client.generate_json(
        system_instruction=system_instruction,
        user_content=user_content,
        response_model=CreatorRerankResult,
        deadline_s=settings.NEGOTIATION_TURN_DEADLINE_S,
        temperature=0.2,
        # ~120 tokens por item (ref + nota + motivo de 1-2 frases) + resumo.
        max_output_tokens=min(4096, 300 + 120 * max_results),
    )
    if result is None:
        return None

    AiCallLogRepository.create(
        db,
        purpose="creator_match",
        model=settings.GEMINI_MODEL,
        key_index=result.key_index,
        attempt=result.attempt,
        status=result.status,
        latency_ms=result.latency_ms,
        prompt=f"{system_instruction}\n{user_content}",
        response_raw=result.raw_text,
        error=result.error,
    )
    if result.parsed is None:
        return None

    ranked: List[Tuple[CreatorDB, int, str]] = []
    seen = set()
    for item in result.parsed.results:
        ref = item.ref.strip()
        creator = by_ref.get(ref)
        if creator is None or ref in seen:
            continue  # ref inventado ou repetido pelo modelo
        seen.add(ref)
        score = max(0, min(100, int(item.score)))
        ranked.append((creator, score, _clean(item.reason, _MAX_REASON_CHARS) or _FALLBACK_REASON))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return _clean(result.parsed.summary, _MAX_SUMMARY_CHARS), ranked[:max_results]
