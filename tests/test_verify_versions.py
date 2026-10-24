"""Version probes for `verify --versions` (Phase 3).

Probes run a stub binary on a temporary PATH, so no real tool is
executed. A timeout or non-zero exit is a reported problem, not a
crash.
"""

import os
import stat
from pathlib import Path

from darkfly import verify as verify_mod


def _stub(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "mytool"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _entry():
    return {"id": "mytool", "backend": "apt", "run": "mytool",
            "files_or_dirs": []}


def test_version_probe_reports_first_line(monkeypatch, tmp_path: Path):
    bin_dir = _stub(tmp_path, 'echo "mytool 1.2.3"\necho "second line"')
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    version, problem = verify_mod.version_probe(_entry())
    assert version == "mytool 1.2.3"
    assert problem is None


def test_version_probe_nonzero_exit_is_problem(monkeypatch, tmp_path: Path):
    bin_dir = _stub(tmp_path, "exit 3")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    version, problem = verify_mod.version_probe(_entry())
    assert version is None
    assert "code 3" in problem


def test_version_probe_timeout_is_problem(monkeypatch, tmp_path: Path):
    bin_dir = _stub(tmp_path, "sleep 5")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    version, problem = verify_mod.version_probe(_entry(), timeout=0.05)
    assert version is None
    assert "timed out" in problem


def test_version_probe_skips_missing_command():
    version, problem = verify_mod.version_probe(
        {"id": "ghost", "backend": "apt", "run": "nosuchtool-xyz",
         "files_or_dirs": []})
    assert version is None
    assert problem is None
