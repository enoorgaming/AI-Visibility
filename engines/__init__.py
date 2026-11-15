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


def _make(cls: type[Engine], cfg: dict, api_key: str, run: dict) -> Engine:
    return cls(cfg, api_key, timeout=run.get("timeout_seconds", 120), max_retries=run.get("max_retries", 3))


def load_engines(
    settings: dict, names: list[str] | None, require_key: bool = True
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
        engines.append(_make(cls, cfg, api_key, run))
    return engines, warnings


def build_utility_engine(settings: dict) -> Engine:
    """The one cheap model (no web search) used for sentiment and query generation.

    Configured under `utility_llm` in settings.yaml. Raises EngineError if its key is missing.
    """
    util = settings["utility_llm"]
    cls = ENGINE_CLASSES[util["engine"]]
    api_key = os.getenv(cls.env_key, "").strip()
    if not api_key:
        raise EngineError(f"{cls.env_key} is not set in .env (needed for the utility model {util['model']})")
    cfg = copy.deepcopy(settings["engines"][util["engine"]])
    cfg.update(model=util["model"], pricing=util.get("pricing", {}), web_search=False)
    return _make(cls, cfg, api_key, settings.get("run", {}))
