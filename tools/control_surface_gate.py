"""Fail the build when a control on screen does not do anything.

This replaces `ui_smoke_probe.py`, which walked the seven main tabs and printed
findings but always returned 0, so nothing it noticed could stop a release. It
also answers the defect that probe was written for: five controls in the
Writing HUD were created and never shown, and a feature that exists in code and
not on screen is invisible to the test suite.

Reachability alone is not enough. A button that is present, named and enabled
still passes while doing nothing at all, and that is the failure this gate is
built to catch. So every control is checked three ways:

1. It is reachable - visible, and enabled or explicably disabled.
2. It is named, because the accessibility acceptance depends on it and because
   an unnamed control cannot be driven by any harness including this one.
3. It is WIRED. A push button with no receiver on clicked() cannot do anything
   when a user presses it, whatever it looks like. Checkable controls are
   exempt and the reason is recorded at `_is_wired`.

What it does NOT do, said plainly so nobody reads more assurance into a green
run than it earned: it does not press the buttons. Clicking every control in a
real window opens modal dialogs that never return, and a gate that hangs is
worse than one that checks less. So this proves a control can do something, not
that what it does is right - that is what the corpus runs and
`writer_session_probe.py` are for. Controls it will not touch at all are named
in the skip list with a reason and printed on every run, because a silent skip
reads as coverage.

    .venv/Scripts/python.exe tools/control_surface_gate.py
    .venv/Scripts/python.exe tools/control_surface_gate.py --json build/acceptance/controls.json

It runs offscreen and needs no running app, so it belongs in the fast tier of
`release_acceptance.py`. The live half - tabs actually switching under a real
click, the HUD following focus between documents - cannot be proved offscreen
and stays with `writer_session_probe.py`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# The environment this needs when it is run as a script. Applied in main(),
# NOT at import.
#
# Setting these at module scope cost a green suite: pytest imports this module
# for its own tests, the four variables landed in the shared process, and every
# test that built a window afterwards got one with no tray.
# test_system_tray_keeps_hotkey_service_ready_when_window_closes then failed on
# `assert window.tray_enabled` about two thousand tests later, which reads as a
# product regression and is not one. conftest.py deliberately does not set the
# tray variable, and the other test modules that need it build an environment
# dict for a subprocess rather than mutating this one.
SCRIPT_ENVIRONMENT = {
    "QT_QPA_PLATFORM": "offscreen",
    "ECHOLINK_DISABLE_HOTKEY": "1",
    "ECHOLINK_DISABLE_TRAY": "1",
    "ECHOLINK_DISABLE_SOUNDS": "1",
    "ECHOLINK_DISABLE_MEDIA_CONTROL": "1",
}

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTabBar,
    QTableWidget,
    QTextEdit,
    QWidget,
)

from echo_link_app.window import EchoLinkWindow  # noqa: E402
from echo_link_core.storage import EchoLinkStore  # noqa: E402

# Controls that must not be pressed by a harness, and why. Printed with every
# run. A skip nobody can see is the thing this gate exists to stop.
SKIPS = {
    "Subscribe": "starts a real payment flow",
    "Manage subscription": "opens the seller's site in a browser",
    "Delete": "destroys the user's own writing",
    "Delete document": "destroys the user's own writing",
    "Purge": "destroys the user's own writing",
    "Clear history": "destroys the user's own writing",
    "Uninstall": "removes the installation",
    "Restore": "overwrites current work with a recovery snapshot",
}

# Buttons legitimately disabled until their precondition is met. A disabled
# control is only a finding when nothing explains it, and each reason here was
# checked against the setEnabled call that turns the control back on rather
# than assumed from the label. Adding a name here is a contract: it says this
# control is meant to be dead at rest, and it has to become live when the named
# thing happens.
DISABLED_UNTIL = {
    "Clear Manuscript private paste": "nothing is staged, so there is nothing to clear",
    "Undo last insert": "nothing has been inserted (window.py enables it at 3695)",
    "Replace selection": "no text has been captured (window.py enables it at 2396)",
    "Stage private paste": "no result to stage yet",
    "Save draft to Documents": "no draft has been generated yet",
    "Stage draft for private paste": "no draft has been generated yet",
    "Save revision": "the document is unedited, so there is no revision to save",
    "Export": "no document is selected",
    "Remove selected AI pack": (
        "no removable user pack is selected; settings_dialog.py enables it at "
        "806 only for a user pack that is not the active model"
    ),
    "Run local speed check": (
        "no local model is ready; writing_models_dialog.py enables it at 173 "
        "when the manager reports one"
    ),
    "Replace text in source box": "no suggestion has been generated yet",
    "Dismiss suggested text": "no suggestion has been generated yet",
    "Undo the correction just applied": "nothing has been applied yet",
    "Rewrite current text locally": "no text has been captured yet",
}

INTERACTIVE = (QAbstractButton, QComboBox, QLineEdit, QListWidget)
TEXT_ENTRY = (QLineEdit, QPlainTextEdit, QTextEdit)


def _name(widget: QWidget) -> str:
    """What to call a control in a finding, preferring the accessible name."""
    for value in (
        widget.accessibleName(),
        getattr(widget, "text", lambda: "")(),
        widget.objectName(),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return type(widget).__name__


def _is_wired(button: QAbstractButton) -> bool:
    """Whether pressing this button reaches any code at all.

    PySide6 rejects `receivers(button.clicked)` and wants the old SIGNAL string
    form, which is why this looks the way it does. Both arities are counted
    because clicked carries an optional checked flag and a connection to either
    one means the control is alive.
    """
    if button.isCheckable():
        # A checkable control is a state holder, not an action, and it is alive
        # whether or not anything is connected to it: SettingsDialog reads
        # fourteen of them with isChecked() when the user saves (line 969)
        # rather than connecting each one. Reporting those as dead produced
        # fourteen false findings in one run. A checkbox nothing ever reads is
        # a real defect, but it is not visible from the widget, so this gate
        # does not claim to see it.
        return True
    for signature in ("2clicked()", "2clicked(bool)"):
        try:
            if button.receivers(signature) > 0:
                return True
        except (RuntimeError, TypeError):
            continue
    return False


def _is_qt_internal(control: QWidget) -> bool:
    """Whether Qt built this control rather than the product.

    A QSpinBox owns a QLineEdit, a QComboBox owns another, a QTableWidget owns
    a corner button, and a QTabBar owns two scroll arrows. None of them carry
    an accessible name and none of them are ours to name, so checking them
    produced six findings in one run that no change to this codebase could fix.
    """
    parent = control.parentWidget()
    return isinstance(parent, (QAbstractSpinBox, QComboBox, QTableWidget, QTabBar))


def _has_an_action(button: QAbstractButton) -> bool:
    """A button may carry a QAction or a menu instead of a clicked receiver."""
    menu = getattr(button, "menu", None)
    if callable(menu) and menu() is not None:
        return True
    return bool(button.actions())


def audit(window: EchoLinkWindow, app: QApplication) -> dict[str, object]:
    findings: list[str] = []
    skipped: list[str] = []
    checked = 0
    per_tab: dict[str, int] = {}

    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        tab = window.tabs.tabText(index)
        controls = [
            widget
            for widget in window.findChildren(QWidget)
            if isinstance(widget, INTERACTIVE) and widget.isVisible()
        ]
        per_tab[tab] = len(controls)
        for control in controls:
            label = _name(control)
            if label in SKIPS:
                skipped.append(f"{tab}/{label}: {SKIPS[label]}")
                continue
            checked += 1
            if not str(control.accessibleName() or "").strip() and not str(
                getattr(control, "text", lambda: "")() or ""
            ).strip():
                findings.append(f"{tab}: a {type(control).__name__} has no name")
            if isinstance(control, QAbstractButton):
                if not control.isEnabled() and label not in DISABLED_UNTIL:
                    findings.append(
                        f"{tab}/{label}: disabled with no recorded reason"
                    )
                if not _is_wired(control) and not _has_an_action(control):
                    findings.append(
                        f"{tab}/{label}: nothing is connected to clicked(), so "
                        "pressing it cannot do anything"
                    )
            if isinstance(control, QComboBox) and control.count() == 0:
                findings.append(f"{tab}/{label}: a list with no entries")

    # Every tab reporting the same count means the pages never switched, and
    # the run verified nothing. ui_smoke_probe learned this the hard way when
    # SelectionItemPattern.Select moved the highlight without changing the row.
    counts = list(per_tab.values())
    if len(counts) > 1 and len(set(counts)) == 1:
        findings.append(
            f"every tab reported the same {counts[0]} controls, so the pages "
            "did not switch and this run verified nothing"
        )

    return {
        "checked": checked,
        "tabs": per_tab,
        "findings": findings,
        "skipped": skipped,
    }


def audit_surface(surface: QWidget, label: str) -> tuple[list[str], list[str], int]:
    """Check one dialog or panel the tab walk cannot reach.

    The Writing HUD and the dialogs are not children of the main tab widget, so
    the walk above never sees them - and the HUD is where the original defect
    was, five controls created and never shown. Same three checks, no clicking:
    a dialog that opens another dialog from a harness never comes back.
    """
    findings: list[str] = []
    skipped: list[str] = []
    checked = 0
    for control in surface.findChildren(QWidget):
        if not isinstance(control, INTERACTIVE) or not control.isVisible():
            continue
        if _is_qt_internal(control):
            continue
        name = _name(control)
        if name in SKIPS:
            skipped.append(f"{label}/{name}: {SKIPS[name]}")
            continue
        checked += 1
        if not str(control.accessibleName() or "").strip() and not str(
            getattr(control, "text", lambda: "")() or ""
        ).strip():
            findings.append(f"{label}: a {type(control).__name__} has no name")
        if isinstance(control, QAbstractButton):
            if (
                not control.isEnabled()
                and name not in DISABLED_UNTIL
            ):
                findings.append(f"{label}/{name}: disabled with no recorded reason")
            if not _is_wired(control) and not _has_an_action(control):
                findings.append(
                    f"{label}/{name}: nothing is connected to clicked(), so "
                    "pressing it cannot do anything"
                )
        if isinstance(control, QComboBox) and control.count() == 0:
            findings.append(f"{label}/{name}: a list with no entries")
    return findings, skipped, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args()

    for name, value in SCRIPT_ENVIRONMENT.items():
        os.environ.setdefault(name, value)

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    with tempfile.TemporaryDirectory(prefix="echo-link-controls-") as directory:
        window = EchoLinkWindow(
            EchoLinkStore(Path(directory) / "echo-link.db"),
            enable_hotkey=False,
            enable_tray=False,
        )
        window.resize(1024, 720)
        window.show()
        # Seed real state before auditing. On an empty store almost every
        # button is correctly disabled - there is no document to export, no
        # revision to save, no selection to replace - and a gate run against
        # that reports eighteen findings that are all the product behaving
        # properly. Judging a control on whether it is enabled only means
        # something once the thing it acts on exists.
        document = window.document_workspace.create_document(
            "Control surface check",
            "A local document with enough text to select, export and revise.",
        )
        window.document_panel.refresh_documents(select_id=document.id)
        app.processEvents()
        report = audit(window, app)

        # The HUD and the dialogs, which the tab walk cannot see. The HUD is
        # built without a parent window on purpose: it is a Tool window that
        # follows focus, and constructing it the way the app does is the only
        # way its controls exist to be checked.
        from echo_link_app.rewrite_palette import RewritePalette
        from echo_link_app.settings_dialog import SettingsDialog
        from echo_link_app.writing_models_dialog import WritingModelsDialog

        # The HUD has to be given a capture before it is checked. At rest one
        # control is visible; with a capture open, the card, its tone list and
        # its action buttons exist, and those are where the five never-shown
        # controls that motivated the original probe lived. Checking it at rest
        # would report a clean HUD while covering almost none of it.
        hud = RewritePalette()
        hud.show_capture("teh draft has erors in it and it are not finished.")
        hud.open_current_capture()

        surfaces = [
            ("Writing HUD", hud),
            ("Settings", SettingsDialog(window.store, window.engine, window)),
            # auto_refresh off: it starts a background poll of the local model
            # host, and a gate must not depend on whether a model is loaded.
            ("Writing models", WritingModelsDialog("", window, auto_refresh=False)),
        ]
        # The tone list is a product contract, not a generic control. Six on
        # the owner's instruction of 2026-08-16, matching Android so one
        # product does not offer two different menus. A list that silently
        # loses an entry looks fine to every check above.
        tones = tuple(
            hud.tone_combo.itemText(index)
            for index in range(hud.tone_combo.count())
        )
        expected = ("Clear", "Improve", "Rephrase", "Shorten", "Formal", "Friendly")
        if tones != expected:
            report["findings"].append(
                f"Writing HUD/Rewrite tone: offers {tones}, expected {expected}"
            )

        for label, surface in surfaces:
            surface.show()
            app.processEvents()
            found, skipped, checked = audit_surface(surface, label)
            report["findings"].extend(found)
            report["skipped"].extend(skipped)
            report["checked"] += checked
            report["tabs"][label] = checked
            surface.close()
            app.processEvents()

    print(f"tabs: {report['tabs']}")  # noqa: T201
    print(f"controls checked: {report['checked']}")  # noqa: T201
    for line in report["skipped"]:
        print(f"  skipped: {line}")  # noqa: T201
    findings = report["findings"]
    if findings:
        print(f"\nFINDINGS ({len(findings)}):")  # noqa: T201
        for line in findings:
            print(f"  - {line}")  # noqa: T201
    else:
        print("\nno findings")  # noqa: T201

    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
