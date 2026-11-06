# Arquivo: services/cache_vetores.py
"""Cache em disco dos vetores dos produtos ("passage: ..."), por modelo de embeddings.

Usada pelos motores remotos (services/e5_remoto.py, services/gemini_embeddings.py): num
arranque da API só os produtos novos ou alterados são calculados de novo. Um modelo
diferente usa outro ficheiro, por isso os vetores de modelos diferentes nunca se misturam.
"""
import hashlib
import os
import pickle


class CacheVetores:
    def __init__(self, pasta: str, id_modelo: str):
        self.ficheiro = os.path.join(pasta, f"vetores-{id_modelo}.pkl")
        self.pasta = pasta
        try:
            with open(self.ficheiro, "rb") as f:
                self._vetores = pickle.load(f)
        except (OSError, pickle.UnpicklingError, EOFError):
            self._vetores = {}

    @staticmethod
    def chave(texto: str) -> str:
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()

    def get(self, texto: str):
        return self._vetores.get(self.chave(texto))

    def put(self, texto: str, vetor) -> None:
        self._vetores[self.chave(texto)] = vetor

    def gravar(self) -> None:
        os.makedirs(self.pasta, exist_ok=True)
        temporario = f"{self.ficheiro}.tmp"
        with open(temporario, "wb") as f:
            pickle.dump(self._vetores, f)
        os.replace(temporario, self.ficheiro)

    def __len__(self):
        return len(self._vetores)
