# DarkFly Next Generation: Design

Status: Phases 1 and 2 implemented (on the `nextgen/phase-2` branch);
agreed direction for the post-v5 rewrite of the modern DarkFly CLI.
Audience: DarkFly-Tool maintainers and contributors.
Companion research: `research_notes/darkfly-nextgen-research-20261023-1633`
(deep-research brief on the modern tool-installer landscape). This document
turns that research into concrete decisions for this project.

## 1. Vision

Classic DarkFly (v4) and its clones (Tool-X, AllHackingTools, Lazymux) share
one design: a numbered menu over a hard-coded list of hundreds of tools,
installed by `git clone` plus `apt`/`pkg` plus Python 2 scripts, with no
version pinning, no verification, no uninstall, and no record of what was
installed. They fail in predictable ways: Python 2 end of life, dead upstream
repositories, renamed projects (CrackMapExec was archived in 2023 and its
successor is NetExec), dependency conflicts that break the host, and users
who cannot tell what is on their system.

The next generation of DarkFly keeps the one thing users loved (pick a tool,
get it installed, on Termux or Linux) and replaces everything else:

- **A small, curated catalog, not 530 entries.** Target size is roughly
  60 to 100 well-maintained tools plus a resources section, organised by
  workflow phase (recon, scanning, web, passwords, Active Directory,
  post-exploitation, wireless, resources). Every entry carries maintenance
  metadata so staleness is visible data, not a silent failure.
- **Real installs through real package managers.** DarkFly delegates to
  `pkg`/`apt`, `pipx`, and `go install` wherever possible instead of running
  bespoke clone-and-build scripts. Python tools are always isolated with
  pipx, never installed into the global environment.
- **Pin, record, reproduce.** Versions and commits are pinned in the
  catalog. Every successful install is written to a manifest
  (`installed.json`) so `installed`, `uninstall`, and later `update` and
  `verify` commands have ground truth to work from.
- **Dry-run first, resumable always.** Every install can be previewed with
  `--dry-run` (commands, sizes, files) before anything executes. Installs
  are journaled step by step so an interrupted install (Android kills
  Termux processes with signal 9, batteries die, networks drop) can resume
  instead of starting over.
- **Safety as a feature.** No `curl | bash`, no foreign APT repositories,
  no root on Termux, no obfuscated catalog, and no bomber, spam, or
  DDoS-for-fun tools. Section 8 lists the rules in full.

Non-goals for this design: a full-screen TUI (deferred, see Section 10), a
Go rewrite (the Python codebase and contributor pool stay), Docker or Nix
as an install mechanism on Termux (not viable on unrooted Android; we copy
their pinning and manifest principles only), an autonomous AI agent that
runs tools (out of scope; at most a later opt-in experiment), and an
"install everything" mode.

## 2. Catalog schema v2

The catalog is a single plain-JSON file, `catalog.json`, at the repository
root. Plain JSON in the repo is a deliberate choice: it is diff-able in
pull requests and auditable by anyone, which is the opposite of the v4
design where the list was hard-coded and obfuscated inside Python 2 code
and `.module` artifacts.

Top level:

```json
{
  "schema_version": 2,
  "tools": [ ... ]
}
```

Each tool entry:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable unique identifier, lowercase, also used on the CLI. |
| `name` | string | Display name. |
| `aliases` | list of string | Alternate names users may type (example: `nxc` for NetExec). |
| `description` | string | One or two sentences, including a "use when" hint where useful. |
| `categories` | list of string | Workflow categories. The category `offensive` triggers the lawful-use notice at install time. |
| `homepage` | string | Canonical project homepage (HTTPS). |
| `source` | string | Canonical source repository URL (HTTPS). |
| `license` | string | SPDX-style license identifier of the tool. |
| `status` | string | One of `maintained`, `stale`, `archived`. Archived tools should normally be removed or point at a successor. |
| `tier` | int | 0 core, 1 modern layer, 2 resources (data, not code), 3 emerging or experimental. |
| `platforms` | object | `termux` (bool), `linux` (bool), `gui_required` (bool). GUI tools are listed for information only and are never fake-installed on Termux. |
| `size_mb_estimate` | number or null | Rough installed size in MB, used for pre-flight warnings. |
| `last_verified` | string | Date (`YYYY-MM-DD`) a maintainer last confirmed the entry installs as described. |
| `install.backend` | string | One of `apt`, `pipx`, `go`, `git`, `manual`, `binary`. See Section 3. |
| `install.package` | string | Package name (apt, pipx), Go module path (go), repository URL (git), or an artifact reference (manual, binary). |
| `install.binary` | object or absent | Binary backend only, required when `backend` is `binary`: `url` (HTTPS, may contain `{arch}` and `{version}` placeholders), `sha256` (map of architecture name to the expected 64-hex digest of the download), `archive` (one of `none`, `tar.gz`, `zip`), and `bin_name` (the file installed into `<darkfly home>/bin/`). See Section 3. |
| `install.termux_package` | string or null | Package name under Termux `pkg` when it differs from `package`; null means "same as `package`". |
| `install.version_pin` | string or null | Version or commit to pin, where the backend supports one. |
| `install.deps_pkg` | list of string | Extra Termux packages to install first (via `pkg`). |
| `install.deps_apt` | list of string | Extra Debian/Ubuntu packages to install first (via `apt-get`). |
| `run` | string | Command the user runs after install. May be empty for tier 2 resources such as wordlists. |
| `successor_to` | string or null | Id or historical name of the tool this one replaces (example: NetExec has `successor_to` set to CrackMapExec). May reference a tool that is deliberately not in the catalog. |

Example (abbreviated):

```json
{
  "id": "netexec",
  "name": "NetExec",
  "aliases": ["nxc"],
  "description": "Network service exploitation and Active Directory assessment tool, the maintained successor to CrackMapExec.",
  "categories": ["ad", "post-exploitation", "offensive"],
  "homepage": "https://www.netexec.wiki",
  "source": "https://github.com/Pennyw0rth/NetExec",
  "license": "BSD-2-Clause",
  "status": "maintained",
  "tier": 1,
  "platforms": {"termux": true, "linux": true, "gui_required": false},
  "size_mb_estimate": 120,
  "last_verified": "2026-10-23",
  "install": {
    "backend": "pipx",
    "package": "netexec",
    "termux_package": null,
    "version_pin": null,
    "deps_pkg": [],
    "deps_apt": []
  },
  "run": "nxc",
  "successor_to": "crackmapexec"
}
```

Validation: `darkfly.catalog.validate_catalog` rejects a catalog with
missing or mistyped fields, duplicate ids or aliases, unknown backends,
unknown status values, tiers outside 0 to 3, and malformed `last_verified`
dates. Binary entries get extra checks: a missing `install.binary` block,
a non-HTTPS URL, a missing or malformed checksum, an unknown archive
type, or a `bin_name` that is a path all fail validation. A catalog that
fails validation is never used for installs.

Lookup order for `catalog.json`: current working directory first, then the
repository root next to the installed package, then inside the package
directory. Since Phase 2 the catalog also ships inside the package as
package data (`darkfly/catalog.json`, declared in `pyproject.toml`), so
pip installs outside a clone work; a test keeps the packaged copy
byte-identical to the root file so the two can never drift.

## 3. Backends and priority

One backend per entry, chosen by the maintainer using this priority order
(most trusted first):

1. **apt / pkg**: a signed package from the distro or Termux repositories.
   APT and `pkg` verify signatures themselves; DarkFly never reimplements
   downloads and never adds foreign repositories (the Katoolin rule: mixing
   a rolling Kali repo into a stable host leads to unresolvable dependency
   conflicts and a broken system).
2. **Vendor binary release with checksum** (`binary` backend,
   implemented in Phase 2, minimal form): for Go and Rust tools on
   Termux arm64 a prebuilt upstream binary avoids 10 to 30 minute
   source builds and most build failures. The entry's
   `install.binary` block carries a URL template (`{arch}` and
   `{version}` placeholders) and a SHA-256 digest per architecture.
   Planning resolves the URL for the detected architecture and fails
   when the catalog has no checksum for it (an architecture without a
   verified digest is not installable, full stop). Installing
   downloads to a temp directory under the darkfly home, verifies the
   digest *before* placing anything, reads the binary out of the
   archive in memory when there is one (nothing is extracted to disk,
   so archive path traversal cannot happen), and moves it into
   `<darkfly home>/bin/<bin_name>` with mode 0755 via a temp file plus
   rename. Uninstall deletes that file; update is not automated for
   this backend (a new version means a new checksum, which is a
   catalog change by design). Example entry shape, with placeholder
   digests that are illustrative only and would fail verification:

   ```json
   "install": {
     "backend": "binary",
     "package": "example-tool",
     "termux_package": null,
     "version_pin": "v1.0.0",
     "deps_pkg": [],
     "deps_apt": [],
     "binary": {
       "url": "https://example.com/releases/{version}/tool-{arch}.tar.gz",
       "sha256": {"aarch64": "<64 hex chars>", "x86_64": "<64 hex chars>"},
       "archive": "tar.gz",
       "bin_name": "tool"
     }
   }
   ```

   No catalog entry ships on this backend yet. The rule that decides
   it: a shipped checksum must be a value a maintainer actually
   verified against the vendor's published release. A wrong checksum
   is worse than no binary entry at all (every install fails, or
   worse, users learn to ignore checksum errors). Ligolo-ng is the
   intended first user; it stays `manual`, pointing at the upstream
   release page, until a maintainer verifies a release digest. The
   backend is covered end to end by tests with a fake tool, including
   the checksum-mismatch abort.
3. **pipx / go install at a pin**: Python tools go through pipx so each
   tool lives in its own virtualenv and can be upgraded or removed
   cleanly. Go tools use `go install module@pin`. This tier is also where
   delegation to single-vendor managers belongs later (for example handing
   ProjectDiscovery tools to `pdtm`, which already does cached catalog,
   binary install, update, and remove well).
4. **git clone at a pinned commit, with a warning**: last resort. The plan
   always carries a visible warning that a git install is not
   package-manager verified. Clones land in a dedicated directory
   (`<darkfly home>/tools/<id>`) so uninstall is a directory removal, never
   a scatter of files. Unpinned clones get an additional warning.
5. **manual**: no automated steps. The entry exists so `search` and the
   catalog can describe the tool honestly; `install` prints the plan,
   explains that installation is manual, points at the homepage, and
   installs nothing.

The backend used is recorded in the manifest, so update and uninstall
later reverse the right thing.

## 4. Install plans and dry-run

Installing is a two-step process internally: build a plan, then execute it.

A plan (`darkfly.planner.InstallPlan`) is data before it is execution:

- `steps`: an ordered list of steps. Each step is a human-readable
  description plus an argv list. Execution always uses argv lists with
  `shell=False`; shell strings are never built for execution. Rendered
  plan text is for display only.
- `warnings`: things the user should know before confirming, for example
  the git-backend warning, the unpinned warning, a heavy-install warning
  when `size_mb_estimate` is over 500 MB, a stale or archived status
  warning, and the lawful-use notice for `offensive` category tools.
- `notes`: non-executable information, such as manual install pointers.
- `size_mb_estimate`, predicted `files_created`, the resolved package
  name, backend, version pin, and run command.

Platform gating happens at plan time, not halfway through an install: if
the tool is not available for the detected environment, or it requires a
GUI, plan building fails with a clear error and no steps.

Backend step shapes:

- apt on Termux: `pkg install -y <termux_package or package>`.
- apt on Linux: `sudo apt-get install -y <package>`. Dependencies from
  `deps_pkg` or `deps_apt` become their own steps first, so the journal
  can resume at fine granularity. Termux never uses sudo.
- pipx: dependency steps, then `pipx install <package>` (with an
  `==<version_pin>` suffix when a pin is set and the package is a plain
  PyPI name).
- go: dependency steps (typically the Go toolchain), then
  `go install <module>@<version_pin or latest>`, with a warning when
  unpinned.
- git: `git clone --depth 1 <url> <dest>` when unpinned; a full clone plus
  `git -C <dest> checkout <version_pin>` when pinned.
- binary: no argv steps at all. The plan instead carries the resolved
  download URL, the expected SHA-256 for the detected architecture, and
  the destination path, and the installer performs the download, the
  verification, and the atomic placement itself (see Section 3).

`--dry-run` prints the rendered plan (tool, backend, environment, size
estimate, warnings, numbered steps, notes) and executes nothing.

## 5. Manifest: installed.json

Location: `<darkfly home>/installed.json`, where the darkfly home is
`$DARKFLY_HOME` when set, else `~/.local/share/darkfly`. The same base
directory holds `journal.json` and the `tools/` directory for git clones,
which keeps tests and sandboxed runs hermetic behind one env var.

Format:

```json
{
  "schema_version": 1,
  "tools": {
    "netexec": {
      "id": "netexec",
      "name": "NetExec",
      "backend": "pipx",
      "package": "netexec",
      "version_pin": null,
      "installed_at": "2026-10-23T17:05:00+00:00",
      "files_or_dirs": [],
      "run": "nxc"
    }
  }
}
```

Fields per entry: `id`, `name`, `backend`, `package` (the resolved package
actually installed on this platform), `version_pin`, `installed_at`
(ISO 8601 UTC timestamp), `files_or_dirs` (paths DarkFly created, mainly
git clone destinations and binary-backend installs), and `run` (the
command the user runs). An entry that has been through `DarkFly5 update`
also carries `updated_at`; the original `installed_at` is preserved.

Writes go through a temp file plus rename so a kill mid-write cannot
corrupt the manifest. The manifest is the single source of truth for the
`installed` and `uninstall` commands; if a tool was installed outside
DarkFly, DarkFly does not claim it.

## 6. Journal and resume

Android 12 and later aggressively kill Termux child processes (the
Phantom Process Killer, visible to users as `[Process completed
(signal 9)]`), and long installs are exactly the workload it kills. The
design consequence: installs must be resumable, and parallelism stays at
zero (steps run one at a time).

Mechanics (`darkfly.installer`):

1. Before executing, the plan is serialized to
   `<darkfly home>/journal.json` together with an empty `completed` list.
2. After each step succeeds, its index is added to `completed` and the
   journal is rewritten.
3. On success the journal is deleted and the manifest entry is written.
4. On failure the journal stays, the error reports which step failed, and
   the user is told to re-run the same install command to resume.
5. On a later `install` for the same tool, a matching journal is picked
   up and completed steps are skipped. A journal for a different tool is
   never silently overwritten; the install refuses and names the journal,
   so two half-finished states cannot corrupt each other.

This gives the signal 9 case a boring ending: unlock the phone, re-run
`DarkFly5 install <tool>`, and the install continues where it stopped.

## 7. CLI surface

The CLI stays argparse-based with subcommands, one code path for
interactive and scripted use. Legacy v5 commands keep working unchanged
against the legacy `darkfly_tools.json`:

| Command | Source | Behaviour |
|---|---|---|
| `DarkFly5 list` | legacy catalog | List tools from `darkfly_tools.json` (unchanged). |
| `DarkFly5 info <id>` | legacy catalog | Show details and suggested commands (unchanged). |
| `DarkFly5 menu` | legacy catalog | Numbered menu (unchanged; will be re-based on the new catalog in a later phase). |
| `DarkFly5 search <query>` | catalog v2 | Case-insensitive search over id, name, aliases, description, categories, and successor links. Ranked: exact id or alias first. |
| `DarkFly5 install <id> [--dry-run] [--yes]` | catalog v2 | Build the plan, print it, confirm interactively unless `--yes`, execute with journaling, record in the manifest. Resumes an interrupted install of the same tool. |
| `DarkFly5 uninstall <id> [--dry-run] [--yes]` | manifest | Build the reverse plan from the manifest entry, confirm, execute, remove files DarkFly created, drop the manifest entry. |
| `DarkFly5 installed` | manifest | List what DarkFly has installed, with backend, pin, and date. |
| `DarkFly5 update [id] [--dry-run] [--yes]` | manifest + catalog | Update one installed tool, or all of them. The plan is built from the manifest entry's recorded backend, with pins and module paths taken from the current catalog when it has newer ones. |
| `DarkFly5 verify [id]` | manifest + catalog | Check installed tools: manifest entry present, tool still in the catalog, run command findable (on PATH, or inside the clone for git installs), recorded paths still exist. Exits non-zero when any check fails. Does not execute the tools. |
| `DarkFly5 doctor` | environment | Print detected environment, Python version, platform, which of `pkg`, `apt-get`, `pipx`, `go`, `git` are on PATH, free disk space, the darkfly home path, and the catalog validation result. |

Uninstall step shapes by backend: apt becomes `pkg uninstall -y` (Termux)
or `sudo apt-get remove -y` (Linux); pipx becomes `pipx uninstall <name>`;
go removes the built binary from `~/go/bin/<run>`; git removes the clone
directory recorded in the manifest; binary deletes the installed file
recorded in the manifest; manual has nothing to undo.

Update step shapes by backend: apt becomes `pkg install -y <package>` on
Termux (installing an installed package upgrades it, while `pkg upgrade`
would upgrade the whole device) and `sudo apt-get install -y
--only-upgrade <package>` on Linux; pipx becomes `pipx upgrade <name>`;
go re-runs `go install <module>@<catalog pin>`; git fetches and checks
out the catalog pin, or pulls with `--ff-only` when unpinned; manual and
binary report that they cannot be updated automatically.

Environment detection (`darkfly.planner.detect_env`) returns `termux`
when `TERMUX_VERSION` is set or the home path contains `com.termux`,
`linux` on other Linux systems, and `other` elsewhere. It accepts an
optional environ mapping so tests can simulate Termux deterministically.

## 8. Safety rules

These are ship-blockers, not aspirations:

- No `curl | bash`, ever. No remote code fetched at startup or menu time,
  no remote "configuration checks" before the UI opens (an anti-pattern
  seen in AllHackingTools), and no obfuscated catalog.
- HTTPS only for homepage, source, and clone URLs.
- Pin versions or commits wherever the backend supports it; verify sha256
  or signatures where upstream provides them (mandatory for the binary
  backend: no verified checksum, no entry).
- Never run as root on Termux and never call sudo there. On Linux, sudo is
  confined to the apt steps the user just approved in the printed plan.
- Never add foreign APT repositories, never edit files outside the tool's
  own install location, never modify the user's shell profile silently.
- Python tools are pipx-isolated; global `pip install` is never used
  (modern Termux and Debian Python refuse it anyway with
  "externally-managed environment" errors).
- Exclude bomber, spam, and DDoS-for-fun tools outright. Tools in the
  `offensive` category show a lawful-use notice in the plan before the
  user confirms: use only on systems you own or are authorised to test.
- Every catalog entry carries license, canonical upstream, status, and
  `last_verified`; contribution rules require a working install test on
  Termux and Linux for new entries.
- The README carries an educational and authorised-use disclaimer, and
  the project keeps a `SECURITY.md` contact for abuse reports. This also
  matches GitHub's acceptable-use framing: dual-use security tooling is
  permitted, content in direct support of unlawful attacks is not.

## 9. Termux realities the design respects

- Termux comes from F-Droid or GitHub, not the abandoned Play Store build;
  `doctor` output is the place to surface environment problems.
- Everything lives under `$PREFIX` and `$HOME`; there is no FHS, no
  systemd, and no root. Installs work fully inside the home directory and
  never require Android storage permission.
- Desktop glibc binaries generally do not run on Termux (Bionic libc), so
  an entry is only marked `platforms.termux: true` when a Termux route
  actually exists (a `pkg` package, a pipx install, a Go build, or a
  clone). "Works on Kali" is not evidence.
- Python on Termux is Python 3 only, which is one more reason the legacy
  Python 2 installer line is finished.
- Storage is finite and shared with the phone: size estimates and the
  heavy-install warning exist because multi-GB "install everything" runs
  are a leading failure mode.

## 10. Phasing

**Phase 1 (this work).** Catalog schema v2 with validation and search;
the existing 8 tools migrated plus an initial modern set (ProjectDiscovery
Go tools, ffuf, NetExec, theHarvester, SecLists, and honest `manual`
entries for Feroxbuster, RustScan, and searchsploit), about 20 entries
total. Planner with dry-run rendering, manifest, journaled installer with
resume, and the `search`, `install`, `uninstall`, `installed`, and
`doctor` commands. Legacy `list`, `info`, and `menu` untouched. Tests for
catalog, planner, and installer. Stdlib only.

**Phase 2 (implemented on the `nextgen/phase-2` branch).** The catalog
grew from 20 to 62 entries: the Tier 0 remainder (TShark, OpenBSD
netcat, Hashcat, Impacket, Responder, and a deliberately `manual`,
heavy-flagged Metasploit entry), the Tier 1 modern layer (the remaining
ProjectDiscovery tools, Amass, the AD set of Kerbrute, BloodHound.py,
Certipy, and evil-winrm-py, plus Ligolo-ng as an honest `manual` entry),
Tier 2 resources (PayloadsAllTheThings, HackTricks, GTFOBins, and
rockyou as a never-bundled `manual` reference), a small Tier 3
(Caido, Scout Suite, Pacu), and GUI companions (ZAP, Burp Suite,
Wireshark) listed as desktop-only `manual` entries that are never
fake-installed. The binary backend landed in its minimal form (Section
3) with validation, per-architecture checksums, and atomic install;
no entry ships on it yet, because no release checksum has been
maintainer-verified. `update` and `verify` commands were built on the
manifest (Sections 5 and 7). Test CI exists now: a GitHub Actions
workflow runs pytest on Python 3.10 and 3.12, validates the catalog,
and smoke-tests `--dry-run` and `doctor` on every push and pull
request. `catalog.json` is packaged as package data with a
byte-identical sync test, so PyPI installs work outside a clone.

Deferred from the original Phase 2 list to Phase 3: delegation to
`pdtm` for ProjectDiscovery tools, re-basing `list`, `info`, and
`menu` on catalog v2 and retiring `darkfly_tools.json`, upstream
liveness and archived-status checks in CI (CI currently validates
schema and dry-runs plans, it does not call home to upstreams), and
further catalog growth toward 100 with wordlist size bundles.

**Phase 3.** The deferred items above. An optional richer front-end (a
fuzzy picker, possibly a Textual UI) strictly on top of the same
subcommands. A published, honest benchmark of install time, failure
rate, and uninstall cleanliness for the top catalog entries on a named
Termux device and a named Linux VM. Deeper `verify` that optionally
executes a tool's version probe. An experimental, opt-in,
dry-run-gated AI assistant layer only if demand appears.

## 11. Open questions

Carried over from the research, deliberately unresolved:

- The published scanner and fuzzer speed numbers circulating on blogs are
  low-authority and partly contradicted by more careful testing. The
  catalog must never quote benchmark numbers as fact; if DarkFly wants
  numbers, it has to measure its own (Phase 3 benchmark).
- Delegate ProjectDiscovery tools to `pdtm`, or keep direct `go install`
  plans? Phases 1 and 2 use `go install`; delegation is deferred to
  Phase 3 and depends on how often `pdtm` is present on target devices.
- First shipped binary-backend entry: Ligolo-ng is the candidate, but
  it waits on a maintainer verifying a release checksum against the
  vendor's published value (see Section 3). Until then it stays
  `manual` on purpose.
- Catalog governance: who verifies entries, how often `last_verified` is
  refreshed, and what the review bar is for new tools. Curation is the
  product; it needs named maintainers, not drive-by additions.
- Pin freshness for git entries: a pinned commit is reproducible but ages.
  SecLists in Phase 1 is pinned to a tag as a placeholder; every pin needs
  a real verification pass before release, and a policy for bumping pins.
- Wheel packaging: resolved in Phase 2. The catalog ships as package
  data inside the wheel (`darkfly/catalog.json`), with a test keeping
  it byte-identical to the root `catalog.json`.
- Whether the v5 README's historical claims about tests and CI match the
  repo: verified during Phase 1 (three CLI tests exist; the only
  workflow was the PyPI publish one). Test CI landed in Phase 2 (see
  Section 10); upstream liveness checks in CI are still open.
- Community sources (Reddit, Null Byte) could not be verified directly in
  the research and are treated as second-hand consensus only; nothing in
  this design depends on them.
