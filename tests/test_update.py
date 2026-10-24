from pathlib import Path

import pytest

import darkfly.cli as cli
import darkfly.installer as installer
import darkfly.manifest as manifest_mod
import darkfly.planner as planner
from darkfly.catalog import ToolEntry


class FakeRunner:
    """Records every argv it is called with; always succeeds."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return 0


def make_entry(**overrides):
    entry = {
        "id": "nmap",
        "name": "Nmap",
        "backend": "apt",
        "package": "nmap",
        "version_pin": None,
        "installed_at": "2026-10-23T00:00:00+00:00",
        "files_or_dirs": [],
        "run": "nmap",
    }
    entry.update(overrides)
    return entry


def make_tool(**overrides) -> ToolEntry:
    data = {
        "id": "nuclei",
        "name": "Nuclei",
        "aliases": [],
        "description": "Vulnerability scanner.",
        "categories": ["web"],
        "homepage": "https://github.com/projectdiscovery/nuclei",
        "source": "https://github.com/projectdiscovery/nuclei",
        "license": "MIT",
        "status": "maintained",
        "tier": 1,
        "platforms": {"termux": True, "linux": True,
                      "gui_required": False},
        "size_mb_estimate": 60,
        "last_verified": "2026-10-23",
        "install": {
            "backend": "go",
            "package": "github.com/projectdiscovery/nuclei/v3/cmd/nuclei",
            "termux_package": None,
            "version_pin": "v3.4.0",
            "deps_pkg": ["golang"],
            "deps_apt": ["golang-go"],
        },
        "run": "nuclei",
        "successor_to": None,
    }
    data.update(overrides)
    return ToolEntry.from_dict(data)


def test_update_plan_apt_linux_uses_only_upgrade():
    plan = planner.build_update_plan(make_entry(), "linux")

    assert plan.action == "update"
    assert plan.steps[0].argv == [
        "sudo", "apt-get", "install", "-y", "--only-upgrade", "nmap"]


def test_update_plan_apt_termux_upgrades_single_package():
    plan = planner.build_update_plan(make_entry(), "termux")

    assert plan.steps[0].argv == ["pkg", "install", "-y", "nmap"]


def test_update_plan_pipx_uses_upgrade():
    entry = make_entry(id="netexec", name="NetExec", backend="pipx",
                       package="netexec", run="nxc")
    plan = planner.build_update_plan(entry, "linux")

    assert plan.steps[0].argv == ["pipx", "upgrade", "netexec"]


def test_update_plan_go_prefers_current_catalog_pin():
    entry = make_entry(
        id="nuclei", name="Nuclei", backend="go",
        package="github.com/projectdiscovery/nuclei/v3/cmd/nuclei",
        version_pin="v3.3.0", run="nuclei")
    plan = planner.build_update_plan(entry, "linux", make_tool())

    assert plan.steps[0].argv == [
        "go", "install",
        "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@v3.4.0"]
    assert plan.version_pin == "v3.4.0"


def test_update_plan_git_pinned_fetches_and_checks_out(tmp_path):
    dest = str(tmp_path / "tools" / "seclists")
    entry = make_entry(id="seclists", name="SecLists", backend="git",
                       package="https://github.com/danielmiessler/"
                               "SecLists.git",
                       version_pin="2025.1", files_or_dirs=[dest],
                       run="")
    plan = planner.build_update_plan(entry, "linux")

    assert plan.steps[0].argv == ["git", "-C", dest, "fetch", "origin"]
    assert plan.steps[1].argv == ["git", "-C", dest, "checkout", "2025.1"]


def test_update_plan_git_unpinned_pulls(tmp_path):
    dest = str(tmp_path / "tools" / "someclone")
    entry = make_entry(id="someclone", name="Some Clone", backend="git",
                       package="https://example.com/x.git",
                       files_or_dirs=[dest], run="")
    plan = planner.build_update_plan(entry, "termux")

    assert plan.steps[0].argv == ["git", "-C", dest, "pull", "--ff-only"]


def test_update_plan_git_without_clone_dir_is_an_error():
    entry = make_entry(id="someclone", name="Some Clone", backend="git",
                       package="https://example.com/x.git", run="")
    with pytest.raises(planner.PlanError):
        planner.build_update_plan(entry, "linux")


def test_update_plan_manual_cannot_auto_update():
    entry = make_entry(id="feroxbuster", name="Feroxbuster",
                       backend="manual", package="feroxbuster",
                       run="feroxbuster")
    plan = planner.build_update_plan(entry, "linux")

    assert plan.steps == []
    assert any("cannot update" in n for n in plan.notes)


def test_update_tool_executes_and_refreshes_manifest(tmp_path):
    entry = make_entry()
    manifest_mod.add_entry(entry, tmp_path)

    plan = planner.build_update_plan(entry, "linux")
    runner = FakeRunner()
    updated = installer.update_tool(plan, runner=runner,
                                    base_dir=tmp_path)

    assert runner.calls == [
        ["sudo", "apt-get", "install", "-y", "--only-upgrade", "nmap"]]
    assert updated["installed_at"] == "2026-10-23T00:00:00+00:00"
    assert "updated_at" in updated
    saved = manifest_mod.get_entry("nmap", tmp_path)
    assert saved["updated_at"] == updated["updated_at"]
    assert installer.load_journal(tmp_path) is None


def test_update_tool_requires_a_manifest_entry(tmp_path):
    plan = planner.build_update_plan(make_entry(), "linux")
    with pytest.raises(installer.InstallError):
        installer.update_tool(plan, runner=FakeRunner(),
                              base_dir=tmp_path)


def test_update_tool_with_no_steps_is_an_error(tmp_path):
    entry = make_entry(id="feroxbuster", name="Feroxbuster",
                       backend="manual", package="feroxbuster",
                       run="feroxbuster")
    manifest_mod.add_entry(entry, tmp_path)
    plan = planner.build_update_plan(entry, "linux")

    with pytest.raises(installer.InstallError):
        installer.update_tool(plan, runner=FakeRunner(),
                              base_dir=tmp_path)


def test_cli_update_named_tool_not_installed(tmp_path, monkeypatch,
                                             capsys):
    monkeypatch.setenv("DARKFLY_HOME", str(tmp_path))
    assert cli.main(["update", "nmap"]) == 1
    assert "not installed" in capsys.readouterr().err


def test_cli_update_all_with_empty_manifest(tmp_path, monkeypatch,
                                            capsys):
    monkeypatch.setenv("DARKFLY_HOME", str(tmp_path))
    assert cli.main(["update", "--dry-run"]) == 0
    assert "nothing to update" in capsys.readouterr().out
