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
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import traceback
from uuid import uuid4

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
from word_essay_session import focus_named_word_window  # noqa: E402

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


def _hud_window_candidates() -> str:
    """Every window whose title mentions Manuscript, visible or not.

    `hud_found=False` means the lookup skipped it, and there are two very
    different reasons: the window is GONE, or it is present but not visible.
    EnumWindows-based lookups filter on IsWindowVisible, so a hidden HUD reads
    exactly like a closed one, and the fix for each is opposite - wait longer,
    or stop requiring visibility.

    That ambiguity survived a 30-second poll on 2026-08-30: both remaining
    failures still reported hud_found=False, which rules out a repaint gap and
    leaves these two open. Never raises.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        proto = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
        )
        seen: list[str] = []

        def callback(hwnd, _result):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value
                if "Manuscript" in title:
                    visible = bool(user32.IsWindowVisible(hwnd))
                    seen.append(f"{title[:40]!r} visible={visible}")
            return True

        user32.EnumWindows(proto(callback), ctypes.byref(ctypes.c_int(0)))
        return "[" + ", ".join(seen) + "]" if seen else "<no Manuscript window at all>"
    except Exception as exc:  # noqa: BLE001 - diagnostics must not mask the failure
        return f"<candidate scan failed: {type(exc).__name__}>"


def _hud_tree_summary(hud, limit: int = 14) -> str:
    """The buttons actually present in the HUD, for a failure that found none.

    Deliberately narrow: control type plus name plus enabled, buttons first,
    truncated. A full dump is thousands of lines and the batch already refuses
    to print those; what a reader needs is whether the tree held a DIFFERENT
    button, no buttons at all, or nothing whatsoever - three causes that the
    message "no Replace button" cannot tell apart.

    Never raises. This runs on a path that is already failing, and losing the
    diagnosis to an exception in the diagnostic would be the worst outcome.
    """
    if hud is None:
        return "<no hud control>"
    try:
        from repro_accept_advance import _descend

        names = []
        for control in _descend(hud):
            try:
                kind = str(control.ControlTypeName or "")
                if kind != "ButtonControl":
                    continue
                name = str(control.Name or "")[:44]
                names.append(f"{name!r}{'' if control.IsEnabled else ' (disabled)'}")
            except Exception:  # noqa: BLE001 - a repainting control answers oddly
                continue
            if len(names) >= limit:
                names.append("...")
                break
        return "[" + ", ".join(names) + "]" if names else "<no buttons in tree>"
    except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the failure
        return f"<tree read failed: {type(exc).__name__}>"


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
    """Legacy global termination is forbidden: unrelated work may be unsaved."""
    raise RuntimeError("Refusing to terminate Word; release only the disposable test document")


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


def log_lines() -> dict:
    """Capture a file identity and byte offset that survive log rotation."""
    with LOG.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        return {"file_id": os.fstat(stream.fileno()).st_ino, "offset": stream.tell()}


def _lines_since(since: int | dict = 0) -> list[str]:
    # Integer offsets remain supported for existing probes and saved fixtures.
    if isinstance(since, int):
        try:
            return LOG.read_text(encoding="utf-8", errors="replace").splitlines()[since:]
        except OSError:
            return []
    chunks = []
    found = False
    # Match the application's RotatingFileHandler backupCount=3. Losing the
    # cursor is an invalid measurement, never permission to read older events.
    for path in [LOG.with_name(LOG.name + f".{n}") for n in (3, 2, 1)] + [LOG]:
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if not found and stat.st_ino == since["file_id"]:
                    if stat.st_size < since["offset"]:
                        raise RuntimeError("application log truncated during measurement")
                    stream.seek(since["offset"])
                    found = True
                if found:
                    chunks.append(stream.read())
        except FileNotFoundError:
            continue
    if not found:
        raise RuntimeError("application log cursor lost during measurement")
    return b"".join(chunks).decode("utf-8", errors="replace").splitlines()


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


def review_finished_since(since: int | dict = 0) -> str:
    """The app's own statement that the review completed, or "".

    The HUD closes when a review is finished, which from the harness looks
    exactly like the HUD disappearing - and those are opposite outcomes. The app
    already distinguishes them in its log; nothing was reading it.

    Returns the reported character count so the stop reason carries evidence
    rather than an assertion.
    """
    lines = _lines_since(since)
    for line in reversed(lines):
        found = re.search(
            r"review finished; will not auto-offer this text again (.+)$", line
        )
        if found:
            return found.group(1).strip()
    return ""


def record_review_completion(report: dict, presses: int) -> bool:
    """Recognize the app acknowledgement before treating its closed HUD as lost."""
    finished = review_finished_since(report.get("_log_start", 0))
    if not finished or presses <= 0:
        return False
    report["stopped_because"] = (
        f"the review finished after press {presses}; "
        f"section {presses}/{presses}, {finished}"
    )
    report["completion_acknowledgement"] = finished
    return True


def commands_since(since: int | dict = 0) -> dict[str, int]:
    """Which Polish commands the app actually ran, from its own log.

    The harness pressing a tone is not evidence that a tone ran. This reads
    back what the app says it did, so a tone run that silently proofread
    everything is visible as such instead of being reported as a tone result.
    """
    lines = _lines_since(since)
    counts: dict[str, int] = {}
    for line in lines:
        found = re.search(r"Polish request command=(\w+)", line)
        if found is not None:
            name = found.group(1)
            counts[name] = counts.get(name, 0) + 1
    return counts


def rules_fallbacks_since(since: int | dict = 0) -> int:
    """Sections that got no AI at all, from the app's own log."""
    lines = _lines_since(since)
    return sum(1 for line in lines if "fell back to rules" in line)


def captured_document_title(since: int | dict = 0) -> str | None:
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
    lines = _lines_since(since)
    for line in reversed(lines):
        found = focus_line.search(line)
        if found:
            return found.group("title")
    return None


# The capture must appear before the HUD is usable, and the line after this
# check waits 40 seconds for the HUD and then 120 for the review. Ten seconds
# for the capture was out of step with both, and on 2026-09-06 it failed all
# three documents of a slice in 17 to 21 seconds each - "captured from None" -
# which the cycle then scored as a completion rate of 0.0 and recorded as a
# baseline. The bound is now the HUD's own, because a capture that has not
# arrived by then will not produce a usable review either.
_CAPTURE_WAIT_SECONDS = 40.0


def wait_for_document_capture(
    stem: str, since: int | dict, timeout: float = _CAPTURE_WAIT_SECONDS
) -> str | None:
    """Await the asynchronous target capture without accepting an old review."""
    deadline = time.monotonic() + timeout
    captured = None
    while time.monotonic() < deadline:
        captured = captured_document_title(since)
        if captured is not None and stem.casefold() in captured.casefold():
            return captured
        time.sleep(0.2)
    return captured


def _foreground_title() -> str:
    """The title of whatever window currently has the foreground, or "".

    Separate from _hold_focus so the decision it feeds can be tested without a
    live desktop.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value or ""
    except Exception:  # noqa: BLE001 - ctypes raises broadly on a locked desktop
        # An unreadable foreground means re-focusing is the safe guess: the cost
        # is a redundant call, the alternative is correcting the wrong document.
        return ""


def _hold_focus(stem: str) -> None:
    """Put the essay's Word window back in front if something took it.

    Cheap on the common path: it only calls into UI automation when the
    foreground window is not already the document being corrected.
    """
    title = _foreground_title()
    if stem.lower() in title.lower() and title.endswith("- Word"):
        return
    focus_named_word_window(stem)


def recover_hud(stem: str, report: dict):
    """Take focus back after the HUD followed it somewhere else, mid-run.

    Measured on 2026-08-27: a twenty-essay batch lost 4 of its first 10 essays,
    and three of the four were the HUD simply not being there - "the HUD was
    gone at press 1", "the HUD could not be found within 8s after press 15",
    and one that sat at view=badge with provenance=waiting and nothing ever
    ready. The app log named the cause at the exact second of the first
    failure: `focus detected title='Claude'`, nine times across the run.

    The app is not at fault and must not be "fixed" for this. Following focus
    into whatever editable window the writer is using is the dictation feature
    working correctly. It is this harness that has to hold Word in front.

    Two things this deliberately does NOT do:

    It does not press the hotkey. The recovery that runs before the first press
    does, which is safe there because no progress exists yet. Mid-run a press
    re-captures the document and restarts the review at section one, and the
    presses that follow would land on a fresh review while the counters kept
    climbing - a wrong number rather than a failure, which is the outcome this
    harness exists to prevent. Re-focusing is enough, because the HUD tracks
    focus on its own.

    It does not return a HUD it cannot vouch for. If the app says the review on
    screen came from another window, that is a failure and it is reported as
    one.
    """
    # Name whatever is holding the foreground, on every failed attempt.
    #
    # This failure has been "the HUD was gone" for three runs and nobody has
    # ever measured WHAT took the screen. More patience is already refuted -
    # six attempts with a lengthening wait scored the same 80.0 as three flat
    # ones, 2026-09-02 - so the question is no longer how long to wait but who
    # to take it back from, and that cannot be answered without the name.
    #
    # Costs one Win32 call on a path that has already failed, and it is the
    # difference between guessing at a fix and knowing which one to write.
    def _foreground() -> str:
        try:
            from echo_link_core.insertion import active_window

            window = active_window()
            return f"{window.title!r} class={window.class_name!r} pid={window.process_id}"
        except Exception:  # noqa: BLE001 - diagnosis must never break the run
            return "unreadable"

    for _attempt in range(3):
        focus_named_word_window(stem)
        time.sleep(2.0)
        hud = hud_window(10.0)
        if hud is None:
            print(
                f"    recover_hud attempt {_attempt + 1}: no HUD, "
                f"foreground is {_foreground()}",
                flush=True,
            )
            continue
        # No `since` offset on purpose. That offset exists so the check before
        # the first press cannot read a line left by the PREVIOUS essay, which
        # once failed essay 2 in 7.3 seconds against a line written during
        # essay 1. Here the most recent capture is exactly the question being
        # asked - where is the HUD pointing right now - and this reads the log
        # backwards, so the default returns it.
        captured = captured_document_title()
        if captured is not None and stem.lower() not in captured.lower():
            # The HUD is back but it is showing somebody else's document.
            # Taking focus again is the only honest move; accepting this is how
            # a review of 26 characters of chat text once got clicked five
            # times and reported as "sections=5 accepted=5".
            continue
        report.setdefault("hud_recoveries", 0)
        report["hud_recoveries"] += 1
        return hud
    return None


def open_in_word(path: Path):
    """Open and activate a disposable essay without closing other documents."""
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
    # Other windows may contain unsaved user work. Activate only our copy.
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
    # Hold the document before the FIRST capture too, not only between presses.
    #
    # _hold_focus was added for the press loop, which left the opening capture
    # unguarded - and that is where essay 10 died on 2026-08-28, 6.7 seconds in,
    # with "the review on screen was captured from 'Claude'". The hotkey press
    # below is what tells the app to capture, so whatever holds focus at this
    # instant is what gets reviewed.
    #
    # Worth recording because it changes the diagnosis: nobody was typing. An
    # earlier failure the same night was caused by commands run mid-experiment,
    # but this one happened while the machine was left alone, between two
    # essays. Focus can move on its own, so "do not touch the machine" is
    # necessary and not sufficient.
    _hold_focus(path.stem)
    press_hotkey()

    report: dict = {"presses": 0, "stalled_at": None, "counters": [], "_log_start": log_start}
    # Which document the app actually captured, from the app's own log, before
    # a single key is pressed. Three runs in a row scored an essay the app had
    # never looked at, and reported err=None while doing it, because nothing
    # here compared the two. A wrong-document run is now an error rather than
    # a quiet zero-change result.
    captured = wait_for_document_capture(path.stem, log_start)
    if captured is None or path.stem.lower() not in captured.lower():
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
        # Take the foreground back before EVERY press, not only when something
        # has already gone wrong.
        #
        # recover_hud handles the HUD being absent. This handles the commoner
        # and quieter case: the HUD is right there, but pointing somewhere else.
        # The first AutoResearch experiment died exactly that way - "no Replace
        # button at press 1; section 1/4, ready 0, view=badge" - with the app
        # log showing `focus detected title='Claude'` four times. The HUD was
        # found, so no recovery fired, and it was showing a badge for the wrong
        # window. unknown=54 and unchanged=11: the essay was never corrected.
        #
        # The app is doing the right thing. It follows the writer's focus, which
        # is the dictation feature. A batch run is the unusual case - nobody is
        # watching, and any editable window that steals focus silently redirects
        # the review - so the harness re-asserts between presses. This costs
        # milliseconds when focus is already correct, because it verifies before
        # it acts.
        _hold_focus(path.stem)
        hud = hud_window(10.0)
        if hud is None:
            hud = recover_hud(path.stem, report)
        if hud is None:
            # A finished review and a vanished one look identical from here,
            # and they are opposite outcomes.
            #
            # Measured 2026-09-02 on 03_multi_level_outline: the app logged
            # `section accepted ... pending 1 -> 0 completed=4 of 4` and then
            # `review finished; will not auto-offer this text again`. Every
            # section was applied and the HUD closed because it was DONE. The
            # harness pressed once more, correctly found no HUD, and reported
            # "the HUD was gone" - and because that message carries no
            # `section M/M` marker, the completion rule scored a successful run
            # as truncated. The whole reliability metric was under-reporting on
            # the strength of it, and three runs of "the HUD was gone" were read
            # as a focus fault that never happened: the foreground at every
            # failed attempt was Word itself, with the right document open.
            #
            # The app says which one it is, so ask it rather than guess.
            finished = review_finished_since(report.get("_log_start", 0))
            if finished:
                report["stopped_because"] = (
                    f"the review finished after press {press - 1}; "
                    f"section {press - 1}/{press - 1}, {finished}"
                )
                break
            report["stopped_because"] = (
                f"the HUD was gone at press {press} and three attempts to take "
                "focus back from another window failed"
            )
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
        #
        # A tone gets far longer, and a section still waiting is waited on again
        # rather than pressed on with. Measured 2026-08-31: the Friendly baseline
        # died at press 24 of 25 with `provenance=waiting`, EVERY button
        # disabled, and the Replace control still reading "Proofreading loc..."
        # - the model was genuinely mid-section. The HUD window was found and
        # visible, so this is not the window race fixed the day before; the
        # 90-second wait simply expired and the loop walked into a button lookup
        # that could not succeed, then reported "no Replace button" as though
        # something were broken.
        #
        # A tone request regenerates the whole span after proofreading it, so it
        # is the slowest thing this harness ever waits for. Giving up on it at 90
        # seconds and calling the result a failure measures the clock, not the
        # product.
        patience = 300.0 if tone else 90.0
        hud, provenance = wait_for_the_ai(hud_window, hud, seconds=patience)
        if provenance == "waiting":
            # One more full round before proceeding. Bounded, because a section
            # that never settles has to end the run eventually - but the reason
            # will then say `provenance=waiting` after ten minutes, which is a
            # claim about the product rather than about the harness's patience.
            hud, provenance = wait_for_the_ai(hud_window, hud, seconds=patience)
        report.setdefault("provenance", []).append(provenance)
        if provenance:
            report["provenance_counts"] = report.get("provenance_counts", {})
            report["provenance_counts"][provenance] = (
                report["provenance_counts"].get(provenance, 0) + 1
            )
        # Wait for the card before looking for its button. The HUD passes
        # through a "loading" view while it swaps sections, and the Replace
        # button is not in the UI tree during it - so a look taken at that
        # instant finds nothing and the run ends.
        #
        # Measured 2026-08-23 on a clean uncontested run: stopped at press 1
        # of 25 with "no Replace button", while the published state said
        # ready 25, refused 0, replace_enabled=True, view=loading. Every
        # section was finished and applicable; the harness was simply looking
        # a moment too early. The state file has carried `view` all along and
        # can_press_replace has always required "card" - the press loop was
        # the one place not consulting it.
        settled = harness_state.wait_for(
            lambda state: state.get("view") != "loading",
            timeout=30.0,
            what="the HUD to leave the loading view",
        )
        # Then wait for the HUD WINDOW itself, not just for the view to settle.
        #
        # This is the actual cause of "no Replace button", found on 2026-08-30
        # once the failure started reporting what the tree held. BOTH failures
        # in the reproduction carried `hud_found=False` - `hud_window(8.0)` had
        # not found the window - and on one of them the stale control it fell
        # back to DID contain 'Replace text in source box. Local AI | CUDA'. The
        # button existed. `replace_button` had looked a moment too early and the
        # diagnostic, running milliseconds later, found it.
        #
        # So the HUD briefly leaves the desktop between sections, and one look
        # taken during that gap ended the run. Waiting for the view to leave
        # "loading" was necessary and not sufficient: the published state can
        # say `view=card, replace_enabled=True` while the window itself is
        # momentarily absent, which is exactly what both failures reported.
        #
        # Polling for both together also removes the stale-control fallback,
        # which is what made this present as two different bugs - sometimes the
        # old control still answered and sometimes it was empty.
        found_hud = None
        button = None
        if settled:
            deadline = time.monotonic() + 30.0
            while True:
                found_hud = hud_window(4.0)
                if found_hud is not None:
                    button = replace_button(found_hud)
                    if button is not None:
                        break
                if time.monotonic() >= deadline:
                    break
                time.sleep(1.0)
        if button is None:
            # Say what WAS there, not only what was missing.
            #
            # "no Replace button at press 1" has now cost four gates and two
            # days of guessing, twice being blamed on the harness looking too
            # early when the app was genuinely hung, and once the reverse. The
            # message names an absence, and an absence is consistent with every
            # hypothesis: a hidden button, a renamed one, a stale window handle,
            # a tree deeper than the walk, a HUD that never appeared. Naming the
            # deepest traceback frame did exactly this for the COMError today and
            # pointed at the real caller on the first run afterwards.
            report["stopped_because"] = (
                f"no Replace button at press {press}; "
                + harness_state.describe(harness_state.read_state())
                + f"; hud_found={found_hud is not None}"
                + f"; tree={_hud_tree_summary(found_hud or hud)}"
                + f"; windows={_hud_window_candidates()}"
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
        if record_review_completion(report, press):
            break
        hud = hud_window(8.0)
        if hud is None and record_review_completion(report, press):
            break
        if hud is None:
            hud = recover_hud(path.stem, report)
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

    save_review_document(word, path, report)
    return report


def save_review_document(word, path: Path, report: dict) -> None:
    """Save/read back only the disposable target, even after focus changes."""
    try:
        document = word.ActiveDocument
        if Path(str(document.FullName)).resolve() != path.resolve():
            report["error"] = "Word target changed before save; no document saved"
            return
        document.Save()
        if not document.Saved:
            report["error"] = "Word still reports unsaved changes after Save"
        report["after"] = str(document.Content.Text or "")
    except Exception as exc:  # noqa: BLE001 - preserve explicit COM save/readback failure
        report["error"] = f"Word save/readback failed: {type(exc).__name__}: {exc}"


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



def _idle_seconds() -> float:
    """Seconds since anyone touched the machine.

    Delegates to autoresearch_run rather than re-implementing GetLastInputInfo,
    so the two paths cannot drift on what "idle" means. Its rule matters and is
    inherited: an idle check that CANNOT answer returns 0.0, meaning assume
    somebody is here, because a check that cannot answer must never grant
    permission to seize the screen.
    """
    try:
        import autoresearch_run

        return autoresearch_run.idle_seconds()
    except Exception:  # noqa: BLE001 - a missing import must not free the gate
        return 0.0


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
        "--require-idle",
        type=float,
        default=0.0,
        help="stop before a document if somebody has touched the machine "
        "within this many seconds. program.md requires it of every measuring "
        "path; typing in another editable window redirects the HUD and the "
        "run then measures the interruption.",
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
    parser.add_argument("--output-root", type=Path, default=ROOT / "build",
                        help="isolated directory for batch documents and evidence")
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
    work = args.output_root / "batch"
    work.mkdir(parents=True, exist_ok=True)
    session_tag = uuid4().hex[:12]
    working = args.output_root / "working"
    working.mkdir(parents=True, exist_ok=True)
    out_dir = args.output_root / "corrected"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    stopped_for_idle = False
    for number in range(args.start, args.end + 1):
        if number > len(essays):
            break
        # THE IDLE GATE, CHECKED ONLY BEFORE THE FIRST DOCUMENT.
        #
        # It was checked before EVERY document for one day, and that made a
        # multi-document run impossible to finish. THE HARNESS ITSELF TYPES:
        # it drives Word through SendInput, and GetLastInputInfo counts
        # synthetic input exactly like a person's. Measured 2026-09-04 - one
        # synthetic VK_SHIFT takes idle from 308 seconds to 0.
        #
        # So document one would run, the harness's own keystrokes would reset
        # the timer, and document two would be refused as "somebody is at the
        # keyboard". The loop attempted a baseline 120 times over four hours and
        # stopped after exactly one document every time, writing nothing. The
        # gate meant to protect the measurement made it unobtainable.
        #
        # Before the first document the question is meaningful: is a person
        # using this machine right now. After that it cannot be asked this way.
        # Mid-run interference is caught where it actually shows - the harness
        # already fails a document with "the review on screen was captured from
        # 'Claude'" when focus moves to another editable window, which is the
        # real symptom and needs no timer.
        if args.require_idle > 0 and number == args.start:
            idle = _idle_seconds()
            if idle < args.require_idle:
                print(  # noqa: T201
                    f"\nSTOPPING before {essays[number - 1].name}: somebody is "
                    f"at the keyboard ({idle:.0f}s idle, "
                    f"{args.require_idle:.0f}s required). Nothing measured from "
                    f"here would be about the product.",
                    flush=True,
                )
                # An EXIT CODE, not just a break. Stopping quietly is how a
                # truncated batch became a score: on 2026-09-03 the gate cut the
                # hard slice after 1 of 3 documents, the cycle scored 0 of 1 as
                # 0.0, and recorded it as a baseline. A caller cannot tell a
                # slice that failed from a slice that was never measured unless
                # this says so.
                stopped_for_idle = True
                break
        source = essays[number - 1]
        # Word disallows opening two files with the same basename from different
        # folders. A unique disposable name also makes focus checks unambiguous.
        target = working / f"{session_tag}-{source.name}"
        before_text = str(corpus_text[number - 1])

        outcomes: list[str] = []
        for attempt in range(1, max(1, args.repeat) + 1):
            # Release only this disposable output, never another document or Word.
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
                # Name the FRAME, not just the exception.
                #
                # "COMError: (-2146233083, None, (None, None, None, 0, None))"
                # is what this reported for four separate lost measurements
                # across two days, and it says nothing about which call failed.
                # Word, UI Automation and the clipboard all raise COMError, and
                # without a location the only way to tell them apart was to
                # guess and re-run - which cost a wrong diagnosis on 2026-08-28,
                # when the focus helper was blamed and a probe cleared it.
                # And name OUR frame as well as the library's, which is the
                # level of detail this still lacked on 2026-08-30. Naming the
                # deepest frame alone gave "uiautomation.py:7015
                # GetNextSiblingControl" for five lost essays in one gate run -
                # true, and useless, because it is the same answer for every
                # tree walk in the harness. It identified the library and not
                # the caller, so a fix aimed at one call site could not be told
                # apart from no fix at all: replacing the enumeration in
                # focus_named_word_window changed nothing here and the frame
                # stayed identical, which is exactly the ambiguity that a
                # location was supposed to remove.
                frame = traceback.extract_tb(exc.__traceback__)
                where = ""
                if frame:
                    last = frame[-1]
                    where = f" at {Path(last.filename).name}:{last.lineno} {last.name}"
                    ours = [
                        item
                        for item in frame
                        if "uiautomation" not in Path(item.filename).name
                    ]
                    if ours:
                        caller = ours[-1]
                        where += (
                            f" from {Path(caller.filename).name}:"
                            f"{caller.lineno} {caller.name}"
                        )
                result = {
                    "error": f"{type(exc).__name__}: {exc}{where}",
                    "after": "",
                }
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
                # Why the press loop stopped. This was recorded from
                # 2026-08-21 and then dropped on the floor: it never reached
                # the row and was never printed, so every partial run showed
                # "stalled=None err=None" beside a press count well short of
                # the section count, and the reason it had already worked out
                # was thrown away. Days of "the harness stalls again" had an
                # answer written down at the moment it happened.
                "stopped_because": result.get("stopped_because"),
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
            # Retain each attempt before the next one replaces the working copy.
            attempt_dir = args.output_root / "attempts" / f"{number:02d}-{attempt}-{uuid4().hex[:8]}"
            attempt_dir.mkdir(parents=True, exist_ok=False)
            if target.is_file():
                # Canonical output names remain compatible with existing graders.
                shutil.copy2(target, work / source.name)
                saved = attempt_dir / source.name
                shutil.copy2(target, saved)
                row["saved_document"] = str(saved)
                row["saved_sha256"] = hashlib.sha256(saved.read_bytes()).hexdigest()
            else:
                row["error"] = row["error"] or "saved document missing"
            row["completion_acknowledgement"] = result.get("completion_acknowledgement", "")
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
                    f" stopped={row['stopped_because']}"
                    if row["stopped_because"]
                    else ""
                )
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

    (args.output_root / "batch-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Never terminate Word or close unrelated documents on completion.
    print(f"\nsummary: {args.output_root / 'batch-summary.json'}")
    # 2 means come back later, the specification word for a tick that
    # measured nothing. The summary is still written, because the documents
    # that DID run are real, but the caller must not average them into a
    # score for the slice - that is how 1 of 3 documents became a 0.0
    # baseline on 2026-09-03.
    return 2 if stopped_for_idle else 0


if __name__ == "__main__":
    raise SystemExit(main())
