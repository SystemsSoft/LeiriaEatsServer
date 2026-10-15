# Arquivo: modules/maria_portuguesa/acordos.py
#
# Acordos entre famílias e domésticas, e o pagamento deles pela plataforma.
#
#   proposto ──(doméstica aceita)──▶ aceite ──(família paga)──▶ pago ──▶ libertado
#      │                               │                          │
#      ├─(doméstica recusa)▶ recusado  └─(família desiste)▶ cancelado
#      └─(família desiste)─▶ cancelado                            └─(problema)▶ em_disputa
#
# Pago, o dinheiro fica retido na plataforma. Segue para a doméstica quando a
# família o liberta, ou sozinho DIAS_LIBERTACAO dias depois do serviço se a família
# não tiver reportado um problema. Em disputa, a equipa decide na Stripe: devolver
# (o aviso charge.refunded passa o acordo a reembolsado) ou deixar a família libertar.
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from modules.maria_portuguesa.database import get_db
from modules.maria_portuguesa.models import EstadoAcordo, MpAcordoDB, MpDomesticaDB, MpFamiliaDB
from modules.maria_portuguesa.pagamentos import (
    DIAS_LIBERTACAO, ErroPagamento, PagamentoCheckout, Pagamentos, PagamentosIndisponiveis, comissao,
    url_de_retorno,
)
from modules.maria_portuguesa.schemas import (
    AcordoRequest, ConfirmarPagamentoRequest, ProblemaRequest, RetornoRequest, perfil_publico,
)
from modules.maria_portuguesa.security import domestica_atual, familia_atual

router_familia = APIRouter(prefix="/familias/eu/acordos")
router_domestica = APIRouter(prefix="/domesticas/eu")

_PAGAMENTOS_EM_BAIXO = "Os pagamentos não estão disponíveis de momento. Tente de novo mais tarde."


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def erro_de_pagamento(e: Exception) -> HTTPException:
    if isinstance(e, PagamentosIndisponiveis):
        print(f"⚠️ [MariaPortuguesa] Pagamentos por configurar: {e}")
        return HTTPException(status_code=503, detail=_PAGAMENTOS_EM_BAIXO)
    return HTTPException(status_code=502, detail=str(e))


# ── Formato de saída ─────────────────────────────────────────────────────────

def _comum(a: MpAcordoDB) -> dict:
    return {
        "gid": a.gid,
        "estado": a.estado,
        "descricao": a.descricao,
        "data_servico": a.data_servico.isoformat(),
        "valor_centimos": a.valor_centimos,
        "criado_em": a.criado_em.isoformat() + "Z",
        "pago_em": a.pago_em.isoformat() + "Z" if a.pago_em else None,
        "libertar_em": a.libertar_em.isoformat() if a.libertar_em and a.estado == EstadoAcordo.PAGO else None,
        "libertado_em": a.libertado_em.isoformat() + "Z" if a.libertado_em else None,
        "libertado_automaticamente": bool(a.libertado_automaticamente),
        "motivo_problema": a.motivo_problema,
    }


def acordo_para_familia(a: MpAcordoDB) -> dict:
    """O acordo como a família o vê: com o perfil público da doméstica."""
    dados = _comum(a)
    dados["domestica"] = perfil_publico(a.domestica) if a.domestica else None
    return dados


def acordo_para_domestica(a: MpAcordoDB) -> dict:
    """O acordo como a doméstica o vê: quanto recebe, e da família só o nome, a zona
    e o contacto que ela escolheu partilhar."""
    dados = _comum(a)
    f = a.familia
    dados.update({
        "comissao_centimos": a.comissao_centimos,
        "a_receber_centimos": a.a_receber_centimos,
        "familia": {"nome": f.nome, "distrito": f.distrito, "concelho": f.concelho,
                    "contacto": a.contacto_familia} if f else None,
    })
    return dados


def _ordenados(acordos: list[MpAcordoDB]) -> list[MpAcordoDB]:
    return sorted(acordos, key=lambda a: a.criado_em, reverse=True)


# ── Regras ───────────────────────────────────────────────────────────────────

def tem_pagamentos_retidos(acordos: list[MpAcordoDB]) -> bool:
    return any(a.estado in EstadoAcordo.RETIDOS for a in acordos)


def fechar_pendentes(acordos: list[MpAcordoDB]) -> None:
    """Conta apagada: os acordos ainda sem pagamento deixam de poder avançar."""
    for a in acordos:
        if a.estado in (EstadoAcordo.PROPOSTO, EstadoAcordo.ACEITE):
            a.estado = EstadoAcordo.CANCELADO


def atualizar_recebimentos(db: Session, domestica: MpDomesticaDB) -> None:
    """Pergunta à Stripe se a conta da doméstica já pode receber. Falhar não impede
    nada: fica como estava e volta a perguntar-se da próxima vez."""
    if not domestica.stripe_conta_id:
        return
    try:
        pronta = Pagamentos.conta_pronta(domestica.stripe_conta_id)
    except (PagamentosIndisponiveis, ErroPagamento) as e:
        print(f"⚠️ [MariaPortuguesa] Estado da conta de {domestica.gid} indisponível: {e}")
        return
    if pronta != domestica.recebimentos_ativos:
        domestica.recebimentos_ativos = pronta
        db.commit()


def confirmar_pagamento(db: Session, acordo: MpAcordoDB, checkout: PagamentoCheckout) -> bool:
    """Regista o pagamento de um acordo. Devolve False se o checkout ainda não está
    pago. Repetir (regresso da Stripe + aviso) não muda nada."""
    if not checkout.pago:
        return False
    if acordo.estado not in (EstadoAcordo.ACEITE, EstadoAcordo.CANCELADO):
        return True  # já registado (ou já seguiu adiante)
    if acordo.estado == EstadoAcordo.CANCELADO:
        # Pagou numa página aberta antes de desistir: o dinheiro entrou, o acordo volta.
        print(f"⚠️ [MariaPortuguesa] Acordo {acordo.gid} pago depois de cancelado; reaberto")
    agora = _agora()
    acordo.estado = EstadoAcordo.PAGO
    acordo.stripe_checkout_id = checkout.sessao
    acordo.stripe_pagamento_id = checkout.pagamento_id
    acordo.stripe_cobranca_id = checkout.cobranca_id
    acordo.pago_em = agora
    acordo.libertar_em = max(acordo.data_servico, agora.date()) + timedelta(days=DIAS_LIBERTACAO)
    db.commit()
    print(f"💶 [MariaPortuguesa] Acordo pago (retido): {acordo.gid}")
    return True


class NaoPodeLibertar(Exception):
    """O pagamento não pode seguir para a doméstica; a mensagem explica porquê."""


def libertar(db: Session, acordo: MpAcordoDB, automaticamente: bool = False) -> None:
    """Transfere para a doméstica o valor menos a comissão. Levanta NaoPodeLibertar
    ou os erros da Stripe."""
    if acordo.estado not in EstadoAcordo.RETIDOS:
        raise NaoPodeLibertar("Este pagamento não está retido.")
    domestica = acordo.domestica
    if domestica is None or not domestica.stripe_conta_id:
        raise NaoPodeLibertar("A conta desta profissional já não existe. Contacte-nos para devolvermos o pagamento.")
    if not acordo.stripe_cobranca_id:
        raise NaoPodeLibertar("Ainda não recebemos a confirmação deste pagamento. Tente de novo dentro de instantes.")
    acordo.stripe_transferencia_id = Pagamentos.transferir(
        conta=domestica.stripe_conta_id, valor_centimos=acordo.a_receber_centimos,
        acordo=acordo.gid, cobranca=acordo.stripe_cobranca_id,
    )
    acordo.estado = EstadoAcordo.LIBERTADO
    acordo.libertado_em = _agora()
    acordo.libertado_automaticamente = automaticamente
    db.commit()
    print(f"💸 [MariaPortuguesa] Pagamento libertado{' (prazo)' if automaticamente else ''}: {acordo.gid}")


def libertar_vencidos(db: Session, hoje: Optional[date] = None) -> dict:
    """Liberta os pagamentos cujo prazo passou sem a família reportar um problema."""
    hoje = hoje or _agora().date()
    vencidos = db.query(MpAcordoDB).filter(
        MpAcordoDB.estado == EstadoAcordo.PAGO, MpAcordoDB.libertar_em <= hoje,
    ).all()
    libertados = falhados = 0
    for acordo in vencidos:
        try:
            libertar(db, acordo, automaticamente=True)
            libertados += 1
        except (NaoPodeLibertar, ErroPagamento, PagamentosIndisponiveis) as e:
            db.rollback()
            falhados += 1
            print(f"⚠️ [MariaPortuguesa] Não foi possível libertar {acordo.gid}: {e}")
    return {"vencidos": len(vencidos), "libertados": libertados, "falhados": falhados}


# ── Libertação automática (tarefa de fundo) ──────────────────────────────────

INTERVALO_LIBERTACAO = timedelta(hours=1)
# Espera depois de o servidor arrancar, para não competir com o arranque do Leiria Eats.
_ATRASO_INICIAL_S = 120
# O servidor de desenvolvimento pode desligá-la.
libertacao_ativa = True
_tarefa: Optional[asyncio.Task] = None


def iniciar_libertacao(fabrica_db: Callable[[], Session]) -> None:
    """Arranca a tarefa, uma só vez por processo: o FastAPI chama o arranque de um
    router incluído mais do que uma vez, e sem isto corriam duas."""
    global _tarefa
    if _tarefa is None or _tarefa.done():
        _tarefa = asyncio.create_task(_libertar_de_hora_a_hora(fabrica_db))


async def _libertar_de_hora_a_hora(fabrica_db: Callable[[], Session]) -> None:
    await asyncio.sleep(_ATRASO_INICIAL_S)
    while True:
        if libertacao_ativa and Pagamentos.configurada():
            try:
                resultado = await asyncio.to_thread(_libertar_com_sessao_propria, fabrica_db)
                if resultado["vencidos"]:
                    print(f"💸 [MariaPortuguesa] Libertação automática: {resultado}")
            except Exception as e:
                # Nunca deixa a tarefa morrer: tenta outra vez na hora seguinte.
                print(f"⚠️ [MariaPortuguesa] A libertação automática falhou: {e}")
        await asyncio.sleep(INTERVALO_LIBERTACAO.total_seconds())


def _libertar_com_sessao_propria(fabrica_db: Callable[[], Session]) -> dict:
    db = fabrica_db()
    try:
        return libertar_vencidos(db)
    finally:
        db.close()


# ── Família ──────────────────────────────────────────────────────────────────

def _da_familia(db: Session, familia: MpFamiliaDB, gid: str) -> MpAcordoDB:
    acordo = db.query(MpAcordoDB).filter(MpAcordoDB.gid == gid, MpAcordoDB.familia_id == familia.id).first()
    if acordo is None:
        raise HTTPException(status_code=404, detail="Acordo não encontrado.")
    return acordo


@router_familia.get("")
def listar_da_familia(familia: MpFamiliaDB = Depends(familia_atual)):
    return [acordo_para_familia(a) for a in _ordenados(familia.acordos)]


@router_familia.post("", status_code=201)
def propor(pedido: AcordoRequest, familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    domestica = db.query(MpDomesticaDB).filter(
        MpDomesticaDB.gid == pedido.domestica, MpDomesticaDB.publicado.is_(True),
    ).first()
    if domestica is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    if not domestica.recebimentos_ativos:
        raise HTTPException(status_code=409, detail=f"{domestica.nome} ainda não recebe pagamentos pela plataforma.")
    if pedido.data_servico < _agora().date():
        raise HTTPException(status_code=422, detail="A data do serviço já passou.")
    acordo = MpAcordoDB(
        familia_id=familia.id, domestica_id=domestica.id, descricao=pedido.descricao,
        contacto_familia=pedido.contacto, data_servico=pedido.data_servico,
        valor_centimos=pedido.valor_centimos, comissao_centimos=comissao(pedido.valor_centimos),
    )
    db.add(acordo)
    db.commit()
    db.refresh(acordo)
    print(f"🤝 [MariaPortuguesa] Acordo proposto: {acordo.gid}")
    return acordo_para_familia(acordo)


@router_familia.post("/confirmar")
def confirmar(pedido: ConfirmarPagamentoRequest, familia: MpFamiliaDB = Depends(familia_atual),
              db: Session = Depends(get_db)):
    """No regresso da Stripe: regista já o pagamento, sem esperar pelo aviso (webhook)."""
    try:
        checkout = Pagamentos.consultar_checkout(pedido.sessao)
    except PagamentosIndisponiveis as e:
        raise erro_de_pagamento(e)
    except ErroPagamento:
        raise HTTPException(status_code=404, detail="Não encontrámos este pagamento.")
    acordo = db.query(MpAcordoDB).filter(
        MpAcordoDB.gid == checkout.acordo, MpAcordoDB.familia_id == familia.id,
    ).first() if checkout.acordo else None
    if acordo is None:
        raise HTTPException(status_code=404, detail="Não encontrámos este pagamento.")
    if not confirmar_pagamento(db, acordo, checkout):
        raise HTTPException(status_code=402, detail="O pagamento ainda não foi confirmado.")
    db.refresh(acordo)
    return acordo_para_familia(acordo)


@router_familia.post("/{gid}/cancelar")
def cancelar(gid: str, familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    acordo = _da_familia(db, familia, gid)
    if acordo.estado not in (EstadoAcordo.PROPOSTO, EstadoAcordo.ACEITE):
        raise HTTPException(status_code=409, detail="Este acordo já não pode ser cancelado.")
    acordo.estado = EstadoAcordo.CANCELADO
    db.commit()
    return acordo_para_familia(acordo)


@router_familia.post("/{gid}/pagar")
def pagar(gid: str, pedido: RetornoRequest, familia: MpFamiliaDB = Depends(familia_atual),
          db: Session = Depends(get_db)):
    """Abre a página de pagamento da Stripe para um acordo aceite."""
    acordo = _da_familia(db, familia, gid)
    try:
        # Uma página aberta antes pode já ter sido paga (e o aviso ainda não chegou):
        # não se cobra duas vezes.
        if acordo.stripe_checkout_id and acordo.estado == EstadoAcordo.ACEITE:
            confirmar_pagamento(db, acordo, Pagamentos.consultar_checkout(acordo.stripe_checkout_id))
        if acordo.estado != EstadoAcordo.ACEITE:
            raise HTTPException(
                status_code=409,
                detail="Este acordo já está pago." if acordo.estado in EstadoAcordo.RETIDOS
                else "Este acordo não está à espera de pagamento.",
            )
        domestica = acordo.domestica
        if domestica is None or not domestica.recebimentos_ativos:
            raise HTTPException(status_code=409, detail="Esta profissional não pode receber pagamentos agora.")
        sessao, url = Pagamentos.criar_checkout(
            acordo=acordo.gid, valor_centimos=acordo.valor_centimos, nome=domestica.nome,
            descricao=f"{acordo.descricao} ({acordo.data_servico.strftime('%d/%m/%Y')})",
            email=familia.email, retorno=url_de_retorno(pedido.retorno),
        )
    except (PagamentosIndisponiveis, ErroPagamento) as e:
        raise erro_de_pagamento(e)
    acordo.stripe_checkout_id = sessao
    db.commit()
    return {"url": url}


@router_familia.post("/{gid}/libertar")
def libertar_pagamento(gid: str, familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    """A família confirma que o serviço foi feito: o pagamento segue para a doméstica."""
    acordo = _da_familia(db, familia, gid)
    try:
        libertar(db, acordo)
    except NaoPodeLibertar as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (PagamentosIndisponiveis, ErroPagamento) as e:
        db.rollback()
        raise erro_de_pagamento(e)
    return acordo_para_familia(acordo)


@router_familia.post("/{gid}/problema")
def reportar_problema(gid: str, pedido: ProblemaRequest, familia: MpFamiliaDB = Depends(familia_atual),
                      db: Session = Depends(get_db)):
    """Algo correu mal: o pagamento deixa de seguir sozinho e a equipa analisa."""
    acordo = _da_familia(db, familia, gid)
    if acordo.estado != EstadoAcordo.PAGO:
        raise HTTPException(status_code=409, detail="Só pode reportar um problema num pagamento retido.")
    acordo.estado = EstadoAcordo.EM_DISPUTA
    acordo.motivo_problema = pedido.motivo
    db.commit()
    print(f"🚩 [MariaPortuguesa] Problema reportado no acordo {acordo.gid} (pagamento {acordo.stripe_pagamento_id})")
    return acordo_para_familia(acordo)


# ── Doméstica ────────────────────────────────────────────────────────────────

@router_domestica.post("/recebimentos/ativar")
def ativar_recebimentos(pedido: RetornoRequest, domestica: MpDomesticaDB = Depends(domestica_atual),
                        db: Session = Depends(get_db)):
    """Abre as páginas da Stripe onde a doméstica indica a identidade e o IBAN.
    Serve também para continuar uma ativação a meio."""
    try:
        if not domestica.stripe_conta_id:
            domestica.stripe_conta_id = Pagamentos.criar_conta(gid=domestica.gid, email=domestica.email)
            db.commit()
        url = Pagamentos.link_de_ativacao(conta=domestica.stripe_conta_id, retorno=url_de_retorno(pedido.retorno))
    except (PagamentosIndisponiveis, ErroPagamento) as e:
        raise erro_de_pagamento(e)
    return {"url": url}


@router_domestica.post("/recebimentos/painel")
def painel_de_recebimentos(domestica: MpDomesticaDB = Depends(domestica_atual)):
    """Painel da Stripe: transferências recebidas e dados bancários."""
    if not domestica.stripe_conta_id or not domestica.recebimentos_ativos:
        raise HTTPException(status_code=409, detail="Ative primeiro os recebimentos.")
    try:
        return {"url": Pagamentos.link_do_painel(domestica.stripe_conta_id)}
    except (PagamentosIndisponiveis, ErroPagamento) as e:
        raise erro_de_pagamento(e)


def _da_domestica(db: Session, domestica: MpDomesticaDB, gid: str) -> MpAcordoDB:
    acordo = db.query(MpAcordoDB).filter(MpAcordoDB.gid == gid, MpAcordoDB.domestica_id == domestica.id).first()
    if acordo is None:
        raise HTTPException(status_code=404, detail="Acordo não encontrado.")
    if acordo.estado != EstadoAcordo.PROPOSTO:
        raise HTTPException(status_code=409, detail="Esta proposta já foi respondida.")
    return acordo


@router_domestica.get("/acordos")
def listar_da_domestica(domestica: MpDomesticaDB = Depends(domestica_atual)):
    return [acordo_para_domestica(a) for a in _ordenados(domestica.acordos)]


@router_domestica.post("/acordos/{gid}/aceitar")
def aceitar(gid: str, domestica: MpDomesticaDB = Depends(domestica_atual), db: Session = Depends(get_db)):
    acordo = _da_domestica(db, domestica, gid)
    if not domestica.recebimentos_ativos:
        raise HTTPException(status_code=409, detail="Ative os recebimentos para poder aceitar acordos.")
    acordo.estado = EstadoAcordo.ACEITE
    acordo.respondido_em = _agora()
    db.commit()
    return acordo_para_domestica(acordo)


@router_domestica.post("/acordos/{gid}/recusar")
def recusar(gid: str, domestica: MpDomesticaDB = Depends(domestica_atual), db: Session = Depends(get_db)):
    acordo = _da_domestica(db, domestica, gid)
    acordo.estado = EstadoAcordo.RECUSADO
    acordo.respondido_em = _agora()
    db.commit()
    return acordo_para_domestica(acordo)


# ── Avisos da Stripe ─────────────────────────────────────────────────────────

def aplicar_aviso_pago(db: Session, checkout: PagamentoCheckout) -> None:
    acordo = db.query(MpAcordoDB).filter(MpAcordoDB.gid == checkout.acordo).first() if checkout.acordo else None
    if acordo is not None:
        confirmar_pagamento(db, acordo, checkout)


def aplicar_aviso_reembolso(db: Session, pagamento_id: Optional[str]) -> None:
    acordo = db.query(MpAcordoDB).filter(MpAcordoDB.stripe_pagamento_id == pagamento_id).first() \
        if pagamento_id else None
    if acordo is None:
        return
    if acordo.estado == EstadoAcordo.LIBERTADO:
        # Devolvido depois de transferido: a equipa recupera a transferência à mão.
        print(f"⚠️ [MariaPortuguesa] Reembolso de um acordo já libertado: {acordo.gid}")
    acordo.estado = EstadoAcordo.REEMBOLSADO
    db.commit()
    print(f"↩️ [MariaPortuguesa] Acordo reembolsado: {acordo.gid}")


def aplicar_aviso_conta(db: Session, conta_id: Optional[str]) -> None:
    domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.stripe_conta_id == conta_id).first() \
        if conta_id else None
    if domestica is not None:
        atualizar_recebimentos(db, domestica)
