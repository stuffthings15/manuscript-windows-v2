"""A section whose AI answer was refused must not be asked again forever.

Measured on essay 17 of the animal-pet corpus, 2026-09-30, with the local AI
genuinely on the GPU. One 126-character section came back as 79 characters,
seven times, byte-identical:

    19:09:06  adaptive Polish request   command=Proofread characters=126
    19:09:07  adaptive Polish completed command=Proofread characters=79
    19:09:07  Writing HUD answer refused: it abandoned its section
    19:09:18  ... the same three lines again, and again, and again

Every other section that run was healthy - 375->383, 579->577, 693->629,
563->564, 852->806 - so this was one span, not general truncation.

answer_abandons_the_source did its job each time and filed the section in
_ai_failed_ids. The loop came from _request_ai_proofread_current: its
stale-mark rule clears a requested mark whenever no request is in flight, and
for a failed section nothing ever is. So the mark was cleared, the section was
asked again, refused again, filed again.

The essay finished "sections=13 accepted=12 skipped=1" with
"section ? offered neither a correction nor a skip within 150.0s" - the
harness timing out on this one section - and took 490s overall.

_request_next_ai_prefetch already skipped _ai_failed_ids. This path did not.

The stale-mark rule is correct and stays; see test_hud_stale_request_mark.
It exists for the 2026-09-01 nine-hour freeze, which is a section with no
answer. This is a section whose answer was heard and turned down.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _palette(failed=(), completed=(), requested=()):
    """A palette with only the fields _request_ai_proofread_current reads."""
    from echo_link_app.rewrite_palette import RewritePalette

    palette = RewritePalette.__new__(RewritePalette)
    palette._ai_proofread = True
    palette._busy = False
    palette._busy_command = ""
    palette._view = "card"
    palette._ai_attempts = {}
    palette._ai_batch_members = {}
    palette._active_ai_attempt_id = 0
    palette._ai_failed_ids = set(failed)
    palette._ai_completed_ids = set(completed)
    palette._ai_requested_ids = set(requested)
    palette._declined_reasons = []

    def _declined(reason, suggestion_id=""):
        palette._declined_reasons.append(reason)
        return False

    palette._declined_ai_request = _declined
    return palette


def _suggestion(suggestion_id="sentence:abc", text="x" * 126):
    class _S:
        pass

    s = _S()
    s.suggestion_id = suggestion_id
    s.replacement = text
    return s


def test_a_refused_section_is_not_requested_again():
    """The loop itself. Without the guard this returns True and asks again."""
    palette = _palette(failed={"sentence:abc"})
    palette._current_review_suggestion = lambda: _suggestion()

    assert palette._request_ai_proofread_current() is False
    assert any("refused" in r for r in palette._declined_reasons), (
        f"expected a refusal reason, got {palette._declined_reasons}"
    )


def test_a_refused_section_is_not_rescued_by_the_stale_mark_rule():
    """The exact shape of the defect.

    The section is BOTH failed and still carrying a requested mark with
    nothing in flight - which is precisely when the stale-mark rule fires.
    The failed check has to come first or the loop returns.
    """
    palette = _palette(failed={"sentence:abc"}, requested={"sentence:abc"})
    palette._current_review_suggestion = lambda: _suggestion()

    assert palette._ai_request_in_flight("sentence:abc") is False
    assert palette._request_ai_proofread_current() is False


def test_an_ordinary_section_is_still_requested():
    """The guard must not swallow healthy sections."""
    palette = _palette()
    palette._current_review_suggestion = lambda: _suggestion()
    started = {}

    def _start(suggestion_id, source, command, label, members):
        started["id"] = suggestion_id

    palette._start_sentence_ai_request = _start
    palette._selected_tone_command = lambda automatic=True: "Proofread"
    palette._tone_batch_ids = lambda sid, cmd: (sid,)

    class _Combo:
        def currentText(self):
            return "Clear"

    palette.tone_combo = _Combo()

    assert palette._request_ai_proofread_current() is True
    assert started["id"] == "sentence:abc"


def test_a_stale_mark_with_no_failure_is_still_rescued():
    """The 2026-09-01 freeze fix must survive this change."""
    palette = _palette(requested={"sentence:abc"})
    palette._current_review_suggestion = lambda: _suggestion()
    started = {}

    def _start(suggestion_id, source, command, label, members):
        started["id"] = suggestion_id

    palette._start_sentence_ai_request = _start
    palette._selected_tone_command = lambda automatic=True: "Proofread"
    palette._tone_batch_ids = lambda sid, cmd: (sid,)

    class _Combo:
        def currentText(self):
            return "Clear"

    palette.tone_combo = _Combo()

    assert palette._request_ai_proofread_current() is True
