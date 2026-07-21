"""
history.py — local record of .deb/.rpm packages installed through Obsidian
Installer, so the "Installed Apps" tab knows what it's allowed to offer for
removal.

Deliberately scoped to deb/rpm only, not Flatpak: Flatpak already has its
own authoritative install record (`flatpak list`), so duplicating that here
would just be a second source of truth that can drift from reality. This
file only needs to answer "did *this tool* put a native package on the
system," which nothing else tracks.

Deliberately scoped to packages installed *through this tool*, not a general
apt/dnf package browser — removing an arbitrary system package can break the
OS, which this tool should never expose to a non-technical user. See
installers.py's uninstall_plan() for the other half of that boundary.

Stdlib-only (json, no Qt) so it stays importable/testable without the GUI.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _history_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    path = Path(data_home) / "obsidian-installer"
    path.mkdir(parents=True, exist_ok=True)
    return path / "history.json"


@dataclass
class HistoryEntry:
    id: str
    kind: str          # "deb" or "rpm"
    package_name: str  # real package name (e.g. "signal-desktop"), not the filename
    version: str        # best-effort, "" if it couldn't be determined
    source_path: str    # the file it was installed from, for reference only
    installed_at: str   # ISO 8601 UTC


def load() -> list[HistoryEntry]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [HistoryEntry(**entry) for entry in raw]


def _save(entries: list[HistoryEntry]) -> None:
    _history_path().write_text(json.dumps([asdict(e) for e in entries], indent=2))


def record_install(kind: str, package_name: str, version: str, source_path: str) -> None:
    entries = load()
    entries.append(HistoryEntry(
        id=str(uuid.uuid4()),
        kind=kind,
        package_name=package_name,
        version=version,
        source_path=source_path,
        installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))
    _save(entries)


def remove_entry(entry_id: str) -> None:
    entries = [e for e in load() if e.id != entry_id]
    _save(entries)
