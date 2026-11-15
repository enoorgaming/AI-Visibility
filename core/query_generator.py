"""Auto-generate realistic buyer-intent queries for a category with a cheap model."""
from __future__ import annotations

import json
import re

from engines import Engine

from .config import Project

PROMPT = """You are helping measure how brands appear in AI search answers.

Write {count} realistic questions that a potential buyer would type into an AI
assistant (ChatGPT, Perplexity, Gemini, Claude) while researching: {category}.

Cover a mix of intents:
- discovery ("best ...", "top ...")
- specific needs (team size, budget, neighbourhood, amenities, contract length)
- pricing and cost comparisons
- trust and reviews
- comparisons and alternatives

Rules:
- Write them the way a real person types: vary the length, tone and detail.
- NEVER mention "{brand}". We are measuring whether AI engines bring it up unprompted.
- At most 3 questions may name these competitors (e.g. "alternatives to X"): {competitors}.
- Return ONLY a JSON array of strings, with no other text."""


def build_prompt(project: Project, count: int) -> str:
    return PROMPT.format(
        count=count,
        category=project.category,
        brand=project.brand.name,
        competitors=", ".join(c.name for c in project.competitors) or "none",
    )


def parse_queries(text: str, project: Project) -> list[str]:
    """Pull the JSON array out of the model's reply and drop anything naming our brand."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"Model did not return a JSON list:\n{text[:500]}")
    items = json.loads(match.group(0))
    queries = [str(q).strip() for q in items if str(q).strip()]
    aliases = [a.lower() for a in project.brand.aliases]
    return [q for q in queries if not any(a in q.lower() for a in aliases)]


async def generate_queries(utility: Engine, project: Project, count: int) -> tuple[list[str], float]:
    """Returns (queries, cost in USD)."""
    try:
        resp = await utility.ask(build_prompt(project, count))
    finally:
        await utility.aclose()
    return parse_queries(resp.answer_text, project), resp.cost_usd


def merge_queries(existing: list[str], new: list[str]) -> list[str]:
    """Append new queries, skipping case-insensitive duplicates."""
    seen = {q.lower() for q in existing}
    merged = list(existing)
    for q in new:
        if q.lower() not in seen:
            merged.append(q)
            seen.add(q.lower())
    return merged
