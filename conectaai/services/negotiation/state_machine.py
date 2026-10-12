# Arquivo: conectaai/services/negotiation/state_machine.py
#
# Estados possíveis de uma NegotiationDB.state e as transições válidas entre
# eles. Não executa nada sozinho — é a tabela de referência que human.py
# consulta antes de gravar uma mudança de estado.
#
# Negociação é 100% humana (empresa e creator escrevem um para o outro —
# ver services/negotiation/human.py); não há mais agente de IA propondo ou
# aceitando automaticamente. Por isso o fluxo é simples: uma negociação
# nasce em `waiting_human_creator` (aberta, os dois podem falar a qualquer
# momento — o nome do estado é histórico, não significa "só o creator pode
# agir"), alguém aceita a oferta corrente e ela vira `waiting_approval` até
# os dois aprovarem o acordo formalmente.
TERMINAL_STATES = {"agreed", "rejected", "expired", "failed"}

VALID_TRANSITIONS = {
    "draft": {"waiting_human_creator", "rejected"},
    "waiting_human_creator": {"waiting_approval", "rejected", "expired"},
    "waiting_approval": {"agreed", "rejected", "expired", "waiting_human_creator"},  # contraproposta reabre a mesa
}


def can_transition(from_state: str, to_state: str) -> bool:
    if from_state in TERMINAL_STATES:
        return False
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
