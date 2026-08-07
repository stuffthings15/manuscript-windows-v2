from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .accepted_inference import AcceptedInferenceCoordinator
from .developer_text import format_developer_text
from .learning import apply_learned_corrections
from .local_transport import LocalTransportError, LoopbackJSONTransport
from .models import DictationMode, DictationSettings, OLLAMA_KEEP_ALIVE


logger = logging.getLogger(__name__)

_QUESTION_START = re.compile(
    r"^(?:who|what|when|where|why|how|can|could|would|should|will|do|does|did|is|are|am|was|were|have|has|had)\b",
    re.IGNORECASE,
)
_PROTECTED_PATTERN = re.compile(
    r"(?:https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?:[A-Za-z]:\\|/)[^\s]+|\b[\w-]+\.[A-Za-z0-9]{1,10}\b|"
    r"\b\d+(?:[.,:/-]\d+)*\b)"
)
_SEMANTIC_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]*")
_NUMBERED_LIST_MARKERS = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "five": 5,
    "fifth": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
    "ten": 10,
    "tenth": 10,
}


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    text: str
    source: str
    warning: str = ""


def strip_repetitions(text: str) -> str:
    words = (text or "").split()
    if not words:
        return ""
    deduped: list[str] = []
    for word in words:
        if deduped and word.casefold() == deduped[-1].casefold():
            continue
        deduped.append(word)
    words = deduped
    for size in range(2, 7):
        index = 0
        output: list[str] = []
        while index < len(words):
            if index + size * 2 <= len(words):
                left = [item.casefold() for item in words[index : index + size]]
                right = [item.casefold() for item in words[index + size : index + size * 2]]
                if left == right:
                    output.extend(words[index : index + size])
                    index += size * 2
                    continue
            output.append(words[index])
            index += 1
        words = output
    return " ".join(words)


def apply_self_corrections(text: str) -> str:
    result = text or ""
    replacement_marker = re.compile(
        r"\b(?P<old>[\w'-]+)[\s,.;:!?…-]+(?:actually[\s,.;:!?…-]+)?"
        r"(?:make|change)[\s,.;:!?…-]+that(?:\s+to)?\s+(?P<new>[\w'-]+)\b",
        re.IGNORECASE,
    )
    while True:
        match = replacement_marker.search(result)
        if not match:
            break
        result = f"{result[:match.start()]}{match.group('new')}{result[match.end():]}"

    simple_marker = re.compile(r"\b(?:no[,.]?\s+wait|actually[,.]?\s+no)\b", re.IGNORECASE)
    while True:
        match = simple_marker.search(result)
        if not match:
            break
        prefix = result[: match.start()].rstrip()
        suffix = result[match.end() :].lstrip(" ,.;:")
        prefix = re.sub(r"(?:\b[\w'-]+)[\s,.;:!?]*$", "", prefix).rstrip()
        result = f"{prefix} {suffix}".strip()

    scratch_marker = re.compile(r"\b(?:scratch that|disregard that)\b", re.IGNORECASE)
    while True:
        match = scratch_marker.search(result)
        if not match:
            break
        prefix = result[: match.start()].rstrip()
        suffix = result[match.end() :].lstrip(" ,.;:")
        boundary = max(prefix.rfind("."), prefix.rfind("?"), prefix.rfind("!"), prefix.rfind("\n"))
        prefix = prefix[: boundary + 1] if boundary >= 0 else ""
        result = f"{prefix} {suffix}".strip()
    return result


def remove_fillers(text: str) -> str:
    result = text or ""
    result = re.sub(
        r"(?i)(?<!\w)(?:um+|uh+|erm+|er+|ah+|hmm+)(?!\w)[\s,.;:!?…-]*",
        " ",
        result,
    )
    result = re.sub(r"(?i)^\s*(?:you know|like)\s*[,;:…-]+\s*", "", result)
    result = re.sub(r"(?i)\s*[,;:…-]+\s*(?:you know|like)\s*[,;:…-]+\s*", " ", result)
    result = re.sub(r"(?i)\s*[,;:…-]?\s+you know\s*[,;:…-]*\s*$", "", result)
    result = re.sub(r"(?i)(?:\[|\(|<)\s*(?:long\s+)?pause\s*(?:\]|\)|>)", " ", result)
    return result


def infer_sentence_boundaries(text: str) -> str:
    imperative = (
        r"create|open|save|send|run|start|stop|update|install|build|check|"
        r"write|copy|insert|review|test|make|show|close|continue"
    )
    result = re.sub(
        rf"(?<![.!?\r\n])[,;:]?[ \t]+(?P<transition>now|next|after[ \t]+that|finally)[ \t]+"
        rf"(?=(?:{imperative})\b)",
        lambda match: f". {match.group('transition').capitalize()} ",
        text or "",
        flags=re.IGNORECASE,
    )

    auxiliary = r"can|could|would|should|will|do|does|did|is|are|was|were|have|has|had"
    subject = r"i|you|we|they|he|she|it|this|that|there|the|a|an|my|your|our|their|his|her"
    question_start = (
        rf"(?:{auxiliary})(?=\s+(?:{subject})\b)|"
        rf"(?:who|what|when|where|why|how)\s+(?:{auxiliary})(?=\s+\w)"
    )
    unsafe_prefix = re.compile(
        r"(?:\b(?:and|or|but|if|whether|because|although|while|when|where|why|how|"
        r"what|who|that|to|now|next|then|also|so|please)|"
        r"\b(?:ask|asked|wonder|wondered|wondering|know|knew|explain|decide|check|"
        r"confirm|see)|\b(?:tell|show)\s+me|\bquestion\s+is)\s*$",
        re.IGNORECASE,
    )

    def split_clear_question(match: re.Match[str]) -> str:
        prefix = match.string[: match.start()].rstrip()
        if len(_SEMANTIC_WORD.findall(prefix)) < 2 or unsafe_prefix.search(prefix):
            return match.group(0)
        return f". {match.group('question').capitalize()}"

    return re.sub(
        rf"(?<![.!?\r\n])[,;:]?[ \t]+(?P<question>{question_start})\b",
        split_clear_question,
        result,
        flags=re.IGNORECASE,
    )


def format_automatic_list(text: str) -> str:
    value = (text or "").strip()
    lines = value.splitlines()
    if len(lines) >= 3 and all(line.strip() for line in lines):
        first_words = [line.strip().split(maxsplit=1)[0].casefold() for line in lines]
        if len(set(first_words)) == 1:
            return "\n".join(f"- {line.strip()}" for line in lines)

    bullet_parts = [part.strip(" ,.;:-") for part in re.split(r"(?i)\bbullet(?:\s+point)?\b", value)]
    bullet_parts = [part for part in bullet_parts if part]
    if re.match(r"(?i)^\s*bullet(?:\s+point)?\b", value) and len(bullet_parts) >= 3:
        return "\n".join(_format_list_item(part) for part in bullet_parts)

    marker_pattern = re.compile(
        r"(?i)\b(" + "|".join(sorted(_NUMBERED_LIST_MARKERS, key=len, reverse=True)) + r")\b"
    )
    markers = list(marker_pattern.finditer(value))
    if len(markers) < 3 or value[: markers[0].start()].strip(" ,.;:-"):
        return text
    numbers = [_NUMBERED_LIST_MARKERS[match.group(1).casefold()] for match in markers]
    if numbers != list(range(1, len(numbers) + 1)):
        return text
    items: list[str] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        item = value[marker.end() : end].strip(" ,.;:-")
        if not item:
            return text
        items.append(item)
    return "\n".join(_format_list_item(item) for item in items)


def _format_list_item(text: str) -> str:
    item = text.strip()
    if item:
        item = item[0].upper() + item[1:]
    return f"- {item}"


def expand_snippet(text: str, snippets: dict[str, str]) -> str | None:
    spoken = " ".join((text or "").strip().split()).casefold()
    prefixes = ("insert snippet ", "snippet ")
    for prefix in prefixes:
        if spoken.startswith(prefix):
            spoken = spoken[len(prefix) :].strip()
            break
    for trigger, content in snippets.items():
        if spoken == " ".join(trigger.strip().split()).casefold():
            return content.strip()
    return None


def apply_vocabulary(text: str, vocabulary: dict[str, str]) -> str:
    result = text or ""
    for spoken in sorted(vocabulary, key=len, reverse=True):
        replacement = vocabulary[spoken]
        pattern = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)", re.IGNORECASE)
        result = pattern.sub(lambda _match, value=replacement: value, result)
    return result


def normalize_spoken_punctuation(text: str) -> str:
    result = text or ""
    replacements = (
        (r"\bnew paragraph\b", "\n\n"),
        (r"\bnew sentence\b", ". "),
        (r"\bnew line\b", "\n"),
        (r"\bquestion mark\b", "?"),
        (r"\bexclamation (?:mark|point)\b", "!"),
        (r"\bfull stop\b", "."),
        (r"\bperiod\b", "."),
        (r"\bsemicolon\b", ";"),
        (r"\bcolon\b", ":"),
    )
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\s+comma(?=\s|$)", ",", result, flags=re.IGNORECASE)
    result = re.sub(r"\?{2,}", "?", result)
    result = re.sub(r"!{2,}", "!", result)
    return result


def format_sentences(text: str) -> str:
    result = (text or "").strip()
    if not result:
        return ""
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\s+([,.;:!?])", r"\1", result)
    result = re.sub(r"([,.;:!?])(?=[A-Za-z0-9])", r"\1 ", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n[ \t]+", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    characters = list(result)
    capitalize_next = True
    for index, character in enumerate(characters):
        if capitalize_next and character.isalpha():
            characters[index] = character.upper()
            capitalize_next = False
        elif character in ".!?\n":
            capitalize_next = True
        elif not character.isspace():
            capitalize_next = False
    result = "".join(characters).strip()
    if result[-1] not in ".!?":
        final_sentence = re.split(r"[.!?]\s+|\n+", result)[-1].strip()
        result += "?" if _QUESTION_START.match(final_sentence) else "."
    return result


def deterministic_cleanup(text: str, vocabulary: dict[str, str]) -> str:
    # Remove repeated speech before punctuation markers become symbols or line breaks.
    result = strip_repetitions(text)
    result = normalize_spoken_punctuation(result)
    result = apply_self_corrections(result)
    result = remove_fillers(result)
    result = apply_vocabulary(result, vocabulary)
    protected_text, replacements = _protect_text(result, vocabulary.values())
    protected_text = infer_sentence_boundaries(protected_text)
    formatted = format_sentences(protected_text)
    for token, value in replacements.items():
        formatted = formatted.replace(token, value)
    return format_automatic_list(formatted)


class OllamaRefiner:
    def __init__(self, endpoint: str = "http://127.0.0.1:11434"):
        self._transport = LoopbackJSONTransport(endpoint)

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint

    def available_models(self, timeout: float = 1.5) -> set[str]:
        try:
            payload = self._transport.get_json("/api/tags", timeout=timeout)
        except LocalTransportError:
            return set()
        return {
            str(item.get("name") or item.get("model") or "")
            for item in payload.get("models", [])
            if item.get("name") or item.get("model")
        }

    def preload(self, *, model: str, timeout: float) -> None:
        self._transport.post_json(
            "/api/generate",
            {
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
            },
            timeout=max(2.0, float(timeout)),
        )

    def refine(
        self,
        text: str,
        *,
        model: str,
        protected_terms: Iterable[str],
        timeout: float,
    ) -> CorrectionResult:
        protected_text, replacements = _protect_text(text, protected_terms)
        prompt = (
            "Correct grammar, punctuation, capitalization, and sentence structure in the dictated text. "
            "Preserve meaning. Do not add facts. Do not remove requests, names, numbers, paths, URLs, or email addresses. "
            "Tokens beginning ECHOLINKTOKEN are immutable. Return only corrected text.\n\n"
            f"DICTATED TEXT:\n{protected_text}"
        )
        try:
            payload = self._transport.post_json(
                "/api/generate",
                {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                    # Greedy decoding with a fixed seed keeps dictation
                    # refinement repeatable; temperature 0 alone still lets
                    # near-tied logits flip between runs.
                    "options": {
                        "temperature": 0.0,
                        "seed": 1,
                        "top_k": 1,
                        "top_p": 1.0,
                        "repeat_penalty": 1.0,
                        "num_ctx": 4096,
                    },
                },
                timeout=max(2.0, float(timeout)),
            )
        except LocalTransportError as exc:
            return CorrectionResult(
                text,
                "deterministic",
                f"Local refinement was unavailable: {exc}",
            )

        candidate = str(payload.get("response") or "").strip().strip('"')
        if not candidate or "```" in candidate:
            return CorrectionResult(text, "deterministic", "Local refinement returned an invalid response.")
        for token in replacements:
            if token not in candidate:
                return CorrectionResult(text, "deterministic", "Local refinement changed protected content.")
        restored = candidate
        for token, value in replacements.items():
            restored = restored.replace(token, value)
        if not _meaning_guard(text, restored):
            return CorrectionResult(text, "deterministic", "Local refinement was rejected to protect meaning.")
        return CorrectionResult(restored, "ollama")


class CorrectionPipeline:
    def __init__(
        self,
        vocabulary_provider,
        settings: DictationSettings | None = None,
        refiner=None,
        snippet_provider=None,
        learned_correction_provider=None,
    ):
        self._vocabulary_provider = vocabulary_provider
        self._settings = settings or DictationSettings()
        # Fast and Developer tests may inject a sentinel refiner, but an AI
        # path can execute only through the exact accepted coordinator type.
        self._refiner = refiner
        self._ai_coordinator = (
            refiner if isinstance(refiner, AcceptedInferenceCoordinator) else None
        )
        self._snippet_provider = snippet_provider or (lambda: {})
        self._learned_correction_provider = learned_correction_provider or (lambda: {})
        # A configured model name is only a preference. It must not execute
        # until the hardware policy and runtime acceptance probe approve it.
        self._accepted_model = ""

    @property
    def accepted_model(self) -> str:
        return self._accepted_model

    def accept_model(self, model: str) -> None:
        accepted = str(model or "").strip()
        if not accepted:
            raise ValueError("An accepted AI model is required.")
        coordinator = self._ai_coordinator
        lease = coordinator.lease() if coordinator is not None else None
        if (
            lease is None
            or lease.plan.model != accepted
            or not coordinator.is_current(
                lease.plan.pack_id,
                accepted,
                lease.plan.policy_version,
            )
        ):
            raise ValueError(
                "Dictation AI requires the current accepted shared coordinator plan."
            )
        self._accepted_model = accepted

    def clear_accepted_model(self) -> None:
        self._accepted_model = ""

    def set_refiner(self, refiner) -> None:
        if not isinstance(refiner, AcceptedInferenceCoordinator):
            raise TypeError("The accepted shared AI coordinator is required.")
        self._refiner = refiner
        self._ai_coordinator = refiner
        self._accepted_model = ""

    def _accepted_coordinator(self) -> AcceptedInferenceCoordinator | None:
        coordinator = self._ai_coordinator
        model = self._accepted_model
        lease = coordinator.lease() if coordinator is not None else None
        if (
            not model
            or lease is None
            or lease.plan.model != model
            or not coordinator.is_current(
                lease.plan.pack_id,
                model,
                lease.plan.policy_version,
            )
        ):
            return None
        return coordinator

    def update_settings(self, settings: DictationSettings) -> None:
        self._settings = settings

    def preload(self, *, timeout: float) -> bool:
        model = self._accepted_model
        coordinator = self._accepted_coordinator()
        if not model or coordinator is None:
            return False
        coordinator.preload(
            model=model,
            timeout=float(timeout),
        )
        return True

    def deterministic(
        self,
        raw_text: str,
        mode: DictationMode | None = None,
    ) -> CorrectionResult:
        selected_mode = mode or self._settings.mode
        snippet = expand_snippet(raw_text, dict(self._snippet_provider() or {}))
        if snippet is not None:
            if self._settings.append_trailing_space and snippet and not snippet.endswith(" "):
                snippet += " "
            return CorrectionResult(snippet, "snippet")
        vocabulary = dict(self._vocabulary_provider() or {})
        if selected_mode is DictationMode.DEVELOPER:
            text = format_developer_text(raw_text, vocabulary)
            if self._settings.append_trailing_space and text and not text.endswith(" "):
                text += " "
            return CorrectionResult(text, "developer")
        text = deterministic_cleanup(raw_text, vocabulary)
        text = self._apply_learning(text, vocabulary)
        if self._settings.append_trailing_space and text and not text.endswith(" "):
            text += " "
        return CorrectionResult(text, "deterministic")

    def correct(self, raw_text: str, mode: DictationMode | None = None) -> CorrectionResult:
        selected_mode = mode or self._settings.mode
        base = self.deterministic(raw_text, selected_mode)
        if base.source == "snippet":
            return base
        if selected_mode in (DictationMode.FAST, DictationMode.DEVELOPER):
            return base
        if not self._settings.local_refinement_enabled:
            return base
        model = self._accepted_model
        coordinator = self._accepted_coordinator()
        if not model or coordinator is None:
            return CorrectionResult(
                base.text,
                base.source,
                "Local AI is not ready. Deterministic correction was kept.",
            )

        vocabulary = dict(self._vocabulary_provider() or {})
        learned = dict(self._learned_correction_provider() or {})
        refined = coordinator.refine(
            base.text.strip(),
            model=model,
            protected_terms=tuple(vocabulary.values()) + tuple(learned.values()),
            timeout=self._settings.correction_timeout_seconds,
        )
        if refined.source not in {"ollama", "ai"}:
            return CorrectionResult(base.text, base.source, refined.warning)
        final = deterministic_cleanup(refined.text, vocabulary)
        final = self._apply_learning(final, vocabulary)
        if self._settings.append_trailing_space and final and not final.endswith(" "):
            final += " "
        return CorrectionResult(final, refined.source, refined.warning)

    def _apply_learning(self, text: str, vocabulary: dict[str, str]) -> str:
        if not self._settings.personal_learning_enabled:
            return text
        rules = dict(self._learned_correction_provider() or {})
        if not rules:
            return text
        protected, replacements = _protect_text(
            text,
            tuple(vocabulary.keys()) + tuple(vocabulary.values()),
        )
        corrected = apply_learned_corrections(protected, rules)
        for token, value in replacements.items():
            corrected = corrected.replace(token, value)
        return corrected

    def local_model_status(
        self,
        *,
        configured: str | None = None,
        timeout: float = 1.5,
    ) -> dict[str, object]:
        coordinator = self._accepted_coordinator()
        models = (
            coordinator.available_models(timeout=timeout)
            if coordinator is not None
            else set()
        )
        configured = configured or self._accepted_model
        configured_base = configured.split(":", 1)[0]
        available = configured in models or configured_base in {
            item.split(":", 1)[0] for item in models
        }
        return {"configured": configured, "available": available, "models": sorted(models)}


def _protect_text(
    text: str,
    protected_terms: Iterable[str],
    *,
    protect_detected: bool = True,
) -> tuple[str, dict[str, str]]:
    values = [value for value in protected_terms if value]
    if protect_detected:
        values.extend(match.group(0) for match in _PROTECTED_PATTERN.finditer(text))
    unique = sorted(set(values), key=lambda value: (-len(value), value.casefold(), value))
    # Resolve every match against the untouched source. Iterative substitution
    # is unsafe because a later protected number such as "8" can match the
    # digit inside an earlier placeholder such as ECHOLINKTOKEN8X.
    occupied: list[tuple[int, int]] = []
    spans: list[tuple[int, int, str, str]] = []
    replacements: dict[str, str] = {}
    token_index = 0
    for value in unique:
        pattern = _protected_value_pattern(value)
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < right and end > left for left, right in occupied):
                continue
            token = f"ECHOLINKTOKEN{token_index}X"
            token_index += 1
            occupied.append((start, end))
            spans.append((start, end, token, value))
            replacements[token] = value
    result = text
    for start, end, token, _value in sorted(spans, reverse=True):
        result = result[:start] + token + result[end:]
    return result, replacements


def _protected_value_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    left = r"(?<!\w)" if value[:1].isalnum() or value[:1] == "_" else ""
    right = r"(?!\w)" if value[-1:].isalnum() or value[-1:] == "_" else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


def _meaning_guard(source: str, candidate: str) -> bool:
    source_plain = source.strip()
    candidate_plain = candidate.strip()
    if not source_plain or not candidate_plain:
        return False
    ratio = len(candidate_plain) / max(1, len(source_plain))
    if ratio < 0.55 or ratio > 1.75:
        return False
    for protected in _PROTECTED_PATTERN.findall(source_plain):
        if protected not in candidate_plain:
            return False
    source_words = [word.casefold() for word in _SEMANTIC_WORD.findall(source_plain)]
    candidate_words = [word.casefold() for word in _SEMANTIC_WORD.findall(candidate_plain)]
    return SequenceMatcher(None, source_words, candidate_words).ratio() >= 0.58
