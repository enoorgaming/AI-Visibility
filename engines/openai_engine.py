"""OpenAI engine, using the Responses API with the built-in `web_search` tool.

Docs: https://developers.openai.com/api/docs/guides/tools-web-search

Response shape (only the fields we use):
    {
      "model": "gpt-6-sol",
      "status": "completed",
      "output": [
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "search", "query": "...",
                    "sources": [{"type": "url", "url": "..."}]}},   # needs `include`
        {"type": "message", "content": [
            {"type": "output_text", "text": "...",
             "annotations": [{"type": "url_citation", "url": "...", "title": "...",
                              "start_index": 10, "end_index": 50}]}]}
      ],
      "usage": {"input_tokens": 0, "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0}}
    }

OpenAI doesn't return a cost, so cost is computed from settings.yaml prices.
(The file is called openai_engine.py so it doesn't shadow the `openai` package.)
"""
from __future__ import annotations

import time

from .base import Citation, Engine, EngineError, EngineResponse

DEFAULT_ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAIEngine(Engine):
    name = "openai"
    env_key = "OPENAI_API_KEY"

    def cache_params(self) -> dict:
        return {
            **super().cache_params(),
            "search_context_size": self.config.get("search_context_size"),
            "reasoning_effort": self.config.get("reasoning_effort"),
        }

    def build_request(self, query: str, instructions: str | None = None) -> dict:
        body: dict = {"model": self.model, "input": query, "store": False}
        if self.web_search:
            tool: dict = {"type": "web_search"}
            if self.config.get("search_context_size"):
                tool["search_context_size"] = self.config["search_context_size"]
            location = _location(self.location)
            if location:
                tool["user_location"] = location
            body["tools"] = [tool]
            # Also return every URL the search looked at, not just the cited ones
            body["include"] = ["web_search_call.action.sources"]
        if self.config.get("max_output_tokens"):
            body["max_output_tokens"] = self.config["max_output_tokens"]
        if self.config.get("reasoning_effort"):
            body["reasoning"] = {"effort": self.config["reasoning_effort"]}
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
            raise EngineError(f"OpenAI returned an error: {data.get('error')}")
        return self.parse_response(query, data, latency_ms)

    def parse_response(self, query: str, data: dict, latency_ms: int = 0) -> EngineResponse:
        texts, cited, retrieved, search_calls = [], {}, [], 0

        for item in data.get("output") or []:
            if item.get("type") == "web_search_call":
                action = item.get("action") or {}
                if action.get("type", "search") == "search":
                    search_calls += 1
                retrieved += [s["url"] for s in action.get("sources") or [] if s.get("url")]
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") != "output_text":
                        continue
                    texts.append(part.get("text", ""))
                    for ann in part.get("annotations") or []:
                        if ann.get("type") == "url_citation" and ann.get("url"):
                            cited.setdefault(ann["url"], ann.get("title") or "")

        # Cited sources first (in order of appearance), then sources only retrieved
        citations: dict[str, Citation] = {}
        for url, title in cited.items():
            c = Citation(url=url, title=title, cited_in_text=True)
            citations.setdefault(c.url, c)
        for url in retrieved:
            c = Citation(url=url)
            citations.setdefault(c.url, c)

        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0  # includes reasoning tokens

        return EngineResponse(
            engine=self.name,
            model=data.get("model") or self.model,
            query=query,
            answer_text="\n".join(texts),
            citations=list(citations.values()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_calls=search_calls,
            cost_usd=self.pricing.cost(input_tokens, output_tokens, search_calls),
            cost_source="estimated",
            latency_ms=latency_ms,
            raw=data,
        )


def _location(loc: dict | None) -> dict | None:
    """OpenAI wants {"type": "approximate", country, region, city, timezone}."""
    if not loc:
        return None
    fields = {k: loc[k] for k in ("country", "region", "city", "timezone") if loc.get(k)}
    return {"type": "approximate", **fields} if fields else None
