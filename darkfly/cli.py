"""Command-line interface for the modern DarkFly v5 tool.

This CLI is intentionally small and focused. It is designed to:

- Run on Python 3.10+ on Linux / Termux.
- Read a simple tool configuration file (YAML or JSON).
- Provide basic commands:
  - `DarkFly5 list`  – list available tools / categories.
  - `DarkFly5 info`  – show details about a tool.
  - `DarkFly5 menu`  – simple interactive menu.

The goal is to provide a clean base that can be extended over time.

The next-generation commands (see ``docs/DESIGN.md``) work on the
schema v2 catalog in ``catalog.json`` instead:

- `DarkFly5 search`    - search the catalog by name, alias, or keyword.
- `DarkFly5 install`   - install a tool with a journaled, resumable plan.
- `DarkFly5 uninstall` - reverse an install using the manifest.
- `DarkFly5 installed` - list what DarkFly has installed.
- `DarkFly5 update`    - update one installed tool, or all of them.
- `DarkFly5 verify`    - check installed tools are still present.
- `DarkFly5 doctor`    - print environment diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from darkfly import catalog as catalog_mod
from darkfly import installer as installer_mod
from darkfly import manifest as manifest_mod
from darkfly import planner as planner_mod
from darkfly import verify as verify_mod


DEFAULT_CONFIG_LOCATIONS = [
    Path("darkfly_tools.json"),
    Path("darkfly_tools.yaml"),  # reserved for a future YAML-based config
]


@dataclass
class Tool:
    id: str
    name: str
    description: str = ""
    category: str = "general"
    install_commands: List[str] | None = None


def _detect_env() -> str:
    """Detect a coarse runtime environment for better suggestions.

    Returns one of:
    - "termux" – running under Termux on Android.
    - "linux" – Linux (non-Termux).
    - "other" – anything else.
    """

    # Termux commonly exposes this variable
    if os.environ.get("TERMUX_VERSION"):
        return "termux"

    home = str(Path.home())
    if "com.termux" in home:
        return "termux"

    if sys.platform.startswith("linux"):
        return "linux"

    return "other"


def _load_config(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Load the tool configuration from the repository or install directory.

    For the initial version we support a small JSON file. If no configuration
    file is found, we fall back to a tiny built-in example so that the CLI is
    still usable out of the box.
    """

    base = base_dir or Path.cwd()

    # Look for config files in a few conventional locations
    for rel in DEFAULT_CONFIG_LOCATIONS:
        candidate = base / rel
        if candidate.is_file() and candidate.suffix == ".json":
            with candidate.open("r", encoding="utf-8") as f:
                return json.load(f)
        # YAML support can be added later if desired.

    # Fallback: built-in minimal configuration
    return {
        "categories": [
            {
                "id": "examples",
                "name": "Example Tools",
                "tools": [
                    {
                        "id": "nmap",
                        "name": "Nmap",
                        "description": "Show basic information about Nmap and how to install it.",
                        "install_commands": [
                            "sudo apt install nmap",
                            "# Termux: pkg install nmap",
                        ],
                    }
                ],
            }
        ]
    }


def _iter_tools(config: Dict[str, Any]) -> List[Tool]:
    tools: List[Tool] = []
    for cat in config.get("categories", []):
        cat_id = cat.get("id", "general")
        for t in cat.get("tools", []):
            tools.append(
                Tool(
                    id=t.get("id"),
                    name=t.get("name", t.get("id", "")),
                    description=t.get("description", ""),
                    category=cat_id,
                    install_commands=t.get("install_commands"),
                )
            )
    return tools


def _cmd_list(args: argparse.Namespace) -> int:
    config = _load_config()
    tools = _iter_tools(config)

    if not tools:
        print("No tools are defined in darkfly_tools.json (or built-in config).", file=sys.stderr)
        return 1

    print("Available tools:\n")
    for tool in tools:
        print(f"- {tool.id} (category: {tool.category}) - {tool.name}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    config = _load_config()
    tools = {t.id: t for t in _iter_tools(config)}

    tool = tools.get(args.tool_id)
    if not tool:
        print(f"Unknown tool id: {args.tool_id}", file=sys.stderr)
        return 1

    print(f"ID:   {tool.id}")
    print(f"Name: {tool.name}")
    print(f"Category: {tool.category}")
    if tool.description:
        print(f"Description: {tool.description}")
    if tool.install_commands:
        env = _detect_env()

        # Heuristics based on common package manager commands
        termux_cmds = [c for c in tool.install_commands if "pkg " in c or "Termux" in c]
        linux_cmds = [c for c in tool.install_commands if "apt " in c]
        generic_cmds = [
            c
            for c in tool.install_commands
            if c not in termux_cmds and c not in linux_cmds
        ]

        if env == "termux":
            preferred = termux_cmds or linux_cmds or generic_cmds
            label = "Termux"
        elif env == "linux":
            preferred = linux_cmds or termux_cmds or generic_cmds
            label = "Linux"
        else:
            preferred = generic_cmds or linux_cmds or termux_cmds
            label = "this platform"

        print(f"\nRecommended install commands for {label}:")
        for cmd in preferred:
            print(f"  {cmd}")

        remaining = [c for c in tool.install_commands if c not in preferred]
        if remaining:
            print("\nOther install commands:")
            for cmd in remaining:
                print(f"  {cmd}")
    else:
        print("\nNo install commands recorded for this tool yet.")
    return 0


def _cmd_menu(args: argparse.Namespace) -> int:
    """Very small interactive menu.

    This is intentionally basic; it can be evolved into something richer
    (curses-based, fuzzy finder, etc.) in future versions.
    """

    config = _load_config()
    tools = _iter_tools(config)
    if not tools:
        print("No tools are defined in darkfly_tools.json (or built-in config).", file=sys.stderr)
        return 1

    print("DarkFly v5 - simple menu")
    print("Select a tool by number, or 'q' to quit.\n")

    for idx, t in enumerate(tools, start=1):
        print(f"{idx}. {t.name} [{t.id}] (category: {t.category})")

    while True:
        choice = input("Select: ").strip()
        if choice.lower() in {"q", "quit", "exit"}:
            return 0
        if not choice.isdigit():
            print("Please enter a number or 'q' to quit.")
            continue
        idx = int(choice)
        if not (1 <= idx <= len(tools)):
            print("Choice out of range.")
            continue
        tool = tools[idx - 1]
        # Reuse info command behavior
        print()
        _cmd_info(argparse.Namespace(tool_id=tool.id))
        print("\n(Use the suggested commands above in your shell to install/run this tool.)\n")
    # Unreachable


# ---------------------------------------------------------------------------
# Next-generation commands (catalog schema v2, see docs/DESIGN.md)
# ---------------------------------------------------------------------------


def _load_v2_catalog() -> Optional["catalog_mod.Catalog"]:
    """Load the v2 catalog, printing problems and returning None on error."""

    try:
        return catalog_mod.load_catalog()
    except catalog_mod.CatalogError as exc:
        print("The tool catalog could not be loaded:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return None


def _confirm(prompt: str) -> bool:
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def _cmd_search(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    results = catalog_mod.search_tools(catalog, args.query)
    if not results:
        print(f"No tools matched '{args.query}'.")
        return 0

    print(f"Found {len(results)} tool(s) matching '{args.query}':\n")
    for tool in results:
        categories = ", ".join(tool.categories)
        print(f"- {tool.id} - {tool.name} (tier {tool.tier}, {categories})")
        if tool.description:
            print(f"    {tool.description}")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    tool = catalog.get(args.tool_id)
    if tool is None:
        print(f"Unknown tool id: {args.tool_id} "
              "(try 'DarkFly5 search' to find it)", file=sys.stderr)
        return 1

    env = planner_mod.detect_env()
    try:
        plan = planner_mod.build_install_plan(tool, env)
    except planner_mod.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # If a previous install of this same tool was interrupted, say so:
    # the installer will resume it instead of starting over.
    journal = installer_mod.load_journal()
    if journal is not None and journal.get("tool_id") == tool.id:
        done = len(journal.get("completed", []))
        total = len(journal.get("steps", []))
        print(f"Resuming an interrupted install of {tool.name}: "
              f"{done} of {total} steps already done.\n")

    print(plan.render())

    if args.dry_run:
        print("\nDry run: nothing was executed.")
        return 0

    if not plan.steps and plan.binary is None:
        print("\nThis tool has no automated install steps, so nothing "
              "was installed.")
        return 1

    if not args.yes:
        if not _confirm("\nProceed with this install? [y/N] "):
            print("Aborted. Nothing was installed.")
            return 1

    try:
        installer_mod.install_tool(plan)
    except installer_mod.InstallError as exc:
        print(f"\nInstall did not complete: {exc}", file=sys.stderr)
        return 1

    print(f"\nInstalled {tool.name}.")
    if tool.run:
        print(f"Run it with: {tool.run}")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    entry = manifest_mod.get_entry(args.tool_id)
    if entry is None:
        print(f"'{args.tool_id}' is not installed (no manifest entry). "
              "See 'DarkFly5 installed'.", file=sys.stderr)
        return 1

    env = planner_mod.detect_env()
    try:
        plan = planner_mod.build_uninstall_plan(entry, env)
    except planner_mod.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(plan.render())

    if args.dry_run:
        print("\nDry run: nothing was executed.")
        return 0

    if not args.yes:
        if not _confirm("\nProceed with this uninstall? [y/N] "):
            print("Aborted. Nothing was removed.")
            return 1

    try:
        installer_mod.uninstall_tool(args.tool_id, env=env)
    except installer_mod.InstallError as exc:
        print(f"\nUninstall did not complete: {exc}", file=sys.stderr)
        return 1

    print(f"\nUninstalled {entry.get('name', args.tool_id)}.")
    return 0


def _resolve_manifest_entry(tool_id: str,
                              catalog: "catalog_mod.Catalog",
                              ) -> Optional[Dict[str, Any]]:
    """Find a manifest entry by tool id, resolving aliases first."""

    entry = manifest_mod.get_entry(tool_id)
    if entry is not None:
        return entry
    tool = catalog.get(tool_id)
    if tool is not None:
        return manifest_mod.get_entry(tool.id)
    return None


def _cmd_update(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1
    env = planner_mod.detect_env()

    if args.tool_id:
        entry = _resolve_manifest_entry(args.tool_id, catalog)
        if entry is None:
            print(f"'{args.tool_id}' is not installed (no manifest "
                  "entry). See 'DarkFly5 installed'.", file=sys.stderr)
            return 1
        tool = catalog.get(entry["id"])
        try:
            plan = planner_mod.build_update_plan(entry, env, tool)
        except planner_mod.PlanError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(plan.render())
        if args.dry_run:
            print("\nDry run: nothing was executed.")
            return 0
        if not plan.steps:
            print("\nThis tool cannot be updated automatically, so "
                  "nothing was changed.")
            return 1
        if not args.yes:
            if not _confirm("\nProceed with this update? [y/N] "):
                print("Aborted. Nothing was updated.")
                return 1
        try:
            installer_mod.update_tool(plan)
        except installer_mod.InstallError as exc:
            print(f"\nUpdate did not complete: {exc}", file=sys.stderr)
            return 1
        print(f"\nUpdated {plan.name}.")
        return 0

    # Update everything in the manifest.
    tools = manifest_mod.load_manifest()
    if not tools:
        print("No tools have been installed with DarkFly yet, so "
              "there is nothing to update.")
        return 0

    plans = []
    for tool_id in sorted(tools):
        entry = tools[tool_id]
        try:
            plan = planner_mod.build_update_plan(
                entry, env, catalog.get(tool_id))
        except planner_mod.PlanError as exc:
            print(f"{tool_id}: cannot build an update plan: {exc}")
            continue
        plans.append(plan)

    for plan in plans:
        print(plan.render())
        print()

    if args.dry_run:
        print("Dry run: nothing was executed.")
        return 0

    runnable = [plan for plan in plans if plan.steps]
    if not runnable:
        print("None of the installed tools can be updated "
              "automatically.")
        return 0

    if not args.yes:
        if not _confirm(f"Update {len(runnable)} tool(s)? [y/N] "):
            print("Aborted. Nothing was updated.")
            return 1

    failures = 0
    for plan in runnable:
        try:
            installer_mod.update_tool(plan)
        except installer_mod.InstallError as exc:
            print(f"Update of {plan.tool_id} failed: {exc}",
                  file=sys.stderr)
            failures += 1
        else:
            print(f"Updated {plan.name}.")
    return 1 if failures else 0


def _cmd_verify(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    if args.tool_id:
        entry = _resolve_manifest_entry(args.tool_id, catalog)
        if entry is None:
            print(f"'{args.tool_id}' is not installed (no manifest "
                  "entry). See 'DarkFly5 installed'.", file=sys.stderr)
            return 1
        problems = verify_mod.verify_entry(entry, catalog)
        if not problems:
            print(f"OK: {entry['id']} - manifest entry, run command, "
                  "and recorded paths all check out.")
            return 0
        print(f"PROBLEM: {entry['id']}")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    tools = manifest_mod.load_manifest()
    if not tools:
        print("No tools have been installed with DarkFly yet, so "
              "there is nothing to verify.")
        return 0

    results = verify_mod.verify_all(catalog)
    bad = 0
    for tool_id, problems in results.items():
        if not problems:
            print(f"OK: {tool_id}")
        else:
            bad += 1
            print(f"PROBLEM: {tool_id}")
            for problem in problems:
                print(f"  - {problem}")
    print(f"\nVerified {len(results)} tool(s): "
          f"{len(results) - bad} OK, {bad} with problems.")
    return 1 if bad else 0


def _cmd_installed(args: argparse.Namespace) -> int:
    tools = manifest_mod.load_manifest()
    if not tools:
        print("No tools have been installed with DarkFly yet.")
        return 0

    print("Installed with DarkFly:\n")
    for tool_id in sorted(tools):
        entry = tools[tool_id]
        pin = entry.get("version_pin") or "-"
        run = entry.get("run") or "-"
        print(f"- {tool_id} (backend: {entry.get('backend', '?')}, "
              f"pin: {pin}, installed: {entry.get('installed_at', '?')}, "
              f"run: {run})")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    import platform
    import shutil

    env = planner_mod.detect_env()
    print("DarkFly doctor\n")
    print(f"Environment:      {env}")
    print(f"Python version:   {platform.python_version()}")
    print(f"Platform:         {platform.platform()}")
    print(f"DarkFly home:     {manifest_mod.darkfly_home()}")

    home = Path.home()
    try:
        usage = shutil.disk_usage(home)
        free_gb = usage.free / (1024 ** 3)
        print(f"Free disk (home): {free_gb:.1f} GB")
    except OSError:
        print("Free disk (home): unknown")

    print("\nTools on PATH:")
    for name in ("pkg", "apt-get", "pipx", "go", "git"):
        location = shutil.which(name)
        status = location if location else "not found"
        print(f"  {name:<8} {status}")

    print("\nCatalog:")
    try:
        catalog = catalog_mod.load_catalog()
    except catalog_mod.CatalogError as exc:
        for problem in exc.problems:
            print(f"  - {problem}")
        return 1
    print(f"  OK: schema v{catalog.schema_version}, "
          f"{len(catalog)} tools, validation passed")

    journal = installer_mod.load_journal()
    if journal is not None:
        print(f"\nNote: an interrupted install of "
              f"'{journal.get('tool_id')}' is pending. Re-run "
              f"'DarkFly5 install {journal.get('tool_id')}' to resume it.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DarkFly5", description="Modern DarkFly CLI (Python 3)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List available tools")
    p_list.set_defaults(func=_cmd_list)

    p_info = sub.add_parser("info", help="Show details for a tool")
    p_info.add_argument("tool_id", help="ID of the tool (see 'list')")
    p_info.set_defaults(func=_cmd_info)

    p_menu = sub.add_parser("menu", help="Interactive text menu")
    p_menu.set_defaults(func=_cmd_menu)

    p_search = sub.add_parser("search", help="Search the catalog (v2)")
    p_search.add_argument("query", help="Name, alias, or keyword to search for")
    p_search.set_defaults(func=_cmd_search)

    p_install = sub.add_parser("install", help="Install a tool (v2 catalog)")
    p_install.add_argument("tool_id", help="ID of the tool (see 'search')")
    p_install.add_argument("--dry-run", action="store_true",
                           help="Print the install plan without executing it")
    p_install.add_argument("--yes", "-y", action="store_true",
                           help="Do not ask for confirmation")
    p_install.set_defaults(func=_cmd_install)

    p_uninstall = sub.add_parser("uninstall",
                                 help="Uninstall a tool installed by DarkFly")
    p_uninstall.add_argument("tool_id", help="ID of the tool (see 'installed')")
    p_uninstall.add_argument("--dry-run", action="store_true",
                             help="Print the uninstall plan without executing it")
    p_uninstall.add_argument("--yes", "-y", action="store_true",
                             help="Do not ask for confirmation")
    p_uninstall.set_defaults(func=_cmd_uninstall)

    p_installed = sub.add_parser("installed",
                                 help="List tools installed by DarkFly")
    p_installed.set_defaults(func=_cmd_installed)

    p_update = sub.add_parser("update",
                              help="Update installed tools (v2 catalog)")
    p_update.add_argument("tool_id", nargs="?",
                          help="Tool to update (default: all installed)")
    p_update.add_argument("--dry-run", action="store_true",
                          help="Print the update plan(s) without "
                               "executing them")
    p_update.add_argument("--yes", "-y", action="store_true",
                          help="Do not ask for confirmation")
    p_update.set_defaults(func=_cmd_update)

    p_verify = sub.add_parser("verify",
                              help="Check installed tools still work")
    p_verify.add_argument("tool_id", nargs="?",
                          help="Tool to verify (default: all installed)")
    p_verify.set_defaults(func=_cmd_verify)

    p_doctor = sub.add_parser("doctor", help="Diagnose the environment")
    p_doctor.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
