"""A tone needs a span big enough to have a tone.

Measured 2026-08-20: the app applied tones one paragraph at a time, and the
app log recorded requests as small as `command=Formal characters=43`. The
refusal probe caught a section whose entire text was "* ." being sent to the
model, which replied that it had been given only an asterisk. Nothing sounds
friendly in 43 characters, and the judge scores paragraphs.

So the REQUEST is batched to 4-5 paragraphs or at least 800 characters. The
APPLY unit deliberately stays one paragraph: reviewing across a paragraph mark
and writing the answer back as one run silently deletes the break, which fused
essay 2's title and subtitle (ten paragraphs in, nine out), and the AI Brain
records from 2026-08-01 that both local models merge multi-paragraph requests.
The paragraph-count check below is what makes revisiting that safe rather than
repeating it.
"""
from __future__ import annotations

from echo_link_app.review_spans import (
    TONE_BATCH_MAXIMUM_CHARACTERS,
    join_tone_batch,
    split_tone_batch,
    tone_request_batches,
)


def test_short_paragraphs_are_grouped_into_one_request():
    texts = ["Ducks are funny pets.", "They swim.", "They quack.", "They eat."]

    assert tone_request_batches(texts) == ((0, 1, 2, 3),)


def test_a_long_paragraph_goes_on_its_own():
    texts = ["word " * 200, "short one"]
    batches = tone_request_batches(texts)

    assert batches[0] == (0,)
    assert batches[1] == (1,)


def test_a_batch_closes_at_five_paragraphs_even_when_tiny():
    texts = ["a." for _ in range(7)]

    assert tone_request_batches(texts) == ((0, 1, 2, 3, 4), (5, 6))


def test_a_batch_never_exceeds_the_timeout_budget():
    texts = ["x" * 900 for _ in range(5)]

    for batch in tone_request_batches(texts):
        total = sum(len(texts[index]) for index in batch)
        assert total <= TONE_BATCH_MAXIMUM_CHARACTERS


def test_the_answer_is_split_back_to_one_string_per_paragraph():
    joined = join_tone_batch(["First one.", "Second one."])
    answer = "First one, warmly.\n\nSecond one, warmly."

    assert split_tone_batch(answer, 2) == ("First one, warmly.", "Second one, warmly.")
    assert "\n\n" in joined


def test_a_merged_answer_is_refused_rather_than_applied():
    """The 2026-08-01 finding, now caught instead of avoided.

    A model that merges two paragraphs into one returns fewer blocks than it
    was given. Returning None sends those sections back to individual
    requests, which is exactly today's behaviour, instead of writing one run
    over two paragraphs and deleting the break between them.
    """
    assert split_tone_batch("Both paragraphs merged into one.", 2) is None


def test_an_answer_that_invents_a_paragraph_is_refused():
    assert split_tone_batch("One.\n\nTwo.\n\nThree.", 2) is None


def test_blank_runs_in_the_answer_do_not_count_as_paragraphs():
    answer = "First one.\n\n\n\nSecond one.\n"

    assert split_tone_batch(answer, 2) == ("First one.", "Second one.")
