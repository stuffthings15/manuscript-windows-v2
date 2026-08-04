from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path

import numpy as np

from .paths import resource_path
from .polish_types import ClassifierDecision, ContextScope, PolishTask


_WORD = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?](?:[\"'\u201d\u2019)\]]+)?(?:\s+|$)")
_COMMON_ENGLISH = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "before",
        "but",
        "by",
        "can",
        "do",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "up",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)
_UNRESOLVED_PATTERNS = (
    (
        "agreement",
        re.compile(
            r"\b(?:he|she|it|this|that)\s+(?:are|were|have|do|need|want|say|go|fail|run|work)\b"
            r"|\b(?:we|you|they|these|those)\s+(?:is|was|has|does|needs|wants|says|goes|fails|runs|works)\b"
            r"|\b(?:reports|documents|results|files|tests|users|models|systems|services|teams)\s+"
            r"(?:is|was|has|does|needs|fails|runs|works)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "verb-form",
        re.compile(
            r"\b(?:did|does|do)\s+[A-Za-z]+(?:ed|s)\b"
            r"|\b(?:did|does|do)\s+(?:went|gone|done|wrote|written|ran|seen|"
            r"took|taken|made|ate|eaten|came|come)\b"
            r"|\b(?:has|have|had)\s+(?:go|do|write|run|see|take|make|went|"
            r"did|wrote|ran|saw|took|ate|came)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "run-on",
        re.compile(
            r"\b(?:however|therefore|meanwhile|instead)\s*,?\s+[a-z]"
            r"|,\s*(?:he|she|it|we|they|you|i)\s+"
            r"(?:is|are|was|were|will|can|did|do|have|has|tried|failed|"
            r"worked|ran|stopped|started|finished|tested|fixed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fragment",
        re.compile(
            r"(?:^|[.!?]\s+)(?:Because|Although|While|Unless|Since)\b[^.!?]{2,80}[.!?](?:\s|$)",
            re.IGNORECASE,
        ),
    ),
)


class MiniLMEncoder:
    """Pinned INT8 English MiniLM encoder using ONNX Runtime CPU."""

    def __init__(self, model_root: str | Path | None = None) -> None:
        self.model_root = Path(
            model_root
            or resource_path("models", "polish", "minilm-l6-v2-int8")
        )
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._load_error = ""

    @property
    def ready(self) -> bool:
        return self._session is not None and self._tokenizer is not None

    @property
    def load_error(self) -> str:
        return self._load_error

    def warm(self) -> bool:
        self._ensure_loaded()
        return self.ready

    def embed(self, texts: tuple[str, ...] | list[str]) -> np.ndarray:
        self._ensure_loaded()
        if not self.ready:
            raise RuntimeError(
                self._load_error or "Bundled MiniLM classifier is unavailable."
            )
        values = [str(text)[:12_000] for text in texts]
        if not values:
            return np.empty((0, 384), dtype=np.float32)
        with self._lock:
            encoded = [self._tokenizer.encode(value) for value in values]
            maximum = min(256, max(len(item.ids) for item in encoded))
            ids = np.zeros((len(encoded), maximum), dtype=np.int64)
            masks = np.zeros((len(encoded), maximum), dtype=np.int64)
            types = np.zeros((len(encoded), maximum), dtype=np.int64)
            for row, item in enumerate(encoded):
                item_ids = item.ids[:maximum]
                item_types = item.type_ids[:maximum]
                size = len(item_ids)
                ids[row, :size] = item_ids
                masks[row, :size] = 1
                if item_types:
                    types[row, :size] = item_types
            output = self._session.run(
                ["last_hidden_state"],
                {
                    "input_ids": ids,
                    "attention_mask": masks,
                    "token_type_ids": types,
                },
            )[0]
        expanded_mask = masks[:, :, np.newaxis].astype(np.float32)
        summed = (output * expanded_mask).sum(axis=1)
        counts = np.maximum(expanded_mask.sum(axis=1), 1e-9)
        pooled = summed / counts
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.maximum(norms, 1e-9)).astype(np.float32)

    def similarity(self, left: str, right: str) -> float:
        if left == right:
            return 1.0
        values = self.embed((left, right))
        if len(values) != 2:
            return 0.0
        return max(-1.0, min(1.0, float(np.dot(values[0], values[1]))))

    def _ensure_loaded(self) -> None:
        if self._session is not None or self._load_error:
            return
        with self._lock:
            if self._session is not None or self._load_error:
                return
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                model = self.model_root / "onnx" / "model_quint8_avx2.onnx"
                tokenizer = self.model_root / "tokenizer.json"
                if not model.is_file() or not tokenizer.is_file():
                    raise FileNotFoundError("Bundled MiniLM files are missing.")
                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min(4, (os_cpu_count() // 2) or 1))
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session = ort.InferenceSession(
                    str(model),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                value = Tokenizer.from_file(str(tokenizer))
                value.enable_truncation(max_length=256)
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._load_error = str(exc)
                return
            self._session = session
            self._tokenizer = value


class MiniLMPolishClassifier:
    """Multi-head English router built on the bundled INT8 MiniLM features."""

    def __init__(self, encoder: MiniLMEncoder | None = None) -> None:
        self.encoder = encoder or MiniLMEncoder()
        self._prototype_lock = threading.Lock()
        self._prototype_vectors: np.ndarray | None = None

    def warm(self) -> bool:
        if not self.encoder.warm():
            return False
        self._ensure_prototypes()
        return True

    def classify(
        self,
        source: str,
        deterministic: str,
        *,
        task: PolishTask,
        deterministic_categories: tuple[str, ...] = (),
    ) -> ClassifierDecision:
        started = time.perf_counter()
        english = english_confidence(deterministic or source)
        categories = list(dict.fromkeys(deterministic_categories))
        unresolved: list[str] = []
        for category, pattern in _UNRESOLVED_PATTERNS:
            if pattern.search(deterministic):
                unresolved.append(category)
        categories.extend(value for value in unresolved if value not in categories)
        word_count = len(_WORD.findall(deterministic))
        sentences = max(1, len(_SENTENCE_END.findall(deterministic)))
        paragraphs = len([value for value in re.split(r"\n\s*\n", deterministic) if value.strip()])
        if word_count >= 36 and "clarity" not in categories:
            categories.append("clarity")
        if task in {PolishTask.REWRITE, PolishTask.TONE} and "style" not in categories:
            categories.append("style")

        prototype_error = self._prototype_error_score(deterministic)
        complexity = min(
            1.0,
            max(
                word_count / 120.0,
                sentences / 8.0,
                paragraphs / 4.0,
            ),
        )
        severity = min(
            1.0,
            0.18 * len(categories)
            + 0.34 * len(unresolved)
            + 0.20 * complexity
            + 0.15 * prototype_error,
        )
        if task is PolishTask.DOCUMENT:
            scope = ContextScope.DOCUMENT
        elif paragraphs > 1 or word_count > 80 or sentences > 3:
            scope = ContextScope.PARAGRAPH
        else:
            scope = ContextScope.SENTENCE

        if task is not PolishTask.PROOFREAD:
            needs_llm = english >= 0.72
            severity = max(severity, 0.70)
        else:
            needs_llm = bool(unresolved) or (
                not deterministic_categories
                and (complexity >= 0.55 or prototype_error >= 0.72)
            )
        elapsed = (time.perf_counter() - started) * 1000.0
        return ClassifierDecision(
            needs_llm=bool(needs_llm),
            error_categories=tuple(categories),
            severity=max(0.0, min(1.0, severity)),
            context_scope=scope,
            english_confidence=english,
            backend=(
                "MiniLM INT8 ONNX + deterministic heads"
                if self.encoder.ready
                else "Deterministic English heads"
            ),
            elapsed_ms=elapsed,
        )

    def semantic_similarity(self, source: str, candidate: str) -> float:
        try:
            return self.encoder.similarity(source, candidate)
        except RuntimeError:
            return lexical_similarity(source, candidate)

    def _prototype_error_score(self, text: str) -> float:
        if not text.strip() or not self.encoder.warm():
            return 0.0
        self._ensure_prototypes()
        if self._prototype_vectors is None:
            return 0.0
        try:
            value = self.encoder.embed((text,))[0]
        except (IndexError, RuntimeError, ValueError):
            return 0.0
        scores = self._prototype_vectors @ value
        error_score = float(np.max(scores[:4]))
        clean_score = float(np.max(scores[4:]))
        # The embedding is a feature. Hard linguistic heads remain decisive.
        return max(0.0, min(1.0, 0.5 + (error_score - clean_score)))

    def _ensure_prototypes(self) -> None:
        if self._prototype_vectors is not None or not self.encoder.ready:
            return
        with self._prototype_lock:
            if self._prototype_vectors is not None:
                return
            values = (
                "The reports is not ready and the team do not knows why.",
                "We tested it yesterday it fail, however nobody fixed it.",
                "Because the document was missing.",
                "This sentence have awkward grammar and unclear punctuation",
                "The reports are ready, and the team knows why.",
                "We tested it yesterday. It failed, and nobody fixed it.",
                "The document was missing because the export failed.",
                "This sentence has clear grammar and correct punctuation.",
            )
            try:
                self._prototype_vectors = self.encoder.embed(values)
            except RuntimeError:
                self._prototype_vectors = None


def english_confidence(text: str) -> float:
    letters = _LETTER.findall(text)
    if not letters:
        return 0.0
    latin = sum(("A" <= value <= "Z") or ("a" <= value <= "z") for value in letters)
    latin_ratio = latin / len(letters)
    words = [match.group(0).casefold() for match in _WORD.finditer(text)]
    if not words:
        return max(0.0, min(1.0, latin_ratio * 0.75))
    common = sum(word in _COMMON_ENGLISH for word in words)
    common_ratio = common / len(words)
    if len(words) <= 4 and latin_ratio >= 0.95:
        base = 0.82
    else:
        base = 0.55 * latin_ratio + 0.45 * min(1.0, common_ratio * 3.5)
    return max(0.0, min(1.0, base))


def lexical_similarity(source: str, candidate: str) -> float:
    left = [word.casefold() for word in _WORD.findall(source)]
    right = [word.casefold() for word in _WORD.findall(candidate)]
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    left_counts: dict[str, int] = {}
    right_counts: dict[str, int] = {}
    for word in left:
        left_counts[word] = left_counts.get(word, 0) + 1
    for word in right:
        right_counts[word] = right_counts.get(word, 0) + 1
    shared = sum(min(count, right_counts.get(word, 0)) for word, count in left_counts.items())
    precision = shared / len(right)
    recall = shared / len(left)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def os_cpu_count() -> int:
    import os

    return max(1, int(os.cpu_count() or 1))


__all__ = [
    "MiniLMEncoder",
    "MiniLMPolishClassifier",
    "english_confidence",
    "lexical_similarity",
]
