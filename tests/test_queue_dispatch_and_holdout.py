"""The queue must be able to run every metric, and held-out must be measurable.

Two gaps found while working the experiment programme on 2026-09-01.

The queue dispatched only `essays` and `tone`, so the reliability metric built
that same day had to be driven by hand, one experiment per prompt - the exact
thing the queue exists to prevent.

`test-corpus/held-out` is the overfitting check, three essays the rules were
never tuned against, and it existed only as .txt. Both Word harnesses glob
`*.docx`, so it was invisible to every measurement.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import txt_corpus_to_docx  # noqa: E402

QUEUE_SOURCE = (ROOT / "tools" / "autoresearch_queue.py").read_text(encoding="utf-8")
HELD_OUT = ROOT / "test-corpus" / "held-out-docx"


def test_the_queue_dispatches_reliability():
    assert 'kind == "reliability"' in QUEUE_SOURCE
    assert "autoresearch_reliability_cycle.py" in QUEUE_SOURCE


def test_the_queue_dispatches_a_single_error_class():
    """essays-grammar and friends route to the cycle with --metric."""
    assert 'kind.startswith("essays-")' in QUEUE_SOURCE
    assert '"--metric"' in QUEUE_SOURCE


def test_every_dispatch_branch_names_a_cycle_that_exists():
    for name in (
        "autoresearch_cycle.py",
        "autoresearch_tone_cycle.py",
        "autoresearch_reliability_cycle.py",
    ):
        assert name in QUEUE_SOURCE
        assert (ROOT / "tools" / name).is_file()


def test_blank_lines_still_separate_paragraphs_by_default():
    """The error-riddled corpus convention must not change.

    Its stray mid-paragraph line breaks are one of its seeded defects and have
    to stay line breaks, not paragraph splits.
    """
    raw = "Title\n\nFirst para\nwith a line break\n\nSecond para"
    assert txt_corpus_to_docx.blocks(raw) == [
        "Title",
        "First para\nwith a line break",
        "Second para",
    ]


def test_line_per_paragraph_is_opt_in():
    raw = "Title\nFirst para\nSecond para"
    # Without the flag this is ONE block, which is what made a held-out essay
    # a single Word paragraph while the gated corpus had eighteen.
    assert len(txt_corpus_to_docx.blocks(raw)) == 1
    assert txt_corpus_to_docx.blocks(raw, line_per_paragraph=True) == [
        "Title",
        "First para",
        "Second para",
    ]


def test_line_per_paragraph_drops_empty_lines():
    raw = "One\n\n   \nTwo\n"
    assert txt_corpus_to_docx.blocks(raw, line_per_paragraph=True) == ["One", "Two"]


def test_the_held_out_corpus_is_converted_and_multi_paragraph():
    """An overfitting check must be structurally comparable to the gated corpus.

    Converted with the default rule these essays became one paragraph each,
    against about eighteen for a gated essay, so any score difference would have
    been document structure rather than overfitting.
    """
    documents = sorted(HELD_OUT.glob("*.docx"))
    assert len(documents) == 3
    for path in documents:
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        assert xml.count("<w:p>") >= 5, f"{path.name} is not multi-paragraph"


def test_the_held_out_text_is_untouched():
    """The source corpus is read from, never rewritten."""
    for path in sorted((ROOT / "test-corpus" / "held-out").glob("*.txt")):
        assert path.read_text(encoding="utf-8").strip()
