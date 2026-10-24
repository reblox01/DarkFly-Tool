"""Install verification: does what the manifest claims still hold?

``verify`` is deliberately shallow in this phase (see
``docs/DESIGN.md``): it checks that an installed tool is still in the
catalog, that its run command can still be found, and that the files
and directories DarkFly recorded still exist. It does not execute
the tools or compare versions; that belongs to a later phase.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from darkfly import manifest as manifest_mod
from darkfly.catalog import Catalog


def verify_entry(entry: Dict[str, Any],
                 catalog: Optional[Catalog]) -> List[str]:
    """Return the list of problems with one manifest entry.

    An empty list means the entry verified OK.
    """

    problems: List[str] = []
    tool_id = entry.get("id", "?")

    if catalog is None or catalog.get(tool_id) is None:
        problems.append(
            f"'{tool_id}' is no longer present in the catalog; "
            "DarkFly cannot vouch for it anymore."
        )

    files_or_dirs = entry.get("files_or_dirs", [])
    for path_str in files_or_dirs:
        if not Path(path_str).exists():
            problems.append(f"Recorded path is missing: {path_str}")

    run = (entry.get("run") or "").strip()
    if run:
        command = run.split()[0]
        if entry.get("backend") == "git" and files_or_dirs:
            # A git install runs from inside its clone directory, so
            # check for the script there instead of on PATH.
            candidate = Path(files_or_dirs[0]) / command
            if not candidate.exists():
                problems.append(
                    f"Run target '{command}' is missing from the "
                    f"clone directory {files_or_dirs[0]}"
                )
        elif shutil.which(command) is None:
            problems.append(
                f"Run command '{command}' was not found on PATH; "
                "the tool may have been removed outside DarkFly."
            )
    elif not files_or_dirs:
        problems.append(
            "Nothing checkable is recorded: the manifest entry has "
            "no run command and no files."
        )

    return problems


def verify_all(catalog: Optional[Catalog],
               base_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """Verify every manifest entry. Returns tool id -> problems."""

    tools = manifest_mod.load_manifest(base_dir)
    return {
        tool_id: verify_entry(tools[tool_id], catalog)
        for tool_id in sorted(tools)
    }
