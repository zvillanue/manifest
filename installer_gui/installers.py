"""
installers.py — classifies a package file and builds the install command for
it. Stdlib-only, no Qt: this module decides *what* to run, app.py (Qt) is the
only place that actually runs it, via QProcess, so the GUI stays responsive
without needing a separate thread.

Design note: .deb only installs on the apt family and .rpm only on the dnf
family — there is no attempt to convert one to the other (alien/debtap-style
conversion is fragile and produces packages the native tool doesn't really
understand). If the file and the machine don't match, install_plan() returns
a plan with can_install=False and a plain-English reason, same spirit as
app_catalog.py's "manual_note" for apps with no native/flatpak package.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from distro import FAMILY_LABELS, flatpak_available

KIND_DEB = "deb"
KIND_RPM = "rpm"
KIND_FLATPAK = "flatpak"  # covers both .flatpak (bundle) and .flatpakref (pointer)

_EXTENSION_KINDS = {
    ".deb": KIND_DEB,
    ".rpm": KIND_RPM,
    ".flatpak": KIND_FLATPAK,
    ".flatpakref": KIND_FLATPAK,
}

_MAGIC_KINDS = {
    b"!<arch>\n": KIND_DEB,       # .deb is an ar archive
    b"\xed\xab\xee\xdb": KIND_RPM,  # rpm lead magic
}

KIND_LABELS = {
    KIND_DEB: "Debian package (.deb)",
    KIND_RPM: "RPM package (.rpm)",
    KIND_FLATPAK: "Flatpak app",
}


def classify_file(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in _EXTENSION_KINDS:
        return _EXTENSION_KINDS[suffix]

    try:
        with path.open("rb") as f:
            head = f.read(4)
    except OSError:
        return None
    for magic, kind in _MAGIC_KINDS.items():
        if head.startswith(magic):
            return kind
    return None


@dataclass
class InstallPlan:
    kind: str
    path: Path
    can_install: bool
    reason: str            # why it can/can't install, shown in the UI either way
    command: list | None    # argv to run, or None if can_install is False
    needs_privilege: bool = False  # True -> app.py runs command via pkexec
    # Real package name/version, read from the file itself — only set for
    # deb/rpm, only when can_install is True. Needed (not just cosmetic)
    # because history.py records installs by package name, not filename, so
    # uninstall_plan() can later ask apt/dnf to remove the right thing even
    # if this exact file has since been moved or deleted.
    package_name: str | None = None
    version: str | None = None


def _deb_info(path: Path) -> tuple[str | None, str | None]:
    try:
        name = subprocess.run(
            ["dpkg-deb", "-f", str(path), "Package"], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        version = subprocess.run(
            ["dpkg-deb", "-f", str(path), "Version"], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return (name or None), (version or None)


def _rpm_info(path: Path) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["rpm", "-qp", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}", str(path)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if "\t" not in result.stdout:
        return None, None
    name, _, version = result.stdout.strip().partition("\t")
    return (name or None), (version or None)


def build_plan(path: Path, family: str | None) -> InstallPlan:
    kind = classify_file(path)
    if kind is None:
        return InstallPlan(
            kind="unknown", path=path, can_install=False,
            reason="This doesn't look like a .deb, .rpm, or Flatpak file — "
                   "nothing here to install.",
            command=None,
        )

    if kind == KIND_FLATPAK:
        if not flatpak_available():
            return InstallPlan(
                kind=kind, path=path, can_install=False,
                reason="Flatpak isn't installed on this system, so this file "
                       "can't be installed. Install Flatpak first (usually "
                       "available from your Software Center or package manager).",
                command=None,
            )
        return InstallPlan(
            kind=kind, path=path, can_install=True,
            reason="Will install with Flatpak, for your user account only "
                   "(no password needed).",
            # --user avoids needing root at all: no pkexec prompt, and it
            # still sees the flathub remote fleetctl's post-install script
            # configures system-wide (flatpak shares remote config between
            # the system and per-user installations).
            command=["flatpak", "install", "-y", "--user", str(path)],
        )

    if kind == KIND_DEB:
        if family != "apt":
            return InstallPlan(
                kind=kind, path=path, can_install=False,
                reason=_mismatch_reason(kind, family),
                command=None,
            )
        name, version = _deb_info(path)
        return InstallPlan(
            kind=kind, path=path, can_install=True,
            reason="Will install with apt (and pull in any missing dependencies). "
                   "You'll be asked for your password.",
            command=["apt-get", "install", "-y", str(path)],
            needs_privilege=True,
            package_name=name, version=version,
        )

    if kind == KIND_RPM:
        if family != "dnf":
            return InstallPlan(
                kind=kind, path=path, can_install=False,
                reason=_mismatch_reason(kind, family),
                command=None,
            )
        name, version = _rpm_info(path)
        return InstallPlan(
            kind=kind, path=path, can_install=True,
            reason="Will install with dnf (and pull in any missing dependencies). "
                   "You'll be asked for your password.",
            command=["dnf", "install", "-y", str(path)],
            needs_privilege=True,
            package_name=name, version=version,
        )

    return InstallPlan(kind=kind, path=path, can_install=False, reason="Unsupported file type.", command=None)


@dataclass
class UninstallPlan:
    command: list
    needs_privilege: bool


def build_uninstall_plan(kind: str, package_name: str) -> UninstallPlan:
    """kind is "deb"/"rpm" (package_name = native package name) or
    "flatpak" (package_name = the Flatpak application id)."""
    if kind == KIND_FLATPAK:
        # Matches build_plan()'s install choice: --user, no pkexec.
        return UninstallPlan(command=["flatpak", "uninstall", "-y", "--user", package_name], needs_privilege=False)
    if kind == KIND_DEB:
        return UninstallPlan(command=["apt-get", "remove", "-y", package_name], needs_privilege=True)
    if kind == KIND_RPM:
        return UninstallPlan(command=["dnf", "remove", "-y", package_name], needs_privilege=True)
    raise ValueError(f"Don't know how to uninstall kind={kind!r}")


def is_native_package_installed(kind: str, package_name: str) -> bool:
    """Cross-checks a history.py entry against live system state, so a
    package removed outside this tool (terminal, Software Center) doesn't
    linger in the Installed Apps list as if it were still there."""
    try:
        if kind == KIND_DEB:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Status}", package_name],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0 and "installed" in result.stdout
        if kind == KIND_RPM:
            result = subprocess.run(["rpm", "-q", package_name], capture_output=True, text=True, timeout=10)
            return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    return False


def list_installed_flatpaks() -> list[dict]:
    """Live query, not our own record — Flatpak already tracks this
    authoritatively regardless of what installed each app (this tool,
    Software Center, or the command line), so there's nothing for
    history.py to add here. Returns [] if flatpak isn't available."""
    if not flatpak_available():
        return []
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=name,application,version"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    apps = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            apps.append({
                "name": parts[0] or parts[1],
                "app_id": parts[1],
                "version": parts[2] if len(parts) > 2 else "",
            })
    return apps


def _mismatch_reason(kind: str, family: str | None) -> str:
    article = "a" if kind == KIND_DEB else "an"
    file_label = "Debian (.deb)" if kind == KIND_DEB else "RPM (.rpm)"
    system_label = FAMILY_LABELS.get(family, "this system") if family else "this system"
    return (
        f"This is {article} {file_label} package, but {system_label} can't install "
        f"that format natively — there's no reliable way to convert it. "
        f"Check if the app has a Flatpak version on Flathub instead, or look "
        f"for a version of this file built for {system_label}."
    )
