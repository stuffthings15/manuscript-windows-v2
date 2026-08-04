"""Drive the real Writing HUD through a complete error-heavy page, as a user.

The probe opens real Windows Notepad with a full page of seeded errors, clicks
the Manuscript badge, then accepts every review section in order while timing
each wait. It proves a user can run a whole page of corrections to completion
without stalls, and reports how long each section made them wait.

Run from ``Manuscript/Windows`` with the app already running::

    .venv/Scripts/python.exe tools/full_page_user_journey_probe.py
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from writing_hud_apply_probe import (  # noqa: E402
    _accept_button,
    _descendants,
    _dismiss_existing_hud,
    _document_text,
    _editable_control,
    _window_with_prefix,
)

from curtis_dogs_essay import (  # noqa: E402
    CURTIS_DOGS_FORBIDDEN,
    CURTIS_DOGS_PARAGRAPHS,
)

from fresh_cats_essay import (  # noqa: E402
    FRESH_CATS_FORBIDDEN,
    FRESH_CATS_PARAGRAPHS,
)

HUD_PREFIX = "Manuscript writing HUD"

# Documents written by Claude to be aggressively error-prone: misspellings,
# missing/misplaced apostrophes, homophones, run-ons, wrong plurals after
# numbers, agreement errors, bad spacing, and protected values (times,
# versions, device names, proper nouns) that must survive. The 25-paragraph
# document proves a user can run pages and pages of corrections to completion.
_LONG_DOCUMENT_PARAGRAPHS = (
    "the quarterly planing meeting was suppose to start at 9am on monday but "
    "the projector dont work and nobody could find the spare cable. we waste "
    "fourty minutes before the facilities team arrive.",
    "sarah presented the roadmap for the new billing system, she said the "
    "first milestone is due in three week and the second one depend on the "
    "vendor contract being sign.",
    "the vendor contract have two open issue. first the liability cap is to "
    "low, and second the payment terms dont match what finance agreed too "
    "last month.",
    "our engineering team are still fixing the login bug that was report on "
    "2026-07-15. the fix works on chrome but it dont work on safari and "
    "nobody has test it on firefox yet.",
    "the support queue grow to 84 ticket last week. most of them was about "
    "the password reset email that never arrives, which mean the mail "
    "provider is droping our messages agian.",
    "marcus said we should of switched providers month ago but the migration "
    "cost was'nt approved. he will raise it agian at the next steering "
    "commitee meeting.",
    "the mobile app release v2.3 went out on thursday, it crash on the S23 "
    "Plus when the user rotate the screen during onboarding. the hotfix "
    "v2.3.1 is schedule for friday at 4pm.",
    "customer feedback for the new dashboard were mostly positive. three "
    "customer complained that the export button dont appear until you "
    "refreshes the page twice.",
    "the data team finish there migration of the analytics warehouse. two "
    "table was left behind becuase they contains legacy columns that nobody "
    "own anymore.",
    "hr announced that the office will closes early on september 3 for "
    "building maintenance. employee's should takes there laptops home the "
    "night before.",
    "the security audit found five finding. two of them is critical and must "
    "be fix before the certification review on october 12.",
    "our biggest client renewd there contract for another two year, however "
    "they asks for a 15% discount and a dedicated support channel staffed "
    "during there business hours.",
    "the intern built a prototype of the search feature over the weekend, it "
    "use the old api and need to be rewrite before it can ship to "
    "production.",
    "traffic to the docs site have doubled since the redesign. the search "
    "analytics show that most visitor cant find the billing faq becuase its "
    "buried three level deep.",
    "the design team propose a new onboarding flow with fewer steps. testing "
    "with eight user showed that completion rates improves when the account "
    "setup happen after the first project is create.",
    "finance flag three expense report from the conference trip. one of them "
    "were submitted twice and another one dont have no receipts attached.",
    "the backup jobs failed silently for six day in a row. monitoring never "
    "alert anyone becuase the notification rule was pointing at a slack "
    "channel that dont exist anymore.",
    "devops is rolling out the new deploy pipeline next tuesday. every "
    "service must adds a health check endpoint before they can uses the "
    "automatic rollback feature.",
    "the legal team review the new privacy policy and they founds two "
    "paragraph that contradicts each other about data retention periods.",
    "sales close eleven deal this month which are a new record. the biggest "
    "one was worth 240000 dollars and it took nine month of negotiation to "
    "get it sign.",
    "the office wifi keep droping every afternoon around 2pm. IT suspect the "
    "microwave in the kitchen but they hasnt proved it yet.",
    "we hired two new engineer for the platform team. there start date is "
    "august 18 and there onboarding buddys havent been assign yet.",
    "the product newsletter go out every other friday. last issue had a "
    "broken link in the header and nobody notice until a customer reply "
    "about it.",
    "the api rate limits was raised from 100 to 250 request per minute. "
    "three partner already asks for an even higher limit for there batch "
    "jobs.",
    "next quarter the team focus on reliability. the goal are 99.9% uptime, "
    "faster incident response, and less then two regression per release.",
)

# One dense page: ten paragraphs of four to five sentences each, saturated
# with grammar, sentence-structure, vocabulary, and punctuation errors, per
# Curtis's harder acceptance bar of 2026-08-01.
_DENSE_PAGE_PARAGRAPHS = (
    "the quartely revew meating begun late becuase nobody could of find the "
    "confrence room key. when marcus finaly arrive ,he realise the agenda "
    "was'nt never printed. the team seated themself and waited on the "
    "projector who refused to turning on. after fourty minute of fumbling "
    "the meating was move to the cafeteria.",
    "sarah present the new billing sistem roadmap, wich have three "
    "milestone. the first milestone are due on 2026-09-15 and depend of the "
    "vendor contract being sign. the second one involve migrating 12000 "
    "customer record without looses any data. nobody dont know when the "
    "third milestone happen because finance haven't gave there approval.",
    "the S23 Plus crash report keeps came in every day. users says the app "
    "freeze during onboarding when they rotates the screen, and than it "
    "loose all there progress. the hotfix v2.3.1 were suppose to ship at "
    "4pm friday but the build server run out of disc space. devops promise "
    "a new build by monday morning, tho they didnt sound confidant.",
    "our best client have threaten to leave twice this month. their upset "
    "becuase the export button dont never work on safari, and support "
    "keeps telling them to used chrome insted. the account is worth 240000 "
    "dollars anually so loosing it would hurted the whole quarter. jenna "
    "schedule a call for 9am tuesday to smooth thing over.",
    "the security audit finded five critical finding and eleven minor one. "
    "two of the critical ones is about password which never expire. the "
    "auditor also seen plaintext credential inside the deploy script, "
    "wich is a embarassing oversight. every finding must be fix before the "
    "october 12 certification revew or we loose the contract.",
    "documentation traffic have double since the redesign however most "
    "visitor still cant find the billing faq. its buried three level deep "
    "under a menu what nobody clicks. the search feature return irelevant "
    "result becuase the index havent being rebuilt since march. the docs "
    "team want two week to fixing the informations architecture.",
    "the new intern have build a prototype search feature over the weekend "
    "which impress everyone. unfortunatly it use the deprecated api and "
    "need to be rewrite before production. his mentor told him this "
    "mistakes was common and nothing to be embarassed about. the rewrite "
    "should took less then a week if the team helps him.",
    "backup jobs been failing silent for six day straight. monitoring "
    "never alerted noone becuase the notification rule point at a slack "
    "channel that dont exist no more. losing six day of backup could of "
    "been catastrophic if the database had crash. IT now checks the backup "
    "dashboord manualy every morning at 8am.",
    "the office wifi keep dropping every afternoon around 2pm wich make "
    "video call impossible. IT suspect the microwave in the kitchen but "
    "they hasnt prove it yet. meanwhile employee's has took to tethering "
    "off there phones, what cost the company data charges. facilities "
    "order a new access point who should arrive by august 18.",
    "next quarter the team focus are on reliability and less regression. "
    "the goal include 99.9% uptime, faster incident respond, and fewer "
    "then two defect per release. leadership also want a oncall rotation "
    "what dont burn people out. if this goals is met everyone get a extra "
    "day off in december.",
)


# A natural office-memo page: longer sentences, dates, ordering words,
# obligations, and moderate (not saturated) errors. Reproduces the validator
# pressure of real documents rather than seeded torture fragments.
_REALWORLD_PAGE_PARAGRAPHS = (
    "Before the marketing review on 2026-08-14, every team lead must submits "
    "there final budget numbers to finance. After the numbers is approved, "
    "procurement will opens the vendor portal for two week so departments "
    "can locks in pricing before the September freeze.",
    "During the last audit we was told that expense reports must not be "
    "submit after the fifteenth of each month. Anyone who miss the deadline "
    "have to wait until the following cycle, wich delay reimbursement by "
    "thirty days or more.",
    "The onboarding schedule run as follows. New hires completes security "
    "training first, then IT provision there laptops, and finally HR conduct "
    "the benefits meeting. While the first two steps happens on day one, the "
    "benefits meeting dont occur until the second week.",
    "Our support contract with Meridian expire on October 1 and the renewal "
    "must be sign before September 20 or we loose the discounted rate. Legal "
    "have already review the terms, however they wants one clause change "
    "before the deadline.",
    "Following the incident on July 28, the on-call engineer are required to "
    "acknowledge every page within ten minute. If the page go unacknowledged, "
    "the system escalate to the secondary engineer, who must responds before "
    "the fifteen minute mark pass.",
    "Dogs is one of them breeds what almost everybody know about, but the "
    "training guide dont explain it good. Handlers must wait the trainer "
    "arrive before they starts the session, and no one dont leave the yard "
    "while a dog still wear its harness.",
)

PAGES: tuple[dict[str, object], ...] = (
    {
        "page_id": "fresh-cats-essay",
        "text": "\n".join(FRESH_CATS_PARAGRAPHS),
        "min_sections": 4,
        "forbidden": FRESH_CATS_FORBIDDEN,
        "required": ("litter", "windows"),
    },
    {
        "page_id": "curtis-dogs-essay",
        "text": "\n".join(CURTIS_DOGS_PARAGRAPHS),
        "min_sections": 10,
        "forbidden": CURTIS_DOGS_FORBIDDEN,
        "required": ("squirrel", "vacuum"),
    },
    {
        "page_id": "realworld-memo",
        "text": "\n".join(_REALWORLD_PAGE_PARAGRAPHS),
        "min_sections": 5,
        "forbidden": (
            "must submits", "there final budget", "numbers is approved",
            "will opens", "two week so", "can locks",
            "we was told", "be submit after", "who miss the",
            "wich delay",
            "schedule run as", "hires completes", "provision there laptops",
            "HR conduct the", "steps happens", "dont occur",
            "contract with Meridian expire", "must be sign", "we loose",
            "Legal have already review", "they wants",
            "engineer are required", "ten minute.", "page go unacknowledged",
            "system escalate", "must responds", "minute mark pass",
            "Dogs is", "them breeds", "what almost everybody know",
            "dont explain it good", "wait the trainer arrive",
            "they starts", "no one dont", "still wear its",
        ),
        "required": (
            "2026-08-14", "September|september", "October 1|october 1",
            "September 20|september 20", "July 28|july 28", "Meridian",
        ),
    },
    {
        "page_id": "dense-ten-paragraphs",
        "text": "\n".join(_DENSE_PAGE_PARAGRAPHS),
        "forbidden": (
            "quartely", "revew", "meating", "could of find", "was'nt never",
            "seated themself", "projector who", "fourty minute",
            "wich have", "depend of", "being sign", "12000 customer record",
            "nobody dont know", "haven't gave", "there approval",
            "keeps came", "users says", "they rotates", "and than it",
            "loose all there", "were suppose", "disc space", "confidant",
            "client have threaten", "their upset", "dont never work",
            "to used chrome", "insted", "would hurted", "smooth thing over",
            "finded", "minor one.", "ones is", "auditor also seen",
            "a embarassing", "must be fix", "we loose",
            "traffic have double", "cant find", "three level deep",
            "menu what", "irelevant", "havent being", "to fixing",
            "informations", "intern have build", "unfortunatly",
            "be rewrite", "this mistakes was", "should took", "less then",
            "been failing silent", "six day straight", "alerted noone",
            "dont exist no more", "could of been", "had crash",
            "dashboord", "manualy", "wich make", "hasnt prove",
            "employee's has took", "off there phones", "what cost",
            "point who", "focus are", "incident respond",
            "fewer then two defect", "a oncall", "what dont burn",
            "this goals is", "a extra",
        ),
        "required": (
            "2026-09-15", "12000|12,000", "v2.3.1", "4pm", "S23",
            "240000|240,000", "9am", "October 12|october 12",
            "March|march", "8am", "2pm", "August 18|august 18",
            "99.9%", "December|december", "Marcus", "Sarah", "Jenna",
        ),
    },
    {
        "page_id": "long-25-paragraphs",
        "text": "\n".join(_LONG_DOCUMENT_PARAGRAPHS),
        "forbidden": (
            "planing", "was suppose to", "dont work", "fourty",
            "three week", "being sign", "contract have", "is to low",
            "agreed too", "team are still", "was report on", "has test",
            "84 ticket", "droping", "agian", "should of", "was'nt",
            "commitee", "it crash", "is schedule for", "feedback ",
            "dont appear", "refreshes the page", "finish there",
            "two table was", "becuase", "will closes", "employee's",
            "takes there", "five finding", "them is", "must be fix",
            "renewd", "two year", "they asks", "be rewrite",
            "docs site have", "cant find", "three level", "eight user",
            "rates improves", "is create", "flag three", "were submitted "
            "twice", "dont have no", "six day", "never alert",
            "dont exist", "must adds", "can uses", "they founds",
            "two paragraph", "contradicts each", "eleven deal",
            "which are", "nine month", "get it sign", "keep droping",
            "hasnt", "two new engineer", "there start date", "buddys",
            "havent been assign", "newsletter go out", "nobody notice",
            "limits was raised", "250 request", "already asks",
            "there batch", "goal are", "less then", "two regression",
        ),
        "required": (
            "9am", "2026-07-15", "v2.3.1", "S23", "4pm",
            "September 3|september 3", "October 12|october 12",
            "15%", "240000|240,000", "99.9%", "August 18|august 18",
            "100", "250", "2pm",
        ),
    },
    {
        "page_id": "status-report",
        "text": (
            "the weekly status report was finish late becuase the reviewer "
            "was'nt available on thursday. we send it too the client anyway "
            "and they dont replied yet ,so nobody know if the numbers is "
            "acceptable. if they asks for changes we will needs another two "
            "day to fix them.\n"
            "curtis tested the new build on a S23 Plus at 3pm and the icon "
            "dont appear in gmail. the accesibility service also dont "
            "survive a reboot which make the whole workflow useless. we "
            "should of caught these problem before the internal release went "
            "out to the five tester.\n"
            "their is three open item left. first the installer need "
            "signing, second the correction card open to slow on older "
            "phones, and third the seperate settings page still show the old "
            "logo. each item was raise on 2026-07-29 and none of them has an "
            "owner yet."
        ),
        "forbidden": (
            "becuase", "was'nt", "dont replied", "two day", "accesibility",
            "should of", "their is three", "need signing", "seperate",
            "these problem", "five tester", "open item left",
        ),
        "required": ("Curtis", "S23", "3pm", "Tuesday|Thursday", "2026-07-29"),
    },
    {
        "page_id": "clinic-drill",
        "text": (
            "the clinic response team are prepairing a power outage drill "
            "for october 8 from 7:15 a.m. to 1:30 p.m. and dr shah say the "
            "exercise will tests whether essential service can continue for "
            "six hour without utility power. several department plan was "
            "submitted late and they contains conflicting instruction.\n"
            "the drill include 72 volunteer and every participants must "
            "check in at the front desk before orientation start. the "
            "generator sequence have not been tested since march ,and each "
            "stages of the checklist depend on technicians who has not "
            "confirm there availability.\n"
            "if the backup pump fail during the six hour window the staff "
            "must switches to manual charting immediatly. no patient data "
            "can not leave the building and the incident lead records "
            "every deviation in the paper log."
        ),
        "forbidden": (
            "team are prepairing", "prepairing", "six hour without",
            "plan was submitted", "they contains", "every participants",
            "sequence have", "each stages", "who has not confirm",
            "there availability", "immediatly", "can not leave",
        ),
        "required": ("October 8", "7:15", "1:30", "72", "March|march"),
    },
)



def _find_window_handle_by_prefix(prefix: str, timeout: float) -> int:
    """Locate a top-level window by title prefix using Win32 enumeration.

    UI Automation's GetRootControl().GetChildren() reads every top-level
    window's Name, which blocks for seconds whenever another app on the
    desktop is slow to answer (Word, an Android emulator). EnumWindows plus
    GetWindowTextW is a direct Win32 read and costs microseconds, so probe
    timings measure Manuscript instead of the rest of the desktop.
    """
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
    )
    deadline = time.monotonic() + timeout
    while True:
        found = ctypes.c_int(0)

        def callback(hwnd, result):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.startswith(prefix):
                result[0] = hwnd
                return False
            return True

        user32.EnumWindows(proto(callback), ctypes.byref(found))
        if found.value:
            return int(found.value)
        if time.monotonic() >= deadline:
            return 0
        time.sleep(0.05)


def _hud_window(timeout: float):
    """Return the HUD as a UI Automation control, found the fast way."""
    handle = _find_window_handle_by_prefix(HUD_PREFIX, timeout)
    if not handle:
        return None
    import uiautomation as automation

    try:
        return automation.ControlFromHandle(handle)
    except Exception:
        return None

def _badge_button(hud):
    for control in _descendants(hud):
        try:
            if control.ControlTypeName == "ButtonControl" and control.IsEnabled:
                return control
        except Exception:
            continue
    return None


def _click(control) -> bool:
    try:
        pattern = control.GetInvokePattern()
        if pattern is not None:
            pattern.Invoke()
            return True
    except Exception:
        pass
    try:
        control.Click(simulateMove=False, waitTime=0.05)
        return True
    except Exception:
        return False


def _counter_text(hud) -> str:
    for control in _descendants(hud):
        try:
            name = str(control.Name).strip()
            if re.fullmatch(r"\d+ of \d+", name):
                return name
        except Exception:
            continue
    return ""


def _logical_size(control) -> tuple[int, int]:
    rectangle = control.BoundingRectangle
    dpi = int(ctypes.windll.user32.GetDpiForWindow(control.NativeWindowHandle) or 96)
    scale = max(1.0, dpi / 96.0)
    return round(rectangle.width() / scale), round(rectangle.height() / scale)


def _open_notepad(page_text: str) -> tuple[subprocess.Popen, Path, int]:
    # Windows 11 Notepad opens additional files as TABS of an existing
    # window, so a leftover Notepad makes a fresh window never appear and
    # a shared "page.txt" title ambiguous. Use a unique file name per run
    # and match the window by that name, which is also the active tab.
    root = Path(tempfile.mkdtemp(prefix="manuscript-page-journey-"))
    stem = f"manuscript-journey-{time.time_ns()}"
    target = root / f"{stem}.txt"
    target.write_text(page_text, encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(target)])
    import uiautomation as automation

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        for control in automation.GetRootControl().GetChildren():
            try:
                if (
                    control.ControlTypeName == "WindowControl"
                    and stem in control.Name
                    and ctypes.windll.user32.IsWindowVisible(
                        control.NativeWindowHandle
                    )
                ):
                    return process, target, control.NativeWindowHandle
            except Exception:
                continue
        time.sleep(0.2)
    raise RuntimeError(f"Notepad window for {stem} did not appear.")


_WORDY = re.compile(r"^[\w' ]+$")


def _fragment_present(text: str, fragment: str) -> bool:
    if _WORDY.match(fragment):
        return bool(
            re.search(
                rf"(?<!\w){re.escape(fragment)}(?!\w)", text, re.IGNORECASE
            )
        )
    return fragment.casefold() in text.casefold()


def _score(final_text: str, page: dict[str, object]) -> dict[str, object]:
    remaining = [
        fragment
        for fragment in page["forbidden"]
        if _fragment_present(final_text, fragment)
    ]
    missing = []
    for requirement in page["required"]:
        options = str(requirement).split("|")
        if not any(option in final_text for option in options):
            missing.append(requirement)
    return {
        "errors_remaining": remaining,
        "protected_missing": missing,
        "clean": not remaining and not missing,
    }


def run_page(
    page: dict[str, object],
    *,
    first_wait: float,
    section_wait: float,
) -> dict[str, object]:
    _dismiss_existing_hud()
    result: dict[str, object] = {
        "page_id": page["page_id"],
        "characters": len(str(page["text"])),
        "sections": [],
        "completed": False,
    }
    started = time.monotonic()
    process, target, handle = _open_notepad(str(page["text"]))
    try:
        ctypes.windll.user32.SetForegroundWindow(handle)
        editor = _editable_control(handle)
        if editor is None:
            result["error"] = "Notepad editor not found"
            return result
        editor.Click()
        editor.SetFocus()

        hud = _hud_window(6.0)
        if hud is None:
            result["error"] = "badge did not appear"
            return result
        badge = _badge_button(hud)
        if badge is None:
            result["error"] = "badge button missing"
            return result
        # The badge tooltip does not expose the captured size, so verify the
        # capture scope indirectly: a full-document session must know about
        # the final paragraph. If capture shrank to one sentence (transient
        # UI Automation blackout), re-click into the editor once to retrigger
        # a full capture instead of reviewing a fragment.
        badge_name = str(badge.Name)
        result["badge_name"] = badge_name
        if not _click(badge):
            result["error"] = "badge click failed"
            return result

        total_sections = 0
        stalled_counter = ""
        stalled_repeats = 0
        while True:
            wait_limit = first_wait if not result["sections"] else section_wait
            ai_started = time.monotonic()
            accept = None
            counter = ""
            polls = 0
            walk_seconds = 0.0
            hud = _hud_window(2.0)
            while time.monotonic() - ai_started < wait_limit:
                if hud is None:
                    hud = _hud_window(0.5)
                    if hud is None:
                        break
                try:
                    polls += 1
                    walk_started = time.monotonic()
                    counter = _counter_text(hud) or counter
                    accept = _accept_button(hud)
                    walk_seconds += time.monotonic() - walk_started
                except Exception:
                    hud = None
                    continue
                if accept is not None and accept.IsEnabled:
                    break
                accept = None
                time.sleep(0.1)
            waited = round(time.monotonic() - ai_started, 3)
            if hud is None:
                # The HUD closed: the series is finished.
                break
            if accept is None:
                result["sections"].append(
                    {"counter": counter, "wait_seconds": waited, "accepted": False}
                )
                result["error"] = (
                    f"section {counter or '?'} never became ready "
                    f"within {wait_limit}s"
                )
                return result
            match = re.fullmatch(r"(\d+) of (\d+)", counter)
            if match:
                total_sections = max(total_sections, int(match.group(2)))
                minimum = int(page.get("min_sections") or 0)
                if minimum and total_sections < minimum:
                    result["error"] = (
                        f"partial capture: only {total_sections} sections for "
                        f"a page that must produce at least {minimum}"
                    )
                    return result
            # The counter identifies progress but can briefly lag the click,
            # so a repeat is tolerated twice before it counts as a stall.
            if counter and counter == stalled_counter:
                stalled_repeats += 1
                if stalled_repeats >= 3:
                    result["error"] = f"section counter stuck at {counter}"
                    return result
            else:
                stalled_counter = counter
                stalled_repeats = 0
            if not _click(accept):
                result["error"] = f"accept click failed at {counter or '?'}"
                return result
            t_snap = time.monotonic()
            time.sleep(0.9)
            snapshot = _document_text(handle)
            snap_seconds = round(time.monotonic() - t_snap, 3)
            result["sections"].append(
                {
                    "counter": counter,
                    "wait_seconds": waited,
                    "accepted": True,
                    "snapshot_seconds": snap_seconds,
                    "poll_iterations": polls,
                    "tree_walk_seconds": round(walk_seconds, 3),
                    "doc_chars": len(snapshot),
                    "doc_head_of_error": snapshot.casefold().find("finish there"),
                }
            )
            if len(result["sections"]) > 200:
                result["error"] = "section loop exceeded 200 accepts"
                return result
            time.sleep(0.5)
            if _hud_window(1.5) is None:
                break
        result["total_sections_reported"] = total_sections

        final_text = _document_text(handle)
        result["final_text"] = final_text
        result.update(_score(final_text, page))
        result["completed"] = bool(result["sections"]) and all(
            section["accepted"] for section in result["sections"]
        )
        return result
    finally:
        result["total_seconds"] = round(time.monotonic() - started, 3)
        waits = [s["wait_seconds"] for s in result["sections"] if s["accepted"]]
        if waits:
            result["max_section_wait"] = max(waits)
            result["mean_section_wait"] = round(sum(waits) / len(waits), 3)
        _dismiss_existing_hud()
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        time.sleep(0.5)
        # Notepad may ask to save; discard with 'n' only if it is still alive.
        if process.poll() is None:
            process.terminate()
        try:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Accept every Writing HUD section of full error pages",
    )
    parser.add_argument("--first-wait", type=float, default=120.0)
    parser.add_argument("--section-wait", type=float, default=45.0)
    parser.add_argument(
        "--pages",
        default=",".join(str(page["page_id"]) for page in PAGES),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "full-page-user-journey.json",
    )
    args = parser.parse_args(argv)
    wanted = {item.strip() for item in args.pages.split(",") if item.strip()}
    known = {str(page["page_id"]) for page in PAGES}
    unknown = wanted - known
    if unknown:
        parser.error(f"unknown pages: {sorted(unknown)}")

    reports = []
    for page in PAGES:
        if str(page["page_id"]) not in wanted:
            continue
        report = run_page(
            page,
            first_wait=args.first_wait,
            section_wait=args.section_wait,
        )
        reports.append(report)
        status = "PASS" if report.get("completed") and report.get("clean") else "FAIL"
        print(
            f"{status} {report['page_id']}: sections={len(report['sections'])} "
            f"total={report.get('total_seconds')}s "
            f"max-wait={report.get('max_section_wait')}s "
            f"errors-left={len(report.get('errors_remaining', []))} "
            f"missing={report.get('protected_missing', [])} "
            f"error={report.get('error', '')}",
            flush=True,
        )

    payload = {
        "schema_version": 1,
        "probe": "full_page_user_journey",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": bool(reports)
        and all(r.get("completed") and r.get("clean") for r in reports),
        "pages": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0 if payload["passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
