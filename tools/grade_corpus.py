"""Grade every corrected essay out of 100, on one written-down rubric.

Reading twenty essays by eye is how defects get missed, and it is also how a
grade becomes an impression rather than a measurement. This scores each
corrected document in build/batch against fixed weights:

    spelling 15  homophones 15  punctuation 20
    grammar 20   coherence 15

scaled to 100 by SCALE below.

The weights come from the hand grade of essay 14 on 2026-08-13 (spelling A,
homophones A, punctuation A-, grammar B+, organization D, coherence F = 80), so
the two are comparable, except that organization has since been removed.

Organization left the rubric on 2026-08-19 at the owner's decision, and this is
the third category to go for one reason. Long sentences went, then meta
commentary, and now this: each measured something the corrector is not allowed
to change, so the term returned the same value whether the corrector worked
perfectly or never ran at all. Organization scored where the AUTHOR put an
ordinal marker. Essay 3 announces "Another benefit" three paragraphs before
"The first reason is", and the only repair available is to reorder the writer's
paragraphs, which the product must not do. About 4 of the 4.6 points the animal
corpus was missing from 95 sat in that term and were unreachable by design.

Scores from before that date are on the old scale and are not comparable.

Every category is measured from the document. Nothing is assumed, and the
per-essay counts are printed beside the score so a number can be checked
against the text that produced it.

    .venv/Scripts/python.exe tools/grade_corpus.py
    .venv/Scripts/python.exe tools/grade_corpus.py --directory build/batch

Read the limits before trusting a number:

* Coherence is detected as meta-commentary, sentences whose subject is the text
  rather than the topic. That is general, but it does not see an argument that
  is merely weak.
* Coherence currently has no scored term at all, so it is always full marks.
  That is stated again beside the code that does it. It inflates every score by
  its whole weight and is the next thing this rubric has to resolve.
* These are proxies. A gain here that does not also appear on essays the rules
  were never tuned against is overfitting, not a gain.

Nothing that scores may ask the correction rules what they would still change.
Three categories did, and a document that has been through those rules therefore
scored full marks by construction: held-out essay 2 was given 15 of 15 for
homophones while it read "the print in the textbook is to small for her" and
"while your putting it away". Homophones, punctuation and grammar are now scored
on a residual measured independently - confusable words in impossible positions,
clauses joined with no punctuation, and the ported grammar error score - and the
rule-based counts survive only as diagnostics, because they say which rule
missed. A rule that cannot see an error has to cost points, or the number moves
when a rule fires rather than when the writing improves.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.polish_advice import (  # noqa: E402
    long_sentence_advice,
    meta_commentary_advice,
)
from echo_link_core.polish_error_score import error_score  # noqa: E402
from echo_link_core.polish_pos import ADJECTIVE, NOUN, VERB, tags  # noqa: E402
from echo_link_core.polish_spelling import EnglishDictionary  # noqa: E402
from echo_link_core.polish_syntax import (  # noqa: E402
    fix_subject_verb_agreement,
    punctuate_bare_list,
    split_run_on_sentences,
)
from echo_link_core.writing_suggestions import (  # noqa: E402
    WritingSuggestionEngine,
    apply_all_suggestions,
)

# A word may contain accented letters. The earlier version of this pattern was
# [A-Za-z][A-Za-z']*, which split "cafe" written with an accent and then
# reported the fragment as an unknown word: three of the twenty-two unknown
# words in the 2026-08-13 grading were that, not a defect in any essay.
WORD = re.compile(r"[^\W\d_][\w']*", re.UNICODE)
# Two full stops are a slip. Three are an ellipsis the author may have meant,
# and without the lookbehind this matched the last two dots of every one of
# them, marking correct text as an artefact.
ARTEFACT = re.compile(r"[.!?]\s*,|,\s*,|\s+,|\(\s|\s\)|(?<!\.)\.\.(?!\.)")
FILLER = re.compile(
    r"\b(or whatever|you know|to be honest|for real|i guess|anyways|sorta|"
    r"and stuff|basically)\b",
    re.IGNORECASE,
)
# Sentences that comment on the writing rather than the topic, and judge it.
#
# "This essay is about a rooftop gardener" is a thesis statement and belongs in
# the essay. An earlier version of this pattern matched any self-reference and
# so charged every one of the twenty essays 2.5 points for having one, which is
# the proxy marking correct writing as a defect. The subject has to be
# self-referential AND the predicate has to be about whether the writing works.
#
# The version after that still failed the same way, in two places. It allowed a
# bare "it" as the self-reference, which is ordinary English: "It does not take
# much staff to keep that going" and "It is not a movie, and it does not need
# batteries" were both charged. And it carried six literal phrases copied out of
# the twenty essays it was written beside - "it still counts", "good but also
# not", "responsibility comes back again" among them - so it charged "if a kid
# reads comics all afternoon it still counts as reading" while flattering the
# corpus the strings came from. Six of the six coherence hits across the three
# held-out essays were correct writing.
#
# So the subject now has to name the writing, and "this part of the yard" is not
# that. "works" is not a judgement either, because "This essay is about a tour
# guide who works early mornings" is the thesis sentence the note above already
# said belongs in the essay.
#
# The second arm is a sentence that asserts something and takes it back before
# the full stop with nothing in between. That is incoherent whatever the subject
# is, so it needs no self-reference.
_SELF_REFERENCE = r"this\s+(?:part|paragraph|essay|sentence|section)\b(?!\s+of\b)"
_JUDGES_THE_WRITING = (
    r"(?:does not|doesn't|should probably|is mostly|sounds like|"
    r"counts|connects?|belongs?|fits?)"
)
META_COMMENTARY = re.compile(
    rf"\b{_SELF_REFERENCE}[^.!?]{{0,80}}\b{_JUDGES_THE_WRITING}\b"
    r"|\b(?:but|and) also not\b\s*[.!?]",
    re.IGNORECASE,
)
SENTENCE = re.compile(r"[^.!?]*[.!?]")
TOKEN = re.compile(r"[A-Za-z']+")
# Two independent clauses joined with a conjunction and no punctuation at all.
# Length alone is not the defect - a long single clause is merely long - so the
# conjunction has to be there. Measured over both corpora: at 25 words every hit
# is a real run-on, and at 20 the count triples with correct sentences in it.
_RUN_ON_WORDS = 25
_COORDINATOR = re.compile(r"\b(?:and|but|so|or|yet)\b", re.IGNORECASE)
_COPULA = ("is", "are", "was", "were", "feels", "felt", "seems", "seemed", "got", "gets")
# The space before the attributes is load-bearing. Without it "<w:t[^>]*>" also
# matches "<w:tab/>", because [^>]* happily eats "ab/", and the non-greedy scan
# that follows then runs on to the next "</w:t>" swallowing every tag in
# between. Eleven of the twenty essays in the 500-percent corpus contain a tab,
# and each one put a run of Word markup into the graded text:
#
#     </w:r><w:proofErr w:type="gramStart"/><w:r><w:t>rather
#
# That was read as prose. "proofErr" and "gramStart" were counted as misspelled
# words and charged to the essay, and it was very nearly reported to the owner
# as the app corrupting his documents. An XML parser says the text nodes are
# clean; the reader was the only thing broken.
#
# A break and a tab are read as well as the text, because they are whitespace
# the writer can see and dropping them joins the words on either side. The
# error-riddled corpus seeds stray line breaks inside a paragraph, so
# "sufficient by itself\nitself" arrived as the single word "itselfitself",
# was charged as a misspelling, and was very nearly reported as the app
# doubling words in the writer's document. The plain-text branch below keeps
# those breaks, and the same essay has to read the same whichever form it
# arrives in.
_WORD_PIECE = re.compile(
    r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|<w:(br|tab|cr)(?:\s[^>]*)?/>", re.S
)
_WORD_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
WEIGHTS = {
    "spelling": 15,
    "homophones": 15,
    "punctuation": 20,
    "grammar": 20,
    "coherence": 15,
}
# The five remaining weights sum to 85. Every per-error penalty below was tuned
# against its own category's weight, so the scale is applied once at the end
# rather than by rewriting five sets of coefficients: that keeps a spelling
# mistake worth exactly what it was worth relative to spelling, and only the
# printed number changes.
SCALE = 100 / sum(WEIGHTS.values())


def essay_documents(directory: Path) -> list[Path]:
    """The essays in a corpus directory, and nothing else.

    `test-corpus/animal-pet-20` carries a README beside the twenty essays, and
    grading it produced a 21st row scoring 98 that dragged nothing and meant
    nothing. Word also writes a "~$name.docx" owner file beside any document it
    has open, and correcting one of those produces garbage.
    """
    return [
        path
        for path in sorted([*directory.glob("*.docx"), *directory.glob("*.txt")])
        if not path.name.startswith("~$") and path.stem.casefold() != "readme"
    ]


def paragraphs(path: Path) -> list[str]:
    if path.suffix.lower() == ".txt":
        raw = path.read_text(encoding="utf-8")
        # Two plain-text shapes, and reading one as the other changes what is
        # being measured. The held-out essays put one paragraph per line with
        # no blank lines. The error-riddled corpus separates paragraphs with a
        # blank line and injects stray line breaks *inside* a paragraph as one
        # of its formatting defects, so reading line by line handed the engine
        # fragments that begin mid-sentence - a harder and different job than
        # the one the app does, where a captured Word paragraph keeps its own
        # line breaks.
        #
        # A blank line anywhere means blank lines are the separator.
        if re.search(r"\r?\n[ \t]*\r?\n", raw):
            blocks = re.split(r"\r?\n[ \t]*\r?\n", raw)
        else:
            blocks = raw.splitlines()
        return [
            unicodedata.normalize("NFC", block.strip())
            for block in blocks
            if block.strip()
        ]
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    found: list[str] = []
    for block in _WORD_PARAGRAPH.findall(xml):
        pieces: list[str] = []
        for piece in _WORD_PIECE.finditer(block):
            if piece.group(1) is not None:
                pieces.append(piece.group(1))
            else:
                pieces.append("\t" if piece.group(2) == "tab" else "\n")
        text = "".join(pieces)
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )
        if text.strip():
            found.append(unicodedata.normalize("NFC", text))
    return found


def _known(word: str, dictionary: EnglishDictionary) -> bool:
    """Whether the speller knows the word, ignoring diacritics.

    The bundled dictionary holds "cafe" but not the accented spelling, so a
    correctly written "cafe" with its accent was scored as a misspelling three
    times over. The word is right and the dictionary is incomplete; penalising
    the essay for that measures the dictionary.
    """
    folded = word.casefold()
    if dictionary.contains(folded):
        return True
    stripped = "".join(
        character
        for character in unicodedata.normalize("NFD", folded)
        if not unicodedata.combining(character)
    )
    if stripped != folded and dictionary.contains(stripped):
        return True
    return _frequency_dictionary_knows(folded, dictionary)


def _frequency_dictionary_knows(folded: str, dictionary: EnglishDictionary) -> bool:
    """Whether the second bundled word list knows this word exactly.

    The Hunspell list is not the only word list in the build and it is not the
    larger one. It is missing ordinary English that the 82,834-word frequency
    dictionary beside it has: "unaffordable" at 131,969 and "genotypes" at
    632,148. Charging an essay for those measures the dictionary, which is the
    same reason the accented "cafe" case above exists.

    Measured on 2026-08-19 on the first five essays of the coherent corpus.
    Twenty-four words were counted unknown; nineteen were correct English.
    Essay 3 scored 0 of 18 for spelling while its "misspellings" were
    "microgrids", "cybersecurity" and "tradeoffs".

    No frequency floor, because the list has no junk to keep out. Its lowest
    entries are "inhibitive", "rasbora", "insatiably" and "defoliate" at counts
    around 12,700; a floor would exclude real words and admit nothing.

    False accepts were measured against the 24,690 bundled misspellings rather
    than argued about: 171 of them appear here, 0.69 percent, and reading them
    shows they are real words that happen to be wrong somewhere else -
    "behaviour", "apologise", "barbeque", "affective", "campion". Treating
    those as known is correct for a count of unknown words, not a leak.

    That gap is now closed by derivation rather than by listing. The paragraph
    that stood here said "cybersecurity", "microgrid", "tradeoff", "makerspace"
    and "outplanting" were charged, that the coverage gap was real, and that
    they were deliberately not hand-added because adding them is memorising the
    answer. All three statements were correct and the last one still governs:
    none of those words appears anywhere in the source.

    What changed on 2026-08-27 is `recognises_compound`, which asks whether a
    word is English by productive word formation - a prefix on a known word, or
    two common content words joined - with the guards and the measured false
    accept rate documented beside it in polish_spelling.py. The product uses the
    same check, though for a different reason there - it keeps the run-together
    repair from tearing "kneeland" into "kneel and" - while here it stops the
    grader charging correct English as a misspelling. `outplanting` and `lidar`
    are still charged, because neither is derivable and their gap is still real.

    Said plainly, because it flatters this number: most of the movement in the
    score is the grader no longer being wrong, not the corrector getting
    better. The thresholds did not move and the corpus did not change.
    """
    value = dictionary.suggestion(folded)
    if value and value[0].casefold() == folded and value[1] == 0:
        return True
    return dictionary.recognises_compound(folded)


# Confusable words the speller can never see, because both spellings are real.
#
# These are written here rather than read from the correction rules on purpose.
# Asking the rules what they would still change measures the rules against
# themselves: text that has already been through them scores full marks whatever
# is left in it. Held-out essay 2 scored 15 of 15 for homophones while reading
# "the print in the textbook is to small for her" and "while your putting it
# away". A grader has to be able to see an error the product cannot.
#
# Each one was swept over the held-out essays, the twenty-essay corpus and every
# English sentence embedded in tests/, 2464 sentences, and ships only at zero
# false positives. The first version of the "to" rule charged "from noon to
# five", "worked together to correct the problem" and "moved to New York".


def _confusable_hits(sentence: str) -> list[str]:
    """Every confusable word used in a position its spelling cannot take."""
    found: list[str] = []
    tokens = TOKEN.findall(sentence)
    for index, token in enumerate(tokens):
        lowered = token.casefold()
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        after = tokens[index + 2] if index + 2 < len(tokens) else ""
        previous = tokens[index - 1].casefold() if index else ""
        if lowered == "to" and following:
            # "small" is also a noun, so asking for an unambiguous adjective
            # finds nothing. Nothing can be a noun in this position anyway,
            # because "to" is in front of it and there is no determiner. A word
            # that can be a verb is the infinitive, not a degree phrase. What
            # follows settles the rest: a degree word takes "for" or "to", and
            # anything else is the preposition doing its ordinary job.
            if (
                ADJECTIVE in tags(following)
                and VERB not in tags(following)
                and after.casefold() in ("for", "to")
            ):
                found.append(f"to {following} -> too")
        elif lowered == "your" and following and _is_only_verb(following):
            found.append(f"your {following} -> you're")
        elif lowered == "then" and previous and _is_comparative(tokens[index - 1]):
            found.append(f"{tokens[index - 1]} then -> than")
        elif lowered == "lose" and previous in _COPULA:
            found.append(f"{previous} lose -> loose")
        elif lowered == "loose" and previous in ("to", "will", "would", "might", "could", "may"):
            found.append(f"{previous} loose -> lose")
        elif lowered == "its" and following:
            # Same shape as "to": an adjective after a possessive is normally
            # modifying the noun that follows it, so the word after decides.
            if (
                ADJECTIVE in tags(following)
                and VERB not in tags(following)
                and NOUN not in tags(following)
                and not (after and (NOUN in tags(after) or ADJECTIVE in tags(after)))
            ):
                found.append(f"its {following} -> it's")
    return found


def _is_only_verb(word: str) -> bool:
    classes = tags(word)
    return bool(classes) and classes == frozenset({VERB})


def _is_comparative(word: str) -> bool:
    """Whether the word is a comparative, judged from its base form.

    The bundled lexicon holds base forms only, so "wider" and "taller" are not
    in it at all and asking about the inflected word finds nothing. The base is,
    which is why this strips the ending rather than looking the whole word up.
    """
    lowered = word.casefold()
    if lowered in ("more", "less", "fewer", "better", "worse"):
        return True
    if not lowered.endswith("er"):
        return False
    bases = [lowered[:-2], lowered[:-1]]
    # "bigger" doubles the consonant, so the base is three letters shorter.
    if len(lowered) > 4 and lowered[-3] == lowered[-4]:
        bases.append(lowered[:-3])
    return any(tags(base) == frozenset({ADJECTIVE}) for base in bases)


def _run_on_sentences(sentences: list[str]) -> list[str]:
    """Sentences that join clauses with a conjunction and no punctuation."""
    return [
        sentence
        for sentence in sentences
        if len(sentence.split()) > _RUN_ON_WORDS
        and not re.search(r"[,;:]", sentence)
        and _COORDINATOR.search(sentence)
    ]


def grade(path: Path, dictionary: EnglishDictionary, engine: WritingSuggestionEngine) -> dict:
    body = paragraphs(path)
    text = "\n".join(body)

    # Capitalised tokens are names, which are not the speller's business.
    # Distinct words, not occurrences. One word the dictionary does not have is
    # one thing for a writer to resolve however many times they use it, and
    # charging per occurrence measures how often a subject comes up rather than
    # how correct the writing is. Measured on 2026-08-19: essay 3 of the
    # coherent corpus scored 0 of 18 for spelling on fifteen charges that were
    # five distinct words, seven of them the single word "microgrids".
    #
    # No error is hidden by this. Every distinct unknown word is still charged,
    # and the full list goes into the report so a low score can be read rather
    # than guessed at.
    unknown_words = [
        word
        for word in WORD.findall(text)
        if not word[:1].isupper() and not _known(word, dictionary)
    ]
    unknown = sorted({word.casefold() for word in unknown_words})
    spelling = max(0.0, WEIGHTS["spelling"] - 1.5 * len(unknown))

    # Three categories used to be scored on what the deterministic rules would
    # still change, which grades the rules against themselves: any document that
    # has been through them scores full marks by construction. Those counts are
    # still computed, because they say which rule missed, but they no longer
    # score. What scores is a residual measured without asking the rules.
    caught = 0
    for line in body:
        repaired = apply_all_suggestions(line, engine.analyze(line))
        caught += sum(1 for a, b in zip(line.split(), repaired.split()) if a != b)
    sentences = SENTENCE.findall(text)
    confusables = [hit for sentence in sentences for hit in _confusable_hits(sentence)]
    homophones = max(0.0, WEIGHTS["homophones"] - 2.0 * len(confusables))

    artefacts = len(ARTEFACT.findall(text))
    unsplit = sum(1 for line in body if split_run_on_sentences(line) != line)
    unlisted = sum(1 for line in body if punctuate_bare_list(line) != line)
    # Sentence length is ADVICE, not a punctuation defect, and this stopped
    # being scored on 2026-08-17 at the owner's decision.
    #
    # Every one of the six over-long sentences in essay 1 of the 800-percent
    # corpus was correctly punctuated - semicolons, commas and conjunctions all
    # right - and the source and the corrected document contained the same six.
    # A term that returns an identical value whether the corrector worked
    # perfectly or never ran measures the corpus, not the product, and it was
    # costing 12 of the 20 punctuation points. `split_run_on_sentences` already
    # declines to touch these on purpose: splitting a long correct sentence
    # rewrites the writer's prose instead of repairing it.
    #
    # They are still counted and still reported, in the advice column, because
    # the writer should hear about them. They are not charged.
    advice = long_sentence_advice(text)
    long_runs = [sentence for sentence in sentences if len(sentence.split()) > 45]
    # A sentence long enough to be advised about is not also charged as a
    # run-on, which is why long_runs is still computed.
    run_ons = [
        sentence for sentence in _run_on_sentences(sentences) if sentence not in long_runs
    ]
    punctuation = max(
        0.0,
        WEIGHTS["punctuation"]
        - 3.0 * artefacts
        - 2.0 * len(run_ons),
    )

    agreement = sum(1 for line in body if fix_subject_verb_agreement(line) != line)
    filler = len(FILLER.findall(text))
    errors = error_score(text)
    grammar = max(0.0, WEIGHTS["grammar"] - 2.0 * errors - 1.5 * filler)

    # Meta commentary is ADVICE, not a coherence charge, from 2026-08-17 at the
    # owner's decision and for the same reason as sentence length: the only
    # repair available is deleting the writer's sentence, so the source and the
    # corrected document contain the same ones and the term returned an
    # identical value whether the corrector worked perfectly or never ran.
    # Essay 2 lost ten of its fifteen coherence points to four seeded sentences
    # while scoring full marks on spelling, homophones, punctuation and
    # grammar.
    #
    # NOTE that this leaves coherence with no scored term at all: it is now
    # always full marks. Said plainly rather than left for a reader to notice,
    # because a category that cannot vary is not measuring anything, and the
    # honest options are to give it a real term or to drop it and reweight.
    meta = sum(1 for sentence in sentences if META_COMMENTARY.search(sentence))
    advice = advice + meta_commentary_advice(text, META_COMMENTARY)
    coherence = float(WEIGHTS["coherence"])

    return {
        "name": path.stem,
        "total": round(
            SCALE * (spelling + homophones + punctuation + grammar + coherence)
        ),
        # Scaled as well as the total, so a column and the score it feeds are
        # on the same scale and a reader can add the row up.
        "spelling": SCALE * spelling,
        "homophones": SCALE * homophones,
        "punctuation": SCALE * punctuation,
        "grammar": SCALE * grammar,
        "coherence": SCALE * coherence,
        "unknown": len(unknown),
        # Named, like the confusable words are, because an unknown-word count
        # is unreadable on its own: fifteen charges on essay 3 looked like a
        # broken corrector and were "microgrids" and "cybersecurity".
        "unknown_words": unknown,
        "confusable": len(confusables),
        # Separated on 2026-08-17: these were added together, so a charged
        # run-on and an advisory long sentence were indistinguishable in the
        # report. Essay 1 read "runon=7" when it had one run-on and six long
        # sentences, which is what sent a reader looking for seven defects.
        "run_ons": len(run_ons),
        "advice": len(advice),
        "errors": errors,
        "artefacts": artefacts,
        "filler": filler,
        "meta": meta,
        # Diagnostics only. These say which rule missed; they do not score,
        # because they are the rules being asked about themselves.
        "caught": caught,
        "agreement": agreement,
        "unsplit": unsplit,
        "unlisted": unlisted,
        "hits": confusables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade the corrected essays.")
    parser.add_argument("--directory", default="build/batch")
    arguments = parser.parse_args()

    directory = ROOT / arguments.directory
    paths = essay_documents(directory)
    if not paths:
        print(f"no documents in {directory}", file=sys.stderr)  # noqa: T201
        return 1

    dictionary = EnglishDictionary()
    dictionary.warm()
    engine = WritingSuggestionEngine()
    rows = [grade(path, dictionary, engine) for path in paths]

    print(  # noqa: T201
        f"{'essay':34}{'/100':>5}{'spell':>7}{'homo':>6}{'punct':>7}"
        f"{'gram':>6}{'coh':>6}   unknown/confusable/runon/error/filler/meta/advice"
    )
    for row in rows:
        print(  # noqa: T201
            f"{row['name'][:32]:34}{row['total']:>5}{row['spelling']:>7.0f}"
            f"{row['homophones']:>6.0f}{row['punctuation']:>7.0f}"
            f"{row['grammar']:>6.0f}{row['coherence']:>6.0f}"
            f"      {row['unknown']}/{row['confusable']}/{row['run_ons']}/"
            f"{row['errors']}/{row['filler']}/{row['meta']}/{row['advice']}"
        )
    # The confusable words are named, because a homophone the reader can see is
    # the one number here that is worth acting on directly.
    for row in rows:
        for hit in row["hits"]:
            print(f"  {row['name'][:32]:34}{hit}")  # noqa: T201
    mean = sum(row["total"] for row in rows) / len(rows)
    lowest = min(rows, key=lambda row: row["total"])
    print(  # noqa: T201
        f"\nmean {mean:.1f} over {len(rows)} essays, "
        f"lowest {lowest['total']} ({lowest['name'][:32]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
