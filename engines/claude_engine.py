"""Claude engine, using the official Anthropic SDK with the server-side web search tool.

Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool

Web search runs on Anthropic's servers within one API call. The response
`content` is a list of blocks:
    {"type": "server_tool_use", "name": "web_search", "input": {"query": "..."}}
    {"type": "web_search_tool_result",
     "content": [{"type": "web_search_result", "url": "...", "title": "..."}]}
    {"type": "text", "text": "...",
     "citations": [{"type": "web_search_result_location", "url": "...", "title": "..."}]}
usage.server_tool_use.web_search_requests counts the searches ($10 per 1,000).

Long searches can stop with stop_reason "pause_turn". We then send the partial
answer back so Claude can carry on, up to a few times.
(The file is called claude_engine.py so it doesn't shadow the `anthropic` package.)
"""
from __future__ import annotations

import time

import anthropic

from .base import Citation, Engine, EngineError, EngineResponse

WEB_SEARCH_TOOL = "web_search_20260209"  # current version for Opus 5.5 / Sonnet 5.5
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 3


class ClaudeEngine(Engine):
    name = "claude"
    env_key = "ANTHROPIC_API_KEY"

    _client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:  # created lazily so --dry-run never needs a key
            self._client = anthropic.AsyncAnthropic(
                api_key=self.api_key, timeout=self.timeout, max_retries=self.max_retries
            )
        return self._client

    def cache_params(self) -> dict:
        return {
            **super().cache_params(),
            "effort": self.config.get("effort"),
            "max_searches": self.config.get("max_searches"),
        }

    def build_request(self, query: str, instructions: str | None = None) -> dict:
        params: dict = {
            "model": self.model,
            "max_tokens": self.config.get("max_tokens", 16000),
            "messages": [{"role": "user", "content": query}],
        }
        if self.web_search:
            tool: dict = {"type": WEB_SEARCH_TOOL, "name": "web_search"}
            if self.config.get("max_searches"):
                tool["max_uses"] = self.config["max_searches"]
            location = _location(self.location)
            if location:
                tool["user_location"] = location
            params["tools"] = [tool]
        if self.config.get("effort"):
            params["output_config"] = {"effort": self.config["effort"]}
        if self.config.get("fallbacks"):
            # If a safety classifier declines, the API retries on a fallback model
            # within the same call instead of returning an empty refusal.
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = self.config["fallbacks"]
        if instructions:
            params["system"] = instructions
        return params

    async def ask(self, query: str, instructions: str | None = None) -> EngineResponse:
        params = self.build_request(query, instructions)
        turns: list[dict] = []
        started = time.perf_counter()
        try:
            for _ in range(MAX_CONTINUATIONS + 1):
                message = await self.client.beta.messages.create(**params)
                turns.append(message.to_dict())
                if message.stop_reason != "pause_turn":
                    break
                # Send the paused turn back unchanged so Claude continues where it left off
                params["messages"] = [
                    params["messages"][0],
                    {"role": "assistant", "content": message.content},
                ]
        except anthropic.APIStatusError as exc:
            raise EngineError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise EngineError(f"network error: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if turns[-1].get("stop_reason") == "refusal":
            raise EngineError(f"Claude declined to answer: {turns[-1].get('stop_details')}")
        return self.parse_response(query, turns, latency_ms)

    def parse_response(self, query: str, turns: list[dict], latency_ms: int = 0) -> EngineResponse:
        """`turns` is one message dict, or several if the answer needed pause_turn continuations."""
        texts, cited, retrieved = [], {}, {}
        input_tokens = output_tokens = search_calls = 0

        for turn in turns:
            for block in turn.get("content") or []:
                kind = block.get("type")
                if kind == "text":
                    texts.append(block.get("text", ""))
                    for cit in block.get("citations") or []:
                        if cit.get("url"):
                            cited.setdefault(cit["url"], cit.get("title") or "")
                elif kind == "web_search_tool_result":
                    results = block.get("content")
                    if isinstance(results, list):  # an error comes back as a dict, not a list
                        for r in results:
                            if r.get("url"):
                                retrieved.setdefault(r["url"], r.get("title") or "")

            usage = turn.get("usage") or {}
            input_tokens += (
                (usage.get("input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
            )
            output_tokens += usage.get("output_tokens") or 0
            search_calls += (usage.get("server_tool_use") or {}).get("web_search_requests") or 0

        citations: dict[str, Citation] = {}
        for url, title in cited.items():
            c = Citation(url=url, title=title, cited_in_text=True)
            citations.setdefault(c.url, c)
        for url, title in retrieved.items():
            c = Citation(url=url, title=title)
            citations.setdefault(c.url, c)

        last = turns[-1]
        return EngineResponse(
            engine=self.name,
            model=last.get("model") or self.model,
            query=query,
            answer_text="".join(texts).strip(),
            citations=list(citations.values()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_calls=search_calls,
            cost_usd=self.pricing.cost(input_tokens, output_tokens, search_calls),
            cost_source="estimated",
            latency_ms=latency_ms,
            raw=last if len(turns) == 1 else {"turns": turns},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
        await super().aclose()


def _location(loc: dict | None) -> dict | None:
    """Claude wants {"type": "approximate", city, region, country, timezone}."""
    if not loc:
        return None
    fields = {k: loc[k] for k in ("city", "region", "country", "timezone") if loc.get(k)}
    return {"type": "approximate", **fields} if fields else None
