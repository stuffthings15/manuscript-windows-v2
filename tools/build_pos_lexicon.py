"""Build the part-of-speech lexicon from the bundled Hunspell dictionary.

The plan for this called for Open English WordNet's index files. The bundled
Hunspell dictionary turned out to be a better source, and it is the document's
own point that Hunspell encodes morphology rather than a word list: the affix
flag on each entry says which inflections the word takes, and which inflections
a word takes is a statement about its word class.

    dog/SM              takes -s and 's                  noun
    walk/MDRZGS         takes -ed and -ing               verb
    quick/MNRYXTP       takes -ly, -er and -est          adjective
    volunteer/SGMD      takes -s and -ing                noun and verb, both

That last one is the reason this layer exists. "volunteer hours" is a noun
phrase and "they volunteer hours" is a clause, and only a lexicon that admits
both readings can stop a rule from guessing.

Using the bundled dictionary means no download, no new dependency, and no new
licence: it already ships, and its notice is already in
resources/polish/THIRD_PARTY_NOTICES.md.

Run once; the output is committed.

    .venv/Scripts/python.exe tools/build_pos_lexicon.py
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = ROOT / "resources" / "polish" / "hunspell-en-us" / "en_US.dic"
AFFIXES = ROOT / "resources" / "polish" / "hunspell-en-us" / "en_US.aff"
OUTPUT = ROOT / "resources" / "polish" / "pos" / "en_US.pos"

NOUN = "n"
VERB = "v"
ADJECTIVE = "j"
ADVERB = "r"

# What each affix flag says about the word class of its base. Only the flags
# whose evidence holds in both directions are listed, because a lexicon that
# guesses is worse than one that says nothing: a caller asking is_only() would
# get a confident wrong answer instead of a refusal.
#
#   D -ed  G -ing  J -ings   only a verb inflects this way
#   T -est                   only an adjective has a superlative
#   P -ness                  builds a noun from an adjective
#
# Deliberately absent, with the entry that disproved each one:
#
#   M -'s     quick/MNRYXTP  attaches to adjectives too
#   Y -ly     hour/MYS       "hourly" comes from a noun
#   B -able   habit/ISB      "habitable" comes from a noun
#   R -er                    builds a comparative and an agent noun alike
#   N -ion    quick/MNRYXTP  "quicken" is a verb built from an adjective
#   L V X                    same ambiguity, no case worth the risk
#
#   S -s                     plural or third person, so it means noun unless
#                            the entry also inflects as a verb, and both when
#                            it does: "walk" is a noun and a verb, and saying
#                            so is the whole point of this table
_BASE_CLASS = {
    "D": VERB,
    "G": VERB,
    "J": VERB,
    "T": ADJECTIVE,
    "P": ADJECTIVE,
}
# The class of the form each flag produces, which is not always the class of
# the base: "quickly" is an adverb built from an adjective.
_FORM_CLASS = {
    "D": VERB,
    "G": VERB,
    "J": NOUN,
    "Y": ADVERB,
    "P": NOUN,
    "T": ADJECTIVE,
    "R": None,
    "Z": NOUN,
    "B": ADJECTIVE,
    "L": NOUN,
    "N": NOUN,
    "X": NOUN,
    "V": ADJECTIVE,
    "S": None,
    "M": NOUN,
    "H": ADJECTIVE,
}
_SFX_HEADER = re.compile(r"^SFX\s+(?P<flag>\S+)\s+(?P<cross>[YN])\s+(?P<count>\d+)\s*$")
_SFX_RULE = re.compile(
    r"^SFX\s+(?P<flag>\S+)\s+(?P<strip>\S+)\s+(?P<add>\S+)\s+(?P<condition>\S+)"
)


def _suffix_rules(text: str) -> dict[str, list[tuple[str, str, re.Pattern[str]]]]:
    rules: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {}
    for line in text.splitlines():
        if _SFX_HEADER.match(line):
            continue
        match = _SFX_RULE.match(line)
        if match is None:
            continue
        condition = match.group("condition")
        rules.setdefault(match.group("flag"), []).append(
            (
                "" if match.group("strip") == "0" else match.group("strip"),
                "" if match.group("add") == "0" else match.group("add"),
                re.compile(("." if condition == "." else condition) + r"$"),
            )
        )
    return rules


def _inflect(word: str, flag: str, rules) -> str | None:
    for strip, add, condition in rules.get(flag, ()):
        if not condition.search(word):
            continue
        if strip and not word.endswith(strip):
            continue
        return (word[: len(word) - len(strip)] if strip else word) + add
    return None


def _base_classes(flags: str) -> set[str]:
    classes = {_BASE_CLASS[flag] for flag in flags if flag in _BASE_CLASS}
    # -er alone builds an agent noun ("walker"); with -est it is a comparative
    # and the base is an adjective. On its own it proves nothing either way.
    if "R" in flags and "T" in flags:
        classes.add(ADJECTIVE)
    # -s is the plural of a noun or the third person of a verb, so it is
    # evidence of a noun reading whatever else the entry says. "walk/MDRZGS"
    # first came out verb-only, which is the dangerous direction: is_only()
    # would have answered "verb" confidently about a word that is also a noun.
    # Over-admitting readings only ever makes a rule refuse.
    if "S" in flags:
        classes.add(NOUN)
    # A word whose only inflection is the possessive is a noun, unless the
    # entry already shows adjective or verb inflections; "quick/MNRYXTP" takes
    # -'s as well, which is why this is a fallback and not a flag mapping.
    if "M" in flags and not classes:
        classes.add(NOUN)
    return classes


def build() -> dict[str, set[str]]:
    rules = _suffix_rules(AFFIXES.read_text(encoding="utf-8", errors="replace"))
    lexicon: dict[str, set[str]] = {}
    lines = DICTIONARY.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[1:]:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        word, _, flags = entry.partition("/")
        word = word.strip()
        if not word or not word.isalpha() or not word.islower():
            continue
        classes = _base_classes(flags)
        if classes:
            lexicon.setdefault(word, set()).update(classes)
        else:
            lexicon.setdefault(word, set())
        for flag in flags:
            form = _inflect(word, flag, rules)
            if form is None or not form.isalpha():
                continue
            form_class = _FORM_CLASS.get(flag)
            if form_class is None:
                # -s and -er take the class of whatever the base can be.
                form_class_set = classes if flag == "S" else set()
                lexicon.setdefault(form, set()).update(form_class_set)
                continue
            lexicon.setdefault(form, set()).add(form_class)
        # Every verb has a third person in -s, whether or not the entry
        # carries the -s flag. "offer/JGMD" does not, so "offers" was reaching
        # the table only as a spurious generation from "off" plus -ers, tagged
        # noun and nothing else. A rule asking is_only() would have been told
        # confidently that "it offers companionship" contains no verb.
        if VERB in classes:
            third_person = _inflect(word, "S", rules)
            if third_person and third_person.isalpha():
                lexicon.setdefault(third_person, set()).add(VERB)
    return lexicon


def main() -> int:
    if not DICTIONARY.exists():
        print(f"missing {DICTIONARY}", file=sys.stderr)  # noqa: T201
        return 1
    lexicon = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# Word classes derived from resources/polish/hunspell-en-us by\n"
            "# tools/build_pos_lexicon.py. One line per form: word<TAB>classes,\n"
            "# where n=noun v=verb j=adjective r=adverb. An empty class list\n"
            "# means the dictionary knows the word but says nothing about its\n"
            "# class, which callers must treat as unknown, never as a default.\n"
        )
        for word in sorted(lexicon):
            handle.write(f"{word}\t{''.join(sorted(lexicon[word]))}\n")
    tagged = sum(1 for classes in lexicon.values() if classes)
    print(  # noqa: T201
        f"{len(lexicon)} forms, {tagged} with a class, written to "
        f"{OUTPUT.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
