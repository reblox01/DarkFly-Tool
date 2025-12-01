"""Command-line interface for the modern DarkFly v5 tool.

This CLI is intentionally small and focused. It is designed to:

- Run on Python 3.10+ on Linux / Termux.
- Read a simple tool configuration file (YAML or JSON).
- Provide basic commands:
  - `DarkFly5 list`  – list available tools / categories.
  - `DarkFly5 info`  – show details about a tool.
  - `DarkFly5 menu`  – simple interactive menu.

The goal is to provide a clean base that can be extended over time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


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
