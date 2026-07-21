"""
app.py — Obsidian Installer: a small GUI so a customer can install a
downloaded .deb/.rpm/Flatpak file without opening a terminal, see what it's
installed through this tool, remove it again, and get pointed at safe places
to find packages in the first place.

Launch modes:
  obsidian-installer                 -> opens on the Install tab, empty
  obsidian-installer /path/to/x.deb  -> Install tab loads that file directly
                                         (this is the mode file managers use
                                         via the .desktop/MIME association,
                                         see packaging/)

Only this module imports Qt (PySide6) — distro.py, installers.py, and
history.py stay stdlib-only so they can be unit-tested and reused (e.g. by
scriptgen.py) without pulling a GUI toolkit into fleetctl's CLI/TUI
dependency set.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import history
from distro import detect_family
from installers import (
    KIND_LABELS, build_plan, build_uninstall_plan, is_native_package_installed,
    list_installed_flatpaks,
)

WINDOW_TITLE = "Obsidian Installer"

HELP_HTML = """
<h3>What this is</h3>
<p>Obsidian Installer installs <b>.deb</b>, <b>.rpm</b>, and <b>Flatpak</b>
files by double-click, without needing the terminal. It's the tool this
computer's file manager opens when you double-click one of those files.</p>

<h3>Where to get packages safely</h3>
<ul>
<li>Prefer your <b>Software Center</b> or the <b>Search Flathub</b> button
below first — both are curated, so there's nothing to verify yourself.</li>
<li>If an app only offers a direct download, get it from the
<b>app's own official website</b>, not a search result or a third-party
mirror site.</li>
<li>Check the file is actually built for this computer: look for
<b>amd64</b>/<b>x86_64</b> in the filename (not arm64/aarch64, which is for
phones/Raspberry Pi-style hardware) — Obsidian Installer will still tell you
plainly if a file can't install here, but picking the right one from the
start avoids the confusion.</li>
<li><b>Avoid</b> instructions that say to download and run a random
<code>.sh</code> script from an unfamiliar site — unlike a .deb/.rpm/Flatpak,
a shell script isn't sandboxed or verified by anything, and Obsidian
Installer deliberately won't run one.</li>
</ul>

<h3>Why some files won't install</h3>
<p>A <b>.deb</b> file only installs on Debian/Ubuntu/Mint-based systems, and
an <b>.rpm</b> file only on Fedora-based systems — Obsidian Installer won't
try to convert one to the other, since that reliably produces packages that
don't actually work right. If a file doesn't match this computer, check
whether the app has a <b>Flatpak</b> version instead — Flatpak works the
same way on every Linux system.</p>

<h3>Removing something later</h3>
<p>The <b>Installed Apps</b> tab lists every Flatpak app on this computer
(safe to remove regardless of how it got here), plus any .deb/.rpm package
<i>installed through this tool specifically</i> — not a general list of
every system package, since removing the wrong one of those could stop the
computer working properly.</p>
"""


class RunDialog(QDialog):
    """Runs one command (install or uninstall) with a live log, used by both
    the Install tab and the Installed Apps tab so there's exactly one place
    that knows how to drive QProcess + pkexec."""

    def __init__(self, parent, title: str, command: list, needs_privilege: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 320)
        self.setModal(True)
        self.success = False
        self._needs_privilege = needs_privilege

        layout = QVBoxLayout(self)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.close_button = QPushButton("Close")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button)

        if needs_privilege:
            program, args = "pkexec", command
        else:
            program, args = command[0], command[1:]
        self.log.appendPlainText(f"$ {' '.join([program] + args)}\n")

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_finished)
        self.process.start(program, args)

    def _on_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.log.appendPlainText(text.rstrip("\n"))

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self.progress.hide()
        self.close_button.setEnabled(True)
        # pkexec: 126 = auth dialog dismissed/cancelled, 127 = auth failed.
        if self._needs_privilege and exit_code in (126, 127):
            self.log.appendPlainText("\nCancelled — no password was entered.")
        elif exit_code == 0:
            self.success = True
            self.log.appendPlainText("\n✓ Done.")
        else:
            self.log.appendPlainText(f"\nFailed (exit code {exit_code}).")


class InstallTab(QWidget):
    installed = Signal()

    def __init__(self, initial_path: Path | None):
        super().__init__()
        self._family = detect_family()
        self._plan = None

        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)
        layout.addStretch(1)

        button_row = QHBoxLayout()
        self.pick_button = QPushButton("Open File…")
        self.pick_button.clicked.connect(self._pick_file)
        button_row.addWidget(self.pick_button)
        button_row.addStretch(1)
        self.install_button = QPushButton("Install")
        self.install_button.clicked.connect(self._start_install)
        self.install_button.setEnabled(False)
        button_row.addWidget(self.install_button)
        layout.addLayout(button_row)

        if initial_path is not None:
            self._load_file(initial_path)
        else:
            self.summary_label.setText("Choose a .deb, .rpm, or Flatpak file to install.")

    def _pick_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Choose a package to install", str(Path.home()),
            "Installable packages (*.deb *.rpm *.flatpak *.flatpakref);;All files (*)",
        )
        if name:
            self._load_file(Path(name))

    def _load_file(self, path: Path) -> None:
        if not path.is_file():
            QMessageBox.warning(self, WINDOW_TITLE, f"Can't find this file:\n{path}")
            return

        self._plan = build_plan(path, self._family)
        kind_label = KIND_LABELS.get(self._plan.kind, "Unknown file")
        self.summary_label.setText(f"<b>{path.name}</b><br>{kind_label}")
        self.reason_label.setText(self._plan.reason)
        self.install_button.setEnabled(self._plan.can_install)

    def _start_install(self) -> None:
        plan = self._plan
        if plan is None or not plan.can_install:
            return

        dialog = RunDialog(self, f"Installing {plan.path.name}", plan.command, plan.needs_privilege)
        dialog.exec()

        if dialog.success:
            if plan.kind in ("deb", "rpm") and plan.package_name:
                history.record_install(plan.kind, plan.package_name, plan.version or "", str(plan.path))
            self.reason_label.setText(f"✓ {plan.path.name} installed successfully.")
            self.installed.emit()
        else:
            self.reason_label.setText("Installation didn't complete — see the log for details.")


class InstalledAppsTab(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)

        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        refresh_row.addWidget(refresh_button)
        outer.addLayout(refresh_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.addStretch(1)
        scroll.setWidget(self._content)

        self.refresh()

    def refresh(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        flatpak_group = QGroupBox("Flatpak apps")
        flatpak_layout = QVBoxLayout(flatpak_group)
        flatpaks = list_installed_flatpaks()
        if not flatpaks:
            flatpak_layout.addWidget(QLabel("None installed."))
        for app in flatpaks:
            flatpak_layout.addLayout(self._app_row(
                f"{app['name']} ({app['version']})" if app["version"] else app["name"],
                lambda _checked=False, a=app: self._uninstall("flatpak", a["app_id"], a["name"]),
            ))
        self._content_layout.insertWidget(0, flatpak_group)

        native_group = QGroupBox("Installed by Obsidian Installer")
        native_layout = QVBoxLayout(native_group)
        entries = history.load()
        live_entries = []
        for entry in entries:
            if is_native_package_installed(entry.kind, entry.package_name):
                live_entries.append(entry)
            else:
                # No longer actually installed (removed via terminal, etc.)
                # — drop the stale record rather than show a Remove button
                # for something that isn't there.
                history.remove_entry(entry.id)
        if not live_entries:
            native_layout.addWidget(QLabel("None installed."))
        for entry in live_entries:
            label = f"{entry.package_name} ({entry.version})" if entry.version else entry.package_name
            native_layout.addLayout(self._app_row(
                label, lambda _checked=False, e=entry: self._uninstall(e.kind, e.package_name, e.package_name, e.id),
            ))
        self._content_layout.insertWidget(1, native_group)

    def _app_row(self, label: str, on_remove) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label), stretch=1)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(on_remove)
        row.addWidget(remove_button)
        return row

    def _uninstall(self, kind: str, package_name: str, display_name: str, history_id: str | None = None) -> None:
        if QMessageBox.question(
            self, "Remove app", f"Remove {display_name}?",
        ) != QMessageBox.Yes:
            return

        plan = build_uninstall_plan(kind, package_name)
        dialog = RunDialog(self, f"Removing {display_name}", plan.command, plan.needs_privilege)
        dialog.exec()

        if dialog.success and history_id is not None:
            history.remove_entry(history_id)
        self.refresh()


class HelpTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser, stretch=1)

        flathub_button = QPushButton("Search Flathub")
        flathub_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://flathub.org/apps"))
        )
        layout.addWidget(flathub_button)


class MainWindow(QMainWindow):
    def __init__(self, initial_path: Path | None):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(520, 420)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.install_tab = InstallTab(initial_path)
        self.installed_apps_tab = InstalledAppsTab()
        self.install_tab.installed.connect(self.installed_apps_tab.refresh)

        tabs.addTab(self.install_tab, "Install")
        tabs.addTab(self.installed_apps_tab, "Installed Apps")
        tabs.addTab(HelpTab(), "Help")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)

    file_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    initial_path = Path(file_args[0]).resolve() if file_args else None

    window = MainWindow(initial_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
