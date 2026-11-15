"""Write 3 key findings and 3 recommendations from the metrics, using fixed rules.

Deterministic and free: every sentence can be traced back to a metric.
"""
from __future__ import annotations

from collections import Counter


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def rule_based_summary(report: dict) -> dict:
    brand, overall = report["brand"], report["overall"]
    per_engine = report["per_engine"]
    sov = report["share_of_voice"]
    gaps = report["opportunity_gaps"]
    findings, recs = [], []

    # Finding 1: overall visibility, best vs worst engine
    ranked = sorted(per_engine.items(), key=lambda kv: kv[1]["mention_rate"] or 0, reverse=True)
    line = f"{brand} appears in {pct(overall['mention_rate'])} of {overall['responses']} AI answers"
    if len(ranked) > 1:
        (best, b), (worst, w) = ranked[0], ranked[-1]
        line += f", from {pct(b['mention_rate'])} on {best} down to {pct(w['mention_rate'])} on {worst}"
    if overall["avg_position"]:
        line += f". When mentioned, it is on average brand #{overall['avg_position']:.1f} in the answer"
    findings.append(line + ".")

    # Finding 2: share of voice vs the leader
    rank = next((i for i, row in enumerate(sov, 1) if row["is_target"]), None)
    target_row = next((row for row in sov if row["is_target"]), None)
    leader = sov[0] if sov else None
    if leader and target_row:
        if leader["is_target"]:
            findings.append(
                f"{brand} leads share of voice at {pct(target_row['share_of_voice'])} of all brand "
                f"mentions, ahead of {sov[1]['brand'] if len(sov) > 1 else 'competitors'}."
            )
        else:
            findings.append(
                f"{brand} ranks #{rank} of {len(sov)} brands on share of voice "
                f"({pct(target_row['share_of_voice'])}); {leader['brand']} leads with "
                f"{pct(leader['share_of_voice'])} and appears in {pct(leader['mention_rate'])} of answers."
            )

    # Finding 3: opportunity gaps
    if gaps:
        top_comp = _most_common([c for g in gaps for c in g["competitors"]])
        findings.append(
            f"{len(gaps)} of {report['queries_answered']} queries are opportunity gaps: competitors "
            f"(most often {top_comp}) are recommended but {brand} is not, on at least one engine."
        )
    else:
        findings.append(f"No opportunity gaps: whenever competitors appear, {brand} appears too.")

    # Recommendation 1: get onto the sources engines cite for gap queries
    gap_domains = _most_common_n([d for g in gaps for d in g["top_domains"]], 3)
    third_party = [d for d in gap_domains if not _owned(report, d)]
    if third_party:
        recs.append(
            f"Get {brand} listed or reviewed on the sources engines cite for gap queries: "
            f"{', '.join(third_party)}. These pages shape the answers where {brand} is missing."
        )
    else:
        top = [d["domain"] for d in report["top_domains"] if not _owned(report, d["domain"])][:3]
        recs.append(f"Strengthen presence on the most-cited third-party sources: {', '.join(top) or 'n/a'}.")

    # Recommendation 2: content for the biggest gap queries
    if gaps:
        examples = "; ".join(f'"{g["query"]}"' for g in gaps[:2])
        recs.append(
            f"Publish pages that directly answer the gap queries (e.g. {examples}) with clear, specific "
            f"facts buyers compare on (features, pricing, who it's for, how it compares), so engines "
            f"have {brand} content to cite."
        )
    else:
        recs.append("Expand the query set to more specific buyer needs to find where visibility drops.")

    # Recommendation 3: weakest engine
    if len(ranked) > 1:
        worst = ranked[-1][0]
        recs.append(f"Focus on {worst}, the weakest engine: study the sources it cites and target them first.")
    else:
        recs.append("Add more engines (API keys) to compare visibility across platforms.")

    return {"findings": findings[:3], "recommendations": recs[:3]}


def _most_common(items: list[str]) -> str:
    return _most_common_n(items, 1)[0] if items else "n/a"


def _most_common_n(items: list[str], n: int) -> list[str]:
    return [x for x, _ in Counter(items).most_common(n)]


def _owned(report: dict, domain: str) -> bool:
    """True if the domain belongs to our brand or a competitor (not a neutral third party)."""
    return domain in report.get("domain_owners", {})
