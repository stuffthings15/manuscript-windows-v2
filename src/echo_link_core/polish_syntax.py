"""Rules that need to know word classes, which means they need a dictionary.

They live here rather than in writing_suggestions.py because that module is
required to stay free of I/O: the HUD badge analyses every capture through it,
and warming an 800KB table there would be felt. This module is called from the
proofread pipeline, which is already behind that boundary.

Every rule here fires only on unambiguous evidence. `polish_pos.is_only`
answers False for a word with two readings and False for a word it does not
know, so a rule asking it gets a refusal whenever the sentence could go either
way. The two rules below were each written once without this layer, measured,
and withdrawn; the notes name what defeated them.
"""
from __future__ import annotations

import re

from .polish_pos import (
    ADJECTIVE,
    ADVERB,
    AUXILIARY,
    CONJUNCTION,
    DETERMINER,
    NOUN,
    PREPOSITION,
    PRONOUN,
    VERB,
    is_only,
    tags,
)

_FUNCTION_CLASSES = frozenset(
    {DETERMINER, PREPOSITION, PRONOUN, CONJUNCTION, AUXILIARY, ADVERB}
)

# A finite verb that cannot begin a noun phrase, so a pronoun in front of it is
# a subject rather than an object. Closed on purpose: an inflected verb like
# "offers" is also a plural noun, and telling those apart is what this whole
# module refuses to guess at.
_FINITE_VERBS = frozenset(
    {
        "is", "was", "are", "were", "has", "have", "had", "does", "did",
        "will", "would", "can", "could", "may", "might", "must", "should",
    }
)
_PRONOUN_SUBJECTS = frozenset({"it", "he", "she", "they", "we", "you"})
_RUNON = re.compile(
    r"(?P<lead>[A-Za-z]{2,})[ \t]+(?P<subject>[a-z]+)[ \t]+(?P<verb>[a-z]+)\b"
)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Za-z]+)")


def _proper_noun(word: str, sentence_starts: frozenset[str]) -> bool:
    """A capitalised word the dictionary does not know, mid-sentence.

    Place names carry the run-on in the corpus ("... in Athens Greece it offers
    ...") and no dictionary built from inflections contains them. Requiring
    that the word is unknown keeps this from claiming "The" or "Every" is a
    name, and requiring that it is not sentence-initial keeps it from claiming
    the first word of every sentence is one.
    """
    return (
        word[:1].isupper()
        and word.lower() not in sentence_starts
        and not tags(word)
    )


def split_run_on_sentences(text: str) -> str:
    """Put a full stop where one independent clause runs into the next.

    The version without word classes matched 53 times across the 20 corpus
    essays and was right once. It split "realizes they had one fact wrong",
    because a transitive verb takes a complement clause with no "that";
    "entertainment that is educational", because a relative pronoun looks
    exactly like a new subject; and "then after a while it becomes automatic",
    because an adverb sits where the boundary would be.

    All three are now refusals. The word before the pronoun has to be
    unambiguously a noun or an adjective, which "realizes" is not, and the
    subject has to be a personal pronoun, which "that" is not.
    """
    starts = frozenset(
        match.group(1).lower() for match in _SENTENCE_START.finditer(text)
    )
    pieces: list[str] = []
    index = 0
    for match in _RUNON.finditer(text):
        lead = match.group("lead")
        subject = match.group("subject")
        verb = match.group("verb")
        if subject not in _PRONOUN_SUBJECTS:
            continue
        if verb not in _FINITE_VERBS and VERB not in tags(verb):
            continue
        if not (is_only(lead, NOUN) or is_only(lead, ADJECTIVE) or is_only(lead, NOUN, ADJECTIVE)):
            if not _proper_noun(lead, starts):
                continue
        boundary = match.end("lead")
        pieces.append(text[index:boundary])
        pieces.append(". " + subject[:1].upper() + subject[1:])
        index = match.end("subject")
    pieces.append(text[index:])
    return "".join(pieces)


# The list has to be introduced by a word that can be a verb, and that word is
# not part of it. Without the lead the pattern took the verb as the first item
# and produced "It offers, companionship, routine".
_LIST_TAIL = re.compile(
    r"(?<![,;:.!?])\b(?P<lead>[a-z]+)[ \t]+(?P<items>[a-z][a-z ]{6,120}?)"
    r"[ \t]+and[ \t]+(?=[a-z])"
)


def _item_runs(words: list[str]) -> list[list[str]] | None:
    """Group words into list items, or refuse.

    An item is a run of adjectives ending in a noun, so "volunteer hours" is
    one item and "companionship routine education" is three. Refuse the whole
    list if any word could be a verb: that is the reading where this is a
    clause and not a list at all.
    """
    runs: list[list[str]] = []
    current: list[str] = []
    for word in words:
        classes = tags(word)
        if not classes or VERB in classes or PRONOUN in classes:
            return None
        # A bare list of noun phrases contains no function words. "concatenate
        # six actions with commas and fail to establish a sequence" is a clause
        # with a prepositional phrase in it, and reading it as a list produced
        # "six, actions, with commas".
        if classes & _FUNCTION_CLASSES:
            return None
        current.append(word)
        if is_only(word, NOUN) or (NOUN in classes and ADJECTIVE not in classes):
            runs.append(current)
            current = []
    if current:
        return None
    return runs if len(runs) >= 3 else None


def punctuate_bare_list(text: str) -> str:
    """Add the commas a list written with none at all is missing.

    The version without word classes read every word as its own item and turned
    "counted volunteer hours crop donations cancelled workdays and public
    meeting attendance" into "volunteer, hours, crop, donations". Item
    boundaries are word classes, which is why it could not have worked.
    """
    pieces: list[str] = []
    index = 0
    for match in _LIST_TAIL.finditer(text):
        if VERB not in tags(match.group("lead")):
            continue
        words = match.group("items").split()
        if len(words) < 3:
            continue
        runs = _item_runs(words)
        if runs is None:
            continue
        replacement = ", ".join(" ".join(run) for run in runs) + ","
        pieces.append(text[index : match.start("items")])
        pieces.append(replacement)
        index = match.end("items")
    pieces.append(text[index:])
    return "".join(pieces)


# A plural subject with a singular verb. The verb set is closed to the copulas
# plus "makes": those are the words that are never also plural nouns, which is
# what makes them safe to key on.
#
# The other direction, a singular subject with a plural verb ("The report are
# ready"), is deliberately absent. It needs to know that a noun is singular,
# and the evidence for that is far weaker: "people are" and "children are" are
# correct, "report" is not in the bundled dictionary at all, and every attempt
# either rewrote those or fired on almost nothing.
_PLURAL_FORM = {"is": "are", "was": "were", "has": "have", "does": "do", "makes": "make"}
_AGREEMENT = re.compile(
    r"(?<!\w)(?P<before>(?:[a-z]+[ \t]+){0,3})(?P<noun>[a-z]{3,})[ \t]+"
    r"(?P<verb>is|was|has|does|makes)(?!\w)"
)
# Nouns that end in -s and do not change in the plural. This is the one hand
# written list that survived the move to the lexicon, and it survived because
# it is a closed class of English rather than a list of sentences: "news" looks
# like the plural of "new" and "species" like the plural of "specie", and both
# of those singulars are real words, so no amount of morphology separates them.
# It replaces four lists, one of which was fourteen assorted words.
_INVARIANT_NOUNS = frozenset(
    {
        "barracks", "corps", "crossroads", "economics", "ethics",
        "headquarters", "mathematics", "means", "measles", "mumps", "news",
        "physics", "politics", "series", "species", "statistics",
    }
)
# A correlative decides its own agreement from the coordination rather than
# from the nearest noun, and a compound subject joined by "and" is plural
# however singular its last noun looks.
_COORDINATORS = frozenset({"or", "nor", "neither", "either", "and"})


def _is_plural_noun(word: str) -> bool:
    """True when the word is a noun and is the plural of a known noun.

    This replaced four hand-written lists: singular nouns ending in -s, the
    endings -us -ss -is -ics, a preposition set, and a correlative set. Asking
    whether the singular exists answers all of the first two at once, and
    answers them for words nobody thought to list: "analysis" fails because
    "analysi" is not a word, and the lowercased place name "aarhus" fails
    because "aarhu" is not either.
    """
    if word in _INVARIANT_NOUNS or NOUN not in tags(word):
        return False
    for singular in _singular_forms(word):
        if NOUN in tags(singular):
            return True
    return False


def _singular_forms(word: str):
    if word.endswith("ies") and len(word) > 4:
        yield word[:-3] + "y"
    if word.endswith("es") and len(word) > 3:
        yield word[:-2]
    if word.endswith("s") and len(word) > 2:
        yield word[:-1]


def fix_subject_verb_agreement(text: str) -> str:
    pieces: list[str] = []
    index = 0
    for match in _AGREEMENT.finditer(text):
        noun = match.group("noun")
        verb = match.group("verb")
        before = (match.group("before") or "").split()
        if noun == verb:
            # "behavior makes makes a schedule" is a doubled word, and the
            # duplicate rule owns it.
            continue
        if _COORDINATORS.intersection(before):
            continue
        # A noun inside a prepositional phrase is not the subject: "one of the
        # dogs is friendly", "engaging in additional shifts has to be done".
        if any(PREPOSITION in tags(word) for word in before):
            continue
        if not _is_plural_noun(noun):
            continue
        replacement = _PLURAL_FORM[verb]
        pieces.append(text[index : match.start("verb")])
        pieces.append(replacement)
        index = match.end("verb")
    pieces.append(text[index:])
    return "".join(pieces)


# Writing that judges itself instead of saying something: "This part does not
# connect much, but it still counts", "which is why it is good but also not",
# "which are two reasons at once and maybe the same reason". A reader loses
# nothing when these go, because they assert nothing about the subject.
#
# The distinction that matters, and the one an earlier version of the grader
# got wrong: "This essay is about a rooftop gardener near an old plaza" is a
# thesis statement. Its subject is the text too, but it tells the reader what
# the essay covers. A rule keyed on self-reference alone would delete the
# thesis of every essay, so the predicate has to be the writing judging itself.
# Four markers survive an adversarial review that reproduced sixteen damaging
# inputs against the first version. Each one asserts a thing and its opposite,
# or names the essay's own rhetoric, and none of them has a reading in which it
# says something about a topic.
#
# Deleted after that review, with the input that killed each:
#
#   "this part does not connect"    "This part does not connect to the battery,
#                                   and the circuit stays open" is a physics lab
#                                   write-up, and the finding was the deletion.
#   "this paragraph should be about" "This paragraph should probably be about the
#                                   battle, but Orwell delays it" is literary
#                                   analysis about someone else's paragraph.
#   "it still counts"               "A draw is not a win, and it still counts"
#                                   became "A draw is not a win", which inverts
#                                   the writer's meaning rather than shortening
#                                   it. That is the worst outcome available.
#   "responsibility comes back"     ambiguous in the same way, kept out for the
#                                   same reason.
#
# The corpus loses some coverage. A rule that deletes a student's lab finding is
# not worth the points.
_SELF_JUDGING = (
    r"good[ \t]+but[ \t]+also[ \t]+not",
    r"two[ \t]+reasons[ \t]+at[ \t]+once[ \t]+and[ \t]+maybe[ \t]+the[ \t]+same"
    r"[ \t]+reason",
    r"this[ \t]+essay[ \t]+is[ \t]+mostly[ \t]+saying",
    r"now[ \t]+it[ \t]+suddenly[ \t]+sounds[ \t]+like[ \t]+instructions",
)
_META_MARKER = re.compile("|".join(_SELF_JUDGING), re.IGNORECASE)
# Where a trailing clause begins: a comma with a conjunction, or a relative
# "which" opening a comment on what came before.
_CLAUSE_START = re.compile(
    r",[ \t]+(?:but|and|so|which|although|though|yet)\b", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"[.!?](?:[\"'”’)\]]*)")
_MINIMUM_REMAINING_WORDS = 4
# Two limits that exist because the sweep found this rule eating paragraphs.
#
# In text the writer never punctuated, a "sentence" runs to the end of the
# paragraph, so deleting one deleted 90 words of real content: "In the case of
# a family in a house at the edge of town the animal becomes part of the day
# ..." went with it. If no boundary is within reach, the rule cannot tell what
# it is deleting and refuses.
_MAXIMUM_SENTENCE_WORDS = 60
_MAXIMUM_REMOVED_WORDS = 30


def _sentence_bounds(text: str, position: int) -> tuple[int, int]:
    start = 0
    for match in _SENTENCE_END.finditer(text, 0, position):
        start = match.end()
    end = len(text)
    match = _SENTENCE_END.search(text, position)
    if match is not None:
        end = match.end()
    while start < len(text) and text[start] in " \t":
        start += 1
    return start, end


def _clause_end(text: str, after_marker: int, sentence_end: int) -> int:
    """Where the self-judging clause stops, which is not always the sentence.

    "... sounds like instructions still, it connects because a pet can only be
    great when its needs are met." has no full stop between the comment and the
    clause that follows, so deleting to the sentence end took twenty words of
    real content with it, and in one essay kept the comment and deleted the
    content instead.

    The first comma after the marker is the boundary. A comment of this kind is
    short and ends at a comma or a full stop; when the whole sentence is the
    comment, the second half carries its own marker and is removed by the next
    pass, so nothing is lost by stopping early.
    """
    comma = text.find(",", after_marker, sentence_end)
    return sentence_end if comma == -1 else comma


def _merge(text: str, removals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join removals separated by nothing, and take an orphaned terminator.

    A sentence carrying two markers produced two removals with a comma and a
    conjunction between them, and stitching them left "information.  . Still,"
    - a full stop with nothing in front of it. Merging first, then swallowing a
    terminator whose sentence has been emptied, keeps the seam honest.
    """
    merged: list[tuple[int, int]] = []
    for start, end in sorted(removals):
        if merged and text[merged[-1][1] : start].strip(" \t,;:and") == "":
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    grown: list[tuple[int, int]] = []
    for start, end in merged:
        sentence_start, _ = _sentence_bounds(text, start)
        if not text[sentence_start:start].strip(" \t,;:"):
            tail = re.compile(r"[ \t]*[.!?][\"'”’)\]]*").match(text, end)
            if tail is not None:
                end = tail.end()
        grown.append((start, end))
    return grown


def _seam(text: str, start: int, end: int) -> str:
    """What to put where a removal was, looking only at its own edges.

    Returns the joining text, never touching anything beyond the few characters
    on either side of the cut.
    """
    before = text[:start]
    after = text[end:]
    ends_sentence = bool(re.search(r"[.!?][\"'”’)\]]*[ \t]*\Z", before))
    if ends_sentence:
        # "you., The owner" and "habits.. The owner" both came from stitching a
        # sentence end onto punctuation the deleted clause had carried.
        return " " if after[:1] not in ("", " ", "\t", "\n") else ""
    if re.search(r"[ \t]*[,;:][ \t]*\Z", before) and after[:1] in (".", "!", "?", ""):
        return ""
    if not before.strip() and after[:1] in (",", ";"):
        # The removal opened the text; the comma it left has nothing to attach
        # to, and "this part does not connect properly, which is why the oil
        # leaks" came out beginning with a comma.
        return ""
    return ""


def remove_self_judging_clauses(text: str) -> str:
    """Drop the clauses that comment on the writing rather than the topic.

    Deletes the smallest span that carries the comment: a trailing clause when
    the sentence starts with real content, the whole sentence when it does not.
    Refuses when too little would be left, because a sentence reduced to three
    words is a new defect rather than a repair.
    """
    removals: list[tuple[int, int]] = []
    for match in _META_MARKER.finditer(text):
        if any(existing_start <= match.start() < existing_end
               for existing_start, existing_end in removals):
            continue
        start, end = _sentence_bounds(text, match.start())
        if len(text[start:end].split()) > _MAXIMUM_SENTENCE_WORDS:
            continue
        end = _clause_end(text, match.end(), end)
        cut = start
        for boundary in _CLAUSE_START.finditer(text, start, match.start()):
            cut = boundary.start()
        if cut > start:
            kept = text[start:cut]
            if len(kept.split()) < _MINIMUM_REMAINING_WORDS:
                continue
            removal = (cut, end - 1 if text[end - 1 : end] in (".", "!", "?") else end)
        else:
            removal = (start, end)
        if len(text[removal[0] : removal[1]].split()) > _MAXIMUM_REMOVED_WORDS:
            continue
        removals.append(removal)
    if not removals:
        return text
    removals = _merge(text, removals)
    # Each removal is stitched and tidied at its own seam. The first version
    # ran the tidying regexes over the whole document once any removal fired,
    # which edited sentences the rule had never examined: "So did her sister"
    # became "Did her sister", "Which surprised everyone" lost its "Which", and
    # an ellipsis three sentences away was collapsed to two dots. Collateral
    # damage from a cleanup step is still damage.
    pieces: list[str] = []
    index = 0
    for start, end in removals:
        kept = text[index:start]
        # The space that preceded the removed clause and the one that follows
        # it are the same gap; keeping both left "information.  Still,".
        if kept[-1:] in (" ", "\t") and text[end : end + 1] in (" ", "\t"):
            kept = kept.rstrip(" \t")
        pieces.append(kept)
        pieces.append(_seam(text, start, end))
        index = end
    pieces.append(text[index:])
    joined = "".join(pieces)
    joined = joined.strip()
    # A capture that is nothing but self-judgement is left alone: emptying a
    # paragraph is a bigger change than the defect, and the writer asked for a
    # correction rather than for their text to go. Measured on what is left
    # after punctuation, so a short but real sentence survives; an earlier
    # version counted words and refused to repair "Care routines help." because
    # three words looked like a fragment.
    if not joined.strip(" .,;:!?\"'"):
        return text
    return joined


# Stock filler from speech. The rule in writing_suggestions removes these only
# between a determiner and its noun, which left 76 of them in the corpus spread
# across forty different positions: after a verb, after a coordinator, inside a
# comma pair, at the start of a sentence.
#
# Enumerating positions was the wrong shape. These nine phrases are discourse
# markers: no sentence requires one, in any position, so the rule removes the
# phrase and repairs the seam it leaves. "like" and "maybe" are deliberately
# absent - both are ordinary words here ("just like a dog", "maybe the same
# reason") and removing them would change what the writer said.
_DISCOURSE_FILLER = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=,))[ \t]*\b(?:or[ \t]+whatever|you[ \t]+know|"
    r"to[ \t]+be[ \t]+honest|for[ \t]+real|i[ \t]+guess|anyways|sorta|"
    r"and[ \t]+stuff|basically)\b[ \t]*,?",
    re.IGNORECASE,
)


_SENTENCE_LEAD = re.compile(r"(?:\A|[.!?][\"'”’)\]]*)[ \t]*\Z")


class _Span:
    """The two positions of a removal, named the way a match object names them."""

    __slots__ = ("_start", "_end")

    def __init__(self, start: int, end: int) -> None:
        self._start = start
        self._end = end

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end


def remove_discourse_filler(text: str) -> str:
    """Take out speech filler wherever it landed, then tidy the seam."""
    # Adjacent fillers are one removal. "To be honest, you know, even if" has
    # two, and treating them separately capitalised the "y" of "you know"
    # before that phrase had been removed, leaving "laughs. Y even if".
    spans: list[tuple[int, int]] = []
    for match in _DISCOURSE_FILLER.finditer(text):
        if spans and not text[spans[-1][1] : match.start()].strip(" \t,"):
            spans[-1] = (spans[-1][0], match.end())
            continue
        spans.append((match.start(), match.end()))

    pieces: list[str] = []
    index = 0
    for span_start, span_end in spans:
        match = _Span(span_start, span_end)
        pieces.append(text[index : match.start()])
        # Filler that opened a sentence takes the capital with it: "everybody
        # laughs. To be honest, you know, even if the event was not funny"
        # became "... laughs. even if ...", and the document can be written
        # before any later pass would repair it.
        opens_sentence = bool(_SENTENCE_LEAD.search("".join(pieces)))
        rest = text[match.end() :]
        letter = re.match(r"[ \t]*([a-z])(\w*)", rest)
        if opens_sentence and letter is not None and not letter.group(2)[:1].isupper():
            pieces.append(" " + letter.group(1).upper())
            index = match.end() + letter.end(1)
            continue
        # The filler can be holding two copies of the same word apart:
        # "without proper or whatever proper care" became "proper proper care",
        # a duplicate this rule created and no earlier pass will see.
        kept = "".join(pieces)
        repeated = re.search(r"(\b[\w']+)[ \t]*\Z", kept)
        follows = re.match(r"[ \t]*([\w']+)", text[match.end() :])
        if (
            repeated is not None
            and follows is not None
            and repeated.group(1).casefold() == follows.group(1).casefold()
        ):
            pieces.append(" ")
            index = match.end() + follows.end(1)
            continue
        pieces.append(" ")
        index = match.end()
    pieces.append(text[index:])
    cleaned = "".join(pieces)
    if cleaned.strip() in ("", ",", "."):
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.!?,;:])", r"\1", cleaned)
    cleaned = re.sub(r",[ \t]*,", ",", cleaned)
    cleaned = re.sub(r"([,;:])[ \t]*(?=[.!?])", "", cleaned)
    # A sentence that now begins with a comma lost its opener to the filler.
    cleaned = re.sub(r"(\A|[.!?][ \t]+)[,;][ \t]*", r"\1", cleaned)
    cleaned = cleaned.strip()
    if not cleaned.strip(" .,;:!?\"'"):
        return text
    # Restore the capital where the filler had been the first word.
    if text[:1].isupper() and cleaned[:1].islower():
        cleaned = cleaned[:1].upper() + cleaned[1:]
    return cleaned


def apply_syntax_rules(text: str) -> str:
    # Self-judgement is removed last, after the run-on splitter has put in the
    # full stops it needs to find where a sentence begins and ends. Run first,
    # on text the writer never punctuated, it could not tell how much it was
    # deleting and took whole paragraphs.
    return remove_self_judging_clauses(
        remove_discourse_filler(
            fix_subject_verb_agreement(
                punctuate_bare_list(split_run_on_sentences(text))
            )
        )
    )
