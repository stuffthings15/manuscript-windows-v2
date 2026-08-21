"""Score how well a corrected document followed a requested tone.

The corrections grader answers "were the errors fixed". It says nothing about
whether the writing actually sounds Formal, or Friendly, or is meaningfully
Shorter, which is the other half of what this product does. On 2026-08-18 that
gap was wider than it looked: the batch harness only ever pressed Proofread, so
no tone had ever been measured through Word at all.

The judge here is the one `full_page_tone_quality_probe.py` already uses - the
same CRITERIA, the same 0 to 5 target_style and usefulness fields, the same
rule that a section passes only when both are at least 4. Reused rather than
rewritten so a tone cannot be graded to two different standards depending on
which tool somebody happened to run.

Scored per section rather than per document, because that is the unit a writer
accepts or rejects in the HUD, and because one number for a whole essay hides
which paragraph the tone failed on and gives nothing to fix.

    .venv/Scripts/python.exe tools/tone_correctness.py \
        --source <original.docx> --result <corrected.docx> --tone Friendly
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from echo_link_core.local_transport import LoopbackJSONTransport  # noqa: E402
from full_page_tone_quality_probe import TONES, _judge  # noqa: E402
from grade_corpus import paragraphs  # noqa: E402


# Below this a block is a heading, a byline, or a corpus marker rather than
# writing with a tone.
MINIMUM_PROSE_WORDS = 20


def sections(text: str) -> list[str]:
    """Paragraphs, because they are the unit that survives a correction.

    Review spans were tried first, since they are what the HUD actually offers
    a writer. They do not line up across a correction: splitting one run-on
    sentence turns eleven spans into twelve, and section three of the source
    then gets judged against section four of the result. Paragraph structure is
    something the product is forbidden to change - there is a meaning guard for
    exactly that - so paragraphs pair up reliably.
    """
    # Split on the paragraph separator, never on a bare newline.
    #
    # A single newline also occurs INSIDE a paragraph, and this corpus is full
    # of them: the seeded damage breaks lines mid-sentence, as in "...because
    # street" / "street trees...". Splitting on it made one paragraph into two
    # blocks on the source side and one on the corrected side, because removing
    # those breaks is exactly what the corrector is supposed to do.
    #
    # Measured on 2026-08-20, essay 1 of the coherent corpus: the source had 18
    # paragraphs of which 7 held an internal newline, and the corrected
    # document had 18 of which none did. This returned 18 and 14, the counts
    # did not match, and every tone scored 0.0 percent in the release gate -
    # six tones, all zero, on documents whose tone had in fact been applied 25
    # times each. The product was being marked down for repairing the damage.
    separator = chr(10) * 2
    blocks = [
        # Line breaks that survive inside a paragraph are not boundaries, so
        # they are flattened before the block is measured or judged.
        " ".join(block.split())
        for block in text.split(separator)
        if block.strip()
    ]
    # Every block, in order, with nothing dropped.
    #
    # Titles and boilerplate still must not be judged for warmth - "error-filled
    # practice essay 7" cost essay 7 a tenth of its score and told nobody
    # anything - but they are skipped AFTER the two sides are paired, never
    # before.
    #
    # Filtering first makes the pairing depend on the very thing being
    # measured. Measured twice on 2026-08-20 on the same essay: before the tone
    # ran, the source had 18 blocks and 14 long enough to score while the
    # corrected document had 18 and 14; after Friendly ran, a short block grew
    # past the threshold and the sides came out 14 against 15. Both times the
    # judge refused to score and the tone was reported as 0.0 percent.
    # Paragraph structure is what pairs reliably, so pair on that.
    return blocks


def score(
    source_text: str,
    result_text: str,
    *,
    tone: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> dict:
    transport = LoopbackJSONTransport(endpoint)
    source_sections = sections(source_text)
    result_sections = sections(result_text)
    if len(source_sections) != len(result_sections):
        # Judging paragraph 3 of the source against paragraph 4 of the result
        # would produce a confident, meaningless number. Say so instead.
        # This is the guard that caught the review-span version.
        return {
            "error": (
                f"section counts differ: source {len(source_sections)}, "
                f"result {len(result_sections)}"
            ),
            "tone": tone,
        }
    rows: list[dict] = []
    for index, (before, after) in enumerate(
        zip(source_sections, result_sections), start=1
    ):
        if (
            len(before.split()) < MINIMUM_PROSE_WORDS
            and len(after.split()) < MINIMUM_PROSE_WORDS
        ):
            # A title or byline on both sides. Not prose, so not judged - but
            # it kept its place in the pairing, which is the point.
            continue
        if before == after:
            # Nothing was applied here. It is not a tone failure to judge, but
            # it is certainly not a tone success, so it counts against the
            # score rather than being quietly dropped from the denominator.
            rows.append(
                {
                    "section": index,
                    "pass": False,
                    "target_style": 0,
                    "usefulness": 0,
                    "reason": "unchanged; the tone was never applied here",
                }
            )
            continue
        judged = _judge(
            transport,
            model=model,
            tone=tone,
            source=before,
            result=after,
            timeout=timeout,
        )
        rows.append({"section": index, **judged})
    passed = sum(1 for row in rows if row.get("pass"))
    total = len(rows) or 1
    return {
        "tone": tone,
        "sections": len(rows),
        "passed": passed,
        "percent": round(100.0 * passed / total, 1),
        "mean_target_style": round(
            sum(int(row.get("target_style", 0)) for row in rows) / total, 2
        ),
        "mean_usefulness": round(
            sum(int(row.get("usefulness", 0)) for row in rows) / total, 2
        ),
        "failures": [row for row in rows if not row.get("pass")],
    }


def document_text(path: Path) -> str:
    """The document as text, with paragraphs separated unambiguously.

    Joined with a blank line rather than a single newline, because `sections`
    has to be able to tell a paragraph boundary from a line break inside a
    paragraph, and this corpus is full of the latter.
    """
    return (chr(10) * 2).join(paragraphs(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--tone", required=True, choices=list(TONES))
    parser.add_argument("--model", default="gemma4:12b-it-q4_K_M")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    report = score(
        document_text(args.source),
        document_text(args.result),
        tone=args.tone,
        model=args.model,
        endpoint=args.endpoint,
        timeout=args.timeout,
    )
    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if "error" in report:
        print(f"{args.tone}: {report['error']}")  # noqa: T201
        return 1
    print(  # noqa: T201
        f"{report['tone']}: {report['percent']}% "
        f"({report['passed']}/{report['sections']} sections) "
        f"style {report['mean_target_style']} usefulness {report['mean_usefulness']}"
    )
    for row in report["failures"]:
        print(  # noqa: T201
            f"  section {row['section']}: style={row.get('target_style')} "
            f"useful={row.get('usefulness')} {str(row.get('reason'))[:110]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
