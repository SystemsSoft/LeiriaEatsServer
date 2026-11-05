"""Gera o E5 quantizado (int8) que a imagem leva pronto — corre só no `docker build`.

Mesma quantização do AIService._carregar_e5_int8 da API (camadas Linear em int8,
motor qnnpack), feita uma vez aqui para o serviço arrancar em segundos em vez de
carregar o modelo float32 (~2,2 GB) e quantizá-lo a cada arranque.

Uso: python quantizar.py /modelo/e5_int8.pt
"""
import sys

import torch
from sentence_transformers import SentenceTransformer

destino = sys.argv[1]
torch.backends.quantized.engine = "qnnpack"
modelo = SentenceTransformer("intfloat/multilingual-e5-large", device="cpu")
torch.ao.quantization.quantize_dynamic(modelo, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
torch.save(modelo, destino)
print(f"E5 int8 gravado em {destino}")
