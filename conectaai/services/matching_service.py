# Arquivo: conectaai/services/matching_service.py
#
# Matching semântico bilateral: empresa busca creators, creator busca
# oportunidades (mandatos de empresas com campanha ativa) — mesma técnica
# dos dois lados, só troca a "coleção" e o texto canônico. Sem pgvector: a
# escala é de dezenas/poucas centenas de creators, então carregar os vetores
# do MySQL e comparar em numpy é suficiente (ver conectaai/models/sql_models.py,
# EntityEmbeddingDB).
#
# Reindexação é preguiçosa (sob demanda, dentro do próprio request de busca):
# compara o hash do texto canônico atual com o que está salvo, só reprocessa
# quem mudou. Sem cron, sem fila — aceitável nesta escala; documentado como
# limite conhecido.
import hashlib
from typing import List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import Session

from conectaai.models.sql_models import CommercialMandateDB, CreatorDB
from conectaai.repositories.entity_embedding_repo import EntityEmbeddingRepository
from conectaai.services.ai import embeddings

MODEL_NAME = embeddings.MODEL_NAME


def semantic_available() -> bool:
    return embeddings.available()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _creator_text(creator: CreatorDB) -> str:
    bits = [
        creator.name,
        creator.bio or "",
        creator.city or "",
        ", ".join(creator.categories or []),
        ", ".join(creator.content_types or []),
    ]
    return " | ".join(b for b in bits if b)


def _mandate_text(mandate: CommercialMandateDB, company_name: str) -> str:
    deliverables = ", ".join(d.get("content_type", "") for d in (mandate.deliverables or []))
    bits = [company_name, mandate.objective or "", deliverables, ", ".join(mandate.non_negotiable_fields or [])]
    return " | ".join(b for b in bits if b)


def _reindex(db: Session, entity_type: str, items: List[Tuple[str, str]]) -> None:
    to_embed_ids, to_embed_texts, hashes = [], [], {}
    for entity_id, text in items:
        h = _hash(text)
        hashes[entity_id] = h
        existing = EntityEmbeddingRepository.get(db, entity_type, entity_id, MODEL_NAME)
        if existing is None or existing.source_hash != h:
            to_embed_ids.append(entity_id)
            to_embed_texts.append(text)
    if not to_embed_texts:
        return

    vectors = embeddings.embed_texts(to_embed_texts, task_type="RETRIEVAL_DOCUMENT")
    if vectors is None:
        return
    for entity_id, vec in zip(to_embed_ids, vectors):
        EntityEmbeddingRepository.upsert(
            db, entity_type=entity_type, entity_id=entity_id, model=MODEL_NAME, vector=vec, source_hash=hashes[entity_id]
        )


def rank_creators(db: Session, query_text: str, creators: List[CreatorDB], limit: int = 10) -> Optional[List[Tuple[CreatorDB, float]]]:
    """Devolve None quando a semântica não está disponível agora (sem chave,
    ou a chamada de embeddings falhou) — o endpoint deve cair pro fallback
    por palavra-chave nesse caso, nunca devolver lista vazia como se
    realmente não houvesse match nenhum."""
    if not creators:
        return []
    _reindex(db, "creator", [(c.id, _creator_text(c)) for c in creators])

    query_vec = embeddings.embed_texts([query_text], task_type="RETRIEVAL_QUERY")
    if query_vec is None:
        return None

    rows = EntityEmbeddingRepository.get_all_for_type(db, "creator", MODEL_NAME, [c.id for c in creators])
    by_id = {c.id: c for c in creators}
    q = np.array(query_vec[0], dtype="float32")
    scored = []
    for row in rows:
        creator = by_id.get(row.entity_id)
        if creator is None:
            continue
        vec = np.frombuffer(row.vector, dtype="float32")
        scored.append((creator, float(np.dot(q, vec))))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def rank_mandates(
    db: Session, query_text: str, mandates_with_company: List[Tuple[CommercialMandateDB, str]], limit: int = 10
) -> Optional[List[Tuple[CommercialMandateDB, str, float]]]:
    """`mandates_with_company` = [(mandate, company_name), ...]."""
    if not mandates_with_company:
        return []
    _reindex(db, "mandate", [(m.id, _mandate_text(m, name)) for m, name in mandates_with_company])

    query_vec = embeddings.embed_texts([query_text], task_type="RETRIEVAL_QUERY")
    if query_vec is None:
        return None

    ids = [m.id for m, _ in mandates_with_company]
    rows = EntityEmbeddingRepository.get_all_for_type(db, "mandate", MODEL_NAME, ids)
    by_id = {m.id: (m, name) for m, name in mandates_with_company}
    q = np.array(query_vec[0], dtype="float32")
    scored = []
    for row in rows:
        pair = by_id.get(row.entity_id)
        if pair is None:
            continue
        vec = np.frombuffer(row.vector, dtype="float32")
        scored.append((pair[0], pair[1], float(np.dot(q, vec))))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:limit]


def keyword_score_creator(query_text: str, creator: CreatorDB) -> int:
    """Fallback determinístico (sem IA) — usado quando semantic_available()
    é False ou a chamada de embeddings falha em tempo real."""
    q = query_text.lower()
    score = 10
    for cat in creator.categories or []:
        if cat.lower() in q:
            score += 35
    if creator.city and creator.city.lower() in q:
        score += 20
    score += min(20, int((creator.engagement_rate or 0) * 4))
    return min(100, score)


def keyword_score_mandate(query_text: str, mandate: CommercialMandateDB, company_name: str) -> int:
    q = query_text.lower()
    score = 10
    text = f"{mandate.objective or ''} {company_name}".lower()
    for word in set(q.split()):
        if len(word) > 3 and word in text:
            score += 15
    deliverable_types = [d.get("content_type", "").lower() for d in (mandate.deliverables or [])]
    if any(d in q for d in deliverable_types if d):
        score += 20
    return min(100, score)
