"""Gemini engine, using generateContent with Grounding with Google Search.

Docs: https://ai.google.dev/gemini-api/docs/google-search
(Google also offers a newer "Interactions" API. generateContent is still fully
supported and its grounding metadata is well documented, so we use it.)

Response shape (only the fields we use):
    {
      "candidates": [{
        "content": {"parts": [{"text": "..."}]},
        "groundingMetadata": {
          "webSearchQueries": ["..."],
          "groundingChunks": [{"web": {"uri": "https://vertexaisearch...", "title": "example.com"}}],
          "groundingSupports": [{"segment": {...}, "groundingChunkIndices": [0, 2]}]
        }
      }],
      "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0,
                        "thoughtsTokenCount": 0, "toolUsePromptTokenCount": 0},
      "modelVersion": "gemini-3.8-flash"
    }

Gemini's source URIs are Google redirect links. The real site is usually in
`title` (e.g. "example.com"). With resolve_redirects on, we also follow each
redirect once to recover the real URL.
"""
from __future__ import annotations

import asyncio
import re
import time

from .base import Citation, Engine, EngineError, EngineResponse

DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
REDIRECT_HOST = "vertexaisearch.cloud.google.com"
LOOKS_LIKE_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", re.IGNORECASE)


class GeminiEngine(Engine):
    name = "gemini"
    env_key = "GEMINI_API_KEY"

    def cache_params(self) -> dict:
        return {**super().cache_params(), "search_instruction": self._search_instruction()}

    def _search_instruction(self) -> str | None:
        # Gemini decides for itself whether to search, and often skips it for evergreen
        # questions. This instruction (from settings.yaml) asks it to always search first.
        return self.config.get("search_instruction") if self.web_search else None

    def build_request(self, query: str, instructions: str | None = None) -> dict:
        body: dict = {"contents": [{"role": "user", "parts": [{"text": query}]}]}
        if self.web_search:
            body["tools"] = [{"google_search": {}}]
            instructions = instructions or self._search_instruction()
        if self.config.get("max_output_tokens"):
            body["generationConfig"] = {"maxOutputTokens": self.config["max_output_tokens"]}
        if instructions:
            body["systemInstruction"] = {"parts": [{"text": instructions}]}
        return body

    async def ask(self, query: str, instructions: str | None = None) -> EngineResponse:
        endpoint = self.config.get("endpoint", DEFAULT_ENDPOINT)
        url = f"{endpoint}/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}

        # Gemini sometimes answers from memory even when told to search. Those answers have
        # no sources, so re-ask; if it never searches, fail (not cached, retried next run).
        attempts = 1 + (self.config.get("search_retries", 2) if self.web_search else 0)
        wasted = 0.0  # cost of answers we threw away for not searching
        started = time.perf_counter()
        for _ in range(attempts):
            data = await self.post_json(url, headers, self.build_request(query, instructions))
            if not data.get("candidates"):
                raise EngineError(f"Gemini returned no answer: {data.get('promptFeedback')}")
            resp = self.parse_response(query, data)
            if not self.web_search or resp.citations:
                break
            wasted += resp.cost_usd
        else:
            raise EngineError(f"Gemini answered without searching {attempts} times (~${wasted:.4f} spent); "
                              "not saved, will retry next run")
        resp.cost_usd += wasted
        resp.latency_ms = int((time.perf_counter() - started) * 1000)
        if self.config.get("resolve_redirects", True):
            await self._resolve_redirects(resp.citations)
        return resp

    def parse_response(self, query: str, data: dict, latency_ms: int = 0) -> EngineResponse:
        candidate = data["candidates"][0]
        parts = (candidate.get("content") or {}).get("parts") or []
        answer = "".join(p.get("text", "") for p in parts if not p.get("thought"))

        grounding = candidate.get("groundingMetadata") or {}
        chunks = grounding.get("groundingChunks") or []
        cited_indices = {
            i for support in grounding.get("groundingSupports") or [] for i in support.get("groundingChunkIndices") or []
        }
        citations: dict[str, Citation] = {}
        for i, chunk in enumerate(chunks):
            web = chunk.get("web") or {}
            if not web.get("uri"):
                continue
            title = web.get("title") or ""
            # The URI is a Google redirect, so take the domain from the title when it looks like one
            domain = web.get("domain") or (title if LOOKS_LIKE_DOMAIN.match(title) else "")
            c = Citation(url=web["uri"], title=title, cited_in_text=i in cited_indices, domain=domain)
            citations.setdefault(c.url, c)

        usage = data.get("usageMetadata") or {}
        input_tokens = (usage.get("promptTokenCount") or 0) + (usage.get("toolUsePromptTokenCount") or 0)
        output_tokens = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
        # Gemini 3+ bills grounding per search query the model runs
        search_calls = len(grounding.get("webSearchQueries") or [])

        return EngineResponse(
            engine=self.name,
            model=data.get("modelVersion") or self.model,
            query=query,
            answer_text=answer,
            citations=list(citations.values()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_calls=search_calls,
            cost_usd=self.pricing.cost(input_tokens, output_tokens, search_calls),
            cost_source="estimated",
            latency_ms=latency_ms,
            raw=data,
        )

    async def _resolve_redirects(self, citations: list[Citation]) -> None:
        """Swap Google redirect links for the real URL (best effort, never fails the call)."""

        async def resolve(c: Citation) -> None:
            if REDIRECT_HOST not in c.url:
                return
            try:
                r = await self.http.get(c.url, follow_redirects=False, timeout=10)
                target = r.headers.get("location")
            except Exception:
                return
            if target:
                fresh = Citation(url=target)
                c.url, c.domain = fresh.url, fresh.domain

        await asyncio.gather(*(resolve(c) for c in citations))
