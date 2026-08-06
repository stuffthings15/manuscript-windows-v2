"""Score correction quality across the whole essay corpus, headless.

Driving one essay through the Writing HUD in real Word costs about 45 seconds
and tells you about one essay. Tightening a meaning guard or a speller rule
needs to be judged against all twenty at once, or a fix for essay 2 quietly
breaks essay 11. Every earlier defect this session was found by reading
corrected text, and the test suite was green through all of them - so this
measures the text, not the tests.

Five numbers per essay, in the order they matter:

  swaps     content words the corrector removed without offering a respelling
            or an inflection. "enters" -> "measures" is a swap. This must be
            zero; a proofreader that changes what you said is the worst defect
            the product can have.
  bad_spell deterministic speller substitutions the writer did not ask for,
            where the replacement is not a believable repair of the original.
            "cair" -> "chair" is one.
  left      known misspellings still present after correction. This is the
            counterweight: a guard that drives `swaps` to zero by refusing
            every correction shows up here immediately.
  errors    grammar error score before and after, from the same scorer the
            acceptance gate uses.
  tokens    protected vocabulary tokens the model dropped.

    .venv/Scripts/python.exe tools/correction_quality_scorer.py
    .venv/Scripts/python.exe tools/correction_quality_scorer.py --essays 2
    .venv/Scripts/python.exe tools/correction_quality_scorer.py --label baseline

Writes `build/correction-quality.md` and `build/correction-quality.json`.
Pass `--baseline build/correction-quality-baseline.json` to print a delta.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from echo_link_core.meaning_diff import (  # noqa: E402
    describe_losses,
    unexplained_losses,
)
from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_error_score import error_score  # noqa: E402
from echo_link_core.polish_spelling import (  # noqa: E402
    EnglishDictionary,
    PolishRuleEngine,
)
from echo_link_core.polish_types import PolishMode  # noqa: E402
from echo_link_core.meaning_diff import _explains  # noqa: E402
from repair_probe import probe  # noqa: E402

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")
_TOKEN = re.compile(r"ECHOLINKTOKEN\d+X")


def _essays(limit: int) -> list[dict[str, object]]:
    from animal_essay_corpus_probe import CORPUS, load_essays

    essays = load_essays(CORPUS)
    return essays[:limit] if limit else essays


def _paragraphs(text: str) -> list[str]:
    return [value for value in str(text).split("\n") if value.strip()]


def _unknown_words(dictionary: EnglishDictionary, text: str) -> list[str]:
    """Words the bundled dictionary does not know - the residual typos."""
    unknown: list[str] = []
    for match in _WORD.finditer(text):
        word = match.group(0)
        if not dictionary.contains(word):
            unknown.append(word.casefold())
    return unknown


def _speller_substitutions(rules: PolishRuleEngine, text: str) -> list[tuple[str, str]]:
    """What the deterministic speller alone would change, as `was -> now`.

    Read straight from the suggestion objects rather than by diffing text, so
    a substitution is attributed to the speller and not to a later rule.
    """
    pairs: list[tuple[str, str]] = []
    try:
        suggestions = rules.analyze_spelling(text)
    except Exception:  # noqa: BLE001 - the dictionary may not be installed
        return pairs
    for suggestion in suggestions:
        original = str(getattr(suggestion, "original", "") or "").strip()
        replacement = str(getattr(suggestion, "replacement", "") or "").strip()
        if original and replacement and original.casefold() != replacement.casefold():
            pairs.append((original.casefold(), replacement.casefold()))
    return pairs


def better_candidate(dictionary: EnglishDictionary, word: str, chosen: str) -> str:
    """A dictionary word that repairs `word` better than `chosen` does, or "".

    `_typo_shape_rank` in `polish_spelling` knows a collapsed double and a
    dropped letter but has no idea what a transposition is, so every swapped
    pair lands in its catch-all bucket and loses to whatever is more frequent.
    That is how "wehre" became "were" instead of "where", "chroes" became
    "chromes" instead of "chores", and "recieve" would become "relieve".

    Ranking a transposition where it belongs - right beside a collapsed double
    - and searching two edits out instead of one is what this measures. The
    number it produces is Phase 2's target and must reach zero.
    """
    better, _ambiguous = judge_substitution(dictionary, word, chosen)
    return better


def judge_substitution(
    dictionary: EnglishDictionary, word: str, chosen: str
) -> tuple[str, tuple[str, ...]]:
    """Return (better replacement or "", the tied candidates when ambiguous).

    Two separate defects fall out of the same candidate list:

    *Beaten* - a candidate with a more recognisable typing shape existed and
    lost to a more frequent word of a worse shape. "wehre" -> "were" is this:
    "where" is a transposition, "were" is an unexplained dropped letter, and
    frequency decided.

    *Ambiguous* - two candidates share the best shape and only frequency
    separates them, so the speller is guessing. "esay" is essay or easy;
    "cair" is care or chair; "peopes" is people or peoples. No shape rule
    resolves these, and guessing wrong corrupts the sentence beyond the
    model's reach, because the model never sees the original word.
    """
    try:
        from symspellpy import Verbosity

        values = dictionary._symspell.lookup(
            word.casefold(), Verbosity.ALL, max_edit_distance=2, include_unknown=False
        )
    except Exception:  # noqa: BLE001 - dictionary may be absent
        return "", ()
    scored = [
        (_shape(word.casefold(), str(value.term)), -int(value.count), str(value.term))
        for value in values
    ]
    scored = [value for value in scored if value[0] < 3]
    if not scored:
        return "", ()
    scored.sort()
    best_shape = scored[0][0]
    tied = tuple(term for shape, _count, term in scored if shape == best_shape)
    top = scored[0][2]
    # Compare without apostrophes. The speller writes "dont" as "don't", and
    # the dictionary holds the same word with the mark, so a straight string
    # comparison reported every contraction it repaired as a defect.
    better = "" if _bare(top) == _bare(chosen) else top
    ambiguous = tied[:4] if len(tied) > 1 else ()
    return better, ambiguous


def _bare(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def _shape(source: str, candidate: str) -> int:
    """How recognisable a typing mistake turns `source` into `candidate`.

    0 - a doubled letter collapsed to one ("pett" -> "pet"), or the reverse
    1 - one letter left out ("esay" -> "essay") or two swapped ("teh" -> "the")

        These sit at the same rank on purpose. An earlier attempt ranked a
        transposition above a dropped letter, which fixed "wehre" -> "where"
        but broke "esay" -> "essay" into "easy". Both mistakes are equally
        common; neither shape wins on shape alone.

    3 - anything else, which is where a plain substitution lands
    """
    from echo_link_core.meaning_diff import edit_distance
    from echo_link_core.polish_spelling import _deleted_index

    removed = _deleted_index(source, candidate)
    if removed is not None:
        doubled = (removed > 0 and source[removed - 1] == source[removed]) or (
            removed + 1 < len(source) and source[removed + 1] == source[removed]
        )
        return 0 if doubled else 3
    if _deleted_index(candidate, source) is not None:
        return 1
    if len(source) == len(candidate) and edit_distance(source, candidate, limit=2) == 1:
        differing = [i for i, (a, b) in enumerate(zip(source, candidate)) if a != b]
        if (
            len(differing) == 2
            and differing[1] == differing[0] + 1
            and source[differing[0]] == candidate[differing[1]]
            and source[differing[1]] == candidate[differing[0]]
        ):
            return 1
    return 3


def score_essay(
    engine: PolishEngine,
    rules: PolishRuleEngine,
    dictionary: EnglishDictionary,
    essay: dict[str, object],
    mode: PolishMode,
) -> dict[str, object]:
    paragraphs = _paragraphs(str(essay["text"]))
    started = time.monotonic()
    swaps: list[str] = []
    bad_spell: list[str] = []
    token_drops: list[str] = []
    substitutions: list[str] = []
    sections: list[dict[str, object]] = []
    before_all: list[str] = []
    after_all: list[str] = []

    for number, paragraph in enumerate(paragraphs, start=1):
        report = probe(engine, f"p{number}", paragraph, mode)
        output = str(report["output"] or "")
        before_all.append(paragraph)
        after_all.append(output)

        losses = describe_losses(paragraph, output) if output.strip() else ()
        for loss in losses:
            swaps.append(f"p{number}: {loss}")

        for original, replacement in _speller_substitutions(rules, paragraph):
            plain = re.sub(r"[^a-z]", "", original)
            chosen = re.sub(r"[^a-z]", "", replacement)
            if not plain or not chosen:
                continue
            substitutions.append(f"p{number}: {original} -> {replacement}")
            if not _explains(plain, chosen):
                bad_spell.append(f"p{number}: {original} -> {replacement} (implausible)")
                continue
            better = better_candidate(dictionary, plain, chosen)
            if better:
                bad_spell.append(
                    f"p{number}: {original} -> {replacement} (better: {better})"
                )

        expected = tuple(_TOKEN.findall(paragraph))
        actual = tuple(_TOKEN.findall(output))
        if sorted(expected) != sorted(actual):
            token_drops.append(f"p{number}: {expected} -> {actual}")

        sections.append(
            {
                "paragraph": number,
                "used_llm": bool(report["used_llm"]),
                "changed": bool(report["changed"]),
                "seconds": report["seconds"],
                "failures": report["failures"],
                "source": paragraph,
                "output": output,
                "losses": list(losses),
            }
        )

    before = "\n".join(before_all)
    after = "\n".join(after_all)
    return {
        "essay": str(essay.get("page_id") or essay.get("name") or "?"),
        "paragraphs": len(paragraphs),
        "seconds": round(time.monotonic() - started, 1),
        "errors_before": error_score(before),
        "errors_after": error_score(after),
        "left_before": len(set(_unknown_words(dictionary, before))),
        "left_after": len(set(_unknown_words(dictionary, after))),
        "swaps": swaps,
        "bad_spell": bad_spell,
        "substitutions": substitutions,
        "token_drops": token_drops,
        "changed_sections": sum(1 for value in sections if value["changed"]),
        "llm_sections": sum(1 for value in sections if value["used_llm"]),
        "sections": sections,
    }


def _table(rows: list[dict[str, object]]) -> str:
    lines = [
        "| essay | paras | errors before -> after | typos left before -> after "
        "| swaps | bad spell | token drops |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['essay']} | {row['paragraphs']} "
            f"| {row['errors_before']} -> {row['errors_after']} "
            f"| {row['left_before']} -> {row['left_after']} "
            f"| {len(row['swaps'])} | {len(row['bad_spell'])} "
            f"| {len(row['token_drops'])} |"
        )
    return "\n".join(lines)


def _markdown(rows: list[dict[str, object]], label: str, mode: str) -> str:
    swaps = sum(len(row["swaps"]) for row in rows)
    bad_spell = sum(len(row["bad_spell"]) for row in rows)
    drops = sum(len(row["token_drops"]) for row in rows)
    worse = [
        row["essay"] for row in rows if row["errors_after"] > row["errors_before"]
    ]
    parts = [
        f"# Correction quality - {label}",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}, "
        f"mode `{mode}`, {len(rows)} essays.",
        "",
        "| exit criterion | target | result |",
        "|---|---|---|",
        f"| E1 meaning changes | 0 | **{swaps}** |",
        f"| E2 wrong speller substitutions | 0 | **{bad_spell}** |",
        f"| E4 protected tokens dropped | 0 | **{drops}** |",
        f"| E5 essays where errors rose | 0 | **{len(worse)}** |",
        "",
        _table(rows),
        "",
    ]
    if swaps:
        parts += ["## E1 - meaning changed", ""]
        for row in rows:
            for value in row["swaps"]:
                parts.append(f"- `{row['essay']}` {value}")
        parts.append("")
    if bad_spell:
        parts += ["## E2 - speller chose a word the writer did not mean", ""]
        for row in rows:
            for value in row["bad_spell"]:
                parts.append(f"- `{row['essay']}` {value}")
        parts.append("")
    if drops:
        parts += ["## E4 - protected tokens dropped", ""]
        for row in rows:
            for value in row["token_drops"]:
                parts.append(f"- `{row['essay']}` {value}")
        parts.append("")
    return "\n".join(parts) + "\n"


def _delta(rows: list[dict[str, object]], baseline: Path) -> str:
    try:
        previous = json.loads(baseline.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"baseline unreadable: {exc}"
    old = {row["essay"]: row for row in previous.get("essays", [])}
    lines = ["", "delta against baseline (negative is better):"]
    for key in ("swaps", "bad_spell", "token_drops"):
        now = sum(len(row[key]) for row in rows)
        then = sum(len(old[row["essay"]][key]) for row in rows if row["essay"] in old)
        lines.append(f"  {key:12s} {then:4d} -> {now:4d}  ({now - then:+d})")
    now_left = sum(row["left_after"] for row in rows)
    then_left = sum(
        old[row["essay"]]["left_after"] for row in rows if row["essay"] in old
    )
    lines.append(f"  {'typos left':12s} {then_left:4d} -> {now_left:4d}  ({now_left - then_left:+d})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure correction quality across the essay corpus",
    )
    parser.add_argument("--essays", type=int, default=0, help="Limit essay count.")
    parser.add_argument("--label", default="current")
    parser.add_argument(
        "--mode",
        default="balanced",
        choices=[value.value for value in PolishMode],
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "correction-quality.md")
    args = parser.parse_args(argv)

    engine = PolishEngine()
    rules = PolishRuleEngine()
    dictionary = rules.dictionary
    mode = PolishMode(args.mode)

    rows: list[dict[str, object]] = []
    for essay in _essays(args.essays):
        row = score_essay(engine, rules, dictionary, essay, mode)
        rows.append(row)
        print(
            f"{row['essay']:<44} {row['paragraphs']:2d}p "
            f"err {row['errors_before']:3d}->{row['errors_after']:3d} "
            f"typos {row['left_before']:3d}->{row['left_after']:3d} "
            f"swaps {len(row['swaps']):2d} "
            f"badspell {len(row['bad_spell']):2d} "
            f"drops {len(row['token_drops']):2d} "
            f"{row['seconds']}s",
            flush=True,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_markdown(rows, args.label, args.mode), encoding="utf-8")
    json_path = args.output.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "label": args.label,
                "mode": args.mode,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "essays": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    swaps = sum(len(row["swaps"]) for row in rows)
    bad_spell = sum(len(row["bad_spell"]) for row in rows)
    drops = sum(len(row["token_drops"]) for row in rows)
    print(
        f"\nE1 meaning changes {swaps} | E2 wrong speller fixes {bad_spell} "
        f"| E4 token drops {drops}"
    )
    for row in rows:
        for value in row["swaps"]:
            print(f"  SWAP  {row['essay']} {value}")
        for value in row["bad_spell"]:
            print(f"  SPELL {row['essay']} {value}")
    if args.baseline:
        print(_delta(rows, args.baseline))
    print(f"\n{args.output}\n{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
