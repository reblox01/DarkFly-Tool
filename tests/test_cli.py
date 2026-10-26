import json
from pathlib import Path

import darkfly.cli as cli


def _write_v2_catalog(tmp_path: Path):
    """Write a minimal catalog v2 file in tmp_path and return it."""

    cfg = {
        "schema_version": 2,
        "tools": [
            {
                "id": "mytool",
                "name": "My Tool",
                "aliases": [],
                "description": "Custom tool",
                "categories": ["custom"],
                "homepage": "https://example.com/mytool",
                "source": "https://example.com/mytool.git",
                "license": "MIT",
                "status": "maintained",
                "tier": 1,
                "platforms": {"termux": True, "linux": True,
                              "gui_required": False},
                "size_mb_estimate": 1,
                "last_verified": "2026-10-23",
                "install": {
                    "backend": "apt",
                    "package": "mytool",
                    "termux_package": None,
                    "version_pin": None,
                    "deps_pkg": [],
                    "deps_apt": [],
                },
                "run": "mytool",
                "successor_to": None,
            }
        ],
    }
    (tmp_path / "catalog.json").write_text(json.dumps(cfg),
                                           encoding="utf-8")
    return cfg


def test_cli_list_works_with_v2_catalog(monkeypatch, capsys, tmp_path: Path):
    _write_v2_catalog(tmp_path)
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "mytool" in captured.out
    assert "My Tool" in captured.out


def test_cli_info_works_with_v2_catalog(monkeypatch, capsys, tmp_path: Path):
    _write_v2_catalog(tmp_path)
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["info", "mytool"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "My Tool" in captured.out
    assert "apt" in captured.out
    assert "mytool" in captured.out


def test_cli_list_fails_without_any_catalog(monkeypatch, capsys, tmp_path: Path):
    # An empty directory has no catalog.json anywhere the loader
    # checks (the packaged catalog is only found next to the
    # installed package, not in an arbitrary CWD). list must fail
    # cleanly instead of inventing tools.
    monkeypatch.chdir(tmp_path)
    # Hide the packaged catalog by replacing the loader with one
    # that only looks in the (empty) tmp directory.
    import darkfly.catalog as catalog_mod

    def fake_load(path=None):
        found = (tmp_path / "catalog.json")
        if not found.is_file():
            raise catalog_mod.CatalogError(["catalog.json not found"])
        return catalog_mod.load_catalog(found)

    monkeypatch.setattr("darkfly.cli.catalog_mod.load_catalog", fake_load)
    exit_code = cli.main(["list"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "mytool" not in captured.out


def test_cli_list_ignores_old_format_file(monkeypatch, capsys, tmp_path: Path):
    # A legacy darkfly_tools.json in the directory must NOT be picked
    # up anymore: list fails because no v2 catalog exists.
    old_cfg = {
        "categories": [
            {
                "id": "custom",
                "name": "Custom",
                "tools": [
                    {
                        "id": "oldtool",
                        "name": "Old Tool",
                        "description": "Legacy entry",
                        "install_commands": ["echo install-oldtool"],
                    }
                ],
            }
        ]
    }
    (tmp_path / "darkfly_tools.json").write_text(json.dumps(old_cfg),
                                                 encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    import darkfly.catalog as catalog_mod

    def fake_load(path=None):
        raise catalog_mod.CatalogError(["catalog.json not found"])

    monkeypatch.setattr("darkfly.cli.catalog_mod.load_catalog", fake_load)
    exit_code = cli.main(["list"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "oldtool" not in captured.out


def test_cli_menu_picks_tool_and_quits(monkeypatch, capsys, tmp_path: Path):
    _write_v2_catalog(tmp_path)
    monkeypatch.chdir(tmp_path)
    answers = iter(["1", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    exit_code = cli.main(["menu"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "My Tool" in captured.out
    assert "darkfly.sohailkoutari.com" in captured.out


def test_cli_help_mentions_research_url():
    parser = cli.build_parser()
    assert cli.RESEARCH_URL == "https://darkfly.sohailkoutari.com"
    assert "darkfly.sohailkoutari.com" in (parser.epilog or "")
