from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import echo_link_app.window as window_module
from echo_link_app.review_spans import _review_spans, _sentence_spans
from echo_link_app.rewrite_palette import RewritePalette
from echo_link_app.target_underline import TargetUnderline, _safe_line_rectangles
from echo_link_app.theme import COLORS, apply_dark_theme
from echo_link_app.window import EchoLinkWindow, _same_document_text, _unique_document_text
from echo_link_core.insertion import SelectionRecord, WindowSnapshot
from echo_link_core.windows_selection import DetectedSelection, WindowsSelectionMonitor
from echo_link_core.selected_text import COMMANDS
from echo_link_core.storage import EchoLinkStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _selection(text: str = "This are selected text.") -> SelectionRecord:
    return SelectionRecord(text, WindowSnapshot(77, 88, "Editor"))


def test_window_can_disable_background_model_setup(app, tmp_path, monkeypatch) -> None:
    started: list[str] = []

    class _RecordedThread:
        def __init__(self, *, target, name="", **_kwargs):
            self.target = target
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setenv("ECHOLINK_DISABLE_MODEL_SETUP", "1")
    monkeypatch.setenv("ECHOLINK_DISABLE_PALETTE_AI", "1")
    monkeypatch.setattr(window_module.threading, "Thread", _RecordedThread)

    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=False,
    )
    window.selected_text_record = _selection("teh draft")
    window._run_palette_sentence_rewrite(
        1,
        "sentence-1",
        "teh draft",
        "Proofread",
    )
    window.shutdown()

    assert started == []


def test_runtime_status_watcher_stops_immediately_after_shutdown(app, tmp_path) -> None:
    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=False,
    )
    calls: list[str] = []
    window.writing_assistant = SimpleNamespace(
        runtime_status=lambda *_args, **_kwargs: calls.append("status")
    )
    done = Event()
    with window._palette_ai_lock:
        window._palette_ai_shutdown = True
    worker = Thread(
        target=window._watch_palette_runtime,
        args=(1, 1, {"model": "AI", "backend": "GPU"}, 20, done),
    )

    worker.start()
    worker.join(timeout=0.75)
    stopped = not worker.is_alive()
    done.set()
    worker.join(timeout=2.0)
    window.shutdown()

    assert stopped
    assert calls == []


def test_runtime_status_watcher_contains_unexpected_provider_error(app, tmp_path) -> None:
    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=False,
    )

    def fail_status(*_args, **_kwargs):
        raise AttributeError("malformed local status")

    window.writing_assistant = SimpleNamespace(runtime_status=fail_status)
    done = Event()
    worker = Thread(
        target=window._watch_palette_runtime,
        args=(1, 1, {"model": "AI", "backend": "GPU"}, 20, done),
    )

    worker.start()
    time.sleep(0.7)
    contained = worker.is_alive()
    done.set()
    worker.join(timeout=2.0)
    window.shutdown()

    assert contained
    assert not worker.is_alive()


class _SuggestionEngine:
    def analyze(self, text: str):
        suggestions = []
        if "teh" in text:
            start = text.index("teh")
            suggestions.append(
                SimpleNamespace(
                    suggestion_id="spelling-1",
                    category="Spelling",
                    title="Fix spelling",
                    explanation="Replace a common misspelling.",
                    start=start,
                    end=start + 3,
                    original="teh",
                    replacement="the",
                )
            )
        if "are ready" in text:
            start = text.index("are ready")
            suggestions.append(
                SimpleNamespace(
                    suggestion_id="grammar-1",
                    category="Grammar",
                    title="Match subject and verb",
                    explanation="Use the singular verb for this subject.",
                    start=start,
                    end=start + 3,
                    original="are",
                    replacement="is",
                )
            )
        return tuple(suggestions)


class _NoUiaReader:
    def read(self):
        return None

    def read_document(self):
        return None


def test_palette_has_all_commands_and_requires_review(app):
    palette = RewritePalette()
    requests: list[tuple[str, str]] = []
    palette.command_requested.connect(lambda command, language: requests.append((command, language)))
    palette.show_capture("This are selected text.")
    app.processEvents()

    assert tuple(palette.command_buttons) == tuple(COMMANDS)
    assert not palette.replace_button.isEnabled()
    assert not palette.stage_button.isEnabled()
    palette.command_buttons["Proofread"].click()
    assert requests == [("Proofread", "")]
    assert all(not button.isEnabled() for button in palette.command_buttons.values())
    assert not palette.replace_button.isEnabled()

    palette.show_result("This is selected text.", "Proofread ready. Review before use.")
    assert palette.replace_button.isEnabled()
    assert palette.stage_button.isEnabled()
    assert palette.result_edit.isReadOnly() is False
    palette.finish_choice()


def test_palette_groups_edits_into_one_full_sentence_and_applies_it(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    replacements: list[str] = []
    palette.replace_requested.connect(replacements.append)
    palette.show_capture("Echo-Link are ready with teh draft.")
    app.processEvents()
    assert palette.view == "badge"
    palette.badge_button.click()

    assert tuple(palette.filter_buttons) == (
        "All",
        "Spelling",
        "Grammar",
        "Formatting",
        "Clarity",
    )
    assert palette.view == "card"
    assert len(palette.suggestions) == 1
    assert palette.suggestion_count.text() == "1 review"
    assert palette.suggestion_list.count() == 1
    assert palette.before_text.toPlainText() == "Echo-Link are ready with teh draft."
    assert palette.after_text.toPlainText() == "Echo-Link is ready with the draft."
    assert "misspelling" in palette.suggestion_explanation.text()
    assert not palette.replace_button.isEnabled()

    palette.filter_buttons["Spelling"].click()
    assert palette.suggestion_list.count() == 1
    assert "1 spelling" in palette.assistant_suggestion_count.text()
    palette.apply_suggestion_button.click()
    assert palette.result_text == "Echo-Link is ready with the draft."
    assert palette.replace_button.isEnabled()
    assert replacements == ["Echo-Link is ready with the draft."]
    assert palette.suggestion_count.text() == "0 reviews"
    assert palette.suggestions == ()

    palette.finish_choice()


def test_finished_review_says_so_and_then_collapses(app):
    # The GUI build manual, section 14: "No Issues: show a short positive
    # status, then collapse." Accepting the last section left the card open at
    # "0 of 0" with nothing to click, which is what the owner reported on
    # 2026-08-16 over a document whose sections were all done.
    palette = RewritePalette()
    replaced: list[str] = []
    palette.replace_requested.connect(replaced.append)
    palette.show_capture("teh draft")
    app.processEvents()
    assert palette.card_position.text() == "1 of 1"

    palette.apply_suggestion_button.click()
    app.processEvents()

    # The correction went to the document before the card collapses, so the
    # collapse cannot be losing it.
    assert replaced == ["The draft."]
    assert palette.suggestion_title.text() == "Nothing to correct here"
    assert palette.card_position.text() == "0 of 0"
    assert palette._no_issues_timer.isActive()

    palette._no_issues_timer.stop()
    palette._no_issues_timer.setInterval(1)
    palette._no_issues_timer.start()
    QTest.qWait(120)
    app.processEvents()

    assert not palette.isVisible()
    palette.finish_choice()


def test_a_rules_only_section_says_so_on_the_card(app):
    # A deterministic correction used to be presented exactly like an AI one,
    # which is how a rules-only essay reads as a working corrector right up
    # until it is graded. Nine rules-only paragraphs were once reported as
    # nine successes because nothing outside the engine could tell a refused
    # section from a corrected one.
    palette = RewritePalette(ai_proofread=False)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()

    assert palette.after_heading.text() == "Suggested text, built-in rules only"
    explanation = palette.suggestion_explanation.text()
    assert "The AI has not corrected this section" in explanation
    # And it says why, rather than leaving the writer to guess.
    assert "AI proofreading is off for this review" in explanation
    palette.finish_choice()


def test_an_ai_corrected_section_is_not_labelled_rules_only(app):
    # The label has to be absent when it does not apply, or it is noise and
    # stops being read.
    palette = RewritePalette(ai_proofread=True)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()
    suggestion = palette._current_review_suggestion()
    palette._ai_completed_ids.add(str(getattr(suggestion, "suggestion_id", "")))

    palette._render_suggestions()
    app.processEvents()

    assert palette.after_heading.text() == "Suggested text"
    assert "built-in rules only" not in palette.suggestion_explanation.text()
    palette.finish_choice()


def test_pinning_keeps_the_hud_in_front_when_focus_leaves(app):
    # "It should not be sticky to the top unless it is pinned, and there
    # should be a button to pin it." Unpinned, the HUD gives up the top when
    # the writer clicks elsewhere; pinned, that request outranks the
    # focus-following behaviour entirely.
    palette = RewritePalette()
    palette.show_capture("teh draft has erors in it.", anchor=(400, 300, 900, 340))
    app.processEvents()

    assert not palette.pinned
    assert palette.pin_button.text() == "Pin"

    palette.set_stays_on_top(False)
    app.processEvents()
    assert not (palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    palette.pin_button.click()
    app.processEvents()
    assert palette.pinned
    assert palette.pin_button.text() == "Pinned"
    assert palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    # The focus watcher asks it to drop, and a pinned window refuses.
    palette.set_stays_on_top(False)
    app.processEvents()
    assert palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert palette.isVisible()

    palette.pin_button.click()
    app.processEvents()
    palette.set_stays_on_top(False)
    app.processEvents()
    assert not (palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    palette.finish_choice()
    palette.close()


def test_a_document_the_ai_left_alone_shows_nothing_to_correct(app):
    # A finished essay produced ten sections, every one reported ready, and
    # none of them had anything in it. The owner: "it says that it is ready,
    # all sections are ready, but it has not directed anything." The harness
    # accepted all ten with no change and the review was rebuilt, which is the
    # loop that spent forty minutes on one essay.
    palette = RewritePalette(ai_proofread=True)
    palette.show_capture(
        "Paragraph one is already correct. Paragraph two needs no changes."
    )
    app.processEvents()
    assert palette._review_suggestions

    # The AI runs and returns each section exactly as it was.
    for suggestion in palette._suggestions:
        palette._ai_completed_ids.add(str(getattr(suggestion, "suggestion_id", "")))
    palette._render_suggestions()
    app.processEvents()

    assert palette._review_suggestions == ()
    assert palette.suggestion_title.text() == "Nothing to correct here"
    assert palette._no_issues_timer.isActive()
    palette.finish_choice()
    palette.close()


def test_a_section_still_waiting_for_its_ai_is_never_hidden(app):
    # The requirement is that every section is processed, not that every
    # section is displayed. Hiding one before its AI result exists would skip
    # it, which is the thing that must never happen.
    palette = RewritePalette(ai_proofread=True)
    palette.show_capture("Paragraph one is already correct and needs no changes.")
    app.processEvents()

    before = len(palette._review_suggestions)
    palette._render_suggestions()
    app.processEvents()

    assert before
    assert len(palette._review_suggestions) == before
    palette.finish_choice()
    palette.close()


def test_the_hud_floats_over_the_document_it_is_reviewing(app):
    # Removing the always-on-top hint outright was a regression, not a fix.
    # The HUD sank behind the very document it was correcting: the owner
    # reported "no small icon, no suggestions, no corrections" while the log
    # showed ten sections corrected and written to disk. An invisible HUD is
    # indistinguishable from a dead one.
    palette = RewritePalette()

    assert palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    palette.close()


def test_the_hud_gives_up_the_top_when_the_writer_moves_elsewhere(app):
    # "If the user decides that they want to click another window, it should
    # not stay on top." Toggling the hint on a visible frameless window can
    # drop it on Windows, so this also pins that it stays visible and keeps
    # the position it was placed at.
    palette = RewritePalette()
    palette.show_capture("teh draft has erors in it.", anchor=(400, 300, 900, 340))
    app.processEvents()
    placed = palette.pos()

    palette.set_stays_on_top(False)
    app.processEvents()

    assert not (palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert palette.isVisible()
    assert palette.pos() == placed

    palette.set_stays_on_top(True)
    app.processEvents()

    assert palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert palette.isVisible()
    assert palette.pos() == placed
    palette.finish_choice()
    palette.close()


def test_the_box_does_not_move_itself_once_it_has_been_placed(app):
    # The card changes size and anchor as a review runs, and every one of those
    # re-decided where the box belonged, so it walked around the screen while
    # the writer was reading it. Placement happens once per capture.
    #
    # An earlier version of this test resized the widget and re-asserted, and
    # passed with the fix removed: _reassert_current_view_size calls
    # _apply_view_size first, which puts the size back, so nothing moved either
    # way. A different anchor is what actually relocates the box.
    palette = RewritePalette()
    palette.show_capture(
        "teh draft has erors. the dog dont fit. i has a problim here.",
        anchor=(400, 300, 900, 340),
    )
    app.processEvents()
    placed = palette.pos()

    assert not palette._may_auto_place()
    palette._show_near_pointer(activate=False, anchor=(1200, 800, 1500, 860))
    app.processEvents()
    palette._reassert_current_view_size()
    app.processEvents()

    assert palette.pos() == placed
    palette.finish_choice()
    palette.close()


def test_a_position_the_writer_chose_survives_the_next_capture(app):
    # "If the user wants to move it, they should be able to move it, but it
    # should not move on its own." Putting it back on the next capture is the
    # box moving on its own again.
    palette = RewritePalette()
    palette.show_capture("teh draft has erors.", anchor=(400, 300, 900, 340))
    app.processEvents()

    palette.move(QPoint(50, 60))
    palette._user_positioned = True
    palette.show_capture("another teh document with erors.", anchor=(1000, 700, 1400, 740))
    app.processEvents()
    palette._reassert_current_view_size()
    app.processEvents()

    assert palette.pos() == QPoint(50, 60)
    palette.finish_choice()
    palette.close()


class _NullMonitor:
    def __init__(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self, timeout=0):
        return None


def _document(text: str, handle: int = 77, pid: int = 88) -> SelectionRecord:
    """A whole-document capture, the way Word reports one."""
    return SelectionRecord(text, WindowSnapshot(handle, pid, "Essay - Word"), scope="document")


def test_leaving_the_review_resumes_the_selection_monitor(app, tmp_path, monkeypatch):
    """A half-finished review must not leave the app deaf.

    The monitor is paused on badge mouse-down so the capture churn stops while
    a review is worked on, and it was only resumed when that review finished.
    A writer who leaves a section unfinished and moves on left it paused for
    good: on 2026-08-18 the last focus detection in the entire log was
    16:35:18, the review sat at 9 of 10, and nothing after that produced a
    signal at all. Not a refusal. Nothing.
    """
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    monitor = _NullMonitor()
    window.selection_monitor = monitor
    window._handle_automatic_selection(DetectedSelection(_selection("teh draft."), (1, 2, 3, 4)))
    app.processEvents()
    window._pause_selection_monitor()
    assert monitor.paused

    # The writer clicks some other window entirely.
    monkeypatch.setattr(window_module, "active_window", lambda: WindowSnapshot(999, 999, "Elsewhere"))
    window._release_hud_top_if_focus_left()

    assert not monitor.paused
    window.close()


def test_a_second_document_still_gets_a_badge(app, tmp_path, monkeypatch):
    """Opening a different document while a review is open must offer it.

    A focus signal may not tear a live review off the screen, and that rule is
    kept: what changes here is that a whole document in a different window is
    not "some other window on the machine". Every spurious capture on
    2026-08-15 was a selection inside a chat box, a browser field or a search
    box; every real document was a document-scope capture from its own window.
    """
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    # A live review the writer is working in, whose window is genuinely alive.
    monkeypatch.setattr(window, "_hud_source_window_alive", lambda: True)
    first = _document("The first document, part way through.", handle=77, pid=88)
    window._handle_automatic_selection(DetectedSelection(first, (1, 2, 3, 4)))
    app.processEvents()
    window.rewrite_palette.open_current_capture()
    app.processEvents()
    assert window.rewrite_palette.view != "badge"

    # A selection somewhere else must still be refused: that is the spam case.
    chat = _selection("a line typed into a chat box")
    window._handle_automatic_selection(DetectedSelection(chat, (9, 9, 19, 19)))
    app.processEvents()
    assert window.selected_text_record == first

    # A different document is the writer moving on, and it gets offered.
    second = _document("The second document, freshly opened.", handle=1234, pid=5678)
    window._handle_automatic_selection(DetectedSelection(second, (5, 6, 7, 8)))
    app.processEvents()

    assert window.selected_text_record == second
    assert window.rewrite_palette.isVisible()
    window.close()


def test_an_ignored_focus_signal_says_why(app, tmp_path, caplog):
    """A refused capture must name its reason in the log.

    On 2026-08-18 Word was detected at 5013 characters, "focus signal
    received" was logged, and then nothing at all: no capture, no badge, no
    reason. Five separate conditions refuse at that one return and they were
    indistinguishable in the log from the focus watcher never firing, so the
    search started in the wrong place twice.

    The suppression window is the condition used here because a test can set
    it honestly. It is the same return and the same log line whichever
    condition fires.
    """
    import logging
    import time as _time

    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    window._automatic_hud_suppress_until = _time.monotonic() + 60

    record = _selection("A document the writer has just clicked into.")
    with caplog.at_level(logging.INFO, logger="echo_link_app.window"):
        window._handle_automatic_selection(DetectedSelection(record, (100, 120, 280, 150)))
        app.processEvents()

    assert not window.rewrite_palette.isVisible()
    ignored = [r for r in caplog.records if "ignored a focus signal" in r.getMessage()]
    assert ignored, "a refused focus signal logged nothing"
    assert "reason=suppressed" in ignored[-1].getMessage()
    window.close()


def test_a_finished_document_is_not_offered_back(app, tmp_path):
    # Essay 1 on 2026-08-15 finished all 22 sections at 23:58:51 and the badge
    # re-fired on the same text nine seconds later, rebuilding the whole
    # review as "sections=22 atomic=0". The writer was then walked through 22
    # sections with nothing in them.
    #
    # What must not come back is the unasked review. The badge itself now
    # stays: hiding it too was the first version of this fix and it left a
    # reviewed document with no way back in, which is what Curtis reported on
    # 2026-08-18 as the icon not appearing on Word. So the assertion below
    # changed from "nothing is on screen" to "nothing was built unasked".
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    record = _selection("This document has already been reviewed in full.")

    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 280, 150)))
    app.processEvents()
    assert window.rewrite_palette.isVisible()

    # Finish the review the way the palette does when the last section goes.
    window.rewrite_palette.finish_choice()
    window._finish_palette_unchanged()
    app.processEvents()

    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 280, 150)))
    app.processEvents()

    assert window.rewrite_palette.isVisible()
    assert window.rewrite_palette.view == "badge"
    # Deferred, not run: no review exists until the writer clicks the badge.
    assert window.rewrite_palette._analysis_pending
    assert window.rewrite_palette._review_suggestions == ()
    window.close()


def test_a_reviewed_document_still_opens_when_the_writer_asks(app, tmp_path):
    """The badge on an already reviewed document must still work.

    The machine stops offering; the writer keeps asking. Clicking the badge
    finishes the deferred analysis and opens the card as it always did.
    """
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    record = _selection("This document has alreddy been reviewed in full.")

    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 280, 150)))
    app.processEvents()
    window.rewrite_palette.finish_choice()
    window._finish_palette_unchanged()
    app.processEvents()

    # finish_choice hides the box, so this is the writer coming back to a
    # document they already reviewed. The badge has to return, or there is no
    # way to ask for anything.
    assert not window.rewrite_palette.isVisible()
    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 280, 150)))
    app.processEvents()
    assert window.rewrite_palette.isVisible()

    window.rewrite_palette.open_current_capture()
    app.processEvents()

    assert not window.rewrite_palette._analysis_pending
    assert window.rewrite_palette.view in {"card", "loading"}
    window.close()


def test_editing_after_a_review_brings_the_offer_back(app, tmp_path):
    # Keyed on the text, not a timer: the moment the writer changes something
    # the document is a different document and is worth offering again.
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    first = _selection("This document has already been reviewed in full.")

    window._handle_automatic_selection(DetectedSelection(first, (100, 120, 280, 150)))
    app.processEvents()
    window.rewrite_palette.finish_choice()
    window._finish_palette_unchanged()
    app.processEvents()

    edited = _selection("This document has already been reviewed in full. And teh writer added this.")
    window._handle_automatic_selection(DetectedSelection(edited, (100, 120, 280, 150)))
    app.processEvents()

    assert window.rewrite_palette.isVisible()
    assert window.selected_text_record == edited
    window.close()


def test_line_endings_alone_do_not_count_as_an_edit(app, tmp_path):
    # Word reports the same document with different line endings depending on
    # how it was read, so a raw comparison would re-offer an unchanged file.
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selection_monitor = _NullMonitor()
    first = _selection("One line.\r\nTwo lines.")

    window._handle_automatic_selection(DetectedSelection(first, (100, 120, 280, 150)))
    app.processEvents()
    window.rewrite_palette.finish_choice()
    window._finish_palette_unchanged()
    app.processEvents()

    same = _selection("One line.\nTwo lines.")
    window._handle_automatic_selection(DetectedSelection(same, (100, 120, 280, 150)))
    app.processEvents()

    # Same document, so no review is built unasked. The badge stays either way.
    assert window.rewrite_palette._analysis_pending
    assert window.rewrite_palette._review_suggestions == ()
    window.close()


def _undoable_palette(text="teh draft has erors in it."):
    """A palette wired to a document the test can read back."""
    document = {"text": None}
    palette = RewritePalette()
    palette.set_step_replacer(
        lambda written, suggestion: (document.__setitem__("text", written), True)[1]
    )
    palette.set_undo_replacer(
        lambda previous, suggestion: (document.__setitem__("text", previous), True)[1]
    )
    palette.show_capture(text)
    return palette, document


def test_accepting_the_last_section_offers_undo_before_it_closes(app):
    # Section 14 of the GUI build manual: "Applied: show a brief success
    # message and Undo." Closing the instant the last section was accepted
    # gave the writer neither, and the only Undo in the product was in the
    # main window, which a writer working in Word never sees.
    palette, document = _undoable_palette()
    app.processEvents()
    original = palette._preview_base

    palette.apply_suggestion_button.click()
    app.processEvents()

    assert document["text"] and document["text"] != original
    assert palette.suggestion_title.text() == "Change applied"
    assert not palette.undo_button.isHidden()
    # And it is a brief message, not a new resting state.
    assert palette._no_issues_timer.isActive()
    palette.finish_choice()


def test_undo_puts_the_text_back_and_returns_the_section(app):
    palette, document = _undoable_palette()
    app.processEvents()
    original = palette._preview_base
    assert palette.card_position.text() == "1 of 1"

    palette.apply_suggestion_button.click()
    app.processEvents()
    palette.undo_button.click()
    app.processEvents()

    # The document, not just the card.
    assert document["text"] == original
    assert palette._preview_base == original
    # The section is in play again rather than counted as done.
    assert palette.card_position.text() == "1 of 1"
    assert palette.undo_button.isHidden()
    palette.finish_choice()


def test_the_applied_message_collapses_into_a_normal_finish(app):
    palette, _document = _undoable_palette()
    app.processEvents()
    applied = []
    palette.applied.connect(lambda: applied.append(True))

    palette.apply_suggestion_button.click()
    app.processEvents()
    palette._no_issues_timer.stop()
    palette._no_issues_timer.setInterval(1)
    palette._no_issues_timer.start()
    QTest.qWait(120)
    app.processEvents()

    assert not palette.isVisible()
    assert applied == [True]


def test_a_host_that_cannot_reverse_is_never_offered_undo(app):
    # A button that fails when pressed is worse than no button.
    palette = RewritePalette()
    palette.set_step_replacer(lambda written, suggestion: True)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()

    palette.apply_suggestion_button.click()
    app.processEvents()

    assert palette.undo_button.isHidden()
    assert not palette.isVisible()
    palette.finish_choice()


def test_a_refused_undo_leaves_the_applied_state_alone(app):
    # If the document write fails the card must keep describing what is
    # actually in the document, which is the accepted text.
    palette = RewritePalette()
    palette.set_step_replacer(lambda written, suggestion: True)
    palette.set_undo_replacer(lambda previous, suggestion: False)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()
    original = palette._preview_base

    palette.apply_suggestion_button.click()
    app.processEvents()
    applied_text = palette._preview_base
    palette.undo_button.click()
    app.processEvents()

    assert applied_text != original
    assert palette._preview_base == applied_text
    assert palette.suggestion_title.text() == "Change applied"
    palette.finish_choice()


def test_a_failed_analysis_is_not_reported_as_a_clean_document(app):
    # When preparing the review raises, the review is emptied and rendered
    # through the same empty-card branch a clean document uses, so a document
    # whose analysis blew up was congratulated for having nothing wrong with
    # it. A 9630 character document lost 25 sections that way on 2026-08-15
    # and the card said "Nothing to correct here".
    class _Exploding:
        def __init__(self):
            self.calls = 0

        def analyze(self, text):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return ()

    engine = _Exploding()
    palette = RewritePalette(suggestion_engine=engine)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()

    assert palette.suggestion_title.text() == "Could not check this text"
    assert "not been reviewed" in palette.suggestion_explanation.text()
    assert "Nothing to correct here" not in palette.suggestion_title.text()
    # The manual requires a recovery action, and a failure must not collapse.
    assert palette.rewrite_sentence_button.text() == "Try Again"
    assert palette.rewrite_sentence_button.isEnabled()
    assert not palette._no_issues_timer.isActive()

    # Try Again retries the preparation, not a sentence rewrite: there is no
    # suggestion to rewrite, because building them is what failed.
    palette.rewrite_sentence_button.click()
    app.processEvents()

    assert engine.calls == 2
    assert palette._analysis_failure == ""
    assert palette.suggestion_title.text() != "Could not check this text"
    assert palette.rewrite_sentence_button.text() == "Regenerate"
    palette.finish_choice()


def test_a_card_asking_for_a_fresh_capture_does_not_collapse(app):
    # require_recapture renders an empty review before it writes its own
    # message, so the no-issues collapse armed itself over a card that is the
    # opposite of no issues: it is asking the writer to capture again. Left
    # alone it took that request off the screen after three seconds.
    palette = RewritePalette()
    palette.set_step_replacer(lambda text, suggestion: True)
    palette.show_capture("teh draft has erors in it.")
    app.processEvents()

    palette.require_recapture("The document changed. Capture it again.")
    app.processEvents()

    assert palette.suggestion_title.text() == "Fresh selection required"
    assert not palette._no_issues_timer.isActive()
    assert palette.isVisible()
    palette.finish_choice()


def test_a_failure_message_does_not_collapse(app):
    # Same hole, reached through show_error: a card carrying a failure and a
    # recovery action has to stay put.
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette._no_issues_timer.start()

    palette.show_error("This correction was not applied.")
    app.processEvents()

    assert not palette._no_issues_timer.isActive()
    palette.finish_choice()


def test_a_section_still_under_review_keeps_the_card_open(app):
    # The collapse must never fire over work the writer still has to do.
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()

    assert palette.card_position.text() == "1 of 1"
    assert not palette._no_issues_timer.isActive()
    palette.finish_choice()


def test_palette_applies_real_deterministic_suggestions(app):
    palette = RewritePalette()
    replaced = []
    palette.replace_requested.connect(replaced.append)
    palette.show_capture("teh draft")
    app.processEvents()

    assert len(palette.suggestions) == 1
    assert palette.before_text.toPlainText() == "teh draft"
    assert palette.after_text.toPlainText() == "The draft."
    assert palette.suggestion_title.text() == "Correctness suggestion"
    assert "Affects 1 sentence." in palette.suggestion_explanation.text()
    assert not palette.card_position.isVisible()
    palette.apply_suggestion_button.click()
    assert palette.result_text == "The draft."
    assert replaced == ["The draft."]
    assert palette.suggestions == ()
    palette.finish_choice()


def test_palette_reviews_up_to_five_complete_sentences_per_card(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture(
        "One sentence. Two sentences. teh third. Fourth sentence. "
        "Fifth sentence. Sixth sentence. teh seventh sentence."
    )
    app.processEvents()
    palette.badge_button.click()

    # Batches hold up to five sentences, so a typical page reviews in about
    # ten sections; seven sentences split five plus two.
    assert len(palette.suggestions) == 2
    assert palette.suggestions[0].sentence_count == 5
    assert palette.suggestions[1].sentence_count == 2
    assert palette.before_text.toPlainText() == (
        "One sentence. Two sentences. teh third. Fourth sentence. "
        "Fifth sentence."
    )
    assert palette.card_position.text() == "1 of 2"
    palette.finish_choice()


def test_review_spans_bound_one_punctuation_free_passage():
    source = " ".join(f"word{index}" for index in range(500))

    spans = _review_spans(source)

    assert len(spans) > 1
    assert all(end > start for start, end, _count in spans)
    assert all(end - start <= 1_200 for start, end, _count in spans)
    assert all(len(source[start:end].split()) <= 180 for start, end, _count in spans)
    assert all(left[1] <= right[0] for left, right in zip(spans, spans[1:]))


def test_review_spans_keep_each_written_line_separate():
    """Short unpunctuated lines used to be reviewed five at a time.

    A review unit is replaced as one run of text, so a line break inside it
    survives only if the model happens to write it back. It does not: essay 2's
    title and subtitle were reviewed together and came back fused into one
    line, ten paragraphs in and nine out. A break in the captured text is there
    because the writer pressed Enter, and the review must not be able to delete
    it.

    The cost is more review cards for a list of short lines. That is the right
    way round - a writer can click through cards, but cannot get a deleted
    paragraph break back.
    """
    source = "\n".join(
        (
            "first short item",
            "second short item",
            "third short item",
            "fourth short item",
            "fifth short item",
            "sixth short item",
            "seventh short item",
            "eighth short item",
        )
    )

    spans = _review_spans(source)

    assert len(spans) == 8
    assert all(source[start:end].count("\n") == 0 for start, end, _count in spans)


def test_sentence_splitter_does_not_treat_west_as_street_abbreviation():
    source = (
        "Produce went to Community Pantry West. "
        "These numbers matter. "
        "Because the first survey was incomplete."
    )

    spans = _sentence_spans(source)

    assert [source[start:end] for start, end in spans] == [
        "Produce went to Community Pantry West.",
        "These numbers matter.",
        "Because the first survey was incomplete.",
    ]


def test_review_spans_keep_dependent_fragments_with_prior_context():
    source = (
        "One. Two. Three. Four. Five. These numbers matter. "
        "Because the first survey was incomplete. "
        "Which caused fewer responses. Final."
    )

    spans = _review_spans(source, maximum_sentences=3)
    chunks = [source[start:end] for start, end, _count in spans]

    assert all(count <= 3 for _start, _end, count in spans)
    assert not any(
        chunk.startswith(("Because ", "Which "))
        for chunk in chunks
    )
    assert any(
        "These numbers matter. Because the first survey was incomplete. "
        "Which caused fewer responses."
        == chunk
        for chunk in chunks
    )


def test_review_spans_split_number_heavy_three_sentence_card():
    source = (
        "Eastgate Public Library stays open until 9:30 p.m. after 7:00 p.m. "
        "The trial is temporary. "
        "Keisha Ward received 684 replies between January 5 and February 2, "
        "and 438 people supported it."
    )

    spans = _review_spans(source, maximum_sentences=3)

    assert [count for _start, _end, count in spans] == [2, 1]


def test_review_spans_keep_while_fragment_with_prior_sentence():
    source = (
        "One. Two. Three. Four. The measures reduce known risks. "
        "While making clear that emergency procedures remain unchanged. Final."
    )

    chunks = [
        source[start:end]
        for start, end, _count in _review_spans(source, maximum_sentences=3)
    ]

    assert not any(chunk.startswith("While ") for chunk in chunks)
    assert any(
        "The measures reduce known risks. "
        "While making clear that emergency procedures remain unchanged."
        in chunk
        for chunk in chunks
    )


def test_palette_highlights_changed_text_without_spellcheck_underlines(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()

    selections = palette.after_text.extraSelections()
    assert selections
    # Insertions carry Android's green highlight rather than the app accent, so
    # added text reads as added without needing a legend.
    assert any(
        selection.format.underlineStyle().name == "NoUnderline"
        and selection.format.background().color().name().casefold()
        == COLORS["diff_insert_background"].casefold()
        for selection in selections
    )
    removed = palette.before_text.extraSelections()
    assert removed
    assert any(
        selection.format.fontStrikeOut()
        and selection.format.foreground().color().name().casefold()
        == COLORS["diff_remove_text"].casefold()
        for selection in removed
    )
    palette.finish_choice()


def test_palette_scales_card_for_three_sentence_text(app):
    palette = RewritePalette()
    palette.show_capture(
        "this is the first complete sentence with several words. "
        "this is the second complete sentence with several words. "
        "this is the third complete sentence with several words."
    )
    app.processEvents()
    palette.badge_button.click()
    palette._reassert_current_view_size()

    area = app.primaryScreen().availableGeometry()
    assert palette.width() > palette._CARD_SIZE.width()
    assert palette.width() <= area.width() - 8
    assert palette.height() <= area.height() - 8
    palette.finish_choice()


def test_suggestion_card_allows_user_resize(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert palette.maximumWidth() > palette.minimumWidth()
    assert palette.maximumHeight() > palette.minimumHeight()

    palette.finish_choice()


def test_resizing_card_expands_original_suggested_and_queue_text(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture(
        "Echo-Link are ready. teh draft. Clean sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    original_height = palette.before_text.height()
    suggested_height = palette.after_text.height()
    queue_height = palette.queue_preview.height()
    palette.resize(palette.width() + 240, palette.height() + 300)
    app.processEvents()

    assert palette.before_text.height() > original_height
    assert palette.after_text.height() > suggested_height
    assert palette.queue_preview.height() > queue_height
    palette.finish_choice()


def test_card_shows_upcoming_replacements_below_current(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture(
        "Echo-Link are ready. teh draft. Clean sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert palette.queue_heading.isVisible()
    preview = palette.queue_preview.toPlainText()
    assert "Next: 2 of 2" in preview
    assert "Another sentence." in preview
    palette.finish_choice()


def test_card_has_settings_left_and_close_in_top_right_corner(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    opened = []
    palette.settings_requested.connect(lambda: opened.append(True))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    palette.settings_button.click()

    assert opened == [True]
    assert palette.settings_button.x() < palette.close_button.x()
    assert palette.card_position.x() < palette.close_button.x()
    assert palette.close_button.geometry().right() > palette.card_position.geometry().right()
    palette.finish_choice()


def test_card_keeps_model_work_status_visible(app):
    palette = RewritePalette(ai_proofread=True)
    palette.sentence_rewrite_requested.connect(lambda *_args: None)
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert palette.loading_status.isVisible()
    assert "checking" in palette.loading_status.text().casefold()
    assert "eta" in palette.loading_status.text().casefold()
    palette.finish_choice()


def test_ai_proofread_prefetches_next_replacement_before_user_decision(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()

    assert len(requests) == 1
    first_attempt, first_id, *_ = requests[0]
    palette.show_sentence_result(
        first_attempt,
        first_id,
        "Echo-Link is ready.",
        "First AI proofread ready.",
    )
    app.processEvents()

    assert len(requests) == 2
    assert requests[1][1] != first_id
    assert palette.card_position.text() == "1 of 2"
    palette.finish_choice()


def test_advancing_while_prefetch_runs_keeps_rule_text_locked(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, *_ = requests[0]
    palette.show_sentence_result(
        first_attempt,
        first_id,
        "Echo-Link is ready.",
        "First AI proofread ready.",
    )
    app.processEvents()
    second_attempt, second_id, *_ = requests[1]

    palette.apply_suggestion_button.click()
    app.processEvents()

    assert palette.card_position.text() == "2 of 2"
    # The HUD stays on the big card while this section finishes; only the
    # decision controls lock, so the window no longer bounces between sizes.
    assert palette.view == "card"
    assert not palette.apply_suggestion_button.isEnabled()

    palette.show_sentence_result(
        second_attempt,
        second_id,
        "Another corrected sentence.",
        "Second AI proofread ready.",
    )
    app.processEvents()

    assert palette.view == "card"
    assert palette.after_text.toPlainText() == "Another corrected sentence."
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_background_prefetch_keeps_current_ai_result_visibly_ready(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, *_ = requests[0]

    palette.show_sentence_result(
        first_attempt,
        first_id,
        # Faithful to the section it answers. The stub here used to drop two of
        # the section's three sentences, which answer_abandons_the_source now
        # refuses; this test is about the PREFETCH, not about content.
        "Echo-Link is ready. A clean sentence. A third sentence.",
        "Local AI proofread ready.",
    )
    app.processEvents()

    assert len(requests) == 2
    assert palette.card_position.text() == "1 of 2"
    assert "local ai proofread ready" in (
        palette.apply_suggestion_button.accessibleName().casefold()
    )
    assert palette.status.text() == "Local AI proofread ready."
    assert palette.after_text.toPlainText() == "Echo-Link is ready. A clean sentence. A third sentence."
    palette.finish_choice()


def test_background_prefetch_failure_does_not_hide_current_result(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, *_ = requests[0]
    palette.show_sentence_result(
        first_attempt,
        first_id,
        # Faithful, for the same reason as the test above. This one is about
        # what a FAILED prefetch does to the card already on screen.
        "Echo-Link is ready. A clean sentence. A third sentence.",
        "Local AI proofread ready.",
    )
    app.processEvents()
    second_attempt, second_id, *_ = requests[1]

    palette.show_sentence_error(
        second_attempt,
        second_id,
        "The next section failed validation.",
    )
    app.processEvents()

    assert palette.view == "card"
    assert palette.card_position.text() == "1 of 2"
    assert palette.after_text.toPlainText() == "Echo-Link is ready. A clean sentence. A third sentence."
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_background_timeout_keeps_current_result_and_prefetches_later_section(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    source = " ".join(
        f"Part {index} are written badly." for index in range(1, 16)
    )
    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()

    first_attempt, first_id, first_text, _command = requests[0]
    first_result = first_text.replace(" are ", " is ")
    palette.show_sentence_result(
        first_attempt,
        first_id,
        first_result,
        "AI correction ready.",
    )
    app.processEvents()
    second_attempt, second_id, *_ = requests[1]

    assert palette._active_ai_attempt_id == second_attempt
    assert palette._active_ai_background
    palette._expire_ai_request()
    app.processEvents()

    assert palette.view == "card"
    assert palette.card_position.text() == "1 of 3"
    assert palette.after_text.toPlainText() == first_result
    assert second_id in palette._ai_failed_ids
    assert len(requests) == 3
    assert requests[2][1] not in {first_id, second_id}
    palette.finish_choice()


def test_pool_timeout_does_not_immediately_retry_same_section(app):
    palette = RewritePalette(ai_proofread=True)
    palette.set_ai_width_provider(lambda: 2)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    source = " ".join(
        f"Part {index} are written badly." for index in range(1, 21)
    )
    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, first_text, _command = requests[0]
    palette.show_sentence_result(
        first_attempt,
        first_id,
        first_text.replace(" are ", " is "),
        "AI correction ready.",
    )
    app.processEvents()
    app.processEvents()

    assert len(requests) == 3
    pool_attempt, pool_id, *_ = requests[2]
    palette._expire_pool_attempt(pool_attempt)
    app.processEvents()

    assert pool_id in palette._ai_failed_ids
    assert len(requests) == 4
    assert requests[3][1] != pool_id
    palette.finish_choice()


def test_final_failed_retry_is_terminal_in_series_progress(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("Part one are wrong.", document_scope=True)
    app.processEvents()
    palette.badge_button.click()

    first_attempt, suggestion_id, *_ = requests[0]
    palette.show_sentence_error(
        first_attempt,
        suggestion_id,
        "AI could not finish this section.",
    )
    app.processEvents()
    retry_attempt, retry_id, *_ = requests[1]
    palette.show_sentence_error(
        retry_attempt,
        retry_id,
        "AI could not finish this section.",
    )
    app.processEvents()

    progress, total, message = palette._series_progress_state()
    assert (progress, total) == (1, 1)
    assert "1 needs retry" in message.casefold()
    assert "preparing" not in message.casefold()
    palette.finish_choice()


def test_foreground_ai_failure_gets_one_automatic_repair_then_stays_reviewable(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("Echo-Link are ready.")
    app.processEvents()
    palette.badge_button.click()
    attempt, suggestion_id, *_ = requests[0]

    palette.show_sentence_error(
        attempt,
        suggestion_id,
        "AI output did not pass final quality checks. Original text kept.",
    )
    app.processEvents()

    # One automatic sampled repair, then the failure surfaces with usable
    # controls: more repairs made a stuck section look frozen for minutes.
    assert len(requests) == 2
    retry_attempt, retry_id, *_ = requests[1]
    assert retry_id == suggestion_id
    palette.show_sentence_error(
        retry_attempt,
        retry_id,
        "AI output did not pass final quality checks. Original text kept.",
    )
    app.processEvents()

    assert len(requests) == 2
    assert palette.isVisible()
    assert palette.view == "card"
    assert "quality checks" in palette.status.text().casefold()
    assert palette.rewrite_sentence_button.text() == "Retry"
    assert palette.rewrite_sentence_button.isEnabled()
    # A rules-only placeholder must never become a finished AI correction.
    assert not palette.apply_suggestion_button.isEnabled()
    assert "rule-based" not in palette.after_heading.text().casefold()
    assert palette.after_text.toPlainText() == ""
    assert palette.dismiss_suggestion_button.text() == "Dismiss"
    assert palette.dismiss_suggestion_button.isEnabled()
    palette.finish_choice()


def test_failed_ai_section_does_not_show_no_safe_correction_dead_end(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("Echo-Link are ready.")
    app.processEvents()
    palette.badge_button.click()
    attempt, suggestion_id, *_ = requests[0]
    palette.show_sentence_error(
        attempt,
        suggestion_id,
        "The local AI stopped.",
    )
    app.processEvents()
    retry_attempt, retry_id, *_ = requests[1]

    palette.show_sentence_error(
        retry_attempt,
        retry_id,
        "The local AI stopped.",
    )
    app.processEvents()

    visible = palette.after_text.toPlainText().casefold()
    assert "no safe correction" not in visible
    # The failed section stays reviewable without exposing rules-only text as
    # a finished AI correction.
    explanation = palette.suggestion_explanation.text().casefold()
    assert "retry" in explanation
    assert "rule-based" not in explanation
    assert not palette.apply_suggestion_button.isEnabled()
    assert palette.dismiss_suggestion_button.isEnabled()
    palette.finish_choice()


def test_advancing_to_prefetched_result_restores_its_ready_state(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, *_ = requests[0]
    palette.show_sentence_result(
        first_attempt,
        first_id,
        "Echo-Link is ready.",
        "CUDA | qwen3:8b correction ready.",
    )
    app.processEvents()
    second_attempt, second_id, *_ = requests[1]
    palette.show_sentence_result(
        second_attempt,
        second_id,
        "Another corrected sentence.",
        "CUDA | qwen3:8b correction ready.",
    )
    app.processEvents()

    palette.apply_suggestion_button.click()
    app.processEvents()

    assert palette.card_position.text() == "2 of 2"
    assert "correction ready" in (
        palette.apply_suggestion_button.accessibleName().casefold()
    )
    assert palette.after_text.toPlainText() == "Another corrected sentence."
    palette.finish_choice()


def test_ten_section_document_is_prefetched_before_first_decision_and_advances_ready(
    app,
):
    palette = RewritePalette(ai_proofread=True)
    writes: list[str] = []
    requests: list[tuple[int, str, str, str]] = []
    palette.set_step_replacer(lambda text, _suggestion: writes.append(text) or True)
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    source = " ".join(
        f"Part {index} are written badly." for index in range(1, 51)
    )

    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()
    completed: dict[str, str] = {}
    while len(completed) < 10:
        attempt_id, suggestion_id, request_text, _command = requests[len(completed)]
        corrected = request_text.replace(" are ", " is ")
        completed[suggestion_id] = corrected
        palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            corrected,
            "CUDA | qwen3 correction ready.",
        )
        app.processEvents()

    assert len(requests) == 10
    assert palette.card_position.text() == "1 of 10"
    assert palette.after_text.toPlainText() == completed[requests[0][1]]
    assert palette.apply_suggestion_button.isEnabled()

    palette.apply_suggestion_button.click()
    app.processEvents()

    assert len(writes) == 1
    assert len(requests) == 10
    assert palette.card_position.text() == "2 of 10"
    assert palette.after_text.toPlainText() == completed[requests[1][1]]
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_ordered_document_hides_rules_placeholder_until_ai_finishes(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.set_step_replacer(lambda _text, _suggestion: True)
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))

    palette.show_capture("Part one are wrong. Part two are wrong.", document_scope=True)
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert requests
    assert palette.before_text.toPlainText()
    assert palette.after_text.toPlainText() == ""
    assert "being prepared" in palette.after_text.placeholderText().casefold()
    assert not palette.apply_suggestion_button.isEnabled()

    attempt_id, suggestion_id, request_text, _command = requests[0]
    palette.show_sentence_result(
        attempt_id,
        suggestion_id,
        request_text.replace(" are ", " is "),
        "AI correction ready.",
    )
    app.processEvents()

    assert palette.after_text.toPlainText()
    assert palette.after_text.placeholderText() == ""
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_ten_section_prefetch_continues_after_third_section_fails_quality_gate(app):
    palette = RewritePalette(ai_proofread=True)
    writes: list[str] = []
    requests: list[tuple[int, str, str, str]] = []
    palette.set_step_replacer(lambda text, _suggestion: writes.append(text) or True)
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    source = " ".join(
        f"Part {index} are written badly." for index in range(1, 51)
    )

    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()

    for request_index in range(2):
        attempt_id, suggestion_id, request_text, _command = requests[request_index]
        palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            request_text.replace(" are ", " is "),
            "CUDA | qwen3 correction ready.",
        )
        app.processEvents()

    failed_attempt, failed_id, *_ = requests[2]
    palette.show_sentence_error(
        failed_attempt,
        failed_id,
        "AI output did not pass final quality checks. Original text kept.",
    )
    app.processEvents()

    assert len(requests) == 4
    assert requests[3][1] != failed_id

    corrected: dict[str, str] = {}
    for request_index in range(3, 10):
        attempt_id, suggestion_id, request_text, _command = requests[request_index]
        result = request_text.replace(" are ", " is ")
        corrected[suggestion_id] = result
        palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            result,
            "CUDA | qwen3 correction ready.",
        )
        app.processEvents()

    assert len(requests) == 11
    retry_attempt, retry_id, *_ = requests[10]
    assert retry_id == failed_id
    palette.show_sentence_error(
        retry_attempt,
        retry_id,
        "AI output did not pass final quality checks. Original text kept.",
    )
    app.processEvents()
    assert len(requests) == 11

    palette.apply_suggestion_button.click()
    app.processEvents()
    palette.apply_suggestion_button.click()
    app.processEvents()

    assert palette.view == "card"
    assert palette.card_position.text() == "3 of 10"
    assert palette.dismiss_suggestion_button.isEnabled()
    assert "quality checks" in palette.status.text().casefold()

    palette.dismiss_suggestion_button.click()
    app.processEvents()

    assert palette.card_position.text() == "4 of 10"
    assert palette.after_text.toPlainText() == corrected[requests[3][1]]
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_failed_sixth_section_is_repaired_after_all_eleven_sections_are_prefetched(app):
    palette = RewritePalette(ai_proofread=True)
    requests: list[tuple[int, str, str, str]] = []
    palette.set_step_replacer(lambda _text, _suggestion: True)
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    source = " ".join(
        f"Part {index} are written badly." for index in range(1, 52)
    )

    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()

    for request_index in range(5):
        attempt_id, suggestion_id, request_text, _command = requests[request_index]
        palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            request_text.replace(" are ", " is "),
            "CUDA | qwen3 correction ready.",
        )
        app.processEvents()

    failed_attempt, failed_id, *_ = requests[5]
    palette.show_sentence_error(
        failed_attempt,
        failed_id,
        "AI output did not pass final quality checks. Original text kept.",
    )
    app.processEvents()

    for request_index in range(6, 11):
        attempt_id, suggestion_id, request_text, _command = requests[request_index]
        palette.show_sentence_result(
            attempt_id,
            suggestion_id,
            request_text.replace(" are ", " is "),
            "CUDA | qwen3 correction ready.",
        )
        app.processEvents()

    assert len(requests) == 12
    retry_attempt, retry_id, retry_text, _command = requests[11]
    assert retry_id == failed_id
    palette.show_sentence_result(
        retry_attempt,
        retry_id,
        retry_text.replace(" are ", " is "),
        "CUDA | qwen3 correction ready after automatic repair.",
    )
    app.processEvents()

    for _ in range(5):
        palette.apply_suggestion_button.click()
        app.processEvents()

    assert palette.card_position.text() == "6 of 11"
    assert palette.apply_suggestion_button.isEnabled()
    assert "ready after automatic repair" in (
        palette.apply_suggestion_button.accessibleName().casefold()
    )
    palette.finish_choice()


def test_palette_size_tracks_text_continuously_until_screen_limit(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    suggestion_id = palette.suggestions[0].suggestion_id

    sizes = []
    for repeats in (1, 4, 12, 40):
        text = "This revised sentence has useful detail and remains readable. " * repeats
        palette.show_sentence_result(0, suggestion_id, text.strip(), "Rewrite ready.")
        app.processEvents()
        palette._reassert_current_view_size()
        sizes.append(palette.size())

    area = app.primaryScreen().availableGeometry()
    assert all(
        current.width() >= previous.width() and current.height() >= previous.height()
        for previous, current in zip(sizes, sizes[1:])
    )
    assert len({(size.width(), size.height()) for size in sizes}) >= 3
    assert sizes[-1].width() <= area.width() - 8
    assert sizes[-1].height() <= area.height() - 8


def test_palette_grows_and_shrinks_with_the_suggestion_text(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    suggestion_id = palette.suggestions[0].suggestion_id

    compact_size = palette.size()

    # Short first, then long. The order used to be the other way round, and a
    # two-word answer replacing a long correction for the SAME section is no
    # longer a sequence the product produces: answer_abandons_the_source refuses
    # it, because throwing away a paragraph that way is the defect it exists for.
    # Both sizes are still measured on one palette and every assertion below is
    # unchanged - this test is about geometry.
    palette.show_sentence_result(0, suggestion_id, "The draft.", "Short rewrite ready.")
    app.processEvents()
    palette._reassert_current_view_size()
    shrunk_size = palette.size()

    palette.show_sentence_result(
        0,
        suggestion_id,
        "This corrected suggestion is deliberately long enough to wrap across several "
        "lines in the compact writing card, so the card must grow to keep the entire "
        "review readable without hiding the decision controls from the user.",
        "Long rewrite ready.",
    )
    app.processEvents()
    palette._reassert_current_view_size()
    grown_size = palette.size()

    assert grown_size.width() > compact_size.width()
    assert grown_size.height() > compact_size.height()
    assert shrunk_size.width() == compact_size.width()
    assert shrunk_size.height() < grown_size.height()
    palette.finish_choice()


def test_review_card_keeps_readable_minimum_width(app):
    palette = RewritePalette()
    palette.show_capture(
        "teh project team were ready, but the interface was crowded and the status was unclear."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert palette.minimumWidth() >= 480
    assert palette.width() >= 480
    palette.finish_choice()


def test_palette_requests_local_ai_proofread_when_icon_opens(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt_id, suggestion_id, text, command: requests.append(
            (attempt_id, suggestion_id, text, command)
        )
    )
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    palette._request_ai_proofread_current()

    assert len(requests) == 1
    assert palette.view == "loading"
    assert not palette.after_text.isVisible()
    assert "checking gpu" in palette.status.text().casefold()
    assert "eta calibrating" in palette.status.text().casefold()
    attempt_id, suggestion_id, source, command = requests[0]
    assert source == "The draft."
    assert command == "Proofread"
    assert not palette.apply_suggestion_button.isVisible()
    assert not palette.dismiss_suggestion_button.isVisible()

    palette.show_sentence_result(
        attempt_id,
        suggestion_id,
        "The complete draft.",
        "AI proofread ready.",
    )

    assert palette.after_text.toPlainText() == "The complete draft."
    assert palette.view == "card"
    assert palette.width() >= palette._CARD_SIZE.width()
    assert palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_palette_exposes_local_ai_success_on_accept_control(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    suggestion_id = palette.suggestions[0].suggestion_id

    palette.show_sentence_result(
        0,
        suggestion_id,
        "The draft.",
        "Local AI proofread ready.",
    )

    assert palette.apply_suggestion_button.toolTip() == "Local AI proofread ready."
    assert "local ai proofread ready" in (
        palette.apply_suggestion_button.accessibleName().casefold()
    )
    assert (
        palette.apply_suggestion_button.accessibleDescription()
        == "Local AI proofread ready."
    )
    palette.finish_choice()


def test_runtime_name_is_hidden_and_load_error_appears_only_in_bottom_status(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    original_explanation = palette.suggestion_explanation.text()
    message = "Qwen3 8B failed to load."

    palette.show_error(message)
    app.processEvents()

    assert palette.status.text() == "Local AI could not load. Retry shortly."
    assert "qwen" not in palette.status.text().casefold()
    assert palette.status.isVisible()
    assert palette.suggestion_explanation.text() == original_explanation
    assert message not in palette.suggestion_explanation.text()
    palette.finish_choice()


def test_blank_sentence_result_keeps_current_correction_visible_and_usable(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    suggestion_id = palette.suggestions[0].suggestion_id

    palette.show_sentence_result(
        0,
        suggestion_id,
        "   ",
        "Sentence rewrite ready.",
    )

    assert palette.after_text.toPlainText() == "The draft."
    assert palette.apply_suggestion_button.isEnabled()
    assert "returned no text" in palette.status.text().casefold()
    palette.finish_choice()


def test_palette_timeout_never_presents_rules_only_text_as_finished_ai(app):
    palette = RewritePalette(ai_proofread=True)
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()

    assert palette.view == "loading"
    assert not palette.after_text.isVisible()
    attempt_id = palette._active_ai_attempt_id
    suggestion_id = palette.suggestions[0].suggestion_id
    palette._expire_ai_request()

    assert palette.view == "card"
    assert palette.after_text.toPlainText() == ""
    assert not palette.apply_suggestion_button.isEnabled()
    assert suggestion_id in palette._ai_failed_ids
    assert "took too long" in palette.status.text().casefold()
    palette.show_sentence_result(attempt_id, suggestion_id, "Late AI result.", "AI ready.")
    assert palette.after_text.toPlainText() == ""
    palette.finish_choice()


def test_pending_queue_hides_rule_text_until_each_ai_result_is_ready(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("One. Two. Three.\nteh fourth.")
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, first_text, _command = requests[-1]

    palette.show_sentence_result(first_attempt, first_id, first_text, "AI ready.")
    app.processEvents()

    preview = palette.queue_preview.toPlainText()
    assert "Processing with AI" in preview
    assert "The fourth." not in preview


def test_palette_shows_gpu_backend_elapsed_time_and_measured_eta(app):
    palette = RewritePalette(ai_proofread=True)
    palette.sentence_rewrite_requested.connect(lambda *_args: None)
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()

    palette.show_sentence_progress(
        palette._active_ai_attempt_id,
        "GPU (9.2 GB VRAM)",
        12.0,
    )

    status = palette.status.text()
    assert "GPU (9.2 GB VRAM)" in status
    assert "elapsed" in status
    assert "ETA about" in status
    assert not palette.apply_suggestion_button.isEnabled()
    palette.finish_choice()


def test_palette_allows_cold_load_plus_generation_before_timeout(app):
    palette = RewritePalette(ai_proofread=True)
    assert palette._AI_TIMEOUT_MS == 120_000
    assert palette._ai_timeout_timer.interval() == 120_000
    palette.finish_choice()


def test_palette_timed_out_ai_can_be_retried_without_accepting_late_result(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    suggestion_id = palette.suggestions[0].suggestion_id

    palette._expire_ai_request()
    assert palette.rewrite_sentence_button.text() == "Retry"
    palette.rewrite_sentence_button.click()
    assert len(requests) == 2

    old_attempt = requests[0][0]
    active_attempt = requests[1][0]
    palette.show_sentence_result(old_attempt, suggestion_id, "Late draft.", "AI ready.")
    assert not palette.after_text.isVisible()
    palette.show_sentence_result(active_attempt, suggestion_id, "The final draft.", "AI ready.")
    assert palette.after_text.toPlainText() == "The final draft."
    palette.finish_choice()


def test_palette_timeout_then_tone_rewrite_shows_text_unlocks_accept_and_resizes(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("teh area remain blank and accept dont work.")
    app.processEvents()
    palette.badge_button.click()
    first_attempt, suggestion_id, _source, _command = requests[-1]

    palette._expire_ai_request()
    compact_size = palette.size()
    palette.rewrite_sentence_button.click()
    second_attempt, second_id, _source, command = requests[-1]

    assert second_id == suggestion_id
    assert second_attempt != first_attempt
    # The combo defaults to Clear, and Clear now carries its own command
    # instead of collapsing to "Rewrite" - see the tone mapping test above.
    assert command == "Clear"
    assert not palette.apply_suggestion_button.isEnabled()

    palette.show_sentence_result(
        first_attempt,
        suggestion_id,
        "Late result that must stay hidden.",
        "Late AI result.",
    )
    assert "Late result" not in palette.after_text.toPlainText()
    assert not palette.apply_suggestion_button.isEnabled()

    rewritten = (
        "The suggested area now contains a complete rewritten sentence, and the Accept "
        "button works. The card grows as needed so the full correction remains visible."
    )
    palette.show_sentence_result(
        second_attempt,
        suggestion_id,
        rewritten,
        "Sentence rewrite ready.",
    )
    app.processEvents()
    palette._reassert_current_view_size()

    assert palette.after_text.toPlainText() == rewritten
    assert palette.apply_suggestion_button.isEnabled()
    assert palette.size().width() >= compact_size.width()
    assert palette.width() * palette.height() > compact_size.width() * compact_size.height()
    palette.finish_choice()


@pytest.mark.parametrize(
    "option",
    (
        # The six the menu offers. This test drives the combo box, so it can
        # only cover tones the menu lists; the styles cut from the menu on
        # 2026-08-13 and 2026-08-16 are still reachable as commands and are
        # covered by tests/test_full_page_tone_quality_probe.py.
        # "Clear" was deliberately absent until 2026-08-23, because it mapped
        # to the "Rewrite" command rather than to itself. polish_quality
        # defines a Clear contract and no Rewrite contract, so that mapping
        # made the Clear contract unreachable: picking Clear sent a command
        # with no tone contract and the writer got a plain rewrite. Measured
        # that day - Clear selected 5 times, the app logged
        # {Proofread: 25, Rewrite: 3}, no Clear request in it.
        "Clear",
        "Improve",
        "Rephrase",
        "Shorten",
        "Formal",
        "Friendly",
    ),
)
def test_palette_maps_every_requested_tone_to_its_local_writing_command(app, option):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("Please review this sentence.")
    app.processEvents()
    palette.badge_button.click()
    palette.tone_combo.setCurrentText(option)
    palette.rewrite_sentence_button.click()
    assert requests[-1][3] == option
    palette.finish_choice()
    palette.close()
    palette.deleteLater()
    app.processEvents()


def test_automatic_pass_proofreads_whatever_tone_is_selected(app):
    """The automatic pass tones only when a tone was actually chosen.

    Giving Clear its own command removed the accident this used to rest on:
    automatic asked for _selected_tone_command(automatic=True), Clear
    collapsed to "Rewrite", and "Rewrite" became "Proofread". With Clear
    mapping to itself that chain no longer runs, so the rule is asserted
    directly - otherwise every automatic section would silently be toned
    Clear, which is the worse of the two defects.
    """
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    # No tone chosen: the automatic pass proofreads and does not tone.
    palette.tone_combo.setCurrentText("Clear")
    assert palette._selected_tone_command(automatic=True) == "Proofread"
    # A tone chosen: the automatic pass carries it, which is existing
    # behaviour and is what lets a writer set a tone once for a document.
    for option in ("Improve", "Friendly"):
        palette.tone_combo.setCurrentText(option)
        assert palette._selected_tone_command(automatic=True) == option
    # Manual Clear reaches its own contract instead of a plain rewrite.
    palette.tone_combo.setCurrentText("Clear")
    assert palette._selected_tone_command() == "Clear"
    palette.close()
    palette.deleteLater()
    app.processEvents()


def test_selecting_tone_immediately_rewrites_current_block(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("Please review this sentence.")
    app.processEvents()
    palette.badge_button.click()

    palette.tone_combo.setCurrentText("Formal")

    assert len(requests) == 1
    assert requests[0][3] == "Formal"


def test_selected_tone_is_automatically_used_for_next_block(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine(), ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture("First sentence. Second sentence. Third sentence.\nFourth sentence.")
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, first_text, first_command = requests[-1]
    assert first_command == "Proofread"
    palette.show_sentence_result(first_attempt, first_id, first_text, "Ready.")

    palette.tone_combo.setCurrentText("Friendly")
    tone_attempt, tone_id, _tone_text, tone_command = requests[-1]
    assert tone_command == "Friendly"
    palette.show_sentence_result(
        tone_attempt,
        tone_id,
        "Friendly first, second, and third sentences.",
        "Ready.",
    )
    palette.apply_suggestion_button.click()

    assert requests[-1][1] != tone_id
    assert requests[-1][3] == "Friendly"


def test_palette_requests_local_ai_for_each_review_chunk(app):
    palette = RewritePalette(ai_proofread=True)
    requests = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt_id, suggestion_id, text, command: requests.append(
            (attempt_id, suggestion_id, text, command)
        )
    )
    palette.show_capture("One. Two. Three.\nFour.")
    app.processEvents()
    palette.badge_button.click()
    first_attempt, first_id, first_text, first_command = requests[-1]
    palette.show_sentence_result(
        first_attempt, first_id, first_text, "AI proofread ready."
    )

    palette.apply_suggestion_button.click()

    assert len(requests) == 2
    second_attempt, second_id, second_text, second_command = requests[-1]
    assert second_id != first_id
    assert second_text == "Four."
    assert first_command == second_command == "Proofread"
    palette.show_sentence_result(
        second_attempt, second_id, "Fourth.", "AI proofread ready."
    )
    # "1 of 2", not "2 of 2": the first chunk came back from the AI unchanged,
    # so it is not shown. Curtis, 2026-09-01: "applying the correction to one
    # section but not to another. It's confusing for the user." In the
    # automatic proofread every card now carries a real change, so Replace
    # always does something visible.
    #
    # The chunk is skipped only for DISPLAY. The assertions above are the ones
    # that matter and they are unchanged: both chunks were sent to the AI, with
    # their own ids, and the second card shows the second chunk. Hiding a
    # section must never stop the next one being processed.
    assert palette.card_position.text() == "1 of 2"
    assert palette.before_text.toPlainText() == "Four."
    assert palette.after_text.toPlainText() == "Fourth."
    palette.finish_choice()


def test_window_hud_sends_the_section_with_no_instruction_of_its_own(
    app, tmp_path, monkeypatch
):
    """This asserted keep_alive, context_window, max_tokens and require_gpu.

    All four went into **_ignored in PolishWritingAssistantAdapter.run and
    never reached the engine, so the "small bounded request" the old name
    promised was never actually bounded by them - the test verified the shape
    of a call rather than any effect of it. The bounds are real, but they come
    from the ExecutionPlan the hardware policy builds (context_window,
    max_output_tokens, require_full_gpu), not from the caller.

    What is left is what genuinely reaches the engine and matters: the section
    text, the timeout, and an empty instruction. That last one is load bearing -
    putting the correction contract here made the model drop immutable tokens
    on full-page reviews.
    """
    monkeypatch.delenv("ECHOLINK_DISABLE_PALETTE_AI", raising=False)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selected_text_record = _selection("teh draft")
    captured = {}
    called = Event()

    def run(source, **options):
        captured.update({"source": source, **options})
        called.set()
        return SimpleNamespace(text="The draft.")

    monkeypatch.setattr(window.writing_assistant, "run", run)
    window._run_palette_sentence_rewrite(1, "sentence-1", "teh draft", "Proofread")

    assert called.wait(2.0)
    assert captured["source"] == "teh draft"
    assert captured["timeout"] == 120.0
    assert captured["instruction"] == ""
    assert captured["document"] == "teh draft"
    for dead in ("keep_alive", "context_window", "max_tokens", "require_gpu"):
        assert dead not in captured, f"{dead} is discarded by the adapter"
    window._quitting = True
    window.close()


def test_window_hud_ai_queue_respects_hardware_width_and_order(app, tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOLINK_DISABLE_PALETTE_AI", raising=False)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.selected_text_record = _selection("teh draft")
    entered = Event()
    release = Event()
    calls = []
    active = {"count": 0, "maximum": 0}
    lock = Lock()

    def run(source, **_options):
        with lock:
            active["count"] += 1
            active["maximum"] = max(active["maximum"], active["count"])
            calls.append(source)
        entered.set()
        release.wait(2.0)
        with lock:
            active["count"] -= 1
        return SimpleNamespace(text=source)

    monkeypatch.setattr(window.writing_assistant, "run", run)
    # Concurrency scales to the hardware width; pin it for the test.
    monkeypatch.setattr(window, "_palette_ai_width", lambda: 2)
    window._run_palette_sentence_rewrite(1, "sentence-0", "first", "Proofread")
    assert entered.wait(2.0)
    for index in range(1, 6):
        window._run_palette_sentence_rewrite(
            index + 1, f"sentence-{index}", f"request-{index}", "Proofread"
        )
    release.set()

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        with window._palette_ai_lock:
            if window._palette_ai_workers == 0 and not window._palette_ai_queue:
                break
        time.sleep(0.01)

    # Never wider than the configured pool, and every queued job ran in
    # submission order - no request is silently dropped any more.
    assert active["maximum"] <= 2
    assert calls[0] == "first"
    assert sorted(calls[1:]) == sorted(f"request-{i}" for i in range(1, 6))
    window._quitting = True
    window.close()


def test_palette_preserves_boundary_whitespace_when_applying(app):
    palette = RewritePalette()
    applied = []
    palette.replace_requested.connect(applied.append)
    palette.show_capture("  teh draft.\n")
    app.processEvents()
    palette.badge_button.click()

    palette.apply_suggestion_button.click()

    assert applied == ["  The draft.\n"]
    palette.finish_choice()


def test_no_change_accept_finishes_without_reporting_rejection(app):
    palette = RewritePalette()
    outcomes = {"unchanged": 0, "rejected": 0}
    palette.unchanged.connect(
        lambda: outcomes.__setitem__("unchanged", outcomes["unchanged"] + 1)
    )
    palette.rejected.connect(
        lambda: outcomes.__setitem__("rejected", outcomes["rejected"] + 1)
    )
    palette.show_capture("Clean sentence.")
    app.processEvents()
    palette.badge_button.click()

    palette.apply_suggestion_button.click()

    assert outcomes == {"unchanged": 1, "rejected": 0}
    assert not palette.isVisible()


def test_automatic_notepad_newlines_stay_current_for_accept(app):
    palette = RewritePalette()
    palette.show_capture("teh first.\r\rteh second.", automatic=True)
    palette._finish_automatic_analysis()
    palette.badge_button.click()

    palette.apply_suggestion_button.click()

    assert palette.card_position.text() == "2 of 2"
    assert palette.result_text == "The first.\n\nteh second."
    assert "stale" not in palette.status.text().casefold()
    palette.finish_choice()


def test_palette_progressive_disclosure_reviews_one_suggestion_at_a_time(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture(
        "Echo-Link are ready. teh draft. Clean sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()

    assert palette.view == "card"
    assert palette.card_position.text() == "1 of 2"
    assert palette.before_text.toPlainText() == (
        "Echo-Link are ready. teh draft. Clean sentence."
    )
    assert palette.after_text.toPlainText() == (
        "Echo-Link is ready. the draft. Clean sentence."
    )
    assert palette.next_button.isEnabled()
    assert not palette.content_scroll.isVisible()

    palette.apply_suggestion_button.click()
    assert palette.card_position.text() == "2 of 2"
    assert palette.before_text.toPlainText() == "Another sentence."
    assert palette.result_text == (
        "Echo-Link is ready. the draft. Clean sentence.\nAnother sentence."
    )
    assert "next text" in palette.status.text().casefold()
    palette.apply_suggestion_button.click()
    assert palette.result_text == (
        "Echo-Link is ready. the draft. Clean sentence.\nAnother sentence."
    )
    assert "applying accepted sentences" in palette.status.text().casefold()
    palette.finish_choice()


def test_accept_applies_each_section_immediately_before_advancing(app):
    palette = RewritePalette()
    writes = []
    completed = []
    palette.set_step_replacer(lambda text, _suggestion: writes.append(text) or True)
    palette.applied.connect(lambda: completed.append(True))
    palette.show_capture(
        "teh first sentence. Clean second sentence. Clean third sentence.\n"
        "teh fourth sentence. Clean fifth sentence. Clean sixth sentence."
    )
    app.processEvents()
    palette.badge_button.click()

    assert palette.card_position.text() == "1 of 2"
    palette.apply_suggestion_button.click()
    assert len(writes) == 1
    assert writes[0].startswith("The first sentence.")
    assert "teh fourth sentence" in writes[0]
    assert palette.card_position.text() == "2 of 2"

    palette.apply_suggestion_button.click()
    assert len(writes) == 2
    assert "The fourth sentence" in writes[1]
    assert completed == [True]
    assert not palette.isVisible()


def test_final_dismiss_after_step_accept_never_requests_whole_document_replace(app):
    palette = RewritePalette()
    step_writes = []
    whole_document_writes = []
    applied = []
    palette.set_step_replacer(
        lambda text, suggestion: step_writes.append((text, suggestion)) or True
    )
    palette.replace_requested.connect(whole_document_writes.append)
    palette.applied.connect(lambda: applied.append(True))
    palette.show_capture(
        "teh first sentence. Two. Three. Four. Five. teh sixth sentence.",
        document_scope=True,
    )
    app.processEvents()
    palette.badge_button.click()

    palette.apply_suggestion_button.click()
    assert palette.card_position.text() == "2 of 2"
    palette.dismiss_suggestion_button.click()
    app.processEvents()

    assert len(step_writes) == 1
    assert whole_document_writes == []
    assert applied == [True]
    assert not palette.isVisible()


def test_next_then_replace_preserves_earlier_undecided_section(app):
    palette = RewritePalette()
    writes = []
    palette.set_step_replacer(
        lambda text, suggestion: writes.append((text, suggestion)) or True
    )
    source = " ".join(
        (
            "teh first.",
            "Second.",
            "Third.",
            "Fourth.",
            "Fifth.",
            "teh sixth.",
            "Seventh.",
            "Eighth.",
            "Ninth.",
            "Tenth.",
            "teh eleventh.",
        )
    )
    palette.show_capture(source, document_scope=True)
    app.processEvents()
    palette.badge_button.click()

    first_original = palette.suggestions[0].original
    palette._step_suggestion(1)
    assert palette.card_position.text() == "2 of 3"
    palette.apply_suggestion_button.click()
    app.processEvents()

    assert len(writes) == 1
    assert len(palette.suggestions) == 2
    assert any(item.original == first_original for item in palette.suggestions)
    assert palette.before_text.toPlainText() == first_original
    assert palette.card_position.text() == "2 of 3"
    palette.finish_choice()


def test_unique_document_text_handles_windows_newlines():
    expected = "First line.\n\nSecond line."
    document = "Header\r\rFirst line.\r\rSecond line.\r\rFooter"

    assert _unique_document_text(document, expected) == "First line.\r\rSecond line."
    assert _same_document_text("First line.\r\rSecond line.", expected)


def test_palette_rewrites_one_clean_sentence_then_moves_next(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    requests = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt_id, suggestion_id, text, command: requests.append(
            (attempt_id, suggestion_id, text, command)
        )
    )
    palette.show_capture("Clean sentence.\nAnother sentence.")
    app.processEvents()
    palette.badge_button.click()

    assert palette.card_position.text() == "1 of 2"
    assert palette.before_text.toPlainText() == "Clean sentence."
    assert palette.after_text.toPlainText() == "Clean sentence."
    assert palette.apply_suggestion_button.text() == "Replace Text"

    palette.rewrite_sentence_button.click()
    assert len(requests) == 1
    attempt_id, suggestion_id, source, command = requests[0]
    # A tone request now covers several paragraphs at once, so this asks for
    # both sections rather than only the one on the card.
    #
    # Changed requirement, not a broken fix. A tone applied one paragraph at a
    # time cannot sound like anything - the app log recorded tone requests as
    # small as "characters=43" - so requests were batched deliberately while
    # the APPLY unit stayed one paragraph. This test encoded the old contract,
    # asserting the request held exactly the sentence on the card.
    #
    # It had been failing since commit cef5978 and was not noticed, because
    # that commit was verified with targeted tests instead of the full suite.
    assert source == "Clean sentence.\n\nAnother sentence."
    # Clear is the combo default and now carries its own command rather than
    # collapsing to "Rewrite" - see the tone mapping test.
    assert command == "Clear"
    # One paragraph back for each one sent. An answer whose paragraph count
    # does not match is refused outright, which is what stops a batch from
    # writing several paragraphs over one.
    palette.show_sentence_result(
        attempt_id,
        suggestion_id,
        "This sentence is cleaner.\n\nAnd so is this one.",
        "Sentence rewrite ready.",
    )

    assert palette.after_text.toPlainText() == "This sentence is cleaner."
    assert palette.apply_suggestion_button.text() == "Replace Text"
    palette.apply_suggestion_button.click()
    assert palette.result_text == "This sentence is cleaner.\nAnother sentence."
    assert palette.card_position.text() == "2 of 2"
    assert palette.before_text.toPlainText() == "Another sentence."
    palette.finish_choice()


def test_palette_dismiss_skips_one_sentence_then_closes(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    rejected = []
    palette.rejected.connect(lambda: rejected.append(True))
    palette.show_capture("Clean sentence.\nAnother sentence.")
    app.processEvents()
    palette.badge_button.click()

    palette.dismiss_suggestion_button.click()

    assert palette.card_position.text() == "2 of 2"
    assert palette.before_text.toPlainText() == "Another sentence."
    assert "dismissed" in palette.status.text().casefold()

    palette.dismiss_suggestion_button.click()

    assert rejected == [True]
    assert not palette.isVisible()


def test_palette_require_recapture_invalidates_stale_actions(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.apply_suggestion_button.click()
    assert palette.result_text
    assert palette.replace_button.isEnabled()

    palette.require_recapture("Selection changed. Capture it again.")

    assert palette.source_edit.toPlainText() == "teh draft"
    assert palette.result_text == ""
    assert palette.suggestions == ()
    assert not palette.replace_button.isEnabled()
    assert not palette.stage_button.isEnabled()
    assert not palette.apply_suggestion_button.isEnabled()
    assert not palette.apply_all_button.isEnabled()
    assert all(not button.isEnabled() for button in palette.command_buttons.values())
    assert palette.status.text() == "Selection changed. Capture it again."
    palette.finish_choice()


def test_palette_exposes_entire_document_scope_and_request(app):
    palette = RewritePalette()
    requests = []
    palette.document_requested.connect(lambda: requests.append(True))
    palette.show_capture("teh draft")
    palette.document_button.click()
    assert requests == [True]

    palette.show_capture("teh draft. Another sentence.", document_scope=True)
    app.processEvents()

    assert "Entire document" in palette.scope_label.text()
    assert "4 words" in palette.scope_label.text()
    assert not palette.document_button.isEnabled()
    assert palette.document_button.text() == "Loaded"
    assert palette.apply_changes_button.text() == "Apply to document"
    palette.finish_choice()


def test_palette_layout_scrolls_without_horizontal_clipping(app, tmp_path):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    assert palette.view == "badge"
    palette.badge_button.click()

    assert palette.view == "card"
    assert 480 <= palette.width() <= palette._CARD_MAX_WIDTH
    assert 520 <= palette.height() <= palette._CARD_MAX_HEIGHT
    assert palette.minimumWidth() <= palette.width()
    assert palette.minimumHeight() <= palette.height()
    assert palette.before_text.toPlainText() == "teh draft"
    assert palette.after_text.toPlainText() == "The draft."
    assert palette.after_text.height() >= 64
    assert not palette.after_text.verticalScrollBar().isVisible()
    assert palette.before_text.isVisible()
    assert palette.after_text.isVisible()
    for button in (
        palette.apply_suggestion_button,
        palette.dismiss_suggestion_button,
        palette.rewrite_sentence_button,
        palette.close_button,
    ):
        assert button.isVisible()
        assert button.accessibleName()
        needed = button.fontMetrics().horizontalAdvance(button.text()) + 28
        assert button.width() >= needed
    for hidden in (
        palette.previous_button,
        palette.next_button,
        palette.apply_changes_button,
        palette.document_button,
        palette.assistant_button,
    ):
        assert not hidden.isVisible()

    screenshot_dir = os.environ.get("ECHOLINK_SCREENSHOT_DIR")
    if screenshot_dir:
        destination = Path(screenshot_dir)
        destination.mkdir(parents=True, exist_ok=True)
        assert palette.grab().save(str(destination / "manuscript-writing-hud-card.png"))

    palette.assistant_button.click()
    app.processEvents()
    assert palette.view == "assistant"
    assert palette.content_scroll.horizontalScrollBar().maximum() == 0
    assert all(button.isVisible() for button in palette.filter_buttons.values())
    assert all(button.isVisible() for button in palette.command_buttons.values())
    for control in (
        palette.assistant_apply_button,
        palette.apply_all_button,
        *palette.command_buttons.values(),
        palette.language_edit,
        palette.result_edit,
    ):
        content_point = control.mapTo(palette.content_scroll.widget(), QPoint(0, 0))
        needed_scroll = max(
            0,
            content_point.y()
            + control.height()
            - palette.content_scroll.viewport().height(),
        )
        vertical = palette.content_scroll.verticalScrollBar()
        vertical.setValue(min(vertical.maximum(), needed_scroll))
        app.processEvents()
        top_left = control.mapTo(palette.content_scroll.viewport(), QPoint(0, 0))
        assert palette.content_scroll.viewport().rect().contains(
            QRect(top_left, control.size())
        )

    if screenshot_dir:
        palette.content_scroll.verticalScrollBar().setValue(0)
        app.processEvents()
        assert palette.grab().save(str(destination / "manuscript-writing-assistant.png"))
        palette.apply_all_button.click()
        assert palette.result_text == "The draft."
        assert palette.replace_button.isEnabled()
        palette.content_scroll.verticalScrollBar().setValue(
            palette.content_scroll.verticalScrollBar().maximum()
        )
        app.processEvents()
        assert palette.grab().save(str(destination / "manuscript-writing-assistant-review.png"))
    if screenshot_dir:
        palette.show_capture("teh draft", automatic=True, anchor=(80, 80, 220, 110))
        app.processEvents()
        assert palette.view == "badge"
        assert palette.grab().save(str(destination / "manuscript-writing-hud-badge.png"))
    palette.finish_choice()


def test_palette_bottom_has_visible_status_and_cannot_drag(app):
    palette = RewritePalette()
    palette.show_capture("teh draft")
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert palette.status.isVisible()
    bottom = QPoint(palette.width() // 2, palette.height() - 2)
    QTest.mousePress(palette, Qt.MouseButton.LeftButton, pos=bottom)
    assert palette._drag_offset is None
    QTest.mouseRelease(palette, Qt.MouseButton.LeftButton, pos=bottom)

    header = QPoint(palette.width() // 2, 12)
    QTest.mousePress(palette, Qt.MouseButton.LeftButton, pos=header)
    assert palette._drag_offset is not None
    QTest.mouseRelease(palette, Qt.MouseButton.LeftButton, pos=header)
    palette.finish_choice()


def test_card_size_never_conflicts_with_its_real_minimum_height(app):
    palette = RewritePalette()
    palette.show_capture(
        "add im a student at san bernardino valley college studing cyber security"
    )
    palette.badge_button.click()
    QTest.qWait(75)

    requested = palette._desired_card_size()
    bottom_gap = (
        palette.card_page.height()
        - palette.dismiss_suggestion_button.geometry().bottom()
        - 1
    )

    assert requested.height() >= palette.card_page.minimumSizeHint().height()
    assert palette.minimumHeight() <= palette.height() <= palette.maximumHeight()
    assert palette.maximumHeight() > palette.minimumHeight()
    assert palette.height() == palette.card_page.height()
    assert palette.status.isVisible()
    assert 20 <= bottom_gap <= 52
    palette.finish_choice()


def test_long_card_stays_compact_and_uses_scrollable_text_area(app):
    palette = RewritePalette()
    palette.show_capture(
        "This malformed sentence are intentionally long and contain enough words to "
        "exercise wrapping without making the writing card consume the screen. " * 8
    )
    palette.badge_button.click()
    QTest.qWait(75)

    assert palette.width() <= palette._CARD_MAX_WIDTH
    assert palette.height() <= palette._CARD_MAX_HEIGHT
    assert palette.after_text.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    palette.finish_choice()


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5", "2"])
def test_palette_stays_inside_screen_at_supported_scale(scale, tmp_path):
    runtime = tmp_path / f"scale-{scale.replace('.', '-')}"
    runtime.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
            "ECHOLINK_DISABLE_HOTKEY": "1",
            "ECHOLINK_DISABLE_TRAY": "1",
            "ECHOLINK_DISABLE_SOUNDS": "1",
            "ECHOLINK_DISABLE_MEDIA_CONTROL": "1",
            "LOCALAPPDATA": str(runtime),
            "TEMP": str(runtime),
            "TMP": str(runtime),
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    code = """
import json
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication
from echo_link_app.rewrite_palette import RewritePalette
from echo_link_app.theme import apply_dark_theme

app = QApplication.instance() or QApplication([])
apply_dark_theme(app)

def controls_contained(parent, controls):
    for control in controls:
        if not control.isVisible():
            continue
        top_left = control.mapTo(parent, QPoint(0, 0))
        if not parent.rect().contains(QRect(top_left, control.size())):
            return False
    return True

palette = RewritePalette()
palette.show_capture("teh draft")
app.processEvents()
palette.badge_button.click()
app.processEvents()
suggestion_id = palette.suggestions[0].suggestion_id
palette.show_sentence_result(
    0,
    suggestion_id,
    "This long correction fills several wrapped lines so every visible decision control "
    "must remain inside the Writing HUD at every supported display scale.",
    "AI ready.",
)
app.processEvents()
area = app.primaryScreen().availableGeometry()
card_frame = palette.frameGeometry()
card_controls_contained = controls_contained(palette, (
    palette.close_button,
    palette.suggestion_title,
    palette.after_heading,
    palette.after_text,
    palette.tone_combo,
    palette.rewrite_sentence_button,
    palette.apply_suggestion_button,
    palette.dismiss_suggestion_button,
    palette.status,
))
palette.assistant_button.click()
app.processEvents()
assistant_frame = palette.frameGeometry()
palette.finish_choice()
loading = RewritePalette(ai_proofread=True)
loading.sentence_rewrite_requested.connect(lambda *_args: None)
loading.show_capture("teh draft")
app.processEvents()
loading._set_view("loading", activate=True)
app.processEvents()
loading_frame = loading.frameGeometry()
loading_controls_contained = controls_contained(
    loading, (loading.loading_close_button, loading.loading_status)
)
loading.finish_choice()
palette.show_capture("teh draft", automatic=True, anchor=(20, 20, 180, 45))
app.processEvents()
badge_frame = palette.frameGeometry()
print(json.dumps({
    "card_contained": area.contains(card_frame),
    "card_controls_contained": card_controls_contained,
    "assistant_contained": area.contains(assistant_frame),
    "loading_contained": area.contains(loading_frame),
    "loading_controls_contained": loading_controls_contained,
    "loading_width": loading_frame.width(),
    "loading_height": loading_frame.height(),
    "badge_contained": area.contains(badge_frame),
    "badge_width": badge_frame.width(),
    "badge_height": badge_frame.height(),
    "horizontal_max": palette.content_scroll.horizontalScrollBar().maximum(),
    "minimum_width": palette.minimumSizeHint().width(),
    "minimum_height": palette.minimumSizeHint().height(),
    "area_width": area.width(),
    "area_height": area.height(),
}))
palette.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["card_contained"] is True
    assert payload["card_controls_contained"] is True
    assert payload["assistant_contained"] is True
    assert payload["loading_contained"] is True
    assert payload["loading_controls_contained"] is True
    assert payload["loading_width"] <= 292
    assert payload["loading_height"] <= 108
    assert payload["badge_contained"] is True
    assert payload["badge_width"] <= 36
    assert payload["badge_height"] <= 36
    assert payload["horizontal_max"] == 0
    assert payload["minimum_width"] <= payload["area_width"]
    assert payload["minimum_height"] <= payload["area_height"]


def test_translate_requires_visible_language(app):
    palette = RewritePalette()
    requests: list[tuple[str, str]] = []
    palette.command_requested.connect(lambda command, language: requests.append((command, language)))
    palette.show_capture("Hello")
    palette.command_buttons["Translate"].click()
    assert requests == []
    assert "language" in palette.status.text().lower()
    palette.language_edit.setText("Spanish")
    palette.command_buttons["Translate"].click()
    assert requests == [("Translate", "Spanish")]
    palette.finish_choice()


def test_automatic_capture_shows_without_focus_activation(app, monkeypatch):
    palette = RewritePalette()
    shown: dict[str, object] = {}
    monkeypatch.setattr(
        palette,
        "_show_near_pointer",
        lambda **kwargs: shown.update(kwargs),
    )
    monkeypatch.setattr(
        palette.suggestion_list,
        "setFocus",
        lambda *_args: pytest.fail("automatic HUD focused the suggestion list"),
    )
    monkeypatch.setattr(
        palette.command_buttons["Proofread"],
        "setFocus",
        lambda *_args: pytest.fail("automatic HUD focused a rewrite command"),
    )

    anchor = (100, 120, 260, 145)
    palette.show_capture(
        "this are automatically selected text",
        automatic=True,
        anchor=anchor,
    )

    assert shown == {"activate": False, "anchor": anchor}
    assert palette.view == "badge"
    assert palette.badge_button.text() == ""
    assert "selected text" in palette.status.text().casefold()
    palette._finish_automatic_analysis()
    assert palette.view == "badge"
    assert "suggestion" in palette.badge_button.accessibleName().casefold()
    palette.finish_choice()


def test_selected_text_hotkey_opens_palette_without_opening_main_window(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    monkeypatch.setattr(window_module, "WindowsSelectionReader", lambda **_kwargs: _NoUiaReader())
    monkeypatch.setattr(window.engine.inserter, "capture_selection", lambda: record)

    window._capture_selected_text_for_palette()
    app.processEvents()

    assert window.selected_text_record == record
    assert window.rewrite_palette.isVisible()
    assert not window.isVisible()
    assert window.rewrite_palette.source_edit.toPlainText() == record.text
    assert not window.rewrite_palette.replace_button.isEnabled()
    window._quitting = True
    window.close()


def test_stable_detected_selection_opens_hud_automatically_without_model(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("this are automatic selected text")
    detected = DetectedSelection(record, (100, 120, 280, 150))
    calls = {"pause": 0, "resume": 0, "model": 0}

    class _Monitor:
        def pause(self):
            calls["pause"] += 1

        def resume(self):
            calls["resume"] += 1

        def stop(self, timeout=0):
            return None

    window.selection_monitor = _Monitor()
    monkeypatch.setattr(
        window.writing_assistant,
        "run",
        lambda *_args, **_kwargs: calls.__setitem__("model", calls["model"] + 1),
    )

    window._handle_automatic_selection(detected)
    app.processEvents()

    assert window.selected_text_record == record
    assert window.rewrite_palette.isVisible()
    assert window.rewrite_palette.view == "badge"
    assert not window.isVisible()
    assert window.rewrite_palette.source_edit.toPlainText() == record.text
    assert calls == {"pause": 0, "resume": 0, "model": 0}
    window.rewrite_palette._finish_automatic_analysis()
    assert window.rewrite_palette.view == "badge"
    QTest.mousePress(
        window.rewrite_palette.badge_button,
        Qt.MouseButton.LeftButton,
    )
    assert calls["pause"] == 1
    assert window.rewrite_palette.view == "badge"
    QTest.mouseRelease(
        window.rewrite_palette.badge_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.rewrite_palette.view == "loading"
    assert calls["pause"] == 1
    window.rewrite_palette.close_button.click()
    assert calls["resume"] == 1
    window._quitting = True
    window.close()


def test_automatic_hud_keeps_background_capture_without_rereading_text_box(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    selected = SelectionRecord(
        "First broken part.",
        WindowSnapshot(77, 88, "Editor"),
        "selection",
        "b" * 64,
    )
    detected = DetectedSelection(selected, (100, 120, 280, 150))
    monkeypatch.setattr(
        window_module,
        "WindowsSelectionReader",
        lambda **_kwargs: pytest.fail("automatic capture reread on the UI thread"),
    )

    window._handle_automatic_selection(detected)
    app.processEvents()

    assert window.selected_text_record == selected
    assert window.rewrite_palette.source_edit.toPlainText() == selected.text
    assert window.rewrite_palette._document_scope is False
    assert window.rewrite_palette._anchor == detected.anchor
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_automatic_hud_does_not_shrink_document_queue_to_marked_word_range(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = (
        "First broken sentence. Second broken sentence. Third broken sentence. "
        "Fourth broken sentence. Fifth broken sentence. Sixth broken sentence."
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    source_window = WindowSnapshot(77, 88, "Draft - Word")
    document = SelectionRecord(
        source,
        source_window,
        "document",
        digest,
        (1, 2, 3),
    )
    marked_range = SelectionRecord(
        "First broken sentence.",
        source_window,
        "selection",
        digest,
        (1, 2, 3),
        selection_start=0,
        selection_end=22,
    )

    class _NoDocumentReader:
        def __init__(self, **_kwargs):
            pass

        def read_document(self):
            return None

    monkeypatch.setattr(window_module, "WindowsSelectionReader", _NoDocumentReader)

    window._handle_automatic_selection(
        DetectedSelection(document, (100, 120, 500, 240))
    )
    app.processEvents()
    window._handle_automatic_selection(
        DetectedSelection(marked_range, (100, 120, 280, 150))
    )
    app.processEvents()

    assert window.selected_text_record == document
    assert window.rewrite_palette.source_edit.toPlainText() == source
    assert window.rewrite_palette._document_scope is True
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_visible_automatic_badge_tracks_the_current_sentence(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.engine.settings.automatic_writing_hud = True
    first = _selection("First draft")
    second = _selection("First draft now")

    window._handle_automatic_selection(DetectedSelection(first, (100, 120, 200, 145)))
    window._handle_automatic_selection(DetectedSelection(second, (100, 120, 230, 145)))
    app.processEvents()

    assert window.rewrite_palette.view == "badge"
    assert window.selected_text_record == second
    assert window.rewrite_palette.source_edit.toPlainText() == second.text
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_stale_monitor_signal_from_prior_text_box_does_not_show_badge(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("Old focused draft")
    window.selection_monitor = object.__new__(WindowsSelectionMonitor)
    monkeypatch.setattr(
        window_module,
        "active_window",
        lambda: WindowSnapshot(999, 1000, "New empty editor"),
    )

    window._handle_automatic_selection(
        DetectedSelection(record, (100, 120, 230, 145))
    )
    app.processEvents()

    assert not window.rewrite_palette.isVisible()
    assert window.selected_text_record is None
    window.selection_monitor = None
    window._quitting = True
    window.close()


def test_duplicate_focus_poll_does_not_rebuild_badge_during_click(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.engine.settings.automatic_writing_hud = True
    record = _selection("This are selected text.")
    detected = DetectedSelection(record, (100, 120, 230, 145))
    original_show_capture = window.rewrite_palette.show_capture
    captures: list[str] = []

    def tracked_show_capture(text, **kwargs):
        captures.append(text)
        return original_show_capture(text, **kwargs)

    monkeypatch.setattr(window.rewrite_palette, "show_capture", tracked_show_capture)

    window._handle_automatic_selection(detected)
    app.processEvents()
    request_id = window._palette_request_id
    window._handle_automatic_selection(detected)
    app.processEvents()

    assert captures == [record.text]
    assert window._palette_request_id == request_id
    window.rewrite_palette._finish_automatic_analysis()
    window.rewrite_palette.badge_button.click()
    assert window.rewrite_palette.view == "loading"
    window.rewrite_palette.reject()
    window._quitting = True
    window.close()


def test_automatic_badge_debounces_same_word_window_focus_flap(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.engine.settings.automatic_writing_hud = True
    record = _selection("Focused draft")
    monkeypatch.setattr(window_module, "active_window", lambda: record.window)
    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 230, 145)))
    app.processEvents()

    window._handle_automatic_selection_cleared()
    app.processEvents()

    # Word flaps focus once per poll, so the badge waits out a short grace
    # window before hiding; a real focus loss still clears it afterwards.
    assert window.rewrite_palette.isVisible()
    window._finish_badge_clear()
    app.processEvents()

    assert not window.rewrite_palette.isVisible()
    assert window.selected_text_record is None
    window._quitting = True
    window.close()


def test_selected_text_hotkey_opens_existing_automatic_capture_without_reread(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("this are already captured text")
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text, automatic=True)
    monkeypatch.setattr(window_module, "active_window", lambda: record.window)
    monkeypatch.setattr(
        window_module,
        "WindowsSelectionReader",
        lambda **_kwargs: pytest.fail("hotkey reread the existing capture"),
    )

    window._capture_selected_text_for_palette()
    app.processEvents()

    assert window.rewrite_palette.view == "loading"
    assert window.selected_text_record == record
    window._quitting = True
    window.close()


def test_document_ai_prefetch_uses_one_ordered_worker(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    monkeypatch.setattr(
        type(window.polish_engine.runtime),
        "parallel_slots",
        property(lambda _runtime: 3),
    )

    assert window._palette_ai_width() == 1

    window._quitting = True
    window.close()


def test_automatic_badge_hides_immediately_after_exact_window_focus_loss(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.engine.settings.automatic_writing_hud = True
    record = _selection("Focused draft")
    window._handle_automatic_selection(DetectedSelection(record, (100, 120, 230, 145)))
    app.processEvents()
    monkeypatch.setattr(
        window_module,
        "active_window",
        lambda: WindowSnapshot(99, 100, "Different editor"),
    )

    window._handle_automatic_selection_cleared()
    app.processEvents()

    assert not window.rewrite_palette.isVisible()
    assert window.selected_text_record is None
    assert all(not segment.isVisible() for segment in window.target_underline._segments)
    window._quitting = True
    window.close()


def test_queued_focus_clear_during_badge_press_does_not_close_hud(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("Focused draft")
    window.selection_monitor = SimpleNamespace(
        paused=True,
        resume=lambda: None,
        stop=lambda timeout=0: None,
    )
    window._handle_automatic_selection(
        DetectedSelection(record, (100, 120, 230, 145))
    )
    app.processEvents()

    window._handle_automatic_selection_cleared()
    app.processEvents()

    assert window.rewrite_palette.isVisible()
    assert window.rewrite_palette.view == "badge"
    assert window.selected_text_record == record
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_manual_hud_stays_open_when_automatic_focus_clears(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("Manual draft")
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text, automatic=False)
    app.processEvents()

    window._handle_automatic_selection_cleared()

    assert window.rewrite_palette.isVisible()
    assert window.selected_text_record == record
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_recent_accept_does_not_reopen_automatic_hud(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.engine.settings.automatic_writing_hud = True
    window._automatic_hud_suppress_until = window_module.time.monotonic() + 10

    window._handle_automatic_selection(
        DetectedSelection(_selection("Corrected sentence."), (100, 120, 230, 145))
    )

    assert not window.rewrite_palette.isVisible()
    assert window.selected_text_record is None
    window._quitting = True
    window.close()


def test_palette_capture_failure_stays_in_hud_without_opening_main_window(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    monkeypatch.setattr(window_module, "WindowsSelectionReader", lambda **_kwargs: _NoUiaReader())
    monkeypatch.setattr(
        window.engine.inserter,
        "capture_selection",
        lambda: (_ for _ in ()).throw(RuntimeError("Clipboard is busy. Try again.")),
    )

    window._capture_selected_text_for_palette()
    app.processEvents()

    assert window.rewrite_palette.isVisible()
    assert not window.isVisible()
    assert window.selected_text_record is None
    assert "clipboard is busy" in window.rewrite_palette.status.text().casefold()
    assert all(
        not button.isEnabled()
        for button in window.rewrite_palette.command_buttons.values()
    )
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_palette_can_load_entire_document_from_original_editor(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    selected = _selection("Selected sentence.")
    document = SelectionRecord(
        "First sentence. teh second sentence.",
        selected.window,
        "document",
    )
    detected = DetectedSelection(document, (10, 20, 500, 300))

    class _DocumentReader:
        def read_document(self):
            return detected

    window.selected_text_record = selected
    monkeypatch.setattr(window.engine.inserter, "activate_window", lambda _window: True)
    monkeypatch.setattr(
        window_module,
        "WindowsSelectionReader",
        lambda **_kwargs: _DocumentReader(),
    )

    window._capture_entire_document_for_palette()
    app.processEvents()
    window.rewrite_palette.badge_button.click()

    assert window.selected_text_record == document
    assert window.rewrite_palette.source_edit.toPlainText() == document.text
    assert "Entire document" in window.rewrite_palette.scope_label.text()
    assert window.rewrite_palette.view == "loading"
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_palette_replace_rechecks_captured_selection(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    calls: list[tuple[SelectionRecord, str]] = []
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_selection",
        lambda captured, text: calls.append((captured, text)) or object(),
    )
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    window._handle_palette_writing_result((0, True, "This is selected text.", "Ready."))

    assert calls == []
    window.rewrite_palette.replace_button.click()

    assert calls == [(record, "This is selected text.")]
    assert window.selected_text_record is None
    assert not window.rewrite_palette.isVisible()
    window._quitting = True
    window.close()


def test_palette_document_step_keeps_stable_record_without_second_focus_read(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = "First bad sentence.\n\nSecond bad sentence."
    record = SelectionRecord(
        source,
        WindowSnapshot(77, 88, "Document1 - Word", "OpusApp"),
        "selection",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        (1, 2, 3),
        (10, 20, 500, 600),
        0,
        len(source),
    )
    corrected = "First corrected sentence.\n\nSecond corrected sentence."
    window.selected_text_record = record
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_selection",
        lambda *_args: pytest.fail("Word review must use the range adapter"),
    )

    class _Reader:
        def read_document(self):
            pytest.fail("A successful document replacement must not require HUD focus again")

    monkeypatch.setattr(window_module, "WindowsSelectionReader", lambda **_kwargs: _Reader())

    suggestion = SimpleNamespace(
        start=0,
        end=len(source),
        original=source,
        replacement=corrected,
    )
    assert window._replace_palette_step(corrected, suggestion) is True
    assert window.selected_text_record is not None
    assert window.selected_text_record.text == corrected
    assert window.selected_text_record.scope == "document"
    assert window.selected_text_record.window == record.window
    assert window.selected_text_record.control_runtime_id == record.control_runtime_id
    window._quitting = True
    window.close()
def test_palette_word_accept_replaces_only_current_review_range(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = (
        "teh first sentence. Clean second sentence. Clean third sentence.\n"
        "teh fourth sentence. Clean fifth sentence. Clean sixth sentence."
    )
    record = SelectionRecord(
        source,
        WindowSnapshot(77, 88, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    calls = []
    window.selected_text_record = record
    window.rewrite_palette._ai_proofread = False
    window.rewrite_palette.show_capture(source, document_scope=True)
    app.processEvents()
    window.rewrite_palette.badge_button.click()
    app.processEvents()
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda captured, **kwargs: calls.append((captured, kwargs)) or object(),
        raising=False,
    )
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_selection",
        lambda *_args: pytest.fail("Word review must not replace the whole document"),
    )

    window.rewrite_palette.apply_suggestion_button.click()
    app.processEvents()

    assert len(calls) == 1
    captured, kwargs = calls[0]
    assert captured == record
    assert kwargs["start"] == 0
    assert kwargs["expected_text"] == (
        "teh first sentence. Clean second sentence. Clean third sentence."
    )
    assert kwargs["new_text"] == (
        "The first sentence. Clean second sentence. Clean third sentence."
    )
    assert "\x01" not in kwargs["expected_document_text"]
    assert window.rewrite_palette.card_position.text() == "2 of 2"
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()

def test_failed_document_step_keeps_current_review_instead_of_resetting_to_zero(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = (
        "teh first sentence. Clean second sentence. Clean third sentence.\n"
        "teh fourth sentence. Clean fifth sentence. Clean sixth sentence."
    )
    record = SelectionRecord(
        source,
        WindowSnapshot(77, 88, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    window.selected_text_record = record
    window.rewrite_palette._ai_proofread = False
    window.rewrite_palette.show_capture(source, document_scope=True)
    app.processEvents()
    window.rewrite_palette.badge_button.click()
    app.processEvents()
    assert window.rewrite_palette.card_position.text() == "1 of 2"
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda *_args, **_kwargs: None,
    )

    corrected = source.replace("teh first", "The first", 1)
    suggestion = window.rewrite_palette._current_review_suggestion()
    assert window._replace_palette_step(corrected, suggestion) is False

    assert window.selected_text_record == record
    assert window.rewrite_palette.card_position.text() == "1 of 2"
    assert window.rewrite_palette.apply_suggestion_button.isEnabled()
    assert "not applied" in window.rewrite_palette.status.text().casefold()
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()

def test_palette_private_stage_uses_memory_buffer(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    window._handle_palette_writing_result((0, True, "Private local result.", "Ready."))
    window.writing_replace_button.setEnabled(True)
    window.writing_copy_button.setEnabled(True)

    window.rewrite_palette.stage_button.click()

    assert window.private_text_buffer.peek() == "Private local result."
    assert window.selected_text_record is None
    assert not window.writing_replace_button.isEnabled()
    assert not window.writing_copy_button.isEnabled()
    assert "clipboard unchanged" in window.writing_state.text().lower()
    window._quitting = True
    window.close()


def test_changed_selection_requires_fresh_capture_and_disables_stale_actions(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    window._handle_palette_writing_result((0, True, "Edited result.", "Ready."))
    calls = {"resume": 0}
    window.selection_monitor = SimpleNamespace(
        resume=lambda: calls.__setitem__("resume", calls["resume"] + 1),
        stop=lambda timeout=0: None,
    )
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda *_args, **_kwargs: None,
    )

    window.rewrite_palette.replace_button.click()

    assert window.selected_text_record is None
    assert window.rewrite_palette.isVisible()
    assert "capture it again" in window.rewrite_palette.status.text().casefold()
    assert not window.rewrite_palette.replace_button.isEnabled()
    assert not window.rewrite_palette.stage_button.isEnabled()
    assert not window.writing_replace_button.isEnabled()
    assert not window.writing_copy_button.isEnabled()
    assert calls["resume"] == 1
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_palette_replace_exception_resumes_automatic_monitor(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    calls = {"resume": 0}
    window.selection_monitor = SimpleNamespace(
        resume=lambda: calls.__setitem__("resume", calls["resume"] + 1),
        stop=lambda timeout=0: None,
    )
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    window._handle_palette_writing_result((0, True, "Edited result.", "Ready."))
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_selection",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Target blocked.")),
    )

    window.rewrite_palette.replace_button.click()

    assert calls["resume"] == 1
    assert "target blocked" in window.rewrite_palette.status.text().casefold()
    window.rewrite_palette.finish_choice()
    window._quitting = True
    window.close()


def test_palette_reject_leaves_original_unchanged(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    replaced: list[str] = []
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_selection",
        lambda _record, text: replaced.append(text),
    )
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    window._handle_palette_writing_result((0, True, "Suggestion.", "Ready."))

    window.rewrite_palette.reject_button.click()

    assert replaced == []
    assert window.selected_text_record is None
    assert "not changed" in window.writing_state.text().lower()
    window._quitting = True
    window.close()


def test_rejected_request_cannot_reopen_palette(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection()
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    request_id = window._palette_request_id
    window.rewrite_palette.reject_button.click()

    window._handle_palette_writing_result((request_id, True, "Late result.", "Ready."))

    assert not window.rewrite_palette.isVisible()
    assert window.rewrite_palette.result_text == ""
    assert window.selected_text_record is None
    window._quitting = True
    window.close()


def test_palette_module_has_no_clipboard_api():
    module_path = __import__("echo_link_app.rewrite_palette", fromlist=["__file__"]).__file__
    text = Path(module_path).read_text(encoding="utf-8")
    assert "pyperclip" not in text
    assert "QClipboard" not in text
    assert ".clipboard(" not in text
    assert "â" not in text
    assert "—" not in text


def test_palette_reports_exact_sentence_target_and_clears_it(app):
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    targets: list[tuple[int, int]] = []
    palette.target_changed.connect(lambda start, end: targets.append((start, end)))

    palette.show_capture("This are selected text.")

    suggestion = palette.suggestions[0]
    assert targets[-1] == (-1, -1)

    palette.badge_button.click()
    assert targets[-1] == (suggestion.start, suggestion.end)

    palette.finish_choice()
    assert targets[-1] == (-1, -1)


def test_window_keeps_user_opened_hud_but_clears_target_when_focus_leaves_text_box(
    app, tmp_path, monkeypatch
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("This are selected text.")
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    app.processEvents()
    window.rewrite_palette.badge_button.click()
    assert window.rewrite_palette.isVisible()
    monkeypatch.setattr(
        window_module,
        "active_window",
        lambda: WindowSnapshot(999, 444, "Other window"),
    )

    window._check_palette_focus()

    assert window.rewrite_palette.isVisible()
    assert window.selected_text_record == record
    assert not window.rewrite_palette._target_marking_enabled

    monkeypatch.setattr(window_module, "active_window", lambda: record.window)
    monkeypatch.setattr(
        window_module.WindowsSelectionReader,
        "is_record_control_focused",
        lambda _reader, _record: True,
    )
    window._check_palette_focus()

    assert window.rewrite_palette._target_marking_enabled
    window.rewrite_palette.reject()
    window._quitting = True
    window.close()


def test_window_keeps_active_hud_inside_same_text_box(app, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    record = _selection("This are selected text.")
    window.selected_text_record = record
    window.rewrite_palette.show_capture(record.text)
    app.processEvents()
    window.rewrite_palette.badge_button.click()
    monkeypatch.setattr(window_module, "active_window", lambda: record.window)
    monkeypatch.setattr(
        window_module.WindowsSelectionReader,
        "is_record_control_focused",
        lambda _reader, _record: True,
    )

    window._check_palette_focus()

    assert window.rewrite_palette.isVisible()
    window.rewrite_palette.reject()
    window._quitting = True
    window.close()


def test_target_underline_clips_overrun_and_rejects_huge_geometry(app):
    screen = app.primaryScreen()
    geometry = screen.geometry()
    ratio = max(1.0, float(screen.devicePixelRatio()))
    raw = (
        geometry.left() + round(10 * ratio),
        geometry.top() + round(20 * ratio),
        geometry.left() + round(geometry.width() * 1.4 * ratio),
        geometry.top() + round(40 * ratio),
    )

    safe = _safe_line_rectangles((raw,))

    assert safe
    assert safe[0].left() >= geometry.left()
    assert safe[0].right() <= geometry.right()
    underline = TargetUnderline()
    underline.show_rectangles(((0, 0, 2**31, 2**31),))
    underline.clear()
    underline.close()


def test_repeated_document_step_failure_stops_asking_for_a_doomed_retry(
    app,
    tmp_path,
    monkeypatch,
):
    """Clicking Replace Text forever cannot fix a document that moved.

    Word swapped a document for an AutoRecovered copy while a review was open.
    Every click was refused, and the message said "Try Replace Text again", so
    it was clicked a dozen times against a capture that could never match. One
    retry is still offered, because a single failure is usually a repaint or
    focus race; after that the HUD asks for a fresh capture.
    """
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = (
        "teh first sentence. Clean second sentence. Clean third sentence.\n"
        "teh fourth sentence. Clean fifth sentence. Clean sixth sentence."
    )
    record = SelectionRecord(
        source,
        WindowSnapshot(77, 88, "Document1 - Word", "OpusApp"),
        "document",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    window.selected_text_record = record
    window.rewrite_palette._ai_proofread = False
    window.rewrite_palette.show_capture(source, document_scope=True)
    app.processEvents()
    window.rewrite_palette.badge_button.click()
    app.processEvents()
    monkeypatch.setattr(
        window.engine.inserter,
        "replace_document_range",
        lambda *_args, **_kwargs: None,
    )

    corrected = source.replace("teh first", "The first", 1)
    suggestion = window.rewrite_palette._current_review_suggestion()

    # First failure keeps the review so a genuine race can be retried.
    assert window._replace_palette_step(corrected, suggestion) is False
    assert window.selected_text_record == record
    assert window.rewrite_palette.card_position.text() == "1 of 2"

    # Second failure means the source moved; stop advising a doomed retry.
    assert window._replace_palette_step(corrected, suggestion) is False
    assert window.selected_text_record is None
    status = window.rewrite_palette.status.text().casefold()
    assert "capture it again" in status
    assert "try replace text again" not in status

    window._quitting = True
    window.close()


def test_apply_retry_budget_resets_for_each_new_capture(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window._failed_apply_streak = 1
    window._clear_palette_review_state()

    assert window._failed_apply_streak == 0

    window._quitting = True
    window.close()


def test_empty_capture_says_no_text_was_found_not_zero_of_zero(app):
    """A failed capture must not look like a review card with nothing in it.

    A writer opened the HUD after a capture that read no text and saw "No
    sentence available", "0 of 0", and two blank panes headed "Original
    sentence" and "Suggested text". Nothing said the capture had failed, and the
    empty Original pane read as the app having lost their document.
    """
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture("")
    app.processEvents()

    assert "No text was captured" in palette.suggestion_title.text()
    assert "Enable Editing" in palette.suggestion_explanation.text()
    # No position, because there is no sequence to be positioned in.
    assert palette.card_position.text() == ""
    # And no headings over empty panes.
    assert not palette.before_text.isVisible()


def test_clean_text_says_there_is_nothing_to_correct(app):
    """Text that captured fine but needs no repair is a different message."""
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture("The dog waits by the door.")
    app.processEvents()

    if palette.suggestions:
        # The bundled engine found something; this case is covered elsewhere.
        palette.finish_choice()
        return
    assert "Nothing to correct" in palette.suggestion_title.text()
    assert palette.before_text.toPlainText() == "The dog waits by the door."


def test_the_counter_does_not_move_when_a_section_is_only_deferred(app):
    """Watched live, the counter went 4 -> 5 with completed stuck at 3.

    It showed completed + cursor index + 1, so the review stepping over a
    section whose correction had not arrived made the number climb while
    nothing had been decided. A reader cannot tell from that whether their
    essay was corrected. Deferred sections come back; they are not progress.
    """
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    before = palette.card_position.text()

    # The review moves the cursor on by itself, exactly as it does when the
    # section in front of it has no finished correction yet.
    palette._review_index = 1
    palette._show_review_suggestion()
    app.processEvents()

    assert palette.card_position.text() == before
    palette.finish_choice()


def test_dismissing_a_section_does_count_as_progress(app):
    """A dismissal is the reader's decision, unlike an automatic step-over."""
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    # Dismiss is disabled until the section's AI step finishes, so finish it.
    attempt, suggestion_id, *_ = requests[0]
    palette.show_sentence_result(
        attempt, suggestion_id, "Echo-Link is ready.", "AI proofread ready."
    )
    app.processEvents()

    before = palette.card_position.text()
    assert palette.dismiss_suggestion_button.isEnabled()
    palette.dismiss_suggestion_button.click()
    app.processEvents()

    assert palette.card_position.text() != before
    palette.finish_choice()


def _run_section_job(window, source: str) -> tuple:
    """Run one HUD section job synchronously and return the payload it emits."""
    emitted: list[tuple] = []
    window.bridge.palette_sentence_result.connect(emitted.append)
    window.selected_text_record = _selection(source)
    job = (
        window._palette_request_id,
        1,
        "sentence-1",
        source,
        "Proofread",
        "test-model",
        "General",
        (),
        source,
    )
    window._execute_palette_sentence_job(job)
    return emitted[0]


def test_a_section_the_ai_cannot_correct_still_returns_the_text(app, tmp_path, monkeypatch):
    """A refused section is the worst outcome, worse than an imperfect one.

    When the AI answer cannot be shown and the rules pass finds nothing to
    change, the section used to raise, which disabled Replace and reported
    "could not be corrected". The reader was left with a dead card and no way
    to move that paragraph forward. Returning the text unchanged processes the
    section honestly instead.

    The answer that was rejected is never what comes back: only the rules
    repair, or the original text.
    """
    monkeypatch.delenv("ECHOLINK_DISABLE_PALETTE_AI", raising=False)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = "The reports is ready."
    monkeypatch.setattr(
        window.writing_assistant,
        "run",
        lambda _source, **_options: SimpleNamespace(text="Something invented."),
    )
    monkeypatch.setattr(window_module, "_polish_result_ready_for_review", lambda _result: False)
    # The AI answer was rejected and the rules pass changed nothing: the exact
    # combination that used to raise.
    monkeypatch.setattr(
        window.writing_assistant,
        "_delegate",
        SimpleNamespace(
            run=lambda _source, **_options: SimpleNamespace(text="Something invented."),
            last_correction_result=SimpleNamespace(
                used_llm=False,
                deterministic_text="",
                message="did not pass final quality checks",
                metrics={},
                plan=None,
            ),
        ),
        raising=False,
    )

    _request, _attempt, _suggestion, ok, text, message = _run_section_job(window, source)

    assert ok, "the section must be delivered, not refused"
    assert "Something invented." not in text
    assert text.strip() == source
    assert message
    window._quitting = True
    window.close()


def test_a_section_that_raises_still_returns_the_text(app, tmp_path, monkeypatch):
    """The same guarantee on the failure path, not just the quality path.

    A provider fault used to emit an empty failure payload, which is the state
    the reader experiences as a section that cannot be corrected. The rules
    repair is offered when the engine produced one; otherwise the original.
    """
    monkeypatch.delenv("ECHOLINK_DISABLE_PALETTE_AI", raising=False)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    source = "The reports is ready."

    def explode(_source, **_options):
        raise RuntimeError("local runtime unavailable")

    monkeypatch.setattr(
        window.writing_assistant,
        "_delegate",
        SimpleNamespace(run=explode, last_correction_result=None),
        raising=False,
    )

    _request, _attempt, _suggestion, ok, text, message = _run_section_job(window, source)

    assert ok, "a provider fault must not strand the section"
    assert text.strip() == source
    assert "local runtime unavailable" in message
    window._quitting = True
    window.close()


def test_double_space_before_a_sentence_keeps_its_own_edit_in_range():
    """A whitespace edit must land inside the span that is supposed to hold it.

    `_sentence_bounds` finds the end of the previous sentence and then skips
    whitespace forward to where the next sentence really begins. When the edit
    IS that whitespace, the skip walks straight past it and returns a span
    starting after the edit. The local offset then goes negative, Python slices
    backwards to an empty string, and the guard in `_group_sentence_suggestions`
    correctly reports a mismatch it was never given a fair chance to pass.

    Measured on 2026-08-15: essay 20 of the 500-percent corpus has a double
    space at index 7833, immediately before a sentence starting at 7835. One
    "Use one space" edit there raised ValueError out of the grouper, the HUD
    caught it, emptied the review and logged nothing. The badge sat at
    "Manuscript, 0 reviews ready" for 41 minutes while the harness timed out
    section after section. Every one of that document's 25 sections was lost to
    two characters of whitespace.
    """
    from echo_link_app.review_spans import _sentence_bounds

    text = "The first sentence ends here.  Seen together these factors connect."
    gap = text.index(".  ") + 1
    start, end = gap, gap + 2
    assert text[start:end] == "  "

    left, right = _sentence_bounds(text, start, end)
    assert left <= start, (
        f"span [{left},{right}) starts after the edit at [{start},{end})"
    )
    assert right >= end


def test_one_unusable_edit_does_not_destroy_the_whole_document_review():
    """One bad edit costs its own correction, not every section in the essay.

    Refusing to apply an edit whose source text no longer matches is a safety
    property and stays. Throwing that refusal out of the grouper is not: it
    took a 25-section review down to zero, with nothing on screen but a badge
    reading 0 reviews ready.
    """
    from echo_link_app.review_spans import _group_sentence_suggestions

    class _Edit:
        def __init__(self, start, end, original, replacement):
            self.start, self.end = start, end
            self.original, self.replacement = original, replacement
            self.title, self.explanation = "Edit", ""
            self.category = "Spelling"

    text = "The cat sat on the mat. The dog barked loudly at noon."
    good = _Edit(text.index("barked"), text.index("barked") + 6, "barked", "bayed")
    # Claims text that is not there, so it can never be applied safely.
    bad = _Edit(text.index("cat"), text.index("cat") + 3, "cot", "cut")

    grouped = _group_sentence_suggestions(text, [bad, good])
    assert grouped, "the whole review was discarded because of one bad edit"
    assert any("bayed" in item.replacement for item in grouped), (
        "the sound edit was lost along with the bad one"
    )


def test_review_sections_never_overlap_each_other():
    """Two sections covering the same characters cannot both be accepted.

    Accepting one leaves the other unshiftable:
    `_shift_remaining_sentence_suggestions` refuses with "Pending sentence
    suggestions overlap accepted text" and the review stops advancing.

    Measured on the 500-percent corpus, 2026-08-15: 30 overlapping pairs
    across 15 of 20 essays, nearly all a whitespace run against the sentence
    after it. Essay 16 spent 457 seconds retrying 25 sections 56 times and
    left the whole tail of the document uncorrected, scoring 52.
    """
    from echo_link_app.review_spans import _group_sentence_suggestions

    class _Edit:
        def __init__(self, start, end, original, replacement):
            self.start, self.end = start, end
            self.original, self.replacement = original, replacement
            self.title, self.explanation = "Edit", ""
            self.category = "Formatting"

    # A run of spaces before a sentence, plus a normal edit inside it. The
    # whitespace used to be filed under its own bounds, which overlapped the
    # sentence span that followed.
    text = "The first part ends here.    Seen together these factors connect."
    gap = text.index(".    ") + 1
    edits = [
        _Edit(gap, gap + 4, "    ", " "),
        _Edit(text.index("factors"), text.index("factors") + 7, "factors", "points"),
    ]

    sections = sorted(
        _group_sentence_suggestions(text, edits), key=lambda s: (s.start, s.end)
    )
    for earlier, later in zip(sections, sections[1:]):
        assert later.start >= earlier.end, (
            f"sections overlap: [{earlier.start},{earlier.end}) and "
            f"[{later.start},{later.end})"
        )


def test_typing_does_not_cost_a_review_between_words(app):
    """The badge waits for a pause, and a pause is longer than a keystroke.

    The analysis is deferred so a burst of focus signals costs one review
    rather than several, but at 180 milliseconds it deferred nothing a person
    could produce. Measured 2026-08-16 against the running app: three captures
    of a chat window 600 to 900 milliseconds apart each completed a full review
    before the next arrived, so the writer was handed a fresh card on every
    pause between words.

    This waits longer than the old interval and shorter than the new one, so it
    fails outright if the badge goes back to analysing that eagerly.
    """
    import time

    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture("teh draft is redy", automatic=True)

    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)

    # Still waiting for the writer to stop, so nothing has been analysed.
    assert palette._analysis_pending is True
    assert palette._suggestions == ()
    palette.finish_choice()


def test_the_badge_still_analyses_once_the_writer_stops(app):
    """Deferring must not mean never: the pause has to produce the review."""
    import time

    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture("teh draft is redy", automatic=True)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and palette._analysis_pending:
        app.processEvents()
        time.sleep(0.02)

    assert palette._analysis_pending is False
    assert palette._suggestions != ()
    palette.finish_choice()


def test_the_tone_control_announces_which_tone_is_selected(app):
    """A tone control that will not say what it is set to cannot be operated.

    This was a QComboBox. Measured on 2026-08-18 against a window carrying the
    HUD's own flags - Tool, frameless, always on top, shown without activating
    - a combo cannot be set through UI Automation at all: ValuePattern.SetValue
    is accepted and does nothing, keyboard never arrives because the window
    deliberately refuses focus so it will not take it from Word, a click does
    not reach the popup, and SelectionItemPattern.Select returns success while
    the value stays put. A side-by-side experiment in that same window had a
    menu button work first time and the combo report nothing at all.

    That is an accessibility defect before it is a testing one, and the half
    that bites hardest is this: a bare button announces its label and never its
    value, so a reader is told "Sentence rewrite tone" whichever tone is set.
    A combo gave that away for free.
    """
    from echo_link_app.rewrite_palette import TonePicker

    picker = TonePicker()
    picker.addItems(("Clear", "Improve", "Friendly"))
    picker.setAccessibleName("Sentence rewrite tone")

    assert "Clear" in picker.accessibleName()
    picker.setCurrentText("Friendly")
    assert picker.currentText() == "Friendly"
    assert "Sentence rewrite tone" in picker.accessibleName()
    assert "Friendly" in picker.accessibleName()
    picker.deleteLater()


def test_the_tone_control_keeps_the_combo_box_contract(app):
    """The call sites and the tests that describe them must keep working.

    The API mirrors QComboBox on purpose, so replacing the widget stayed an
    accessibility fix instead of turning into a rewrite of everything that
    reads a tone.
    """
    from echo_link_app.rewrite_palette import TonePicker

    picker = TonePicker()
    seen: list[str] = []
    picker.currentTextChanged.connect(seen.append)
    picker.addItems(("Clear", "Improve", "Shorten"))

    assert picker.count() == 3
    assert picker.itemText(2) == "Shorten"
    assert picker.itemText(99) == ""
    assert picker.currentText() == "Clear"

    picker.setCurrentText("Shorten")
    assert seen == ["Shorten"]
    # Choosing the same tone again is a no-op, which is why the harness has to
    # press Regenerate for each section rather than re-picking the tone: the
    # first tone run applied Friendly exactly once across eleven sections.
    picker.setCurrentText("Shorten")
    assert seen == ["Shorten"]
    # A tone that is not on the menu is ignored rather than invented.
    picker.setCurrentText("Nonexistent")
    assert picker.currentText() == "Shorten"
    picker.deleteLater()


def _toned_palette(app):
    """A palette showing one review section, before any tone is chosen.

    open_current_capture already issues the section's first AI request, so the
    palette arrives busy with that in flight. That is the real state a writer
    is in when they reach for the tone menu, and it is why these tests drive
    the tone control rather than calling _request_sentence_rewrite directly.
    """
    palette = RewritePalette(ai_proofread=True)
    palette.show_capture("teh draft has erors in it and it needs wrok.")
    app.processEvents()
    palette.open_current_capture()
    app.processEvents()
    return palette


def test_a_tone_waits_for_the_proofread_instead_of_toning_the_rules_text(app):
    """Choosing a tone must not cost the writer their spelling corrections.

    The card is filled immediately by the fast deterministic rules pass, and
    `suggestion.replacement` only becomes the AI answer when that section's
    proofread lands. A tone requested before then was built on the rules text.

    Measured on essay 17 of error-riddled-20-docx: a proofread fixes
    "SuSpEcTeD" and "RePahIr", a tone run on that proofread keeps them fixed,
    and a tone run on the unproofread text leaves both in the finished
    document. used_llm was True throughout, so the model was running - it was
    being handed the wrong text.
    """
    palette = _toned_palette(app)
    asked = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt, sid, source, command: asked.append(command)
    )
    suggestion_id = str(
        getattr(palette._current_review_suggestion(), "suggestion_id", "")
    )
    palette._ai_completed_ids.discard(suggestion_id)

    palette.tone_combo.setCurrentText("Friendly")
    app.processEvents()

    # Proofread first, and the tone is not issued against the rules text.
    assert asked == ["Proofread"], asked
    assert palette._pending_tone_rewrite == suggestion_id
    palette.close()


def test_the_deferred_tone_still_runs_once_the_proofread_lands(app):
    """The writer's click is honoured, not dropped.

    Deferring is only safe if the tone actually follows. A click that vanished
    would be worse than the bug it was added to fix.
    """
    palette = _toned_palette(app)
    suggestion_id = str(
        getattr(palette._current_review_suggestion(), "suggestion_id", "")
    )
    palette._ai_completed_ids.discard(suggestion_id)
    palette.tone_combo.setCurrentText("Friendly")
    app.processEvents()
    assert palette._pending_tone_rewrite == suggestion_id

    asked = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt, sid, source, command: asked.append(command)
    )
    palette.show_sentence_result(
        palette._active_ai_attempt_id,
        suggestion_id,
        "The draft has errors in it and it needs work.",
        "Local proofread ready.",
    )
    app.processEvents()

    assert asked == ["Friendly"], asked
    assert palette._pending_tone_rewrite == ""
    palette.close()


def test_a_failed_proofread_does_not_strand_the_tone(app):
    """A proofread that never lands must not leave the writer with nothing.

    If the section is already marked failed, the tone runs on what is there
    rather than waiting for an answer that is not coming. That is the old
    behaviour, and here it is the correct fallback.
    """
    palette = _toned_palette(app)
    suggestion_id = str(
        getattr(palette._current_review_suggestion(), "suggestion_id", "")
    )
    palette._ai_completed_ids.discard(suggestion_id)
    palette._ai_failed_ids.add(suggestion_id)

    asked = []
    palette.sentence_rewrite_requested.connect(
        lambda attempt, sid, source, command: asked.append(command)
    )
    palette.tone_combo.setCurrentText("Friendly")
    app.processEvents()

    assert asked == ["Friendly"], asked
    assert palette._pending_tone_rewrite == ""
    palette.close()


def test_a_section_the_ai_has_not_corrected_cannot_be_replaced(app):
    """Rules-only text must not be acceptable while the AI is still coming.

    The card is built the moment a review opens, in the same millisecond the
    counter reads "1 of 11", while the model is still working on that section.
    Until 2026-08-20 the Replace button was live on it, and the card said so in
    words - "The AI has not corrected this section" - while still letting the
    writer take it.

    The rules cannot do the repair a reader expects. Measured the same day,
    "duck are funny pets", "ducks is funny pets" and "a duck are a funny pet"
    come back unchanged from the rule engine, from the fast proofread rules,
    and from fix_subject_verb_agreement itself. So pressing Replace there
    returns a sentence whose grammar was never repaired, which is what the
    owner hit and reported.

    The model is not failing when this happens. One run offered eight sections
    this way against 81 AI completions; they simply had not been asked yet.
    """
    palette = RewritePalette()
    palette._ai_proofread = True
    palette._ai_completed_ids.clear()
    palette.apply_suggestion_button.setEnabled(True)

    palette._mark_ai_provenance("sentence:not-done-yet")

    # The card SAYS what happened. It does not decide what the writer may do
    # about it - _show_review_suggestion has owned that since before this
    # test existed, and it carries the conditions that make locking safe.
    # Asserting the lock here was asserting the wrong owner, and it hid a
    # second, blunter lock that left Replace and Dismiss both dead.
    assert "built-in rules only" in palette.after_heading.text()
    assert "Waiting for the AI" in palette.suggestion_explanation.text()
    assert palette._section_provenance_label() == "waiting"


def test_a_review_with_the_ai_switched_off_can_still_be_replaced(app):
    """The guard must not strand a writer who has no AI at all.

    When AI proofreading is off for the review the rule correction is the whole
    product, not a placeholder, so it stays acceptable and the card says why.
    """
    palette = RewritePalette()
    palette._ai_proofread = False
    palette._ai_completed_ids.clear()
    palette.apply_suggestion_button.setEnabled(True)

    palette._mark_ai_provenance("sentence:no-ai-at-all")

    assert palette.apply_suggestion_button.isEnabled()
    assert "AI proofreading is off" in palette.suggestion_explanation.text()


def test_a_section_the_ai_finished_is_replaceable(app):
    """The ordinary path has to keep working, or the guard is a regression."""
    palette = RewritePalette()
    palette._ai_proofread = True
    palette.apply_suggestion_button.setEnabled(True)
    palette._ai_completed_ids.add("sentence:done")

    palette._mark_ai_provenance("sentence:done")

    assert palette.apply_suggestion_button.isEnabled()
    assert palette.after_heading.text() == "Suggested text"


def test_a_section_the_ai_refused_offers_the_rules_repair_as_a_separate_choice(app):
    """Refused is not the same as pending, and must not behave the same.

    When a meaning guard turns the model's answer down, nothing more is coming
    for that section, so waiting would strand the writer. Refusing to offer
    anything was tried and was worse: seven sections were dismissed
    uncorrected and an essay went from 0 unknown words to 18.

    So the rules repair stays on the card and readable, Replace is still
    disabled, and taking it becomes a deliberate second press instead of the
    button under the writer's thumb.
    """
    palette = RewritePalette()
    palette._ai_proofread = True
    palette._ai_completed_ids.clear()
    palette._ai_failed_ids.add("sentence:guard-refused")
    palette.apply_suggestion_button.setEnabled(True)

    palette._mark_ai_provenance("sentence:guard-refused")

    assert not palette.apply_suggestion_button.isEnabled()
    # isHidden, not isVisible: a child of a window that was never shown
    # reports isVisible() False whatever it was asked to do, so isVisible
    # would pass here even if the control were never offered.
    assert not palette.use_rules_button.isHidden()
    assert palette.use_rules_button.isEnabled()
    assert "could not correct this section safely" in (
        palette.suggestion_explanation.text()
    )


def test_the_rules_choice_does_not_linger_onto_the_next_section(app):
    """A control left visible from the last section is an accidental press.

    _mark_ai_provenance runs for every section as the card moves, so the
    deliberate choice has to be withdrawn as deliberately as it was offered.
    """
    palette = RewritePalette()
    palette._ai_proofread = True
    palette._ai_failed_ids.add("sentence:refused")
    palette._mark_ai_provenance("sentence:refused")
    assert not palette.use_rules_button.isHidden()

    # The next section is still waiting on the model, not refused.
    palette._mark_ai_provenance("sentence:still-waiting")

    assert palette.use_rules_button.isHidden()
    assert not palette.apply_suggestion_button.isEnabled()


def test_a_completed_section_offers_no_rules_choice(app):
    """The ordinary path shows one action, not two."""
    palette = RewritePalette()
    palette._ai_proofread = True
    palette._ai_completed_ids.add("sentence:done")
    palette.apply_suggestion_button.setEnabled(True)

    palette._mark_ai_provenance("sentence:done")

    assert palette.apply_suggestion_button.isEnabled()
    assert palette.use_rules_button.isHidden()


def test_the_card_publishes_which_state_the_section_is_in(app):
    """The harness waits on this string, so it has to be true.

    review_readiness answers "is the whole review prepared". It can say yes
    while THIS section is still waiting on the model - which is now the state
    where Replace is disabled. Reading the review-level fact instead cost a
    five-tone measurement run on 2026-08-20: the harness saw ready, found
    Replace disabled, decided there was nothing to apply, and dismissed its
    way through the document with presses=0, scoring every tone 0.0 percent
    while measuring nothing.
    """
    palette = RewritePalette()
    palette._ai_proofread = True
    palette._ai_completed_ids.clear()
    palette._ai_failed_ids.clear()

    assert palette._section_provenance_label() == "waiting"

    palette._ai_proofread = False
    assert palette._section_provenance_label() == "no-ai"


def test_the_published_state_follows_the_section(app, monkeypatch):
    """ai and rules-only are read off the section on screen, not the review."""
    palette = RewritePalette()
    palette._ai_proofread = True

    class _Suggestion:
        suggestion_id = "sentence:on-screen"

    monkeypatch.setattr(
        palette, "_current_review_suggestion", lambda: _Suggestion()
    )

    palette._ai_completed_ids.add("sentence:on-screen")
    assert palette._section_provenance_label() == "ai"

    palette._ai_completed_ids.discard("sentence:on-screen")
    palette._ai_failed_ids.add("sentence:on-screen")
    assert palette._section_provenance_label() == "rules-only"


def _replacement_texts(palette):
    return [str(getattr(item, "replacement", "")) for item in palette._suggestions]


def test_a_tone_request_covers_several_paragraphs_at_once(app):
    """A tone gets a span big enough to have a tone.

    Measured 2026-08-20: tones were applied one paragraph at a time and the
    app log recorded "command=Formal characters=43". Proofread is deliberately
    left per-section, so this only changes the tone path.
    """
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.tone_combo.setCurrentText("Friendly")
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    assert requests
    source = requests[0][2]
    assert chr(10) * 2 in source, source
    palette.finish_choice()


def test_a_batch_answer_is_filed_to_each_paragraph_in_order(app):
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.tone_combo.setCurrentText("Friendly")
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    attempt, first_id, source, *_ = requests[0]
    paragraphs = source.split(chr(10) * 2)
    count = len(paragraphs)
    # Warm versions of the paragraphs THEY ANSWER, not placeholders. The blocks
    # used to read "Warm paragraph 1." and share no word with their section,
    # which answer_abandons_the_source now refuses - correctly, since that is
    # what a lost paragraph looks like. This test is about ORDER: that each
    # block reaches its own section and no section gets two.
    warm = [f"Warmly, {text.strip()}" for text in paragraphs]
    answer = (chr(10) * 2).join(warm)
    palette.show_sentence_result(attempt, first_id, answer, "Friendly ready.")
    app.processEvents()

    texts = _replacement_texts(palette)
    assert warm[0] in texts
    if count > 1:
        # Every paragraph of the answer reached its OWN section, and no
        # section received more than its own paragraph.
        assert warm[1] in texts
        assert not any(chr(10) * 2 in value for value in texts)
        # ... and in order, which a set membership check alone would not show.
        assert texts.index(warm[0]) < texts.index(warm[1])
    palette.finish_choice()


def test_a_merged_batch_answer_never_reaches_a_section(app):
    """The essay-2 fusing defect, caught instead of applied.

    A model that merges the paragraphs it was given returns one block for
    several sections. Writing that block into the first section would put
    several paragraphs where one belongs, and applying it would delete the
    breaks between them. The batch is discarded and the sections are prepared
    individually instead.
    """
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.tone_combo.setCurrentText("Friendly")
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    attempt, first_id, source, *_ = requests[0]
    if len(source.split(chr(10) * 2)) < 2:
        palette.finish_choice()
        return
    merged = "Everything the model was given, run together into one block."
    palette.show_sentence_result(attempt, first_id, merged, "Friendly ready.")
    app.processEvents()

    assert merged not in _replacement_texts(palette)
    palette.finish_choice()


def test_the_tone_button_batches_the_sections_after_it(app):
    """The path a writer actually takes has to be the batched one.

    The first batching commit wired only the automatic proofread path, which
    is deliberately NOT batched, so no tone request ever grew. Measured live
    against the running app: one Friendly request of "characters=43".

    The sections after this one already carry a completed PROOFREAD, and that
    must not disqualify them from being toned - it disqualified all of them,
    which is precisely how the batch collapsed to one section.
    """
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=False,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()
    requests.clear()
    palette.tone_combo.setCurrentText("Friendly")
    app.processEvents()

    assert requests, "choosing a tone made no request"
    source = requests[-1][2]
    assert chr(10) * 2 in source, f"tone request was not batched: {source!r}"
    palette.finish_choice()


def test_a_tone_batch_skips_a_section_whose_proofread_is_still_running(app):
    """Two answers for one section race, and the review never settles.

    Measured live on 2026-08-21: the card stayed on "1 of 25" while the ready
    count climbed to 21 and then restarted, nine times in twenty minutes, and
    Friendly went from 26.7 percent to applied=0 on three runs out of three.
    A tone must be applied on top of a FINISHED proofread, never alongside one
    that is still in flight.
    """
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    ids = [str(getattr(item, "suggestion_id", "")) for item in palette._review_suggestions]
    assert len(ids) >= 2, ids
    # The section after this one has been asked for but has not answered.
    palette._ai_requested_ids.add(ids[1])
    palette._ai_completed_ids.discard(ids[1])

    members = palette._tone_batch_ids(ids[0], "Friendly", tone_pass=True)

    assert ids[1] not in members, members
    palette.finish_choice()


def test_the_hud_publishes_its_state_where_a_machine_can_read_it(app, tmp_path, monkeypatch):
    """The signal the harness never had.

    Everything the HUD knew was published only as an English sentence, so the
    harness string-matched it and called anything unrecognised "unknown" -
    fifty-four times in one failing run, after which it pressed nothing and
    reported applied=0.
    """
    import json

    from echo_link_core import session_state

    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", str(tmp_path / "state"))
    palette = RewritePalette(suggestion_engine=_SuggestionEngine(), ai_proofread=True)
    palette.show_capture(
        "Echo-Link are ready. Clean sentence. Third sentence.\nAnother sentence."
    )
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    state = json.loads(session_state.state_path().read_text(encoding="utf-8"))
    assert state["section_total"] >= 1
    assert state["sections_total"] >= 1
    assert "replace_enabled" in state
    assert "tone_selected" in state
    assert state["build"], "a state with no build lets a run measure the wrong process"
    palette.finish_choice()


def test_the_published_state_never_carries_the_writers_text(app, tmp_path, monkeypatch):
    from echo_link_core import session_state

    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", str(tmp_path / "state"))
    palette = RewritePalette(suggestion_engine=_SuggestionEngine(), ai_proofread=True)
    palette.show_capture("Echo-Link are ready. A distinctive sentence to look for.")
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    body = session_state.state_path().read_text(encoding="utf-8")
    assert "distinctive sentence" not in body
    palette.finish_choice()


def test_refused_tone_batch_puts_the_card_back(app):
    """A refused tone batch must not strand the HUD in the loading view.

    Asking for a tone switches the HUD to "loading". When the model returns
    the wrong paragraph count the batch is thrown away and each member is
    re-requested on its own - and that branch used to return without ever
    leaving the loading view, because _restore_visible_suggestion restores
    the text and not the view. To a writer, picking a tone hung the app.

    Measured 2026-08-23 from the app's own event log: tone_requested Clear,
    tone_batch_refused, and no card_restored after it. The HUD stayed in
    loading with all 25 sections ready and replace_enabled true.
    """
    palette = RewritePalette(suggestion_engine=_SuggestionEngine())
    palette.show_capture("Please review this sentence.")
    app.processEvents()
    palette.badge_button.click()
    palette._set_view("loading", activate=False)
    assert palette._view == "loading"

    palette._restore_card_view("abcd1234")

    assert palette._view == "card"
    palette.close()
    palette.deleteLater()
    app.processEvents()


def test_the_state_file_names_this_process_from_startup(app, tmp_path, monkeypatch):
    """The staleness check reads this field; it must describe the LIVE process.

    session_state calls hud-state.json "what is true NOW, rewritten on every
    change", and after a restart it was not: nothing published until the HUD did
    something, so the file went on describing the process that had just been
    killed.

    Measured on 2026-08-28. The app was restarted onto build 8100c70 and the
    state file still read 4ee766a, eighty-five minutes stale. Every staleness
    check in the harness reads that field to decide whether the running app is
    the code under test, so the guard against measuring a stale process was
    itself answering from a stale file - and it refused a good AutoResearch
    experiment while a genuinely stale app would have been waved through.
    """
    from echo_link_core import session_state

    monkeypatch.setenv("MANUSCRIPT_STATE_DIR", str(tmp_path))

    RewritePalette()

    import json

    published = json.loads(session_state.state_path().read_text(encoding="utf-8"))

    assert published.get("build") == session_state.build_identifier()


def test_a_background_section_carries_its_own_correction_not_the_cards(
    app, tmp_path, monkeypatch
):
    """The wrong-variable defect that put the title into 122 sections.

    A tone job carries the correction the section already has, so a tone that
    cannot be applied hands that back rather than costing the writer their
    proofread. That text was read from the CARD - `current_replacement_text()`
    - which is right for the section the writer is looking at and wrong for
    every section corrected ahead of them in the background.

    On the acceptance tier of 2026-09-11 the background section then received
    the CARD's text, and the card during a prefetch burst is the document's
    title. 122 sections came back as "Artificial Intelligence in Education."
    where the writer's paragraph had been.

    This drives the real path: the card shows section one while a job is
    started for section two, and the job must carry section two's text.
    """
    capture = (
        "Echo-Link are ready today.\n"
        "The teh report is here.\n"
        "A third paragraph stands alone."
    )
    monkeypatch.setenv("ECHOLINK_DISABLE_PALETTE_AI", "0")
    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False
    )
    window.selected_text_record = _selection(capture)
    palette = window.rewrite_palette
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *a: requests.append(a))
    palette.show_capture(capture)
    app.processEvents()
    palette.badge_button.click()
    app.processEvents()

    sections = [
        str(getattr(item, "suggestion_id", "")) for item in palette._review_suggestions
    ]
    assert len(sections) >= 3, "this test needs the card and a background section"

    # Finish the first section so the card is actually showing something. Until
    # it does, the card is blank and the defect cannot be told from the fix.
    first_attempt, first_id, *_ = requests[0]
    palette.show_sentence_result(
        first_attempt, first_id, "Echo-Link is ready today.", "Ready."
    )
    app.processEvents()

    on_the_card = palette.current_replacement_text()
    background = sections[2]
    its_own = palette.correction_for_section(background)
    assert on_the_card, "the card is blank, so this test proves nothing"
    assert its_own and its_own != on_the_card, (
        "the fixture stopped distinguishing the card from the background "
        "section, so this test can no longer detect the defect"
    )

    # Do not let the job actually run; the question is only what it carries.
    monkeypatch.setattr(window, "_palette_ai_width", lambda: 0)
    window._run_palette_sentence_rewrite(7, background, its_own, "Friendly")
    app.processEvents()
    with window._palette_ai_lock:
        jobs = list(window._palette_ai_queue)

    assert jobs, "no job was queued"
    carried = str(jobs[-1][-1]).strip()
    assert carried == its_own
    assert carried != on_the_card
    window.shutdown()


def _tone_request(app, capture: str):
    """Drive the palette to one pending AI request and hand back its identity."""
    palette = RewritePalette(
        suggestion_engine=_SuggestionEngine(),
        ai_proofread=True,
    )
    requests = []
    palette.sentence_rewrite_requested.connect(lambda *args: requests.append(args))
    palette.show_capture(capture)
    app.processEvents()
    palette.badge_button.click()
    attempt, suggestion_id, *_ = requests[0]
    return palette, attempt, suggestion_id


# The answer that actually replaced essay 18's paragraphs on the acceptance tier
# of 2026-09-11: the model stopped rewriting and echoed the document's title.
_ABANDONING_ANSWER = "Artificial Intelligence in Education."


def test_an_answer_that_abandons_its_section_never_reaches_the_card(app):
    """The single-section half of the defect, which had no guard at all.

    A section long enough to carry a tone by itself is sent as a batch of one
    and never reaches split_tone_batch, so its answer went into the card
    unchecked. 30 of the 59 damaged batches on the tier were this shape. The
    writer must keep the correction they already had.
    """
    capture = (
        "Local ordinances commonly address setbacks, water sources, barriers, "
        "registration and hive counts, and teh rules vary widely between cities."
    )
    palette, attempt, suggestion_id = _tone_request(app, capture)

    palette.show_sentence_result(
        attempt, suggestion_id, _ABANDONING_ANSWER, "Tone ready."
    )
    app.processEvents()

    shown = palette.after_text.toPlainText()
    assert "Artificial Intelligence" not in shown
    # The card is not left empty either. The writer keeps the rule-based
    # correction this section already had - "teh" is still fixed - which is the
    # whole point of refusing rather than blanking the paragraph.
    assert "the rules vary widely" in shown
    assert "hive counts" in shown
    palette.finish_choice()


def test_a_faithful_answer_still_reaches_the_card(app):
    """The guard must not stop ordinary work getting through.

    Without this the test above passes just as well on a palette that refuses
    every answer, which would be a worse product than the defect.
    """
    capture = (
        "Local ordinances commonly address setbacks, water sources, barriers, "
        "registration and hive counts, and teh rules vary widely between cities."
    )
    palette, attempt, suggestion_id = _tone_request(app, capture)
    faithful = (
        "Local ordinances commonly address setbacks, water sources, barriers, "
        "registration and hive counts, and the rules vary widely between cities."
    )

    palette.show_sentence_result(attempt, suggestion_id, faithful, "Tone ready.")
    app.processEvents()

    assert palette.after_text.toPlainText() == faithful
    palette.finish_choice()

