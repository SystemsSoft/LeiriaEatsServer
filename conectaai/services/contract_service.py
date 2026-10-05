# Arquivo: conectaai/services/contract_service.py
#
# Contrato de uma proposta aceita, com assinatura eletrônica das duas partes.
#
# Quem escreve o quê:
#   - Os TERMOS COMERCIAIS (partes, objeto, valor, entregas, prazo) são montados
#     pelo servidor a partir dos dados da proposta/acordo — a IA nunca decide nem
#     reescreve esses valores.
#   - A IA (Gemini) escreve só as CLÁUSULAS GERAIS (obrigações, aprovação do
#     conteúdo, direitos de imagem, identificação publicitária, sigilo,
#     rescisão), adaptadas ao caso. O texto dela é validado: qualquer dígito
#     (valor, prazo, percentual, multa, número de lei) invalida a resposta, e o
#     contrato cai nas cláusulas-padrão abaixo — assim ela não consegue inventar
#     uma obrigação financeira nem contradizer o que foi acordado.
#   - Sem chave do Gemini, ou se a IA falhar, vale o texto-padrão: o botão
#     "Gerar contrato" nunca fica sem resposta.
#
# Assinatura: eletrônica SIMPLES (MP 2.200-2/2001, art. 10 §2º; Lei 14.063/2020,
# art. 4º, I) — cada parte, logada, confirma o hash da versão que leu; guardamos
# nome, e-mail, data/hora, IP e um código HMAC desses dados. Não é assinatura
# qualificada (ICP-Brasil); para contratos de valor alto, a revisão jurídica e
# uma assinatura qualificada dão mais força de prova.
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conectaai.core.config import settings
from conectaai.models.sql_models import AgreementDB, ContractDB, ContractSignatureDB, ProposalDB, UserDB
from conectaai.repositories.ai_call_log_repo import AiCallLogRepository
from conectaai.repositories.campaign_repo import CampaignRepository
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.contract_repo import ContractRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.notification_repo import NotificationRepository
from conectaai.schemas.ai_structured import ContractClauses
from conectaai.services.ai import gemini_client

CONTRACT_TITLE = "CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE CRIAÇÃO DE CONTEÚDO PUBLICITÁRIO"

_AUTO_PROPOSAL_NOTE = "Gerado automaticamente"
_MAX_HEADING_CHARS = 90
_MAX_CLAUSE_CHARS = 1800
_MIN_AI_CLAUSES = 5
_MAX_AI_CLAUSES = 10
_AI_DEADLINE_S = 20.0


class ContractError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Termos acordados ---------------------------------------------------------


def _agreement_for(db: Session, proposal: ProposalDB) -> Optional[AgreementDB]:
    return db.query(AgreementDB).filter(AgreementDB.proposal_id == proposal.id).first()


def collect_terms(db: Session, proposal: ProposalDB) -> Dict[str, Any]:
    """Instantâneo dos termos acordados. Se a proposta nasceu de uma negociação,
    os termos finais estão no acordo (preço, entregas por formato, prazo em dias,
    exclusividade); senão, nos campos da própria proposta."""
    company = CompanyRepository.get_by_id(db, proposal.company_id)
    creator = CreatorRepository.get_by_id(db, proposal.creator_id)
    campaign = CampaignRepository.get_by_id(db, proposal.campaign_id) if proposal.campaign_id else None
    agreement = _agreement_for(db, proposal)
    agreed = (agreement.terms or {}) if agreement is not None else {}

    description = (proposal.description or "").strip()
    if description.startswith(_AUTO_PROPOSAL_NOTE):
        description = ""  # texto técnico do sistema, não faz parte do que foi combinado

    deliverables = [
        {"content_type": str(d.get("content_type", "")).strip(), "quantity": int(d.get("quantity") or 1)}
        for d in (agreed.get("deliverables") or [])
        if str(d.get("content_type", "")).strip()
    ]
    price = agreed.get("price")
    if price is None:
        price = agreement.total_value if agreement is not None and agreement.total_value else proposal.budget

    deadline_days = agreed.get("deadline_days")
    return {
        "origin": "acordo" if agreement is not None else "proposta",
        "company": {
            "name": (company.name if company else "") or "",
            "segment": (company.segment if company else "") or "",
            "city": (company.city if company else "") or "",
        },
        "creator": {
            "name": (creator.name if creator else "") or "",
            "username": (creator.username if creator else "") or "",
            "city": (creator.city if creator else "") or "",
        },
        "campaign_name": (proposal.campaign_name or (campaign.name if campaign else "") or "").strip(),
        "objective": description,
        "price": float(price or 0),
        "deliverables": deliverables,
        "content_type": (proposal.content_type or "").strip(),
        "quantity": int(proposal.quantity or 1),
        "deadline_days": int(deadline_days) if deadline_days else None,
        "deadline_date": proposal.deadline.date().isoformat() if proposal.deadline and not deadline_days else None,
        "exclusivity": agreed.get("exclusivity") if agreed.get("exclusivity") is not None else None,
    }


# --- Texto -------------------------------------------------------------------


def _money(value: float) -> str:
    text = f"{value:,.2f}"  # 1,500.00
    return "R$ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


def _deliverables_text(terms: Dict[str, Any]) -> str:
    if terms.get("deliverables"):
        return ", ".join(f"{d['quantity']}x {d['content_type']}" for d in terms["deliverables"])
    quantity = terms.get("quantity") or 1
    formats = terms.get("content_type") or "formato a definir"
    return f"{quantity} conteúdo(s) no(s) formato(s): {formats}"


def _deadline_text(terms: Dict[str, Any]) -> str:
    if terms.get("deadline_days"):
        return f"O CONTRATADO(A) entregará e publicará o conteúdo em até {terms['deadline_days']} dias corridos, contados da assinatura deste contrato por ambas as partes."
    if terms.get("deadline_date"):
        year, month, day = terms["deadline_date"].split("-")
        return f"O CONTRATADO(A) entregará e publicará o conteúdo até {day}/{month}/{year}."
    return "O prazo de entrega e publicação não foi definido na proposta aceita e deverá ser ajustado por escrito entre as partes."


def _party_line(label: str, party: Dict[str, str], *, creator: bool) -> str:
    bits = [party["name"] or "(nome não informado)"]
    if creator and party.get("username"):
        bits.append(f"perfil {party['username']}")
    if not creator and party.get("segment"):
        bits.append(f"segmento {party['segment']}")
    if party.get("city"):
        bits.append(party["city"])
    return f"{label}: {', '.join(bits)}."


def _fixed_sections(terms: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """(seções antes das cláusulas gerais, seções depois) — tudo determinístico."""
    objective = terms.get("objective") or ""
    campaign = terms.get("campaign_name") or "campanha sem nome"
    exclusivity = terms.get("exclusivity")
    object_text = (
        f"O objeto deste contrato é a criação e a publicação de conteúdo publicitário pelo CONTRATADO(A) para a campanha \"{campaign}\" "
        f"do CONTRATANTE, na plataforma ConectaAI.\nEntregas acordadas: {_deliverables_text(terms)}."
    )
    if objective:
        object_text += f"\nDescrição e briefing informados na proposta: {objective}"
    if exclusivity is True:
        object_text += "\nExclusividade: acordada, nos termos da proposta aceita."
    elif exclusivity is False:
        object_text += "\nExclusividade: não há."

    before = [
        (
            "DAS PARTES",
            f"{_party_line('CONTRATANTE', terms['company'], creator=False)}\n"
            f"{_party_line('CONTRATADO(A)', terms['creator'], creator=True)}\n"
            "As partes se identificam, para todos os efeitos, pelas contas autenticadas na plataforma ConectaAI que assinam eletronicamente este contrato.",
        ),
        ("DO OBJETO", object_text),
        (
            "DO VALOR E DO PAGAMENTO",
            f"O CONTRATANTE pagará ao CONTRATADO(A) o valor total de {_money(terms['price'])} pelas entregas descritas neste contrato.\n"
            "A forma e a data do pagamento não constam da proposta aceita e deverão ser ajustadas por escrito entre as partes. "
            "Na ausência de ajuste, o pagamento será devido após a publicação e a aprovação do conteúdo entregue.",
        ),
        ("DO PRAZO", _deadline_text(terms)),
    ]
    after = [
        (
            "DAS DISPOSIÇÕES GERAIS",
            "Este contrato reflete o que as partes acordaram na proposta aceita na plataforma ConectaAI. Qualquer alteração só vale se feita por escrito e aceita pelas duas partes.\n"
            "As controvérsias serão resolvidas, sempre que possível, de forma amigável; persistindo, serão submetidas ao foro competente nos termos da lei.",
        ),
        (
            "DA ASSINATURA ELETRÔNICA",
            "As partes concordam em celebrar este contrato por meio eletrônico e reconhecem como válida a assinatura eletrônica simples feita na plataforma ConectaAI, "
            "por meio de conta autenticada, nos termos do art. 10, §2º, da Medida Provisória nº 2.200-2/2001 e do art. 4º, inciso I, da Lei nº 14.063/2020.\n"
            "Cada assinatura registra o nome e o e-mail do signatário, a data e a hora, o endereço IP e o código de integridade (hash) da versão do texto aceita. "
            "O contrato só produz efeitos depois de assinado pelas duas partes.",
        ),
    ]
    return before, after


_TEMPLATE_CLAUSES: List[Tuple[str, str]] = [
    (
        "DAS OBRIGAÇÕES DO CONTRATADO",
        "O CONTRATADO(A) compromete-se a produzir e publicar o conteúdo descrito neste contrato, respeitando o briefing e o prazo acordados, e a comunicar "
        "prontamente ao CONTRATANTE qualquer impedimento que possa afetar a entrega.\n"
        "O CONTRATADO(A) responde pela originalidade do conteúdo e declara possuir os direitos sobre músicas, imagens e demais materiais que utilizar.",
    ),
    (
        "DAS OBRIGAÇÕES DO CONTRATANTE",
        "O CONTRATANTE compromete-se a fornecer as informações, os materiais e os produtos necessários à criação do conteúdo, a responder em prazo razoável "
        "às solicitações do CONTRATADO(A) e a efetuar o pagamento nos termos deste contrato.",
    ),
    (
        "DA APROVAÇÃO E DOS AJUSTES DO CONTEÚDO",
        "Quando o CONTRATANTE solicitar, o conteúdo será submetido à sua aprovação antes da publicação. Ajustes razoáveis, compatíveis com o briefing original, "
        "serão feitos pelo CONTRATADO(A) sem custo adicional.\n"
        "Alterações que mudem o escopo acordado dependem de novo acordo entre as partes, por escrito.",
    ),
    (
        "DOS DIREITOS DE IMAGEM E DA PROPRIEDADE INTELECTUAL",
        "O CONTRATADO(A) autoriza o CONTRATANTE a divulgar o conteúdo produzido em seus canais, pelo período em que ele permanecer publicado.\n"
        "Qualquer outro uso, como anúncios pagos, mídia externa ou prorrogação do uso, depende de autorização expressa e de acordo específico entre as partes. "
        "Os direitos autorais sobre o conteúdo permanecem com o CONTRATADO(A), salvo disposição diferente acordada por escrito.",
    ),
    (
        "DA IDENTIFICAÇÃO PUBLICITÁRIA",
        "O CONTRATADO(A) deverá identificar de forma clara que o conteúdo é publicidade, conforme as normas de publicidade e as regras de cada plataforma, "
        "e declarar apenas o que corresponda à sua experiência real com o produto ou serviço divulgado.",
    ),
    (
        "DA CONFIDENCIALIDADE E DA PROTEÇÃO DE DADOS",
        "As partes manterão sigilo sobre as informações não públicas trocadas em razão deste contrato e tratarão dados pessoais somente para a sua execução, "
        "em conformidade com a Lei Geral de Proteção de Dados (Lei nº 13.709/2018).",
    ),
    (
        "DA RESCISÃO",
        "Qualquer das partes pode rescindir este contrato em caso de descumprimento pela outra, mediante comunicação por escrito e, quando possível, prazo "
        "razoável para correção.\n"
        "Havendo rescisão, o CONTRATANTE pagará apenas pelo que já tiver sido entregue e aprovado, e o CONTRATADO(A) devolverá os materiais fornecidos "
        "pelo CONTRATANTE que não tenham sido utilizados.",
    ),
]

_CLAUSES_SYSTEM = """Você redige as cláusulas gerais de um contrato de prestação de serviços de criação de conteúdo publicitário
entre uma empresa (CONTRATANTE) e um criador de conteúdo / influenciador digital (CONTRATADO(A)), em português do Brasil,
em linguagem jurídica clara e acessível. Você recebe os termos já acordados (JSON) apenas para adaptar o texto ao caso
(formatos de conteúdo, tipo de campanha, exclusividade).

Escreva exatamente estas cláusulas, nesta ordem. Cada uma tem "heading" (título curto em MAIÚSCULAS, sem numeração) e
"text" (1 a 3 parágrafos curtos, sem listas com marcadores):
1. DAS OBRIGAÇÕES DO CONTRATADO
2. DAS OBRIGAÇÕES DO CONTRATANTE
3. DA APROVAÇÃO E DOS AJUSTES DO CONTEÚDO
4. DOS DIREITOS DE IMAGEM E DA PROPRIEDADE INTELECTUAL
5. DA IDENTIFICAÇÃO PUBLICITÁRIA
6. DA CONFIDENCIALIDADE E DA PROTEÇÃO DE DADOS
7. DA RESCISÃO

Regras invioláveis:
- NÃO escreva nenhum dígito: nada de números, valores em reais, percentuais, prazos em dias, multas, datas, quantidades nem
  números de lei. Para se referir a valor, prazo ou entregas, escreva "conforme definido neste contrato" ou "nos termos acordados".
- Não crie obrigações financeiras novas (multas, adiantamentos, bônus, reajustes) e não altere nem contradiga os termos acordados.
- Não invente dados das partes. Chame-as sempre de CONTRATANTE e CONTRATADO(A).
- Não repita as cláusulas de partes, objeto, valor, prazo, assinatura eletrônica ou foro: o sistema já as inclui."""


def _clean_heading(heading: str) -> str:
    # Tira numeração que o modelo possa ter posto ("1.", "CLÁUSULA 3ª –").
    cleaned = re.sub(r"^\s*(cl[áa]usula\s*)?[\d.ºª\s–—-]*", "", heading, flags=re.IGNORECASE).strip()
    return cleaned.upper()


def _validated_ai_clauses(parsed: ContractClauses) -> Optional[List[Tuple[str, str]]]:
    """Cláusulas da IA que passam na validação, ou None (cai no texto-padrão).
    Qualquer dígito invalida: é a trava contra valores, prazos ou multas inventados."""
    clauses: List[Tuple[str, str]] = []
    for clause in parsed.clauses:
        heading, text = (clause.heading or "").strip(), (clause.text or "").strip()
        if not heading or not text:
            return None
        if re.search(r"\d", heading) or re.search(r"\d", text):
            return None
        if len(heading) > _MAX_HEADING_CHARS or len(text) > _MAX_CLAUSE_CHARS:
            return None
        clauses.append((_clean_heading(heading), text))
    if not (_MIN_AI_CLAUSES <= len(clauses) <= _MAX_AI_CLAUSES):
        return None
    return clauses


def _generate_clauses(db: Session, terms: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], str]:
    """(cláusulas gerais, origem) — origem "gemini" ou "template"."""
    if not settings.GEMINI_API_KEYS:
        return _TEMPLATE_CLAUSES, "template"
    briefing = {
        "campanha": terms.get("campaign_name"),
        "entregas": _deliverables_text(terms),
        "exclusividade": terms.get("exclusivity"),
        "descricao": (terms.get("objective") or "")[:500],
    }
    user_content = "Termos acordados (apenas para contextualizar o texto):\n" + json.dumps(briefing, ensure_ascii=False)
    # Os dígitos do briefing não podem influenciar a regra "sem números": o modelo só vê o contexto.
    result = gemini_client.generate_json(
        system_instruction=_CLAUSES_SYSTEM,
        user_content=user_content,
        response_model=ContractClauses,
        deadline_s=_AI_DEADLINE_S,
        temperature=0.3,
        max_output_tokens=3000,
    )
    if result is None:
        return _TEMPLATE_CLAUSES, "template"
    AiCallLogRepository.create(
        db,
        purpose="contract_draft",
        model=settings.GEMINI_MODEL,
        key_index=result.key_index,
        attempt=result.attempt,
        status=result.status,
        latency_ms=result.latency_ms,
        prompt=f"{_CLAUSES_SYSTEM}\n{user_content}",
        response_raw=result.raw_text,
        error=result.error,
    )
    if result.parsed is None:
        return _TEMPLATE_CLAUSES, "template"
    validated = _validated_ai_clauses(result.parsed)
    if validated is None:
        return _TEMPLATE_CLAUSES, "template"
    return validated, "gemini"


def build_content(terms: Dict[str, Any], clauses: List[Tuple[str, str]]) -> Dict[str, Any]:
    before, after = _fixed_sections(terms)
    sections = []
    for number, (heading, text) in enumerate(before + clauses + after, start=1):
        sections.append({"heading": f"CLÁUSULA {number}ª – {heading}", "text": text})
    return {"title": CONTRACT_TITLE, "sections": sections}


def compute_hash(content: Dict[str, Any]) -> str:
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Ciclo de vida --------------------------------------------------------------


def _company_of(db: Session, user_id: str):
    return CompanyRepository.get_by_user_id(db, user_id)


def create_for_proposal(db: Session, proposal_id: str, company_user_id: str) -> ContractDB:
    proposal = db.query(ProposalDB).filter(ProposalDB.id == proposal_id).first()
    company = _company_of(db, company_user_id)
    if proposal is None or company is None or proposal.company_id != company.id:
        raise ContractError(404, "Proposta não encontrada")
    if proposal.status != "accepted":
        raise ContractError(400, "O contrato só pode ser gerado depois que a proposta for aceita")

    existing = ContractRepository.get_by_proposal_id(db, proposal.id)
    if existing is not None:
        return existing

    terms = collect_terms(db, proposal)
    clauses, source = _generate_clauses(db, terms)
    content = build_content(terms, clauses)
    agreement = _agreement_for(db, proposal)
    try:
        contract = ContractRepository.create(
            db,
            {
                "proposal_id": proposal.id,
                "agreement_id": agreement.id if agreement is not None else None,
                "company_id": proposal.company_id,
                "creator_id": proposal.creator_id,
                "campaign_id": proposal.campaign_id,
                "title": content["title"],
                "terms": terms,
                "content": content,
                "content_hash": compute_hash(content),
                "source": source,
            },
        )
    except IntegrityError:  # dois cliques ao mesmo tempo: o outro pedido já criou
        db.rollback()
        existing = ContractRepository.get_by_proposal_id(db, proposal.id)
        if existing is None:
            raise
        return existing

    creator = CreatorRepository.get_by_id(db, proposal.creator_id)
    if creator:
        NotificationRepository.create(
            db,
            user_id=creator.user_id,
            type_="proposal",
            title="Contrato disponível para assinatura",
            message=f'{terms["company"]["name"] or "A empresa"} gerou o contrato da campanha "{terms["campaign_name"] or "acordada"}". Leia e assine em Propostas.',
        )
    return contract


def _signed_at_text(signed_at: datetime) -> str:
    return signed_at.replace(tzinfo=None, microsecond=0).isoformat()


def signature_code(contract_id: str, role: str, user_id: str, content_hash: str, signed_at: datetime) -> str:
    message = "|".join([contract_id, role, user_id, content_hash, _signed_at_text(signed_at)])
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()[:32].upper()


def sign(db: Session, contract: ContractDB, *, user_id: str, role: str, profile_id: str, content_hash: str, ip: str, user_agent: str) -> ContractDB:
    own_id = contract.creator_id if role == "creator" else contract.company_id
    if own_id != profile_id:
        raise ContractError(403, "Esse contrato não é seu")
    if contract.status == "signed":
        raise ContractError(400, "Esse contrato já foi assinado pelas duas partes")
    if any(s.role == role for s in contract.signatures):
        raise ContractError(400, "Você já assinou esse contrato")
    if compute_hash(contract.content or {}) != contract.content_hash:
        raise ContractError(409, "A integridade do contrato não pôde ser confirmada. Fale com o suporte.")
    if content_hash != contract.content_hash:
        raise ContractError(409, "O contrato foi alterado desde que você abriu. Releia a versão atual antes de assinar.")

    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user is None:
        raise ContractError(401, "Usuário não encontrado")

    signed_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    signature = ContractSignatureDB(
        contract_id=contract.id,
        role=role,
        user_id=user.id,
        signer_name=user.name,
        signer_email=user.email,
        signed_at=signed_at,
        ip_address=(ip or "")[:64],
        user_agent=(user_agent or "")[:255],
        content_hash=contract.content_hash,
        signature_code=signature_code(contract.id, role, user.id, contract.content_hash, signed_at),
    )
    db.add(signature)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ContractError(400, "Você já assinou esse contrato")
    db.refresh(contract)

    both_signed = {s.role for s in contract.signatures} == {"company", "creator"}
    if both_signed:
        contract = ContractRepository.update(db, contract, {"status": "signed"})

    _notify_after_signature(db, contract, signer_role=role, signer_name=user.name, both_signed=both_signed)
    return contract


def _notify_after_signature(db: Session, contract: ContractDB, *, signer_role: str, signer_name: str, both_signed: bool) -> None:
    campaign = (contract.terms or {}).get("campaign_name") or "campanha"
    company = CompanyRepository.get_by_id(db, contract.company_id)
    creator = CreatorRepository.get_by_id(db, contract.creator_id)
    if both_signed:
        for recipient in (company, creator):
            if recipient:
                NotificationRepository.create(
                    db,
                    user_id=recipient.user_id,
                    type_="proposalAccepted",
                    title="Contrato assinado!",
                    message=f'O contrato da campanha "{campaign}" foi assinado pelas duas partes.',
                )
        return
    other = creator if signer_role == "company" else company
    if other:
        NotificationRepository.create(
            db,
            user_id=other.user_id,
            type_="proposal",
            title="Falta a sua assinatura no contrato",
            message=f'{signer_name} assinou o contrato da campanha "{campaign}". Falta a sua assinatura.',
        )


def integrity_ok(contract: ContractDB) -> bool:
    """O texto guardado ainda bate com o hash e cada assinatura atesta esse
    mesmo hash e tem o código (HMAC) correto."""
    if compute_hash(contract.content or {}) != contract.content_hash:
        return False
    for s in contract.signatures:
        if s.content_hash != contract.content_hash:
            return False
        if s.signature_code != signature_code(contract.id, s.role, s.user_id, s.content_hash, s.signed_at):
            return False
    return True
