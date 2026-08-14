"""Which word classes a word can belong to.

Three rules were written and withdrawn on 2026-08-13 for one reason: this layer
did not exist. A run-on splitter could not tell "Greece it offers" from "shows
the pet is theirs", and a list rule could not tell "volunteer hours" (one item)
from "companionship routine" (two).

The table answers "can this word be a noun" and, more importantly, "can it be
*only* a noun". It does not disambiguate. `needs` is a noun and a verb in this
table and stays that way in every sentence, because deciding between them needs
a parser this application does not carry. Rules are expected to refuse when the
answer is ambiguous, and that refusal is the feature.

The data is generated from the bundled Hunspell dictionary by
tools/build_pos_lexicon.py. Loading is lazy: writing_suggestions.py must stay
free of I/O, so nothing here may be imported into that module's path.
"""
from __future__ import annotations

from pathlib import Path
import threading

from .paths import resource_path

NOUN = "n"
VERB = "v"
ADJECTIVE = "j"
ADVERB = "r"
DETERMINER = "det"
PREPOSITION = "prep"
PRONOUN = "pron"
CONJUNCTION = "conj"
AUXILIARY = "aux"

# The generated table is built from inflection evidence, which function words
# do not have: it reports "the" as a verb and "is" as a noun, because "the" and
# "is" appear in the dictionary with flags that mean something else entirely.
# Closed-class words are therefore declared here and override the table. There
# are only a few hundred of them and they never change, which is exactly the
# kind of list worth writing by hand.
_CLOSED_CLASS: dict[str, frozenset[str]] = {}


def _declare(words: str, *classes: str) -> None:
    for word in words.split():
        _CLOSED_CLASS[word] = frozenset(classes)


_declare(
    "a an the this that these those each every either neither both all any "
    "some no another my your his her its our their whose which what",
    DETERMINER,
)
_declare(
    "i me you he him she it we us they them myself yourself himself herself "
    "itself ourselves yourselves themselves who whom whoever anyone everyone "
    "someone nobody everybody somebody anybody nothing something everything "
    "mine yours hers ours theirs",
    PRONOUN,
)
_declare(
    "about above across after against along among around at before behind "
    "below beneath beside besides between beyond by despite down during "
    "except for from in inside into near of off on onto outside over past "
    "since through throughout to toward towards under underneath until up "
    "upon with within without",
    PREPOSITION,
)
_declare(
    "and but or nor so yet because although though while whereas unless "
    "whether if since as than when where wherever whenever however therefore "
    "moreover furthermore otherwise",
    CONJUNCTION,
)
_declare(
    "am is are was were be been being have has had do does did will would "
    "shall should can could may might must ought",
    AUXILIARY,
    VERB,
)
_declare(
    "not never always often sometimes rarely seldom already still yet just "
    "very quite rather too also only even almost nearly here there now then "
    "today tomorrow yesterday again once twice perhaps maybe indeed instead",
    ADVERB,
)
# "that" and "which" introduce relative clauses as well as determining a noun,
# and "there" is an existential subject as well as an adverb. Each extra
# reading is here so that a rule asking is_only() gets a refusal rather than a
# confident wrong answer; these three words caused the withdrawn run-on rule to
# split "entertainment that is educational".
_CLOSED_CLASS["that"] = frozenset({DETERMINER, PRONOUN, CONJUNCTION})
_CLOSED_CLASS["which"] = frozenset({DETERMINER, PRONOUN})
_CLOSED_CLASS["there"] = frozenset({ADVERB, PRONOUN})
_CLOSED_CLASS["one"] = frozenset({DETERMINER, PRONOUN, NOUN})

_LEXICON: dict[str, frozenset[str]] | None = None
_LOCK = threading.Lock()


def _lexicon_path() -> Path:
    return resource_path("resources", "polish", "pos", "en_US.pos")


def _load() -> dict[str, frozenset[str]]:
    global _LEXICON
    with _LOCK:
        if _LEXICON is not None:
            return _LEXICON
        table: dict[str, frozenset[str]] = {}
        path = _lexicon_path()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            # A missing table must not break correction. Every caller treats an
            # unknown word as ambiguous and refuses, so the rules that depend
            # on this layer simply stop firing.
            _LEXICON = {}
            return _LEXICON
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            word, _, classes = line.partition("\t")
            if not word:
                continue
            table[word] = frozenset(classes) if classes else frozenset()
        _LEXICON = table
        return _LEXICON


def tags(word: str) -> frozenset[str]:
    """Every class this word can belong to, empty when it is unknown."""
    if not word:
        return frozenset()
    key = word.lower()
    closed = _CLOSED_CLASS.get(key)
    if closed is not None:
        return closed
    return _load().get(key, frozenset())


def can_be(word: str, word_class: str) -> bool:
    """True when this class is one of the readings, or the word is unknown."""
    found = tags(word)
    return not found or word_class in found


def is_only(word: str, *classes: str) -> bool:
    """True when every reading of this word falls inside the given classes.

    This is the test the rules use, and it is deliberately strict: an unknown
    word answers False. A rule built on this fires on evidence, never on the
    absence of evidence.
    """
    found = tags(word)
    return bool(found) and found <= frozenset(classes)


def loaded() -> bool:
    """Whether the table has been read from disk yet, for the timing test."""
    return _LEXICON is not None
