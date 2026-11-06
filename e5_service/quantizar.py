"""Gera o E5 quantizado que o serviço carrega (e5_service/modelo/e5_int8.pt).

- Camadas Linear em int8 dinâmico, motor qnnpack — igual ao AIService._carregar_e5_int8.
- Tabela de vocabulário (250 mil pedaços × 1024, ~1 GB em float32) em 8 bits com uma
  escala por linha: a quantização dinâmica não lhe toca, e era ela que punha o processo
  em ~1,7 GB numa instância de 2 GB. Com ela quantizada o ficheiro fica em ~585 MB.

Uso:
    python quantizar.py destino.pt                       a partir do E5 original (descarrega ~2,2 GB)
    python quantizar.py destino.pt --a-partir-de e5_int8.pt   só quantiza a tabela de um modelo já em int8
"""
import sys

import torch
from sentence_transformers import SentenceTransformer
from torch.ao.quantization import default_dynamic_qconfig, float_qparams_weight_only_qconfig

destino = sys.argv[1]
torch.backends.quantized.engine = "qnnpack"

if "--a-partir-de" in sys.argv:
    modelo = torch.load(sys.argv[sys.argv.index("--a-partir-de") + 1], weights_only=False)
    especificacao = {}
else:
    modelo = SentenceTransformer("intfloat/multilingual-e5-large", device="cpu")
    especificacao = {torch.nn.Linear: default_dynamic_qconfig}

tabela = next(nome for nome, _ in modelo.named_modules() if nome.endswith("embeddings.word_embeddings"))
# padding_idx só serve para o treino (não atualizar o gradiente do token de padding) e a
# versão quantizada da tabela não o aceita.
dict(modelo.named_modules())[tabela].padding_idx = None
especificacao[tabela] = float_qparams_weight_only_qconfig

torch.ao.quantization.quantize_dynamic(modelo, especificacao, inplace=True)
torch.save(modelo, destino)
print(f"E5 quantizado (Linear int8 + tabela de vocabulário 8 bits) gravado em {destino}")
