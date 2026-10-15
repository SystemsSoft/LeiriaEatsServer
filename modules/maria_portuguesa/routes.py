# Arquivo: modules/maria_portuguesa/routes.py
import uuid
from typing import Callable, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, WebSocket
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modules.maria_portuguesa import acordos, catalogo, familias
from modules.maria_portuguesa.database import SessionLocal, get_db
from modules.maria_portuguesa.firebase_auth import TokenFirebaseInvalido, VerificadorFirebase
from modules.maria_portuguesa.live_service import AssistenteVoz
from modules.maria_portuguesa.models import SENHA_NO_FIREBASE, MpDomesticaDB
from modules.maria_portuguesa.pagamentos import AvisoInvalido, ErroPagamento, Pagamentos, PagamentosIndisponiveis
from modules.maria_portuguesa.schemas import (
    BuscaRequest, EntrarFirebaseRequest, LoginRequest, PerfilUpdate, RegistoRequest, campos_em_falta,
    descrever_em_falta, perfil_privado, perfil_publico,
)
from modules.maria_portuguesa.search_service import BuscaService
from modules.maria_portuguesa.security import (
    criar_sessao, domestica_atual, encerrar_sessao, gerar_hash_senha, token_atual, verificar_senha,
)
from services.s3_service import upload_file_to_s3

router = APIRouter(prefix="/maria-portuguesa", tags=["Maria Portuguesa"])


@router.on_event("startup")
async def _arrancar_libertacao_automatica():
    # Corre dentro do processo do Leiria Eats: o main.py junta este arranque ao seu.
    acordos.iniciar_libertacao(SessionLocal)

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
def obter_o_meu_perfil(domestica: MpDomesticaDB = Depends(domestica_atual), db: Session = Depends(get_db)):
    if domestica.stripe_conta_id and not domestica.recebimentos_ativos:
        # A meio da ativação na Stripe (ou de volta dela): vê se já ficou pronta.
        acordos.atualizar_recebimentos(db, domestica)
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
    """Direito ao apagamento (RGPD, art.º 17.º): remove a conta, o perfil e as sessões.
    Os acordos ficam (são registos de pagamentos), sem ligação à conta. Com dinheiro
    retido por libertar não se apaga: a transferência precisa da conta."""
    if acordos.tem_pagamentos_retidos(domestica.acordos):
        raise HTTPException(
            status_code=409,
            detail="Tem pagamentos de famílias por receber. Poderá apagar a conta depois de os receber.",
        )
    acordos.fechar_pendentes(domestica.acordos)
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


# ── Avisos da Stripe ─────────────────────────────────────────────────────────

@router.post("/stripe/webhook")
async def webhook_stripe(request: Request, stripe_signature: Optional[str] = Header(default=None),
                         db: Session = Depends(get_db)):
    """Avisos da Stripe: acordo pago, pagamento devolvido, conta de recebimentos
    alterada. Rede de segurança de quem fecha a página antes de voltar da Stripe."""
    try:
        aviso = Pagamentos.evento_do_webhook(await request.body(), stripe_signature)
    except PagamentosIndisponiveis:
        raise HTTPException(status_code=503, detail="Webhook por configurar.")
    except AvisoInvalido:
        raise HTTPException(status_code=400, detail="Assinatura inválida.")
    except ErroPagamento:
        # Não se conseguiu perguntar à Stripe o estado atual: ela volta a entregar o aviso.
        raise HTTPException(status_code=502, detail="Estado do pagamento indisponível.")
    if aviso is None:
        return {"recebido": True}
    if aviso.tipo == "pago":
        acordos.aplicar_aviso_pago(db, aviso.checkout)
    elif aviso.tipo == "reembolsado":
        acordos.aplicar_aviso_reembolso(db, aviso.pagamento_id)
    elif aviso.tipo == "conta":
        acordos.aplicar_aviso_conta(db, aviso.conta_id)
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


# Conta de quem procura (familias.py) e acordos (acordos.py), debaixo do mesmo
# /maria-portuguesa.
router.include_router(familias.router)
router.include_router(acordos.router_familia)
router.include_router(acordos.router_domestica)


@router.websocket("/voz")
async def conversa_por_voz(websocket: WebSocket, fabrica_db: Callable = Depends(get_fabrica_db)):
    await AssistenteVoz.atender(websocket, fabrica_db)
