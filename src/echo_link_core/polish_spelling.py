from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .correction import _PROTECTED_PATTERN
from .paths import resource_path
from .writing_suggestions import (
    SuggestionCategory,
    WritingSuggestion,
    WritingSuggestionEngine,
    apply_all_suggestions,
)


_WORD = re.compile(r"(?<![\w'-])[A-Za-z][A-Za-z'-]{2,}(?![\w'-])")
_APPLICATION_WORDS = frozenset(
    {
        "Manuscript",
        "Ollama",
        "Gemma",
        "MiniLM",
        "ONNX",
        "PySide",
        "PySide6",
        "SQLCipher",
        "SymSpell",
        "Hunspell",
        "CUDA",
        "Vulkan",
        "SYCL",
        "HIP",
        "VRAM",
        "GPU",
        "CPU",
        "Outlook",
        "Chromebook",
    }
)
_SAFE_APOSTROPHE_REPLACEMENTS = {
    "arent": "aren't",
    "cant": "can't",
    "couldnt": "couldn't",
    "didnt": "didn't",
    "doesnt": "doesn't",
    "dont": "don't",
    "everybodys": "everybody's",
    "hasnt": "hasn't",
    "isnt": "isn't",
    "nobodys": "nobody's",
    "shouldnt": "shouldn't",
    "thats": "that's",
    "wasnt": "wasn't",
    "werent": "weren't",
    "whats": "what's",
    "wont": "won't",
    "wouldnt": "wouldn't",
}


@dataclass(frozen=True, slots=True)
class DeterministicPolishResult:
    text: str
    rule_suggestions: tuple[WritingSuggestion, ...]
    spelling_suggestions: tuple[WritingSuggestion, ...]
    elapsed_ms: float


class EnglishDictionary:
    """Bundled Hunspell word validity plus SymSpell candidate ranking."""

    def __init__(
        self,
        *,
        hunspell_dictionary: str | Path | None = None,
        symspell_dictionary: str | Path | None = None,
    ) -> None:
        self.hunspell_dictionary = Path(
            hunspell_dictionary
            or resource_path("resources", "polish", "hunspell-en-us", "en_US.dic")
        )
        self.symspell_dictionary = Path(
            symspell_dictionary
            or resource_path(
                "resources",
                "polish",
                "symspell",
                "frequency_dictionary_en_82_765.txt",
            )
        )
        self._lock = threading.Lock()
        self._words: frozenset[str] | None = None
        self._symspell = None
        self._loaded = False
        self._load_error = ""

    @property
    def ready(self) -> bool:
        return self._loaded and self._words is not None and self._symspell is not None

    @property
    def load_error(self) -> str:
        return self._load_error

    def warm(self) -> bool:
        self._ensure_loaded()
        return self.ready

    def contains(self, word: str) -> bool:
        self._ensure_loaded()
        folded = _dictionary_form(word)
        return bool(folded and self._words is not None and folded in self._words)

    def suggestion(self, word: str) -> tuple[str, int, int] | None:
        self._ensure_loaded()
        if self._symspell is None:
            return None
        try:
            from symspellpy import Verbosity

            # CLOSEST, not TOP: TOP returns the single most frequent word at
            # the closest distance, which made "esay" into "ebay" because the
            # brand outweighs the word a writer meant. Ranking the equally
            # close candidates by the shape of the mistake first fixes that.
            values = self._symspell.lookup(
                word.casefold(),
                Verbosity.CLOSEST,
                max_edit_distance=1,
                include_unknown=False,
                transfer_casing=False,
            )
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return None
        if not values:
            return None
        best = min(
            values,
            key=lambda value: (
                int(value.distance),
                _typo_shape_rank(word.casefold(), str(value.term)),
                -max(0, int(value.count)),
                str(value.term),
            ),
        )
        replacement = _match_case(word, str(best.term))
        return replacement, int(best.distance), max(0, int(best.count))

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                words = _load_hunspell_words(self.hunspell_dictionary)
                from symspellpy import SymSpell

                symspell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
                loaded = symspell.load_dictionary(
                    str(self.symspell_dictionary),
                    term_index=0,
                    count_index=1,
                    separator=" ",
                    encoding="utf-8",
                )
                if not loaded:
                    raise ValueError("SymSpell frequency dictionary did not load.")
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                self._words = frozenset()
                self._symspell = None
                self._loaded = True
                self._load_error = str(exc)
                return
            self._words = frozenset(words)
            self._symspell = symspell
            self._loaded = True
            self._load_error = ""


def _typo_shape_rank(source: str, candidate: str) -> int:
    """Order equally close candidates by how people actually mistype.

    Frequency alone is a poor tie-break for a misspelling: at one edit from
    "esay" the dictionary holds both "essay" and the far more frequent "ebay",
    and it answered with the brand. Typing shape is the better signal.

    0 - a doubled letter collapsed to one ("pett" -> "pet")
    1 - a letter left out ("esay" -> "essay")
    2 - anything else, which is where a substitution like "ebay" lands
    """
    removed = _deleted_index(source, candidate)
    if removed is not None:
        # The removed letter has to be one half of a doubled pair. Asking only
        # whether the source contains any doubled pair turned "adress" into
        # "dress", because it dropped the leading "a" and the unrelated "ss"
        # further along still answered yes.
        doubled = (
            removed > 0 and source[removed - 1] == source[removed]
        ) or (
            removed + 1 < len(source) and source[removed + 1] == source[removed]
        )
        return 0 if doubled else 2
    if _deleted_index(candidate, source) is not None:
        return 1
    return 2


def _deleted_index(longer: str, shorter: str) -> int | None:
    """Index of the one character whose removal turns `longer` into `shorter`.

    None when the two are not a single deletion apart. Where a run of repeats
    makes several indexes equivalent, the first is returned; any of them
    describes the same edit.
    """
    if len(longer) != len(shorter) + 1:
        return None
    index = 0
    while index < len(shorter) and longer[index] == shorter[index]:
        index += 1
    if longer[index + 1 :] != shorter[index:]:
        return None
    return index


class PolishRuleEngine:
    """Existing rules, high-confidence SymSpell, and bundled Hunspell words."""

    def __init__(
        self,
        *,
        suggestion_engine: WritingSuggestionEngine | None = None,
        dictionary: EnglishDictionary | None = None,
    ) -> None:
        self.suggestion_engine = suggestion_engine or WritingSuggestionEngine()
        self.dictionary = dictionary or EnglishDictionary()

    def warm(self) -> bool:
        return self.dictionary.warm()

    def analyze_spelling(
        self,
        text: str,
        *,
        user_terms: tuple[str, ...] = (),
    ) -> tuple[WritingSuggestion, ...]:
        if not text:
            return ()
        protected = tuple((match.start(), match.end()) for match in _PROTECTED_PATTERN.finditer(text))
        allowed = {
            _dictionary_form(value)
            for value in (*_APPLICATION_WORDS, *user_terms)
            if _dictionary_form(value)
        }
        source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
        suggestions: list[WritingSuggestion] = []
        for match in _WORD.finditer(text):
            word = match.group(0)
            folded = _dictionary_form(word)
            if not folded or folded in allowed:
                continue
            if _is_protected(match.start(), match.end(), protected):
                continue
            safe_apostrophe = _SAFE_APOSTROPHE_REPLACEMENTS.get(folded)
            if safe_apostrophe:
                replacement = _match_case(word, safe_apostrophe)
                material = (
                    f"apostrophe\0{match.start()}\0{match.end()}\0"
                    f"{word}\0{replacement}"
                )
                candidate_digest = hashlib.sha256(
                    material.encode("utf-8")
                ).hexdigest()[:16]
                suggestions.append(
                    WritingSuggestion(
                        suggestion_id=f"ws:{source_digest}:{candidate_digest}",
                        category=SuggestionCategory.GRAMMAR,
                        title="Restore apostrophe",
                        explanation=(
                            f'Context-safe English form uses "{replacement}" '
                            f'instead of "{word}".'
                        ),
                        start=match.start(),
                        end=match.end(),
                        original=word,
                        replacement=replacement,
                    )
                )
                continue
            if _should_skip_word(text, match.start(), word):
                continue
            if self.dictionary.contains(word):
                continue
            value = self.dictionary.suggestion(word)
            if value is None:
                continue
            replacement, distance, frequency = value
            if distance != 1 or replacement.casefold() == word.casefold():
                continue
            if frequency < 20:
                continue
            if not _capitalized_repair_is_safe(word, replacement):
                continue
            material = (
                f"symspell\0{match.start()}\0{match.end()}\0"
                f"{word}\0{replacement}"
            )
            candidate_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
            suggestions.append(
                WritingSuggestion(
                    suggestion_id=f"ws:{source_digest}:{candidate_digest}",
                    category=SuggestionCategory.SPELLING,
                    title="Fix spelling",
                    explanation=(
                        f'Bundled English dictionaries suggest "{replacement}" '
                        f'for "{word}".'
                    ),
                    start=match.start(),
                    end=match.end(),
                    original=word,
                    replacement=replacement,
                )
            )
        return tuple(suggestions)

    def correct(
        self,
        text: str,
        *,
        user_terms: tuple[str, ...] = (),
    ) -> DeterministicPolishResult:
        started = time.perf_counter()
        normalized = normalize_polish_text(text)
        rules = self.suggestion_engine.analyze(normalized)
        rule_text = apply_all_suggestions(normalized, rules) if rules else normalized
        spelling = self.analyze_spelling(rule_text, user_terms=user_terms)
        result = apply_all_suggestions(rule_text, spelling) if spelling else rule_text
        return DeterministicPolishResult(
            text=result,
            rule_suggestions=rules,
            spelling_suggestions=spelling,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def normalize_polish_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Polish text must be a string.")
    # NFC fixes equivalent Unicode spellings without flattening line breaks,
    # indentation, or editor formatting boundaries.
    return unicodedata.normalize("NFC", text)


def _load_hunspell_words(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError("Bundled Hunspell dictionary is empty.")
    words: set[str] = set()
    for line in lines[1:]:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        # Hunspell escapes literal slashes as "\/"; the first unescaped slash
        # starts the affix flags.
        value = raw.replace("\\/", "\0")
        value = value.split("/", 1)[0].replace("\0", "/")
        value = value.split("\t", 1)[0].strip()
        folded = _dictionary_form(value)
        if folded:
            words.add(folded)
    if len(words) < 10_000:
        raise ValueError("Bundled Hunspell dictionary is incomplete.")
    return words


def _dictionary_form(value: str) -> str:
    return unicodedata.normalize("NFC", str(value)).strip("'").casefold()


def _should_skip_word(text: str, start: int, word: str) -> bool:
    if len(word) <= 3 or len(word) > 32:
        return True
    if any(character.isdigit() for character in word):
        return True
    if word.isupper() or "-" in word or "'" in word:
        return True
    # Capitalized words are NOT skipped here. A capitalized token can be a
    # name, so it used to be refused outright - but that also left every
    # capitalized typo in place, which a reader sees first: "Ansering these
    # questions" survived a whole essay, and so did the title "Dogs Makes Grate
    # Petts". The name protection now happens where the repair is known, in
    # _capitalized_repair_is_safe, which can tell a respelling from a different
    # word.
    return False


def _capitalized_repair_is_safe(word: str, replacement: str) -> bool:
    """Whether a capitalized word may be respelled, or is likely a name.

    A name and its near-neighbours differ by a substitution or a plain
    deletion: "Malik" to "Mali" is a real place and a real person, and guessing
    between them renames someone. A typo is shaped differently - a letter left
    out, or a doubled letter collapsed - and those are safe to repair.

    Lowercase words never reach this; they are repaired on the usual rules.
    """
    if not word[:1].isupper():
        return True
    return _typo_shape_rank(word.casefold(), replacement.casefold()) <= 1


def _is_sentence_start(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    return not prefix or prefix[-1:] in ".!?\n"


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _is_protected(
    start: int,
    end: int,
    spans: tuple[tuple[int, int], ...],
) -> bool:
    return any(start < right and end > left for left, right in spans)


__all__ = [
    "DeterministicPolishResult",
    "EnglishDictionary",
    "PolishRuleEngine",
    "normalize_polish_text",
]
