"""One AutoResearch experiment: correct a fixed slice, grade it, print one block.

This is the Manuscript equivalent of `uv run train.py` in
https://github.com/karpathy/autoresearch (pinned at commit 228791f, mirrored
read-only at `Vedit-AI/third_party/autoresearch_ref`). Upstream trains for a
fixed five minutes and prints a summary block; the loop greps one line out of it
and decides keep or discard. This does the same thing with a Word run.

    .venv/Scripts/python.exe tools/autoresearch_run.py > run.log 2>&1
    grep "^score:" run.log

**Redirect it. Never pipe it into an agent's context.** Upstream is emphatic
about this and the reason is arithmetic: a twenty-essay batch prints thousands
of lines, and an agent running a hundred experiments overnight dies of context
exhaustion long before morning. The whole point of the summary block is that one
grep is all anyone needs to read.

Why a Word run and not a headless score
---------------------------------------
`CLAUDE.md` forbids treating a headless number as the result, and the reason is
recorded: on 2026-08-14 a headless run scored a corpus at 62 and it was reported
to the owner as a product regression when the local AI had simply latched off.
`tools/word_essay_session.py` refuses to start in that state. So the ground
truth here costs about five minutes per essay, which is what makes the upstream
five-minute budget fit this project almost exactly - essays measured 311s and
322s on 2026-08-27.

Why the block carries pack, model and build
-------------------------------------------
Because eight consecutive acceptance tiers were served by a 2 billion parameter
pack while reporting a larger one. A speed tiebreak in the hardware ladder
silently selected the smallest pack that passed, every tier looked normal, and
six hypotheses were "eliminated" against a model nobody intended to test. A run
that cannot say what actually served it is not a measurement, so this refuses to
print a score when it cannot tell, and `--require-pack` turns a mismatch into a
hard failure.

This file is part of the evaluator. Per the AutoResearch contract it is
read-only to the loop, in the same way `prepare.py` is upstream.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_status  # noqa: E402

APP_LOG = Path.home() / "AppData/Local/ManuscriptWindowsV2/echo-link.log"

# The corpus the release gate uses. Coherent prose with seeded surface damage,
# which is what a user's writing actually looks like.
DEFAULT_CORPUS = "test-corpus/error-riddled-20-docx"

# Upstream kills anything past double the budget and treats it as a failure. One
# essay is about five minutes, so a slice of N essays gets 10 minutes each before
# it is called dead rather than slow.
TIMEOUT_PER_ESSAY_SECONDS = 600

# The three ways a Word run fails without raising. Each one has cost a real
# measurement, and every one of them used to be read as a low score rather than
# as no score at all, which is far worse than a crash: a crash is obvious.
_DEAD = "dead"
_PARTIAL = "partial"
_CAPTURE_MISMATCH = "captured from"


_MONITOR: subprocess.Popen | None = None


def _start_monitor() -> None:
    """Show the on-screen panel. Delegates, so there is one implementation.

    This used to be the ONLY place the viewer was launched, which meant any
    measurement that did not go through the essay runner wrote a correct status
    file that nothing rendered. It now lives beside the status writer, where it
    belongs: showing the panel is part of what a measurement is, not a side
    effect of one command.
    """
    autoresearch_status.start_monitor()


def _tally() -> dict[str, object]:
    """What results.tsv says so far, for the panel. Never fatal.

    Read here rather than passed in, because the runner is launched fresh for
    every experiment and the file is the only memory the loop has.
    """
    best = 0.0
    counts = {"keep": 0, "discard": 0, "crash": 0}
    try:
        lines = (ROOT / "results.tsv").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        lines = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        if parts[3] in counts:
            counts[parts[3]] += 1
        if parts[3] == "keep":
            try:
                best = max(best, float(parts[1]))
            except ValueError:
                pass
    return {
        "baseline": f"{best:.2f}" if best else "-",
        "best": f"{best:.2f}" if best else "-",
        "kept": counts["keep"],
        "discarded": counts["discard"],
        "crashed": counts["crash"],
    }


def _status(**values: object) -> None:
    """Update what the panel shows, merging into what is already there."""
    current = autoresearch_status.read_status() or {}
    current.update(values)
    autoresearch_status.write_status(current)


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input, or 0.0 if unknown.

    Windows reports this for the whole session through GetLastInputInfo, which
    is what "is anyone using this machine" actually means - not whether a
    window is focused, and not whether a process is busy.

    Unknown deliberately reads as ZERO, meaning "assume someone is here". An
    idle check that cannot answer must not grant permission to seize the screen.
    """
    try:
        import ctypes

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LastInput()
        info.cbSize = ctypes.sizeof(_LastInput)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        ticks = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (ticks - info.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001 - ctypes raises broadly on a locked session
        return 0.0


def _restart_word() -> None:
    """Kill Word before an experiment, so UI Automation has a small tree.

    Measured on 2026-08-28, and it cost most of a morning. Every experiment for
    two hours failed with `COMError (-2146233083)` at 250 seconds and zero
    presses. The error is raised inside uiautomation while walking the control
    tree - GetFirstChildControl, then GetNextSiblingControl - which is why
    "COMError" was such a poor clue: Word, UI Automation and the clipboard all
    raise it, and the bare string was equally consistent with three different
    bugs. Two of my own changes were blamed and cleared by probe first.

    What it actually was: WINWORD had been running eleven hours across dozens
    of documents and app restarts. Killing it and running the same essay
    unchanged scored 91.000000 with 19 presses. The 16-essay tier that mostly
    succeeded had run on a fresh Word too.

    Killing Word between runs is already this harness's default behaviour
    outside --as-a-writer, so this is not a new liberty. It does mean an
    experiment will close a Word document somebody left open, which is part of
    why the loop runs on an idle machine.
    """
    subprocess.run(
        ["taskkill", "/F", "/IM", "WINWORD.EXE"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # Word leaves owner-lock files behind when it is killed, and it reopens the
    # next document read-only if they are still there.
    for lock in (ROOT / "build/batch").glob("~$*.docx"):
        try:
            lock.unlink()
        except OSError:
            # A lock the harness cannot remove is not worth losing the run over;
            # Word will say so itself if it matters.
            pass
    time.sleep(2.0)


def _stale_reason() -> str:
    """Why the running app must not be measured, or "" when it may be."""
    try:
        from release_acceptance import _stale_app_reason

        return _stale_app_reason()
    except Exception as exc:  # noqa: BLE001 - imports drag in the whole harness
        # A staleness check that cannot run must not be silently treated as a
        # pass. That is precisely the shape of the defect it exists to catch.
        return f"the staleness check could not run: {exc}"


def _vram_used_mb() -> float:
    """Megabytes of VRAM in use right now, or 0.0 when there is no GPU."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from echo_link_core.polish_hardware import _nvidia_status

        status = _nvidia_status()
        if not status:
            return 0.0
        # Keys and units taken from the source, not guessed: this returns
        # dedicated_vram_bytes and available_vram_bytes, in BYTES.
        total = float(status.get("dedicated_vram_bytes") or 0)
        free = float(status.get("available_vram_bytes") or 0)
        return max(0.0, (total - free) / (1024.0 * 1024.0))
    except Exception:  # noqa: BLE001 - nvidia-smi answers in several shapes
        return 0.0


class _VramWatch:
    """Peak VRAM across the run, sampled on a thread.

    A single reading afterwards would miss the peak, and the peak is the point:
    the spec keeps VRAM as a soft constraint so a change that quietly doubles
    residency is visible rather than discovered when the pack stops fitting.
    """

    def __init__(self) -> None:
        self.peak = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, _vram_used_mb())
            self._stop.wait(15.0)

    def __enter__(self) -> "_VramWatch":
        self.peak = _vram_used_mb()
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _packs_that_served(since_line: int) -> set[str]:
    """EVERY pack that served a section after a mark, not just the last one.

    `_served_pack` below returns the last pack it sees, which is the right
    answer to "what is loaded now" and the wrong one to "what produced this
    measurement". Measured 2026-09-01: 33 of 629 requests in one retained log
    were served by gemma4-e4b-qat-v1 while 596 used the 12b, and the last one
    was the 12b - so the run reported as clean.

    That matters because the adaptive runtime silently swaps in the standby pack
    when the primary cannot answer in time. A ~4B model writes weaker rewrites,
    weaker rewrites fail the meaning guards, a failed guard falls back to
    deterministic rules, and rules cannot do tone at all. The same essay then
    scores 35.7, 7.1 or 28.6 depending only on GPU timing - which is exactly the
    spread that made every tone verdict this session worthless.
    """
    try:
        lines = APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    packs = set()
    for line in lines[since_line:]:
        found = re.search(r"Polish plan pack=(\S+) model=", line)
        if found and found.group(1) != "-":
            packs.add(found.group(1))
    return packs


def _served_pack(since_line: int) -> tuple[str, str, str]:
    """What the app actually loaded, read from its own log after a mark.

    Read after a mark rather than from the whole file, because the log is
    append-only across restarts and the pack from an earlier session looks
    exactly like the pack from this one.
    """
    try:
        lines = APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ("unknown", "unknown", "unknown")
    pack = model = backend = "unknown"
    for line in lines[since_line:]:
        found = re.search(
            r"Polish plan pack=(\S+) model=(\S+) backend=(\S+)", line
        )
        if found:
            pack, model, backend = found.group(1), found.group(2), found.group(3)
        elif pack == "unknown":
            warm = re.search(r"prewarmed pack=(\S+)", line)
            if warm:
                pack = warm.group(1)
    return (pack, model, backend)


def _log_length() -> int:
    try:
        return len(APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _row_status(row: str) -> str:
    """Read a batch row the way the harness meant it, not by its number.

    A `partial` row carries a real-looking score for half an essay. Scoring it
    is how 11 of 20 runs were once averaged into a mean of 84.5 and reported as
    a product regression when the product had not regressed at all.
    """
    if _CAPTURE_MISMATCH in row:
        return "capture-mismatch"
    for marker in (_DEAD, _PARTIAL):
        if f" {marker}" in row:
            return marker
    if " complete" in row:
        return "complete"
    if " error" in row:
        return "error"
    return "unknown"


def _run_batches(essays: list[int], corpus: str) -> str:
    """Correct each essay and return the combined harness output.

    One invocation per essay rather than one contiguous range, so a slice can
    name the essays worth measuring instead of whichever ones happen to sit
    next to each other. Each invocation is its own Word session, which is the
    harness default; the gate uses --as-a-writer to catch defects that only
    appear on a second document, and that is a different question from the one
    this loop asks. What matters here is that every experiment is measured the
    same way.
    """
    collected = []
    for number in essays:
        # Fresh Word for EVERY essay, not just every experiment.
        #
        # Measured on 2026-08-28: with one restart per experiment, essay 8
        # completed at 298s and 324s across two baselines and essay 10 then
        # failed both times with COMError inside uiautomation's tree walk. The
        # first document after a restart works and the second does not, so the
        # tree Word leaves behind after a single essay is already enough to
        # break traversal on this machine.
        #
        # A 16-essay tier once ran 20 documents in one Word session with only 4
        # failures, so this is not universal and something about the current
        # state makes Word fragile sooner. Restarting per essay costs a few
        # seconds and removes the variable entirely, which matters more for a
        # loop that has to run unattended than the seconds do.
        _status(phase=f"correcting essay {number}")
        _restart_word()
        completed = subprocess.run(
            [
                sys.executable,
                "-u",
                str(ROOT / "tools/run_essays_batch.py"),
                "--from",
                str(number),
                "--to",
                str(number),
                "--corpus",
                corpus,
                "--as-a-writer",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_ESSAY_SECONDS,
        )
        collected.append(completed.stdout + completed.stderr)
    return "\n".join(collected)


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first", type=int, default=8, help="first essay")
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="essays in one experiment. One is the upstream-sized budget; raise "
        "it only once the measured spread says one is too noisy.",
    )
    parser.add_argument(
        "--essays",
        default="8",
        help="explicit essay numbers, comma separated, e.g. 8,10. Overrides "
        "--first/--count. The slice must not be chosen for being easy: essay 1 "
        "scores 100 and would measure every change as zero.",
    )
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument(
        "--require-pack",
        default="",
        help="fail rather than score if this pack did not actually serve the run",
    )
    # Restart by DEFAULT, and opting out is the flag.
    #
    # This was the other way round when the file was written, and it would have
    # wasted the whole first night. The app is a long-running process, so an
    # experiment that edits polish*.py and does not restart measures the code
    # that was already loaded: every result comes back as the baseline plus
    # noise, every keep and discard is decided by chance, and results.tsv fills
    # up looking entirely reasonable.
    #
    # session_state.build_identifier carries this scar already - "three
    # separate times in two days a run measured a process that was not the
    # build under test, and one of those cost six hours" - and an app started
    # on 2026-08-23 went on to serve six acceptance tiers while reporting a
    # build made minutes earlier.
    parser.add_argument(
        "--describe",
        default="",
        help="what this experiment is trying, shown on the status panel and "
        "logged to results.tsv",
    )
    parser.add_argument(
        "--require-idle",
        type=float,
        default=0.0,
        help="only run when nobody has touched the machine for this many "
        "seconds. Exits 2 without measuring anything if someone is here, so a "
        "loop can skip the tick rather than seize the screen.",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="measure the app already running. Only for debugging the harness: "
        "it cannot see a code change you just made.",
    )
    arguments = parser.parse_args(argv)

    # The idle gate was removed on 2026-08-28 at Curtis's decision, to run
    # continuously as upstream does. It created a trap rather than protection:
    # he is at the keyboard when he prompts, so the gate blocked the next
    # experiment, so he saw nothing happen, so he prompted again. Upstream
    # assumes a machine given over to the research and so do we now.
    #
    # The costs are accepted, not overlooked. Word is killed under him about
    # every five minutes, and his typing in an editable window will sometimes
    # redirect the HUD and crash a run - that is logged as a crash row and
    # costs one experiment.

    # An explicit list, because the two weakest essays are 08 and 10 and the
    # batch takes a contiguous range. Spanning 8 to 10 to reach both would drag
    # in 09, which has stopped part way twice today, and a flaky essay in the
    # slice turns experiments into crashes.
    #
    # A slice with no headroom is the quieter mistake. Essay 1 scores 100 out of
    # 100 after today's fixes, so a night spent optimising it would measure
    # every single change as zero and teach nothing.
    wanted = [
        int(part) for part in arguments.essays.replace(" ", "").split(",") if part
    ] or list(range(arguments.first, arguments.first + arguments.count))
    mark = _log_length()
    build = _git_head()

    # The status file FIRST, then the panel. The panel closes itself after four
    # consecutive misses, so a monitor started before anything has been written
    # would poll an absent file and quit within four seconds of launching.
    #
    # And this is the call that was missing. `_start_monitor` was written,
    # tested and never invoked, so every run since it was added has produced no
    # panel at all - which is exactly what Curtis saw. A feature whose only
    # purpose is to be visible fails silently when it is not wired in.
    _status(
        phase="starting",
        description=arguments.describe,
        essays=arguments.essays,
        experiment=build,
        **_tally(),
    )
    _start_monitor()

    if not arguments.no_restart:
        _restart_word()
        subprocess.run(
            [sys.executable, "-u", str(ROOT / "tools/restart_app_and_wait.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        mark = _log_length()

    # Then CHECK, because restarting and assuming is how this went wrong before.
    # The app publishes the build it started with and this compares it against
    # the checkout. Reused from release_acceptance rather than reimplemented:
    # a second copy of a staleness check is a second thing that can rot, and
    # this one was already inert for weeks once because it compared HEAD with
    # HEAD.
    stale = _stale_reason()
    if stale:
        print("---")  # noqa: T201
        print(f"crash:            stale app, {stale}")  # noqa: T201
        return 1

    started = time.monotonic()
    watch = _VramWatch()
    watch.__enter__()
    try:
        batch = _run_batches(wanted, arguments.corpus)
        batch_output = batch
    except subprocess.TimeoutExpired:
        watch.__exit__()
        print("---")  # noqa: T201
        print("crash:            timeout")  # noqa: T201
        print(f"elapsed_seconds:  {time.monotonic() - started:.1f}")  # noqa: T201
        return 1
    elapsed = time.monotonic() - started
    watch.__exit__()
    print(batch_output)  # noqa: T201

    rows = [line for line in batch_output.splitlines() if " run1 " in line]
    statuses = [_row_status(row) for row in rows]
    bad = [s for s in statuses if s != "complete"]
    if not rows or bad:
        # No score. Upstream's rule is that an empty grep means a crash, and
        # this prints no `score:` line precisely so the loop reads it as one.
        print("---")  # noqa: T201
        print(f"crash:            {bad[0] if bad else 'no rows'}")  # noqa: T201
        print(f"rows:             {len(rows)}")  # noqa: T201
        print(f"elapsed_seconds:  {elapsed:.1f}")  # noqa: T201
        return 1

    pack, model, backend = _served_pack(mark)
    if arguments.require_pack and pack != arguments.require_pack:
        print("---")  # noqa: T201
        print(f"crash:            pack mismatch, {pack} served")  # noqa: T201
        print(f"requested:        {arguments.require_pack}")  # noqa: T201
        return 1

    _status(phase="grading")
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import WritingSuggestionEngine
    from grade_corpus import essay_documents, grade

    dictionary = EnglishDictionary()
    dictionary.warm()
    engine = WritingSuggestionEngine()
    # Prefix matching, not equality: the batch prints essay names truncated to
    # thirty characters, so "01_urban_beekeeping_and_the_ec" has to match
    # "01_urban_beekeeping_and_the_ecology_of_cities". Comparing stems directly
    # matches nothing and grades an empty list.
    wanted = _slice_names(arguments, rows)
    graded = [
        grade(path, dictionary, engine)
        for path in essay_documents(ROOT / "build/batch")
        if any(path.stem.startswith(name) for name in wanted)
    ]
    if not graded:
        print("---")  # noqa: T201
        print("crash:            nothing graded")  # noqa: T201
        return 1

    score = sum(row["total"] for row in graded) / len(graded)
    print("---")  # noqa: T201
    print(f"score:            {score:.6f}")  # noqa: T201
    print(f"essays:           {len(graded)}")  # noqa: T201
    print(f"lowest:           {min(r['total'] for r in graded):.6f}")  # noqa: T201
    print(f"elapsed_seconds:  {elapsed:.1f}")  # noqa: T201
    print(f"pack:             {pack}")  # noqa: T201
    print(f"model:            {model}")  # noqa: T201
    print(f"backend:          {backend}")  # noqa: T201
    print(f"build:            {build}")  # noqa: T201
    print(f"peak_vram_mb:     {watch.peak:.1f}")  # noqa: T201
    print(f"unknown:          {sum(r['unknown'] for r in graded)}")  # noqa: T201
    # The per-class means, printed as well as the total.
    #
    # grade_corpus has always RETURNED these - spelling, homophones,
    # punctuation, grammar and coherence are separate scaled terms - and this
    # summary threw four of them away. Every correction experiment ever run was
    # judged on the sum alone, so one that fixed punctuation while breaking
    # grammar measured FLAT and was recorded as a bad idea.
    #
    # It also costs the loop its remaining headroom: nine of twelve measured
    # essay slices sit at exactly 100 on the total and cannot improve under the
    # strict-improvement rule, while a slice at 100 may still have real room on
    # grammar. Curtis approved touching this file on 2026-09-01 for that reason;
    # program.md otherwise calls it ground truth.
    #
    # Additive on purpose. The `score:` line above is unchanged and still means
    # exactly what it meant, so every row already in results.tsv stays
    # comparable with every row written after this.
    for name in ("spelling", "homophones", "punctuation", "grammar", "coherence"):
        mean = sum(row[name] for row in graded) / len(graded)
        print(f"class_{name}:{' ' * (12 - len(name))}{mean:.6f}")  # noqa: T201
    _status(phase="done", last_result=f"{score:.2f}")
    return 0


def _slice_names(arguments, rows: list[str]) -> list[str]:
    """The documents this experiment actually corrected.

    Taken from the batch rows rather than by globbing `build/batch`, because
    that directory accumulates results from every earlier run. Grading it
    wholesale on 2026-08-27 mixed three corpora and several failed runs into one
    number and produced a mean of 96.0 that belonged to nothing.
    """
    names = []
    for row in rows:
        found = re.search(r"^\s*\d+\s+(\S+)", row)
        if found:
            names.append(found.group(1))
    return names


def main(argv: list[str] | None = None) -> int:
    """Run one experiment and always take the status panel down afterwards.

    The panel exists only while a run does, which is the whole of Curtis's
    "should not show up unless AutoResearch is being run". There are half a
    dozen ways out of _run - a stale app, a crash, a timeout, a pack mismatch -
    and a panel left on screen after any of them would be a lie about what the
    machine is doing.
    """
    try:
        return _run(argv)
    finally:
        autoresearch_status.clear_status()


if __name__ == "__main__":
    raise SystemExit(main())
