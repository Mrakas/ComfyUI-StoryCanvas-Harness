from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from ..errors import ProviderError
from ..schemas import ProviderReceipt
from ..utils import sha256_json
from .base import SearchHit, SearchResult


def _collect_urls(value: Any, rows: list[SearchHit]) -> None:
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            title = str(value.get("title") or value.get("text") or url)
            hit = SearchHit(
                title=title[:300],
                url=url,
                publisher=urlparse(url).netloc or None,
                snippet=str(value.get("snippet") or "")[:1000] or None,
            )
            if all(existing.url != hit.url for existing in rows):
                rows.append(hit)
        for nested in value.values():
            _collect_urls(nested, rows)
    elif isinstance(value, list):
        for nested in value:
            _collect_urls(nested, rows)


class MockFactSearch:
    name = "mock"
    model = "mock-fact-search-v1"

    def search(self, query: str) -> SearchResult:
        request_sha = sha256_json({"query": query})
        return SearchResult(
            query=query,
            summary=f"Offline mock result for: {query}",
            hits=[SearchHit(title="Example source", url="https://example.com/storycanvas")],
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="fact_search",
                request_sha256=request_sha,
            ),
        )


class MockVisualSearch:
    name = "mock"
    model = "mock-visual-search-v1"

    def search(self, query: str) -> SearchResult:
        request_sha = sha256_json({"query": query, "kind": "visual"})
        return SearchResult(
            query=query,
            summary=f"Offline mock visual result for: {query}",
            hits=[],
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="visual_search",
                request_sha256=request_sha,
            ),
        )


class OpenAIFactSearch:
    name = "openai"
    model = "web_search"

    def __init__(self, *, text_model: str | None = None, client: Any | None = None):
        self.text_model: str = text_model or os.environ.get("OPENAI_TEXT_MODEL", "gpt-5")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:  # pragma: no cover
                raise ProviderError("Install the openai package to use OpenAIFactSearch") from error
            client = OpenAI()
        self.client: Any = client

    def search(self, query: str) -> SearchResult:
        request = {
            "model": self.text_model,
            "tools": [{"type": "web_search"}],
            "input": (
                "Research the visual facts needed for a fictional video asset. Summarize concrete appearance, "
                f"materials, era, and geography. Cite primary or authoritative pages. Query: {query}"
            ),
        }
        try:
            response = self.client.responses.create(**request)
            dumped = response.model_dump(mode="json") if hasattr(response, "model_dump") else {}
            hits: list[SearchHit] = []
            _collect_urls(dumped, hits)
            return SearchResult(
                query=query,
                summary=str(getattr(response, "output_text", "")),
                hits=hits,
                receipt=ProviderReceipt(
                    provider=self.name,
                    model=self.text_model,
                    operation="fact_search",
                    request_sha256=sha256_json(request),
                    provider_request_id=str(getattr(response, "id", "")) or None,
                ),
            )
        except Exception as error:
            raise ProviderError(
                f"OpenAI fact search failed: {type(error).__name__}: {error}"
            ) from error


class SerperVisualSearch:
    name = "serper"
    model = "images"

    def __init__(
        self, *, api_key: str | None = None, endpoint: str = "https://google.serper.dev/images"
    ):
        resolved_key = api_key or os.environ.get("SERPER_API_KEY", "")
        if not resolved_key:
            raise ProviderError("SERPER_API_KEY is required for visual search")
        self.api_key: str = resolved_key
        self.endpoint = endpoint

    def search(self, query: str) -> SearchResult:
        request = {"q": query, "num": 10}
        try:
            response = httpx.post(
                self.endpoint,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json=request,
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise ProviderError(
                f"Serper visual search failed: {type(error).__name__}: {error}"
            ) from error
        hits = []
        for row in payload.get("images", []):
            source_url = str(row.get("link") or row.get("source") or "")
            image_url = str(row.get("imageUrl") or row.get("thumbnailUrl") or "")
            if not source_url.startswith(("http://", "https://")) or not image_url.startswith(
                ("http://", "https://")
            ):
                continue
            hits.append(
                SearchHit(
                    title=str(row.get("title") or source_url),
                    url=source_url,
                    image_url=image_url,
                    publisher=str(row.get("source") or urlparse(source_url).netloc),
                )
            )
        return SearchResult(
            query=query,
            summary=f"{len(hits)} visual results",
            hits=hits,
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="visual_search",
                request_sha256=sha256_json(request),
            ),
        )
