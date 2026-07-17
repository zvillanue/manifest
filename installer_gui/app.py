"""
app.py — Obsidian Installer: a small GUI so a customer can install a
downloaded .deb/.rpm/Flatpak file without opening a terminal.

Launch modes:
  obsidian-installer                 -> shows an "Open File..." picker
  obsidian-installer /path/to/x.deb  -> loads that file directly (this is
                                         the mode file managers use via the
                                         .desktop/MIME association, see
                                         packaging/)

Only this module imports Qt (PySide6) — distro.py and installers.py stay
stdlib-only so they can be unit-tested and reused (e.g. by scriptgen.py)
without pulling a GUI toolkit into fleetctl's CLI/TUI dependency set.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from distro import detect_family
from installers import KIND_LABELS, build_plan

WINDOW_TITLE = "Obsidian Installer"


class InstallerWindow(QMainWindow):
    def __init__(self, initial_path: Path | None):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(480, 320)

        self._family = detect_family()
        self._plan = None
        self._process: QProcess | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.hide()
        layout.addWidget(self.log, stretch=1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.hide()
        layout.addWidget(self.progress)

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
        self.log.clear()
        self.log.hide()

    def _start_install(self) -> None:
        if self._plan is None or not self._plan.can_install:
            return

        self.install_button.setEnabled(False)
        self.pick_button.setEnabled(False)
        self.log.show()
        self.progress.show()

        command = self._plan.command
        if self._plan.needs_privilege:
            program, args = "pkexec", command
        else:
            program, args = command[0], command[1:]

        self.log.appendPlainText(f"$ {' '.join([program] + args)}\n")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._on_output(process))
        process.finished.connect(self._on_finished)
        process.start(program, args)
        self._process = process

    def _on_output(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardOutput()).decode(errors="replace")
        self.log.appendPlainText(text.rstrip("\n"))

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self.progress.hide()
        self.pick_button.setEnabled(True)

        # pkexec: 126 = auth dialog dismissed/cancelled, 127 = auth failed.
        if self._plan.needs_privilege and exit_code in (126, 127):
            self.reason_label.setText("Cancelled — no password was entered, so nothing was installed.")
        elif exit_code == 0:
            self.reason_label.setText(f"✓ {self._plan.path.name} installed successfully.")
        else:
            self.reason_label.setText(
                f"Installation failed (exit code {exit_code}). See the log below for details."
            )

        self.install_button.setEnabled(self._plan.can_install)
        self._process = None


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)

    file_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    initial_path = Path(file_args[0]).resolve() if file_args else None

    window = InstallerWindow(initial_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
