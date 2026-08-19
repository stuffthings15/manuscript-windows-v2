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
    newline = chr(10)
    blocks = [block.strip() for block in text.split(newline) if block.strip()]
    # Titles and boilerplate are not prose and cannot carry a tone. Judging
    # "intentionally error-filled practice essay 7" for warmth cost essay 7 a
    # tenth of its score and told nobody anything. Every real paragraph in this
    # corpus runs to sixty words or more, so the line is drawn well clear of
    # them rather than tuned to the boundary.
    return [block for block in blocks if len(block.split()) >= MINIMUM_PROSE_WORDS]


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
    return "\n".join(paragraphs(path))


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
