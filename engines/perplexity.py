"""Perplexity engine, using the Agent API (POST /v1/agent).

Perplexity ended support for Sonar Chat Completions (/v1/sonar) on
2026-09-27 and replaced it with the Agent API. We pin `model` to Perplexity's
own Sonar model and enable the built-in `web_search` tool. That is the closest
API equivalent of asking a question in the Perplexity app.

Docs: https://docs.perplexity.ai/docs/agent-api/quickstart
      https://docs.perplexity.ai/docs/agent-api/tools

Response shape (only the fields we use):
    {
      "model": "perplexity/sonar",
      "status": "completed",
      "output": [
        {"type": "search_results", "queries": [...],
         "results": [{"id": 1, "url": "...", "title": "...", ...}]},
        {"type": "message", "content": [{"type": "output_text",
                                         "text": "... [web:1] ...",
                                         "annotations": [...]}]}
      ],
      "usage": {"input_tokens": 0, "output_tokens": 0,
                "cost": {"total_cost": 0.0, ...},
                "tool_calls_details": {"search_web": {"invocation": 1}}}
    }
"""
from __future__ import annotations

import re
import time

from .base import Citation, Engine, EngineError, EngineResponse

DEFAULT_ENDPOINT = "https://api.perplexity.ai/v1/agent"

# Inline citation markers look like "[1]", "[web:1]" or "[1, 3]".
CITATION_MARKER = re.compile(r"\[((?:web:)?\d+(?:\s*,\s*(?:web:)?\d+)*)\]")


class PerplexityEngine(Engine):
    name = "perplexity"
    env_key = "PERPLEXITY_API_KEY"

    def cache_params(self) -> dict:
        return {**super().cache_params(), "search_type": self.config.get("search_type", "web")}

    def build_request(self, query: str, instructions: str | None = None) -> dict:
        body: dict = {"model": self.model, "input": query}
        if self.web_search:
            body["tools"] = [{"type": "web_search", "search_type": self.config.get("search_type", "web")}]
        if self.config.get("max_output_tokens"):
            body["max_output_tokens"] = self.config["max_output_tokens"]
        if instructions:
            body["instructions"] = instructions
        return body

    async def ask(self, query: str, instructions: str | None = None) -> EngineResponse:
        url = self.config.get("endpoint", DEFAULT_ENDPOINT)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        started = time.perf_counter()
        data = await self.post_json(url, headers, self.build_request(query, instructions))
        latency_ms = int((time.perf_counter() - started) * 1000)

        if data.get("status") == "failed" or data.get("error"):
            raise EngineError(f"Perplexity returned an error: {data.get('error')}")
        return self.parse_response(query, data, latency_ms)

    def parse_response(self, query: str, data: dict, latency_ms: int = 0) -> EngineResponse:
        """Turn Perplexity's JSON into our common EngineResponse."""
        answer = _answer_text(data)
        citations = _citations(data, answer)

        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        tool_calls = usage.get("tool_calls_details") or {}
        search_calls = (tool_calls.get("search_web") or {}).get("invocation", 0) or 0

        # Prefer the cost Perplexity reports; fall back to our own price table.
        provider_cost = (usage.get("cost") or {}).get("total_cost")
        if provider_cost is not None:
            cost, cost_source = float(provider_cost), "provider"
        else:
            cost, cost_source = self.pricing.cost(input_tokens, output_tokens, search_calls), "estimated"

        return EngineResponse(
            engine=self.name,
            model=data.get("model") or self.model,
            query=query,
            answer_text=answer,
            citations=citations,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_calls=search_calls,
            cost_usd=cost,
            cost_source=cost_source,
            latency_ms=latency_ms,
            raw=data,
        )


# ---- parsing helpers -------------------------------------------------------

def _message_parts(data: dict) -> list[dict]:
    """All 'output_text' content parts from 'message' items in the output."""
    parts = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            parts += [c for c in item.get("content") or [] if c.get("type") == "output_text"]
    return parts


def _answer_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):  # convenience field, if present
        return data["output_text"]
    return "".join(part.get("text", "") for part in _message_parts(data))


def _citations(data: dict, answer: str) -> list[Citation]:
    """Every source the search returned, flagged by whether the answer cites it."""
    # Ids referenced by inline markers in the answer, e.g. "[web:2]" -> "2"
    cited_ids = {
        num for match in CITATION_MARKER.finditer(answer) for num in re.findall(r"\d+", match.group(1))
    }
    # Some responses attach URLs as annotations instead of (or as well as) markers
    annotated_urls = {
        ann["url"]
        for part in _message_parts(data)
        for ann in part.get("annotations") or []
        if isinstance(ann, dict) and ann.get("url")
    }

    citations: dict[str, Citation] = {}  # keyed by URL to drop duplicates, keeps order
    for item in data.get("output") or []:
        if item.get("type") != "search_results":
            continue
        for result in item.get("results") or []:
            url = result.get("url")
            if not url or url in citations:
                continue
            cited = str(result.get("id")) in cited_ids or url in annotated_urls
            citations[url] = Citation(url=url, title=result.get("title") or "", cited_in_text=cited)

    for url in annotated_urls - citations.keys():  # cited, but missing from search results
        citations[url] = Citation(url=url, cited_in_text=True)

    return list(citations.values())
