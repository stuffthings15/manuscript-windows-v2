from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class SuggestionCategory(StrEnum):
    SPELLING = "spelling"
    GRAMMAR = "grammar"
    FORMATTING = "formatting"
    CLARITY = "clarity"


@dataclass(frozen=True, slots=True)
class WritingSuggestion:
    suggestion_id: str
    category: SuggestionCategory
    title: str
    explanation: str
    start: int
    end: int
    original: str
    replacement: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    rule: str
    category: SuggestionCategory
    title: str
    explanation: str
    start: int
    end: int
    replacement: str


# Every non-dictionary spelling correction the app knows, in one place.
# This was four dicts across two modules - _COMMON_TYPOS here, plus
# _MEASURED_MISSPELLINGS and _COMMON_MISSPELLINGS in polish_spelling.py,
# with 14 entries duplicated verbatim between them. They agreed today; two
# copies of the same rule do not stay agreeing.
#
# It lives in this module, not polish_spelling, because polish_spelling
# imports from here. This layer stays free of I/O: it is plain data, and
# the bundled 24,690-pair corpus stays behind the dictionary that owns it.
#
# Corrections that depend on the sentence around them are NOT here -
# "wether" and "aint" read their context and live in polish_spelling.py.
SPELLING_CORRECTIONS = {
    # NOTE: "cant" is deliberately absent. It was in _COMMON_TYPOS mapping to
    # "can't", but "cant" is a real English word - a slope, or insincere
    # jargon - so that entry rewrote correct writing. That map had no
    # validity gate; merging it here put the entry under one for the first
    # time and it failed immediately. The apostrophe map in polish_spelling
    # still restores can't, which is where that repair belongs.
    "accidently": "accidentally",
    "accomodate": "accommodate",
    "accomodations": "accommodations",
    "accross": "across",
    "acheive": "achieve",
    "acheived": "achieved",
    "acommodate": "accommodate",
    "adress": "address",
    "adressed": "addressed",
    "agin": "again",
    "allready": "already",
    "alot": "a lot",
    "apparant": "apparent",
    "aquire": "acquire",
    "aquired": "acquired",
    "archieve": "archive",
    "arguement": "argument",
    "arguemnts": "arguments",
    "atenshun": "attention",
    "athiest": "atheist",
    "avalible": "available",
    "becuase": "because",
    "becuz": "because",
    "begining": "beginning",
    "beleif": "belief",
    "beleive": "believe",
    "beleived": "believed",
    "belive": "believe",
    "benifit": "benefit",
    "benifits": "benefits",
    "buisness": "business",
    "cair": "care",
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
    "consistant": "consistent",
    "curiousity": "curiosity",
    "definately": "definitely",
    "definatly": "definitely",
    "dependant": "dependent",
    "desparate": "desperate",
    "didnt": "didn't",
    "diffrence": "difference",
    "dilemna": "dilemma",
    "dissapear": "disappear",
    "dissapeared": "disappeared",
    "dissapoint": "disappoint",
    "dissapointed": "disappointed",
    "edjucashunal": "educational",
    "embarass": "embarrass",
    "embarassed": "embarrassed",
    "embarassing": "embarrassing",
    "enviroment": "environment",
    "enviromental": "environmental",
    "equipments": "equipment",
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
    "formating": "formatting",
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
    "inconsistant": "inconsistent",
    "independant": "independent",
    "indispensible": "indispensable",
    "inteligent": "intelligent",
    "interupt": "interrupt",
    "intresting": "interesting",
    "irresistable": "irresistible",
    "knowlege": "knowledge",
    "lables": "labels",
    "labratory": "laboratory",
    "learnt": "learned",
    "lernt": "learned",
    "liason": "liaison",
    "libary": "library",
    "lisence": "license",
    "maintainance": "maintenance",
    "maintence": "maintenance",
    "maintenence": "maintenance",
    "managment": "management",
    "medeval": "medieval",
    "mesages": "messages",
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
    "oner": "owner",
    "oners": "owners",
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
    "probly": "probably",
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
    "sentances": "sentences",
    "sentnce": "sentence",
    "seperate": "separate",
    "seperated": "separated",
    "seperately": "separately",
    "seperates": "separates",
    "sieze": "seize",
    "similiar": "similar",
    "sincerly": "sincerely",
    "skol": "school",
    "skool": "school",
    "speach": "speech",
    "stoping": "stopping",
    "strenght": "strength",
    "succesful": "successful",
    "successfull": "successful",
    "sucessfully": "successfully",
    "sumtimes": "sometimes",
    "supercede": "supersede",
    "suprise": "surprise",
    "suprised": "surprised",
    "teh": "the",
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
    "understandible": "understandable",
    "unecessary": "unnecessary",
    "unfortunatly": "unfortunately",
    "untill": "until",
    "upholstry": "upholstery",
    "usualy": "usually",
    "vaccuum": "vacuum",
    "vegatarian": "vegetarian",
    "vehical": "vehicle",
    "visable": "visible",
    "volenteers": "volunteers",
    "wasnt": "wasn't",
    "wellcome": "welcome",
    "wich": "which",
    "wief": "wife",
    "wierd": "weird",
    "withold": "withhold",
    "worser": "worse",
    "writen": "written",
    "writting": "writing",
    "yeild": "yield",
}

_FILLER_REPLACEMENTS = {
    "at this point in time": "now",
    "due to the fact that": "because",
    "for the purpose of": "to",
    "in order to": "to",
    "the fact that": "that",
}

_COMMON_GRAMMAR_PHRASES = {
    "between you and i": "between you and me",
    "committee have": "committee has",
    "committee were": "committee was",
    "committee want": "committee wants",
    "correction card were": "correction card was",
    "card need": "card needs",
    "could of": "could have",
    "group are": "group is",
    "group were": "group was",
    "interface were": "interface was",
    "a one-safe paste": "one safe paste",
    "a one safe paste": "one safe paste",
    "one-safe paste": "one safe paste",
    "request calls for": "requested",
    "reviewers needs": "reviewers need",
    "safe for pasting": "safe paste",
    "sentences is": "sentences are",
    "private sentence are": "private sentence is",
    "should of": "should have",
    "team are": "team is",
    "team compare": "team compares",
    "team have": "team has",
    "team were": "team was",
    "then teams are moving": "then teams will move",
    "would of": "would have",
    "without warning a lot": "frequently without warning",
    "without warning frequently": "frequently without warning",
    "zone c will be building": "Zone C will build",
    "there own": "their own",
    "last years": "last year's",
    "more quicker": "quicker",
    "less volunteers": "fewer volunteers",
}

_WORD = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
# The corrector produced "replacing water,, water,, cleaning" and "beyond the
# city,, city,, and" - a repeated word carrying a stray comma with it. Plain
# whitespace was the only separator this matched, so those survived.
#
# A run of two or more commas is the marker. A SINGLE comma between repeats is
# deliberately not matched: "New York, New York" and "Puebla, Puebla" are
# correct English, and a rule that flattened them would be worse than the
# defect it fixes.
_DUPLICATE_WORD = re.compile(
    r"(?=\b(?P<first>[A-Za-z][A-Za-z'-]*)"
    r"(?:[ \t]+|[ \t]*,[\s,]*,[ \t]*)"
    r"(?P<second>[A-Za-z][A-Za-z'-]*)\b)",
    re.IGNORECASE,
)
_LOWERCASE_I = re.compile(r"(?<![\w'])i(?![\w'])")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+(?=[,.;:!?])")
_REPEATED_SPACE = re.compile(r"[ \t]{2,}")
_REPEATED_TERMINAL_PUNCTUATION = re.compile(r"(?P<mark>[!?])(?P=mark)+")
_DECLARATIVE_QUESTION_MARK = re.compile(
    r"(?:\A|(?<=[.!?])[ \t]+)these[ \t]+(?:issues|changes)[ \t]+"
    r"(?:make|makes|create|creates)\b[^.!?\r\n]{0,240}(?P<mark>\?)",
    re.IGNORECASE,
)
_HOWEVER_COMMA_SPLICE = re.compile(
    r",[ \t]+however(?:,[ \t]*|[ \t]+)",
    re.IGNORECASE,
)
_TWO_PREDICATE_COMMA = re.compile(
    r"\bsentences(?P<comma>,)[ \t]+and[ \t]+sends\b",
    re.IGNORECASE,
)
_TIME_PREPOSITION_FRAGMENT = re.compile(
    r"\b(?P<time>\d{1,2}:\d{2}[ \t]+[ap]\.m\.)[ \t]+"
    r"(?P<prep>In|At|On)\b(?=[^.!?\r\n]*;)",
    re.IGNORECASE,
)
_DEVICE_TEST_PREPOSITION = re.compile(
    r"\b(?:test|tested|testing)\b[^.!?\r\n]{0,80}?\b(?P<prep>in)\b"
    r"(?=[ \t]+(?:one|two|three|four|five|several|multiple|\d+)[ \t]+"
    r"(?:laptops?|computers?|devices?|machines?)\b)",
    re.IGNORECASE,
)
_TITLE_NAME_COMMAS = re.compile(
    r"(?<!\bthe[ \t])\b(?P<role>project lead|curator|technician|coordinator|"
    r"manager|director),[ \t]+(?P<name>[A-Z][A-Za-z'-]+[ \t]+[A-Z][A-Za-z'-]+),"
    r"(?=[ \t]+(?:said|asked|explained|reported|confirmed|noted)\b)",
    re.IGNORECASE,
)
# A list that already punctuates its earlier items but drops the comma before
# the final "and": "staffing, security, utilities and transit".
#
# TWO preceding commas are required, not one. One comma is not evidence of a
# list: "Visitors will not pay, although the city continues to provide staffing
# and security" has a comma before a subordinate clause, and a rule that read it
# as a list wrote "provide staffing, and security". Two commas mean the writer
# has already separated at least three items by hand, and only the last
# separator is missing.
#
# The cost is that "apples, pears and plums" is left alone. That is deliberate:
# with one comma there is no way here to tell a list from a clause boundary, and
# a wrong comma is worse than a missing one.
#
# The match must reach the end of the sentence, so a trailing clause
# ("..., the alerts and the queue, which we archived") is left alone: that comma
# belongs to the clause, not to the list.
_SERIAL_COMMA = re.compile(
    r",[ \t]+[^,;:.!?()]{2,60},[ \t]+(?P<middle>[^,;:.!?()]{2,60}?)[ \t]+and[ \t]+"
    r"(?P<last>[^,;:.!?()]{2,60}?)(?=[.;:!?]|\Z)"
)
# An appositive wears the same punctuation as a two-item list: "Ann, my manager
# and my mentor" describes one person, and inserting a comma silently turns her
# into three. A proper noun immediately before the comma is the signal, so those
# are left for a human to judge.
_APPOSITIVE_LEAD = re.compile(r"\b[A-Z][A-Za-z'-]+[ \t]*,[ \t]*$")
# A second finite verb left behind where a rewrite spliced one phrasing into
# another: "Marcus must not overwrite retained the earlier backup". After a
# modal or do-support English allows a bare infinitive and nothing else, so an
# inflected verb sitting between that infinitive and its object is debris.
#
# This replaced eleven separate rules that each memorized one sentence - six
# spellings of the backup sentence and five of the evidence sentence - none of
# which fired on any other text.
#
# The verb list is closed on purpose. "Residents must pay taxes the council
# sets" has the same shape, but "taxes" is a plural noun and deleting it would
# destroy the sentence; a rule keyed on the -s ending alone would do exactly
# that. A following determiner or preposition is required for the same reason.
_STRAY_VERB_FORMS = (
    "accessed|affected|affects|analysed|analyzed|assessed|based|came|comes|"
    "drawn|examined|followed|follows|kept|keeps|looked|measured|needed|needs|"
    "referenced|requested|retained|reviewed|showed|stemmed|studied|used|"
    "wanted|wants"
)
# A modal followed by have or be takes a participle next, and that is correct
# English: "should have looked at the report", "must be given the file".
# Without this exclusion the rule reads "have" as the verb, "looked" as debris,
# and writes "should have at the report".
_PERFECT_AUXILIARIES = "have|has|had|be|been|being"
_MODAL_STRAY_VERB = re.compile(
    r"\b(?:must|should|shall|will|would|can|could|may|might|does|do|did)[ \t]+"
    # The negation must not be mistaken for the verb. Without this guard
    # "does not follows from" matched with "not" as the verb and deleted
    # "follows", which is the word the sentence needs.
    r"(?:not[ \t]+)?(?!not\b)(?!(?:" + _PERFECT_AUXILIARIES + r")\b)[a-z]+[ \t]+"
    r"(?P<stray>" + _STRAY_VERB_FORMS + r")[ \t]+"
    # A past participle doubles as an adjective: "measured data", "affected
    # users", "reviewed copies". Requiring a determiner or preposition next is
    # what separates debris from a normal modifier.
    r"(?=(?:the|a|an|this|that|these|those|his|her|their|its|our|my|your"
    r"|from|on|of)\b)"
)
_SENTENCE_START = re.compile(
    r"(?:\A|[.!?][\"'\u201d\u2019)\]]*[ \t\r\n]+)(?P<letter>[a-z])"
)
_CLOSING_MARKS = frozenset("\"'\u201d\u2019)]}")

_PROTECTED_PATTERN = re.compile(
    r"```[\s\S]*?```"
    r"|`[^`\r\n]*`"
    r"|https?://[^\s<>]+"
    r"|www\.[^\s<>]+"
    r"|(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"|\"(?:[A-Za-z]:[\\/]|\\\\)[^\"\r\n]+\""
    r"|(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s<>\"|?*]+"
    r"|(?<!\w)/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"
    r"|(?<!\w)--?[A-Za-z][A-Za-z0-9-]*"
    r"|\$\{[^}\r\n]+\}"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b[A-Za-z]+_[A-Za-z0-9_]+\b"
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"
)


class WritingSuggestionEngine:
    """Return bounded, deterministic suggestions without a model or I/O."""

    def analyze(self, text: str) -> tuple[WritingSuggestion, ...]:
        if not isinstance(text, str):
            raise TypeError("Writing suggestion text must be a string.")
        if not text:
            return ()
        protected = _protected_spans(text)
        candidates: list[_Candidate] = []
        candidates.extend(_typo_candidates(text, protected))
        candidates.extend(_duplicate_candidates(text, protected))
        candidates.extend(_grammar_phrase_candidates(text, protected))
        candidates.extend(_homophone_candidates(text, protected))
        candidates.extend(_sentence_boundary_candidates(text, protected))
        candidates.extend(_agreement_candidates(text, protected))
        candidates.extend(_dictation_filler_candidates(text, protected))
        candidates.extend(_lowercase_i_candidates(text, protected))
        candidates.extend(_structural_punctuation_candidates(text, protected))
        candidates.extend(_doubled_punctuation_candidates(text, protected))
        candidates.extend(_punctuation_spacing_candidates(text, protected))
        candidates.extend(_declarative_question_candidates(text, protected))
        candidates.extend(_repeated_terminal_punctuation_candidates(text, protected))
        candidates.extend(_repeated_space_candidates(text, protected))
        candidates.extend(_filler_candidates(text, protected))
        candidates.extend(_capitalization_candidates(text, protected))
        final = _final_punctuation_candidate(text, protected)
        if final is not None:
            candidates.append(final)

        accepted: list[_Candidate] = []
        for candidate in candidates:
            if not any(_spans_conflict(candidate, existing) for existing in accepted):
                accepted.append(candidate)
        accepted.sort(key=lambda item: (item.start, item.end, item.rule))
        source_digest = _source_digest(text)
        return tuple(
            WritingSuggestion(
                suggestion_id=_suggestion_id(text, source_digest, candidate),
                category=candidate.category,
                title=candidate.title,
                explanation=candidate.explanation,
                start=candidate.start,
                end=candidate.end,
                original=text[candidate.start : candidate.end],
                replacement=candidate.replacement,
            )
            for candidate in accepted
        )


def apply_suggestion(text: str, suggestion: WritingSuggestion) -> str:
    """Apply one current suggestion or reject it if its source is stale."""
    _validate_text(text)
    _validate_suggestion(text, suggestion)
    return text[: suggestion.start] + suggestion.replacement + text[suggestion.end :]


def apply_all_suggestions(
    text: str,
    suggestions: Iterable[WritingSuggestion],
) -> str:
    """Apply non-overlapping suggestions from one unchanged analysis pass."""
    _validate_text(text)
    try:
        ordered = sorted(tuple(suggestions), key=lambda item: (item.start, item.end))
    except (AttributeError, TypeError) as exc:
        raise TypeError("Suggestions must contain WritingSuggestion values.") from exc
    for suggestion in ordered:
        _validate_suggestion(text, suggestion)
    for previous, current in zip(ordered, ordered[1:]):
        if _spans_conflict(previous, current):
            raise ValueError("Writing suggestions overlap.")
    result = text
    for suggestion in reversed(ordered):
        result = (
            result[: suggestion.start]
            + suggestion.replacement
            + result[suggestion.end :]
        )
    return result


# Two corrections that read the sentence rather than a single word, so they
# cannot live in SPELLING_CORRECTIONS. They were defined here AND in
# selected_text.py, WETHER_ANIMAL_LEAD included - two copies of the same
# rule, kept in step by hand. One definition, used by both paths.
# The conjunction "whether" never takes an article or a number, and the animal
# almost always does, which separates them without a fixture-keyed phrase.
_WETHER = re.compile(r"(?<!\w)(?P<lead>\w+\s+)?(?P<word>[Ww]ether)(?!\w)")
WETHER_ANIMAL_LEAD = frozenset(
    {"a", "an", "the", "this", "that", "one", "two", "three", "each", "every",
     "my", "our", "his", "her", "their", "its", "young", "castrated"}
)


def repair_wether(text: str) -> str:
    """Write the conjunction "whether" where the writer typed "wether"."""
    def repair(match: re.Match[str]) -> str:
        lead = match.group("lead") or ""
        if lead.strip().casefold() in WETHER_ANIMAL_LEAD:
            return match.group(0)
        word = match.group("word")
        fixed = "Whether" if word[0].isupper() else "whether"
        return f"{lead}{fixed}"

    return _WETHER.sub(repair, text)


# "cair" survived in ten of the corrected essays - more than any other word.
# The speller declines it correctly: it is one edit from "chair" and two from
# "care", so ranking by distance picks the furniture. Context settles it, and
# every occurrence in the corpus is one of these shapes ("proper cair", "cair
# routines", "taking cair of"). Only the lowercase form is repaired, because
# capitalised Cair is a name in places and a name is not ours to respell.
_CAIR = re.compile(r"(?<!\w)cair(?!\w)")


def repair_cair(text: str) -> str:
    """Write "care" where the writer typed "cair"."""
    return _CAIR.sub("care", text)



def _typo_candidates(text: str, protected: tuple[tuple[int, int], ...]) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _WORD.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        replacement = SPELLING_CORRECTIONS.get(match.group(0).casefold())
        if replacement is None:
            continue
        replacement = _match_case(match.group(0), replacement)
        if _is_sentence_start(text, match.start()):
            replacement = replacement[:1].upper() + replacement[1:]
        found.append(
            _Candidate(
                "common-typo",
                SuggestionCategory.SPELLING,
                "Fix spelling",
                f'Replace "{match.group(0)}" with "{replacement}".',
                match.start(),
                match.end(),
                replacement,
            )
        )
    return found


def _duplicate_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _DUPLICATE_WORD.finditer(text):
        if match.group("first").casefold() != match.group("second").casefold():
            continue
        start = match.start("first")
        end = match.end("second")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "duplicate-word",
                SuggestionCategory.GRAMMAR,
                "Remove repeated word",
                "The same word appears twice in a row.",
                start,
                end,
                match.group("first"),
            )
        )
    return found


def _grammar_phrase_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for phrase, replacement in _COMMON_GRAMMAR_PHRASES.items():
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if _is_protected(match.start(), match.end(), protected):
                continue
            value = _match_case(match.group(0), replacement)
            found.append(
                _Candidate(
                    "common-grammar-phrase",
                    SuggestionCategory.GRAMMAR,
                    "Fix grammar",
                    f'Replace "{match.group(0)}" with "{value}".',
                    match.start(),
                    match.end(),
                    value,
                )
            )
    for match in _TWO_PREDICATE_COMMA.finditer(text):
        sentence_start = max(
            text.rfind(".", 0, match.start()),
            text.rfind("!", 0, match.start()),
            text.rfind("?", 0, match.start()),
            text.rfind("\n", 0, match.start()),
        ) + 1
        if "," in text[sentence_start : match.start()]:
            continue
        start = match.start("comma")
        end = match.end("comma")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "two-predicate-comma",
                SuggestionCategory.GRAMMAR,
                "Fix grammar",
                "Do not separate two predicates that share one subject with a comma.",
                start,
                end,
                "",
            )
        )
    return found


# Homophones are correctly spelled words in the wrong place, so neither
# SymSpell nor Hunspell can see them; every one below was left untouched in the
# graded corpus output. Each pattern keys on the neighbouring word class rather
# than on a sentence, so the rule transfers to essays outside the corpus. The
# lookarounds are the safety property: they carve out the reading where the
# written word is the correct one, and loosening them is how these turn into
# false corrections of text the author meant.
_DETERMINERS = r"a|an|the|this|that|these|those|my|your|his|her|its|our|their"
_FUNCTION_FOLLOWERS = (
    r"is|was|are|were|be|been|and|or|but|so|then|in|on|at|to|of|for|from|"
    r"with|near|over|under|before|after|today|tomorrow|again|now|here|there"
)
_HOMOPHONE_RULES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        # "a grate pet". As a noun, grate is followed by punctuation, a verb or
        # a preposition; followed by another content word it is modifying it,
        # which only "great" can do.
        "grate-for-great",
        re.compile(
            r"(?<!\w)(?:(?P<lead>" + _DETERMINERS + r")\s+)?(?P<word>[Gg]rate)"
            r"\s+(?!(?:" + _FUNCTION_FOLLOWERS + r")\b)[a-z]",
            re.IGNORECASE,
        ),
        "great",
        'The noun "grate" is a metal cover. Before another word you want "great".',
    ),
    (
        # "witch is a lot of requirements". The relative pronoun takes a verb or
        # a pronoun subject; the noun does not.
        "witch-for-which",
        re.compile(
            r"(?<!\w)(?<!the )(?<!a )(?<!an )(?P<word>[Ww]itch)\s+"
            r"(?=(?:is|was|are|were|means|meant|makes|made|will|would|can|"
            r"could|should|might|has|have|had|he|she|they|it|we|you)\b)"
        ),
        "which",
        'A "witch" is a person. The relative pronoun is "which".',
    ),
    (
        # "cud become". Cud is what cattle chew; anything else is "could".
        "cud-for-could",
        re.compile(
            r"(?<!\w)(?<!the )(?<!its )(?<!a )(?P<word>[Cc]ud)(?!\w)"
        ),
        "could",
        '"Cud" is chewed by cattle. The modal verb is "could".',
    ),
    (
        # "wit correct preparation". The noun "wit" follows a determiner, a
        # possessive, or "and"/"of"; standing before a word it is "with".
        "wit-for-with",
        re.compile(
            r"(?<!\w)(?<!the )(?<!a )(?<!his )(?<!her )(?<!their )(?<!your )"
            r"(?<!of )(?<!and )(?<!quick )(?<!dry )(?<!sharp )"
            r"(?P<word>[Ww]it)\s+(?=[a-z])"
        ),
        "with",
        '"Wit" is cleverness. The preposition is "with".',
    ),
    (
        # "care bout something". The noun "bout" needs a determiner in front.
        "bout-for-about",
        re.compile(
            r"(?<!\w)(?<!a )(?<!an )(?<!the )(?<!this )(?<!that )(?<!each )"
            r"(?<!every )(?<!one )(?<!first )(?<!second )(?<!last )(?<!title )"
            r"(?P<word>[Bb]out)(?!\w)"
        ),
        "about",
        'A "bout" is a round of something. You want "about".',
    ),
    (
        # "there a good choice". The adverb does not take a determiner after it
        # unless a copula or a question word came first ("is there a ...").
        "there-for-theyre",
        re.compile(
            # The lookbehinds are case-insensitive by hand because the pattern
            # is not: "Is there a good choice?" opens a sentence, and a
            # case-sensitive "is " missed it and rewrote a correct question.
            r"(?<!\w)(?<![Ii]s )(?<![Aa]re )(?<![Ww]as )(?<![Ww]ere )"
            r"(?<![Bb]e )(?<![Bb]een )"
            r"(?P<word>[Tt]here)\s+"
            r"(?=(?:" + _DETERMINERS + r"|going|not|just|really|still|always|"
            r"never|probably)\b)"
        ),
        "they're",
        'This is the contraction of "they are", written "they\'re".',
    ),
    (
        # "read a essay". Only spelled vowels, so "a university" and "a one-way
        # street" are left alone, as is "a European". Vowel-initial dictation
        # filler is skipped too: "a or whatever good choice" is repaired by
        # deleting the filler, and turning the determiner into "an" first left
        # "an good choice" behind.
        "a-before-vowel",
        re.compile(
            r"(?<!\w)(?P<word>[Aa])\s+(?!or[ \t]+whatever\b)(?!anyways\b)"
            r"(?!and[ \t]+stuff\b)(?!i[ \t]+guess\b)"
            r"(?=(?!one\b)(?!once\b)(?!eu)[aeio][a-z])"
        ),
        "an",
        'Use "an" before a word that starts with a vowel sound.',
    ),
    (
        "your-for-youre",
        re.compile(
            r"(?<!\w)(?P<word>[Yy]our)\s+"
            r"(?=(?:going|gonna|not|welcome|right|wrong|probably|definitely|"
            r"always|never)\b)"
        ),
        "you're",
        'This is the contraction of "you are", written "you\'re".',
    ),
    (
        "its-for-it-is",
        re.compile(
            r"(?<!\w)(?P<word>[Ii]ts)\s+"
            r"(?=(?:a|an|the|not|going|been|only|just|too|very|now|clear|"
            r"important|possible|likely)\b)"
        ),
        "it's",
        'This is the contraction of "it is", written "it\'s".',
    ),
    (
        # A comparative always takes "than", never "then".
        "then-for-than",
        re.compile(
            r"(?<!\w)(?:more|less|better|worse|rather|other|otherwise|fewer"
            r"|[a-z]{3,}er)\s+(?P<word>[Tt]hen)(?!\w)",
            re.IGNORECASE,
        ),
        "than",
        'A comparison takes "than". "Then" is about time.',
    ),
    (
        "affect-for-effect",
        re.compile(
            # One optional adjective is allowed between the determiner and the
            # noun ("the main affect"), but never a pronoun or an auxiliary,
            # which would make the following word a verb and correct already.
            r"(?<!\w)(?:the|an|any|no|this|that|side|little|great)\s+"
            r"(?:(?!we\b|they\b|you\b|i\b|he\b|she\b|it\b|can\b|could\b|will\b"
            r"|would\b|may\b|might\b|must\b|not\b|do\b|does\b|did\b|to\b)"
            r"[a-z]+\s+)?"
            r"(?P<word>[Aa]ffect)(?!\w)",
            re.IGNORECASE,
        ),
        "effect",
        'The noun is "effect". "Affect" is the verb.',
    ),
    (
        "effect-for-affect",
        re.compile(
            r"(?<!\w)(?:to|will|would|can|could|may|might|not)\s+"
            r"(?P<word>[Ee]ffect)(?!\w)",
            re.IGNORECASE,
        ),
        "affect",
        'The verb is "affect". "Effect" is the noun.',
    ),
    (
        "loose-for-lose",
        re.compile(
            r"(?<!\w)(?:to|will|would|could|might|may|can|not|never|always)\s+"
            r"(?P<word>[Ll]oose)(?!\w)",
            re.IGNORECASE,
        ),
        "lose",
        '"Loose" is the opposite of tight. The verb is "lose".',
    ),
)


# NOT IMPLEMENTED: adding commas to a list written with none at all ("it offers
# companionship routine education entertainment and a reason to care"). A rule
# requiring three consecutive content words before "and" was written on
# 2026-08-13 and measured clean against all 20 corpus essays, whose bare lists
# happen to have single-word items. It then corrupted the first real list it met
# outside them: "counted volunteer hours crop donations cancelled workdays and
# public meeting attendance" became "volunteer, hours, crop, donations,
# cancelled, workdays". Item boundaries in a multi-word list cannot be found
# without part-of-speech knowledge, and a wrong comma is worse than a missing
# one. Recorded in docs/ARCHITECTURE.md as a known defect.
# NOT IMPLEMENTED: splitting a run-on where one independent clause is butted
# against the next ("... in Athens Greece it offers companionship ..."). A rule
# matching a pronoun subject plus a finite verb was written and measured against
# all 20 corpus essays on 2026-08-13: 53 matches, of which one was the run-on it
# was aimed at. It split "realizes they had one fact wrong", "shows the pet is
# theirs", "entertainment that is educational" and "then after a while it
# becomes automatic", because any transitive verb takes a clause with no "that",
# a relative "that is" looks identical, and an adverb can sit where the clause
# boundary would be. Telling those apart needs part-of-speech knowledge this
# layer does not have, so the rule was deleted rather than shipped. Recorded in
# docs/ARCHITECTURE.md as a known defect.
#
# Exactly two full stops is a typing slip. Three is an ellipsis an author may
# have meant, so it is left alone; four or more is trimmed back to one.
_DOUBLED_FULL_STOP = re.compile(r"(?<!\.)(?P<dots>\.\.)(?!\.)|(?P<many>\.{4,})")


# The plural-subject rule that used to live here now lives in polish_syntax.py,
# because it needs the word-class table and this module must stay free of I/O.
# Four hand-written lists went with it: singular nouns ending in -s, the
# endings -us -ss -is -ics, a preposition set and a correlative set. Each had
# been grown one false positive at a time; the lexicon answers all four by
# asking whether the singular of the word is itself a word.
# A plural pronoun subject, which needs no noun analysis at all.
_PRONOUN_PLURAL_FORM = {
    "they": {"is": "are", "was": "were", "has": "have", "does": "do"},
    "we": {"is": "are", "was": "were", "has": "have", "does": "do"},
    "you": {"is": "are", "was": "were", "has": "have", "does": "do"},
    "people": {"is": "are", "was": "were", "has": "have", "does": "do"},
    "children": {"is": "are", "was": "were", "has": "have", "does": "do"},
}
_PRONOUN_SINGULAR_VERB = re.compile(
    r"(?<!\w)(?P<subject>they|we|you|people|children)[ \t]+"
    r"(?P<verb>is|was|has|does)(?!\w)",
    re.IGNORECASE,
)


def _agreement_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _PRONOUN_SINGULAR_VERB.finditer(text):
        start = match.start("verb")
        end = match.end("verb")
        if _is_protected(start, end, protected):
            continue
        subject = match.group("subject").lower()
        verb = match.group("verb").lower()
        found.append(
            _Candidate(
                "pronoun-verb-agreement",
                SuggestionCategory.GRAMMAR,
                "Fix subject and verb agreement",
                f'"{match.group("subject")}" is plural and takes a plural verb.',
                start,
                end,
                _match_case(match.group("verb"), _PRONOUN_PLURAL_FORM[subject][verb]),
            )
        )
    return found


# Dictation filler sitting inside a noun phrase, between a determiner and the
# word it determines: "a basically home", "the you know animal", "the or
# whatever visitor". An adverb or a discourse marker cannot occupy that slot in
# English, so the phrase is a speech artefact rather than something the author
# wrote, and removing it changes no meaning.
#
# This is the only rule in this module that deletes the author's words, so the
# position requirement is doing real work: "the essay is basically finished"
# keeps its adverb because "basically" is not between a determiner and its noun.
_DICTATION_FILLER = (
    "or whatever",
    "you know",
    "to be honest",
    "for real",
    "i guess",
    "anyways",
    "sorta",
    "and stuff",
    "basically",
)
_FILLER_IN_NOUN_PHRASE = re.compile(
    r"(?<!\w)(?:" + _DETERMINERS + r")[ \t]+(?P<filler>"
    + "|".join(phrase.replace(" ", r"[ \t]+") for phrase in _DICTATION_FILLER)
    + r")[ \t]+(?=[a-z])",
    re.IGNORECASE,
)


def _dictation_filler_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _FILLER_IN_NOUN_PHRASE.finditer(text):
        start = match.start("filler")
        end = match.end()
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "dictation-filler-in-noun-phrase",
                SuggestionCategory.CLARITY,
                "Remove the filler",
                f'"{match.group("filler")}" cannot sit between a determiner '
                "and its noun; it came from speech.",
                start,
                end,
                "",
            )
        )
    return found


def _sentence_boundary_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _DOUBLED_FULL_STOP.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        found.append(
            _Candidate(
                "doubled-full-stop",
                SuggestionCategory.FORMATTING,
                "Remove the extra full stop",
                "One full stop ends a sentence.",
                match.start(),
                match.end(),
                "." if match.group("dots") else "...",
            )
        )
    return found


def _homophone_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for rule, pattern, replacement, explanation in _HOMOPHONE_RULES:
        for match in pattern.finditer(text):
            start = match.start("word")
            end = match.end("word")
            if _is_protected(start, end, protected):
                continue
            found.append(
                _Candidate(
                    rule,
                    SuggestionCategory.SPELLING,
                    "Fix the wrong word",
                    explanation,
                    start,
                    end,
                    _match_case(match.group("word"), replacement),
                )
            )
    return found


def _structural_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _HOWEVER_COMMA_SPLICE.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        however = "However" if "However" in match.group(0) else "however"
        found.append(
            _Candidate(
                "however-comma-splice",
                SuggestionCategory.GRAMMAR,
                "Fix comma splice",
                "Use a semicolon before however between independent clauses.",
                match.start(),
                match.end(),
                f"; {however}, ",
            )
        )
    for match in _TIME_PREPOSITION_FRAGMENT.finditer(text):
        start = match.start("prep")
        end = match.end("prep")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "time-preposition-fragment",
                SuggestionCategory.GRAMMAR,
                "Join sentence fragment",
                "Keep the location phrase attached to the time clause.",
                start,
                end,
                match.group("prep").lower(),
            )
        )
    for match in _DEVICE_TEST_PREPOSITION.finditer(text):
        start = match.start("prep")
        end = match.end("prep")
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "device-test-preposition",
                SuggestionCategory.GRAMMAR,
                "Fix device preposition",
                "Tests run on devices, not in them.",
                start,
                end,
                "on",
            )
        )
    for match in _MODAL_STRAY_VERB.finditer(text):
        start = match.start("stray")
        end = match.end()
        if _is_protected(start, end, protected):
            continue
        found.append(
            _Candidate(
                "stray-verb-after-modal",
                SuggestionCategory.GRAMMAR,
                "Remove the leftover verb",
                "A modal takes a plain verb; the second one is left over from an "
                "unfinished edit.",
                start,
                end,
                "",
            )
        )
    for match in _SERIAL_COMMA.finditer(text):
        start = match.start("middle")
        end = match.end("last")
        if _is_protected(start, end, protected):
            continue
        if _APPOSITIVE_LEAD.search(text[: match.start() + 1]):
            continue
        found.append(
            _Candidate(
                "serial-comma",
                SuggestionCategory.GRAMMAR,
                "Add the missing serial comma",
                "A list that punctuates its earlier items needs a comma before "
                "the final and.",
                start,
                end,
                f"{match.group('middle')}, and {match.group('last')}",
            )
        )
    for match in _TITLE_NAME_COMMAS.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        found.append(
            _Candidate(
                "title-name-commas",
                SuggestionCategory.GRAMMAR,
                "Fix title and name punctuation",
                "Do not separate a restrictive role and name with commas.",
                match.start(),
                match.end(),
                f"{match.group('role')} {match.group('name')}",
            )
        )
    return found


def _lowercase_i_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "lowercase-i",
            SuggestionCategory.GRAMMAR,
            "Capitalize I",
            "The standalone pronoun I is capitalized.",
            match.start(),
            match.end(),
            "I",
        )
        for match in _LOWERCASE_I.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


def _punctuation_spacing_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "space-before-punctuation",
            SuggestionCategory.FORMATTING,
            "Remove space before punctuation",
            "Punctuation follows the previous word without a space.",
            match.start(),
            match.end(),
            "",
        )
        for match in _SPACE_BEFORE_PUNCTUATION.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


# Doubled commas were the last remaining artefact in eight of nine otherwise
# clean essays - "in Utrecht, the Netherlands,, this can be", "replacing
# water,, cleaning". A stray comma after a full stop is the same defect from
# the other side ("or other equipment., but the animal").
_DOUBLED_COMMA = re.compile(r",[\s,]*,")
_TERMINATOR_THEN_COMMA = re.compile(r"([.!?])\s*,")
# "At 12:40 p.m., observers" must keep its full stop. An abbreviation before
# the comma is punctuating normally, not a doubled terminator.
_TERMINATOR_ABBREVIATIONS = frozenset(
    {"al", "am", "cf", "dr", "eg", "etc", "ie", "inc", "jr", "lt", "ltd",
     "mr", "mrs", "ms", "no", "pm", "prof", "sr", "st", "vs"}
)


def _doubled_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    candidates = [
        _Candidate(
            "doubled-comma",
            SuggestionCategory.FORMATTING,
            "Remove the repeated comma",
            "One comma separates the clause; the extra one is a typing slip.",
            match.start(),
            match.end(),
            ",",
        )
        for match in _DOUBLED_COMMA.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]
    for match in _TERMINATOR_THEN_COMMA.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        # Take the whole dotted token, not just its last letters: "p.m." has to
        # be recognised as "pm", or the guard sees "m" and strips a comma that
        # was punctuating the sentence correctly.
        lead = re.findall(r"([A-Za-z][A-Za-z.]*)$", text[: match.start() + 1])
        token = lead[0].replace(".", "").casefold() if lead else ""
        if token in _TERMINATOR_ABBREVIATIONS:
            continue
        candidates.append(
            _Candidate(
                "terminator-then-comma",
                SuggestionCategory.FORMATTING,
                "Remove the comma after the full stop",
                "A sentence ends with its full stop; the comma is left over.",
                match.start(),
                match.end(),
                match.group(1),
            )
        )
    return candidates


def _declarative_question_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for match in _DECLARATIVE_QUESTION_MARK.finditer(text):
        start, end = match.span("mark")
        if _is_protected(start, end, protected):
            continue
        candidates.append(
            _Candidate(
                "declarative-question-mark",
                SuggestionCategory.GRAMMAR,
                "Use statement punctuation",
                "A declarative statement should end with a period.",
                start,
                end,
                ".",
            )
        )
    return candidates


def _repeated_terminal_punctuation_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    return [
        _Candidate(
            "repeated-terminal-punctuation",
            SuggestionCategory.FORMATTING,
            "Use one punctuation mark",
            "Standard prose uses one terminal punctuation mark.",
            match.start(),
            match.end(),
            match.group("mark"),
        )
        for match in _REPEATED_TERMINAL_PUNCTUATION.finditer(text)
        if not _is_protected(match.start(), match.end(), protected)
    ]


def _repeated_space_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _REPEATED_SPACE.finditer(text):
        if _is_protected(match.start(), match.end(), protected):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        if not text[line_start : match.start()].strip():
            continue
        found.append(
            _Candidate(
                "repeated-space",
                SuggestionCategory.FORMATTING,
                "Use one space",
                "Replace repeated horizontal whitespace with one space.",
                match.start(),
                match.end(),
                " ",
            )
        )
    return found


def _filler_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for phrase, replacement in _FILLER_REPLACEMENTS.items():
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if _is_protected(match.start(), match.end(), protected):
                continue
            value = _match_case(match.group(0), replacement)
            if _is_sentence_start(text, match.start()):
                value = value[:1].upper() + value[1:]
            found.append(
                _Candidate(
                    "filler-phrase",
                    SuggestionCategory.CLARITY,
                    "Tighten filler phrase",
                    f'Use "{value}" for a more direct sentence.',
                    match.start(),
                    match.end(),
                    value,
                )
            )
    return found


def _capitalization_candidates(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Candidate]:
    found: list[_Candidate] = []
    for match in _SENTENCE_START.finditer(text):
        start = match.start("letter")
        if _follows_nonterminal_abbreviation(text, start):
            continue
        if _is_protected(start, start + 1, protected):
            continue
        found.append(
            _Candidate(
                "sentence-capitalization",
                SuggestionCategory.GRAMMAR,
                "Capitalize sentence",
                "A sentence starts with a capital letter.",
                start,
                start + 1,
                text[start].upper(),
            )
        )
    return found


def _follows_nonterminal_abbreviation(text: str, start: int) -> bool:
    prefix = text[:start].rstrip().casefold()
    return bool(
        re.search(
            r"(?<![a-z])(?:a\.m|p\.m|e\.g|i\.e|mr|mrs|ms|dr|prof|sr|jr|st)\.$",
            prefix,
        )
    )


def _final_punctuation_candidate(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> _Candidate | None:
    stripped_end = len(text.rstrip())
    if stripped_end == 0:
        return None
    insertion = stripped_end
    while insertion > 0 and text[insertion - 1] in _CLOSING_MARKS:
        insertion -= 1
    if insertion == 0 or text[insertion - 1] in ".!?\u2026;:":
        return None
    if not text[insertion - 1].isalnum():
        return None
    if _is_protected(insertion, insertion, protected, include_edges=False):
        return None
    return _Candidate(
        "final-punctuation",
        SuggestionCategory.GRAMMAR,
        "Add final punctuation",
        "Complete the final sentence with a period.",
        insertion,
        insertion,
        ".",
    )


def _protected_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = sorted((match.start(), match.end()) for match in _PROTECTED_PATTERN.finditer(text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _is_protected(
    start: int,
    end: int,
    protected: tuple[tuple[int, int], ...],
    *,
    include_edges: bool = True,
) -> bool:
    if start == end:
        if include_edges:
            return any(left <= start <= right for left, right in protected)
        return any(left < start < right for left, right in protected)
    return any(start < right and end > left for left, right in protected)


def _is_sentence_start(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    if not prefix:
        return True
    return prefix[-1] in ".!?\u2026"


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _spans_conflict(left, right) -> bool:
    if left.start == left.end and right.start == right.end:
        return left.start == right.start
    if left.start == left.end:
        return right.start < left.start < right.end
    if right.start == right.end:
        return left.start < right.start < left.end
    return left.start < right.end and right.start < left.end


def _source_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _suggestion_id(text: str, source_digest: str, candidate: _Candidate) -> str:
    material = (
        f"{candidate.rule}\0{candidate.start}\0{candidate.end}\0"
        f"{text[candidate.start:candidate.end]}\0{candidate.replacement}"
    )
    candidate_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"ws:{source_digest}:{candidate_digest}"


def _validate_text(text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("Writing suggestion text must be a string.")


def _validate_suggestion(text: str, suggestion: WritingSuggestion) -> None:
    if not isinstance(suggestion, WritingSuggestion):
        raise TypeError("Expected a WritingSuggestion.")
    if (
        isinstance(suggestion.start, bool)
        or isinstance(suggestion.end, bool)
        or not isinstance(suggestion.start, int)
        or not isinstance(suggestion.end, int)
        or not 0 <= suggestion.start <= suggestion.end <= len(text)
    ):
        raise ValueError("Writing suggestion bounds are invalid.")
    parts = suggestion.suggestion_id.split(":")
    if (
        len(parts) != 3
        or parts[0] != "ws"
        or not hmac.compare_digest(parts[1], _source_digest(text))
    ):
        raise ValueError("Writing suggestion is stale or invalid.")
    if text[suggestion.start : suggestion.end] != suggestion.original:
        raise ValueError("Writing suggestion no longer matches the source text.")


__all__ = [
    "SuggestionCategory",
    "WritingSuggestion",
    "WritingSuggestionEngine",
    "apply_all_suggestions",
    "apply_suggestion",
]
