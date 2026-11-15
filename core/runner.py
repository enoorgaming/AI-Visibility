"""Plan and execute (engine x query) calls, with caching and cost tracking.

Two steps, so --dry-run can share the planning logic:
  1. plan_jobs(): decides which calls are needed and which are already cached
  2. execute():   makes the calls concurrently and saves each result as it lands
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from engines import Engine, EngineError, EngineResponse

from .storage import Store


@dataclass
class Job:
    engine: Engine
    query: str
    cache_key: str


@dataclass
class EngineStats:
    """Per-engine counters for one run."""

    planned: int = 0
    cached: int = 0
    called: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0  # portion of cost_usd we computed ourselves (API gave none)

    def add(self, resp: EngineResponse) -> None:
        self.called += 1
        self.input_tokens += resp.input_tokens
        self.output_tokens += resp.output_tokens
        self.cost_usd += resp.cost_usd
        if resp.cost_source != "provider":
            self.estimated_cost_usd += resp.cost_usd


def plan_jobs(
    engines: list[Engine], queries: list[str], store: Store
) -> tuple[list[Job], dict[str, EngineStats]]:
    """Return the calls to make. Queries that already have a stored answer are skipped."""
    jobs: list[Job] = []
    stats = {e.name: EngineStats() for e in engines}
    for engine in engines:
        for query in queries:
            key = engine.cache_key(query)
            stats[engine.name].planned += 1
            if store.is_cached(key):
                stats[engine.name].cached += 1
            else:
                jobs.append(Job(engine, query, key))
    return jobs, stats


# Called after each job: (job, response or None, error message or None)
ProgressCallback = Callable[[Job, EngineResponse | None, str | None], None]


async def execute(
    jobs: list[Job],
    store: Store,
    run_id: int,
    stats: dict[str, EngineStats],
    concurrency: int,
    on_progress: ProgressCallback,
) -> None:
    """Run all jobs; each engine gets its own concurrency limit so one slow provider doesn't block others."""
    limits = {name: asyncio.Semaphore(concurrency) for name in stats}

    async def run_one(job: Job) -> None:
        async with limits[job.engine.name]:
            try:
                resp = await job.engine.ask(job.query)
            except EngineError as exc:
                stats[job.engine.name].failed += 1
                on_progress(job, None, str(exc))
                return
        store.save_response(run_id, job.cache_key, resp)  # saved immediately, so a crash loses nothing
        stats[job.engine.name].add(resp)
        on_progress(job, resp, None)

    await asyncio.gather(*(run_one(job) for job in jobs))
