"""Load the global settings and per-project YAML configs."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
PROJECTS_DIR = CONFIG_DIR / "projects"
DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"

# Round-trip mode: when we write queries back, your comments and ordering survive.
_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096


@dataclass
class Brand:
    name: str
    aliases: list[str]
    domains: list[str] = field(default_factory=list)  # the brand's own websites


@dataclass
class Project:
    key: str  # file name without .yaml, e.g. "myhq"
    path: Path
    brand: Brand
    category: str
    competitors: list[Brand]
    queries: list[str]

    @property
    def db_path(self) -> Path:
        return DATA_DIR / f"{self.key}.db"

    @property
    def all_brands(self) -> list[Brand]:
        """Target brand first, then competitors."""
        return [self.brand, *self.competitors]


def _plain(obj):
    """Convert ruamel's comment-aware containers into plain dicts and lists."""
    return json.loads(json.dumps(obj))


def _read_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return _yaml.load(f)


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    return _plain(_read_yaml(path))


def resolve_project_path(name_or_path: str) -> Path:
    """Accept 'myhq', 'myhq.yaml' or a full path."""
    candidates = [Path(name_or_path), PROJECTS_DIR / name_or_path, PROJECTS_DIR / f"{name_or_path}.yaml"]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    available = ", ".join(p.stem for p in PROJECTS_DIR.glob("*.yaml")) or "none"
    raise FileNotFoundError(f"Project '{name_or_path}' not found. Available: {available}")


def _parse_brand(entry) -> Brand:
    """A brand is either "WeWork" or {name: WeWork, aliases: [...]}."""
    if isinstance(entry, str):
        return Brand(name=entry, aliases=[entry])
    name = entry["name"]
    aliases = list(dict.fromkeys([name, *(entry.get("aliases") or [])]))  # name always counts
    domains = [d.lower().removeprefix("www.") for d in entry.get("domains") or []]
    return Brand(name=name, aliases=aliases, domains=domains)


def load_project(name_or_path: str) -> Project:
    path = resolve_project_path(name_or_path)
    raw = _plain(_read_yaml(path)) or {}

    for required in ("brand", "category"):
        if not raw.get(required):
            raise ValueError(f"{path.name}: '{required}' is required")

    queries = [q.strip() for q in raw.get("queries") or [] if q and q.strip()]
    return Project(
        key=path.stem,
        path=path,
        brand=_parse_brand(raw["brand"]),
        category=raw["category"],
        competitors=[_parse_brand(c) for c in raw.get("competitors") or []],
        queries=list(dict.fromkeys(queries)),  # drop exact duplicates, keep order
    )


def save_queries(project: Project, queries: list[str]) -> None:
    """Replace the `queries` list in the project YAML and keep everything else as is."""
    data = _read_yaml(project.path)
    data["queries"] = queries
    with open(project.path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)
