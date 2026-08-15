"""The corrector has to say when it did not correct something.

A paragraph the model refused still comes back repaired by the deterministic
rules, and from outside the engine that is indistinguishable from a full
correction. A run of twenty paragraphs where nine were rules-only was once
reported as twenty successes. So the two cases below are the point of this
file: the text is kept either way, and the reason survives.
"""
from __future__ import annotations

from types import SimpleNamespace

from tools import correct_corpus


class _Engine:
    """Stands in for PolishEngine, which needs a model pack and several seconds."""

    def __init__(self, result: object) -> None:
        self._result = result

    def analyze(self, request: object) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _result(*, text: str, used_llm: bool, refusal: str = "", failures: tuple = ()) -> object:
    return SimpleNamespace(
        text=text,
        used_llm=used_llm,
        metrics={"refusal_reason": refusal},
        validation=SimpleNamespace(failures=failures),
    )


def test_a_corrected_paragraph_is_reported_as_the_model_s() -> None:
    engine = _Engine(_result(text="The dog is calm.", used_llm=True))
    text, used_model, reason = correct_corpus.correct(engine, "the dog are calm")

    assert (text, used_model, reason) == ("The dog is calm.", True, "")


def test_a_refused_paragraph_keeps_its_rules_repair_and_names_the_guard() -> None:
    engine = _Engine(
        _result(
            text="The dog are calm.",
            used_llm=False,
            refusal="validation failed: actor changed (missing it)",
        )
    )
    text, used_model, reason = correct_corpus.correct(engine, "the dog are calm")

    # The writer still gets the deterministic repair. What must not happen is
    # this counting as a correction.
    assert text == "The dog are calm."
    assert used_model is False
    assert reason == "validation failed: actor changed (missing it)"


def test_a_refusal_with_no_message_falls_back_to_the_validation_failures() -> None:
    engine = _Engine(
        _result(text="x", used_llm=False, failures=("modal meaning changed", "actor changed"))
    )
    _, _, reason = correct_corpus.correct(engine, "x")

    assert reason == "modal meaning changed; actor changed"


def test_a_refusal_with_nothing_at_all_still_says_so() -> None:
    engine = _Engine(_result(text="x", used_llm=False))
    _, used_model, reason = correct_corpus.correct(engine, "x")

    assert used_model is False
    assert reason == "no reason recorded"


def test_a_crash_keeps_the_writer_s_paragraph_rather_than_losing_it() -> None:
    engine = _Engine(RuntimeError("the pack went away"))
    text, used_model, reason = correct_corpus.correct(engine, "the writer's words")

    assert text == "the writer's words"
    assert used_model is False
    assert reason.startswith("crashed: RuntimeError")


def test_only_the_essays_in_a_corpus_directory_are_corrected(tmp_path) -> None:
    # The locked corpus keeps a README beside its twenty essays, and correcting
    # it produced a 21st graded document scoring 98 that meant nothing. Word
    # writes "~$name.docx" while a document is open, and correcting one of those
    # produces garbage.
    for name in ("01_essay.docx", "~$_essay.docx", "02_essay.txt", "README.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")

    found = [path.name for path in correct_corpus.essay_documents(tmp_path)]

    assert found == ["01_essay.docx", "02_essay.txt"]
