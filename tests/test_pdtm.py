"""Opt-in pdtm delegation (Phase 3).

Delegation renders only when BOTH conditions hold: DARKFLY_PDTM=1
and pdtm on PATH. Otherwise the default go install plan renders.
"""

import shutil
from pathlib import Path

import darkfly.planner as planner
from darkfly.catalog import load_catalog


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / "catalog.json")


def _with_pdtm(monkeypatch, present: bool):
    if present:
        monkeypatch.setattr(
            shutil, "which",
            lambda name: "/usr/bin/pdtm" if name == "pdtm" else None)
    else:
        monkeypatch.setattr(shutil, "which", lambda name: None)


def test_pdtm_delegation_renders_when_opted_in_and_present(
        monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DARKFLY_PDTM", "1")
    _with_pdtm(monkeypatch, True)
    nuclei = CATALOG.get("nuclei")
    plan = planner.build_install_plan(nuclei, "linux", base_dir=tmp_path)
    assert any(step.argv == ["pdtm", "-i", "nuclei"]
               for step in plan.steps)
    assert plan.backend == "go"  # delegation changes steps, not backend


def test_pdtm_delegation_off_without_flag(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DARKFLY_PDTM", raising=False)
    _with_pdtm(monkeypatch, True)
    nuclei = CATALOG.get("nuclei")
    plan = planner.build_install_plan(nuclei, "linux", base_dir=tmp_path)
    assert not any(step.argv[0] == "pdtm" for step in plan.steps)
    assert any(step.argv[0] == "go" for step in plan.steps)


def test_pdtm_delegation_off_when_pdtm_absent(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DARKFLY_PDTM", "1")
    _with_pdtm(monkeypatch, False)
    nuclei = CATALOG.get("nuclei")
    plan = planner.build_install_plan(nuclei, "linux", base_dir=tmp_path)
    assert not any(step.argv[0] == "pdtm" for step in plan.steps)
    assert any(step.argv[0] == "go" for step in plan.steps)


def test_pdtm_delegation_only_for_projectdiscovery(
        monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DARKFLY_PDTM", "1")
    _with_pdtm(monkeypatch, True)
    ffuf = CATALOG.get("ffuf")
    plan = planner.build_install_plan(ffuf, "linux", base_dir=tmp_path)
    assert not any(step.argv[0] == "pdtm" for step in plan.steps)
    assert any(step.argv[0] == "go" for step in plan.steps)


def test_pdtm_update_delegates(monkeypatch):
    monkeypatch.setenv("DARKFLY_PDTM", "1")
    _with_pdtm(monkeypatch, True)
    entry = {"id": "nuclei", "name": "Nuclei", "backend": "go",
             "package": "github.com/projectdiscovery/nuclei/v3/cmd/nuclei",
             "run": "nuclei", "files_or_dirs": []}
    plan = planner.build_update_plan(entry, "linux", CATALOG.get("nuclei"))
    assert plan.steps[0].argv == ["pdtm", "-u", "nuclei"]


def test_pdtm_update_off_without_flag(monkeypatch):
    monkeypatch.delenv("DARKFLY_PDTM", raising=False)
    _with_pdtm(monkeypatch, True)
    entry = {"id": "nuclei", "name": "Nuclei", "backend": "go",
             "package": "github.com/projectdiscovery/nuclei/v3/cmd/nuclei",
             "run": "nuclei", "files_or_dirs": []}
    plan = planner.build_update_plan(entry, "linux", CATALOG.get("nuclei"))
    assert plan.steps[0].argv[0] == "go"
