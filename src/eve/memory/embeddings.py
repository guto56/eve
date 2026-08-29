"""Embeddings locais, via Ollama.

Rodam na máquina, como o resto: nenhum texto de memória sai daqui para virar
vetor. Se o modelo não estiver disponível, a busca semântica desliga e a
textual continua — não há motivo para a memória parar de funcionar por causa
de um modelo ausente.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx2 as httpx

from eve.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "embeddinggemma"
DEFAULT_DIMENSIONS = 768

#: O EmbeddingGemma foi treinado com prefixos de tarefa e perde qualidade sem
#: eles. Medido nesta máquina: a margem entre o acerto e o segundo colocado
#: passou de +0,040 para +0,106, e a similaridade de uma consulta sem relação
#: caiu de 0,345 para 0,249.
PREFIXO_DOCUMENTO = "title: none | text: {}"
PREFIXO_CONSULTA = "task: search result | query: {}"


class Embedder:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        timeout: float = 30.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self._client = httpx.AsyncClient(base_url=self.host, timeout=timeout)
        self._available: bool | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def available(self) -> bool:
        """O modelo está baixado? Verificado uma vez e lembrado."""
        async with self._lock:
            if self._available is None:
                self._available = await self._probe()
            return self._available

    async def _probe(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.info("embeddings.ollama_indisponivel", error=str(exc)[:120])
            return False
        modelos = {m["name"].split(":")[0] for m in response.json().get("models", [])}
        presente = self.model.split(":")[0] in modelos
        if not presente:
            log.info("embeddings.modelo_ausente", model=self.model)
        return presente

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]] | None:
        """Vetores para guardar."""
        return await self.embed([PREFIXO_DOCUMENTO.format(t) for t in texts])

    async def embed_query(self, text: str) -> list[float] | None:
        """Vetor para buscar."""
        vetores = await self.embed([PREFIXO_CONSULTA.format(text)])
        return vetores[0] if vetores else None

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        """Vetores crus, sem prefixo. Prefira ``embed_documents``/``embed_query``."""
        if not texts:
            return []
        if not await self.available():
            return None
        try:
            response = await self._client.post(
                "/api/embed", json={"model": self.model, "input": list(texts)}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("embeddings.falhou", error=str(exc)[:160])
            return None
        vetores = response.json().get("embeddings")
        if not vetores or len(vetores) != len(texts):
            log.warning("embeddings.resposta_inesperada", recebidos=len(vetores or []))
            return None
        return vetores

    async def embed_one(self, text: str) -> list[float] | None:
        vetores = await self.embed_documents([text])
        return vetores[0] if vetores else None
