"""Catalog (schema v2) loading, validation, and search.

The catalog is the heart of the next-generation DarkFly design (see
``docs/DESIGN.md``). It is a single plain-JSON file, ``catalog.json``,
that describes every tool DarkFly knows how to install:

- declarative per-tool metadata (homepage, license, status, tiers),
- platform support (Termux / Linux / GUI-only),
- a single install backend per tool (``apt``, ``pipx``, ``go``, ``git``
  or ``manual``) with an optional version pin and extra dependencies.

Keeping the catalog as data (never code) is a safety property: it is
diff-able in pull requests, and nothing in it can execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 2

VALID_BACKENDS = {"apt", "pipx", "go", "git", "manual"}
VALID_STATUSES = {"maintained", "stale", "archived"}
VALID_TIERS = {0, 1, 2, 3}

# Fields every tool entry must carry. The design doc explains each one.
REQUIRED_TOOL_FIELDS = [
    "id",
    "name",
    "aliases",
    "description",
    "categories",
    "homepage",
    "source",
    "license",
    "status",
    "tier",
    "platforms",
    "size_mb_estimate",
    "last_verified",
    "install",
    "run",
    "successor_to",
]

REQUIRED_INSTALL_FIELDS = [
    "backend",
    "package",
    "termux_package",
    "version_pin",
    "deps_pkg",
    "deps_apt",
]


class CatalogError(Exception):
    """Raised when the catalog cannot be loaded or fails validation.

    ``problems`` holds every problem found, so a maintainer fixing the
    catalog sees the full list in one pass instead of one error at a time.
    """

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        message = "Catalog is invalid:\n" + "\n".join(
            f"  - {p}" for p in self.problems
        )
        super().__init__(message)


@dataclass
class InstallSpec:
    backend: str
    package: str
    termux_package: Optional[str] = None
    version_pin: Optional[str] = None
    deps_pkg: List[str] = field(default_factory=list)
    deps_apt: List[str] = field(default_factory=list)


@dataclass
class Platforms:
    termux: bool = True
    linux: bool = True
    gui_required: bool = False


@dataclass
class ToolEntry:
    id: str
    name: str
    description: str
    categories: List[str]
    homepage: str
    source: str
    license: str
    status: str
    tier: int
    platforms: Platforms
    install: InstallSpec
    run: str
    aliases: List[str] = field(default_factory=list)
    size_mb_estimate: Optional[float] = None
    last_verified: str = ""
    successor_to: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolEntry":
        install = data.get("install", {})
        platforms = data.get("platforms", {})
        return cls(
            id=data["id"],
            name=data["name"],
            aliases=list(data.get("aliases", [])),
            description=data.get("description", ""),
            categories=list(data.get("categories", [])),
            homepage=data.get("homepage", ""),
            source=data.get("source", ""),
            license=data.get("license", ""),
            status=data.get("status", ""),
            tier=data.get("tier", 0),
            platforms=Platforms(
                termux=bool(platforms.get("termux", True)),
                linux=bool(platforms.get("linux", True)),
                gui_required=bool(platforms.get("gui_required", False)),
            ),
            size_mb_estimate=data.get("size_mb_estimate"),
            last_verified=data.get("last_verified", ""),
            install=InstallSpec(
                backend=install.get("backend", ""),
                package=install.get("package", ""),
                termux_package=install.get("termux_package"),
                version_pin=install.get("version_pin"),
                deps_pkg=list(install.get("deps_pkg", [])),
                deps_apt=list(install.get("deps_apt", [])),
            ),
            run=data.get("run", ""),
            successor_to=data.get("successor_to"),
        )


@dataclass
class Catalog:
    schema_version: int
    tools: List[ToolEntry]

    def get(self, tool_id: str) -> Optional[ToolEntry]:
        """Look up a tool by id or alias (case-insensitive)."""
        key = tool_id.strip().lower()
        for tool in self.tools:
            if tool.id.lower() == key:
                return tool
            if any(a.lower() == key for a in tool.aliases):
                return tool
        return None

    def __len__(self) -> int:
        return len(self.tools)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_catalog(data: Any) -> List[str]:
    """Return a list of problems with a parsed catalog document.

    An empty list means the catalog is valid. This is a pure function of
    the parsed JSON so it can be unit-tested without touching the disk.
    """

    problems: List[str] = []

    if not isinstance(data, dict):
        return ["catalog root must be a JSON object"]

    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )

    tools = data.get("tools")
    if not isinstance(tools, list):
        problems.append("tools must be a list")
        return problems

    seen_names: Dict[str, str] = {}  # lowercased id/alias -> owner id

    for idx, entry in enumerate(tools):
        where = f"tools[{idx}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: entry must be an object")
            continue

        tool_id = entry.get("id")
        if isinstance(tool_id, str) and tool_id:
            where = f"tool '{tool_id}'"

        for fname in REQUIRED_TOOL_FIELDS:
            if fname not in entry:
                problems.append(f"{where}: missing field '{fname}'")

        # Basic types for the scalar fields.
        for fname in ("id", "name", "description", "homepage", "source",
                      "license", "run"):
            if fname in entry and not isinstance(entry[fname], str):
                problems.append(f"{where}: '{fname}' must be a string")
        if isinstance(tool_id, str) and not tool_id.strip():
            problems.append(f"{where}: 'id' must not be empty")

        if "aliases" in entry and not _is_str_list(entry["aliases"]):
            problems.append(f"{where}: 'aliases' must be a list of strings")
        if "categories" in entry:
            if not _is_str_list(entry["categories"]):
                problems.append(
                    f"{where}: 'categories' must be a list of strings"
                )
            elif not entry["categories"]:
                problems.append(f"{where}: 'categories' must not be empty")

        if "status" in entry and entry["status"] not in VALID_STATUSES:
            problems.append(
                f"{where}: status must be one of {sorted(VALID_STATUSES)}, "
                f"got {entry['status']!r}"
            )

        if "tier" in entry:
            tier = entry["tier"]
            # bool is a subclass of int; reject it explicitly.
            if not isinstance(tier, int) or isinstance(tier, bool) \
                    or tier not in VALID_TIERS:
                problems.append(
                    f"{where}: tier must be one of {sorted(VALID_TIERS)}, "
                    f"got {tier!r}"
                )

        if "platforms" in entry:
            platforms = entry["platforms"]
            if not isinstance(platforms, dict):
                problems.append(f"{where}: 'platforms' must be an object")
            else:
                for key in ("termux", "linux", "gui_required"):
                    if key in platforms \
                            and not isinstance(platforms[key], bool):
                        problems.append(
                            f"{where}: platforms.{key} must be a boolean"
                        )

        if "size_mb_estimate" in entry:
            size = entry["size_mb_estimate"]
            if size is not None:
                if not isinstance(size, (int, float)) \
                        or isinstance(size, bool) or size < 0:
                    problems.append(
                        f"{where}: 'size_mb_estimate' must be a "
                        "non-negative number or null"
                    )

        if "last_verified" in entry:
            value = entry["last_verified"]
            if not isinstance(value, str):
                problems.append(
                    f"{where}: 'last_verified' must be a date string"
                )
            else:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    problems.append(
                        f"{where}: 'last_verified' must be a YYYY-MM-DD "
                        f"date, got {value!r}"
                    )

        if "successor_to" in entry:
            value = entry["successor_to"]
            if value is not None and not isinstance(value, str):
                problems.append(
                    f"{where}: 'successor_to' must be a string or null"
                )

        if "install" in entry:
            install = entry["install"]
            if not isinstance(install, dict):
                problems.append(f"{where}: 'install' must be an object")
            else:
                for fname in REQUIRED_INSTALL_FIELDS:
                    if fname not in install:
                        problems.append(
                            f"{where}: missing field 'install.{fname}'"
                        )
                backend = install.get("backend")
                if backend is not None and backend not in VALID_BACKENDS:
                    problems.append(
                        f"{where}: unknown backend {backend!r}, must be "
                        f"one of {sorted(VALID_BACKENDS)}"
                    )
                package = install.get("package")
                if package is not None and (
                        not isinstance(package, str) or not package.strip()):
                    problems.append(
                        f"{where}: 'install.package' must be a "
                        "non-empty string"
                    )
                for fname in ("termux_package", "version_pin"):
                    value = install.get(fname)
                    if value is not None and not isinstance(value, str):
                        problems.append(
                            f"{where}: 'install.{fname}' must be a "
                            "string or null"
                        )
                for fname in ("deps_pkg", "deps_apt"):
                    if fname in install and not _is_str_list(install[fname]):
                        problems.append(
                            f"{where}: 'install.{fname}' must be a list "
                            "of strings"
                        )

        # Duplicate detection across ids and aliases.
        names: List[str] = []
        if isinstance(tool_id, str) and tool_id:
            names.append(tool_id)
        if _is_str_list(entry.get("aliases")):
            names.extend(entry["aliases"])
        for name in names:
            key = name.lower()
            if key in seen_names:
                problems.append(
                    f"{where}: id or alias '{name}' is already used by "
                    f"tool '{seen_names[key]}'"
                )
            else:
                seen_names[key] = tool_id if isinstance(tool_id, str) \
                    else where

    return problems


def find_catalog_path(start: Optional[Path] = None) -> Optional[Path]:
    """Locate ``catalog.json``.

    Search order:
    1. the current working directory (or ``start`` when given),
    2. the parent of the ``darkfly`` package directory (the repository
       root in a clone or an editable install),
    3. inside the package directory itself (future packaged installs).
    """

    candidates: List[Path] = []
    base = start if start is not None else Path.cwd()
    candidates.append(base / "catalog.json")
    package_dir = Path(__file__).resolve().parent
    candidates.append(package_dir.parent / "catalog.json")
    candidates.append(package_dir / "catalog.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_catalog(path: Optional[Path] = None) -> Catalog:
    """Load and validate the catalog, raising CatalogError on problems."""

    catalog_path = path if path is not None else find_catalog_path()
    if catalog_path is None or not catalog_path.is_file():
        raise CatalogError([
            "catalog.json not found (looked in the working directory "
            "and next to the darkfly package)"
        ])

    try:
        with catalog_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CatalogError([f"{catalog_path}: invalid JSON: {exc}"]) \
            from exc

    problems = validate_catalog(data)
    if problems:
        raise CatalogError(problems)

    tools = [ToolEntry.from_dict(entry) for entry in data["tools"]]
    return Catalog(schema_version=data["schema_version"], tools=tools)


def _score_tool(tool: ToolEntry, query: str) -> int:
    """Score a tool against a lowercased query. Higher is better.

    Ranking: exact id or alias, then substring hits weighted by field
    importance (id and name first, then aliases, categories, and finally
    description and successor link).
    """

    q = query.strip().lower()
    if not q:
        return 0

    if tool.id.lower() == q or any(a.lower() == q for a in tool.aliases):
        return 100
    if q in tool.id.lower():
        return 60
    if q in tool.name.lower():
        return 50
    if any(q in a.lower() for a in tool.aliases):
        return 40
    if any(q in c.lower() for c in tool.categories):
        return 30
    if tool.successor_to and q in tool.successor_to.lower():
        return 25
    if q in tool.description.lower():
        return 20
    return 0


def search_tools(catalog: Catalog, query: str) -> List[ToolEntry]:
    """Search the catalog for a free-text query.

    Matches are returned best-first; tools with the same score keep
    catalog order. An empty query matches nothing.
    """

    scored = []
    for tool in catalog.tools:
        score = _score_tool(tool, query)
        if score > 0:
            scored.append((score, tool))
    scored.sort(key=lambda pair: -pair[0])
    return [tool for _, tool in scored]
