# Arquivo: services/stripe_prefill.py
"""Contas Stripe Connect Express (restaurantes e estafetas) já criadas com os dados que temos.

Antes a conta era criada só com o email e a Stripe pedia tudo outra vez no formulário hospedado:
setor de atividade, descrição ou site do negócio, telefone, nome e morada. Agora enviamos o que já
está guardado — o formulário da Stripe abre com esses campos preenchidos (o utilizador confirma
ou corrige) e deixa de perguntar o setor e a descrição do negócio. O que a Stripe tem de verificar
por lei (documento de identificação, IBAN, dados da empresa ou do empresário) continua a ser pedido.

Se a Stripe recusar algum valor do pré-preenchimento (ex.: um telefone ou código postal que não
existe), esse campo é retirado e a conta é criada com o resto; em último caso só com o mínimo: o
pré-preenchimento nunca pode impedir um cadastro.
"""
import copy
import re
from typing import Optional

import stripe

# Códigos de atividade (MCC) da Stripe: 5812 = restaurantes; 4215 = serviços de estafeta/entregas.
MCC_RESTAURANTE = "5812"
MCC_ESTAFETA = "4215"

_CAPACIDADES = {
    "card_payments": {"requested": True},
    "transfers": {"requested": True},
}


def telefone_e164(telefone: Optional[str], indicativo: str = "351") -> Optional[str]:
    """"912 345 678" -> "+351912345678"; "+351 912345678" e "00351912345678" também.
    None se não der para ter a certeza (a Stripe exige o formato internacional)."""
    bruto = (telefone or "").strip()
    if not bruto:
        return None
    digitos = re.sub(r"\D", "", bruto)
    if bruto.startswith("+"):
        pronto = digitos
    elif digitos.startswith("00"):
        pronto = digitos[2:]
    elif len(digitos) == 9:
        pronto = indicativo + digitos
    else:
        return None
    return f"+{pronto}" if 8 <= len(pronto) <= 15 else None


def _sem_vazios(dados: dict) -> dict:
    """Tira valores vazios, em profundidade: a Stripe recusa strings vazias."""
    limpo = {}
    for chave, valor in dados.items():
        if isinstance(valor, dict):
            valor = _sem_vazios(valor)
        if valor not in (None, "", {}):
            limpo[chave] = valor
    return limpo


def dados_restaurante(restaurante) -> dict:
    """Campos de pré-preenchimento para a conta de um restaurante."""
    return _sem_vazios({
        "business_profile": {
            "name": restaurante.name,
            "mcc": MCC_RESTAURANTE,
            "product_description": "Restaurante com venda de refeições para entrega e recolha através da plataforma KomaAI.",
            "support_phone": telefone_e164(getattr(restaurante, "phone", None)),
        },
    })


def _partir_nome(nome: Optional[str]):
    partes = (nome or "").split()
    if len(partes) < 2:
        return None, None  # com uma palavra só não dá para separar nome e apelido
    return partes[0], " ".join(partes[1:])


def dados_estafeta(estafeta, email: str) -> dict:
    """Campos de pré-preenchimento para a conta de um estafeta (pessoa singular)."""
    nome, apelido = _partir_nome(estafeta.name)
    telefone = telefone_e164(estafeta.phone)
    return _sem_vazios({
        "business_type": "individual",
        "business_profile": {
            "mcc": MCC_ESTAFETA,
            "product_description": "Estafeta independente de entregas de comida através da plataforma KomaAI.",
            "support_phone": telefone,
        },
        "individual": {
            "first_name": nome,
            "last_name": apelido,
            "email": email,
            "phone": telefone,
            "address": {
                "line1": estafeta.address,
                "city": estafeta.city,
                "postal_code": estafeta.postal_code,
                "country": "PT",
            },
        },
    })


def _retirar_campo(dados: dict, param: Optional[str]) -> bool:
    """Tira de `dados` o campo que a Stripe recusou ("individual[address][postal_code]" ->
    dados["individual"]["address"]["postal_code"]). False se não for possível (param em falta,
    desconhecido ou já retirado)."""
    caminho = [parte for parte in re.split(r"[\[\]]+", param or "") if parte]
    if not caminho:
        return False
    pai = dados
    for chave in caminho[:-1]:
        pai = pai.get(chave)
        if not isinstance(pai, dict):
            return False
    if caminho[-1] not in pai:
        return False
    del pai[caminho[-1]]
    return True


def criar_conta_express(email: str, pre_preenchimento: Optional[dict] = None):
    """Cria a conta Express em Portugal. Com `pre_preenchimento`, a Stripe pode recusar um valor
    (ex.: um código postal que não existe): retira só esse campo e repete, mantendo o resto. Se
    não souber qual foi, ou depois de algumas voltas, cria a conta só com o mínimo."""
    base = {"type": "express", "country": "PT", "email": email, "capabilities": _CAPACIDADES}
    extras = copy.deepcopy(pre_preenchimento) if pre_preenchimento else {}
    for _ in range(6):
        if not extras:
            break
        try:
            return stripe.Account.create(**base, **_sem_vazios(extras))
        except stripe.error.InvalidRequestError as e:
            print(f"⚠️ Stripe recusou um valor do pré-preenchimento ({e}); a retirá-lo e a repetir.")
            if not _retirar_campo(extras, getattr(e, "param", None)):
                break
    return stripe.Account.create(**base)
