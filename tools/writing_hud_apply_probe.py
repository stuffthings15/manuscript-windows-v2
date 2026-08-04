from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import pyperclip
import uiautomation as automation

from echo_link_app.rewrite_palette import _sentence_spans
from echo_link_core.windows_selection import WindowsSelectionReader
from echo_link_core.insertion import active_window
from selected_text_live_probe import (
    VK_A,
    VK_CONTROL,
    VK_V,
    combo,
)


SOURCE_TEXT = (
    "teh project team were definately prepared to launch the new writing assistant, but "
    "nobody had recieve the final checklist and several important settings was still missing . "
    "during our monday meeting the lead designer explain that the interface look wierd on "
    "smaller screens, the buttons was crowded and the status messages did not makes sense . "
    "we had tested the microphone in two laptops but it stop recording without warning, and "
    "the activation sound occured occured after the user already started talking . "
    "the developers was rushing because because the deadline were close, so they skipped a "
    "careful review of the error logs and they didnt document the changes very well . "
    "when the first build were shared with testers it open slowly, placed periods in random "
    "spots and failed to insert corrected text into the active document . "
    "everyone agree the product needed a clearer plan, better punctuation handling and a "
    "reliable way to show suggestions without covering the paragraph the user were editing .\r\n\r\n"
    "the next morning we recieve a long report from three users who had wrote several pages, "
    "each person said the small icon was useful but the card stayed too large . "
    "one tester try to accept a spelling fix, however nothing happen and the hud kept showing "
    "the first sentnce even after she moved to another paragraph . "
    "another tester notice that grammar errors was underlined inconsistently, common words like "
    "accomodate and seperate remained wrong and duplicated words words was not removed . "
    "the third tester ask for formal tone, the local ai returned a shorter draft but it loose two "
    "details and changed names that should of stayed exactly the same . "
    "these problems made the review process confusing because the user could not tell which two "
    "or three sentences were active or whether the replacement had actually finish . "
    "our team decide to rebuild the interaction so the card would grow when text were longer, "
    "shrink when text were shorter and remain beside the selected writing .\r\n\r\n"
    "after the redesign the engineers create an automatic local workflow that reads the active "
    "selection, separates complete sentences and sends each small group to the installed ai model . "
    "the model now check spelling grammar punctuation and basic formatting, then it returns one "
    "clean suggestion while every original paragraph and line break are kept in place . "
    "reviewers can see red marks beneath changed words, choose accept or dismiss and request a "
    "clear formal casual or concise rewrite without opening the main application window . "
    "if the selection disappear while the card is open Echo Link finds the same unchanged text "
    "again, applies one safe paste and restores the normal windows clipboard immediately . "
    "encrypted diagnostic history keep only the information the user allowed, old speech records "
    "is deleted within fifteen days and no private sentence are sent to a cloud service . "
    "before release we need to run this entire page through the real hud, confirm every obvious "
    "error are corrected and verify the finished document is readable consistent and complete ."
)
MISSPELLINGS = re.compile(
    r"\b(?:teh|definately|recieve|wierd|occured|sentnce|accomodate|seperate|didnt)\b",
    re.IGNORECASE,
)
BAD_GRAMMAR = re.compile(
    r"\b(?:settings was|designer explain|interface look|buttons was|"
    r"did not makes|it stop|developers was|deadline were|build were|it open|everyone agree|"
    r"user were|users who had wrote|tester try|nothing happen|tester notice|errors was|"
    r"tester ask|it loose|should of|had actually finish|team decide|text were|engineers create|"
    r"model now check|selection disappear|history keep|records is|sentence are|errors are|"
    r"microphone in two laptops)\b",
    re.IGNORECASE,
)
REPEATED_WORD = re.compile(r"\b([A-Za-z]+)\s+\1\b", re.IGNORECASE)
BAD_SPACING = re.compile(r"(?:[ \t]+[,.!?;:]|[ \t]{2,})")
BAD_STRUCTURE = re.compile(
    r",\s+(?:however|However),|\b[ap]\.m\.\s+(?:In|At|On)\b(?=[^.!?\r\n]*;)",
)
EXPECTED_AI_CYCLES = 6
MAX_AI_CYCLES = 18
MIN_SOURCE_WORDS = 450
MIN_FINAL_WORDS = 400
REQUIRED_CONTENT = (
    "project",
    "microphone",
    "activation sound",
    "error logs",
    "three users",
    "small icon",
    "two or three sentences",
    "red marks",
    "safe paste",
    "clipboard",
    "fifteen days",
    "cloud service",
    "casual",
)
CLIPBOARD_SENTINEL = "ECHOLINK_HUD_APPLY_CLIPBOARD_SENTINEL"


def _button(window, *names: str):
    for name in names:
        control = window.ButtonControl(searchDepth=12, Name=name)
        if control.Exists(0.5, 0.05):
            return control
    return None


def _accept_button(window):
    for control in _descendants(window):
        try:
            name = str(control.Name).strip().casefold()
            if control.ControlTypeName == "ButtonControl" and (
                name == "replace text"
                or name.startswith("replace text in source")
                or name == "accept"
                or name.startswith("accept suggested text")
                or name.startswith("accept current sentence")
            ):
                return control
        except Exception:
            continue
    return None


def _invoke(control) -> bool:
    try:
        pattern = control.GetInvokePattern()
        if pattern is not None:
            return bool(pattern.Invoke(waitTime=0))
    except Exception:
        pass
    try:
        control.Click()
        return True
    except Exception:
        return False


def _card_progress(window) -> tuple[int, int] | None:
    for control in _descendants(window):
        try:
            values = (
                str(control.Name).strip(),
                str(getattr(control, "HelpText", "")).strip(),
            )
        except Exception:
            continue
        for value in values:
            match = re.search(r"(\d+)\s+of\s+(\d+)", value)
            if match:
                return int(match.group(1)), int(match.group(2))
    return None


def _has_text(window, expected: str) -> bool:
    wanted = expected.casefold()
    for control in _descendants(window):
        try:
            if wanted in str(control.Name).casefold():
                return True
        except Exception:
            continue
    return False


def _reports_ai_ready(window, accept_button, *, rewrite: bool = False) -> bool:
    values = (
        str(getattr(accept_button, "Name", "")),
        str(getattr(accept_button, "HelpText", "")),
    )
    if rewrite:
        phrases = ("local ai rewrite ready", "rewrite ready")
    else:
        phrases = ("local ai proofread ready", "correction ready")
    return any(
        phrase in value.casefold()
        for value in values
        for phrase in phrases
    ) or any(_has_text(window, phrase) for phrase in phrases)


def _named_text(window, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for control in _descendants(window):
        try:
            if str(control.Name).strip().casefold() not in wanted:
                continue
            pattern = control.GetTextPattern()
            document = pattern.DocumentRange if pattern is not None else None
            if document is not None:
                return str(document.GetText(20_001)).strip()
        except Exception:
            continue
    return ""


def _window_with_prefix(prefix: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for control in automation.GetRootControl().GetChildren():
            try:
                if (
                    control.ControlTypeName == "WindowControl"
                    and control.Name.startswith(prefix)
                    and ctypes.windll.user32.IsWindowVisible(control.NativeWindowHandle)
                ):
                    return control
            except Exception:
                continue
        time.sleep(0.05)
    return None


def _descendants(control, depth: int = 0):
    if depth > 12:
        return
    try:
        children = control.GetChildren()
    except Exception:
        return
    for child in children:
        yield child
        yield from _descendants(child, depth + 1)


def _editable_control(handle: int):
    window = automation.ControlFromHandle(handle)
    candidates = [
        control
        for control in _descendants(window)
        if control.ControlTypeName in {"DocumentControl", "EditControl"}
        and control.IsEnabled
    ]
    return candidates[0] if candidates else None


def _document_text(handle: int) -> str:
    editor = _editable_control(handle)
    if editor is None:
        return ""
    try:
        pattern = editor.GetTextPattern()
        document = pattern.DocumentRange if pattern is not None else None
        return str(document.GetText(5_001)).strip() if document is not None else ""
    except Exception:
        return ""


def _select_prefix(editor, text: str) -> bool:
    try:
        pattern = editor.GetTextPattern()
        document = pattern.DocumentRange if pattern is not None else None
        selected = document.Clone() if document is not None else None
        if selected is None:
            return False
        whole = str(document.GetText(5_001))
        selected.MoveEndpointByUnit(
            automation.TextPatternRangeEndpoint.End,
            automation.TextUnit.Character,
            len(text) - len(whole),
            waitTime=0,
        )
        return bool(selected.Select())
    except Exception:
        return False


def _normalized_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _fixture_values(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "name": "page-1-product-review",
            "source_text": SOURCE_TEXT,
            "misspellings": MISSPELLINGS,
            "bad_grammar": BAD_GRAMMAR,
            "required_content": REQUIRED_CONTENT,
            "minimum_source_words": MIN_SOURCE_WORDS,
            "minimum_final_words": MIN_FINAL_WORDS,
            "expected_ai_cycles": EXPECTED_AI_CYCLES,
            "maximum_ai_cycles": MAX_AI_CYCLES,
            "paragraph_count": 3,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = payload.get("paragraphs")
    if isinstance(paragraphs, list):
        source_text = "\r\n\r\n".join(str(value) for value in paragraphs)
    else:
        source_text = str(payload.get("source_text") or "")
    misspellings = tuple(str(value) for value in payload.get("misspellings", ()))
    bad_grammar = tuple(str(value) for value in payload.get("bad_grammar", ()))
    required_content = tuple(str(value).casefold() for value in payload.get("required_content", ()))
    if not source_text.strip() or not misspellings or not bad_grammar or not required_content:
        raise ValueError("Grammar fixture is missing required test data.")

    def phrase_pattern(
        values: tuple[str, ...],
        *,
        ignore_case: bool,
    ) -> re.Pattern[str]:
        return re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(value) for value in values) + r")(?!\w)",
            re.MULTILINE | (re.IGNORECASE if ignore_case else 0),
        )

    return {
        "name": str(payload.get("name") or path.stem),
        "source_text": source_text,
        "misspellings": phrase_pattern(misspellings, ignore_case=True),
        "bad_grammar": phrase_pattern(bad_grammar, ignore_case=False),
        "required_content": required_content,
        "minimum_source_words": int(payload.get("minimum_source_words", 400)),
        "minimum_final_words": int(payload.get("minimum_final_words", 350)),
        "expected_ai_cycles": int(payload.get("expected_ai_cycles", 6)),
        "maximum_ai_cycles": int(payload.get("maximum_ai_cycles", 18)),
        "paragraph_count": int(payload.get("paragraph_count", 3)),
    }


def _run_target(title: str, status_path: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget

    app = QApplication([])
    target = QWidget()
    target.setWindowTitle(title)
    target.setAccessibleName("Manuscript isolated Writing HUD test window")
    layout = QVBoxLayout(target)
    layout.setContentsMargins(0, 0, 0, 0)
    editor = QPlainTextEdit(target)
    editor.setAccessibleName("Manuscript isolated Writing HUD test editor")
    layout.addWidget(editor)
    target.resize(900, 600)
    target.show()
    target.raise_()
    target.activateWindow()
    editor.setFocus()
    app.processEvents()
    status_path.write_text(
        json.dumps({"pid": os.getpid(), "window_handle": int(target.winId())}),
        encoding="utf-8",
    )
    QTimer.singleShot(120, target.activateWindow)
    QTimer.singleShot(140, editor.setFocus)
    return int(app.exec())


def _wait_for_target(status_path: Path, process: subprocess.Popen, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return 0
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            handle = int(payload.get("window_handle") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            handle = 0
        if handle:
            return handle
        time.sleep(0.05)
    return 0


def _dismiss_existing_hud() -> None:
    hud = _window_with_prefix("Manuscript writing HUD", 0.4)
    if hud is None:
        return
    close_button = _button(hud, "Close Writing HUD without changing text", "Close")
    if close_button is None:
        buttons = [
            child
            for child in _descendants(hud)
            if child.ControlTypeName == "ButtonControl" and child.IsEnabled
        ]
        if buttons:
            _invoke(buttons[0])
            time.sleep(0.15)
            close_button = _button(
                hud,
                "Close Writing HUD without changing text",
                "Close",
            )
    if close_button is not None:
        _invoke(close_button)
        time.sleep(0.15)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify sentence advance and guarded Apply in an isolated Windows editor."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Optional JSON file containing one malformed full-page grammar fixture.",
    )
    parser.add_argument(
        "--exercise-tone",
        action="store_true",
        help="Physically run one tone rewrite and verify its visible result before Accept.",
    )
    parser.add_argument(
        "--card-screenshot",
        type=Path,
        help="Optional PNG path for the populated physical HUD card.",
    )
    parser.add_argument(
        "--progress-screenshot-dir",
        type=Path,
        help="Optional folder for one physical HUD screenshot per review counter.",
    )
    parser.add_argument(
        "--partial-selection",
        action="store_true",
        help="Select only the first paragraph and verify the HUD continues through the active text box.",
    )
    parser.add_argument("--target-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--target-title", default="", help=argparse.SUPPRESS)
    parser.add_argument("--target-status", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Windows only")
        return 2
    if args.target_mode:
        if not args.target_title or args.target_status is None:
            return 2
        return _run_target(args.target_title, args.target_status)

    fixture = _fixture_values(args.fixture)
    source_text = str(fixture["source_text"])
    misspellings_pattern = fixture["misspellings"]
    bad_grammar_pattern = fixture["bad_grammar"]
    required_content = tuple(fixture["required_content"])
    minimum_source_words = int(fixture["minimum_source_words"])
    minimum_final_words = int(fixture["minimum_final_words"])
    expected_ai_cycles = int(fixture["expected_ai_cycles"])
    maximum_ai_cycles = int(fixture["maximum_ai_cycles"])
    expected_paragraph_count = int(fixture["paragraph_count"])

    try:
        original_clipboard = str(pyperclip.paste())
    except Exception:
        original_clipboard = None
    _dismiss_existing_hud()
    target_root = tempfile.TemporaryDirectory(
        prefix="echo-link-writing-hud-target-",
        ignore_cleanup_errors=True,
    )
    target_status = Path(target_root.name) / "target.json"
    target_title = f"Manuscript isolated writing target {time.time_ns()}"
    note = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--target-mode",
            "--target-title",
            target_title,
            "--target-status",
            str(target_status),
        ],
        cwd=Path(__file__).resolve().parents[1],
    )
    handle = 0
    checks = {
        "tiny_icon_visible": False,
        "icon_left_of_text": False,
        "compact_sentence_card": False,
        "no_blank_bottom": False,
        "accept_available": False,
        "dismiss_available": False,
        "tone_rewrite_available": False,
        "full_page_input": False,
        "all_ai_proofread_cycles": False,
        "progress_advanced": False,
        "document_updated_each_accept": False,
        "three_paragraphs_preserved": False,
        "spelling_corrected": False,
        "grammar_corrected": False,
        "punctuation_corrected": False,
        "formatting_corrected": False,
        "content_retained": False,
        "document_replaced": False,
        "clipboard_restored": False,
    }
    if args.exercise_tone:
        checks["suggested_text_visible"] = False
        checks["tone_rewrite_visible"] = False
        checks["tone_rewrite_resized"] = False
    details: dict[str, object] = {}
    try:
        handle = _wait_for_target(target_status, note, 6.0)
        if not handle:
            details["error"] = "Notepad window not found."
            return _finish(args.output, checks, details)

        ctypes.windll.user32.SetWindowPos(handle, 0, 480, 180, 900, 600, 0x0004)
        ctypes.windll.user32.SetForegroundWindow(handle)
        editor = _editable_control(handle)
        details["notepad_editor"] = (
            {"type": editor.ControlTypeName, "name": editor.Name}
            if editor is not None
            else None
        )
        if editor is not None:
            editor.Click()
            editor.SetFocus()
        # Clear any automatic badge created from the window that was focused
        # between probe runs, then let the monitor observe this empty editor.
        time.sleep(0.35)
        _dismiss_existing_hud()
        ctypes.windll.user32.SetForegroundWindow(handle)
        if editor is not None:
            editor.Click()
            editor.SetFocus()
        time.sleep(0.6)
        combo(VK_CONTROL, VK_A)
        pyperclip.copy(source_text)
        combo(VK_CONTROL, VK_V)
        pyperclip.copy(CLIPBOARD_SENTINEL)
        selected_source = (
            source_text.split("\r\n\r\n", 1)[0]
            if args.partial_selection
            else source_text
        )
        if args.partial_selection:
            if editor is None or not _select_prefix(editor, selected_source):
                details["error"] = "The first text-box section could not be selected."
                return _finish(args.output, checks, details)
        else:
            combo(VK_CONTROL, VK_A)
        # Verify the real selection before the 300 ms automatic-HUD debounce
        # can move focus on editors that expose selection only while focused.
        time.sleep(0.05)
        checks["full_page_input"] = (
            len(re.findall(r"\b\w+\b", source_text)) >= minimum_source_words
        )
        probe_selection = None
        selection_deadline = time.monotonic() + 3.0
        while time.monotonic() < selection_deadline:
            if editor is not None:
                editor.Click()
                editor.SetFocus()
            ctypes.windll.user32.SetForegroundWindow(handle)
            if args.partial_selection:
                if editor is None or not _select_prefix(editor, selected_source):
                    continue
            else:
                combo(VK_CONTROL, VK_A)
            time.sleep(0.15)
            candidate_selection = WindowsSelectionReader(
                max_characters=5_000,
                include_caret_sentence=True,
            ).read()
            if (
                candidate_selection is not None
                and _normalized_text(candidate_selection.record.text).strip()
                == _normalized_text(selected_source).strip()
            ):
                probe_selection = candidate_selection
                break
        # Give the automatic monitor its 300 ms stability window so the HUD
        # belongs to this full-page selection, never an earlier caret sentence.
        time.sleep(0.55)
        focused = automation.GetFocusedControl()
        foreground = active_window()
        details["selection_before_hud"] = (
            probe_selection.record.text if probe_selection is not None else None
        )
        details["partial_selection"] = bool(args.partial_selection)
        if probe_selection is None:
            details["error"] = "The full-page source selection could not be verified."
            return _finish(args.output, checks, details)
        details["focused_process_id"] = int(focused.ProcessId) if focused is not None else 0
        details["foreground_process_id"] = foreground.process_id
        details["foreground_handle"] = foreground.handle

        hud = _window_with_prefix("Manuscript writing HUD", 8.0)
        checks["tiny_icon_visible"] = hud is not None
        if not checks["tiny_icon_visible"]:
            details["error"] = "Writing HUD did not appear."
            return _finish(args.output, checks, details)
        rectangle = hud.BoundingRectangle
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hud.NativeWindowHandle) or 96)
        scale = max(1.0, dpi / 96.0)
        logical_size = [
            round(rectangle.width() / scale),
            round(rectangle.height() / scale),
        ]
        details["icon_size_physical"] = [int(rectangle.width()), int(rectangle.height())]
        details["icon_size_logical"] = logical_size
        details["hud_dpi"] = dpi
        checks["tiny_icon_visible"] = (
            checks["tiny_icon_visible"]
            and logical_size[0] <= 36
            and logical_size[1] <= 36
        )
        if probe_selection is not None and probe_selection.anchor is not None:
            checks["icon_left_of_text"] = int(rectangle.right) <= probe_selection.anchor[0]
            details["sentence_anchor"] = list(probe_selection.anchor)
            details["icon_rectangle"] = [
                int(rectangle.left),
                int(rectangle.top),
                int(rectangle.right),
                int(rectangle.bottom),
            ]

        badge_buttons = [
            child
            for child in _descendants(hud)
            if child.ControlTypeName == "ButtonControl" and child.IsEnabled
        ]
        if not badge_buttons:
            details["error"] = "Tiny Writing HUD icon was not clickable."
            return _finish(args.output, checks, details)
        _invoke(badge_buttons[0])

        card_sizes: list[list[int]] = []
        loading_sizes: list[list[int]] = []
        card_progress: list[list[int]] = []
        card_bottom_gaps: list[int] = []
        progress_screenshots: list[str] = []
        ai_cycles = 0
        reached_final_review = False
        document_updates: list[int] = []
        last_document_text = source_text
        controls_seen = {"accept": True, "dismiss": True, "tone": True}
        for cycle in range(maximum_ai_cycles):
            deadline = time.monotonic() + 15.0
            accept_button = dismiss_button = rewrite_button = None
            observed_ai_busy = False
            while time.monotonic() < deadline:
                rectangle = hud.BoundingRectangle
                current_size = [
                    round(rectangle.width() / scale),
                    round(rectangle.height() / scale),
                ]
                if current_size[0] <= 292 and current_size[1] <= 108:
                    observed_ai_busy = True
                    if not loading_sizes or loading_sizes[-1] != current_size:
                        loading_sizes.append(current_size)
                accept_button = _accept_button(hud)
                dismiss_button = _button(
                    hud, "Dismiss suggested text", "Dismiss current sentence", "Dismiss"
                )
                rewrite_button = _button(
                    hud,
                    "Rewrite current text locally",
                    "Rewrite the current sentence locally",
                    "Rewrite tone",
                )
                if accept_button is not None and not accept_button.IsEnabled:
                    observed_ai_busy = True
                if accept_button and dismiss_button and rewrite_button:
                    break
                time.sleep(0.05)
            controls_seen["accept"] = controls_seen["accept"] and accept_button is not None
            controls_seen["dismiss"] = controls_seen["dismiss"] and dismiss_button is not None
            controls_seen["tone"] = controls_seen["tone"] and rewrite_button is not None
            if not all(controls_seen.values()):
                details["error"] = f"Writing controls did not appear for review {cycle + 1}."
                return _finish(args.output, checks, details)

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                accept_button = _accept_button(hud)
                if accept_button is not None and accept_button.IsEnabled:
                    break
                time.sleep(0.1)
            if accept_button is None or not accept_button.IsEnabled:
                details["error"] = f"Local AI did not finish review {cycle + 1}."
                return _finish(args.output, checks, details)
            ready_deadline = time.monotonic() + 1.0
            ai_ready = False
            while time.monotonic() < ready_deadline:
                ai_ready = _reports_ai_ready(hud, accept_button)
                if ai_ready:
                    break
                time.sleep(0.05)
            if not ai_ready:
                details["review_ready_diagnostic"] = {
                    "accept_name": str(getattr(accept_button, "Name", "")),
                    "accept_help_text": str(getattr(accept_button, "HelpText", "")),
                    "visible_text": [
                        str(getattr(item, "Name", ""))
                        for item in _descendants(hud)
                        if str(getattr(item, "Name", "")).strip()
                    ],
                }
                details["error"] = (
                    f"Local AI did not produce an accepted proofread for review {cycle + 1}."
                )
                return _finish(args.output, checks, details)
            ai_cycles += 1
            time.sleep(0.2)

            deadline = time.monotonic() + 3.0
            card_size = [0, 0]
            while time.monotonic() < deadline:
                rectangle = hud.BoundingRectangle
                card_size = [
                    round(rectangle.width() / scale),
                    round(rectangle.height() / scale),
                ]
                if card_size[0] <= 460 and card_size[1] <= 420:
                    break
                time.sleep(0.05)
            card_sizes.append(card_size)
            dismiss_rectangle = dismiss_button.BoundingRectangle
            card_bottom_gaps.append(
                max(0, round((rectangle.bottom - dismiss_rectangle.bottom) / scale))
            )
            if args.card_screenshot is not None and cycle == 0 and not args.exercise_tone:
                from PIL import ImageGrab

                args.card_screenshot.parent.mkdir(parents=True, exist_ok=True)
                ImageGrab.grab(
                    bbox=(
                        int(rectangle.left),
                        int(rectangle.top),
                        int(rectangle.right),
                        int(rectangle.bottom),
                    )
                ).save(args.card_screenshot)
                details["card_screenshot"] = str(args.card_screenshot)
            suggested_text = _named_text(
                hud,
                "Suggestion replacement text",
                "Suggested correction with red underlines",
            )
            if args.exercise_tone and cycle == 0:
                checks["suggested_text_visible"] = bool(suggested_text)
                details["suggested_text_before_tone"] = suggested_text
                before_tone_size = card_size
                if not suggested_text:
                    details["error"] = "Suggested text remained blank."
                    return _finish(args.output, checks, details)
                if not _invoke(rewrite_button):
                    details["error"] = "Tone rewrite could not be invoked."
                    return _finish(args.output, checks, details)
                tone_deadline = time.monotonic() + 15.0
                tone_ready = False
                while time.monotonic() < tone_deadline:
                    accept_button = _accept_button(hud)
                    if (
                        accept_button is not None
                        and accept_button.IsEnabled
                        and (
                            _reports_ai_ready(hud, accept_button, rewrite=True)
                        )
                    ):
                        tone_ready = True
                        break
                    time.sleep(0.1)
                tone_text = _named_text(
                    hud,
                    "Suggestion replacement text",
                    "Suggested correction with red underlines",
                )
                tone_rectangle = hud.BoundingRectangle
                tone_size = [
                    round(tone_rectangle.width() / scale),
                    round(tone_rectangle.height() / scale),
                ]
                details["suggested_text_after_tone"] = tone_text
                details["tone_card_size_before"] = before_tone_size
                details["tone_card_size_after"] = tone_size
                checks["tone_rewrite_visible"] = tone_ready and bool(tone_text)
                checks["tone_rewrite_resized"] = (
                    480 <= tone_size[0] <= 760
                    and 320 <= tone_size[1] <= 860
                )
                if not checks["tone_rewrite_visible"]:
                    details["error"] = "Tone rewrite remained blank or locked Accept."
                    return _finish(args.output, checks, details)
                if args.card_screenshot is not None:
                    from PIL import ImageGrab

                    args.card_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    ImageGrab.grab(
                        bbox=(
                            int(tone_rectangle.left),
                            int(tone_rectangle.top),
                            int(tone_rectangle.right),
                            int(tone_rectangle.bottom),
                        )
                    ).save(args.card_screenshot)
                    details["card_screenshot"] = str(args.card_screenshot)
            progress = _card_progress(hud)
            if progress is not None:
                card_progress.append([progress[0], progress[1]])
                if args.progress_screenshot_dir is not None:
                    from PIL import ImageGrab

                    rectangle = hud.BoundingRectangle
                    args.progress_screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot = args.progress_screenshot_dir / (
                        f"review-{progress[0]:02d}-of-{progress[1]:02d}.png"
                    )
                    ImageGrab.grab(
                        bbox=(
                            int(rectangle.left),
                            int(rectangle.top),
                            int(rectangle.right),
                            int(rectangle.bottom),
                        )
                    ).save(screenshot)
                    progress_screenshots.append(str(screenshot))
            accept_button = _accept_button(hud)
            if accept_button is None or not accept_button.IsEnabled:
                details["error"] = f"Accept was unavailable for review {cycle + 1}."
                return _finish(args.output, checks, details)
            if not _invoke(accept_button):
                details["error"] = f"Accept could not be invoked for review {cycle + 1}."
                return _finish(args.output, checks, details)
            document_changed = False
            transition_deadline = time.monotonic() + 3.0
            while time.monotonic() < transition_deadline:
                current = _document_text(handle)
                if (
                    _normalized_text(current).strip()
                    != _normalized_text(last_document_text).strip()
                ):
                    document_changed = True
                    last_document_text = current
                    document_updates.append(cycle + 1)
                    break
                time.sleep(0.05)
            if not document_changed:
                details["error"] = f"Accept did not update the document for review {cycle + 1}."
                return _finish(args.output, checks, details)
            if progress is None and expected_ai_cycles == 1:
                reached_final_review = True
                break
            if progress is not None and progress[0] == progress[1]:
                reached_final_review = True
                break
            time.sleep(0.15)

        details["card_sizes_logical"] = card_sizes
        details["loading_sizes_logical"] = loading_sizes
        details["card_progress"] = card_progress
        details["card_bottom_gaps_logical"] = card_bottom_gaps
        details["progress_screenshots"] = progress_screenshots
        details["document_updates_after_accept"] = document_updates
        details["ai_proofread_cycles"] = ai_cycles
        checks["progress_advanced"] = (
            ai_cycles == 1 and expected_ai_cycles == 1 and not card_progress
        ) or (
            bool(card_progress)
            and all(
                position == index + 1 and total == card_progress[0][1]
                for index, (position, total) in enumerate(card_progress)
            )
        )
        checks["document_updated_each_accept"] = (
            bool(document_updates)
            and document_updates == list(range(1, ai_cycles + 1))
        )
        checks["compact_sentence_card"] = all(
            480 <= size[0] <= 760 and 320 <= size[1] <= 860
            for size in card_sizes
        )
        checks["no_blank_bottom"] = bool(card_bottom_gaps) and all(
            # The visible bottom status row occupies about 30 logical pixels.
            gap <= 32 for gap in card_bottom_gaps
        )
        checks["accept_available"] = controls_seen["accept"]
        checks["dismiss_available"] = controls_seen["dismiss"]
        checks["tone_rewrite_available"] = controls_seen["tone"]
        checks["all_ai_proofread_cycles"] = (
            ai_cycles >= expected_ai_cycles and reached_final_review
        )

        deadline = time.monotonic() + 12.0
        actual = ""
        previous = ""
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            actual = _document_text(handle)
            if actual != previous:
                previous = actual
                stable_since = time.monotonic()
            normalized = _normalized_text(actual)
            paragraph_count = len(
                [value for value in re.split(r"\n{2,}", normalized) if value.strip()]
            )
            if (
                len(re.findall(r"\b\w+\b", actual)) >= minimum_final_words
                and paragraph_count == expected_paragraph_count
                and misspellings_pattern.search(actual) is None
                and time.monotonic() - stable_since >= 0.75
            ):
                break
            time.sleep(0.05)
        details["actual_text"] = actual
        paragraphs = [
            value.strip()
            for value in re.split(r"\n{2,}", _normalized_text(actual).strip())
            if value.strip()
        ]
        checks["three_paragraphs_preserved"] = (
            len(paragraphs) == expected_paragraph_count
        )
        checks["spelling_corrected"] = (
            bool(actual) and misspellings_pattern.search(actual) is None
        )
        checks["grammar_corrected"] = (
            bool(actual)
            and bad_grammar_pattern.search(actual) is None
            and REPEATED_WORD.search(actual) is None
            and BAD_STRUCTURE.search(actual) is None
        )
        sentence_parts = [
            normalized[start:end].strip()
            for start, end in _sentence_spans(normalized)
            if normalized[start:end].strip()
        ]
        checks["punctuation_corrected"] = bool(sentence_parts) and all(
            sentence[0].isupper() and sentence.endswith((".", "!", "?"))
            for sentence in sentence_parts
        )
        checks["formatting_corrected"] = bool(actual) and BAD_SPACING.search(actual) is None
        folded_actual = actual.casefold()
        checks["content_retained"] = (
            len(re.findall(r"\b\w+\b", actual)) >= minimum_final_words
            and all(term in folded_actual for term in required_content)
        )
        checks["document_replaced"] = (
            bool(actual)
            and _normalized_text(actual).strip() != _normalized_text(source_text).strip()
        )

        details["fixture"] = str(fixture["name"])

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and pyperclip.paste() != CLIPBOARD_SENTINEL:
            time.sleep(0.05)
        checks["clipboard_restored"] = pyperclip.paste() == CLIPBOARD_SENTINEL
        return _finish(args.output, checks, details)
    finally:
        _dismiss_existing_hud()
        if handle:
            ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        note.terminate()
        try:
            note.wait(timeout=3)
        except subprocess.TimeoutExpired:
            note.kill()
        target_root.cleanup()
        if original_clipboard is not None:
            try:
                pyperclip.copy(original_clipboard)
            except Exception:
                pass


def _finish(output: Path | None, checks: dict[str, bool], details: dict[str, object]) -> int:
    passed = all(checks.values())
    payload = {
        "schema_version": 1,
        "probe": "writing_hud_full_page_ai_cycle",
        "passed": passed,
        "checks": checks,
        "details": details,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "limitations": [
            "This probe covers a full page across three selected paragraphs in a dedicated isolated Qt editor.",
            "Word, browser rich editors, and elevated windows remain manual acceptance targets.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if passed else 5


if __name__ == "__main__":
    raise SystemExit(main())
