# Módulo: Maria Portuguesa
#
# Marketplace de empregadas domésticas para Portugal. Vive no mesmo servidor do
# Leiria Eats / Koma, mas é isolado: banco de dados próprio (MariaPortuguesaDB, ver
# database.py), rotas próprias (prefixo /maria-portuguesa) e nenhum acoplamento com
# pedidos ou restaurantes. A Stripe é a mesma conta, mas com chave e webhook próprios
# (pagamentos.py).
#
# O único recurso partilhado é o modelo de embeddings E5 já carregado pelo AIService
# (evita ter dois modelos de 2 GB em memória) e as chaves GEMINI_API_KEY.
from modules.maria_portuguesa import models  # noqa: F401 — regista as tabelas na Base
from modules.maria_portuguesa.database import criar_banco_e_tabelas
from modules.maria_portuguesa.routes import router

__all__ = ["router", "criar_banco_e_tabelas"]
