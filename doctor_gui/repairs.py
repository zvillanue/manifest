"""
repairs.py — the list of one-click repair actions Obsidian Doctor can run,
per package-manager family, plus Flatpak (which applies regardless of
family). Stdlib-only, no Qt — app.py is the only place that actually runs a
repair (via QProcess), same split as installer_gui's installers.py.

Every command here is a fixed argv template with nothing user-supplied
interpolated into it — Doctor doesn't take file paths or free-text input the
way Obsidian Installer does — so there's never a reason for shell=True or
building a command by string concatenation here.

Scope is deliberately limited to package-manager-level repairs (locks,
broken/interrupted transactions, corrupted local databases, stale caches,
missing repo metadata). Nothing here touches partitions, bootloaders, or
personal files — see doctor_gui/README section in the main README for the
reasoning and the rest of the roadmap (disk/boot, network, upgrade issues).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Repair:
    id: str
    title: str
    description: str        # plain-English, shown in the UI before running
    steps: list              # list[list[str]] — argv per step, run in order
    needs_privilege: bool = True
    precheck: str | None = None   # key into PRECHECK_NAMES/PRECHECK_MESSAGES


PRECHECK_NAMES = {
    "no-apt-running": ("apt", "apt-get", "dpkg", "aptitude", "synaptic"),
    "no-pacman-running": ("pacman", "pamac"),
}

PRECHECK_MESSAGES = {
    "no-apt-running": (
        "A package manager (apt, dpkg, or a Software Center) looks like it's "
        "still running. Close it and wait for it to finish before clearing "
        "the lock — removing it while something is actually installing can "
        "corrupt the package database."
    ),
    "no-pacman-running": (
        "pacman (or a Software Center using it) looks like it's still "
        "running. Close it and wait for it to finish before clearing the "
        "lock — removing it mid-install can corrupt the package database."
    ),
}


def process_running(names: tuple) -> bool:
    """Best-effort /proc scan — stdlib only, no psutil dependency for one
    check used by exactly two repairs."""
    try:
        pid_dirs = os.listdir("/proc")
    except OSError:
        return False
    for pid_dir in pid_dirs:
        if not pid_dir.isdigit():
            continue
        try:
            with open(f"/proc/{pid_dir}/comm") as f:
                comm = f.read().strip()
        except OSError:
            continue
        if comm in names:
            return True
    return False


def _apt_repairs() -> list[Repair]:
    return [
        Repair(
            id="apt-unlock",
            title="Release stuck package-manager lock",
            description=(
                "If an update or install got interrupted (for example, the "
                "computer lost power partway through), apt can get stuck "
                "saying it's “locked,” even though nothing is "
                "actually running. This checks that's really the case, then "
                "clears the lock."
            ),
            steps=[["rm", "-f",
                    "/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock",
                    "/var/cache/apt/archives/lock"]],
            precheck="no-apt-running",
        ),
        Repair(
            id="apt-fix-broken",
            title="Fix broken or interrupted installs",
            description=(
                "Finishes any package install that got interrupted, and "
                "resolves missing dependencies. Try this first if apt or "
                "the Software Center is refusing to install or remove "
                "anything."
            ),
            steps=[["dpkg", "--configure", "-a"],
                   ["apt-get", "install", "-f", "-y"]],
        ),
        Repair(
            id="apt-refresh-repos",
            title="Refresh software sources",
            description=(
                "Re-downloads the list of available software and updates. "
                "Fixes “could not get lock” or “failed to "
                "fetch” errors that persist after the two fixes above, "
                "and stale or broken repository information."
            ),
            steps=[["apt-get", "update"]],
        ),
        Repair(
            id="apt-clean-cache",
            title="Free up disk space used by downloaded packages",
            description=(
                "Deletes old downloaded package files apt keeps around "
                "after installing them. Safe — doesn't touch anything "
                "currently installed."
            ),
            steps=[["apt-get", "clean"]],
        ),
    ]


def _dnf_repairs() -> list[Repair]:
    return [
        Repair(
            id="dnf-rebuild-database",
            title="Rebuild the package database",
            description=(
                "Fixes a corrupted local package database — the usual cause "
                "of errors like “rpmdb: damaged header” or dnf "
                "refusing to run at all."
            ),
            steps=[["rpm", "--rebuilddb"]],
        ),
        Repair(
            id="dnf-refresh-repos",
            title="Refresh software sources",
            description=(
                "Re-downloads the latest list of available software and "
                "updates. Fixes stale or broken repository metadata."
            ),
            steps=[["dnf", "makecache", "--refresh"]],
        ),
        Repair(
            id="dnf-clean-cache",
            title="Free up disk space used by downloaded packages",
            description=(
                "Deletes dnf's cached package data and old downloads. Safe "
                "— doesn't touch anything currently installed."
            ),
            steps=[["dnf", "clean", "all"]],
        ),
    ]


def _pacman_repairs() -> list[Repair]:
    return [
        Repair(
            id="pacman-unlock",
            title="Release stuck package-manager lock",
            description=(
                "If an update or install got interrupted (for example, the "
                "computer lost power partway through), pacman can get stuck "
                "saying the database is “locked,” even though "
                "nothing is actually running. This checks that's really the "
                "case, then clears the lock."
            ),
            steps=[["rm", "-f", "/var/lib/pacman/db.lck"]],
            precheck="no-pacman-running",
        ),
        Repair(
            id="pacman-refresh-keyring",
            title="Reset the package signature trust database",
            description=(
                "Fixes “signature is unknown trust” or “key "
                "could not be looked up” errors, common after a fresh "
                "install or a long gap between updates."
            ),
            steps=[["pacman-key", "--init"], ["pacman-key", "--populate", "archlinux"]],
        ),
        Repair(
            id="pacman-sync-databases",
            title="Force-refresh software sources",
            description=(
                "Re-downloads the package databases from your mirrors. "
                "Fixes “database file ... is missing” or "
                "out-of-sync mirror errors."
            ),
            steps=[["pacman", "-Syy", "--noconfirm"]],
        ),
        Repair(
            id="pacman-clean-cache",
            title="Free up disk space used by downloaded packages",
            description=(
                "Deletes old cached package files pacman keeps around after "
                "installing them. Only touches cached files, not anything "
                "currently installed."
            ),
            steps=[["pacman", "-Sc", "--noconfirm"]],
        ),
    ]


def _flatpak_repairs(flathub_present: bool) -> list[Repair]:
    repairs = [
        Repair(
            id="flatpak-repair",
            title="Repair corrupted Flatpak app data",
            description=(
                "Fixes corrupted or incomplete Flatpak app data. Try this "
                "if a Flatpak app won't launch or won't update."
            ),
            steps=[["flatpak", "repair"]],
        ),
        Repair(
            id="flatpak-cleanup",
            title="Remove unused Flatpak runtimes",
            description=(
                "Removes old runtime versions and leftover data that "
                "Flatpak apps no longer need. Safe — doesn't remove any app "
                "you're currently using."
            ),
            steps=[["flatpak", "uninstall", "--unused", "-y"]],
        ),
    ]
    if not flathub_present:
        repairs.append(Repair(
            id="flatpak-add-flathub",
            title="Restore the Flathub app store",
            description=(
                "Adds back the Flathub app store, so the Software Center "
                "and Flatpak can find and install apps again. Flathub "
                "should already be configured on this laptop — this is "
                "here in case it was removed."
            ),
            steps=[["flatpak", "remote-add", "--if-not-exists", "flathub",
                    "https://flathub.org/repo/flathub.flatpakrepo"]],
        ))
    return repairs


def build_repairs(family: str | None, flatpak_present: bool, flathub_present: bool) -> dict:
    """Returns {"section title": [Repair, ...]} for whatever's actually
    relevant to this machine — an Arch user never sees apt repairs, and a
    system with no Flatpak never sees the Flatpak section at all."""
    sections = {}

    if family == "apt":
        sections["Debian/Ubuntu/Mint (apt)"] = _apt_repairs()
    elif family == "dnf":
        sections["Fedora (dnf)"] = _dnf_repairs()
    elif family == "pacman":
        sections["Arch (pacman)"] = _pacman_repairs()

    if flatpak_present:
        sections["Flatpak"] = _flatpak_repairs(flathub_present)

    return sections
