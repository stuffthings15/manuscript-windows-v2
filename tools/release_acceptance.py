"""One gate that says whether Manuscript is fit to ship, and nothing else.

Before this, the answer lived in 111 test files, 2344 tests and about 70 probes
with no written thresholds and no single command, so "is it ready" was a
judgement call every time it was asked. The thresholds are in
`docs/RELEASE_ACCEPTANCE.md` and they are enforced here so they cannot drift
into an impression again.

Two tiers, because a full sweep is hours and most iterations fail on something
a fifteen-minute run would have caught:

    .venv/Scripts/python.exe tools/release_acceptance.py --fast
    .venv/Scripts/python.exe tools/release_acceptance.py --full

fast: ruff, the suite, diagnostics, the control-surface gate, and five essays
through Word graded on the rubric. It is the loop's every-iteration tier.

full: everything in fast, then twenty essays on both corpora and all six
palette tones through Word scored by the tone judge. Run it when fast passes,
or overnight.

This composes the tools that already exist. It does not reimplement grading,
judging or Word driving, and if a number here disagrees with the tool that
produces it, the tool is right and this is wrong.

Every Word batch runs `--as-a-writer`: one app session, with disposable
documents opened sequentially. The harness preserves unrelated Word documents.
Reusing the app matters because review state must remain correct when a writer
moves to their second document; a fresh app per essay cannot measure that.

Word runs take the screen. Nothing here starts one without --fast or --full
being asked for explicitly, and the runner prints what it is about to do and
how long it takes before it does it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EVIDENCE = ROOT / "build" / "acceptance"
BATCH = ROOT / "build" / "batch"
SUMMARY = ROOT / "build" / "batch-summary.json"

# The thresholds. Changing a number here changes what shipping means, so it is
# a decision to be recorded in docs/RELEASE_ACCEPTANCE.md and in the AI Brain,
# never a quiet edit to get a run green.
CORRECTIONS_MEAN = 95.0
CORRECTIONS_LOWEST = 90.0
TONE_PERCENT = 95.0
# The six in the palette, on the owner's instruction of 2026-08-16. The gate
# measures what the product offers, not everything the engine can do.
PALETTE_TONES = ("Clear", "Improve", "Rephrase", "Shorten", "Formal", "Friendly")
# The judge, named here rather than left to the tool's default.
#
# tone_correctness defaults to gemma4:12b-it-q4_K_M, which is not installed on
# this machine, so every judged run died with HTTP 404 and was recorded as
# 0.0 percent. The judge is a measuring instrument and the gate has to say
# which one it used, the same way it says which corpus it graded.
JUDGE_MODEL = "gemma3:27b"

# Coherent prose with seeded surface damage: what a user's writing actually
# looks like, and the corpus the corrections threshold is set against.
COHERENT = ROOT / "test-corpus" / "error-riddled-20-docx"
# Deliberately incoherent. Reported so a regression shows, never gated, because
# it is harder than anything a user will type. It sits beside the platform
# trees rather than inside this one, because every platform grades against it.
INCOHERENT = ROOT.parent / "20_Animal_Pet_Essays_800_Percent_More_Errors"


def _say(message: str) -> None:
    """Print and flush.

    Python block-buffers stdout whenever it is not a terminal, so piping this
    runner anywhere showed nothing at all until it exited. On a fifteen-minute
    gate that takes the screen to drive Word, silence is indistinguishable from
    a hang, and whoever is watching cannot tell whether to wait or intervene.
    """
    print(message, flush=True)  # noqa: T201


def _run(command: list[str], label: str) -> tuple[bool, str]:
    """Run a step, echoing its output as it arrives rather than at the end.

    subprocess.run with capture_output holds everything until the child exits,
    so a twelve-minute test suite produced one silent block and then a wall of
    text. This streams each line through as the child writes it and keeps a
    copy for the callers that parse a number back out of it.
    """
    global BATCH, SUMMARY
    if "tools/run_essays_batch.py" in command:
        step = EVIDENCE / ("batch-" + uuid4().hex[:12])
        command = [*command, "--output-root", str(step)]
        BATCH = step / "batch"
        SUMMARY = step / "batch-summary.json"
    _say(f"\n=== {label}")
    started = time.time()
    lines: list[str] = []
    # Streaming on this side is only half of it: a child Python block-buffers
    # its own stdout when it is a pipe, so pytest still delivered twelve
    # minutes of dots in one burst at the end. -u turns that off. Applied here
    # rather than at each call site so a new step cannot forget it.
    if command and command[0] == str(PYTHON):
        command = [command[0], "-u", *command[1:]]
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # UTF-8 and replace, not the console default. `text=True` decodes as
        # cp1252 on this machine, and a single 0x9d byte in a tool's output -
        # ruff prints curly quotes around code it cites - raised
        # UnicodeDecodeError and took the whole gate run down with a traceback,
        # after the tests it was reporting on had already passed. A release gate
        # must fail on what it measures, never on how it reads the output.
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is not None:
        for line in process.stdout:
            lines.append(line)
            _say(f"  | {line.rstrip()}")
    code = process.wait()
    state = "ok" if code == 0 else f"FAILED (exit {code})"
    # The exit code, not just the word. The first time a step failed with every
    # test passing, "FAILED" alone said nothing about whether that was pytest
    # reporting a problem or the interpreter crashing on the way out, and on
    # Windows those look completely different: 1 is a real failure, a large
    # value like 3221225477 is an access violation at shutdown.
    _say(f"=== {label}: {state} in {time.time() - started:.0f}s")
    return code == 0, "".join(lines)


_SECTION_PROGRESS = re.compile(r"section (\d+)/(\d+)")


def _run_was_truncated(row: dict) -> bool:
    """Whether this essay run stopped before it reached the last section.

    This used to be `outcome != "complete"`, and that conflated two different
    things. `run_essays_batch.outcome` returns "partial" whenever `unchanged` is
    non-empty, but `unchanged` counts paragraphs THE REVIEW LEFT ALONE - a
    quality signal, not a completion signal. For a tone run an unchanged
    paragraph is expected, because a tone may legitimately have nothing to say
    about a sentence.

    Measured on 2026-08-30: essay 3 pressed all 22 of its 22 sections with no
    stall and no error, reported `unchanged=[1]`, and was therefore "partial" -
    which made its whole tone UNMEASURED. A gate that discards a fully applied
    run because one paragraph did not need changing is not measuring what it
    claims to.

    So ask the precise question instead: did the run REACH THE LAST SECTION. The
    batch already records that in `stopped_because` as "section N/M", and N < M
    is the honest test. This is deliberately not a loosening - a run that stopped
    at "section 5/25" is still excluded, and so is any run with no presses, a
    stall, or an error. Only the unchanged-paragraph case changes.
    """
    if row.get("error"):
        return True
    if not row.get("presses"):
        return True
    if row.get("stalled_at"):
        return True
    stopped = str(row.get("stopped_because") or "")
    found = _SECTION_PROGRESS.search(stopped)
    if found:
        reached, total = int(found.group(1)), int(found.group(2))
        # The HUD closing after the final press is how a finished document
        # ends, and it arrives here as "section M/M".
        return reached < total
    if not stopped:
        # No reason recorded at all, and nothing else wrong: a clean run.
        return False
    # A STATED reason with no progress marker in it means the run stopped for
    # something that was not completion, and completion was never confirmed.
    #
    # Measured 2026-09-01 by the first reliability run over
    # test-corpus/document-shapes. Four of five documents carried a marker and
    # were honestly judged. The fifth stopped with "the HUD was gone at press 5
    # and three attempts to take focus back from another window failed", left
    # four paragraphs unreviewed, and scored as COMPLETE, because a reason with
    # no marker in it fell through to False. The metric reported 100 percent on
    # a run that had visibly failed.
    #
    # A finished document always says "section M/M", because the HUD publishes
    # its counter as it closes. Absence of that is absence of evidence, and for
    # a reliability metric the honest default is to disbelieve.
    return True


def _incomplete_runs() -> list[dict[str, object]]:
    """Essays whose Word run did not finish, with the reason the batch gave.

    A stalled essay currently becomes a LOW SCORE rather than a failed run, and
    a low score is indistinguishable from a product that corrects badly. That
    is the same class of error that produced three tone results of 0.0 percent
    on 2026-08-20, each judged after a run that had applied nothing.

    The harness stall is timing-dependent - one essay stopped at 4 presses of
    25 three times and then completed unchanged - so over a twenty-essay tier
    it is not a question of whether this happens but how often, and an eight
    hour run that grades interrupted work is eight hours wasted.

    `stopped_because` is carried through so the evidence file says WHY, rather
    than leaving the next reader to rediscover it from a HUD dump.
    """
    summary = SUMMARY
    try:
        rows = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    bad = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not _run_was_truncated(row):
            continue
        bad.append(
            {
                "essay": row.get("essay"),
                "outcome": row.get("outcome"),
                "presses": row.get("presses"),
                "stalled_at": row.get("stalled_at"),
                "stopped_because": row.get("stopped_because"),
                "error": row.get("error"),
            }
        )
    return bad


def _graded(corpus: Path, first: int, last: int, after: float) -> dict[str, object]:
    """Grade what this run just corrected, and nothing else.

    grade_corpus prints a table rather than returning one, so this imports it
    instead of parsing that table: a report that reads its own tool's stdout
    breaks the first time a column moves.

    The `names` filter is not a convenience. build/batch is a shared output
    directory that accumulates every corpus anybody has ever run, and grading
    all of it mixes corrected essays with uncorrected sources left behind by an
    earlier run. Measured while building this: a five-essay smoke graded 11
    documents and returned a mean of 74.4 with a lowest of 35, and every one of
    the low scores was a file this run never touched. A gate that reads other
    runs' leftovers is worse than no gate.
    """
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import WritingSuggestionEngine
    from grade_corpus import essay_documents, grade

    from acceptance_evidence import validate_batch
    from animal_essay_corpus_probe import _essay_files

    expected = {path.name for path in _essay_files(corpus)[first - 1:last]}
    if len(expected) != last - first + 1:
        return {"error": "requested corpus slice is missing documents"}
    evidence = validate_batch(SUMMARY, expected, BATCH, after)
    if evidence["status"] != "passed":
        return {"error": "; ".join(evidence["failures"]), "evidence": evidence}
    dictionary = EnglishDictionary()
    engine = WritingSuggestionEngine()
    # The essays this run actually put through Word, by name, taken from the
    # corpus rather than guessed from a numbering pattern.
    #
    # _essay_files, not glob. Word writes a "~$name.docx" owner file beside any
    # document it has open, and a raw glob counts it: the slice then shifts and
    # this grades a different five essays than the batch corrected. The batch
    # runner already learned that lesson, so this uses the same function rather
    # than repeating the mistake next to it.
    from animal_essay_corpus_probe import _essay_files

    wanted = {path.name for path in _essay_files(corpus)[first - 1:last]}
    paths = [path for path in essay_documents(BATCH) if path.name in wanted]
    # A document older than this run is a leftover, not a result. Without this
    # a Word batch that never started grades whatever the last one left behind
    # and the gate reports a number for work that did not happen, which is the
    # one failure a release gate must not have.
    stale = [path.name for path in paths if path.stat().st_mtime < after]
    paths = [path for path in paths if path.stat().st_mtime >= after]
    if stale:
        return {
            "error": (
                f"{len(stale)} document(s) in {BATCH.name} are older than this "
                f"run and were not graded: {sorted(stale)[:5]}"
            )
        }
    # An incomplete essay is excluded from the score and reported, rather than
    # discarding the whole corpus.
    #
    # The first version refused to grade ANYTHING if any essay was incomplete,
    # which is too strict to be useful: measured on the first full tier, essay
    # 4 of 20 came back partial, and that would have thrown away nineteen good
    # results and left the corrections step with no number at all. The point of
    # the rule is that an interrupted run must not become a quality number, not
    # that one bad essay erases the other nineteen.
    #
    # The gate still fails, because "19 of 20 measured" is not the same as
    # passing, and the reason travels with the evidence.
    incomplete = _incomplete_runs()
    excluded = {str(row.get("essay") or "") for row in incomplete}
    if excluded:
        paths = [path for path in paths if path.stem not in excluded]
    rows = [grade(path, dictionary, engine) for path in paths]
    if not rows:
        return {"error": f"no corrected essays from {corpus.name} found in {BATCH}"}
    totals = [float(row["total"]) for row in rows]
    lowest = min(rows, key=lambda row: row["total"])
    # Per category, not just the total.
    #
    # A mean of 93.0 against a gate of 95 says the product is two points short
    # and NOTHING about where the two points went, so the next session has to
    # rediscover that before it can fix anything. grade() already returns every
    # category; only this function was throwing them away.
    #
    # Reported as points LOST rather than scored, because the question being
    # asked of this table is always "what is costing the most".
    # Against the category's own maximum from the rubric, not against the best
    # essay in the set. Measuring spread from the best essay would report zero
    # loss on a corpus where every essay was equally bad.
    from grade_corpus import SCALE, WEIGHTS

    categories = tuple(WEIGHTS)
    lost = {
        name: round(
            sum(
                (SCALE * WEIGHTS[name]) - float(row.get(name, 0.0))
                for row in rows
            ),
            1,
        )
        for name in categories
    }
    return {
        "essays": len(rows),
        # Named so a reader is never told a mean without being told what it
        # left out.
        "excluded_incomplete": incomplete,
        "mean": round(sum(totals) / len(totals), 1),
        "lowest": lowest["total"],
        "lowest_essay": lowest["name"],
        # Worst first, so the fix target is the first line a reader sees.
        "points_lost_by_category": dict(
            sorted(lost.items(), key=lambda item: item[1], reverse=True)
        ),
        "rows": [
            {
                "name": row["name"],
                "total": row["total"],
                **{name: round(float(row.get(name, 0.0)), 1) for name in categories},
                "unknown": row.get("unknown"),
            }
            for row in rows
        ],
    }


def _stale_app_reason() -> str:
    """Why the running app must not be measured, or "" when it may be.

    The app publishes the build it STARTED with; this compares that against
    the checkout being tested. A missing state file means no app is publishing
    at all, which is also a reason not to measure.
    """
    import harness_state

    state = harness_state.read_state()
    if not state:
        return (
            "no app is publishing its state, so there is nothing to measure, "
            "or the running app predates state publishing. Restart it with "
            "tools/restart_app_and_wait.py"
        )
    running = str(state.get("build") or "").strip()
    checkout = _checkout_build()
    if not running or not checkout:
        return "build identity missing; restart and verify the app before measuring"
    if running != checkout:
        return (
            f"the running app is build {running} and this checkout is "
            f"{checkout}; restart the app with tools/restart_app_and_wait.py "
            "before measuring"
        )
    return ""


def _checkout_build() -> str:
    head = ROOT / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if ref.startswith("ref: "):
        try:
            return (head.parent / ref[5:]).read_text(encoding="utf-8").strip()[:12]
        except OSError:
            return ""
    return ref[:12]


def fast_tier(results: dict[str, object], started: float) -> list[str]:
    """Everything that does not need Word, plus a five-essay Word smoke."""
    failures: list[str] = []

    passed, _ = _run([str(PYTHON), "-m", "ruff", "check", "."], "ruff")
    results["ruff"] = passed
    if not passed:
        failures.append("ruff reported findings")

    passed, output = _run([str(PYTHON), "-m", "pytest", "-q"], "test suite")
    # Both the summary and process exit must succeed. A shutdown crash is
    # incomplete release evidence even when individual assertions passed.
    summary = re.search(r"(\d+) passed", output)
    broken = re.search(r"(\d+) (?:failed|error)", output)
    results["tests_passed"] = int(summary.group(1)) if summary else 0
    results["suite"] = bool(summary) and not broken
    if not summary:
        failures.append("the test suite did not report a result")
    elif broken:
        failures.append(f"the test suite reported {broken.group(0)}")
    elif not passed:
        _say(
            "  NOTE: every test passed but the process exited abnormally. "
            "The shutdown failure requires investigation and "
            "blocks release acceptance."
        )
        results["abnormal_exit"] = True
        results["suite"] = False
        failures.append("test process exited abnormally; release evidence incomplete")

    passed, _ = _run([str(PYTHON), "tools/diagnostics_check.py"], "diagnostics")
    results["diagnostics"] = passed
    if not passed:
        failures.append("diagnostics_check reported a problem")

    passed, _ = _run(
        [str(PYTHON), "tools/control_surface_gate.py",
         "--json", str(EVIDENCE / "controls.json")],
        "control surface",
    )
    results["controls"] = passed
    if not passed:
        failures.append("a control on screen cannot do anything")

    # Refuse to measure an app that is not this checkout.
    #
    # This tier never restarted the app - it measured whatever happened to be
    # running - and session_state.build_identifier read .git/HEAD on every
    # call, so the app always reported the CHECKOUT's commit rather than its
    # own. The staleness check the harness was supposed to perform compared
    # HEAD against HEAD and could never fail.
    #
    # Measured 2026-08-25: an app process started on 2026-08-23 served SIX
    # acceptance tiers while reporting a commit made minutes earlier. Every
    # number came from two-day-old code, and two were quoted as evidence
    # about a model that code had never heard of.
    stale = _stale_app_reason()
    if stale:
        failures.append(stale)
        _say("\n=== NOT MEASURING: " + stale)
        return failures

    _say("\n=== five essays through Word (takes the screen, about 10 minutes)")
    passed, _ = _run(
        [str(PYTHON), "tools/run_essays_batch.py",
         "--from", "1", "--to", "5", "--corpus", str(COHERENT),
         "--as-a-writer"],
        "Word smoke, essays 1 to 5",
    )
    if not passed:
        failures.append("the Word batch did not finish")
    smoke = _graded(COHERENT, 1, 5, started)
    results["corrections_smoke"] = smoke
    if "error" in smoke:
        failures.append(str(smoke["error"]))
    elif smoke["mean"] < CORRECTIONS_MEAN:
        failures.append(
            f"corrections smoke mean {smoke['mean']} is below {CORRECTIONS_MEAN}"
        )
    return failures


def full_tier(results: dict[str, object], started: float) -> list[str]:
    """Both corpora at twenty essays, then every palette tone through Word."""
    failures: list[str] = []

    for label, corpus, gated in (
        ("coherent", COHERENT, True),
        ("incoherent", INCOHERENT, False),
    ):
        passed, _ = _run(
            [str(PYTHON), "tools/run_essays_batch.py",
             "--from", "1", "--to", "20", "--corpus", str(corpus),
             "--as-a-writer"],
            f"twenty essays, {label} corpus",
        )
        if not passed:
            failures.append(f"the {label} Word batch did not finish")
        report = _graded(corpus, 1, 20, started)
        results[f"corrections_{label}"] = report
        if "error" in report:
            failures.append(str(report["error"]))
            continue
        excluded = report.get("excluded_incomplete") or []
        if excluded:
            # 19 of 20 measured is not passing, whatever the mean says.
            failures.append(
                f"{label}: {len(excluded)} essay run(s) did not complete and were "
                f"not graded ({excluded[0].get('essay')}: "
                f"{excluded[0].get('stopped_because') or excluded[0].get('outcome')})"
            )
        if not gated:
            # Reported, never gated. Said out loud on every run so a reader
            # does not mistake a low number here for a release blocker.
            _say(
                f"  {label}: mean {report['mean']}, reported only, not gated"
            )
            continue
        if report["mean"] < CORRECTIONS_MEAN:
            failures.append(
                f"{label} mean {report['mean']} is below {CORRECTIONS_MEAN}"
            )
        if report["lowest"] < CORRECTIONS_LOWEST:
            failures.append(
                f"{label} lowest {report['lowest']} ({report['lowest_essay']}) "
                f"is below {CORRECTIONS_LOWEST}"
            )

    from acceptance_evidence import validate_batch, aggregate_tones
    from animal_essay_corpus_probe import _essay_files

    tones: dict[str, object] = {}
    tone_reports = {}
    sources = _essay_files(COHERENT)[:20]
    for tone in PALETTE_TONES:
        tone_started = time.time()
        passed, _ = _run(
            [str(PYTHON), "tools/run_essays_batch.py",
             "--from", "1", "--to", "20", "--corpus", str(COHERENT),
             "--tone", tone, "--as-a-writer"],
            f"tone through Word: {tone}",
        )
        evidence = validate_batch(SUMMARY, {p.name for p in sources}, BATCH, tone_started)
        if not passed or evidence["status"] != "passed" or len(sources) != 20:
            tones[tone] = None
            tone_reports[tone] = {"status": "incomplete", "batch": evidence}
            failures.append(f"tone {tone}: all twenty documents must finish before judging")
            continue
        reports = []
        for source in sources:
            target = EVIDENCE / f"tone-{tone.lower()}-{source.stem}.json"
            passed, _ = _run(
                [str(PYTHON), "tools/tone_correctness.py",
                 "--source", str(source), "--result", str(BATCH / source.name),
                 "--tone", tone, "--model", JUDGE_MODEL, "--json", str(target)],
                f"tone judge: {tone}, {source.stem}",
            )
            try:
                report = json.loads(target.read_text(encoding="utf-8")) if passed else {}
            except (OSError, ValueError):
                report = {}
            reports.append(report)
        aggregate = aggregate_tones(reports, len(sources), tone)
        tone_reports[tone] = aggregate
        tones[tone] = aggregate.get("percent")
        if aggregate["status"] != "passed":
            failures.append(f"tone {tone}: missing or invalid judge evidence")
        elif aggregate["percent"] < TONE_PERCENT:
            failures.append(f"tone {tone} at {aggregate['percent']}% is below {TONE_PERCENT}%")
    results["tone_reports"] = tone_reports
    results["tones"] = tones
    return failures


def main() -> int:
    global EVIDENCE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fast", action="store_true")
    group.add_argument("--full", action="store_true")
    arguments = parser.parse_args()

    EVIDENCE = EVIDENCE / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12])
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    results: dict[str, object] = {
        "tier": "full" if arguments.full else "fast",
        "checkout_build": _checkout_build(),
        "evidence_directory": str(EVIDENCE),
        # Passed in rather than stamped inside a helper so the evidence file
        # says when the run happened and not when it was written up.
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    started = time.time()
    failures = fast_tier(results, started)
    if arguments.full and not failures:
        failures += full_tier(results, started)
    elif arguments.full:
        results["full_tier_status"] = "skipped: prerequisite fast tier failed"

    results["failures"] = failures
    results["passed"] = not failures
    evidence_file = EVIDENCE / f"acceptance-{results['tier']}.json"
    evidence_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    _say("\n" + "=" * 60)
    if failures:
        _say(f"NOT READY. {len(failures)} threshold(s) missed:")
        for line in failures:
            _say(f"  - {line}")
    else:
        _say(f"{results['tier']} tier passed every threshold.")
    _say(f"evidence: {evidence_file}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
