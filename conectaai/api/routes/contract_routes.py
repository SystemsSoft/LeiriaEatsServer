# Arquivo: conectaai/api/routes/contract_routes.py
#
# Contrato de uma proposta aceita: a empresa gera (com ajuda da IA), as duas
# partes leem e assinam eletronicamente. A regra de negócio fica em
# services/contract_service.py; aqui só autorização e tradução de erros.
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from conectaai.api.deps import self_id
from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, get_current_user, require_role
from conectaai.models.sql_models import ContractDB
from conectaai.repositories.contract_repo import ContractRepository
from conectaai.schemas.contract import (
    ContractContent,
    ContractResponse,
    ContractSignatureResponse,
    CreateContractRequest,
    SignContractRequest,
)
from conectaai.services import contract_service

router = APIRouter(prefix="/contracts", tags=["Contratos"])


def _to_response(contract: ContractDB) -> ContractResponse:
    return ContractResponse(
        id=contract.id,
        proposal_id=contract.proposal_id,
        agreement_id=contract.agreement_id,
        company_id=contract.company_id,
        creator_id=contract.creator_id,
        campaign_id=contract.campaign_id,
        title=contract.title,
        content=ContractContent(**(contract.content or {"title": contract.title})),
        content_hash=contract.content_hash,
        source=contract.source,
        status=contract.status,
        signatures=[ContractSignatureResponse.model_validate(s) for s in contract.signatures],
        integrity_ok=contract_service.integrity_ok(contract),
        terms=contract.terms or {},
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


def _client_ip(request: Request) -> str:
    # Atrás do nginx o IP real vem em X-Forwarded-For (primeiro da lista).
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _load_for_participant(db: Session, contract_id: str, current_user: CurrentUser) -> ContractDB:
    contract = ContractRepository.get_by_id(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    own_id = contract.creator_id if current_user.role == "creator" else contract.company_id
    if own_id != self_id(db, current_user):
        raise HTTPException(status_code=403, detail="Esse contrato não é seu")
    return contract


@router.get("", response_model=List[ContractResponse])
def list_contracts(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    my_id = self_id(db, current_user)
    company_id = my_id if current_user.role == "company" else None
    creator_id = my_id if current_user.role == "creator" else None
    if not my_id:
        return []
    return [_to_response(c) for c in ContractRepository.get_all_for_user(db, company_id=company_id, creator_id=creator_id)]


@router.post("", response_model=ContractResponse, status_code=201)
def create_contract(
    data: CreateContractRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Gera o contrato da proposta aceita. Se já existe um, devolve o mesmo (sem gerar de novo)."""
    try:
        contract = contract_service.create_for_proposal(db, data.proposal_id, current_user.user_id)
    except contract_service.ContractError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    return _to_response(contract)


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(contract_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return _to_response(_load_for_participant(db, contract_id, current_user))


@router.post("/{contract_id}/sign", response_model=ContractResponse)
def sign_contract(
    contract_id: str,
    data: SignContractRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contract = _load_for_participant(db, contract_id, current_user)
    try:
        contract = contract_service.sign(
            db,
            contract,
            user_id=current_user.user_id,
            role=current_user.role,
            profile_id=self_id(db, current_user),
            content_hash=data.content_hash,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except contract_service.ContractError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    return _to_response(contract)
