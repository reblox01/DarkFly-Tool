"""Catalog-wide coverage tests.

Every shipped entry must either produce an install plan in both
simulated environments, or fail planning with a *platform* error
(not available here, needs a GUI). Anything else - a crash, a bad
argv shape, a mystery error - means the entry is broken.
"""

from pathlib import Path

import pytest

import darkfly.planner as planner
from darkfly.catalog import load_catalog


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / "catalog.json")

PLATFORM_ERROR_MARKERS = (
    "not available on Termux",
    "not available on Linux",
    "needs a graphical desktop",
    "unsupported environment",
    "no binary build for architecture",
)


def test_catalog_reached_phase_2_size():
    assert 58 <= len(CATALOG) <= 64


def test_catalog_has_all_phase_2_additions():
    ids = {tool.id for tool in CATALOG.tools}
    expected = {
        # Tier 0 remainder
        "tshark", "netcat-openbsd", "hashcat", "impacket", "responder",
        "metasploit-framework",
        # Tier 1 modern layer
        "naabu", "katana", "dnsx", "amass", "dalfox", "chisel",
        "kerbrute", "bloodhound-python", "certipy", "evil-winrm-py",
        "xsstrike", "enum4linux-ng", "wpscan", "trufflehog",
        "ligolo-ng", "zap", "burpsuite", "wireshark",
        # Tier 2 resources
        "payloadsallthethings", "hacktricks", "gtfobins", "rockyou",
        # Tier 3 emerging
        "caido", "scoutsuite", "pacu",
    }
    missing = expected - ids
    assert not missing, f"missing catalog entries: {sorted(missing)}"


def test_catalog_has_no_banned_tool_families():
    banned_words = ("bomber", "spam", "ddos", "phish", "callflood",
                    "smsbomb")
    for tool in CATALOG.tools:
        blob = f"{tool.id} {tool.name} {tool.description}".lower()
        for word in banned_words:
            assert word not in blob, f"{tool.id} mentions '{word}'"


def test_catalog_gui_companions_are_never_fake_installed():
    for tool_id in ("zap", "burpsuite", "wireshark", "caido"):
        tool = CATALOG.get(tool_id)
        assert tool is not None
        assert tool.platforms.gui_required
        assert not tool.platforms.termux
        assert tool.install.backend == "manual"
        with pytest.raises(planner.PlanError):
            planner.build_install_plan(tool, "linux")


@pytest.mark.parametrize("tool", CATALOG.tools,
                         ids=[t.id for t in CATALOG.tools])
@pytest.mark.parametrize("env", ["linux", "termux"])
def test_every_entry_plans_or_fails_as_platform_error(tool, env,
                                                      tmp_path):
    try:
        plan = planner.build_install_plan(tool, env, base_dir=tmp_path)
    except planner.PlanError as exc:
        message = str(exc)
        assert any(marker in message for marker in
                   PLATFORM_ERROR_MARKERS), (
            f"{tool.id} on {env} failed planning with a non-platform "
            f"error: {message}")
        return

    assert plan.tool_id == tool.id
    assert plan.backend == tool.install.backend
    # Dry-run rendering must never blow up on a real entry.
    rendered = plan.render()
    assert tool.name in rendered
    # Every executable step is an argv list of strings.
    for step in plan.steps:
        assert isinstance(step.argv, list) and step.argv
        assert all(isinstance(part, str) for part in step.argv)


def test_metasploit_plan_warns_heavy():
    tool = CATALOG.get("metasploit-framework")
    plan = planner.build_install_plan(tool, "linux")
    assert any("Heavy install" in w for w in plan.warnings)
    assert plan.steps == []  # manual: described, never automated
