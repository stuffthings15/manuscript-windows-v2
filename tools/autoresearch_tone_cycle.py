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
    autoresearch_run._restart_word()
    subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    stale = autoresearch_run._stale_reason()
    if stale:
        return None, f"stale app, {stale}"
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [
                sys.executable, str(ROOT / "tools/run_essays_batch.py"),
                "--from", str(FIRST_ESSAY), "--to", str(LAST_ESSAY),
                "--corpus", str(CORPUS), "--tone", tone, "--as-a-writer",
            ],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=5400,
        )
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
    autoresearch_cycle.record(commit, percent, 0.0, status, description)
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
