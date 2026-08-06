"""Regression tests for the correction-gate reliability redesign (2026-07-31).

Locks the two behaviors that were failing:
  1. The safety validator must ACCEPT correct proofreads it used to false-reject
     (added subject, missing apostrophes, capitalization) while still REJECTING
     real meaning changes (number/entity/negation/modal/ordering/agent swaps).
  2. The quality gate must not flag correct text (Node.JS, U.S.A., Bora Bora)
     as a residual error, while still catching accidental doubled words.
"""
import pytest

from echo_link_core.polish import PolishValidator
from echo_link_core.polish_classifier import MiniLMPolishClassifier
from echo_link_core.polish_quality import CorrectionQualityGate
from echo_link_core.polish_types import PolishTask


@pytest.fixture(scope="module")
def validator():
    return PolishValidator(MiniLMPolishClassifier())


# correct proofreads that must pass the safety gate
ACCEPT_CASES = [
    ("went to the store and bought milk.", "I went to the store and bought milk."),
    ("the datas is corrupted, we cant use it.", "The data is corrupted, so we can't use it."),
    ("she dont like it when they is late.", "She doesn't like it when they are late."),
    ("we dont have no bananas.", "We don't have any bananas."),
    ("the team need to finish today.", "The team needs to finish today."),
    ("john and mary went too there house.", "John and Mary went to their house."),
    ("the dog wagged it's tail, its happy.", "The dog wagged its tail; it's happy."),
    ("youre going to love you're gift.", "You're going to love your gift."),
    ("i cant believe its friday.", "I can't believe it's Friday."),
    ("youre house is nice.", "Your house is nice."),
    ("dont judge nobody.", "Don't judge anyone."),
]

# real meaning changes that must still be blocked
REJECT_CASES = [
    ("Pay $50 by Friday.", "Pay $500 by Friday."),
    ("We need 3 servers.", "We need 8 servers."),
    ("Email Alice about the bug.", "Email Bob about the bug."),
    ("Do not deploy on Friday.", "Deploy on Friday."),
    ("The tests passed.", "The tests did not pass."),
    ("We can ship today.", "We cannot ship today."),
    ("You must sign the form.", "You may sign the form."),
    ("Staff must wear badges.", "Staff should consider wearing badges."),
    ("Ship the code before the review.", "Ship the code after the review."),
    ("She shipped it on Monday.", "He shipped it on Monday."),
]


@pytest.mark.parametrize(("source", "candidate"), ACCEPT_CASES)
def test_validator_accepts_correct_proofreads(validator, source, candidate):
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert result.accepted, result.failures


@pytest.mark.parametrize(("source", "candidate"), REJECT_CASES)
def test_validator_still_rejects_meaning_changes(validator, source, candidate):
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert not result.accepted


@pytest.mark.parametrize(
    "text",
    [
        "I deployed the app to Node.JS servers.",
        "We visited Bora Bora last spring.",
        "She was born in the U.S.A. and moved here.",
        "They met in Walla Walla, Washington.",
    ],
)
def test_quality_gate_passes_correct_text(text):
    assert CorrectionQualityGate().evaluate(text, command="Proofread").complete


@pytest.mark.parametrize(
    "text",
    ["By by the time the team arrived, the floor was wet.", "the the report is late."],
)
def test_quality_gate_still_flags_doubled_words(text):
    assert not CorrectionQualityGate().evaluate(text, command="Proofread").complete


# --- 2026-08-01 reliability follow-up ------------------------------------
# Locks the section-size fixes: misplaced-apostrophe contractions must not be
# read as negation changes, and written-number fact phrases must not quote a
# wrong singular time unit back to the model as an immutable fact.

from echo_link_core.selected_text import (  # noqa: E402
    _missing_number_fact_phrases,
    _repair_time_unit_plural,
)

MISPLACED_APOSTROPHE_CASES = [
    (
        "the report was finish late becuase the reviewer was'nt available.",
        "The report was finished late because the reviewer was not available.",
    ),
    (
        "the reviewer was'nt available.",
        "The reviewer wasn't available.",
    ),
    (
        "she do'nt like the plan.",
        "She doesn't like the plan.",
    ),
    (
        "they could'nt finish on time.",
        "They couldn't finish on time.",
    ),
]


@pytest.mark.parametrize(("source", "candidate"), MISPLACED_APOSTROPHE_CASES)
def test_validator_accepts_misplaced_apostrophe_repairs(validator, source, candidate):
    result = validator.validate(source, candidate, task=PolishTask.PROOFREAD)
    assert result.accepted, result.failures


def test_validator_still_rejects_dropped_negation_with_misplaced_apostrophe(validator):
    result = validator.validate(
        "the reviewer was'nt available.",
        "The reviewer was available.",
    task=PolishTask.PROOFREAD,
    )
    assert not result.accepted


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("two day", "two days"),
        ("another two day", "another two days"),
        ("three week", "three weeks"),
        ("one day", "one day"),
        ("zero day", "zero day"),
        ("two days", "two days"),
        ("first day", "first day"),
    ],
)
def test_repair_time_unit_plural(phrase, expected):
    assert _repair_time_unit_plural(phrase) == expected


def test_number_fact_check_accepts_plural_repair():
    assert not _missing_number_fact_phrases(
        ("two day",),
        "If they ask for changes, we will need another two days.",
    )


def test_number_fact_check_still_flags_missing_quantity():
    assert _missing_number_fact_phrases(
        ("two days",),
        "If they ask for changes, we will need more time.",
    )


# --- benchmark-cache expiry (2026-08-01) ----------------------------------
# One transient probe failure (GPU held by another local app) must not mark a
# model pack failed forever. Failed records expire; passing records persist.

from datetime import datetime, timedelta, timezone  # noqa: E402

from echo_link_core.polish_hardware import (  # noqa: E402
    BenchmarkRecord,
    PolishBenchmarkCache,
)


class _DictStore:
    def __init__(self):
        self.values = {}

    def get_meta(self, key, default=None):
        return self.values.get(key, default)

    def set_meta(self, key, value):
        self.values[key] = value


def _record(*, accepted: bool, measured_at: str) -> BenchmarkRecord:
    return BenchmarkRecord(
        accepted=accepted,
        correctness_passed=accepted,
        protected_passed=accepted,
        elapsed_seconds=1.0,
        backend="cuda",
        reason="" if accepted else "model did not stay on the GPU",
        measured_at=measured_at,
    )


def test_failed_benchmark_record_expires():
    cache = PolishBenchmarkCache(_DictStore())
    stale = (
        datetime.now(timezone.utc)
        - timedelta(seconds=PolishBenchmarkCache.FAILED_RECORD_TTL_SECONDS + 60)
    ).isoformat()
    cache.put("key", _record(accepted=False, measured_at=stale))
    assert cache.get("key") is None


def test_fresh_failed_benchmark_record_is_kept():
    cache = PolishBenchmarkCache(_DictStore())
    now = datetime.now(timezone.utc).isoformat()
    cache.put("key", _record(accepted=False, measured_at=now))
    record = cache.get("key")
    assert record is not None and not record.accepted


def test_passing_benchmark_record_never_expires():
    cache = PolishBenchmarkCache(_DictStore())
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cache.put("key", _record(accepted=True, measured_at=old))
    record = cache.get("key")
    assert record is not None and record.accepted


def test_failed_record_with_invalid_timestamp_expires():
    cache = PolishBenchmarkCache(_DictStore())
    cache.put("key", _record(accepted=False, measured_at="not-a-date"))
    assert cache.get("key") is None


def test_repairing_a_typo_is_not_read_as_changing_the_meaning() -> None:
    """The meaning guards were reading the source before the rules fixed it.

    They count exact words, so a misspelled one is invisible to them. "befor"
    is not an ordering word but "before" is, and "thaey" is not an actor but
    "they" is - so the deterministic layer repairing those spellings, before
    the model ever saw the text, registered as the model inventing an ordering
    relationship and an actor. Across the twenty-essay corpus that accounted
    for 197 of about 250 refusals: the corrector turned down for doing its job.

    `prepared` is what the model was actually handed. It differs from the
    source by spelling only, so judging against it is more accurate rather than
    more permissive - a word the model genuinely adds is still absent from
    `prepared` and still fails, as the second half of this test shows.
    """
    from echo_link_core.polish import MiniLMPolishClassifier, PolishValidator
    from echo_link_core.polish_types import PolishTask

    validator = PolishValidator(MiniLMPolishClassifier())
    source = (
        "wen somebody says a certain animul cant be a pett thaey usualy is "
        "speaking befor anyways THEY lernt much bout it."
    )
    prepared = (
        "When somebody says a certain animal cant be a pet thaey usually is "
        "speaking before anyways THEY learnt much about it."
    )
    candidate = (
        "When somebody says a certain animal cannot be a pet, they usually are "
        "speaking before they learnt much about it."
    )

    result = validator.validate(
        source,
        candidate,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
        prepared=prepared,
    )
    joined = " ".join(result.failures)
    assert "ordering relationship changed" not in joined, joined
    assert "actor changed" not in joined, joined

    # An ordering word the model really does invent is still refused, because
    # it is absent from the prepared text too.
    invented = (
        "When somebody says a certain animal cannot be a pet, they usually are "
        "speaking before they learnt much about it, and after that they agree."
    )
    invented_result = validator.validate(
        source,
        invented,
        task=PolishTask.PROOFREAD,
        grammar_mode=True,
        prepared=prepared,
    )
    assert any(
        "ordering relationship changed" in value for value in invented_result.failures
    ), invented_result.failures
