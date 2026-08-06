"""Find content words a correction removed without explaining.

A proofreader that quietly rewrites what the writer said is worse than one
that misses an error. Two of those shipped in a single live essay run:

    "when the oner ENTERS the ROOM"  ->  "when the owner measures the room"
    "a cat may not grete a person"   ->  "a cat may not get as much as"

Both passed every guard in `selected_text`, because the guards there look for
negation flips, perspective shifts and a list of literal fixture phrases. A
plain verb swap walks straight through all of them.

The test here is deliberately narrow: a content word present in the source and
absent from the candidate is a defect *unless* the candidate contains a word
that plausibly explains it - the same word respelled, an inflection of it, or a
known non-standard form of it. Nothing about grammar or tone is judged. That
keeps honest repairs ("oner" -> "owner", "wuld" -> "would") away from the
guard while "enters" -> "measures" is caught, because "measures" is nowhere
near "enters" by any of those relations.

`selected_text` uses this to refuse a candidate. `tools/correction_quality_scorer.py`
uses the same function to measure the corpus, so the number a run reports and
the rule the product enforces can never drift apart.
"""
from __future__ import annotations

from collections import Counter
import re

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")

# Function words carry structure, not claims. Losing one is a style edit;
# losing "enters" is a different sentence. Kept small on purpose - every word
# added here is a word the guard stops protecting.
_FUNCTION_WORDS = frozenset(
    """
    a an the this that these those
    i me my mine myself you your yours yourself
    he him his she her hers it its they them their theirs
    we us our ours who whom whose which what
    am is are was were be been being
    do does did done doing
    have has had having
    will would shall should can could may might must
    of to in on at by for with from into onto upon about over under
    as if then than so and or but nor yet because while when where
    there here not no nor never very just really quite also too
    all any some each every both few more most other another such
    own same only own
    """.split()
)

# Non-standard forms whose correct repair is not a near-spelling of the
# original. Without these, fixing "aint" to "is not" reads as a meaning change,
# because "aint" and "not" share almost no letters. Each entry is a word the
# corrector is allowed to replace with any of the listed words.
_KNOWN_REWRITES: dict[str, frozenset[str]] = {
    "aint": frozenset({"is", "are", "am", "not", "isnt", "arent"}),
    "gonna": frozenset({"going", "to"}),
    "wanna": frozenset({"want", "to"}),
    "gotta": frozenset({"got", "have", "to"}),
    "kinda": frozenset({"kind", "of"}),
    "sorta": frozenset({"sort", "of"}),
    "lemme": frozenset({"let", "me"}),
    "gimme": frozenset({"give", "me"}),
    "cuz": frozenset({"because"}),
    "becuz": frozenset({"because"}),
    "coz": frozenset({"because"}),
    "til": frozenset({"until", "till"}),
    "thru": frozenset({"through"}),
    "tho": frozenset({"though", "although"}),
    "alot": frozenset({"lot", "many", "much"}),
    "dont": frozenset({"do", "not"}),
    "cant": frozenset({"cannot", "can", "not"}),
    "wont": frozenset({"will", "not"}),
    "isnt": frozenset({"is", "not"}),
    "arent": frozenset({"are", "not"}),
    "wasnt": frozenset({"was", "not"}),
    "werent": frozenset({"were", "not"}),
    "doesnt": frozenset({"does", "not"}),
    "didnt": frozenset({"did", "not"}),
    "couldnt": frozenset({"could", "not"}),
    "wouldnt": frozenset({"would", "not"}),
    "shouldnt": frozenset({"should", "not"}),
    "hasnt": frozenset({"has", "not"}),
    "havent": frozenset({"have", "not"}),
}

# Intensifiers and hedges. Cutting one is the concision a proofreader is for:
# "I really just very much like it" -> "I like it" says the same thing. These
# are not function words - they are content words whose loss is always
# explained, so they stay out of `_FUNCTION_WORDS` and cannot be used to
# explain the loss of anything else.
_SOFTENERS = frozenset(
    """
    much many quite rather somewhat actually basically simply literally
    definitely certainly absolutely totally completely entirely honestly
    obviously clearly essentially generally usually often sometimes
    kind sort bit lot lots pretty fairly slightly extremely incredibly
    kinda sorta
    """.split()
)

_SUFFIXES = ("ing", "edly", "ies", "ied", "est", "ers", "er", "ed", "es", "ly", "s")


def _normalise(word: str) -> str:
    """Fold a word to the form the comparisons use: lowercase, letters only."""
    return re.sub(r"[^a-z]", "", word.casefold())


def content_words(text: str) -> list[str]:
    """Words that carry a claim, in order, normalised."""
    values = []
    for match in _WORD.finditer(text):
        word = _normalise(match.group(0))
        if len(word) < 3 or word in _FUNCTION_WORDS:
            continue
        values.append(word)
    return values


def _stem(word: str) -> str:
    """Crude suffix strip, enough to see "moves" and "move" as one word.

    A real stemmer is not wanted here: over-stemming would make "cat" and
    "cats" and "cater" all one word and let a genuine swap through.
    """
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            if suffix == "ies":
                return stem + "y"
            if stem.endswith(word[-len(suffix) - 1 : -len(suffix)]) and len(stem) > 3:
                # doubled consonant before the suffix: "running" -> "run"
                if stem[-1] == stem[-2]:
                    return stem[:-1]
            return stem
    return word


def edit_distance(left: str, right: str, *, limit: int = 3) -> int:
    """Damerau-Levenshtein (optimal string alignment), capped at `limit`.

    Transpositions must cost one, not two. Without that, "rqeuirements" sits
    two edits from "requirements" and a correct repair of a very common typing
    slip reads to the guard as a substituted word.
    """
    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous: list[int] = list(range(len(right) + 1))
    before_previous: list[int] = []
    for i, left_char in enumerate(left, start=1):
        current = [i] + [0] * len(right)
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
            if (
                i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                current[j] = min(current[j], before_previous[j - 2] + cost)
        if min(current) > limit:
            return limit + 1
        before_previous, previous = previous, current
    return previous[-1]


def _explains(lost: str, replacement: str) -> bool:
    """Whether `replacement` is a believable repair of `lost`.

    Believable means one of: the same word inflected, one typing edit away, or
    two edits away *while growing or holding length from a strong shared
    prefix* - which is what a dropped or doubled letter looks like.

    The length rule is what separates the two live failures. "grete" -> "get"
    is two edits but the answer is shorter and loses the shared "gr", so it is
    refused; "becuz" -> "because" is two edits, longer, and keeps "be", so it
    is allowed.
    """
    if lost == replacement:
        return True
    if replacement in _KNOWN_REWRITES.get(lost, frozenset()):
        return True
    if _stem(lost) == _stem(replacement):
        return True
    distance = edit_distance(lost, replacement)
    if distance <= 1:
        return True
    if distance == 2 and len(replacement) >= len(lost) and lost[:1] == replacement[:1]:
        return True
    return False


def _all_words(text: str) -> list[str]:
    """Every word, normalised, function words included.

    A repair is often a function word: "wuld of" becomes "would have", "aint"
    becomes "is not". Judging explanations against content words alone flagged
    all three of those as meaning changes, because the words that explained
    them had been filtered out before the comparison.
    """
    return [
        word
        for word in (_normalise(match.group(0)) for match in _WORD.finditer(text))
        if word
    ]


def unexplained_losses(source: str, candidate: str) -> tuple[str, ...]:
    """Content words the candidate dropped and did not account for.

    Empty means the candidate said the same things the source said. Anything
    returned is a word the writer used and the corrector removed without
    offering a respelling or an inflection of it.
    """
    source_words = content_words(source)
    if not source_words:
        return ()
    candidate_words = content_words(candidate)
    remaining = Counter(source_words) - Counter(candidate_words)
    if not remaining:
        return ()
    # Only words the candidate introduced can explain a loss. Reusing a word
    # the source already had would let "the cat rests" explain a dropped
    # "runs" simply because "rests" was already present.
    introduced = _introduced_words(source, candidate)
    losses: list[str] = []
    for lost in remaining.elements():
        if lost in _SOFTENERS:
            continue
        if not any(_explains(lost, value) for value in introduced):
            losses.append(lost)
    return tuple(losses)


def _introduced_words(source: str, candidate: str) -> list[str]:
    return list((Counter(_all_words(candidate)) - Counter(_all_words(source))).elements())


def describe_losses(source: str, candidate: str) -> tuple[str, ...]:
    """Losses rendered as `was -> nearest offered word` for a report."""
    described: list[str] = []
    introduced = _introduced_words(source, candidate)
    for lost in unexplained_losses(source, candidate):
        if introduced:
            nearest = min(introduced, key=lambda value: edit_distance(lost, value, limit=99))
            described.append(f"{lost} -> {nearest}")
        else:
            described.append(f"{lost} -> (removed)")
    return tuple(described)
