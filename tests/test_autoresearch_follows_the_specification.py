"""The apparatus must obey the upstream AutoResearch specification.

https://github.com/karpathy/autoresearch/blob/master/program.md

These tests exist because the specification was already violated once, silently,
by a paraphrase. Local `program.md` rendered the upstream FIXED five-minute
budget - "a fixed time budget of 5 minutes (wall clock training time, excluding
startup/compilation)" - as "about 5 minutes per essay". Read that way, a
twenty-document run taking 36 minutes looked compliant. It is seven times the
budget. The loop ran at 1.6 experiments an hour where the spec is built for
about 12, and a 150-experiment programme went from one night to four days.

Prose cannot hold a specification. Every rule below is one an agent got wrong or
could get wrong again, so each is asserted rather than described.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = (ROOT / "program.md").read_text(encoding="utf-8")

# Upstream: five minutes of measurement per experiment, kill at ten.
EXPERIMENT_BUDGET_SECONDS = 300
EXPERIMENT_KILL_SECONDS = 600

# Measured 2026-09-03 over 20 shapes documents: 78.5s to 129.8s, mean about 100.
SECONDS_PER_DOCUMENT = 100


def _time_budget_rule() -> str:
    """The rule itself, not the history recorded underneath it.

    The section explains the old wording in order to stop it coming back, so a
    search of the whole file finds the very phrase it forbids. Only the rule is
    binding, so only the rule is checked.
    """
    after = PROGRAM.split("**Time budget**", 1)[1]
    return after.split("\n\n", 1)[0]


def test_the_budget_is_fixed_not_per_document():
    """The exact mistranslation that cost the programme four days."""
    rule = _time_budget_rule()
    assert "per essay" not in rule and "per document" not in rule.replace(
        "not five minutes per document", ""
    ), "the fixed budget has been paraphrased back into a per-document one"
    assert "FIXED FIVE MINUTES" in rule


def test_a_documents_run_fits_the_budget():
    """Five minutes at ~100s a document is three documents, not twenty."""
    affordable = EXPERIMENT_BUDGET_SECONDS // SECONDS_PER_DOCUMENT
    assert affordable == 3
    assert 20 * SECONDS_PER_DOCUMENT > EXPERIMENT_KILL_SECONDS, (
        "a twenty-document run is past the kill threshold, not merely slow"
    )


def test_the_specification_says_to_measure_only_what_has_headroom():
    """Upstream: essays 8 and 10 "because those are the two weakest essays and
    therefore the only ones with headroom"."""
    assert "MEASURE ONLY WHAT HAS HEADROOM" in PROGRAM


def test_a_baseline_is_not_an_experiment():
    """Whole-corpus verification is a different job with a different budget.
    Paying for it on every idea is what consumed the night."""
    assert "A BASELINE IS NOT AN EXPERIMENT" in PROGRAM


def test_every_measuring_path_requires_idle():
    """The gate is not politeness.

    On 2026-09-03 a reliability run failed with `captured from 'Claude'` while
    the agent typed in its own session. --require-idle existed in exactly one
    tool and the reliability path never passed it.
    """
    assert "Idle gate" in PROGRAM
    measuring = (
        "autoresearch_run.py",
        "autoresearch_reliability_cycle.py",
        "run_essays_batch.py",
    )
    missing = [
        name
        for name in measuring
        if "require-idle" not in (ROOT / "tools" / name).read_text(encoding="utf-8")
        and "require_idle" not in (ROOT / "tools" / name).read_text(encoding="utf-8")
    ]
    assert not missing, f"these measure with no idle gate: {missing}"


def test_results_tsv_has_the_five_upstream_columns():
    """commit, metric, memory, status, description - tab separated."""
    ledger = ROOT / "results.tsv"
    if not ledger.is_file():
        return
    for number, line in enumerate(
        ledger.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        columns = line.split("\t")
        assert len(columns) == 5, f"row {number} has {len(columns)} columns"


def test_a_crash_is_zero_and_a_status_is_one_of_three():
    """Upstream: 0.000000 for crashes; keep, discard or crash.

    A crash is a claim about the RUN, a zero is a claim about the product, and
    confusing them is what voided five tone measurements.
    """
    ledger = ROOT / "results.tsv"
    if not ledger.is_file():
        return
    allowed = {"keep", "discard", "crash", "void", "status"}
    for number, line in enumerate(
        ledger.read_text(encoding="utf-8").splitlines(), start=1
    ):
        columns = line.split("\t")
        if len(columns) != 5 or columns[3] == "status":
            continue
        assert columns[3] in allowed, f"row {number} status {columns[3]!r}"
        if columns[3] == "crash":
            assert float(columns[1]) == 0.0, f"row {number} crashed with a score"


def test_the_suite_does_not_run_between_experiments():
    """Upstream runs no tests inside the loop; the suite gates the merge.

    A twelve-minute suite between five-minute experiments would more than triple
    the cost of every idea.
    """
    assert "DO NOT run the test suite between experiments" in PROGRAM


def test_the_loop_does_not_stop_to_ask():
    """Upstream: "do NOT pause to ask the human"."""
    assert "NEVER STOP" in PROGRAM
