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
_SELF_JUDGING = (
    # subject is the writing, predicate is about whether the writing works
    # The subject is "this part" or "this paragraph", never a bare "it": the
    # first version accepted "it does not ..." and deleted "It does not need
    # batteries, although some habitats do need lights", which is content.
    r"this[ \t]+(?:part|paragraph)[ \t]+(?:does[ \t]+not|doesn't|dont)"
    r"[ \t]+connect",
    r"this[ \t]+(?:part|paragraph)[ \t]+should[ \t]+probably[ \t]+be[ \t]+about",
    # "it still counts" ends the sentence when it is the writing excusing
    # itself. "The owner counts the hours and it still counts as work" carries
    # on into real content, and deleting from there took the sentence with it.
    r"it[ \t]+still[ \t]+counts[ \t]*(?=[.!?]|$)",
    r"(?:this[ \t]+essay[ \t]+is[ \t]+mostly[ \t]+saying|"
    r"now[ \t]+it[ \t]+suddenly[ \t]+sounds[ \t]+like[ \t]+instructions)",
    # a claim and its own denial in one breath
    r"good[ \t]+but[ \t]+also[ \t]+not",
    r"two[ \t]+reasons[ \t]+at[ \t]+once",
    r"maybe[ \t]+the[ \t]+same[ \t]+reason",
    r"responsibility[ \t]+(?:comes?|came)[ \t]+back[ \t]+again",
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
    pieces: list[str] = []
    index = 0
    for start, end in removals:
        pieces.append(text[index:start])
        index = end
    pieces.append(text[index:])
    joined = "".join(pieces)
    # Deleting a sentence leaves the space that separated it from the next one.
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    joined = re.sub(r"[ \t]+([.!?,;:])", r"\1", joined)
    # Tidy what a removal leaves at the seam: a comma with nothing after it, a
    # doubled full stop where the deleted clause carried its own, and a
    # conjunction left dangling at the start of what remains.
    joined = re.sub(r"[ \t]*,(?=[ \t]*[.!?])", "", joined)
    joined = re.sub(r"([.!?])[ \t]*,", r"\1", joined)
    joined = re.sub(r"\.[ \t]*\.", ".", joined)
    # Dropping a dangling conjunction leaves the next word starting the
    # sentence, so it has to be capitalised in the same step. Without this the
    # pass produced ". the person wakes up" and left the repair to the next
    # one, which meant the document could be written with a lowercase sentence.
    joined = re.sub(
        r"(\A|[.!?][ \t]+)(?:but|and|so|which|yet)\b[ \t,]*(\w)",
        lambda match: match.group(1) + match.group(2).upper(),
        joined,
        flags=re.IGNORECASE,
    )
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


def apply_syntax_rules(text: str) -> str:
    # Self-judgement is removed last, after the run-on splitter has put in the
    # full stops it needs to find where a sentence begins and ends. Run first,
    # on text the writer never punctuated, it could not tell how much it was
    # deleting and took whole paragraphs.
    return remove_self_judging_clauses(
        fix_subject_verb_agreement(
            punctuate_bare_list(split_run_on_sentences(text))
        )
    )
