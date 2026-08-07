"""Show exactly what gets protected, and what the model gives back.

The refusal that leaves essay 1 paragraph 2 uncorrected reports two immutable
tokens on a forty-four character fragment, and reading the source only accounts
for one of them. Rather than reason about which rule added the second, this
wraps the real protection call and prints its arguments and its result, then
runs the real engine on the same text.

    .venv/Scripts/python.exe tools/protected_token_probe.py
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from animal_essay_corpus_probe import CORPUS, load_essays  # noqa: E402
import echo_link_core.selected_text as selected_text  # noqa: E402

_real_protect = selected_text._protect_text


def traced_protect_text(text, values, *args, **kwargs):
    protected, replacements = _real_protect(text, values, *args, **kwargs)
    print("\n--- _protect_text ---")
    print(f"  source      : {text!r}")
    print(f"  values in   : {sorted(set(filter(None, values)))!r}")
    print(f"  protected   : {protected!r}")
    print(f"  replacements: {replacements!r}")
    return protected, replacements


selected_text._protect_text = traced_protect_text


def main() -> int:
    from echo_link_core.polish import PolishEngine
    from echo_link_core.polish_types import (
        CorrectionRequest,
        PolishMode,
        PolishTask,
    )

    essays = load_essays(CORPUS)
    text = str(essays[0]["text"])
    paragraphs = [line for line in text.split("\n") if line.strip()]
    target = paragraphs[1]
    print(f"paragraph 2 ({len(target)} chars): {target!r}")

    engine = PolishEngine()
    result = engine.analyze(
        CorrectionRequest(
            text=target,
            task=PolishTask.PROOFREAD,
            mode=PolishMode("balanced"),
            command="Proofread",
            download_consent=True,
            manual=True,
        )
    )
    print("\n--- engine result ---")
    print(f"  used_llm : {result.used_llm}")
    print(f"  text     : {str(result.text)!r}")
    metrics = dict(getattr(result, "metrics", {}) or {})
    for key in ("refusal_reason", "fallback_reason"):
        if key in metrics:
            print(f"  {key}: {metrics[key]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
