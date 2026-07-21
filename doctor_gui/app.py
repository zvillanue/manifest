"""
app.py — Obsidian Doctor: a small GUI that runs common Linux package-manager
repairs (stuck locks, broken/interrupted installs, corrupted local
databases, stale caches) without the buyer ever opening a terminal.

Only this module imports Qt (PySide6) — distro.py and repairs.py stay
stdlib-only so they can be imported and tested without pulling a GUI
toolkit into fleetctl's CLI/TUI dependency set, same split installer_gui
uses for the same reason.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QApplication, QDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from distro import detect_family, flatpak_available, flathub_remote_present
from repairs import PRECHECK_MESSAGES, PRECHECK_NAMES, build_repairs, process_running

WINDOW_TITLE = "Obsidian Doctor"

HELP_HTML = """
<h3>What this does</h3>
<p>Obsidian Doctor runs a small set of well-known fixes for the package
manager (apt, dnf, or pacman) and Flatpak — the layer that installs and
updates software. Each fix only shows up if it's actually relevant to this
computer, and tells you plainly what it's about to do before it runs
anything.</p>

<h3>What this won't do</h3>
<p>Doctor doesn't reinstall the operating system, doesn't touch your
personal files, and doesn't remove or change individual apps — only the
package manager's own internal state (locks, interrupted transactions,
local databases, caches, and repository lists). If a fix needs your
password, that's your desktop's own password prompt, not something Doctor
stores or sends anywhere.</p>

<h3>If a fix doesn't help</h3>
<p>These cover the most common package-manager problems, not everything
that can go wrong with a computer. If something's still broken afterward —
or the problem looks like a hardware issue rather than a software one —
get in touch with whoever you bought this laptop from.</p>
"""


class RunDialog(QDialog):
    """Runs a repair's steps in order (QProcess + pkexec where needed), one
    after another, stopping at the first failure. Reused for every repair
    so there's exactly one place that knows how to drive that sequence —
    same role installer_gui's RunDialog plays for install/uninstall there."""

    def __init__(self, parent, title: str, steps: list, needs_privilege: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 320)
        self.setModal(True)
        self.success = False
        self._steps = steps
        self._step_index = 0
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

        self._run_step()

    def _run_step(self) -> None:
        step = self._steps[self._step_index]
        program, args = ("pkexec", step) if self._needs_privilege else (step[0], step[1:])
        self.log.appendPlainText(f"$ {' '.join([program] + args)}\n")

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_output)
        self.process.finished.connect(self._on_step_finished)
        self.process.start(program, args)

    def _on_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.log.appendPlainText(text.rstrip("\n"))

    def _on_step_finished(self, exit_code: int, _exit_status) -> None:
        # pkexec: 126 = auth dialog dismissed/cancelled, 127 = auth failed.
        if self._needs_privilege and exit_code in (126, 127):
            self._finish(False, "\nCancelled — no password was entered.")
            return
        if exit_code != 0:
            self._finish(False, f"\nFailed at step {self._step_index + 1} of "
                                 f"{len(self._steps)} (exit code {exit_code}).")
            return

        self._step_index += 1
        if self._step_index >= len(self._steps):
            self._finish(True, "\n✓ Done.")
        else:
            self._run_step()

    def _finish(self, success: bool, message: str) -> None:
        self.success = success
        self.progress.hide()
        self.close_button.setEnabled(True)
        self.log.appendPlainText(message)


class RepairsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._family = detect_family()

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll.setWidget(content)

        sections = build_repairs(
            self._family, flatpak_available(), flathub_remote_present(),
        )

        if not sections:
            content_layout.addWidget(QLabel(
                "No supported package manager (apt, dnf, or pacman) or "
                "Flatpak was found on this system — nothing for Doctor to "
                "check here."
            ))

        for section_title, repair_list in sections.items():
            group = QGroupBox(section_title)
            group_layout = QVBoxLayout(group)
            for repair in repair_list:
                group_layout.addLayout(self._repair_row(repair))
            content_layout.addWidget(group)

        content_layout.addStretch(1)

    def _repair_row(self, repair) -> QVBoxLayout:
        row = QVBoxLayout()
        header = QHBoxLayout()
        title_label = QLabel(f"<b>{repair.title}</b>")
        header.addWidget(title_label, stretch=1)
        run_button = QPushButton("Fix it")
        run_button.clicked.connect(lambda _checked=False, r=repair, b=title_label: self._run(r, b))
        header.addWidget(run_button)
        row.addLayout(header)

        description_label = QLabel(repair.description)
        description_label.setWordWrap(True)
        row.addWidget(description_label)
        return row

    def _run(self, repair, status_label: QLabel) -> None:
        if repair.precheck is not None and process_running(PRECHECK_NAMES[repair.precheck]):
            QMessageBox.warning(self, WINDOW_TITLE, PRECHECK_MESSAGES[repair.precheck])
            return

        password_note = " You'll be asked for your password." if repair.needs_privilege else ""
        if QMessageBox.question(
            self, repair.title, f"{repair.description}{password_note}\n\nContinue?",
        ) != QMessageBox.Yes:
            return

        dialog = RunDialog(self, repair.title, repair.steps, repair.needs_privilege)
        dialog.exec()

        if dialog.success:
            status_label.setText(f"<b>{repair.title}</b> — ✓ done")
        else:
            status_label.setText(f"<b>{repair.title}</b> — didn't complete, see the log")


class HelpTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser, stretch=1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(520, 420)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(RepairsTab(), "Repairs")
        tabs.addTab(HelpTab(), "Help")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
