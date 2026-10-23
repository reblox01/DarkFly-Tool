import hashlib
import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

import darkfly.installer as installer
import darkfly.manifest as manifest_mod
import darkfly.planner as planner
import darkfly.catalog as catalog_mod
from darkfly.catalog import ToolEntry


PAYLOAD = b"#!/bin/sh\necho fake tool\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_binary_tool(sha256_map=None, archive="none",
                     url="https://example.com/releases/{version}/"
                         "faketool-{arch}",
                     pin="v1.2.3", bin_name="faketool") -> ToolEntry:
    if sha256_map is None:
        sha256_map = {"x86_64": digest(PAYLOAD),
                      "aarch64": digest(PAYLOAD)}
    data = {
        "id": "faketool",
        "name": "Fake Tool",
        "aliases": [],
        "description": "A tool that only exists in tests.",
        "categories": ["utilities"],
        "homepage": "https://example.com",
        "source": "https://example.com/faketool",
        "license": "MIT",
        "status": "maintained",
        "tier": 1,
        "platforms": {"termux": True, "linux": True,
                      "gui_required": False},
        "size_mb_estimate": 5,
        "last_verified": "2026-10-23",
        "install": {
            "backend": "binary",
            "package": "faketool",
            "termux_package": None,
            "version_pin": pin,
            "deps_pkg": [],
            "deps_apt": [],
            "binary": {
                "url": url,
                "sha256": sha256_map,
                "archive": archive,
                "bin_name": bin_name,
            },
        },
        "run": "faketool",
        "successor_to": None,
    }
    return ToolEntry.from_dict(data)


class FakeDownloader:
    """Serves fixed bytes for any URL; records what was requested."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.urls = []

    def __call__(self, url, out):
        self.urls.append(url)
        out.write(self.payload)


# ------------------------- validation ---------------------------------


def _binary_catalog(**binary_overrides):
    tool = {
        "id": "faketool",
        "name": "Fake Tool",
        "aliases": [],
        "description": "Fake.",
        "categories": ["utilities"],
        "homepage": "https://example.com",
        "source": "https://example.com/faketool",
        "license": "MIT",
        "status": "maintained",
        "tier": 1,
        "platforms": {"termux": True, "linux": True,
                      "gui_required": False},
        "size_mb_estimate": 5,
        "last_verified": "2026-10-23",
        "install": {
            "backend": "binary",
            "package": "faketool",
            "termux_package": None,
            "version_pin": "v1.2.3",
            "deps_pkg": [],
            "deps_apt": [],
            "binary": {
                "url": "https://example.com/faketool-{arch}",
                "sha256": {"x86_64": digest(PAYLOAD)},
                "archive": "none",
                "bin_name": "faketool",
            },
        },
        "run": "faketool",
        "successor_to": None,
    }
    tool["install"]["binary"].update(binary_overrides)
    return {"schema_version": 2, "tools": [tool]}


def test_validate_accepts_a_binary_entry():
    assert catalog_mod.validate_catalog(_binary_catalog()) == []


def test_validate_rejects_binary_without_block():
    data = _binary_catalog()
    del data["tools"][0]["install"]["binary"]
    problems = catalog_mod.validate_catalog(data)
    assert any("install.binary is required" in p for p in problems)


def test_validate_rejects_binary_missing_checksum():
    data = _binary_catalog()
    del data["tools"][0]["install"]["binary"]["sha256"]
    problems = catalog_mod.validate_catalog(data)
    assert any("sha256" in p for p in problems)


def test_validate_rejects_binary_bad_digest():
    problems = catalog_mod.validate_catalog(
        _binary_catalog(sha256={"x86_64": "not-a-digest"}))
    assert any("64-character hex" in p for p in problems)


def test_validate_rejects_binary_unknown_archive():
    problems = catalog_mod.validate_catalog(
        _binary_catalog(archive="rar"))
    assert any("archive" in p for p in problems)


def test_validate_rejects_binary_non_https_url():
    problems = catalog_mod.validate_catalog(
        _binary_catalog(url="http://example.com/faketool"))
    assert any("HTTPS" in p for p in problems)


# --------------------------- planner ----------------------------------


def test_detect_arch_mapping():
    assert planner.detect_arch("x86_64") == "x86_64"
    assert planner.detect_arch("AMD64") == "x86_64"
    assert planner.detect_arch("aarch64") == "aarch64"
    assert planner.detect_arch("arm64") == "aarch64"
    assert planner.detect_arch("armv7l") == "arm"


def test_binary_plan_resolves_url_dest_and_checksum(tmp_path):
    tool = make_binary_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")

    assert plan.binary is not None
    assert plan.binary.url == \
        "https://example.com/releases/v1.2.3/faketool-x86_64"
    assert plan.binary.sha256 == digest(PAYLOAD)
    assert plan.binary.dest == str(tmp_path / "bin" / "faketool")
    assert plan.files_created == [plan.binary.dest]
    assert plan.steps == []
    rendered = plan.render()
    assert "SHA-256" in rendered
    assert "verified before install" in rendered


def test_binary_plan_without_build_for_arch_is_an_error(tmp_path):
    tool = make_binary_tool(sha256_map={"aarch64": digest(PAYLOAD)})
    with pytest.raises(planner.PlanError) as excinfo:
        planner.build_install_plan(tool, "termux", base_dir=tmp_path,
                                   arch="x86_64")
    assert "no binary build for architecture" in str(excinfo.value)


# --------------------------- installer --------------------------------


def test_binary_install_lands_in_bin_and_manifest(tmp_path):
    tool = make_binary_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")
    downloader = FakeDownloader(PAYLOAD)

    entry = installer.install_tool(plan, downloader=downloader,
                                   base_dir=tmp_path)

    dest = tmp_path / "bin" / "faketool"
    assert dest.is_file()
    assert dest.read_bytes() == PAYLOAD
    assert stat.S_IMODE(dest.stat().st_mode) == 0o755
    assert downloader.urls == [
        "https://example.com/releases/v1.2.3/faketool-x86_64"]

    assert entry["backend"] == "binary"
    assert entry["files_or_dirs"] == [str(dest)]
    saved = manifest_mod.get_entry("faketool", tmp_path)
    assert saved is not None
    assert saved["files_or_dirs"] == [str(dest)]


def test_binary_checksum_mismatch_aborts_and_installs_nothing(tmp_path):
    tool = make_binary_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")
    downloader = FakeDownloader(b"tampered bytes")

    with pytest.raises(installer.InstallError) as excinfo:
        installer.install_tool(plan, downloader=downloader,
                               base_dir=tmp_path)

    assert "Checksum mismatch" in str(excinfo.value)
    assert not (tmp_path / "bin" / "faketool").exists()
    assert manifest_mod.get_entry("faketool", tmp_path) is None
    # The temp download directory is cleaned up too.
    assert list(tmp_path.glob("darkfly-download-*")) == []


def _tar_gz_with(member_name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_with(member_name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member_name, payload)
    return buffer.getvalue()


def test_binary_install_from_tar_gz(tmp_path):
    archive_bytes = _tar_gz_with("faketool-1.2.3/faketool", PAYLOAD)
    tool = make_binary_tool(
        sha256_map={"x86_64": digest(archive_bytes)}, archive="tar.gz")
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")

    installer.install_tool(plan, downloader=FakeDownloader(archive_bytes),
                           base_dir=tmp_path)

    assert (tmp_path / "bin" / "faketool").read_bytes() == PAYLOAD


def test_binary_install_from_zip(tmp_path):
    archive_bytes = _zip_with("faketool.exe", PAYLOAD)
    tool = make_binary_tool(
        sha256_map={"x86_64": digest(archive_bytes)}, archive="zip",
        bin_name="faketool")
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")

    # The zip member is named faketool.exe, not faketool: no match.
    with pytest.raises(installer.InstallError):
        installer.install_tool(
            plan, downloader=FakeDownloader(archive_bytes),
            base_dir=tmp_path)
    assert manifest_mod.get_entry("faketool", tmp_path) is None

    archive_bytes = _zip_with("dist/faketool", PAYLOAD)
    tool = make_binary_tool(
        sha256_map={"x86_64": digest(archive_bytes)}, archive="zip")
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")
    installer.install_tool(plan, downloader=FakeDownloader(archive_bytes),
                           base_dir=tmp_path)
    assert (tmp_path / "bin" / "faketool").read_bytes() == PAYLOAD


def test_binary_uninstall_removes_file_and_entry(tmp_path):
    tool = make_binary_tool()
    plan = planner.build_install_plan(tool, "linux", base_dir=tmp_path,
                                      arch="x86_64")
    installer.install_tool(plan, downloader=FakeDownloader(PAYLOAD),
                           base_dir=tmp_path)
    dest = tmp_path / "bin" / "faketool"
    assert dest.is_file()

    removed = installer.uninstall_tool(
        "faketool", runner=lambda argv: 0, base_dir=tmp_path,
        env="linux")

    assert removed is True
    assert not dest.exists()
    assert manifest_mod.get_entry("faketool", tmp_path) is None


def test_binary_cannot_be_auto_updated(tmp_path):
    entry = {
        "id": "faketool", "name": "Fake Tool", "backend": "binary",
        "package": "faketool", "version_pin": "v1.2.3",
        "installed_at": "2026-10-23T00:00:00+00:00",
        "files_or_dirs": [str(tmp_path / "bin" / "faketool")],
        "run": "faketool",
    }
    plan = planner.build_update_plan(entry, "linux")
    assert plan.steps == []
    assert any("cannot update" in n for n in plan.notes)
