"""Which capitalised words are names, and which are ordinary words.

Split out of polish.py. Getting this wrong is expensive in both directions: a
name treated as an ordinary word gets "corrected" into something the writer did
not write, and an ordinary word frozen as a name blocks every repair in the
sentence around it. The rules here were measured against the essay corpus, and
the comments record what each one cost to find.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher



_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


_NAME = re.compile(
    # Hyphenated compounds with capitalized parts ("Wi-Fi", "Jean-Pierre")
    # extract as one name; splitting them produced fragments ("Wi", "Fi")
    # that read as invented entities and rejected correct spelling
    # standardization. A lowercase continuation ("Vietnamese-speaking") is
    # an adjective construction, not a name, and stays split.
    r"\b(?:[A-Z][a-z]{1,30}(?:-[A-Z][a-z]{0,30})*)"
    r"(?:\s+[A-Z][a-z]{1,30}(?:-[A-Z][a-z]{0,30})*){0,5}\b"
)


_HONORIFIC_NAME = re.compile(
    r"\b(?:Dr|Mr|Mrs|Ms|Prof)\.\s+[A-Z][a-z]{1,30}"
    r"(?:\s+[A-Z][a-z]{1,30}){0,2}\b"
)


_VERSIONED_NAME = re.compile(
    r"\b(?!(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|At)\b)"
    r"[A-Z][A-Za-z0-9.+-]{1,30}\s+\d+(?:\.\d+){0,3}\b"
)


_NON_ENTITY_STARTERS = frozenset(
    {
        "i", "me", "my", "mine", "we", "us", "our", "ours",
        "you", "your", "yours", "he", "him", "his", "she", "her", "hers",
        "it", "its", "they", "them", "their", "theirs",
        "a", "an", "the", "and", "or", "but", "so", "if", "then", "because",
        "however", "this", "that", "these", "those", "some", "any", "each",
        "every", "for", "to", "of", "in", "on", "at", "by", "with", "from",
        "as", "into", "over", "under", "is", "are", "was", "were", "be",
        "been", "being", "do", "does", "did", "have", "has", "had", "will",
        "would", "can", "could", "may", "might", "must", "should", "shall",
        "not", "no", "nor", "yes", "when", "while", "where", "which", "who",
        "what", "why", "how",
        "don't", "doesn't", "didn't", "can't", "won't", "isn't", "aren't",
        "wasn't", "weren't", "haven't", "hasn't", "hadn't", "couldn't",
        "wouldn't", "shouldn't", "mustn't",
        "i'm", "we're", "you're", "they're", "it's", "that's", "there's",
        "here's", "i've", "we've", "you've", "they've", "i'll", "we'll",
        "you'll", "they'll", "i'd", "we'd", "you'd", "they'd",
    }
)


def _edit_distance_at_most(a: str, b: str, limit: int) -> bool:
    """Damerau-Levenshtein: a swapped pair of letters counts as one edit.

    Every caller asks the same question - is this word a misspelling of that
    one - and transposition is the most common typo there is. Counting it as
    two edits put "Utercht" out of reach of "Utrecht" at a limit of one, so
    correcting the city's name was refused as "named entity removed" and the
    paragraph lost its correction. The speller already treats transposition as
    its own category for the same reason.
    """
    if abs(len(a) - len(b)) > limit:
        return False
    before_previous: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        best = i
        for j, cb in enumerate(b, start=1):
            value = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            )
            if (
                before_previous is not None
                and j >= 2
                and ca == b[j - 2]
                and a[i - 2] == cb
            ):
                value = min(value, before_previous[j - 2] + 1)
            current.append(value)
            best = min(best, value)
        if best > limit:
            return False
        before_previous = previous
        previous = current
    return previous[-1] <= limit


def _misspelled_non_entity_starter(word: str) -> bool:
    """True when a sentence-opening word is a typo of a known function word.

    Real names are rarely within two edits of "their", "these" or "when", and a
    name that is would be caught by every other guard in the validator. A typo
    of one is common in exactly the text this product exists for.
    """
    if len(word) < 3:
        return False
    if len(word) == 3:
        # "Wen somebody says" opens essay 1, and the four-character floor below
        # let it through as a proper name. It was then frozen as an opaque
        # token, the model correctly wrote "When", the token vanished, and the
        # whole 627-character section was refused as changed protected content.
        #
        # Two edits is far too loose at this length - "Ann" is one substitution
        # from "and" - so a three-letter word only counts as a typo when a
        # single letter was dropped from the function word. "when" minus "h" is
        # "wen"; no deletion from "and" produces "ann", so real short names are
        # still protected.
        return any(
            starter[:1] == word[:1]
            and len(starter) == len(word) + 1
            and any(
                starter[:index] + starter[index + 1 :] == word
                for index in range(len(starter))
            )
            for starter in _NON_ENTITY_STARTERS
            if len(starter) >= 4
        )
    return any(
        starter[:1] == word[:1]
        and abs(len(starter) - len(word)) <= 2
        and _edit_distance_at_most(word, starter, 2)
        for starter in _NON_ENTITY_STARTERS
        if len(starter) >= 4
    )


def _named_entity_respelled_in(entity: str, text: str) -> bool:
    """True when every word of a "removed" name appears respelled in the text.

    A real removal drops the name. A respelling keeps it one or two letters
    different, which the exact-match entity check cannot tell apart, so
    correcting a capitalized misspelling looked like deleting a name.

    Every word has to be accounted for, so dropping one word of a two-word name
    still fails.
    """
    words = [word for word in re.findall(r"[A-Za-z']+", entity) if len(word) >= 4]
    if not words:
        return False
    tokens = re.findall(r"[A-Za-z']+", text)
    for word in words:
        folded = word.casefold()
        limit = 1 if len(folded) < 8 else 2
        if not any(
            # Three, not four: the repair is often what shortens the word, and
            # "Pett" -> "Pet" was missed because the fixed spelling fell under
            # the floor that the misspelling cleared.
            len(token) >= 3
            and _same_name_respelled(token.casefold(), folded, limit)
            for token in tokens
        ):
            return False
    return True


def _same_name_respelled(candidate: str, source: str, limit: int) -> bool:
    """Whether two spellings are the same name, one of them mistyped.

    Close is not enough on its own. "Malik" -> "Mali" is one edit and is a
    different person; forgiving it let a model quietly shorten a name, which is
    the exact failure the entity guard exists to catch. What separates a typo
    from a different name here is the ending: a repair fixes a doubled letter or
    a missing vowel inside the word ("Pett" -> "Pet", "Csco" -> "Cusco"), while
    a truncation or an addition changes the last letter.
    """
    if candidate.rstrip("s") == source.rstrip("s") and candidate != source:
        # An added or removed plural "s" is agreement, not identity: "Parrot
        # Make" -> "Parrot Makes" is the subject-verb repair being asked for,
        # and _named_entities reads any title-cased phrase as a name.
        return True
    if not _edit_distance_at_most(candidate, source, limit):
        return False
    if len(candidate) == len(source):
        # Substitutions and swapped letters: "Utercht" -> "Utrecht".
        return True
    return candidate[-1:] == source[-1:]


def _entity_dictionary():
    """One shared dictionary. Warming it is expensive; this is called per name."""
    from .polish_spelling import EnglishDictionary

    return EnglishDictionary()


def _document_occurrences(word: str, document: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", document, re.IGNORECASE))


def _capitalised_ordinary_word(value: str, document: str) -> bool:
    """Is this capitalised single word an ordinary word rather than a name?

    Measured over the twenty-essay corpus: place names occur 2 to 6 times in a
    document and never exactly once, while 14 of the 20 ordinary words wrongly
    frozen as names occur exactly once. Dictionary membership alone is not
    enough - Netherlands, Peru, Malta, France and Utrecht are all in the
    bundled word list - and "appears lowercase elsewhere" fails backwards,
    because Therefore never does and Utrecht always does. Both were tried and
    measured; see tools/entity_frequency_probe.py.
    """
    if not document or " " in value:
        return False
    if _document_occurrences(value, document) != 1:
        return False
    try:
        return _entity_dictionary().contains(value.casefold())
    except Exception:  # noqa: BLE001 - a missing dictionary must not unprotect names
        return False


_TYPO_CORRECTION_FREQUENCY = 25_000_000


def _capitalised_misspelling(value: str) -> bool:
    """Is this capitalised word a misspelling rather than a name?

    Essays 4, 8 and 15 died in three consecutive runs on "Afer", "Educcation",
    "Peeple" and "Learniing". Each is capitalised at a clause opening, so it
    reads as a name, gets frozen as an opaque immutable token, and the model
    cannot spell it correctly because it cannot touch it. The section then
    falls back to the rules with every other typo in it left standing.

    A real name is either known to the speller outright (Cusco, Aarhus, Colmar
    all resolve at distance 0) or has no near neighbour at all (Csco, Sanjay,
    Kenji). A typo has exactly one edit between it and a word the language uses
    constantly. That is the whole test.
    """
    if len(value) < 4:
        # Too short to tell apart. "Ann" is one edit from "and"; the existing
        # three-letter rule in _misspelled_non_entity_starter handles that case
        # with a much narrower test.
        return False
    try:
        dictionary = _entity_dictionary()
        if dictionary.contains(value.casefold()):
            return False
        suggestion = dictionary.suggestion(value.casefold())
    except Exception:  # noqa: BLE001 - a missing dictionary must not unprotect names
        return False
    if suggestion is None:
        return False
    _word, distance, frequency = suggestion
    return distance == 1 and frequency >= _TYPO_CORRECTION_FREQUENCY


def _named_entities(text: str, document: str = "") -> set[str]:
    ignored = {
        "A",
        "An",
        "And",
        "After",
        "Analyst",
        "At",
        "Before",
        "Because",
        "Better",
        "By",
        "But",
        "Cleaning",
        "Dogs",
        "During",
        "Each",
        "Every",
        "Facilities",
        "For",
        "Funding",
        "He",
        "Her",
        "His",
        "Having",
        "However",
        "I",
        "If",
        "In",
        "It",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Friday",
        "Monday",
        "Neither",
        "No",
        "Nobody",
        "Not",
        "Nurse",
        "On",
        "One",
        "Or",
        "Operations",
        "Orientation",
        "Room",
        "Saturday",
        "She",
        "Sunday",
        "That",
        "Thats",
        "The",
        "There",
        "Teh",
        "They",
        "These",
        "This",
        "Thursday",
        "Technicians",
        "Tuesday",
        "Until",
        "Visitors",
        "We",
        "Wednesday",
        "When",
        "While",
        "Those",
        "Both",
        "Last",
        "People",
        "Project",
        "Signs",
        "Some",
        "Several",
        "Sometimes",
        "Staff",
        "Success",
        "Which",
        "You",
    }
    values: set[str] = set()
    common_words = {word.casefold() for word in ignored} | {
        "another", "also", "then", "even", "maybe", "eventually",
        "instead", "still", "just", "only", "again", "here", "now",
        "yes", "well", "so", "as", "of", "to", "with", "without",
    }
    for match in _NAME.finditer(text):
        value = match.group(0)
        if value in ignored:
            continue
        words = value.split()
        if len(words) > 1 and all(
            word.casefold() in common_words for word in words
        ):
            # Sentence splits can leave adjacent capitalized function words
            # ("Another One", "Another The"). A "name" made entirely of
            # common words is casing noise, not an entity.
            continue
        prefix = text[: match.start()].rstrip()
        sentence_start = not prefix or prefix[-1:] in ".!?\n"
        first_word = value.split(None, 1)[0]
        if sentence_start and " " not in value:
            lowered = value.casefold()
            trailing = re.match(r"['’](\w+)", text[match.end() :])
            lowered_full = (
                f"{lowered}'{trailing.group(1).casefold()}" if trailing else lowered
            )
            if (
                lowered in _NON_ENTITY_STARTERS
                or lowered_full in _NON_ENTITY_STARTERS
                # The multi-word check above already treats these as function
                # words; the single-word check consulted a different set and
                # disagreed. "With out proper cair" -> "Without proper care" was
                # refused as "new named entity added" because "without" was in
                # one set and not the other.
                or lowered in common_words
            ):
                # Ordinary sentence casing of a pronoun, contraction, or
                # function word ("Your", "Don't", "The") is not a new entity.
                continue
            if _misspelled_non_entity_starter(lowered):
                # A typo of a function word is still not a name. "Thier is
                # emotional benefits" starts with a misspelled "Their", and
                # correcting it was refused as "named entity removed" - which
                # cost the paragraph its whole correction. The set above already
                # declares these words are never names; that has to survive the
                # writer misspelling one, or the list grows a typo at a time
                # ("Teh" is already in it).
                continue
            if _capitalised_ordinary_word(value, document):
                # "Therefore", "Watching", "Without" and "Weather" open a
                # clause, get read as names, and are frozen as opaque tokens the
                # model cannot write around. It drops them through every retry
                # and the section falls back to the rules with each typo intact,
                # which is why seven essays never corrected a single word.
                continue
        repeated_lowercase = bool(
            re.search(
                rf"(?<!\w){re.escape(value.casefold())}(?!\w)",
                text[match.end() :],
            )
        )
        plural_sentence_subject = bool(
            value.endswith("s")
            and re.match(
                r"\s+(?:are|were|have|do|don't|dont)\b",
                text[match.end() :],
                re.IGNORECASE,
            )
        )
        if (
            sentence_start
            and " " not in value
            and (repeated_lowercase or plural_sentence_subject)
        ):
            # A capitalized sentence starter that later appears lowercase is
            # an ordinary word, not a person or product name. Protecting it as
            # opaque makes small models treat plural nouns such as "Dogs" as
            # singular proper names.
            continue
        # A correction normally capitalizes the first word of a sentence.
        # Do not mistake ordinary starts such as "During The..." or
        # "The Project..." for a newly invented named entity.
        if sentence_start and first_word in ignored:
            suffix = value.split(None, 1)[1] if " " in value else ""
            if len(suffix.split()) >= 2:
                values.add(suffix)
            continue
        if " " not in value and _capitalised_misspelling(value):
            # A misspelling is not a name wherever it appears, so this is not
            # limited to sentence openings the way the frequency rule above is.
            continue
        values.add(value)
    return values


def _named_entity_matches_source_phrase(value: str, source: str) -> bool:
    entity_words = tuple(word.casefold() for word in _WORD.findall(value))
    source_words = tuple(word.casefold() for word in _WORD.findall(source))
    if not entity_words:
        return False
    if len(entity_words) == 1:
        return entity_words[0] in source_words
    for start in range(len(source_words) - len(entity_words) + 1):
        window = source_words[start : start + len(entity_words)]
        mismatches = [
            (candidate_word, source_word)
            for candidate_word, source_word in zip(entity_words, window)
            if candidate_word != source_word
        ]
        if not mismatches:
            return True
        if (
            len(mismatches) <= 3
            and len(entity_words) >= 3
            and all(
                min(len(candidate_word), len(source_word)) >= 4
                and abs(len(candidate_word) - len(source_word)) <= 2
                and SequenceMatcher(
                    a=candidate_word,
                    b=source_word,
                    autojunk=False,
                ).ratio()
                >= 0.78
                for candidate_word, source_word in mismatches
            )
        ):
            return True
        if len(mismatches) != 1:
            continue
        candidate_word, source_word = mismatches[0]
        if (
            min(len(candidate_word), len(source_word)) >= 4
            and abs(len(candidate_word) - len(source_word)) <= 2
            and SequenceMatcher(
                a=candidate_word,
                b=source_word,
                autojunk=False,
            ).ratio()
            >= 0.8
        ):
            return True
    return False


def _maximal_named_entities(text: str, document: str = "") -> tuple[str, ...]:
    values = _named_entities(text, document)
    values.update(match.group(0) for match in _HONORIFIC_NAME.finditer(text))
    values.update(match.group(0) for match in _VERSIONED_NAME.finditer(text))
    return tuple(
        sorted(
            (
                value
                for value in values
                if not any(
                    value != other
                    and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", other)
                    for other in values
                )
            ),
            key=lambda value: (text.find(value), -len(value)),
        )
    )


def _runtime_opaque_entities(text: str, document: str = "") -> tuple[str, ...]:
    """Use opaque tokens only where they improve safety without hurting fluency.

    Single-word names need the strongest guard because small models sometimes
    spell-correct them (for example, Malik -> Mali). Versioned product names
    must also remain byte-for-byte exact. Multiword names remain protected by
    the validator and the one targeted safety retry; tokenizing every long name
    made otherwise-correct generations fail before validation.
    """
    versioned = {
        match.group(0) for match in _VERSIONED_NAME.finditer(text)
    }
    return tuple(
        value
        for value in _maximal_named_entities(text, document)
        if " " not in value or value in versioned
    )
