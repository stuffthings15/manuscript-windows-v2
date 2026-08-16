from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from echo_link_core.local_transport import LoopbackJSONTransport
from echo_link_core.selected_text import LocalWritingAssistant
from echo_link_app.review_spans import _review_spans


TONES = (
    "Clear",
    "Improve",
    "Rephrase",
    "Shorten",
    "Formal",
    "Friendly",
)
TONE_COMMANDS = {tone: ("Rewrite" if tone == "Clear" else tone) for tone in TONES}
HARD_TERMS = (
    "Manuscript",
    "Windows",
    "fifteen days",
    "three users",
    "microphone",
    "activation sound",
    "error logs",
    "small icon",
    "safe paste",
    "cloud service",
)
CRITERIA = {
    "Clear": "Clear, natural, readable wording and flow with all errors corrected and meaning preserved.",
    "Improve": "Clearer grammar, diction, structure, and flow without a needless tone change.",
    "Rephrase": "Materially different natural wording and syntax with the same details and force.",
    "Shorten": "Substantially shorter, concise, and free of repetition while keeping all key facts.",
    "Friendly": (
        "Visibly warm, conversational, people-centered, and helpful without slang, fake enthusiasm, "
        "or invented thanks. Contractions, gentle transitions, approachable vocabulary, and "
        "people-centered descriptions count as warmth; do not require extra commentary, invented "
        "emotion, direct reader address, or new supportive claims."
    ),
    "Formal": "Conventional, impersonal, formal language without contractions or colloquialisms.",
    "Professional": "Polished, confident, concise workplace language with clear actions and next steps.",
    "Academic": "Precise, objective academic prose with logical transitions and no invented citations.",
    "Conversational": "Natural spoken-style wording that remains correct, clear, and faithful.",
    "Concise": "Compact wording without filler or repetition and without losing essential details.",
    "Detailed": "Existing details and relationships made explicit without invented information.",
    "Persuasive": "Compelling organization of the source position without invented evidence or exaggeration.",
    "Confident": "Assured, decisive wording without increasing certainty, hostility, or obligation.",
    "Casual": "Relaxed everyday language with correct grammar and all important facts retained.",
    "Technical": "Precise technical language with identifiers, components, and constraints preserved.",
    "Simplified": "Plain vocabulary and simpler sentences without lost facts or childish language.",
    "High-school level": "Clear high-school-level vocabulary, complete sentences, and direct explanations.",
    "College level": "Polished college-level vocabulary, varied syntax, and organized reasoning.",
    "Diplomatic": (
        "Tactful, respectful, neutral framing that keeps requests and boundaries clear. Firm must or "
        "need wording retained from a source requirement is compatible with this style."
    ),
    "Empathetic": (
        "Recognizes stated impact and concerns with restrained, people-centered care, without inventing "
        "feelings or apologies. Describing the reported difficulty, confusion, or failure from the "
        "affected user's perspective counts as care; do not require emotional commentary."
    ),
    "Cooperative": "Collaborative, shared-goal, solution-focused language without changing ownership.",
    "Assertive": "Direct, active, firm, respectful language without hostility, threats, or weak hedging.",
    "Caveman": (
        "Short, blunt, plain standard English. Lead with the point. Use common words, short active sentences, "
        "and one idea per sentence. Keep paragraph form; do not add headings, bullets, numbered list "
        "formatting, or labels. "
        "Remove filler and jargon without losing facts. No broken grammar, mock "
        "accent, stereotype, childish speech, or confusing shorthand."
    ),
}
WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
NUMBER_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
JUDGE_SCORE_FIELDS = (
    "target_style",
    "usefulness",
)
JUDGE_KEYS = {
    "pass",
    *JUDGE_SCORE_FIELDS,
    "reason",
}

# This deliberately small verb set is limited to clear actor/action statements in
# the fixed quality document. It avoids pretending to be a general semantic parser.
ACTION_LEMMAS = {
    "accept": "accept", "accepted": "accept", "accepts": "accept",
    "apply": "apply", "applied": "apply", "applies": "apply",
    "change": "change", "changed": "change", "changes": "change",
    "check": "check", "checked": "check", "checks": "check",
    "confirm": "confirm", "confirmed": "confirm", "confirms": "confirm",
    "delete": "delete", "deleted": "delete", "deletes": "delete",
    "document": "document", "documented": "document", "documents": "document",
    "inspect": "inspect", "inspected": "inspect", "inspects": "inspect",
    "keep": "keep", "kept": "keep", "keeps": "keep",
    "remove": "remove", "removed": "remove", "removes": "remove",
    "request": "request", "requested": "request", "requests": "request",
    "restore": "restore", "restored": "restore", "restores": "restore",
    "review": "review", "reviewed": "review", "reviews": "review",
    "run": "run", "ran": "run", "runs": "run",
    "send": "send", "sent": "send", "sends": "send",
    "test": "test", "tested": "test", "tests": "test",
    "use": "use", "used": "use", "uses": "use",
    "verify": "verify", "verified": "verify", "verifies": "verify",
    "write": "write", "wrote": "write", "written": "write", "writes": "write",
}
RELATION_STOP_WORDS = {
    "a", "an", "and", "as", "at", "because", "but", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "their",
    "there", "these", "they", "this", "those", "to", "was", "were", "with",
}
DEFECT_PATTERNS = {
    "spelling": re.compile(
        r"\b(?:teh|mesages|recieved|wierd|grammer|seperate|seperates|sentances|"
        r"formating|occured|visable|privite|availible|understandible)\b",
        re.IGNORECASE,
    ),
    "grammar": re.compile(
        r"\b(?:team were|interface were|recording stop|each people|text don't|issues makes|"
        r"card need|reviewers needs|model check|workflow restore|requirements remains|"
        r"one-safe paste|a one safe paste)\b",
        re.IGNORECASE,
    ),
    "punctuation": re.compile(
        r"\s+[,.!?;:]|[!?]{2,}|\.{2,}|"
        r"\bthese\s+(?:issues|changes)\b[^?\r\n]{0,220}\b(?:active|completed|finished)\b[^?\r\n]*\?",
        re.IGNORECASE,
    ),
    "formatting": re.compile(r"[ \t]{2,}|^[a-z]", re.MULTILINE),
}

SOURCE_TEXT = """the project team were ready to launch Manuscript after monday review but teh interface were crowded on smaller screens and several status mesages wasnt clear . They tested the microphone on two laptops, however the recording stop without warning alot. the activation sound played after the speaker already started talking!! because the deadline was close the developers rush there review of the error logs, they documented changes to briefly. The first shared build open slow it put periods in wierd spots and didnt insert corrected text into the active document

the next morning the team recieved a detail report from three users who wrote several pages. each people liked the small icon but the correction card were still too large . One tester accept a spelling fix yet the text don't change and the HUD stay on first sentence; another tester seen inconsistant grammer marks while common spelling problems remains. The third tester request formal tone, the local AI return a shorter draft removed two details and change names that needed to remain exact. these issues makes review confusing because users cant tell which two or three sentances is active, or if replacement finished??

TEAM PLAN  the team agree to rebuild interaction around a compact dependable card . the card need to grow when text is longer shrink when shorter and stay beside selected writing. reviewers needs clear red marks under changed words, visable Accept and Dismiss controls and a reliable way moving to next sentence. They also need clear, formal, casual, concise, friendly, professional, diplomatic, empathetic, cooperative or assertive versions without opening main application window

the revised local workflow read active selection,seperates complete sentances and send each small group to installed AI model. The model check spelling grammer punctuation and basic formating before it return one clean suggestion . Every original paragraph and line break must remains in place unless user ask structural rewrite. if selection disappear while card is open Manuscript must finds same unchanged text before applying one safe paste; the workflow restore normal Windows clipboard immediate after any approved fallback

privacy requirements remains strict.. encrypted diagnostic history keep only information user approved. Old speech records is deleted within fifteen days and no privite sentence are sent to a cloud service . Application should works locally explain failures clear,and avoid hidden background uploads. Developers can use synthetic local evidence to improve product but they must keeps private user writing out test reports

before release team must run this entire page threw real HUD and inspect each result . Review should confirm document stay complete readable and consistant; it should verify microphone, activation sound,error logs, three users, small icon, two or three sentences, red marks, safe paste, clipboard restoration, fifteen days retention and cloud service boundary. final decision belong to user after local evidence is visable and understandible"""

QUICK_SOURCE_TEXT = """the project team were ready to launch Manuscript after monday review but teh interface were crowded on smaller screens and several status mesages wasnt clear . They tested the microphone on two laptops, however the recording stop without warning alot. the activation sound played after the speaker already started talking!!

the next morning the team recieved a detail report from three users who wrote several pages. each people liked the small icon but the correction card were still too large . One tester accept a spelling fix yet the text don't change and the HUD stay on first sentence; another tester seen inconsistant grammer marks while common spelling problems remains. these issues makes review confusing because users cant tell which two or three sentances is active, or if replacement finished??"""


def _words(text: str) -> int:
    return len(WORD.findall(text))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _surface_defects(text: str) -> dict[str, list[str]]:
    defects = {}
    for category, pattern in DEFECT_PATTERNS.items():
        matches = [match.group(0) for match in pattern.finditer(text)]
        if matches:
            defects[category] = matches
    return defects


def _fact_words(text: str) -> list[str]:
    words: list[str] = []
    for raw_word in WORD.findall(text):
        word = raw_word.casefold()
        hyphenated = word.split("-")
        if len(hyphenated) > 1 and all(
            part in NUMBER_WORDS or part in NUMBER_SCALES for part in hyphenated
        ):
            words.extend(hyphenated)
        else:
            words.append(word)
    return words


def _number_at(words: list[str], start: int) -> tuple[str, int] | None:
    if words[start] not in NUMBER_WORDS and words[start] not in NUMBER_SCALES:
        return None
    total = 0
    current = 0
    index = start
    saw_number = False
    while index < len(words):
        word = words[index]
        if word in NUMBER_WORDS:
            current += NUMBER_WORDS[word]
            saw_number = True
        elif word == "hundred" and saw_number:
            current = max(1, current) * NUMBER_SCALES[word]
        elif word in {"thousand", "million"} and saw_number:
            total += max(1, current) * NUMBER_SCALES[word]
            current = 0
        elif (
            word == "and"
            and saw_number
            and index + 1 < len(words)
            and words[index + 1] in NUMBER_WORDS
        ):
            index += 1
            continue
        else:
            break
        index += 1
    return str(total + current), index


def _normalized_fact_tokens(text: str) -> tuple[str, ...]:
    words = _fact_words(text)
    normalized: list[str] = []
    index = 0
    while index < len(words):
        number = _number_at(words, index)
        if number is None:
            normalized.append(words[index])
            index += 1
            continue
        value, index = number
        normalized.append(value)
    return tuple(normalized)


def _normalized_fact_text(text: str) -> str:
    return " ".join(_normalized_fact_tokens(text))


def _required_term_present(text: str, term: str, *, exact: bool) -> bool:
    if exact:
        return term.casefold() in text.casefold()
    expected = _normalized_fact_tokens(term)
    actual = _normalized_fact_tokens(text)
    if not expected:
        return False
    width = len(expected)
    return any(actual[index : index + width] == expected for index in range(len(actual) - width + 1))


def _hud_review_chunks(text: str) -> tuple[tuple[int, int, int], ...]:
    """Use the exact 1-3 sentence spans shown and rewritten by the HUD."""
    spans = _review_spans(text, maximum_sentences=3)
    if any(not 1 <= sentence_count <= 3 for _, _, sentence_count in spans):
        raise RuntimeError("HUD review span exceeded the 1-3 sentence quality-test contract.")
    return spans


def _rewrite_full_page(
    writer: LocalWritingAssistant,
    *,
    source: str,
    command: str,
    model: str,
    timeout: float,
    protected_terms: tuple[str, ...] = (),
) -> str:
    """Rewrite the full fixture in one resident-model call.

    The deterministic checks below still verify the exact HUD review spans.
    Batching avoids dozens of serial model calls per tone.
    """
    try:
        return writer.run(
            source,
            command=command,
            model=model,
            profile="General",
            protected_terms=protected_terms,
            timeout=timeout,
            keep_alive="300s",
            context_window=8192,
            max_tokens=2048,
        ).text.strip()
    except Exception as exc:
        raise RuntimeError(f"Full-page rewrite: {exc}") from exc


def _sentence_quotes(text: str) -> tuple[str, ...]:
    quotes = []
    for match in re.finditer(r"[^.!?\r\n]+(?:[.!?]+|(?=\r?$))", text, re.MULTILINE):
        quote = match.group(0).strip()
        if quote:
            quotes.append(quote)
    return tuple(quotes)


def _relation_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        ACTION_LEMMAS.get(word, word)
        for word in _fact_words(text)
        if word not in RELATION_STOP_WORDS
    )


def _anchor_matches(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    first_set = set(first)
    second_set = set(second)
    if not first_set or not second_set:
        return False
    overlap = len(first_set & second_set)
    required = 1 if min(len(first_set), len(second_set)) <= 2 else 2
    return overlap >= required


def _temporal_relations(text: str) -> tuple[dict[str, object], ...]:
    relations = []
    for quote in _sentence_quotes(text):
        for match in re.finditer(r"\b(before|after)\b", quote, re.IGNORECASE):
            left = _relation_tokens(quote[: match.start()])[-10:]
            right = _relation_tokens(quote[match.end() :])[:10]
            if left and right:
                relations.append(
                    {
                        "relation": match.group(1).casefold(),
                        "left": left,
                        "right": right,
                        "quote": quote,
                    }
                )
    return tuple(relations)


def _actor_action_relations(text: str) -> tuple[dict[str, object], ...]:
    relations = []
    passive_auxiliaries = {"am", "are", "be", "been", "being", "is", "was", "were"}
    for quote in _sentence_quotes(text):
        words = _fact_words(quote)
        for index, word in enumerate(words):
            lemma = ACTION_LEMMAS.get(word)
            if lemma is None:
                continue
            passive = (
                index > 0
                and words[index - 1] in passive_auxiliaries
                and "by" in words[index + 1 : index + 7]
            )
            if passive:
                continue
            left = tuple(
                ACTION_LEMMAS.get(item, item)
                for item in words[max(0, index - 6) : index]
                if item not in RELATION_STOP_WORDS and item not in ACTION_LEMMAS
            )
            right = tuple(
                ACTION_LEMMAS.get(item, item)
                for item in words[index + 1 : index + 9]
                if item not in RELATION_STOP_WORDS and item not in ACTION_LEMMAS
            )
            if left and right and not (set(left) & set(right)):
                relations.append(
                    {"action": lemma, "left": left, "right": right, "quote": quote}
                )
    return tuple(relations)


def _deterministic_relation_errors(source: str, result: str) -> list[dict[str, str]]:
    """Catch only high-confidence temporal and active-voice role reversals."""
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    source_temporal = _temporal_relations(source)
    result_temporal = _temporal_relations(result)
    for original in source_temporal:
        preserved_match = False
        reversed_matches = []
        for rewritten in result_temporal:
            same_events = _anchor_matches(original["left"], rewritten["left"]) and _anchor_matches(
                original["right"], rewritten["right"]
            )
            swapped_events = _anchor_matches(original["left"], rewritten["right"]) and _anchor_matches(
                original["right"], rewritten["left"]
            )
            preserved_relation = (
                same_events and original["relation"] == rewritten["relation"]
            ) or (swapped_events and original["relation"] != rewritten["relation"])
            if preserved_relation:
                preserved_match = True
                break
            reversed_relation = (
                same_events and original["relation"] != rewritten["relation"]
            ) or (swapped_events and original["relation"] == rewritten["relation"])
            if reversed_relation:
                reversed_matches.append(rewritten)
        if preserved_match:
            continue
        for rewritten in reversed_matches:
            key = ("temporal_relation_reversal", original["quote"], rewritten["quote"])
            if key not in seen:
                errors.append(
                    {
                        "issue": "Temporal before/after relation was reversed.",
                        "source_quote": str(original["quote"]),
                        "rewrite_quote": str(rewritten["quote"]),
                    }
                )
                seen.add(key)

    source_actions = _actor_action_relations(source)
    result_actions = _actor_action_relations(result)
    for original in source_actions:
        for rewritten in result_actions:
            if original["action"] != rewritten["action"]:
                continue
            normal_left = len(set(original["left"]) & set(rewritten["left"]))
            normal_right = len(set(original["right"]) & set(rewritten["right"]))
            inverse_left = len(set(original["left"]) & set(rewritten["right"]))
            inverse_right = len(set(original["right"]) & set(rewritten["left"]))
            normal_score = normal_left + normal_right
            inverse_score = inverse_left + inverse_right
            if not (
                inverse_left >= 1
                and inverse_right >= 1
                and inverse_score >= 2
                and inverse_score > normal_score
            ):
                continue
            key = ("actor_action_inversion", original["quote"], rewritten["quote"])
            if key not in seen:
                errors.append(
                    {
                        "issue": f"Actor and receiver were inverted for action '{original['action']}'.",
                        "source_quote": str(original["quote"]),
                        "rewrite_quote": str(rewritten["quote"]),
                    }
                )
                seen.add(key)
    return errors


def _required_meaning_errors(result: str) -> list[dict[str, str]]:
    """Verify the fixed stress page's concrete facts and relationships without an LLM."""
    paragraphs = tuple(part.strip() for part in result.split("\n\n") if part.strip())
    sentences = _sentence_quotes(result)
    errors: list[dict[str, str]] = []

    def context_with(*terms: str) -> str:
        for sentence in sentences:
            lowered = sentence.casefold()
            if all(term.casefold() in lowered for term in terms):
                return sentence
        for paragraph in paragraphs:
            lowered = paragraph.casefold()
            if all(term.casefold() in lowered for term in terms):
                return paragraph
        return ""

    def context_around(term: str, *, radius: int = 1) -> str:
        for index, sentence in enumerate(sentences):
            if term.casefold() in sentence.casefold():
                start = max(0, index - radius)
                end = min(len(sentences), index + radius + 1)
                return " ".join(sentences[start:end])
        return context_with(term)

    def require(ok: bool, issue: str, context: str) -> None:
        if not ok:
            errors.append({"issue": issue, "rewrite_quote": context})

    activation = context_with("activation sound")
    activation_ok = bool(
        re.search(
            r"activation sound[^.!?]{0,120}\bafter\b[^.!?]{0,120}\b(?:speaker|speech|talk)",
            activation,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:speaker|talk)[^.!?]{0,120}\bbefore\b[^.!?]{0,120}activation sound",
            activation,
            re.IGNORECASE,
        )
    )
    require(
        activation_ok,
        "Activation sound must remain after the speaker started talking.",
        activation,
    )

    launch = context_with("Manuscript", "Monday", "review")
    launch_ok = bool(
        re.search(r"\b(?:ready|prepared|set)\b", launch, re.IGNORECASE)
    ) and bool(re.search(r"\b(?:launch|release)\w*\b", launch, re.IGNORECASE)) and bool(
        re.search(
            r"\b(?:after|following)\b[^.!?]{0,40}\bMonday(?:'s)?\b[^.!?]{0,20}\breview\b",
            launch,
            re.IGNORECASE,
        )
    )
    require(
        launch_ok,
        "The team must remain ready to launch Manuscript after Monday's review.",
        launch,
    )

    retention = context_with("fifteen days")
    retention_ok = bool(
        re.search(
            r"\b(?:records?|retention|(?:speech|voice)\s+recordings?)\b",
            retention,
            re.IGNORECASE,
        )
    )
    retention_moved = bool(
        re.search(
            r"clipboard restoration\s+(?:for|of|within)\s+fifteen days",
            retention,
            re.IGNORECASE,
        )
    )
    require(
        retention_ok and not retention_moved,
        "Fifteen days must remain the speech-record retention period, not clipboard timing.",
        retention,
    )

    report_boundary = context_with("test reports")
    report_boundary_ok = bool(
        re.search(
            r"\b(?:private\s+user|user(?:'s)?\s+private|user)\s+(?:writing|data|text)\b"
            r"[^.!?]{0,80}\b(?:out\s+of|excluded?\s+from|omitted?\s+from|not\s+included\s+in|"
            r"(?:does|do)\s+not\s+appear\s+in|(?:doesn't|don't)\s+appear\s+in|kept\s+out\s+of)\b"
            r"[^.!?]{0,40}\btest\s+reports?\b",
            report_boundary,
            re.IGNORECASE,
        )
        or re.search(
            r"\btest\s+reports?\b[^.!?]{0,80}\b(?:exclude|excluded|omit|omitted|"
            r"must\s+not\s+(?:contain|include)|do\s+not\s+(?:contain|include))\b"
            r"[^.!?]{0,80}\b(?:private\s+user|user(?:'s)?\s+private|user)\s+"
            r"(?:writing|data|text)\b",
            report_boundary,
            re.IGNORECASE,
        )
    )
    developer_force_ok = bool(
        re.search(
            r"\bdevelopers?\b[^.!?]{0,180}\b(?:must|(?:are|remain)\s+required\s+to)\b",
            report_boundary,
            re.IGNORECASE,
        )
    )
    require(
        report_boundary_ok and developer_force_ok,
        "Private user writing must stay out of test reports.",
        report_boundary,
    )

    release_check = context_with("HUD", "inspect")
    release_order_ok = bool(
        re.search(
            r"\brun\b[^.!?]{0,160}\bHUD\b[^.!?]{0,100}\binspect\b",
            release_check,
            re.IGNORECASE,
        )
    ) and not bool(
        re.search(
            r"\bbefore\s+(?:the\s+)?(?:release\s+)?team\s+runs?\b",
            release_check,
            re.IGNORECASE,
        )
    )
    require(
        release_order_ok,
        "The page must run through the HUD before its results are inspected.",
        release_check,
    )

    tone_owner = next(
        (
            paragraph
            for paragraph in paragraphs
            if all(term in paragraph.casefold() for term in ("formal", "friendly", "assertive"))
        ),
        "",
    )
    tone_owner_ok = bool(
        re.search(r"\b(?:reviewers?|they)\s+(?:also\s+)?(?:need|require)\b", tone_owner, re.IGNORECASE)
    )
    require(
        tone_owner_ok,
        "Reviewers must remain the owners who need the tone versions.",
        tone_owner,
    )

    workflow = next(
        (
            paragraph
            for paragraph in paragraphs
            if "workflow" in paragraph.casefold()
            and (
                "selection" in paragraph.casefold()
                or "active text" in paragraph.casefold()
            )
        ),
        "",
    )
    workflow_ok = bool(
        re.search(
            r"\bworkflow\b[^.!?]{0,120}(?:"
            r"\b(?:reads|processes|starts\s+(?:with|by\s+reading)|begins\s+(?:with|by\s+reading))\b"
            r"[^.!?]{0,80}\b(?:selection|selecting\s+active\s+text)\b|"
            r"\binvolves\s+selecting\s+(?:the\s+)?active\s+(?:text|selection)\b)"
            r"[^.!?]{0,100}\b(?:separates|separating)\b"
            r"[^.!?]{0,120}\b(?:sends|sending)\b",
            workflow,
            re.IGNORECASE,
        )
        or re.search(
            r"\bworkflow\b[^.!?]{0,120}\b(?:reads|processes)\b[^.!?]{0,80}"
            r"\b(?:selection|active text)\b[.!?]\s*(?:It|The workflow)\b[^.!?]{0,80}"
            r"\b(?:separates|separating)\b[^.!?]{0,120}\b(?:sends|sending)\b",
            workflow,
            re.IGNORECASE,
        )
    ) and not bool(re.search(r"\bselection\s+separates\b", workflow, re.IGNORECASE))
    require(
        workflow_ok,
        "The workflow must read the selection, separate sentences, and send the groups.",
        workflow,
    )

    shared_build = context_around("shared build")
    build_ok = bool(re.search(r"\b(?:slow\w*|delay\w*)\b", shared_build, re.IGNORECASE)) and bool(
        re.search(
            r"\b(?:fail\w*|did\s+not|didn(?:'|\u2019)t|not|without)\b[^.!?]{0,80}"
            r"\b(?:insert|incorporate|place)\w*\b[^.!?]{0,60}\bcorrected text\b",
            shared_build,
            re.IGNORECASE,
        )
    )
    require(
        build_ok,
        "The first shared build must remain slow and fail to insert corrected text.",
        shared_build,
    )

    local_app = context_with("application", "failures")
    local_ok = bool(re.search(r"\b(?:local|locally|on-device)\b", local_app, re.IGNORECASE))
    application_force_ok = bool(
        re.search(
            r"\bapplication\b[^.!?]{0,100}\b(?:should|is\s+expected\s+to)\b",
            local_app,
            re.IGNORECASE,
        )
    )
    require(
        local_ok and application_force_ok,
        "The application should still work locally without a stronger obligation.",
        local_app,
    )

    user_report = context_with("three users")
    user_report_ok = bool(
        re.search(r"\bthree users\b[^.!?]{0,100}\b(?:wrote|written|writing)\b[^.!?]{0,40}\bpages\b", user_report, re.IGNORECASE)
    )
    require(
        user_report_ok,
        "The report must remain from three users who wrote several pages.",
        user_report,
    )

    spelling_fix = context_around("spelling fix", radius=2)
    spelling_fix_ok = bool(
        re.search(
            r"\btester\b[^.!?]{0,80}\baccept\w*\b[^.!?]{0,40}\bspelling fix\b",
            spelling_fix,
            re.IGNORECASE,
        )
    ) and bool(
        re.search(
            r"\btext\b[^.!?]{0,50}(?:\b(?:did\s+not|didn(?:'|\u2019)t)\s+change\b|"
            r"\b(?:remained|stayed)\s+unchanged\b)",
            spelling_fix,
            re.IGNORECASE,
        )
    ) and bool(
        re.search(
            r"\bHUD\b[^.!?]{0,70}\b(?:stayed|remained)\b[^.!?]{0,40}\bfirst sentence\b",
            spelling_fix,
            re.IGNORECASE,
        )
    )
    require(
        spelling_fix_ok,
        "The accepted spelling fix must leave the unchanged text and HUD on the first sentence.",
        spelling_fix,
    )

    changed_names = next(
        (paragraph for paragraph in paragraphs if "two details" in paragraph.casefold()),
        "",
    )
    names_ok = "names" in changed_names.casefold() and bool(
        re.search(
            r"\b(?:exact|exactly|unchanged|unaltered|precise)\b",
            changed_names,
            re.IGNORECASE,
        )
    )
    names_ok = names_ok and bool(
        re.search(
            r"(?:\bthird tester\b[^.!?]{0,100}\brequest\w*\b[^.!?]{0,50}\bformal tone\b|"
            r"\brequest\w*\b[^.!?]{0,50}\b(?:from|by)\s+(?:the\s+)?third tester\b"
            r"[^.!?]{0,50}\bformal tone\b)",
            changed_names,
            re.IGNORECASE,
        )
    ) and bool(
        re.search(
            r"\blocal AI\b[^.!?]{0,100}\b(?:return\w*|produc\w*|provid\w*)\b[^.!?]{0,80}"
            r"\b(?:shorter|shortened) (?:draft|version)\b[^.!?]{0,100}\b(?:remov\w*|omitt\w*)\b"
            r"[^.!?]{0,40}\btwo details\b[^.!?]{0,100}\b(?:chang\w*|alter\w*)\b"
            r"[^.!?]{0,40}\bnames\b",
            changed_names,
            re.IGNORECASE,
        )
        or re.search(
            r"\blocal AI\b[^.!?]{0,100}\b(?:made|return\w*|produc\w*|provid\w*)\b[^.!?]{0,80}"
            r"\b(?:shorter|shortened) (?:draft|version)\b[.!?]\s*(?:It|The draft)\b"
            r"[^.!?]{0,80}\b(?:remov\w*|omitt\w*)\b[^.!?]{0,40}\btwo details\b"
            r"[^.!?]{0,100}\b(?:chang\w*|alter\w*)\b[^.!?]{0,40}\bnames\b",
            changed_names,
            re.IGNORECASE,
        )
    )
    require(
        names_ok,
        "The removed two details and names that must remain exact must stay linked.",
        changed_names,
    )

    clipboard = context_with("clipboard")
    fallback_ok = "fallback" in clipboard.casefold() and bool(
        re.search(r"\b(?:restor\w*|return\w*\s+to\s+using)\b", clipboard, re.IGNORECASE)
    )
    require(
        fallback_ok,
        "Clipboard restoration must remain tied to an approved fallback.",
        clipboard,
    )
    return errors


def _judge_formula_pass(judged: dict) -> bool:
    return all(
        type(judged.get(field)) is int and 4 <= judged[field] <= 5
        for field in JUDGE_SCORE_FIELDS
    )


def _normalize_judgment(judged: dict) -> dict:
    """Make the deterministic score formula authoritative over model arithmetic."""
    return {**judged, "pass": _judge_formula_pass(judged)}


def _validate_judgment(judged: dict, *, source: str, result: str) -> None:
    if set(judged) != JUDGE_KEYS:
        missing = sorted(JUDGE_KEYS - set(judged))
        extra = sorted(set(judged) - JUDGE_KEYS)
        raise RuntimeError(f"Local judge schema mismatch; missing={missing}, extra={extra}.")
    if type(judged["pass"]) is not bool:
        raise RuntimeError("Local judge key 'pass' must be a boolean.")
    for field in JUDGE_SCORE_FIELDS:
        if type(judged[field]) is not int or not 0 <= judged[field] <= 5:
            raise RuntimeError(f"Local judge score '{field}' must be an integer from 0 to 5.")
    if not isinstance(judged["reason"], str) or not judged["reason"].strip():
        raise RuntimeError("Local judge key 'reason' must be a non-empty string.")


def _judge(
    transport: LoopbackJSONTransport,
    *,
    model: str,
    tone: str,
    source: str,
    result: str,
    timeout: float,
) -> dict:
    tone_note = ""
    if tone == "Diplomatic":
        tone_note = (
            "For Diplomatic, a firm must or need copied from a source requirement is not a style flaw. "
            "Do not demand that obligations become optional suggestions.\n"
        )
    elif tone == "Friendly":
        tone_note = (
            "For Friendly, contractions, gentle transitions, approachable vocabulary, and "
            "people-centered descriptions are valid warmth. Accuracy rules forbid invented feelings, "
            "thanks, direct reader address, and extra supportive claims; do not demand them.\n"
        )
    elif tone == "Empathetic":
        tone_note = (
            "For Empathetic, restrained people-centered descriptions of a reported difficulty, confusion, "
            "or failure count as care. Accuracy rules forbid invented emotions, apologies, and extra "
            "supportive claims; do not demand them. Do not require empathy markers in paragraphs that "
            "contain only technical requirements and no stated human impact.\n"
        )
    elif tone == "Caveman":
        tone_note = (
            "For Caveman, do not penalize a source 'should' that remains for force accuracy or an exact "
            "required source enumeration that cannot be shortened. The source requires the tone names, "
            "writing checks, and release-check facts; preserving those items is not list formatting or "
            "filler. This style intentionally uses correct, complete standard English rather than caricature "
            "or telegram fragments. An ordinary sentence of 18 words or fewer meets the stated length target. "
            "Judge the ordinary prose around required facts for bluntness and brevity.\n"
        )
    prompt = f"""You are a strict style judge. Evaluate only tone and natural usefulness.
Return only one JSON object. Do not use Markdown.

TARGET STYLE: {tone}
TARGET CRITERIA: {CRITERIA[tone]}

Objective spelling, grammar, punctuation, completeness, fact, number, ownership, timing,
privacy-boundary, and structure checks are performed separately by deterministic code. Do not
score or discuss those areas. Do not treat ordinary tense or agreement correction as a style flaw.
For Shorten, compression length is also checked separately; judge whether the wording is concise.
{tone_note}

Score each field from 0 to 5:
- target_style: how clearly and consistently the requested style is expressed.
- usefulness: how natural, readable, and usable the writing feels for that style.

The pass formula is exact: pass=true if and only if both scores are at least 4.
The harness recomputes pass from the two scores, so score each field carefully.
Required JSON keys exactly:
{{"pass":false,"target_style":0,"usefulness":0,"reason":"brief style-only reason"}}
Do not add keys.

SOURCE DOCUMENT:
{source}

REWRITTEN DOCUMENT:
{result}
"""
    last_error: Exception | None = None
    for attempt in range(3):
        request_prompt = prompt
        if attempt:
            request_prompt = (
                "RETRY: Your prior JSON failed strict schema validation. Return a fresh JSON object "
                "with exactly pass, target_style, usefulness, and reason. "
                f"VALIDATION FAILURE: {last_error}\n\n" + prompt
            )
        payload = transport.post_json(
            "/api/generate",
            {
                "model": model,
                "prompt": request_prompt,
                "stream": False,
                "keep_alive": "300s",
                "format": "json",
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 256},
            },
            timeout=timeout,
        )
        try:
            raw = str(payload.get("response") or "").strip()
            judged = json.loads(raw)
            if not isinstance(judged, dict):
                raise RuntimeError("Local judge did not return an object.")
            _validate_judgment(judged, source=source, result=result)
            return _normalize_judgment(judged)
        except (json.JSONDecodeError, RuntimeError, TypeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"Local style judge failed strict validation three times: {last_error}")


def run_probe(
    *,
    writer_model: str,
    judge_model: str,
    timeout: float,
    tones: tuple[str, ...] = TONES,
    model_overrides: dict[str, str] | None = None,
    protect_facts: bool = False,
    source_text: str = SOURCE_TEXT,
) -> dict:
    source = source_text.replace("\r\n", "\n")
    writer = LocalWritingAssistant()
    judge_transport = LoopbackJSONTransport(writer.endpoint)
    source_words = _words(source)
    source_defects = _surface_defects(source)
    cases = []
    overrides = model_overrides or {}
    # Generate every tone before loading the independent judge. Alternating a
    # large writer and judge model forces repeated GPU unload/reload cycles.
    for tone in tones:
        command = TONE_COMMANDS[tone]
        case_model = overrides.get(tone, writer_model)
        written = None
        try:
            written = _rewrite_full_page(
                writer,
                source=source,
                command=command,
                model=case_model,
                timeout=timeout,
                protected_terms=HARD_TERMS if protect_facts else (),
            )
            result_words = _words(written)
            ratio = result_words / source_words
            source_terms = [
                term for term in HARD_TERMS if _required_term_present(source, term, exact=False)
            ]
            missing_terms = [
                term
                for term in source_terms
                if not _required_term_present(written, term, exact=protect_facts)
            ]
            remaining_defects = _surface_defects(written)
            paragraph_count_ok = len(written.split("\n\n")) == len(source.split("\n\n"))
            line_break_structure_ok = re.findall(r"\n+", written) == re.findall(r"\n+", source)
            deterministic_relation_errors = [
                *_deterministic_relation_errors(source, written),
                *(
                    _required_meaning_errors(written)
                    if source == SOURCE_TEXT.replace("\r\n", "\n")
                    else ()
                ),
            ]
            shorten_ceiling = 0.995 if source_text == QUICK_SOURCE_TEXT else 0.93
            # Computed but never recorded. The length check this probe was meant
            # to apply was lost; see the debt register in docs/ARCHITECTURE.md.
            length_ok = 0.35 <= ratio <= shorten_ceiling if tone == "Shorten" else ratio >= 0.65  # noqa: F841
            cases.append(
                {
                    "tone": tone,
                    "command": command,
                    "writer_model": case_model,
                    "passed": False,
                    "source_words": source_words,
                    "result_words": result_words,
                    "length_ratio": round(ratio, 3),
                    "missing_required_terms": missing_terms,
                    "remaining_surface_defects": remaining_defects,
                    "deterministic_relation_errors": deterministic_relation_errors,
                    "paragraph_count_ok": paragraph_count_ok,
                    "line_break_structure_ok": line_break_structure_ok,
                    "hud_chunk_count": len(_hud_review_chunks(source)),
                    "judge_formula_pass": False,
                    "judge": None,
                    "result_sha256": _digest(written),
                    "result": written,
                }
            )
        except Exception as exc:
            failed_case = {
                "tone": tone,
                "command": command,
                "writer_model": case_model,
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            if written is not None:
                failed_case["result_sha256"] = _digest(written)
                failed_case["result"] = written
            cases.append(failed_case)

    for case in cases:
        written = case.get("result")
        if not written or case.get("error"):
            continue
        try:
            judged = _judge(
                judge_transport,
                model=judge_model,
                tone=str(case["tone"]),
                source=source,
                result=str(written),
                timeout=timeout,
            )
            judge_formula_pass = _judge_formula_pass(judged)
            case["judge"] = judged
            case["judge_formula_pass"] = judge_formula_pass
            case["passed"] = bool(
                judge_formula_pass
                and not case["missing_required_terms"]
                and not case["remaining_surface_defects"]
                and not case["deterministic_relation_errors"]
                and case["paragraph_count_ok"]
                and case["line_break_structure_ok"]
                and (
                    0.35 <= float(case["length_ratio"]) <= (
                        0.995 if source_text == QUICK_SOURCE_TEXT else 0.93
                    )
                    if case["tone"] == "Shorten"
                    else float(case["length_ratio"]) >= 0.65
                )
            )
        except Exception as exc:
            case["error"] = f"{type(exc).__name__}: {exc}"
    passed = sum(bool(case.get("passed")) for case in cases)
    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": "local-only",
        "endpoint": writer.endpoint,
        "writer_model": writer_model,
        "judge_model": judge_model,
        "source_words": source_words,
        "source_sha256": _digest(source),
        "source_defects": {key: len(value) for key, value in source_defects.items()},
        "protected_fact_stress": protect_facts,
        "passed": passed == len(tones),
        "passed_count": passed,
        "case_count": len(tones),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every Manuscript tone on a full page and judge it locally")
    parser.add_argument("--writer-model", default="gemma4:12b-it-q4_K_M")
    parser.add_argument("--judge-model", default="gemma4:12b-it-q4_K_M")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--tones",
        default=",".join(TONES),
        help="Comma-separated subset of visible tone settings",
    )
    parser.add_argument(
        "--model-overrides",
        default="",
        help="Comma-separated Tone=model overrides",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "full-page-tone-quality.json",
    )
    parser.add_argument(
        "--protect-facts",
        action="store_true",
        help="Protect required phrases as a glossary stress test",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use the compact sealed tone corpus for fast routine verification",
    )
    args = parser.parse_args(argv)
    tones = tuple(item.strip() for item in args.tones.split(",") if item.strip())
    if not tones or any(tone not in TONES for tone in tones):
        parser.error("--tones contains an unknown tone setting")
    overrides = {}
    for item in (part.strip() for part in args.model_overrides.split(",") if part.strip()):
        if "=" not in item:
            parser.error("--model-overrides must use Command=model")
        tone, model = (part.strip() for part in item.split("=", 1))
        if tone not in TONES or not model:
            parser.error("--model-overrides contains an invalid tone or model")
        overrides[tone] = model
    report = run_probe(
        writer_model=args.writer_model,
        judge_model=args.judge_model,
        timeout=args.timeout,
        tones=tones,
        model_overrides=overrides,
        protect_facts=args.protect_facts,
        source_text=QUICK_SOURCE_TEXT if args.quick else SOURCE_TEXT,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Full-page tone quality: {report['passed_count']}/{report['case_count']}")
    for case in report["cases"]:
        reason = case.get("error") or case.get("judge", {}).get("reason", "")
        print(f"{case['tone']}: {'PASS' if case.get('passed') else 'FAIL'} - {reason}")
    print(args.output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
