"""Turn stored responses into visibility metrics.

Step 1, build_analyses(): one ResponseAnalysis per (engine, query), using the
        latest answer for each query currently in the project config.
Step 2, summarize():      aggregate into per-engine and overall metrics, share
        of voice, top cited domains and opportunity gaps. The result is a plain
        dict, so it is easy to print, pass to an LLM, or render as HTML.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean

from core.config import Project
from core.storage import Store

from .mentions import MentionResult, analyze_mentions, brands_named_in, domain_owner


@dataclass
class ResponseAnalysis:
    response_id: int
    engine: str
    model: str
    query: str
    created_at: str
    mention: MentionResult
    prompted_brands: list[str]  # brands the question itself names
    cited_domains: list[str]  # unique domains the answer cites, in order
    sentiment: str | None = None
    sentiment_reason: str = ""


def build_analyses(store: Store, project: Project) -> list[ResponseAnalysis]:
    wanted = set(project.queries)
    rows = [r for r in store.latest_responses() if r["query"] in wanted]
    citations = store.citations_for([r["id"] for r in rows])

    analyses = []
    for row in rows:
        cits = citations[row["id"]]
        # Prefer sources the answer explicitly cites. If an engine marked none
        # as cited, fall back to everything it retrieved.
        used = [c for c in cits if c["cited_in_text"]] or cits
        domains = list(dict.fromkeys(c["domain"] for c in used if c["domain"]))
        analyses.append(
            ResponseAnalysis(
                response_id=row["id"],
                engine=row["engine"],
                model=row["model"],
                query=row["query"],
                created_at=row["created_at"],
                mention=analyze_mentions(row["answer_text"] or "", project),
                prompted_brands=brands_named_in(row["query"], project),
                cited_domains=domains,
            )
        )
    return analyses


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _engine_metrics(items: list[ResponseAnalysis]) -> dict:
    mentioned = [a for a in items if a.mention.brand_mentioned]
    positions = [a.mention.brand_position for a in mentioned]
    sentiments = Counter(a.sentiment for a in mentioned if a.sentiment)
    return {
        "responses": len(items),
        "mentioned": len(mentioned),
        "mention_rate": _rate(len(mentioned), len(items)),
        "avg_position": mean(positions) if positions else None,
        "sentiment": {label: sentiments.get(label, 0) for label in ("positive", "neutral", "negative", "unknown")},
        "avg_sources_cited": mean(len(a.cited_domains) for a in items) if items else None,
    }


def summarize(analyses: list[ResponseAnalysis], project: Project) -> dict:
    by_engine: dict[str, list[ResponseAnalysis]] = defaultdict(list)
    for a in analyses:
        by_engine[a.engine].append(a)
    engines = sorted(by_engine)

    # ---- share of voice: how often each brand appears, relative to all brands
    brand_names = [b.name for b in project.all_brands]
    counts = Counter(name for a in analyses for name in a.mention.brands_in_order)
    total_mentions = sum(counts.values())
    share_of_voice = sorted(
        (
            {
                "brand": name,
                "is_target": name == project.brand.name,
                "responses_mentioning": counts.get(name, 0),
                "mention_rate": _rate(counts.get(name, 0), len(analyses)),
                "share_of_voice": _rate(counts.get(name, 0), total_mentions),
            }
            for name in brand_names
        ),
        key=lambda row: row["responses_mentioning"],
        reverse=True,
    )

    # ---- top cited domains (each response counts a domain once)
    domain_responses: Counter = Counter()
    domain_engines: dict[str, set] = defaultdict(set)
    for a in analyses:
        for d in a.cited_domains:
            domain_responses[d] += 1
            domain_engines[d].add(a.engine)
    top_domains = [
        {
            "domain": d,
            "responses": n,
            "share": _rate(n, len(analyses)),
            "engines": sorted(domain_engines[d]),
            "owner": domain_owner(d, project),
        }
        for d, n in domain_responses.most_common(15)
    ]

    # ---- opportunity gaps: brand missing while competitors appear
    by_query: dict[str, list[ResponseAnalysis]] = defaultdict(list)
    for a in analyses:
        by_query[a.query].append(a)
    gaps = []
    for query in project.queries:
        items = by_query.get(query, [])
        missing = [a for a in items if not a.mention.brand_mentioned and a.mention.competitors_mentioned]
        if not missing:
            continue
        competitors = Counter(c for a in missing for c in a.mention.competitors_mentioned)
        domains = Counter(d for a in missing for d in a.cited_domains)
        gaps.append(
            {
                "query": query,
                "engines_missing": sorted(a.engine for a in missing),
                "engines_answered": len(items),
                "competitors": [name for name, _ in competitors.most_common()],
                "top_domains": [d for d, _ in domains.most_common(5)],
                "prompted_competitors": items[0].prompted_brands if items else [],
            }
        )
    gaps.sort(key=lambda g: (len(g["engines_missing"]), len(g["competitors"])), reverse=True)

    # ---- per-query grid: brand position per engine (0 = not mentioned)
    grid = []
    for query in project.queries:
        row = {"query": query, "engines": {}}
        for a in by_query.get(query, []):
            row["engines"][a.engine] = a.mention.brand_position or 0
        grid.append(row)

    dates = sorted(a.created_at for a in analyses)
    return {
        "brand": project.brand.name,
        "category": project.category,
        "engines": engines,
        "models": {e: sorted({a.model for a in by_engine[e]}) for e in engines},
        "queries_total": len(project.queries),
        "queries_answered": len(by_query),
        "responses": len(analyses),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "overall": _engine_metrics(analyses),
        "per_engine": {e: _engine_metrics(by_engine[e]) for e in engines},
        "share_of_voice": share_of_voice,
        "top_domains": top_domains,
        "domain_owners": {d: o for d in domain_responses if (o := domain_owner(d, project))},
        "opportunity_gaps": gaps,
        "query_grid": grid,
    }
