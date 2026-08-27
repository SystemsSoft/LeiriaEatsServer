"""
Testes de indexação do AIService (F1.1, F1.4, F2.2 do PLANO_EXECUCAO_IA.md).

Não carrega o modelo E5 real (~560M parâmetros, baixaria pesos na primeira execução) —
substitui AIService.get_model por um dublê cujo .encode() devolve um tensor qualquer,
já que estes testes cobrem a estrutura dos dados indexados, não a qualidade semântica
da busca (isso é o conjunto de avaliação da F0.2, construído a partir de tráfego real).

Execução:
    python3 tests/test_ai_service_indexing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-nao-usada")

import torch
from services.ai_service import AIService


class _ModeloFake:
    def encode(self, textos, convert_to_tensor=True):
        # Um vetor por texto, dimensão irrelevante para estes testes.
        return torch.zeros((len(textos), 4))


class _ProdutoFake:
    def __init__(self, id, name, description=None, category=None, ingredients=None,
                 dietary_tags=None, search_tags=None, recommended_for=None,
                 portion_size=None, spice_level="não picante", price=10.0):
        self.id = id
        self.name = name
        self.description = description
        self.category = category
        self.ingredients = ingredients
        self.dietary_tags = dietary_tags
        self.search_tags = search_tags
        self.recommended_for = recommended_for
        self.portion_size = portion_size
        self.spice_level = spice_level
        self.price = price


class _RestauranteFake:
    def __init__(self, name, category, products):
        self.name = name
        self.category = category
        self.products = products


def teste_texto_indexado_inclui_colunas_de_ia():
    """F1.1: o texto do embedding deve incluir ingredients/dietary_tags/search_tags/
    recommended_for/spice_level — antes só usava name + description."""
    p = _ProdutoFake(
        id=1, name="Buda Bowl", description="Tigela vegetariana",
        ingredients="grão-de-bico, quinoa, abacate",
        dietary_tags="vegetariano, sem glúten",
        search_tags="leve, saudável",
        recommended_for="almoço, jantar",
        spice_level="picante",
    )
    texto = AIService._texto_para_indice(p, categoria_restaurante="Saudável")

    for esperado in ["Buda Bowl", "vegetariana", "Saudável", "grão-de-bico",
                      "vegetariano", "leve", "almoço", "picante"]:
        assert esperado in texto, f"'{esperado}' ausente do texto indexado: {texto!r}"
    print("OK  - texto indexado inclui ingredients/dietary_tags/search_tags/recommended_for/spice_level")


def teste_texto_indexado_nao_quebra_com_campos_vazios():
    """Produto sem nenhuma coluna extra preenchida não deve gerar exceção nem 'None' no texto."""
    p = _ProdutoFake(id=2, name="Água", description=None)
    texto = AIService._texto_para_indice(p, categoria_restaurante="")
    assert "None" not in texto
    assert "Água" in texto
    print("OK  - campos vazios não geram 'None' no texto indexado nem quebram a função")


def teste_product_by_id_populado_apos_index_data():
    """F2.2: _product_by_id deve permitir lookup O(1) equivalente ao antigo O(n)."""
    original_get_model = AIService.get_model
    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    try:
        produtos = [_ProdutoFake(id=10, name="Pizza"), _ProdutoFake(id=11, name="Sushi")]
        restaurantes = [_RestauranteFake(name="Rest A", category="Variado", products=produtos)]

        AIService._index_data(restaurantes)

        assert AIService._product_by_id.get(10) is not None
        assert AIService._product_by_id.get(10).name == "Pizza"
        assert AIService._product_by_id.get(11).name == "Sushi"
        assert AIService._product_by_id.get(999) is None
        assert len(AIService._product_obj_cache) == 2
    finally:
        AIService.get_model = original_get_model
    print("OK  - _product_by_id é populado corretamente por _index_data (lookup O(1))")


def teste_rebind_e_atomico_entre_gerações():
    """F5.3 (parcial): uma segunda chamada a _index_data não deve deixar _product_by_id
    e _product_obj_cache dessincronizados entre si."""
    original_get_model = AIService.get_model
    AIService.get_model = classmethod(lambda cls: _ModeloFake())
    try:
        r1 = [_RestauranteFake(name="Rest A", category="X", products=[_ProdutoFake(id=1, name="A")])]
        AIService._index_data(r1)
        assert set(AIService._product_by_id.keys()) == {1}

        r2 = [_RestauranteFake(name="Rest B", category="Y", products=[_ProdutoFake(id=2, name="B")])]
        AIService._index_data(r2)

        ids_no_cache = {p.id for p in AIService._product_obj_cache}
        ids_no_dict = set(AIService._product_by_id.keys())
        assert ids_no_cache == ids_no_dict == {2}, (
            f"cache e dict devem refletir a MESMA geração de dados: "
            f"cache={ids_no_cache} dict={ids_no_dict}"
        )
    finally:
        AIService.get_model = original_get_model
    print("OK  - reindexação substitui cache e dict de forma consistente (mesma geração)")


if __name__ == "__main__":
    teste_texto_indexado_inclui_colunas_de_ia()
    teste_texto_indexado_nao_quebra_com_campos_vazios()
    teste_product_by_id_populado_apos_index_data()
    teste_rebind_e_atomico_entre_gerações()
    print("\nTodos os testes de indexação passaram.")
