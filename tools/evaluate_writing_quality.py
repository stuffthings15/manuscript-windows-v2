from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.writing_quality import (
    evaluate_writing_quality,
    load_writing_quality_corpus,
    reference_responses,
)


DEFAULT_CORPUS = ROOT / "benchmarks" / "writing_quality_v1.json"


def load_responses(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Responses must be valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Responses must use schema version 1.")
    raw_responses = payload.get("responses")
    if not isinstance(raw_responses, list):
        raise ValueError("Responses must contain a response list.")
    responses: dict[str, str] = {}
    for item in raw_responses:
        if not isinstance(item, dict):
            raise ValueError("Each response must be an object.")
        identifier = str(item.get("id") or "").strip()
        response_text = item.get("text")
        if not identifier or not isinstance(response_text, str):
            raise ValueError("Each response needs a text id and string text.")
        if identifier in responses:
            raise ValueError(f"Duplicate response id: {identifier}")
        responses[identifier] = response_text
    return responses


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Score fixed Manuscript writing responses without running a model or network call."
    )
    result.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--responses", type=Path)
    source.add_argument(
        "--reference-self-check",
        action="store_true",
        help="Check corpus references only. This is not model-quality evidence.",
    )
    result.add_argument("--output", type=Path)
    result.add_argument("--check", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        corpus = load_writing_quality_corpus(args.corpus)
        responses = (
            reference_responses(corpus)
            if args.reference_self_check
            else load_responses(args.responses)
        )
        report = evaluate_writing_quality(corpus, responses)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    summary = report["summary"]
    print(
        f"{summary['passed_count']}/{summary['case_count']} fixed cases passed; "
        f"reference matches {summary['reference_match_count']}",
        file=sys.stderr,
    )
    return 1 if args.check and not summary["all_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
