# Arquivo: conectaai/services/negotiation/state_machine.py
#
# Estados possíveis de uma NegotiationDB.state e as transições válidas entre
# eles. Não executa nada sozinho — é a tabela de referência que engine.py
# consulta antes de gravar uma mudança de estado, pra impedir um bug de
# pular etapa (ex.: ir direto de "running" para "agreed" sem passar por
# "waiting_approval").
TERMINAL_STATES = {"agreed", "rejected", "impasse", "expired", "failed"}

VALID_TRANSITIONS = {
    "draft": {"queued", "waiting_human_creator", "rejected"},
    "queued": {"running", "rejected", "expired"},
    "running": {
        "running",  # próxima rodada
        "waiting_approval",
        "waiting_human_company",
        "waiting_human_creator",
        "impasse",
        "expired",
        "failed",
        "queued",  # lease perdida no meio, volta pra fila (ver runner.py)
    },
    "waiting_human_company": {"queued", "running", "rejected", "expired"},
    "waiting_human_creator": {"queued", "running", "rejected", "expired"},
    "waiting_approval": {"agreed", "rejected", "expired"},
}


def can_transition(from_state: str, to_state: str) -> bool:
    if from_state in TERMINAL_STATES:
        return False
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
