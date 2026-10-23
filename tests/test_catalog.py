import copy
import json
from pathlib import Path

import pytest

import darkfly.catalog as catalog_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


def _valid_tool(**overrides):
    tool = {
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
    tool.update(overrides)
    return tool


def _valid_catalog(tools=None):
    return {"schema_version": 2, "tools": tools or [_valid_tool()]}


def test_real_catalog_loads_and_validates():
    catalog = catalog_mod.load_catalog(REPO_ROOT / "catalog.json")

    assert catalog.schema_version == 2
    assert len(catalog) >= 18

    ids = {tool.id for tool in catalog.tools}
    # The migrated v5 tools are all present.
    for expected in ("nmap", "whois", "dnsenum", "nikto", "sqlmap",
                     "hydra", "john", "aircrack-ng"):
        assert expected in ids
    # Plus the modern layer the classic installers lack.
    for expected in ("nuclei", "subfinder", "httpx", "ffuf", "netexec",
                     "seclists", "theharvester"):
        assert expected in ids
    # CrackMapExec is archived; only its successor is listed.
    assert "crackmapexec" not in ids


def test_validate_accepts_minimal_catalog():
    assert catalog_mod.validate_catalog(_valid_catalog()) == []


def test_validate_catches_missing_field():
    data = _valid_catalog()
    del data["tools"][0]["license"]
    problems = catalog_mod.validate_catalog(data)

    assert any("license" in p for p in problems)


def test_validate_catches_unknown_backend():
    tool = _valid_tool()
    tool["install"] = dict(tool["install"], backend="teleport")
    problems = catalog_mod.validate_catalog(_valid_catalog([tool]))

    assert any("unknown backend" in p for p in problems)


def test_validate_catches_bad_status_and_tier():
    tool = _valid_tool(status="zombie", tier=9)
    problems = catalog_mod.validate_catalog(_valid_catalog([tool]))

    assert any("status" in p for p in problems)
    assert any("tier" in p for p in problems)


def test_validate_catches_duplicate_ids_and_aliases():
    first = _valid_tool()
    second = _valid_tool(name="Other Nmap")
    problems = catalog_mod.validate_catalog(_valid_catalog([first, second]))
    assert any("already used" in p for p in problems)

    alias_clash = _valid_tool(id="other", aliases=["nmap"])
    problems = catalog_mod.validate_catalog(
        _valid_catalog([first, alias_clash]))
    assert any("already used" in p for p in problems)


def test_load_catalog_raises_with_all_problems(tmp_path: Path):
    bad = _valid_catalog()
    del bad["tools"][0]["name"]
    bad["tools"][0]["status"] = "zombie"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(catalog_mod.CatalogError) as excinfo:
        catalog_mod.load_catalog(path)

    assert len(excinfo.value.problems) >= 2


def test_load_catalog_missing_file(tmp_path: Path):
    with pytest.raises(catalog_mod.CatalogError):
        catalog_mod.load_catalog(tmp_path / "nope.json")


@pytest.fixture(scope="module")
def real_catalog():
    return catalog_mod.load_catalog(REPO_ROOT / "catalog.json")


def test_search_finds_netexec_by_alias(real_catalog):
    results = catalog_mod.search_tools(real_catalog, "nxc")

    assert results, "Expected the alias 'nxc' to find NetExec"
    assert results[0].id == "netexec"


def test_search_finds_netexec_by_predecessor(real_catalog):
    # Users still search for CrackMapExec; they should land on NetExec.
    results = catalog_mod.search_tools(real_catalog, "crackmapexec")

    assert any(tool.id == "netexec" for tool in results)


def test_search_is_case_insensitive(real_catalog):
    results = catalog_mod.search_tools(real_catalog, "NUCLEI")

    assert results and results[0].id == "nuclei"


def test_search_matches_categories_and_descriptions(real_catalog):
    by_category = catalog_mod.search_tools(real_catalog, "fuzzing")
    assert {t.id for t in by_category} >= {"ffuf", "gobuster"}

    by_description = catalog_mod.search_tools(real_catalog, "wordlists")
    assert any(t.id == "seclists" for t in by_description)


def test_search_no_match_returns_empty(real_catalog):
    assert catalog_mod.search_tools(real_catalog, "zzzz-not-a-tool") == []


def test_catalog_get_matches_id_and_alias(real_catalog):
    assert real_catalog.get("netexec").name == "NetExec"
    assert real_catalog.get("nxc").id == "netexec"
    assert real_catalog.get("does-not-exist") is None
