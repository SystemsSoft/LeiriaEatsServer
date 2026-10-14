# Arquivo: modules/maria_portuguesa/assinaturas.py
#
# Liga as assinaturas Premium da Stripe aos perfis: aplicar o estado de uma
# assinatura a um perfil, e a sincronização diária que corrige o que um aviso
# perdido da Stripe tenha deixado desatualizado.
import asyncio
from datetime import timedelta
from typing import Callable

from sqlalchemy.orm import Session

from modules.maria_portuguesa.models import MpDomesticaDB
from modules.maria_portuguesa.pagamentos import (
    ESTADOS_TERMINADOS, Assinatura, ErroPagamento, Pagamentos, PagamentosIndisponiveis,
)

INTERVALO_SINCRONIZACAO = timedelta(hours=24)
# Espera depois de o servidor arrancar, para não competir com o arranque do Leiria Eats.
_ATRASO_INICIAL_S = 120
# O servidor de desenvolvimento desliga a sincronização (não tem assinaturas verdadeiras).
sincronizacao_ativa = True


def aplicar_assinatura(domestica: MpDomesticaDB, assinatura: Assinatura) -> None:
    domestica.stripe_assinatura_id = assinatura.id
    if assinatura.cliente:
        domestica.stripe_cliente_id = assinatura.cliente
    domestica.premium_estado = assinatura.estado
    domestica.premium_ate = assinatura.valida_ate if assinatura.ativa else None
    domestica.premium_renova = assinatura.ativa and not assinatura.cancela_no_fim


def sincronizar_assinaturas(db: Session) -> dict:
    """Pergunta à Stripe o estado de cada assinatura que ainda não terminou e corrige
    os perfis. É a rede de segurança dos avisos (webhook): a Stripe só os repete
    durante 3 dias, e uma renovação perdida tirava o destaque a quem pagou."""
    pendentes = db.query(MpDomesticaDB).filter(
        MpDomesticaDB.stripe_assinatura_id.isnot(None),
        MpDomesticaDB.premium_estado.is_(None) | MpDomesticaDB.premium_estado.notin_(ESTADOS_TERMINADOS),
    ).all()
    alteradas = falhadas = 0
    for domestica in pendentes:
        try:
            assinatura = Pagamentos.consultar_assinatura(domestica.stripe_assinatura_id)
        except ErroPagamento:
            falhadas += 1
            continue
        antes = (domestica.premium_ate, domestica.premium_renova, domestica.premium_estado)
        aplicar_assinatura(domestica, assinatura)
        if (domestica.premium_ate, domestica.premium_renova, domestica.premium_estado) != antes:
            alteradas += 1
    db.commit()
    return {"verificadas": len(pendentes), "alteradas": alteradas, "falhadas": falhadas}


async def sincronizar_todos_os_dias(fabrica_db: Callable[[], Session]) -> None:
    """Corre a sincronização pouco depois do arranque e depois uma vez por dia."""
    await asyncio.sleep(_ATRASO_INICIAL_S)
    while True:
        if sincronizacao_ativa and Pagamentos.configurada():
            try:
                resultado = await asyncio.to_thread(_sincronizar_com_sessao_propria, fabrica_db)
                print(f"⭐ [MariaPortuguesa] Assinaturas sincronizadas com a Stripe: {resultado}")
            except PagamentosIndisponiveis:
                pass
            except Exception as e:
                # Nunca deixa a tarefa morrer: tenta outra vez no dia seguinte.
                print(f"⚠️ [MariaPortuguesa] A sincronização das assinaturas falhou: {e}")
        await asyncio.sleep(INTERVALO_SINCRONIZACAO.total_seconds())


def _sincronizar_com_sessao_propria(fabrica_db: Callable[[], Session]) -> dict:
    db = fabrica_db()
    try:
        return sincronizar_assinaturas(db)
    finally:
        db.close()
