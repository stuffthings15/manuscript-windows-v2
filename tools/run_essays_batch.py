"""Drive essays through the Writing HUD in Word, then judge the text.

One essay per Word session, accepting every section the way a writer does,
then saving the document and scoring what came back. The score is deliberately
objective, because reading twenty essays by eye is how defects get missed:

  * paragraphs the review never changed
  * words the bundled dictionary does not know, which are residual misspellings
  * punctuation the corrector introduced (".," ",," " ,")

Writes build/corrected/<name>.review.md per essay and a summary table.

    .venv/Scripts/python.exe tools/run_essays_batch.py --from 2 --to 20
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import harness_state  # noqa: E402
from animal_essay_corpus_probe import CORPUS, _essay_files, load_essays  # noqa: E402
from repro_accept_advance import (  # noqa: E402
    counter_text,
    hud_window,
    replace_button,
)
from repro_zero_of_zero import (  # noqa: E402
    click_in_document,
    foreground_word,
    press_hotkey,
)

# A word may contain accented letters. [A-Za-z][A-Za-z']* split "cafe" written
# with an accent and reported the fragment as an unknown word, so three of the
# unknown words in the 2026-08-13 batch were this pattern, not any essay.
WORD = re.compile(r"[^\W\d_][\w']*", re.UNICODE)
ARTEFACT = re.compile(r"[.!?]\s*,|,\s*,|\s+,|\(\s|\s\)")


def dismiss_button(hud):
    """The control a reader uses when a section has no finished correction.

    The HUD disables Replace for such a section and enables Dismiss, telling
    the reader that later sections keep processing. This harness only knew
    about Replace, so it sat on that one section until it timed out and
    reported the essay as stalled - essays 13 and 18 were called a
    reproducible app defect on that evidence, and they are not one.
    """
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if str(control.Name or "").startswith("Dismiss"):
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def badge_button(hud):
    """The badge the HUD shows before anyone opens the review.

    Captured at the moment of a "timeout": the only control present was
    'Manuscript, 11 reviews ready. Click for writing suggestions.' The reviews
    were ready and the HUD was waiting to be clicked, while this harness
    watched for a section counter that only exists once the card is open. Every
    essay recorded dead today failed this way - the app was working.
    """
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if "reviews ready" in str(control.Name or ""):
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def tone_combo(hud):
    """The HUD's tone chooser, or None.

    It is a menu button, not a combo box. It was a QComboBox until 2026-08-18,
    and a combo in this window cannot be set through UI Automation at all - the
    HUD is shown without activating so it never receives keys, and every
    pattern route reports success while the value stays put. The control was
    changed for accessibility; this follows it.

    Matched on the accessible name, case-folded and loose, because the card
    reports "Sentence rewrite tone" while the source sets "Rewrite tone" on a
    different widget of the same kind. Matching the source string exactly found
    nothing, and the first tone run pressed nothing at all while reporting a
    healthy result.
    """
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            if control.ControlTypeName not in {"ButtonControl", "ComboBoxControl"}:
                continue
            if "rewrite tone" in str(control.Name or "").casefold():
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def current_tone(hud) -> str:
    """The tone the control is showing now, or "" if it cannot be read.

    The published state answers this directly, and it is believed first.

    Measured 2026-08-21: a Friendly run reported `tone_selected=0`,
    `tone_unavailable=1` and `applied=0`, while the app's own log showed it had
    run Friendly - the click had worked and only the READ-BACK failed. Reading
    a control's name through UI Automation while the HUD is repainting is
    exactly the kind of guess this whole exercise is replacing, and here it
    made a working selection look like an unreachable control. That produced
    the message "The HUD tone control is not reachable through UI Automation",
    which was false and misled two investigations.
    """
    published = harness_state.recent_state()
    if published is not None:
        stated = harness_state.selected_tone(published)
        if stated:
            return stated
    combo = tone_combo(hud)
    if combo is None:
        return ""
    for reader in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        try:
            value = str(getattr(combo, reader)().Value or "").strip()
            if value:
                return value
        except Exception:  # noqa: BLE001 - not every provider offers both
            continue
    try:
        # A menu button carries its choice as its label.
        return str(combo.Name or "").strip()
    except Exception:  # noqa: BLE001 - the HUD may have closed
        return ""


def select_tone(hud, tone: str) -> bool:
    """Choose a tone the way a writer does, and confirm it took.

    The return value is a READ BACK of what the control shows, never the fact
    that something was clicked. A harness that reports success because it
    pressed something is how a run measures nothing and says it passed - which
    is exactly what happened the first time this flag ran.
    """
    import uiautomation as automation

    button = tone_combo(hud)
    # What the read-back saw, printed whether it worked or not. The false
    # message "the HUD tone control is not reachable" has now misled three
    # investigations, and every one of them had to reconstruct by hand what
    # this function saw at the moment it gave up. It says so itself now.
    published = harness_state.recent_state()
    print(
        f"    tone read-back: want={tone} state={harness_state.selected_tone(published)!r} "
        f"state_fresh={published is not None} combo={button is not None}",
        flush=True,
    )
    if button is None:
        return False
    if tone.casefold() in current_tone(hud).casefold():
        return True
    for opener in ("GetInvokePattern", "GetExpandCollapsePattern"):
        try:
            pattern = getattr(button, opener)()
            if opener == "GetInvokePattern":
                pattern.Invoke()
            else:
                pattern.Expand()
        except Exception:  # noqa: BLE001 - try the next way in
            continue
        time.sleep(1.0)
        item = automation.MenuItemControl(searchDepth=12, Name=tone)
        if not item.Exists(4, 0.4):
            _close_any_open_menu()
            continue
        try:
            item.GetInvokePattern().Invoke()
        except Exception:  # noqa: BLE001 - fall back to a real click
            try:
                item.Click(simulateMove=False)
            except Exception:  # noqa: BLE001 - the menu may have closed
                continue
        time.sleep(1.5)
        if tone.casefold() in current_tone(hud_window(8.0) or hud).casefold():
            return True
    # Failing to choose a tone must not also break everything after it. This
    # opens the tone menu and, when the item is not found, used to return with
    # the menu still hanging over the card - so replace_button found nothing,
    # the press loop broke on its first section, and a run that should have
    # reported "the tone was not applied" reported presses=0 and corrected
    # nothing at all. Measured on 2026-08-20: the same essay completed with
    # presses=25 without --tone and died at presses=0 with it.
    _close_any_open_menu()
    return False


def _close_any_open_menu() -> None:
    """Send Escape, so a half-opened dropdown cannot cover the card."""
    try:
        import uiautomation as automation

        automation.SendKeys("{Esc}", waitTime=0.2)
    except Exception:  # noqa: BLE001 - nothing was open, or the HUD closed
        return


def regenerate_button(hud):
    """The control that re-runs the current section with the chosen tone."""
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if "rewrite the current sentence" in str(control.Name or "").casefold():
                return control
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
    return None


def review_readiness(hud) -> str:
    """What the HUD says about its own readiness, or "" if it is not saying.

    Read from the published state first, and only fall back to the accessible
    name when the app is not publishing one.

    The label version could only recognise sentences it already knew, and
    returned "unknown" for anything else. Measured 2026-08-21: a failing tone
    run reported unknown=54, pressed nothing, and reported applied=0 - while
    the app's own event log showed it had requested the tone, filed the batch
    and restored the card. The signal existed and nothing was reading it.
    """
    published = harness_state.recent_state()
    if published is not None:
        stated = harness_state.readiness(published)
        if stated:
            return stated
    published = harness_state.recent_state()
    if published is not None and published.get("provenance"):
        return str(published["provenance"])
    if hud is None:
        return ""
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            name = str(control.Name or "")
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
        if "Current writing suggestion position" in name:
            if "all sections ready" in name:
                return "ready"
            if "all sections finished" in name:
                # Every section has had its turn; some have no correction.
                return "finished"
            if "preparing" in name:
                return "preparing"
            return "unknown"
    return ""


def section_provenance(hud) -> str:
    """Who produced the text on the card: ai, waiting, rules-only, no-ai.

    From the published state when there is one, for the same reason as
    readiness: a fact the app states beats a sentence the harness parses.

    Separate from review_readiness, because the review can report every section
    ready while THIS one is still waiting on the model - and that is exactly
    the state where Replace is disabled.

    Reading the review-level fact instead cost a whole five-tone run on
    2026-08-20: the harness saw "ready", found Replace disabled, concluded
    there was nothing to apply, and dismissed its way through the document
    with presses=0. Every tone scored 0.0 percent while measuring nothing.
    """
    if hud is None:
        return ""
    from repro_accept_advance import _descend

    for control in _descend(hud):
        try:
            name = str(control.Name or "")
        except Exception:  # noqa: BLE001 - the HUD may be repainting
            continue
        if "Current writing suggestion position" in name and ", section " in name:
            return name.rsplit(", section ", 1)[1].strip()
    return ""


def wait_for_the_ai(hud_reader, hud, seconds: float = 90.0):
    """Hold until the section stops waiting on the model, and say what it got.

    Returns the HUD and the provenance it settled on. A section that is still
    waiting when the time is up is reported as waiting rather than quietly
    treated as having nothing to offer, because those are different facts and
    only one of them is the product's fault.
    """
    deadline = time.monotonic() + seconds
    state = section_provenance(hud)
    while state == "waiting" and time.monotonic() < deadline:
        time.sleep(2.0)
        hud = hud_reader(8.0) or hud
        state = section_provenance(hud)
    return hud, state


def invoke(control) -> bool:
    try:
        control.GetInvokePattern().Invoke()
        return True
    except Exception:  # noqa: BLE001 - fall back to a real click
        try:
            control.Click(simulateMove=False)
            return True
        except Exception:  # noqa: BLE001 - the HUD may have closed
            return False


def release_document(path) -> None:
    """Ask Word to let go of one document, without closing Word.

    --as-a-writer leaves Word running between essays on purpose, which is what
    a writer does. The cost is that Word still holds the .docx it last opened,
    so copying a fresh source over it fails:

        PermissionError: [Errno 13] Permission denied: build\\batch\\01_....docx

    Measured on 2026-08-20, that killed a six-tone run outright. Every batch
    died on the copy before opening Word at all, produced no rows, and each
    tone was then judged against the stale document and scored 0.0 percent.
    Six zeroes that measured nothing, for the second time in one day.

    Closing Word entirely would work and would also throw away the session
    that --as-a-writer exists to preserve. Closing the one document is what a
    writer does before opening the same file again.
    """
    import comtypes.client

    wanted = str(path.resolve()).casefold()
    try:
        word = comtypes.client.GetActiveObject("Word.Application", dynamic=True)
    except Exception:  # noqa: BLE001 - Word is not running, so nothing holds it
        return
    for open_document in list(word.Documents):
        try:
            if str(open_document.FullName).casefold() != wanted:
                continue
        except Exception:  # noqa: BLE001 - Word raises assorted COM errors
            continue
        try:
            # Positional, not SaveChanges=0: the keyword form silently does
            # nothing through the dynamic COM wrapper, which is recorded
            # further down this file and cost three documents staying open.
            open_document.Close(0)
        except Exception as exc:  # noqa: BLE001 - Word raises assorted COM errors
            print(f"   warning: could not release the document: {exc}", flush=True)


def close_word() -> None:
    import subprocess

    subprocess.run(
        ["taskkill", "/IM", "WINWORD.EXE", "/F"],
        capture_output=True,
        check=False,
    )
    time.sleep(2.0)


def restart_app() -> bool:
    """Start a fresh app before each essay, and wait for the model to load.

    The Writing HUD carried one document's review into the next document's run
    and reported "4 of 11" for a ten-section essay. Essay 18 was called a
    reproducible defect on that evidence and completes perfectly against a
    freshly started app. Sharing one process across a batch measures the batch,
    not the essay.
    """
    import subprocess

    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "restart_app_and_wait.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        print(f"   restart failed: {done.stdout.strip()[-200:]}", flush=True)
        return False
    return True


def outcome(row: dict) -> str:
    """One word for what happened, so repeats can be counted rather than read."""
    if row.get("error"):
        return "error"
    if row["presses"] == 0:
        return "dead"
    if row["unchanged"] or row["stalled_at"]:
        return "partial"
    return "complete"


LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "ManuscriptWindowsV2" / "echo-link.log"


def log_tail(lines: int = 60) -> list[str]:
    """The app's own account of the window that just failed."""
    try:
        text = LOG.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - the diagnosis says so itself
        return [f"<could not read {LOG}: {type(exc).__name__}: {exc}>"]
    kept = [
        line
        for line in text.splitlines()
        if "faster_whisper" not in line and "VAD filter" not in line
    ]
    return kept[-lines:]


def hud_tree(hud, limit: int = 60) -> list[str]:
    """Every control the HUD is showing, named and enabled-or-not."""
    if hud is None:
        return ["<no Writing HUD window>"]
    from repro_accept_advance import _descend

    rows: list[str] = []
    for control in _descend(hud):
        try:
            rows.append(
                f"{control.ControlTypeName:<22} enabled={str(control.IsEnabled):<5} "
                f"name={str(control.Name or '')[:90]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - a repainting control is data too
            rows.append(f"<unreadable control: {type(exc).__name__}: {exc}>")
        if len(rows) >= limit:
            rows.append(f"<truncated at {limit} controls>")
            break
    return rows


def diagnose(reason: str, hud) -> list[str]:
    """Capture why a run failed, at the moment it failed.

    A bare "dead" in the table is unreproducible by the time anyone reads it.
    Four essays were recorded that way in the full batch and each had to be
    chased by hand afterwards. Every timeout now carries its own evidence.
    """
    block = [f"### {reason}", "", "**HUD controls at the moment of failure**", ""]
    block += [f"    {row}" for row in hud_tree(hud)]
    block += ["", "**Application log**", ""]
    block += [f"    {row}" for row in log_tail()]
    block += [""]
    return block


def log_lines() -> int:
    try:
        return len(LOG.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def tone_row_error(
    tone: str, tone_selected: int, commands: dict[str, int]
) -> str | None:
    """Why this tone row is not a measurement, or None when it is one.

    Two different failures, and the harness could only see the second one
    until 2026-08-23.

    A tone that was never SELECTED is the older failure. The message it used
    to print - "the HUD tone control is not reachable through UI Automation" -
    was false often enough to mislead three separate investigations, so it now
    states what was observed and leaves the cause to the reader.

    A tone that was selected and then never REQUESTED is the newer one, and it
    is the dangerous one, because the run completes and produces a number.
    Measured that day: --tone Clear, selected 5 times and confirmed by the
    published state every time, while the app's own log read
    {Proofread: 25, Rewrite: 3} with no Clear request in it - the palette was
    mapping Clear onto the Rewrite command, so the Clear contract was never
    applied. A tone that silently proofreads is scored by the judge exactly
    like a tone that failed to land, which is how a one-line mapping bug spent
    days looking like a prompt-quality problem.
    """
    if not tone:
        return None
    if not tone_selected:
        return (
            f"--tone {tone} was never applied to any section; "
            f"the app ran {commands}."
        )
    if not commands.get(tone):
        return (
            f"--tone {tone} was selected {tone_selected} time(s) but the app "
            f"never ran it; the app ran {commands}. The tone reaching the "
            "writer is not the tone that was asked for."
        )
    return None


def commands_since(since: int = 0) -> dict[str, int]:
    """Which Polish commands the app actually ran, from its own log.

    The harness pressing a tone is not evidence that a tone ran. This reads
    back what the app says it did, so a tone run that silently proofread
    everything is visible as such instead of being reported as a tone result.
    """
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    counts: dict[str, int] = {}
    for line in lines[since:]:
        found = re.search(r"Polish request command=(\w+)", line)
        if found is not None:
            name = found.group(1)
            counts[name] = counts.get(name, 0) + 1
    return counts


def rules_fallbacks_since(since: int = 0) -> int:
    """Sections that got no AI at all, from the app's own log."""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines[since:] if "fell back to rules" in line)


def captured_document_title(since: int = 0) -> str | None:
    """The document the app reported capturing since `since` log lines.

    Read from the app's own log rather than guessed from window focus, because
    the app's view is what decides which document gets corrected.

    The offset is the whole point. Without it the newest line is the PREVIOUS
    essay's capture, so the check either compares against a stale title or
    fires on one - which is exactly what it did on its first run, failing essay
    2 in 7.3 seconds against a line written during essay 1.
    """
    focus_line = re.compile(
        r"Writing HUD editable focus detected title='(?P<title>.*?)' scope="
    )
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines[since:]):
        found = focus_line.search(line)
        if found:
            return found.group("title")
    return None


def open_in_word(path: Path):
    """Open one essay and leave exactly that essay open.

    Every other document is closed first. Leaving the previous essay open is
    what made three batch runs measure the wrong file: --as-a-writer keeps Word
    running, essay 1's window kept the foreground, and essay 2's hotkey press
    captured essay 1 - "review prepared sections=10 atomic=0 characters=5065"
    on 2026-08-17, ten more corrections applied to an essay that was already
    finished, while essay 2 came back byte-identical to its source.

    The app is not at fault for that and must not be "fixed" for it: it
    correctly reviews the document in front of the writer. The harness was
    leaving the wrong one there.
    """
    import comtypes.client

    # Attach to the Word that is already running rather than starting another.
    #
    # CreateObject starts a NEW Word instance every time, and that is the root
    # of the wrong-document defect: essay 1 lived in one instance and essay 2
    # opened in the next, so closing "the other documents" closed nothing that
    # mattered and essay 1's window kept the foreground. The app then reviewed
    # essay 1 again while the harness scored essay 2, three runs in a row. It
    # is also why eight WINWORD processes were found alive earlier.
    try:
        word = comtypes.client.GetActiveObject("Word.Application", dynamic=True)
    except Exception:  # noqa: BLE001 - Word is simply not running yet
        word = comtypes.client.CreateObject("Word.Application", dynamic=True)
        time.sleep(2.0)
    word.Visible = True
    target = str(path.resolve())
    for open_document in list(word.Documents):
        try:
            if str(open_document.FullName) == target:
                continue
        except Exception:  # noqa: BLE001 - Word raises assorted COM errors
            continue
        try:
            # Positional, not SaveChanges=0. The keyword form silently did
            # nothing through the dynamic COM wrapper, which is why three
            # documents were still open after a loop written to close them,
            # and why the failure has to be printed rather than swallowed: a
            # close that quietly does not happen is what leaves the wrong
            # window in front for the next essay.
            open_document.Close(0)
        except Exception as exc:  # noqa: BLE001 - Word raises assorted COM errors
            print(f"   warning: could not close a document: {exc}", flush=True)
    document = word.Documents.Open(target)
    try:
        # Activate explicitly rather than trusting the open to raise the
        # window: with several windows in play Word does not always move the
        # foreground, and the HUD reviews whatever is in front.
        document.Activate()
        window = document.ActiveWindow
        window.Activate()
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors
        pass
    time.sleep(3.0)
    remaining = []
    try:
        remaining = [str(item.Name) for item in word.Documents]
    except Exception:  # noqa: BLE001 - Word raises assorted COM errors
        pass
    if len(remaining) > 1:
        print(
            f"   warning: {len(remaining)} documents open, expected 1: {remaining}",
            flush=True,
        )
    return word


def paragraphs(value: str) -> list[str]:
    return [
        line.strip()
        for line in str(value).replace("\r", "\n").split("\n")
        if line.strip()
    ]


def review_one(path: Path, *, max_presses: int = 30, tone: str = "") -> dict:
    """Accept every section, then return the saved document text.

    With `tone` set, each section is put through the tone picker before it is
    accepted, which is the only way this harness can measure tone at all.
    Until 2026-08-18 it only ever pressed Replace: a full 20-essay run issued
    213 Polish requests and every one was command=Proofread, so "run the essays
    through Word" could not see the tone feature and never had. The tone
    numbers quoted up to then all came from a headless probe.
    """
    log_start = log_lines()
    word = open_in_word(path)
    _handle, bounds = foreground_word()
    click_in_document(bounds)
    press_hotkey()

    report: dict = {"presses": 0, "stalled_at": None, "counters": []}
    # Which document the app actually captured, from the app's own log, before
    # a single key is pressed. Three runs in a row scored an essay the app had
    # never looked at, and reported err=None while doing it, because nothing
    # here compared the two. A wrong-document run is now an error rather than
    # a quiet zero-change result.
    captured = captured_document_title(log_start)
    if captured is not None and path.stem.lower() not in captured.lower():
        report["error"] = (
            f"the review on screen was captured from {captured!r}, "
            f"not from {path.stem}"
        )
        report["after"] = ""
        return report
    hud = hud_window(40.0)
    if hud is None:
        report["error"] = "the Writing HUD never appeared"
        report["after"] = str(word.ActiveDocument.Content.Text or "")
        return report

    report["diagnosis"] = []
    deadline = time.monotonic() + 120.0
    reviewed = False
    while time.monotonic() < deadline:
        if counter_text(hud) not in ("<none>", "0 of 0", ""):
            reviewed = True
            break
        badge = badge_button(hud)
        if badge is not None and badge.IsEnabled and invoke(badge):
            # Open the review, the way a reader does. Without this the wait
            # could never end: the counter it watches for does not exist until
            # the badge is clicked.
            report["opened_badge"] = report.get("opened_badge", 0) + 1
            time.sleep(3.0)
            hud = hud_window(8.0) or hud
        time.sleep(1.0)
    if not reviewed:
        # The failure that killed essays 5, 7, 11 and 20, and essay 18 on a
        # freshly restarted app. Record it here rather than leaving a bare
        # "dead" that has to be reproduced by hand later.
        report["timed_out"] = True
        report["diagnosis"] = diagnose(
            "First review never appeared within 120 seconds",
            hud_window(5.0) or hud,
        )

    if reviewed:
        # Clicking the badge opens the card at once, but the sections behind it
        # are still being prepared. Pressing into that half-built review made
        # essay 15 dismiss FOURTEEN sections whose corrections were still
        # arriving. Wait for the first correction to actually be offered.
        settle = time.monotonic() + 180.0
        while time.monotonic() < settle:
            hud = hud_window(8.0) or hud
            state = review_readiness(hud)
            if state in {"ready", "finished"}:
                break
            if state == "":
                # The HUD is not reporting readiness (older build, or the card
                # is not up yet). Fall back to the old signal rather than
                # waiting out the whole timeout for a fact nobody is stating.
                button = replace_button(hud)
                if button is not None and button.IsEnabled:
                    break
            time.sleep(2.0)
        report["settled"] = review_readiness(hud_window(8.0) or hud)
    report["_log_start"] = log_start

    for press in range(1, max_presses + 1):
        hud = hud_window(10.0)
        if hud is None:
            report["stopped_because"] = f"the HUD was gone at press {press}"
            break
        before = counter_text(hud)
        if tone:
            # Ask for the tone, then wait for the answer it triggers. Pressing
            # Replace straight after selecting would apply whatever the card
            # was already showing - the proofread - and the run would look
            # like a tone run while measuring nothing of the kind.
            #
            # Choosing the SAME tone again is correctly a no-op, so on every
            # section after the first the choice alone changes nothing: the
            # first run of this ran Friendly exactly once across eleven
            # sections. Regenerate is what re-runs the section under the tone
            # already chosen, and it is what a writer would press too.
            if select_tone(hud, tone):
                report["tone_selected"] = report.get("tone_selected", 0) + 1
                # Skip the regenerate when this section already carries the
                # tone. A batched tone request tones four or five sections at
                # once, so most sections arrive already done, and regenerating
                # them spent about 69 seconds each re-asking for work that had
                # been finished minutes earlier: 4 sections in 277 seconds.
                published = harness_state.recent_state()
                already = bool(published and published.get("section_toned"))
                if already:
                    report["tone_already"] = report.get("tone_already", 0) + 1
                else:
                    again = regenerate_button(hud)
                    if again is not None and again.IsEnabled and invoke(again):
                        report["tone_regenerated"] = (
                            report.get("tone_regenerated", 0) + 1
                        )
                waited = time.monotonic() + (0.0 if already else 90.0)
                while time.monotonic() < waited:
                    time.sleep(3.0)
                    hud = hud_window(8.0) or hud
                    ready = replace_button(hud)
                    if ready is not None and ready.IsEnabled:
                        break
            else:
                report["tone_unavailable"] = report.get("tone_unavailable", 0) + 1
        # Wait for the model before looking at the button at all.
        #
        # Replace is disabled while a section has no AI answer yet, so reading
        # the button first cannot tell "the AI is still working" from "there is
        # nothing to apply here". The card states which it is; wait on that.
        hud, provenance = wait_for_the_ai(hud_window, hud)
        report.setdefault("provenance", []).append(provenance)
        if provenance:
            report["provenance_counts"] = report.get("provenance_counts", {})
            report["provenance_counts"][provenance] = (
                report["provenance_counts"].get(provenance, 0) + 1
            )
        button = replace_button(hud)
        if button is None:
            report["stopped_because"] = (
                f"no Replace button at press {press}; "
                + harness_state.describe(harness_state.read_state())
            )
            break
        if not button.IsEnabled:
            # Only wait while the app might still produce a correction. Once it
            # reports every section finished, a disabled Replace means this one
            # has none, and polling for 45 seconds waits for something the app
            # has already said will not happen - that cost essay 19 most of its
            # 356 seconds. Before the readiness signal existed this poll was the
            # only way to tell the two apart.
            if review_readiness(hud) not in {"ready", "finished"}:
                wait_until = time.monotonic() + 45.0
                while time.monotonic() < wait_until:
                    time.sleep(3.0)
                    button = replace_button(hud_window(8.0) or hud)
                    if button is not None and button.IsEnabled:
                        break
            if button is None or not button.IsEnabled:
                # No correction to apply here. Do what a reader does rather
                # than calling the essay stalled: dismiss and keep going.
                skip = dismiss_button(hud)
                if skip is not None and skip.IsEnabled and invoke(skip):
                    report["dismissed"] = report.get("dismissed", 0) + 1
                    report["counters"].append(f"{before}->dismissed")
                    time.sleep(4.0)
                    continue
                report["stalled_at"] = before
                report["diagnosis"] = diagnose(
                    f"Replace and Dismiss both unavailable at {before}", hud
                )
                break
        if not invoke(button):
            report["stopped_because"] = (
                f"pressing Replace failed at press {press}; "
                + harness_state.describe(harness_state.read_state())
            )
            break
        report["presses"] = press
        time.sleep(6.0)
        hud = hud_window(8.0)
        if hud is None:
            # The last unnamed exit, and the one that was actually being taken.
            # A run stopped at four presses of twenty-five with no stall, no
            # error and no reason, three times in a row, because this break
            # recorded nothing. presses is set just above it, so the count
            # looked like a result rather than an interruption.
            report["stopped_because"] = (
                f"the HUD could not be found within 8s after press {press}; "
                + harness_state.describe(harness_state.read_state())
            )
            break
        after = counter_text(hud)
        report["counters"].append(f"{before}->{after}")
        if after == before:
            # Poll rather than checking once more after a fixed pause. Six
            # seconds plus ten was not enough for a section whose correction
            # was still being generated, so essays 2, 11, 13 and 15 were
            # recorded as stalled at "1 of 11" after a single press, in 40 to
            # 46 seconds, with ~90 unknown words - the untouched original.
            # Essay 2 had been clean on the previous run. That is the harness
            # giving up on the app, not the app failing.
            patient = time.monotonic() + 60.0
            retried = 0
            while time.monotonic() < patient:
                time.sleep(4.0)
                hud = hud_window(8.0) or hud
                again = counter_text(hud)
                if again != before:
                    break
                # The app says so itself on the button: "This correction was
                # not applied. The document may have been busy. Try Replace
                # Text again." Word occasionally refuses the write, and the
                # correction is still there waiting. Pressing once and calling
                # it a stall ignores the one instruction the app gave.
                retry = replace_button(hud)
                message = str(getattr(retry, "Name", "") or "")
                if (
                    retry is not None
                    and not retry.IsEnabled
                    and review_readiness(hud) in {"ready", "finished"}
                ):
                    # Replace is dead and the app says nothing more is coming
                    # for this section, so there is no correction to wait for.
                    # This is NOT the paused-counter case that was measured
                    # throwing away eight sections: there, work was still
                    # arriving. Here the app has said it has finished.
                    skip = dismiss_button(hud)
                    if skip is not None and skip.IsEnabled and invoke(skip):
                        report["dismissed"] = report.get("dismissed", 0) + 1
                        time.sleep(4.0)
                        again = counter_text(hud_window(8.0) or hud)
                        if again != before:
                            break
                if "not applied" in message and retry is not None and retried < 3:
                    if retry.IsEnabled and invoke(retry):
                        retried += 1
                        report["reapplied"] = report.get("reapplied", 0) + 1
                        time.sleep(6.0)
                        again = counter_text(hud_window(8.0) or hud)
                        if again != before:
                            break
            else:
                again = before
            if again == before:
                # Do NOT dismiss here, although the captured evidence shows
                # Dismiss enabled at this moment. Measured over three runs,
                # dismissing on a counter that has merely paused threw away
                # eight sections whose corrections were still arriving and
                # turned complete 2/3 into partial 2/3, dead 1/3. A paused
                # counter means wait; only a disabled Replace means skip.
                report["stalled_at"] = before
                report["diagnosis"] = diagnose(
                    f"Counter stopped advancing at {before}", hud
                )
                break

    document = word.ActiveDocument
    try:
        document.Save()
    except Exception:  # noqa: BLE001 - report the text we can still read
        pass
    report["after"] = str(document.Content.Text or "")
    return report


def score(before_text: str, after_text: str, dictionary) -> dict:
    before = paragraphs(before_text)
    after = paragraphs(after_text)
    unchanged = [
        index + 1
        for index in range(min(len(before), len(after)))
        if before[index] == after[index]
    ]
    unknown: list[str] = []
    for paragraph in after:
        for match in WORD.finditer(paragraph):
            token = match.group(0)
            if token[:1].isupper():
                continue  # names are not the speller's business
            if not dictionary.contains(token.casefold()):
                unknown.append(token)
    artefacts = [match.group(0) for match in ARTEFACT.finditer("\n".join(after))]
    return {
        "paragraphs_before": len(before),
        "paragraphs_after": len(after),
        "unchanged": unchanged,
        "unknown_words": sorted(set(unknown)),
        "artefacts": sorted(set(artefacts)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=int, default=2)
    parser.add_argument("--to", dest="end", type=int, default=20)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per essay; one run is a hypothesis, not a result",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="do not restart the app between essays (measures the batch, not the essay)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS,
        help="the folder of .docx essays to run; defaults to the animal-pet corpus",
    )
    parser.add_argument(
        "--tone",
        default="",
        choices=["", "Clear", "Improve", "Rephrase", "Shorten", "Formal", "Friendly"],
        help=(
            "apply this tone to every section before accepting it. Without it "
            "the run only proofreads, which is all this harness could ever do "
            "before 2026-08-18 and is why no tone number ever came from Word."
        ),
    )
    parser.add_argument(
        "--as-a-writer",
        action="store_true",
        help=(
            "one Word session and one app for the whole run, the way somebody "
            "actually works. Implies --no-restart and never kills Word."
        ),
    )
    args = parser.parse_args()
    # Killing Word and restarting the app between essays is isolation, not use.
    # It exists because the HUD carried one document's review into the next, and
    # while it is switched on that defect cannot be seen and no run measures
    # what a writer would get on their second document.
    if args.as_a_writer:
        args.no_restart = True

    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()

    essays = _essay_files(args.corpus)
    corpus_text = [str(item.get("text", "")) for item in load_essays(args.corpus)]
    work = ROOT / "build" / "batch"
    work.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "build" / "corrected"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for number in range(args.start, args.end + 1):
        if number > len(essays):
            break
        source = essays[number - 1]
        target = work / source.name
        before_text = str(corpus_text[number - 1])

        outcomes: list[str] = []
        for attempt in range(1, max(1, args.repeat) + 1):
            # Release the previous run's lock before copying. A writer never
            # does this, so --as-a-writer leaves Word alone and relies on
            # word_essay_session closing the document it opened and waiting out
            # a busy Word instead of killing it.
            if not args.as_a_writer:
                close_word()
            else:
                # Word still holds the last copy of this document, and a fresh
                # source cannot be written over a file Word has open.
                release_document(target)
            shutil.copy2(source, target)  # every run starts from the original
            if not args.no_restart and not restart_app():
                # The app did not come up. Every essay after this one will
                # report zero presses and "the Writing HUD never appeared",
                # which is an environment failure wearing the costume of a
                # quality result.
                #
                # On 2026-08-20 the overnight full tier hit this on its first
                # essay and kept going, and would have spent about five hours
                # producing nothing. Stopping here is the difference between
                # reporting that the app would not start and reporting twenty
                # essays of zeroes that somebody has to interpret.
                print(  # noqa: T201
                    "\nSTOPPING: the app did not start, so nothing after this "
                    "would measure the writing. This is an environment "
                    "failure, not a corpus result.",
                    flush=True,
                )
                return 2
            started = time.monotonic()
            try:
                result = review_one(target, tone=args.tone)
            except Exception as exc:  # noqa: BLE001 - keep going through the corpus
                result = {"error": f"{type(exc).__name__}: {exc}", "after": ""}
            elapsed = round(time.monotonic() - started, 1)

            judged = score(before_text, result.get("after", ""), dictionary)
            row = {
                "essay": source.stem,
                "number": number,
                "attempt": attempt,
                "seconds": elapsed,
                "presses": result.get("presses", 0),
                "dismissed": result.get("dismissed", 0),
                "stalled_at": result.get("stalled_at"),
                "error": result.get("error"),
                "timed_out": bool(result.get("timed_out")),
                "tone": args.tone,
                "tone_selected": result.get("tone_selected", 0),
                "tone_unavailable": result.get("tone_unavailable", 0),
                "tone_regenerated": result.get("tone_regenerated", 0),
                # What the APP says it ran, not what the harness pressed.
                "commands": commands_since(result.get("_log_start", 0)),
                "rules_fallbacks": rules_fallbacks_since(result.get("_log_start", 0)),
                **judged,
            }
            row["error"] = row["error"] or tone_row_error(
                args.tone, row["tone_selected"], row["commands"]
            )
            row["outcome"] = outcome(row)
            outcomes.append(row["outcome"])
            summary.append(row)
            print(
                f"{number:>2} {source.stem[:30]:<30} run{attempt} "
                f"{elapsed:>6}s {row['outcome']:<8} presses={row['presses']:>2} "
                f"dismissed={row['dismissed']:>2} "
                f"unchanged={len(row['unchanged']):>2} "
                f"unknown={len(row['unknown_words']):>3} "
                f"artefacts={len(row['artefacts']):>2} "
                f"stalled={row['stalled_at']} err={row['error']}"
                + (
                    f" tone={args.tone} applied={row['tone_selected']}"
                    f" ran={row['commands']} rules={row['rules_fallbacks']}"
                    if args.tone
                    else ""
                ),
                flush=True,
            )

        if args.repeat > 1:
            tally = {name: outcomes.count(name) for name in sorted(set(outcomes))}
            summary_line = "  ".join(
                f"{name} {count}/{len(outcomes)}" for name, count in tally.items()
            )
            print(f"   -> {source.stem[:30]:<30} {summary_line}", flush=True)

        lines = [f"# {source.stem}", ""]
        diagnosis = result.get("diagnosis") or []
        if diagnosis:
            # Put it first. A failure the reader has to scroll past the whole
            # document to find is a failure nobody reads.
            lines += ["## Why this run failed", ""] + diagnosis
        b = paragraphs(before_text)
        a = paragraphs(result.get("after", ""))
        for index in range(max(len(b), len(a))):
            lines.append(f"## Paragraph {index + 1}")
            lines.append("")
            lines.append(f"**WAS:** {b[index] if index < len(b) else '<missing>'}")
            lines.append("")
            lines.append(f"**NOW:** {a[index] if index < len(a) else '<missing>'}")
            lines.append("")
        (out_dir / f"{source.stem}.review.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    (ROOT / "build" / "batch-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Leave the writer's Word where they left it.
    if not args.as_a_writer:
        close_word()
    print(f"\nsummary: {ROOT / 'build' / 'batch-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
