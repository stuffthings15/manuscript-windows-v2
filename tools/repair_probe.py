"""Answer "why did this text not get corrected" in seconds, not minutes.

Driving the whole UI to understand one rejected section costs several
minutes and usually does not say which check refused the correction. This
runs the same engine on the same text and prints the exact failures, the
residual errors, and whether the AI ran at all.

    .venv/Scripts/python.exe tools/repair_probe.py --text "The dogs is happy."
    .venv/Scripts/python.exe tools/repair_probe.py --essay 1 --sections 3
    .venv/Scripts/python.exe tools/repair_probe.py --essay all --json out.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)


def essay_sections(index: str) -> list[tuple[str, str]]:
    from animal_essay_corpus_probe import CORPUS, load_essays

    essays = load_essays(CORPUS)
    if index != "all":
        wanted = int(index)
        essays = [essays[wanted - 1]]
    sections: list[tuple[str, str]] = []
    for essay in essays:
        for number, paragraph in enumerate(
            [value for value in str(essay["text"]).split("\n") if value.strip()],
            start=1,
        ):
            sections.append((f"{essay['page_id']}#{number}", paragraph))
    return sections


def probe(engine: PolishEngine, label: str, text: str, mode: PolishMode) -> dict:
    started = time.monotonic()
    result = engine.analyze(
        CorrectionRequest(
            text=text,
            task=PolishTask.PROOFREAD,
            mode=mode,
            command="Proofread",
            # Without this the engine keeps to rules only and every report
            # reads "full AI correction did not run", which hides whichever
            # check actually refused the model's answer.
            download_consent=True,
            manual=True,
        )
    )
    output = str(result.text or "")
    return {
        "label": label,
        "characters": len(text),
        "seconds": round(time.monotonic() - started, 3),
        "used_llm": bool(result.used_llm),
        "retries": int(getattr(result, "retries", 0) or 0),
        "failures": list(getattr(result.validation, "failures", ()) or ()),
        "residual": list(result.residual_errors or ()),
        "message": str(getattr(result, "message", "")),
        "changed": output.strip() != text.strip(),
        "source": text,
        "output": output,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run text through the real correction engine and explain it",
    )
    parser.add_argument("--text", default="")
    parser.add_argument("--essay", default="")
    parser.add_argument("--sections", type=int, default=0, help="Limit sections.")
    parser.add_argument(
        "--mode",
        default="balanced",
        choices=[value.value for value in PolishMode],
    )
    parser.add_argument("--json", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.text and not args.essay:
        parser.error("pass --text or --essay")

    if args.text:
        sections = [("inline", args.text)]
    else:
        sections = essay_sections(args.essay)
    if args.sections:
        sections = sections[: args.sections]

    engine = PolishEngine()
    mode = PolishMode(args.mode)
    reports = [probe(engine, label, text, mode) for label, text in sections]

    changed = sum(1 for report in reports if report["changed"])
    with_ai = sum(1 for report in reports if report["used_llm"])
    if not args.quiet:
        for report in reports:
            print("=" * 78)
            print(
                f"{report['label']}  chars={report['characters']} "
                f"{report['seconds']}s used_llm={report['used_llm']} "
                f"retries={report['retries']} changed={report['changed']}"
            )
            print(f"  SOURCE : {report['source'][:200]}")
            print(f"  OUTPUT : {report['output'][:200]}")
            if report["failures"]:
                print(f"  VETOED : {report['failures']}")
            if report["residual"]:
                print(f"  LEFT   : {report['residual']}")
            if report["message"]:
                print(f"  MESSAGE: {report['message']}")
    print(
        f"\n{changed}/{len(reports)} sections changed, "
        f"{with_ai}/{len(reports)} used the AI"
    )

    failure_counts: dict[str, int] = {}
    for report in reports:
        for failure in report["failures"]:
            key = failure.split("(")[0].strip()
            failure_counts[key] = failure_counts.get(key, 0) + 1
    if failure_counts:
        print("veto reasons:")
        for reason, count in sorted(
            failure_counts.items(), key=lambda item: -item[1]
        ):
            print(f"  {count:4d}  {reason}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "sections": len(reports),
                    "changed": changed,
                    "used_llm": with_ai,
                    "veto_reasons": failure_counts,
                    "reports": reports,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
