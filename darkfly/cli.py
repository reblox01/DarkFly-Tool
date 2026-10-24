"""Command-line interface for the modern DarkFly v5 tool.

This CLI is intentionally small and focused. It is designed to:

- Run on Python 3.10+ on Linux / Termux.
- Read the schema v2 tool catalog in ``catalog.json`` (see
  ``docs/DESIGN.md``).
- Provide commands:
  - `DarkFly5 list`      - list available tools.
  - `DarkFly5 info`      - show details and the install plan for a tool.
  - `DarkFly5 menu`      - simple interactive menu.
  - `DarkFly5 search`    - search the catalog by name, alias, or keyword.
  - `DarkFly5 install`   - install a tool with a journaled, resumable plan.
  - `DarkFly5 uninstall` - reverse an install using the manifest.
  - `DarkFly5 installed` - list what DarkFly has installed.
  - `DarkFly5 update`    - update one installed tool, or all of them.
  - `DarkFly5 verify`    - check installed tools are still present
                           (--versions also probes their versions).
  - `DarkFly5 doctor`    - print environment diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from darkfly import catalog as catalog_mod
from darkfly import installer as installer_mod
from darkfly import manifest as manifest_mod
from darkfly import planner as planner_mod
from darkfly import verify as verify_mod


# ---------------------------------------------------------------------------
# Legacy commands (list / info / menu), re-based on catalog v2.
#
# Phase 3 retired darkfly_tools.json: these commands now read the same
# schema v2 catalog as the rest of the CLI. Their outward behavior is
# kept as close as possible: list prints tools, info prints a tool's
# details plus the install plan for this environment, and menu lets a
# user pick a tool by number and shows its info. There is no longer a
# built-in fallback catalog: the packaged catalog ships inside the
# darkfly package, so load failure is a real error, not a silent
# substitution.
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    print("Available tools:\n")
    for tool in catalog.tools:
        categories = ", ".join(tool.categories)
        print(f"- {tool.id} (categories: {categories}) - {tool.name}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    tool = catalog.get(args.tool_id)
    if tool is None:
        print(f"Unknown tool id: {args.tool_id} "
              "(try 'DarkFly5 search' to find it)", file=sys.stderr)
        return 1

    print(f"ID:   {tool.id}")
    print(f"Name: {tool.name}")
    print(f"Categories: {', '.join(tool.categories)}")
    if tool.description:
        print(f"Description: {tool.description}")
    print(f"Backend: {tool.install.backend}")
    print(f"Status: {tool.status}, tier: {tool.tier}")
    print(f"Homepage: {tool.homepage}")
    if tool.run:
        print(f"Run: {tool.run}")

    # The install plan for this environment replaces the old
    # hand-written suggestion lists.
    env = planner_mod.detect_env()
    try:
        plan = planner_mod.build_install_plan(tool, env)
    except planner_mod.PlanError as exc:
        print(f"\nInstall on this environment: {exc}")
        return 0

    print(f"\nRecommended install plan for {env}:")
    if plan.steps:
        for step in plan.steps:
            print(f"  $ {' '.join(step.argv)}")
    elif plan.binary is not None:
        print(f"  Download and verify: {plan.binary.url}")
    else:
        print("  No automated install steps (manual tool).")
    for warning in plan.warnings:
        print(f"  ! {warning}")
    return 0


def _cmd_menu(args: argparse.Namespace) -> int:
    """Very small interactive menu.

    This is intentionally basic; it can be evolved into something richer
    (curses-based, fuzzy finder, etc.) in future versions.
    """

    catalog = _load_v2_catalog()
    if catalog is None:
        return 1

    tools = catalog.tools
    print("DarkFly v5 - simple menu")
    print("Select a tool by number, or 'q' to quit.\n")

    for idx, t in enumerate(tools, start=1):
        print(f"{idx}. {t.name} [{t.id}]")

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
        print("\n(Use 'DarkFly5 install' to install the tool above.)\n")
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


def _report_verify(tool_id: str, problems: List[str],
                   versions: bool, entry: Dict[str, Any]) -> int:
    """Print one tool's verify result; return 1 when it has problems."""

    if versions:
        version, probe_problem = verify_mod.version_probe(entry)
        if version is not None:
            print(f"  version: {version}")
        elif probe_problem is not None:
            problems = problems + [probe_problem]

    if not problems:
        print(f"OK: {tool_id}")
        return 0
    print(f"PROBLEM: {tool_id}")
    for problem in problems:
        print(f"  - {problem}")
    return 1


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
        if not problems and not args.versions:
            print(f"OK: {entry['id']} - manifest entry, run command, "
                  "and recorded paths all check out.")
            return 0
        return _report_verify(entry["id"], problems, args.versions,
                              entry)

    tools = manifest_mod.load_manifest()
    if not tools:
        print("No tools have been installed with DarkFly yet, so "
              "there is nothing to verify.")
        return 0

    results = verify_mod.verify_all(catalog)
    bad = 0
    for tool_id, problems in results.items():
        bad += _report_verify(tool_id, problems, args.versions,
                              tools[tool_id])
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
    if env in ("debian", "rhel", "linux"):
        distro_id = planner_mod.os_release_id()
        print(f"Distro family:    {env}")
        if distro_id:
            print(f"Distro ID:        {distro_id}")
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
    p_verify.add_argument("--versions", action="store_true",
                          help="Also run '<run> --version' for tools "
                               "found on PATH (opt-in execution)")
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
