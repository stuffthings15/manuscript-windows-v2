"""Waiting out a busy Word, so the harness never has to kill it.

Killing Word between essays is not how anybody writes, and while it was
happening the run could not show what a writer gets on their second document:
the app restart that goes with it exists because the HUD carried one document's
review into the next. On 2026-08-15 a whole 20-essay run died on essay 1
because a Word left open from the previous run was still busy.
"""
from __future__ import annotations

import pytest

from tools.word_essay_session import com_retry

REJECTED = -2147418111  # RPC_E_CALL_REJECTED
RETRY_LATER = -2147417846  # RPC_E_SERVERCALL_RETRYLATER
NOT_BUSY = -2147352570  # DISP_E_UNKNOWNNAME, a real fault


def _com_error(hresult: int):
    from comtypes import COMError

    return COMError(hresult, "Call was rejected by callee.", (None,) * 5)


class _Word:
    """Answers busy a few times, the way Word does while it opens a document."""

    def __init__(self, busy_times: int, hresult: int = REJECTED) -> None:
        self.remaining = busy_times
        self.hresult = hresult
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise _com_error(self.hresult)
        return "the essay text"


@pytest.mark.parametrize("hresult", [REJECTED, RETRY_LATER])
def test_a_busy_word_is_waited_out_rather_than_killed(hresult: int) -> None:
    word = _Word(busy_times=3, hresult=hresult)

    assert com_retry(word, pause=0.0) == "the essay text"
    assert word.calls == 4


def test_a_word_that_answers_first_time_is_not_delayed() -> None:
    word = _Word(busy_times=0)

    assert com_retry(word, pause=0.0) == "the essay text"
    assert word.calls == 1


def test_a_real_com_fault_is_raised_at_once() -> None:
    """Only "busy" is retried. Everything else is a fault and must surface."""
    from comtypes import COMError

    word = _Word(busy_times=1, hresult=NOT_BUSY)

    with pytest.raises(COMError):
        com_retry(word, pause=0.0)
    assert word.calls == 1


def test_a_word_that_stays_busy_gives_up_and_says_so() -> None:
    from comtypes import COMError

    word = _Word(busy_times=99)

    with pytest.raises(COMError):
        com_retry(word, attempts=3, pause=0.0)
    assert word.calls == 3
