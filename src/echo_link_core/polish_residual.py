"""A second look at words the speller deliberately refused to guess.

`EnglishDictionary.suggestion` returns nothing when two candidates are equally
close, and that refusal is correct. Measured on 2026-08-17 against the
800-percent corpus, the words left behind and their two best candidates were:

    nees   -> needs  vs knees    prson -> person vs prison
    esay   -> easy   vs essay    wrte  -> write  vs wrote
    mssy   -> messy  vs missy    saing -> saying vs saving

Frequency is the wrong tie-break for every one of them. It prefers "easy" for
"esay", and the writer meant "essay" - the word appears in "intentionally
error-filled practice esay". Loosening the speller enough to catch "nees"
would bake that wrong guess in permanently, so the speller must keep refusing.

What separates person from prison is the sentence, and the model reads the
sentence. The speller's own docstring says so: it refuses "leaving the
misspelling in place for the model". Nothing then re-checked after the model
ran, so on Windows those words survived to the finished document. This module
is that missing re-check.

It never repairs anything itself. It finds the words still worth asking about,
and it judges the answer that comes back. The judging is the important half: a
residual repair may only change the words that were unknown, and every one of
them must become a word the dictionary knows. Anything else - a reworded
sentence, a dropped clause, a swap of some other word - is refused whole, so a
second model call cannot quietly rewrite a sentence that was already correct.
"""
from __future__ import annotations

import re
import unicodedata


_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _folded(word: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", word.casefold())
        if not unicodedata.combining(character)
    )


def is_known(word: str, dictionary: object) -> bool:
    """Whether the speller knows the word, ignoring diacritics.

    The bundled dictionary holds "cafe" but not the accented spelling, and a
    correctly written word must never be treated as residue.

    Two kinds of correct English used to land here as residue, and both were
    measured on 2026-08-27 against one ordinary sentence. Twelve words were
    reported misspelled and all twelve were correct:

        long-term short-term hip-hop follow-through brass-band well-known
        state-of-the-art self-driving        hyphenated, parts all known
        tradeoffs cybersecurity tiltmeters stormwater   derivable compounds

    That is not only a wrong answer. Every one of them sent the sentence back
    to the model with the instruction "correct only the misspelled words:
    long-term, cybersecurity", which is an invitation to damage correct text,
    costs a whole generation per section, and was being caught only by
    `accept_residual_repair` refusing the answer afterwards.
    """
    lowered = word.casefold()
    try:
        if dictionary.contains(lowered):
            return True
        if dictionary.contains(_folded(word)):
            return True
    except AttributeError:
        return True

    # A hyphenated word is known when every part is. The writer typed the
    # hyphens, so the split is theirs rather than a guess, which makes this far
    # safer than splitting a run-together word: "teh-cat" still comes back
    # unknown because "teh" does.
    if "-" in lowered:
        parts = [part for part in lowered.split("-") if part]
        if len(parts) > 1:
            try:
                if all(
                    dictionary.contains(part) or dictionary.contains(_folded(part))
                    for part in parts
                ):
                    return True
            except AttributeError:
                return True

    # Compounds and prefixed words the bundled lists happen not to carry.
    # Same check the corrector and the grader use, so all three agree on what
    # counts as English instead of each having its own opinion.
    recognises = getattr(dictionary, "recognises_compound", None)
    if callable(recognises):
        try:
            return bool(recognises(word))
        except (AttributeError, TypeError, ValueError):
            return False
    return False


def residual_unknown_words(text: str, dictionary: object) -> tuple[str, ...]:
    """Words a reader would call misspelled that survived the correction.

    Capitalised words are skipped. Place and personal names are not the
    speller's business - "Arequipa", "Kisumu" and "Dlaat" are all unknown to
    the dictionary and only one of them is a mistake, and a corrector that
    rewrites names does more damage than the typo it fixes.
    """
    if not isinstance(text, str):
        raise TypeError("Residual text must be a string.")
    found: list[str] = []
    seen: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0)
        if word[:1].isupper():
            continue
        if is_known(word, dictionary):
            continue
        if word.casefold() in seen:
            continue
        seen.add(word.casefold())
        found.append(word)
    return tuple(found)


def accept_residual_repair(
    original: str,
    candidate: str,
    dictionary: object,
) -> str | None:
    """The repaired sentence, or None when the answer may not be trusted.

    The contract is deliberately narrow, because this runs after a sentence has
    already been corrected and validated once. Everything it accepts is a
    spelling repair and nothing else:

    * the same words in the same order, so nothing was added, dropped or moved;
    * every word that differs was unknown before;
    * every word that differs is known now.

    A model that returns a nicer sentence, or fixes the spelling and also
    rephrases, is refused. That is not a failure of the model - it is the only
    way to be sure a second pass cannot undo the first.
    """
    if not isinstance(original, str) or not isinstance(candidate, str):
        raise TypeError("Residual repair text must be a string.")
    if not candidate.strip():
        return None
    before = _WORD.findall(original)
    after = _WORD.findall(candidate)
    if len(before) != len(after):
        return None
    changed = 0
    for old, new in zip(before, after):
        if old == new:
            continue
        if is_known(old, dictionary):
            # A word that was already correct may not be touched here.
            return None
        if not is_known(new, dictionary):
            # Replacing one unknown word with another is not a repair.
            return None
        changed += 1
    if not changed:
        return None
    return candidate


__all__ = [
    "accept_residual_repair",
    "is_known",
    "residual_unknown_words",
]
