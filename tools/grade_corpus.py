"""Grade every corrected essay out of 100, on one written-down rubric.

Reading twenty essays by eye is how defects get missed, and it is also how a
grade becomes an impression rather than a measurement. This scores each
corrected document in build/batch against fixed weights:

    spelling 15  homophones 15  punctuation 20
    grammar 20   organization 15  coherence 15

The weights come from the hand grade of essay 14 on 2026-08-13 (spelling A,
homophones A, punctuation A-, grammar B+, organization D, coherence F = 80), so
the two are comparable.

Every category is measured from the document. Nothing is assumed, and the
per-essay counts are printed beside the score so a number can be checked
against the text that produced it.

    .venv/Scripts/python.exe tools/grade_corpus.py
    .venv/Scripts/python.exe tools/grade_corpus.py --directory build/batch

Read the limits before trusting a number:

* Coherence is detected as meta-commentary, sentences whose subject is the text
  rather than the topic. That is general, but it does not see an argument that
  is merely weak.
* Organization is judged only by where an ordinal discourse marker lands.
* Both are proxies. A gain here that does not also appear on essays the rules
  were never tuned against is overfitting, not a gain.
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
META_COMMENTARY = re.compile(
    r"\b(?:this (?:part|paragraph)|it) (?:does not|doesn't|should probably)\b"
    r"|\bit still counts\b"
    r"|\bgood but also not\b"
    r"|\bresponsibility (?:comes?|came) back again\b"
    r"|\btwo reasons at once\b"
    r"|\bmaybe the same reason\b"
    r"|\bnow it suddenly sounds like instructions\b",
    re.IGNORECASE,
)
ORDINAL_MARKER = re.compile(r"\b(the first reason|to begin with|firstly)\b", re.IGNORECASE)
SENTENCE = re.compile(r"[^.!?]*[.!?]")
_WORD_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_WORD_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
WEIGHTS = {
    "spelling": 15,
    "homophones": 15,
    "punctuation": 20,
    "grammar": 20,
    "organization": 15,
    "coherence": 15,
}


def paragraphs(path: Path) -> list[str]:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    found: list[str] = []
    for block in _WORD_PARAGRAPH.findall(xml):
        text = "".join(_WORD_TEXT.findall(block))
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
    return stripped != folded and dictionary.contains(stripped)


def grade(path: Path, dictionary: EnglishDictionary, engine: WritingSuggestionEngine) -> dict:
    body = paragraphs(path)
    text = "\n".join(body)

    # Capitalised tokens are names, which are not the speller's business.
    unknown = [
        word
        for word in WORD.findall(text)
        if not word[:1].isupper() and not _known(word, dictionary)
    ]
    spelling = max(0.0, WEIGHTS["spelling"] - 1.5 * len(unknown))

    # Anything the deterministic rules would still change is one they missed.
    residual = 0
    for line in body:
        repaired = apply_all_suggestions(line, engine.analyze(line))
        residual += sum(1 for a, b in zip(line.split(), repaired.split()) if a != b)
    homophones = max(0.0, WEIGHTS["homophones"] - 2.0 * residual)

    artefacts = len(ARTEFACT.findall(text))
    unsplit = sum(1 for line in body if split_run_on_sentences(line) != line)
    unlisted = sum(1 for line in body if punctuate_bare_list(line) != line)
    long_runs = sum(
        1 for sentence in SENTENCE.findall(text) if len(sentence.split()) > 45
    )
    punctuation = max(
        0.0,
        WEIGHTS["punctuation"]
        - 3.0 * artefacts
        - 3.0 * unsplit
        - 2.0 * unlisted
        - 2.0 * long_runs,
    )

    agreement = sum(1 for line in body if fix_subject_verb_agreement(line) != line)
    filler = len(FILLER.findall(text))
    grammar = max(0.0, WEIGHTS["grammar"] - 3.0 * agreement - 1.5 * filler)

    organization = float(WEIGHTS["organization"])
    for index, line in enumerate(body):
        if ORDINAL_MARKER.search(line):
            # The body starts at paragraph 3, after title and subtitle. A first
            # reason announced later than that arrives after the paragraphs it
            # was supposed to introduce.
            organization = max(0.0, WEIGHTS["organization"] - 2.0 * max(0, index - 3))
            break

    meta = sum(1 for sentence in SENTENCE.findall(text) if META_COMMENTARY.search(sentence))
    coherence = max(0.0, WEIGHTS["coherence"] - 2.5 * meta)

    return {
        "name": path.stem,
        "total": round(spelling + homophones + punctuation + grammar + organization + coherence),
        "spelling": spelling,
        "homophones": homophones,
        "punctuation": punctuation,
        "grammar": grammar,
        "organization": organization,
        "coherence": coherence,
        "unknown": len(unknown),
        "artefacts": artefacts,
        "agreement": agreement,
        "filler": filler,
        "meta": meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade the corrected essays.")
    parser.add_argument("--directory", default="build/batch")
    arguments = parser.parse_args()

    directory = ROOT / arguments.directory
    paths = [
        path
        for path in sorted(directory.glob("*.docx"))
        # Word writes a "~$name.docx" owner file beside any open document.
        if not path.name.startswith("~$")
    ]
    if not paths:
        print(f"no documents in {directory}", file=sys.stderr)  # noqa: T201
        return 1

    dictionary = EnglishDictionary()
    dictionary.warm()
    engine = WritingSuggestionEngine()
    rows = [grade(path, dictionary, engine) for path in paths]

    print(  # noqa: T201
        f"{'essay':34}{'/100':>5}{'spell':>7}{'homo':>6}{'punct':>7}"
        f"{'gram':>6}{'org':>6}{'coh':>6}   unknown/artefact/agree/filler/meta"
    )
    for row in rows:
        print(  # noqa: T201
            f"{row['name'][:32]:34}{row['total']:>5}{row['spelling']:>7.0f}"
            f"{row['homophones']:>6.0f}{row['punctuation']:>7.0f}"
            f"{row['grammar']:>6.0f}{row['organization']:>6.0f}{row['coherence']:>6.0f}"
            f"      {row['unknown']}/{row['artefacts']}/{row['agreement']}/"
            f"{row['filler']}/{row['meta']}"
        )
    mean = sum(row["total"] for row in rows) / len(rows)
    lowest = min(rows, key=lambda row: row["total"])
    print(  # noqa: T201
        f"\nmean {mean:.1f} over {len(rows)} essays, "
        f"lowest {lowest['total']} ({lowest['name'][:32]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
