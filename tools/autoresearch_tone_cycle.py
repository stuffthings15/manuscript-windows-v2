"""One whole AutoResearch experiment for a TONE, from commit to keep-or-reset.

    .venv/Scripts/python.exe tools/autoresearch_tone_cycle.py --tone Shorten \
        --describe "tell Shorten how much shorter"

Prints exactly one line: `KEEP 41.2`, `DISCARD 21.4` or `CRASH dead`.

WHY THIS EXISTS. `autoresearch_cycle.py` measures the grade_corpus score, and
that number does not move when a tone contract changes - the corpus grader scores
spelling, punctuation and grammar, none of which a tone rewrite is trying to
alter. So the loop as built could run a hundred tone experiments and record a
hundred identical discards, which looks exactly like a hundred bad ideas.

The metric here is the tone judge's PASS PERCENTAGE from
`tools/tone_correctness.py`: the share of sections a separate model agrees were
actually rewritten in the target style. Higher is better, same as the corpus
score, so keep-or-discard reads the same way.

Everything else is deliberately shared with the correction cycle rather than
copied. `decide`, `record` and `current_baseline` come from
`autoresearch_cycle`, so the two loops cannot drift apart on what a keep means,
and both write the same five-column results.tsv the specification requires.

Rows are tagged `tone Shorten:` in the description, which is how
`current_baseline` separates slices - the same mechanism the essay slices use.
A tone measured against a corpus baseline, or against another tone's, would be
meaningless.

WHAT THIS DOES NOT DO: run the test suite, for the reason recorded in
`autoresearch_cycle` - the suite gates the merge, not the experiment. ruff runs,
because a syntax error would waste a twenty-minute Word run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402

# The gated corpus and the judge the release gate uses. Same corpus, same judge
# model, so a number produced here means the same thing as a number in
# docs/RELEASE_ACCEPTANCE.md. A cheaper judge would be a different metric
# wearing the same name.
CORPUS = ROOT / "test-corpus" / "error-riddled-20-docx"
JUDGE_MODEL = "gemma3:27b"

# The pack a tone measurement must be served by, end to end. Anything
# smaller is a different product answering, not a slower one.
REQUIRED_PACK = "gemma4-12b-qat-v1"

# Three essays, like the gate. The judge only reads the FIRST one - that is the
# gate's own design, recorded there: a stall in essay 2 or 3 says nothing about
# the number being taken. The other two still run, because the tone has to
# survive a multi-document session to be worth measuring.
FIRST_ESSAY = 1
LAST_ESSAY = 3


def _essay_paths() -> tuple[Path, Path]:
    """(source document, corrected document) for the essay the judge reads."""
    source = sorted(CORPUS.glob("*.docx"))[0]
    return source, ROOT / "build" / "batch" / source.name


def _judged_run_completed(source_stem: str) -> str:
    """"" if the judged essay finished, else why it did not.

    Guarding this is the whole reason the 2026-08-20 tone numbers were invalid:
    three tones were judged after runs that had applied nothing and each came
    back at 0.0 percent, which read as total product failure and was not. An
    absent measurement must never be recorded as a bad one.
    """
    import release_acceptance

    for row in release_acceptance._incomplete_runs():
        if str(row.get("essay") or "") == source_stem:
            return str(
                row.get("stopped_because") or row.get("error") or "no reason recorded"
            )
    return ""


def _release_the_judge() -> None:
    """Evict the 27B judge from VRAM as soon as it has answered.

    THE ROOT CAUSE of the tone spread, and it was self-inflicted. The judge asks
    ollama for `keep_alive: 300s`, so a 27B model sits in VRAM for five minutes
    after the last section is judged. The next tone run then cannot fit the 12b
    correction pack, cold-loads it, exceeds the 30-second timeout and the
    45-second retry, and the adaptive runtime hands the section to the ~4B
    standby. A 4B rewrite fails the meaning guards, a failed guard falls back to
    deterministic rules, and rules cannot do tone at all.

    That is why the same essay scored 35.7 in an isolated run and 7.1 inside the
    release tier, where six tones were judged back to back: the tier poisoned
    each run with the previous run's judge.

    Never raises. Failing to unload costs a slow next run; failing the
    measurement over it would cost the run outright.
    """
    try:
        import urllib.request

        body = json.dumps({"model": JUDGE_MODEL, "keep_alive": 0}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                body,
                {"Content-Type": "application/json"},
            ),
            timeout=60,
        ).read()
    except Exception:  # noqa: BLE001 - an unload failure must not lose the run
        pass


def judge_is_resident() -> bool | None:
    """Whether the 27B judge still holds the card. None when it cannot be read.

    None rather than a guess: a machine that cannot answer should let the run
    proceed and be refused by the prewarm guard, not stall forever.
    """
    try:
        import urllib.request

        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/ps", timeout=30
        ) as answer:
            loaded = json.loads(answer.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - ollama may be down or busy loading
        return None
    models = loaded.get("models")
    if not isinstance(models, list):
        return None
    return any(
        str(entry.get("model") or entry.get("name") or "").startswith(JUDGE_MODEL)
        for entry in models
        if isinstance(entry, dict)
    )


def _wait_for_the_judge_to_leave(timeout_seconds: float = 300.0) -> None:
    """Block until the judge is off the card, or give up quietly.

    The judge does not fit beside the pack it is judging. `gemma3:27b` is 17 GB
    on a 16 GB card, so while it answers the correction pack is evicted, and the
    unload that follows takes time the next tone did not wait for.

    Measured 2026-09-02, five baselines in one sitting on a card that started
    completely empty: Clear and Formal died with TWO packs serving one run,
    because the judge was still unloading while the batch ran, and Shorten and
    Friendly were refused at prewarm because the planner found no room and took
    the ~4B standby. One tone in five produced a number.

    Asks whether the JUDGE is resident rather than how much memory is free. A
    free-memory threshold was written first and was wrong: the primary pack is
    7.7 GB and is normally resident, so "wait for 9 GB free" would stall for the
    whole timeout on a perfectly healthy card, every single run.

    Gives up rather than raising. The prewarm guard immediately after is what
    decides whether a run is trustworthy; this only removes the commonest reason
    it has to refuse.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        resident = judge_is_resident()
        if resident is None or not resident:
            return
        time.sleep(5.0)


def measure(tone: str, log: Path) -> tuple[float | None, str]:
    """(pass percentage, reason it is missing). Never both.

    RESTARTS THE APP FIRST, then checks the build it published. Without this
    every tone experiment would be a false discard: the cycle edits
    polish_quality.py, but the app is a long-running process that already has
    the OLD contract loaded, so the batch would measure the baseline again and
    the honest conclusion "no improvement" would be recorded for an idea that
    was never tried.

    `autoresearch_run` carries the same guard and the scar behind it - "three
    separate times in two days a run measured a process that was not the build
    under test, and one of those cost six hours". This cycle was written without
    it, and the queue would have run three tone experiments against stale code.

    Both helpers are imported rather than reimplemented. A second copy of a
    staleness check is a second thing that can rot, and the original was once
    inert for weeks because it compared HEAD with HEAD.
    """
    import autoresearch_run

    source, corrected = _essay_paths()
    # Wait for the card before starting, because the judge does not fit beside
    # the pack it is judging.
    #
    # `gemma3:27b` is 17 GB on a 16 GB card. While it judges, the correction
    # pack is evicted and the GPU thrashes, and the eviction that follows takes
    # time the next tone does not wait for. Measured 2026-09-02, five baselines
    # in one sitting on a card that started completely empty: Clear and Formal
    # died with TWO packs serving one run, because the judge was still unloading
    # while the batch ran, and Shorten and Friendly were refused at prewarm
    # because the planner found no room and took the ~4B standby.
    #
    # One tone in five produced a number. The guards were right to refuse the
    # other four - those same conditions are what made the earlier 7.1 and 64.3
    # figures void rather than real - but a measurement that refuses four times
    # out of five is not a measurement pipeline.
    _wait_for_the_judge_to_leave()
    before_restart = autoresearch_run._log_length()
    autoresearch_run._restart_word()
    subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    stale = autoresearch_run._stale_reason()
    if stale:
        return None, f"stale app, {stale}"

    # Check the pack BEFORE the batch, not only after it.
    #
    # The after-the-fact guard below is correct but expensive: on
    # 2026-09-01 it refused five of six tone baselines, each after driving
    # Word for about twenty-five minutes. Two hours of measurement produced
    # one number.
    #
    # The planner takes a VRAM snapshot per request and picks a smaller pack
    # when headroom is short, and headroom is short whenever another GPU
    # application is running - ChatGPT.exe was on the GPU during those runs.
    # So the condition is visible at prewarm, before a single essay opens.
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
            f"free the GPU before measuring tone"
        )
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [
                sys.executable, str(ROOT / "tools/run_essays_batch.py"),
                "--from", str(FIRST_ESSAY), "--to", str(LAST_ESSAY),
                "--corpus", str(CORPUS), "--tone", tone, "--as-a-writer",
            ],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=5400,
        )

    # A run that two different packs served is not a measurement of either.
    #
    # The adaptive runtime silently swaps in the standby pack when the primary
    # cannot answer in time. A ~4B model writes weaker rewrites, weaker rewrites
    # fail the meaning guards, a failed guard falls back to deterministic rules,
    # and rules cannot do tone at all - so the score tracks how many sections
    # happened to be handed to the small model.
    #
    # That is not a theory. On 2026-09-01 the SAME source essay, the SAME judge
    # and the SAME tone produced 35.7, 7.1 and 28.6 across three runs, while the
    # judge itself returned 28.6 three times out of three on a fixed document.
    # The judge is reproducible; the correction run was not.
    #
    # A crash, not a low score, for the reason this file already carries: an
    # absent measurement is a claim about the run, a zero is a claim about the
    # product.
    packs = autoresearch_run._packs_that_served(mark)
    if len(packs) > 1:
        return None, f"more than one pack served this run: {sorted(packs)}"

    reason = _judged_run_completed(source.stem)
    if reason:
        return None, reason
    if not corrected.exists():
        return None, "the corrected document was never written"

    verdict = ROOT / "build" / f"tone-cycle-{tone.lower()}.json"
    subprocess.run(
        [
            sys.executable, str(ROOT / "tools/tone_correctness.py"),
            "--source", str(source), "--result", str(corrected),
            "--tone", tone, "--model", JUDGE_MODEL, "--json", str(verdict),
        ],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=3600,
    )
    _release_the_judge()
    try:
        report = json.loads(verdict.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the judge wrote no readable verdict ({type(exc).__name__})"
    if "error" in report:
        # The section-count guard inside the judge. It refuses rather than
        # judging paragraph 3 against paragraph 4, and that refusal is a crash
        # here, not a zero.
        return None, str(report["error"])
    percent = report.get("percent")
    if percent is None:
        return None, "the judge returned no percentage"
    return float(percent), ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tone", required=True)
    parser.add_argument("--describe", required=True, help="what this experiment tries")
    arguments = parser.parse_args(argv)

    tag = autoresearch_cycle.slice_tag(arguments.tone, kind="tone")
    description = f"{tag} {arguments.describe}"
    before = autoresearch_cycle._git("rev-parse", "--short", "HEAD")

    # Same guard as the queue. Five of six tones have no baseline, so without
    # this a direct call would record a bar that nothing measured.
    blocked = autoresearch_cycle.missing_baseline(arguments.tone, "tone")
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
    autoresearch_cycle._git("commit", "-q", "-m", f"tone experiment: {description}")
    commit = autoresearch_cycle._git("rev-parse", "--short", "HEAD")
    if commit == before:
        print("NOCHANGE nothing was edited")  # noqa: T201
        return 4

    baseline = autoresearch_cycle.current_baseline(arguments.tone, kind="tone")
    autoresearch_status.write_status(
        {
            "phase": f"tone {arguments.tone}",
            "description": arguments.describe,
            "essays": f"{FIRST_ESSAY}-{LAST_ESSAY}",
            "experiment": commit,
            "baseline": f"{baseline:.1f}",
        }
    )
    try:
        percent, reason = measure(
            arguments.tone, ROOT / f"tone-run-{arguments.tone.lower()}.log"
        )
    finally:
        autoresearch_status.clear_status()

    status = autoresearch_cycle.decide(baseline, percent)
    autoresearch_cycle.record(
        commit, percent, 0.0, status,
        autoresearch_cycle.with_crash_reason(
            description, reason if percent is None else None
        ),
    )
    if status != "keep":
        # Recoverable by the hash in results.tsv, and only ever on the
        # autoresearch branch. Same contract as the correction cycle.
        autoresearch_cycle._git("reset", "--hard", before)

    if percent is None:
        print(f"CRASH {reason}")  # noqa: T201
    else:
        print(f"{status.upper()} {percent:.1f}  (baseline {baseline:.1f})")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
