#!/usr/bin/env python3
"""Check that every catalog homepage and source URL still answers.

This is the upstream liveness check behind
``.github/workflows/upstream-check.yml``: for each tool in
``catalog.json`` it issues a HEAD request to the homepage and the
source URL (falling back to GET on 405/501, since some hosts refuse
HEAD), with a 10 second timeout, one URL at a time. It prints a
per-tool report plus a summary count and exits non-zero when any URL
fails, so a dead upstream or a renamed repository shows up as a CI
failure instead of a silent broken catalog entry.

Stdlib only. It never installs or executes anything.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


TIMEOUT_SECONDS = 10
USER_AGENT = "DarkFly-Tool upstream check (catalog liveness)"


def check_url(url: str) -> str:
    """Return a one-line verdict for one URL: ok, an HTTP status, or
    an error description."""

    for method in ("HEAD", "GET"):
        request = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request,
                                        timeout=TIMEOUT_SECONDS) as resp:
                return f"ok ({resp.status})"
        except urllib.error.HTTPError as exc:
            if exc.code in (405, 501) and method == "HEAD":
                continue  # some hosts refuse HEAD; retry with GET
            return f"http {exc.code}"
        except urllib.error.URLError as exc:
            return f"error: {exc.reason}"
        except (TimeoutError, OSError) as exc:
            return f"error: {exc}"
    return "error: no verdict"  # pragma: no cover - defensive


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    catalog_path = Path(args[0]) if args else Path("catalog.json")
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read catalog at {catalog_path}: {exc}",
              file=sys.stderr)
        return 2

    checked = 0
    failed = 0
    for tool in data.get("tools", []):
        tool_id = tool.get("id", "?")
        urls = {}
        for label in ("homepage", "source"):
            url = tool.get(label)
            if url and url not in urls.values():
                urls[label] = url
        for label, url in urls.items():
            verdict = check_url(url)
            checked += 1
            if not verdict.startswith("ok"):
                failed += 1
            print(f"{tool_id} {label}: {verdict} ({url})")

    print(f"\nChecked {checked} URL(s): {checked - failed} ok, "
          f"{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
