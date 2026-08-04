"""
Test-driven spec for the redesigned safety validator.

- MUST_ACCEPT: correct proofreads the gate should PASS (these are the bugs today).
- MUST_REJECT: real meaning changes the gate must STILL block (safety net).

Runs against the repo with the bundled MiniLM (no GPU/Ollama needed):
    PYTHONPATH="<repo>/Manuscript/Windows/src" python3 validator_spec.py
"""
from __future__ import annotations
import datetime as _dt, enum as _enum, typing as _t
if not hasattr(_dt, "UTC"): _dt.UTC = _dt.timezone.utc
if not hasattr(_enum, "StrEnum"):
    class StrEnum(str, _enum.Enum):
        def __str__(self): return str(self.value)
    _enum.StrEnum = StrEnum
if not hasattr(_t, "Self"): _t.Self = _t.Any

import re
from collections import Counter
from echo_link_core import polish as P
from echo_link_core.polish_classifier import MiniLMPolishClassifier
from echo_link_core.polish_types import PolishTask

CLF = MiniLMPolishClassifier()
CLF.warm()

# ---- correct proofreads: MUST ACCEPT ----
MUST_ACCEPT = [
    ("went to the store and bought milk.",          "I went to the store and bought milk."),
    ("the datas is corrupted, we cant use it.",     "The data is corrupted, so we can't use it."),
    ("she dont like it when they is late.",         "She doesn't like it when they are late."),
    ("we dont have no bananas.",                    "We don't have any bananas."),
    ("the team need to finish today.",              "The team needs to finish today."),
    ("john and mary went too there house.",         "John and Mary went to their house."),
    ("the dog wagged it's tail, its happy.",        "The dog wagged its tail; it's happy."),
    ("youre going to love you're gift.",            "You're going to love your gift."),
    ("i cant believe its friday.",                  "I can't believe it's Friday."),
    ("we tested it yesterday it failed nobody fixed it.",
     "We tested it yesterday. It failed, and nobody fixed it."),
    # memory-log regressions that must keep passing:
    ("youre house is nice.",                        "Your house is nice."),
    ("dont judge nobody.",                          "Don't judge anyone."),
]

# ---- real meaning changes: MUST REJECT ----
MUST_REJECT = [
    ("Pay $50 by Friday.",                 "Pay $500 by Friday."),            # number
    ("We need 3 servers.",                 "We need 8 servers."),             # number
    ("Email Alice about the bug.",         "Email Bob about the bug."),       # entity swap
    ("The Boston office flooded.",         "The office flooded."),            # entity drop
    ("Do not deploy on Friday.",           "Deploy on Friday."),              # negation flip
    ("The tests passed.",                  "The tests did not pass."),        # negation add
    ("We can ship today.",                 "We cannot ship today."),          # modal flip
    ("You must sign the form.",            "You may sign the form."),         # modal weaken
    ("Staff must wear badges.",            "Staff should consider wearing badges."),  # obligation drop
    ("Ship the code before the review.",   "Ship the code after the review."),# ordering
    ("She shipped it on Monday.",          "He shipped it on Monday."),       # agent gender swap
    ("Call the client at 3pm.",            "Call the client at 5pm."),        # number/time
]

# ---------- prototype: normalized apostrophe-less contractions ----------
_CONTRACTIONS = {
    "dont": "don't", "doesnt": "doesn't", "didnt": "didn't", "cant": "can't",
    "wont": "won't", "isnt": "isn't", "arent": "aren't", "wasnt": "wasn't",
    "werent": "weren't", "havent": "haven't", "hasnt": "hasn't", "hadnt": "hadn't",
    "couldnt": "couldn't", "wouldnt": "wouldn't", "shouldnt": "shouldn't",
    "mustnt": "mustn't", "neednt": "needn't", "cannot": "can't",
}
_CONTRACTION_RE = re.compile(r"\b(" + "|".join(_CONTRACTIONS) + r")\b", re.IGNORECASE)

def _norm(text: str) -> str:
    return _CONTRACTION_RE.sub(lambda m: _CONTRACTIONS[m.group(0).lower()], text)

def _gendered(text: str) -> Counter:
    c = P._actor_counter(_norm(text))
    return Counter({k: v for k, v in c.items() if k in {"he", "she"}})

def _hard_obligation_present(text: str) -> bool:
    return P._obligation_counter(_norm(text)).get("hard requirement", 0) > 0

def new_validate(source: str, candidate: str, *, grammar_mode: bool = True):
    """Redesigned narrow validator (prototype)."""
    failures = []
    if not candidate.strip():
        failures.append("empty output")
    if "—" in candidate:
        failures.append("em dash is not allowed")
    # protected facts / numbers / tokens
    sc = P._critical_values(source, ())
    cc = P._critical_values(candidate, ())
    for value in tuple(cc):
        extra = cc[value] - sc[value]
        if extra > 0 and P._quotes_existing_source_text(value, source):
            cc[value] -= extra
            if cc[value] <= 0:
                del cc[value]
    if sc != cc:
        failures.append("protected facts or tokens changed")
    if P._counter(P._IDENTIFIER, source) != P._counter(P._IDENTIFIER, candidate):
        failures.append("code or identifiers changed")
    ns, nc = _norm(source), _norm(candidate)
    if not P._negation_preserved(ns, nc):
        failures.append("negation changed")
    if not P._modal_meaning_preserved(ns, nc):
        failures.append("modal meaning changed")
    if _hard_obligation_present(source) != _hard_obligation_present(candidate):
        failures.append("obligation strength changed")
    if not P._ordering_preserved(source, candidate):
        failures.append("date or ordering relationship changed")
    if _gendered(source) != _gendered(candidate):
        failures.append("actor changed")
    # named entities add/remove (unchanged logic)
    src_names = P._named_entities(source)
    cand_names = P._named_entities(candidate)
    new_names = {v for v in cand_names - src_names
                 if not P._single_word_present(v, source)
                 and not P._named_entity_matches_source_phrase(v, source)}
    removed_names = {v for v in src_names - cand_names
                     if not P._single_word_present(v, candidate)}
    if new_names:
        failures.append("new named entity added")
    if removed_names:
        failures.append("named entity removed")
    sim = CLF.semantic_similarity(source, candidate)
    if sim < 0.88:
        failures.append(f"semantic similarity {sim:.3f} is below 0.880")
    if grammar_mode:
        sw = max(1, len(P._WORD.findall(source)))
        cw = len(P._WORD.findall(candidate))
        delta = abs(cw - sw)
        if delta / sw > 0.35 and delta > 4:
            failures.append("grammar-mode length changed by more than 35%")
        if P._paragraph_count(source) != P._paragraph_count(candidate):
            failures.append("paragraph structure changed")
    return (not failures), tuple(failures), sim


def run(title, validate_fn):
    from echo_link_core.polish import PolishValidator
    print(f"\n########## {title} ##########")
    acc_ok = rej_ok = 0
    print("-- MUST ACCEPT (want ACCEPT) --")
    for s, c in MUST_ACCEPT:
        ok, fails, sim = validate_fn(s, c)
        mark = "PASS" if ok else "**BUG**"
        if ok: acc_ok += 1
        print(f"  {('ACCEPT' if ok else 'reject'):7}{mark:8}{str(list(fails))[:44]:46}{s!r}->{c!r}")
    print("-- MUST REJECT (want REJECT) --")
    for s, c in MUST_REJECT:
        ok, fails, sim = validate_fn(s, c)
        mark = "PASS" if not ok else "**LEAK**"
        if not ok: rej_ok += 1
        print(f"  {('reject' if not ok else 'ACCEPT'):7}{mark:8}{str(list(fails))[:44]:46}{s!r}->{c!r}")
    print(f"  SCORE: accept {acc_ok}/{len(MUST_ACCEPT)}  reject {rej_ok}/{len(MUST_REJECT)}")
    return acc_ok, rej_ok


def current_validate(s, c):
    from echo_link_core.polish import PolishValidator
    v = PolishValidator(CLF)
    r = v.validate(s, c, task=PolishTask.PROOFREAD, grammar_mode=True)
    return r.accepted, r.failures, r.semantic_similarity


if __name__ == "__main__":
    run("CURRENT validator (baseline)", current_validate)
    run("PROTOTYPE new_validate", new_validate)
