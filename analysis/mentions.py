"""Find which brands an answer mentions, and in what order.

Pure text matching with no API calls, so it is free, instant and repeatable.
Re-run it any time you edit aliases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from core.config import Brand, Project


@dataclass
class MentionResult:
    brand_mentioned: bool
    brand_position: int | None  # 1 = our brand was the first brand named; None = not mentioned
    brands_in_order: list[str] = field(default_factory=list)  # every brand named, by first appearance
    competitors_mentioned: list[str] = field(default_factory=list)
    brand_snippets: list[str] = field(default_factory=list)  # sentences mentioning our brand


@lru_cache(maxsize=256)
def _compile(aliases: tuple[str, ...]) -> re.Pattern:
    # Longest alias first so "myHQ by ANAROCK" wins over "myHQ".
    # The lookarounds make it whole-word: "Awfis" matches, "Awfisxyz" doesn't.
    alternatives = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])", re.IGNORECASE)


def alias_pattern(brand: Brand) -> re.Pattern:
    return _compile(tuple(brand.aliases))


def first_mention(text: str, brand: Brand) -> int | None:
    match = alias_pattern(brand).search(text)
    return match.start() if match else None


def mention_snippets(text: str, brand: Brand, limit: int = 4, max_chars: int = 400) -> list[str]:
    """The sentences (or list items) that mention the brand, for sentiment scoring."""
    pattern = alias_pattern(brand)
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    snippets = []
    for piece in pieces:
        piece = piece.strip()
        if piece and pattern.search(piece):
            snippets.append(piece[:max_chars])
            if len(snippets) >= limit:
                break
    return snippets


def analyze_mentions(text: str, project: Project) -> MentionResult:
    positions = {}
    for brand in project.all_brands:
        pos = first_mention(text, brand)
        if pos is not None:
            positions[brand.name] = pos

    in_order = sorted(positions, key=positions.get)
    target = project.brand.name
    mentioned = target in positions
    return MentionResult(
        brand_mentioned=mentioned,
        brand_position=in_order.index(target) + 1 if mentioned else None,
        brands_in_order=in_order,
        competitors_mentioned=[b for b in in_order if b != target],
        brand_snippets=mention_snippets(text, project.brand) if mentioned else [],
    )


def brands_named_in(query: str, project: Project) -> list[str]:
    """Brands the question itself names (e.g. "WeWork vs Awfis"). These are prompted mentions."""
    return [b.name for b in project.all_brands if first_mention(query, b) is not None]


def domain_owner(domain: str, project: Project) -> str | None:
    """Which brand owns this domain, if any (matches subdomains too)."""
    for brand in project.all_brands:
        for d in brand.domains:
            if domain == d or domain.endswith("." + d):
                return brand.name
    return None
