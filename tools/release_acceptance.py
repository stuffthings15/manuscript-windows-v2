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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EVIDENCE = ROOT / "build" / "acceptance"
BATCH = ROOT / "build" / "batch"

# The thresholds. Changing a number here changes what shipping means, so it is
# a decision to be recorded in docs/RELEASE_ACCEPTANCE.md and in the AI Brain,
# never a quiet edit to get a run green.
CORRECTIONS_MEAN = 95.0
CORRECTIONS_LOWEST = 90.0
TONE_PERCENT = 95.0
# The six in the palette, on the owner's instruction of 2026-08-16. The gate
# measures what the product offers, not everything the engine can do.
PALETTE_TONES = ("Clear", "Improve", "Rephrase", "Shorten", "Formal", "Friendly")

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
    rows = [grade(path, dictionary, engine) for path in paths]
    if not rows:
        return {"error": f"no corrected essays from {corpus.name} found in {BATCH}"}
    totals = [float(row["total"]) for row in rows]
    lowest = min(rows, key=lambda row: row["total"])
    return {
        "essays": len(rows),
        "mean": round(sum(totals) / len(totals), 1),
        "lowest": lowest["total"],
        "lowest_essay": lowest["name"],
        "rows": [{"name": row["name"], "total": row["total"]} for row in rows],
    }


def fast_tier(results: dict[str, object], started: float) -> list[str]:
    """Everything that does not need Word, plus a five-essay Word smoke."""
    failures: list[str] = []

    passed, _ = _run([str(PYTHON), "-m", "ruff", "check", "."], "ruff")
    results["ruff"] = passed
    if not passed:
        failures.append("ruff reported findings")

    passed, output = _run([str(PYTHON), "-m", "pytest", "-q"], "test suite")
    results["suite"] = passed
    match = re.search(r"(\d+) passed", output)
    results["tests_passed"] = int(match.group(1)) if match else 0
    if not passed:
        failures.append("the test suite failed")

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

    _say("\n=== five essays through Word (takes the screen, about 10 minutes)")
    passed, _ = _run(
        [str(PYTHON), "tools/run_essays_batch.py",
         "--from", "1", "--to", "5", "--corpus", str(COHERENT)],
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
             "--from", "1", "--to", "20", "--corpus", str(corpus)],
            f"twenty essays, {label} corpus",
        )
        if not passed:
            failures.append(f"the {label} Word batch did not finish")
        report = _graded(corpus, 1, 20, started)
        results[f"corrections_{label}"] = report
        if "error" in report:
            failures.append(str(report["error"]))
            continue
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

    tones: dict[str, object] = {}
    for tone in PALETTE_TONES:
        passed, _ = _run(
            [str(PYTHON), "tools/run_essays_batch.py",
             "--from", "1", "--to", "3", "--corpus", str(COHERENT),
             "--tone", tone],
            f"tone through Word: {tone}",
        )
        if not passed:
            failures.append(f"the {tone} Word batch did not finish")
            continue
        source = sorted(COHERENT.glob("*.docx"))[0]
        target = EVIDENCE / f"tone-{tone.lower()}.json"
        passed, output = _run(
            [str(PYTHON), "tools/tone_correctness.py",
             "--source", str(source),
             "--result", str(BATCH / source.name),
             "--tone", tone, "--json", str(target)],
            f"tone judge: {tone}",
        )
        match = re.search(r"(\d+(?:\.\d+)?)%", output)
        percent = float(match.group(1)) if match else 0.0
        tones[tone] = percent
        if percent < TONE_PERCENT:
            failures.append(f"tone {tone} at {percent}% is below {TONE_PERCENT}%")
    results["tones"] = tones
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fast", action="store_true")
    group.add_argument("--full", action="store_true")
    arguments = parser.parse_args()

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {
        "tier": "full" if arguments.full else "fast",
        # Passed in rather than stamped inside a helper so the evidence file
        # says when the run happened and not when it was written up.
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    started = time.time()
    failures = fast_tier(results, started)
    if arguments.full:
        failures += full_tier(results, started)

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
