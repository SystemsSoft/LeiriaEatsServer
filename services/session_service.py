import time
import uuid
import json
import redis
from typing import Dict, List, Optional
from core.config import settings


class CartItem:
    def __init__(self, product_id: int, name: str, price: float, restaurant_gid: str,
                 quantity: int = 1, serves_people: Optional[int] = 1, category: str = ""):
        self.product_id = product_id
        self.name = name
        self.price = price
        self.restaurant_gid = restaurant_gid
        self.quantity = quantity
        self.serves_people = serves_people if serves_people is not None else 1
        self.category = category

    def to_dict(self) -> Dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "price": self.price,
            "restaurant_gid": self.restaurant_gid,
            "quantity": self.quantity,
            "serves_people": self.serves_people,
            "category": self.category,
            "subtotal": round(self.price * self.quantity, 2)
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'CartItem':
        return cls(
            product_id=data["product_id"],
            name=data["name"],
            price=data["price"],
            restaurant_gid=data.get("restaurant_gid") or "",
            quantity=data["quantity"],
            serves_people=data.get("serves_people") if data.get("serves_people") is not None else 1,
            category=data.get("category", "")
        )


class UserSession:
    """Sessão de um usuário com carrinho e histórico de conversa"""

    def __init__(self, session_id: str, restaurant_gid: Optional[str] = None):
        self.session_id = session_id
        self.restaurant_gid = restaurant_gid
        self.cart: List[CartItem] = []
        self.history: List[Dict] = []  # [{"role": "user"|"assistant", "content": "..."}]
        self.context: Dict = {
            "pessoas": None,
            "categoria_atual": None,
            "aguardando": None,
        }
        self.created_at = time.time()
        self.last_active = time.time()

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "restaurant_gid": self.restaurant_gid,
            "cart": [item.to_dict() for item in self.cart],
            "history": self.history,
            "context": self.context,
            "last_suggested_ids": getattr(self, 'last_suggested_ids', []),
            "created_at": self.created_at,
            "last_active": self.last_active
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'UserSession':
        session = cls(data["session_id"], data.get("restaurant_gid"))
        session.cart = [CartItem.from_dict(item) for item in data.get("cart", [])]
        session.history = data.get("history", [])
        session.context = data.get("context", {
            "pessoas": None,
            "categoria_atual": None,
            "aguardando": None,
        })
        session.last_suggested_ids = data.get("last_suggested_ids", [])
        session.created_at = data.get("created_at", time.time())
        session.last_active = data.get("last_active", time.time())
        return session

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
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def get_history_text(self) -> str:
        """Formata histórico para incluir no prompt do Gemini"""
        if not self.history:
            return ""
        lines = []
        for msg in self.history[-10:]:
            role_label = "Cliente" if msg["role"] == "user" else "Você"
            lines.append(f"{role_label}: {msg['content']}")
        return "\n".join(lines)

    # ── Carrinho ───────────────────────────────────────────────────────────

    def add_to_cart(self, product_id: int, name: str, price: float, restaurant_gid: str,
                    quantity: int = 1, serves_people: int = 1, category: str = "") -> str:
        """Adiciona ou incrementa item no carrinho"""
        for item in self.cart:
            if item.product_id == product_id:
                item.quantity += quantity
                return f"Quantidade de {name} atualizada para {item.quantity}"

        self.cart.append(CartItem(product_id, name, price, restaurant_gid, quantity, serves_people, category))
        return f"{name} adicionado ao carrinho"

    def remove_from_cart(self, product_id: int) -> bool:
        """Remove item do carrinho"""
        before = len(self.cart)
        self.cart = [item for item in self.cart if item.product_id != product_id]
        return len(self.cart) < before

    def clear_cart(self):
        """Limpa o carrinho"""
        self.cart = []

    def reset_session(self):
        """Limpa histórico e carrinho (pós-venda)"""
        self.cart = []
        self.history = []
        self.context = {
            "pessoas": None,
            "categoria_atual": None,
            "aguardando": None,
        }
        print(f"🧹 [Session] Dados limpos para a sessão: {self.session_id[:8]}...")

    def get_cart_summary(self) -> Dict:
        """Resumo do carrinho com total"""
        items = [item.to_dict() for item in self.cart]
        total = sum(i["subtotal"] for i in items)
        total_pessoas = sum(
            (item.serves_people or 1) * item.quantity for item in self.cart
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
                f"• {item.name} x{item.quantity} = € {item.price * item.quantity:.2f}"
                + (f" (serve {item.serves_people * item.quantity}p)" if item.serves_people > 1 else "")
            )
        total = sum(i.price * i.quantity for i in self.cart)
        lines.append(f"Total: € {total:.2f}")
        return "\n".join(lines)

    def get_cart_as_list(self) -> List[Dict]:
        return [item.to_dict() for item in self.cart]


class SessionManager:
    """
    Gerenciador de sessões (Redis ou In-Memory)
    TTL padrão: 30 minutos de inatividade
    """
    _sessions: Dict[str, UserSession] = {}
    _TTL = 1800  # 30 minutos
    _redis = None

    @classmethod
    def _get_redis(cls):
        if settings.USE_REDIS and cls._redis is None:
            try:
                cls._redis = redis.Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    password=settings.REDIS_PASSWORD,
                    db=settings.REDIS_DB,
                    decode_responses=True
                )
                cls._redis.ping()
                print("✅ [Session] Conectado ao Redis com sucesso")
            except Exception as e:
                print(f"⚠️ [Session] Erro ao conectar ao Redis: {e}. Usando fallback In-Memory.")
                cls._redis = None
        return cls._redis

    @classmethod
    def save(cls, session: UserSession):
        """Persiste a sessão (Redis ou In-Memory)"""
        r = cls._get_redis()
        if r:
            r.setex(
                f"session:{session.session_id}",
                cls._TTL,
                json.dumps(session.to_dict())
            )
        else:
            cls._sessions[session.session_id] = session

    @classmethod
    def get_or_create(cls, session_id: Optional[str],
                      restaurant_gid: Optional[str] = None) -> UserSession:
        """Retorna sessão existente ou cria nova"""
        if not session_id:
            session_id = str(uuid.uuid4())

        session = cls.get(session_id)
        
        if not session:
            session = UserSession(session_id, restaurant_gid)
            print(f"🆕 [Session] Nova sessão criada: {session_id[:8]}...")
        else:
            if restaurant_gid and not session.restaurant_gid:
                session.restaurant_gid = restaurant_gid
        
        # Salvar imediatamente para garantir persistência inicial
        cls.save(session)
        return session

    @classmethod
    def get(cls, session_id: str) -> Optional[UserSession]:
        """Retorna sessão se existir e não expirada"""
        r = cls._get_redis()
        if r:
            data = r.get(f"session:{session_id}")
            if data:
                # Atualizar TTL no Redis (touch)
                r.expire(f"session:{session_id}", cls._TTL)
                return UserSession.from_dict(json.loads(data))
            return None
        
        # Fallback In-Memory
        session = cls._sessions.get(session_id)
        if session and not session.is_expired(cls._TTL):
            session.touch()
            return session
        return None

    @classmethod
    def delete(cls, session_id: str):
        """Remove sessão"""
        r = cls._get_redis()
        if r:
            r.delete(f"session:{session_id}")
        else:
            cls._sessions.pop(session_id, None)

    @classmethod
    def _cleanup(cls):
        """Remove sessões expiradas (apenas para In-Memory)"""
        if cls._get_redis():
            return  # Redis gerencia TTL automaticamente
            
        expired = [sid for sid, s in cls._sessions.items() if s.is_expired(cls._TTL)]
        for sid in expired:
            del cls._sessions[sid]
        if expired:
            print(f"🧹 [Session] {len(expired)} sessão(ões) expirada(s) removida(s)")

    @classmethod
    def get_stats(cls) -> Dict:
        """Estatísticas das sessões ativas"""
        r = cls._get_redis()
        if r:
            # Aproximação para Redis (keys com prefixo)
            keys = r.keys("session:*")
            return {
                "type": "Redis",
                "active_sessions": len(keys),
                "total_sessions": len(keys)
            }
            
        active = [s for s in cls._sessions.values() if not s.is_expired(cls._TTL)]
        return {
            "type": "In-Memory",
            "active_sessions": len(active),
            "total_sessions": len(cls._sessions)
        }

