"""The pack guard must count packs that SERVED, not packs that were tried.

On 2026-09-02 the twenty-document reliability baseline ran for 36 minutes and
was then refused: "more than one pack served this run: ['gemma4-12b-qat-v1',
'gemma4-e2b-qat-v1', 'gemma4-e4b-qat-v1']". The log said otherwise. 171 accepted
plans, every one of them on the 12b, none on either smaller pack.

The cause was the app's retry ladder. When output fails validation the app tries
the next pack down, and logs a `Polish plan` line for each attempt:

    12b   -> validation failed: residual missing sentence spacing
    e4b   -> the same validation failure
    e2b   -> the same validation failure
    rules -> the section is returned unchanged

Two of those cascades in a twenty-document run were enough to refuse it. Five
documents made a cascade uncommon; twenty makes at least one near certain, so on
the old reading the metric could essentially never complete - and the corpus had
just been enlarged specifically to make that metric usable.

WHAT MUST NOT BE LOST. The guard exists because a pack that genuinely ANSWERS
changes the score: on 2026-09-01, 33 of 629 requests were served by e4b, those
answers were accepted and scored, the last request happened to be the 12b, and
the run reported clean. That case must still be caught, so the fixture below
asserts it as well - a guard that stopped catching it would be worse than the
false refusal it replaced.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import autoresearch_run  # noqa: E402

# Copied from C:/Users/stuff/AppData/Local/ManuscriptWindowsV2/echo-link.log,
# 2026-09-02 23:40:02 to 23:40:23, with the timestamps kept so the shape of a
# real cascade is visible. This is what the refused run actually contained.
CASCADE = """\
2026-09-02 23:40:03,263 INFO echo_link_core.polish: Polish plan pack=gemma4-12b-qat-v1 model=gemma4:12b-it-qat backend=cuda use_llm=True reason=-
2026-09-02 23:40:06,199 INFO echo_link_core.polish: Polish plan pack=gemma4-e4b-qat-v1 model=gemma4:e4b-it-qat backend=cuda use_llm=True reason=-
2026-09-02 23:40:10,872 INFO echo_link_core.polish: Polish plan pack=gemma4-e4b-qat-v1 model=gemma4:e4b-it-qat backend=cuda use_llm=True reason=validation failed: residual missing sentence spacing
2026-09-02 23:40:15,702 INFO echo_link_core.polish: Polish plan pack=gemma4-e2b-qat-v1 model=gemma4:e2b-it-qat backend=cuda use_llm=True reason=-
2026-09-02 23:40:19,165 INFO echo_link_core.polish: Polish plan pack=gemma4-e2b-qat-v1 model=gemma4:e2b-it-qat backend=cuda use_llm=True reason=validation failed: residual missing sentence spacing
2026-09-02 23:40:23,339 INFO echo_link_core.polish: Polish plan pack=- model=- backend=rules use_llm=False reason=validation failed: residual missing sentence spacing
2026-09-02 23:40:23,366 INFO echo_link_app.window: Writing HUD returning the section unchanged; no safe correction was available characters=99
"""

ACCEPTED_ON_THE_BIG_PACK = (
    "2026-09-02 23:40:02,671 INFO echo_link_core.engine: Accepted AI plan "
    "pack=gemma4-12b-qat-v1 model=gemma4:12b-it-qat policy=6 reason=-\n"
)

ACCEPTED_ON_A_SMALL_PACK = (
    "2026-09-01 04:11:07,001 INFO echo_link_core.engine: Accepted AI plan "
    "pack=gemma4-e4b-qat-v1 model=gemma4:e4b-it-qat policy=6 reason=-\n"
)


def _guard_over(text: str, monkeypatch, tmp_path) -> set[str]:
    log = tmp_path / "echo-link.log"
    log.write_text(text, encoding="utf-8")
    monkeypatch.setattr(autoresearch_run, "APP_LOG", log)
    return autoresearch_run._packs_that_served(0)


def test_a_failed_cascade_does_not_count_as_three_packs(monkeypatch, tmp_path):
    """The refusal that cost 36 minutes. Every used correction came from the 12b."""
    served = _guard_over(
        ACCEPTED_ON_THE_BIG_PACK + CASCADE + ACCEPTED_ON_THE_BIG_PACK,
        monkeypatch,
        tmp_path,
    )
    assert served == {"gemma4-12b-qat-v1"}, (
        "a pack that was tried and rejected served nothing, and counting it "
        "refuses a run that one pack served"
    )


def test_a_pack_that_actually_answered_is_still_caught(monkeypatch, tmp_path):
    """The 2026-09-01 case the guard was built for. It must not be weakened.

    e4b did not merely get tried here; its answer was accepted and scored. That
    is the failure that made a run's score depend on GPU timing, and it has to
    keep failing the guard.
    """
    served = _guard_over(
        ACCEPTED_ON_THE_BIG_PACK + ACCEPTED_ON_A_SMALL_PACK,
        monkeypatch,
        tmp_path,
    )
    assert served == {"gemma4-12b-qat-v1", "gemma4-e4b-qat-v1"}
    assert len(served) > 1, "a run scored on two packs must still be refused"


def test_a_rules_only_fallback_is_never_counted_as_a_pack(monkeypatch, tmp_path):
    """`pack=-` is the absence of a pack, not a third one."""
    served = _guard_over(
        ACCEPTED_ON_THE_BIG_PACK + CASCADE, monkeypatch, tmp_path
    )
    assert "-" not in served


def test_a_run_that_accepted_nothing_reports_no_pack(monkeypatch, tmp_path):
    """An empty set means "nothing served this", which the caller must be free
    to treat as a crash rather than as a clean single-pack run."""
    assert _guard_over(CASCADE, monkeypatch, tmp_path) == set()


def test_the_mark_still_excludes_an_earlier_session(monkeypatch, tmp_path):
    """The log is append-only across restarts, so a pack from an earlier run
    looks exactly like one from this run without the mark."""
    log = tmp_path / "echo-link.log"
    log.write_text(
        ACCEPTED_ON_A_SMALL_PACK + ACCEPTED_ON_THE_BIG_PACK, encoding="utf-8"
    )
    monkeypatch.setattr(autoresearch_run, "APP_LOG", log)
    assert autoresearch_run._packs_that_served(1) == {"gemma4-12b-qat-v1"}
