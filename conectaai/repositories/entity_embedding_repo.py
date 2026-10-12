# Arquivo: conectaai/repositories/entity_embedding_repo.py
from typing import List, Optional

import numpy as np
from sqlalchemy.orm import Session

from conectaai.models.sql_models import EntityEmbeddingDB


class EntityEmbeddingRepository:
    @staticmethod
    def get(db: Session, entity_type: str, entity_id: str, model: str) -> Optional[EntityEmbeddingDB]:
        return (
            db.query(EntityEmbeddingDB)
            .filter(
                EntityEmbeddingDB.entity_type == entity_type,
                EntityEmbeddingDB.entity_id == entity_id,
                EntityEmbeddingDB.model == model,
            )
            .first()
        )

    @staticmethod
    def get_all_for_type(
        db: Session, entity_type: str, model: str, entity_ids: Optional[List[str]] = None
    ) -> List[EntityEmbeddingDB]:
        query = db.query(EntityEmbeddingDB).filter(
            EntityEmbeddingDB.entity_type == entity_type, EntityEmbeddingDB.model == model
        )
        if entity_ids is not None:
            if not entity_ids:
                return []
            query = query.filter(EntityEmbeddingDB.entity_id.in_(entity_ids))
        return query.all()

    @staticmethod
    def upsert(db: Session, *, entity_type: str, entity_id: str, model: str, vector: List[float], source_hash: str) -> EntityEmbeddingDB:
        vec_bytes = np.array(vector, dtype="float32").tobytes()
        existing = EntityEmbeddingRepository.get(db, entity_type, entity_id, model)
        if existing:
            existing.vector = vec_bytes
            existing.dim = len(vector)
            existing.source_hash = source_hash
            db.commit()
            db.refresh(existing)
            return existing

        row = EntityEmbeddingDB(
            entity_type=entity_type, entity_id=entity_id, model=model, dim=len(vector), vector=vec_bytes, source_hash=source_hash
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
