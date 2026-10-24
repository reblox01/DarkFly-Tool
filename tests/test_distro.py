"""Distro-family detection and dnf planning (Phase 3).

Detection reads /etc/os-release through an injectable path, so these
tests simulate RHEL, Fedora, Ubuntu, and Debian with fake files.
No real RHEL machine is involved; see docs/DESIGN.md for the honest
caveat about that.
"""

from pathlib import Path

import pytest

import darkfly.planner as planner
from darkfly.catalog import load_catalog


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / "catalog.json")

OS_RELEASE_SAMPLES = {
    "rhel9": 'NAME="Red Hat Enterprise Linux"\nVERSION="9.4"\n'
             'ID="rhel"\nID_LIKE="fedora"\n',
    "fedora": 'NAME="Fedora Linux"\nID="fedora"\n',
    "ubuntu": 'NAME="Ubuntu"\nID="ubuntu"\nID_LIKE="debian"\n',
    "debian": 'NAME="Debian GNU/Linux"\nID="debian"\n',
    "arch": 'NAME="Arch Linux"\nID="arch"\n',
}


def _write_os_release(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(OS_RELEASE_SAMPLES[name], encoding="utf-8")
    return path


@pytest.mark.parametrize("sample,expected", [
    ("rhel9", "rhel"),
    ("fedora", "rhel"),
    ("ubuntu", "debian"),
    ("debian", "debian"),
    ("arch", "linux"),
])
def test_detect_distro_family(tmp_path: Path, sample: str, expected: str):
    path = _write_os_release(tmp_path, sample)
    assert planner.detect_distro_family(path) == expected


def test_detect_distro_family_missing_file_falls_back(tmp_path: Path):
    assert planner.detect_distro_family(tmp_path / "no-such-file") \
        == "linux"


def test_detect_distro_family_env_override(monkeypatch, tmp_path: Path):
    path = _write_os_release(tmp_path, "rhel9")
    monkeypatch.setenv("DARKFLY_OS_RELEASE", str(path))
    assert planner.detect_distro_family() == "rhel"
    assert planner.os_release_id() == "rhel"


def test_detect_env_uses_distro_family(monkeypatch, tmp_path: Path):
    path = _write_os_release(tmp_path, "rhel9")
    env = planner.detect_env({"HOME": "/home/user"}, path)
    assert env == "rhel"


def test_rhel_apt_install_uses_dnf_and_rhel_package(tmp_path: Path):
    netcat = CATALOG.get("netcat-openbsd")
    plan = planner.build_install_plan(netcat, "rhel", base_dir=tmp_path)
    assert plan.steps[-1].argv == [
        "sudo", "dnf", "install", "-y", "nmap-ncat"]

    nmap = CATALOG.get("nmap")
    plan = planner.build_install_plan(nmap, "rhel", base_dir=tmp_path)
    assert plan.steps[-1].argv == ["sudo", "dnf", "install", "-y", "nmap"]


def test_rhel_update_and_uninstall_use_dnf():
    entry = {"id": "nmap", "name": "Nmap", "backend": "apt",
             "package": "nmap", "run": "nmap", "files_or_dirs": []}
    update = planner.build_update_plan(entry, "rhel")
    assert update.steps[0].argv == [
        "sudo", "dnf", "upgrade", "-y", "nmap"]
    uninstall = planner.build_uninstall_plan(entry, "rhel")
    assert uninstall.steps[0].argv == [
        "sudo", "dnf", "remove", "-y", "nmap"]


def test_debian_install_still_uses_apt_get(tmp_path: Path):
    nmap = CATALOG.get("nmap")
    plan = planner.build_install_plan(nmap, "debian", base_dir=tmp_path)
    assert plan.steps[-1].argv == [
        "sudo", "apt-get", "install", "-y", "nmap"]


def test_termux_install_unaffected(tmp_path: Path):
    nmap = CATALOG.get("nmap")
    plan = planner.build_install_plan(nmap, "termux", base_dir=tmp_path)
    assert plan.steps[-1].argv == ["pkg", "install", "-y", "nmap"]


def test_rhel_package_must_be_string_or_null():
    from darkfly.catalog import validate_catalog

    base = {
        "schema_version": 2,
        "tools": [{
            "id": "t", "name": "T", "aliases": [], "description": "d",
            "categories": ["c"], "homepage": "https://example.com",
            "source": "https://example.com/t", "license": "MIT",
            "status": "maintained", "tier": 1,
            "platforms": {"termux": True, "linux": True,
                          "gui_required": False},
            "size_mb_estimate": None, "last_verified": "2026-10-23",
            "install": {"backend": "apt", "package": "t",
                        "termux_package": None, "rhel_package": 42,
                        "version_pin": None, "deps_pkg": [],
                        "deps_apt": []},
            "run": "t", "successor_to": None,
        }],
    }
    problems = validate_catalog(base)
    assert any("rhel_package" in p for p in problems)
