"""Protected View was detected by matching an English string in a window title.

    if "protected view" in title.casefold():

A German Word captions that window "Geschuetzte Ansicht", a French one "Mode
protege", a Spanish one "Vista protegida". On every non-English Word the branch
never fired, so a writer whose document came from a download, an email
attachment or a network share was told:

    "No selected text was found. Highlight text and try again."

on a document where nothing can be selected at all. That is advice they have
already followed, and it sends them round the same loop with no way out. Of
everything a non-English user meets, this is the most likely.

`Application.ActiveProtectedViewWindow` asks Word the same question instead of
reading its caption, and is not translated. The title test is kept as a
fallback: Word may be starting or unreachable through COM, and English must
behave exactly as it did before.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core import insertion  # noqa: E402
from echo_link_core.insertion import WindowSnapshot  # noqa: E402

# Real captions. The doubled spaces are Word's own.
ENGLISH = "Report  -  Protected View  -  Word"
GERMAN = "Bericht  -  Geschuetzte Ansicht  -  Word"
FRENCH = "Rapport  -  Mode protege  -  Word"
SPANISH = "Informe  -  Vista protegida  -  Word"
ORDINARY = "Report  -  Word"


def _word_window(title: str) -> WindowSnapshot:
    """A Word window as the capture path sees one, class name included."""
    return WindowSnapshot(1, 2, title, "OpusApp")


def _word_says(monkeypatch, protected):
    class _Application:
        ActiveProtectedViewWindow = protected

    monkeypatch.setattr(insertion, "_active_word_application", lambda: _Application())


def test_a_localised_protected_view_is_named(monkeypatch):
    """The defect: three languages that used to fall through to the wrong advice."""
    _word_says(monkeypatch, object())
    for title in (GERMAN, FRENCH, SPANISH):
        message = insertion._no_selection_message(_word_window(title))
        assert "Protected View" in message, (
            f"{title!r} was not recognised as Protected View, so the writer is "
            f"told to highlight text on a document that cannot be selected"
        )
        assert "Enable Editing" in message


def test_english_still_works_without_asking_word(monkeypatch):
    """The title path costs nothing and must not have been made to depend on COM."""

    def _unreachable():
        raise OSError("Word is not running")

    monkeypatch.setattr(insertion, "_active_word_application", _unreachable)
    message = insertion._no_selection_message(_word_window(ENGLISH))
    assert "Protected View" in message and "Enable Editing" in message


def test_an_ordinary_document_is_unchanged(monkeypatch):
    """The other half of the contract: don't cry Protected View on a live document."""
    _word_says(monkeypatch, None)
    assert insertion._no_selection_message(_word_window(ORDINARY)) == (
        "No selected text was found. Highlight text and try again."
    )


def test_word_being_unreachable_is_not_evidence(monkeypatch):
    """COM failing must not turn an ordinary document into a Protected View one."""

    def _unreachable():
        raise OSError("Word is not running")

    monkeypatch.setattr(insertion, "_active_word_application", _unreachable)
    assert insertion._no_selection_message(_word_window(ORDINARY)) == (
        "No selected text was found. Highlight text and try again."
    )


def test_a_non_word_window_never_asks_word(monkeypatch):
    """Word may hold a protected document while the writer is in another app."""
    asked = []

    def _record():
        asked.append(True)
        raise AssertionError("Word was asked about a window that is not Word's")

    monkeypatch.setattr(insertion, "_active_word_application", _record)
    other = WindowSnapshot(1, 2, "Notes - Some Other Editor", "SomeOtherClass")
    assert insertion._no_selection_message(other) == (
        "No selected text was found. Highlight text and try again."
    )
    assert not asked


def test_a_com_object_that_cannot_be_tested_for_truth_still_counts(monkeypatch):
    """A live COM proxy can raise from __bool__; its existence is the answer."""

    class _Awkward:
        def __bool__(self):
            raise OSError("the RPC server is unavailable")

    _word_says(monkeypatch, _Awkward())
    message = insertion._no_selection_message(_word_window(GERMAN))
    assert "Protected View" in message
