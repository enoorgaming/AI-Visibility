"""One call that runs the whole analysis: mentions, sentiment, then aggregate metrics."""
from __future__ import annotations

from collections.abc import Callable

from core.config import Project
from core.storage import Store
from engines import EngineError, build_utility_engine

from .metrics import ResponseAnalysis, build_analyses, summarize
from .sentiment import SentimentJob, score_sentiments


async def analyze_project(
    store: Store,
    project: Project,
    settings: dict,
    with_sentiment: bool = True,
    warn: Callable[[str], None] = print,
) -> tuple[dict, list[ResponseAnalysis], float]:
    """Returns (summary dict, per-response analyses, new sentiment spend in USD)."""
    analyses = build_analyses(store, project)
    spent = 0.0

    jobs = [
        SentimentJob(a.response_id, a.query, a.mention.brand_snippets)
        for a in analyses
        if a.mention.brand_mentioned and a.mention.brand_snippets
    ]
    if with_sentiment and jobs:
        try:
            utility = build_utility_engine(settings)
        except EngineError as exc:
            warn(f"Skipping sentiment: {exc}")
        else:
            try:
                results = await score_sentiments(
                    utility, store, project.brand.name, jobs, settings.get("run", {}).get("concurrency", 3)
                )
            finally:
                await utility.aclose()
            for a in analyses:
                if a.response_id in results:
                    a.sentiment = results[a.response_id].sentiment
                    a.sentiment_reason = results[a.response_id].reason
            spent = sum(r.cost_usd for r in results.values() if not r.cached)

    return summarize(analyses, project), analyses, spent
