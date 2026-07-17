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
        return InstallPlan(
            kind=kind, path=path, can_install=True,
            reason="Will install with apt (and pull in any missing dependencies). "
                   "You'll be asked for your password.",
            command=["apt-get", "install", "-y", str(path)],
            needs_privilege=True,
        )

    if kind == KIND_RPM:
        if family != "dnf":
            return InstallPlan(
                kind=kind, path=path, can_install=False,
                reason=_mismatch_reason(kind, family),
                command=None,
            )
        return InstallPlan(
            kind=kind, path=path, can_install=True,
            reason="Will install with dnf (and pull in any missing dependencies). "
                   "You'll be asked for your password.",
            command=["dnf", "install", "-y", str(path)],
            needs_privilege=True,
        )

    return InstallPlan(kind=kind, path=path, can_install=False, reason="Unsupported file type.", command=None)


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
