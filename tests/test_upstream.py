"""Tests for tools/check_upstream.py (Phase 3).

The HTTP layer is faked, so no test touches the network.
"""

import contextlib
import importlib.util
import io
import json
import urllib.error
from pathlib import Path


def _load_script():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "check_upstream", root / "tools" / "check_upstream.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen_factory(monkeypatch):
    """HEAD returns 405 for one host (GET fallback), 200 elsewhere."""

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        method = request.get_method()
        if "refuse-head.example" in url and method == "HEAD":
            raise urllib.error.HTTPError(url, 405, "Not Allowed", {}, None)
        if "dead.example" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return _FakeResponse(200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_check_url_head_fallback_and_status(monkeypatch):
    mod = _load_script()
    _fake_urlopen_factory(monkeypatch)
    assert mod.check_url("https://ok.example/x").startswith("ok")
    # HEAD refused, GET succeeds: still ok.
    assert mod.check_url("https://refuse-head.example/x").startswith("ok")
    assert mod.check_url("https://dead.example/x") == "http 404"


def test_main_reports_and_exit_code(monkeypatch, tmp_path: Path, capsys):
    mod = _load_script()
    _fake_urlopen_factory(monkeypatch)
    catalog = {
        "tools": [
            {"id": "good", "homepage": "https://ok.example/a",
             "source": "https://ok.example/b"},
            {"id": "bad", "homepage": "https://dead.example/a",
             "source": "https://dead.example/a"},
        ]
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        code = mod.main([str(path)])
    out = buf.getvalue()
    assert code == 1
    assert "good homepage: ok (200)" in out
    assert "bad homepage: http 404" in out
    # Same URL twice in one tool is checked once.
    assert out.count("dead.example") == 1
    assert "1 failed" in out
