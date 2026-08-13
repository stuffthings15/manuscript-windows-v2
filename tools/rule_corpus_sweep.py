"""Show every candidate a suggestion rule produces, over two corpora.

This tool exists because of a specific failure on 2026-08-13. A rule that added
commas to an unpunctuated list was measured against the 20 animal-pet essays,
found clean, and shipped. The essays' bare lists happen to have single-word
items. The first multi-word list outside that corpus, in an existing test
fixture, was destroyed: "counted volunteer hours crop donations cancelled
workdays" became "volunteer, hours, crop, donations".

One corpus is not a measurement. This sweeps two:

* ``essays``  - the 20 locked .docx essays in test-corpus/animal-pet-20
* ``fixtures`` - every English sentence already embedded as a string literal in
  tests/, which is text the rule under examination was not designed around

Read the output. A rule ships when every hit in both corpora is a correction
you would defend to the writer, not when the count looks small.

    .venv/Scripts/python.exe tools/rule_corpus_sweep.py
    .venv/Scripts/python.exe tools/rule_corpus_sweep.py --rule plural-subject-agreement
    .venv/Scripts/python.exe tools/rule_corpus_sweep.py --corpus fixtures --context 60
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.writing_suggestions import (  # noqa: E402
    WritingSuggestionEngine,
    _protected_spans,
)

CORPUS = ROOT / "test-corpus" / "animal-pet-20"
TESTS = ROOT / "tests"
_WORD_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_WORD_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
# A fixture string is worth sweeping when it reads as English prose. Regex
# patterns, file paths and identifiers share the quotes but not the shape, and
# feeding them to the rules produces noise that hides the real hits.
_PROSE = re.compile(r"^[A-Z][^\n\r]{24,}[.!?]$")
_NOT_PROSE = re.compile(r"[\\{}<>|^$*+\[\]]|::|__|\.py\b|https?://")


def _essay_paragraphs() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(CORPUS.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        for paragraph in _WORD_PARAGRAPH.findall(xml):
            text = "".join(_WORD_TEXT.findall(paragraph))
            text = (
                text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&apos;", "'")
            )
            if text.strip():
                found.append((path.stem, text))
    return found


def _fixture_sentences() -> list[tuple[str, str]]:
    """Every prose string literal in tests/, by source file.

    Parsed rather than grepped so that a multi-line implicit concatenation
    arrives as the one sentence the test actually asserts on. That matters:
    the list rule's victim was written across four source lines.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(TESTS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = node.value.strip()
            if not _PROSE.match(text) or _NOT_PROSE.search(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            found.append((path.stem, text))
    return found


CORPORA = {
    "essays": _essay_paragraphs,
    "fixtures": _fixture_sentences,
}


def sweep(corpus: str, rule: str | None, context: int) -> int:
    engine = WritingSuggestionEngine()
    counts: dict[str, int] = {}
    for source, text in CORPORA[corpus]():
        # _protected_spans is read only so that a hit inside a URL or a code
        # span is reported the same way the engine would treat it.
        _protected_spans(text)
        for suggestion in engine.analyze(text):
            name = _rule_of(suggestion)
            if rule is not None and name != rule:
                continue
            counts[name] = counts.get(name, 0) + 1
            window = text[max(0, suggestion.start - context) : suggestion.end + context]
            print(
                f"{name:34} {source[:30]:32} "
                f"{suggestion.original!r} -> {suggestion.replacement!r}\n"
                f"{'':34} ...{window}..."
            )
    print()
    for name in sorted(counts):
        print(f"{counts[name]:6}  {name}")
    return sum(counts.values())


def _rule_of(suggestion) -> str:
    # WritingSuggestion carries the category and title, not the internal rule
    # name, so the title is the stable public label to group by.
    return suggestion.title


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=(*CORPORA, "both"), default="both")
    parser.add_argument("--rule", default=None, help="only this rule title")
    parser.add_argument("--context", type=int, default=40)
    arguments = parser.parse_args()

    names = tuple(CORPORA) if arguments.corpus == "both" else (arguments.corpus,)
    total = 0
    for name in names:
        print(f"=== {name} ===")
        total += sweep(name, arguments.rule, arguments.context)
        print()
    print(f"{total} candidates over {len(names)} corpora")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
