"""Install planning: turn a catalog entry into an executable plan.

A plan is data before it is execution (see ``docs/DESIGN.md``). The
planner decides, for a given environment, exactly which argv steps an
install or uninstall would run, collects the warnings a user should see
before confirming, and renders the whole thing as text for
``--dry-run``. Nothing in this module executes anything.

Steps are always argv lists. They are executed elsewhere with
``shell=False``; shell strings are never built for execution.
"""

from __future__ import annotations

import os
import platform
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from darkfly.catalog import ToolEntry
from darkfly.manifest import darkfly_home


# Installs estimated above this size get an explicit heavy warning:
# on a phone, multi-GB installs are a leading failure mode.
HEAVY_INSTALL_MB = 500

LAWFUL_USE_NOTICE = (
    "Lawful use: this tool is in the 'offensive' category. Only use it "
    "on systems you own or are explicitly authorised to test."
)

GIT_BACKEND_WARNING = (
    "Git backend: this tool is installed from a source clone, not a "
    "signed package. Review the upstream repository before running it."
)

GIT_UNPINNED_WARNING = (
    "This git install is not pinned to a version or commit, so what you "
    "get can change between installs."
)

GO_UNPINNED_WARNING = (
    "This Go install is not pinned to a version (uses @latest), so "
    "what you get can change between installs."
)

BINARY_BACKEND_WARNING = (
    "Binary backend: the file is downloaded from the vendor release "
    "URL recorded in the catalog, and its SHA-256 checksum is "
    "verified before anything is installed."
)

# platform.machine() spellings mapped to the short architecture
# names catalog entries use in their checksum tables.
_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv7l": "arm",
    "armv6l": "arm",
    "arm": "arm",
}


def detect_arch(machine: Optional[str] = None) -> str:
    """Return the short architecture name for checksum lookups.

    ``machine`` defaults to ``platform.machine()``; tests pass an
    explicit value to simulate other hardware.
    """

    raw = machine if machine is not None else platform.machine()
    return _ARCH_ALIASES.get(raw.lower(), raw.lower())


class PlanError(Exception):
    """Raised when no plan can be built for a tool in this environment."""


def detect_env(environ: Optional[Mapping[str, str]] = None) -> str:
    """Detect a coarse runtime environment.

    Returns one of:
    - "termux" - running under Termux on Android.
    - "linux" - Linux (non-Termux).
    - "other" - anything else.

    ``environ`` defaults to ``os.environ``; tests pass an explicit
    mapping to simulate Termux deterministically.
    """

    env = os.environ if environ is None else environ

    # Termux commonly exposes this variable
    if env.get("TERMUX_VERSION"):
        return "termux"

    home = env.get("HOME", str(Path.home()))
    if "com.termux" in home:
        return "termux"

    if sys.platform.startswith("linux"):
        return "linux"

    return "other"


@dataclass
class PlanStep:
    description: str
    argv: List[str]


@dataclass
class BinaryPlan:
    """A resolved binary download: where from, and how to trust it.

    Binary installs are not argv steps; the installer performs the
    download, checksum verification, and atomic placement itself,
    driven by this data (see ``darkfly.installer``).
    """

    url: str
    sha256: str
    arch: str
    archive: str
    bin_name: str
    dest: str


@dataclass
class InstallPlan:
    tool_id: str
    name: str
    backend: str
    env: str
    steps: List[PlanStep] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    size_mb_estimate: Optional[float] = None
    files_created: List[str] = field(default_factory=list)
    package: str = ""
    version_pin: Optional[str] = None
    run: str = ""
    action: str = "install"  # or "uninstall"
    binary: Optional[BinaryPlan] = None

    def render(self) -> str:
        """Render the plan as text for --dry-run and confirmations."""

        lines: List[str] = []
        lines.append(f"Plan to {self.action} {self.name} [{self.tool_id}]")
        lines.append(f"  Backend:     {self.backend}")
        lines.append(f"  Environment: {self.env}")
        if self.size_mb_estimate is not None:
            lines.append(
                f"  Size estimate: {self.size_mb_estimate:g} MB"
            )
        if self.version_pin:
            lines.append(f"  Version pin: {self.version_pin}")
        if self.binary is not None:
            lines.append(f"  Download:    {self.binary.url}")
            lines.append(
                f"  SHA-256:     {self.binary.sha256} "
                f"({self.binary.arch}, verified before install)")
        if self.files_created:
            if self.action == "uninstall":
                lines.append("  Files/dirs to be removed:")
            else:
                lines.append("  Files/dirs to be created:")
            for path in self.files_created:
                lines.append(f"    {path}")
        if self.warnings:
            lines.append("  Warnings:")
            for warning in self.warnings:
                lines.append(f"    ! {warning}")
        if self.steps:
            lines.append("  Steps:")
            for idx, step in enumerate(self.steps, start=1):
                lines.append(f"    {idx}. {step.description}")
                lines.append(f"       $ {shlex.join(step.argv)}")
        elif self.binary is not None:
            lines.append(
                "  Steps: none (DarkFly downloads, verifies, and "
                "installs the binary itself; see Download above)")
        else:
            lines.append("  Steps: none (nothing to execute)")
        for note in self.notes:
            lines.append(f"  Note: {note}")
        return "\n".join(lines)


def _check_platform(tool: ToolEntry, env: str) -> None:
    """Refuse, at plan time, tools that cannot be installed here."""

    if env == "other":
        raise PlanError(
            f"{tool.name} cannot be installed here: unsupported "
            "environment (DarkFly targets Termux and Linux)."
        )
    if env == "termux" and not tool.platforms.termux:
        raise PlanError(
            f"{tool.name} is not available on Termux. See {tool.homepage} "
            "for desktop installation options."
        )
    if env == "linux" and not tool.platforms.linux:
        raise PlanError(
            f"{tool.name} is not available on Linux. See {tool.homepage} "
            "for other platforms."
        )
    if tool.platforms.gui_required:
        raise PlanError(
            f"{tool.name} needs a graphical desktop, so DarkFly will "
            "not install it on this device. It is listed for information "
            f"only; see {tool.homepage}."
        )


def _dependency_steps(tool: ToolEntry, env: str) -> List[PlanStep]:
    """Steps that install a tool's extra dependencies first."""

    steps: List[PlanStep] = []
    if env == "termux":
        for dep in tool.install.deps_pkg:
            steps.append(PlanStep(
                description=f"Install dependency {dep} (Termux pkg)",
                argv=["pkg", "install", "-y", dep],
            ))
    elif env == "linux":
        for dep in tool.install.deps_apt:
            steps.append(PlanStep(
                description=f"Install dependency {dep} (apt)",
                argv=["sudo", "apt-get", "install", "-y", dep],
            ))
    return steps


def build_install_plan(tool: ToolEntry, env: str,
                       base_dir: Optional[Path] = None,
                       arch: Optional[str] = None) -> InstallPlan:
    """Build the install plan for a catalog tool in an environment.

    Raises PlanError when the tool cannot be installed here. A manual
    backend is not an error: the plan simply has no steps and carries
    notes explaining the manual route. ``arch`` only matters for the
    binary backend; it defaults to the detected architecture and
    exists so tests can simulate other hardware.
    """

    _check_platform(tool, env)

    base = base_dir if base_dir is not None else darkfly_home()
    spec = tool.install

    plan = InstallPlan(
        tool_id=tool.id,
        name=tool.name,
        backend=spec.backend,
        env=env,
        size_mb_estimate=tool.size_mb_estimate,
        version_pin=spec.version_pin,
        run=tool.run,
        action="install",
    )

    # Warnings that apply regardless of backend.
    if tool.size_mb_estimate is not None \
            and tool.size_mb_estimate > HEAVY_INSTALL_MB:
        plan.warnings.append(
            f"Heavy install: about {tool.size_mb_estimate:g} MB. On "
            "Termux, make sure you have the free space and expect the "
            "install to take a while; if it is interrupted, re-run the "
            "same command to resume."
        )
    if tool.status == "archived":
        successor = f" Its successor is {tool.successor_to}." \
            if tool.successor_to else ""
        plan.warnings.append(
            f"This tool is archived upstream and no longer "
            f"maintained.{successor}"
        )
    elif tool.status == "stale":
        plan.warnings.append(
            "This tool is marked stale: upstream activity has slowed "
            "and the entry may be out of date."
        )
    if "offensive" in tool.categories:
        plan.warnings.append(LAWFUL_USE_NOTICE)

    if spec.backend == "apt":
        package = spec.termux_package or spec.package \
            if env == "termux" else spec.package
        plan.package = package
        plan.steps.extend(_dependency_steps(tool, env))
        if env == "termux":
            plan.steps.append(PlanStep(
                description=f"Install {package} (Termux pkg)",
                argv=["pkg", "install", "-y", package],
            ))
        else:
            plan.steps.append(PlanStep(
                description=f"Install {package} (apt)",
                argv=["sudo", "apt-get", "install", "-y", package],
            ))

    elif spec.backend == "pipx":
        plan.package = spec.package
        plan.steps.extend(_dependency_steps(tool, env))
        package_spec = spec.package
        if spec.version_pin and "://" not in spec.package \
                and not spec.package.startswith("git+"):
            package_spec = f"{spec.package}=={spec.version_pin}"
        plan.steps.append(PlanStep(
            description=f"Install {spec.package} in an isolated "
                        "pipx environment",
            argv=["pipx", "install", package_spec],
        ))

    elif spec.backend == "go":
        plan.package = spec.package
        plan.steps.extend(_dependency_steps(tool, env))
        pin = spec.version_pin or "latest"
        if spec.version_pin is None:
            plan.warnings.append(GO_UNPINNED_WARNING)
        plan.steps.append(PlanStep(
            description=f"Build and install {spec.package} with Go",
            argv=["go", "install", f"{spec.package}@{pin}"],
        ))
        plan.notes.append(
            "The binary lands in ~/go/bin; make sure that directory is "
            "on your PATH."
        )

    elif spec.backend == "git":
        plan.package = spec.package
        plan.warnings.append(GIT_BACKEND_WARNING)
        dest = base / "tools" / tool.id
        plan.files_created.append(str(dest))
        if spec.version_pin:
            plan.steps.append(PlanStep(
                description=f"Clone {spec.package} into {dest}",
                argv=["git", "clone", spec.package, str(dest)],
            ))
            plan.steps.append(PlanStep(
                description=f"Check out pinned ref {spec.version_pin}",
                argv=["git", "-C", str(dest), "checkout",
                      spec.version_pin],
            ))
        else:
            plan.warnings.append(GIT_UNPINNED_WARNING)
            plan.steps.append(PlanStep(
                description=f"Shallow-clone {spec.package} into {dest}",
                argv=["git", "clone", "--depth", "1", spec.package,
                      str(dest)],
            ))

    elif spec.backend == "binary":
        if spec.binary is None:  # pragma: no cover - validation gates
            raise PlanError(
                f"{tool.name} uses the binary backend but has no "
                "install.binary block in the catalog."
            )
        resolved_arch = arch if arch is not None else detect_arch()
        digest = spec.binary.sha256.get(resolved_arch)
        if digest is None:
            available = ", ".join(sorted(spec.binary.sha256)) \
                or "none"
            raise PlanError(
                f"{tool.name} has no binary build for architecture "
                f"'{resolved_arch}' (catalog checksums cover: "
                f"{available}). It cannot be installed on this "
                "device with the binary backend."
            )
        version = spec.version_pin or ""
        if "{version}" in spec.binary.url and not version:
            raise PlanError(
                f"{tool.name} has a download URL that needs a "
                "version, but the catalog entry has no version_pin."
            )
        url = spec.binary.url.replace("{arch}", resolved_arch) \
            .replace("{version}", version)
        dest = base / "bin" / spec.binary.bin_name
        plan.package = spec.binary.bin_name
        plan.binary = BinaryPlan(
            url=url,
            sha256=digest.lower(),
            arch=resolved_arch,
            archive=spec.binary.archive,
            bin_name=spec.binary.bin_name,
            dest=str(dest),
        )
        plan.files_created.append(str(dest))
        plan.warnings.append(BINARY_BACKEND_WARNING)

    elif spec.backend == "manual":
        plan.package = spec.package
        plan.notes.append(
            "Manual installation required: DarkFly does not automate "
            f"this tool yet. Get it from {tool.homepage} and follow the "
            "upstream instructions."
        )

    else:  # pragma: no cover - validation prevents unknown backends
        raise PlanError(f"Unknown backend: {spec.backend!r}")

    if tool.run:
        plan.notes.append(f"After install, run it with: {tool.run}")
    return plan


def _pipx_name_from_package(package: str) -> str:
    """Derive the pipx environment name from a stored package string."""

    return package.split("==")[0].rstrip("/").split("/")[-1]


def build_update_plan(entry: Dict[str, Any], env: str,
                      tool: Optional[ToolEntry] = None) -> InstallPlan:
    """Build the update plan for a manifest entry.

    The backend comes from the manifest (it records what was actually
    installed). Version pins and module paths prefer the *current*
    catalog entry when one is supplied, because updating is exactly
    how a newer catalog pin reaches an existing install. Raises
    PlanError on an unsupported environment. Manual and binary
    backends cannot be updated automatically: the plan has no steps
    and a note saying so.
    """

    if env == "other":
        raise PlanError(
            "Update is only supported on Termux and Linux."
        )

    tool_id = entry["id"]
    backend = entry["backend"]
    package = entry.get("package", "")
    run = entry.get("run", "")
    pin = entry.get("version_pin")
    if tool is not None and tool.install.version_pin is not None:
        pin = tool.install.version_pin

    plan = InstallPlan(
        tool_id=tool_id,
        name=entry.get("name", tool_id),
        backend=backend,
        env=env,
        package=package,
        version_pin=pin,
        run=run,
        action="update",
    )

    if tool is not None:
        if tool.status == "archived":
            successor = f" Its successor is {tool.successor_to}." \
                if tool.successor_to else ""
            plan.warnings.append(
                f"This tool is archived upstream and no longer "
                f"maintained.{successor}"
            )
        elif tool.status == "stale":
            plan.warnings.append(
                "This tool is marked stale: upstream activity has "
                "slowed and the entry may be out of date."
            )

    if backend == "apt":
        if env == "termux":
            # A plain pkg install of an installed package upgrades it
            # to the newest Termux build; pkg upgrade would upgrade
            # every package on the device, which is not our call.
            plan.steps.append(PlanStep(
                description=f"Upgrade {package} to the latest "
                            "Termux package",
                argv=["pkg", "install", "-y", package],
            ))
        else:
            plan.steps.append(PlanStep(
                description=f"Upgrade {package} (apt, only-upgrade)",
                argv=["sudo", "apt-get", "install", "-y",
                      "--only-upgrade", package],
            ))

    elif backend == "pipx":
        pipx_name = _pipx_name_from_package(package)
        plan.steps.append(PlanStep(
            description=f"Upgrade the pipx environment {pipx_name}",
            argv=["pipx", "upgrade", pipx_name],
        ))

    elif backend == "go":
        module = tool.install.package if tool is not None else package
        plan.package = module
        effective_pin = pin or "latest"
        if pin is None:
            plan.warnings.append(GO_UNPINNED_WARNING)
        plan.steps.append(PlanStep(
            description=f"Rebuild and install {module} with Go",
            argv=["go", "install", f"{module}@{effective_pin}"],
        ))
        plan.notes.append(
            "The binary lands in ~/go/bin; make sure that directory "
            "is on your PATH."
        )

    elif backend == "git":
        dirs = entry.get("files_or_dirs", [])
        if not dirs:
            raise PlanError(
                f"Cannot update {tool_id}: the manifest records no "
                "clone directory for it."
            )
        dest = dirs[0]
        if pin:
            plan.steps.append(PlanStep(
                description=f"Fetch updates for the clone in {dest}",
                argv=["git", "-C", dest, "fetch", "origin"],
            ))
            plan.steps.append(PlanStep(
                description=f"Check out the catalog pin {pin}",
                argv=["git", "-C", dest, "checkout", pin],
            ))
            plan.notes.append(
                "A pinned git install only moves when the catalog "
                "pin changes; this update applies the current pin."
            )
        else:
            plan.steps.append(PlanStep(
                description=f"Pull the latest changes into {dest}",
                argv=["git", "-C", dest, "pull", "--ff-only"],
            ))

    elif backend in ("manual", "binary"):
        where = f" See {tool.homepage}." if tool is not None else ""
        plan.notes.append(
            f"DarkFly cannot update {plan.name} automatically "
            f"(backend: {backend}). Reinstall it manually to get a "
            f"newer version.{where}"
        )

    else:  # pragma: no cover - manifest entries come from our plans
        raise PlanError(f"Unknown backend in manifest: {backend!r}")

    return plan


def build_uninstall_plan(entry: Dict[str, Any], env: str) -> InstallPlan:
    """Build the reverse plan for a manifest entry.

    The steps undo what the install plan did, per backend. Removing the
    files DarkFly created (git clone directories) is handled by the
    installer, guided by ``files_created`` here.
    """

    if env == "other":
        raise PlanError(
            "Uninstall is only supported on Termux and Linux."
        )

    tool_id = entry["id"]
    backend = entry["backend"]
    package = entry.get("package", "")
    run = entry.get("run", "")

    plan = InstallPlan(
        tool_id=tool_id,
        name=entry.get("name", tool_id),
        backend=backend,
        env=env,
        package=package,
        version_pin=entry.get("version_pin"),
        run=run,
        action="uninstall",
        # For an uninstall plan, files_created lists the paths the
        # install created and the uninstall will remove.
        files_created=list(entry.get("files_or_dirs", [])),
    )
    if plan.files_created:
        plan.notes.append(
            "The directories above were created by DarkFly and will "
            "be removed."
        )

    if backend == "apt":
        if env == "termux":
            plan.steps.append(PlanStep(
                description=f"Remove {package} (Termux pkg)",
                argv=["pkg", "uninstall", "-y", package],
            ))
        else:
            plan.steps.append(PlanStep(
                description=f"Remove {package} (apt)",
                argv=["sudo", "apt-get", "remove", "-y", package],
            ))
    elif backend == "pipx":
        pipx_name = _pipx_name_from_package(package)
        plan.steps.append(PlanStep(
            description=f"Remove the pipx environment for {pipx_name}",
            argv=["pipx", "uninstall", pipx_name],
        ))
    elif backend == "go":
        if run:
            binary = Path.home() / "go" / "bin" / run
            plan.steps.append(PlanStep(
                description=f"Remove the Go-built binary {binary}",
                argv=["rm", "-f", str(binary)],
            ))
        else:
            plan.notes.append(
                "No run command recorded, so there is no binary to "
                "remove."
            )
    elif backend == "git":
        plan.notes.append(
            "The clone directory recorded in the manifest will be "
            "deleted; there are no package steps to reverse."
        )
    elif backend == "binary":
        plan.notes.append(
            "The binary file recorded in the manifest will be "
            "deleted; there are no package steps to reverse."
        )
    elif backend == "manual":
        plan.notes.append(
            "This tool was installed manually, so DarkFly has nothing "
            "to undo beyond removing its manifest entry."
        )

    return plan
