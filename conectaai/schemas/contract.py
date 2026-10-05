# Arquivo: conectaai/schemas/contract.py
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ContractSection(BaseModel):
    heading: str
    text: str


class ContractContent(BaseModel):
    title: str
    sections: List[ContractSection] = []


class ContractSignatureResponse(BaseModel):
    role: str  # "company" | "creator"
    signer_name: str
    signer_email: str
    signed_at: datetime
    ip_address: str
    content_hash: str
    signature_code: str

    class Config:
        from_attributes = True


class ContractResponse(BaseModel):
    id: str
    proposal_id: str
    agreement_id: Optional[str]
    company_id: str
    creator_id: str
    campaign_id: Optional[str]
    title: str
    content: ContractContent
    content_hash: str
    source: str  # "gemini" | "template"
    status: str  # "awaiting_signatures" | "signed"
    signatures: List[ContractSignatureResponse] = []
    # false se o conteúdo guardado não bate mais com o hash (adulteração) ou se alguma
    # assinatura atesta outra versão do texto.
    integrity_ok: bool = True
    terms: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime


class CreateContractRequest(BaseModel):
    proposal_id: str


class SignContractRequest(BaseModel):
    # Hash do contrato que a pessoa viu na tela. Se não bate com o texto atual, a
    # assinatura é recusada — ninguém assina uma versão que não leu.
    content_hash: str
