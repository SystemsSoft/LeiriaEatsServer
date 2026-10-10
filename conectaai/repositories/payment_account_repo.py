# Arquivo: conectaai/repositories/payment_account_repo.py
from typing import Optional

from sqlalchemy.orm import Session

from conectaai.models.sql_models import PaymentAccountDB


class PaymentAccountRepository:
    @staticmethod
    def get_by_user_id(db: Session, user_id: str) -> Optional[PaymentAccountDB]:
        return db.query(PaymentAccountDB).filter(PaymentAccountDB.user_id == user_id).first()

    @staticmethod
    def get_by_stripe_id(db: Session, stripe_account_id: str) -> Optional[PaymentAccountDB]:
        return db.query(PaymentAccountDB).filter(PaymentAccountDB.stripe_account_id == stripe_account_id).first()

    @staticmethod
    def create(db: Session, user_id: str, role: str, stripe_account_id: str, country: str) -> PaymentAccountDB:
        account = PaymentAccountDB(user_id=user_id, role=role, stripe_account_id=stripe_account_id, country=country)
        db.add(account)
        db.commit()
        db.refresh(account)
        return account

    @staticmethod
    def update_status(db: Session, account: PaymentAccountDB, status: dict) -> PaymentAccountDB:
        """Copia a situação que a Stripe informou (ver stripe_connect.account_status)."""
        account.details_submitted = bool(status.get("details_submitted"))
        account.charges_enabled = bool(status.get("charges_enabled"))
        account.payouts_enabled = bool(status.get("payouts_enabled"))
        account.requirements_due = list(status.get("requirements_due") or [])
        db.commit()
        db.refresh(account)
        return account
