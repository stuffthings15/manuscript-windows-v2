"""Show what is still frozen as a name in the three essays that never correct.

Essays 4, 8 and 15 have now failed in three consecutive batch runs, which is
what separates them from the essays that merely wobble between runs. This
lists every value the runtime would make an opaque immutable token, with the
document count and whether the bundled dictionary knows the word, so the
remaining cause is read rather than guessed at.

    .venv/Scripts/python.exe tools/dead_essay_probe.py
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
from echo_link_core.polish_spelling import EnglishDictionary  # noqa: E402


def main() -> int:
    essays = load_essays(CORPUS)
    dictionary = EnglishDictionary()
    dictionary.warm()
    for number in (4, 8, 15):
        document = str(essays[number - 1]["text"])
        print(f"=== essay {number} ===")
        seen: set[str] = set()
        for paragraph in (line for line in document.splitlines() if line.strip()):
            for value in _runtime_opaque_entities(paragraph, document):
                if value in seen:
                    continue
                seen.add(value)
                count = len(
                    re.findall(rf"(?<!\w){re.escape(value)}(?!\w)", document, re.I)
                )
                known = dictionary.contains(value.casefold())
                suggestion = dictionary.suggestion(value.casefold())
                print(
                    f"  {value:<16} count={count}  in_dictionary={known}  "
                    f"suggestion={suggestion}"
                )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
