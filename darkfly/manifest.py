"""The install manifest: DarkFly's record of what it installed.

The manifest lives at ``<darkfly home>/installed.json`` (see
``docs/DESIGN.md``). It is the single source of truth for the
``installed`` and ``uninstall`` commands: if a tool is not in the
manifest, DarkFly did not install it and will not claim otherwise.

The darkfly home is ``$DARKFLY_HOME`` when that environment variable is
set, and ``~/.local/share/darkfly`` otherwise. The same base directory
holds the install journal and the ``tools/`` directory used by the git
backend, which keeps tests and sandboxed runs hermetic behind one
variable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


MANIFEST_FILENAME = "installed.json"
MANIFEST_SCHEMA_VERSION = 1


def darkfly_home() -> Path:
    """Return the base directory for manifests, journals, and tools."""

    override = os.environ.get("DARKFLY_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "darkfly"


def manifest_path(base_dir: Optional[Path] = None) -> Path:
    base = base_dir if base_dir is not None else darkfly_home()
    return base / MANIFEST_FILENAME


def load_manifest(base_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load the manifest as a mapping of tool id to entry.

    A missing manifest is not an error: it simply means DarkFly has not
    installed anything yet, so an empty mapping is returned.
    """

    path = manifest_path(base_dir)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        return {}
    return tools


def save_manifest(tools: Dict[str, Dict[str, Any]],
                  base_dir: Optional[Path] = None) -> None:
    """Persist the manifest atomically (temp file plus rename).

    A kill in the middle of a plain write could truncate the file; the
    rename swap means readers see either the old or the new manifest,
    never half of one.
    """

    path = manifest_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tools": tools,
    }
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(path)


def get_entry(tool_id: str,
              base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the manifest entry for a tool, or None if not installed."""

    return load_manifest(base_dir).get(tool_id)


def add_entry(entry: Dict[str, Any],
              base_dir: Optional[Path] = None) -> None:
    """Add or replace the manifest entry for ``entry['id']``."""

    tools = load_manifest(base_dir)
    tools[entry["id"]] = entry
    save_manifest(tools, base_dir)


def remove_entry(tool_id: str,
                 base_dir: Optional[Path] = None) -> bool:
    """Remove a tool from the manifest. Returns True if it was present."""

    tools = load_manifest(base_dir)
    if tool_id not in tools:
        return False
    del tools[tool_id]
    save_manifest(tools, base_dir)
    return True
