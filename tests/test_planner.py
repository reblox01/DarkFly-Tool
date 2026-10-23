from pathlib import Path

import pytest

import darkfly.planner as planner
from darkfly.catalog import ToolEntry


def make_tool(**overrides) -> ToolEntry:
    data = {
        "id": "nmap",
        "name": "Nmap",
        "aliases": [],
        "description": "Network mapper.",
        "categories": ["scanning"],
        "homepage": "https://nmap.org",
        "source": "https://github.com/nmap/nmap",
        "license": "GPL-2.0-only",
        "status": "maintained",
        "tier": 0,
        "platforms": {"termux": True, "linux": True, "gui_required": False},
        "size_mb_estimate": 30,
        "last_verified": "2026-10-23",
        "install": {
            "backend": "apt",
            "package": "nmap",
            "termux_package": "nmap",
            "version_pin": None,
            "deps_pkg": [],
            "deps_apt": [],
        },
        "run": "nmap",
        "successor_to": None,
    }
    for key, value in overrides.items():
        if key in ("install", "platforms"):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return ToolEntry.from_dict(data)


def test_detect_env_simulated_termux():
    assert planner.detect_env({"TERMUX_VERSION": "0.118.0"}) == "termux"
    assert planner.detect_env(
        {"HOME": "/data/data/com.termux/files/home"}) == "termux"
    linux_env = planner.detect_env({"HOME": "/home/user"})
    assert linux_env in {"linux", "other"}  # depends on the host platform


def test_apt_plan_uses_pkg_on_termux():
    plan = planner.build_install_plan(make_tool(), "termux")

    assert len(plan.steps) == 1
    assert plan.steps[0].argv == ["pkg", "install", "-y", "nmap"]
    assert not any(step.argv[0] == "sudo" for step in plan.steps)


def test_apt_plan_uses_apt_get_on_linux():
    plan = planner.build_install_plan(make_tool(), "linux")

    assert plan.steps[0].argv == ["sudo", "apt-get", "install", "-y", "nmap"]


def test_apt_plan_prefers_termux_package_name():
    tool = make_tool(install={"package": "foo", "termux_package": "foo-tx"})
    plan = planner.build_install_plan(tool, "termux")

    assert plan.steps[0].argv[-1] == "foo-tx"
    assert plan.package == "foo-tx"


def test_dependencies_come_first(tmp_path: Path):
    tool = make_tool(
        id="nuclei",
        name="Nuclei",
        install={
            "backend": "go",
            "package": "github.com/projectdiscovery/nuclei/v3/cmd/nuclei",
            "termux_package": None,
            "version_pin": "v3.3.0",
            "deps_pkg": ["golang"],
            "deps_apt": ["golang-go"],
        },
        run="nuclei",
    )

    termux_plan = planner.build_install_plan(tool, "termux",
                                             base_dir=tmp_path)
    assert termux_plan.steps[0].argv == ["pkg", "install", "-y", "golang"]
    assert termux_plan.steps[1].argv == [
        "go", "install",
        "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@v3.3.0",
    ]

    linux_plan = planner.build_install_plan(tool, "linux",
                                            base_dir=tmp_path)
    assert linux_plan.steps[0].argv == [
        "sudo", "apt-get", "install", "-y", "golang-go"]


def test_pipx_plan_with_and_without_pin():
    tool = make_tool(
        id="netexec",
        name="NetExec",
        install={"backend": "pipx", "package": "netexec",
                 "termux_package": None},
        run="nxc",
    )
    plan = planner.build_install_plan(tool, "linux")
    assert plan.steps[0].argv == ["pipx", "install", "netexec"]

    pinned = make_tool(
        id="sometool",
        name="Some Tool",
        install={"backend": "pipx", "package": "sometool",
                 "termux_package": None, "version_pin": "1.2.3"},
        run="sometool",
    )
    plan = planner.build_install_plan(pinned, "linux")
    assert plan.steps[0].argv == ["pipx", "install", "sometool==1.2.3"]


def test_git_plan_carries_warning_and_pin_checkout(tmp_path: Path):
    tool = make_tool(
        id="seclists",
        name="SecLists",
        categories=["resources"],
        install={
            "backend": "git",
            "package": "https://github.com/danielmiessler/SecLists.git",
            "termux_package": None,
            "version_pin": "2024.3",
        },
        run="",
        tier=2,
    )
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path)

    dest = str(tmp_path / "tools" / "seclists")
    assert plan.files_created == [dest]
    assert plan.steps[0].argv == [
        "git", "clone",
        "https://github.com/danielmiessler/SecLists.git", dest]
    assert plan.steps[1].argv == ["git", "-C", dest, "checkout", "2024.3"]
    assert any("Git backend" in w for w in plan.warnings)


def test_git_plan_unpinned_warns(tmp_path: Path):
    tool = make_tool(
        id="clonetool",
        name="Clone Tool",
        install={"backend": "git",
                 "package": "https://example.com/x.git",
                 "termux_package": None, "version_pin": None},
    )
    plan = planner.build_install_plan(tool, "termux", base_dir=tmp_path)

    assert plan.steps[0].argv[:3] == ["git", "clone", "--depth"]
    assert any("not pinned" in w for w in plan.warnings)


def test_heavy_install_warns():
    tool = make_tool(size_mb_estimate=1500)
    plan = planner.build_install_plan(tool, "termux")

    assert any("Heavy install" in w for w in plan.warnings)


def test_offensive_category_shows_lawful_use_notice():
    tool = make_tool(categories=["passwords", "offensive"])
    plan = planner.build_install_plan(tool, "linux")

    assert any("Lawful use" in w for w in plan.warnings)


def test_unsupported_platform_is_a_plan_error():
    tool = make_tool(platforms={"termux": False})
    with pytest.raises(planner.PlanError):
        planner.build_install_plan(tool, "termux")


def test_gui_tool_is_a_plan_error():
    tool = make_tool(platforms={"gui_required": True})
    with pytest.raises(planner.PlanError):
        planner.build_install_plan(tool, "linux")


def test_other_environment_is_a_plan_error():
    with pytest.raises(planner.PlanError):
        planner.build_install_plan(make_tool(), "other")


def test_manual_backend_has_no_steps_but_notes():
    tool = make_tool(
        id="rustscan",
        name="RustScan",
        install={"backend": "manual", "package": "rustscan",
                 "termux_package": None},
        run="rustscan",
    )
    plan = planner.build_install_plan(tool, "linux")

    assert plan.steps == []
    assert any("Manual installation" in n for n in plan.notes)


def test_render_shows_steps_size_and_warnings(tmp_path: Path):
    tool = make_tool(size_mb_estimate=600)
    plan = planner.build_install_plan(tool, "termux", base_dir=tmp_path)
    text = plan.render()

    assert "Nmap" in text
    assert "pkg install -y nmap" in text
    assert "600 MB" in text
    assert "Heavy install" in text


def test_uninstall_plan_reverses_apt(tmp_path: Path):
    entry = {
        "id": "nmap", "name": "Nmap", "backend": "apt",
        "package": "nmap", "version_pin": None,
        "installed_at": "2026-10-23T00:00:00+00:00",
        "files_or_dirs": [], "run": "nmap",
    }
    linux_plan = planner.build_uninstall_plan(entry, "linux")
    assert linux_plan.steps[0].argv == [
        "sudo", "apt-get", "remove", "-y", "nmap"]

    termux_plan = planner.build_uninstall_plan(entry, "termux")
    assert termux_plan.steps[0].argv == ["pkg", "uninstall", "-y", "nmap"]
