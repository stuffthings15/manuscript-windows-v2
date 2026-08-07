"""Measure whether occurrence count separates a name from an ordinary word.

Seven of nineteen essays die because an ordinary word capitalised at a clause
opening - "Therefore", "Watching", "Without", "Weather" - is frozen as an
opaque immutable token. The model cannot reword around a token that reads as a
proper name, drops it, and the section falls back to the rules with every typo
intact.

Two discriminators were tried and disproved before this one:

  * dictionary membership - fails, because Netherlands, Peru, Malta, France
    and Utrecht are all in the bundled word list
  * the word appearing in lowercase elsewhere - fails backwards, because
    "Therefore" never does and "Utrecht" and "Cusco" both do

This one holds on essays 3, 4, 8 and 10: no real place name occurs only once,
and the ordinary words occur exactly once. Run it before building the rule, and
run it again on the other sixteen essays before trusting it.

    .venv/Scripts/python.exe tools/entity_frequency_probe.py
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402
from echo_link_core.polish import _runtime_opaque_entities  # noqa: E402

# Judged by reading the essays: these are real places, everything else the
# entity detector froze in them is an ordinary word or a typo of one.
PLACE_NAMES = frozenset(
    {"Netherlands", "Peru", "Malta", "France", "Utrecht", "Cusco",
     "Valletta", "Colmar"}
)


def occurrences(word: str, document: str) -> int:
    return len(
        re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", document, re.IGNORECASE)
    )


def main() -> int:
    essays = load_essays(CORPUS)
    names: list[int] = []
    ordinary: list[int] = []
    for number in (3, 4, 8, 10):
        document = str(essays[number - 1]["text"])
        frozen: set[str] = set()
        for paragraph in [p for p in document.split("\n") if p.strip()]:
            frozen.update(
                value for value in _runtime_opaque_entities(paragraph)
                if " " not in value
            )
        for value in sorted(frozen):
            count = occurrences(value, document)
            bucket = names if value in PLACE_NAMES else ordinary
            bucket.append(count)
            print(
                f"essay {number:>2}  {value:<13} count={count:<3}"
                f"{'PLACE NAME' if value in PLACE_NAMES else ''}"
            )

    print(f"\nplace names   : min={min(names)} max={max(names)}")
    print(f"ordinary words: min={min(ordinary)} max={max(ordinary)}")
    once = sum(1 for count in ordinary if count == 1)
    print(f"\nordinary words occurring exactly once: {once} of {len(ordinary)}")
    print(f"place names occurring exactly once   : {sum(1 for c in names if c == 1)}")
    return 0 if min(names) > 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
