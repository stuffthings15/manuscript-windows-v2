"""Things worth telling a writer about, which Manuscript must not rewrite.

A correction and a piece of advice are different products. A correction is
applied to the writer's document, so it has to be certainly right: the meaning
guards exist because a wrong correction silently changes what somebody said.
Advice is offered, and the writer decides.

Sentence length is the case that forced this module. On 2026-08-17 essay 1 of
the 800-percent corpus scored 82, and 12 of the 14 points it lost came from six
sentences longer than 45 words. Every one of those was correctly punctuated:

    "Some people will disagree before hearing the reasons; however, the reasons
    do not all connect perfectly, but they still show the animal can belong in
    a home..."

Semicolon, correct commas, correct conjunctions, 48 words. Nothing in it is a
punctuation error. The source had six such sentences and the corrected document
had the same six, so the measurement returned an identical value whether the
corrector had worked well or not run at all - it was measuring the corpus, not
the product. `split_run_on_sentences` already declines to touch these on
purpose, because splitting a long correct sentence rewrites the writer's prose
rather than fixing it.

So they are advice. This module names them; it never changes them.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


# Long enough that a reader loses the thread, and well past the length of
# ordinary academic prose. Kept here rather than in the grader so the product
# and the measurement cannot drift apart on the definition.
LONG_SENTENCE_WORDS = 45

_SENTENCE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")
_WORD = re.compile(r"[\w'-]+")


@dataclass(frozen=True)
class WritingAdvice:
    """One suggestion the writer may take or ignore. Never applied."""

    kind: str
    detail: str
    start: int
    end: int
    excerpt: str


def long_sentence_advice(text: str) -> tuple[WritingAdvice, ...]:
    """Sentences long enough to be worth splitting by hand.

    Returns advice regardless of whether the sentence is punctuated correctly,
    because that is the point: a correct sentence can still be too long to
    follow, and that is a judgement for the writer rather than an error to
    repair.
    """
    if not isinstance(text, str):
        raise TypeError("Advice text must be a string.")
    advice: list[WritingAdvice] = []
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0)
        words = len(_WORD.findall(sentence))
        if words <= LONG_SENTENCE_WORDS:
            continue
        stripped = sentence.strip()
        advice.append(
            WritingAdvice(
                kind="long-sentence",
                detail=(
                    f"This sentence runs to {words} words. Splitting it would "
                    "be easier to read. Manuscript has not changed it, because "
                    "the punctuation is a choice rather than a mistake."
                ),
                start=match.start(),
                end=match.end(),
                excerpt=stripped[:120],
            )
        )
    return tuple(advice)


__all__ = ["LONG_SENTENCE_WORDS", "WritingAdvice", "long_sentence_advice"]
