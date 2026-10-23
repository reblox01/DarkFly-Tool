"""Plan execution: journaled installs, resume, and uninstalls.

Execution model (see ``docs/DESIGN.md``):

- A plan is executed step by step through a *runner*: a callable that
  takes an argv list and returns a process return code. The default
  runner uses ``subprocess.run`` with ``shell=False``. Tests inject fake
  runners, so no test ever installs anything for real.
- Before the first step runs, the plan is written to
  ``<darkfly home>/journal.json`` with an empty ``completed`` list. After
  each successful step the journal is rewritten with that step's index
  added. On success the journal is deleted and the manifest entry is
  written. On failure the journal stays, so re-running the same install
  command resumes where it stopped. That design exists for Android's
  Phantom Process Killer (signal 9), which routinely kills long Termux
  installs halfway through.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from darkfly import manifest as manifest_mod
from darkfly.planner import InstallPlan, build_uninstall_plan


JOURNAL_FILENAME = "journal.json"

# A runner takes an argv list and returns a process return code.
Runner = Callable[[List[str]], int]


class InstallError(Exception):
    """Raised when an install or uninstall cannot complete."""


def default_runner(argv: List[str]) -> int:
    """Run one argv step as a subprocess, never through a shell."""

    try:
        completed = subprocess.run(argv, shell=False)
    except FileNotFoundError:
        # The program itself is missing (for example pipx not installed).
        # 127 is the conventional "command not found" code.
        return 127
    return completed.returncode


def journal_path(base_dir: Optional[Path] = None) -> Path:
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()
    return base / JOURNAL_FILENAME


def load_journal(base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the pending journal, or None when no install is in flight."""

    path = journal_path(base_dir)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_journal(journal: Dict[str, Any],
                   base_dir: Optional[Path] = None) -> None:
    path = journal_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(journal, f, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def _delete_journal(base_dir: Optional[Path] = None) -> None:
    path = journal_path(base_dir)
    if path.exists():
        path.unlink()


def _plan_steps_as_json(plan: InstallPlan) -> List[Dict[str, Any]]:
    return [
        {"description": step.description, "argv": list(step.argv)}
        for step in plan.steps
    ]


def install_tool(plan: InstallPlan, runner: Optional[Runner] = None,
                 base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Execute an install plan with journaling. Returns the manifest entry.

    If a journal for the same tool already exists (an interrupted
    install), completed steps are skipped. A journal for a different
    tool is never overwritten: InstallError is raised instead.
    """

    if runner is None:
        runner = default_runner
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()

    if not plan.steps:
        raise InstallError(
            f"Nothing to execute for {plan.tool_id}: the plan has no "
            "steps (manual installs are not automated)."
        )

    existing = load_journal(base)
    completed: List[int] = []
    if existing is not None:
        if existing.get("tool_id") != plan.tool_id:
            raise InstallError(
                f"An interrupted install of "
                f"'{existing.get('tool_id')}' is still pending "
                f"(journal: {journal_path(base)}). Re-run "
                f"'DarkFly5 install {existing.get('tool_id')}' to "
                "resume it before starting another install."
            )
        if len(existing.get("steps", [])) == len(plan.steps):
            completed = list(existing.get("completed", []))

    journal = {
        "tool_id": plan.tool_id,
        "tool_name": plan.name,
        "steps": _plan_steps_as_json(plan),
        "completed": completed,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_journal(journal, base)

    for idx, step in enumerate(plan.steps):
        if idx in journal["completed"]:
            continue
        rc = runner(list(step.argv))
        if rc != 0:
            raise InstallError(
                f"Step {idx + 1} of {len(plan.steps)} failed for "
                f"{plan.tool_id} (exit code {rc}): {step.description}. "
                "Fix the problem and re-run the same install command "
                "to resume where it stopped."
            )
        journal["completed"].append(idx)
        _write_journal(journal, base)

    _delete_journal(base)

    entry = {
        "id": plan.tool_id,
        "name": plan.name,
        "backend": plan.backend,
        "package": plan.package,
        "version_pin": plan.version_pin,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files_or_dirs": list(plan.files_created),
        "run": plan.run,
    }
    manifest_mod.add_entry(entry, base)
    return entry


def uninstall_tool(tool_id: str, runner: Optional[Runner] = None,
                   base_dir: Optional[Path] = None,
                   env: Optional[str] = None) -> bool:
    """Reverse an install using its manifest entry.

    Runs the uninstall plan steps, deletes the files and directories
    DarkFly created (recorded in the manifest), and removes the entry.
    Returns False when the tool is not in the manifest.
    """

    if runner is None:
        runner = default_runner
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()

    entry = manifest_mod.get_entry(tool_id, base)
    if entry is None:
        return False

    if env is None:
        from darkfly.planner import detect_env
        env = detect_env()

    plan = build_uninstall_plan(entry, env)
    for idx, step in enumerate(plan.steps):
        rc = runner(list(step.argv))
        if rc != 0:
            raise InstallError(
                f"Uninstall step {idx + 1} failed for {tool_id} "
                f"(exit code {rc}): {step.description}. The manifest "
                "entry was kept so you can try again."
            )

    for path_str in entry.get("files_or_dirs", []):
        path = Path(path_str)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    manifest_mod.remove_entry(tool_id, base)
    return True
