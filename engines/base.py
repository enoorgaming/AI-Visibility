"""The common interface every AI engine implements.

Each provider (Perplexity, OpenAI, Claude, Gemini) lives in its own file and
subclasses `Engine`. The rest of the tool (runner, storage, analysis) only
talks to this interface, so adding a provider never touches that code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

# HTTP status codes worth retrying: timeouts, rate limits, server overload.
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


@dataclass
class Pricing:
    """USD prices from config/settings.yaml. Provider prices change, so they live in config."""

    input_per_mtok: float = 0.0  # per 1M input tokens
    output_per_mtok: float = 0.0  # per 1M output tokens
    per_search_call: float = 0.0  # fee per web-search tool call

    def cost(self, input_tokens: int, output_tokens: int, search_calls: int = 0) -> float:
        return (
            input_tokens * self.input_per_mtok / 1_000_000
            + output_tokens * self.output_per_mtok / 1_000_000
            + search_calls * self.per_search_call
        )


@dataclass
class Citation:
    """A web source attached to an answer."""

    url: str
    title: str = ""
    # True if the answer text actually references this source (e.g. a "[2]" marker).
    # False means the engine retrieved it during search but didn't cite it.
    cited_in_text: bool = False
    domain: str = ""  # filled from the URL unless the engine knows better

    def __post_init__(self):
        self.url = clean_url(self.url)
        self.domain = (self.domain or domain_of(self.url)).lower().removeprefix("www.")


@dataclass
class EngineResponse:
    """One answer from one engine, in the same shape for every provider."""

    engine: str
    model: str
    query: str
    answer_text: str
    citations: list[Citation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0
    cost_usd: float = 0.0
    cost_source: str = "estimated"  # "provider" if the API told us the cost, else "estimated"
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)  # full untouched API response


class EngineError(Exception):
    """A request failed (bad key, bad parameters, or retries exhausted)."""


def domain_of(url: str) -> str:
    """'https://www.example.com/a/b' -> 'example.com'"""
    return urlparse(url).netloc.lower().removeprefix("www.")


def clean_url(url: str) -> str:
    """Drop tracking parameters (e.g. OpenAI adds ?utm_source=openai) so the same page dedupes."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Engine(ABC):
    name: str = ""  # short id used in config and CLI, e.g. "perplexity"
    env_key: str = ""  # .env variable holding the API key

    def __init__(
        self,
        config: dict,
        api_key: str,
        timeout: float = 120,
        max_retries: int = 3,
    ):
        self.config = config
        self.model: str = config["model"]
        self.api_key = api_key
        self.pricing = Pricing(**config.get("pricing", {}))
        self.timeout = timeout
        self.max_retries = max_retries
        self._http: httpx.AsyncClient | None = None

    # ---- what each provider must implement -------------------------------

    @abstractmethod
    async def ask(self, query: str, instructions: str | None = None) -> EngineResponse:
        """Send one query and return a normalised response.

        With web_search on (the default) this is a tracked buyer query. With it
        off, the engine works as a plain cheap LLM for helper tasks.
        """

    @property
    def web_search(self) -> bool:
        return self.config.get("web_search", True)

    # ---- caching ---------------------------------------------------------

    def cache_params(self) -> dict:
        """Everything apart from the query that can change the answer.

        Changing any of these (say, the model) produces a new cache key,
        so old answers stop counting as cache hits.
        """
        return {"model": self.model, "web_search": self.web_search}

    def cache_key(self, query: str) -> str:
        normalised_query = " ".join(query.lower().split())
        payload = {"engine": self.name, "query": normalised_query, **self.cache_params()}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]

    # ---- cost ------------------------------------------------------------

    def estimated_cost_per_call(self) -> float:
        """Rough cost for --dry-run, from the token assumptions in settings.yaml."""
        est = self.config.get("estimate", {})
        return self.pricing.cost(
            est.get("input_tokens", 0), est.get("output_tokens", 0), est.get("search_calls", 0)
        )

    # ---- HTTP helper with retries -----------------------------------------

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:  # created lazily so --dry-run never opens a connection
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def post_json(self, url: str, headers: dict, body: dict) -> dict:
        """POST JSON, retrying with exponential backoff on rate limits and server errors."""
        error = ""
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.http.post(url, headers=headers, json=body)
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS
                error = f"network error: {exc!r}"
            else:
                if resp.status_code == 200:
                    return resp.json()
                error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if resp.status_code not in RETRYABLE_STATUS:
                    raise EngineError(error)  # e.g. 401 bad key, 400 bad params
            if attempt < self.max_retries:
                await asyncio.sleep(2**attempt + random.random())
        raise EngineError(f"gave up after {self.max_retries + 1} attempts: {error}")

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
