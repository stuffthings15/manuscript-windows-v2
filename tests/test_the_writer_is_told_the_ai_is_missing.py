"""Coming up rules-only turns the main feature off, silently.

`_show_ready_state` reported a hotkey that would not register and a speech
model that would not load, and said nothing at all about the corrector. When
the writing AI came up rules-only - another program holding the GPU at launch,
a machine below the VRAM floor, Ollama not installed - the panel said "Ready"
in green and the writer found out only when a review card rendered with no AI
in it.

The reason existed. `_warm_adaptive_polish` logged it at WARNING and threw it
away:

    logger.warning("Adaptive Polish came up rules-only: %s", ...)

A log the writer has no reason to open is not a way of telling them. This is
the same shape as the capture failures in windows_selection: the evidence was
produced and then discarded by the code holding it.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_app.ai_status import user_ai_status  # noqa: E402


class _Window:
    """The two attributes _show_ready_state needs, and nothing else.

    Constructing a real EchoLinkWindow needs Qt, a database and a model probe;
    the branch under test is a two-line decision about which status to show.
    """

    def __init__(self, reason=""):
        self._writing_ai_reason = reason
        self.shown = None

    def _set_status(self, text, color):
        self.shown = (text, color)


def _ready_state_branch(window, speech_available=True):
    """The decision _show_ready_state makes once the hotkeys are healthy."""
    if not speech_available:
        window._set_status("Speech AI unavailable. Open Settings to repair it.", "amber")
    elif window._writing_ai_reason:
        window._set_status(window._writing_ai_reason, "amber")
    else:
        window._set_status("Ready", "green")


def test_a_healthy_machine_still_says_ready():
    window = _Window()
    _ready_state_branch(window)
    assert window.shown == ("Ready", "green")


def test_a_rules_only_machine_says_so():
    window = _Window("Local AI is not available. Corrections use built-in rules.")
    _ready_state_branch(window)
    assert window.shown[0] == "Local AI is not available. Corrections use built-in rules."
    assert window.shown[1] == "amber", "this is a warm-up state, not a hard failure"


def test_speech_still_takes_precedence():
    """Dictation is the more basic feature; its failure is reported first."""
    window = _Window("Local AI is not available.")
    _ready_state_branch(window, speech_available=False)
    assert "Speech AI unavailable" in window.shown[0]


def test_the_reason_never_names_the_model_or_the_runtime():
    """The laundering convention this codebase already keeps.

    downgrade_reason can name a pack or a runtime, and those names are kept out
    of user-facing text - which is what user_ai_status exists for.
    """
    for raw in (
        "gemma4-12b-qat-v1 could not load",
        "ollama is not installed or is not available on PATH",
        "the llama.cpp host failed to start",
        "qwen3:14b timed out",
    ):
        shown = user_ai_status(raw)
        lowered = shown.casefold()
        for vendor in ("gemma", "ollama", "llama", "qwen"):
            assert vendor not in lowered, (
                f"{vendor!r} reached user-facing text through {raw!r} -> {shown!r}"
            )


def test_the_window_reports_the_writing_ai_in_its_ready_state():
    """The live check: the branch is actually in _show_ready_state.

    The tests above pin the decision; this pins that the product makes it.
    """
    import ast

    tree = ast.parse(
        (ROOT / "src" / "echo_link_app" / "window.py").read_text(encoding="utf-8")
    )
    ready = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_show_ready_state"
    )
    reads = {
        node.attr
        for node in ast.walk(ready)
        if isinstance(node, ast.Attribute)
    }
    assert "_writing_ai_reason" in reads, (
        "_show_ready_state does not consult the writing AI's state, so a "
        "rules-only machine reports 'Ready' in green."
    )


def test_a_missing_runtime_says_so_instead_of_saying_nothing():
    """The likeliest first-run state produced the least useful sentence.

    "Ollama is not installed or is not available on PATH" fell through every
    branch to the catch-all "Local AI status updated.", which tells a writer
    nothing. On a machine that has just installed Manuscript and nothing else,
    that IS the state - so the most common case had the worst message, and it
    undercut the point of surfacing the rules-only reason at all.
    """
    # Both name a runtime. user_ai_status deliberately passes a message that
    # names none straight through, because it is already safe to show, so a
    # vendor-free string is not a case this branch applies to.
    for raw_message in (
        "Ollama is not installed or is not available on PATH.",
        "ollama was not found on PATH",
    ):
        shown = user_ai_status(raw_message)
        assert shown != "Local AI status updated.", (
            f"{raw_message!r} still produces the catch-all, which says nothing"
        )
        assert "not installed" in shown.casefold()
        assert "settings" in shown.casefold(), (
            "the writer is told what is wrong but not where to go about it"
        )
        assert "ollama" not in shown.casefold(), "the runtime was named"


def test_a_runtime_that_is_installed_but_broken_reads_differently():
    """Missing and broken need different answers; one is actionable."""
    missing = user_ai_status("Ollama is not installed or is not available on PATH.")
    broken = user_ai_status("gemma4-12b-qat-v1 could not load")
    assert missing != broken
    assert "Retry" in broken, "a transient failure should suggest retrying"
    assert "Retry" not in missing, (
        "retrying does nothing when the runtime is not installed at all"
    )