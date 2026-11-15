"""Offline parsing tests for the OpenAI, Claude and Gemini engines, plus the engine loader."""
from core.config import load_settings
from engines import build_utility_engine, load_engines
from engines.claude_engine import ClaudeEngine
from engines.gemini_engine import GeminiEngine
from engines.openai_engine import OpenAIEngine
from tests.fixtures import CLAUDE, GEMINI, OPENAI

LOCATION = {"country": "IN", "region": "Karnataka", "city": "Bangalore", "timezone": "Asia/Kolkata",
            "latitude": 12.97, "longitude": 77.59}


def engine(cls, name, **overrides):
    return cls(load_settings()["engines"][name] | overrides, api_key="test", location=LOCATION)


def test_openai_request_and_parse():
    e = engine(OpenAIEngine, "openai")
    body = e.build_request("q")
    tool = body["tools"][0]
    assert tool["type"] == "web_search" and tool["user_location"]["type"] == "approximate"
    assert "latitude" not in tool["user_location"]
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
    assert params["tools"][0]["user_location"]["timezone"] == "Asia/Kolkata"
    assert params["fallbacks"] == "default" and params["betas"]

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
    assert body["tools"] == [{"google_search": {}}]
    assert body["toolConfig"]["retrievalConfig"]["latLng"]["latitude"] == 12.97

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
    engines, warnings = load_engines(load_settings(), None, None)
    assert [e.name for e in engines] == ["gemini"]
    assert len(warnings) == 3

    engines, _ = load_engines(load_settings(), None, None, require_key=False)  # dry run
    assert len(engines) == 4


def test_utility_engine_uses_first_candidate_with_a_key(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    util = build_utility_engine(load_settings())
    assert util.name == "claude" and util.model == "claude-haiku-5-5"
    params = util.build_request("hi")
    assert "tools" not in params and "fallbacks" not in params
    assert params["output_config"] == {"effort": "low"}
