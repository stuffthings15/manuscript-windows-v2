"""The tone cycle's contract, minus the twenty-minute Word run.

Everything asserted here is a decision the cycle makes about a number it has
already been handed. The measurement itself needs Word and the judge and cannot
be a unit test, so the parts that CAN be tested are the ones that have gone
wrong before: recording an absent measurement as a bad one, and comparing a tone
against a baseline that belongs to something else.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import autoresearch_cycle  # noqa: E402
import autoresearch_tone_cycle  # noqa: E402


def test_a_tone_is_tagged_as_a_tone_not_as_essays():
    """The row has to say what kind of thing produced the number.

    results.tsv is fixed at the specification's five columns, so the slice lives
    in the description. Tagging a judge percentage "essays Shorten:" would be a
    lie in the one field that says what a number means, and would also pool it
    with the corpus scores - which are on a different scale entirely, so the
    baseline would be nonsense in both directions.
    """
    assert autoresearch_cycle.slice_tag("Shorten", kind="tone") == "tone Shorten:"
    assert autoresearch_cycle.slice_tag("8") == "essays 8:"


def test_a_tone_baseline_never_sees_a_corpus_score(tmp_path, monkeypatch):
    """98.0 from essay 8 must not become the bar a tone has to beat.

    Corpus scores sit near 100 and tone percentages near 20, so one leaking into
    the other would make every tone experiment an automatic discard - which in
    the log is indistinguishable from having no ideas.
    """
    results = tmp_path / "results.tsv"
    results.write_text(
        "commit\tscore\tmemory_gb\tstatus\tdescription\n"
        "aaaaaaa\t98.000000\t15.1\tkeep\tessays 8: something good\n"
        "bbbbbbb\t21.400000\t0.0\tkeep\ttone Shorten: a first number\n"
        "ccccccc\t41.200000\t0.0\tkeep\ttone Friendly: a different tone\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", results)

    assert autoresearch_cycle.current_baseline("Shorten", kind="tone") == 21.4
    assert autoresearch_cycle.current_baseline("Friendly", kind="tone") == 41.2
    assert autoresearch_cycle.current_baseline("8") == 98.0


def test_an_unmeasured_tone_is_a_crash_and_never_a_zero():
    """The mistake that invalidated five tone numbers on 2026-08-20.

    Three tones were judged after runs that had applied nothing, each came back
    at 0.0 percent, and that read as total product failure. A zero is a claim
    about the product; an absent measurement is a claim about the run.
    """
    assert autoresearch_cycle.decide(21.4, None) == "crash"
    assert autoresearch_cycle.decide(0.0, None) == "crash"


def test_the_keep_rule_is_strict_improvement():
    """Equal is a discard, so a no-op cannot be recorded as progress."""
    assert autoresearch_cycle.decide(21.4, 41.2) == "keep"
    assert autoresearch_cycle.decide(21.4, 21.4) == "discard"
    assert autoresearch_cycle.decide(21.4, 7.1) == "discard"


def test_the_judge_and_corpus_match_the_release_gate():
    """A number that does not mean what the gate means is not worth having.

    A cheaper judge model or a different corpus would still produce a tidy
    percentage, and every comparison against RELEASE_ACCEPTANCE.md would be
    quietly wrong.
    """
    import release_acceptance

    assert autoresearch_tone_cycle.JUDGE_MODEL == release_acceptance.JUDGE_MODEL
    assert autoresearch_tone_cycle.CORPUS == release_acceptance.COHERENT


def test_the_judged_essay_is_the_one_the_gate_judges():
    """The gate judges the FIRST essay of the corpus; so must this."""
    import release_acceptance

    source, corrected = autoresearch_tone_cycle._essay_paths()
    assert source == sorted(release_acceptance.COHERENT.glob("*.docx"))[0]
    assert corrected.name == source.name
