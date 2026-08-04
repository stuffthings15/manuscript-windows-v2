"""Measure Manuscript correction reliability end to end on real local hardware.

The probe answers the two reported complaints with numbers instead of opinion:

* Consistency - the same input is corrected ``--repeats`` times. More than one
  distinct accepted output, or a run that silently drops to rules-only, is a
  reliability defect.
* Accuracy - each accepted output is checked against the expected fix for the
  seeded error. A source error still present in the output is a defect.

Nothing is written outside the chosen report path, and no source text leaves the
machine. Run from ``Manuscript/Windows``::

    .venv/Scripts/python.exe tools/correction_reliability_probe.py --repeats 3
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.polish import PolishEngine  # noqa: E402
from echo_link_core.polish_hardware import PolishBenchmarkCache  # noqa: E402
from echo_link_core.polish_model_packs import ModelPackManager  # noqa: E402
from echo_link_core.polish_runtime import AdaptiveInferenceRuntime  # noqa: E402
from echo_link_core.polish_runtime_packs import RuntimePackManager  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)

LIVE_DATA_ROOT = Path.home() / "AppData" / "Local" / "ManuscriptWindowsV2"


class _MemoryStore:
    """Benchmark-cache store that never touches the user's real database."""

    def __init__(self) -> None:
        self._meta: dict[str, object] = {}

    def get_meta(self, key: str, default=None):
        return self._meta.get(key, default)

    def set_meta(self, key: str, value) -> None:
        self._meta[key] = value


@dataclass(frozen=True)
class Case:
    """One seeded defect plus the evidence that it was actually corrected.

    ``forbidden`` fragments are matched case-insensitively, because an error such
    as ``"seperate"`` is still an error whatever its capitalisation. Each group in
    ``required_any`` is satisfied when at least one of its alternatives is
    present, so an equally correct rewrite ("do not" for "don't") is not scored
    as a failure. ``required_cased`` is matched with exact case and is the only
    way capitalisation is asserted.
    """

    name: str
    source: str
    forbidden: tuple[str, ...] = ()
    required_any: tuple[tuple[str, ...], ...] = ()
    required_cased: tuple[str, ...] = ()


SENTENCE_CASES: tuple[Case, ...] = (
    Case(
        "subject-verb agreement",
        "The team need to finish the report before friday.",
        forbidden=("team need",),
        required_cased=("Friday",),
    ),
    Case(
        "missing apostrophe and double negative",
        "we dont have no bananas left in the store.",
        forbidden=("dont", "no bananas"),
        required_any=(("don't", "do not"),),
        required_cased=("We",),
    ),
    Case(
        "homophones",
        "john and mary went too there house yesterday.",
        forbidden=("too there", "went too"),
        required_any=(("their",),),
        required_cased=("John", "Mary"),
    ),
    Case(
        "your/you're contraction repair",
        "your going to like what i found in youre house.",
        forbidden=("your going", "youre"),
        required_any=(("you're", "you are"),),
        required_cased=("I",),
    ),
    Case(
        "run-on plus capitalisation",
        "the meeting is on tuesday we should prepare the slides its important.",
        forbidden=("tuesday we should", "its important"),
        required_cased=("Tuesday",),
    ),
    Case(
        "plural and tense",
        "she dont like it when they is late for the standups.",
        forbidden=("dont", "they is"),
        required_any=(("doesn't", "does not"),),
    ),
    Case(
        "misspellings with protected product noun",
        "the seperate Manuscript workflow recieves teh text and check it.",
        forbidden=("seperate", "recieves"),
        required_any=(("receives",), ("separate",)),
        required_cased=("Manuscript",),
    ),
    Case(
        "punctuation and spacing",
        "I finished the draft ,but the reviewer said it need more work .",
        forbidden=("draft ,but", "it need", "work ."),
    ),
)

# A Writing HUD review section is one to three sentences of a real page, which is
# the size the reported failures appear at. These reproduce that size.
SECTION_CASES: tuple[Case, ...] = (
    Case(
        "three-sentence section with mixed errors",
        "the report was finish late becuase the reviewer was'nt available. "
        "we send it too the client anyway and they dont replied yet. "
        "if they asks for changes we will needs another two day.",
        forbidden=("becuase", "was'nt", "sent it too", "dont replied", "two day"),
        required_cased=("The", "We"),
    ),
    Case(
        "section with protected names and a date",
        "Manuscript was tested on a S23 Plus by curtis at 3pm on tuesday. "
        "the tester said the icon dont appear in gmail and the card wont open. "
        "we should of checked the accessibility service first.",
        forbidden=("dont appear", "wont open", "should of"),
        required_cased=("Manuscript", "Curtis", "Tuesday"),
        required_any=(("3pm", "3 pm", "3:00"),),
    ),
    Case(
        "section with a list and numbers",
        "there is three open items. first the installer need signing, "
        "second the icon is to small on the S23, and third the accesibility "
        "service dont survive a reboot. each item was raise on 2026-07-29.",
        forbidden=("there is three", "need signing", "is to small", "accesibility", "dont survive"),
        required_cased=("S23",),
        required_any=(("first",), ("2026-07-29",)),
    ),
    Case(
        "section that is already correct",
        "The installer is unsigned, so Windows blocks the first launch. "
        "We will sign it before the release build. No other change is needed.",
        forbidden=(),
        required_cased=("The installer is unsigned",),
    ),
)

_WORD_BOUNDARY_SAFE = re.compile(r"^[\w' ]+$")


def _present(text: str, fragment: str, *, ignore_case: bool) -> bool:
    """Containment that respects word edges when the fragment allows it."""

    flags = re.IGNORECASE if ignore_case else 0
    if _WORD_BOUNDARY_SAFE.match(fragment):
        pattern = re.compile(rf"(?<!\w){re.escape(fragment)}(?!\w)", flags)
        return bool(pattern.search(text))
    if ignore_case:
        return fragment.casefold() in text.casefold()
    return fragment in text


def _evaluate(case: Case, text: str) -> tuple[str, ...]:
    problems = [
        f"still contains {fragment!r}"
        for fragment in case.forbidden
        if _present(text, fragment, ignore_case=True)
    ]
    problems.extend(
        f"none of {list(group)} present"
        for group in case.required_any
        if not any(_present(text, item, ignore_case=True) for item in group)
    )
    problems.extend(
        f"missing exact {fragment!r}"
        for fragment in case.required_cased
        if not _present(text, fragment, ignore_case=False)
    )
    return tuple(problems)


def run_probe(
    *,
    repeats: int,
    mode: PolishMode,
    manual: bool,
    protected_terms: tuple[str, ...],
    cases_to_run: tuple[Case, ...],
) -> dict[str, object]:
    packs = ModelPackManager(install_root=LIVE_DATA_ROOT / "polish-model-packs")
    engine = PolishEngine(
        benchmark_cache=PolishBenchmarkCache(_MemoryStore()),
        packs=packs,
        runtime=AdaptiveInferenceRuntime(
            model_packs=packs,
            runtime_packs=RuntimePackManager(
                install_root=LIVE_DATA_ROOT / "polish-runtime-packs",
            ),
        ),
        preferred_pack_provider=lambda: "",
    )
    engine.warm()
    snapshot = engine.hardware_provider()
    cases: list[dict[str, object]] = []
    for case in cases_to_run:
        runs: list[dict[str, object]] = []
        for index in range(repeats):
            result = engine.analyze(
                CorrectionRequest(
                    text=case.source,
                    task=PolishTask.PROOFREAD,
                    mode=mode,
                    command="Proofread",
                    manual=manual,
                    protected_terms=protected_terms,
                )
            )
            runs.append(
                {
                    "attempt": index + 1,
                    "text": result.text,
                    "used_llm": bool(result.used_llm),
                    "planned_llm": bool(result.plan.use_llm),
                    "rejected_llm": bool(result.rejected_llm),
                    "quality_complete": bool(result.quality_complete),
                    "retries": int(result.retries),
                    "model": result.plan.model or "rules",
                    "backend": result.plan.backend.value,
                    "accepted": bool(result.validation.accepted),
                    "validation_failures": list(result.validation.failures),
                    "residual_errors": list(result.residual_errors),
                    "downgrade_reason": result.plan.downgrade_reason,
                    "message": result.message,
                    "problems": list(_evaluate(case, result.text)),
                }
            )
        distinct = Counter(str(run["text"]) for run in runs)
        llm_runs = sum(1 for run in runs if run["used_llm"])
        rejected_runs = sum(1 for run in runs if run["rejected_llm"])
        accurate_runs = sum(1 for run in runs if not run["problems"])
        cases.append(
            {
                "name": case.name,
                "source": case.source,
                "distinct_outputs": len(distinct),
                "llm_runs": llm_runs,
                "rejected_llm_runs": rejected_runs,
                "accurate_runs": accurate_runs,
                "consistent": len(distinct) == 1,
                "always_llm": llm_runs == repeats,
                "always_accurate": accurate_runs == repeats,
                "runs": runs,
            }
        )
    consistent = sum(1 for case in cases if case["consistent"])
    always_llm = sum(1 for case in cases if case["always_llm"])
    accurate = sum(1 for case in cases if case["always_accurate"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repeats": repeats,
        "mode": mode.value,
        "manual": manual,
        "protected_terms": list(protected_terms),
        "hardware": {
            "gpu": snapshot.gpu_name,
            "driver_healthy": snapshot.driver_healthy,
            "dedicated_vram_bytes": snapshot.dedicated_vram_bytes,
            "available_vram_bytes": snapshot.available_vram_bytes,
            "total_ram_bytes": snapshot.total_ram_bytes,
            "available_ram_bytes": snapshot.available_ram_bytes,
            "on_battery": snapshot.on_battery,
        },
        "case_count": len(cases),
        "consistent_cases": consistent,
        "always_llm_cases": always_llm,
        "accurate_cases": accurate,
        "passed": consistent == len(cases)
        and always_llm == len(cases)
        and accurate == len(cases),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Manuscript correction consistency and accuracy",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--scope",
        default="all",
        choices=("sentence", "section", "all"),
        help="Single sentences, Writing HUD review sections, or both",
    )
    parser.add_argument(
        "--mode",
        default=PolishMode.BALANCED.value,
        choices=[value.value for value in PolishMode],
    )
    parser.add_argument(
        "--automatic",
        action="store_true",
        help="Run as background automatic review instead of an explicit user action",
    )
    parser.add_argument(
        "--protected-terms",
        default="Manuscript",
        help="Comma-separated protected glossary terms",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "correction-reliability.json",
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    protected = tuple(
        item.strip() for item in args.protected_terms.split(",") if item.strip()
    )
    selected: tuple[Case, ...]
    if args.scope == "sentence":
        selected = SENTENCE_CASES
    elif args.scope == "section":
        selected = SECTION_CASES
    else:
        selected = SENTENCE_CASES + SECTION_CASES
    report = run_probe(
        repeats=args.repeats,
        mode=PolishMode.coerce(args.mode),
        manual=not args.automatic,
        protected_terms=protected,
        cases_to_run=selected,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"consistent {report['consistent_cases']}/{report['case_count']}  "
        f"always-AI {report['always_llm_cases']}/{report['case_count']}  "
        f"accurate {report['accurate_cases']}/{report['case_count']}"
    )
    for case in report["cases"]:
        flags = []
        if not case["consistent"]:
            flags.append(f"{case['distinct_outputs']} different outputs")
        if not case["always_llm"]:
            flags.append(f"AI ran {case['llm_runs']}/{report['repeats']}")
        if not case["always_accurate"]:
            problems = sorted(
                {
                    problem
                    for run in case["runs"]
                    for problem in run["problems"]
                }
            )
            flags.append("; ".join(problems))
        status = "PASS" if not flags else "FAIL"
        print(f"{status}  {case['name']}: {' | '.join(flags) or 'stable and correct'}")
    print(args.output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
