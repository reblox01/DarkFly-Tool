# DarkFly-Tool

The current main version is **DarkFly v5** – a modern Python 3 CLI that runs on current Linux/Termux systems. The previous **DarkFly v4.0** (Python 2, Termux-focused) installer is still included below as a legacy option.

In its original (v4) form, DarkFly-Tool is an installation tool for installing tools. this tool makes it easy for you. so you don't need to type git clone or look for the github repository. You only have to choose the number. which tool you want to install. there are 530 tools ready for intall. and for those of you who like to have fun. there are 7 SMS spam tools that are ready to use, you just need to choose spam to use the target number. there is a tocopedia DLL.

## Version comparison

| Feature                                      | DarkFly v5 (current) | DarkFly v4.0 (legacy) |
| -------------------------------------------- | -------------------- | ---------------------- |
| Python 3 support                             | ✓                    | ✗                      |
| Python 2 support                             | ✗                    | ✓                      |
| Target: Linux                                | ✓                    | ✗ (not reliable)       |
| Target: Termux                               | ✓                    | ✓                      |
| Actively maintained                          | ✓                    | ✗ (frozen legacy)      |
| Configurable tools via `darkfly_tools.json`  | ✓                    | ✗                      |
| Obfuscated/installer-based tool management   | ✗                    | ✓                      |

### Detailed comparison

| Aspect              | DarkFly v5 (current)                                           | DarkFly v4.0 (legacy)                                                |
| ------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------- |
| Implementation      | Python 3 CLI in `darkfly/`                                     | Python 2 installer `install.py` plus `.module` and `lib` scripts     |
| Target environments | Linux and Termux with Python 3.10+ and pip                     | Primarily Termux (Android); regular Linux may not work reliably      |
| Status              | Active / modern path                                           | Frozen legacy path, kept for historical compatibility                |
| Install / usage     | `python -m pip install -e .[dev]` then `DarkFly5 ...`          | `python2 install.py` then `DarkFly`                                  |
| Tool list           | Config-driven via `darkfly_tools.json` (easy to extend/modify) | Hard-coded/obfuscated inside legacy Python 2 and `.module` artifacts |

---

## Architecture: the next-generation engine

The next-generation install engine (Phases 1 and 2) lives in the v5 package, alongside the classic v5 commands. The full design document is [docs/DESIGN.md](docs/DESIGN.md). This section is the short version, including the schemas.

### How it fits together

```
                        +----------------------+
   user command ------> |  darkfly/cli.py      |
                        |  search, install,    |
                        |  uninstall, update,  |
                        |  verify, doctor      |
                        +----------+-----------+
                                   |
              +--------------------+--------------------+
              |                    |                    |
   +----------v---------+  +-------v--------+  +--------v---------+
   | darkfly/catalog.py |  | darkfly/       |  | darkfly/         |
   | loads + validates  |  | planner.py     |  | manifest.py      |
   | catalog.json       |  | builds an      |  | installed.json:  |
   | (schema v2)        |  | install plan   |  | what DarkFly     |
   +----------+---------+  | for THIS       |  | installed, where,|
              |            | environment    |  | with what backend|
              |            +-------+--------+  +--------^---------+
              |                    |                    |
              |            +-------v--------------------+---------+
              +----------> | darkfly/installer.py               |
                           | executes the plan step by step,    |
                           | journals progress, resumes after   |
                           | an interruption, records results   |
                           | in the manifest                    |
                           +------------------+-----------------+
                                              |
                            +-----------------v------------------+
                            | the system itself: pkg (Termux),   |
                            | apt-get (Linux), pipx, go, git     |
                            +------------------------------------+
```

Design rules that shape everything above:

- The catalog is plain JSON in the repo: diff-able in pull requests, auditable by anyone. No obfuscated tool lists, no code fetched at startup.
- Installing is two steps: build a **plan** (data), then execute it. `--dry-run` prints the plan and executes nothing.
- Backends are chosen per tool by trust: signed `apt`/`pkg` package first, then a vendor `binary` release whose SHA-256 is verified before install, then `pipx` or `go install` at a pinned version, then a pinned `git` clone (with a visible warning), then `manual` (documented, never fake-installed).
- Everything DarkFly installs is recorded in a manifest, so `uninstall` removes exactly what was added, and nothing else.
- Installs are journaled: if Android kills Termux mid-install (the signal 9 problem), the next run resumes where it stopped instead of starting over.

### Schema 1: the catalog (`catalog.json`, schema v2)

Top level: `{ "schema_version": 2, "tools": [ ... ] }`. The catalog currently ships **62 curated tools**: 14 core (tier 0), 40 in the modern layer (tier 1), 5 resources (tier 2), and 3 emerging (tier 3). Each tool entry:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Stable unique identifier, also used on the CLI. |
| `name` | string | Display name. |
| `aliases` | list of string | Alternate names users may type (`nxc` for NetExec). |
| `description` | string | What the tool does, with a "use when" hint where useful. |
| `categories` | list of string | Workflow categories. `offensive` triggers a lawful-use notice at install time. |
| `homepage`, `source` | string | Canonical homepage and source repository (HTTPS). |
| `license` | string | SPDX-style license identifier. |
| `status` | string | `maintained`, `stale`, or `archived`. Archived tools point at a successor instead of silently rotting. |
| `tier` | int | 0 core, 1 modern layer, 2 resources (data, not code), 3 emerging. |
| `platforms` | object | `termux` (bool), `linux` (bool), `gui_required` (bool). |
| `size_mb_estimate` | number or null | Rough installed size, used for pre-flight warnings. |
| `last_verified` | string | Date a maintainer last confirmed the entry installs as described. |
| `install.backend` | string | One of `apt`, `pipx`, `go`, `git`, `manual`, `binary`. |
| `install.package` | string | Package name, Go module path, or repo URL, depending on backend. |
| `install.binary` | object | Binary backend only: download `url` (with `{arch}` and `{version}` placeholders), per-architecture `sha256` checksums, `archive` (`none`, `tar.gz`, `zip`), and the installed `bin_name`. The checksum is verified before anything is installed, and an architecture with no checksum cannot be installed. |
| `install.termux_package` | string or null | Termux `pkg` name when it differs from `package`. |
| `install.version_pin` | string or null | Version or commit to pin, where the backend supports one. |
| `install.deps_pkg`, `install.deps_apt` | list of string | Extra packages to install first, per platform. |
| `run` | string | Command the user runs after install. |
| `successor_to` | string or null | The tool this one replaces, if any. |

A real entry, NetExec:

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

The catalog is validated before use: missing or mistyped fields, duplicate ids or aliases, unknown backends or statuses, and bad dates are all rejected with a clear error. An invalid catalog is never used for installs.

### Schema 2: the manifest (`installed.json`)

Stored in the DarkFly home (`$DARKFLY_HOME` if set, else `~/.local/share/darkfly/`), next to the install journal and the `tools/` directory used by the git backend. One entry per installed tool:

```json
{
  "schema_version": 1,
  "tools": {
    "netexec": {
      "version_pin": null,
      "backend": "pipx",
      "installed_at": "2026-10-23T17:00:00+00:00",
      "files_or_dirs": [],
      "run": "nxc"
    }
  }
}
```

The manifest is the single source of truth for `installed`, `uninstall`, `update`, and `verify`: if a tool is not in the manifest, DarkFly did not install it and will not claim otherwise. After an update, the entry also gains an `updated_at` timestamp.

### Next-gen commands

```bash
DarkFly5 search nuclei            # search id, name, aliases, description, categories
DarkFly5 install nmap --dry-run   # print the plan for THIS environment, change nothing
DarkFly5 install netexec          # shows the plan, asks, then installs (journaled, resumable)
DarkFly5 installed                # list what the manifest says is installed
DarkFly5 uninstall seclists       # reverse an install using the manifest
DarkFly5 update                   # update everything DarkFly installed (or: update nmap)
DarkFly5 verify                   # check installed tools: manifest, run command, files
DarkFly5 doctor                   # environment, tools on PATH, catalog validation
```

### Testing and simulation

The engine is tested without touching a real system: no test installs a real tool, and no test uses the network. The suite (210 tests) runs with:

```bash
python -m pip install -e .[dev]
pytest
```

How the simulation works:

- **Simulated environments.** Environment detection is injectable, so tests plan installs for a simulated Termux and a simulated Linux side by side. That is how we know a Termux plan says `pkg install` and a Linux plan says `apt-get install`, without needing a phone.
- **Every catalog entry is planned, on both environments.** Each of the 62 entries must produce either a valid plan with well-formed steps, or a clean platform error (for example a Linux-only tool refusing to plan on Termux). A catalog edit that breaks a plan breaks the build.
- **Fake runner, fake downloader.** Installs execute through a runner interface and downloads through a single downloader function. Tests substitute fakes, so the journal, the resume-after-interruption logic, manifest writes, uninstall reversal, and the binary backend's SHA-256 check (including the mismatch case, which must install nothing) are all exercised end to end with zero side effects.
- **Try a simulation yourself.** `--dry-run` is the same plan the tests assert on, shown to you: `DarkFly5 install metasploit-framework --dry-run` prints the size estimate, the heavy-install and lawful-use warnings, and every step it would take, then changes nothing. `DarkFly5 doctor` validates the catalog and reports your environment the same way the test suite sees it.

CI runs the suite on Python 3.10 and 3.12 for every push and pull request.

---

## DarkFly v5 – modern Python 3 CLI (Linux / Termux)

DarkFly v5 is a new, configurable CLI that does **not** depend on the old Python 2 installer. It is implemented as a normal Python package in the `darkfly/` directory.

### Install (editable) for development

From a Python 3.10+ environment on Linux (or Termux with Python 3):

```bash
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

This installs the `DarkFly5` command and development dependencies (like pytest).

### Usage

List available tools (from `darkfly_tools.json` or the built-in examples):

```bash
DarkFly5 list
```

Show details and suggested install commands for a specific tool:

```bash
DarkFly5 info nmap
```

Open a simple interactive menu:

```bash
DarkFly5 menu
```

You can add or change tools and categories by editing `darkfly_tools.json` in the repository root. The JSON structure is simple and designed to be extended over time.

### Running tests (modern CLI)

After installing with the `dev` extras:

```bash
pytest
```

This runs the tests under `tests/` for the modern Python 3 CLI.

---

## DarkFly v4 – legacy Python 2 installer (Termux)

> **Warning:** DarkFly v4 depends on Python 2 and is kept for historical compatibility. Modern systems may not ship Python 2 by default.
>
> If you specifically want this legacy version, download it from the v4 release:
> <https://github.com/reblox01/DarkFly-Tool/releases/tag/darkfly-v4.0.0>

### Information

DarkFly now is: <a href="https://github.com/Ranginang67/DarkFly-2019.1.git">DarkFly-2019.1</a> (generation of DarkFly-tool) Try it.

DarkFly-Tool is an installation tool for installing tools. this tool makes it easy for you. so you don't need to type git clone or look for the github repository. You only have to choose the number. which tool you want to install. there are 530 tools ready for intall. and for those of you who like to have fun. there are 7 SMS spam tools that are ready to use, you just need to choose spam to use the target number. there is a tocopedia DLL.

### Install (Termux)

**Termux:**

* `pkg install python2`
* `pkg install git`
* `git clone https://github.com/reblox01/DarkFly-Tool`
* `cd DarkFly-Tool`
* `python2 install.py`

```bash
1.if installed is complite, use command
$cd
2.then run it
$DarkFly
```

**NOTE:**

```bash
if python2 install.py is not allowed or fail, Use this
$chmod +x install.py
$python2 install.py
```

### How to update v4

For update this tool, just do the reinstallation, the first way is to install the Darkfly tools, by reinstalling, the old file will be deleted and replaced with the new one installed.

### Note

The original DarkFly v4 installer was mainly tested on Termux. On regular Linux distributions it may not work correctly.
