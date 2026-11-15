"""Engine registry: maps names used in config/CLI to engine classes."""
from __future__ import annotations

import copy
import os

from .base import Citation, Engine, EngineError, EngineResponse, Pricing
from .claude_engine import ClaudeEngine
from .gemini_engine import GeminiEngine
from .openai_engine import OpenAIEngine
from .perplexity import PerplexityEngine

ENGINE_CLASSES: dict[str, type[Engine]] = {
    "perplexity": PerplexityEngine,
    "openai": OpenAIEngine,
    "claude": ClaudeEngine,
    "gemini": GeminiEngine,
}

__all__ = ["Citation", "Engine", "EngineError", "EngineResponse", "Pricing",
           "ENGINE_CLASSES", "load_engines", "build_utility_engine", "has_key"]


def has_key(engine_name: str) -> bool:
    cls = ENGINE_CLASSES.get(engine_name)
    return bool(cls and os.getenv(cls.env_key, "").strip())


def _make(cls: type[Engine], cfg: dict, api_key: str, location: dict | None, run: dict) -> Engine:
    return cls(
        cfg,
        api_key,
        location=location,
        timeout=run.get("timeout_seconds", 120),
        max_retries=run.get("max_retries", 3),
    )


def load_engines(
    settings: dict,
    names: list[str] | None,
    location: dict | None,
    require_key: bool = True,
) -> tuple[list[Engine], list[str]]:
    """Create the engines that are requested, enabled, and have an API key.

    Returns (engines, warnings). A missing key never crashes the run: that
    engine is skipped with a warning. With require_key=False (--dry-run),
    engines without keys are still returned so you can preview cost.
    """
    engine_settings = settings.get("engines", {})
    run = settings.get("run", {})
    if names is None:
        names = [n for n, cfg in engine_settings.items() if cfg.get("enabled", True)]

    engines, warnings = [], []
    for name in names:
        cls = ENGINE_CLASSES.get(name)
        cfg = engine_settings.get(name)
        if cls is None or cfg is None:
            warnings.append(f"Skipping '{name}': unknown engine (choose from {', '.join(ENGINE_CLASSES)})")
            continue
        api_key = os.getenv(cls.env_key, "").strip()
        if not api_key:
            if require_key:
                warnings.append(f"Skipping '{name}': {cls.env_key} is not set in .env")
                continue
            warnings.append(f"'{name}' has no API key ({cls.env_key}); showing estimate only")
        engines.append(_make(cls, cfg, api_key, location, run))
    return engines, warnings


def build_utility_engine(settings: dict) -> Engine:
    """A cheap model with no web search, for helper tasks (query generation, sentiment, summary).

    Uses the first candidate in settings.yaml whose API key is set.
    """
    candidates = settings["utility_llm"]["candidates"]
    for cand in candidates:
        cls = ENGINE_CLASSES[cand["engine"]]
        api_key = os.getenv(cls.env_key, "").strip()
        if not api_key:
            continue
        cfg = copy.deepcopy(settings["engines"][cand["engine"]])
        cfg.update(cand.get("overrides") or {})
        cfg.update(model=cand["model"], pricing=cand.get("pricing", {}), web_search=False)
        return _make(cls, cfg, api_key, None, settings.get("run", {}))
    keys = ", ".join(sorted({ENGINE_CLASSES[c["engine"]].env_key for c in candidates}))
    raise EngineError(f"No API key for any utility model. Set one of: {keys}")
