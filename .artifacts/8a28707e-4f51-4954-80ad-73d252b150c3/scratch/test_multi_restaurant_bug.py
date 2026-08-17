
import sys
import os

# Adicionar o diretório raiz ao path para importar os módulos do projeto
sys.path.append(os.getcwd())

from core.database import SessionLocal
from services.ai_service import AIService
from services.hybrid_ai_service import HybridAIService
from services.session_service import SessionManager
import uuid

def test_multi_restaurant_cart():
    db = SessionLocal()
    session_id = f"test_{uuid.uuid4()}"
    
    print(f"🧪 Iniciando teste de múltiplos restaurantes com session_id: {session_id}")
    
    # 1. Limpar sessão anterior se existir
    SessionManager.delete(session_id)
    
    # 2. Simular o que o HybridAIService faz quando recebe as tags da IA
    session = SessionManager.get_or_create(session_id)
    
    # Simular adição de produto do Restaurante 1
    session.add_to_cart(product_id=1, name="King Cheese", price=10.0, restaurant_id=1, quantity=1)
    # Simular adição de produto do Restaurante 6
    session.add_to_cart(product_id=6, name="Pizza de Calabresa", price=15.0, restaurant_id=6, quantity=1)
    
    SessionManager.save(session)
    
    # 3. Chamar o chat apenas para ver o resumo do carrinho
    user_message = "Resumo do meu carrinho"
    print(f"💬 Usuário: '{user_message}'")
    
    result = HybridAIService.process_sales_chat(
        user_message=user_message,
        restaurant_id=None,
        cart=[],
        db=db,
        session_id=session_id
    )
    
    # 4. Validar retorno
    returned_cart = result['cart']['items']
    
    print(f"\n--- Resultados ---")
    print(f"🛒 Itens no carrinho JSON 'cart.items' ({len(returned_cart)}):")
    for item in returned_cart:
        print(f"   • {item['name']} (Restaurante ID: {item.get('restaurant_id')})")
    
    # Verificação
    success = True
    if len(returned_cart) == 2:
        print("✅ SUCESSO: Ambos os produtos estão no carrinho.")
    else:
        print(f"❌ ERRO: Esperava 2 itens no carrinho, mas recebi {len(returned_cart)}.")
        success = False
        
    for item in returned_cart:
        if item.get('restaurant_id') is None or item.get('restaurant_id') == 0:
            print(f"❌ ERRO: Produto {item['name']} está sem restaurant_id correto!")
            success = False
        else:
            print(f"✅ SUCESSO: Produto {item['name']} tem restaurant_id {item['restaurant_id']}.")
            
    return success
    
    # Verificação
    success = True
    if len(returned_cart) < 2:
        print("❌ ERRO: O carrinho no JSON de retorno tem menos de 2 itens!")
        success = False
    else:
        print("✅ SUCESSO: Todos os itens do carrinho estão presentes no resumo.")
        
    # Limpar
    SessionManager.delete(session_id)
    db.close()
    return success

if __name__ == "__main__":
    test_multi_restaurant_cart()
