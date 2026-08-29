"""Cliente do Tavily.

A EVE não deve inventar o que não sabe. Quando a informação é atual, ela
pesquisa — e volta com as fontes, para o usuário poder conferir (spec §16).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2 as httpx

from eve.logging import get_logger

log = get_logger(__name__)

ENDPOINT = "https://api.tavily.com"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "score": round(self.score, 3),
        }


@dataclass(frozen=True)
class SearchResponse:
    query: str
    answer: str = ""
    results: list[SearchResult] = field(default_factory=list)
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "results": [r.as_dict() for r in self.results],
            "sources": [r.url for r in self.results],
            "count": len(self.results),
            "duration_ms": round(self.duration_ms, 1),
        }


class SearchError(RuntimeError):
    def __init__(self, message: str, kind: str = "unavailable") -> None:
        super().__init__(message)
        self.kind = kind


class TavilySearch:
    def __init__(self, api_key: str, timeout: float = 45.0) -> None:
        if not api_key:
            raise ValueError("TAVILY_API_KEY não configurada")
        self._client = httpx.AsyncClient(
            base_url=ENDPOINT,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        depth: Literal["basic", "advanced"] = "basic",
        topic: Literal["general", "news"] = "general",
        include_answer: bool = True,
        days: int | None = None,
    ) -> SearchResponse:
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": depth,
            "topic": topic,
            "include_answer": include_answer,
        }
        if topic == "news" and days:
            payload["days"] = days

        started = time.perf_counter()
        dados = await self._post("/search", payload)
        return SearchResponse(
            query=query,
            answer=(dados.get("answer") or "").strip(),
            results=[
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=(item.get("content") or "").strip(),
                    score=float(item.get("score", 0.0)),
                )
                for item in dados.get("results", [])
            ],
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    async def extract(self, urls: list[str]) -> list[dict[str, Any]]:
        """Conteúdo limpo de páginas específicas."""
        dados = await self._post("/extract", {"urls": urls})
        return [
            {"url": item.get("url", ""), "content": (item.get("raw_content") or "").strip()}
            for item in dados.get("results", [])
        ]

    async def _post(self, caminho: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = await self._client.post(caminho, json=payload)
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily não respondeu: {str(exc)[:140]}") from exc
        if resposta.status_code == 401:
            raise SearchError("credencial do Tavily recusada", "auth")
        if resposta.status_code == 429:
            raise SearchError("limite de pesquisas atingido", "rate_limit")
        if resposta.status_code >= 400:
            raise SearchError(f"Tavily respondeu {resposta.status_code}", "bad_request")
        return resposta.json()
