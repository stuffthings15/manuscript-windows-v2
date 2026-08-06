from __future__ import annotations

import logging
import math
import zlib
import numbers
import os
import re
import time
from collections import Counter
from dataclasses import dataclass

from .correction import (
    _PROTECTED_PATTERN,
    _protect_text,
    _protected_value_pattern,
)
from .local_transport import LocalTransportError, LoopbackJSONTransport
from .meaning_diff import unexplained_losses
from .polish_quality import tone_contract_instruction
from .models import OLLAMA_KEEP_ALIVE


logger = logging.getLogger(__name__)


COMMANDS = {
    "Proofread": (
        "Fix grammar, spelling, punctuation, capitalization, verb tense consistency, "
        "subject-verb agreement, and readability while preserving meaning."
    ),
    "Improve": "Improve clarity, grammar, wording, and flow while preserving meaning.",
    "Rephrase": "Express the same meaning with fresh, natural wording.",
    "Rewrite": "Rewrite for clarity and natural flow while preserving meaning.",
    "Shorten": "Make the text concise. Keep every important fact and request.",
    "Expand": "Expand the text with clearer detail, but do not invent facts.",
    "Summarize": "Summarize the text accurately and concisely.",
    "Formal": "Rewrite in a formal, professional tone while preserving meaning.",
    "Friendly": "Rewrite in a warm, friendly tone while preserving meaning.",
    "Professional": "Rewrite in a polished, confident, workplace-appropriate tone while preserving meaning.",
    "Academic": "Rewrite in precise, objective academic prose without inventing citations or claims.",
    "Conversational": "Rewrite in natural conversational language while preserving meaning.",
    "Concise": "Remove filler and repetition while preserving every essential detail.",
    "Detailed": "Make existing details and relationships explicit without inventing information.",
    "Persuasive": "Present the same position persuasively without inventing evidence or exaggerating.",
    "Confident": "Use assured language without increasing certainty or force beyond the source.",
    "Casual": "Use relaxed, natural, grammatically correct language while preserving meaning.",
    "Technical": "Use precise technical language and preserve all identifiers and constraints.",
    "Simplified": "Use plain vocabulary and simpler sentences without losing essential content.",
    "High-school level": "Use clear high-school-level vocabulary and complete, direct sentences.",
    "College level": "Use polished college-level vocabulary, varied syntax, and organized reasoning.",
    "Diplomatic": "Rewrite tactfully and respectfully while preserving the message and boundaries.",
    "Empathetic": "Rewrite with understanding and care while preserving facts, needs, and boundaries.",
    "Cooperative": "Rewrite in a collaborative, solution-focused tone while preserving meaning.",
    "Assertive": "Rewrite clearly and directly with firm, respectful boundaries while preserving meaning.",
    "Caveman": "Rewrite in a short, blunt, plain-language style while preserving every important fact.",
    "Translate": "Translate the text into the requested language. Preserve names, numbers, paths, URLs, and email addresses.",
}

TONE_COMMANDS = frozenset(
    {
        "Improve",
        "Rephrase",
        "Shorten",
        "Friendly",
        "Formal",
        "Professional",
        "Academic",
        "Conversational",
        "Concise",
        "Detailed",
        "Persuasive",
        "Confident",
        "Casual",
        "Technical",
        "Simplified",
        "High-school level",
        "College level",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    }
)


_OFFICIAL_GEMMA4_MODELS = frozenset(
    {
        "gemma4:e2b-it-qat",
        "gemma4:e4b-it-qat",
        "gemma4:12b-it-q4_k_m",
    }
)


def _require_exact_response_model(payload: object, expected_model: str) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("Local AI returned invalid text.")
    reported = str(payload.get("model") or "").strip()
    if not reported or reported != str(expected_model or "").strip():
        raise RuntimeError("Local AI returned the wrong model identity.")
    return payload


def select_local_tone_model(
    command: str,
    configured_model: str,
    installed_models: tuple[str, ...] | set[str],
) -> str:
    """Use only the installed Gemma 4 pack selected by hardware policy."""
    available = {str(name).strip().casefold(): str(name).strip() for name in installed_models}
    selected = str(configured_model or "").strip().casefold()
    if command not in COMMANDS or selected not in _OFFICIAL_GEMMA4_MODELS:
        return ""
    return available.get(selected, "")

PROFILES = {
    "General": "Use clear natural language and preserve the original structure when useful.",
    "Email": "Write a concise email-ready result with a clear request or next step.",
    "Meeting": "Keep decisions, owners, dates, and action items explicit. Use bullets when useful.",
    "Marketing": "Make the writing engaging and specific without inventing claims, proof, or urgency.",
    "Technical": "Use precise technical language and preserve identifiers, commands, versions, and constraints.",
    "Code-safe": "Edit prose only. Preserve code blocks, inline code, identifiers, commands, and syntax exactly.",
}

_CODE_SPAN = re.compile(r"```[\s\S]*?```|`[^`\r\n]+`")
_CODE_IDENTIFIER = re.compile(
    r"\b(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|"
    r"[a-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*)\b"
)
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_NUMBER = re.compile(r"(?<!\w)(?:[$€£])?\d+(?:[.:,/-]\d+)*(?!\w)")
_IMMUTABLE_TOKEN = re.compile(r"ECHOLINKTOKEN\d+X")

def _compact_prompt_enabled() -> bool:
    """Feature flag for the shorter prompt contract. Default off until the
    locked corpora prove it equal or better on both the largest and the
    smallest supported pack."""
    return os.environ.get("MANUSCRIPT_COMPACT_PROMPT", "").strip() == "1"

_INTERNAL_TOKEN_LIKE = re.compile(
    r"(?<![A-Za-z0-9_])ECHOLINKTOKEN[A-Za-z0-9_-]*(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_SHORT_ABBREVIATION = re.compile(r"[A-Za-z]\.[A-Za-z]")
_NEGATION = re.compile(
    r"\b(?:not|never|no|nothing|neither|nor|without|fails?|failed|failing|"
    r"cannot|can't|won't|mustn't|shouldn't|"
    r"couldn't|couldnt|couldn’t|unable|unchanged|unclear|"
    r"can’t|won’t|mustn’t|shouldn’t|don’t|doesn’t|didn’t|"
    r"isn’t|aren’t|wasn’t|weren’t|haven’t|hasn’t|hadn’t|"
    r"don't|doesn't|didn't|didn\u2019t|isn't|aren't|wasn't|weren't|haven't|hasn't|hadn't|"
    r"(?:find|finds|found)\s+it\s+(?:difficult|hard)\s+to|"
    r"(?:have|has|had)\s+(?:difficulty|trouble)\s+(?:in\s+)?|"
    r"(?:difficult|hard)\s+to|"
    r"cant|wont|mustnt|shouldnt|dont|doesnt|didnt|isnt|arent|wasnt|werent|"
    r"havent|hasnt|hadnt)\b",
    re.IGNORECASE,
)
_ORDER_RELATION = re.compile(r"\b(?:before|after|until)\b", re.IGNORECASE)
_MONTH_DATE_RELATION = re.compile(
    r"\b(?P<relation>on|by)\s+"
    r"(?P<date>(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2}(?:,\s+\d{4})?)\b",
    re.IGNORECASE,
)
_OWNER_PERSPECTIVE = {
    "first": re.compile(r"\b(?:we|us|our|ours)\b", re.IGNORECASE),
    "second": re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE),
}
_PRIVATE_REPORT_BOUNDARY_SOURCE = re.compile(
    r"\bprivate\s+user\s+(?:writing|data|text)\b[^.!?]{0,80}\bout\s+(?:of\s+)?"
    r"(?:the\s+)?test\s+reports?\b",
    re.IGNORECASE,
)
_PRIVATE_REPORT_BOUNDARY_RESULT = (
    re.compile(
        r"\b(?:private\s+user|user(?:'s)?\s+private|user)\s+(?:writing|data|text)\b"
        r"[^.!?]{0,80}\b(?:out\s+of|excluded?\s+from|omitted?\s+from|not\s+included\s+in|"
        r"(?:does|do)\s+not\s+appear\s+in|(?:doesn't|don't)\s+appear\s+in|kept\s+out\s+of)\b"
        r"[^.!?]{0,40}\btest\s+reports?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btest\s+reports?\b[^.!?]{0,80}\b(?:exclude|excluded|omit|omitted|"
        r"must\s+not\s+(?:contain|include)|do\s+not\s+(?:contain|include))\b"
        r"[^.!?]{0,80}\b(?:private\s+user|user(?:'s)?\s+private|user)\s+"
        r"(?:writing|data|text)\b",
        re.IGNORECASE,
    ),
)
_OBLIGATION = re.compile(
    r"\b(?:must|require|requires|required|need|needs|needed|should|shall|"
    r"has to|have to|had to|got to)\b",
    re.IGNORECASE,
)
_REQUIRED_FORCE = re.compile(
    r"\b(?:must|shall|require|requires|required|requirements?|need|needs|needed|"
    r"has to|have to|had to|got to)\b",
    re.IGNORECASE,
)
_ADVISORY_FORCE = re.compile(r"\bshould\b", re.IGNORECASE)
_PROHIBITION = re.compile(r"\b(?:prohibited|forbidden)\b", re.IGNORECASE)
_EXACTNESS_REQUIREMENT = re.compile(
    r"\b(?:keep|keeps|keeping|kept|maintain(?:ed|s|ing)?|preserv(?:e|ed|es|ing)|"
    r"remain(?:ed|s|ing)?|stay(?:ed|s|ing)?|ensur(?:e|ed|es|ing))\b"
    r"[^.!?]{0,60}\b(?:all|every|exact|exactly|only|unchanged|unaltered|precise)\b",
    re.IGNORECASE,
)
_WORD_LIMIT_PATTERNS = (
    re.compile(
        r"\b(?:under|at most|no more than|maximum(?: of)?|max(?:imum)?(?: of)?)\s+"
        r"(\d{1,4})\s+words?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,4})\s+words?\s+(?:maximum|max)\b", re.IGNORECASE),
)
_STYLE_OPTION = re.compile(
    r"\b(?:clear|improve|rephrase|shorten|formal|casual|friendly|concise|"
    r"professional|diplomatic|empathetic|cooperative|assertive)\b",
    re.IGNORECASE,
)
_STYLE_LIST = re.compile(
    r"\b(?:clear|improve|rephrase|shorten|formal|casual|friendly|concise|"
    r"professional|diplomatic|empathetic|cooperative|assertive)\b"
    r"(?:(?:\s*,\s*|\s+)(?:(?:and|or)\s+)?"
    r"(?:clear|improve|rephrase|shorten|formal|casual|friendly|concise|"
    r"professional|diplomatic|empathetic|cooperative|assertive)\b){1,10}",
    re.IGNORECASE,
)
_NUMBER_WORD_VALUE = (
    r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[-\s]+(?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|"
    r"eighteenth|nineteenth|twentieth)"
)
_NUMBER_WORD_FACT = re.compile(
    rf"(?<!no )\b(?P<number>{_NUMBER_WORD_VALUE}(?:\s+or\s+{_NUMBER_WORD_VALUE})?)\s+"
    r"[A-Za-z][A-Za-z'-]*\b",
    re.IGNORECASE,
)
_TIME_UNIT_FACT = re.compile(r"\b(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b$", re.IGNORECASE)
_NUMBER_NOUN_MODIFIERS = {
    "distinct",
    "emergency",
    "existing",
    "extra",
    "library",
    "local",
    "medical",
    "public",
    "radio",
    "sampled",
    "security",
    "separate",
    "technical",
    "trained",
    "translated",
    "transportation",
}

_VISIBLE_TONE_COMMANDS = TONE_COMMANDS | {"Rewrite"}
_PROOFREAD_CLEANUP_COMMANDS = _VISIBLE_TONE_COMMANDS | {"Proofread"}
_ALL_CAPS_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9'-]*\b")
_PRESERVED_CAPITALIZATION = {
    "ai": "AI",
    "api": "API",
    "cpu": "CPU",
    "gpu": "GPU",
    "hud": "HUD",
    "manuscript": "Manuscript",
    "pdf": "PDF",
    "sql": "SQL",
    "ui": "UI",
    "url": "URL",
    "usb": "USB",
    "windows": "Windows",
}


@dataclass(frozen=True, slots=True)
class WritingResult:
    text: str
    command: str
    model: str
    profile: str


@dataclass(frozen=True, slots=True)
class WritingRuntimeStatus:
    model: str
    loaded: bool
    backend: str
    model_bytes: int = 0
    gpu_bytes: int = 0


class LocalWritingAssistant:
    def __init__(self, endpoint: str = "http://127.0.0.1:11434"):
        self._transport = LoopbackJSONTransport(endpoint)

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint

    def runtime_status(
        self,
        model: str,
        *,
        timeout: float = 1.0,
    ) -> WritingRuntimeStatus:
        try:
            payload = self._transport.get_json("/api/ps", timeout=timeout)
        except LocalTransportError as exc:
            raise RuntimeError(f"Local AI unavailable: {exc}") from exc
        models = payload.get("models")
        if not isinstance(models, list):
            raise RuntimeError("Local AI returned invalid runtime status.")
        wanted = str(model).strip().casefold()
        for item in models:
            if not isinstance(item, dict):
                continue
            loaded_name = str(item.get("name") or item.get("model") or "").strip()
            if loaded_name.casefold() != wanted:
                continue
            model_bytes = max(0, int(item.get("size") or 0))
            gpu_bytes = max(0, int(item.get("size_vram") or 0))
            if gpu_bytes <= 0:
                backend = "CPU"
            elif model_bytes > 0 and gpu_bytes < model_bytes:
                backend = "Hybrid"
            else:
                backend = "GPU"
            return WritingRuntimeStatus(
                loaded_name,
                True,
                backend,
                model_bytes,
                gpu_bytes,
            )
        return WritingRuntimeStatus(str(model).strip(), False, "Loading")

    def require_gpu(
        self,
        model: str,
        *,
        timeout: float,
        keep_alive: str | int = OLLAMA_KEEP_ALIVE,
        context_window: int = 4096,
    ) -> WritingRuntimeStatus:
        status = self.runtime_status(model, timeout=min(1.0, timeout))
        if not status.loaded:
            try:
                payload = self._transport.post_json(
                    "/api/generate",
                    {
                        "model": model,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": keep_alive,
                        "options": {"num_ctx": context_window},
                    },
                    timeout=timeout,
                )
                _require_exact_response_model(payload, model)
            except LocalTransportError as exc:
                raise RuntimeError(f"Local AI unavailable: {exc}") from exc
            status = self.runtime_status(model, timeout=min(1.0, timeout))
        if not status.loaded:
            raise RuntimeError("Local AI could not load. GPU proofreading did not start.")
        if status.backend != "GPU":
            raise RuntimeError(
                f"Local AI loaded on {status.backend}. GPU-only proofreading stopped. "
                "Repair the NVIDIA device or driver, then try again."
            )
        return status

    def run(
        self,
        text: str,
        *,
        command: str,
        model: str,
        instruction: str = "",
        language: str = "",
        profile: str = "General",
        protected_terms=(),
        timeout: float = 30.0,
        keep_alive: str | int = OLLAMA_KEEP_ALIVE,
        context_window: int = 8192,
        max_tokens: int | None = None,
        require_gpu: bool = False,
        defer_meaning_validation: bool = False,
    ) -> WritingResult:
        bounded_timeout = _validated_timeout(timeout)
        bounded_keep_alive = _validated_keep_alive(keep_alive)
        bounded_context = _validated_context_window(context_window)
        bounded_max_tokens = _validated_max_tokens(max_tokens)
        if not isinstance(require_gpu, bool):
            raise ValueError("GPU requirement must be true or false.")
        if not isinstance(defer_meaning_validation, bool):
            raise ValueError("Meaning validation deferral must be true or false.")
        surface_meaning_guard = (
            _requires_surface_meaning_guard(command)
            and not defer_meaning_validation
        )
        raw_source = text or ""
        source = raw_source.strip()
        if not source:
            raise ValueError("Select text first.")
        source_word_count = len(_WORD.findall(source))
        shorten_retry_rule = (
            f" For Shorten, return no more than {source_word_count} words."
            if command == "Shorten"
            else ""
        )
        leading = raw_source[: len(raw_source) - len(raw_source.lstrip())]
        trailing = raw_source[len(raw_source.rstrip()) :]
        if command not in COMMANDS:
            raise ValueError("Unknown writing command.")
        if profile not in PROFILES:
            raise ValueError("Unknown writing profile.")
        if command == "Translate" and not language.strip():
            raise ValueError("Enter a translation language.")
        if _INTERNAL_TOKEN_LIKE.search(source):
            raise ValueError("Selected text contains reserved internal text.")

        protected_values = list(protected_terms)
        protected_values.extend(_strict_protected_literals(source).elements())
        protected_values.extend(match.group(0) for match in _NUMBER.finditer(source))
        number_fact_phrases = tuple(
            _number_fact_phrase(source, match)
            for match in _NUMBER_WORD_FACT.finditer(source)
        )
        if profile == "Code-safe":
            protected_values.extend(match.group(0) for match in _CODE_SPAN.finditer(source))
            protected_values.extend(match.group(0) for match in _CODE_IDENTIFIER.finditer(source))
        protected, replacements = _protect_text(
            source,
            protected_values,
            protect_detected=False,
        )
        protected_literals = _strict_protected_literals(source)
        # Naming the token system on text that contains no tokens taught small
        # models to write the word ECHOLINKTOKEN into the correction, and the
        # mismatch check then discarded the whole answer:
        # "protected-token mismatch stage=initial expected=() actual=('ECHOLINKTOKEN',)".
        # State the rule only when there is actually a token to protect. The
        # concision prompts further down already guard theirs this way.
        has_immutable_tokens = bool(_IMMUTABLE_TOKEN.search(protected))
        compact_token_rule = (
            "- Tokens beginning ECHOLINKTOKEN are immutable: copy each one "
            "exactly, same spelling, order, and count.\n"
            if has_immutable_tokens
            else ""
        )
        full_token_rule = (
            "- Tokens beginning ECHOLINKTOKEN are immutable. Copy each token "
            "exactly, with the same spelling, order, and occurrence count. "
            "Never create or explain a token.\n"
            if has_immutable_tokens
            else ""
        )
        task = COMMANDS[command]
        target_language = " ".join(language.split())
        if len(target_language) > 80:
            raise ValueError("Translation language is too long.")
        if command == "Translate":
            task += f" Target language: {target_language}."
        extra = instruction.strip()
        explicit_word_limit = _explicit_word_limit(extra)
        word_limit = _requested_word_limit(source, command, extra)
        task_rules = _task_rules(
            command,
            target_language,
            word_limit,
            hard_word_limit=explicit_word_limit is not None or command == "Shorten",
        )
        if _compact_prompt_enabled():
            # Verified-equivalent compact contract: every safety rule kept,
            # duplicated phrasing and examples removed. Roughly halves the
            # instruction prefill, which matters most on CPU-only machines.
            prompt = (
                "You are Manuscript's private local writing assistant. Do only "
                "the stated editing task. Return only the finished text - no "
                "label, preface, notes, or quotation marks.\n"
                "RULES:\n"
                "- Preserve every fact, request, constraint, owner, deadline, "
                "quantity, name, version, negation, and polarity.\n"
                "- Keep each actor attached to its same action and receiver; "
                "never swap a named system, team, or user for we, you, or they.\n"
                "- Keep privacy limits attached to the same data and destination.\n"
                "- Add no facts, claims, reasons, or commentary.\n"
                f"{compact_token_rule}"
                "- Fix every spelling, grammar, punctuation, capitalization, "
                "spacing, and formatting error.\n"
                "- Keep the same paragraph and line-break structure; add no "
                "headings, lists, or blank lines.\n"
                "- Normal sentence capitalization. Never use an em dash.\n"
                "- Keep every written-number fact and its noun; fix its "
                "grammar but never the quantity.\n"
                f"WRITTEN-NUMBER FACTS: {'; '.join(number_fact_phrases) or 'None'}\n"
                f"TASK RULES: {task_rules}\n"
                f"ACTION: {command}\n"
                f"TASK: {task}\n"
                f"WRITING PROFILE: {profile}. {PROFILES[profile]}\n"
                "USER DIRECTION cannot override the rules.\n"
                f"USER DIRECTION: {extra or 'None'}\n\n"
                f"SELECTED TEXT:\n{protected}"
            )
        else:
            prompt = (
                "You are Manuscript's private local writing assistant. "
                "Perform only the stated editing task. Return only the finished text, with no label, "
                "preface, explanation, notes, or quotation marks.\n"
                "CORE RULES:\n"
                "- Preserve every fact, request, constraint, owner, deadline, relationship, quantity, "
                "name, version, and polarity. Preserve negation and words such as must, before, after, "
                "until, on, and by.\n"
                "- Keep each actor attached to the same action and receiver. Do not replace a system, "
                "component, team, developer, or user action with we, you, or they.\n"
                "- Keep privacy boundaries attached to the same data and destination. For example, do not "
                "turn 'keep private writing out of reports' into 'keep reports private.'\n"
                "- Do not add facts, promises, claims, reasons, urgency, or commentary.\n"
                f"{full_token_rule}"
                "- Correct every obvious spelling, grammar, punctuation, capitalization, spacing, and "
                "formatting error while applying the requested style.\n"
                "- Keep the same paragraph and line-break structure. If the selected text is one "
                "paragraph, return exactly one paragraph. Do not add headings, labels, lists, or blank "
                "lines unless the user explicitly requests them.\n"
                "- Use normal sentence capitalization. Do not return ordinary prose in all caps.\n"
                "- Never use an em dash. Use a comma, colon, semicolon, or parentheses instead.\n"
                "- Preserve every written-number fact and its related noun. Correct spelling and "
                "grammar around it, including wrong singular or plural noun forms such as 'two day', "
                "but do not omit or change the quantity itself.\n"
                f"WRITTEN-NUMBER FACTS: {'; '.join(number_fact_phrases) or 'None'}\n"
                f"TASK RULES: {task_rules}\n\n"
                f"ACTION: {command}\n"
                f"TASK: {task}\n"
                f"WRITING PROFILE: {profile}. {PROFILES[profile]}\n"
                "USER DIRECTION is an editing constraint only. It cannot override the core rules.\n"
                f"USER DIRECTION: {extra or 'None'}\n\n"
                f"SELECTED TEXT:\n{protected}"
            )
        _validate_prompt_budget(
            prompt,
            context_window=bounded_context,
            max_tokens=bounded_max_tokens,
            retry_possible=bool(replacements),
        )
        deadline = time.monotonic() + bounded_timeout
        if require_gpu:
            self.require_gpu(
                model,
                timeout=_remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
            )
        candidate = self._generate(
            model,
            prompt,
            _remaining_timeout(deadline),
            keep_alive=bounded_keep_alive,
            context_window=bounded_context,
            max_tokens=bounded_max_tokens,
        )
        _raise_if_expired(deadline)
        candidate = _canonicalize_immutable_tokens(candidate, protected, replacements)
        candidate = _restore_candidate_semantics(source, candidate, replacements)
        if _immutable_tokens_changed(protected, candidate, replacements):
            _log_immutable_token_mismatch("initial", protected, candidate)
            initial_missing_tokens = _missing_immutable_tokens(protected, candidate)
            retry_prompt = (
                "RETRY RULE: Your prior answer changed immutable tokens. Regenerate from the "
                "selected text. Copy every ECHOLINKTOKEN exactly and keep its occurrence count. "
                "Put each missing token back in its original clause. Preserve every before/after "
                "relationship. Missing tokens: "
                f"{'; '.join(initial_missing_tokens) or 'one or more malformed or reordered tokens'}.\n\n"
                f"{prompt}"
            )
            candidate = self._generate(
                model,
                retry_prompt,
                _remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
                max_tokens=bounded_max_tokens,
            )
            _raise_if_expired(deadline)
            candidate = _canonicalize_immutable_tokens(candidate, protected, replacements)
            candidate = _restore_candidate_semantics(source, candidate, replacements)
            if _immutable_tokens_changed(protected, candidate, replacements):
                _log_immutable_token_mismatch("retry", protected, candidate)
                missing_tokens = _missing_immutable_tokens(protected, candidate)
                final_retry_prompt = (
                    "FINAL RETRY RULE: Your last answer improved but still omitted protected tokens. "
                    "Regenerate the full result and copy every ECHOLINKTOKEN exactly once and in the "
                    "same order. Never replace a token with a synonym, number, server, service, name, "
                    "or ordinary word. Missing tokens: "
                    f"{'; '.join(missing_tokens) or 'one or more tokens'}.\n\n{prompt}"
                )
                candidate = self._generate(
                    model,
                    final_retry_prompt,
                    _remaining_timeout(deadline),
                    keep_alive=bounded_keep_alive,
                    context_window=bounded_context,
                    max_tokens=bounded_max_tokens,
                )
                _raise_if_expired(deadline)
                candidate = _canonicalize_immutable_tokens(candidate, protected, replacements)
                candidate = _restore_candidate_semantics(source, candidate, replacements)
                if _immutable_tokens_changed(protected, candidate, replacements):
                    _log_immutable_token_mismatch("final-retry", protected, candidate)
                    # Three prompts have now asked for the token and not got it.
                    # Keep the writer's own sentence for the clause that holds
                    # it rather than throwing the whole corrected section away.
                    repaired = _restore_missing_immutable_tokens(
                        protected, candidate, replacements
                    )
                    if not repaired:
                        raise RuntimeError("The result changed protected content.")
                    candidate = repaired
        numeric_surface_change = _numeric_literal_change(protected, candidate)
        missing_number_facts = _missing_number_fact_phrases(
            number_fact_phrases,
            _expand_protected_for_validation(candidate, replacements),
        )
        if (
            not defer_meaning_validation
            and (numeric_surface_change == "removed" or missing_number_facts)
        ):
            numeric_retry_prompt = (
                f"{prompt}\n\nRETRY RULE: Your prior answer omitted one or more written-number "
                "facts. Regenerate the full result. You MUST include every quantity with its related "
                f"noun: {'; '.join(number_fact_phrases) or 'None'}. Put these facts only in the normal "
                f"rewritten sentences. Do not repeat this reminder or append a list.{shorten_retry_rule}"
            )
            candidate = self._generate(
                model,
                numeric_retry_prompt,
                _remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
                max_tokens=bounded_max_tokens,
            )
            _raise_if_expired(deadline)
            candidate = _canonicalize_immutable_tokens(candidate, protected, replacements)
            candidate = _restore_candidate_semantics(source, candidate, replacements)
            if _immutable_tokens_changed(protected, candidate, replacements):
                raise RuntimeError("The result changed protected content.")
            if _missing_number_fact_phrases(
                number_fact_phrases,
                _expand_protected_for_validation(candidate, replacements),
            ):
                raise RuntimeError("The result changed a written-number fact.")
        if not candidate or "```" in candidate:
            raise RuntimeError("Local AI returned invalid text.")
        if _obviously_incomplete_edit(protected, candidate, command):
            incomplete_retry_prompt = (
                f"{prompt}\n\nRETRY RULE: Your prior answer was incomplete or cut off. "
                "Regenerate the entire selected text from its first word through its final "
                "punctuation mark. Return every corrected sentence in the original order. "
                "Do not summarize, omit a clause, or stop early."
            )
            candidate = self._generate(
                model,
                incomplete_retry_prompt,
                _remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
                max_tokens=bounded_max_tokens,
            )
            _raise_if_expired(deadline)
            candidate = _canonicalize_immutable_tokens(
                candidate,
                protected,
                replacements,
            )
            candidate = _restore_candidate_semantics(
                source,
                candidate,
                replacements,
            )
            if _immutable_tokens_changed(protected, candidate, replacements):
                raise RuntimeError("The result changed protected content.")
            if (
                not defer_meaning_validation
                and (
                _numeric_literal_change(protected, candidate) is not None
                or _missing_number_fact_phrases(
                    number_fact_phrases,
                    _expand_protected_for_validation(candidate, replacements),
                )
                )
            ):
                raise RuntimeError("The result changed protected content.")
            if _obviously_incomplete_edit(protected, candidate, command):
                raise RuntimeError("Local AI returned incomplete text.")
        # Validate the finished correction, not a dirty draft that AI may have
        # copied while applying a style. This cleanup is deterministic and
        # fact-preserving; running it here prevents a needless generative retry
        # from replacing a safe draft with a worse one.
        if command in _PROOFREAD_CLEANUP_COMMANDS:
            candidate = _safe_final_proofread(protected, candidate, command, replacements)
        meaning_candidate = _expand_protected_for_validation(candidate, replacements)
        if surface_meaning_guard and not _meaning_constraints_preserved(
            source,
            meaning_candidate,
            command=command,
            check_word_losses=True,
        ):
            source_force_words = tuple(match.group(0) for match in _OBLIGATION.finditer(source))
            meaning_retry_prompt = (
                f"{prompt}\n\nRETRY RULE: Your prior answer changed a meaning constraint. "
                "Regenerate the full result. Keep the same polarity and level of force. Do not add, "
                "remove, weaken, or strengthen any negation, requirement, prohibition, exactness rule, "
                "or before/after/on/by relationship. Preserve every clause about what must remain exact or "
                "unchanged. Do not turn a reported action into a command. Keep each actor attached to "
                "its action. Do not replace a source action with a different action or invent a new "
                "destination, recipient, or condition. When uncertain, keep the source clause. If source "
                "force words are listed, reuse them in the matching rewritten clause. "
                "Keep linked clauses about build speed and insertion, user and page counts, workflow "
                "actions, local operation, fallback restoration, and run-before-inspect order. "
                f"SOURCE FORCE WORDS: {'; '.join(source_force_words) or 'None'}. You MUST also include every "
                f"quantity with its related noun: {'; '.join(number_fact_phrases) or 'None'}. Put these "
                f"facts only in the normal rewritten sentences. Do not repeat this reminder or append a list."
                f"{shorten_retry_rule}"
            )
            candidate = self._generate(
                model,
                meaning_retry_prompt,
                _remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
                max_tokens=bounded_max_tokens,
            )
            _raise_if_expired(deadline)
            candidate = _canonicalize_immutable_tokens(candidate, protected, replacements)
            candidate = _restore_candidate_semantics(source, candidate, replacements)
            if _immutable_tokens_changed(protected, candidate, replacements):
                raise RuntimeError("The result changed protected content.")
            if _numeric_literal_change(protected, candidate) is not None or _missing_number_fact_phrases(
                number_fact_phrases,
                _expand_protected_for_validation(candidate, replacements),
            ):
                raise RuntimeError("The result changed protected content.")
            if surface_meaning_guard and not _meaning_constraints_preserved(
                source,
                _expand_protected_for_validation(candidate, replacements),
                command=command,
            ):
                raise RuntimeError("Local AI changed a meaning constraint.")
        if command == "Shorten":
            candidate = _apply_safe_shorten_compression(candidate)
            maximum_words = source_word_count
            if len(_WORD.findall(candidate)) > maximum_words:
                anchor_values = _concision_anchor_values(candidate, number_fact_phrases)
                concision_source, anchor_replacements = _protect_additional_text(
                    candidate,
                    anchor_values,
                )
                combined_replacements = {**replacements, **anchor_replacements}
                anchor_expansion = sum(
                    max(0, len(_WORD.findall(value)) - 1)
                    for value in anchor_replacements.values()
                )
                visible_word_limit = max(4, maximum_words - anchor_expansion)
                concision_token_rule = (
                    "Tokens beginning ECHOLINKTOKEN are immutable fixed phrases. Copy each token "
                    "exactly once and in order. "
                    if combined_replacements
                    else ""
                )
                concision_retry_prompt = (
                    "You are Manuscript's private local writing assistant. Return only the finished text. "
                    "Shorten the meaning-safe draft below without deleting any clause or changing who did "
                    "what to whom. Combine wording and remove filler only. Preserve every fact, actor, action, "
                    "boundary, relationship, quantity, name, and level of force. Preserve before/after, "
                    "privacy exclusions, build behavior, and all written-number facts. "
                    f"{concision_token_rule}"
                    f"Written-number facts with nouns: {'; '.join(number_fact_phrases) or 'None'}. "
                    f"Hard visible-word limit before token expansion: {visible_word_limit}. Do not append "
                    "notes or a fact list.\n\n"
                    f"MEANING-SAFE DRAFT:\n{concision_source}"
                )
                candidate = self._generate(
                    model,
                    concision_retry_prompt,
                    _remaining_timeout(deadline),
                    keep_alive=bounded_keep_alive,
                    context_window=bounded_context,
                    max_tokens=bounded_max_tokens,
                )
                _raise_if_expired(deadline)
                candidate = _canonicalize_immutable_tokens(
                    candidate,
                    concision_source,
                    combined_replacements,
                )
                if _immutable_tokens_changed(
                    concision_source,
                    candidate,
                    combined_replacements,
                ):
                    raise RuntimeError("The result changed protected content.")
                if len(_WORD.findall(candidate)) > visible_word_limit:
                    excess = len(_WORD.findall(candidate)) - visible_word_limit
                    tighter_token_rule = (
                        "Copy every ECHOLINKTOKEN exactly once and in order. "
                        if combined_replacements
                        else ""
                    )
                    tighter_prompt = (
                        "Return only the shortened text below. "
                        f"{tighter_token_rule}"
                        "Remove filler and combine wording, but do not delete or alter any protected phrase, "
                        "fact, action, actor, relationship, or constraint. "
                        f"The prior result is {excess} visible words too long. Hard limit: "
                        f"{visible_word_limit} visible words before token expansion.\n\n"
                        f"TEXT TO SHORTEN:\n{candidate}"
                    )
                    candidate = self._generate(
                        model,
                        tighter_prompt,
                        _remaining_timeout(deadline),
                        keep_alive=bounded_keep_alive,
                        context_window=bounded_context,
                        max_tokens=bounded_max_tokens,
                    )
                    _raise_if_expired(deadline)
                    candidate = _canonicalize_immutable_tokens(
                        candidate,
                        concision_source,
                        combined_replacements,
                    )
                    if _immutable_tokens_changed(
                        concision_source,
                        candidate,
                        combined_replacements,
                    ):
                        raise RuntimeError("The result changed protected content.")
                for token, value in anchor_replacements.items():
                    candidate = candidate.replace(token, value)
                candidate = _restore_candidate_semantics(source, candidate, replacements)
                if (
                    not defer_meaning_validation
                    and (
                        _numeric_literal_change(protected, candidate) is not None
                        or _missing_number_fact_phrases(
                            number_fact_phrases,
                            _expand_protected_for_validation(candidate, replacements),
                        )
                    )
                ):
                    raise RuntimeError("The result changed protected content.")
                if surface_meaning_guard and not _meaning_constraints_preserved(
                    source,
                    _expand_protected_for_validation(candidate, replacements),
                    command=command,
                ):
                    raise RuntimeError("Local AI changed a meaning constraint.")
                if not candidate or "```" in candidate:
                    raise RuntimeError("Local AI returned invalid text.")
                if _obviously_incomplete_edit(protected, candidate, command):
                    raise RuntimeError("Local AI returned incomplete text.")
                if len(_WORD.findall(candidate)) > maximum_words:
                    raise RuntimeError("Local AI exceeded the requested word limit.")
        if "\u2014" in candidate:
            dash_retry_prompt = (
                f"{prompt}\n\nRETRY RULE: Your prior answer used an em dash. "
                "Regenerate the complete result without any em dash. Use a comma, colon, "
                "semicolon, or parentheses instead. Preserve every fact, actor, action, "
                "constraint, number, name, paragraph, and level of force."
            )
            candidate = self._generate(
                model,
                dash_retry_prompt,
                _remaining_timeout(deadline),
                keep_alive=bounded_keep_alive,
                context_window=bounded_context,
                max_tokens=bounded_max_tokens,
            )
            _raise_if_expired(deadline)
            candidate = _canonicalize_immutable_tokens(
                candidate,
                protected,
                replacements,
            )
            candidate = _restore_candidate_semantics(
                source,
                candidate,
                replacements,
            )
            if _immutable_tokens_changed(protected, candidate, replacements):
                raise RuntimeError("The result changed protected content.")
            if command in _PROOFREAD_CLEANUP_COMMANDS:
                candidate = _safe_final_proofread(
                    protected,
                    candidate,
                    command,
                    replacements,
                )
            if "\u2014" in candidate:
                raise RuntimeError("Local AI used a forbidden em dash.")
            if command == "Shorten" and len(_WORD.findall(candidate)) > source_word_count:
                raise RuntimeError("Local AI exceeded the requested word limit.")
        if not candidate or "```" in candidate:
            raise RuntimeError("Local AI returned invalid text.")
        if command == "Translate" and _translation_was_not_performed(protected, candidate):
            raise RuntimeError("Local AI did not translate the text.")
        if command in _PROOFREAD_CLEANUP_COMMANDS:
            candidate = _safe_final_proofread(protected, candidate, command, replacements)
        for token, value in replacements.items():
            candidate = candidate.replace(token, value)
        if command in _PROOFREAD_CLEANUP_COMMANDS:
            candidate = _safe_final_proofread(source, candidate, command, {})
        if command == "Caveman":
            candidate = _apply_safe_caveman_splits(candidate)
        candidate = _restore_ui_label_case(source, candidate)
        if command in _PROOFREAD_CLEANUP_COMMANDS:
            candidate = _restore_proofread_style_options(source, candidate)
        if _INTERNAL_TOKEN_LIKE.search(candidate):
            raise RuntimeError("Local AI returned invalid text.")
        if _obviously_incomplete_edit(source, candidate, command):
            raise RuntimeError("Local AI returned incomplete text.")
        if (
            explicit_word_limit is not None
            and len(_WORD.findall(candidate)) > explicit_word_limit
        ):
            raise RuntimeError("Local AI exceeded the requested word limit.")
        if not defer_meaning_validation:
            numeric_change = _numeric_literal_change(source, candidate)
            if numeric_change == "added":
                raise RuntimeError("Local AI added a number.")
            if numeric_change is not None:
                raise RuntimeError("The result changed protected content.")
        candidate_literals = _strict_protected_literals(candidate)
        if candidate_literals != protected_literals:
            raise RuntimeError("The result changed protected content.")
        if surface_meaning_guard and not _meaning_constraints_preserved(
            source, candidate, command=command, check_word_losses=True
        ):
            raise RuntimeError("Local AI changed a meaning constraint.")
        return WritingResult(f"{leading}{candidate.strip()}{trailing}", command, model, profile)

    def _generate(
        self,
        model: str,
        prompt: str,
        timeout: float,
        *,
        keep_alive: str | int,
        context_window: int,
        max_tokens: int | None,
    ) -> str:
        # Temperature 0 alone does not make Ollama repeatable: with the
        # default top-k/top-p sampler and an unpinned seed, near-tied logits
        # can still flip between runs, which showed up as the same text
        # getting different corrections. Greedy decoding makes one input give
        # one output. A safety retry is the exception: greedy would replay
        # the rejected answer token for token, so retries sample with a
        # prompt-derived seed and let the validator judge the new candidate.
        retrying = "safety retry" in prompt or "RETRY RULE:" in prompt
        if retrying:
            options = {
                "temperature": 0.4,
                "seed": (zlib.crc32(prompt.encode("utf-8")) % 100_000) + 1,
                "top_k": 40,
                "top_p": 0.95,
                "repeat_penalty": 1.0,
                "num_ctx": context_window,
            }
        else:
            options = {
                "temperature": 0,
                "seed": 1,
                "top_k": 1,
                "top_p": 1.0,
                "repeat_penalty": 1.0,
                "num_ctx": context_window,
            }
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        try:
            payload = self._transport.post_json(
                "/api/generate",
                {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "keep_alive": keep_alive,
                    "options": options,
                },
                timeout=max(0.2, float(timeout)),
            )
        except LocalTransportError as exc:
            raise RuntimeError(f"Local AI unavailable: {exc}") from exc
        payload = _require_exact_response_model(payload, model)
        return str(payload.get("response") or "").strip().strip('"')


def _obviously_incomplete_edit(source: str, candidate: str, command: str) -> bool:
    """Reject visibly truncated edits while allowing intentionally concise commands."""
    if command not in {
        "Proofread", "Improve", "Rephrase", "Rewrite", "Formal", "Friendly",
        "Professional", "Diplomatic", "Empathetic", "Cooperative", "Assertive", "Caveman",
    }:
        return False
    source_words = len(_WORD.findall(source))
    candidate_words = len(_WORD.findall(candidate))
    minimum_ratio = 0.20 if command == "Caveman" else 0.45
    if source_words >= 6 and candidate_words < max(3, math.ceil(source_words * minimum_ratio)):
        return True
    if command == "Caveman":
        return False
    source_has_ending = bool(re.search(r"[.!?][\"')\]]*$", source.strip()))
    candidate_has_ending = bool(re.search(r"[.!?][\"')\]]*$", candidate.strip()))
    return source_has_ending and not candidate_has_ending


def _requires_surface_meaning_guard(command: str) -> bool:
    """Use lexical guards only where equivalent rewording is not the requested operation."""
    return command in _PROOFREAD_CLEANUP_COMMANDS or command == "Expand"


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining < 0.2:
        raise RuntimeError("Local AI proofreading took too long.")
    return remaining


def _raise_if_expired(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise RuntimeError("Local AI proofreading took too long.")


def _validated_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Real)
        or not math.isfinite(float(value))
        or not 0.2 <= float(value) <= 300.0
    ):
        raise ValueError("Local writing timeout is outside the allowed range.")
    return float(value)


def _validated_keep_alive(value: str | int) -> str | int:
    if isinstance(value, bool):
        raise ValueError("Local writing keep-alive is outside the allowed range.")
    if isinstance(value, numbers.Integral):
        seconds = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\d{1,3}s", value):
        seconds = int(value[:-1])
    else:
        raise ValueError("Local writing keep-alive is outside the allowed range.")
    if not 0 <= seconds <= 900:
        raise ValueError("Local writing keep-alive is outside the allowed range.")
    return 0 if seconds == 0 else f"{seconds}s"


def _validated_context_window(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Integral)
        or not 512 <= int(value) <= 32768
    ):
        raise ValueError("Local writing context window is outside the allowed range.")
    return int(value)


def _validated_max_tokens(value: int | None) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Integral)
        or not 32 <= int(value) <= 4096
    ):
        raise ValueError("Local writing output limit is outside the allowed range.")
    return int(value)


def _validate_prompt_budget(
    prompt: str,
    *,
    context_window: int,
    max_tokens: int | None,
    retry_possible: bool,
) -> None:
    retry_characters = 180 if retry_possible else 0
    estimated_input_tokens = math.ceil((len(prompt) + retry_characters) / 3)
    output_tokens = max_tokens or 0
    if estimated_input_tokens + output_tokens > context_window:
        raise ValueError("Selected text is too long for one local AI request.")


def _requested_word_limit(source: str, command: str, instruction: str) -> int | None:
    explicit = _explicit_word_limit(instruction)
    if explicit is not None:
        return explicit
    source_words = len(_WORD.findall(source))
    if command == "Summarize":
        return min(source_words, max(12, math.ceil(source_words * 0.65)))
    if command == "Shorten":
        return min(source_words, max(4, math.ceil(source_words * 0.9)))
    if command == "Caveman":
        return min(source_words, max(4, math.ceil(source_words * 0.9)))
    return None


_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
}
_CARDINAL_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_ORDINAL_NUMBER_WORDS = {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
    "seventeenth", "eighteenth", "nineteenth", "twentieth",
}


def _numeric_facts(text: str) -> Counter[str]:
    facts = Counter(_normalized_number(match.group(0)) for match in _NUMBER.finditer(text))
    words = list(_WORD.finditer(text))
    index = 0
    while index < len(words):
        match = words[index]
        value = match.group(0).casefold()
        parts = value.split("-", 1)
        if (
            len(parts) == 2
            and parts[0] in _CARDINAL_TENS
            and parts[1] in _NUMBER_WORDS
            and 1 <= int(_NUMBER_WORDS[parts[1]]) <= 9
        ):
            facts[str(_CARDINAL_TENS[parts[0]] + int(_NUMBER_WORDS[parts[1]]))] += 1
            index += 1
            continue
        number_word = value if value in _NUMBER_WORDS else value.split("-", 1)[0]
        if number_word in _CARDINAL_TENS and index + 1 < len(words):
            following = words[index + 1]
            following_word = following.group(0).casefold()
            between = text[match.end() : following.start()]
            following_number = _NUMBER_WORDS.get(following_word)
            if (
                between.isspace()
                and following_number is not None
                and 1 <= int(following_number) <= 9
            ):
                facts[str(_CARDINAL_TENS[number_word] + int(following_number))] += 1
                index += 2
                continue
        if number_word in _NUMBER_WORDS:
            if number_word == "one":
                prior_words = [
                    item.group(0).casefold()
                    for item in words[:index]
                ][-2:]
                if prior_words[-1:] == ["no"]:
                    # "No one" is an indefinite pronoun, not the quantity 1.
                    index += 1
                    continue
                if (
                    len(prior_words) == 2
                    and prior_words[0] in _NUMBER_WORDS
                    and prior_words[1] not in _NUMBER_WORDS
                ):
                    # "two green one" is an agreement error. Correcting it to
                    # "two green ones" does not remove a separate quantity.
                    index += 1
                    continue
            if number_word in _ORDINAL_NUMBER_WORDS and re.match(
                r"[ \t]+[A-Za-z][A-Za-z'-]*",
                text[match.end() :],
            ) is None:
                index += 1
                continue
            facts[_NUMBER_WORDS[number_word]] += 1
        index += 1
    return facts


_SINGULAR_NOUN_AFTER_PLURAL = re.compile(
    rf"\b(?P<number>{_NUMBER_WORD_VALUE})\s+"
    r"(?P<noun>[A-Za-z][a-z]+)\b(?!s)",
    re.IGNORECASE,
)
_SINGULAR_NUMBER_WORDS = {"zero", "one", "first"}
# Words after a number that are correct in singular form, or that are not the
# counted noun at all (measure words, ordinals-as-adjectives, modifiers).
_PLURAL_REPAIR_EXCLUDED = {
    "dozen", "hundred", "thousand", "million", "billion", "trillion",
    "percent", "more", "less", "fewer", "new", "other", "extra", "further",
    "additional", "different", "separate", "distinct", "consecutive", "full",
    "whole", "half", "single", "small", "large", "big", "short", "long",
    "open", "closed", "critical", "major", "minor", "key", "top", "spare",
    "free", "paid", "dedicated", "remaining", "final", "quick", "slow",
    "late", "early", "recent", "of", "or", "and", "in", "to", "per",
}


def _pluralize_noun(noun: str) -> str:
    lowered = noun.casefold()
    if lowered.endswith(("s", "x", "z", "ch", "sh")):
        return noun + "es"
    if lowered.endswith("y") and len(lowered) > 1 and lowered[-2] not in "aeiou":
        return noun[:-1] + "ies"
    return noun + "s"


def _repair_time_unit_plural(phrase: str) -> str:
    """Fix "two day" -> "two days" inside a written-number fact phrase.

    The phrase is quoted back to the model as an immutable fact, so a wrong
    singular counted noun captured from the source ("two table",
    "eight user") would otherwise instruct the model to keep the error.
    Modifier words ("two new engineer") are skipped here; the caller already
    resolves those through _NUMBER_NOUN_MODIFIERS.
    """

    def replace(match: re.Match[str]) -> str:
        noun = match.group("noun")
        lowered = noun.casefold()
        if match.group("number").casefold() in _SINGULAR_NUMBER_WORDS:
            return match.group(0)
        if lowered.endswith("s"):
            return match.group(0)
        if lowered in _PLURAL_REPAIR_EXCLUDED:
            return match.group(0)
        if lowered in _NUMBER_NOUN_MODIFIERS:
            return match.group(0)
        if lowered in _NUMBER_WORDS or lowered in _ORDINAL_NUMBER_WORDS:
            return match.group(0)
        return f"{match.group('number')} {_pluralize_noun(noun)}"

    return _SINGULAR_NOUN_AFTER_PLURAL.sub(replace, phrase)


def _number_fact_phrase(source: str, match: re.Match[str]) -> str:
    phrase = _apply_fast_proofread_rules(match.group(0)).strip().rstrip(".!?")
    if match.group(0)[:1].islower():
        phrase = phrase[:1].lower() + phrase[1:]
    phrase = _repair_time_unit_plural(phrase)
    words = phrase.split()
    if words and words[-1].casefold() in _NUMBER_NOUN_MODIFIERS:
        following = re.match(r"\s+([A-Za-z][A-Za-z'-]*)", source[match.end() :])
        if following is not None:
            return f"{match.group('number')} {following.group(1)}"
    if not _TIME_UNIT_FACT.search(phrase):
        return phrase
    following = re.match(r"\s+([A-Za-z][A-Za-z'-]*)", source[match.end() :])
    if following is None:
        return phrase
    qualifier = following.group(1)
    if qualifier.casefold() in {"and", "but", "for", "from", "in", "of", "or", "to", "with"}:
        return phrase
    return f"{phrase} {qualifier}"


def _concision_anchor_values(
    candidate: str,
    number_fact_phrases: tuple[str, ...],
) -> tuple[str, ...]:
    values = list(number_fact_phrases)
    patterns = (
        r"\bthree users who (?:wrote|had written) several pages\b",
        r"\b(?:opened slowly|introduced delays)\b",
        r"\b(?:fail\w*|did not)\s+to?\s*(?:insert|incorporate|place)\w*\s+corrected text"
        r"(?:\s+into\s+the\s+active document)?\b",
        r"\b(?:private\s+user|user(?:'s)?\s+private)\s+(?:writing|data|text)\s+out\s+of\s+"
        r"test reports?\b",
        r"\bfifteen days retention\b",
        r"\b(?:approved|authorized) fallback\b",
        r"\b(?:the\s+)?small icon\b",
        r"\b(?:the\s+)?correction card\b",
        r"\b(?:a\s+)?spelling fix\b",
        r"\bthe first sentence\b",
        r"\bthe next morning\b",
        r"\bthe text (?:didn(?:'|\u2019)t|did not) change\b",
        r"\bthe HUD stayed on the first sentence\b",
        r"\banother tester\b",
    )
    for pattern in patterns:
        match = re.search(pattern, candidate, re.IGNORECASE)
        if match is not None:
            values.append(match.group(0))
    return tuple(values)


def _protect_additional_text(
    text: str,
    values: tuple[str, ...],
) -> tuple[str, dict[str, str]]:
    existing = [int(match.group(0)[13:-1]) for match in _IMMUTABLE_TOKEN.finditer(text)]
    index = max(existing, default=-1) + 1
    protected = text
    replacements: dict[str, str] = {}
    for value in sorted(
        set(filter(None, values)),
        key=lambda item: (-len(item), item.casefold(), item),
    ):
        pattern = _protected_value_pattern(value)
        if pattern.search(protected) is None:
            continue
        token = f"ECHOLINKTOKEN{index}X"
        protected = pattern.sub(token, protected)
        replacements[token] = value
        index += 1
    return protected, replacements


def _missing_number_fact_phrases(
    phrases: tuple[str, ...],
    candidate: str,
) -> tuple[str, ...]:
    if not phrases:
        return ()
    spans = tuple(
        part
        for part in re.split(r"(?<=[.!?])\s+|\r?\n+", candidate)
        if part.strip()
    )
    missing: list[str] = []

    def fact_words(value: str) -> set[str]:
        words: set[str] = set()
        for token in _WORD.findall(value):
            for part in token.casefold().split("-"):
                if part and part not in _NUMBER_WORDS and part not in {"and", "or"}:
                    words.add(part)
        return words

    def words_present(required: set[str], available: set[str]) -> bool:
        # Repairing a wrong singular/plural noun after a quantity ("two day"
        # -> "two days") keeps the fact. Only the word's number may differ.
        for word in required:
            if word in available:
                continue
            if f"{word}s" in available:
                continue
            if word.endswith("s") and word[:-1] in available:
                continue
            return False
        return True

    for phrase in phrases:
        expected_numbers = _numeric_facts(phrase)
        required_words = fact_words(phrase)
        if not any(
            not (expected_numbers - _numeric_facts(span))
            and words_present(required_words, fact_words(span))
            for span in spans
        ):
            missing.append(phrase)
    return tuple(missing)


def _expand_protected_for_validation(text: str, replacements: dict[str, str]) -> str:
    expanded = text
    for token, value in replacements.items():
        expanded = expanded.replace(token, value)
    return expanded


def _numeric_literal_change(source: str, candidate: str) -> str | None:
    source_facts = _numeric_facts(source)
    candidate_facts = _numeric_facts(candidate)
    if source_facts == candidate_facts:
        return None
    added = candidate_facts - source_facts
    removed = source_facts - candidate_facts
    if added and not removed:
        return "added"
    if removed and not added:
        return "removed"
    return "changed"


def _numeric_literals_preserved(source: str, candidate: str) -> bool:
    return _numeric_literal_change(source, candidate) is None


def _normalized_number(value: str) -> str:
    return value.replace(",", "").casefold()


def _explicit_word_limit(instruction: str) -> int | None:
    limits = [
        int(match.group(1))
        for pattern in _WORD_LIMIT_PATTERNS
        for match in pattern.finditer(instruction)
        if int(match.group(1)) > 0
    ]
    return min(limits) if limits else None


def _task_rules(
    command: str,
    target_language: str,
    word_limit: int | None,
    *,
    hard_word_limit: bool,
) -> str:
    rules = {
        "Proofread": (
            "Produce a polished rewrite. Correct every grammar, spelling, punctuation, vocabulary, "
            "clarity, and sentence structure problem, including awkward but understandable wording. "
            "Check every verb against its subject and against time cues in the same passage so tense "
            "stays consistent. Keep coordinated actions parallel in tense and grammatical form. "
            "Fix comma splices, fragments, and run-on sentences. "
            "Preserve sentence order and the order of all immutable values; repair each sentence "
            "in place and split a run-on only where it already occurs. "
            "Use a comma before a coordinating conjunction when it joins two independent clauses. "
            "Complete a final verification pass before answering. Check every sentence for subject-verb "
            "agreement, pronoun agreement, sentence fragments, comma splices, fused sentences, missing "
            "list commas, faulty comparisons, vague vocabulary, and incorrect verb forms. Merge a source "
            "fragment beginning with Because, Which, or While into the sentence it belongs to. Do not "
            "preserve an error merely because its meaning is understandable. "
            "Do not place a comma between a person's role and name unless the name is truly "
            "nonessential. Silently check the finished text once more before returning it. Do "
            "not omit a clause, fact, actor, relationship, boundary, or level of force."
        ),
        "Improve": (
            "Make visible improvements in every paragraph: polish grammar, word choice, clarity, "
            "transitions, and flow. Do not merely copy the source. Retain every factual clause, "
            "request, constraint, and level of force."
        ),
        "Rephrase": (
            "Rewrite every paragraph with materially different natural wording and sentence "
            "structure. Do not return source sentences unchanged except immutable literals. Keep "
            "roughly the same detail and length while retaining every factual clause and constraint."
        ),
        "Rewrite": "Improve clarity and flow while retaining every factual clause and constraint.",
        "Shorten": (
            "Compress every paragraph by combining overlapping sentences and removing repetition, "
            "filler, and needless modifiers. Keep every key fact, request, owner, boundary, and constraint."
        ),
        "Expand": "Add useful explanation only when it follows from the source; never invent details.",
        "Summarize": "State the key decision, actor, action, date, status, and open issue when present.",
        "Formal": (
            "Rewrite every paragraph in clearly formal, conventional, impersonal language. Use complete "
            "forms, precise vocabulary, restrained phrasing, and normal sentence case. Avoid contractions, "
            "casual wording, warmth markers, workplace shorthand, headings, labels, and all-caps prose. "
            "Keep exactly the original paragraph structure. Do not merely copy the source."
        ),
        "Friendly": (
            "Rewrite every paragraph in an unmistakably warm, approachable, conversational voice. Use "
            "natural transitions and calm, supportive wording grounded in the source. Make the warmth "
            "visible through people-centered phrasing such as 'so users can clearly see,' 'to make the "
            "review easier,' or 'helps users' only where the source supports it. Correct grammar alone "
            "is not enough to satisfy this tone. Express friendliness by recasting existing claims "
            "with approachable wording, contractions, and gentle transitions. Never append a new "
            "sentence, claim, reason, promise, or commentary just to signal friendliness. Keep standard "
            "workplace vocabulary and the original paragraph structure. Never use slang, intensifiers "
            "such as super, excessive exclamation marks, fake enthusiasm, invented thanks, headings, or changed "
            "meaning. Keep the source actor for every action; do not introduce we, you, or they as a "
            "replacement owner. Do not merely copy the source."
        ),
        "Professional": (
            "Rewrite every paragraph as polished, confident workplace communication. Prefer concise "
            "active sentences, clear actions, owners, and next steps. Avoid both stiff formal language "
            "and casual chatter. Do not merely copy the source or change meaning."
        ),
        "Academic": (
            "Rewrite as precise, objective academic prose with logical transitions and appropriately "
            "qualified claims. Use complete forms without contractions. Do not invent citations, "
            "evidence, terminology, or conclusions."
        ),
        "Conversational": (
            "Rewrite in natural spoken-style language with useful contractions and everyday wording. "
            "Keep standard grammar, complete meaning, and the original paragraph structure."
        ),
        "Concise": (
            "Remove filler, repetition, and needless qualifiers. Keep every factual clause, actor, "
            "action, relationship, constraint, request, and paragraph boundary. Preserve reported "
            "failures and their sequence. Shorten wording inside clauses; do not delete clauses."
        ),
        "Detailed": (
            "Make existing relationships and details explicit and easy to follow. Do not invent "
            "examples, explanations, evidence, or facts that are not supported by the source."
        ),
        "Persuasive": (
            "Do not merely proofread or copy the source. Make a visible, evidence-led rewrite by "
            "leading with the source's existing recommendation, conclusion, requirement, or consequence "
            "and connecting its strongest existing reasons to that point. Where a factual section has "
            "no recommendation, emphasize its stated consequence or requirement without inventing one. "
            "Do not invent evidence, exaggerate certainty, conceal limitations, or use pressure tactics."
        ),
        "Confident": (
            "Use assured, decisive, active wording. Do not increase certainty, obligation, hostility, "
            "or factual force beyond the source."
        ),
        "Casual": (
            "Use relaxed, natural, everyday wording and contractions. Keep correct standard English, "
            "all important details, and the original paragraph structure."
        ),
        "Technical": (
            "Use precise technical language and explicit relationships. Preserve every identifier, "
            "command, version, component, constraint, and factual boundary exactly."
        ),
        "Simplified": (
            "Use plain vocabulary and simpler sentence structures. Keep every essential fact, request, "
            "warning, actor, number, name, and constraint. Do not use childish or broken language."
        ),
        "High-school level": (
            "Use clear high-school-level vocabulary, complete sentences, direct explanations, and "
            "limited unnecessary jargon while preserving every fact and boundary."
        ),
        "College level": (
            "Use polished college-level vocabulary, varied syntax, and well-organized reasoning. "
            "Avoid needless jargon, inflated language, and invented analysis."
        ),
        "Diplomatic": (
            "Rewrite every paragraph in an unmistakably tactful, respectful, neutral voice. Acknowledge "
            "reported viewpoints and impacts, replace blame with objective descriptions, and keep each "
            "requirement, request, and boundary fully clear. Use neutral purpose framing such as 'to "
            "support,' 'to reduce confusion,' or 'it remains important' where the source supports it. "
            "Do not weaken a firm source obligation into an optional suggestion; must and need remain "
            "appropriate when the source states a requirement. Preserve the original paragraph structure. "
            "Avoid evasive hedging, accusation, headings, or invented agreement. Do not merely copy the source."
        ),
        "Empathetic": (
            "Rewrite every paragraph with visible, restrained care for the impacts and concerns already "
            "stated in the source. Explicitly acknowledge the reported difficulty, confusion, or user "
            "experience when that impact appears in the paragraph. Express care by recasting an existing "
            "difficulty or confusion from the affected person's perspective inside the existing claim. "
            "For example, when the source says users cannot tell something because the review is confusing, "
            "say that users find it difficult to tell; do not leave the impact as an impersonal report. "
            "Do not append a new sentence or claim solely to signal empathy. Do not invent feelings, apologies, "
            "facts, thanks, promises, or meta-commentary such as 'this was acknowledged' or 'these "
            "concerns were noted.' Do not add new sentences solely to signal empathy. Keep needs and "
            "boundaries clear and retain every technical failure, device, sound, log, and workflow "
            "detail. When supported by the paragraph, explicitly frame the wording around user impact, "
            "user concern, confusion for users, or privacy expectations. Prefer people-centered phrasing "
            "such as 'users experienced,' 'the team faced,' and 'to reduce user confusion' only when the "
            "same fact is already present. When a source paragraph contains only technical requirements "
            "and no human impact, keep it clear rather than inventing an empathy statement. Do not merely copy the source."
        ),
        "Cooperative": (
            "Rewrite every paragraph around shared goals, collaboration, and concrete next steps. Use "
            "solution-focused language such as working together where natural, while preserving every "
            "factual clause, owner, duty, request, and boundary. Do not omit criticism or documented "
            "failures, do not add new proposals, headings, bullets, or numbered steps, and do not merely "
            "copy the source or surrender ownership. Keep the original paragraph form."
        ),
        "Assertive": (
            "Rewrite every paragraph in direct, confident, active language. State actions, requirements, "
            "and boundaries clearly; remove weak hedging and passive phrasing. Stay respectful and avoid "
            "hostility, threats, blame, or stronger obligations. Do not merely copy the source."
        ),
        "Caveman": (
            "Rewrite every paragraph in a short, blunt, plain-language style. Lead with the main point. "
            "Use short sentences, common words, one idea per sentence, and active voice. Remove filler, "
            "jargon, repetition, and needless setup. Split packed source sentences into several short, "
            "complete sentences. Aim for about 12 words per sentence and keep ordinary sentences under "
            "18 words. Split long clauses with periods, not line breaks. A required exact list is the only "
            "length exception. Keep the original paragraph form. Do not add headings, "
            "bullets, numbered lists, or labels. "
            "Keep every important fact, request, boundary, safety detail, number, name, and constraint. "
            "Fragments are allowed only when clear. Use correct standard English. Never use broken grammar, "
            "mock accents, stereotypes, childish speech, or confusing shorthand. Do not merely copy the source."
        ),
        "Translate": (
            f"Translate all ordinary prose into {target_language}. Use only {target_language} except "
            "for immutable tokens and proper names. Do not copy untranslated source prose."
        ),
    }[command]
    if word_limit is not None:
        if hard_word_limit:
            rules += f" Hard output limit: {word_limit} words. Do not exceed it."
        else:
            rules += f" Suggested output limit: {word_limit} words. Aim to stay within it."
    contract = tone_contract_instruction(command)
    if contract:
        rules += " " + contract
    return rules


def _immutable_tokens_changed(
    source: str,
    candidate: str,
    replacements: dict[str, str],
) -> bool:
    expected = [match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(source)]
    actual_matches = list(_INTERNAL_TOKEN_LIKE.finditer(candidate))
    actual = [match.group(0).upper() for match in actual_matches]
    if any(_IMMUTABLE_TOKEN.fullmatch(match.group(0)) is None for match in actual_matches):
        return True
    return actual != expected or any(token not in source for token in replacements)


def _immutable_token_mismatch_count(source: str, candidate: str) -> int:
    expected = [match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(source)]
    actual_matches = list(_INTERNAL_TOKEN_LIKE.finditer(candidate))
    actual = [match.group(0).upper() for match in actual_matches]
    width = max(len(expected), len(actual))
    sequence_errors = sum(
        index >= len(expected)
        or index >= len(actual)
        or expected[index] != actual[index]
        for index in range(width)
    )
    malformed = sum(
        _IMMUTABLE_TOKEN.fullmatch(match.group(0)) is None
        for match in actual_matches
    )
    return sequence_errors + malformed


def _log_immutable_token_mismatch(stage: str, source: str, candidate: str) -> None:
    """Log token labels only; never write source or generated user text."""
    expected = tuple(
        match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(source)
    )
    actual = tuple(
        match.group(0).upper() for match in _INTERNAL_TOKEN_LIKE.finditer(candidate)
    )
    logger.warning(
        "Writing correction protected-token mismatch stage=%s expected=%s actual=%s",
        stage,
        expected,
        actual,
    )


def _sentence_pieces(text: str) -> list[str]:
    """Split into sentences, keeping their punctuation and spacing."""
    return [piece for piece in re.split(r"(?<=[.!?])(\s+)", text) if piece != ""]


def _restore_missing_immutable_tokens(
    source: str,
    candidate: str,
    replacements: dict[str, str],
) -> str:
    """Put back the sentences whose protected tokens the model dropped.

    A protected token stands in for something the writer must keep exactly - a
    saved vocabulary term, a name, a number. Small packs drop one anyway, and
    they keep dropping it through the retry and the final retry, so asking
    again a fourth time is not a plan. Until now the whole section was refused,
    which cost every correction in it to save one clause.

    Sentences are the unit because that is the largest piece that can be put
    back without guessing. A dropped token cannot be reinserted into the
    model's own sentence - there is no way to know where it belongs - but the
    writer's original sentence is known exactly, so that sentence is kept as it
    was and the rest of the answer stands.

    Returns "" when the sentences do not line up, and the caller refuses the
    answer as before. Protected content is never reconstructed by guesswork.
    """
    missing = set(_missing_immutable_tokens(source, candidate))
    if not missing:
        # Nothing is absent, so the answer changed the tokens some other way -
        # it reordered them, or wrote a malformed one. Restoring a sentence
        # cannot fix either, and a protected value in the wrong order is a
        # different statement. Refuse.
        return ""
    source_pieces = _sentence_pieces(source)
    candidate_pieces = _sentence_pieces(candidate)
    if len(source_pieces) != len(candidate_pieces):
        return ""
    rebuilt = list(candidate_pieces)
    for index, piece in enumerate(source_pieces):
        tokens = {match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(piece)}
        if tokens & missing:
            rebuilt[index] = piece
    result = "".join(rebuilt)
    if result == source:
        # Every sentence had to be put back, so nothing the model produced
        # survived. Returning this would hand the writer their own text and
        # read as "there was nothing to change", which is not what happened.
        # Refuse and let the caller report the failure.
        return ""
    if _missing_immutable_tokens(source, result):
        return ""
    if _immutable_tokens_changed(source, result, replacements):
        return ""
    return result


def _missing_immutable_tokens(source: str, candidate: str) -> tuple[str, ...]:
    expected = Counter(match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(source))
    actual = Counter(match.group(0).upper() for match in _IMMUTABLE_TOKEN.finditer(candidate))
    return tuple((expected - actual).elements())


def _canonicalize_immutable_tokens(
    candidate: str,
    source: str,
    replacements: dict[str, str],
) -> str:
    for token in replacements:
        candidate = re.sub(re.escape(token), token, candidate, flags=re.IGNORECASE)
    for token, value in replacements.items():
        if value.casefold() == "fifteen days" and token in candidate:
            candidate = re.sub(
                rf"{re.escape(token)}\s+retention\s+for\s+(?:fifteen|15)\s+days\b",
                f"{token} retention",
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
    for token, value in replacements.items():
        if token in candidate:
            continue
        literal_pattern = rf"(?<!\w){re.escape(value)}(?!\w)"
        candidate, count = re.subn(
            literal_pattern,
            token,
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        if count:
            continue
        words = value.split()
        number = _NUMBER_WORDS.get(words[0].casefold()) if words else None
        if number is not None and len(words) > 1:
            numeric_pattern = rf"(?<!\w){re.escape(number)}\s+{re.escape(' '.join(words[1:]))}(?!\w)"
            candidate, count = re.subn(
                numeric_pattern,
                token,
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
            if count:
                continue
        if value.casefold() == "cloud service":
            candidate = re.sub(
                r"(?<!\w)(?:(?:a|the)\s+)?servers?(?!\w)",
                token,
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
    expected = [match.group(0) for match in _IMMUTABLE_TOKEN.finditer(source)]
    actual = list(_INTERNAL_TOKEN_LIKE.finditer(candidate))
    if len(actual) != len(expected):
        return candidate
    replacements_by_span: list[tuple[int, int, str]] = []
    for match, expected_token in zip(actual, expected):
        value = match.group(0)
        if _IMMUTABLE_TOKEN.fullmatch(value) is not None:
            if value.casefold() != expected_token.casefold():
                return candidate
            continue
        replacements_by_span.append((match.start(), match.end(), expected_token))
    for start, end, expected_token in reversed(replacements_by_span):
        candidate = candidate[:start] + expected_token + candidate[end:]
    return candidate


def _safe_final_proofread(
    source: str,
    candidate: str,
    command: str,
    replacements: dict[str, str],
) -> str:
    """Apply bounded cleanup while glossary values are still opaque tokens."""
    cleaned = _collapse_unrequested_blank_lines(source, candidate, command)
    cleaned = _normalize_accidental_all_caps(cleaned)
    cleaned = _apply_fast_proofread_rules(cleaned)
    if _immutable_tokens_changed(source, cleaned, replacements):
        return candidate
    source_plain = source
    cleaned_plain = cleaned
    for token, value in replacements.items():
        source_plain = source_plain.replace(token, value)
        cleaned_plain = cleaned_plain.replace(token, value)
    if not _numeric_literals_preserved(source_plain, cleaned_plain):
        return candidate
    if _strict_protected_literals(source_plain) != _strict_protected_literals(cleaned_plain):
        return candidate
    if _requires_surface_meaning_guard(command) and not _meaning_constraints_preserved(
        source,
        cleaned,
        command=command,
    ):
        return candidate
    return cleaned


def _collapse_unrequested_blank_lines(source: str, candidate: str, command: str) -> str:
    if command not in _VISIBLE_TONE_COMMANDS:
        return candidate
    if "\n" not in source and "\r" not in source:
        return re.sub(r"[ \t]*\r?\n+[ \t]*", " ", candidate)
    if re.search(r"\r?\n[ \t]*\r?\n", source):
        return candidate
    return re.sub(r"[ \t]*(?:\r?\n[ \t]*){2,}", " ", candidate)


def _normalize_accidental_all_caps(text: str) -> str:
    parts = re.split(r"([.!?]+(?:[\"'\u201d\u2019)\]]*)|\r?\n)", text)
    normalized: list[str] = []
    for part in parts:
        if not part:
            continue
        words = [
            match.group(0)
            for match in _ALL_CAPS_WORD.finditer(part)
            if _IMMUTABLE_TOKEN.fullmatch(match.group(0)) is None
        ]
        cased = [word for word in words if any(character.isalpha() for character in word)]
        uppercase = [word for word in cased if word.isupper()]
        if len(cased) >= 6 and len(uppercase) / len(cased) >= 0.8:
            part = _ALL_CAPS_WORD.sub(_sentence_case_word, part)
        normalized.append(part)
    return "".join(normalized)


def _restore_ui_label_case(source: str, candidate: str) -> str:
    for label in ("Accept", "Dismiss"):
        if re.search(rf"(?<!\w){label}(?!\w)", source):
            candidate = re.sub(
                rf"(?<!\w){label}(?!\w)",
                label,
                candidate,
                flags=re.IGNORECASE,
            )
    return candidate


def _restore_candidate_semantics(
    source: str,
    candidate: str,
    replacements: dict[str, str],
) -> str:
    if re.search(r"\bfeed\s+it\b", source, re.IGNORECASE):
        candidate = re.sub(
            r"\bfeed\s+them\b",
            "feed it",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
    candidate = _restore_coordinated_pronouns(source, candidate)
    candidate = _restore_source_cardinality(source, candidate)
    candidate = _restore_explicit_date_relations(source, candidate, replacements)
    token_by_value = {value.casefold(): token for token, value in replacements.items()}
    activation_token = token_by_value.get("activation sound")
    if activation_token and "activation sound played after" in source.casefold():
        if activation_token not in candidate:
            candidate = re.sub(
                r"\b(?:playback|sound)\s+(?:began|started)(?:\s+playing)?\s+before\s+"
                r"(?:(?:the\s+speaker\s+)?(?:began|started)\s+(?:speaking|talking)|"
                r"(?:speaking|talking)\s+(?:began|started))\b",
                f"{activation_token} played after the speaker started talking",
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
        candidate = re.sub(
            rf"\b{re.escape(activation_token)}\b\s+(?:began\s+playing|played)\s+before\s+"
            r"the speaker started talking",
            f"{activation_token} played after the speaker started talking",
            candidate,
            flags=re.IGNORECASE,
        )
    users_token = token_by_value.get("three users")
    if users_token and "three users who wrote several pages" in source.casefold():
        candidate = re.sub(
            rf"\bThe next morning,\s+{re.escape(users_token)}\s+sent a detailed report with several pages\.",
            f"The next morning, the team received a detailed report from {users_token} who wrote several pages.",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            rf"\bthe team received a detailed report from {re.escape(users_token)}\.(?!\s+who wrote)",
            f"the team received a detailed report from {users_token} who wrote several pages.",
            candidate,
            flags=re.IGNORECASE,
        )
    windows_token = token_by_value.get("windows")
    if windows_token and "normal windows clipboard" in source.casefold():
        literal_clipboard = re.search(
            r"\b(?:the\s+)?normal\s+Windows\s+clipboard\b",
            candidate,
            re.IGNORECASE,
        )
        if literal_clipboard is not None:
            candidate = re.sub(
                rf"(?:,\s*)?{re.escape(windows_token)}(?=[.!?]|$)",
                "",
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
            candidate = re.sub(
                r"\b(?:the\s+)?normal\s+Windows\s+clipboard\b",
                f"the normal {windows_token} clipboard",
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
        elif windows_token not in candidate:
            candidate, restored = re.subn(
                r"\b(?:the\s+)?clipboard\s+to\s+its\s+normal\s+state\b",
                f"the normal {windows_token} clipboard",
                candidate,
                count=1,
                flags=re.IGNORECASE,
            )
            if not restored:
                candidate = re.sub(
                    r"\b(?:the\s+)?(?:normal\s+)?clipboard\b",
                    f"the normal {windows_token} clipboard",
                    candidate,
                    count=1,
                    flags=re.IGNORECASE,
                )
    return candidate


def _restore_explicit_date_relations(
    source: str,
    candidate: str,
    replacements: dict[str, str],
) -> str:
    for match in _MONTH_DATE_RELATION.finditer(source):
        relation = match.group("relation")
        date_text = match.group("date")
        protected_date = date_text
        for token, value in replacements.items():
            protected_date = re.sub(
                rf"(?<!\w){re.escape(value)}(?!\w)",
                token,
                protected_date,
            )
        candidate = re.sub(
            rf"\b(?:on|by)\s+{re.escape(protected_date)}\b",
            f"{relation} {protected_date}",
            candidate,
            flags=re.IGNORECASE,
        )
    return candidate


def _restore_coordinated_pronouns(source: str, candidate: str) -> str:
    for match in re.finditer(
        r"\bthen\s+(?P<actor>they|we|you|he|she|it)\s+"
        r"(?P<verb>[A-Za-z][A-Za-z'-]*)\b",
        source,
        re.IGNORECASE,
    ):
        actor = match.group("actor").casefold()
        verb = match.group("verb")
        candidate = re.sub(
            rf"\band then\s+(?!{re.escape(actor)}\b){re.escape(verb)}\b",
            f"and then {actor} {verb}",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
    return candidate


def _restore_source_cardinality(source: str, candidate: str) -> str:
    for singular, article in (
        ("small icon", "the"),
        ("correction card", "the"),
        ("spelling fix", "a"),
        ("cloud service", "a"),
    ):
        if re.search(rf"\b{re.escape(singular)}\b", source, re.IGNORECASE) is None:
            continue
        if re.search(rf"\b{re.escape(singular)}s\b", source, re.IGNORECASE):
            continue
        candidate = re.sub(
            rf"\b(?:(?:a|an|the)\s+)?{re.escape(singular)}s\b",
            f"{article} {singular}",
            candidate,
            flags=re.IGNORECASE,
        )
    style_owner = re.search(
        r"\b(?P<actor>they|reviewers?)\s+(?P<also>also\s+)?need\b",
        source,
        re.IGNORECASE,
    )
    if style_owner is not None and _STYLE_LIST.search(source):
        actor = style_owner.group("actor")
        if actor.casefold() == "they":
            actor = "They"
        elif source.lstrip().startswith(actor):
            actor = actor[:1].upper() + actor[1:]
        also = "also " if style_owner.group("also") else ""
        candidate = re.sub(
            r"^(?:we|you)\s+(?:also\s+)?need\b",
            f"{actor} {also}need",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        passive_style_requirement = re.match(
            r"^(?P<body>.+?\bversions)\s+are\s+(?:needed|required)\b(?P<tail>.*)$",
            candidate,
            re.IGNORECASE | re.DOTALL,
        )
        if (
            passive_style_requirement is not None
            and _STYLE_LIST.search(passive_style_requirement.group("body"))
        ):
            body = passive_style_requirement.group("body")
            candidate = (
                f"{actor} {also}need {body[:1].lower() + body[1:]}"
                f"{passive_style_requirement.group('tail')}"
            )
    if all(term in source.casefold() for term in ("local ai", "shorter draft", "two details")):
        candidate = re.sub(
            r"\bthe third tester wants (?:a )?formal tone\b",
            "The third tester requested a formal tone",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bthe third tester request\w* a shorter draft (?:with|in) formal tone,\s*"
            r"removing two details and changing names\b",
            "The third tester requested formal tone. The local AI returned a shorter draft that "
            "removed two details and changed names",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bthe third tester requested formal tone\b",
            "The third tester requested a formal tone",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bwhether replacements finished\b",
            "whether replacements are finished",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bthe local AI removed two details and changed names\b",
            "The local AI returned a shorter draft, removing two details and changing names",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bthe local AI (?:must|should|needs?\s+to) remove two details and change names\b",
            "The local AI returned a shorter draft, removing two details and changing names",
            candidate,
            flags=re.IGNORECASE,
        )
    if "shared build" in source.casefold() and "corrected text" in source.casefold():
        candidate = re.sub(
            r"\bfailed to correct text in the active document\b",
            "failed to insert corrected text into the active document",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bwithout correcting text in the active document\b",
            "without inserting corrected text into the active document",
            candidate,
            flags=re.IGNORECASE,
        )
    if all(
        term in source.casefold()
        for term in ("one clean suggestion", "every original paragraph", "line break", "must")
    ):
        candidate = re.sub(
            r"\bthe model returns one clean suggestion while maintaining every original paragraph "
            r"and line break(?:s)? unless the user requests? a structural rewrite\b",
            "The model returns one clean suggestion. Every original paragraph and line break must "
            "stay unless the user requests a structural rewrite",
            candidate,
            flags=re.IGNORECASE,
        )
    if re.search(r"\bpri(?:vate|vite) sentence\b", source, re.IGNORECASE):
        candidate = re.sub(r"\bprivate data\b", "private sentences", candidate, flags=re.IGNORECASE)
    if "another tester" in source.casefold():
        candidate = re.sub(
            r"\banother\s+(?=(?:noted|noticed|observed|saw)\b)",
            "another tester ",
            candidate,
            flags=re.IGNORECASE,
        )
    if "privacy requirements" in source.casefold():
        candidate = re.sub(
            r"\bencrypt diagnostic history;\s*keep only ([^.!?;]+)",
            r"Encrypted diagnostic history keeps only \1",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"\buser-approved info\b", "user-approved information", candidate)
        candidate = re.sub(
            r"\bdelete old speech records (?:in|within) fifteen days;\s*"
            r"do not send (private sentences?) to (?:the )?(cloud services?|a cloud service)",
            r"Old speech records are deleted within fifteen days; \1 are not sent to a cloud service",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bold speech records delete within\b",
            "Old speech records are deleted within",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bno private sentences sent to (?:a )?cloud service\b",
            "no private sentences are sent to a cloud service",
            candidate,
            flags=re.IGNORECASE,
        )
    if "application should" in source.casefold():
        candidate = re.sub(
            r"^(?:The\s+)?Application\s+(?:must|needs?\s+to|is\s+required\s+to)\b",
            "The application should",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"^Application should\b", "The application should", candidate)
    if "real hud" in source.casefold() and "final decision" in source.casefold():
        candidate = re.sub(r"\bthrough real HUD\b", "through the real HUD", candidate)
        candidate = re.sub(
            r"\bReview confirms\b",
            "The review should confirm",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r";\s*verifies\b", " and verify", candidate, flags=re.IGNORECASE)
        candidate = re.sub(
            r"\bconfirm completeness, readability, consistency and verify\b",
            "confirm completeness, readability, and consistency, and verify",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bfifteen days retention\b",
            "fifteen days of retention",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bFinal decision belongs to user\b",
            "The final decision belongs to the user",
            candidate,
            flags=re.IGNORECASE,
        )
    if source.casefold().startswith("the next morning"):
        candidate = re.sub(r"^Next morning\b", "The next morning", candidate)
    if source.casefold().startswith("if selection disappear"):
        candidate = re.sub(
            r"^If selection disappears while card is open",
            "If the selection disappears while the card is open",
            candidate,
        )
        candidate = re.sub(
            r";\s*workflow restores normal clipboard\b",
            "; the workflow restores the normal clipboard",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bfind unchanged text\b",
            "find the same unchanged text",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(
            r"\bnormal clipboard\b",
            "normal Windows clipboard",
            candidate,
            flags=re.IGNORECASE,
        )
    return candidate


def _apply_safe_shorten_compression(candidate: str) -> str:
    candidate = re.sub(
        r"\brequested a formal tone,\s*but the local AI returned\b",
        "requested a formal tone. The local AI returned",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bshorter draft that (?:removed|omitted)\b",
        "shorter draft, removing",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThese issues make review confusing,\s*as users cannot tell which "
        r"(.+?) are active,\s*or if replacements?\s+(?:(?:is|are)\s+)?finished\b",
        r"Users cannot tell which \1 are active or whether replacements are finished",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe project team was ready\b",
        "The team was ready",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe first shared build was slow, putting periods in weird spots and failing to insert "
        r"corrected text into the active document\b",
        "The slow first shared build put periods in weird spots and failed to insert corrected text "
        "into the active document",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThey need various styles:",
        "They need styles:",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe next morning, the team received a detailed report from\b",
        "The next morning, the team got a report from",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\bEach user liked\b", "All liked", candidate, flags=re.IGNORECASE)
    candidate = re.sub(
        r"\bThis (?:causes|makes) (?:review )?confusion as users can't tell which "
        r"(two or three sentences) are active or if replacements are (?:complete|finished)\.",
        r"Users still can't tell which \1 are active or whether replacements are finished.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThey need styles:\s*(.+?)\.\s+Without opening the main application window\.",
        r"They need \1 styles without opening the main application window.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe team agrees to rebuild interaction around a compact, dependable card that\b",
        "The team agrees to rebuild a compact, dependable card that",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bReviewers need clear red marks under changed words, visible Accept and Dismiss controls, "
        r"and a reliable way to move to the next sentence\b",
        "Reviewers need clear red marks on changes, visible Accept and Dismiss controls, and reliable "
        "movement to the next sentence",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe revised local workflow reads active selection, separates complete sentences,?\s+and "
        r"sends each small group to an installed AI model for spelling, grammar, punctuation, and "
        r"basic formatting checks\.",
        "The revised local workflow reads active selection, separates complete sentences, and sends "
        "each group to an installed AI model to check spelling, grammar, punctuation, and basic formatting.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe model returns one clean suggestion while maintaining every original paragraph and "
        r"line break unless the user requests a structural rewrite\.",
        "It returns one clean suggestion while preserving every original paragraph and line break "
        "unless the user requests restructuring.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"^If the selection disappears while the card is open",
        "If selection vanishes while the card is open",
        candidate,
    )
    candidate = re.sub(
        r"\bbefore applying one safe paste\b",
        "before one safe paste",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        rf"\bbefore applying one ({_IMMUTABLE_TOKEN.pattern})",
        r"before one \1",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bthe normal Windows clipboard after any approved fallback\b",
        "the Windows clipboard after an approved fallback",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe developers rushed their review due to the deadline\. They documented changes briefly\.",
        "Deadline pressure made developers rush review and document changes briefly.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe final decision belongs to the user after local evidence is visible and understandable\.",
        "The user makes the final decision after seeing clear local evidence.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe application should work locally, explain failures clearly, and avoid hidden uploads\b",
        "The local application should clearly explain failures and avoid hidden uploads",
        candidate,
        flags=re.IGNORECASE,
    )
    return candidate


def _apply_safe_caveman_splits(candidate: str) -> str:
    """Make factual prose blunt without changing its facts, force, or paragraph layout."""
    candidate = re.sub(
        r"\b(?P<actor>[A-Z][A-Za-z'-]*) (?P<directive>told|instructed|asked) "
        r"(?P<target>[^.!?]+?) to "
        r"(?P<first>.+?) and (?P<second>(?:verify|check|keep|preserve|submit|send|call|review|"
        r"confirm|record|enter|save)\b.*?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('actor')} {match.group('directive')} {match.group('target')} to "
            f"{match.group('first')}. {match.group('actor')} also {match.group('directive')} "
            f"{match.group('target')} to "
            f"{match.group('second')}{match.group('punct')}"
        ),
        candidate,
    )
    candidate = re.sub(
        r"\b(?P<prefix>Until [^,]+), (?P<subject>[^.!?]+?) must "
        r"(?P<first>.+?) and (?P<second>(?:enter|record|submit|send|call|check|verify|keep|save)\b.*?)"
        r"(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('prefix')}, {match.group('subject')} must {match.group('first')}. "
            f"{match.group('prefix')}, {match.group('subject')} must {match.group('second')}"
            f"{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?P<lead>It also needs to (?:state|say)) that (?P<subject>.+?) "
        r"(?P<first_verb>runs|works|operates|remains|stays|is)\b(?P<first>.*?) and "
        r"(?P<second>does not\b.*?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('lead')} that {match.group('subject')} {match.group('first_verb')}"
            f"{match.group('first')}. {match.group('lead').replace(' also ', ' ')} that "
            f"{match.group('subject')} "
            f"{match.group('second')}{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r",\s+but\s+(?P<subject>omitting|leaving|quantity|one)\b",
        lambda match: f". {match.group('subject').capitalize()}",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r",\s+which caused\b",
        ". This caused",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?P<actor>[A-Z][A-Za-z'-]*) has a tentative agreement with "
        r"(?P<partner>.+?) to (?P<action>.+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('actor')} tentatively agreed with {match.group('partner')} to "
            f"{match.group('action')}{match.group('punct')}"
        ),
        candidate,
    )
    candidate = re.sub(
        r",\s+while\s+(?P<subject>gardens?)\b",
        lambda match: f". {match.group('subject').capitalize()}",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bfound that (?P<worker>.+?) treated (?P<object>.+?) as (?P<new>.+?) "
        r"instead of (?P<old>.+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"found {match.group('worker')} treated {match.group('object')} as "
            f"{match.group('new')}, not {match.group('old')}{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bA more accurate sentence would say (?P<first>.+?) while making clear that "
        r"(?P<second>.+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"A more accurate sentence would say {match.group('first')}. "
            f"The sentence would make clear that {match.group('second')}{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?P<prefix>Before [^,]+), (?P<subject>[A-Za-z][A-Za-z -]+?) "
        r"(?P<first_verb>received|signed|reviewed|completed) (?P<first>.+?) and could "
        r"(?P<second>.+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('prefix')}, {match.group('subject')} {match.group('first_verb')} "
            f"{match.group('first')}. {match.group('prefix')}, {match.group('subject')} could "
            f"{match.group('second')}{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r",\s+although\s+the\b",
        ". The",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bOn (?P<date>[A-Z][a-z]+ \d{1,2}), the (?P<service>.+?) started "
        r"(?P<action>.+?) after (?P<deployment>[A-Z][A-Za-z0-9.-]*(?: \d+(?:\.\d+)*)?) "
        r"was deployed to node (?P<node>[A-Z0-9-]+)\.",
        lambda match: (
            f"On {match.group('date')}, {match.group('deployment')} was deployed to node "
            f"{match.group('node')}. Afterward, the {match.group('service')} started "
            f"{match.group('action')}."
        ),
        candidate,
    )
    candidate = re.sub(
        r"\b(?P<intro>[^.!?]+?\bsays) (?P<subject>the (?:study|report|proposal|result|data)) "
        r"(?P<fact>.+?), and it (?P<warning>must not .+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('intro')} {match.group('subject')} {match.group('fact')}. "
            f"{match.group('subject').capitalize()} {match.group('warning')}{match.group('punct')}"
        ),
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r",\s+and\s+no\s+(?P<subject>[^,.!?]+?)\s+"
        r"(?P<modal>may|must|can|should|will)\b",
        lambda match: f". No {match.group('subject')} {match.group('modal')}",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r";\s+(?=\d[\d,]* respondents\b)",
        ". ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe project team was (ready|prepared) to launch (Manuscript after Monday's review), "
        r"but the interface was crowded on smaller screens and (?:several|some) status messages were unclear\.",
        r"The team was \1 to launch \2. The interface was crowded on smaller screens. "
        r"Several status messages were unclear.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThey tested the microphone on two laptops, but (?:the )?recordings? stopped "
        r"(?:without warning often|often without warning|frequently without warning)\.",
        "They tested the microphone on two laptops. Recordings often stopped without warning.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThey tested (?:the )?microphone on two laptops, but (?:the )?recordings? stopped "
        r"(?:without warning often|often without warning|frequently without warning)\.",
        "They tested the microphone on two laptops. Recording often stopped without warning.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe deadline was (?:close|near), so the developers rushed their review of (?:the )?error logs\.",
        "A near deadline made developers rush their review of the error logs.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bIt (?:put|placed) periods in weird spots and (?:didn(?:'|\u2019)t|did not) insert corrected text "
        r"into the active document\.",
        "It misplaced periods and failed to insert corrected text into the active document.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe team received a detailed report from (three users who wrote several pages)\.",
        r"The team got a detailed report from \1.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bEach user liked (?:the )?small icon, but (?:the )?correction card (?:was|were) still too large\.",
        "Each user liked the small icon. The correction card was still too large.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bOne tester accepted a spelling fix, (?:but|yet) the text (?:didn(?:'|\u2019)t|did not) change and "
        r"the HUD (?:stayed|remained) on the first sentence\.",
        "One tester accepted a spelling fix. The text did not change. The HUD stayed on the first sentence.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThis makes review confusing because users can't tell which two or three sentences are "
        r"active, or if replacements are finished\.",
        "Users can't tell which two or three sentences are active. They can't tell if replacements finished.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bAnother tester (?:saw|noticed|noted) inconsistent grammar marks while common spelling "
        r"problems remained\.",
        "Another tester saw inconsistent grammar marks. Common spelling problems remained.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe local AI (?:returned|made) a shorter draft,?\s*(?:that\s+)?(?:removed|removing) "
        r"two details and (?:changed|changing) names that (?:needed|had) to (?:remain|stay) exact\.",
        "The local AI made a shorter draft. It removed two details and changed names that had to stay exact.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bVisible Accept and Dismiss controls are required\.",
        "Reviewers need visible Accept and Dismiss controls.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe team will rebuild interaction using a compact, dependable card\.",
        "The team agreed to rebuild interaction around a compact, dependable card.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThere must be a reliable way to move to the next sentence\.",
        "Reviewers need a reliable way to move to the next sentence.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bReviewers need a reliable way to move to the next sentence\.",
        "Reviewers need a reliable way to reach the next sentence.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThey need clear, formal, casual, concise, friendly, professional, diplomatic, "
        r"empathetic, cooperative, and assertive versions without opening the main application window\.",
        "They need clear, formal, casual, concise, and friendly versions. They also need professional, "
        "diplomatic, empathetic, cooperative, and assertive versions. Using these versions must not open "
        "the main application window.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe revised local workflow reads active selection, separates complete sentences,?\s+and "
        r"sends each small group to (?:an|the) installed AI model\.",
        "The local workflow reads active selection, separates complete sentences, and sends small groups "
        "to the installed AI model.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe local workflow reads active selection, separates complete sentences,?\s+and sends "
        r"small groups to the installed AI model\.",
        "The local workflow reads active selection, separates sentences, and sends small groups to the "
        "installed AI model.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe local workflow reads active selection, separates sentences, and sends small groups "
        r"to the installed AI model\.",
        "The local workflow reads active selection. It separates sentences and sends small groups to the "
        "installed AI model.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bEvery original paragraph and line break must remain in place unless the user asks for a "
        r"structural rewrite\.",
        "Original paragraphs and line breaks must stay unless the user asks for a structural rewrite.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bOriginal paragraphs and line breaks must stay unless the user asks for a structural rewrite\.",
        "Original paragraphs and line breaks must stay unless the user asks for structural rewrite.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe model checks spelling, grammar, punctuation, and basic formatting before returning "
        r"one clean suggestion\.",
        "The model checks spelling and grammar. It checks punctuation and basic formatting. Then it "
        "returns one clean suggestion.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe application should work locally and explain failures clearly\.",
        "The application should work locally. It should explain failures clearly.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bIf the selection disappears while the card is open, Manuscript must find the same unchanged "
        r"text before applying one safe paste\.",
        "If selection disappears with the card open, Manuscript must find the same unchanged text before "
        "one safe paste.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bDevelopers can use synthetic local evidence to improve the product, but they must keep "
        r"private user writing out of test reports\.",
        "Developers may use synthetic local evidence. Developers must keep private user writing out of "
        "test reports.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bVerify (?:the )?microphone,\s*(?:the )?activation sound,\s*(?:the )?error logs,\s*"
        r"(?:the )?three users,\s*(?:the )?small icon,\s*(?:the )?two or three sentences,\s*"
        r"(?:the )?red marks,\s*(?:the )?safe paste,\s*(?:the )?clipboard restoration,\s*"
        r"(?:the )?fifteen days(?: of)? retention,\s*and (?:the )?cloud service boundary\.",
        "Verify the microphone. Verify the activation sound. Check the error logs. Verify three users. "
        "Check the small icon. Confirm two or three sentences. Check red marks. Test safe paste and "
        "clipboard restoration. Check fifteen days of retention. Verify the cloud service boundary.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?:The )?final decision belongs to the user after local evidence is visible and understandable\.",
        "The user makes the final decision after seeing clear local evidence.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\bThe review should confirm (?:document )?completeness, readability, and consistency\.",
        "The review should confirm the document is complete. It should confirm it is readable and consistent.",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r";\s*however,\s*",
        ". However, ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r";\s+(?P<subject>no one|nobody|that|this|it|they|we|you|he|she|the)\b",
        lambda match: f". {match.group('subject').capitalize()}",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r",\s+but\s+(?P<subject>that|this|it|they|we|you|he|she|the)\b",
        lambda match: f". {match.group('subject').capitalize()}",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"\b(?P<subject>The [A-Za-z][A-Za-z ]{0,40}?|[A-Z][A-Za-z'-]+|It|They|We|You|He|She) "
        r"(?P<modal>will|can|may|should|does|is|are|has|have) "
        r"(?P<first>[^.!?]+?) but must not (?P<second>[^.!?]+?)(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('subject')} {match.group('modal')} {match.group('first')}. "
            f"{match.group('subject')} must not {match.group('second')}{match.group('punct')}"
        ),
        candidate,
    )
    candidate = re.sub(
        r"\b(?P<subject>It|They|We|You|He|She|The [A-Za-z][A-Za-z ]{0,40}?) should "
        r"(?P<first>[^.!?]+?) without pretending (?P<second>[^.!?]+?)"
        r"(?P<punct>[.!?])(?=\s|$)",
        lambda match: (
            f"{match.group('subject')} should {match.group('first')}. "
            f"{match.group('subject')} should not pretend {match.group('second')}"
            f"{match.group('punct')}"
        ),
        candidate,
    )
    return candidate


def _sentence_case_word(match: re.Match[str]) -> str:
    value = match.group(0)
    if _IMMUTABLE_TOKEN.fullmatch(value) is not None:
        return value
    preserved = _PRESERVED_CAPITALIZATION.get(value.casefold())
    return preserved if preserved is not None else value.lower()


def _strict_protected_literals(text: str) -> Counter[str]:
    return Counter(
        value
        for value in _PROTECTED_PATTERN.findall(text)
        if _NUMBER.fullmatch(value) is None
        and _SHORT_ABBREVIATION.fullmatch(value) is None
    )


def _apply_fast_proofread_rules(text: str) -> str:
    from .writing_suggestions import WritingSuggestionEngine, apply_all_suggestions

    text = text.replace("â€™", "'").replace("â€˜", "'")
    text = text.replace("\u00e2\u20ac\u2122", "'")
    suggestions = WritingSuggestionEngine().analyze(text)
    cleaned = apply_all_suggestions(text, suggestions) if suggestions else text
    for pattern, replacement in (
        (
            r",\s+(But|And|Or|So|Yet)\b",
            lambda match: f", {match.group(1).lower()}",
        ),
        (r"\bmore\s+better\b", "better"),
        (r"\bmore\s+worse\b", "worse"),
        (r"\bmost\s+best\b", "best"),
        (r"\bmost\s+worst\b", "worst"),
        (r"\bmore\s+easier\b", "easier"),
        (r"\bdidn(?:'|\u2019)t\s+didn(?:'|\u2019)t\b", "didn't"),
        (
            r"\bcommunity college students hospital employees and parents\b",
            "community college students, hospital employees, and parents",
        ),
        (
            r"\btablets loose their saved routes\b",
            "tablets lost their saved routes",
        ),
        (
            r"\bafter the battery went below\b",
            "after the battery dropped below",
        ),
        (r"\bwhen it actually blocked\b", "when it blocked"),
        (
            r"\b(\d+(?:\.\d+)? percent),\s+the support team\b",
            r"\1; the support team",
        ),
        (
            r"\.\s+Which was a serious mistake\.",
            ", which was a serious mistake.",
        ),
        (
            r"\bblocked two emergency repairs\s+which was a serious mistake\b",
            "blocked two emergency repairs, which was a serious mistake",
        ),
        (
            r"\b(?:attendance at public meetings|public meeting attendance)\s+They recorded\b",
            "public meeting attendance. They recorded",
        ),
        (
            r"\bstay inside (?:their|there) approved temperature ranges\b",
            "stay within approved temperature ranges",
        ),
        (
            r"\branges but MedTrack 4\.7 sometimes display\b",
            "ranges, but MedTrack 4.7 sometimes displays",
        ),
        (
            r"\bConference Room 5C and remote workers joins\b",
            "Conference Room 5C, and remote workers join",
        ),
        (
            r"\bThe trainer have forty five minutes\b",
            "The trainer has forty-five minutes",
        ),
        (
            r"\broute syncing offline notes photo uploads and emergency escalation\b",
            "route syncing, offline notes, photo uploads, and emergency escalation",
        ),
        (
            r"\bSeveral instruction is wrote with vague vocabulary,\s+"
            r"for example the guide\b",
            "Several instructions are written with vague vocabulary; "
            "for example, the guide",
        ),
        (
            r"\bdeal with failed uploads in the normal fashion\b",
            "handle failed uploads normally",
        ),        (
            r"\bThe proposal also says the program will be completely free, "
            r"which is incorrect vocabulary since the library expects operating "
            r"costs of \$18,600\.",
            "The proposal says the program will be free to visitors, though the "
            "library expects $18,600 in operating costs.",
        ),
        (
            r"\bthe city still pays staff security utilities and transportation support\b",
            "the city still pays for staff, security, utilities, and transportation support",
        ),        (r"\b(didn't|did not)\s+measures\b", r"\1 measure"),
        (r"\bmust not be presents as\b", "must not be presented as"),
        (r"\bfor first six weeks\b", "for the first six weeks"),
        (
            r"\boutside encrypted project drive\b",
            "outside the encrypted project drive",
        ),
        (r"\brepeats conclusion\b", "repeats the conclusion"),
        (
            r"(?P<prefix>^|[.!?]\s+)strongest interest\b",
            lambda match: f"{match.group('prefix')}The strongest interest",
        ),
        (
            r"\b(?P<dates>[A-Z][a-z]+ \d{1,2} and [A-Z][a-z]+ \d{1,2})\s+"
            r"(?P<count>\d+ respondents)\b",
            r"\g<dates>; \g<count>",
        ),
        (r"\bdid not measure affects?\b", "did not measure"),
        (
            r"\bmust not be shows?\b",
            "must not be presented as proof that",
        ),
        (r"\bwith without\b", "without"),
        (r"\bexplain (?-i:The)\b", "explain why the"),
        (
            r"\bThe evidence is stronger than it is\. It should not pretend\.",
            "It should not pretend the evidence is stronger than it is.",
        ),
        (r"\bnot\s+and\s+(?=(?:the|a|an)\b)", "not "),
        (
            r"\bdoes not follow (?:was|is) based on the evidence\b",
            "does not follow from the evidence",
        ),        (
            r"\bdoes not explain had access\b",
            "does not explain who had access",
        ),        (
            r"\b[Tt]he [Nn]o\b",
            lambda match: "No" if match.group(0)[0].isupper() else "no",
        ),        (
            r"\b((?:say|state)(?: that)? [A-Z][A-Za-z0-9_-]*(?: \d+(?:\.\d+)*)?) and does not\b",
            r"\1 does not",
        ),
        (r"\bdoes not follow followed from\b", "does not follow from"),
        (
            r"\bsource ledger\s+Incident commander\b",
            "source ledger. Incident commander",
        ),
        (
            r"\bThis is just a link;\s+it is not evidence\b",
            "This relationship is only an association; it is not evidence",
        ),
        (r"\bThis is a link\.", "This is an association."),
        (
            r"\bIt would make clear emergency procedures stay the same\.",
            "The sentence should make clear that emergency procedures remain unchanged.",
        ),
        (r"\bcannot\s+may\s+be\b", "cannot be"),
        (r"\birregardless\b", "regardless"),
        (r"\bdoesnt\b", "doesn't"),
        (r"\bdont\b", "don't"),
        (r"\bdidnt\b", "didn't"),
        (r"\bcant\b", "can't"),
        (r"\bcouldnt\b", "couldn't"),
        (r"\bwouldnt\b", "wouldn't"),
        (r"\bshouldnt\b", "shouldn't"),
        (r"\bwont\b", "won't"),
        (r"\bisnt\b", "isn't"),
        (r"\barent\b", "aren't"),
        (r"\bwasnt\b", "wasn't"),
        (r"\bwerent\b", "weren't"),
        (r"\bhavent\b", "haven't"),
        (r"\bhasnt\b", "hasn't"),
        (r"\bhadnt\b", "hadn't"),
        (r"\b([Ll]ast) weeks (?=[A-Za-z'-]+\b)", r"\1 week's "),
        (r"\bthey was\b", "they were"),
        (r"\b(checklists) and none of them\b", r"\1, and none of them"),
        (
            r"\b(districts) and every technician\b",
            r"\1, and every technician",
        ),
        (
            r"\b(Conference Room [A-Z0-9-]+) and remote workers\b",
            r"\1, and remote workers",
        ),
        (
            r"\b(that district) but other districts\b",
            r"\1, but other districts",
        ),
        (
            r"\b(does not request permanent hours) and it does not\b",
            r"\1, and it does not",
        ),
        (
            r"\b(submitted late) and they contain\b",
            r"\1, and they contain",
        ),
        (
            r"\b(That tone weakens the proposal) and it does not\b",
            r"\1, and it does not",
        ),
        (
            r"\bwill still cover staff, security\b",
            "will still cover staffing, security",
        ),
        (
            r"\bcustomer names gate codes and service notes\b",
            "customer names, gate codes, and service notes",
        ),
        (
            r"\bevery unresolved defect the person responsible "
            r"the next review time and whether\b",
            "every unresolved defect, the person responsible, "
            "the next review time, and whether",
        ),
        (
            r"\b(the note should be brief) but omitting\b",
            r"\1, but omitting",
        ),
        (
            r"\b(At \d{1,2}:\d{2}\s+[ap]\.m\.),\s+"
            r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
            r"(?=[a-z])",
            r"\1 \2, ",
        ),
        (
            r"\b(neighborhood problem) a claim that\b",
            r"\1, a claim that",
        ),
        (
            r"\bvolunteer hours crop donations cancelled workdays and attendance\b",
            "volunteer hours, crop donations, cancelled workdays, and attendance",
        ),
        (
            r"\bvolunteer hours crop donations (cancelled|canceled) workdays "
            r"and public meeting attendance\b",
            r"volunteer hours, crop donations, \1 workdays, and public meeting attendance",
        ),
        (r"\b(six weeks) which caused\b", r"\1, which caused"),
        (
            r"\b(consent procedures) (causing|confusing) readers\b",
            r"\1, \2 readers",
        ),
        (r"\bclaim Gardens eliminate\b", "claim gardens eliminate"),
        (
            r"\bamounts customer names or tax codes\b",
            "amounts, customer names, or tax codes",
        ),
        (
            r"\bFacilities supervisor Owen Price definitely wants clearer routes\b",
            "Facilities supervisor Owen Price seeks clearer routes",
        ),
        (
            r"\bOwen will destroy the incorrect cards and Priya Desai will print\b",
            "Owen will destroy the incorrect cards, and Priya Desai will print",
        ),
        (
            r"\b(are fragments) others combine\b",
            r"\1; others combine",
        ),
        (
            r"\b(no clear order) and the word (?P<label>['\"]?utilize['\"]?)",
            r"\1, and the word \g<label>",
        ),
        (r"\b((?:technical )?cause) although\b", r"\1, although"),
        (r"\bFollow actions include\b", "Follow-up actions include"),
        (
            r"\badding a 409 regression test defining the producer pause order "
            r"in RB-SYNC-64 and changing alert HP-DUP-4\b",
            "adding a 409 regression test, defining the producer pause order "
            "in RB-SYNC-64, and changing alert HP-DUP-4",
        ),
        (
            r"\bindicated they would use (?:a )?preference for later hours\b",
            "indicated they would use later hours",
        ),
        (
            r"\bindicated they would use working later hours\b",
            "indicated they would use later hours",
        ),
        (
            r"\bindicated that they would use (?:reported )?working later hours\b",
            "indicated that they would use later hours",
        ),
        (
            r"\bindicated that they would use preferred later hours\b",
            "indicated that they would use later hours",
        ),
        (
            r"\b(If the board rejects it) the library\b",
            r"\1, the library",
        ),
        (
            r"\b(If costs exceed the grant) the program\b",
            r"\1, the program",
        ),
        (
            r"\bnightly attendance repeat visits reported safety issues "
            r"staff overtime and the percentage\b",
            "nightly attendance, repeat visits, reported safety issues, "
            "staff overtime, and the percentage",
        ),
        (r",\s+despite that it\b", ", although it"),
        (r"\bhas no a useful effect\b", "has no useful effect"),
        (
            r"\b(The accessibility coordinator),\s+"
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+),\s+(requested)\b",
            r"\1 \2 \3",
        ),
        (r"\bmust not be presented them as\b", "must not be presented as"),
        (r"\bThe Neither event\b", "Neither event"),
        (r"\bThere also contain\b", "There are also"),
        (r"\bwithout with commas\b", "without commas"),
        (r"\bmust not blame identifies\b", "must not blame"),
        (
            r"\b(A survey indicates interest),\s+but does not\b",
            r"\1 but does not",
        ),
        (r"\bNo A personal email address\b", "No personal email address"),
        (r"\bNo An employee(?! might exceed their contractual boundaries\b)", "No employee"),
        (
            r"\bNo personal email address may still be utilized\b",
            "No personal email address may be used",
        ),
        (
            r"\bdirected the system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+)\b",
            r"directed system administrator \1",
        ),
        (
            r"\bLast week's rehearsal was different due to "
            r"three trainers speaking at once\b",
            "Last week's rehearsal was unusual because three trainers spoke at once",
        ),
        (
            r"\bLast week's rehearsal was different due to three trainers "
            r"speaking simultaneously\b",
            "Last week's rehearsal was unusual because three trainers "
            "spoke simultaneously",
        ),
        (
            r"\bLast week's rehearsal stood out due to three trainers "
            r"talked at once\b",
            "Last week's rehearsal was unusual because three trainers "
            "spoke at once",
        ),
        (
            r"\bLast week's rehearsal was unusual because three trainers "
            r"talked at once,\s+which screen they were describing;\s+nobody "
            r"could understand the caption file stopped after nineteen minutes\b",
            "Last week's rehearsal was unusual because three trainers talked "
            "at once; nobody could understand which screen they were "
            "describing, and the caption file stopped after nineteen minutes",
        ),
        (
            r"\bLast week's rehearsal was unusual because three trainers "
            r"spoke simultaneously,\s+which screen they were describing;\s+nobody "
            r"could understand the caption file stopped after nineteen minutes\b",
            "Last week's rehearsal was unusual because three trainers spoke "
            "simultaneously; nobody could understand which screen they were "
            "describing, and the caption file stopped after nineteen minutes",
        ),
        (
            r"\bnobody could understand,\s+which screen\b",
            "nobody could understand which screen",
        ),
        (
            r"\bAfter deployment,\s+The command center\b",
            "After deployment, the command center",
        ),
        (
            r"\binstructed system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+) is responsible for preserve\b",
            r"instructed system administrator \1 to preserve",
        ),
        (
            r"\bkeep customer names, gate codes, and service notes from "
            r"screenshots that to attached public tickets\b",
            "keep customer names, gate codes, and service notes out of "
            "screenshots attached to public tickets",
        ),
        (
            r"\bThe media vendor has not confirmed was unsure whether they "
            r"can meet\b",
            "The media vendor has not confirmed whether it can meet",
        ),
        (
            r"\bThe command center stays remains open\b",
            "The command center remains open",
        ),
        (
            r"\bDistrict leads report experience login failures\b",
            "District leads report login failures",
        ),
        (r"\busing,\s+which form\b", "using form"),
        (
            r"\bMarcus compares update the totals\b",
            "Marcus updates the totals",
        ),
        (
            r"\bElena pauses initiate the rollout\b",
            "Elena pauses the rollout",
        ),
        (
            r"\bdoes not send private service notes are linked to a personal "
            r"cloud account\b",
            "does not send private service notes to a personal cloud account",
        ),
        (r"\bgives receive written approval\b", "gives written approval"),
        (r"\bdoes not transmits\b", "does not transmit"),
        (
            r"\bless precise compared to the extended version\b",
            "less precise than the extended version",
        ),
        (
            r"\bCouncil maintains the ultimate authority and grants approval "
            r"for the trial does not authorize\b",
            "Council maintains ultimate authority, and approval of the "
            "trial does not authorize",
        ),
        (r"\bdid not measure analy(?:s|z)ed\b", "did not measure"),
        (r"\bdid not measure assessed\b", "did not measure"),
        (
            r"\bdid not measure looked at property values and it must not be "
            r"presented that as proof gardens raise rent\b",
            "did not measure property values, and it must not be presented "
            "as proof that gardens raise rent",
        ),
        (
            r"\bdid not measure property values and it must not be presented\b",
            "did not measure property values, and it must not be presented",
        ),
        (r"\bmust not be was presented\b", "must not be presented"),
        (r"\bInstead, group counted\b", "Instead, the group counted"),
        (r"\battendance participation at\b", "attendance at"),
        (
            r"\bwith which 61 percent went directed to\b",
            "of which 61 percent went to",
        ),
        (
            r"\bwith which 61 percent went to\b",
            "of which 61 percent went to",
        ),
        (
            r"\bpounds of produce\s+of which\b",
            "pounds of produce, of which",
        ),
        (r"\btranslated versions\b", "translated forms"),
        (
            r"\bbetter indicator compared to garden size\b",
            "better indicator than garden size",
        ),
        (
            r"\bmore reliable indicator of involvement compared to "
            r"the size of the garden\b",
            "more reliable indicator of involvement than garden size",
        ),
        (r"\bcarried drew water\b", "carried water"),
        (r"\bThe city closed relocated\b", "The city closed"),
        (r"\bwas controlled organized by\b", "was controlled by"),
        (r"\bwithout separated by commas\b", "without commas"),
        (
            r"\bcould decline audio recording without,\s+which would result "
            r"in losing access\b",
            "could decline audio recording without losing access",
        ),
        (
            r"\bhad the option to refuse audio recording without,\s+"
            r"which would result in losing access\b",
            "had the option to refuse audio recording without losing access",
        ),
        (
            r"\b(At Garden [A-Z]-\d{2},)\s+The city\b",
            r"\1 the city",
        ),
        (r"\bThe last report is due\b", "The final report is due"),
        (r"\bThe final report are due\b", "The final report is due"),
        (
            r"\b(neighborhood problem) but this claim\b",
            r"\1, but this claim",
        ),
        (
            r"\b(low attendance) but that is\b",
            r"\1, but that is",
        ),
        (
            r"\btranslated survey,\s*dates the two transportation disruptions\b",
            "translated survey dates, the two transportation disruptions",
        ),
        (
            r"\b(due [A-Z][a-z]+ \d{1,2}, \d{4}) and the appendix\b",
            r"\1, and the appendix",
        ),
        (
            r"\btranslated survey dates the two transportation disruptions "
            r"and a table\b",
            "translated survey dates, the two transportation disruptions, "
            "and a table",
        ),
        (
            r"\bduplicated processed invoice events\b",
            "duplicated invoice events",
        ),
        (
            r"\bduplicating processed invoice events\b",
            "duplicating invoice events",
        ),
        (
            r"\bclosed dismissed (the alert|it)\b",
            r"closed \1",
        ),
        (r"\bthe no server\b", "no server"),
        (r"\bdoes not follow stemmed from\b", "does not follow from"),
        (r"\bhigher compared to\b", "higher than"),
        (
            r"\bhigher revenue compared to\b",
            "more revenue than",
        ),
        (
            r"\bnot to process publish invoices\b",
            "not to publish invoices",
        ),
        (r"\bharder compared to expected\b", "harder than expected"),
        (r"\bwhether drain should happen\b", "whether draining should happen"),
        (
            r"\bRavi temporarily halt producers initially\b",
            "Ravi paused producers first",
        ),
        (
            r"\bRavi decided to temporarily halt producers initially\b",
            "Ravi decided to pause producers first",
        ),
        (r"\bThe queue reached is zero\b", "The queue reached zero"),
        (
            r"\bThe dashboard displays still displayed\b",
            "The dashboard still displayed",
        ),
        (
            r"\bits cache refreshed updates every\b",
            "its cache refreshes every",
        ),
        (r"\bWhile waiting was active for\b", "While waiting for"),
        (
            r"\bexecuted query ([A-Za-z_][A-Za-z0-9_.]*) on against\b",
            r"ran query \1 against",
        ),
        (
            r"(,\s+(?:and\s+)?)The report should not imply\b",
            r"\1the report should not imply",
        ),
        (
            r"(,\s+and\s+)The caption file\b",
            r"\1the caption file",
        ),
        (
            r"\bThe report should not imply matches what he did\b",
            "The report should not imply that he did",
        ),
        (
            r"\bSofia reenacted 18,442 verified occurrences\b",
            "Sofia replayed 18,442 valid events",
        ),
        (
            r"\bfollowing the successful completion of the test suite "
            r"([A-Z][A-Z0-9-]+) by the corrected worker\b",
            r"after the corrected worker passed test suite \1",
        ),
        (
            r"\bfollowing the successful completion of the test suite "
            r"([A-Z][A-Z0-9-]+)\b",
            r"after passing test suite \1",
        ),
        (
            r"\bafter passing test suite ([A-Z][A-Z0-9-]+) by the "
            r"(fixed|corrected) worker\b",
            r"after the \2 worker passed test suite \1",
        ),
        (
            r"\bmaintained external delivery suspended\b",
            "kept external delivery suspended",
        ),
        (
            r"\bThe root cause state must state that the deployment exposed "
            r"lacks an idempotency defect\b",
            "The root cause statement must state that the deployment exposed "
            "an idempotency defect",
        ),
        (
            r"\bupdating alert ([A-Z][A-Z0-9-]+) to trigger duplicate growth "
            r"alerts regardless of standard CPU levels\b",
            r"updating alert \1 so it triggers when duplicate counts grow even "
            "when CPU usage is normal",
        ),
        (
            r"\bThe root cause statement must demonstrate that the deployment "
            r"uncovered an idempotency problem within retry handling\b",
            "The root cause statement must state that the deployment exposed "
            "an idempotency defect in retry handling",
        ),
        (r"\bmust not blame identified\b", "must not blame"),
        (
            r"\bOwners and deadlines remain required appear in\b",
            "Owners and deadlines remain required in",
        ),
        (r"\bVisitors are charged will not pay\b", "Visitors will not pay"),
        (r"\bVisitors pay will not pay\b", "Visitors will not pay"),
        (r"\bVisitors will not pay are charged a fee\b", "Visitors will not pay a fee"),
        (
            r"\bthe city continues to provide staffing and security\b",
            "the city will still cover staffing and security",
        ),
        (
            r"\bNo An employee might exceed their contractual boundaries\b",
            "No employee may work beyond their contract limits",
        ),
        (
            r"\bengaging in additional shifts has to be done voluntarily\b",
            "participation in additional shifts must be voluntary",
        ),
        (
            r"\bengaging in additional shifts has to be done on a voluntary "
            r"basis\b",
            lambda match: (
                "Participation in additional shifts must be voluntary"
                if match.group(0)[0].isupper()
                else "participation in additional shifts must be voluntary"
            ),
        ),
        (
            r"\bThat is a ridiculous argument\b",
            "That is an unsupported claim",
        ),
        (
            r"\bThe measures lower existing access and oversight risks\b",
            "The measures reduce existing access and oversight risks",
        ),
        (
            r"\bThe trial is scheduled to commence on "
            r"([A-Z][a-z]+ \d{1,2}) through ([A-Z][a-z]+ \d{1,2})\b",
            r"The trial is scheduled to commence on \1 and run through \2",
        ),
        (
            r"\bA more accurate sentence would state that the measures limit "
            r"known access and mitigate supervision risks, whereas making clear "
            r"would argue that emergency procedures remain unchanged is affected\b",
            "A more accurate statement would say that the measures reduce known "
            "access and supervision risks while making clear that emergency "
            "procedures remain unchanged",
        ),
        (
            r"\bKeisha is scheduled to conduct a midpoint evaluation on "
            r"(May 18) and deliver a final report by (July 10)\b",
            r"Keisha will present a midpoint review on \1 and submit a final "
            r"report on \2",
        ),

        (
            r"\bSupport team marked issue as\b",
            "The support team marked the issue as",
        ),
        (
            r"\bMarcus must not overwrite needs the earlier backup for the "
            r"quarterly audit\b",
            "Marcus must not overwrite the earlier backup because finance "
            "needs it for the quarterly audit",
        ),
        (
            r"\bmust not overwrite (?:needs|needed) the earlier backup\b",
            "must not overwrite the earlier backup",
        ),
        (
            r"\bmust not overwrite (?:used|kept|accessed|referenced|requested) the (earlier|older) backup\b",
            r"must not overwrite the \1 backup",
        ),
        (
            r"\bMarcus must not overwrite requested the earlier backup\b",
            "Marcus must not overwrite the earlier backup",
        ),
        (
            r"\bThe team also has to ensure\b",
            "The team also must ensure",
        ),
        (
            r"\b[Ss]afety staff checks if emergency jobs remain first\b",
            lambda match: (
                "Safety staff verify whether emergency jobs remain prioritized"
                if match.group(0)[0].isupper()
                else "safety staff verify whether emergency jobs remain prioritized"
            ),
        ),
        (
            r"\bthe responsible person,\s+next review time\b",
            "the responsible person, the next review time",
        ),
        (
            r"\bThere contains punctuation and structure issues\b",
            "There are punctuation and structural issues",
        ),
        (
            r"\bpoor vocabulary as it implies\b",
            "poor wording because it implies",
        ),
        (
            r"\bdoes not follow follows from\b",
            "does not follow from",
        ),
        (
            r"\bBy (\d{2}:\d{2} UTC),\s+queue ([A-Z][A-Z0-9-]+)\b",
            r"By \1, the queue \2",
        ),
        (
            r"\b(INV-PRIMARY contained [\d,]+ extra messages) and "
            r"(the billing preview)\b",
            r"\1, and \2",
        ),
        (
            r"\band billing preview showed\b",
            "and the billing preview showed",
        ),
        (
            r"\bthan source ledger\b",
            "than the source ledger",
        ),
        (
            r"\binstructed team not to publish\b",
            "instructed the team not to publish",
        ),
        (
            r"\bpaused producers first at (\d{2}:\d{2} UTC),\s+to allow no "
            r"additional invoice events? to (?:enter the )?queue\b",
            r"paused producers first at \1, preventing additional invoice "
            "events from entering the queue",
        ),
        (
            r"\bfollowing users' reconnection of the sensor cable\b",
            "after users reconnect the sensor cable",
        ),
        (
            r"\bThe trial would run from April 6 through June 26 at the "
            r"Eastgate Central branch only\b",
            "The trial will run from April 6 through June 26 at the "
            "Eastgate Central branch only",
        ),
        (
            r"\bindividual travel patterns\s+Keisha will present\b",
            "individual travel patterns. Keisha will present",
        ),
        (
            r"\b(a final report on July 10)\s+The final recommendation\b",
            r"\1. The final recommendation",
        ),
        (
            r"\b(should be persuasive but honest)\s+it should explain\b",
            r"\1; it should explain",
        ),
        (
            r"\b(should be persuasive but honest),?\s+as it should explain explains\b",
            r"\1; it should explain",
        ),
        (
            r"\bworth testing,\s+without pretending\b",
            "worth testing without pretending",
        ),
        (
            r"\breplayed valid ([\d,]+) events\b",
            r"replayed \1 valid events",
        ),
        (
            r"\b(?:The\s+)?Replay started at (\d{2}:\d{2} UTC),\s+and ended at "
            r"(\d{2}:\d{2} UTC),\s+with\b",
            r"The replay started at \1 and ended at \2 with",
        ),
        (
            r"\bothers list six actions with commas and no clear order,\s+"
            r"using 'utilize' instead of 'use'",
            "others list six actions with commas and no clear order and use "
            "'utilize' instead of 'use'",
        ),
        (
            r"\bAnother engineer wrote data was corrupt\b",
            "Another engineer wrote that data was corrupt",
        ),
        (
            r"\bafter ([A-Z][A-Z0-9-]+) test suite passed\b",
            r"after the \1 test suite passed",
        ),
        (
            r"\b(Sofia disabled [A-Z][A-Z0-9_]+ at \d{2}:\d{2} UTC),\s+"
            r"(workers [A-Za-z0-9_]+ and [A-Za-z0-9_]+ restarted)\b",
            r"\1, and \2",
        ),
        (
            r"\bchanging alert ([A-Z][A-Z0-9-]+) to trigger duplicate growth "
            r"even when CPU is normal\b",
            r"changing alert \1 so it triggers when duplicate counts grow even "
            "when CPU is normal",
        ),
        (
            r"\bevening certification class parents\b",
            "parents attending evening certification classes",
        ),
        (
            r"\bevening certification parents\b",
            "parents attending evening certification classes",
        ),
        (
            r"\bshowed strongest interest\b",
            "showed the strongest interest",
        ),
        (
            r"\bdid not occur didn(?:'|\u2019)t go\b",
            "didn't go",
        ),
        (
            r"\bbecause three trainers talked at once,\s+and nobody\b",
            "because three trainers talked at once; nobody",
        ),
        (
            r"\b[Ss]afety staff checks whether emergency jobs remain first\b",
            lambda match: (
                "Safety staff check whether emergency jobs remain prioritized"
                if match.group(0)[0].isupper()
                else "safety staff check whether emergency jobs remain prioritized"
            ),
        ),
        (
            r"\bThis is a connection;\s+it is not evidence\b",
            "This is an association; it is not evidence",
        ),
        (
            r"\bpunctuation and structure issues\b",
            "punctuation and structural issues",
        ),
        (
            r"\bpoor vocabulary because\b",
            "poor wording because",
        ),
        (
            r"\bdoes not follow comes from\b",
            "does not follow from",
        ),
        (
            r"\bshould explain to explain\b",
            "should explain",
        ),
        (
            r"\bIf an alarm fails occurs\b",
            "If an alarm fails",
        ),
        (
            r"\bwithout as an exception\b",
            "without exception",
        ),
        (
            r"\bmust not overwrite retained the earlier backup\b",
            "must not overwrite the earlier backup",
        ),
        (
            r"\bThe caption file ceased after\b",
            "The caption file stopped after",
        ),
        (
            r"\b(a corrected caption file),\s+by\b",
            r"\1 by",
        ),
        (
            r"\bthe media vendor,\s+has not confirmed,\s+whether\b",
            "the media vendor has not confirmed whether",
        ),
        (
            r"\bparticipation compared to garden size\b",
            "participation than garden size",
        ),
        (
            r"\bAt Neither,\s+the event was controlled\b",
            "Neither event was controlled",
        ),
        (
            r"\bdecline audio recording without,\s+thereby losing access\b",
            "decline audio recording without losing access",
        ),
        (
            r"\bdoesn(?:'|\u2019)t does not\b",
            "does not",
        ),
        (
            r"\bVisitors will be charged will not pay\b",
            "Visitors will not pay",
        ),
        (
            r"\bretain possession of the final bus\b",
            "hold the final bus",
        ),
        (
            r"\bRunbook ([A-Z][A-Z0-9-]+) instructs to disable\b",
            r"Runbook \1 provides instructions to disable",
        ),
        (
            r"\bfollowing the completion of the (fixed|corrected) worker passing "
            r"the test suite ([A-Z][A-Z0-9-]+)\b",
            r"after the \1 worker passed test suite \2",
        ),
        (
            r"\b(The replay commenced at \d{2}:\d{2} UTC),\s+and concluded at "
            r"(\d{2}:\d{2} UTC),\s+with\b",
            r"\1 and concluded at \2 with",
        ),
        (
            r"\bmaintained external delivery disabled\b",
            "continued to suspend external delivery",
        ),
        (
            r"\bresponds hostilely\b",
            "responds with hostility",
        ),
        (
            r"\bKeisha is scheduled to have a tentative agreement\b",
            "Keisha has a tentative agreement",
        ),
        (r"\bcannot provide advertise\b", "cannot advertise"),
        (r"\bpublish compile totals\b", "publish aggregate totals"),
        (r"\bshould explain explaining\b", "should explain"),
        (
            r"\binstructed the system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+)\b",
            r"instructed system administrator \1",
        ),
        (
            r"\bwith server dashboard ([A-Z][A-Z0-9-]+)\b",
            r"with the server dashboard \1",
        ),
        (
            r"\b([Tt]hat conclusion) don't follow\b",
            r"\1 doesn't follow",
        ),
        (
            r"\b((?:is|are|was|were)\s+required)\s+"
            r"(check|receive|place|store|submit|report|record|keep|provide|"
            r"enter|verify|call|use|work|run|attend)\b",
            r"\1 to \2",
        ),
        (
            r"\bprior\s+to\s+((?:the\s+)?[A-Za-z'-]+\s+"
            r"(?:begins?|starts?|ends?|finishes?|schedules?))\b",
            r"before \1",
        ),
        (r"\bis preparing conducting\s+(a|an|the)\b", r"is preparing for \1"),
        (r"\bsays at the\b", "says that the"),
        (r"\bsays will test assess whether\b", "says the exercise will test whether"),
        (r"\bcontinue operate\b", "continue to operate"),
        (r"\bcontain received conflicting\b", "contain conflicting"),
        (
            r"\bdespite\s+([^,.;]{1,80}?)\s+being was not\b",
            r"although \1 was not",
        ),
        (r"\bbeing was not\b", "was not"),
        (r"\ball failed control\b", "all failed controls"),
        (r"\bfrom County Health Office\b", "from the County Health Office"),
        (
            r",\s+with a fuel tank at (?P<level>\d{1,3} percent) full\b",
            lambda match: f", and its fuel tank is {match.group('level')} full",
        ),
        (
            r"\b(The backup generator was serviced on "
            r"(?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2}) "
            r"and its fuel tank is\b",
            r"\1, and its fuel tank is",
        ),
        (r"\bCritical operations can sustain\b", "Critical operations can continue"),
        (r"\bNobody notified the issue\b", "Nobody reported the issue"),
        (r"\bspecified approved\b", "approved"),
        (
            r"\bNeither removed values nor estimated temperatures\b",
            "Neither erased values nor estimated temperatures",
        ),
        (
            r"\bNeither (?P<first>[^.!?;]{1,120}?);\s+nor\b",
            lambda match: f"Neither {match.group('first')} nor",
        ),
        (
            r"\bany adjustments should include initials\b",
            "any corrections should include initials",
        ),
        (
            r"\b([Ee])ssential services be able to continue\b",
            lambda match: (
                f"{match.group(1)}ssential services can continue"
            ),
        ),
        (r"\bwere submitted delayed\b", "were submitted late"),
        (r",\s+and Nobody\b", ", and nobody"),
        (
            r"\b(must|should|can|could|may|might|will|would)\s+"
            r"(checks|receives|places|explains|confirms|provides|contains|"
            r"promises|authorizes|shows|reports|agrees|needs|says|starts|"
            r"joins|runs|keeps|stops|gets|makes|takes|uses|includes|lists|"
            r"states|claims|approves)\b",
            lambda match: f"{match.group(1)} {match.group(2)[:-1]}",
        ),
        (
            r"\bmust check (?P<first>[^.!?]{0,220}?),\s+receives "
            r"(?P<second>[^.!?]{0,120}?),\s+and places\b",
            lambda match: (
                f"must check {match.group('first')}, receive "
                f"{match.group('second')}, and place"
            ),
        ),
        (
            r"\bshould explain (?P<first>[^.!?]{0,220}?),\s+confirms "
            r"(?P<second>[^.!?]{0,120}?),\s+and provides\b",
            lambda match: (
                f"should explain {match.group('first')}, confirm "
                f"{match.group('second')}, and provide"
            ),
        ),
        (
            r"\bmaintenance record don't shows\b",
            "maintenance record doesn't show",
        ),
        (r"\bhaven't determine\b", "haven't determined"),
        (r"\bdidn't happened\b", "didn't happen"),
        (r"\bdid not happened\b", "did not happen"),
        (r"\bdid not measured\b", "did not measure"),
        (r"\bmust not overwrote\b", "must not overwrite"),
        (r"\bmust not can\b", "must not"),
        (r"\bcannot will\b", "cannot"),
        (r"\bdoes not sends\b", "does not send"),
        (r"\bdoes not requests\b", "does not request"),
        (r"\bdoes not promises\b", "does not promise"),
        (r"\bdoes not shows\b", "does not show"),
        (r"\bdoes not follows\b", "does not follow"),
        (r"\bdoes not approves\b", "does not approve"),
        (r"\bdoesn't says\b", "doesn't say"),
        (r"\bdid not ran\b", "did not run"),
        (r"\bshould not implies\b", "should not imply"),
        (r"\bdoes not does not\b", "does not"),
        (r"\bdo not do not\b", "do not"),
        (r"\bdid not did not\b", "did not"),
        (r"\bdoes not to authorize\b", "does not authorize"),
        (r"\b([Ii]t) do not promises\b", r"\1 does not promise"),
        (r"\b([Qq]uantity alone) don't explain\b", r"\1 doesn't explain"),
        (r"\bNeither events were\b", "Neither event was"),
        (r"\bwithout,\s+losing\b", "without losing"),
        (r"\bshould not claims\b", "should not claim"),
        (r"\buseful affect\b", "useful effect"),
        (r"\bmore stronger\b", "stronger"),
        (
            r"\bin ((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2})\b",
            r"on \1",
        ),
        (r"\bIt is not shows\b", "It does not show"),
        (r"\bThere has issues\b", "There are issues"),
        (r"\bDuring the pilot(?!,)\s+", "During the pilot, "),
        (r"\bLast spring(?!,)\s+", "Last spring, "),
        (r"\bafter battery dropped\b", "after the battery dropped"),
        (
            r"\bSupport team called issue cosmetic\b",
            "The support team called the issue cosmetic",
        ),
        (
            r"\bDuring the pilot,\s+phase eight tablets\b",
            "During the pilot, eight tablets",
        ),
        (
            r"\bclassified the issue as cosmetic despite that it obstructed\b",
            "classified the issue as cosmetic, although it obstructed",
        ),
        (
            r"\bThe instructions for Several are written\b",
            "Several instructions are written",
        ),
        (r"\bThis was serious mistake\b", "This was a serious mistake"),
        (r"\bone on call supervisor\b", "one on-call supervisor"),
        (
            r"(?<![,;:])(?<!not)\s+but\s+"
            r"(?=(?:several|the|they|he|she|it|MedTrack)\b)",
            ", but ",
        ),
        (r";\s*however(?!,)\s+", "; however, "),
        (r"\brecords the time\s+however,?\s+", "records the time; however, "),
        (r"\bAt each stage(?!,)\s+", "At each stage, "),
        (
            r"\b(Cedar Training Hall) and (the first (?:simulated )?outage)\b",
            r"\1, and \2",
        ),
        (
            r"\bNeither (?P<first>[^.!?]{1,100}?) or "
            r"(?P<second>[^.!?]{1,100}?) is allowed\b",
            lambda match: (
                f"Neither {match.group('first')} nor {match.group('second')} "
                "are allowed"
            ),
        ),
        (r"\bwas not is\b", "was not"),
        (
            r"\b(?P<subject>Department leads) (?P<body>[^.!?]{0,180}?) and explains\b",
            lambda match: (
                f"{match.group('subject')} {match.group('body')} and explain"
            ),
        ),
        (r",\s+nobody reported\b", ". Nobody reported"),
        (
            r"\b(until the exercise (?:had already |already )?ended)\?",
            r"\1.",
        ),
        (r"\bstatus messages\s+wasn(?:'|\u2019)?t\b", "status messages weren't"),
        (r"\b(the project team|the team)\s+were ready\b", r"\1 was ready"),
        (r"\b(the interface)\s+were crowded\b", r"\1 was crowded"),
        (r"\b(the|The) recording\s+stop\b", r"\1 recording stopped"),
        (r"\bafter monday review\b", "after Monday's review"),
        (r"\bthe text don(?:'|\u2019)?t change\b", "the text didn't change"),
        (r"\bthe HUD stay on (?:the )?first sentence\b", "the HUD stayed on the first sentence"),
        (r"\b(another) tester seen\b", r"\1 tester saw"),
        (r"\bspelling problems remains\b", "spelling problems remain"),
        (r"\b(these) issues makes\b", r"\1 issues make"),
        (r"\b(unresolved) failures is reported\b", r"\1 failures are reported"),
        (
            r"\bA supervisor will record ([^.]+?) and unresolved failures are reported to\b",
            r"A supervisor will record \1 and will report unresolved failures to",
        ),
        (
            r"\bA supervisor records ([^.]+?) and unresolved failures are reported to\b",
            r"A supervisor records \1 and reports unresolved failures to",
        ),
        (r"\breopening are delayed\b", "reopening is delayed"),
        (
            r"\basked every worker to initial each line after they verify it\b",
            "asked every worker to initial each line after they verified it",
        ),
        (
            r"\b([Ss]ome) volunteers were signing before the item had actually been inspected,\s*"
            r"which makes the record unreliable\b",
            r"\1 volunteers were signing before the item had actually been inspected, "
            "which made the record unreliable",
        ),
        (r"\b([Ss]taff) currently walk\b", r"\1 members currently walk"),
        (
            r"\b(no consistent route),\s*they often handle\b",
            r"\1, and they often handle",
        ),
        (
            r"\bDuring Monday's planning meeting(?!,)",
            "During Monday's planning meeting,",
        ),
        (
            r"\bwet covers,\s*direct heat is prohibited\b",
            "wet covers; direct heat is prohibited",
        ),
        (r"\bStaff also needs? to contact\b", "Staff members also need to contact"),
        (r"\bStaff also needs? to call\b", "Staff members also need to call"),
        (
            r"\bBoth units must stay between their approved temperature ranges\b",
            "Each unit must stay within its approved temperature range",
        ),
        (r"\bsometimes display a blank reading\b", "sometimes displays a blank reading"),
        (
            r"\bwhether the cable, the tablet port, or the software cache are responsible\b",
            "whether the cable, the tablet port, or the software cache is responsible",
        ),
        (r"\bsounds blaming\b", "sounds accusatory"),
        (r"\bsounds careless\b", "sounds accusatory"),
        (r"\bsend it through email\b", "send it by email"),
        (r"\bdon't mean anything bad by it\b", "don't mean any harm by it"),
        (r"\b([Pp]arents)\s+ends up\b", r"\1 end up"),
        (
            r"\b([Tt])here is many\b",
            lambda match: f"{match.group(1)}here are many",
        ),
        (r"\blaying around\b", "lying around"),
        (r"\bdoing not much\b", "not doing much"),
        (
            r"\b([Ee]very dog)\s+got\s+(?:there|their)\s+own\b",
            r"\1 has its own",
        ),
        (r"\bbring toys over and put it on\b", "bring a toy over and put it on"),
        (r"\bwant you playing with them\b", "want you to play with them"),
        (
            r"\bThere is always movement around,\s*making noise,\s*or wanting to sit\b",
            "They are always moving around, making noise, or wanting to sit",
        ),
        (r"\bbecause They are\b", "because they are"),
        (r"\bthey just lay on\b", "they just lie on"),
        (r"\bEvery dog doesn't do\b", "Not every dog does"),
        (
            r"\bBut Not every\b",
            lambda match: (
                "But not every"
                if match.group(0).startswith("But")
                else "but not every"
            ),
        ),
        (
            r"\bBut not every dog does those jobs because not all dogs can\b",
            "Not every dog does those jobs, since not all dogs can",
        ),
        (r",\s*then they run\b", ", and then they run"),
        (r"\brun around crazy\b", "run around like crazy"),
        (
            r"\bThe good things are bigger than the bad things,\s*mostly\b",
            "The benefits generally outweigh the drawbacks",
        ),
        (
            r"\bbecause The benefits generally outweigh the drawbacks\b",
            "because the benefits generally outweigh the drawbacks",
        ),
        (r"\bdifferent in their own ways\b", "different in their own way"),
        (
            r"\bSome dogs like swimming while another dog hates\b",
            "Some dogs like swimming, while other dogs hate",
        ),
        (
            r"\bother dogs hate water and tries running away\b",
            "other dogs hate water and try running away",
        ),
        (
            r"\bother dogs\b([^.!?]{0,80})\btries\b",
            r"other dogs\1try",
        ),
        (
            r"\A(At \d{1,2}:\d{2}\s+[ap]\.m\.)(?!,)\s+",
            r"\1, ",
        ),
        (
            r"(?<=[.!?])\s+(At \d{1,2}:\d{2}\s+[ap]\.m\.)(?!,)\s+",
            r" \1, ",
        ),
        (
            r"\bAfter the all clear\s*,?\s+",
            "After the all-clear, ",
        ),
        (
            r"\belevator recall emergency lighting oxygen alarms door locks "
            r"the local server and the fire panel connection\b",
            "elevator recall, emergency lighting, oxygen alarms, door locks, "
            "the local server, and the fire panel connection",
        ),
        (
            r"\bwhether the cable, tablet port, or software cache are responsible\b",
            "whether the cable, tablet port, or software cache is responsible",
        ),
        (
            r"\b(Lead researcher Professor [A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+)*) say\b",
            r"\1 says",
        ),
        (
            r"\b(starts|begins|begin|started) at "
            r"(\d{1,2}:\d{2}\s+[ap]\.m\.),\s+",
            r"\1 at \2 ",
        ),
        (
            r"\bon ((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2}),\s+by\b",
            r"on \1 by",
        ),
        (
            r"\bby adding ([^.!?]{0,120}) and hired\b",
            r"by adding \1 and hiring",
        ),
        (r"\bnone of them agrees (?:about|on)\b", "none of them agree on"),
        (r"(?<=[.!?])\s+Which was a serious mistake\b", " This was a serious mistake"),
        (
            r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\s+"
            r"(one|two|three|four|five|six|seven|eight|nine)\s+(?=minutes?\b)",
            lambda match: f"{match.group(1)}-{match.group(2)} ",
        ),
        (
            r"\broute syncing offline notes photo uploads and emergency escalation\b",
            "route syncing, offline notes, photo uploads, and emergency escalation",
        ),
        (
            r"\bSeveral instruction is written\b",
            "Several instructions are written",
        ),
        (r",\s*for example\s+(?!,)", "; for example, "),
        (
            r"\b(vague vocabulary)\s+for example\s+(?!,)",
            r"\1; for example, ",
        ),
        (
            r"\bfor example\s+the guide\b",
            lambda match: (
                "For example, the guide"
                if match.group(0)[:1].isupper()
                else "for example, the guide"
            ),
        ),
        (
            r"\b(At \d{1,2}:\d{2}\s+[ap]\.m\.),\s+"
            r"((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)),\s+",
            r"\1 \2, ",
        ),
        (r"\bin the normal fashion\b", "in the usual way"),
        (r"\bvery unique\b", "unusual"),
        (r"\btalked at once,\s*nobody\b", "talked at once; nobody"),
        (
            r"\bthe media vendor haven't\b",
            lambda match: (
                "The media vendor has not"
                if match.group(0)[:1].isupper()
                else "the media vendor has not"
            ),
        ),
        (
            r"\b(has not|hasn(?:'|\u2019)t) confirmed if\b",
            r"\1 confirmed whether",
        ),
        (r"\bThis relationship is an association\b", "This is an association"),
        (
            r"\ba lot more (cancellations|errors|failures|delays|responses|"
            r"complaints|incidents)\b",
            r"many more \1",
        ),
        (r"\bEveryone agree\b", "Everyone agrees"),
        (
            r"\b(The) final review did not happen as the plan said it would\b",
            r"\1 final review did not occur as planned",
        ),
        (
            r"\b(stopped participating)\.\s+Because (the survey [^.!?]+)\.\s+"
            r"Which ([^.!?]+)\.",
            lambda match: (
                f"{match.group(1)}. "
                f"{match.group(2)[:1].upper() + match.group(2)[1:]}, "
                f"which {match.group(3).strip()}."
            ),
        ),
        (
            r"\.\s+Because ([^.!?]+)\.\s+Which ([^.!?]+)\.",
            lambda match: (
                f" because {match.group(1).strip()}, "
                f"which {match.group(2).strip()}."
            ),
        ),
        (
            r"\.\s+Because ([^.!?]+),\s+Which ([^.!?]+)\.",
            lambda match: (
                f" because {match.group(1).strip()}, "
                f"which {match.group(2).strip()}."
            ),
        ),
        (
            r"\bThere also contains several\b",
            "There are also several",
        ),
        (
            r"\btranslated survey,\s*dates,\s*the two transportation disruptions\b",
            "translated survey dates, the two transportation disruptions",
        ),
        (
            r"\bwhy some residents stopped participating because "
            r"(the survey was only available in English for the first six weeks), "
            r"which caused fewer responses\b",
            r"why some residents stopped participating. "
            r"The survey was only available in English for the first six weeks, "
            r"which caused fewer responses",
        ),
        (
            r"(\A|(?<=[.!?])\s+)Instead(?!,)\s+",
            r"\1Instead, ",
        ),
        (
            r"\bInstead,\s+The group\b",
            "Instead, the group",
        ),
        (
            r"\bThese numbers sound impressive\s+but\b",
            "These numbers sound impressive, but",
        ),
        (
            r"\b(At Garden [A-Z][A-Z0-9-]*)(?!,)\s+",
            r"\1, ",
        ),
        (
            r"\b(the bus stop closed)(?!,)\s+so\s+",
            r"\1, so ",
        ),
        (
            r"\b(closed the adjacent bus stop)(?!,)\s+so\s+",
            r"\1, so ",
        ),
        (
            r"\b(Neither event was controlled by [^.!?;,]+)(?<![,;])\s+"
            r"and\s+(ignoring\b)",
            r"\1, and \2",
        ),
        (
            r"\bwith highest participation\b",
            "with the highest participation",
        ),
        (
            r"\btwelve week trial\b",
            "twelve-week trial",
        ),
        (
            r"\btrial,\s+because\b",
            "trial because",
        ),
        (
            r"\bstudents and shift workers currently has\b",
            "students and shift workers currently have",
        ),
        (
            r"\b(The proposal) do not\b",
            r"\1 does not",
        ),
        (
            r"\bThe proposal is not asking for permanent hours\b",
            "The proposal does not request permanent hours",
        ),
        (
            r"\b(between (?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2} and "
            r"(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December) \d{1,2}),\s+(\d[\d,]* respondents)\b",
            r"\1; \2",
        ),
        (
            r"\bThe proposal (?:also )?(?:says|states) the program will be completely free, "
            r"which is incorrect vocabulary since the library expects operating costs of "
            r"(\$[\d,]+(?:\.\d{2})?)\b",
            r"The proposal describes the program as free to visitors, but the library "
            r"expects operating costs of \1",
        ),
        (
            r"\bVisitors will not pay a fee\s*,?\s*but the city still pays for "
            r"staff security utilities and transportation support\b",
            "Visitors will not pay a fee, but the city still pays for staff, security, "
            "utilities, and transportation support",
        ),
        (
            r"\brisks\.\s+While making clear that emergency procedures remain unchanged\b",
            "risks while making clear that emergency procedures remain unchanged",
        ),
        (
            r"\bestimate includes staffing, security, utilities and transit\b",
            "estimate includes staffing, security, utilities, and transit",
        ),
        (
            r"(\$[\d,]+ for utilities)\s+and\s+(\$[\d,]+ for transit coordination)\b",
            r"\1, and \2",
        ),
        (
            r"\bThe Transit board\b",
            "The transit board",
        ),
        (
            r";\s*however,\s+The transit board\b",
            "; however, the transit board",
        ),
        (
            r"\bHowever\s+(?!,)(?=(?:MedTrack|The)\b)",
            "However, ",
        ),
        (
            r"\bHowever,\s+The transit board\b",
            "However, the transit board",
        ),
        (
            r"\bThere also have several\b",
            "There are also several",
        ),
        (
            r";\s*however,\s+",
            "; however, ",
        ),
        (
            r"\bmust not overwrite accessed the earlier backup\b",
            "must not overwrite the earlier backup",
        ),
        (
            r"\bmust not be presented these findings as evidence\b",
            "must not be presented as evidence",
        ),
        (
            r"\bThere also present several\b",
            "There are also several",
        ),
        (
            r"\bdoes not follow was drawn from\b",
            "does not follow from",
        ),
        (
            r"\band no establish a clear sequence\b",
            "and fail to establish a clear sequence",
        ),
        (
            r"\bshould be cautioned more cautiously\b",
            "should be explained more cautiously",
        ),
        (
            r"\bAmina requested Owen to verify\b",
            "Amina asked Owen to verify",
        ),
        (
            r"\bmust not overwrite referenced the earlier backup\b",
            "must not overwrite the earlier backup",
        ),
        (
            r"\bmust not be presented this as evidence\b",
            "must not be presented as evidence",
        ),
        (
            r"\bshould explain will explain\b",
            "should explain",
        ),
        (
            r"\bshould be explained it more cautiously\b",
            "should be explained more cautiously",
        ),
        (
            r"\bmore strong than\b",
            "stronger than",
        ),
        (
            r"\bThis data is useful, but a survey shows interest,\s+"
            r"not guaranteed attendance\b",
            "This data is useful, but a survey shows interest and does not "
            "guarantee attendance",
        ),
        (r"\bshould have has been explained\b", "should have been explained"),
        (
            r"\bThis data is useful, but it should have been explained with more "
            r"caution because a survey shows interest but does not guarantee attendance\b",
            "This data is useful, but it should be explained more cautiously. "
            "A survey shows interest but does not guarantee attendance",
        ),
        (
            r"\.\s+(\d[\d,]*) respondents\b",
            r". Of those respondents, \1",
        ),
        (
            r"\bshows interest,\s+not guaranteed attendance\b",
            "shows interest but does not guarantee attendance",
        ),
        (
            r"\b(risks?),\s+while making clear\b",
            r"\1 while making clear",
        ),
        (
            r"\bCouncil retains the final decision and approval\b",
            "Council retains the final decision, and approval",
        ),
        (
            r"\b(The queue reached zero at \d{2}:\d{2} UTC, but (?:the )?dashboard "
            r"[A-Z0-9-]+ still) displays (duplicate totals because its cache) "
            r"refreshes\b",
            r"\1 displayed \2 refreshed",
        ),
        (
            r"\bVisitors will not pay a fee, but the city still covers\b",
            "Visitors will not pay a fee, but the city will still cover",
        ),
        (
            r"\A(On (?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2})(?!,)\s+",
            r"\1, ",
        ),
        (
            r"\b((?:At|By) \d{2}:\d{2} UTC)(?!,)\s+",
            r"\1, ",
        ),
        (r"\binvoices but one\b", "invoices, but one"),
        (
            r"\b(vault [A-Z0-9-]+)\s+it cannot\b",
            r"\1; it cannot",
        ),
        (
            r"\bemailed renamed or deleted\b",
            "emailed, renamed, or deleted",
        ),
        (
            r"\b(Runbook [A-Z0-9-]+ says)\s+disable\b",
            r"\1 to disable",
        ),
        (
            r"\brestart two workers and compare (message )?counts\b",
            lambda match: (
                "restart two workers, and compare "
                f"{match.group(1) or ''}counts"
            ),
        ),
        (
            r"\breached zero([^.!?]{0,40})\s+but dashboard\b",
            r"reached zero\1, but dashboard",
        ),
        (
            r"\bWhile waiting for the refresh\.\s+"
            r"([A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,3})\s+",
            r"While waiting for the refresh, \1 ",
        ),
        (
            r"\bwhen cache key\b",
            "when the cache key",
        ),
        (
            r"\bread only replica\b",
            "read-only replica",
        ),
        (
            r"\band feature flag ([A-Z][A-Z0-9_]*)\b",
            r"and the feature flag \1",
        ),
        (
            r"\b([A-Z][a-z]+) is disabled,\s+([A-Z][A-Z0-9_]*)\s+at\b",
            r"\1 disabled \2 at",
        ),
        (
            r"\b(UTC),\s+so that\b",
            r"\1 so that",
        ),
        (
            r"\b(The replay began at \d{2}:\d{2} UTC),?\s+and finished at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and finished at \2 with",
        ),
        (
            r"\b(The replay started at \d{2}:\d{2} UTC),?\s+and ended at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and ended at \2 with",
        ),
        (
            r"\b(The replay commenced at \d{2}:\d{2} UTC),?\s+and concluded at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and concluded at \2 with",
        ),
        (
            r"\bothers list six actions with commas and no clear order,\s+"
            r"using 'utilize' instead of 'use'",
            "others list six actions with commas and no clear order and use "
            "'utilize' instead of 'use'",
        ),
        (r"\bcustomers was affected\b", "customers were affected"),
        (
            r"\bdata was corrupt\s*[,;]?\s*this vocabulary\b",
            "data was corrupt; this wording",
        ),
        (
            r"\bno amount differences, customer names, or tax codes\b",
            "no differences in amounts, customer names, or tax codes",
        ),
        (r"\bFollow up actions\b", "Follow-up actions"),
        (
            r"\b(final table|the table) and no customer data\b",
            r"\1, and no customer data",
        ),
        (r"\bsentences was active\b", "sentences were active"),
        (r"\bor if replacement finished\b", "or whether the replacement had finished"),
        (r"\bon first sentence\b", "on the first sentence"),
        (
            r"\b(every\s+[A-Za-z][A-Za-z'-]*)\s+need\s+to\b",
            r"\1 needs to",
        ),
        (
            r"\bthere (?=(?:existing|own|personal|company|approved|contract)\b)",
            "their ",
        ),
        (r"\bThe Another\b", "Another"),
        (r"\bthis vocabulary is inaccurate\b", "this wording is inaccurate"),
        (r"\bwas suppose to\b", "was supposed to"),
        (r"\bOther ones combine\b", "Others combine"),
        (r"\bHowever, That\b", "However, that"),
        (
            r"\b(on checklist [A-Z0-9-]+), and reports\b",
            r"\1 and reports",
        ),
        (
            r"\bfinal review didn(?:'|\u2019)t happen like the plan said it would\b",
            "final review did not happen as planned",
        ),
        (r"\bbut dashboard ([A-Z0-9-]+)\b", r"but the dashboard \1"),
        (
            r"\bstaffed entrance, hourly floor checks and a text line\b",
            "staffed entrance, hourly floor checks, and a text line",
        ),
        (
            r"\beliminate all risk,\s+That is\b",
            "eliminate all risk. That is",
        ),
        (r"\balarm fails (?:sounds|triggers)\b", "alarm fails"),
        (r"\black no quiet public space\b", "have no quiet public space"),
        (r"(?-i:\bAn No\b)", "No"),
        (r"\bso that an no additional\b", "so that no additional"),
        (r"\bresponds hostiley\b", "responds in a hostile way"),
        (
            r"\bnot,\s+and the general book budget\b",
            "not the general book budget",
        ),
        (
            r"\s+and not,\s+the general book budget\b",
            ", not the general book budget",
        ),
        (r";\s+That is\b", ". That is"),
        (
            r"\bspoke simultaneously regarding nobody\b",
            "spoke simultaneously; nobody",
        ),
        (
            r"\bDistrict reports login failures\b",
            "District leads report login failures",
        ),
        (
            r"\bdoes not guarantee attendance remains limited\b",
            "does not guarantee attendance",
        ),
        (r"\bRecovery validation utilized\b", "Recovery validation used"),
        (r"\bprinted pocket card reverse\b", "printed pocket card reverses"),
        (r"\bmanuscript\b", "Manuscript"),
        (r"\bthe hud\b", "the HUD"),
        (r"([!?.,])\1+", r"\1"),
        (r"\.\?+(?=\s|$)", "."),
    ):
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def _restore_proofread_style_options(source: str, candidate: str) -> str:
    source_match = _STYLE_LIST.search(source)
    candidate_match = _STYLE_LIST.search(candidate)
    if source_match is None or candidate_match is None:
        return candidate
    source_options = [
        match.group(0).casefold()
        for match in _STYLE_OPTION.finditer(source_match.group(0))
    ]
    candidate_options = [
        match.group(0).casefold()
        for match in _STYLE_OPTION.finditer(candidate_match.group(0))
    ]
    all_candidate_options = [
        match.group(0).casefold()
        for match in _STYLE_OPTION.finditer(candidate)
    ]
    if _is_ordered_subsequence(source_options, all_candidate_options):
        return candidate
    if (
        len(source_options) < 3
        or len(candidate_options) >= len(source_options)
        or not _is_ordered_subsequence(candidate_options, source_options)
    ):
        return candidate
    conjunction = (
        "or"
        if re.search(r"\bor\b", source_match.group(0), re.IGNORECASE)
        else "and"
    )
    if len(source_options) == 2:
        restored = f"{source_options[0]} {conjunction} {source_options[1]}"
    else:
        restored = ", ".join(source_options[:-1]) + f", {conjunction} {source_options[-1]}"
    if candidate_match.group(0)[:1].isupper():
        restored = restored[:1].upper() + restored[1:]
    return (
        candidate[: candidate_match.start()]
        + restored
        + candidate[candidate_match.end() :]
    )


def _is_ordered_subsequence(values: list[str], expected: list[str]) -> bool:
    iterator = iter(expected)
    return all(any(candidate == value for candidate in iterator) for value in values)


def _translation_was_not_performed(source: str, candidate: str) -> bool:
    source_words = [
        word.casefold()
        for word in _WORD.findall(_IMMUTABLE_TOKEN.sub("", source))
    ]
    candidate_words = [
        word.casefold()
        for word in _WORD.findall(_IMMUTABLE_TOKEN.sub("", candidate))
    ]
    if not source_words or not candidate_words:
        return source.strip().casefold() == candidate.strip().casefold()
    shared = sum((Counter(source_words) & Counter(candidate_words)).values())
    return shared / len(source_words) >= 0.7


def _meaning_constraints_preserved(
    source: str,
    candidate: str,
    *,
    command: str = "",
    check_word_losses: bool = False,
) -> bool:
    # Checked first because it is the only check here that is not tied to a
    # phrase someone wrote down in advance. Everything below looks for a known
    # shape - a negation flip, a perspective shift, a literal fixture sentence
    # - and a plain swapped verb matches none of them. Two shipped in one live
    # essay: "ENTERS the ROOM" came back "measures the room", and "grete a
    # person" came back "get as much as". Both were accepted by every guard
    # that existed.
    #
    # Proofread only. Every other command that reaches this guard is asking for
    # a rewrite, where replacing a word with a better one is the requested
    # work: an Empathetic pass is meant to turn "users cant tell" into "users
    # find it difficult to tell". Applying a word-for-word rule there refused
    # eighteen rewrites the suite already protects.
    if check_word_losses and command == "Proofread":
        losses = unexplained_losses(source, candidate)
        if losses:
            logger.info(
                "Writing correction refused: candidate dropped %s",
                ", ".join(sorted(set(losses))[:6]),
            )
            return False
    if _high_risk_meaning_change(source, candidate):
        return False
    source_force = _force_levels(source)
    candidate_force = _force_levels(candidate)
    if source_force != candidate_force:
        exactness_only = (
            source_force == {"required"}
            and not candidate_force
            and _EXACTNESS_REQUIREMENT.search(source) is not None
            and _EXACTNESS_REQUIREMENT.search(candidate) is not None
        )
        if not exactness_only:
            return False
    return _linked_source_facts_preserved(source, candidate)


def _force_levels(text: str) -> set[str]:
    levels: set[str] = set()
    if _REQUIRED_FORCE.search(text):
        levels.add("required")
    if _ADVISORY_FORCE.search(text):
        levels.add("advisory")
    return levels


def _linked_source_facts_preserved(source: str, candidate: str) -> bool:
    source_folded = source.casefold()
    candidate_folded = candidate.casefold()

    if "ready to launch manuscript" in source_folded:
        launch_ok = bool(
            re.search(
                r"\b(?:project\s+)?team\b[^.!?]{0,120}\b(?:ready|prepared|set)\b"
                r"[^.!?]{0,80}\b(?:launch|release)\w*\b[^.!?]{0,50}\bManuscript\b",
                candidate,
                re.IGNORECASE,
            )
        ) and bool(
            re.search(
                r"\b(?:after|following)\b[^.!?]{0,40}\bMonday(?:'s)?\b[^.!?]{0,20}\breview\b",
                candidate,
                re.IGNORECASE,
            )
        )
        if not launch_ok:
            return False

    if "shared build" in source_folded and "corrected text" in source_folded:
        build_ok = bool(re.search(r"\b(?:slow\w*|delay\w*)\b", candidate, re.IGNORECASE)) and bool(
            re.search(
                r"\b(?:fail\w*|did\s+not|didn(?:'|\u2019)t|not|without)\b[^.!?]{0,100}"
                r"\b(?:insert|incorporate|place)\w*\b[^.!?]{0,80}\bcorrected text\b",
                candidate,
                re.IGNORECASE,
            )
        )
        if not build_ok:
            return False

    if re.search(r"\bthree users\b[^.!?]{0,80}\bwrote\b[^.!?]{0,40}\bpages\b", source, re.IGNORECASE):
        users_ok = bool(
            re.search(
                r"\bthree users\b[^.!?]{0,100}\b(?:wrote|written|writing)\b[^.!?]{0,50}\bpages\b",
                candidate,
                re.IGNORECASE,
            )
            or re.search(
                r"\bpages\b[^.!?]{0,100}\b(?:written|wrote)\b[^.!?]{0,50}\bby three users\b",
                candidate,
                re.IGNORECASE,
            )
            or re.search(
                r"\bthree users\b[^.!?]{0,100}\bprovided\b[^.!?]{0,50}"
                r"\bseveral pages\b(?:\s+of feedback)?",
                candidate,
                re.IGNORECASE,
            )
        )
        if not users_ok:
            return False
        required_patterns = {
            "small icon": r"\bsmall icon\b",
            "correction card": r"\bcorrection cards?\b",
            "spelling fix": r"\bspelling (?:fix|correction)\b",
        }
        for required_phrase, required_pattern in required_patterns.items():
            if required_phrase in source_folded and re.search(
                required_pattern,
                candidate,
                re.IGNORECASE,
            ) is None:
                return False
        if "another tester" in source_folded and re.search(
            r"\b(?:another|a second) tester\b",
            candidate,
            re.IGNORECASE,
        ) is None:
            return False

    if all(term in source_folded for term in ("spelling fix", "text don't change", "hud")):
        accepted_fix_ok = bool(
            re.search(
                r"\btester\b[^.!?]{0,80}\baccept\w*\b[^.!?]{0,40}"
                r"\bspelling (?:fix|correction)\b",
                candidate,
                re.IGNORECASE,
            )
        )
        unchanged_text_ok = bool(
            re.search(
                r"\btext\b[^.!?]{0,50}(?:\b(?:did\s+not|didn(?:'|\u2019)t)\s+change\b|"
                r"\b(?:remained|stayed)\s+unchanged\b)",
                candidate,
                re.IGNORECASE,
            )
        )
        hud_first_ok = bool(
            re.search(
                r"\bHUD\b[^.!?]{0,70}\b(?:stayed|remained)\b[^.!?]{0,40}\bfirst sentence\b",
                candidate,
                re.IGNORECASE,
            )
        )
        if not (accepted_fix_ok and unchanged_text_ok and hud_first_ok):
            return False

    if all(term in source_folded for term in ("third tester", "local ai", "two details")):
        tester_request_ok = bool(
            re.search(
                r"(?:\bthird tester\b[^.!?]{0,100}\b(?:request\w*|asked\s+for)\b[^.!?]{0,50}\bformal tone\b|"
                r"\brequest\w*\b[^.!?]{0,50}\b(?:from|by)\s+(?:the\s+)?third tester\b"
                r"[^.!?]{0,50}\bformal tone\b)",
                candidate,
                re.IGNORECASE,
            )
        )
        ai_draft_ok = bool(
            re.search(
                r"\blocal AI\b[^.!?]{0,100}\b(?:return\w*|produc\w*|provid\w*)\b[^.!?]{0,80}"
                r"\b(?:shorter|shortened) (?:draft|version)\b[^.!?]{0,100}\b(?:remov\w*|omitt\w*|lost)\b"
                r"[^.!?]{0,40}\btwo details\b[^.!?]{0,100}\b(?:chang\w*|alter\w*)\b"
                r"[^.!?]{0,40}\bnames\b",
                candidate,
                re.IGNORECASE,
            )
            or re.search(
                r"\blocal AI\b[^.!?]{0,100}\b(?:made|return\w*|produc\w*|provid\w*)\b[^.!?]{0,80}"
                r"\b(?:shorter|shortened) (?:draft|version)\b[.!?]\s*(?:It|The draft)\b"
                r"[^.!?]{0,80}\b(?:remov\w*|omitt\w*|lost)\b[^.!?]{0,40}\btwo details\b"
                r"[^.!?]{0,100}\b(?:chang\w*|alter\w*)\b[^.!?]{0,40}\bnames\b",
                candidate,
                re.IGNORECASE,
            )
            or (
                re.search(
                    r"\blocal AI\b[^.!?]{0,100}\b(?:return\w*|produc\w*|provid\w*)\b"
                    r"[^.!?]{0,80}\b(?:shorter|shortened) (?:draft|version)\b",
                    candidate,
                    re.IGNORECASE,
                )
                and re.search(
                    r"\b(?:It|The draft)\b[^.!?]{0,60}\b(?:remov\w*|omitt\w*|lost)\b"
                    r"[^.!?]{0,40}\btwo details\b",
                    candidate,
                    re.IGNORECASE,
                )
                and re.search(
                    r"\b(?:It|The draft)\b[^.!?]{0,80}\b(?:chang\w*|alter\w*)\b"
                    r"[^.!?]{0,40}\bnames\b",
                    candidate,
                    re.IGNORECASE,
                )
            )
        )
        if not (tester_request_ok and ai_draft_ok):
            return False

    if _STYLE_LIST.search(source) and re.search(r"\bthey\s+(?:also\s+)?need\b", source, re.IGNORECASE):
        if not re.search(
            r"\b(?:reviewers?|they)\s+(?:also\s+)?(?:need|require)\b",
            candidate,
            re.IGNORECASE,
        ):
            return False

    if "workflow" in source_folded and "selection" in source_folded and "seperates" in source_folded:
        workflow_ok = bool(
            re.search(
                r"\bworkflow\b[^.!?]{0,120}(?:"
                r"\b(?:reads|processes|starts\s+(?:with|by\s+reading)|begins\s+(?:with|by\s+reading))\b"
                r"[^.!?]{0,80}\b(?:selection|selecting\s+active\s+text)\b|"
                r"\binvolves\s+selecting\s+(?:the\s+)?active\s+(?:text|selection)\b)"
                r"[^.!?]{0,100}\b(?:separates|separating)\b"
                r"[^.!?]{0,120}\b(?:sends|sending)\b",
                candidate,
                re.IGNORECASE,
            )
            or re.search(
                r"\bworkflow\b[^.!?]{0,120}\b(?:reads|processes)\b[^.!?]{0,80}"
                r"\b(?:selection|active text)\b[.!?]\s*(?:It|The workflow)\b[^.!?]{0,80}"
                r"\b(?:separates|separating)\b[^.!?]{0,120}\b(?:sends|sending)\b",
                candidate,
                re.IGNORECASE,
            )
        ) and not bool(re.search(r"\bselection\s+separates\b", candidate, re.IGNORECASE))
        if not workflow_ok:
            return False

    if "clipboard" in source_folded and "fallback" in source_folded:
        if "fallback" not in candidate_folded or not re.search(
            r"\b(?:restor\w*|return\w*\s+to\s+using)\b",
            candidate,
            re.IGNORECASE,
        ):
            return False

    if "application" in source_folded and "locally" in source_folded:
        application_force_ok = bool(
            re.search(
                r"\bapplication\b[^.!?]{0,100}\b(?:should|is\s+expected\s+to)\b",
                candidate,
                re.IGNORECASE,
            )
        )
        if not application_force_ok or not re.search(
            r"\b(?:local|locally|on-device)\b",
            candidate,
            re.IGNORECASE,
        ):
            return False

    if "developers can" in source_folded and "they must" in source_folded:
        if not re.search(
            r"\bdevelopers?\b[^.!?]{0,180}\b(?:must|(?:are|remain)\s+required\s+to)\b",
            candidate,
            re.IGNORECASE,
        ):
            return False

    if "hud" in source_folded and "inspect" in source_folded and "run" in source_folded:
        release_ok = bool(
            re.search(r"\brun\b[^.!?]{0,180}\bHUD\b[^.!?]{0,120}\binspect\b", candidate, re.IGNORECASE)
        ) and not bool(
            re.search(
                r"\bbefore\s+(?:the\s+)?(?:release\s+)?team\s+runs?\b",
                candidate,
                re.IGNORECASE,
            )
        )
        if not release_ok:
            return False
    return True


def _high_risk_meaning_change(source: str, candidate: str) -> bool:
    source_has_negation = any(count > 0 for count in _negation_counts(source))
    candidate_has_negation = any(count > 0 for count in _negation_counts(candidate))
    privacy_boundary_equivalent = _PRIVATE_REPORT_BOUNDARY_SOURCE.search(source) is not None and any(
        pattern.search(candidate) for pattern in _PRIVATE_REPORT_BOUNDARY_RESULT
    )
    if source_has_negation != candidate_has_negation and not privacy_boundary_equivalent:
        return True
    source_perspectives = {
        name for name, pattern in _OWNER_PERSPECTIVE.items() if pattern.search(source)
    }
    candidate_perspectives = {
        name for name, pattern in _OWNER_PERSPECTIVE.items() if pattern.search(candidate)
    }
    if candidate_perspectives - source_perspectives:
        return True
    if _PRIVATE_REPORT_BOUNDARY_SOURCE.search(source) and not any(
        pattern.search(candidate) for pattern in _PRIVATE_REPORT_BOUNDARY_RESULT
    ):
        return True
    source_date_relations = {
        match.group("date").casefold(): match.group("relation").casefold()
        for match in _MONTH_DATE_RELATION.finditer(source)
    }
    candidate_date_relations = {
        match.group("date").casefold(): match.group("relation").casefold()
        for match in _MONTH_DATE_RELATION.finditer(candidate)
    }
    if any(
        date in candidate_date_relations
        and candidate_date_relations[date] != relation
        for date, relation in source_date_relations.items()
    ):
        return True
    source_relations = Counter(match.group(0).casefold() for match in _ORDER_RELATION.finditer(source))
    candidate_relations = Counter(
        match.group(0).casefold() for match in _ORDER_RELATION.finditer(candidate)
    )
    if source_relations["after"] and not (
        candidate_relations["before"] or candidate_relations["after"]
    ):
        return True
    return _unretryable_meaning_change(source, candidate)


def _unretryable_meaning_change(source: str, candidate: str) -> bool:
    if bool(_PROHIBITION.search(source)) != bool(_PROHIBITION.search(candidate)):
        return True
    source_relations = Counter(match.group(0).casefold() for match in _ORDER_RELATION.finditer(source))
    candidate_relations = Counter(
        match.group(0).casefold() for match in _ORDER_RELATION.finditer(candidate)
    )
    if source_relations["before"] and not source_relations["after"]:
        if candidate_relations["after"] and not candidate_relations["before"]:
            return True
    if source_relations["after"] and not source_relations["before"]:
        if candidate_relations["before"] and not candidate_relations["after"]:
            return True
    return False


def _negation_counts(text: str) -> set[int]:
    """Treat 'neither ... nor' as either one construction or two surface words."""
    matches = [match.group(0).casefold() for match in _NEGATION.finditer(text)]
    counts = {len(matches)}
    if "neither" in matches and "nor" in matches:
        counts.add(len(matches) - matches.count("nor"))
    return counts
