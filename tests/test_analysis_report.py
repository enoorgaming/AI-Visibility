"""Offline tests for Phase 3 (analysis) and Phase 4 (report), using fake stored answers."""
import asyncio

from analysis.mentions import analyze_mentions, domain_owner
from analysis.pipeline import analyze_project
from analysis.sentiment import parse_sentiment
from core.config import load_project, load_settings
from core.storage import Store
from engines import ENGINE_CLASSES, Citation, EngineResponse
from reports.html_report import render_report
from reports.summary import rule_based_summary


def test_mentions_order_aliases_and_whole_words():
    proj = load_project("myhq")
    text = "Try Awfis first. **myHQ by ANAROCK** offers day passes. WeWork is pricey. Awfisxyz is not a brand."
    m = analyze_mentions(text, proj)
    assert m.brands_in_order == ["Awfis", "myHQ", "WeWork"]
    assert m.brand_mentioned and m.brand_position == 2
    assert m.competitors_mentioned == ["Awfis", "WeWork"]
    assert m.brand_snippets == ["**myHQ by ANAROCK** offers day passes."]

    none = analyze_mentions("Where we work matters.", proj)  # "We Work" is not an alias
    assert not none.brand_mentioned and none.brands_in_order == []


def test_domain_owner_matches_subdomains():
    proj = load_project("myhq")
    assert domain_owner("blog.myhq.in", proj) == "myHQ"
    assert domain_owner("awfis.com", proj) == "Awfis"
    assert domain_owner("reddit.com", proj) is None


def test_parse_sentiment_handles_noise():
    assert parse_sentiment('Sure! {"sentiment": "Positive", "reason": "recommended"}') == ("positive", "recommended")
    assert parse_sentiment("no json")[0] == "unknown"


def _seed(store: Store, proj) -> None:
    """Two engines x the first 3 queries, with varied brand coverage."""
    answers = {
        ("perplexity", 0): ("myHQ and Awfis are top picks.", ["https://myhq.in/a", "https://reddit.com/r/x"]),
        ("perplexity", 1): ("WeWork and Smartworks suit teams.", ["https://reddit.com/r/y"]),
        ("perplexity", 2): ("Day passes: Awfis, then myHQ.", ["https://awfis.com/day"]),
        ("gemini", 0): ("WeWork leads, then myHQ.", ["https://reddit.com/r/x", "https://news.example/z"]),
        ("gemini", 1): ("IndiQube and BHIVE.", ["https://news.example/q"]),
        ("gemini", 2): ("No brands here.", []),
    }
    run_id = store.start_run(["perplexity", "gemini"])
    for (engine, qi), (text, urls) in answers.items():
        query = proj.queries[qi]
        resp = EngineResponse(
            engine=engine, model=f"{engine}-model", query=query, answer_text=text,
            citations=[Citation(url=u, cited_in_text=True) for u in urls],
            input_tokens=100, output_tokens=50, cost_usd=0.001,
        )
        store.save_response(run_id, f"{engine}-{qi}", resp)


def test_metrics_report_and_html(tmp_path, monkeypatch):
    proj = load_project("myhq")
    proj.queries = proj.queries[:3]
    store = Store(tmp_path / "t.db")
    _seed(store, proj)

    report, analyses, spent = asyncio.run(
        analyze_project(store, proj, load_settings(), with_sentiment=False)
    )
    assert spent == 0 and report["responses"] == 6
    pplx, gem = report["per_engine"]["perplexity"], report["per_engine"]["gemini"]
    assert pplx["mention_rate"] == 2 / 3 and pplx["avg_position"] == 1.5
    assert gem["mention_rate"] == 1 / 3 and gem["avg_position"] == 2

    sov = {row["brand"]: row for row in report["share_of_voice"]}
    assert sov["myHQ"]["responses_mentioning"] == 3
    assert abs(sum(r["share_of_voice"] for r in report["share_of_voice"]) - 1) < 1e-9

    assert report["top_domains"][0]["domain"] == "reddit.com"
    gap_queries = [g["query"] for g in report["opportunity_gaps"]]
    assert gap_queries == [proj.queries[1]]  # only q1 names competitors without myHQ
    assert report["opportunity_gaps"][0]["engines_missing"] == ["gemini", "perplexity"]

    summary = rule_based_summary(report)
    assert len(summary["findings"]) == 3 and len(summary["recommendations"]) == 3

    out = render_report(report, summary, [dict(r) for r in store.spend_by_engine()], tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    assert "AI Visibility Report: myHQ" in html
    assert "can differ" in html  # API vs consumer-app caveat
    assert "Opportunity gaps" in html and "reddit.com" in html
    assert "<script" not in html and "http-equiv" not in html  # self-contained, no JS


def test_sentiment_skipped_with_warning_when_utility_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv(ENGINE_CLASSES[load_settings()["utility_llm"]["engine"]].env_key, raising=False)
    proj = load_project("myhq")
    proj.queries = proj.queries[:3]
    store = Store(tmp_path / "t.db")
    _seed(store, proj)
    warnings = []
    report, _, spent = asyncio.run(analyze_project(store, proj, load_settings(), warn=warnings.append))
    assert report["responses"] == 6 and spent == 0
    assert warnings and "Skipping sentiment" in warnings[0]
