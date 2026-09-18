# Arquivo: conectaai/repositories/user_repo.py
import secrets
from typing import Optional

from sqlalchemy.orm import Session

from conectaai.core.security import hash_password
from conectaai.models.sql_models import CompanyDB, CreatorDB, UserDB


class UserRepository:
    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[UserDB]:
        return db.query(UserDB).filter(UserDB.email == email).first()

    @staticmethod
    def get_by_id(db: Session, user_id: str) -> Optional[UserDB]:
        return db.query(UserDB).filter(UserDB.id == user_id).first()

    @staticmethod
    def create(db: Session, *, role: str, name: str, email: str, password: Optional[str] = None) -> UserDB:
        # `password=None` cobre contas criadas via Google — não têm senha
        # própria, então geramos uma aleatória só para preencher a coluna
        # (não-nula); essa conta nunca faz login por senha.
        real_password = password or secrets.token_hex(32)
        user = UserDB(role=role, name=name, email=email, password_hash=hash_password(real_password))
        db.add(user)
        db.flush()  # garante user.id antes de criar o perfil dependente

        if role == "company":
            db.add(CompanyDB(user_id=user.id, name=name))
        elif role == "creator":
            db.add(CreatorDB(user_id=user.id, name=name))

        db.commit()
        db.refresh(user)
        return user
