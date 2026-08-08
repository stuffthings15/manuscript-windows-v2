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
# Measured across the twenty-essay corpus: after the rules and the bundled
# dictionary have run, 106 distinct words remain unresolved, and these account
# for nearly all of them - each appears in 13 to 20 of the 20 essays. The
# speller declines them for good reasons that will not change: "cair" is one
# edit from "chair" and two from "care", so distance ranking picks the
# furniture; "edjucashunal" is six edits from "educational"; "equipments" and
# "worser" are real-looking morphology rather than typos.
#
# Every entry is a word with exactly one sensible English reading. Words whose
# correction depends on the sentence around them are NOT here - see
# _SUBJECT_SENSITIVE_MISSPELLINGS below.
_MEASURED_MISSPELLINGS = {
    "agin": "again",
    "atenshun": "attention",
    "avalible": "available",
    "becuz": "because",
    "cair": "care",
    "edjucashunal": "educational",
    "equipments": "equipment",
    "learnt": "learned",  # Americanise, per the corpus's US setting
    # Measured: the speller turns "skool" into "skol", a Nordic drinking toast,
    # and "lernt" into the British "learnt". Both are one pass, so nothing gets
    # a second chance to repair them - they must be right the first time.
    "skool": "school",
    "lernt": "learned",
    "oner": "owner",
    "oners": "owners",
    "probly": "probably",
    "skol": "school",
    "sumtimes": "sometimes",
    "worser": "worse",
}

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

# The standard commonly-misspelled-English set (the pool the English Club 100,
# Mondly 100 and the Wikipedia list all draw from). These are consulted only
# after _MEASURED_MISSPELLINGS and the context-sensitive maps above, so a
# general entry can never override a rule that reads the sentence.
#
# Every pair was validated against the bundled dictionary before it shipped:
# the misspelling must be absent from it and the correction present. That check
# rejected three entries which would have corrupted correct English -
# "defiantly" (defiant + ly), "florescent" (flowering) and "therefor" (for
# that) are all real words that a naive list maps onto something else.
_COMMON_MISSPELLINGS = {
    "accomodate": "accommodate",
    "accross": "across",
    "acheive": "achieve",
    "acheived": "achieved",
    "acommodate": "accommodate",
    "adress": "address",
    "adressed": "addressed",
    "allready": "already",
    "alot": "a lot",
    "apparant": "apparent",
    "aquire": "acquire",
    "aquired": "acquired",
    "arguement": "argument",
    "arguemnts": "arguments",
    "athiest": "atheist",
    "becuase": "because",
    "begining": "beginning",
    "beleif": "belief",
    "beleive": "believe",
    "beleived": "believed",
    "belive": "believe",
    "benifit": "benefit",
    "benifits": "benefits",
    "buisness": "business",
    "calender": "calendar",
    "catagory": "category",
    "cemetary": "cemetery",
    "changable": "changeable",
    "cheif": "chief",
    "collegue": "colleague",
    "collegues": "colleagues",
    "comming": "coming",
    "commited": "committed",
    "commitee": "committee",
    "completly": "completely",
    "concensus": "consensus",
    "concious": "conscious",
    "conciousness": "consciousness",
    "curiousity": "curiosity",
    "definately": "definitely",
    "definatly": "definitely",
    "dependant": "dependent",
    "desparate": "desperate",
    "diffrence": "difference",
    "dilemna": "dilemma",
    "dissapear": "disappear",
    "dissapeared": "disappeared",
    "dissapoint": "disappoint",
    "dissapointed": "disappointed",
    "embarass": "embarrass",
    "embarassed": "embarrassed",
    "embarassing": "embarrassing",
    "enviroment": "environment",
    "enviromental": "environmental",
    "equiptment": "equipment",
    "excede": "exceed",
    "excellant": "excellent",
    "existance": "existence",
    "experiance": "experience",
    "experianced": "experienced",
    "explaination": "explanation",
    "familar": "familiar",
    "fasinating": "fascinating",
    "finaly": "finally",
    "foriegn": "foreign",
    "fourty": "forty",
    "freind": "friend",
    "freinds": "friends",
    "fullfil": "fulfill",
    "garantee": "guarantee",
    "gaurd": "guard",
    "goverment": "government",
    "grammer": "grammar",
    "harrass": "harass",
    "harrassment": "harassment",
    "heirarchy": "hierarchy",
    "hieght": "height",
    "humourous": "humorous",
    "hygeine": "hygiene",
    "immediatly": "immediately",
    "independant": "independent",
    "indispensible": "indispensable",
    "inteligent": "intelligent",
    "interupt": "interrupt",
    "intresting": "interesting",
    "irresistable": "irresistible",
    "knowlege": "knowledge",
    "labratory": "laboratory",
    "liason": "liaison",
    "libary": "library",
    "lisence": "license",
    "maintainance": "maintenance",
    "maintenence": "maintenance",
    "managment": "management",
    "medeval": "medieval",
    "millenium": "millennium",
    "miniture": "miniature",
    "mischevious": "mischievous",
    "mispell": "misspell",
    "neccessary": "necessary",
    "necessery": "necessary",
    "neice": "niece",
    "nieghbor": "neighbor",
    "noticable": "noticeable",
    "occassion": "occasion",
    "occassionally": "occasionally",
    "occurance": "occurrence",
    "occured": "occurred",
    "occuring": "occurring",
    "ommision": "omission",
    "oppurtunity": "opportunity",
    "paralel": "parallel",
    "parliment": "parliament",
    "particurly": "particularly",
    "pasttime": "pastime",
    "perseverence": "perseverance",
    "personel": "personnel",
    "persuit": "pursuit",
    "phenomenom": "phenomenon",
    "playwrite": "playwright",
    "posession": "possession",
    "possable": "possible",
    "potatoe": "potato",
    "practise": "practice",
    "preceed": "precede",
    "prefered": "preferred",
    "prejudise": "prejudice",
    "presance": "presence",
    "priviledge": "privilege",
    "probaly": "probably",
    "proffesional": "professional",
    "pronounciation": "pronunciation",
    "publically": "publicly",
    "questionaire": "questionnaire",
    "readible": "readable",
    "realy": "really",
    "recieve": "receive",
    "recieved": "received",
    "recomend": "recommend",
    "recomended": "recommended",
    "refered": "referred",
    "refering": "referring",
    "relevent": "relevant",
    "religous": "religious",
    "remeber": "remember",
    "repitition": "repetition",
    "restaraunt": "restaurant",
    "rythm": "rhythm",
    "secratary": "secretary",
    "seperate": "separate",
    "seperated": "separated",
    "seperately": "separately",
    "sieze": "seize",
    "similiar": "similar",
    "sincerly": "sincerely",
    "speach": "speech",
    "stoping": "stopping",
    "strenght": "strength",
    "succesful": "successful",
    "successfull": "successful",
    "supercede": "supersede",
    "suprise": "surprise",
    "suprised": "surprised",
    "temperture": "temperature",
    "tendancy": "tendency",
    "threshhold": "threshold",
    "tommorow": "tomorrow",
    "tommorrow": "tomorrow",
    "tounge": "tongue",
    "truely": "truly",
    "twelth": "twelfth",
    "tyrany": "tyranny",
    "underate": "underrate",
    "unecessary": "unnecessary",
    "unfortunatly": "unfortunately",
    "untill": "until",
    "upholstry": "upholstery",
    "usualy": "usually",
    "vaccuum": "vacuum",
    "vegatarian": "vegetarian",
    "vehical": "vehicle",
    "visable": "visible",
    "wellcome": "welcome",
    "wich": "which",
    "wief": "wife",
    "wierd": "weird",
    "withold": "withhold",
    "writen": "written",
    "writting": "writing",
    "yeild": "yield",
}

def _preceding_word(text: str, start: int) -> str:
    lead = re.findall(r"([A-Za-z']+)\W*$", text[:start])
    return lead[0].casefold() if lead else ""


# The conjunction "whether" never takes an article, number or possessive; the
# castrated ram almost always does. Same discriminator the LLM path already
# uses, so both paths agree rather than fighting each other.
_WETHER_ANIMAL_LEAD = frozenset(
    {"a", "an", "the", "this", "that", "one", "two", "three", "each", "every",
     "my", "our", "his", "her", "their", "its", "young", "castrated"}
)


def _measured_replacement(text: str, start: int, folded: str) -> str:
    """The correction for a word the speller cannot resolve, or "" for none."""
    if folded == "wether":
        if _preceding_word(text, start) in _WETHER_ANIMAL_LEAD:
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
    measured = _MEASURED_MISSPELLINGS.get(folded, "")
    return measured or _COMMON_MISSPELLINGS.get(folded, "")


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
        if _is_ambiguous(folded, ranked):
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


def _is_ambiguous(source: str, ranked: list) -> bool:
    """Whether two candidates are equally good repairs, so neither may be used.

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
    return max(0, int(first.count)) < _DOMINANT_FREQUENCY_RATIO * max(
        1, int(second.count)
    )


# How far ahead the best candidate must be before frequency alone is allowed
# to break a tie between two equally well-shaped repairs.
_DOMINANT_FREQUENCY_RATIO = 50


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
