"""Measure what ordinary English the bundled dictionaries do not know.

The release gate charges an essay for every word neither bundled list has, and
on its first run nineteen of the twenty-four words it charged were correct
English: microgrids, cybersecurity, tradeoffs, outplanting, genotypes,
unaffordable, makerspaces. That is a dictionary problem, not a writing problem,
and a speller that does not know "cybersecurity" is weak for any user rather
than only for this corpus.

Before replacing or supplementing a word list, the gap has to be measured, or
there is no way to tell whether a candidate list fixed anything or simply
happened to contain the words in the essays being graded. This reports:

    how many distinct words the corpora use that neither list knows
    which of them the frequency list knows and Hunspell does not
    which of them neither knows, which is the real gap

Run it over the corpora, and over any candidate word list with --candidate to
see how much of the gap that list would close.

    .venv/Scripts/python.exe tools/dictionary_coverage.py
    .venv/Scripts/python.exe tools/dictionary_coverage.py --candidate words.txt

It reads corpora and prints. It changes nothing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from echo_link_core.polish_spelling import EnglishDictionary  # noqa: E402

# The same word pattern the grader uses, so a word counted here is a word that
# would be charged there. Anything else measures a different question.
WORD = re.compile(r"[^\W\d_][\w']*", re.UNICODE)
TAG = re.compile(r"<[^>]+>")

CORPORA = (
    ROOT / "test-corpus" / "error-riddled-20-docx",
    ROOT / "test-corpus" / "animal-pet-20",
    ROOT.parent / "20_Animal_Pet_Essays_800_Percent_More_Errors",
)


def document_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("word/document.xml").decode("utf-8")
    return TAG.sub("", raw.replace("</w:p>", "\n"))


def corpus_words(folder: Path) -> Counter:
    """Every lowercase word in a corpus, with how often it appears.

    Capitalised words are skipped for the same reason the grader skips them:
    they are names, and names are not the speller's business.
    """
    counts: Counter = Counter()
    if not folder.is_dir():
        return counts
    for path in sorted(folder.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        try:
            text = document_text(path)
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
        for word in WORD.findall(text):
            if not word[:1].isupper():
                counts[word.casefold()] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate",
        type=Path,
        default=None,
        help="a word list to score, one word per line",
    )
    parser.add_argument("--show", type=int, default=40)
    arguments = parser.parse_args()

    dictionary = EnglishDictionary()
    dictionary.warm()

    candidate: set[str] = set()
    if arguments.candidate:
        candidate = {
            line.strip().casefold()
            for line in arguments.candidate.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        print(f"candidate list: {len(candidate)} words")  # noqa: T201

    total: Counter = Counter()
    for folder in CORPORA:
        counts = corpus_words(folder)
        total.update(counts)
        print(f"{folder.name[:44]:46} {len(counts):>6} distinct words")  # noqa: T201

    hunspell_only = []
    neither = []
    for word in sorted(total):
        if dictionary.contains(word):
            continue
        value = dictionary.suggestion(word)
        if value and value[0].casefold() == word and value[1] == 0:
            hunspell_only.append(word)
        else:
            neither.append(word)

    print(  # noqa: T201
        f"\ndistinct words across every corpus: {len(total)}"
        f"\n  known to Hunspell                 : "
        f"{len(total) - len(hunspell_only) - len(neither)}"
        f"\n  frequency list only, Hunspell gap : {len(hunspell_only)}"
        f"\n  neither list knows them           : {len(neither)}"
    )

    # The words neither list knows are a mix of real English and the corpora's
    # seeded misspellings, and only a reader can tell them apart. Printed by
    # how often they appear, because a word used seven times is more likely to
    # be the subject of the essay than a typo.
    print("\nneither list, most used first:")  # noqa: T201
    for word in sorted(neither, key=lambda value: -total[value])[: arguments.show]:
        mark = "  <- candidate has it" if word in candidate else ""
        print(f"  {total[word]:>4}x  {word}{mark}")  # noqa: T201

    if candidate:
        closes = [word for word in neither if word in candidate]
        print(  # noqa: T201
            f"\ncandidate closes {len(closes)} of {len(neither)} unknown words"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
