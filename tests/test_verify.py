from pathlib import Path

import darkfly.cli as cli
import darkfly.manifest as manifest_mod
import darkfly.verify as verify_mod
from darkfly.catalog import load_catalog


REPO_ROOT = Path(__file__).resolve().parent.parent


def real_catalog():
    return load_catalog(REPO_ROOT / "catalog.json")


def make_entry(**overrides):
    entry = {
        "id": "nmap",
        "name": "Nmap",
        "backend": "apt",
        "package": "nmap",
        "version_pin": None,
        "installed_at": "2026-10-23T00:00:00+00:00",
        "files_or_dirs": [],
        # python3 exists on the test host's PATH, so this verifies OK.
        "run": "python3",
    }
    entry.update(overrides)
    return entry


def test_verify_ok_entry_has_no_problems():
    problems = verify_mod.verify_entry(make_entry(), real_catalog())
    assert problems == []


def test_verify_flags_missing_run_command():
    entry = make_entry(run="darkfly-no-such-command-xyz")
    problems = verify_mod.verify_entry(entry, real_catalog())

    assert any("not found on PATH" in p for p in problems)


def test_verify_flags_tool_removed_from_catalog():
    entry = make_entry(id="ghost-tool")
    problems = verify_mod.verify_entry(entry, real_catalog())

    assert any("no longer present in the catalog" in p for p in problems)


def test_verify_flags_missing_recorded_path(tmp_path):
    missing = str(tmp_path / "gone")
    entry = make_entry(files_or_dirs=[missing])
    problems = verify_mod.verify_entry(entry, real_catalog())

    assert any("missing" in p for p in problems)


def test_verify_git_entry_checks_script_inside_clone(tmp_path):
    clone = tmp_path / "tools" / "xsstrike"
    clone.mkdir(parents=True)
    (clone / "xsstrike.py").write_text("# fake\n", encoding="utf-8")
    entry = make_entry(id="xsstrike", name="XSStrike", backend="git",
                       package="https://github.com/s0md3v/XSStrike.git",
                       files_or_dirs=[str(clone)], run="xsstrike.py")

    assert verify_mod.verify_entry(entry, real_catalog()) == []

    (clone / "xsstrike.py").unlink()
    problems = verify_mod.verify_entry(entry, real_catalog())
    assert any("missing from the clone directory" in p for p in problems)


def test_verify_resource_with_existing_dir_is_ok(tmp_path):
    clone = tmp_path / "tools" / "seclists"
    clone.mkdir(parents=True)
    entry = make_entry(id="seclists", name="SecLists", backend="git",
                       files_or_dirs=[str(clone)], run="")

    assert verify_mod.verify_entry(entry, real_catalog()) == []


def test_verify_entry_with_nothing_checkable_is_a_problem():
    entry = make_entry(run="", files_or_dirs=[])
    problems = verify_mod.verify_entry(entry, real_catalog())

    assert any("Nothing checkable" in p for p in problems)


def test_verify_all_reports_each_entry(tmp_path):
    manifest_mod.add_entry(make_entry(), tmp_path)
    manifest_mod.add_entry(
        make_entry(id="ghost-tool", run="python3"), tmp_path)

    results = verify_mod.verify_all(real_catalog(), base_dir=tmp_path)

    assert results["nmap"] == []
    assert results["ghost-tool"] != []


def test_cli_verify_empty_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DARKFLY_HOME", str(tmp_path))
    assert cli.main(["verify"]) == 0
    assert "nothing to verify" in capsys.readouterr().out


def test_cli_verify_ok_and_problem_exit_codes(tmp_path, monkeypatch,
                                              capsys):
    monkeypatch.setenv("DARKFLY_HOME", str(tmp_path))
    manifest_mod.add_entry(make_entry(), tmp_path)
    assert cli.main(["verify", "nmap"]) == 0
    assert "OK" in capsys.readouterr().out

    manifest_mod.add_entry(
        make_entry(id="john", name="John", run="darkfly-nope-xyz"),
        tmp_path)
    assert cli.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM: john" in out
    assert "1 with problems" in out


def test_cli_verify_named_tool_not_installed(tmp_path, monkeypatch,
                                             capsys):
    monkeypatch.setenv("DARKFLY_HOME", str(tmp_path))
    assert cli.main(["verify", "nmap"]) == 1
    assert "not installed" in capsys.readouterr().err
