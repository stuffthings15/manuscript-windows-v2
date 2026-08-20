"""The grader is measured too, because a broken rubric aims the product wrong.

Coherence charged six sentences of correct writing across the three held-out
essays, and every one of those points came from wording memorised out of the
twenty-essay corpus. A rule tuned to satisfy that rubric would have learned to
delete good sentences, which is the damage the narrowed clause removal in
`fbc38c0` was written to undo.

The two groups below are the whole test: writing that must never be charged,
and the defects that must still be caught.
"""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from grade_corpus import (  # noqa: E402
    META_COMMENTARY,
    SCALE,
    WEIGHTS,
    _confusable_hits,
    _run_on_sentences,
)

# Ordinary sentences from the held-out essays. Each was charged 2.5 points.
CORRECT_WRITING = (
    # A bare "it" is not a reference to the writing.
    "It does not take much staff to keep that going, although the heating bill "
    "does climb a little.",
    "It is not a movie, and it does not need batteries, although some habitats "
    "do need lights, heaters, filters, or other equipment.",
    "Watching the cat move, eat, rest, or investigate something gives the owner "
    "a small event to pay attention to, and it does not need a screen.",
    # "still counts" is ordinary English with a subject that is not the writing.
    "If a kid reads comics all afternoon it still counts as reading, and nobody "
    "at the desk makes a face about it.",
    "The strip of grass along the north fence is only about ten feet wide, but "
    "it still counts.",
    # A thesis statement belongs in the essay, and "works" is not a judgement.
    "This essay is about a tour guide who works early mornings.",
    # The self-referential noun is doing ordinary work here.
    "This part of the yard does not get any sun after two in the afternoon.",
)

# The planted defects, from the corpus and from held-out essay 3.
JUDGES_THE_WRITING = (
    "This part does not connect much, but it still counts.",
    "This paragraph should probably be about fun, but responsibility comes back "
    "again.",
    "However, this essay is mostly saying that pets are good, and now it "
    "suddenly sounds like instructions.",
    "I am not sure this paragraph fits with the one before it but I already "
    "typed it out.",
    # Asserted and taken back before the full stop, with nothing in between.
    "This is why it is good but also not.",
    "Which is why it is good but also not.",
)


@pytest.mark.parametrize("sentence", CORRECT_WRITING)
def test_ordinary_writing_is_not_charged_as_incoherence(sentence: str) -> None:
    assert META_COMMENTARY.search(sentence) is None


@pytest.mark.parametrize("sentence", JUDGES_THE_WRITING)
def test_a_sentence_that_judges_the_writing_is_charged(sentence: str) -> None:
    assert META_COMMENTARY.search(sentence) is not None


# Confusable words the speller cannot see, because both spellings are real.
# The first three are live misses in the held-out essays, scored 15 of 15 for
# homophones by the rubric that asked the correction rules what they would
# change.
CONFUSED = (
    ("My cousin reads with an app on hers because the print is to small for her.", "too"),
    ("If a teacher catches you with it out while your putting it away it counts.", "you're"),
    ("Two of the screws on the swing set frame are lose and one bolt is missing.", "loose"),
    ("I do not want to loose my place in the queue.", "lose"),
    ("The new bridge is wider then the one it replaced.", "than"),
)

# Every one of these was charged by a version of the rules that was too wide.
CORRECT_USE = (
    "The doors are open from noon to five, and anybody can walk in.",
    "The researchers worked together to correct the problem before the deadline.",
    "We moved to New York last year and the rent has not stopped climbing.",
    "She put the leftover paint to good use on the shed door.",
    "He fell asleep listening to loud music with the window open.",
    "Its best feature is the way the roof folds back in under a minute.",
    "The council will lose the grant if the forms are late.",
    "Back then the fence was still standing.",
)


@pytest.mark.parametrize(("sentence", "meant"), CONFUSED)
def test_a_confusable_word_in_an_impossible_position_is_found(
    sentence: str, meant: str
) -> None:
    hits = _confusable_hits(sentence)
    assert hits, f"nothing found in {sentence!r}"
    assert any(hit.endswith(meant) for hit in hits), hits


@pytest.mark.parametrize("sentence", CORRECT_USE)
def test_a_confusable_word_used_correctly_is_left_alone(sentence: str) -> None:
    assert _confusable_hits(sentence) == []


def test_clauses_joined_with_no_punctuation_are_a_run_on() -> None:
    source = [
        "Almost everybody in my grade owns one and they bring it to school "
        "anyway so the argument is not really about the devices it is about "
        "what we do with them."
    ]
    assert _run_on_sentences(source) == source


def test_a_long_sentence_that_is_punctuated_is_not_a_run_on() -> None:
    # Length is not the defect. This is the same clause count, written properly.
    source = [
        "Almost everybody in my grade owns one, and they bring it to school "
        "anyway, so the argument is not really about the devices, it is about "
        "what we do with them."
    ]
    assert _run_on_sentences(source) == []


def test_a_long_single_clause_is_not_a_run_on() -> None:
    # No coordinator, so there is no second clause to have punctuated. Merely
    # long is not the same defect, and the 45-word rule already charges it.
    source = [
        "The enormous weathered wooden climbing frame at the far northern end "
        "of the recreation ground near the allotment gates was repainted again "
        "last autumn by the parish maintenance team."
    ]
    assert _run_on_sentences(source) == []


def test_no_corpus_wording_is_memorised_in_the_pattern() -> None:
    """The strings that made the rubric flatter the essays it was written beside.

    Each one marked unseen writing wrong. They are named here so a later session
    cannot quietly put them back to make a corpus number move.
    """
    for memorised in (
        "still counts",
        "good but also not",
        "responsibility",
        "two reasons at once",
        "maybe the same reason",
        "instructions",
    ):
        assert memorised not in META_COMMENTARY.pattern


def _docx(tmp_path, body: str):
    """A minimal .docx carrying one document.xml body."""
    import zipfile

    path = tmp_path / "essay.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            f"{body}</w:body></w:document>",
        )
    return path


def test_a_tab_does_not_turn_word_markup_into_prose(tmp_path) -> None:
    """The reader must not read "<w:tab/>" as the start of a text run.

    "<w:t[^>]*>" matched "<w:tab/>" because the character class ate "ab/", and
    the non-greedy scan then ran on to the next "</w:t>" taking every tag in
    between with it. Eleven of the twenty essays in the 500-percent corpus
    contain a tab, and each one put a run of Word markup into the graded text,
    where "proofErr" and "gramStart" were charged as misspelled words. It was
    very nearly reported as the app corrupting documents.
    """
    from grade_corpus import paragraphs

    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>A serious discussion</w:t></w:r>"
        "<w:r><w:tab/></w:r>"
        '<w:proofErr w:type="gramStart"/>'
        "<w:r><w:t>must examine the people involved.</w:t></w:r></w:p>",
    )

    found = paragraphs(path)

    assert "proofErr" not in found[0]
    assert "w:" not in found[0]
    # The tab is whitespace the writer can see, so it separates the words
    # rather than vanishing and joining them. That is the sister defect, tested
    # on its own below.
    assert found[0].split() == [
        "A", "serious", "discussion", "must", "examine", "the", "people", "involved.",
    ]


def test_a_text_run_with_attributes_is_still_read(tmp_path) -> None:
    """Tightening the pattern must not cost the ordinary case."""
    from grade_corpus import paragraphs

    path = _docx(
        tmp_path,
        '<w:p><w:r><w:t xml:space="preserve">Managed hives can </w:t></w:r>'
        "<w:r><w:t>support education.</w:t></w:r></w:p>",
    )

    assert paragraphs(path) == ["Managed hives can support education."]


def test_a_line_break_inside_a_paragraph_still_separates_the_words(tmp_path) -> None:
    """Dropping "<w:br/>" joins the words on either side of it.

    The error-riddled corpus seeds a stray line break inside a paragraph, so
    "sufficient by itself\nitself" arrived as the single word "itselfitself".
    It was charged as a misspelling in seven of the twenty essays, and was very
    nearly reported to the owner as the app doubling words in his document. The
    seeded error is real; what was wrong was reading it as one word.
    """
    from grade_corpus import paragraphs

    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>neither is sufficient by itself</w:t></w:r>"
        "<w:r><w:br/></w:r>"
        "<w:r><w:t>itself. A fair evaluation asks who defines success.</w:t></w:r></w:p>",
    )

    found = paragraphs(path)

    assert "itselfitself" not in found[0]
    assert found[0].split()[:6] == [
        "neither", "is", "sufficient", "by", "itself", "itself.",
    ]


def test_a_tab_separates_the_words_around_it(tmp_path) -> None:
    """Same defect, the other whitespace element the corpus seeds."""
    from grade_corpus import paragraphs

    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>A serious discussion</w:t></w:r>"
        "<w:r><w:tab/></w:r>"
        "<w:r><w:t>discussion must examine the people.</w:t></w:r></w:p>",
    )

    assert "discussiondiscussion" not in paragraphs(path)[0]


def test_a_page_break_carrying_attributes_is_still_read(tmp_path) -> None:
    """Word writes "<w:br w:type=\\"page\\"/>", not only the bare form."""
    from grade_corpus import paragraphs

    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>before</w:t></w:r>"
        '<w:r><w:br w:type="page"/></w:r>'
        "<w:r><w:t>after the break.</w:t></w:r></w:p>",
    )

    assert "beforeafter" not in paragraphs(path)[0]


# Sentence length became advice rather than a punctuation charge on 2026-08-17.
# Essay 1 of the 800-percent corpus lost 12 of its 20 punctuation points to six
# sentences, every one of them correctly punctuated, and the source and the
# corrected document held the same six - so the term returned the same value
# whether the corrector had worked perfectly or never run.

LONG_BUT_CORRECT = (
    "Some people will disagree before hearing the reasons; however, the "
    "reasons do not all connect perfectly, but they still show the animal can "
    "belong in a home, so a dog has habits, needs, and a personality just "
    "like more famous pets do, only in a different looking package."
)


def test_a_long_correct_sentence_is_advice_and_not_a_punctuation_charge() -> None:
    from echo_link_core.polish_advice import long_sentence_advice

    advice = long_sentence_advice(LONG_BUT_CORRECT)

    assert len(advice) == 1
    assert advice[0].kind == "long-sentence"
    # It says how long, because "too long" without a number is not actionable.
    assert "48 words" in advice[0].detail
    # And it says the punctuation was not wrong, so the writer does not go
    # looking for a mistake that is not there.
    assert "not changed it" in advice[0].detail


def test_ordinary_length_prose_earns_no_advice() -> None:
    from echo_link_core.polish_advice import long_sentence_advice

    assert long_sentence_advice(
        "The dog waited by the door. It had learned the routine."
    ) == ()


def test_advice_never_rewrites_the_writers_sentence() -> None:
    """The whole point of the split: advice is offered, corrections are applied.

    A long correct sentence must come back untouched. Splitting it would
    rewrite the writer's prose rather than repair it, which is why
    split_run_on_sentences declines to touch these as well.
    """
    from echo_link_core.polish_advice import long_sentence_advice
    from echo_link_core.polish_syntax import split_run_on_sentences

    advice = long_sentence_advice(LONG_BUT_CORRECT)

    assert advice  # it is worth telling the writer about
    assert split_run_on_sentences(LONG_BUT_CORRECT) == LONG_BUT_CORRECT


def test_a_long_correct_sentence_costs_no_punctuation_points(tmp_path) -> None:
    """The scoring change itself, driven through the grader.

    Without this the sentence above cost two points as a "long run", and the
    report added charged run-ons and advisory long sentences into one column,
    so essay 1 read "runon=7" when it had one run-on and six long sentences.
    """
    from grade_corpus import grade
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import WritingSuggestionEngine

    path = _docx(tmp_path, f"<w:p><w:r><w:t>{LONG_BUT_CORRECT}</w:t></w:r></w:p>")

    row = grade(path, EnglishDictionary(), WritingSuggestionEngine())

    assert row["punctuation"] == pytest.approx(20 * SCALE)
    assert row["advice"] == 1
    assert row["run_ons"] == 0


META_SENTENCE = "This part dont connect much but it still counts."


def test_meta_commentary_is_advice_and_names_what_it_matched() -> None:
    from echo_link_core.polish_advice import meta_commentary_advice
    from grade_corpus import META_COMMENTARY

    advice = meta_commentary_advice(META_SENTENCE, META_COMMENTARY)

    assert len(advice) == 1
    assert advice[0].kind == "meta-commentary"
    # It says why Manuscript left it, so the writer does not read the advice as
    # a failure to correct.
    assert "never a correction" in advice[0].detail


def test_meta_commentary_costs_no_coherence_points(tmp_path) -> None:
    """Essay 2 lost ten of fifteen coherence points to four seeded sentences.

    The only repair available is deleting the writer's sentence, so the source
    and the corrected document carry the same ones and the term said nothing
    about the corrector.
    """
    from grade_corpus import grade
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import WritingSuggestionEngine

    path = _docx(tmp_path, f"<w:p><w:r><w:t>{META_SENTENCE}</w:t></w:r></w:p>")

    row = grade(path, EnglishDictionary(), WritingSuggestionEngine())

    assert row["coherence"] == pytest.approx(15 * SCALE)
    assert row["meta"] == 1
    assert row["advice"] >= 1


def test_ordinary_prose_earns_no_meta_advice() -> None:
    from echo_link_core.polish_advice import meta_commentary_advice
    from grade_corpus import META_COMMENTARY

    assert meta_commentary_advice(
        "The dog waited by the door and learned the routine.", META_COMMENTARY
    ) == ()


def test_the_rubric_no_longer_scores_the_writers_paragraph_order() -> None:
    """Organization scored where the AUTHOR put an ordinal marker.

    Essay 3 announces "Another benefit" three paragraphs before "The first
    reason is". The only repair available is reordering the writer's
    paragraphs, which the product must not do, so the term returned the same
    value whether the corrector worked perfectly or never ran. Removed on
    2026-08-19 at the owner's decision, the third category to go for exactly
    that reason after long sentences and meta commentary.
    """
    assert "organization" not in WEIGHTS
    # The five that remain still add up to a score out of 100, so a reader can
    # add a printed row up and get the total beside it.
    assert sum(WEIGHTS.values()) * SCALE == pytest.approx(100)


def test_a_perfect_document_scores_one_hundred(tmp_path) -> None:
    """The rescale has to leave a clean document at 100, not at 85."""
    from grade_corpus import grade
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import WritingSuggestionEngine

    clean = (
        "The dog waited by the door and learned the routine. "
        "Its owner filled the water bowl every morning before work. "
        "The walk came later, once the rain had stopped."
    )
    path = _docx(tmp_path, f"<w:p><w:r><w:t>{clean}</w:t></w:r></w:p>")

    row = grade(path, EnglishDictionary(), WritingSuggestionEngine())

    assert row["total"] == 100
