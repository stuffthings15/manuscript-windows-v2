from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from .correction import _PROTECTED_PATTERN
from .paths import resource_path
from .writing_suggestions import (
    SPELLING_CORRECTIONS,
    WETHER_ANIMAL_LEAD,
    SuggestionCategory,
    WritingSuggestion,
    WritingSuggestionEngine,
    _match_case,
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


# "aint" is not a spelling mistake with one answer; it is a contraction whose
# expansion depends on its subject. Writing "isn't" after "they" would be a
# worse error than leaving the word alone, so an unrecognised subject is left
# untouched rather than guessed at.
_SUBJECT_SENSITIVE_MISSPELLINGS = {
    "aint": {
        "i": "am not",
        "they": "are not",
        "we": "are not",
        "you": "are not",
        "he": "is not",
        "she": "is not",
        "it": "is not",
        "that": "is not",
        "this": "is not",
        "there": "is not",
    },
}


# Words the speller deliberately refuses to guess at, because more than one
# correction is genuinely possible and only the sentence can decide. "esay" is
# one edit from both "essay" and "easy", and the frequency dictionary favours
# "easy" tenfold - so a flat lookup would confidently write the wrong word. The
# bundled corpus offers an answer for these; it does not get to give one.
_AMBIGUOUS_LEAVE_TO_MODEL = frozenset({"esay"})


@lru_cache(maxsize=1)
def _bundled_misspellings() -> dict[str, str]:
    """30,464 validated pairs from the bundled corpus file.

    Loaded once, on first use, because it is 588 kB and most sessions never
    reach a word the other maps do not already cover. A missing or unreadable
    file is not an error: the maps above still apply, so the reader loses
    coverage rather than the whole review.
    """
    path = Path(
        resource_path("resources", "polish", "misspellings", "misspellings-en.txt")
    )
    pairs: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                wrong, _, right = line.strip().partition("\t")
                if wrong and right and wrong not in _AMBIGUOUS_LEAVE_TO_MODEL:
                    pairs[wrong] = right
    except OSError:
        return {}
    return pairs


def _preceding_word(text: str, start: int) -> str:
    lead = re.findall(r"([A-Za-z']+)\W*$", text[:start])
    return lead[0].casefold() if lead else ""




def _measured_replacement(text: str, start: int, folded: str) -> str:
    """The correction for a word the speller cannot resolve, or "" for none."""
    if folded == "wether":
        if _preceding_word(text, start) in WETHER_ANIMAL_LEAD:
            return ""  # the animal, not the conjunction
        return "whether"
    subject_map = _SUBJECT_SENSITIVE_MISSPELLINGS.get(folded)
    if subject_map is not None:
        lead = _preceding_word(text, start)
        if lead not in subject_map and lead.endswith("ing") and len(lead) > 4:
            # A gerund subject takes a singular verb: "forgetting aint" ->
            # "forgetting is not". Measured, this is the only unhandled subject
            # left in the corpus, at 12 of the 13 remaining occurrences.
            return "is not"
        # No recognised subject means no correction. Guessing here writes the
        # wrong verb into the sentence, which is worse than leaving the word
        # alone for a reader to fix.
        return subject_map.get(_preceding_word(text, start), "")
    # SPELLING_CORRECTIONS is the shared map in writing_suggestions.py; the
    # bundled corpus is consulted last and never outranks it, because a flat
    # 24,690-entry lookup should not overrule a curated or measured answer.
    return SPELLING_CORRECTIONS.get(folded, "") or _bundled_misspellings().get(
        folded, ""
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


# Productive English word formation, so that ordinary compounds and prefixed
# words are recognised as English instead of charged as misspellings.
#
# The gap this closes was measured on 2026-08-27 over the twenty-essay corpus:
# `tradeoffs` was charged on nine of the ten worst essays, and the essay that
# failed the release gate outright was charged five times for `cyber`,
# `cyberinsurance`, `cybersecurity`, `multifactor` and `tradeoffs`, every one
# of them real English. Neither bundled list has them, and neither does any
# list that could replace them: the SymSpell 500,000-word list turns 15.89% of
# our own 24,691 bundled misspellings into "correct" words, against 0.69% for
# the list we ship, because it is a Google Books frequency dump carrying OCR
# typos rather than a curated lexicon; and LibreOffice's en_US is byte for byte
# the file already in this repository.
#
# So this derives the words instead of listing them. Listing them would be
# memorising the corpus, which is the rule that governs the 149 frozen
# sentences in polish_proofread.py, and grade_corpus.py already records an
# earlier session refusing exactly that.
# Productive English suffixes. A known stem carrying one of these is a
# real word: "passivation" is passive + ation, ordinary English that
# neither bundled list has. Same principle as the prefixes below -
# derive the word, never list it.
_COMPOUND_SUFFIXES = (
    "ation", "ations", "isation", "ization", "ising", "izing",
    "ised", "ized", "ment", "ments", "ness", "less", "able", "ible",
)

_COMPOUND_PREFIXES = (
    "cyber", "micro", "multi", "para", "bio", "geo", "eco", "nano", "tele",
    "pre", "post", "anti", "non", "semi", "sub", "super", "inter", "ultra",
    # Native English prefixes, every bit as productive as the Greek and
    # Latin ones above and left out for no stated reason. "outplanting"
    # is ordinary English that both bundled lists lack, and
    # RELEASE_ACCEPTANCE.md names it as a word the spelling term charges
    # wrongly. DERIVING it is exactly what this table is for; listing the
    # word itself would be memorising the corpus.
    "out", "over", "under", "up", "down", "off", "back", "cross",
    "counter", "self", "re", "un", "mis", "co",
)

# A compound joins content words. These are the words that never form one, and
# without this guard the corpus's own missing-space defects are accepted as
# English: `vendorsmay` is vendors + may and `canappear` is can + appear, so
# two real errors would be hidden to gain nothing.
_COMPOUND_FUNCTION_WORDS = frozenset(
    """may can will shall must might could would should do does did is are was
    were be been being am has have had at in on of to for by with from as and
    or but if so the a an this that these those it its he she they we you i him
    her them us me my your his their our not no yes up out who whom which what
    when where why how all any some such than then there here too very also
    just only""".split()
)

# Both halves must be common. Measured, not chosen: at no floor the rule turns
# 3.16% of the bundled misspellings into words, at 100,000 it is 1.33%, at
# 1,000,000 it is 0.86%, and at 5,000,000 it is 0.33% but stops recognising
# `tradeoffs` and `tiltmeters`. One million is the knee, and it lands beside
# the 0.69% the shipped list already produces.
#
# What the floor removes is the shape of a misspelling rather than a compound:
# the accepted junk was `absorb` + `sion`, `anal` + `ices`, `alum` + `nigh`,
# `eco` + `monica` - one common word joined to something rare or to a name.
_COMPOUND_MINIMUM_FREQUENCY = 1_000_000

# A misspelling is close to the word it should have been; a compound is not.
# `absorbant` is one edit from "absorbent" and `accessable` one from
# "accessible", and requiring two edits of separation took the rule from 10.72%
# to 3.16% before the frequency floor was applied at all.
_COMPOUND_MINIMUM_EDIT_SEPARATION = 2


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

    def recognises_compound(self, word: str) -> bool:
        """Whether a word the lists lack is still English by word formation.

        Answers the narrow question "is this a real word", not "what should it
        become", which is why it is separate from `suggestion`. A word already
        in the lists never reaches here; callers ask this only after `contains`
        has said no.

        Three guards, each measured against the 24,691 bundled misspellings
        rather than reasoned about, because every one of them was added after
        watching the rule accept something it should not have. See the
        constants above for the numbers.
        """
        self._ensure_loaded()
        if self._symspell is None or self._words is None:
            return False
        folded = _dictionary_form(word)
        if not folded or len(folded) < 6:
            return False

        # Close to a real word means it is a typo of that word, whatever else
        # it can be decomposed into.
        if self.has_close_candidate(folded):
            return False

        counts = getattr(self._symspell, "words", None) or {}

        def common(part: str) -> bool:
            return counts.get(part, 0) >= _COMPOUND_MINIMUM_FREQUENCY

        def real(part: str) -> bool:
            return part in self._words

        for prefix in _COMPOUND_PREFIXES:
            if not folded.startswith(prefix):
                continue
            rest = folded[len(prefix) :]
            if len(rest) > 2 and real(rest) and common(rest):
                return True

        for suffix in _COMPOUND_SUFFIXES:
            if not folded.endswith(suffix):
                continue
            stem = folded[: -len(suffix)]
            # The stem as written, and with a dropped final -e restored:
            # "passivation" is passive + ation with the e elided, which
            # is how English actually forms these.
            for candidate in (stem, stem + "e"):
                if len(candidate) > 3 and real(candidate) and common(candidate):
                    return True

        # Both parts at least four characters: shorter splits are how "acid" +
        # "dent" and "annal" + "isis" got in.
        for cut in range(4, len(folded) - 3):
            head, tail = folded[:cut], folded[cut:]
            if head in _COMPOUND_FUNCTION_WORDS or tail in _COMPOUND_FUNCTION_WORDS:
                continue
            if real(head) and real(tail) and common(head) and common(tail):
                return True
        return False

    def has_close_candidate(self, word: str) -> bool:
        """Whether a single-word repair exists at one edit.

        Separate from `suggestion` because that answers "what should this
        become", and returns None both when there is nothing and when there are
        two equally good answers. The run-together repair needs the other
        question - is there any close single word at all - so that a plain
        misspelling with a real repair is never rewritten as two words.
        """
        self._ensure_loaded()
        if self._symspell is None:
            return False
        try:
            from symspellpy import Verbosity

            return bool(
                self._symspell.lookup(
                    word.casefold(),
                    Verbosity.CLOSEST,
                    max_edit_distance=1,
                    include_unknown=False,
                )
            )
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def suggestion(
        self,
        word: str,
        *,
        tie_break: Callable[[tuple[str, ...]], str | None] | None = None,
    ) -> tuple[str, int, int] | None:
        """The repair for a misspelling, or None when nothing may be chosen.

        `tie_break` is offered the equally-good candidates when, and only when,
        this would otherwise refuse. It returns one of them or None, and None
        keeps the refusal exactly as it was. Passed in as a callable rather
        than held as an attribute so the dictionary keeps knowing nothing about
        the classifier or the ONNX runtime; the caller owns that dependency.
        """
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
        folded = word.casefold()
        ranked = sorted(
            values,
            key=lambda value: (
                int(value.distance),
                _typo_shape_rank(folded, str(value.term)),
                -max(0, int(value.count)),
                str(value.term),
            ),
        )
        best = ranked[0]
        # The word as the writer typed it decides which bar applies, so this
        # cannot use `folded` - casefolding is what threw the evidence away.
        if _is_ambiguous(folded, ranked, capitalised=word[:1].isupper()):
            if tie_break is None:
                return None
            # Only the candidates that actually tie are offered. Handing over
            # the whole ranked list would let a distant third word win on
            # topic alone, which is a different and much weaker claim than
            # separating two equally good repairs.
            tied = tuple(
                str(value.term)
                for value in ranked
                if int(value.distance) == int(best.distance)
                and _typo_shape_rank(folded, str(value.term))
                == _typo_shape_rank(folded, str(best.term))
            )
            chosen = tie_break(tied)
            if chosen is None:
                return None
            best = next(
                (value for value in ranked if str(value.term) == chosen), None
            )
            if best is None:
                return None
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
                    # utf-8-sig, not utf-8. The frequency file begins with a
                    # byte order mark, and plain utf-8 kept it attached to the
                    # first entry, so the most common word in English was
                    # stored as "﻿the" and could never be offered as a
                    # correction. "teh" came back as "tech".
                    encoding="utf-8-sig",
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


def _is_ambiguous(source: str, ranked: list, *, capitalised: bool = False) -> bool:
    """Whether two candidates are equally good repairs, so neither may be used.

    `capitalised` says the word wore a capital where the writer typed it, which
    makes it a probable name and raises the bar back to the old, stricter one.
    Lowering the ratio to 30 unfroze "Csco": it sits one edit from cisco at
    27.9M and cusco at 627K, a 44.5x lead that is a tie at 50x and decisive at
    30x, so the repair became "Cisco". Cusco is a city in Peru and THIS corpus
    has an essay set there. Choosing between two proper nouns by raw frequency
    is the one place frequency is worst, and the product must not rewrite a
    person's or a company's name.

    Caught by test_names_the_speller_cannot_place_are_still_frozen, which is why
    the fix is here and not in that test.

    When the best two share a distance and a typing shape, nothing left in the
    dictionary can separate them and only raw frequency remains - which is
    exactly the wrong tie-break, because the more common word is often not the
    one the writer meant. "cair" is care or chair; "esay" is essay or easy;
    "chroes" is chores or chromes. Frequency picked chair, easy and chromes.

    Refusing here leaves the misspelling in place for the model, which reads
    the whole sentence and can tell that a pet gets poor care, not poor chair.
    Substituting first is unrecoverable: the model never sees the original
    word, so a wrong guess is baked in before it is asked.

    One escape: frequency is a weak signal between neighbours but a decisive
    one across orders of magnitude. "the" outweighs "tech" by 250 times and
    "animal" outweighs "animus" by 300, and refusing those would leave obvious
    typos on screen. Only near-neighbours in frequency are treated as a real
    tie - "chair" leads "cairo" by nine times, and that is a guess.
    """
    if len(ranked) < 2:
        return False
    first, second = ranked[0], ranked[1]
    if int(first.distance) != int(second.distance):
        return False
    if _typo_shape_rank(source, str(first.term)) != _typo_shape_rank(
        source, str(second.term)
    ):
        return False
    ratio = _DOMINANT_FREQUENCY_RATIO_NAMED if capitalised else _DOMINANT_FREQUENCY_RATIO
    return max(0, int(first.count)) < ratio * max(1, int(second.count))


# How far ahead the best candidate must be before frequency alone is allowed
# to break a tie between two equally well-shaped repairs.
#
# 30 rather than 50 since 2026-08-29, measured: it took essay 10 from 96 to 98
# by resolving "stateon" to station, which leads staten 32x.
_DOMINANT_FREQUENCY_RATIO = 30

# The bar for a word the writer capitalised, which is probably a name. Held at
# the old 50 because the cost of being wrong is different in kind: a mangled
# common word is a typo a reader forgives, and a rewritten name is the product
# putting words in someone's mouth. Lowering this to 30 turned "Csco" into
# "Cisco" when "Cusco" was one edit away and equally real.
_DOMINANT_FREQUENCY_RATIO_NAMED = 50


def _typo_shape_rank(source: str, candidate: str) -> int:
    """Order equally close candidates by how people actually mistype.

    Frequency alone is a poor tie-break for a misspelling: at one edit from
    "esay" the dictionary holds both "essay" and the far more frequent "ebay",
    and it answered with the brand. Typing shape is the better signal.

    0 - a doubled letter collapsed to one ("pett" -> "pet")
    1 - a letter left out ("esay" -> "essay"), or two letters swapped
        ("wehre" -> "where"). Both are ordinary typing slips and neither is
        more likely than the other, so they share a rank and are separated
        below by frequency like any other tie. Ranking a transposition above a
        dropped letter was tried and turned "esay" into "easy".
    2 - anything else, which is where a substitution like "ebay" lands

    Before transpositions were recognised at all they fell into rank 2 and
    lost to whatever was more frequent: "wehre" became "were" instead of
    "where", and "chroes" became "chromes" instead of "chores".
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
    if _is_transposition(source, candidate):
        return 1
    return 2


def _is_transposition(source: str, candidate: str) -> bool:
    """Whether one adjacent swap turns `source` into `candidate`."""
    if len(source) != len(candidate):
        return False
    differing = [
        index
        for index, (left, right) in enumerate(zip(source, candidate))
        if left != right
    ]
    return (
        len(differing) == 2
        and differing[1] == differing[0] + 1
        and source[differing[0]] == candidate[differing[1]]
        and source[differing[1]] == candidate[differing[0]]
    )


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
        context_ranker=None,
    ) -> None:
        self.suggestion_engine = suggestion_engine or WritingSuggestionEngine()
        self.dictionary = dictionary or EnglishDictionary()
        # Imported here, not at module scope, so polish_spelling keeps its
        # existing import shape: the classifier pulls in numpy and the ONNX
        # runtime, and this module is imported by tools that only want the
        # word list. Pass a ranker in to override it, or False to switch the
        # context tie-break off entirely.
        if context_ranker is None:
            from .polish_classifier import ContextWordRanker

            context_ranker = ContextWordRanker()
        self.context_ranker = context_ranker

    def warm(self) -> bool:
        return self.dictionary.warm()

    def _context_tie_break(self, text: str, match):
        """A tie-break bound to one misspelling, or None when there is no ranker.

        The dictionary is given a word and nothing else, which is why it has to
        refuse a tie: it cannot see what the sentence is about. This side can,
        so it carves out the sentence holding the word and the paragraph
        holding the sentence and hands both to the ranker.
        """
        if not self.context_ranker:
            return None
        sentence_start, sentence_end = _sentence_bounds(text, match.start())
        sentence = text[sentence_start:sentence_end]
        # The paragraph WITHOUT the sentence being repaired. Leaving it in
        # makes every candidate look like a good fit, because the sentence is
        # most of what the comparison is measuring against itself.
        context = (text[:sentence_start] + " " + text[sentence_end:]).strip()

        def tie_break(candidates: tuple[str, ...]) -> str | None:
            return self.context_ranker.choose(
                sentence,
                match.start() - sentence_start,
                match.end() - sentence_start,
                candidates,
                context,
            )

        return tie_break

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
            measured = _measured_replacement(text, match.start(), folded)
            if measured:
                replacement = _match_case(word, measured)
                material = (
                    f"measured\0{match.start()}\0{match.end()}\0"
                    f"{word}\0{replacement}"
                )
                candidate_digest = hashlib.sha256(
                    material.encode("utf-8")
                ).hexdigest()[:16]
                suggestions.append(
                    WritingSuggestion(
                        suggestion_id=f"ws:{source_digest}:{candidate_digest}",
                        category=SuggestionCategory.SPELLING,
                        title="Correct spelling",
                        explanation=(
                            f'"{word}" is not an English word; the word meant '
                            f'here is "{replacement}".'
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
            repaired_case = _mangled_case_repair(word, self.dictionary)
            if repaired_case != word:
                material = (
                    f"mangled-case\0{match.start()}\0{match.end()}\0"
                    f"{word}\0{repaired_case}"
                )
                candidate_digest = hashlib.sha256(
                    material.encode("utf-8")
                ).hexdigest()[:16]
                suggestions.append(
                    WritingSuggestion(
                        suggestion_id=f"ws:{source_digest}:{candidate_digest}",
                        category=SuggestionCategory.SPELLING,
                        title="Correct capitalization",
                        explanation=(
                            f'"{word}" carries capitals in the middle of the '
                            f'word; the word meant here is "{repaired_case}".'
                        ),
                        start=match.start(),
                        end=match.end(),
                        original=word,
                        replacement=repaired_case,
                    )
                )
                continue
            if self.dictionary.contains(word):
                continue
            # Real English the bundled lists happen not to carry: compounds and
            # prefixed words like "cybersecurity", "stormwater", "tradeoffs".
            #
            # Not because the speller offers a wrong repair for them - measured
            # on 2026-08-27, all thirteen corpus compounds came back with no
            # suggestion at all. It is the fall-through that does the damage:
            # `suggestion` returning None hands the word to the run-together
            # repair, which splits anything whose halves are both real words.
            # Eleven words in a 120,000-word scan were torn in half that way,
            # mostly names - "kneeland" to "kneel and", "groveland" to "grovel
            # and". Replacing a writer's word with two wrong ones is worse than
            # leaving it alone.
            #
            # This sits after `contains` and before `suggestion` so it can only
            # ever suppress a correction, never propose one. The run-together
            # repair below still fires for "vendorsmay" and "canappear",
            # because a compound may not contain a function word - that guard
            # exists precisely so this check cannot swallow a missing space.
            if self.dictionary.recognises_compound(word):
                continue
            value = self.dictionary.suggestion(
                word, tie_break=self._context_tie_break(text, match)
            )
            if value is None:
                separated = _run_together_repair(word, self.dictionary)
                if separated:
                    replacement = _match_case(word, separated)
                    material = (
                        f"runtogether\0{match.start()}\0{match.end()}\0"
                        f"{word}\0{replacement}"
                    )
                    candidate_digest = hashlib.sha256(
                        material.encode("utf-8")
                    ).hexdigest()[:16]
                    suggestions.append(
                        WritingSuggestion(
                            suggestion_id=f"ws:{source_digest}:{candidate_digest}",
                            category=SuggestionCategory.SPELLING,
                            title="Separate run-together words",
                            explanation=(
                                f'"{word}" is two words with the space missing; '
                                f'the words here are "{replacement}".'
                            ),
                            start=match.start(),
                            end=match.end(),
                            original=word,
                            replacement=replacement,
                        )
                    )
                continue
            replacement, distance, frequency = value
            if distance != 1 or replacement.casefold() == word.casefold():
                continue
            if frequency < 20:
                continue
            # A word carrying a stray apostrophe may only have the apostrophe
            # taken out. Letting it be respelled as well would let one typo
            # authorise a second, larger guess on the same word.
            #
            # Defensive, and say so rather than let the next reader assume it
            # is load-bearing: on today's dictionary it cannot be triggered,
            # because the distance-1 requirement above already means the single
            # edit on an apostrophe word IS the apostrophe. Every attempt to
            # build a case that reaches here returned no suggestion at all. It
            # stays because a dictionary change could make it reachable, and
            # there is no test, because a test that cannot fail is decoration.
            if (
                "'" in word
                and word.replace("'", "").casefold() != replacement.casefold()
            ):
                continue
            if not _capitalized_repair_is_safe(word, replacement):
                continue
            if _case_is_mangled(word):
                # The source capitals were damage, so they cannot be copied on
                # to the repair. SymSpell returns its own casing - "Repair" for
                # RePahIr - which leaves a capital mid-sentence. Lowering it
                # unconditionally was tried first and produced "repair work is
                # needed here." at the start of a sentence: nothing downstream
                # puts that capital back on this path.
                replacement = (
                    replacement.capitalize()
                    if _is_sentence_start(text, match.start())
                    else replacement.lower()
                )
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


def _affix_condition_matches(condition: str, word: str, *, suffix: bool) -> bool:
    """Whether a Hunspell affix condition matches the end (or start) of a word.

    A condition is a run of single characters and bracketed classes, "[aeiou]"
    or negated "[^aeiou]", and "." matches anything.
    """
    if not condition or condition == ".":
        return True
    tokens: list[tuple[bool, set[str]]] = []
    index = 0
    while index < len(condition):
        if condition[index] == "[":
            close = condition.find("]", index)
            if close < 0:
                return False
            group = condition[index + 1 : close]
            negated = group.startswith("^")
            tokens.append((negated, set(group[1:] if negated else group)))
            index = close + 1
        else:
            tokens.append((False, {condition[index]}))
            index += 1
    if len(tokens) > len(word):
        return False
    segment = word[-len(tokens) :] if suffix else word[: len(tokens)]
    for character, (negated, allowed) in zip(segment, tokens):
        if (character in allowed) == negated:
            return False
    return True


def _load_affix_rules(path: Path) -> dict[str, list[tuple[str, str, str, str]]]:
    """Read `en_US.aff` into {flag: [(kind, strip, add, condition), ...]}."""
    rules: dict[str, list[tuple[str, str, str, str]]] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return rules
    for line in lines:
        parts = line.split()
        # A rule line is "SFX flag strip add condition"; the header that
        # precedes each block is "SFX flag cross_product count" and is skipped
        # by requiring five fields.
        if len(parts) < 5 or parts[0] not in {"SFX", "PFX"}:
            continue
        kind, flag, strip, add = parts[0], parts[1], parts[2], parts[3]
        condition = parts[4]
        if add == "0":
            add = ""
        if strip == "0":
            strip = ""
        # Morphological fields can follow the condition; they are not needed.
        rules.setdefault(flag, []).append((kind, strip, add.split("/", 1)[0], condition))
    return rules


def _apply_affix(word: str, kind: str, strip: str, add: str, condition: str) -> str:
    if kind == "SFX":
        if not _affix_condition_matches(condition, word, suffix=True):
            return ""
        if strip and not word.endswith(strip):
            return ""
        return (word[: len(word) - len(strip)] if strip else word) + add
    if not _affix_condition_matches(condition, word, suffix=False):
        return ""
    if strip and not word.startswith(strip):
        return ""
    return add + (word[len(strip) :] if strip else word)


def _load_hunspell_words(path: Path) -> set[str]:
    """Every form the dictionary describes, not just the entries it lists.

    A Hunspell dictionary is a word list plus an affix file, and the flags
    after each entry's slash say which affix rules it takes. Reading only the
    list and discarding the flags left 48,262 base forms: the speller knew
    "cat" but not "cats", "change" but not "changes", and did not know "after"
    at all. Every spelling decision in the product inherited that - whether a
    word is a typo, whether a repair is trustworthy, and the residual-error
    count in the quality scorer, which was mostly plurals.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError("Bundled Hunspell dictionary is empty.")
    rules = _load_affix_rules(path.with_suffix(".aff"))
    words: set[str] = set()
    for line in lines[1:]:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        # Hunspell escapes literal slashes as "\/"; the first unescaped slash
        # starts the affix flags.
        value = raw.replace("\\/", "\0")
        head, _, flags = value.partition("/")
        stem = head.replace("\0", "/").split("\t", 1)[0].strip()
        folded = _dictionary_form(stem)
        if not folded:
            continue
        words.add(folded)
        if not flags or not rules:
            continue
        flags = flags.split("\t", 1)[0].strip()
        suffixed: list[str] = []
        for flag in flags:
            for kind, strip, add, condition in rules.get(flag, ()):
                form = _apply_affix(folded, kind, strip, add, condition)
                if not form:
                    continue
                derived = _dictionary_form(form)
                if derived:
                    words.add(derived)
                    if kind == "SFX":
                        suffixed.append(derived)
        # A prefix may also attach to a form a suffix already made
        # ("re" + "checked"), which is what Hunspell's cross-product allows.
        for flag in flags:
            for kind, strip, add, condition in rules.get(flag, ()):
                if kind != "PFX":
                    continue
                for base in suffixed:
                    form = _apply_affix(base, kind, strip, add, condition)
                    derived = _dictionary_form(form) if form else ""
                    if derived:
                        words.add(derived)
    if len(words) < 10_000:
        raise ValueError("Bundled Hunspell dictionary is incomplete.")
    return words


def _dictionary_form(value: str) -> str:
    return unicodedata.normalize("NFC", str(value)).strip("'").casefold()


# The endings an apostrophe legitimately makes: a possessive, or one of the
# contractions English actually has. Anything else in the middle of a word is a
# stray keystroke.
_CONTRACTION_ENDINGS = frozenset({"s", "t", "d", "m", "ll", "re", "ve", "clock"})


def _apostrophe_is_doing_its_job(word: str) -> bool:
    """Whether the apostrophe forms a possessive or a contraction."""
    head, _, tail = word.rpartition("'")
    return not head or tail.casefold() in _CONTRACTION_ENDINGS


# Alternating-case damage flips on almost every letter; a real mixed-case word
# flips two or three times wherever it happens to be long. Counting flips alone
# put JavaScript and McDonald on three and lowercased both, so the test is the
# RATIO of flips to letters. Measured on the words that matter:
#
#   SuSpEcTeD      8 flips / 8  = 1.00   damage
#   ThIs           3 / 3        = 1.00   damage
#   ArChAeOlOgIsTs 13 / 13      = 1.00   damage
#   eBay           2 / 3        = 0.67   real
#   MacBook        3 / 6        = 0.50   real
#   JavaScript     3 / 9        = 0.33   real
#
# 0.7 sits in the gap. eBay is the closest real word to the line and it is the
# reason this is not set lower.
_MANGLED_CASE_RATIO = 0.7


def _case_changes(word: str) -> int:
    """How many times the word switches between upper and lower case."""
    letters = [c for c in word if c.isalpha()]
    return sum(
        1
        for first, second in zip(letters, letters[1:])
        if first.isupper() != second.isupper()
    )


def _case_is_mangled(word: str) -> bool:
    """Whether the word's capitals are damage rather than a real spelling."""
    if len(word) <= 3 or "'" in word or word.isupper() or word.islower():
        return False
    if word.istitle():
        return False
    letters = max(1, len([c for c in word if c.isalpha()]) - 1)
    return _case_changes(word) / letters >= _MANGLED_CASE_RATIO


def _mangled_case_repair(word: str, dictionary) -> str:
    """Lower-case a word whose capitals are damage, or return it unchanged.

    The dictionary cannot help decide this. `_dictionary_form` casefolds, and
    the word set holds only casefolded entries, so `contains("SuSpEcTeD")` and
    `contains("iPhone")` are both True and indistinguishable. That is exactly
    why these survived: the scan asks `contains` first and skips anything it
    recognises, so a word wearing mangled capitals looked correct and was left
    alone. Essay 17 of error-riddled-20-docx finished with SuSpEcTeD, OnE and
    ArChAeOlOgIsTs still in it while the model fixed AnCiEnT and ThIs in the
    same document - it had to notice, and it noticed inconsistently.

    So the shape of the capitals decides instead, and only for words the
    dictionary already knows when folded. A word it does not know is left for
    the ordinary spelling repair below, which is better at it.
    """
    if not _case_is_mangled(word):
        return word
    lowered = word.lower()
    if not dictionary.contains(lowered):
        return word
    return lowered


def _should_skip_word(text: str, start: int, word: str) -> bool:
    if len(word) <= 3 or len(word) > 32:
        return True
    if any(character.isdigit() for character in word):
        return True
    if word.isupper() or "-" in word:
        return True
    # An apostrophe used to skip the word outright, and that cost every repair
    # of a stray one: "critics may foc'us on serious failures" and "review
    # failures as i'nformation about the system" both survived a whole essay,
    # eleven of them across the 500-percent corpus, while the dictionary held
    # the answer the entire time - it ranks "focus" one edit away with a
    # frequency of 65 million.
    #
    # The skip was buying nothing, because the words it was there to protect
    # never reach this far: "dog's", "don't", "it's" and "o'clock" are all in
    # the dictionary, so `contains` returns before any repair is considered.
    # What is skipped now is the shape rather than the character, so a
    # possessive of a word the dictionary does not know - "microgrid's" - is
    # still left alone rather than flattened into a plural.
    if "'" in word and _apostrophe_is_doing_its_job(word):
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
    # Alternating capitals are damage, not a name. Nobody is called RePahIr.
    # The guard above exists because "Malik" and "Mali" are both real, and
    # guessing between them renames someone - but that reasoning only holds for
    # a word whose capitalisation could be deliberate. RePahIr reached here
    # with the answer already found: symspell ranked "Repair" one edit away at
    # a frequency of 49 million, and this refused it, so the word survived a
    # whole Word run of essay 17.
    if _case_is_mangled(word):
        return True
    return _typo_shape_rank(word.casefold(), replacement.casefold()) <= 1


def _is_sentence_start(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    return not prefix or prefix[-1:] in ".!?\n"


# Short closed-class words that get glued onto the word before them. This is a
# dictation product first, and "vendors may" arriving as "vendorsmay" is what
# running speech into text does; the same shape turns up in fast typing.
#
# A closed class on purpose, rather than "any two dictionary words". Splitting
# on any pair wrecks ordinary misspellings: measured over the 24,690 bundled
# misspellings, an any-pair rule with four-letter halves rewrote 786 of them,
# turning "accessable" into "access able" and "acheives" into "ache ives".
_RUN_TOGETHER_TAILS = frozenset(
    {
        "may", "not", "and", "the", "but", "for", "are", "was", "will", "can",
        "has", "have", "that", "this", "with", "from", "they", "their", "when",
        "which", "would", "should", "could", "into", "than", "then", "also",
        "only", "more", "most",
        # Prepositions and pronouns. Everything above is a modal, a
        # determiner or a conjunction, which is why "vendorsmay" split
        # and "closelyat" did not - an arbitrary gap rather than a
        # decision anyone took.
        "at", "in", "on", "of", "to", "by", "as", "or", "up", "out",
        "is", "be", "it", "its", "his", "her", "our", "your",
    }
)
# The head has to be a real word of at least this length. Measured: at 4 the
# rule also rewrote "gallare", "hearwith", "origonly" and "beththe"; at 5 the
# only casualty left in 24,690 known misspellings is "questionare", which
# becomes "question are" instead of "questionnaire". One in twenty-four
# thousand, against a class of error this product's own input method creates.
_RUN_TOGETHER_HEAD = 4


def _run_together_repair(word: str, dictionary: EnglishDictionary) -> str:
    """Two words with the space missing, or "" when this is something else.

    Only reached when the speller has already declined the word, and it
    declines again the moment a single-word repair is available at one edit.
    That guard is what keeps "bandwith" as bandwidth rather than "band with",
    and it took the false-accept rate from 0.057 percent to 0.004 percent.
    """
    folded = _dictionary_form(word)
    if not folded or len(folded) < _RUN_TOGETHER_HEAD + 3:
        return ""
    if dictionary.has_close_candidate(folded):
        return ""
    for tail in _RUN_TOGETHER_TAILS:
        if not folded.endswith(tail):
            continue
        head = folded[: -len(tail)]
        if len(head) >= _RUN_TOGETHER_HEAD and dictionary.contains(head):
            return f"{head} {tail}"
    # The same missing space with the function word FIRST. The loop above only
    # ever looked for it at the end, so "vendorsmay" was repaired and
    # "canappear" - which the comment beside the compound guard names as a case
    # this rule handles - was not. Essay 12 of the error-riddled corpus carries
    # it, and it survived every other layer.
    #
    # Symmetric guards, for the reason the originals exist: the function word
    # comes from the same closed list, the other half must be a real word of at
    # least _RUN_TOGETHER_HEAD characters, and has_close_candidate above has
    # already refused anything a single edit could repair as one word.
    #
    # Measured the same way _RUN_TOGETHER_HEAD was, over all 24,690 bundled
    # misspellings: four false accepts, of which "questionare" was already a
    # casualty of the loop above. The three this direction adds are
    # "canelation" to "can elation", "forwould" to "for would" and
    # "theirselves" to "their selves". That is 0.012 percent, against a class of
    # error this product's own input method creates.
    for lead in _RUN_TOGETHER_TAILS:
        if not folded.startswith(lead):
            continue
        rest = folded[len(lead) :]
        if len(rest) >= _RUN_TOGETHER_HEAD and dictionary.contains(rest):
            return f"{lead} {rest}"
    return ""


def _sentence_bounds(text: str, position: int) -> tuple[int, int]:
    """The sentence around a position, as (start, end) offsets into `text`.

    Deliberately crude. It splits on terminal punctuation and newlines, so an
    abbreviation ends a sentence early. That costs the context tie-break a
    little topic and nothing else, because a short or thin context makes the
    ranker decline rather than guess.
    """
    start = 0
    for index in range(position - 1, -1, -1):
        if text[index] in ".!?\n":
            start = index + 1
            break
    end = len(text)
    for index in range(position, len(text)):
        if text[index] in ".!?\n":
            end = index + 1
            break
    return start, end


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
