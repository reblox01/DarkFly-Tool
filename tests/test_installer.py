from pathlib import Path

import pytest

import darkfly.installer as installer
import darkfly.manifest as manifest_mod
import darkfly.planner as planner
from darkfly.catalog import ToolEntry


def make_tool(data_overrides=None, install_overrides=None) -> ToolEntry:
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
    if data_overrides:
        data.update(data_overrides)
    if install_overrides:
        data["install"] = {**data["install"], **install_overrides}
    return ToolEntry.from_dict(data)


class FakeRunner:
    """Records every argv it is called with; can fail on chosen calls."""

    def __init__(self, fail_on_calls=(), on_call=None):
        self.calls = []
        self.fail_on_calls = set(fail_on_calls)
        self.on_call = on_call

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.on_call is not None:
            self.on_call(argv)
        if len(self.calls) - 1 in self.fail_on_calls:
            return 1
        return 0


def test_install_completes_writes_manifest_and_clears_journal(tmp_path):
    plan = planner.build_install_plan(make_tool(), "linux",
                                      base_dir=tmp_path)
    runner = FakeRunner()

    entry = installer.install_tool(plan, runner=runner, base_dir=tmp_path)

    assert runner.calls == [["sudo", "apt-get", "install", "-y", "nmap"]]
    assert entry["id"] == "nmap"
    assert entry["backend"] == "apt"
    assert entry["run"] == "nmap"
    assert "installed_at" in entry

    saved = manifest_mod.get_entry("nmap", tmp_path)
    assert saved is not None
    assert saved["package"] == "nmap"

    assert installer.load_journal(tmp_path) is None
    assert not installer.journal_path(tmp_path).exists()


def _two_step_tool() -> ToolEntry:
    # A pinned git install has two steps: clone, then checkout.
    return make_tool(
        data_overrides={
            "id": "seclists",
            "name": "SecLists",
            "categories": ["resources"],
            "tier": 2,
            "run": "",
        },
        install_overrides={
            "backend": "git",
            "package": "https://github.com/danielmiessler/SecLists.git",
            "termux_package": None,
            "version_pin": "2024.3",
        },
    )


def test_failed_install_leaves_journal_and_resume_skips_steps(tmp_path):
    tool = _two_step_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path)
    assert len(plan.steps) == 2

    # First attempt: the second step (checkout) fails.
    failing = FakeRunner(fail_on_calls={1})
    with pytest.raises(installer.InstallError):
        installer.install_tool(plan, runner=failing, base_dir=tmp_path)

    journal = installer.load_journal(tmp_path)
    assert journal is not None
    assert journal["tool_id"] == "seclists"
    assert journal["completed"] == [0]
    assert manifest_mod.get_entry("seclists", tmp_path) is None

    # Resume: only the remaining step runs.
    resuming = FakeRunner()
    entry = installer.install_tool(plan, runner=resuming,
                                   base_dir=tmp_path)

    assert len(resuming.calls) == 1
    assert resuming.calls[0][:2] == ["git", "-C"]
    assert entry["id"] == "seclists"
    assert installer.load_journal(tmp_path) is None
    assert manifest_mod.get_entry("seclists", tmp_path) is not None


def test_journal_for_another_tool_blocks_install(tmp_path):
    first = planner.build_install_plan(_two_step_tool(), "linux",
                                       base_dir=tmp_path)
    with pytest.raises(installer.InstallError):
        installer.install_tool(first, runner=FakeRunner(fail_on_calls={0}),
                               base_dir=tmp_path)

    other = planner.build_install_plan(make_tool(), "linux",
                                       base_dir=tmp_path)
    with pytest.raises(installer.InstallError) as excinfo:
        installer.install_tool(other, runner=FakeRunner(),
                               base_dir=tmp_path)
    assert "seclists" in str(excinfo.value)


def test_install_with_no_steps_is_an_error(tmp_path):
    tool = make_tool(
        data_overrides={"id": "rustscan", "name": "RustScan",
                        "run": "rustscan"},
        install_overrides={"backend": "manual", "package": "rustscan",
                           "termux_package": None},
    )
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path)

    with pytest.raises(installer.InstallError):
        installer.install_tool(plan, runner=FakeRunner(), base_dir=tmp_path)


def test_uninstall_pipx_removes_manifest_entry(tmp_path):
    tool = make_tool(
        data_overrides={"id": "netexec", "name": "NetExec", "run": "nxc"},
        install_overrides={"backend": "pipx", "package": "netexec",
                           "termux_package": None},
    )
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path)
    installer.install_tool(plan, runner=FakeRunner(), base_dir=tmp_path)
    assert manifest_mod.get_entry("netexec", tmp_path) is not None

    runner = FakeRunner()
    removed = installer.uninstall_tool("netexec", runner=runner,
                                       base_dir=tmp_path, env="linux")

    assert removed is True
    assert runner.calls == [["pipx", "uninstall", "netexec"]]
    assert manifest_mod.get_entry("netexec", tmp_path) is None


def test_uninstall_git_removes_clone_dir_and_entry(tmp_path):
    tool = _two_step_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path)
    dest = Path(plan.files_created[0])

    def fake_clone(argv):
        if argv[:2] == ["git", "clone"]:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "README.md").write_text("fake", encoding="utf-8")

    installer.install_tool(plan, runner=FakeRunner(on_call=fake_clone),
                           base_dir=tmp_path)
    assert dest.is_dir()

    removed = installer.uninstall_tool("seclists", runner=FakeRunner(),
                                       base_dir=tmp_path, env="linux")

    assert removed is True
    assert not dest.exists()
    assert manifest_mod.get_entry("seclists", tmp_path) is None


def test_uninstall_unknown_tool_returns_false(tmp_path):
    assert installer.uninstall_tool("nope", runner=FakeRunner(),
                                    base_dir=tmp_path,
                                    env="linux") is False
