"""
Session Service
Gerencia sessões de usuário com carrinho e histórico de conversa
Cada sessão tem TTL de 30 minutos de inatividade
"""
import time
import uuid
from typing import Dict, List, Optional


class CartItem:
    def __init__(self, product_id: int, name: str, price: float, quantity: int = 1,
                 serves_people: int = 1, category: str = ""):
        self.product_id = product_id
        self.name = name
        self.price = price
        self.quantity = quantity
        self.serves_people = serves_people
        self.category = category

    def to_dict(self) -> Dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "price": self.price,
            "quantity": self.quantity,
            "serves_people": self.serves_people,
            "category": self.category,
            "subtotal": round(self.price * self.quantity, 2)
        }


class UserSession:
    """Sessão de um usuário com carrinho e histórico de conversa"""

    def __init__(self, session_id: str, restaurant_id: Optional[int] = None):
        self.session_id = session_id
        self.restaurant_id = restaurant_id
        self.cart: List[CartItem] = []
        self.history: List[Dict] = []  # [{"role": "user"|"assistant", "content": "..."}]
        self.context: Dict = {
            "pessoas": None,        # quantas pessoas vão comer
            "categoria_atual": None, # pizza, hamburguer, etc
            "aguardando": None,     # o que a IA está esperando (bebida, sobremesa, etc)
        }
        self.created_at = time.time()
        self.last_active = time.time()

    def touch(self):
        """Atualiza timestamp de última atividade"""
        self.last_active = time.time()

    def is_expired(self, ttl_seconds: int = 1800) -> bool:
        """Verifica se sessão expirou (padrão: 30 minutos)"""
        return (time.time() - self.last_active) > ttl_seconds

    # ── Histórico de conversa ──────────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """Adiciona mensagem ao histórico (mantém últimas 10 trocas = 20 mensagens)"""
        self.history.append({"role": role, "content": content})
        # Manter apenas as últimas 20 mensagens para não estourar o prompt
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def get_history_text(self) -> str:
        """Formata histórico para incluir no prompt do Gemini"""
        if not self.history:
            return ""
        lines = []
        for msg in self.history[-10:]:  # últimas 10 mensagens no prompt
            role_label = "Cliente" if msg["role"] == "user" else "Você"
            lines.append(f"{role_label}: {msg['content']}")
        return "\n".join(lines)

    # ── Carrinho ───────────────────────────────────────────────────────────

    def add_to_cart(self, product_id: int, name: str, price: float,
                    quantity: int = 1, serves_people: int = 1, category: str = "") -> str:
        """Adiciona ou incrementa item no carrinho"""
        # Verificar se já existe no carrinho
        for item in self.cart:
            if item.product_id == product_id:
                item.quantity += quantity
                return f"Quantidade de {name} atualizada para {item.quantity}"

        self.cart.append(CartItem(product_id, name, price, quantity, serves_people, category))
        return f"{name} adicionado ao carrinho"

    def remove_from_cart(self, product_id: int) -> bool:
        """Remove item do carrinho"""
        before = len(self.cart)
        self.cart = [item for item in self.cart if item.product_id != product_id]
        return len(self.cart) < before

    def clear_cart(self):
        """Limpa o carrinho"""
        self.cart = []

    def get_cart_summary(self) -> Dict:
        """Resumo do carrinho com total"""
        items = [item.to_dict() for item in self.cart]
        total = sum(i["subtotal"] for i in items)
        total_pessoas = sum(
            item.serves_people * item.quantity for item in self.cart
        )
        return {
            "items": items,
            "total_items": sum(item.quantity for item in self.cart),
            "total_price": round(total, 2),
            "total_serves": total_pessoas
        }

    def get_cart_for_prompt(self) -> str:
        """Formata carrinho para o prompt do Gemini"""
        if not self.cart:
            return "vazio"
        lines = []
        for item in self.cart:
            lines.append(
                f"• {item.name} x{item.quantity} = R$ {item.price * item.quantity:.2f}"
                + (f" (serve {item.serves_people * item.quantity}p)" if item.serves_people > 1 else "")
            )
        total = sum(i.price * i.quantity for i in self.cart)
        lines.append(f"Total: R$ {total:.2f}")
        return "\n".join(lines)

    def get_cart_as_list(self) -> List[Dict]:
        """Retorna carrinho como lista de dicts (para compatibilidade com código existente)"""
        return [item.to_dict() for item in self.cart]


class SessionManager:
    """
    Gerenciador de sessões em memória RAM
    TTL padrão: 30 minutos de inatividade
    """
    _sessions: Dict[str, UserSession] = {}
    _TTL = 1800  # 30 minutos

    @classmethod
    def get_or_create(cls, session_id: Optional[str],
                      restaurant_id: Optional[int] = None) -> UserSession:
        """Retorna sessão existente ou cria nova"""
        # Limpar sessões expiradas periodicamente
        cls._cleanup()

        # Gerar session_id se não fornecido
        if not session_id:
            session_id = str(uuid.uuid4())

        if session_id not in cls._sessions:
            cls._sessions[session_id] = UserSession(session_id, restaurant_id)
            print(f"🆕 [Session] Nova sessão criada: {session_id[:8]}...")
        else:
            session = cls._sessions[session_id]
            session.touch()
            # Atualizar restaurant_id se fornecido
            if restaurant_id and not session.restaurant_id:
                session.restaurant_id = restaurant_id

        return cls._sessions[session_id]

    @classmethod
    def get(cls, session_id: str) -> Optional[UserSession]:
        """Retorna sessão se existir e não expirada"""
        session = cls._sessions.get(session_id)
        if session and not session.is_expired(cls._TTL):
            session.touch()
            return session
        return None

    @classmethod
    def delete(cls, session_id: str):
        """Remove sessão"""
        cls._sessions.pop(session_id, None)

    @classmethod
    def _cleanup(cls):
        """Remove sessões expiradas"""
        expired = [sid for sid, s in cls._sessions.items() if s.is_expired(cls._TTL)]
        for sid in expired:
            del cls._sessions[sid]
        if expired:
            print(f"🧹 [Session] {len(expired)} sessão(ões) expirada(s) removida(s)")

    @classmethod
    def get_stats(cls) -> Dict:
        """Estatísticas das sessões ativas"""
        active = [s for s in cls._sessions.values() if not s.is_expired(cls._TTL)]
        return {
            "active_sessions": len(active),
            "total_sessions": len(cls._sessions),
            "sessions": [
                {
                    "id": s.session_id[:8] + "...",
                    "cart_items": len(s.cart),
                    "history_messages": len(s.history),
                    "idle_minutes": round((time.time() - s.last_active) / 60, 1)
                }
                for s in active
            ]
        }

