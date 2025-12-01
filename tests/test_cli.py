import json
from pathlib import Path

import darkfly.cli as cli


def test_iter_tools_uses_builtin_when_no_config(tmp_path: Path, monkeypatch):
    # Run with CWD in a temp directory that has no darkfly_tools.json
    monkeypatch.chdir(tmp_path)
    config = cli._load_config()
    tools = cli._iter_tools(config)

    assert tools, "Expected at least one built-in example tool"
    assert any(t.id == "nmap" for t in tools)


def test_cli_list_works_with_builtin_config(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "nmap" in captured.out


def test_cli_respects_json_config(tmp_path: Path, monkeypatch):
    # Write a custom config file and ensure it is picked up.
    cfg = {
        "categories": [
            {
                "id": "custom",
                "name": "Custom",
                "tools": [
                    {
                        "id": "mytool",
                        "name": "My Tool",
                        "description": "Custom tool",
                        "install_commands": ["echo install-mytool"],
                    }
                ],
            }
        ]
    }
    (tmp_path / "darkfly_tools.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = cli._load_config()
    tools = cli._iter_tools(config)

    assert any(t.id == "mytool" for t in tools)
    assert not any(t.id == "nmap" for t in tools), "Expected only custom tools when config exists"
