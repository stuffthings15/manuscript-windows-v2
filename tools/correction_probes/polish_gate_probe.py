"""
Reproducible probe: shows where Manuscript's correction GATES reject or wrongly
flag CORRECT text, which is why good AI corrections get discarded.

Run against the repo (no GPU / no Ollama needed - uses the bundled MiniLM only):
    PYTHONPATH="<repo>/Manuscript/Windows/src" python3 polish_gate_probe.py

Each "REJECT" or "FLAGGED" below is the pipeline throwing away a correct edit and
falling back to the original or rules-only text.
"""
from __future__ import annotations

# --- shims so this also runs on Python 3.10; the app targets 3.11+ ---
import datetime as _dt, enum as _enum, typing as _t
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc
if not hasattr(_enum, "StrEnum"):
    class StrEnum(str, _enum.Enum):
        def __str__(self): return str(self.value)
    _enum.StrEnum = StrEnum
if not hasattr(_t, "Self"):
    _t.Self = _t.Any
# ---------------------------------------------------------------------

from echo_link_core.polish import PolishValidator
from echo_link_core.polish_classifier import MiniLMPolishClassifier
from echo_link_core.polish_quality import CorrectionQualityGate
from echo_link_core.polish_types import PolishTask


def main() -> None:
    clf = MiniLMPolishClassifier()
    ready = clf.warm()
    print(f"MiniLM encoder ready: {ready} | load_error: {clf.encoder.load_error or 'none'}\n")

    # 1) SAFETY VALIDATOR: correct fixes a good proofreader should accept.
    validator = PolishValidator(clf)
    fixes = [
        ("i met john yesterday.",               "I met John yesterday."),
        ("went to the store and bought milk.",  "I went to the store and bought milk."),
        ("we dont have no bananas.",            "We don't have any bananas."),
        ("the team need to finish today.",      "The team needs to finish today."),
        ("john and mary went too there house.", "John and Mary went to their house."),
        ("this are wrong.",                     "This is wrong."),
        ("the datas is corrupted, we cant use it.", "The data is corrupted, so we can't use it."),
        ("she dont like it when they is late.", "She doesn't like it when they are late."),
    ]
    print("=== SAFETY VALIDATOR (meaning-preservation gate) ===")
    print(f"{'VERDICT':8}{'sim':7}{'failures':46}fix")
    print("-" * 118)
    v_bad = 0
    for src, cand in fixes:
        r = validator.validate(src, cand, task=PolishTask.PROOFREAD, grammar_mode=True)
        verdict = "ACCEPT" if r.accepted else "REJECT"
        if not r.accepted:
            v_bad += 1
        print(f"{verdict:8}{r.semantic_similarity:<7.3f}{str(list(r.failures))[:44]:46}{src!r} -> {cand!r}")

    # 2) QUALITY GATE: already-correct sentences that should pass clean.
    gate = CorrectionQualityGate()
    clean = [
        "I deployed the app to Node.JS servers.",
        "We visited Bora Bora last spring.",
        "She was born in the U.S.A. and moved here.",
        "They met in Walla Walla, Washington.",
        "The quarterly plan is due Monday.",
    ]
    print("\n=== QUALITY GATE (residual-error detector) ===")
    print(f"{'RESULT':8}{'residual errors':40}text")
    print("-" * 100)
    q_bad = 0
    for t in clean:
        r = gate.evaluate(t, command="proofread")
        result = "FLAGGED" if not r.complete else "ok"
        if not r.complete:
            q_bad += 1
        print(f"{result:8}{str(list(r.residual_errors))[:38]:40}{t}")

    print(f"\nSUMMARY: validator wrongly rejected {v_bad}/{len(fixes)} correct fixes; "
          f"quality gate wrongly flagged {q_bad}/{len(clean)} correct sentences.")


if __name__ == "__main__":
    main()
