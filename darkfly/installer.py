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

Binary-backend installs are the exception: they are a single atomic
action, not argv steps. The file is downloaded (through one injectable
downloader, so tests never touch the network) into a temp directory,
its SHA-256 is verified *before* anything is installed, and the binary
is then moved into ``<darkfly home>/bin/`` with a rename, so a kill
mid-install leaves either the old state or the new binary, never a
half-written file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional

from darkfly import manifest as manifest_mod
from darkfly.planner import InstallPlan, build_uninstall_plan


JOURNAL_FILENAME = "journal.json"

# A runner takes an argv list and returns a process return code.
Runner = Callable[[List[str]], int]

# A downloader takes a URL and a writable binary stream and writes
# the response body to it. Tests inject fakes; production uses
# urllib through default_downloader.
Downloader = Callable[[str, BinaryIO], None]


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


def _execute_steps(plan: InstallPlan, runner: Runner,
                   base: Path) -> None:
    """Run a plan's steps under the journal, resuming where possible.

    If a journal for the same tool already exists (an interrupted
    run), completed steps are skipped. A journal for a different
    tool is never overwritten: InstallError is raised instead. The
    journal is deleted once every step has succeeded; on failure it
    stays, with the completed step indexes recorded.
    """

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
                "Fix the problem and re-run the same command to "
                "resume where it stopped."
            )
        journal["completed"].append(idx)
        _write_journal(journal, base)

    _delete_journal(base)


def default_downloader(url: str, out: BinaryIO) -> None:
    """Download ``url`` into the open stream ``out`` with urllib.

    This is the only function in the codebase that performs network
    downloads for installs, and it is injectable everywhere it is
    used, so tests never touch the network.
    """

    request = urllib.request.Request(
        url, headers={"User-Agent": "DarkFly5"})
    with urllib.request.urlopen(request, timeout=120) as response:
        shutil.copyfileobj(response, out)


def _extract_binary_member(download: Path, archive: str,
                           bin_name: str) -> bytes:
    """Return the bytes of ``bin_name`` from a downloaded file.

    For archives, only the single member whose file name matches is
    read; nothing is extracted to disk, so archive path traversal is
    impossible by construction.
    """

    if archive == "none":
        return download.read_bytes()

    if archive == "tar.gz":
        with tarfile.open(download, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile() \
                        and Path(member.name).name == bin_name:
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        return extracted.read()
        raise InstallError(
            f"The downloaded archive does not contain a file "
            f"named '{bin_name}'."
        )

    if archive == "zip":
        with zipfile.ZipFile(download) as zf:
            for info in zf.infolist():
                if not info.is_dir() \
                        and Path(info.filename).name == bin_name:
                    return zf.read(info)
        raise InstallError(
            f"The downloaded archive does not contain a file "
            f"named '{bin_name}'."
        )

    raise InstallError(  # pragma: no cover - validation gates this
        f"Unknown archive type: {archive!r}")


def install_binary_tool(plan: InstallPlan,
                        downloader: Optional[Downloader] = None,
                        base_dir: Optional[Path] = None,
                        ) -> Dict[str, Any]:
    """Execute a binary-backend install plan. Returns the manifest entry.

    The download lands in a temp directory under the darkfly home.
    Its SHA-256 is checked against the catalog value *before* the
    binary is placed; a mismatch aborts the install and nothing is
    written outside the temp directory. Placement is atomic: the
    binary is written to a temp file in ``bin/``, chmodded 0755, and
    renamed over the destination.
    """

    if downloader is None:
        downloader = default_downloader
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()
    spec = plan.binary
    if spec is None:  # pragma: no cover - guarded by install_tool
        raise InstallError(
            f"Plan for {plan.tool_id} is not a binary plan.")

    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="darkfly-download-",
                                  dir=str(base)))
    try:
        download = work / "download"
        with download.open("wb") as out:
            downloader(spec.url, out)

        digest = hashlib.sha256(download.read_bytes()).hexdigest()
        if digest.lower() != spec.sha256.lower():
            raise InstallError(
                f"Checksum mismatch for {plan.tool_id}: the catalog "
                f"expects SHA-256 {spec.sha256} for {spec.arch}, "
                f"but the download from {spec.url} hashes to "
                f"{digest}. Nothing was installed; the download may "
                "be corrupted or the catalog entry may be stale."
            )

        payload = _extract_binary_member(download, spec.archive,
                                         spec.bin_name)

        dest = Path(spec.dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=spec.bin_name + ".",
                                        dir=str(dest.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
            os.chmod(tmp_name, 0o755)
            os.replace(tmp_name, dest)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    entry = {
        "id": plan.tool_id,
        "name": plan.name,
        "backend": plan.backend,
        "package": plan.package,
        "version_pin": plan.version_pin,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files_or_dirs": [str(dest)],
        "run": plan.run,
    }
    manifest_mod.add_entry(entry, base)
    return entry


def install_tool(plan: InstallPlan, runner: Optional[Runner] = None,
                 base_dir: Optional[Path] = None,
                 downloader: Optional[Downloader] = None,
                 ) -> Dict[str, Any]:
    """Execute an install plan. Returns the manifest entry.

    Step-based backends run journaled through ``_execute_steps``;
    the binary backend dispatches to ``install_binary_tool``.
    """

    if runner is None:
        runner = default_runner
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()

    if plan.binary is not None:
        return install_binary_tool(plan, downloader=downloader,
                                   base_dir=base)

    if not plan.steps:
        raise InstallError(
            f"Nothing to execute for {plan.tool_id}: the plan has no "
            "steps (manual installs are not automated)."
        )

    _execute_steps(plan, runner, base)

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


def update_tool(plan: InstallPlan, runner: Optional[Runner] = None,
                base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Execute an update plan and refresh the manifest entry.

    The entry keeps its original ``installed_at`` and gains an
    ``updated_at`` timestamp; its pin and package move to the values
    the update plan applied. Raises InstallError when the tool is not
    in the manifest or the plan has nothing to execute.
    """

    if runner is None:
        runner = default_runner
    base = base_dir if base_dir is not None else manifest_mod.darkfly_home()

    existing = manifest_mod.get_entry(plan.tool_id, base)
    if existing is None:
        raise InstallError(
            f"Cannot update {plan.tool_id}: it is not in the "
            "manifest (not installed by DarkFly)."
        )
    if not plan.steps:
        raise InstallError(
            f"Nothing to execute for {plan.tool_id}: backend "
            f"'{plan.backend}' cannot be updated automatically."
        )

    _execute_steps(plan, runner, base)

    entry = dict(existing)
    entry.update({
        "name": plan.name,
        "backend": plan.backend,
        "package": plan.package,
        "version_pin": plan.version_pin,
        "run": plan.run or existing.get("run", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
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
