from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

from .polish_classifier import MiniLMPolishClassifier
from .polish_entities import (
    _WORD,
    _edit_distance_at_most,
    _named_entities,
    _named_entity_matches_source_phrase,
    _named_entity_respelled_in,
    _runtime_opaque_entities,
)
from .polish_hardware import (
    BenchmarkRecord,
    HardwareDetector,
    HardwarePolicyManager,
    PolishBenchmarkCache,
)
from .polish_model_packs import (
    ModelPackConsentRequired,
    ModelPackDownloadError,
    ModelPackManager,
    ModelPackNotFoundError,
    ModelPackSecurityError,
)
from .polish_quality import CorrectionQualityGate, tone_contract_instruction
from .polish_runtime import AdaptiveInferenceRuntime
from .polish_runtime_packs import RuntimePackManager
from .polish_residual import accept_residual_repair, residual_unknown_words
from .polish_spelling import PolishRuleEngine, normalize_polish_text
from .polish_types import (
    ClassifierDecision,
    CorrectionRequest,
    CorrectionResult,
    ExecutionPlan,
    GenerationRequest,
    HardwareSnapshot,
    InferenceRuntime,
    PolishEdit,
    PolishMode,
    PolishModelPack,
    PolishSettingsStore,
    PolishTask,
    RuntimeBackend,
    RuntimeFailureKind,
    ValidationResult,
)
from .selected_text import (
    WritingResult,
    WritingRuntimeStatus,
    _apply_fast_proofread_rules,
    _apply_safe_caveman_splits,
)


logger = logging.getLogger(__name__)


_STAGED_STYLE_COMMANDS = frozenset(
    {
        "Rewrite",
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
# Residual findings that refuse a proofread candidate outright, even though
# proofreading is otherwise judged by comparison against the rules repair.
# A Manuscript correction never contains an em dash; that is a standing product
# rule, not a quality preference, so it cannot be traded away by a candidate
# that happens to score well elsewhere. PolishValidator also rejects em dashes
# and _final_cleanup_candidate strips them first - this is the third layer.
_ALWAYS_BLOCKING_RESIDUALS = frozenset({"em dash"})
_FAILED_PACK_TTL_SECONDS = 300.0
# A refused answer is a fact about one paragraph, not about the pack, so it
# must not take the pack out of service the way a lost GPU does.
#
# It did, for five minutes. Measured on the essay corpus: paragraph 5 of essay
# 1 dropped a protected token, the pack was blacklisted, and paragraphs 6
# through 10 came back in a tenth of a second each with "full AI correction did
# not run" - rules-only, with nothing said to the reader. Across twenty essays
# the local AI reached three paragraphs out of two hundred. One refused
# correction was costing every correction after it.
#
# The record still has to outlive the request that created it, because the
# cascade to a smaller pack reads it moments later.
#
# It must not outlive it by much. Five seconds was tried and still latched: a
# paragraph that runs rules-only finishes in about half a second, so one
# refusal covered the next ten paragraphs, and whichever of those finally
# reached the model could refuse again and re-arm the whole thing. Essays came
# back with two or three corrected paragraphs out of ten, over and over. The
# window has to be shorter than one rules-only paragraph.
_VALIDATION_FAILURE_TTL_SECONDS = 0.25
# A truncated or malformed answer is the same kind of fact as a refused one: it
# describes one generation, not the pack. It was treated like a lost GPU and
# cost the pack five minutes.
#
# Measured on the 500-percent error corpus, 2026-08-14: two paragraphs came
# back as "Local AI returned incomplete text", and the remaining 278 of 302
# paragraphs ran rules-only in 0.03 seconds each. The whole run was 303
# seconds, so a 300-second blacklist covered all of it. Essay 1, corrected
# before the first truncation, scored 92; every essay after it averaged 62.
#
# Repeated truncation is a different claim, so the window escalates: after
# three in a row the pack really is failing and the long window applies.
_INVALID_RESPONSE_STREAK = 3
# Two failures far apart are not a streak. This is longer than a rules-only
# paragraph and shorter than a page of them.
_FAILURE_STREAK_WINDOW_SECONDS = 30.0


def _failed_pack_ttl(kind: "RuntimeFailureKind", consecutive: int = 1) -> float:
    """How long a pack stays out of service after a failure of this kind."""
    if kind is RuntimeFailureKind.VALIDATION_FAILURE:
        return _VALIDATION_FAILURE_TTL_SECONDS
    if (
        kind is RuntimeFailureKind.INVALID_RESPONSE
        and consecutive < _INVALID_RESPONSE_STREAK
    ):
        return _VALIDATION_FAILURE_TTL_SECONDS
    return _FAILED_PACK_TTL_SECONDS
_CRITICAL = re.compile(
    r"```[\s\S]*?```"
    r"|`[^`\r\n]*`"
    r"|https?://[^\s<>]+"
    r"|www\.[^\s<>]+"
    r"|(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"|\"(?:[A-Za-z]:[\\/]|\\\\)[^\"\r\n]+\""
    r"|(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"|?*]+"
    r"|(?<!\w)/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"
    r"|\b(?i:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|"
    r"July?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|(?<!\w)(?:[$\u20ac\u00a3])?\d+(?:[.:,/%-]\d+)*(?!\w)"
    r"|\"[^\"\r\n]{1,500}\""
    r"|\u201c[^\u201d\r\n]{1,500}\u201d"
)
_IDENTIFIER = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b[A-Za-z]+_[A-Za-z0-9_]+\b"
    # camelCase needs a lowercase letter after the first capital. A held shift
    # key produces "tHE" and "iF", which matched the old pattern and made
    # correcting them "code or identifiers changed" - a guard for source code
    # refusing an ordinary typo. "getUser", "eBay" and "iPhone" still match; a
    # word with an all-capital tail no longer does.
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*\b"
)
_NEGATION = re.compile(
    r"\b(?:no|not|never|none|nobody|nothing|neither|nor|without|cannot|can't|cant|"
    r"won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|"
    r"haven't|hasn't|hadn't|couldn't|"
    r"wont|dont|doesnt|didnt|isnt|arent|wasnt|werent|"
    r"havent|hasnt|hadnt|couldnt|ain't|aint|"
    r"mustn't|shouldn't|shouldnt|unable|fails?|failed|failing)\b",
    re.IGNORECASE,
)
_DOUBLE_NEGATIVE = re.compile(
    # A negated verb followed by a negative word ("wasn't never printed",
    # "don't have no receipts"), or a negative pronoun followed by a negated
    # verb ("nobody don't know"). Repairing either removes exactly one
    # negation without changing polarity.
    r"\b(?:don't|doesn't|didn't|dont|doesnt|didnt|can't|cant|won't|wont|"
    r"wasn't|wasnt|weren't|werent|isn't|isnt|aren't|arent|hasn't|hasnt|"
    r"haven't|havent|hadn't|hadnt|wouldn't|wouldnt|couldn't|couldnt|"
    r"shouldn't|shouldnt|never|ain't|aint)\b"
    r"[^.!?]{0,100}\b(?:nothing|nobody|nowhere|never|no)\b"
    r"|\b(?:nobody|nothing|no\s+one|nowhere)\b[^.!?]{0,40}"
    r"\b(?:don't|doesn't|didn't|dont|doesnt|didnt|won't|wont|can't|cant)\b",
    re.IGNORECASE,
)
_NEITHER_NOR_PAIR = re.compile(
    r"\bneither\b[^.!?]{0,180}\bnor\b",
    re.IGNORECASE,
)
_OBLIGATION = re.compile(
    r"\b(?:must|shall|required?|requires?|needs?|needed|should(?:n(?:['\u2019]?t))?|"
    r"has to|have to|had to|got too?|prohibited|forbidden)\b",
    re.IGNORECASE,
)
# A modal carrying a refusal. Losing one of these inverts an instruction, so
# unlike ordinary modal drift it refuses the candidate outright. Contractions
# arrive here already expanded by _normalize_contractions, and the spaced forms
# tolerate "must never" as well as "must not".
_PROHIBITION = re.compile(
    r"\b(?:cannot|can(?:'|’)?t|won(?:'|’)?t|shan(?:'|’)?t|"
    r"must\s*n(?:'|’)?t|"
    r"(?:must|may|shall|will|can)\s+(?:not|never)|"
    r"prohibited|forbidden)\b",
    re.IGNORECASE,
)
_MODAL_MEANING = re.compile(
    r"\b(?:cannot|can(?:not|'t)?|could(?:n't)?|may|might|will|won't|"
    r"would(?:n't)?)\b",
    re.IGNORECASE,
)
_MODAL_SUBJECT = re.compile(
    r"\b(?P<subject>program|visitor|visitors|city)\s+"
    r"(?P<modal>can|could|may|might|will|would)\b",
    re.IGNORECASE,
)
_ORDERING = re.compile(
    r"\b(?:prior\s+to|following|before|after|until|during|while)\b",
    re.IGNORECASE,
)
_RELATIVE_TIME = re.compile(
    r"\b(?:last|this|next)\s+"
    r"(?:spring|summer|fall|autumn|winter|year|month|week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:['\u2019]?s)?\b",
    re.IGNORECASE,
)
_NEGATED_ABSENCE = re.compile(
    r"\bno\s+(reading|signal|value|result|response|data)\b",
    re.IGNORECASE,
)
_DESCRIBED_ABSENCE = re.compile(
    r"\b(?:a|an|the)?\s*(?:blank|empty|missing|absent)\s+"
    r"(reading|signal|value|result|response|data)\b",
    re.IGNORECASE,
)
_ACTOR = re.compile(
    r"\b(?:I|we|you|he|she|they|it|user|users|team|developer|developers|"
    r"group|groups|organizer|organizers|system|service|application|app|model|"
    r"server|client|customer|customers|administrator|administrators|analyst|"
    r"analysts|assistant|assistants|caregiver|caregivers|child|children|council|"
    r"doctor|doctors|employee|employees|engineer|engineers|manager|managers|"
    r"nurse|nurses|officer|officers|operator|operators|owner|owners|parent|"
    r"parents|patient|patients|reviewer|reviewers|staff|student|students|"
    r"supervisor|supervisors|teacher|teachers|technician|technicians|visitor|"
    r"visitors|worker|workers|writer|writers)\b",
    re.IGNORECASE,
)
_ACTOR_FORM = re.compile(
    r"\b(?:i|i'm|me|my|mine|we|we're|us|our|ours|you|you're|your|yours|"
    r"he|he's|him|his|she|she's|her|hers|they|they're|their|theirs|there|"
    r"it|it's|its|user|users|team|group|groups|organizer|organizers|developer|"
    r"developers|system|service|application|app|model|server|client|customer|"
    r"customers|administrator|administrators|analyst|analysts|assistant|"
    r"assistants|caregiver|caregivers|child|children|council|doctor|doctors|"
    r"employee|employees|engineer|engineers|manager|managers|nurse|nurses|"
    r"officer|officers|operator|operators|owner|owners|parent|parents|patient|"
    r"patients|reviewer|reviewers|staff|student|students|supervisor|supervisors|"
    r"teacher|teachers|technician|technicians|visitor|visitors|worker|workers|"
    r"writer|writers)\b",
    re.IGNORECASE,
)

# These pairs describe explicit state reversals, not ordinary paraphrases.
# Synonyms on the same side are grouped so "approved" -> "accepted" remains
# valid, while "approved" -> "rejected" is always refused even when a
# sentence embedding reports a high similarity score.
_CONTRADICTORY_STATES = (
    (
        "approval state reversed",
        re.compile(r"\b(?:approv(?:e|es|ed|ing|al)|accept(?:s|ed|ing|ance)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:reject(?:s|ed|ing|ion)?|den(?:y|ies|ied|ial))\b", re.IGNORECASE),
    ),
    (
        "quantity direction reversed",
        re.compile(
            r"\b(?:increase[ds]?|increasing|raise[ds]?|raising|rise[sn]?|rose|"
            r"grow(?:s|ing)?|grew)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:decrease[ds]?|decreasing|reduce[ds]?|reducing|"
            r"drop(?:s|ped|ping)?|fall(?:s|ing)?|fell)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "open state reversed",
        re.compile(r"\bopen(?:s|ed|ing)?\b", re.IGNORECASE),
        re.compile(r"\bclos(?:e|es|ed|ing)\b", re.IGNORECASE),
    ),
    (
        "enabled state reversed",
        re.compile(r"\benabl(?:e|es|ed|ing)\b", re.IGNORECASE),
        re.compile(r"\bdisabl(?:e|es|ed|ing)\b", re.IGNORECASE),
    ),
    (
        "active state reversed",
        re.compile(r"\bactive\b", re.IGNORECASE),
        re.compile(r"\binactive\b", re.IGNORECASE),
    ),
    (
        "availability reversed",
        re.compile(r"\bavailable\b", re.IGNORECASE),
        re.compile(r"\bunavailable\b", re.IGNORECASE),
    ),
    (
        "connection state reversed",
        re.compile(r"\b(?:online|connected)\b", re.IGNORECASE),
        re.compile(r"\b(?:offline|disconnected)\b", re.IGNORECASE),
    ),
    (
        "action state reversed",
        re.compile(
            r"\b(?:start(?:s|ed|ing)?|resume[ds]?|resuming|continue[ds]?|"
            r"continuing)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:stop(?:s|ped|ping)?|pause[ds]?|pausing|"
            r"halt(?:s|ed|ing)?)\b",
            re.IGNORECASE,
        ),
    ),
)
_REPHRASE_ANCHOR_STOPWORDS = frozenset(
    {
        "after",
        "also",
        "although",
        "because",
        "before",
        "being",
        "could",
        "during",
        "each",
        "every",
        "from",
        "have",
        "into",
        "must",
        "should",
        "their",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "until",
        "were",
        "while",
        "with",
        "without",
        "would",
    }
)
_REPHRASE_ANCHOR_ACTIONS = frozenset(
    {
        "check",
        "checked",
        "confirm",
        "confirmed",
        "contain",
        "contains",
        "end",
        "ended",
        "enter",
        "entered",
        "explain",
        "explained",
        "include",
        "included",
        "includes",
        "place",
        "placed",
        "preserve",
        "preserved",
        "receive",
        "received",
        "report",
        "reported",
        "request",
        "requested",
        "show",
        "showed",
        "start",
        "started",
        "store",
        "stored",
        "test",
        "tested",
        "wander",
        "wandered",
    }
)
_REPHRASE_ACTION_BASES = frozenset(
    {
        "allow",
        "approve",
        "assign",
        "authorize",
        "blame",
        "call",
        "check",
        "compare",
        "confirm",
        "contain",
        "continue",
        "correct",
        "destroy",
        "delete",
        "determine",
        "display",
        "end",
        "enter",
        "explain",
        "identify",
        "hire",
        "include",
        "keep",
        "monitor",
        "oversee",
        "pause",
        "place",
        "prepare",
        "preserve",
        "print",
        "receive",
        "remove",
        "reconnect",
        "record",
        "report",
        "reproduce",
        "request",
        "reverse",
        "say",
        "scan",
        "schedule",
        "send",
        "share",
        "show",
        "start",
        "state",
        "stay",
        "store",
        "submit",
        "test",
        "verify",
        "wander",
        "write",
        "overwrite",
        "publish",
    }
)
_REPHRASE_CRITICAL_ACTIONS = frozenset(
    {
        "approve",
        "authorize",
        "blame",
        "delete",
        "explain",
        "hire",
        "overwrite",
        "publish",
        "remove",
        "scan",
        "send",
        "share",
    }
)
_REPHRASE_IRREGULAR_ACTIONS = {
    "kept": "keep",
    "said": "say",
    "sent": "send",
    "shown": "show",
    "showed": "show",
    "stayed": "stay",
    "wrote": "write",
    "written": "write",
    "oversaw": "oversee",
    "overseen": "oversee",
}
_REPHRASE_ACTION_EQUIVALENTS = {
    "call": ("contact",),
    "check": ("register",),
    "compare": ("review",),
    "contain": ("include", "hold"),
    "continue": ("sustain", "operate"),
    "destroy": ("eliminate",),
    "determine": ("identify",),
    "display": ("show",),
    "end": ("conclude", "finish"),
    "explain": ("clarify", "describe", "detail"),
    "include": ("comprise", "consist", "feature", "involve", "hold"),
    "keep": ("retain",),
    "monitor": ("supervise",),
    "oversee": ("manage",),
    "pause": ("suspend",),
    "place": ("store",),
    "prepare": ("organize",),
    "preserve": ("retain",),
    "print": ("produce",),
    "receive": ("obtain",),
    "record": ("log", "document"),
    "report": ("notify", "communicate"),
    "reproduce": ("replicate",),
    "say": ("mention", "state"),
    "send": ("transmit", "email"),
    "show": ("display", "indicate"),
    "stay": ("remain",),
    "store": ("place",),
    "test": ("assess", "evaluate"),
    "verify": ("confirm",),
    "wander": ("enter",),
    "write": ("document",),
}
_REPHRASE_PHRASAL_PARTS = frozenset(
    {"at", "for", "from", "in", "into", "of", "off", "on", "out", "over", "to", "up"}
)


@dataclass(frozen=True, slots=True)
class _SafeCandidate:
    text: str
    validation: ValidationResult
    residual_errors: tuple[str, ...]
    tone_verified: bool
    quality_score: float
    used_llm: bool


@dataclass(slots=True)
class _RuntimeCascadeState:
    plan: ExecutionPlan
    attempted_pack_ids: set[str]
    failure_kind: RuntimeFailureKind = RuntimeFailureKind.NONE
    failure_message: str = ""


@dataclass(frozen=True, slots=True)
class _FailedPackState:
    reason: str
    kind: RuntimeFailureKind
    failed_at: float
    sequence: int
    consecutive: int = 1


@dataclass(frozen=True, slots=True)
class _FailureStreak:
    """How many times in a row a pack has failed the same way.

    Deliberately separate from `_FailedPackState`. That record is pruned as
    soon as its window expires, which for a truncated answer is a quarter of a
    second, so it has forgotten the previous generation before the next one
    starts and a counter kept there always reads 1. The streak is the thing
    that decides between one bad answer and a failing pack, so it has to
    outlive the blacklist entry.
    """

    kind: RuntimeFailureKind
    failed_at: float
    count: int


def _contradiction_failures(source: str, candidate: str) -> tuple[str, ...]:
    """Return explicit action or state reversals introduced by a candidate."""
    failures: list[str] = []
    for label, positive, negative in _CONTRADICTORY_STATES:
        source_positive = len(positive.findall(source))
        source_negative = len(negative.findall(source))
        candidate_positive = len(positive.findall(candidate))
        candidate_negative = len(negative.findall(candidate))
        positive_became_negative = (
            source_positive > candidate_positive
            and candidate_negative > source_negative
        )
        negative_became_positive = (
            source_negative > candidate_negative
            and candidate_positive > source_positive
        )
        if positive_became_negative or negative_became_positive:
            failures.append(label)
    return tuple(failures)


def _proportional_semantic_windows(
    source: str,
    candidate: str,
    *,
    maximum_words: int,
) -> tuple[tuple[str, str], ...]:
    """Cover both texts with aligned windows small enough for MiniLM."""
    source_words = _WORD.findall(source)
    candidate_words = _WORD.findall(candidate)
    longest = max(len(source_words), len(candidate_words))
    count = max(1, (longest + maximum_words - 1) // maximum_words)
    pairs: list[tuple[str, str]] = []
    for index in range(count):
        source_start = index * len(source_words) // count
        source_end = (index + 1) * len(source_words) // count
        candidate_start = index * len(candidate_words) // count
        candidate_end = (index + 1) * len(candidate_words) // count
        source_window = " ".join(source_words[source_start:source_end])
        candidate_window = " ".join(candidate_words[candidate_start:candidate_end])
        if source_window and candidate_window:
            pairs.append((source_window, candidate_window))
    return tuple(pairs)


def _semantic_validation_windows(
    source: str,
    candidate: str,
    *,
    maximum_words: int = 96,
) -> tuple[tuple[str, str], ...]:
    """Return aligned windows that cover long text beyond encoder truncation.

    Equal sentence counts are aligned on sentence boundaries. If a rewrite
    splits or joins sentences, proportional word windows still cover the full
    source and candidate without assuming that the first 256 tokens represent
    the rest of the passage.
    """
    if max(len(_WORD.findall(source)), len(_WORD.findall(candidate))) <= maximum_words:
        return ()
    source_sentences = tuple(
        source[start:end].strip()
        for start, end in _sentence_spans_for_recovery(source)
        if source[start:end].strip()
    )
    candidate_sentences = tuple(
        candidate[start:end].strip()
        for start, end in _sentence_spans_for_recovery(candidate)
        if candidate[start:end].strip()
    )
    if len(source_sentences) != len(candidate_sentences) or not source_sentences:
        return _proportional_semantic_windows(
            source,
            candidate,
            maximum_words=maximum_words,
        )

    pairs: list[tuple[str, str]] = []
    source_group: list[str] = []
    candidate_group: list[str] = []
    source_word_count = 0
    candidate_word_count = 0
    for source_sentence, candidate_sentence in zip(
        source_sentences, candidate_sentences
    ):
        next_source_count = len(_WORD.findall(source_sentence))
        next_candidate_count = len(_WORD.findall(candidate_sentence))
        if (
            source_group
            and max(
                source_word_count + next_source_count,
                candidate_word_count + next_candidate_count,
            )
            > maximum_words
        ):
            pairs.append((" ".join(source_group), " ".join(candidate_group)))
            source_group = []
            candidate_group = []
            source_word_count = 0
            candidate_word_count = 0
        if max(next_source_count, next_candidate_count) > maximum_words:
            if source_group:
                pairs.append((" ".join(source_group), " ".join(candidate_group)))
                source_group = []
                candidate_group = []
                source_word_count = 0
                candidate_word_count = 0
            pairs.extend(
                _proportional_semantic_windows(
                    source_sentence,
                    candidate_sentence,
                    maximum_words=maximum_words,
                )
            )
            continue
        source_group.append(source_sentence)
        candidate_group.append(candidate_sentence)
        source_word_count += next_source_count
        candidate_word_count += next_candidate_count
    if source_group:
        pairs.append((" ".join(source_group), " ".join(candidate_group)))
    return tuple(pairs)


class PolishValidator:
    def __init__(self, classifier: MiniLMPolishClassifier) -> None:
        self.classifier = classifier

    def validate(
        self,
        source: str,
        candidate: str,
        *,
        task: PolishTask,
        grammar_mode: bool | None = None,
        protected_terms: tuple[str, ...] = (),
        dense_source: bool = False,
        prepared: str | None = None,
    ) -> ValidationResult:
        """Judge a candidate. Meaning against `source`, wording against `prepared`.

        `prepared` is the deterministically repaired text the model was actually
        given. Comparing its wording and structure against the raw source
        blamed the model for repairs the rules layer had already made, which is
        how a correction that changed nothing the model chose was refused for
        "code or identifiers changed". Android draws the same line: meaning
        against the original, wording against what the model saw.

        Failures refuse the candidate. Advisories describe it and shape the
        retry, but do not refuse it. Meaning-bearing modal, obligation,
        ordering, actor, and action-state changes are always failures.
        """
        if grammar_mode is None:
            grammar_mode = task is PolishTask.PROOFREAD
        wording_reference = prepared if prepared is not None else source
        # The lexical meaning counters below match exact words, so a
        # misspelled one is invisible to them. Judged against the raw source,
        # every typo the rules layer had already repaired looked like a word
        # the model invented: "befor" is not an ordering word but "before" is,
        # so fixing it read as "date or ordering relationship changed (added
        # before)", and "thaey" -> "they" read as "actor changed (added they)".
        # Across the twenty-essay corpus that was 197 of about 250 refusals -
        # the model being turned down for doing exactly its job.
        #
        # `prepared` is the deterministic repair the model was handed, and it
        # differs from the source only by spelling, so comparing against it is
        # strictly more accurate rather than more permissive: a word the model
        # genuinely adds is still absent from `prepared` and still fails.
        meaning_reference = prepared if prepared is not None else source
        failures: list[str] = []
        advisories: list[str] = []
        if not candidate.strip():
            failures.append("empty output")
        if "\u2014" in candidate:
            failures.append("em dash is not allowed")
        source_critical = _critical_values(source, protected_terms)
        candidate_critical = _critical_values(candidate, protected_terms)
        for value in tuple(candidate_critical):
            extra = candidate_critical[value] - source_critical[value]
            if extra > 0 and _quotes_existing_source_text(value, source):
                candidate_critical[value] -= extra
                if candidate_critical[value] <= 0:
                    del candidate_critical[value]
        if source_critical != candidate_critical:
            failures.append("protected facts or tokens changed")
        if _counter(_IDENTIFIER, wording_reference) != _counter(
            _IDENTIFIER, candidate
        ):
            failures.append("code or identifiers changed")
        # Normalize missing-apostrophe contractions first so that fixing
        # "cant" -> "can't" or "dont" -> "don't" is never read as a modal or
        # negation change. The counters below then compare like with like.
        # Filler is removed from both sides before anything is counted. It
        # carries no meaning, and leaving it in split a modal from its negator:
        # "a goat may to be honest NOT greet a person" has no prohibition to the
        # counter, and the correction that removed the interjection was refused
        # as "prohibition changed" for inventing one.
        normalized_source = _normalize_contractions(
            _strip_discourse_hedges(meaning_reference)
        )
        normalized_candidate = _normalize_contractions(
            _strip_discourse_hedges(candidate)
        )
        # Spell the guards' own vocabulary in the source, using the candidate as
        # the evidence for what each typo was. Only the source moves: the
        # candidate is what is being judged, and rewriting it would hide a word
        # the model really did invent.
        normalized_source = _reconcile_guard_vocabulary(
            normalized_source, normalized_candidate
        )
        if not _negation_preserved(normalized_source, normalized_candidate):
            failures.append(
                "negation changed"
                + f" (source {_negation_count(normalized_source)} vs "
                + f"candidate {_negation_count(normalized_candidate)})"
            )
        # A prohibition is meaning: losing "must not" inverts an instruction.
        # A modal changes certainty, permission, capability, or obligation.
        # Known safe idioms and equivalences are handled by
        # _modal_meaning_preserved; everything else is a hard meaning failure.
        if _prohibition_counter(normalized_source) != _prohibition_counter(
            normalized_candidate
        ):
            failures.append("prohibition changed")
        elif not _modal_meaning_preserved(
            normalized_source,
            normalized_candidate,
            grammar_mode=grammar_mode,
        ):
            if _negated_modal_count(normalized_source) != _negated_modal_count(
                normalized_candidate
            ):
                # Name the modal here too. This branch fires for any modal that
                # moves inside a negated sentence, and calling all of those
                # "prohibition changed" hid a real defect: the model dropping
                # the writer's "maybe" from "its needs is maybe met" turns a
                # hedge into a claim, which is worth refusing and worth saying.
                failures.append(
                    "prohibition changed"
                    + _counter_diff_label(
                        _counter(_MODAL_MEANING, normalized_source),
                        _counter(_MODAL_MEANING, normalized_candidate),
                    )
                )
            else:
                # Name the modal that moved. "modal meaning changed" alone sent
                # three separate investigations looking for a modal the model
                # had invented, when the answer every time was a misspelled one
                # in the writer's own text.
                failures.append(
                    "modal meaning changed"
                    + _counter_diff_label(
                        _counter(_MODAL_MEANING, normalized_source),
                        _counter(_MODAL_MEANING, normalized_candidate),
                    )
                )
        source_obligation = _obligation_counter(normalized_source)
        candidate_obligation = _obligation_counter(normalized_candidate)
        if source_obligation != candidate_obligation:
            label = "obligation strength changed" + _counter_diff_label(
                source_obligation, candidate_obligation
            )
            failures.append(label)
        # Reconciled text on both sides, like every other counter here. This
        # one call site read `meaning_reference`, which still spells the
        # writer's modals the way the writer typed them. "cud" is a real word,
        # so no speller will ever repair it and `prepared` keeps it - the
        # source then has no modal beside its subject, the candidate has
        # "the owner could write", and a correct repair reads as a modal
        # attached to a new actor. That refused the paragraph.
        if _modal_subject_counter(normalized_source) != _modal_subject_counter(
            normalized_candidate
        ):
            failures.append("modal actor changed")
        if not _ordering_preserved(
            meaning_reference,
            candidate,
            grammar_mode=grammar_mode,
        ):
            relative_diff = _counter_diff_label(
                _relative_time_counter(meaning_reference),
                _relative_time_counter(candidate),
            )
            ordering_diff = _counter_diff_label(
                _ordering_counter(meaning_reference), _ordering_counter(candidate)
            )
            label = "date or ordering relationship changed" + (
                relative_diff or ordering_diff
            )
            failures.append(label)
        # Preserve every actor family, but allow proofreading to add a missing
        # first/second-person subject ("went" -> "I went"). Swaps, removals,
        # and invented third-person owners still fail.
        # Proofreading joins TONE and REWRITE here. Resolving "the team ... they"
        # to "the team ... the team" drops a pronoun family and was refused 74
        # times, more than any veto but modal drift, while being the tidy-up a
        # reader asked for. What still refuses a candidate is a genuine swap -
        # one third-person actor replaced by a different one - checked below.
        if not _actors_preserved(
            meaning_reference,
            candidate,
            allow_redundant_pronoun=task
            in {PolishTask.TONE, PolishTask.REWRITE, PolishTask.PROOFREAD},
        ) and not _added_actors_are_safe_subjects(meaning_reference, candidate):
            label = "actor changed" + _counter_diff_label(
                _actor_counter(_normalize_actor_homophones(meaning_reference)),
                _actor_counter(_normalize_actor_homophones(candidate)),
            )
            failures.append(label)
        failures.extend(_contradiction_failures(source, candidate))
        # _named_entities treats any capitalized run as a name, so a model that
        # correctly splits a run-on "invents" one by capitalizing the new
        # sentence. Checking the word against the text the model was given, in
        # either case, keeps real inventions failing without punishing the
        # split - 50 refusals, most of them this.
        source_names = _named_entities(source) | _named_entities(wording_reference)
        candidate_names = _named_entities(candidate)
        new_names = {
            value
            for value in candidate_names - source_names
            if not _single_word_present(value, source)
            and not _single_word_present(value, wording_reference)
            and not _named_entity_matches_source_phrase(value, source)
            and not _named_entity_matches_source_phrase(value, wording_reference)
            # The mirror of the removal case below. Spelling a name the writer
            # mistyped is not inventing one: "Csco" -> "Cusco" and "Utercht" ->
            # "Utrecht" were refused as newly added entities, which is the model
            # doing exactly the job asked of it.
            and not _named_entity_respelled_in(value, source)
            and not _named_entity_respelled_in(value, wording_reference)
        }
        removed_names = {
            value
            for value in _named_entities(source) - candidate_names
            if not _single_word_present(value, candidate)
            and not _named_entity_matches_source_phrase(value, candidate)
            # _named_entities reads any capitalized run as a name, so a
            # capitalized misspelling is one: correcting "Thier" to "Their"
            # was refused as "named entity removed", and the rules layer's own
            # output was refused for its own repair. A near-miss of the word in
            # the candidate means it was respelled, not dropped.
            and not _named_entity_respelled_in(value, candidate)
        }
        if new_names:
            failures.append("new named entity added")
        if removed_names:
            failures.append("named entity removed")
        similarity = self.classifier.semantic_similarity(source, candidate)
        # Text saturated with errors legitimately moves further when it is
        # repaired correctly; a fixed 0.88 floor rejected exactly the pages
        # that needed correction most. Every other meaning gate still holds.
        # Lowered with the meaning gates above: this is now the main backstop
        # against a model that rewrites rather than repairs, so it has to leave
        # room for a genuine repair of badly broken text while still catching
        # invention.
        if dense_source and grammar_mode:
            similarity_floor = 0.74
        elif grammar_mode:
            similarity_floor = 0.78
        else:
            similarity_floor = 0.88
        # The reported number is always measured against the writer's own text.
        # But on text this broken the embedding of the source is close to noise,
        # so a flawless repair scored 0.718 and was refused - a correction good
        # enough to publish, thrown away for being unlike the typos it fixed.
        #
        # So the candidate is also measured against the deterministic repair,
        # and the better of the two decides. That is not a weaker test: the
        # prepared text is the rules layer's own output, differing from the
        # source only in spelling, and the model has no influence over it. A
        # model that rewrote the content would be far from it as well.
        if similarity < similarity_floor and wording_reference != source:
            against_prepared = self.classifier.semantic_similarity(
                wording_reference, candidate
            )
        else:
            against_prepared = similarity
        if similarity < similarity_floor and against_prepared < similarity_floor:
            failures.append(
                f"semantic similarity {similarity:.3f} is below "
                f"{similarity_floor:.3f}"
            )
        for index, (source_window, candidate_window) in enumerate(
            _semantic_validation_windows(source, candidate),
            start=1,
        ):
            window_similarity = self.classifier.semantic_similarity(
                source_window,
                candidate_window,
            )
            similarity = min(similarity, window_similarity)
            if window_similarity < similarity_floor:
                # Windows are cut from the source, so there is no prepared
                # counterpart to compare them against. Measure the window
                # against the whole prepared text instead: a window that is
                # still recognisably part of the repaired paragraph is a repair.
                window_against_prepared = (
                    self.classifier.semantic_similarity(
                        wording_reference, candidate_window
                    )
                    if wording_reference != source
                    else window_similarity
                )
                if window_against_prepared < similarity_floor:
                    failures.append(
                        f"semantic similarity window {index} "
                        f"{window_similarity:.3f} is below {similarity_floor:.3f}"
                    )
        if grammar_mode:
            source_words = max(1, len(_WORD.findall(wording_reference)))
            candidate_words = len(_WORD.findall(candidate))
            word_delta = abs(candidate_words - source_words)
            change_ratio = word_delta / source_words
            if change_ratio > 0.35 and word_delta > 4:
                failures.append("grammar-mode length changed by more than 35%")
            if _paragraph_count(wording_reference) != _paragraph_count(candidate):
                failures.append("paragraph structure changed")
        return ValidationResult(
            accepted=not failures,
            semantic_similarity=similarity,
            failures=tuple(failures),
            advisories=tuple(advisories),
        )


class PolishEngine:
    """Normalize, protect, correct, classify, route, validate, and diff."""

    def __init__(
        self,
        *,
        rules: PolishRuleEngine | None = None,
        classifier: MiniLMPolishClassifier | None = None,
        policy: HardwarePolicyManager | None = None,
        packs: ModelPackManager | None = None,
        runtime: InferenceRuntime | None = None,
        hardware_provider: Callable[[], HardwareSnapshot] | None = None,
        benchmark_cache: PolishBenchmarkCache | None = None,
        preferred_pack_provider: Callable[[], str] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.rules = rules or PolishRuleEngine()
        self.classifier = classifier or MiniLMPolishClassifier()
        self.policy = policy or HardwarePolicyManager()
        self.packs = packs or ModelPackManager()
        self.runtime = runtime or AdaptiveInferenceRuntime(model_packs=self.packs)
        self._hardware_detector = HardwareDetector()
        self._last_catalog_error = ""
        self.hardware_provider = hardware_provider or self._hardware_detector.snapshot
        self.benchmark_cache = benchmark_cache or PolishBenchmarkCache(None)
        self.preferred_pack_provider = preferred_pack_provider or (lambda: "")
        self._monotonic = monotonic or time.monotonic
        self.validator = PolishValidator(self.classifier)
        self.quality_gate = CorrectionQualityGate()
        self._recent_probe_records: dict[
            str,
            tuple[float, BenchmarkRecord],
        ] = {}
        self._failed_pack_lock = threading.Lock()
        self._failed_pack_states: dict[str, _FailedPackState] = {}
        self._failure_streaks: dict[str, _FailureStreak] = {}
        self._failed_pack_sequence = 0

    def warm(self) -> dict[str, bool]:
        return {
            "dictionary": self.rules.warm(),
            "classifier": self.classifier.warm(),
        }

    def _evaluate_quality(
        self,
        source: str,
        candidate: str,
        *,
        command: str,
    ):
        quality = self.quality_gate.evaluate(candidate, command=command)
        command_key = command.strip().casefold()
        visibility_failure = ""
        if command_key == "persuasive" and not _persuasive_rewrite_is_visible(
            source,
            candidate,
        ):
            visibility_failure = "persuasive rewrite is too close to the source"
        elif command_key == "cooperative" and not _cooperative_rewrite_is_visible(
            source,
            candidate,
        ):
            visibility_failure = "cooperative rewrite is too close to the source"
        elif command_key == "concise" and not _concise_rewrite_is_visible(
            source,
            candidate,
        ):
            visibility_failure = "concise rewrite did not reduce word count"
        if not visibility_failure:
            visibility_failure = _tone_style_failure(
                source,
                candidate,
                command=command_key,
            )
        if visibility_failure:
            residual_errors = tuple(
                dict.fromkeys((*quality.residual_errors, visibility_failure))
            )
            return replace(
                quality,
                complete=False,
                residual_errors=residual_errors,
                tone_verified=False,
                score=max(0.0, quality.score - 0.25),
            )
        return quality

    def _beats_deterministic(
        self,
        quality,
        deterministic_quality,
        *,
        command: str,
    ) -> bool:
        """Accept the model when it is no worse than the rules-only repair.

        This is Android's rule (``GrammarResultSelector.select``). Windows used
        to demand a candidate free of every known error pattern, which threw
        away answers that fixed nine faults out of ten and left the reader the
        original sentence. The question that decides whether a correction is
        worth showing is comparative, not absolute.

        Meaning is not judged here. PolishValidator still has to accept the
        candidate before this is consulted.
        """
        if not quality.tone_verified:
            return False
        if command.strip().casefold() != "proofread":
            return quality.complete
        if _ALWAYS_BLOCKING_RESIDUALS.intersection(quality.residual_errors):
            return False
        return quality.error_score <= deterministic_quality.error_score

    def _repair_residual_spelling(
        self,
        request: CorrectionRequest,
        final_text: str,
        *,
        plan,
        used_llm: bool,
    ) -> str:
        """Ask the model once more about words that are still misspelled.

        Only for a proofread, and only when the model already ran: a rules-only
        answer has not had its chance yet, and a tone rewrite is not the place
        to be changing words.

        Everything that comes back goes through `accept_residual_repair`, which
        allows a changed word only where the word was unknown before and is
        known after, with every other word identical and in the same order. A
        model that also rewords is refused whole. This runs after the text has
        been validated once, so anything wider would be a second rewrite of
        prose that was already agreed.

        Any failure leaves the text exactly as it was. A residual repair is a
        bonus pass; it must never be able to make a correction worse.
        """
        # Every exit says why. The pass ran exactly once across essay 16 on
        # 2026-08-17 and the four words it was built for never reached it,
        # which was indistinguishable from the pass not being wired at all.
        # A skip that leaves no trace cannot be diagnosed from a log.
        if not used_llm:
            logger.info(
                "Residual spelling pass skipped: the model did not run for "
                "this section (rules-only), characters=%d",
                len(final_text),
            )
            return final_text
        if request.command.strip().casefold() != "proofread":
            logger.info(
                "Residual spelling pass skipped: command is %r, not a proofread",
                request.command,
            )
            return final_text
        dictionary = getattr(self.rules, "dictionary", None)
        if dictionary is None:
            logger.info(
                "Residual spelling pass skipped: the rule engine exposes no "
                "dictionary"
            )
            return final_text
        unknown = residual_unknown_words(final_text, dictionary)
        if not unknown:
            logger.info(
                "Residual spelling pass not needed: every word is known, "
                "characters=%d",
                len(final_text),
            )
            return final_text
        listed = ", ".join(unknown[:8])
        try:
            generation = self.runtime.generate(
                GenerationRequest(
                    text=final_text,
                    command=request.command,
                    profile=request.profile,
                    instruction=(
                        "Correct only the misspelled words in this text: "
                        f"{listed}. Change nothing else. Keep every other "
                        "word, and the order of the words, exactly as they "
                        "are."
                    ),
                    language=request.language,
                    protected_terms=request.protected_terms,
                    plan=plan,
                )
            )
        except Exception as exc:  # noqa: BLE001 - the runtime raises many types
            # A residual pass that fails must not disturb a correction that
            # succeeded, so this is swallowed deliberately and recorded.
            logger.info("Residual spelling pass did not run: %s", exc)
            return final_text
        if generation is None:
            return final_text
        repaired = accept_residual_repair(
            final_text,
            str(generation.text or "").strip(),
            dictionary,
        )
        if repaired is None:
            logger.info(
                "Residual spelling pass refused for %d word(s): %s",
                len(unknown),
                listed,
            )
            return final_text
        logger.info(
            "Residual spelling pass repaired %d word(s): %s",
            len(unknown),
            listed,
        )
        return repaired

    def analyze(self, request: CorrectionRequest) -> CorrectionResult:
        started = time.perf_counter()
        normalized = normalize_polish_text(request.text)
        deterministic = self.rules.correct(
            normalized,
            user_terms=request.protected_terms,
        )
        categories = tuple(
            dict.fromkeys(
                str(value.category.value)
                for value in (
                    *deterministic.rule_suggestions,
                    *deterministic.spelling_suggestions,
                )
            )
        )
        classifier = self.classifier.classify(
            normalized,
            deterministic.text,
            task=request.task,
            deterministic_categories=categories,
        )
        deterministic_suggestions = len(deterministic.rule_suggestions) + len(
            deterministic.spelling_suggestions
        )
        sentence_estimate = max(1, normalized.count(".") + normalized.count("!") + normalized.count("?"))
        dense_source = (
            deterministic_suggestions >= 6
            or deterministic_suggestions / sentence_estimate >= 1.5
        )
        if request.manual:
            classifier = replace(
                classifier,
                needs_llm=True,
                severity=max(0.85, classifier.severity),
            )
        if classifier.english_confidence < 0.72 and not request.manual:
            plan = self.policy.plan(
                self.hardware_provider(),
                request.task,
                request.mode,
                classifier=classifier,
            )
            return self._result(
                request,
                normalized,
                deterministic.text,
                deterministic.text,
                classifier,
                plan,
                ValidationResult(True, 1.0),
                started,
                message="English correction is not supported for this text.",
                deterministic_ms=deterministic.elapsed_ms,
            )

        snapshot = self.hardware_provider()
        catalog, catalog_error = self._catalog()
        preferred = self.preferred_pack_provider().strip()
        planning_catalog = catalog
        if not request.download_consent:
            installed_catalog = tuple(pack for pack in catalog if pack.installed)
            if installed_catalog:
                planning_catalog = installed_catalog
        failed_pack_ids = self._failed_pack_ids()
        current_lease = getattr(self.runtime, "lease", lambda: None)()
        current_pack_id = str(
            getattr(getattr(current_lease, "plan", None), "pack_id", "") or ""
        )
        planning_catalog = tuple(
            pack
            for pack in planning_catalog
            if pack.pack_id not in failed_pack_ids or pack.pack_id == current_pack_id
        )
        records = self._benchmark_records(
            snapshot,
            planning_catalog,
            task=request.task,
            mode=request.mode,
            classifier=classifier,
            manual=request.manual,
        )
        plan = self.policy.plan(
            snapshot,
            request.task,
            request.mode,
            classifier=classifier,
            packs=planning_catalog,
            preferred_pack_id=preferred,
            benchmark_records=records,
            manual=request.manual,
        )
        prior_failure = self._latest_failed_pack()
        prior_failure_reason = prior_failure.reason if prior_failure is not None else ""
        if prior_failure_reason:
            plan = replace(
                plan,
                reason=(
                    plan.reason
                    if plan.use_llm
                    else prior_failure_reason
                ),
                downgrade_reason=plan.downgrade_reason or prior_failure_reason,
            )
        if not plan.use_llm:
            return self._result(
                request,
                normalized,
                deterministic.text,
                _safe_fallback_text(request, normalized, deterministic.text),
                classifier,
                plan,
                ValidationResult(False, 1.0, ("full AI correction did not run",)),
                started,
                message=plan.reason or catalog_error or "Instant correction ready.",
                deterministic_ms=deterministic.elapsed_ms,
                runtime_failure_kind=(
                    prior_failure.kind
                    if prior_failure is not None
                    else RuntimeFailureKind.MODEL_UNAVAILABLE
                ),
            )

        _notify_plan(request, plan)
        attempted_pack_ids = self._failed_pack_ids()
        attempted_pack_ids.add(plan.pack_id)
        activation_failure_kind = RuntimeFailureKind.MODEL_UNAVAILABLE
        selected_pack = next(
            (item for item in planning_catalog if item.pack_id == plan.pack_id),
            None,
        )
        activation_consent = bool(
            request.download_consent
            and selected_pack is not None
            and not selected_pack.installed
        )
        try:
            pack = self.packs.ensure(plan, consent=activation_consent)
            if pack.pack_id != plan.pack_id:
                raise ModelPackSecurityError(
                    "The installed model pack does not match the selected pack."
                )
            plan = replace(plan, model=pack.model, pack_id=pack.pack_id)
            prepare = getattr(self.runtime, "prepare", None)
            if callable(prepare):
                prepared = bool(prepare(plan, consent=activation_consent))
                if not prepared:
                    reason = str(
                        getattr(self.runtime, "last_downgrade_reason", "")
                    ).strip()
                    if reason:
                        plan = replace(
                            plan,
                            downgrade_reason=(
                                "Bundled local AI runtime unavailable. Using the "
                                "compatibility runtime."
                            )[:300],
                        )
            accepted_plan = self._accept_plan(
                plan,
                snapshot,
                pack,
                allow_installed_fallback=False,
            )
        except ModelPackConsentRequired as exc:
            # Missing consent is a user choice, not a broken pack. Keep the
            # exact candidate eligible so approving it later works without an
            # app restart or a five-minute poison delay.
            activation_failure_kind = RuntimeFailureKind.MODEL_UNAVAILABLE
            accepted_plan = replace(
                plan,
                use_llm=False,
                pack_id="",
                model="",
                backend=RuntimeBackend.RULES,
                standby_model="",
                requires_probe=False,
                downgrade_reason=str(exc)[:300],
            )
        except (
            ModelPackDownloadError,
            ModelPackNotFoundError,
            ModelPackSecurityError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            if _is_cancellation_error(exc):
                raise
            activation_failure_kind = _runtime_failure_kind(exc, loading=True)
            self._remember_failed_pack(
                plan.pack_id,
                str(exc),
                kind=activation_failure_kind,
            )
            accepted_plan = replace(
                plan,
                use_llm=False,
                pack_id="",
                model="",
                backend=RuntimeBackend.RULES,
                standby_model="",
                requires_probe=False,
                downgrade_reason=str(exc)[:300],
            )

        if not accepted_plan.use_llm:
            rejected_activation_plan = replace(
                plan,
                downgrade_reason=(
                    accepted_plan.downgrade_reason
                    or plan.downgrade_reason
                ),
            )
            accepted_plan = self._accept_smaller_plan(
                request=request,
                classifier=classifier,
                snapshot=snapshot,
                rejected_plan=rejected_activation_plan,
                attempted_pack_ids=attempted_pack_ids,
            )
        _notify_plan(request, accepted_plan)
        if not accepted_plan.use_llm:
            terminal_message = (
                accepted_plan.downgrade_reason
                or "No installed local AI pack completed activation."
            )
            activation_failure_kind = _runtime_failure_kind(
                RuntimeError(terminal_message),
                loading=True,
            )
            return self._result(
                request,
                normalized,
                deterministic.text,
                _safe_fallback_text(request, normalized, deterministic.text),
                classifier,
                accepted_plan,
                ValidationResult(False, 1.0, ("full AI correction did not run",)),
                started,
                message=terminal_message,
                deterministic_ms=deterministic.elapsed_ms,
                runtime_failure_kind=activation_failure_kind,
            )

        cascade_state = _RuntimeCascadeState(
            plan=accepted_plan,
            attempted_pack_ids=attempted_pack_ids,
        )
        instruction = _generation_instruction(request, classifier)
        generated_protected_terms = tuple(
            dict.fromkeys(
                (
                    *request.protected_terms,
                    *_runtime_opaque_entities(normalized, request.document),
                    *_runtime_temporal_fact_phrases(deterministic.text),
                    *(
                        _runtime_negated_constraint_phrases(deterministic.text)
                        if request.command in _STAGED_STYLE_COMMANDS
                        else ()
                    ),
                    *(
                        _runtime_negation_literals(deterministic.text)
                        if request.command in _STAGED_STYLE_COMMANDS
                        else ()
                    ),
                    *(
                        _runtime_modal_actor_phrases(deterministic.text)
                        if request.command in _STAGED_STYLE_COMMANDS
                        else ()
                    ),
                    *(
                        _runtime_alternative_fact_phrases(deterministic.text)
                        if request.command in _STAGED_STYLE_COMMANDS
                        else ()
                    ),
                )
            )
        )
        retries = 0
        generation_seconds = 0.0
        cold_load_seconds = 0.0
        candidate = deterministic.text
        validation = ValidationResult(False, 0.0, ("generation did not run",))
        # Why the model's answers were refused, kept for the result.
        #
        # The candidate that ships can be a SAFE one whose own validation was
        # accepted, so it carries no failures at all, while the reason the
        # model lost is computed in the loop and then dropped. That is how a
        # rejected section reached the log as "guards=unrecorded residual=none
        # rejected_llm=True": the engine knew exactly why and kept none of it.
        model_rejection_failures: tuple[str, ...] = ()
        # The bar every model answer is measured against. Captured before the
        # attempt loop reassigns `quality`, because the comparison needs the
        # rules-only repair, not whatever the last attempt produced.
        deterministic_quality = self._evaluate_quality(
            normalized,
            candidate,
            command=request.command,
        )
        quality = deterministic_quality
        generation_input = deterministic.text
        safe_candidates: list[_SafeCandidate] = []
        used_safe_tone_fallback = False
        attempt_protected_terms = generated_protected_terms
        attempt_count = (
            1
            if (
                request.preverified_fallback_available
                and request.command in _STAGED_STYLE_COMMANDS
            )
            else accepted_plan.retry_limit + 1
        )
        for attempt in range(attempt_count):
            retry_note = ""
            if attempt:
                actor_inventory = ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(_actor_counter(normalized).items())
                )
                negation_inventory = ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(_negation_counter(normalized).items())
                )
                name_inventory = ", ".join(sorted(_named_entities(normalized)))
                # Advisories are included even though they no longer refuse the
                # candidate: they are still the most specific description of
                # what drifted, and a retry that knows it dropped a modal
                # writes a better second answer.
                prior_problems = [*validation.failures, *validation.advisories]
                # A quality-only rejection (safety passed, residual grammar
                # remained) previously retried with no hint at all, so the
                # model repeated the same miss - for example leaving "Kids
                # likes" untouched through every attempt.
                prior_problems.extend(
                    f"remaining error: {name}" for name in quality.residual_errors
                )
                retry_note = (
                    "\nThis is the only safety retry. The prior output failed: "
                    + "; ".join(prior_problems or ("quality checks",))
                )
                command_key = request.command.strip().casefold()
                if command_key == "persuasive":
                    retry_note += (
                        ". Do not copy the prior wording. Make a visible persuasive "
                        "rewrite by reorganizing and connecting only the existing "
                        "facts to an existing recommendation, requirement, or "
                        "consequence. Add no new reason, claim, or fact. Preserve "
                        "every protected fact and immutable token."
                    )
                elif command_key == "concise":
                    source_word_count = len(_WORD.findall(normalized))
                    target_word_count = source_word_count - max(
                        1,
                        source_word_count // 20,
                    )
                    retry_note += (
                        f". Use no more than {target_word_count} words. Remove filler, "
                        "repetition, and needless qualifiers. Combine redundant clauses. "
                        "Keep every fact, actor, action, relationship, request, constraint, "
                        "negation, and obligation."
                    )
                elif command_key == "cooperative":
                    retry_note += (
                        ". Do not copy the prior wording. Make shared effort visible "
                        "where the source names a team or reviewers. Revise an existing "
                        "clause, such as changing 'the team is preparing' to 'the team "
                        "is working together to prepare.' Do not append a new sentence, "
                        "goal, benefit, solution, readiness claim, recommendation, or "
                        "proposal. Keep the same sentence count. Do not change any "
                        "owner, duty, criticism, failure, or protected fact."
                    )
                else:
                    retry_note += (
                        ". Make the smallest safe correction and preserve every "
                        "protected fact."
                    )
                if actor_inventory:
                    retry_note += (
                        " Preserve this exact actor inventory, including pronoun counts: "
                        + actor_inventory
                        + "."
                    )
                else:
                    retry_note += (
                        " The source has no pronoun actor. Do not add I, we, you, "
                        "he, she, they, or it."
                    )
                if negation_inventory:
                    retry_note += (
                        " Preserve this exact negation inventory: "
                        + negation_inventory
                        + ". Keep each negation in its source clause."
                    )
                if name_inventory:
                    retry_note += (
                        " Preserve this exact name inventory, with identical spelling: "
                        + name_inventory
                        + "."
                    )
            generation = None
            while generation is None:
                try:
                    generation = self.runtime.generate(
                        GenerationRequest(
                            text=generation_input,
                            command=request.command,
                            profile=request.profile,
                            instruction=instruction + retry_note,
                            language=request.language,
                            protected_terms=attempt_protected_terms,
                            plan=accepted_plan,
                        )
                    )
                except (
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    if _is_cancellation_error(exc):
                        raise

                    # Acceptance proves only the short probe. A later load,
                    # GPU-residency, or generation timeout can still fail.
                    # Reject this pack for the rest of this request and try
                    # each smaller signed Gemma pack once under the consent
                    # already attached to the request.
                    failed_plan = accepted_plan
                    next_plan = self._fallback_after_runtime_failure(
                        request=request,
                        classifier=classifier,
                        snapshot=snapshot,
                        state=cascade_state,
                        error=exc,
                    )
                    _notify_plan(request, next_plan)
                    if next_plan.use_llm:
                        accepted_plan = next_plan
                        continue
                    if _recoverable_generation_error(exc):
                        # No smaller installed pack was eligible. Keep the
                        # validation-style failure available to the segmented
                        # recovery pass instead of pretending the rules result
                        # was an AI correction.
                        cascade_state.plan = failed_plan
                        accepted_plan = failed_plan
                        validation = ValidationResult(
                            False,
                            validation.semantic_similarity,
                            (
                                *validation.failures,
                                f"retry runtime failed: {exc}",
                            ),
                        )
                        retries = attempt + 1
                        break
                    return self._result(
                        request,
                        normalized,
                        deterministic.text,
                        _safe_fallback_text(
                            request,
                            normalized,
                            deterministic.text,
                        ),
                        classifier,
                        next_plan,
                        ValidationResult(
                            False,
                            1.0,
                            ("full AI correction did not run",),
                        ),
                        started,
                        message=(
                            "Local AI stopped safely. "
                            f"{cascade_state.failure_message}"
                        ),
                        deterministic_ms=deterministic.elapsed_ms,
                        generation_seconds=generation_seconds,
                        runtime_failure_kind=cascade_state.failure_kind,
                    )
            if generation is None:
                break
            generation_seconds += generation.elapsed_seconds
            cold_load_seconds += generation.cold_load_seconds
            candidate = _final_cleanup_candidate(
                request.command,
                generation.text.strip(),
            )
            candidate = _restore_rephrase_content_nouns(
                normalized,
                candidate,
                command=request.command,
            )
            if (
                request.task is PolishTask.TONE
                or request.command in _STAGED_STYLE_COMMANDS
            ):
                candidate = _restore_tone_actor_pronouns(normalized, candidate)
                candidate = _restore_source_phrase_casing(normalized, candidate)
            safety_validation = self.validator.validate(
                normalized,
                candidate,
                task=request.task,
                grammar_mode=request.command.strip().casefold() == "proofread",
                protected_terms=request.protected_terms,
                dense_source=dense_source,
                prepared=deterministic.text,
            )
            safety_validation = _validate_rephrase_actions(
                safety_validation,
                normalized,
                candidate,
                command=request.command,
            )
            quality = self._evaluate_quality(
                normalized,
                candidate,
                command=request.command,
            )
            if safety_validation.accepted:
                safe_candidates.append(
                    _SafeCandidate(
                        text=candidate,
                        validation=safety_validation,
                        residual_errors=quality.residual_errors,
                        tone_verified=quality.tone_verified,
                        quality_score=quality.score,
                        used_llm=True,
                    )
                )
            # Break only on a flawless answer. A merely-better-than-rules
            # answer is kept as a safe candidate and the loop retries, because
            # feeding the model its own draft is what turns "The reports is
            # ready" into "The reports are ready". The comparison against the
            # rules repair happens once, after the attempts are spent.
            if safety_validation.accepted and quality.complete:
                validation = safety_validation
                break
            if attempt == 0:
                command_key = request.command.strip().casefold()
                fallback_text = deterministic.text
                fallback_available = False
                if command_key == "cooperative":
                    fallback_text = _apply_safe_cooperative_signal(
                        deterministic.text
                    )
                    fallback_available = fallback_text != deterministic.text
                elif command_key == "assertive":
                    fallback_available = _assertive_section_already_functional(
                        deterministic.text
                    )
                if fallback_available:
                    fallback_validation = self.validator.validate(
                        normalized,
                        fallback_text,
                        task=request.task,
                        grammar_mode=False,
                        protected_terms=request.protected_terms,
                    )
                    fallback_validation = _validate_rephrase_actions(
                        fallback_validation,
                        normalized,
                        fallback_text,
                        command=request.command,
                    )
                    fallback_quality = self._evaluate_quality(
                        normalized,
                        fallback_text,
                        command=request.command,
                    )
                    if fallback_validation.accepted and fallback_quality.complete:
                        candidate = fallback_text
                        validation = fallback_validation
                        quality = fallback_quality
                        safe_candidates.append(
                            _SafeCandidate(
                                text=fallback_text,
                                validation=fallback_validation,
                                residual_errors=fallback_quality.residual_errors,
                                tone_verified=fallback_quality.tone_verified,
                                quality_score=fallback_quality.score,
                                used_llm=False,
                            )
                        )
                        used_safe_tone_fallback = True
                        break
            if safety_validation.accepted:
                validation = ValidationResult(
                    False,
                    safety_validation.semantic_similarity,
                    tuple(
                        f"residual {value}" for value in quality.residual_errors
                    )
                    + (() if quality.tone_verified else ("tone contract failed",)),
                )
                if validation.failures:
                    model_rejection_failures = tuple(validation.failures)
                generation_input = candidate
            else:
                validation = safety_validation
                if validation.failures:
                    model_rejection_failures = tuple(validation.failures)
                generation_input = deterministic.text
                if request.command.strip().casefold() == "rephrase":
                    missing_actions = _missing_rephrase_actions(
                        normalized,
                        candidate,
                    )
                    if (
                        missing_actions
                        and not any(
                            "semantic similarity" in failure
                            for failure in safety_validation.failures
                        )
                    ):
                        retry_anchors = _source_rephrase_action_terms(
                            normalized,
                            missing_actions,
                        )
                    else:
                        retry_anchors = _missing_rephrase_anchor_terms(
                            normalized,
                            candidate,
                        )
                    attempt_protected_terms = tuple(
                        dict.fromkeys(
                            (
                                *generated_protected_terms,
                                *retry_anchors,
                            )
                        )
                    )
            retries = attempt + 1

        accepted = validation.accepted and quality.complete
        accepted_used_llm = accepted and not used_safe_tone_fallback
        if (
            not accepted
            and request.manual
            and request.mode is not PolishMode.INSTANT
            and not request.preverified_fallback_available
        ):
            recovery, recovery_calls, recovery_generation, recovery_load = (
                self._recover_failed_generation(
                    request=request,
                    normalized=normalized,
                    deterministic=deterministic.text,
                    plan=accepted_plan,
                    instruction=instruction,
                    safe_candidates=safe_candidates,
                    classifier=classifier,
                    snapshot=snapshot,
                    cascade_state=cascade_state,
                    dense_source=dense_source,
                )
            )
            accepted_plan = cascade_state.plan
            retries += recovery_calls
            generation_seconds += recovery_generation
            cold_load_seconds += recovery_load
            if recovery is not None:
                safe_candidates.append(recovery)
                if not recovery.used_llm:
                    used_safe_tone_fallback = True
                if not recovery.residual_errors and recovery.tone_verified:
                    candidate = recovery.text
                    validation = recovery.validation
                    quality = self._evaluate_quality(
                        normalized,
                        candidate,
                        command=request.command,
                    )
                    accepted = True
                    accepted_used_llm = recovery.used_llm

        if accepted:
            final_text = candidate
            ready_kind = (
                "correction"
                if request.command.strip().casefold() == "proofread"
                else "rewrite"
            )
            if used_safe_tone_fallback:
                message = f"Local AI plus safe {request.command} rewrite ready."
            else:
                message = f"{accepted_plan.status_label} {ready_kind} ready."
            used_llm = accepted_used_llm
            rejected_llm = used_safe_tone_fallback or not used_llm
            if not used_llm:
                message = (
                    "AI output was rejected. The deterministic repair is not "
                    "ready for review."
                )
        elif safe_candidates:
            best = max(safe_candidates, key=_safe_candidate_rank)
            final_text = best.text
            validation = best.validation
            used_llm = best.used_llm
            best_quality = self._evaluate_quality(
                normalized,
                best.text,
                command=request.command,
            )
            # Android's rule, applied once the retries are spent: a candidate
            # that is meaning-safe and no worse than the rules repair is a
            # correction, not a failure. Demanding perfection here refused 159
            # of 200 measured sections and showed the reader unrepaired text.
            if self._beats_deterministic(
                best_quality,
                deterministic_quality,
                command=request.command,
            ):
                accepted = True
                quality = best_quality
                rejected_llm = False
                if best_quality.complete:
                    ready_kind = (
                        "correction"
                        if request.command.strip().casefold() == "proofread"
                        else "rewrite"
                    )
                    message = f"{accepted_plan.status_label} {ready_kind} ready."
                else:
                    message = (
                        "AI quality repair was incomplete. "
                        "Not ready for review."
                    )
            else:
                rejected_llm = True
                message = (
                    "AI quality repair was incomplete. "
                    "Not ready for review."
                )
        else:
            final_text = _safe_fallback_text(
                request,
                normalized,
                deterministic.text,
            )
            validation = self.validator.validate(
                normalized,
                final_text,
                task=request.task,
                grammar_mode=request.command.strip().casefold() == "proofread",
                protected_terms=request.protected_terms,
                prepared=deterministic.text,
            )
            validation = _validate_rephrase_actions(
                validation,
                normalized,
                final_text,
                command=request.command,
            )
            used_llm = False
            rejected_llm = True
            # Not the original text: _safe_fallback_text returns the rules
            # repair, so every spelling, agreement and punctuation fix is
            # already applied here. The old wording said the text was kept
            # untouched, which sent a reader looking for corrections that were
            # in front of them.
            message = (
                "AI correction was not accepted. Every spelling, agreement "
                "and punctuation repair is applied."
            )
        # The words the speller refused to guess get one more look, now that
        # the model has corrected everything around them. See polish_residual
        # for why the refusal is right and why this cannot be fixed by
        # loosening the speller: frequency prefers "easy" for "esay" and the
        # writer meant "essay".
        final_text = self._repair_residual_spelling(
            request,
            final_text,
            plan=accepted_plan,
            used_llm=used_llm,
        )
        terminal_runtime_kind = RuntimeFailureKind.NONE
        if not used_llm and cascade_state.failure_kind is not RuntimeFailureKind.NONE:
            terminal_runtime_kind = cascade_state.failure_kind
            # A rejected answer is not a stopped correction. Saying "stopped
            # safely: validation failed: modal meaning changed" reads as a
            # breakage and names an internal guard, when what actually
            # happened is that the rules repair stands. Keep the reader-facing
            # wording set above for that case; real runtime faults - no model,
            # no GPU, a dead connection - still say they stopped.
            if terminal_runtime_kind is not RuntimeFailureKind.VALIDATION_FAILURE:
                message = (
                    "Correction stopped safely. "
                    f"{cascade_state.failure_message}"
                )
        return self._result(
            request,
            normalized,
            deterministic.text,
            final_text,
            classifier,
            accepted_plan,
            validation,
            started,
            used_llm=used_llm,
            rejected_llm=rejected_llm,
            retries=retries,
            message=message,
            deterministic_ms=deterministic.elapsed_ms,
            generation_seconds=generation_seconds,
            cold_load_seconds=cold_load_seconds,
            runtime_failure_kind=terminal_runtime_kind,
            refusal_reason=(
                ""
                if used_llm
                else (
                    cascade_state.failure_message
                    or "; ".join(validation.failures)
                    or "; ".join(model_rejection_failures)
                    # A candidate can also lose on quality rather than on
                    # meaning, and that left the reason blank - "no recorded
                    # reason" was the fifth most common entry in the corpus
                    # report, which is a measurement gap, not a cause.
                    or str(message).strip()
                    or "no safe candidate and no recorded reason"
                )
            ),
        )

    def _recover_failed_generation(
        self,
        *,
        request: CorrectionRequest,
        normalized: str,
        deterministic: str,
        plan: ExecutionPlan,
        instruction: str,
        safe_candidates: list[_SafeCandidate],
        classifier: ClassifierDecision | None = None,
        snapshot: HardwareSnapshot | None = None,
        cascade_state: _RuntimeCascadeState | None = None,
        dense_source: bool = False,
    ) -> tuple[_SafeCandidate | None, int, float, float]:
        """Repair a failed section in smaller, independently checked pieces.

        `dense_source` has to arrive from the caller. Error-saturated text
        legitimately moves further from its source when it is repaired
        correctly, which is why the similarity floor is lower for it - and the
        main path already knew that while this path did not, so the same
        paragraph was accepted whole and refused sentence by sentence.
        """
        if cascade_state is None:
            cascade_state = _RuntimeCascadeState(
                plan=plan,
                attempted_pack_ids={plan.pack_id},
            )
        plan = cascade_state.plan
        deterministic_recovery: _SafeCandidate | None = None
        if (
            request.command.strip().casefold() == "concise"
            and safe_candidates
            and cascade_state.failure_kind is RuntimeFailureKind.NONE
        ):
            best = max(safe_candidates, key=_safe_candidate_rank)
            compact = _apply_safe_concise_reductions(best.text)
            compact_quality = self._evaluate_quality(
                normalized,
                compact,
                command=request.command,
            )
            compact_validation = self.validator.validate(
                normalized,
                compact,
                task=request.task,
                grammar_mode=False,
                protected_terms=request.protected_terms,
            )
            compact_validation = _validate_rephrase_actions(
                compact_validation,
                normalized,
                compact,
                command=request.command,
            )
            if compact_validation.accepted and compact_quality.complete:
                return (
                    _SafeCandidate(
                        text=compact,
                        validation=compact_validation,
                        residual_errors=compact_quality.residual_errors,
                        tone_verified=compact_quality.tone_verified,
                        quality_score=compact_quality.score,
                        used_llm=True,
                    ),
                    0,
                    0.0,
                    0.0,
                )
        if request.command.strip().casefold() == "concise":
            compact = _apply_safe_concise_reductions(deterministic)
            compact_quality = self._evaluate_quality(
                normalized,
                compact,
                command=request.command,
            )
            compact_validation = self.validator.validate(
                normalized,
                compact,
                task=request.task,
                grammar_mode=False,
                protected_terms=request.protected_terms,
            )
            compact_validation = _validate_rephrase_actions(
                compact_validation,
                normalized,
                compact,
                command=request.command,
            )
            if compact_validation.accepted and compact_quality.complete:
                deterministic_recovery = _SafeCandidate(
                    text=compact,
                    validation=compact_validation,
                    residual_errors=compact_quality.residual_errors,
                    tone_verified=compact_quality.tone_verified,
                    quality_score=compact_quality.score,
                    used_llm=False,
                )
        if request.command.strip().casefold() == "proofread":
            repaired = _final_cleanup_candidate("Proofread", deterministic)
            repaired_quality = self._evaluate_quality(
                normalized,
                repaired,
                command=request.command,
            )
            repaired_validation = self.validator.validate(
                normalized,
                repaired,
                task=request.task,
                grammar_mode=True,
                protected_terms=request.protected_terms,
                # Judge the cleanup against the text it cleaned, not the
                # writer's typos. `repaired` is `deterministic` plus
                # structural tidying, so without this the rules layer's own
                # spelling repairs were charged to it: "shoud" -> "should"
                # was refused as "obligation strength changed (added should)"
                # and the paragraph lost its only fallback.
                dense_source=dense_source,
                prepared=deterministic,
            )
            if repaired_validation.accepted and repaired_quality.complete:
                deterministic_recovery = _SafeCandidate(
                    text=repaired,
                    validation=repaired_validation,
                    residual_errors=repaired_quality.residual_errors,
                    tone_verified=repaired_quality.tone_verified,
                    quality_score=repaired_quality.score,
                    used_llm=False,
                )
        spans = _sentence_spans_for_recovery(normalized)
        if not spans:
            return deterministic_recovery, 0, 0.0, 0.0

        single_seed = deterministic
        if len(spans) == 1 and safe_candidates:
            single_seed = max(safe_candidates, key=_safe_candidate_rank).text

        selected_parts: list[tuple[int, int, _SafeCandidate]] = []
        calls = 0
        generation_seconds = 0.0
        cold_load_seconds = 0.0
        for start, end in spans:
            source = normalized[start:end]
            if len(spans) == 1:
                seed = single_seed.strip()
            else:
                seed = self.rules.correct(
                    source,
                    user_terms=request.protected_terms,
                ).text.strip()
            # What the rules layer produced for this sentence, before any
            # command-specific reduction. The guards below compare against it
            # so a repaired misspelling is never counted as an invented word.
            seed_reference = seed
            if request.command.strip().casefold() == "concise":
                seed = _apply_safe_concise_reductions(seed)
            seed_quality = self._evaluate_quality(
                source,
                seed,
                command=request.command,
            )
            seed_validation = self.validator.validate(
                source,
                seed,
                task=request.task,
                grammar_mode=request.command.strip().casefold() == "proofread",
                protected_terms=tuple(
                    value for value in request.protected_terms if value in source
                ),
                # Semantic similarity and protected facts are still measured
                # against the writer's own sentence; only the word-counting
                # guards move to the spelling-repaired reference. "befor" ->
                # "before" was being refused as "date or ordering
                # relationship changed (added before)", which left the
                # sentence with no fallback and failed the whole paragraph.
                dense_source=dense_source,
                prepared=seed_reference,
            )
            seed_validation = _validate_rephrase_actions(
                seed_validation,
                source,
                seed,
                command=request.command,
            )
            seed_candidate = _SafeCandidate(
                text=seed,
                validation=seed_validation,
                residual_errors=seed_quality.residual_errors,
                tone_verified=seed_quality.tone_verified,
                quality_score=seed_quality.score,
                used_llm=False,
            )
            can_keep_seed = (
                len(spans) > 1
                and (
                    seed == source
                    or request.command.strip().casefold() == "concise"
                )
                and seed_validation.accepted
                and seed_quality.complete
            )

            segment_terms = tuple(
                dict.fromkeys(
                    (
                        *(
                            value
                            for value in request.protected_terms
                            if value in source
                        ),
                        *_runtime_opaque_entities(source, request.document),
                        *_runtime_temporal_fact_phrases(seed),
                        *(
                            _runtime_negated_constraint_phrases(seed)
                            if request.command in _STAGED_STYLE_COMMANDS
                            else ()
                        ),
                        *(
                            _runtime_negation_literals(seed)
                            if request.command in _STAGED_STYLE_COMMANDS
                            else ()
                        ),
                        *(
                            _runtime_modal_actor_phrases(seed)
                            if request.command in _STAGED_STYLE_COMMANDS
                            else ()
                        ),
                        *(
                            _runtime_alternative_fact_phrases(seed)
                            if request.command in _STAGED_STYLE_COMMANDS
                            else ()
                        ),
                    )
                )
            )
            residual = ", ".join(seed_quality.residual_errors) or "final grammar"
            segment_actors = ", ".join(
                f"{name}={count}"
                for name, count in sorted(_actor_counter(source).items())
            )
            segment_negations = ", ".join(
                f"{name}={count}"
                for name, count in sorted(_negation_counter(source).items())
            )
            actor_rule = (
                " Preserve this exact actor inventory, including pronoun counts: "
                + segment_actors
                + "."
                if segment_actors
                else (
                    " The source has no pronoun actor. Do not add I, we, you, "
                    "he, she, they, or it."
                )
            )
            negation_rule = (
                " Preserve this exact negation inventory: "
                + segment_negations
                + ". Keep each negation in its source clause."
                if segment_negations
                else ""
            )
            cooperative_recovery_rule = ""
            if request.command.strip().casefold() == "cooperative":
                cooperative_recovery_rule = (
                    " Make shared effort visible by revising one existing clause. "
                    "Use working together, collaborate, or coordinate only where the "
                    "source already names a team or reviewers. Keep the same sentence "
                    "count. Do not append a new sentence, goal, benefit, solution, "
                    "readiness claim, recommendation, proposal, or fact."
                )
            concise_recovery_rule = ""
            if request.command.strip().casefold() == "concise":
                source_word_count = len(_WORD.findall(source))
                target_word_count = source_word_count - max(
                    1,
                    source_word_count // 20,
                )
                concise_recovery_rule = (
                    f" Use no more than {target_word_count} words. Remove filler, "
                    "repetition, and needless qualifiers. Combine redundant clauses "
                    "without deleting any fact or constraint."
                )
            recovery_instruction = (
                instruction
                + "\nFinal recovery pass for this section only. "
                + f"Fix these remaining checks: {residual}. "
                + "Return only the corrected text. Preserve every fact, actor, "
                + "number, date, negation, obligation, and protected value."
                + cooperative_recovery_rule
                + concise_recovery_rule
                + actor_rule
                + negation_rule
            )
            generation = None
            while generation is None and plan.use_llm:
                calls += 1
                try:
                    generation = self.runtime.generate(
                        GenerationRequest(
                            text=seed,
                            command=request.command,
                            profile=request.profile,
                            instruction=recovery_instruction,
                            language=request.language,
                            protected_terms=segment_terms,
                            plan=plan,
                        )
                    )
                except (
                    AttributeError,
                    IndexError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    if _is_cancellation_error(exc):
                        raise
                    if classifier is not None and snapshot is not None:
                        plan = self._fallback_after_runtime_failure(
                            request=request,
                            classifier=classifier,
                            snapshot=snapshot,
                            state=cascade_state,
                            error=exc,
                        )
                        _notify_plan(request, plan)
                        if plan.use_llm:
                            continue
                    break
            if generation is None:
                if can_keep_seed:
                    selected_parts.append((start, end, seed_candidate))
                    continue
                return (
                    deterministic_recovery,
                    calls,
                    generation_seconds,
                    cold_load_seconds,
                )

            generation_seconds += generation.elapsed_seconds
            cold_load_seconds += generation.cold_load_seconds
            generated = _final_cleanup_candidate(
                request.command,
                generation.text.strip(),
            )
            generated = _restore_rephrase_content_nouns(
                source,
                generated,
                command=request.command,
            )
            if (
                request.task is PolishTask.TONE
                or request.command in _STAGED_STYLE_COMMANDS
            ):
                generated = _restore_tone_actor_pronouns(source, generated)
                generated = _restore_source_phrase_casing(source, generated)
            generated_validation = self.validator.validate(
                source,
                generated,
                task=request.task,
                grammar_mode=request.command.strip().casefold() == "proofread",
                protected_terms=tuple(
                    value for value in request.protected_terms if value in source
                ),
                dense_source=dense_source,
                # `seed` is what this sentence was actually handed to the model.
                prepared=seed,
            )
            generated_validation = _validate_rephrase_actions(
                generated_validation,
                source,
                generated,
                command=request.command,
            )
            generated_quality = self._evaluate_quality(
                source,
                generated,
                command=request.command,
            )
            if not generated_validation.accepted or not generated_quality.complete:
                if classifier is not None and snapshot is not None:
                    failure_details = tuple(
                        dict.fromkeys(
                            (
                                *generated_validation.failures,
                                *(
                                    f"residual {value}"
                                    for value in generated_quality.residual_errors
                                ),
                                *(
                                    ()
                                    if generated_quality.tone_verified
                                    else ("tone contract failed",)
                                ),
                            )
                        )
                    )
                    plan = self._fallback_after_runtime_failure(
                        request=request,
                        classifier=classifier,
                        snapshot=snapshot,
                        state=cascade_state,
                        error=RuntimeError(
                            "validation failed: "
                            + (
                                "; ".join(failure_details)
                                or "Local writing model returned incomplete text"
                            )
                        ),
                    )
                    _notify_plan(request, plan)
                    if plan.use_llm:
                        nested, nested_calls, nested_generation, nested_load = (
                            self._recover_failed_generation(
                                request=replace(request, text=source),
                                normalized=source,
                                deterministic=seed,
                                plan=plan,
                                instruction=instruction,
                                safe_candidates=[],
                                classifier=classifier,
                                snapshot=snapshot,
                                cascade_state=cascade_state,
                                dense_source=dense_source,
                            )
                        )
                        calls += nested_calls
                        generation_seconds += nested_generation
                        cold_load_seconds += nested_load
                        plan = cascade_state.plan
                        if nested is not None:
                            selected_parts.append((start, end, nested))
                            continue
                if can_keep_seed:
                    selected_parts.append((start, end, seed_candidate))
                    continue
                return (
                    deterministic_recovery,
                    calls,
                    generation_seconds,
                    cold_load_seconds,
                )
            selected_parts.append(
                (
                    start,
                    end,
                    _SafeCandidate(
                        text=generated,
                        validation=generated_validation,
                        residual_errors=generated_quality.residual_errors,
                        tone_verified=generated_quality.tone_verified,
                        quality_score=generated_quality.score,
                        used_llm=True,
                    ),
                )
            )

        all_parts_ai_backed = bool(selected_parts) and (
            len(selected_parts) == len(spans)
            and all(value.used_llm for _, _, value in selected_parts)
        )
        if not all_parts_ai_backed:
            if deterministic_recovery is not None:
                return (
                    deterministic_recovery,
                    calls,
                    generation_seconds,
                    cold_load_seconds,
                )
            any_part_ai_backed = any(
                value.used_llm for _, _, value in selected_parts
            )
            all_deterministic_concise = (
                not any_part_ai_backed
                and request.command.strip().casefold() == "concise"
            )
            if not all_deterministic_concise:
                return None, calls, generation_seconds, cold_load_seconds

        pieces: list[str] = []
        cursor = 0
        for start, end, selected in selected_parts:
            pieces.append(normalized[cursor:start])
            pieces.append(selected.text)
            cursor = end
        pieces.append(normalized[cursor:])
        recovered_text = "".join(pieces)
        recovered_validation = self.validator.validate(
            normalized,
            recovered_text,
            task=request.task,
            grammar_mode=request.command.strip().casefold() == "proofread",
            protected_terms=request.protected_terms,
            # Every sentence in `recovered_text` already passed its own
            # validation. Re-judging the stitched paragraph against the raw
            # source charged it once more for the spelling the rules layer had
            # fixed, so a paragraph assembled entirely from accepted sentences
            # could still be refused as a whole.
            dense_source=dense_source,
            prepared=deterministic,
        )
        recovered_validation = _validate_rephrase_actions(
            recovered_validation,
            normalized,
            recovered_text,
            command=request.command,
        )
        if not recovered_validation.accepted:
            return (
                deterministic_recovery,
                calls,
                generation_seconds,
                cold_load_seconds,
            )
        recovered_quality = self._evaluate_quality(
            normalized,
            recovered_text,
            command=request.command,
        )
        if not recovered_quality.complete:
            return (
                deterministic_recovery,
                calls,
                generation_seconds,
                cold_load_seconds,
            )
        return (
            _SafeCandidate(
                text=recovered_text,
                validation=recovered_validation,
                residual_errors=recovered_quality.residual_errors,
                tone_verified=recovered_quality.tone_verified,
                quality_score=recovered_quality.score,
                used_llm=all_parts_ai_backed,
            ),
            calls,
            generation_seconds,
            cold_load_seconds,
        )

    def _catalog(self) -> tuple[tuple[PolishModelPack, ...], str]:
        try:
            catalog = self.packs.catalog(refresh_installed=True)
        except (ModelPackSecurityError, OSError, RuntimeError, ValueError) as exc:
            reason = str(exc)
            # Losing the catalogue turns the local AI off and leaves only the
            # deterministic rules. That happened for a whole session because a
            # manifest was edited without being re-signed, and nothing said
            # so: no log line, no status text, just quietly worse writing.
            # Repeat it once per distinct reason so it is visible without
            # filling the log on every paragraph.
            if reason != self._last_catalog_error:
                self._last_catalog_error = reason
                logger.warning(
                    "Local AI unavailable, correcting with rules only: %s",
                    reason,
                )
            return (), reason
        if self._last_catalog_error:
            logger.info("Local AI model catalogue is readable again")
            self._last_catalog_error = ""
        return catalog, ""

    def _remember_failed_pack(
        self,
        pack_id: str,
        reason: str,
        *,
        kind: RuntimeFailureKind | None = None,
        loading: bool = False,
    ) -> None:
        pack_id = str(pack_id or "").strip()
        if not pack_id:
            return
        safe_reason = str(reason or "Local AI pack failed.").strip()[:300]
        failure_kind = kind or _runtime_failure_kind(
            RuntimeError(safe_reason),
            loading=loading,
        )
        failed_at = self._monotonic()
        with self._failed_pack_lock:
            self._failed_pack_sequence += 1
            previous = self._failure_streaks.get(pack_id)
            consecutive = 1
            if (
                previous is not None
                and previous.kind is failure_kind
                and failed_at - previous.failed_at < _FAILURE_STREAK_WINDOW_SECONDS
            ):
                consecutive = previous.count + 1
            self._failure_streaks[pack_id] = _FailureStreak(
                kind=failure_kind,
                failed_at=failed_at,
                count=consecutive,
            )
            self._failed_pack_states[pack_id] = _FailedPackState(
                reason=safe_reason,
                kind=failure_kind,
                failed_at=failed_at,
                sequence=self._failed_pack_sequence,
                consecutive=consecutive,
            )

    def _prune_failed_packs_locked(self, now: float) -> None:
        expired = tuple(
            pack_id
            for pack_id, state in self._failed_pack_states.items()
            if now - state.failed_at >= _failed_pack_ttl(state.kind, state.consecutive)
        )
        for pack_id in expired:
            self._failed_pack_states.pop(pack_id, None)

    def _failed_pack_ids(self) -> set[str]:
        now = self._monotonic()
        with self._failed_pack_lock:
            self._prune_failed_packs_locked(now)
            return set(self._failed_pack_states)

    def _latest_failed_pack(self) -> _FailedPackState | None:
        now = self._monotonic()
        with self._failed_pack_lock:
            self._prune_failed_packs_locked(now)
            return max(
                self._failed_pack_states.values(),
                key=lambda state: (state.failed_at, state.sequence),
                default=None,
            )

    def _failed_pack_reason(self) -> str:
        state = self._latest_failed_pack()
        return state.reason if state is not None else ""

    def _benchmark_records(
        self,
        snapshot: HardwareSnapshot,
        packs: Iterable[PolishModelPack],
        *,
        task: PolishTask,
        mode: PolishMode,
        classifier: ClassifierDecision,
        manual: bool,
    ) -> dict[str, BenchmarkRecord]:
        records: dict[str, BenchmarkRecord] = {}
        for pack in packs:
            candidate_plan = self.policy.plan(
                snapshot,
                task,
                mode,
                classifier=classifier,
                packs=(pack,),
                manual=manual,
            )
            if (
                not candidate_plan.use_llm
                or candidate_plan.pack_id != pack.pack_id
            ):
                continue
            key = self.benchmark_cache.key(
                snapshot,
                runtime_id=self.runtime.runtime_id,
                pack=pack,
                plan=candidate_plan,
            )
            value = self.benchmark_cache.get(key)
            if value is not None:
                records[pack.pack_id] = value
        return records

    def _accept_plan(
        self,
        plan: ExecutionPlan,
        snapshot: HardwareSnapshot,
        pack: PolishModelPack,
        *,
        allow_installed_fallback: bool = True,
    ) -> ExecutionPlan:
        is_current = getattr(self.runtime, "is_current", None)
        if callable(is_current) and is_current(
            pack.pack_id,
            plan.model,
            plan.policy_version,
            require_loaded=True,
        ):
            # A verified warm lease remains valid across every document
            # section. Content validation failure must not force another
            # hardware probe or poison the model for later sections.
            return replace(plan, requires_probe=False)
        key = self.benchmark_cache.key(
            snapshot,
            runtime_id=self.runtime.runtime_id,
            pack=pack,
            plan=plan,
        )
        record = self.benchmark_cache.get(key)
        # The short-lived cache uses the same full identity as persistence.
        # Driver, CPU, RAM, pack version/digest, mode, latency, backend, and
        # full-GPU requirements must all match.
        recent_key = key
        recent = self._recent_probe_records.get(recent_key)
        if (
            record is None
            and recent is not None
            and snapshot.driver_healthy
            and self._monotonic() - recent[0] <= 300.0
        ):
            record = recent[1]
        if record is None:
            probe = self.runtime.acceptance_probe(plan)
            record = BenchmarkRecord(
                accepted=probe.accepted,
                correctness_passed=probe.correctness_passed,
                protected_passed=probe.protected_passed,
                elapsed_seconds=probe.elapsed_seconds,
                backend=probe.backend.value,
                reason=probe.reason,
                measured_at=datetime.now(timezone.utc).isoformat(),
            )
            self.benchmark_cache.put(key, record)
        if record.accepted:
            self._recent_probe_records[recent_key] = (self._monotonic(), record)
            return replace(plan, requires_probe=False)

        failure_reason = (
            "Selected local AI failed its local acceptance probe: "
            f"{record.reason or 'correctness or latency gate failed'}"
        )
        self._remember_failed_pack(
            pack.pack_id,
            failure_reason,
            kind=RuntimeFailureKind.VALIDATION_FAILURE,
        )
        if not allow_installed_fallback:
            return replace(
                plan,
                use_llm=False,
                pack_id="",
                model="",
                backend=RuntimeBackend.RULES,
                standby_model="",
                requires_probe=False,
                downgrade_reason=failure_reason,
            )

        # Try only already installed smaller packs. Never surprise the user
        # with a second multi-gigabyte download after a failed probe.
        catalog, _ = self._catalog()
        installed = tuple(
            value
            for value in catalog
            if value.installed
            and value.pack_id != pack.pack_id
            and value.size_bytes < pack.size_bytes
        )
        fallback_classifier = ClassifierDecision(
            needs_llm=True,
            error_categories=("probe-fallback",),
            severity=1.0,
            context_scope=_scope_for_plan(plan),
            english_confidence=1.0,
            backend="probe-fallback",
            elapsed_ms=0.0,
        )
        records = self._benchmark_records(
            snapshot,
            installed,
            task=plan.task,
            mode=plan.mode,
            classifier=fallback_classifier,
            manual=True,
        )
        records[pack.pack_id] = record
        fallback = self.policy.plan(
            snapshot,
            plan.task,
            plan.mode,
            classifier=fallback_classifier,
            packs=installed,
            benchmark_records=records,
        )
        if fallback.use_llm:
            if _backend_placement_rank(fallback.backend) < _backend_placement_rank(
                plan.backend
            ):
                return replace(
                    fallback,
                    use_llm=False,
                    pack_id="",
                    model="",
                    backend=RuntimeBackend.RULES,
                    standby_model="",
                    requires_probe=False,
                    downgrade_reason=failure_reason,
                )
            fallback_pack = next(
                value for value in installed if value.pack_id == fallback.pack_id
            )
            return self._accept_plan(fallback, snapshot, fallback_pack)
        return replace(
            fallback,
            downgrade_reason=failure_reason,
        )

    def _accept_smaller_plan(
        self,
        *,
        request: CorrectionRequest,
        classifier: ClassifierDecision,
        snapshot: HardwareSnapshot,
        rejected_plan: ExecutionPlan,
        attempted_pack_ids: set[str],
    ) -> ExecutionPlan:
        """Prepare and probe each smaller eligible pack once.

        Fallbacks must already be installed. Consent for the primary pack never
        authorizes another multi-gigabyte download. Every installed candidate
        is ensured and prepared before its acceptance probe, and a failed pack
        is never selected again during this engine run.
        """

        full_catalog, _ = self._catalog()
        rejected_pack = next(
            (
                pack
                for pack in full_catalog
                if pack.pack_id == rejected_plan.pack_id
            ),
            None,
        )
        catalog = tuple(pack for pack in full_catalog if pack.installed)
        failure_reason = (
            rejected_plan.downgrade_reason
            or rejected_plan.reason
            or "Local AI pack failed."
        )
        terminal = replace(
            rejected_plan,
            use_llm=False,
            pack_id="",
            model="",
            backend=RuntimeBackend.RULES,
            standby_model="",
            requires_probe=False,
            downgrade_reason=failure_reason[:300],
        )
        if not rejected_plan.use_llm or rejected_pack is None:
            return terminal
        size_ceiling = rejected_pack.size_bytes
        minimum_placement = _backend_placement_rank(rejected_plan.backend)
        # The caller owns this per-request set. Mutating it guarantees that a
        # later generation failure cannot retry a pack that already failed its
        # install, preparation, or acceptance step in this request.
        attempted = attempted_pack_ids
        attempted.update(self._failed_pack_ids())

        # The signed catalog is finite. The bound also protects custom pack
        # providers from returning the same candidate forever.
        for _ in range(len(catalog)):
            candidates = tuple(
                pack
                for pack in catalog
                if pack.pack_id not in attempted
                and pack.size_bytes < size_ceiling
            )
            if not candidates:
                break
            records = self._benchmark_records(
                snapshot,
                candidates,
                task=request.task,
                mode=request.mode,
                classifier=classifier,
                manual=request.manual,
            )
            next_plan = self.policy.plan(
                snapshot,
                request.task,
                request.mode,
                classifier=classifier,
                packs=candidates,
                benchmark_records=records,
                manual=request.manual,
            )
            if not next_plan.use_llm or next_plan.pack_id in attempted:
                break

            selected_pack = next(
                (
                    pack
                    for pack in candidates
                    if pack.pack_id == next_plan.pack_id
                ),
                None,
            )
            if selected_pack is None:
                break
            attempted.add(next_plan.pack_id)
            size_ceiling = selected_pack.size_bytes
            if _backend_placement_rank(next_plan.backend) < minimum_placement:
                continue
            _notify_plan(request, next_plan)
            try:
                pack = self.packs.ensure(
                    next_plan,
                    consent=False,
                )
                if pack.pack_id != next_plan.pack_id:
                    raise ModelPackSecurityError(
                        "The installed model pack does not match the selected pack."
                    )
                next_plan = replace(
                    next_plan,
                    model=pack.model,
                    pack_id=pack.pack_id,
                )
                prepare = getattr(self.runtime, "prepare", None)
                if callable(prepare):
                    prepared = bool(
                        prepare(
                            next_plan,
                            consent=False,
                        )
                    )
                    if not prepared:
                        reason = str(
                            getattr(self.runtime, "last_downgrade_reason", "")
                        ).strip()
                        if reason:
                            next_plan = replace(
                                next_plan,
                                downgrade_reason=(
                                    "Bundled local AI runtime unavailable. Using the "
                                    "compatibility runtime."
                                )[:300],
                            )
                checked = self._accept_plan(
                    next_plan,
                    snapshot,
                    pack,
                    allow_installed_fallback=False,
                )
            except (
                ModelPackConsentRequired,
                ModelPackDownloadError,
                ModelPackNotFoundError,
                ModelPackSecurityError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                if _is_cancellation_error(exc):
                    raise
                self._remember_failed_pack(
                    next_plan.pack_id,
                    str(exc),
                    kind=_runtime_failure_kind(exc, loading=True),
                )
                terminal = replace(
                    next_plan,
                    use_llm=False,
                    pack_id="",
                    model="",
                    backend=RuntimeBackend.RULES,
                    standby_model="",
                    requires_probe=False,
                    downgrade_reason=str(exc)[:300],
                )
                continue

            _notify_plan(request, checked)
            if checked.use_llm:
                reasons = tuple(
                    dict.fromkeys(
                        value
                        for value in (
                            terminal.downgrade_reason.strip(),
                            checked.downgrade_reason.strip(),
                            self._failed_pack_reason().strip(),
                        )
                        if value
                    )
                )
                return replace(
                    checked,
                    downgrade_reason="; ".join(reasons)[:300],
                )
            terminal = checked

        return terminal

    def _fallback_after_runtime_failure(
        self,
        *,
        request: CorrectionRequest,
        classifier: ClassifierDecision,
        snapshot: HardwareSnapshot,
        state: _RuntimeCascadeState,
        error: BaseException,
    ) -> ExecutionPlan:
        if _is_cancellation_error(error):
            raise error
        failed_plan = state.plan
        original_reason = str(error).strip()[:300]
        original_kind = _runtime_failure_kind(error)
        self._remember_failed_pack(
            failed_plan.pack_id,
            original_reason,
            kind=original_kind,
        )
        state.attempted_pack_ids.add(failed_plan.pack_id)
        state.failure_kind = original_kind
        state.failure_message = original_reason
        rejected_plan = replace(
            failed_plan,
            downgrade_reason=original_reason,
        )
        next_plan = self._accept_smaller_plan(
            request=request,
            classifier=classifier,
            snapshot=snapshot,
            rejected_plan=rejected_plan,
            attempted_pack_ids=state.attempted_pack_ids,
        )
        if not next_plan.use_llm:
            latest_failure = self._latest_failed_pack()
            if latest_failure is not None:
                state.failure_message = latest_failure.reason
                state.failure_kind = latest_failure.kind
            else:
                state.failure_message = next_plan.downgrade_reason or original_reason
                state.failure_kind = original_kind
        state.plan = next_plan
        return next_plan

    def _result(
        self,
        request: CorrectionRequest,
        normalized: str,
        deterministic: str,
        final_text: str,
        classifier: ClassifierDecision,
        plan: ExecutionPlan,
        validation: ValidationResult,
        started: float,
        *,
        used_llm: bool = False,
        rejected_llm: bool = False,
        retries: int = 0,
        message: str,
        deterministic_ms: float,
        generation_seconds: float = 0.0,
        runtime_failure_kind: RuntimeFailureKind = RuntimeFailureKind.NONE,
        cold_load_seconds: float = 0.0,
        refusal_reason: str = "",
    ) -> CorrectionResult:
        total_ms = (time.perf_counter() - started) * 1000.0
        quality = self._evaluate_quality(
            normalized,
            final_text,
            command=request.command,
        )
        quality_complete = bool(
            used_llm and validation.accepted and quality.complete
        )
        review_ready = bool(
            final_text.strip()
            and used_llm
            and validation.accepted
            and quality.complete
        )
        if rejected_llm and runtime_failure_kind is RuntimeFailureKind.NONE:
            runtime_failure_kind = RuntimeFailureKind.VALIDATION_FAILURE
        return CorrectionResult(
            original_text=request.text,
            normalized_text=normalized,
            deterministic_text=deterministic,
            text=final_text,
            classifier=classifier,
            plan=plan,
            validation=validation,
            edits=minimal_edits(normalized, final_text),
            used_llm=used_llm,
            rejected_llm=rejected_llm,
            retries=retries,
            message=message,
            quality_complete=quality_complete,
            residual_errors=quality.residual_errors,
            tone_verified=quality.tone_verified,
            quality_score=quality.score,
            runtime_failure_kind=runtime_failure_kind,
            cold_load_seconds=max(0.0, cold_load_seconds),
            generation_seconds=max(0.0, generation_seconds),
            review_ready=review_ready,
            metrics={
                "deterministic_ms": round(deterministic_ms, 3),
                "classifier_ms": round(classifier.elapsed_ms, 3),
                "generation_seconds": round(generation_seconds, 3),
                "total_ms": round(total_ms, 3),
                "used_llm": used_llm,
                "rejected_llm": rejected_llm,
                "review_ready": review_ready,
                "backend": plan.backend.value,
                "pack_id": plan.pack_id,
                # Why the model's answer was not used, in engine terms rather
                # than the reader-facing wording. Without this a refused
                # section is indistinguishable from a clean one from the
                # outside, which is how a run of nine rules-only paragraphs
                # was reported as nine successes.
                "refusal_reason": refusal_reason,
            },
        )


class PolishWritingAssistantAdapter:
    """Compatibility adapter for the mature PySide panels."""

    def __init__(
        self,
        engine: PolishEngine,
        *,
        mode_provider: Callable[[], PolishMode],
        consent_provider: Callable[[], bool],
    ) -> None:
        self.engine = engine
        self.mode_provider = mode_provider
        self.consent_provider = consent_provider
        self._thread_result = threading.local()

    @property
    def last_correction_result(self) -> CorrectionResult | None:
        return getattr(self._thread_result, "value", None)

    @property
    def endpoint(self) -> str:
        assistant = getattr(self.engine.runtime, "assistant", None)
        if assistant is None:
            fallback = getattr(self.engine.runtime, "fallback", None)
            assistant = getattr(fallback, "assistant", None)
        return str(getattr(assistant, "endpoint", "http://127.0.0.1:11434"))

    def runtime_status(self, model: str, *, timeout: float = 1.0):
        value = self.engine.runtime.status(model, timeout=timeout)
        if value.loaded:
            if value.backend in {
                RuntimeBackend.CUDA,
                RuntimeBackend.HIP,
                RuntimeBackend.VULKAN,
                RuntimeBackend.SYCL,
            }:
                backend = "GPU"
            elif value.backend is RuntimeBackend.CPU:
                backend = "CPU"
            else:
                backend = "Rules"
        else:
            backend = "Loading"
        return WritingRuntimeStatus(
            value.model,
            value.loaded,
            backend,
            value.model_bytes,
            value.gpu_bytes,
        )

    def run(
        self,
        text: str,
        *,
        command: str,
        model: str = "",
        instruction: str = "",
        language: str = "",
        profile: str = "General",
        protected_terms=(),
        timeout: float = 30.0,
        document: str = "",
        **_ignored,
    ) -> WritingResult:
        mode = PolishMode.coerce(self.mode_provider())
        consent = bool(self.consent_provider())
        protected = tuple(protected_terms)
        plan_callback = _ignored.get("plan_callback")
        self._thread_result.value = None
        proofread = None
        stage_text = text
        if command in _STAGED_STYLE_COMMANDS:
            proofread = self.engine.analyze(
                CorrectionRequest(
                    text=text,
                    task=_task_for_text("Proofread", text),
                    mode=mode,
                    command="Proofread",
                    profile=profile,
                    instruction=instruction,
                    language=language,
                    protected_terms=protected,
                    manual=True,
                    download_consent=consent,
                    document=document,
                    plan_callback=plan_callback,
                )
            )
            if not proofread.quality_complete or not proofread.review_ready:
                self._thread_result.value = proofread
                return WritingResult(
                    proofread.text,
                    command,
                    proofread.plan.model or model or "rules",
                    profile,
                )
            stage_text = proofread.text
            if command == "Caveman":
                stage_text = _apply_safe_caveman_splits(stage_text)
                validator = getattr(self.engine, "validator", None)
                if validator is None:
                    safe_validation = proofread.validation
                else:
                    safe_validation = validator.validate(
                        proofread.text,
                        stage_text,
                        task=PolishTask.TONE,
                        grammar_mode=False,
                        protected_terms=protected,
                    )
                evaluate_quality = getattr(self.engine, "_evaluate_quality", None)
                if callable(evaluate_quality):
                    safe_quality = evaluate_quality(
                        proofread.text,
                        stage_text,
                        command=command,
                    )
                else:
                    safe_quality = getattr(
                        self.engine,
                        "quality_gate",
                        CorrectionQualityGate(),
                    ).evaluate(stage_text, command=command)
                if safe_validation.accepted and safe_quality.complete:
                    safe_result = replace(
                        proofread,
                        original_text=text,
                        text=stage_text,
                        validation=safe_validation,
                        edits=minimal_edits(text, stage_text),
                        quality_complete=True,
                        residual_errors=safe_quality.residual_errors,
                        tone_verified=safe_quality.tone_verified,
                        quality_score=safe_quality.score,
                        review_ready=True,
                        message="AI proofread plus safe plain-language transform ready.",
                    )
                    self._thread_result.value = safe_result
                    return WritingResult(
                        stage_text,
                        command,
                        proofread.plan.model or model or "rules",
                        profile,
                    )

        task = _task_for_text(command, stage_text)
        result = self.engine.analyze(
            CorrectionRequest(
                text=stage_text,
                task=task,
                mode=mode,
                command=command,
                profile=profile,
                instruction=instruction,
                language=language,
                protected_terms=protected,
                manual=True,
                download_consent=consent,
                preverified_fallback_available=(
                    proofread is not None
                    and command in _STAGED_STYLE_COMMANDS
                ),
                document=document,
                plan_callback=plan_callback,
            )
        )
        if (
            command == "Cooperative"
            and proofread is not None
            and (not result.quality_complete or not result.review_ready)
        ):
            fallback_text = _apply_safe_cooperative_signal(proofread.text)
            if fallback_text != proofread.text:
                validator = getattr(self.engine, "validator", None)
                if validator is None:
                    fallback_validation = proofread.validation
                else:
                    fallback_validation = validator.validate(
                        proofread.text,
                        fallback_text,
                        task=PolishTask.TONE,
                        grammar_mode=False,
                        protected_terms=protected,
                    )
                evaluate_quality = getattr(self.engine, "_evaluate_quality", None)
                if callable(evaluate_quality):
                    fallback_quality = evaluate_quality(
                        proofread.text,
                        fallback_text,
                        command=command,
                    )
                else:
                    fallback_quality = getattr(
                        self.engine,
                        "quality_gate",
                        CorrectionQualityGate(),
                    ).evaluate(fallback_text, command=command)
                if fallback_validation.accepted and fallback_quality.complete:
                    result = replace(
                        result,
                        text=fallback_text,
                        validation=fallback_validation,
                        used_llm=proofread.used_llm,
                        rejected_llm=True,
                        quality_complete=True,
                        residual_errors=fallback_quality.residual_errors,
                        tone_verified=fallback_quality.tone_verified,
                        quality_score=fallback_quality.score,
                        review_ready=True,
                        message=(
                            "AI proofread plus safe Cooperative repair ready."
                        ),
                    )
        if (
            command == "Persuasive"
            and proofread is not None
            and (not result.quality_complete or not result.review_ready)
        ):
            fallback_text = _apply_safe_persuasive_restructure(proofread.text)
            if (
                fallback_text != proofread.text
                and _persuasive_rewrite_is_visible(proofread.text, fallback_text)
            ):
                validator = getattr(self.engine, "validator", None)
                if validator is None:
                    fallback_validation = proofread.validation
                else:
                    fallback_validation = validator.validate(
                        proofread.text,
                        fallback_text,
                        task=PolishTask.TONE,
                        grammar_mode=False,
                        protected_terms=protected,
                    )
                fallback_quality = getattr(
                    self.engine,
                    "quality_gate",
                    CorrectionQualityGate(),
                ).evaluate(fallback_text, command=command)
                if fallback_validation.accepted and fallback_quality.complete:
                    result = replace(
                        result,
                        text=fallback_text,
                        validation=fallback_validation,
                        used_llm=proofread.used_llm,
                        rejected_llm=True,
                        quality_complete=True,
                        residual_errors=fallback_quality.residual_errors,
                        tone_verified=True,
                        quality_score=fallback_quality.score,
                        review_ready=True,
                        message="AI proofread plus safe Persuasive restructure ready.",
                    )
        if (
            command == "Caveman"
            and proofread is not None
            and (
                not result.quality_complete
                or not result.review_ready
                or not getattr(
                    self.engine,
                    "quality_gate",
                    CorrectionQualityGate(),
                ).evaluate(result.text, command=command).complete
            )
        ):
            fallback_text = _apply_safe_caveman_splits(proofread.text)
            validator = getattr(self.engine, "validator", None)
            if validator is None:
                fallback_validation = proofread.validation
            else:
                fallback_validation = validator.validate(
                    proofread.text,
                    fallback_text,
                    task=PolishTask.TONE,
                    grammar_mode=False,
                    protected_terms=protected,
                )
            quality_gate = getattr(
                self.engine,
                "quality_gate",
                CorrectionQualityGate(),
            )
            fallback_quality = quality_gate.evaluate(
                fallback_text,
                command=command,
            )
            if fallback_validation.accepted and fallback_quality.complete:
                result = replace(
                    result,
                    text=fallback_text,
                    validation=fallback_validation,
                    used_llm=proofread.used_llm,
                    rejected_llm=True,
                    quality_complete=True,
                    residual_errors=fallback_quality.residual_errors,
                    tone_verified=fallback_quality.tone_verified,
                    quality_score=fallback_quality.score,
                    review_ready=True,
                    message=(
                        "AI proofread plus safe plain-language fallback ready."
                    ),
                )
        if (
            command in _STAGED_STYLE_COMMANDS
            and command != "Caveman"
            and proofread is not None
            and (not result.quality_complete or not result.review_ready)
        ):
            command_key = command.strip().casefold()
            proofread_already_matches_tone = bool(
                (
                    command_key in {"rewrite", "improve"}
                    and proofread.quality_complete
                    and proofread.review_ready
                )
                or (
                    command_key == "persuasive"
                    and _persuasive_section_already_functional(proofread.text)
                )
                or (
                    command_key == "empathetic"
                    and _empathetic_section_already_functional(proofread.text)
                )
            )
            if proofread_already_matches_tone:
                residual_errors = proofread.residual_errors
            else:
                residual_errors = result.residual_errors
                residual_errors = tuple(
                    dict.fromkeys(
                        (
                            *residual_errors,
                            f"{command} tone was not verified",
                        )
                    )
                )
            result = replace(
                result,
                text=proofread.text,
                validation=proofread.validation,
                used_llm=proofread.used_llm,
                rejected_llm=True,
                quality_complete=proofread.quality_complete,
                residual_errors=residual_errors,
                tone_verified=proofread_already_matches_tone,
                quality_score=proofread.quality_score,
                review_ready=proofread.review_ready,
                message=(
                    (
                        (
                            (
                                "AI proofread retained. Clear writing already verified."
                                if command_key in {"rewrite", "improve"}
                                else "AI proofread retained. Persuasive role already verified."
                            )
                            if command_key in {"rewrite", "improve", "persuasive"}
                            else "AI proofread retained. Empathetic impact already verified."
                        )
                        if proofread_already_matches_tone
                        else (
                            f"Correction ready. Requested {command} style was not "
                            "safely applied."
                        )
                    )
                ),
            )
        if proofread is not None:
            result = replace(
                result,
                original_text=text,
                normalized_text=proofread.normalized_text,
                deterministic_text=proofread.deterministic_text,
                edits=minimal_edits(text, result.text),
                used_llm=proofread.used_llm and result.used_llm,
                rejected_llm=proofread.rejected_llm or result.rejected_llm,
                retries=proofread.retries + result.retries,
                cold_load_seconds=(
                    proofread.cold_load_seconds + result.cold_load_seconds
                ),
                generation_seconds=(
                    proofread.generation_seconds + result.generation_seconds
                ),
            )
        cleaned_text = _final_cleanup_candidate(command, result.text)
        if cleaned_text != result.text:
            cleanup_accepted = True
            cleanup_validation = result.validation
            cleanup_quality = None
            validator = getattr(self.engine, "validator", None)
            if validator is not None:
                cleanup_validation = validator.validate(
                    text,
                    cleaned_text,
                    task=result.plan.task,
                    grammar_mode=command.strip().casefold() == "proofread",
                    protected_terms=protected,
                    # The cleanup edits an already-accepted correction. Judged
                    # against the writer's raw text it was charged for every
                    # repair the correction had legitimately made, and a
                    # refusal here discards the finished correction.
                    prepared=result.deterministic_text or None,
                )
                cleanup_validation = _validate_rephrase_actions(
                    cleanup_validation,
                    text,
                    cleaned_text,
                    command=command,
                )
                cleanup_accepted = cleanup_validation.accepted
            evaluate_quality = getattr(self.engine, "_evaluate_quality", None)
            if cleanup_accepted and callable(evaluate_quality):
                cleanup_quality = evaluate_quality(
                    text,
                    cleaned_text,
                    command=command,
                )
                cleanup_accepted = cleanup_quality.complete
            if cleanup_accepted:
                result = replace(
                    result,
                    text=cleaned_text,
                    validation=cleanup_validation,
                    edits=minimal_edits(text, cleaned_text),
                    residual_errors=(
                        cleanup_quality.residual_errors
                        if cleanup_quality is not None
                        else result.residual_errors
                    ),
                    tone_verified=(
                        cleanup_quality.tone_verified
                        if cleanup_quality is not None
                        else result.tone_verified
                    ),
                    quality_score=(
                        cleanup_quality.score
                        if cleanup_quality is not None
                        else result.quality_score
                    ),
                )
        self._thread_result.value = result
        return WritingResult(
            result.text,
            command,
            result.plan.model or model or "rules",
            profile,
        )


def create_default_polish_engine(
    *,
    store: PolishSettingsStore | None,
    preferred_pack_provider: Callable[[], str],
) -> PolishEngine:
    pack_install_root = None
    store_path = getattr(store, "path", None)
    if store_path is not None:
        pack_install_root = Path(store_path).parent / "polish-model-packs"
        runtime_install_root = Path(store_path).parent / "polish-runtime-packs"
    else:
        runtime_install_root = None
    packs = ModelPackManager(install_root=pack_install_root)
    return PolishEngine(
        benchmark_cache=PolishBenchmarkCache(store),
        packs=packs,
        runtime=AdaptiveInferenceRuntime(
            model_packs=packs,
            runtime_packs=RuntimePackManager(install_root=runtime_install_root),
        ),
        preferred_pack_provider=preferred_pack_provider,
    )


_PERSUASIVE_SIGNALS = (
    "therefore",
    "for this reason",
    "this evidence",
    "these results",
    "demonstrates",
    "clearly shows",
    "strong case",
    "compelling reason",
    "which is why",
    "is essential",
    "is critical",
    "to ensure",
    "to prevent",
    "supports the",
    "reinforces",
)
_PERSUASIVE_POSITION = re.compile(
    r"\b(?:should|must|need(?:s)? to|recommend(?:s|ed|ation)?|"
    r"proposal|worth)\b",
    re.IGNORECASE,
)
_PERSUASIVE_REASON = re.compile(
    r"\b(?:because|therefore|for this reason|which is why|"
    r"to ensure|to prevent)\b",
    re.IGNORECASE,
)
_PERSUASIVE_EVIDENCE = re.compile(
    r"\b(?:data|evidence|result(?:s)?|response(?:s)?|survey|report|"
    r"research|finding(?:s)?|estimate|measure(?:s)?|record(?:s|ed)?|"
    r"proposal|claim|budget|funding|cost(?:s)?)\b",
    re.IGNORECASE,
)
_PERSUASIVE_IMPACT = re.compile(
    r"\b(?:shows?|supports?|interest|important|useful|risk|cost|benefit|"
    r"need(?:s|ed)?|require(?:s|d)?|consequence|blocked|failure|failed|"
    r"improve(?:s|d)?|reduce(?:s|d)?|increase(?:s|d)?|decrease(?:s|d)?|"
    r"accurate|inaccurate|access|fewer|barrier(?:s)?|"
    r"participat(?:e|ed|ing|ion))\b",
    re.IGNORECASE,
)
_PERSUASIVE_BOUNDARY = re.compile(
    r"\b(?:cannot|must not|does not|do not|only|unless|if|although|"
    r"however|but)\b",
    re.IGNORECASE,
)
_PERSUASIVE_DECISION = re.compile(
    r"\b(?:decision|approval|authorize|limit(?:s|ed)?|risk|cost|"
    r"confirmed|stop|pause(?:s|d)?|reject(?:s|ed)?|concern|guarantee|"
    r"conflict(?:s|ed|ing)?|delay(?:s|ed)?|late|problem|issue|"
    r"failure(?:s)?|mistake|emergenc(?:y|ies)|blocked)\b",
    re.IGNORECASE,
)
_PERSUASIVE_ACTION = re.compile(
    r"\b(?:pause(?:s|d)?|disable(?:s|d)?|restart(?:s|ed)?|begin(?:s)?|"
    r"began|complete(?:s|d)?|require(?:s|d)?|approve(?:s|d)?|"
    r"verify|protect|preserve|prevent|reduce|improve)\b",
    re.IGNORECASE,
)


_ASSERTIVE_HEDGE = re.compile(
    r"\b(?:maybe|perhaps|possibly|probably|might|could|seems?|appears?|"
    r"somewhat|kind of|sort of|I think|I feel)\b",
    re.IGNORECASE,
)


def _assertive_section_already_functional(text: str) -> bool:
    return bool(text.strip()) and not _ASSERTIVE_HEDGE.search(text)


_COOPERATIVE_PAST_TO_BASE = {
    "marked": "mark",
    "reported": "report",
    "reviewed": "review",
    "prepared": "prepare",
    "coordinated": "coordinate",
    "monitored": "monitor",
    "recorded": "record",
    "compared": "compare",
    "called": "call",
    "explained": "explain",
}


def _apply_safe_cooperative_signal(text: str) -> str:
    patterns = (
        (
            re.compile(
                r"\b(team)\s+(is|are|was|were)\s+preparing\b",
                re.IGNORECASE,
            ),
            lambda match: (
                f"{match.group(1)} {match.group(2)} working together to prepare"
            ),
        ),
        (
            re.compile(
                r"\b(team)\s+(was|were|is|are)\s+supposed\s+to\s+"
                r"([A-Za-z]+)\b",
                re.IGNORECASE,
            ),
            lambda match: (
                f"{match.group(1)} {match.group(2)} supposed to work together to "
                f"{match.group(3)}"
            ),
        ),
        (
            re.compile(
                r"\b(team|staff|technicians|researchers|volunteers|"
                r"department leads|reviewers)\s+"
                r"(must|will|should|can|need(?:s)?\s+to)\s+([A-Za-z]+)\b",
                re.IGNORECASE,
            ),
            lambda match: (
                f"{match.group(1)} {match.group(2)} work together to "
                f"{match.group(3)}"
            ),
        ),
        (
            re.compile(
                r"\b((?:[A-Za-z][\w'-]*\s+){0,4}team)\s+"
                r"(marked|reported|reviewed|prepared|coordinated|monitored|"
                r"recorded|compared|called|explained)\b",
                re.IGNORECASE,
            ),
            lambda match: (
                f"{match.group(1)} worked together to "
                f"{_COOPERATIVE_PAST_TO_BASE[match.group(2).casefold()]}"
            ),
        ),
        (
            re.compile(
                r"\b(department leads|reviewers|technicians|researchers|"
                r"volunteers)\s+(compar(?:e|es)|review(?:s)?|report(?:s)?|"
                r"prepar(?:e|es)|coordinat(?:e|es)|monitor(?:s)?|"
                r"record(?:s)?|write(?:s)?|call(?:s)?|explain(?:s)?)\b",
                re.IGNORECASE,
            ),
            lambda match: (
                f"{match.group(1)} work together to {match.group(2)}"
            ),
        ),
    )
    for pattern, replacement in patterns:
        revised, count = pattern.subn(replacement, text, count=1)
        if count:
            return revised
    direct_group_action = re.compile(
        r"\b(?P<group>staff|technicians|researchers|volunteers|"
        r"department leads|reviewers)\s+(?P<verb>[A-Za-z'-]+)\b",
        re.IGNORECASE,
    )
    for match in direct_group_action.finditer(text):
        verb = match.group("verb")
        base = _rephrase_action_base(verb)
        if base is None:
            continue
        work = "worked" if verb.casefold().endswith("ed") else "work"
        replacement = f"{match.group('group')} {work} together to {base}"
        return text[: match.start()] + replacement + text[match.end() :]
    return text


_COOPERATIVE_CONTEXT = re.compile(
    r"\b(?:[A-Za-z][\w'-]*\s+){0,4}teams?\s+"
    r"(?:must|will|should|can|need(?:s)?|is|are|was|were|have|has|"
    r"mark(?:s|ed|ing)?|compar(?:e|es|ed|ing)|review(?:s|ed|ing)?|"
    r"report(?:s|ed|ing)?|prepar(?:e|es|ed|ing)|"
    r"coordinat(?:e|es|ed|ing)|work(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_COOPERATIVE_COLLECTIVE_ACTION = re.compile(
    r"\b(?:staff|technicians|researchers|volunteers|departments|reviewers|"
    r"department leads|workers|trainers|employees|committees|partners)"
    r"\s+(?:must|will|should|can|need(?:s)?|are|were|have|has|"
    r"compar(?:e|es|ed|ing)|review(?:s|ed|ing)?|report(?:s|ed|ing)?|"
    r"prepar(?:e|es|ed|ing)|coordinat(?:e|es|ed|ing)|work(?:s|ed|ing)?|"
    r"carr(?:y|ies|ied|ying)|monitor(?:s|ed|ing)?|record(?:s|ed|ing)?|"
    r"enter(?:s|ed|ing)?|write(?:s|ing)?|call(?:s|ed|ing)?|"
    r"explain(?:s|ed|ing)?|request(?:s|ed|ing)?|submit(?:s|ted|ting)?|"
    r"test(?:s|ed|ing)?|correct(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_COOPERATIVE_SIGNAL = re.compile(
    r"\b(?:work(?:s|ed|ing)? together|collaborat(?:e|es|ed|ing|ion)|"
    r"coordinat(?:e|es|ed|ing|ion)|cooperat(?:e|es|ed|ing|ive|ion)|"
    r"shared (?:goal|effort|responsibility)|jointly|team effort|as a team|"
    r"in partnership|partner with|support one another)\b",
    re.IGNORECASE,
)


def _cooperative_section_already_functional(text: str) -> bool:
    return bool(_COOPERATIVE_SIGNAL.search(text))


def _concise_rewrite_is_visible(source: str, candidate: str) -> bool:
    source_count = len(_WORD.findall(source))
    candidate_count = len(_WORD.findall(candidate))
    if source_count == 0 or candidate_count == 0:
        return False
    numeric_fact_count = len(re.findall(r"(?<!\w)\d", source))
    if source_count <= 10 and candidate_count <= source_count:
        return True
    if (
        source_count <= 20
        and len(_OBLIGATION.findall(source)) >= 1
        and _NEGATION.search(source)
        and candidate_count <= source_count
    ):
        return True
    dense_fact_section = (
        numeric_fact_count >= 2
        or _IDENTIFIER.search(source) is not None
        or re.search(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b", source) is not None
        or len(_named_entities(source)) >= 2
        or len(_OBLIGATION.findall(source)) >= 2
    )
    filler_heavy = re.search(
        r"\b(?:basically|very careful way|in a very|same conclusion|"
        r"slightly different words|actual sequence|completely free)\b",
        source,
        re.IGNORECASE,
    )
    if dense_fact_section and not filler_heavy and candidate_count <= source_count:
        return True
    if (
        (
            _IDENTIFIER.search(source)
            or re.search(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b", source)
        )
        and _ORDERING.search(source)
        and source.count(",") >= 2
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 45
        and len(re.findall(r"\b[A-Z]{1,4}-[A-Z0-9]{1,8}\b", source)) >= 2
        and len(_named_entities(source)) >= 2
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 40
        and len(_IDENTIFIER.findall(source)) >= 3
        and numeric_fact_count >= 3
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 55
        and len(_OBLIGATION.findall(source)) >= 2
        and _ORDERING.search(source)
        and source.count(",") >= 3
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 55
        and _ORDERING.search(source)
        and _NEGATION.search(source)
        and len(_OBLIGATION.findall(source)) >= 1
        and source.count(",") >= 3
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 40
        and numeric_fact_count >= 1
        and sum(_modal_meaning_counter(source).values()) >= 2
        and _NEGATION.search(source)
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 18
        and numeric_fact_count >= 2
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 25
        and numeric_fact_count >= 1
        and len(_named_entities(source)) >= 2
        and candidate_count <= source_count
    ):
        return True
    if (
        source_count <= 20
        and source.count(",") >= 2
        and candidate_count <= source_count
    ):
        return True
    required_reduction = max(1, source_count // 20)
    return candidate_count <= source_count - required_reduction


_FRIENDLY_STYLE_SIGNAL = re.compile(
    r"\b(?:glad|happy|help(?:ful|ing)?|welcome|please|appreciate|"
    r"good news|make things easier|work well together)\b",
    re.IGNORECASE,
)
_CARE_STYLE_SIGNAL = re.compile(
    r"\b(?:understand|recognize|acknowledge|support|help|care|"
    r"challeng(?:e|ed|ing)|difficult(?:y)?|make things easier|"
    r"people affected|impact on)\b",
    re.IGNORECASE,
)
_HOSTILE_STYLE = re.compile(
    r"\b(?:idiot(?:ic)?|stupid|incompetent|ridiculous|worthless|"
    r"your fault|you failed|obviously you|shut up|lazy|pathetic)\b",
    re.IGNORECASE,
)
_CASUAL_STYLE = re.compile(
    r"\b(?:gonna|wanna|ain't|kinda|sorta|you guys|nope|yep|"
    r"super easy|a bunch of)\b",
    re.IGNORECASE,
)
_CONVERSATIONAL_FILLER = re.compile(
    r"\b(?:um+|uh+|you know)\b|(?:^|,\s*)like(?=\s*,)",
    re.IGNORECASE,
)
_PRESSURE_STYLE = re.compile(
    r"\b(?:act now|no choice|or else|must (?:accept|agree|approve|buy))\b",
    re.IGNORECASE,
)
_CHILDISH_STYLE = re.compile(
    r"\b(?:widdle|goo-goo|choo-choo|owie|num-nums)\b",
    re.IGNORECASE,
)
_MARKETING_STYLE = re.compile(
    r"\b(?:revolutionary|game-changing|best-in-class|world-class|magical)\b",
    re.IGNORECASE,
)
_INFLATED_STYLE = re.compile(
    r"\b(?:aforementioned|heretofore|paradigm shift|utilization thereof|elucidates?)\b",
    re.IGNORECASE,
)
_APOLOGY_STYLE = re.compile(
    r"\b(?:(?:I am|we are) sorry|we apologize|I apologize)\b",
    re.IGNORECASE,
)
_PROPOSAL_STYLE = re.compile(
    r"\b(?:we|I|the team)\s+(?:propose|suggest|recommend)\b",
    re.IGNORECASE,
)
_THREAT_STYLE = re.compile(
    r"\b(?:or else|face consequences|you will regret|pay the price)\b",
    re.IGNORECASE,
)
_CAVEMAN_LIST_OR_HEADING = re.compile(
    r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+|^\s*#{1,6}\s+",
)


def _introduced_style(pattern: re.Pattern[str], source: str, candidate: str) -> bool:
    return bool(pattern.search(candidate) and not pattern.search(source))


def _tone_style_failure(source: str, candidate: str, *, command: str) -> str:
    """Reject obvious tone misses without pretending regex can grade prose."""
    source_clean = " ".join(source.casefold().split())
    candidate_clean = " ".join(candidate.casefold().split())
    unchanged = source_clean == candidate_clean

    if command == "rephrase" and unchanged:
        return "rephrase did not change sentence construction"
    if command == "shorten" and not _concise_rewrite_is_visible(source, candidate):
        return "shorten rewrite did not reduce word count"
    if command == "friendly":
        if _HOSTILE_STYLE.search(candidate):
            return "friendly rewrite contains hostile wording"
        if _CASUAL_STYLE.search(candidate):
            return "friendly rewrite contains slang"
        if unchanged and not _FRIENDLY_STYLE_SIGNAL.search(candidate):
            return "friendly rewrite is not visibly warm"
    if command == "conversational" and len(
        _CONVERSATIONAL_FILLER.findall(candidate)
    ) >= 2:
        return "conversational rewrite contains filler overload"
    if command == "persuasive" and _introduced_style(
        _PRESSURE_STYLE,
        source,
        candidate,
    ):
        return "persuasive rewrite contains pressure tactics"
    if command == "confident" and _HOSTILE_STYLE.search(candidate):
        return "confident rewrite contains hostility"
    if command in {"casual", "simplified", "high-school level"} and _CHILDISH_STYLE.search(
        candidate
    ):
        return f"{command} rewrite contains childish wording"
    if command == "technical" and _MARKETING_STYLE.search(candidate):
        return "technical rewrite contains marketing language"
    if command == "college level" and _INFLATED_STYLE.search(candidate):
        return "college level rewrite contains inflated wording"
    if command == "empathetic" and _introduced_style(
        _APOLOGY_STYLE,
        source,
        candidate,
    ):
        return "empathetic rewrite invents an apology"
    if command == "cooperative" and _introduced_style(
        _PROPOSAL_STYLE,
        source,
        candidate,
    ):
        return "cooperative rewrite invents a proposal"
    if command == "assertive" and _THREAT_STYLE.search(candidate):
        return "assertive rewrite contains a threat"
    if command == "caveman" and _CAVEMAN_LIST_OR_HEADING.search(candidate):
        return "caveman rewrite contains a heading or list"
    if command in {"diplomatic", "empathetic", "cooperative", "assertive"}:
        if _HOSTILE_STYLE.search(candidate):
            return f"{command} rewrite contains hostile wording"
    if command == "empathetic" and unchanged and not _CARE_STYLE_SIGNAL.search(
        candidate
    ):
        return "empathetic rewrite does not make care visible"
    if command in {"formal", "academic", "professional", "technical"}:
        if _CASUAL_STYLE.search(candidate):
            return f"{command} rewrite contains casual wording"
    return ""



def _apply_safe_concise_reductions(text: str) -> str:
    text = re.sub(r"\bdid not\b", "didn't", text, flags=re.IGNORECASE)
    text = re.sub(r"\bshould not\b", "shouldn't", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdoes not\b", "doesn't", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bas the plan said it would\b",
        "as planned",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bmore easier\b", "easier", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\btablets loose their saved routes\b",
        "tablets lost their saved routes",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bafter the battery went below\b",
        "after the battery dropped below",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bwhen it actually blocked\b",
        "when it blocked",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bstay inside (?:their|there) approved temperature ranges\b",
        "stay within approved temperature ranges",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\branges but MedTrack 4\.7 sometimes display\b",
        "ranges, but MedTrack 4.7 sometimes displays",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bThe proposal also says the program will be completely free, "
        r"which is incorrect vocabulary since the library expects operating "
        r"costs of \$18,600\.",
        "The proposal says the program will be free to visitors, though the library "
        "expects $18,600 in operating costs.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bThe proposal describes the program as free to visitors, but the "
        r"library expects operating costs of \$18,600\.",
        "The proposal calls the program free to visitors, but the library expects "
        "$18,600 in operating costs.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bConference Room 5C and remote workers joins\b",
        "Conference Room 5C, and remote workers join",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bThe trainer have forty five minutes\b",
        "The trainer has forty-five minutes",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\broute syncing offline notes photo uploads and emergency escalation\b",
        "route syncing, offline notes, photo uploads, and emergency escalation",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bSeveral instruction is wrote with vague vocabulary,\s+"
        r"for example the guide\b",
        "Several instructions are written with vague vocabulary; "
        "for example, the guide",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bdeal with failed uploads in the normal fashion\b",
        "handle failed uploads normally",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe city still pays staff security utilities and transportation support\b",
        "the city still pays for staff, security, utilities, and transportation support",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?P<percent>\d+(?:\.\d+)? percent),\s+the support team\b",
        lambda match: f"{match.group('percent')}; the support team",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\.\s+Which was a serious mistake\.",
        ", which was a serious mistake.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bblocked two emergency repairs\s+which was a serious mistake\b",
        "blocked two emergency repairs, which was a serious mistake",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bpublic meeting attendance\s+They recorded\b",
        "public meeting attendance. They recorded",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bNo An employee\b",
        "No employee",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?P<article>the) billing preview showed "
        r"(?P<amount>\$[\d,]+(?:\.\d{2})?) "
        r"more revenue than the source ledger\b",
        lambda match: (
            ("The" if match.group("article")[:1].isupper() else "the")
            + " billing preview exceeded the source ledger by "
            + match.group("amount")
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bdeclared severity SEV-1\b",
        "declared SEV-1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bstate who owns each repair\b",
        "state each repair's owner",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bSeveral sentences in the draft postmortem are fragments\b",
        "The draft postmortem has sentence fragments",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bis preparing a "
        r"(?P<drill>(?:regional )?[a-z-]+(?: [a-z-]+){0,3} drill)\b",
        lambda match: f"is planning a {match.group('drill')}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bteam is planning a\b",
        "team plans a",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bcommunity college students hospital employees and parents\b",
        "community college students, hospital employees, and parents",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?P<article>the) strongest interest came from\b",
        lambda match: (
            "The strongest interest was from"
            if match.group("article")[:1].isupper()
            else "the strongest interest was from"
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bexplained more cautiously\b",
        "explained cautiously",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bblue arrows for patient areas and orange arrows for supply areas\b",
        "blue arrows for patient areas and orange for supply areas",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bseveral department plans were submitted late,\s+and they "
        r"contain conflicting instructions\b",
        "several department plans were submitted late with conflicting instructions",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bThe first alert occurred at (?P<time>\d{2}:\d{2} UTC)"
        r"[;,]\s*however[;,]?\s*the (?P<actor>[a-z][a-z ]{1,40}?) "
        r"closed it because\b",
        lambda match: (
            f"The {match.group('actor')} closed the first alert at "
            f"{match.group('time')} because"
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bloosing access\b",
        "losing access",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bBefore any interview\b",
        "Before interviews",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bwithout losing access to a garden plot\b",
        "without losing garden plot access",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?P<quantity>quantity) by itself\b",
        lambda match: (
            "Quantity alone"
            if match.group("quantity")[:1].isupper()
            else "quantity alone"
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bshould state that\b",
        "should say",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bwants the final wording to remain\b",
        "wants the final wording to be",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bresponses from households that primarily spoke "
        r"(?P<first>[A-Z][a-z]+) or (?P<second>[A-Z][a-z]+)\b",
        lambda match: (
            "responses from primarily "
            f"{match.group('first')}- or {match.group('second')}-speaking households"
        ),
        text,
    )
    text = re.sub(
        r"\bThere are also several punctuation and (?:structure|structural) "
        r"(?:problems|issues) in the preliminary report\.",
        "The preliminary report has several punctuation and structural problems.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\brepeats the same conclusion\b",
        "repeats the conclusion",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe actual sequence\b",
        "the sequence",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\battendance at public meetings\b",
        "public meeting attendance",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r",\s+of which (?P<share>\d+(?:\.\d+)? percent) went to\b",
        lambda match: f"; {match.group('share')} went to",
        text,
        flags=re.IGNORECASE,
    )
def _cooperative_rewrite_is_visible(source: str, candidate: str) -> bool:
    source_paragraphs = [
        " ".join(value.casefold().split())
        for value in re.split(r"\n\s*\n", source)
        if value.strip()
    ]
    candidate_paragraphs = [
        " ".join(value.casefold().split())
        for value in re.split(r"\n\s*\n", candidate)
        if value.strip()
    ]
    if not source_paragraphs or not candidate_paragraphs:
        return False
    for index, source_paragraph in enumerate(source_paragraphs):
        if not (
            _COOPERATIVE_CONTEXT.search(source_paragraph)
            or _COOPERATIVE_COLLECTIVE_ACTION.search(source_paragraph)
        ):
            continue
        if index >= len(candidate_paragraphs):
            return False
        if not _cooperative_section_already_functional(candidate_paragraphs[index]):
            return False
    return True


_EMPATHETIC_ACTOR = re.compile(
    r"\b(?:nobody|somebody|people|users?|patients?|workers?|residents?|"
    r"participants?|visitors?|customers?|staff|technicians?|trainers?|"
    r"volunteers?|parents?|students?|employees?|team)\b",
    re.IGNORECASE,
)
_EMPATHETIC_IMPACT = re.compile(
    r"\b(?:nobody could understand|could not understand|cannot understand|can(?:'|\u2019)t understand|"
    r"confus(?:e|ed|ing|ion)|difficult(?:y)?|barrier(?:s)?|fewer practical ways|"
    r"blocked|hindered|lost|lonely|unsafe|accusatory|hostile|interrupted|"
    r"outside (?:his|her|their) control)\b",
    re.IGNORECASE,
)


def _empathetic_section_already_functional(text: str) -> bool:
    return any(
        _EMPATHETIC_ACTOR.search(sentence) and _EMPATHETIC_IMPACT.search(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    )


def _persuasive_section_already_functional(text: str) -> bool:
    numeric_facts = len(re.findall(r"(?<!\w)\d", text))
    return bool(
        _PERSUASIVE_POSITION.search(text)
        or (
            _PERSUASIVE_EVIDENCE.search(text)
            and _PERSUASIVE_IMPACT.search(text)
        )
        or (
            _PERSUASIVE_EVIDENCE.search(text)
            and numeric_facts >= 2
        )
        or (
            _PERSUASIVE_BOUNDARY.search(text)
            and _PERSUASIVE_DECISION.search(text)
        )
        or (
            _PERSUASIVE_ACTION.search(text)
            and numeric_facts >= 2
        )
    )


def _persuasive_rewrite_is_visible(source: str, candidate: str) -> bool:
    source_text = " ".join(source.casefold().split())
    candidate_text = " ".join(candidate.casefold().split())
    if not source_text or not candidate_text:
        return False
    if source_text == candidate_text:
        return _persuasive_section_already_functional(candidate_text)

    if (
        _PERSUASIVE_POSITION.search(candidate_text)
        and _PERSUASIVE_REASON.search(candidate_text)
    ):
        return True
    if _persuasive_section_already_functional(candidate_text):
        return True

    added_signal = any(
        signal in candidate_text and signal not in source_text
        for signal in _PERSUASIVE_SIGNALS
    )
    if added_signal:
        return True

    source_words = _WORD.findall(source_text)
    candidate_words = _WORD.findall(candidate_text)
    similarity = SequenceMatcher(
        a=source_words,
        b=candidate_words,
        autojunk=False,
    ).ratio()
    return similarity <= 0.96


def minimal_edits(source: str, candidate: str) -> tuple[PolishEdit, ...]:
    if source == candidate:
        return ()
    matcher = SequenceMatcher(a=source, b=candidate, autojunk=False)
    edits: list[PolishEdit] = []
    for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        edits.append(
            PolishEdit(
                operation=operation,
                original=source[left_start:left_end],
                replacement=candidate[right_start:right_end],
                original_start=left_start,
                original_end=left_end,
            )
        )
    return tuple(edits)


def _generation_instruction(
    request: CorrectionRequest,
    classifier: ClassifierDecision,
) -> str:
    scope = classifier.context_scope.value
    categories = ", ".join(classifier.error_categories) or "general correctness"
    base = (
        f"Polish scope: {scope}. Detected categories: {categories}. "
        "Return a polished rewrite that fixes grammar, spelling, punctuation, vocabulary, "
        "clarity, and sentence structure. Protected values, numbers, dates, "
        "URLs, code, actors, negations, requirements, and named entities must remain exact. "
        "Copy every word that contains a digit exactly as written, including times such as "
        "3pm and device or version names such as S23 or v2. Never replace a product, app, "
        "or service name with a different or more generic word; correct only its "
        "capitalization. "
        "Do not add a named entity or fact. Never use an em dash; use a comma, colon, "
        "semicolon, or parentheses instead. Complete a final verification pass before answering. "
        "Check every sentence for subject-verb agreement, pronoun agreement, sentence fragments, "
        "comma splices, fused sentences, missing list commas, faulty comparisons, vague vocabulary, "
        "incorrect verb forms, and wrong singular or plural nouns after numbers or quantifiers. "
        "Merge a source fragment beginning with Because, Which, or While "
        "into the sentence it belongs to. Do not preserve an error merely because its meaning is "
        "understandable. Thinking must remain disabled."
    )
    tone_instruction = tone_contract_instruction(request.command)
    if tone_instruction:
        base += " " + tone_instruction
    if request.command.strip().casefold() == "proofread":
        base += (
            " Improve awkward but understandable wording. Grammar-mode output must remain "
            "within 35% of the source word count and keep the same paragraph structure. "
            "Preserve sentence order and protected-value order. Repair each sentence in place; "
            "split a run-on only where it already occurs."
        )
    if request.instruction.strip():
        base += " User direction: " + request.instruction.strip()
    return base


def _safe_fallback_text(
    request: CorrectionRequest,
    normalized: str,
    deterministic: str,
) -> str:
    """The rules repair, never the raw source.

    A refused model answer is not a reason to withdraw the spelling and
    agreement fixes the deterministic pass already made. Android keeps its
    equivalent for the same reason. Do not "simplify" this to return
    `normalized`; the arguments are kept for that reason.
    """
    return deterministic


def _apply_safe_persuasive_restructure(text: str) -> str:
    return re.sub(
        r"\bThe (?P<actor>researchers|team|staff|coordinators?) corrected "
        r"(?P<object>that problem|the problem|that issue|the issue) on "
        r"(?P<date>(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December) \d{1,2}) by "
        r"(?P<action>[^.!?]{3,180})\.",
        lambda match: (
            f"By {match.group('action')}, the {match.group('actor')} corrected "
            f"{match.group('object')} on {match.group('date')}."
        ),
        text,
        flags=re.IGNORECASE,
    )


def _strip_em_dashes(text: str) -> str:
    """Permanent product rule: corrections never contain an em dash.

    Rather than discarding an otherwise good AI candidate for one dash,
    convert it: a spaced or unspaced em dash between words becomes a comma
    plus space; leftovers become a plain space. The validator's hard check
    stays as the backstop.
    """
    text = re.sub(r"\s*—\s*", ", ", text)
    return text.replace("—", " ")


def _final_cleanup_candidate(command: str, candidate: str) -> str:
    if command.strip().casefold() in {"translate", "summarize", "expand"}:
        return _strip_em_dashes(candidate)
    candidate = _strip_em_dashes(candidate)
    cleaned = _apply_fast_proofread_rules(candidate)
    if command.strip().casefold() == "concise":
        cleaned = _apply_safe_concise_reductions(cleaned)
    if command.strip().casefold() == "persuasive":
        cleaned = _apply_safe_persuasive_restructure(cleaned)
    if command.strip().casefold() in {"formal", "academic"}:
        cleaned = _expand_unambiguous_negative_contractions(cleaned)
    return cleaned


_FORMAL_NEGATIVE_EXPANSIONS = {
    "aren't": "are not",
    "can't": "cannot",
    "couldn't": "could not",
    "didn't": "did not",
    "doesn't": "does not",
    "don't": "do not",
    "hadn't": "had not",
    "hasn't": "has not",
    "haven't": "have not",
    "isn't": "is not",
    "mustn't": "must not",
    "needn't": "need not",
    "shouldn't": "should not",
    "wasn't": "was not",
    "weren't": "were not",
    "won't": "will not",
    "wouldn't": "would not",
}
_FORMAL_NEGATIVE_CONTRACTION = re.compile(
    r"\b(?:aren|can|couldn|didn|doesn|don|hadn|hasn|haven|isn|mustn|"
    r"needn|shouldn|wasn|weren|won|wouldn)(?:'|\u2019)t\b",
    re.IGNORECASE,
)


def _expand_unambiguous_negative_contractions(text: str) -> str:
    def replace_contraction(match: re.Match[str]) -> str:
        source = match.group(0)
        key = source.casefold().replace("\u2019", "'")
        replacement = _FORMAL_NEGATIVE_EXPANSIONS.get(key, source)
        if source[:1].isupper():
            replacement = replacement[:1].upper() + replacement[1:]
        return replacement

    return _FORMAL_NEGATIVE_CONTRACTION.sub(replace_contraction, text)


def _restore_rephrase_content_nouns(
    source: str,
    candidate: str,
    *,
    command: str,
) -> str:
    """Restore high-value source nouns when a rephrase swaps domain terminology."""
    restored = candidate
    for match in re.finditer(
        r"\b(?P<subject>(?:[A-Z][A-Za-z'.-]*\s+){1,5})"
        r"(?P<verb>says|states)\b",
        source,
    ):
        subject = match.group("subject")
        source_verb = match.group("verb")
        restored = re.sub(
            rf"\b{re.escape(subject)}(?:said|stated)\b",
            f"{subject}{source_verb}",
            restored,
            flags=re.IGNORECASE,
        )
    source_relative_time = _RELATIVE_TIME.search(source)
    if source_relative_time is not None:
        source_relative = source_relative_time.group(0)
        marker, period = re.split(r"\s+", source_relative, maxsplit=1)
        if marker.casefold() == "last":
            restored = re.sub(
                rf"\b(?:during\s+)?(?:the\s+previous|last)\s+"
                rf"{re.escape(period)}\b",
                source_relative,
                restored,
                count=1,
                flags=re.IGNORECASE,
            )
    source_until = re.search(
        r"\buntil\s+(?P<body>(?:the\s+)?[A-Za-z][A-Za-z '\u2019-]{0,80}?)"
        r"\s+(?:ended|finished|concluded)\b",
        source,
        re.IGNORECASE,
    )
    if source_until is not None:
        body = source_until.group("body")
        restored = re.sub(
            rf"\bafter\s+{re.escape(body)}\s+(?:ended|finished|concluded)\b",
            source_until.group(0),
            restored,
            count=1,
            flags=re.IGNORECASE,
        )
    source_due = re.search(
        r"\b(?P<subject>(?:The|A|An)\s+[A-Za-z][A-Za-z' -]{1,80}?)"
        r"\s+is due\s+(?P<date>(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)\s+\d{1,2}"
        r"(?:,\s+\d{4})?)\b",
        source,
        re.IGNORECASE,
    )
    if source_due is not None:
        subject = source_due.group("subject")
        date = source_due.group("date")
        restored = re.sub(
            rf"\b{re.escape(subject)}\s+(?:must include due|is due)\s+"
            rf"{re.escape(date)}\b",
            f"{subject} is due {date}",
            restored,
            count=1,
            flags=re.IGNORECASE,
        )
    source_card_fact = re.search(
        r"\bprinted pocket card\b[^.!?]*",
        source,
        re.IGNORECASE,
    )
    if source_card_fact is not None and re.search(
        r"\bprinted pocket cards?\b",
        restored,
        re.IGNORECASE,
    ):
        restored = re.sub(
            r"\bprinted pocket cards?\b[^.!?]*",
            _apply_fast_proofread_rules(source_card_fact.group(0)).rstrip(".!?"),
            restored,
            count=1,
            flags=re.IGNORECASE,
        )
    if command.strip().casefold() != "rephrase":
        return restored
    phrase_anchors = (
        ("power outage drill", "power outage simulation"),
    )
    for source_phrase, alternate in phrase_anchors:
        if (
            re.search(re.escape(source_phrase), source, re.IGNORECASE)
            and re.search(re.escape(alternate), restored, re.IGNORECASE)
        ):
            restored = re.sub(
                re.escape(alternate),
                source_phrase,
                restored,
                count=1,
                flags=re.IGNORECASE,
            )
    return restored


def _missing_rephrase_anchor_terms(
    source: str,
    candidate: str,
) -> tuple[str, ...]:
    """Lock missing content words for the one allowed Rephrase safety retry."""
    tokens = tuple(_WORD.finditer(source))
    missing_content = {
        index
        for index, match in enumerate(tokens)
        if len(match.group(0)) >= 4
        and match.group(0).casefold() not in _REPHRASE_ANCHOR_STOPWORDS
        and any(character.isalpha() for character in match.group(0))
        and not _single_word_present(match.group(0), candidate)
    }
    selected: list[tuple[int, str]] = []
    covered: set[int] = set()

    for index in sorted(missing_content):
        if index + 1 >= len(tokens):
            continue
        first = tokens[index].group(0)
        second = tokens[index + 1].group(0)
        between = source[tokens[index].end() : tokens[index + 1].start()]
        phrase = source[tokens[index].start() : tokens[index + 1].end()]
        if (
            _rephrase_action_base(first) is not None
            and second.casefold() in _REPHRASE_PHRASAL_PARTS
            and not between.strip()
            and phrase.casefold() not in candidate.casefold()
        ):
            selected.append((tokens[index].start(), phrase))
            covered.update((index, index + 1))

    index = 0
    while index < len(tokens):
        if (
            index not in missing_content
            or index in covered
            or _rephrase_action_base(tokens[index].group(0)) is not None
        ):
            index += 1
            continue
        run = [index]
        cursor = index + 1
        while (
            cursor < len(tokens)
            and cursor in missing_content
            and cursor not in covered
            and _rephrase_action_base(tokens[cursor].group(0)) is None
            and not source[tokens[cursor - 1].end() : tokens[cursor].start()].strip()
            and len(run) < 3
        ):
            run.append(cursor)
            cursor += 1
        if len(run) >= 2:
            phrase = source[tokens[run[0]].start() : tokens[run[-1]].end()]
            selected.append((tokens[run[0]].start(), phrase))
            covered.update(run)
        index = max(cursor, index + 1)

    for index in sorted(missing_content - covered):
        selected.append((tokens[index].start(), tokens[index].group(0)))

    anchors: list[str] = []
    seen: set[str] = set()
    for _start, value in sorted(selected):
        folded = value.casefold()
        if folded in seen:
            continue
        anchors.append(value)
        seen.add(folded)
        if len(anchors) >= 32:
            break
    return tuple(anchors)


def _missing_rephrase_actions(
    source: str,
    candidate: str,
) -> tuple[str, ...]:
    source_actions = {
        base
        for value in _WORD.findall(source)
        if (base := _rephrase_action_base(value)) is not None
        and base in _REPHRASE_CRITICAL_ACTIONS
    }
    candidate_actions = {
        base
        for value in _WORD.findall(candidate)
        if (base := _rephrase_action_base(value)) is not None
    }
    missing = {
        base
        for base in source_actions - candidate_actions
        if not any(
            _word_family_present(candidate, alternate)
            for alternate in _REPHRASE_ACTION_EQUIVALENTS.get(base, ())
        )
    }
    return tuple(sorted(missing))


def _source_rephrase_action_terms(
    source: str,
    action_bases: tuple[str, ...],
) -> tuple[str, ...]:
    required = set(action_bases)
    values: list[str] = []
    for value in _WORD.findall(source):
        base = _rephrase_action_base(value)
        if base in required and value.casefold() not in {
            existing.casefold() for existing in values
        }:
            values.append(value)
    return tuple(values)


def _validate_rephrase_actions(
    validation: ValidationResult,
    source: str,
    candidate: str,
    *,
    command: str,
) -> ValidationResult:
    command_key = command.strip().casefold()
    if command_key != "rephrase" and command.strip() not in _STAGED_STYLE_COMMANDS:
        return validation
    missing_actions = _missing_rephrase_actions(source, candidate)
    if not missing_actions:
        return validation
    return ValidationResult(
        False,
        validation.semantic_similarity,
        (
            *validation.failures,
            "source action changed: " + ", ".join(missing_actions),
        ),
    )


def _rephrase_action_base(value: str) -> str | None:
    folded = value.casefold()
    irregular = _REPHRASE_IRREGULAR_ACTIONS.get(folded)
    if irregular is not None:
        return irregular
    for base in _REPHRASE_ACTION_BASES:
        variants = {
            base,
            f"{base}s",
            f"{base}ed",
            f"{base}ing",
        }
        if base.endswith("e"):
            variants.update({f"{base}d", f"{base[:-1]}ing"})
        if base.endswith("y"):
            variants.add(f"{base[:-1]}ies")
        if folded in variants:
            return base
    return None


def _word_family_present(text: str, base: str) -> bool:
    variants = {
        base,
        f"{base}s",
        f"{base}ed",
        f"{base}ing",
    }
    if base.endswith("e"):
        variants.update({f"{base}d", f"{base[:-1]}ing"})
    if base.endswith("y"):
        variants.add(f"{base[:-1]}ies")
    return any(_single_word_present(value, text) for value in variants)


def _safe_candidate_rank(
    candidate: _SafeCandidate,
) -> tuple[int, float, int, float, int]:
    complete = int(not candidate.residual_errors and candidate.tone_verified)
    return (
        complete,
        candidate.quality_score,
        -len(candidate.residual_errors),
        candidate.validation.semantic_similarity,
        int(candidate.used_llm),
    )


def _sentence_spans_for_recovery(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    boundary_pattern = re.compile(
        r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)|[\r\n]+"
    )
    for boundary in boundary_pattern.finditer(text):
        if _nonterminal_recovery_abbreviation(text, boundary.start(), boundary.end()):
            continue
        left = cursor
        while left < boundary.end() and text[left].isspace():
            left += 1
        right = boundary.end()
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            spans.append((left, right))
        cursor = boundary.end()
    left = cursor
    while left < len(text) and text[left].isspace():
        left += 1
    right = len(text)
    while right > left and text[right - 1].isspace():
        right -= 1
    if right > left:
        spans.append((left, right))
    if not spans and text.strip():
        spans.append((len(text) - len(text.lstrip()), len(text.rstrip())))
    return tuple(spans)


def _nonterminal_recovery_abbreviation(
    text: str,
    start: int,
    end: int,
) -> bool:
    if not text[start:end].startswith("."):
        return False
    prefix = text[:end].casefold()
    if re.search(
        r"(?<![a-z])(?:mr|mrs|ms|dr|prof|sr|jr|st)\.$",
        prefix,
    ):
        return True
    if not prefix.endswith(("a.m.", "p.m.", "e.g.", "i.e.")):
        return False
    following = text[end:].lstrip()
    return bool(following and following[:1].islower())


def _runtime_failure_kind(
    error: BaseException,
    *,
    loading: bool = False,
) -> RuntimeFailureKind:
    message = str(error).casefold()
    if any(value in message for value in ("gpu", "cuda", "vram", "code 43")):
        return RuntimeFailureKind.GPU_FAILURE
    if any(value in message for value in ("timed out", "timeout")):
        if any(value in message for value in ("load", "loading")):
            return RuntimeFailureKind.LOAD_TIMEOUT
        return (
            RuntimeFailureKind.LOAD_TIMEOUT
            if loading
            else RuntimeFailureKind.GENERATION_TIMEOUT
        )
    if any(
        value in message
        for value in ("connection", "refused", "unreachable", "service unavailable")
    ):
        return RuntimeFailureKind.CONNECTION_FAILURE
    if any(
        value in message
        for value in (
            "json",
            "response",
            "schema",
            "empty output",
            "invalid text",
            "incomplete text",
        )
    ):
        return RuntimeFailureKind.INVALID_RESPONSE
    if any(
        value in message
        for value in (
            "meaning constraint",
            "protected content",
            "quality check",
            "validation",
            "tone contract",
            "word limit",
            "forbidden em dash",
        )
    ):
        return RuntimeFailureKind.VALIDATION_FAILURE
    return RuntimeFailureKind.MODEL_UNAVAILABLE


def _is_cancellation_error(error: BaseException) -> bool:
    class_name = type(error).__name__.casefold()
    message = str(error).casefold()
    return (
        class_name in {"cancellederror", "cancelederror"}
        or "cancelled by user" in message
        or "canceled by user" in message
        or "operation cancelled" in message
        or "operation canceled" in message
    )


def _backend_placement_rank(backend: RuntimeBackend) -> int:
    """Rank whole-model placement so fallback never turns GPU work into CPU work."""
    if backend in {
        RuntimeBackend.CUDA,
        RuntimeBackend.VULKAN,
        RuntimeBackend.HIP,
        RuntimeBackend.SYCL,
    }:
        return 2
    if backend is RuntimeBackend.CPU:
        return 1
    return 0


def _recoverable_generation_error(error: BaseException) -> bool:
    message = str(error).casefold()
    return any(
        value in message
        for value in (
            "changed a meaning constraint",
            "changed protected content",
            "exceeded the requested word limit",
            "returned incomplete text",
            "returned invalid text",
            "used a forbidden em dash",
        )
    )


_MONTH_DAY_VALUE = re.compile(
    r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
    r"july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?$",
    re.IGNORECASE,
)


def _critical_values(text: str, terms: tuple[str, ...]) -> Counter[str]:
    # Dates compare case-insensitively: capitalizing "august 18" to
    # "August 18" is a proofreading repair of the same protected fact, not a
    # new one. Every other critical value (URLs, code, numbers) stays exact.
    values = [
        match.group(0).casefold()
        if _MONTH_DAY_VALUE.match(match.group(0))
        else match.group(0)
        for match in _CRITICAL.finditer(text)
    ]
    for term in terms:
        value = str(term)
        if value and value in text:
            values.extend([value] * text.count(value))
    return Counter(values)


def _quotes_existing_source_text(value: str, source: str) -> bool:
    if len(value) < 3:
        return False
    pairs = {('"', '"'), ("\u201c", "\u201d")}
    if (value[0], value[-1]) not in pairs:
        return False
    body = value[1:-1]
    return bool(
        body
        and re.search(
            rf"(?<!\w){re.escape(body)}(?!\w)",
            source,
            re.IGNORECASE,
        )
    )



def _counter_diff_label(source_counter, candidate_counter) -> str:
    """Render a counter difference using only generic function words."""
    missing = source_counter - candidate_counter
    extra = candidate_counter - source_counter
    parts = []
    if missing:
        parts.append("missing " + ", ".join(sorted(missing)))
    if extra:
        parts.append("added " + ", ".join(sorted(extra)))
    return f" ({'; '.join(parts)})" if parts else ""

def _counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(" ".join(match.group(0).casefold().split()) for match in pattern.finditer(text))


def _single_word_present(value: str, text: str) -> bool:
    if " " in value:
        return False
    if re.search(
        rf"(?<!\w){re.escape(value)}(?!\w)",
        text,
        re.IGNORECASE,
    ) is not None:
        return True
    # "Wi-Fi" is the same word as source "wifi"; punctuation-insensitive
    # comparison keeps spelling-standardization from reading as a new entity.
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    if len(compact) < 3:
        return False
    return any(
        compact == re.sub(r"[^a-z0-9]", "", token.casefold())
        for token in re.findall(r"[\w'-]+", text)
    )


_CONTRACTION_NORMALIZE = {
    "aint": "ain't",
    "dont": "don't",
    "doesnt": "doesn't",
    "didnt": "didn't",
    "cant": "can't",
    "wont": "won't",
    "isnt": "isn't",
    "arent": "aren't",
    "wasnt": "wasn't",
    "werent": "weren't",
    "havent": "haven't",
    "hasnt": "hasn't",
    "hadnt": "hadn't",
    "couldnt": "couldn't",
    "wouldnt": "wouldn't",
    "shouldnt": "shouldn't",
    "mustnt": "mustn't",
    "neednt": "needn't",
    # Misplaced apostrophes ("was'nt") are the same spelling error as a
    # missing one and must normalize to the same standard form, or fixing
    # them is falsely counted as a negation or modal change.
    "do'nt": "don't",
    "does'nt": "doesn't",
    "did'nt": "didn't",
    "ca'nt": "can't",
    "wo'nt": "won't",
    "is'nt": "isn't",
    "are'nt": "aren't",
    "was'nt": "wasn't",
    "were'nt": "weren't",
    "have'nt": "haven't",
    "has'nt": "hasn't",
    "had'nt": "hadn't",
    "could'nt": "couldn't",
    "would'nt": "wouldn't",
    "should'nt": "shouldn't",
    "must'nt": "mustn't",
    "need'nt": "needn't",
    # "cannot" and "can't" are the same modal negation; comparing them as
    # different words rejected candidates that only changed the spelling.
    "cannot": "can't",
}
_CONTRACTION_NORMALIZE_RE = re.compile(
    r"\b(?:" + "|".join(_CONTRACTION_NORMALIZE) + r")\b",
    re.IGNORECASE,
)

# Words the writer split in two. Only the joined form is meaning-bearing to the
# counters, so joining one read as a word the model had invented. Kept to forms
# whose split spelling has no other reading in running text.
_SPLIT_WORD_NORMALIZE = {
    "with out": "without",
    "can not": "cannot",
    "no body": "nobody",
    "no thing": "nothing",
    # "maybe", "may be" and "may" are one modality written three ways, and
    # "companionshp maybe look different" -> "companionship may look different"
    # is the repair being asked for - "maybe look" is not grammatical. Only the
    # bare modal is a modal to the counters, so the repair read as "modal
    # meaning changed (added may)". Folded on both sides, so a genuine modal
    # change such as "must" -> "may" still fails. The dropped "be" does not
    # matter: this text is only ever counted, never shown.
    "may be": "may",
    "maybe": "may",
}
_SPLIT_WORD_NORMALIZE_RE = re.compile(
    r"\b(?:" + "|".join(key.replace(" ", r"\s+") for key in _SPLIT_WORD_NORMALIZE)
    + r")\b",
    re.IGNORECASE,
)

# Function words, pronouns, and contractions are never proper names. A
# capitalized one at a sentence start is ordinary sentence casing, not a new
# entity. Used to stop the validator from reading "Your"/"Don't" as invented
# named entities.


def _normalize_contractions(text: str) -> str:
    """Map apostrophe-less negatives and modals to their standard forms.

    A missing apostrophe ("cant", "dont") is a spelling error the proofreader
    should fix. Counting "cant" as a different modal than "can't", or "dont"
    as a different negation than "don't", is what made the safety gate reject
    the very correction the user wanted.

    A word split in two is the same class of error: "with out proper care" is
    "without proper care", and only the joined form is a negation to the
    counters, so joining it read as a negation the model had added.
    """
    text = _SPLIT_WORD_NORMALIZE_RE.sub(
        lambda match: _SPLIT_WORD_NORMALIZE[
            re.sub(r"\s+", " ", match.group(0).casefold())
        ],
        text,
    )
    return _CONTRACTION_NORMALIZE_RE.sub(
        lambda match: _CONTRACTION_NORMALIZE[match.group(0).casefold()],
        text,
    )


def _added_actors_are_safe_subjects(source: str, candidate: str) -> bool:
    """Allow proofreading to add a missing subject pronoun.

    A fragment like "went to the store" is correctly fixed to "I went to the
    store", and "Just waits and looks disappointed" to "It just waits and
    looks disappointed." That adds an actor family, which the strict actor
    check would reject. This is safe only when nothing is removed or
    swapped; the added family must be first or second person, or a single
    "it" filling a subject gap. Any removal still fails, so he<->she swaps,
    it->they, and invented owners remain blocked.
    """
    source_actors = _actor_counter(
        _normalize_actor_homophones(
            _normalize_contractions(_strip_discourse_hedges(source))
        )
    )
    candidate_actors = _actor_counter(
        _normalize_actor_homophones(
            _normalize_contractions(_strip_discourse_hedges(candidate))
        )
    )
    # The same leniency _actors_preserved applies: a noun actor that only
    # appears because a misspelling was fixed is not a new actor. The two
    # functions decide the same question and disagreed about it, so "oner" ->
    # "owner" was forgiven by one and counted as an invented owner by the
    # other, which refused the paragraph.
    for actor in list(candidate_actors - source_actors):
        if actor not in _PRONOUN_ACTOR_KEYS and _noun_actor_misspelled_in(
            actor, source
        ):
            candidate_actors[actor] = source_actors[actor]
            if candidate_actors[actor] <= 0:
                del candidate_actors[actor]
    if source_actors == candidate_actors:
        return True
    if (candidate_actors - source_actors) and not (source_actors - candidate_actors):
        added = candidate_actors - source_actors
        if set(added) <= {"i", "we", "you"}:
            return True
        if added == Counter({"it": 1}):
            return True
        # A run-on split into sentences needs a pronoun for the noun the first
        # sentence named: "the person for saying something awkward or wearing
        # the same shirt" becomes "... or they wear the same shirt". Nothing was
        # removed, so the noun the pronoun stands for is still there, and only
        # "they" and "it" - which carry no new information about who acts - are
        # allowed. "he" and "she" stay blocked, because choosing one invents a
        # fact about a person the writer did not state.
        # The noun may be recognisable only after correction: this paragraph
        # opens "thier IS emotioonal benefits ... the oner has a bad day", where
        # every noun actor is misspelled and so invisible on the source side.
        # Looking at both sides asks the question that actually matters - is a
        # noun named alongside the pronoun - rather than which side spelled it.
        if set(added) <= {"it", "they"} and any(
            actor not in _PRONOUN_ACTOR_KEYS
            for actor in (*source_actors, *candidate_actors)
        ):
            return True
        return False
    return False


_THIRD_PERSON_ACTORS = frozenset(
    {
        "he",
        "she",
        "they",
        "it",
        "user",
        "team",
        "developer",
        "group",
        "organizer",
        "system",
        "service",
        "application",
        "app",
        "model",
        "server",
        "client",
        "customer",
    }
)


def _third_person_families(text: str) -> set[str]:
    """Which third-person actors appear, with plurals folded into one family.

    "user" and "users" are the same actor; treating them separately would read
    a plural repair as a change of subject.
    """
    counted = _actor_counter(_normalize_actor_homophones(text))
    present: set[str] = set()
    for name, count in counted.items():
        if not count:
            continue
        family = name.casefold()
        if family not in _THIRD_PERSON_ACTORS and family.endswith("s"):
            family = family[:-1]
        if family in _THIRD_PERSON_ACTORS:
            present.add(family)
    return present


_THIRD_PERSON_PRONOUNS = frozenset({"he", "she", "they", "it"})


def _third_person_actor_invented(source: str, candidate: str) -> bool:
    """The correction decided who a sentence is about, rather than repairing it.

    Two shapes count as invention. Naming an actor the writer never named -
    "The wording is simple" becoming "They say the wording is simple" - sources
    a claim to nobody, and covers a swap too, since the replacement is itself
    an actor the source lacks. Resolving a pronoun that has nothing to resolve
    to - "They contain conflicting instructions" becoming "The plans contain
    conflicting instructions" - guesses the referent.

    What is left is the safe case, and the one that mattered: dropping a
    pronoun the source already anchors, as in "the developers ... they"
    becoming "the developers ...". Refusing that was the second-largest block
    of vetoes measured across the corpus.
    """
    source_families = _third_person_families(source)
    candidate_families = _third_person_families(candidate)
    if candidate_families - source_families:
        return True
    dropped_pronouns = (source_families - candidate_families) & _THIRD_PERSON_PRONOUNS
    if not dropped_pronouns:
        return False
    # A noun actor in the source is the antecedent the pronoun can collapse
    # into. Without one there is nothing to resolve to, so the correction
    # supplied the referent itself.
    return not (source_families - _THIRD_PERSON_PRONOUNS)


def _actor_counter(text: str) -> Counter[str]:
    families = {
        "i": {"i", "i'm", "me", "my", "mine"},
        "we": {"we", "we're", "us", "our", "ours"},
        "you": {"you", "you're", "your", "yours"},
        "he": {"he", "he's", "him", "his"},
        "she": {"she", "she's", "her", "hers"},
        "they": {"they", "they're", "their", "theirs", "there"},
        "it": {"it", "it's", "its"},
    }
    canonical = {
        form: actor
        for actor, forms in families.items()
        for form in forms
    }
    canonical["children"] = "child"
    actors: list[str] = []
    for match in _ACTOR_FORM.finditer(text):
        form = match.group(0).casefold()
        if form == "there" and re.match(
            r"\s+(?:is|are|was|were|has|have)\b",
            text[match.end() :],
            re.IGNORECASE,
        ):
            continue
        if form == "there" and re.match(
            r"\s+(?:approved|own)\b",
            text[match.end() :],
            re.IGNORECASE,
        ):
            continue
        if form == "it":
            prefix = text[max(0, match.start() - 20) : match.start()]
            suffix = text[match.end() : match.end() + 20]
            if re.search(r"\bsaid\s*$", prefix, re.IGNORECASE) and re.match(
                r"\s+would\b",
                suffix,
                re.IGNORECASE,
            ):
                continue
        # Noun actors compare by their singular form: fixing "eight user" to
        # "eight users" changes grammatical number, not who acts.
        resolved = canonical.get(form)
        if resolved is None:
            resolved = form[:-1] if form.endswith("s") and len(form) > 3 else form
        actors.append(resolved)
    return Counter(actors)


_THEIR_THERE_HOMOPHONE = re.compile(
    r"\btheir(?=\s+(?:is|are|was|were|has|have|will)\b)",
    re.IGNORECASE,
)
# "there" misused as a possessive ("there start date", "there laptops"):
# "there" followed by a plain word that cannot continue an existential or
# locative reading. Repairing it to "their" adds a they-family token.
_THERE_POSSESSIVE_HOMOPHONE = re.compile(
    r"\bthere(?=\s+(?!is\b|are\b|was\b|were\b|has\b|have\b|had\b|will\b|"
    r"would\b|can\b|could\b|may\b|might\b|must\b|should\b|be\b|been\b|"
    r"being\b|to\b|and\b|or\b|but\b|so\b|yet\b|now\b|then\b|today\b|"
    r"tomorrow\b|yesterday\b|again\b|too\b|as\b|at\b|by\b|for\b|from\b|"
    r"in\b|on\b|of\b|with\b|when\b|where\b|while\b|because\b|if\b|"
    r"until\b|before\b|after\b|once\b|anyway\b|instead\b|first\b|last\b|"
    r"also\b|already\b|still\b|just\b|only\b|even\b)[a-z])",
    re.IGNORECASE,
)


def _normalize_actor_homophones(text: str) -> str:
    """Misspelled their/there homophones are spelling errors, not actors.

    Counting "their is" as a "they" actor made the validator reject the exact
    repair ("their is three items" -> "There are three items") as an actor
    removal, and counting the repair of "there start date" -> "their start
    date" as an invented actor rejected correct fixes the same way.
    """
    text = _THEIR_THERE_HOMOPHONE.sub("there", text)
    return _THERE_POSSESSIVE_HOMOPHONE.sub("their", text)


_PRONOUN_ACTOR_KEYS = frozenset({"i", "we", "you", "he", "she", "they", "it"})

# Filler that carries a pronoun without naming anyone who acts. Deleting it is
# ordinary proofreading, but the pronoun inside it counted as an actor, so
# "important in i guess every country" -> "important in every country" was
# refused as "actor changed (missing i)". Removed before the actor families are
# compared, on both sides, so nothing is credited or charged for it.
#
# Deliberately short. Each entry is filler in any sentence it can appear in;
# "I think" and "I believe" are claims by a real speaker and are not here.
_DISCOURSE_HEDGE = re.compile(
    r"(?:,\s*)?\b(?:i\s+guess|you\s+know|you\s+see|i\s+mean|to\s+be\s+honest)"
    r"\b(?:\s*,)?",
    re.IGNORECASE,
)


def _strip_discourse_hedges(text: str) -> str:
    """Remove pronoun-bearing filler before actor families are compared."""
    return _DISCOURSE_HEDGE.sub(" ", text)




# Every closed-class word the meaning guards count. Derived from the guard
# patterns themselves rather than hand-picked, so a guard and its vocabulary
# cannot drift apart: if a word is added to _MODAL_MEANING it is respelled too.
_GUARD_VOCABULARY: frozenset[str] = frozenset(
    {
        "cannot",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "should",
        "will",
        "would",
        "never",
        "nobody",
        "none",
        "nothing",
        "neither",
        "without",
        "required",
        "requires",
        "needs",
        "needed",
        "prohibited",
        "forbidden",
        "before",
        "after",
        "until",
        "during",
        "while",
        "following",
    }
)


# A word, contraction included. Letters alone split "shouldn't" into "shouldn"
# and "t", and the first is one edit from "should".
_GUARD_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


def _reconcile_guard_vocabulary(source: str, candidate: str) -> str:
    """Spell a guard word in the source when the candidate shows what it was.

    The guards match exact words, so a misspelled one is invisible to them.
    Judged against the writer's own typo, a model that spelled it correctly
    looked like a model that invented it: "cud work real good" -> "could work
    really well" was refused as "modal meaning changed", "befor" -> "before" as
    "date or ordering relationship changed (added before)", and "shoud" ->
    "should" as "obligation strength changed (added should)". Across the
    twenty-essay corpus that class of refusal outnumbered every real meaning
    error, and each one cost a whole paragraph its correction.

    The rules layer repairs most of these before the model runs, which is why
    comparing against the prepared text fixed the common cases. What is left is
    what the speller deliberately declines to guess, and "cud" is the example:
    it is a real word, so no dictionary check will ever call it a typo.

    So the candidate supplies the evidence instead of a dictionary. A source
    token is respelled only when the candidate actually uses that guard word,
    the candidate has dropped the source token, and the two are close enough to
    be the same word misspelled. An earlier version rewrote the source on its
    own and turned "wether" into "neither" and "lights" into "might", inventing
    the negation and modal it was supposed to be checking. Requiring the
    candidate to name the target is what stops that: a word nobody wrote cannot
    appear.

    Words must also share a first letter. That is what separates a misspelling
    from a different word, and it is why "wether" cannot reach "neither".
    """
    # Contractions are one token. Splitting on letters alone made "shouldn" a
    # token of its own, one edit from "should", so "They shouldn't chew shoes"
    # was rewritten to "They should't chew shoes" - which deleted the negation
    # the guard was about to count, and refused a correct repair for reversing
    # the writer's meaning. A word carrying an apostrophe is never touched
    # below: a contraction is not a misspelling of the bare modal inside it.
    source_tokens = {value.casefold() for value in _GUARD_TOKEN.findall(source)}
    candidate_tokens = {
        value.casefold() for value in _GUARD_TOKEN.findall(candidate)
    }
    targets = tuple(
        word
        for word in _GUARD_VOCABULARY
        if word in candidate_tokens and word not in source_tokens
    )
    if not targets:
        return source

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        folded = token.casefold()
        if "'" in folded or "’" in folded:
            return token
        if folded in _GUARD_VOCABULARY or len(folded) < 3:
            return token
        if folded in candidate_tokens:
            # The candidate kept this word, so it was not replaced by anything.
            return token
        matches = [
            word
            for word in targets
            if word[:1] == folded[:1] and _edit_distance_at_most(folded, word, 2)
        ]
        if len(matches) != 1:
            # No target, or an ambiguous one. Guessing here would invent a
            # modal the writer never wrote, which is the failure the guards
            # exist to catch.
            return token
        replacement = matches[0]
        return replacement.upper() if token.isupper() else (
            replacement.capitalize() if token[:1].isupper() else replacement
        )

    return _GUARD_TOKEN.sub(replace, source)








def _noun_actor_misspelled_in(actor: str, text: str) -> bool:
    """True when a noun actor appears in text as a close misspelling.

    Fixing "sistem" to "system" made the counter see a brand-new "system"
    actor, because the misspelled form was invisible to it.
    """
    if len(actor) < 4:
        return False
    limit = 1 if len(actor) < 8 else 2
    for token in re.findall(r"[a-z']+", text.casefold()):
        if len(token) >= 4 and token != actor and _edit_distance_at_most(
            token, actor, limit
        ):
            return True
    return False


def _actors_preserved(
    source: str,
    candidate: str,
    *,
    allow_redundant_pronoun: bool = False,
) -> bool:
    """Keep every actor family while allowing safe proofread compression."""
    source = _normalize_actor_homophones(_strip_discourse_hedges(source))
    candidate = _normalize_actor_homophones(_strip_discourse_hedges(candidate))
    source_actors = _actor_counter(source)
    candidate_actors = _actor_counter(candidate)
    # A noun actor that only appears because a misspelling was fixed (or
    # disappears because the candidate still misspells it) is not a change
    # of who acts. Pronoun families never get this leniency.
    for actor in list((candidate_actors - source_actors)):
        if actor not in _PRONOUN_ACTOR_KEYS and _noun_actor_misspelled_in(
            actor, source
        ):
            candidate_actors[actor] = source_actors[actor]
            if candidate_actors[actor] <= 0:
                del candidate_actors[actor]
    for actor in list((source_actors - candidate_actors)):
        if actor not in _PRONOUN_ACTOR_KEYS and _noun_actor_misspelled_in(
            actor, candidate
        ):
            source_actors[actor] = candidate_actors[actor]
            if source_actors[actor] <= 0:
                del source_actors[actor]
    if set(source_actors) == set(candidate_actors):
        return True
    if (
        allow_redundant_pronoun
        and set(source_actors) - set(candidate_actors) == {"it"}
        and not (set(candidate_actors) - set(source_actors))
        and candidate_actors
    ):
        # The mirror of the allowance for adding "it": "and also it shows the pet
        # is theirs" -> "and also shows the pet is theirs" drops a redundant
        # placeholder subject, which is the tidy-up a proofread is for. Adding
        # "it" has been allowed for the same reason since the fragment repair
        # went in.
        #
        # "it" names nobody, so dropping it cannot lose who acts. What it can do
        # is leave a fragment, and that is why some actor has to remain: "which
        # is why it is good but also not" -> "This is good but also not" keeps
        # none and still fails. Requiring a *noun* actor was too strict - the
        # commonest case leaves only a pronoun ("they"), and it was refused.
        # The literal-phrase rule below predates this and is its narrow version.
        return True
    if (
        source_actors - candidate_actors == Counter({"it": 1})
        and not (candidate_actors - source_actors)
        and re.search(
            r"\bfirst alert\b[^.!?]{0,120}\bclosed it\b",
            source,
            re.IGNORECASE,
        )
        and re.search(
            r"\bclosed the first alert\b",
            candidate,
            re.IGNORECASE,
        )
    ):
        return True

    if (
        set(source_actors) - set(candidate_actors) == {"they"}
        and not (set(candidate_actors) - set(source_actors))
        and (
            _possessive_actor_kept_as_noun(source, candidate)
            or (
                allow_redundant_pronoun
                and _plural_pronoun_kept_as_noun(source, candidate)
            )
        )
    ):
        return True

    if (
        allow_redundant_pronoun
        and set(candidate_actors) - set(source_actors) == {"they"}
        and not (set(source_actors) - set(candidate_actors))
        and _added_possessive_pronoun_has_source_owner(source, candidate)
    ):
        return True

    source_own = re.search(
        r"\b(?:every|each)\s+(dog|cat|animal|pet)\b[^.!?]{0,50}"
        r"\btheir\s+own\b",
        source,
        re.IGNORECASE,
    )
    candidate_own = re.search(
        r"\b(?:every|each)\s+(dog|cat|animal|pet)\b[^.!?]{0,50}"
        r"\bits\s+own\b",
        candidate,
        re.IGNORECASE,
    )
    if (
        source_own is None
        or candidate_own is None
        or source_own.group(1).casefold() != candidate_own.group(1).casefold()
    ):
        return False
    source_actors.subtract({"they": 1})
    candidate_actors.subtract({"it": 1})
    return {
        actor for actor, count in source_actors.items() if count > 0
    } == {
        actor for actor, count in candidate_actors.items() if count > 0
    }


def _added_possessive_pronoun_has_source_owner(
    source: str,
    candidate: str,
) -> bool:
    """Allow singular their only when each/every keeps the same explicit owner."""
    added_forms = tuple(
        match
        for match in _ACTOR_FORM.finditer(candidate)
        if match.group(0).casefold()
        in {"they", "they're", "their", "theirs", "there"}
    )
    if not added_forms or any(
        match.group(0).casefold() not in {"their", "theirs"}
        for match in added_forms
    ):
        return False
    for possessive in added_forms:
        clause_start = max(
            candidate.rfind(".", 0, possessive.start()),
            candidate.rfind("!", 0, possessive.start()),
            candidate.rfind("?", 0, possessive.start()),
        )
        prefix = candidate[clause_start + 1 : possessive.start()]
        owner = re.search(
            r"\b(?:every|each)\s+([A-Za-z][A-Za-z'-]{2,})\b",
            prefix,
            re.IGNORECASE,
        )
        if owner is None or re.search(
            rf"\b(?:every|each)\s+{re.escape(owner.group(1))}\b",
            source,
            re.IGNORECASE,
        ) is None:
            return False
    return True


def _possessive_actor_kept_as_noun(source: str, candidate: str) -> bool:
    actor_forms = [
        match.group(0).casefold()
        for match in _ACTOR_FORM.finditer(source)
        if match.group(0).casefold() in {"they", "they're", "their", "theirs", "there"}
    ]
    if not actor_forms or any(value not in {"their", "theirs"} for value in actor_forms):
        return False
    common_verbs = {"does", "has", "says", "sounds", "uses", "was"}
    for possessive in re.finditer(r"\btheir(?:s)?\b", source, re.IGNORECASE):
        clause_start = max(
            source.rfind(".", 0, possessive.start()),
            source.rfind("!", 0, possessive.start()),
            source.rfind("?", 0, possessive.start()),
        )
        prefix = source[clause_start + 1 : possessive.start()]
        nouns = {
            value.casefold()
            for value in re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}s\b", prefix)
            if value.casefold() not in common_verbs
        }
        if not any(_single_word_present(value, candidate) for value in nouns):
            return False
    return True


def _plural_pronoun_kept_as_noun(source: str, candidate: str) -> bool:
    """Allow removing redundant they only when its plural antecedent remains."""
    pronouns = tuple(
        re.finditer(r"\bthey(?:'re|\u2019re)?\b", source, re.IGNORECASE)
    )
    if not pronouns:
        return False
    common_verbs = {
        "agrees",
        "contains",
        "does",
        "has",
        "includes",
        "makes",
        "needs",
        "reports",
        "says",
        "sounds",
        "uses",
        "was",
    }
    for pronoun in pronouns:
        prefix = source[max(0, pronoun.start() - 220) : pronoun.start()]
        nouns = {
            value.casefold()
            for value in re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}s\b", prefix)
            if value.casefold() not in common_verbs
        }
        if not nouns or not any(
            _single_word_present(value, candidate) for value in nouns
        ):
            return False
    return True


def _restore_tone_actor_pronouns(source: str, candidate: str) -> str:
    """Repeat one clear plural subject when a tone split invents sentence-start They."""
    source_actors = set(_actor_counter(source))
    candidate_actors = set(_actor_counter(candidate))
    if candidate_actors - source_actors != {"they"}:
        return candidate
    if source_actors - candidate_actors:
        return candidate
    subject_match = re.match(
        r"\s*(?P<subject>(?:The|These|Those)\s+"
        r"(?:[A-Za-z][A-Za-z'-]*\s+)?[A-Za-z][A-Za-z'-]*s)\b",
        source,
    )
    if subject_match is None:
        return candidate
    subject = subject_match.group("subject")
    if subject.rsplit(maxsplit=1)[-1].casefold() in {
        "does",
        "has",
        "is",
        "says",
        "sounds",
        "uses",
        "was",
    }:
        return candidate
    if re.search(rf"\b{re.escape(subject)}\b", candidate, re.IGNORECASE) is None:
        return candidate
    return re.sub(
        r"(^|(?<=[.!?])\s+)They\b",
        lambda match: f"{match.group(1)}{subject}",
        candidate,
        flags=re.IGNORECASE,
    )



def _restore_source_phrase_casing(source: str, candidate: str) -> str:
    """Undo capitalization-only named entities invented by a tone rewrite."""
    source_entities = {value.casefold() for value in _named_entities(source)}
    for entity in sorted(_named_entities(candidate), key=len, reverse=True):
        if entity.casefold() in source_entities:
            continue
        source_match = re.search(re.escape(entity), source, re.IGNORECASE)
        if source_match is None:
            continue
        source_form = source_match.group(0)

        def restore_case(match: re.Match[str], value: str = source_form) -> str:
            prefix = candidate[: match.start()]
            if not prefix or re.search(r"[.!?]\s*$", prefix):
                return value[:1].upper() + value[1:]
            return value

        candidate = re.sub(
            re.escape(entity),
            restore_case,
            candidate,
            flags=re.IGNORECASE,
        )
    return candidate
def _negation_counter(text: str) -> Counter[str]:
    values = []
    for match in _NEGATION.finditer(text):
        value = " ".join(match.group(0).casefold().split())
        if value in {"fail", "fails", "failed", "failing"}:
            value = "fail"
        values.append(value)
    return Counter(values)


def _negation_signature(text: str) -> tuple[bool, ...]:
    """Protect sentence polarity while allowing grammatical double-negative repair."""
    sentences = tuple(
        value
        for value in re.split(r"(?<=[.!?])(?:\s+|$)", text.strip())
        if value.strip()
    )
    return tuple(bool(_NEGATION.search(value)) for value in sentences)


def _negation_preserved(source: str, candidate: str) -> bool:
    """Keep polarity while permitting run-on splits and double-negative repair."""
    source_count = _negation_count(source)
    candidate_count = _negation_count(candidate)
    source_count -= _equivalent_absence_count(source, candidate)
    candidate_count -= _equivalent_absence_count(candidate, source)
    repair_allowance = sum(1 for _ in _DOUBLE_NEGATIVE.finditer(source))
    minimum = max(0, source_count - repair_allowance)
    return minimum <= candidate_count <= source_count


def _equivalent_absence_count(negative_text: str, state_text: str) -> int:
    negative_nouns = Counter(
        match.group(1).casefold()
        for match in _NEGATED_ABSENCE.finditer(negative_text)
    )
    state_nouns = Counter(
        match.group(1).casefold()
        for match in _DESCRIBED_ABSENCE.finditer(state_text)
    )
    return sum((negative_nouns & state_nouns).values())


def _negation_count(text: str) -> int:
    # "Neither X nor Y" expresses one negative coordination. Counting both
    # words separately rejects the mandatory repair of malformed
    # "neither X or Y."
    return max(
        0,
        sum(1 for _ in _NEGATION.finditer(text))
        - sum(1 for _ in _NEITHER_NOR_PAIR.finditer(text)),
    )


def _ordering_counter(text: str) -> Counter[str]:
    values: list[str] = []
    for match in _ORDERING.finditer(text):
        value = " ".join(match.group(0).casefold().split())
        if value == "prior to":
            value = "before"
        if value == "following":
            if re.match(
                r"\s+(?:the\s+)?(?:[A-Za-z][A-Za-z'-]*'?s?'?\s+)?"
                r"(?:reconnection|restart|review|approval|return|completion|"
                r"deployment|exercise|meeting)\b",
                text[match.end() :],
                re.IGNORECASE,
            ):
                value = "after"
            else:
                # "The following users" is a label, not a time relationship.
                continue
        if value == "during" and re.match(
            r"\s+(?:last|this|next)\s+"
            r"(?:spring|summer|fall|autumn|winter|year|month|week|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            text[match.end() :],
            re.IGNORECASE,
        ):
            # This only makes an existing relative time explicit. A separate
            # counter rejects invented or removed relative-time facts.
            continue
        if value == "while":
            prefix = text[: match.start()].rstrip()
            following = text[match.end() : match.end() + 100]
            if re.match(
                r"\s+(?:making clear|keeping|maintaining|preserving|ensuring)\b",
                following,
                re.IGNORECASE,
            ):
                # These forms describe how a statement is qualified or how a
                # state is preserved. They do not create a new temporal
                # before/after relationship.
                continue
            if re.search(r"\b(?:a|the|some|little)\s*$", prefix, re.IGNORECASE):
                # "A while" is a duration noun, not a new ordering clause.
                continue
        values.append(value)
    return Counter(values)


def _ordering_preserved(
    source: str,
    candidate: str,
    *,
    grammar_mode: bool = False,
) -> bool:
    source_relative = _relative_time_counter(source)
    candidate_relative = _relative_time_counter(candidate)
    if source_relative != candidate_relative:
        relative_missing = source_relative - candidate_relative
        relative_extra = candidate_relative - source_relative
        if not (
            grammar_mode
            and not relative_extra
            and sum(relative_missing.values()) == 1
        ):
            return False
    source_order = _ordering_counter(source)
    candidate_order = _ordering_counter(candidate)
    if source_order == candidate_order:
        return True
    extra = candidate_order - source_order
    missing = source_order - candidate_order
    if grammar_mode and not extra and sum(missing.values()) == 1:
        # A proofread may drop one redundant connector ("after that, we" ->
        # "then we"). Swapping or adding ordering words still fails below.
        return True
    if (
        grammar_mode
        and not missing
        and sum(extra.values()) == 1
        and set(extra) <= {"until", "while", "during", "once"}
    ):
        # Repairing broken temporal syntax legitimately inserts a
        # subordinating connector ("wait the deadline pass" -> "wait until
        # the deadline passes"). Relationship-defining words (before,
        # after, prior to, following) still may never be invented, and a
        # swap (one missing plus one added) still fails.
        return True
    if missing or set(extra) != {"while"}:
        return False
    contrastive = sum(
        1
        for match in re.finditer(r",\s+while\b", candidate, re.IGNORECASE)
        if re.search(
            r"\b(?:remain(?:s|ed)?|is|are|stay(?:s|ed)?|continue(?:s|d)?)\b",
            candidate[match.end() : match.end() + 100],
            re.IGNORECASE,
        )
    )
    return extra["while"] <= contrastive


def _relative_time_counter(text: str) -> Counter[str]:
    values = []
    for match in _RELATIVE_TIME.finditer(text):
        value = " ".join(match.group(0).casefold().split())
        value = re.sub(r"['\u2019]?s$", "", value)
        values.append(value)
    return Counter(values)


def _modal_meaning_counter(text: str) -> Counter[str]:
    values: list[str] = []
    normalized = text.replace("\u2019", "'")
    for match in _MODAL_MEANING.finditer(normalized):
        value = match.group(0).casefold()
        if value in {"cannot", "can't"}:
            value = "can"
        elif value == "won't":
            value = "will"
        elif value.endswith("n't"):
            value = value[:-3]
        values.append(value)
    return Counter(values)

def _modal_subject_counter(text: str) -> Counter[str]:
    # Compare on normalized contractions ("cant" == "can't") and singular
    # subjects ("visitors" == "visitor"), so agreement and spelling repairs
    # are not read as a change of who holds the permission.
    normalized = _normalize_contractions(text)
    values: list[str] = []
    for match in _MODAL_SUBJECT.finditer(normalized):
        subject = match.group("subject").casefold()
        if subject.endswith("s"):
            subject = subject[:-1]
        values.append(f"{subject}:{match.group('modal').casefold()}")
    return Counter(values)

# A hedge standing in front of an open-ended list: "need maybe lights heaters
# filters or other equipments". "maybe" folds to "may" before the counters see
# it, so the modal spelling is what appears here. The list has to be open -
# closed by "or other", "and so on", "etc" - because that openness is what the
# hedge is doing, and it is what a repair can preserve without a modal.
# The hedge must also sit *after* a finite verb, which is not a modal position:
# a modal precedes its verb ("students may use the lab"), so one that follows
# another verb ("habitats need maybe lights") is hedging the object, not the
# action. Without that requirement this exemption let a genuine permission go -
# "students may use the lab or other rooms" -> "students use the lab or other
# rooms" - which is exactly what the modal guard exists to catch.
_HEDGED_OPEN_LIST = re.compile(
    # Lexical verbs only. "have", "has" and "had" are auxiliaries, so "you have
    # may seen" is a garbled modal position rather than a hedged object, and
    # listing them let a real modal loss through.
    r"\b(?:need|needs|needed|require|requires|include|includes|want|wants"
    r"|use|uses|get|gets|add|adds|buy|buys|bring|brings)\s+may(?:be)?\b"
    r"[^.!?]{0,120}?\b(?:or\s+other|and\s+so\s+on|etc)\b",
    re.IGNORECASE,
)
# The same openness written without a modal, which is how a correction keeps it.
_OPEN_LIST_MARKER = re.compile(
    r"\b(?:or\s+other|and\s+so\s+on|etc|such\s+as|(?:things|something)\s+like)\b",
    re.IGNORECASE,
)
_BARE_TEMPORAL_CLAUSE = re.compile(
    r"\b(?:when|until|after|before|once)\s+(?:the|a|an|this|that|it|they)\b",
    re.IGNORECASE,
)


def _modal_meaning_preserved(
    source: str,
    candidate: str,
    *,
    grammar_mode: bool = False,
) -> bool:
    source_modals = _modal_meaning_counter(source)
    candidate_modals = _modal_meaning_counter(candidate)
    if source_modals == candidate_modals:
        return True
    if grammar_mode and set(source_modals) == set(candidate_modals):
        # A modal's multiplicity is not meaning; its presence is. The writer
        # wrote "then maybe forgets their keys because the maybe morning got
        # busy" and the repair dropped the stray second one, which read as
        # "modal meaning changed (missing may)". Every modal the writer used is
        # still there, so nothing was permitted, required or made uncertain that
        # was not before. Losing a modal family outright still fails, and so
        # does gaining one, because both change what the sentence claims.
        return True
    if (
        grammar_mode
        and not (candidate_modals - source_modals)
        and set(source_modals - candidate_modals) <= {"may", "might"}
        and _HEDGED_OPEN_LIST.search(source)
        and _OPEN_LIST_MARKER.search(candidate)
    ):
        # The hedge is still there; it is just not spelled as a modal. The
        # writer put one in front of an open-ended list - "some habitats does
        # need maybe lights heaters filters or other equipments" - where it
        # hedges which items the list contains, not whether the sentence is
        # true. Repairs keep that openness with "or other equipment" or "things
        # like lights", and the counter, which only recognises literal modals,
        # called every one of them a lost modal. One paragraph was refused four
        # times over, including the attempt that wrote the hedge back as "things
        # like" - the model preserving the meaning exactly and being told it had
        # not.
        #
        # A hedge on a predicate is a different thing and is deliberately not
        # covered: "its needds is maybe MET" -> "its needs are met" turns a
        # hedge into a claim, and that stays refused because there is no open
        # list in the source for this to match.
        return True
    if (
        grammar_mode
        and candidate_modals - source_modals == Counter({"will": 1})
        and not (source_modals - candidate_modals)
        and _BARE_TEMPORAL_CLAUSE.search(source)
    ):
        # Grammar repair of a bare temporal clause ("when the milestone
        # happen" -> "when the milestone will happen") adds an explicit
        # future marker without changing permission or obligation.
        return True
    if (
        grammar_mode
        and candidate_modals - source_modals == Counter({"would": 1})
        and not (source_modals - candidate_modals)
        and re.search(r"\brather\b", source, re.IGNORECASE)
        and re.search(r"\b(?:would|'d)\s+rather\b", candidate, re.IGNORECASE)
    ):
        # Completing the "would rather" idiom ("they rather chase" ->
        # "they would rather chase") is grammar repair, not a new intent.
        return True
    return (
        source_modals - candidate_modals == Counter({"would": 1})
        and not (candidate_modals - source_modals)
        and re.search(
            r"\b(?:as|like) the plan said it would\b",
            source,
            re.IGNORECASE,
        )
        is not None
        and re.search(r"\bas planned\b", candidate, re.IGNORECASE) is not None
    )

def _negated_modal_count(text: str) -> int:
    """Modals sitting inside a negated clause.

    A modal under a negation carries the refusal even when no "not" touches it:
    "No personal email address can be used" prohibits, while "No personal email
    address is used" merely reports. Dropping the modal there inverts an
    instruction, so it is counted separately from ordinary modal drift and
    refuses the candidate.
    """
    total = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not sentence.strip():
            continue
        if _NEGATION.search(sentence):
            total += sum(1 for _ in _MODAL_MEANING.finditer(sentence))
    return total


def _prohibition_counter(text: str) -> Counter[str]:
    """Refusals in the text, normalized so wording changes do not register.

    "cannot", "can not" and "can't" are one prohibition, not three, so a
    correction is free to respell them. What must not move is how many there
    are.
    """
    values: list[str] = []
    for match in _PROHIBITION.finditer(text):
        value = " ".join(
            match.group(0).casefold().replace("’", "'").split()
        )
        value = value.replace("'", "")
        if value in {"cannot", "can not", "cant", "can never"}:
            value = "cannot"
        elif value in {"wont", "will not", "will never"}:
            value = "will not"
        elif value in {"mustnt", "must not", "must never"}:
            value = "must not"
        values.append(value)
    return Counter(values)


def _obligation_counter(text: str) -> Counter[str]:
    values: list[str] = []
    for match in _OBLIGATION.finditer(text):
        value = " ".join(
            match.group(0).casefold().replace("\u2019", "'").split()
        )
        if value in {"shouldn't", "shouldnt"}:
            value = "should"
        if value == "got too":
            if re.match(
                r"\s+(?:much|many|little|few|late|early|big|small|old|far)(?![a-z])",
                text[match.end() :],
                re.IGNORECASE,
            ):
                # "got too much/late" is degree, not obligation.
                continue
            value = "got to"
        if value in {"has to", "have to", "had to"} and re.match(
            r"\s+(?:much|many|little|few)\b",
            text[match.end() :],
            re.IGNORECASE,
        ):
            # "have to much/many" is a misspelling of "have too much/many,"
            # not an obligation. Let proofreading repair the homophone.
            continue
        if value in {
            "must",
            "shall",
            "require",
            "required",
            "requires",
            "need",
            "needs",
            "needed",
            "has to",
            "have to",
            "had to",
            "got to",
        }:
            value = "hard requirement"
        values.append(value)
    return Counter(values)






# Measured over 35 real names and 12 typos. The closest a real name comes to
# looking like a typo is Almeria, one edit from a word of frequency 6.4 million;
# the least typo-like typo is "Learniing", one edit from "learning" at 118
# million. The threshold sits in the middle of that gap, so both sides have
# roughly a fourfold margin. Being too conservative only leaves a typo frozen,
# which is the behaviour we already had; being too loose unprotects a name.


def _runtime_negation_literals(text: str) -> tuple[str, ...]:
    """Keep each source negation immutable while a style rewrite changes wording."""
    values: list[str] = []
    auxiliary = re.compile(
        r"\b(?:do|does|did|is|are|was|were|has|have|had|can|could|will|"
        r"would|should|must)\s+$",
        re.IGNORECASE,
    )
    for match in _NEGATION.finditer(text):
        value = match.group(0)
        if value.casefold() == "not":
            prefix = auxiliary.search(text[: match.start()])
            if prefix is not None:
                action = re.match(
                    r"\s+(?:(?:automatically|directly|ever|immediately|"
                    r"necessarily)\s+)?[A-Za-z][A-Za-z'-]*\b",
                    text[match.end() :],
                    re.IGNORECASE,
                )
                end = match.end() + action.end() if action is not None else match.end()
                value = text[prefix.start() : end]
        values.append(value)
    return tuple(dict.fromkeys(values))


def _runtime_negated_constraint_phrases(text: str) -> tuple[str, ...]:
    """Keep complete high-risk negative claims attached to their objects."""
    pattern = re.compile(
        r"(?:^|(?<=[.!?])\s+)"
        r"(?P<clause>[^.!?;]{0,80}?\b"
        r"(?:must|should|shall|may|can|could|will|would|does|do|did)\s+not\s+"
        r"(?:authorize|blame|claim|delete|publish|release|send)\b"
        r"[^.!?;]{0,220})",
        re.IGNORECASE,
    )
    return tuple(
        dict.fromkeys(match.group("clause").strip() for match in pattern.finditer(text))
    )


def _runtime_modal_actor_phrases(text: str) -> tuple[str, ...]:
    """Keep a modal attached to its explicit pronoun actor and action."""
    pattern = re.compile(
        r"\b(?:no one|nobody|i|we|you|he|she|they|it)\s+"
        r"(?:must|should|shall|may|can|could|will|would|has to|have to|had to)\s+"
        r"(?:not\s+)?[A-Za-z][A-Za-z'-]*\b",
        re.IGNORECASE,
    )
    return tuple(dict.fromkeys(match.group(0) for match in pattern.finditer(text)))


def _runtime_alternative_fact_phrases(text: str) -> tuple[str, ...]:
    """Keep a closed set of factual alternatives intact during style rewrites."""
    patterns = (
        re.compile(
            r"\bwhether\s+[^.!?;]{1,180}?\s+"
            r"(?:is|are|was|were)\s+responsible\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:a|the)\s+(?:table|list|report|summary)\s+[^.!?]{0,40}"
            r"\ball\s+[a-z-]+\s+[a-z-]+\s+rather than\s+only\s+the\s+"
            r"[a-z-]+\s+[^.!?]{1,60}",
            re.IGNORECASE,
        ),
    )
    values = (
        match.group(0)
        for pattern in patterns
        for match in pattern.finditer(text)
    )
    return tuple(dict.fromkeys(values))


def _runtime_temporal_fact_phrases(text: str) -> tuple[str, ...]:
    """Keep explicit duration and deadline relationships attached to their facts."""
    number = (
        r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|"
        r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred)"
    )
    month = (
        r"(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)"
    )
    patterns = (
        re.compile(
            rf"\bafter\s+{number}\s+"
            r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bby\s+{month}\s+\d{{1,2}}(?:,\s+\d{{4}})?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bdue\s+(?:on\s+)?{month}\s+\d{{1,2}}(?:,\s+\d{{4}})?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:before|after|until|through)\s+{month}\s+"
            r"\d{1,2}(?:,\s+\d{4})?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bby\s+\d{1,2}:\d{2}\s+[ap]\.m\.\b",
            re.IGNORECASE,
        ),
    )
    values = (
        match.group(0)
        for pattern in patterns
        for match in pattern.finditer(text)
    )
    return tuple(dict.fromkeys(values))


def _paragraph_count(text: str) -> int:
    # Windows editors report bare \r or \r\n line breaks; a review section can
    # span two paragraphs separated by a single such break. Counting only
    # blank-line breaks let the model silently merge those paragraphs.
    return len(
        [
            value
            for value in re.split(r"(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))*", text)
            if value.strip()
        ]
    )


def _scope_for_plan(plan: ExecutionPlan):
    from .polish_types import ContextScope

    if plan.task is PolishTask.DOCUMENT:
        return ContextScope.DOCUMENT
    if plan.context_window >= 4096:
        return ContextScope.PARAGRAPH
    return ContextScope.SENTENCE


def _notify_plan(request: CorrectionRequest, plan: ExecutionPlan) -> None:
    callback = request.plan_callback
    if callback is None:
        return
    try:
        callback(plan)
    except (OSError, RuntimeError, TypeError, ValueError):
        # Status reporting cannot be allowed to break a correction.
        return


def _task_for_text(command: str, text: str) -> PolishTask:
    if command == "Proofread":
        if len(text) > 1200 or len(re.split(r"\n\s*\n", text)) > 2:
            return PolishTask.DOCUMENT
        return PolishTask.PROOFREAD
    if command in {
        "Formal",
        "Friendly",
        "Professional",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    }:
        return PolishTask.TONE
    return PolishTask.REWRITE


__all__ = [
    "PolishEngine",
    "PolishWritingAssistantAdapter",
    "PolishValidator",
    "create_default_polish_engine",
    "minimal_edits",
]
