"""Offline parsing tests for the OpenAI, Claude and Gemini engines, plus the engine loader."""
import pytest

from core.config import load_settings
from engines import ENGINE_CLASSES, EngineError, build_utility_engine, load_engines
from engines.claude_engine import ClaudeEngine
from engines.gemini_engine import GeminiEngine
from engines.openai_engine import OpenAIEngine
from tests.fixtures import CLAUDE, GEMINI, OPENAI


def engine(cls, name, **overrides):
    return cls(load_settings()["engines"][name] | overrides, api_key="test")


def test_openai_request_and_parse():
    e = engine(OpenAIEngine, "openai")
    body = e.build_request("q")
    tool = body["tools"][0]
    assert tool["type"] == "web_search" and "user_location" not in tool
    assert body["include"] == ["web_search_call.action.sources"]

    resp = e.parse_response("q", OPENAI)
    urls = [(c.url, c.cited_in_text) for c in resp.citations]
    # utm params stripped, so the cited awfis URL and the retrieved one dedupe
    assert urls == [("https://www.awfis.com/", True), ("https://myhq.in/blog", True),
                    ("https://retrieved-only.com/x", False)]
    assert resp.search_calls == 1
    assert resp.cost_usd == e.pricing.cost(5000, 800, 1)


def test_claude_request_and_parse():
    e = engine(ClaudeEngine, "claude")
    params = e.build_request("q")
    assert params["tools"][0]["type"] == "web_search_20260209"
    assert "fallbacks" not in params and "betas" not in params

    resp = e.parse_response("q", [CLAUDE])
    assert resp.answer_text == "Good options include Smartworks for large teams and IndiQube."
    assert [(c.domain, c.cited_in_text) for c in resp.citations] == [("reddit.com", True), ("smartworks.in", False)]
    assert resp.search_calls == 2 and resp.input_tokens == 12000


def test_claude_sums_usage_across_pause_turn_continuations():
    resp = engine(ClaudeEngine, "claude").parse_response("q", [CLAUDE, CLAUDE])
    assert resp.search_calls == 4 and resp.output_tokens == 1200
    assert "turns" in resp.raw


def test_gemini_request_and_parse():
    e = engine(GeminiEngine, "gemini")
    body = e.build_request("q")
    assert body["tools"] == [{"google_search": {}}] and "toolConfig" not in body

    resp = e.parse_response("q", GEMINI)
    assert resp.answer_text.startswith("BHIVE")  # thought parts skipped
    assert [(c.domain, c.cited_in_text) for c in resp.citations] == [
        ("bhiveworkspace.com", True),  # domain taken from the title
        ("vertexaisearch.cloud.google.com", False),  # title isn't a domain; resolved at runtime
    ]
    assert resp.search_calls == 2
    assert (resp.input_tokens, resp.output_tokens) == (920, 400)


def test_missing_keys_skip_engines_with_warning(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    engines, warnings = load_engines(load_settings(), None)
    assert [e.name for e in engines] == ["gemini"]
    assert len(warnings) == 3

    engines, _ = load_engines(load_settings(), None, require_key=False)  # dry run
    assert len(engines) == 4


def test_utility_engine_is_the_one_configured_model(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings()
    with pytest.raises(EngineError):  # key missing: caller warns and skips sentiment
        build_utility_engine(settings)

    monkeypatch.setenv(ENGINE_CLASSES[settings["utility_llm"]["engine"]].env_key, "x")
    util = build_utility_engine(settings)
    assert (util.name, util.model) == (settings["utility_llm"]["engine"], settings["utility_llm"]["model"])
    assert "tools" not in util.build_request("hi")  # no web search for helper tasks


def test_gemini_reasks_when_it_answers_without_searching():
    import asyncio

    ungrounded = {"candidates": [{"content": {"parts": [{"text": "from memory"}]}}],
                  "usageMetadata": {"candidatesTokenCount": 1000}}
    replies = [ungrounded, GEMINI]

    class FakeGemini(GeminiEngine):
        async def post_json(self, url, headers, body):
            return replies.pop(0)

    e = FakeGemini(load_settings()["engines"]["gemini"] | {"resolve_redirects": False}, api_key="test")
    resp = asyncio.run(e.ask("q"))
    assert resp.citations and resp.answer_text.startswith("BHIVE")  # the searched answer is kept
    assert resp.cost_usd > e.parse_response("q", GEMINI).cost_usd  # discarded attempt still counted

    replies[:] = [ungrounded] * 3
    with pytest.raises(EngineError, match="without searching"):  # never searches -> fail, not cached
        asyncio.run(e.ask("q"))
