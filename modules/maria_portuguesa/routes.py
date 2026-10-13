# Arquivo: modules/maria_portuguesa/routes.py
import hashlib
import uuid
from typing import Callable, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, WebSocket
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modules.maria_portuguesa import catalogo
from modules.maria_portuguesa.database import SessionLocal, get_db
from modules.maria_portuguesa.firebase_auth import TokenFirebaseInvalido, VerificadorFirebase
from modules.maria_portuguesa.live_service import AssistenteVoz
from modules.maria_portuguesa.models import SENHA_NO_FIREBASE, MpDesbloqueioDB, MpDomesticaDB
from modules.maria_portuguesa.pagamentos import (
    PRECO_CONTACTO_CENTIMOS, ErroPagamento, Pagamentos, PagamentosIndisponiveis, SessaoPagamento, url_de_retorno,
)
from modules.maria_portuguesa.schemas import (
    BuscaRequest, CheckoutContactoRequest, ConfirmarContactoRequest, EntrarFirebaseRequest, LoginRequest,
    PerfilUpdate, RegistoRequest, campos_em_falta, descrever_em_falta, motivo_sem_contacto, perfil_privado,
    perfil_publico, tem_contacto,
)
from modules.maria_portuguesa.search_service import BuscaService
from modules.maria_portuguesa.security import (
    criar_sessao, domestica_atual, encerrar_sessao, gerar_hash_senha, token_atual, verificar_senha,
)
from services.s3_service import upload_file_to_s3

router = APIRouter(prefix="/maria-portuguesa", tags=["Maria Portuguesa"])

_TAMANHO_MAX_FOTO = 5 * 1024 * 1024


def get_fabrica_db() -> Callable[[], Session]:
    """A sessão de voz dura minutos; em vez de prender uma ligação do pool o tempo
    todo, recebe a fábrica e abre uma sessão curta por pesquisa."""
    return SessionLocal


@router.get("/catalogo")
def obter_catalogo():
    return catalogo.como_dict()


# ── Conta da doméstica ───────────────────────────────────────────────────────

@router.post("/domesticas/registo", status_code=201)
def registar(pedido: RegistoRequest, db: Session = Depends(get_db)):
    domestica = MpDomesticaDB(
        nome=pedido.nome,
        email=pedido.email,
        senha_hash=gerar_hash_senha(pedido.senha),
    )
    db.add(domestica)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma conta com este email.")
    db.refresh(domestica)
    print(f"🧹 [MariaPortuguesa] Novo registo: {domestica.gid}")
    return {"token": criar_sessao(db, domestica), "perfil": perfil_privado(domestica)}


@router.post("/domesticas/login")
def iniciar_sessao(pedido: LoginRequest, db: Session = Depends(get_db)):
    domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.email == pedido.email).first()
    if domestica is None or not verificar_senha(pedido.senha, domestica.senha_hash):
        raise HTTPException(status_code=401, detail="Email ou palavra-passe incorretos.")
    return {"token": criar_sessao(db, domestica), "perfil": perfil_privado(domestica)}


@router.post("/domesticas/firebase")
def entrar_com_firebase(pedido: EntrarFirebaseRequest, db: Session = Depends(get_db)):
    """Criar perfil e iniciar sessão com o Firebase Authentication: troca o ID token
    do Firebase por uma sessão deste servidor. Com nome e consentimento cria o
    perfil se ainda não existir; sem eles só entra em perfis que já existam."""
    try:
        conta = VerificadorFirebase.verificar(pedido.id_token)
    except TokenFirebaseInvalido as e:
        print(f"⚠️ [MariaPortuguesa] ID token do Firebase recusado: {e}")
        raise HTTPException(status_code=401, detail="Não foi possível confirmar a sua conta. Inicie sessão novamente.")

    domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.firebase_uid == conta.uid).first()
    if domestica is None:
        domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.email == conta.email).first()
        if domestica is not None:
            # Conta anterior ao Firebase com o mesmo email. Só é ligada se o Firebase
            # confirmou que o email é de quem entra — sem isso, bastava criar uma conta
            # Firebase com o email de outra pessoa para lhe ficar com o perfil.
            if domestica.firebase_uid is not None or not conta.email_verificado:
                raise HTTPException(status_code=409, detail="Já existe uma conta com este email.")
            domestica.firebase_uid = conta.uid
            db.commit()

    if domestica is None:
        if pedido.nome is None:
            raise HTTPException(status_code=404, detail="Ainda não existe um perfil para esta conta. Crie o seu perfil.")
        if not pedido.consentimento_rgpd:
            raise HTTPException(
                status_code=422, detail="É necessário aceitar o tratamento dos dados para criar o perfil.",
            )
        domestica = MpDomesticaDB(
            nome=pedido.nome, email=conta.email, firebase_uid=conta.uid, senha_hash=SENHA_NO_FIREBASE,
        )
        db.add(domestica)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Já existe uma conta com este email.")
        db.refresh(domestica)
        print(f"🧹 [MariaPortuguesa] Novo registo (Firebase): {domestica.gid}")
        return {"token": criar_sessao(db, domestica), "perfil": perfil_privado(domestica), "criado": True}

    return {"token": criar_sessao(db, domestica), "perfil": perfil_privado(domestica), "criado": False}


@router.post("/domesticas/sair", status_code=204)
def terminar_sessao(token: str = Depends(token_atual), db: Session = Depends(get_db)):
    encerrar_sessao(db, token)


# ── Perfil da própria ────────────────────────────────────────────────────────

@router.get("/domesticas/eu")
def obter_o_meu_perfil(domestica: MpDomesticaDB = Depends(domestica_atual)):
    return perfil_privado(domestica)


@router.put("/domesticas/eu")
def atualizar_o_meu_perfil(
        pedido: PerfilUpdate,
        domestica: MpDomesticaDB = Depends(domestica_atual),
        db: Session = Depends(get_db),
):
    alteracoes = pedido.model_dump(exclude_unset=True)
    if "nome" in alteracoes and not alteracoes["nome"]:
        raise HTTPException(status_code=422, detail="O nome não pode ficar vazio.")

    for campo, valor in alteracoes.items():
        # Listas e interruptores nunca ficam a NULL: "limpar" é lista vazia / falso.
        if valor is None and isinstance(getattr(domestica, campo), (list, bool)):
            valor = [] if isinstance(getattr(domestica, campo), list) else False
        setattr(domestica, campo, valor)

    if domestica.publicado:
        em_falta = campos_em_falta(domestica)
        if em_falta:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail=f"Para publicar o perfil falta indicar {descrever_em_falta(em_falta)}.",
            )

    try:
        BuscaService.indexar(domestica)
    except Exception as e:
        # O perfil é guardado na mesma; a busca indexa-o na primeira pesquisa.
        print(f"⚠️ [MariaPortuguesa] Não foi possível indexar o perfil {domestica.gid}: {e}")
        domestica.embedding = None

    db.commit()
    db.refresh(domestica)
    return perfil_privado(domestica)


@router.post("/domesticas/eu/foto")
async def enviar_foto(
        file: UploadFile = File(...),
        domestica: MpDomesticaDB = Depends(domestica_atual),
        db: Session = Depends(get_db),
):
    # O s3_service grava tudo como image/jpeg, por isso só se aceita JPEG (o
    # frontend recorta e converte a fotografia antes de a enviar).
    if file.content_type != "image/jpeg":
        raise HTTPException(status_code=415, detail="A fotografia tem de estar em formato JPEG.")
    if file.size is not None and file.size > _TAMANHO_MAX_FOTO:
        raise HTTPException(status_code=413, detail="A fotografia não pode ter mais de 5 MB.")

    nome_ficheiro = f"{domestica.gid}-{uuid.uuid4().hex[:8]}.jpg"
    url = upload_file_to_s3(file.file, nome_ficheiro, folder="MariaPortuguesa")
    if not url:
        raise HTTPException(status_code=500, detail="Não foi possível guardar a fotografia.")

    domestica.foto_url = url
    db.commit()
    return {"foto_url": url}


@router.delete("/domesticas/eu", status_code=204)
def apagar_a_minha_conta(domestica: MpDomesticaDB = Depends(domestica_atual), db: Session = Depends(get_db)):
    """Direito ao apagamento (RGPD, art.º 17.º): remove a conta, o perfil e as sessões."""
    print(f"🗑️ [MariaPortuguesa] Conta apagada a pedido da própria: {domestica.gid}")
    db.delete(domestica)
    db.commit()


# ── Lado do contratante ──────────────────────────────────────────────────────

@router.get("/domesticas")
def listar_perfis(
        distrito: Optional[str] = None,
        servico: Optional[str] = None,
        limite: int = Query(default=12, ge=1, le=24),
        db: Session = Depends(get_db),
):
    return BuscaService.procurar(
        db, distrito=distrito, servicos=[servico] if servico else None, limite=limite,
    )


@router.get("/domesticas/{gid}")
def obter_perfil(gid: str, db: Session = Depends(get_db)):
    domestica = db.query(MpDomesticaDB).filter(
        MpDomesticaDB.gid == gid, MpDomesticaDB.publicado.is_(True),
    ).first()
    if domestica is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    return perfil_publico(domestica)


# ── Contacto pago ────────────────────────────────────────────────────────────

_PAGAMENTOS_EM_BAIXO = "Os pagamentos não estão disponíveis de momento. Tente de novo mais tarde."


def _hash_sessao_pagamento(sessao_id: str) -> str:
    return hashlib.sha256(sessao_id.encode("utf-8")).hexdigest()


def _registar_desbloqueio(db: Session, sessao: SessaoPagamento) -> Optional[MpDesbloqueioDB]:
    """Guarda o contacto como pago (uma vez por sessão de pagamento). None se a
    sessão não estiver paga, não for deste módulo ou o perfil já não existir."""
    if not sessao.pago or not sessao.gid:
        return None
    domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.gid == sessao.gid).first()
    if domestica is None:
        return None
    marca = _hash_sessao_pagamento(sessao.id)
    desbloqueio = MpDesbloqueioDB(
        sessao_hash=marca, domestica_id=domestica.id,
        valor_centimos=sessao.valor_centimos, payment_intent=sessao.payment_intent,
    )
    db.add(desbloqueio)
    try:
        db.commit()
    except IntegrityError:
        # O aviso da Stripe e o regresso da pessoa chegaram ao mesmo tempo.
        db.rollback()
        return db.query(MpDesbloqueioDB).filter(MpDesbloqueioDB.sessao_hash == marca).first()
    print(f"💶 [MariaPortuguesa] Contacto pago: perfil {domestica.gid}")
    return desbloqueio


_SEM_CONTACTO = {
    "sem_contacto": "Esta profissional ainda não registou um contacto.",
    "nao_partilhado": "Esta profissional não está a partilhar o contacto de momento.",
}


def _perfil_publicado(db: Session, gid: str) -> MpDomesticaDB:
    domestica = db.query(MpDomesticaDB).filter(
        MpDomesticaDB.gid == gid, MpDomesticaDB.publicado.is_(True),
    ).first()
    if domestica is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    return domestica


@router.get("/domesticas/{gid}/contacto")
def verificar_contacto(gid: str, db: Session = Depends(get_db)):
    """Diz se há contacto para ver neste perfil, sem o mostrar. O frontend pergunta
    antes de propor o pagamento: o perfil que tem no ecrã pode já estar desatualizado."""
    motivo = motivo_sem_contacto(_perfil_publicado(db, gid))
    return {"disponivel": motivo is None, "motivo": motivo, "preco_centimos": PRECO_CONTACTO_CENTIMOS}


@router.post("/domesticas/{gid}/contacto/checkout")
def pagar_contacto(gid: str, pedido: CheckoutContactoRequest, db: Session = Depends(get_db)):
    """Abre a página de pagamento da Stripe para ver o contacto deste perfil."""
    domestica = _perfil_publicado(db, gid)
    motivo = motivo_sem_contacto(domestica)
    if motivo is not None:
        # Nunca se cobra por um contacto que não existe ou que a própria não autorizou a mostrar.
        raise HTTPException(status_code=409, detail=_SEM_CONTACTO[motivo])
    try:
        url = Pagamentos.criar_checkout(gid=domestica.gid, nome=domestica.nome, retorno=url_de_retorno(pedido.retorno))
    except PagamentosIndisponiveis as e:
        print(f"⚠️ [MariaPortuguesa] Pagamentos por configurar: {e}")
        raise HTTPException(status_code=503, detail=_PAGAMENTOS_EM_BAIXO)
    except ErroPagamento as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"url": url}


@router.post("/contactos/confirmar")
def confirmar_contacto(pedido: ConfirmarContactoRequest, db: Session = Depends(get_db)):
    """Entrega o contacto a quem o pagou. Serve no regresso da Stripe e sempre que a
    pessoa volta a abrir um perfil que já pagou (o navegador guarda o id da sessão)."""
    desbloqueio = db.query(MpDesbloqueioDB).filter(
        MpDesbloqueioDB.sessao_hash == _hash_sessao_pagamento(pedido.sessao),
    ).first()
    if desbloqueio is None:
        try:
            sessao = Pagamentos.consultar(pedido.sessao)
        except PagamentosIndisponiveis as e:
            print(f"⚠️ [MariaPortuguesa] Pagamentos por configurar: {e}")
            raise HTTPException(status_code=503, detail=_PAGAMENTOS_EM_BAIXO)
        except ErroPagamento:
            raise HTTPException(status_code=404, detail="Não encontrámos este pagamento.")
        if sessao.gid and not sessao.pago:
            raise HTTPException(status_code=402, detail="O pagamento ainda não foi confirmado.")
        desbloqueio = _registar_desbloqueio(db, sessao)
        if desbloqueio is None:
            raise HTTPException(status_code=404, detail="Não encontrámos este pagamento.")

    domestica = desbloqueio.domestica
    if not tem_contacto(domestica):
        # Pagou, mas entretanto a profissional retirou o contacto: não se mostra.
        raise HTTPException(
            status_code=409,
            detail="Esta profissional deixou de partilhar o contacto. Fale connosco para ser reembolsado.",
        )
    return {"perfil": perfil_publico(domestica), "telemovel": domestica.telemovel}


@router.post("/stripe/webhook")
async def webhook_stripe(request: Request, stripe_signature: Optional[str] = Header(default=None),
                         db: Session = Depends(get_db)):
    """Aviso da Stripe de que um pagamento ficou concluído. Garante que o contacto
    fica registado como pago mesmo que a pessoa feche o separador antes de voltar."""
    try:
        sessao = Pagamentos.evento_do_webhook(await request.body(), stripe_signature)
    except PagamentosIndisponiveis:
        raise HTTPException(status_code=503, detail="Webhook por configurar.")
    except ErroPagamento:
        raise HTTPException(status_code=400, detail="Assinatura inválida.")
    if sessao is not None:
        _registar_desbloqueio(db, sessao)
    return {"recebido": True}


@router.post("/busca")
def procurar(pedido: BuscaRequest, db: Session = Depends(get_db)):
    return BuscaService.procurar(
        db,
        consulta=pedido.consulta,
        localidade=pedido.localidade,
        distrito=pedido.distrito,
        servicos=pedido.servicos,
        dias=pedido.dias,
        preco_max=pedido.preco_max,
        limite=pedido.limite,
    )


@router.websocket("/voz")
async def conversa_por_voz(websocket: WebSocket, fabrica_db: Callable = Depends(get_fabrica_db)):
    await AssistenteVoz.atender(websocket, fabrica_db)
