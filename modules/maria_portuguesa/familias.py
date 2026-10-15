# Arquivo: modules/maria_portuguesa/familias.py
#
# Conta de quem procura uma doméstica: entrar (Firebase), nome e zona, e as
# domésticas guardadas para contactar mais tarde. A pesquisa continua aberta a
# todos; a conta serve para não perder quem já se encontrou.
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modules.maria_portuguesa import acordos
from modules.maria_portuguesa.database import get_db
from modules.maria_portuguesa.firebase_auth import TokenFirebaseInvalido, VerificadorFirebase
from modules.maria_portuguesa.models import MpDomesticaDB, MpFamiliaDB, MpFavoritoDB
from modules.maria_portuguesa.schemas import EntrarFirebaseRequest, FamiliaUpdate, perfil_publico
from modules.maria_portuguesa.security import (
    criar_sessao_familia, encerrar_sessao_familia, familia_atual, token_atual,
)

router = APIRouter(prefix="/familias")


def conta_da_familia(f: MpFamiliaDB) -> dict:
    """A conta tal como a própria família a vê. Das domésticas guardadas só vêm as
    que continuam publicadas, com o perfil público (nunca o email)."""
    return {
        "gid": f.gid,
        "nome": f.nome,
        "email": f.email,
        "distrito": f.distrito,
        "concelho": f.concelho,
        "favoritos": [perfil_publico(fav.domestica) for fav in f.favoritos if fav.domestica.publicado],
    }


@router.post("/firebase")
def entrar_com_firebase(pedido: EntrarFirebaseRequest, db: Session = Depends(get_db)):
    """Criar conta e iniciar sessão com o Firebase Authentication. Com nome e
    consentimento cria a conta se ainda não existir; sem eles só entra."""
    try:
        conta = VerificadorFirebase.verificar(pedido.id_token)
    except TokenFirebaseInvalido as e:
        print(f"⚠️ [MariaPortuguesa] ID token do Firebase recusado (família): {e}")
        raise HTTPException(status_code=401, detail="Não foi possível confirmar a sua conta. Inicie sessão novamente.")

    familia = db.query(MpFamiliaDB).filter(MpFamiliaDB.firebase_uid == conta.uid).first()
    criada = False
    if familia is None:
        if pedido.nome is None:
            raise HTTPException(status_code=404, detail="Ainda não tem conta. Crie a sua conta para continuar.")
        if not pedido.consentimento_rgpd:
            raise HTTPException(status_code=422, detail="É necessário aceitar o tratamento dos dados para criar a conta.")
        familia = MpFamiliaDB(nome=pedido.nome, email=conta.email, firebase_uid=conta.uid)
        db.add(familia)
        try:
            db.commit()
        except IntegrityError:
            # Dois pedidos de criação ao mesmo tempo: fica a conta que entrou primeiro.
            db.rollback()
            familia = db.query(MpFamiliaDB).filter(MpFamiliaDB.firebase_uid == conta.uid).one()
        else:
            db.refresh(familia)
            criada = True
            print(f"👪 [MariaPortuguesa] Nova conta de família: {familia.gid}")
    elif familia.email != conta.email:
        # O email mudou no Firebase: acompanha.
        familia.email = conta.email
        db.commit()
    return {"token": criar_sessao_familia(db, familia), "familia": conta_da_familia(familia), "criada": criada}


@router.post("/sair", status_code=204)
def terminar_sessao(token: str = Depends(token_atual), db: Session = Depends(get_db)):
    encerrar_sessao_familia(db, token)


@router.get("/eu")
def obter_a_minha_conta(familia: MpFamiliaDB = Depends(familia_atual)):
    return conta_da_familia(familia)


@router.put("/eu")
def atualizar_a_minha_conta(
        pedido: FamiliaUpdate,
        familia: MpFamiliaDB = Depends(familia_atual),
        db: Session = Depends(get_db),
):
    alteracoes = pedido.model_dump(exclude_unset=True)
    if "nome" in alteracoes and not alteracoes["nome"]:
        raise HTTPException(status_code=422, detail="O nome não pode ficar vazio.")
    for campo, valor in alteracoes.items():
        setattr(familia, campo, valor)
    db.commit()
    db.refresh(familia)
    return conta_da_familia(familia)


@router.delete("/eu", status_code=204)
def apagar_a_minha_conta(familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    """Direito ao apagamento (RGPD, art.º 17.º): remove a conta, as sessões e as
    domésticas guardadas. Os acordos ficam (são registos de pagamentos), sem ligação
    à conta. Com dinheiro retido não se apaga: falta decidir se segue ou volta."""
    if acordos.tem_pagamentos_retidos(familia.acordos):
        raise HTTPException(
            status_code=409,
            detail="Tem pagamentos retidos por libertar. Liberte-os (ou reporte um problema) antes de apagar a conta.",
        )
    acordos.fechar_pendentes(familia.acordos)
    print(f"🗑️ [MariaPortuguesa] Conta de família apagada a pedido da própria: {familia.gid}")
    db.delete(familia)
    db.commit()


@router.put("/eu/favoritos/{gid}", status_code=204)
def guardar_domestica(gid: str, familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    domestica = db.query(MpDomesticaDB).filter(
        MpDomesticaDB.gid == gid, MpDomesticaDB.publicado.is_(True),
    ).first()
    if domestica is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    db.add(MpFavoritoDB(familia_id=familia.id, domestica_id=domestica.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # já estava guardada: guardar outra vez não muda nada


@router.delete("/eu/favoritos/{gid}", status_code=204)
def retirar_domestica(gid: str, familia: MpFamiliaDB = Depends(familia_atual), db: Session = Depends(get_db)):
    domestica = db.query(MpDomesticaDB).filter(MpDomesticaDB.gid == gid).first()
    if domestica is not None:
        db.query(MpFavoritoDB).filter(
            MpFavoritoDB.familia_id == familia.id, MpFavoritoDB.domestica_id == domestica.id,
        ).delete()
        db.commit()
