from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping


MAX_RULES = 500
MAX_PROPOSALS = 3
MAX_REPLACEMENTS = 10
MAX_OUTPUT_GROWTH = 512
_TOKEN = re.compile(r"\w+(?:['’-]\w+)*|[^\w\s]", re.UNICODE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE = re.compile(
    r"(?:https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?:[A-Za-z]:\\|/)|\b[\w-]+\.[A-Za-z0-9]{1,10}\b|"
    r"\b\d+(?:[.,:/-]\d+)*\b|ECHOLINKTOKEN|API[_-]?KEY|TOKEN|SECRET|PASSWORD)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LearningProposal:
    source: str
    replacement: str


def normalize_learning_key(text: str) -> str:
    return " ".join((text or "").strip().split()).casefold()


def validate_learning_rule(source: str, replacement: str) -> LearningProposal:
    source_value = " ".join((source or "").strip().split())
    replacement_value = " ".join((replacement or "").strip().split())
    if not source_value or not replacement_value or source_value == replacement_value:
        raise ValueError("A learned correction needs different From and To text.")
    if _CONTROL.search(source or "") or _CONTROL.search(replacement or ""):
        raise ValueError("Learned corrections cannot contain line breaks or control characters.")
    if len(source_value) > 80 or len(replacement_value) > 160:
        raise ValueError("The learned correction is too long.")
    if len(source_value.split()) > 8 or len(replacement_value.split()) > 8:
        raise ValueError("Learned corrections are limited to short phrases.")
    if not _is_plain_word_phrase(source_value) or not _is_plain_word_phrase(replacement_value):
        raise ValueError("Only plain-word phrase corrections can be learned.")
    if _SENSITIVE.search(source_value) or _SENSITIVE.search(replacement_value):
        raise ValueError("Protected or sensitive text cannot become a learned correction.")
    if len(source_value) < 3 and source_value.casefold() != "i":
        raise ValueError("The From phrase is too short to apply safely.")
    return LearningProposal(source_value, replacement_value)


def _is_plain_word_phrase(value: str) -> bool:
    return any(character.isalpha() for character in value) and all(
        character.isalpha() or character in {" ", "'", "’", "-"}
        for character in value
    )


def propose_learning(suggested: str, accepted: str) -> tuple[LearningProposal, ...]:
    source_tokens = _TOKEN.findall((suggested or "").strip())
    target_tokens = _TOKEN.findall((accepted or "").strip())
    if not source_tokens or not target_tokens or source_tokens == target_tokens:
        return ()
    opcodes = SequenceMatcher(
        None,
        [token.casefold() for token in source_tokens],
        [token.casefold() for token in target_tokens],
        autojunk=False,
    ).get_opcodes()
    changes = [opcode for opcode in opcodes if opcode[0] != "equal"]
    if any(change[0] != "replace" for change in changes):
        return ()
    proposals: list[LearningProposal] = []
    try:
        for _tag, source_start, source_end, target_start, target_end in changes:
            if source_end - source_start > 3 or target_end - target_start > 3:
                return ()
            proposals.append(
                validate_learning_rule(
                    " ".join(source_tokens[source_start:source_end]),
                    " ".join(target_tokens[target_start:target_end]),
                )
            )
        for tag, source_start, source_end, target_start, _target_end in opcodes:
            if tag != "equal":
                continue
            for offset, source_token in enumerate(source_tokens[source_start:source_end]):
                target_token = target_tokens[target_start + offset]
                if source_token != target_token and source_token.casefold() == target_token.casefold():
                    proposals.append(validate_learning_rule(source_token, target_token))
    except ValueError:
        return ()
    if not proposals or len(proposals) > MAX_PROPOSALS:
        return ()
    keys = [normalize_learning_key(item.source) for item in proposals]
    if len(keys) != len(set(keys)):
        return ()
    return tuple(proposals)


def apply_learned_corrections(text: str, rules: Mapping[str, str]) -> str:
    value = text or ""
    valid: dict[str, str] = {}
    for source, replacement in list(rules.items())[:MAX_RULES]:
        try:
            rule = validate_learning_rule(source, replacement)
        except ValueError:
            continue
        valid[normalize_learning_key(rule.source)] = rule.replacement
    if not value or not valid:
        return value
    sources = sorted(valid, key=lambda item: (len(item.split()), len(item)), reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(item) for item in sources) + r")(?!\w)",
        re.IGNORECASE,
    )
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if count >= MAX_REPLACEMENTS:
            return match.group(0)
        count += 1
        return valid[normalize_learning_key(match.group(0))]

    result = pattern.sub(replace, value)
    if len(result) - len(value) > MAX_OUTPUT_GROWTH:
        return value
    return result
