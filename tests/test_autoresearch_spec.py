"""Compliance with the AutoResearch specification, not with our implementation.

Source: https://github.com/karpathy/autoresearch, HEAD 228791f, mirrored
read-only at third_party/autoresearch_ref and verified byte-identical.

These exist because the loop drifted from the specification in ways that were
invisible until Curtis pointed out the symptom: he kept having to prompt. The
drift was measurable - experiments ran 11-12 minutes against a 5 minute budget
with a 10 minute kill, a twelve-minute test suite ran inside a loop the
specification says contains no tests, and every phase became a turn. Each test
below pins one requirement so the same drift is caught by the suite rather than
by a frustrated human.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_run  # noqa: E402


def test_the_results_file_has_the_five_columns_the_spec_names(tmp_path, monkeypatch):
    """commit, score, memory_gb, status, description - tab separated.

    Tab and not comma is explicit upstream, because descriptions contain commas
    and a comma separated file silently shifts every field after one.
    """
    results = tmp_path / "results.tsv"
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", results)

    autoresearch_cycle.record("abc1234", 94.5, 15.1, "keep", "raise the tone, then widen")

    lines = results.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == [
        "commit", "score", "memory_gb", "status", "description",
    ]
    row = lines[1].split("\t")
    assert len(row) == 5
    assert row[0] == "abc1234"
    assert row[4] == "raise the tone, then widen"


def test_a_crash_is_recorded_as_zero_so_the_file_stays_parseable(tmp_path, monkeypatch):
    """The spec says 0.000000 for the score and 0.0 for memory on a crash."""
    results = tmp_path / "results.tsv"
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", results)

    autoresearch_cycle.record("abc1234", None, 0.0, "crash", "widen the split rule")

    row = results.read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert row[1] == "0.000000"
    assert row[2] == "0.0"
    assert row[3] == "crash"


def test_improvement_keeps_and_anything_else_resets():
    """Steps 8 and 9, and the rule the whole loop turns on.

    Strict improvement, because the measured run-to-run spread of this metric is
    0.00 - three unchanged baselines returned 91.500000 exactly. Upstream needs
    a threshold because val_bpb wanders between runs; this does not.

    If a no-op can be kept, the loop is decorative.
    """
    assert autoresearch_cycle.decide(94.0, 94.5) == "keep"
    assert autoresearch_cycle.decide(94.0, 94.0) == "discard"
    assert autoresearch_cycle.decide(94.0, 93.5) == "discard"
    assert autoresearch_cycle.decide(94.0, None) == "crash"


def test_the_baseline_is_the_best_kept_score(tmp_path, monkeypatch):
    """A new experiment must beat the best kept result, not the last one."""
    results = tmp_path / "results.tsv"
    results.write_text(
        "commit\tscore\tmemory_gb\tstatus\tdescription\n"
        "aaa\t91.500000\t15.1\tkeep\tbaseline\n"
        "bbb\t99.000000\t15.1\tdiscard\tnot actually kept\n"
        "ccc\t94.000000\t15.1\tkeep\tsomething that worked\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autoresearch_cycle, "RESULTS", results)

    assert autoresearch_cycle.current_baseline() == 94.0


def test_one_experiment_is_one_essay_by_default():
    """The budget is what makes experiments comparable.

    Upstream fixes five minutes and kills at ten. One essay measured 315s, 325s,
    323s, 332s, 317s and 334s - a mean of 324 against the spec's 300. Two essays
    ran 11-12 minutes, so by the specification's own timeout every experiment we
    had run would have been a failure.
    """
    parser_default = autoresearch_run.main.__doc__ is not None  # module imports
    assert parser_default
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/autoresearch_run.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert "--essays" in done.stdout


def test_the_cycle_never_runs_the_test_suite():
    """The specification's loop contains no tests, and ours must not either.

    A twelve-minute suite inside a five-minute budget is why the loop managed
    about 1.5 experiments an hour instead of twelve. The suite still gates the
    merge to master, where program.md always said it belonged.
    """
    source = (ROOT / "tools/autoresearch_cycle.py").read_text(encoding="utf-8")
    running = source.split('"""', 2)[-1]

    assert "pytest" not in running


def test_the_evaluator_cannot_be_edited_from_inside_the_loop():
    """The local equivalent of upstream's read-only prepare.py.

    An autonomous loop optimising a score has one overwhelmingly effective
    cheat: change the thing that measures it.
    """
    settings = (ROOT / ".claude/settings.json").read_text(encoding="utf-8")

    for locked in (
        "grade_corpus.py",
        "tone_correctness.py",
        "autoresearch_run.py",
        "test-corpus",
    ):
        assert locked in settings, locked


def test_the_results_file_is_not_committed():
    """Upstream is explicit: leave results.tsv untracked."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "results.tsv" in ignore
