# Serviço de embeddings E5 (int8) do Koma — ver app.py e deploy_e5.sh.
# Versões iguais às da API (requirements.txt da raiz) para os vetores serem idênticos.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/hf

WORKDIR /app

RUN pip install --no-cache-dir torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Descarrega o E5, quantiza e guarda só o resultado: o float32 (~2,2 GB) não fica na imagem.
COPY quantizar.py .
RUN mkdir -p /modelo && python quantizar.py /modelo/e5_int8.pt && rm -rf /tmp/hf

COPY app.py .

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
