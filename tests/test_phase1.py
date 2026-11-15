"""Offline tests for Phase 1 (Perplexity, storage, caching, config). No API calls, no keys.

Run with:  py -m pytest -q
"""
import asyncio

from core.config import load_project, load_settings
from core.query_generator import build_prompt, merge_queries, parse_queries
from core.runner import execute, plan_jobs
from core.storage import Store
from engines.perplexity import PerplexityEngine
from tests.fixtures import PERPLEXITY as SAMPLE_RESPONSE


def make_engine(**overrides) -> PerplexityEngine:
    cfg = load_settings()["engines"]["perplexity"] | overrides
    return PerplexityEngine(cfg, api_key="test")


def test_parse_response_extracts_text_citations_and_cost():
    resp = make_engine().parse_response("q", SAMPLE_RESPONSE, latency_ms=1200)
    assert resp.answer_text.startswith("Top picks are WeWork")
    assert resp.model == "perplexity/sonar"
    assert [c.url for c in resp.citations] == [  # duplicate URL dropped, order kept
        "https://www.example.com/best-coworking",
        "https://blog.other.in/wework-review",
        "https://uncited.com/page",
    ]
    assert [c.cited_in_text for c in resp.citations] == [True, True, False]
    assert resp.citations[0].domain == "example.com"
    assert (resp.cost_usd, resp.cost_source) == (0.0049, "provider")
    assert resp.search_calls == 1


def test_cost_falls_back_to_price_table_when_api_reports_none():
    data = {**SAMPLE_RESPONSE, "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
    resp = make_engine().parse_response("q", data)
    assert resp.cost_source == "estimated"
    assert resp.cost_usd == 0.25  # input_per_mtok in settings.yaml


def test_request_body_enables_web_search():
    body = make_engine().build_request("best coworking in Bangalore")
    assert body["model"] == "perplexity/sonar"
    assert body["tools"] == [{"type": "web_search", "search_type": "web"}]
    assert "tools" not in make_engine(web_search=False).build_request("hi")


def test_cache_key_changes_with_model_but_not_whitespace_or_case():
    engine = make_engine()
    assert engine.cache_key("Best  coworking ") == engine.cache_key("best coworking")
    assert engine.cache_key("best coworking") != make_engine(model="other").cache_key("best coworking")


def test_cached_queries_are_skipped(tmp_path):
    store = Store(tmp_path / "t.db")
    engine = make_engine()
    resp = engine.parse_response("q1", SAMPLE_RESPONSE)
    store.save_response(store.start_run(["perplexity"]), engine.cache_key("q1"), resp)

    jobs, stats = plan_jobs([engine], ["q1", "q2"], store)
    assert [j.query for j in jobs] == ["q2"] and stats["perplexity"].cached == 1


def test_execute_saves_responses(tmp_path):
    class FakeEngine(PerplexityEngine):
        async def ask(self, query, instructions=None):
            return self.parse_response(query, SAMPLE_RESPONSE)

    store = Store(tmp_path / "t.db")
    engine = FakeEngine(load_settings()["engines"]["perplexity"], api_key="x")
    jobs, stats = plan_jobs([engine], ["q1", "q2"], store)
    asyncio.run(execute(jobs, store, store.start_run(["perplexity"]), stats, 2, lambda *a: None))

    assert stats["perplexity"].called == 2
    assert round(stats["perplexity"].cost_usd, 4) == 0.0098
    assert len(store.latest_responses()) == 2
    assert "example.com" in [c["domain"] for c in store.citations_for([1])[1]]


def test_project_config_loads():
    proj = load_project("myhq")
    assert proj.brand.name == "myHQ" and "myHQ by ANAROCK" in proj.brand.aliases
    assert proj.brand.domains == ["myhq.in"]
    assert any(c.name == "WeWork" for c in proj.competitors)
    assert proj.queries


def test_generated_queries_parsing_drops_brand_mentions_and_dupes():
    proj = load_project("myhq")
    text = 'Here you go:\n["Best coworking in Indiranagar?", "Is myHQ good?", "Day pass prices"]'
    assert parse_queries(text, proj) == ["Best coworking in Indiranagar?", "Day pass prices"]
    assert merge_queries(["Day pass prices"], ["day pass prices", "new one"]) == ["Day pass prices", "new one"]


def test_query_prompt_is_industry_neutral():
    proj = load_project("myhq")
    proj.category = "CRM software for small businesses"
    prompt = build_prompt(proj, 15).lower()
    assert "crm software for small businesses" in prompt
    for coworking_word in ("team size", "neighbourhood", "amenities", "contract length"):
        assert coworking_word not in prompt
    assert '"myhq"' in prompt and "never mention" in prompt  # brand rule kept
