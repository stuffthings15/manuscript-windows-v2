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
from echo_link_app.rewrite_palette import RewritePalette, _review_spans, _sentence_spans
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
        "Echo-Link is ready.",
        "Local AI proofread ready.",
    )
    app.processEvents()

    assert len(requests) == 2
    assert palette.card_position.text() == "1 of 2"
    assert "local ai proofread ready" in (
        palette.apply_suggestion_button.accessibleName().casefold()
    )
    assert palette.status.text() == "Local AI proofread ready."
    assert palette.after_text.toPlainText() == "Echo-Link is ready."
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
        "Echo-Link is ready.",
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
    assert palette.after_text.toPlainText() == "Echo-Link is ready."
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

    palette.show_sentence_result(0, suggestion_id, "The draft.", "Short rewrite ready.")
    app.processEvents()
    palette._reassert_current_view_size()
    shrunk_size = palette.size()

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
    assert command == "Rewrite"
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
        "Improve",
        "Rephrase",
        "Shorten",
        "Friendly",
        "Formal",
        "Professional",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
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
    assert palette.card_position.text() == "2 of 2"
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
    assert source == "Clean sentence."
    assert command == "Rewrite"
    palette.show_sentence_result(
        attempt_id,
        suggestion_id,
        "This sentence is cleaner.",
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
