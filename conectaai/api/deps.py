# Arquivo: conectaai/api/deps.py
#
# Helpers de autorização compartilhados entre routers. Extraídos de
# conversation_routes.py (onde nasceram) para serem reaproveitados por
# negotiation_routes.py, agreement_routes.py e mandate_routes.py sem duplicar
# a lógica de "quem é o dono desse recurso".
from sqlalchemy.orm import Session

from conectaai.core.security import CurrentUser
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository


def self_id(db: Session, current_user: CurrentUser) -> str:
    """Resolve o id do *perfil* (company.id ou creator.id) do usuário
    autenticado — é esse id, não o user_id do token, que aparece como
    company_id/creator_id nas tabelas de negócio."""
    if current_user.role == "company":
        company = CompanyRepository.get_by_user_id(db, current_user.user_id)
        return company.id if company else ""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    return creator.id if creator else ""
