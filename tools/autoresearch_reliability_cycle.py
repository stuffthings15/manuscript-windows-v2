"""One whole AutoResearch experiment for RELIABILITY, from commit to keep-or-reset.

    .venv/Scripts/python.exe tools/autoresearch_reliability_cycle.py \
        --slice shapes --describe "pin the pack so the planner cannot downgrade"

Prints exactly one line: `KEEP 100.0`, `DISCARD 60.0` or `CRASH dead`.

WHY THIS EXISTS. The loop could only ever measure correction QUALITY, so it was
structurally blind to the thing that wasted the most machine time: of 55 logged
experiments, 11 produced no information at all - pack downgrades, a nine hour HUD
hang, harness faults. One experiment in five told us nothing, and the metric
could not see it.

The metric here is COMPLETION RATE: the share of documents whose Word run
actually finished, from the same `build/batch-summary.json` the release gate
reads, using the same `_run_was_truncated` rule so the two cannot drift on what
"finished" means. Higher is better, like the corpus score.

WHY THIS CORPUS. `test-corpus/document-shapes` carries the structures that broke
a real assignment on 2026-09-01 - automatic numbering, automatic bullets, two
numbering levels, trailing blank paragraphs, a non-breaking space - and which
the gated corpus has never contained. That corpus has no scoring ground truth,
so it cannot feed a quality metric, but completion needs no ground truth: either
the run finished or it did not. The two fit together exactly.

WHAT THIS DOES NOT DO: score the corrections. A reliability experiment that
raises completion by making the corrections worse is a bad trade, and this
cannot see that, which is why the plan requires the corpus score to be rechecked
before any reliability change is kept for good. Recorded here rather than
enforced, because the check belongs to the merge and not to the experiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402

CORPUS = ROOT / "test-corpus" / "document-shapes"

# One corpus per slice, so an EXPERIMENT and a BASELINE can cost different
# amounts. program.md fixes an experiment at five minutes, which at roughly 100
# seconds a document is three documents, and the spec's own reason for choosing
# essays 8 and 10 applies here: a document that already completes cannot improve
# and spends the budget proving it. "hard" holds the list documents that fail.
# "shapes" keeps all twenty for the baseline, where resolution is the point and
# the run is rare.
CORPORA = {
    "shapes": CORPUS,
    "hard": ROOT / "test-corpus" / "document-shapes-hard",
}


def corpus_for(name: str) -> Path:
    return CORPORA.get(name, CORPUS)


# The same 300 seconds `program.md` gives autoresearch_run, and a DEFAULT rather
# than an opt-in. An opt-in gate is how this path went without one: the flag
# existed, nothing here passed it, and nothing failed to say so.
_DEFAULT_REQUIRE_IDLE = 300.0
# run_essays_batch returns this when it stopped before a document because
# somebody was using the machine.
_STOPPED_FOR_IDLE = 2
SUMMARY = ROOT / "build" / "batch-summary.json"

# The pack a measurement must be served by, end to end. Anything smaller is a
# different product answering, which is the lesson the tone cycle paid for.
REQUIRED_PACK = "gemma4-12b-qat-v1"


def _documents(corpus: Path | None = None) -> list[Path]:
    return sorted((corpus or CORPUS).glob("*.docx"))


def completion_rate() -> tuple[float | None, str]:
    """(percentage of documents whose run finished, reason it is missing).

    Reads the batch summary the gate reads, and calls a run finished by the
    gate's own `_run_was_truncated`. A second opinion on what "finished" means
    is a second thing that can rot.
    """
    import release_acceptance

    try:
        rows = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the batch wrote no readable summary ({type(exc).__name__})"
    if not isinstance(rows, list) or not rows:
        return None, "the batch summary held no runs"
    # A HARNESS ERROR IS A CRASH, NOT A ZERO. program.md lists the crash shapes
    # - dead, partial, `captured from 'Claude'`, COMError - and says they "all
    # mean no score rather than a low one".
    #
    # On 2026-09-06 all three documents came back outcome='error' with "the
    # review on screen was captured from None", after 17 to 21 seconds instead
    # of the usual hundred. Not one of them was measured, and this scored the
    # run 0.0 and recorded it as a baseline. That is the crash-versus-zero
    # confusion the specification exists to prevent, and it is the third time
    # this loop has turned a failed measurement into a number.
    broken = [
        str(row.get("error") or row.get("outcome"))
        for row in rows
        if isinstance(row, dict) and row.get("outcome") == "error"
    ]
    if broken:
        return None, (
            f"{len(broken)} of {len(rows)} documents could not be measured: "
            f"{broken[0][:110]}"
        )
    finished = sum(
        1
        for row in rows
        if isinstance(row, dict) and not release_acceptance._run_was_truncated(row)
    )
    return 100.0 * finished / len(rows), ""


def measure(
    log: Path,
    *,
    require_idle: float = _DEFAULT_REQUIRE_IDLE,
    corpus: Path | None = None,
) -> tuple[float | None, str]:
    """(completion rate, reason it is missing). Never both.

    Restarts Word and the app first and refuses a stale build, for the reason
    `autoresearch_run` records and paid for three times in two days: a run that
    measures a process which is not the build under test is worse than no run,
    because it produces a number.
    """
    import autoresearch_run

    corpus = corpus or CORPUS
    documents = _documents(corpus)
    if not documents:
        return None, f"no documents under {corpus}"

    # THE IDLE GATE, which this path did not have until 2026-09-03.
    #
    # `program.md` requires it and only `autoresearch_run.py` implemented it, so
    # every reliability number ever taken was measured with somebody possibly at
    # the keyboard. That night it finally showed: 14_dashes died with
    # `the review on screen was captured from 'Claude', not from 14_dashes`
    # because this session was interactive and Curtis was typing into it while
    # the run was on screen. The agent does not generate input events; the
    # fault was starting a measurement during a live conversation at all.
    #
    # It also puts a name to the reliability metric's supposed noise floor. Four
    # runs of one corpus scored 80, 80, 60, 80 with a different document failing
    # each time, and that was recorded as randomness. A run interrupted by
    # whoever happens to be typing produces exactly that pattern.
    #
    # Returning a REASON rather than a score, because an interrupted run is a
    # claim about the room and never about the product.
    if require_idle > 0:
        idle = autoresearch_run.idle_seconds()
        if idle < require_idle:
            return None, (
                f"someone is at the keyboard ({idle:.0f}s idle, "
                f"{require_idle:.0f}s required)"
            )

    before_restart = autoresearch_run._log_length()
    autoresearch_run._restart_word()
    subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    stale = autoresearch_run._stale_reason()
    if stale:
        return None, f"stale app, {stale}"

    # Refused at prewarm rather than after driving Word for half an hour. The
    # tone cycle learned this the expensive way on 2026-09-01: five of six
    # baselines were thrown away AFTER the runs, and two hours of measurement
    # produced one number.
    # Read the prewarm from THIS restart, not from the whole log.
    #
    # `_served_pack` says in its own docstring that it must read after a mark,
    # "because the log is append-only across restarts and the pack from an
    # earlier session looks exactly like the pack from this one" - and both
    # cycles then called it with 0, scanning the entire file and returning the
    # LAST pack line in it.
    #
    # At prewarm that line belongs to the PREVIOUS tone's batch. Measured
    # 2026-09-02: Clear's first run was served partly by the ~4B standby, and
    # every run after it was then refused with "the app prewarmed
    # gemma4-e4b-qat-v1" while the app log said, four times in a row,
    # `prewarmed pack=gemma4-12b-qat-v1 loaded=True`. Four false refusals, and
    # they read exactly like a real GPU problem.
    #
    # The mark has to be taken BEFORE the restart. Taking it after, which is
    # where the old `mark` sat, is after the prewarm line has already been
    # written, so the guard would find nothing and pass everything.
    prewarmed = autoresearch_run._served_pack(before_restart)[0]
    mark = autoresearch_run._log_length()
    if prewarmed not in ("unknown", REQUIRED_PACK):
        return None, (
            f"the app prewarmed {prewarmed}, not {REQUIRED_PACK}; "
            f"free the GPU before measuring reliability"
        )

    SUMMARY.unlink(missing_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        batch = subprocess.run(
            [
                sys.executable, str(ROOT / "tools/run_essays_batch.py"),
                "--from", "1", "--to", str(len(documents)),
                "--corpus", str(corpus), "--as-a-writer",
                # Passed through so the batch makes the same check with the
                # same threshold. It applies to the FIRST document only: the
                # harness types through SendInput and GetLastInputInfo counts
                # that, so a per-document check refuses everything after
                # document one. See program.md, Idle gate.
                "--require-idle", str(require_idle),
            ],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=7200,
        )

    # A BATCH THAT STOPPED EARLY IS NOT A SCORE, checked two ways because this
    # already produced a false baseline once.
    #
    # On 2026-09-03 the idle gate cut the hard slice after 1 of 3 documents, and
    # `completion_rate` averaged that single partial document into 0.0, which
    # was then recorded as the slice's baseline. Every experiment afterwards
    # would have been compared against a number describing an interruption.
    #
    # The return code is the direct signal. The row count is the independent
    # one, and it catches every other way a batch can end short - a crash, a
    # timeout, an app that would not start - none of which say so in an exit
    # code that reaches here.
    if batch.returncode == _STOPPED_FOR_IDLE:
        return None, (
            "someone is at the keyboard; the batch stopped part way and "
            "measured nothing about the product"
        )
    try:
        measured = len(json.loads(SUMMARY.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        measured = 0
    if measured < len(documents):
        return None, (
            f"the batch ran {measured} of {len(documents)} documents, so there "
            f"is no score for this slice"
        )

    # A run two packs served is not a measurement of either. Same rule as tone,
    # and for the same reason: a smaller pack fails the meaning guards, a failed
    # guard falls back to rules, and the score then tracks which sections
    # happened to be handed to the small model.
    packs = autoresearch_run._packs_that_served(mark)
    if len(packs) > 1:
        return None, f"more than one pack served this run: {sorted(packs)}"
    return completion_rate()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slice", default="shapes")
    parser.add_argument("--describe", required=True, help="what this experiment tries")
    parser.add_argument(
        "--require-idle",
        type=float,
        default=_DEFAULT_REQUIRE_IDLE,
        help="only measure when nobody has touched the machine for this "
        "many seconds. 0 disables the gate, which makes the run evidence "
        "about the room rather than the product.",
    )
    arguments = parser.parse_args(argv)

    tag = autoresearch_cycle.slice_tag(arguments.slice, kind="reliability")
    description = f"{tag} {arguments.describe}"
    before = autoresearch_cycle._git("rev-parse", "--short", "HEAD")

    # Refuse a slice with no baseline, the way the queue always has. Calling a
    # cycle directly used to bypass that guard entirely and keep whatever it
    # scored against a bar of 0.0.
    blocked = autoresearch_cycle.missing_baseline(arguments.slice, "reliability")
    if blocked:
        print(f"NO BASELINE {blocked}")  # noqa: T201
        return 2

    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tools"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    if lint.returncode != 0:
        tail = lint.stdout.strip().splitlines()[-1] if lint.stdout else "failed"
        print(f"LINT {tail}")  # noqa: T201
        return 3

    autoresearch_cycle._git("add", "-A", "src", "tools")
    autoresearch_cycle._git("commit", "-q", "-m", f"reliability experiment: {description}")
    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    if commit == before:
        print("NOCHANGE nothing was edited")  # noqa: T201
        return 4

    baseline = autoresearch_cycle.current_baseline(arguments.slice, kind="reliability")
    autoresearch_status.write_status(
        {
            "phase": f"reliability {arguments.slice}",
            "description": arguments.describe,
            "essays": f"1-{len(_documents())}",
            "experiment": commit,
            "baseline": f"{baseline:.1f}",
        }
    )
    try:
        rate, reason = measure(
            ROOT / f"reliability-run-{arguments.slice}.log",
            require_idle=arguments.require_idle,
            corpus=corpus_for(arguments.slice),
        )
    finally:
        autoresearch_status.clear_status()

    # A SKIP IS NOT A CRASH, and here it must also not be a DISCARD. The
    # experiment's commit is already made at this point, so recording a row and
    # resetting would throw away an idea nobody measured and log it as a failure
    # - the loop would work through the whole queue while somebody typed, and
    # every entry would read as refuted.
    #
    # program.md: "do NOT write it to results.tsv, because nothing was measured
    # and a skipped tick is not a crash."
    if rate is None and "someone is at the keyboard" in reason:
        autoresearch_cycle._git("reset", "--hard", before)
        print(f"SKIPPED {reason}")  # noqa: T201
        return 2

    status = autoresearch_cycle.decide(baseline, rate)
    autoresearch_cycle.record(commit, rate, 0.0, status, description)
    if status != "keep":
        autoresearch_cycle._git("reset", "--hard", before)

    if rate is None:
        print(f"CRASH {reason}")  # noqa: T201
    else:
        print(f"{status.upper()} {rate:.1f}  (baseline {baseline:.1f})")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
