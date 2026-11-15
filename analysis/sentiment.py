"""Score how an answer portrays our brand (positive / neutral / negative) with a cheap model.

Only the sentences that mention the brand are sent, which keeps each call to
a few hundred tokens. Results are cached in SQLite by response and excerpt
text, so re-running analysis never pays twice.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass

from core.storage import Store
from engines import Engine, EngineError

LABELS = ("positive", "neutral", "negative")

PROMPT = """You are rating how an AI assistant's answer portrays the brand "{brand}".

The user asked: "{query}"

Excerpts from the answer that mention {brand}:
---
{snippets}
---

Definitions:
- positive: {brand} is recommended, praised, or presented as a strong option
- neutral: {brand} is listed or described factually, without a judgement
- negative: {brand} is criticised, warned against, or compared unfavourably

Reply with ONLY this JSON, nothing else:
{{"sentiment": "positive" | "neutral" | "negative", "reason": "<at most 15 words>"}}"""


@dataclass
class SentimentJob:
    response_id: int
    query: str
    snippets: list[str]


@dataclass
class SentimentResult:
    sentiment: str
    reason: str
    cost_usd: float = 0.0
    cached: bool = False


def input_hash(brand: str, snippets: list[str]) -> str:
    return hashlib.sha256(json.dumps([brand, snippets]).encode()).hexdigest()[:24]


def parse_sentiment(text: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    label = str(data.get("sentiment", "")).strip().lower()
    return (label if label in LABELS else "unknown"), str(data.get("reason", "")).strip()


async def score_sentiments(
    utility: Engine, store: Store, brand: str, jobs: list[SentimentJob], concurrency: int = 4
) -> dict[int, SentimentResult]:
    """Returns {response_id: SentimentResult}. Uses the cache where possible."""
    results: dict[int, SentimentResult] = {}
    todo = []
    for job in jobs:
        key = input_hash(brand, job.snippets)
        row = store.get_sentiment(job.response_id, key)
        if row:
            results[job.response_id] = SentimentResult(row["sentiment"], row["reason"] or "", 0.0, True)
        else:
            todo.append((job, key))

    limit = asyncio.Semaphore(concurrency)

    async def run(job: SentimentJob, key: str) -> None:
        prompt = PROMPT.format(brand=brand, query=job.query, snippets="\n".join(f"- {s}" for s in job.snippets))
        async with limit:
            try:
                resp = await utility.ask(prompt)
            except EngineError as exc:
                results[job.response_id] = SentimentResult("unknown", f"scoring failed: {exc}"[:200])
                return
        label, reason = parse_sentiment(resp.answer_text)
        store.save_sentiment(job.response_id, key, label, reason, resp.model, resp.cost_usd)
        results[job.response_id] = SentimentResult(label, reason, resp.cost_usd)

    await asyncio.gather(*(run(job, key) for job, key in todo))
    return results
