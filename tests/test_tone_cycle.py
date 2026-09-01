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


def test_a_stale_app_is_a_crash_and_the_batch_never_runs(monkeypatch):
    """The defect that would have made every tone experiment a false discard.

    The cycle edits polish_quality.py, but the app is a long-running process
    that already holds the OLD contract. Without restarting and then CHECKING,
    the batch measures the baseline again and records "no improvement" for an
    idea that was never actually tried.

    autoresearch_run carries the same guard and the scar behind it: three runs
    in two days measured a process that was not the build under test, and one
    cost six hours.

    Asserting the batch is never launched, not merely that the return value is
    None - a version that ran Word for twenty minutes and then reported stale
    would pass a weaker test while wasting the measurement.
    """
    import autoresearch_run
    import autoresearch_tone_cycle as cycle

    launched = []

    def explode(*args, **kwargs):
        launched.append(args)
        raise AssertionError("the batch ran against a stale app")

    monkeypatch.setattr(autoresearch_run, "_restart_word", lambda: None)
    monkeypatch.setattr(autoresearch_run, "_stale_reason", lambda: "build 1234 != 5678")
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: None)

    percent, reason = cycle.measure("Shorten", Path("build/unused.log"))

    assert percent is None
    assert "stale app" in reason
    assert "1234" in reason
    assert not launched


def test_a_lower_is_better_metric_is_not_judged_by_the_higher_is_better_rule():
    """Latency must not be optimised backwards.

    The keep rule was written for scores where higher wins. Handing it a
    latency, where lower wins, would keep the SLOWEST result every time and
    record it as an improvement - a loop confidently going the wrong way while
    every row in results.tsv reads "keep". Nothing about the log would look
    wrong.
    """
    from autoresearch_cycle import decide

    assert decide(15.0, 12.0, higher_is_better=False) == "keep"
    assert decide(15.0, 18.0, higher_is_better=False) == "discard"
    # Equal is still a discard, so a no-op cannot be recorded as progress.
    assert decide(15.0, 15.0, higher_is_better=False) == "discard"
    # And the default is unchanged, so no existing caller shifts direction.
    assert decide(15.0, 18.0) == "keep"


def test_no_baseline_means_no_bar_for_a_lower_is_better_metric():
    """0.0 is an impossible floor when smaller wins, not a strict one.

    For a score, a 0.0 baseline is the honest "nothing measured yet" bar and
    anything beats it. For a latency it would be a bar nothing can ever beat,
    so every first measurement would be discarded and the slice could never
    acquire a baseline at all.
    """
    from autoresearch_cycle import decide

    assert decide(0.0, 18.0, higher_is_better=False) == "keep"


def test_a_run_served_by_two_packs_is_a_crash_not_a_score(monkeypatch):
    """The cause of the tone spread that made every verdict this session void.

    The adaptive runtime silently swaps in the standby pack when the primary
    cannot answer in time. A ~4B model writes weaker rewrites, those fail the
    meaning guards, a failed guard falls back to deterministic rules, and rules
    cannot do tone - so the score tracks how many sections happened to be handed
    to the small model rather than how good the tone is.

    Measured: the same source essay, judge and tone gave 35.7, 7.1 and 28.6
    across three runs, while the judge returned 28.6 three times out of three on
    a fixed document.

    Asserting the JUDGE never runs, not merely that the result is None. A
    version that judged a contaminated document and then discarded the number
    would pass a weaker test while burning a 27B model for nothing.
    """
    import autoresearch_run
    import autoresearch_tone_cycle as cycle

    judged = []
    monkeypatch.setattr(autoresearch_run, "_restart_word", lambda: None)
    monkeypatch.setattr(autoresearch_run, "_stale_reason", lambda: "")
    monkeypatch.setattr(autoresearch_run, "_log_length", lambda: 0)
    monkeypatch.setattr(
        autoresearch_run,
        "_packs_that_served",
        lambda _mark: {"gemma4-12b-qat-v1", "gemma4-e4b-qat-v1"},
    )
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: judged.append(a))

    percent, reason = cycle.measure("Friendly", Path("build/unused.log"))

    assert percent is None
    assert "more than one pack" in reason
    assert "e4b" in reason
    # The batch runs; the judge must not.
    assert len(judged) <= 2


def test_every_pack_that_served_is_reported_not_just_the_last(tmp_path, monkeypatch):
    """`_served_pack` answers 'what is loaded now', which is the wrong question.

    It returns the LAST pack it sees. In the retained log 33 of 629 requests
    were served by the e4b and 596 by the 12b, and because the last was the 12b
    the run reported as clean. A check that reports the final value rather than
    whether anything varied is the defect shape that has cost this session most
    of its wrong turns.
    """
    import autoresearch_run

    log = tmp_path / "echo-link.log"
    log.write_text(
        "Polish plan pack=gemma4-12b-qat-v1 model=x backend=cuda\n"
        "Polish plan pack=gemma4-e4b-qat-v1 model=y backend=cuda\n"
        "Polish plan pack=gemma4-12b-qat-v1 model=x backend=cuda\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autoresearch_run, "APP_LOG", log)

    assert autoresearch_run._packs_that_served(0) == {
        "gemma4-12b-qat-v1",
        "gemma4-e4b-qat-v1",
    }
    # The old helper sees only the last, which is exactly why it could not
    # catch this.
    assert autoresearch_run._served_pack(0)[0] == "gemma4-12b-qat-v1"


def test_a_downgraded_pack_is_refused_before_the_batch_runs(monkeypatch):
    """Fail fast, at prewarm, not after twenty-five minutes of Word.

    The post-run guard refused five of six tone baselines on 2026-09-01, each
    after driving Word for about twenty-five minutes - two hours of machine time
    for one number. The condition is visible the moment the app prewarms, so
    there is no reason to spend the essays first.
    """
    import autoresearch_run
    import autoresearch_tone_cycle as cycle

    ran = []
    monkeypatch.setattr(autoresearch_run, "_restart_word", lambda: None)
    monkeypatch.setattr(autoresearch_run, "_stale_reason", lambda: "")
    monkeypatch.setattr(autoresearch_run, "_log_length", lambda: 0)
    monkeypatch.setattr(
        autoresearch_run, "_served_pack", lambda _m: ("gemma4-e4b-qat-v1", "m", "cuda")
    )
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: ran.append(a))

    percent, reason = cycle.measure("Friendly", Path("build/unused.log"))

    assert percent is None
    assert "e4b" in reason and cycle.REQUIRED_PACK in reason
    # Only the app restart may have run. The essay batch must not.
    assert not any("run_essays_batch" in str(a) for a in ran)


def test_an_unknown_pack_does_not_block_the_run(monkeypatch):
    """A log that cannot be read must not become a permanent refusal.

    `_served_pack` returns "unknown" when it cannot find a plan line, which
    happens on a fresh log. Treating that as a downgrade would make the cycle
    refuse every run on a clean machine - a guard that fails closed on missing
    evidence stops all work rather than protecting it.
    """
    import autoresearch_run
    import autoresearch_tone_cycle as cycle

    monkeypatch.setattr(autoresearch_run, "_restart_word", lambda: None)
    monkeypatch.setattr(autoresearch_run, "_stale_reason", lambda: "")
    monkeypatch.setattr(autoresearch_run, "_log_length", lambda: 0)
    monkeypatch.setattr(
        autoresearch_run, "_served_pack", lambda _m: ("unknown", "m", "cuda")
    )
    monkeypatch.setattr(
        autoresearch_run, "_packs_that_served", lambda _m: {"gemma4-12b-qat-v1"}
    )
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: None)

    percent, reason = cycle.measure("Friendly", Path("build/unused.log"))

    # It proceeds past the pack check; it stops later for a different reason.
    assert "prewarmed" not in reason
