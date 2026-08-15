"""The two rules that needed word classes to be safe.

Both were written without them, measured, and withdrawn on 2026-08-13. The
sentences that defeated them are in UNTOUCHED below, and they are the point of
this file: a rule that corrects the graded essay but splits "shows the pet is
theirs" is worse than no rule.
"""

import pytest

from echo_link_core.polish_syntax import (
    apply_syntax_rules,
    fix_subject_verb_agreement,
    misplaced_first_reason,
    move_paragraph,
    punctuate_bare_list,
    remove_discourse_filler,
    remove_self_judging_clauses,
    split_run_on_sentences,
)

SPLIT = (
    (
        "In conclusion a pigeon can make a great pet for a gardener in Athens "
        "Greece it offers plenty of shade.",
        "In conclusion a pigeon can make a great pet for a gardener in Athens "
        "Greece. It offers plenty of shade.",
    ),
    (
        "The chores are simple they are finished in ten minutes.",
        "The chores are simple. They are finished in ten minutes.",
    ),
)

UNTOUCHED = (
    # A transitive verb takes a complement clause with no "that", so the
    # pronoun after it is an object, not a new subject.
    "The owner realizes they had one fact wrong.",
    "This shows the pet is theirs.",
    "He knows it is late.",
    "The report shows it is working.",
    # A relative pronoun is shaped exactly like a new subject.
    "The animal creates entertainment that is educational.",
    "The plaza where it is quiet stays open.",
    "She wondered whether it was true.",
    # An adverb sits exactly where the boundary would go.
    "At first the chores feel annoying then after a while it is automatic.",
    # The one the essays produced: "that is why they are a good choice" was
    # split after "why", because the generated table calls "why" a noun.
    "They do not fit the situation, and that is why they are a good choice.",
)

LISTS = (
    (
        "It offers companionship routine education entertainment and a reason to care.",
        "It offers companionship, routine, education, entertainment, and a reason to care.",
    ),
)

LISTS_UNTOUCHED = (
    # The sentence the first version destroyed. "volunteer" is a noun and a
    # verb, so the whole list is refused rather than guessed at.
    "Instead, the group counted volunteer hours crop donations cancelled "
    "workdays and public meeting attendance.",
    # A clause with a prepositional phrase, not a list.
    "Others concatenate six actions with commas and fail to establish a sequence.",
    "The dog ran fast and jumped over the fence.",
    "Feeding the pet and cleaning the cage take time.",
    "The city continues to provide staffing and security.",
    # Already punctuated.
    "She packed apples, oranges, bananas, and water.",
    # A noun phrase, not a list introduced by a verb.
    "Whether in Athens housing size available vets and the travel all change.",
)


@pytest.mark.parametrize(("source", "expected"), SPLIT)
def test_a_run_on_is_split(source: str, expected: str) -> None:
    assert split_run_on_sentences(source) == expected


@pytest.mark.parametrize("source", UNTOUCHED)
def test_a_sentence_that_only_looks_like_a_run_on_is_left_alone(source: str) -> None:
    assert split_run_on_sentences(source) == source


@pytest.mark.parametrize(("source", "expected"), LISTS)
def test_a_bare_list_gets_its_commas(source: str, expected: str) -> None:
    assert punctuate_bare_list(source) == expected


@pytest.mark.parametrize("source", LISTS_UNTOUCHED)
def test_text_that_is_not_a_bare_list_is_left_alone(source: str) -> None:
    assert punctuate_bare_list(source) == source


AGREEMENT = (
    ("Therefore pigeons makes good pets.", "Therefore pigeons make good pets."),
    (
        "A pet can only be great when its needs is met.",
        "A pet can only be great when its needs are met.",
    ),
    (
        "Some habitats does need lights and heaters.",
        "Some habitats do need lights and heaters.",
    ),
    ("The turtles is a patient pet.", "The turtles are a patient pet."),
    (
        "Unresolved failures is reported immediately.",
        "Unresolved failures are reported immediately.",
    ),
)

AGREEMENT_UNTOUCHED = (
    # Singular nouns ending in -s. The lexicon answers these by asking whether
    # the singular is itself a word: "analysi" and "busines" are not, and
    # neither is "aarhu", which is how the lowercased place name in the corpus
    # stopped being corrected without anyone listing it.
    "The bus is late.",
    "Her analysis is finished.",
    "The business has three owners.",
    "The crisis is over.",
    "The campus is closed.",
    "She is a witness.",
    "The class was quiet.",
    # Invariant nouns, where the singular *is* a word and morphology cannot
    # help: "new" and "specie" are both real.
    "Physics is hard.",
    "The news was good.",
    "The species is protected.",
    "The series is ending.",
    "The statistics is clear.",
    # The subject is "one"; the plural is in a prepositional phrase.
    "One of the dogs is friendly.",
    "Engaging in additional shifts has to be done.",
    # A compound subject and a correlative decide their own agreement.
    "The dog and the cat are happy.",
    "Neither values or temperatures is allowed.",
    # The word after the -s noun is another noun, not a verb.
    "A gecko got habits needs and a personality.",
    "Daily life sometimes gets boring.",
    # The other direction is deliberately not implemented; see the note in
    # polish_syntax.py. "report" is not even in the bundled dictionary.
    "The report are ready.",
)


@pytest.mark.parametrize(("source", "expected"), AGREEMENT)
def test_a_plural_subject_gets_a_plural_verb(source: str, expected: str) -> None:
    assert fix_subject_verb_agreement(source) == expected


@pytest.mark.parametrize("source", AGREEMENT_UNTOUCHED)
def test_correct_agreement_is_left_alone(source: str) -> None:
    assert fix_subject_verb_agreement(source) == source


def test_a_doubled_word_is_not_read_as_an_agreement_error() -> None:
    # "behavior makes makes a schedule" is a duplicate, and rewriting the
    # second one would hide the defect the duplicate rule owns.
    source = "Checking its behavior makes makes a schedule."
    assert "makes make " not in fix_subject_verb_agreement(source)


def test_an_unknown_word_makes_the_list_rule_refuse() -> None:
    # The rules run after the speller, so a word it could not repair reaches
    # here unknown. Unknown must mean refuse, never "probably a noun".
    source = "It offers companionship rutine education entertainment and a reason."
    assert punctuate_bare_list(source) == source


def test_both_rules_run_together() -> None:
    source = (
        "The gardener lives in Athens Greece it offers companionship routine "
        "education and a reason to care."
    )
    assert apply_syntax_rules(source) == (
        "The gardener lives in Athens Greece. It offers companionship, routine, "
        "education, and a reason to care."
    )


SELF_JUDGING = (
    (
        "Sometimes animals stare because they care, which is why it is good but "
        "also not.",
        "Sometimes animals stare because they care.",
    ),
    (
        "The person wakes up. This shows responsibility and shows the pet is "
        "there, which are two reasons at once and maybe the same reason.",
        "The person wakes up. This shows responsibility and shows the pet is there.",
    ),
    (
        "The owner should use trustworthy local information, but this essay is "
        "mostly saying pets are good, and now it suddenly sounds like instructions.",
        "The owner should use trustworthy local information.",
    ),
)

# Every one of these was produced by an adversarial review of the first version
# of this rule, and every one is text the rule must not touch. They are kept
# verbatim because each represents a way a real student essay differs from the
# corpus this was built against.
SELF_JUDGING_UNTOUCHED = (
    # "this part" is a component in a lab write-up, and the deletion took the
    # finding with it.
    "We wired the bulbs in a row. This part does not connect to the battery, "
    "and the circuit stays open. Then we tested the switch.",
    "The engine has a small pipe near the pump. This part does not connect "
    "properly, which is why the oil leaks.",
    # "this paragraph" is a paragraph in the book being analysed.
    "In Chapter Three the writer describes the weather. This paragraph should "
    "probably be about the battle, but Orwell delays it.",
    # Deleting "and it still counts" inverts the writer's meaning rather than
    # shortening it, which is worse than leaving the defect alone.
    "A draw is not a win, and it still counts. Every point matters in the "
    "league table.",
    "It still counts. That is what the referee told us.",
    # Collateral damage: the cleanup used to run over the whole document, so
    # "So did her sister" became "Did her sister" in a sentence the rule never
    # examined, and an ellipsis elsewhere was collapsed to two dots.
    "Rosa trained all summer. This part does not connect much, but it still "
    "counts. And she won. So did her sister. Yet the coach said nothing. "
    "Which surprised everyone.",
    "A pigeon has habits... This part does not connect much, but it still "
    "counts. The owner is calm.",
    # A whole bullet disappeared from a three-item list.
    "Reasons to recycle:\n- Landfills fill up fast.\n- Even one can matters "
    "and it still counts.\n- Glass can be melted again.",
    # A thesis statement, whose subject is the text but which says what the
    # essay covers.
    "This essay is about a rooftop gardener near an old plaza and why keeping "
    "one could work well.",
    "This paragraph explains the care routine in detail.",
    "It does not need batteries, although some habitats do need lights.",
    "The dog does not bark at night, which is a relief.",
    "The owner counts the hours and it still counts as work. The pet is fine.",
    "But the dog is loyal and the cat is calm.",
)


@pytest.mark.parametrize(("source", "expected"), SELF_JUDGING)
def test_writing_that_judges_itself_is_removed(source: str, expected: str) -> None:
    assert remove_self_judging_clauses(source) == expected


@pytest.mark.parametrize("source", SELF_JUDGING_UNTOUCHED)
def test_writing_that_says_something_is_kept(source: str) -> None:
    assert remove_self_judging_clauses(source) == source


def test_a_capture_that_is_only_self_judgement_is_left_alone() -> None:
    # Emptying a paragraph is a bigger change than the defect. The writer asked
    # for a correction, not for their text to be deleted.
    source = "Which is why it is good but also not."
    assert remove_self_judging_clauses(source) == source


def test_unpunctuated_text_is_refused_rather_than_guessed_at() -> None:
    # Without full stops a "sentence" runs to the end of the paragraph, and the
    # first version deleted ninety words of argument on the strength of one
    # marker. Over sixty words with no boundary in reach, the rule refuses.
    source = (
        "in the case of a family in a house at the edge of town the animal "
        "becomes part of the day instead of just being decoration the person "
        "wakes up checks the habitat says hello then maybe forgets their keys "
        "because the morning got busy and this shows responsibility and also "
        "shows the pet is there which are two reasons at once and maybe the "
        "same reason"
    )
    assert remove_self_judging_clauses(source) == source


FILLER = (
    (
        "Checking its behavior makes or whatever a repeating schedule.",
        "Checking its behavior makes a repeating schedule.",
    ),
    (
        "Some habitats basically do need lights, heaters and filters.",
        "Some habitats do need lights, heaters and filters.",
    ),
    (
        "Therefore fish makes or whatever good pets.",
        "Therefore fish makes good pets.",
    ),
    (
        "Daily life sometimes gets boring anyways. Watching the fish helps.",
        "Daily life sometimes gets boring. Watching the fish helps.",
    ),
    (
        "And that is to be honest why they are a good choice.",
        "And that is why they are a good choice.",
    ),
    (
        "It offers a reason to care about something beyond you or whatever.",
        "It offers a reason to care about something beyond you.",
    ),
)

FILLER_UNTOUCHED = (
    # "like" and "maybe" are ordinary words here, and removing them would
    # change what the writer said. They are deliberately not filler.
    "The dog is just like a cat in some ways.",
    "Maybe the same reason applies here.",
    "She knows the answer.",
    "The pet is calm, and the owner is patient.",
)


@pytest.mark.parametrize(("source", "expected"), FILLER)
def test_speech_filler_is_removed_wherever_it_landed(source: str, expected: str) -> None:
    assert remove_discourse_filler(source) == expected


@pytest.mark.parametrize("source", FILLER_UNTOUCHED)
def test_ordinary_words_are_not_treated_as_filler(source: str) -> None:
    assert remove_discourse_filler(source) == source


def test_a_capture_that_is_only_filler_is_left_alone() -> None:
    assert remove_discourse_filler("You know.") == "You know."


def test_filler_that_opens_a_sentence_hands_back_the_capital() -> None:
    # "everybody laughs. To be honest, you know, even if the event was not
    # funny" lost its capital when the filler went, and the document can be
    # written before any later pass would repair it.
    source = "Everybody laughs. To be honest, you know, even if the event was not funny."
    assert remove_discourse_filler(source) == (
        "Everybody laughs. Even if the event was not funny."
    )


def test_two_fillers_in_a_row_are_one_removal() -> None:
    # Treating them separately capitalised the "y" of "you know" before that
    # phrase had been removed, leaving "laughs. Y even if".
    assert remove_discourse_filler("To be honest, you know, the dog is calm.") == (
        "The dog is calm."
    )


def test_a_word_that_starts_lowercase_on_purpose_keeps_its_shape() -> None:
    source = "She knows the answer. Basically iPhone users disagree."
    assert remove_discourse_filler(source) == "She knows the answer. iPhone users disagree."


def test_a_word_the_filler_was_holding_apart_is_not_doubled() -> None:
    # The removal itself creates the duplicate, so no earlier pass has a chance
    # to see it: "without proper or whatever proper care" left "proper proper".
    source = "The bird needs without proper or whatever proper care every day."
    assert remove_discourse_filler(source) == (
        "The bird needs without proper care every day."
    )


def test_the_duplicate_is_matched_whatever_its_case() -> None:
    assert remove_discourse_filler("She wanted proper or whatever Proper training.") == (
        "She wanted proper training."
    )


def test_a_duplicate_the_filler_was_set_off_from_by_commas_is_not_doubled() -> None:
    # The match consumes the comma after the filler and not the one before it,
    # so the kept text used to end at that first comma and the word in front of
    # it was never read as the first of the pair.
    assert remove_discourse_filler("Feeding, you know, Feeding is the hard part.") == (
        "Feeding is the hard part."
    )


def test_the_comma_that_introduced_the_filler_goes_with_it() -> None:
    # Keeping it leaves "It was hot, enough to melt." once the second "hot" has
    # gone, which is a worse sentence than the duplicate it replaced.
    assert remove_discourse_filler("It was hot, you know, hot enough to melt.") == (
        "It was hot enough to melt."
    )


def test_a_comma_that_is_not_holding_a_duplicate_is_kept() -> None:
    source = "The dog was calm, you know, quiet and friendly."
    assert remove_discourse_filler(source) == "The dog was calm, quiet and friendly."


def test_the_same_word_either_side_of_a_full_stop_is_two_sentences() -> None:
    # Not a duplicate: each sentence uses the word once, and merging them would
    # delete a subject rather than a repeat.
    assert remove_discourse_filler("He left. You know, Left early.") == (
        "He left. Left early."
    )


def test_a_repeated_phrase_rather_than_a_repeated_word_is_a_known_gap() -> None:
    # Only one word either side of the filler is compared, so a two-word restart
    # survives. Recorded rather than hidden; it doubles with or without a comma.
    assert remove_discourse_filler("The plan, you know, the plan worked.") == (
        "The plan, the plan worked."
    )


def test_two_different_words_around_a_filler_both_survive() -> None:
    source = "They kept the cage clean and stuff dry all week."
    assert remove_discourse_filler(source) == "They kept the cage clean dry all week."


def test_the_first_reason_paragraph_is_found_and_placed():
    paragraphs = [
        "Why Pigeons Make Good Pets",
        "Intentionally error-filled practice essay.",
        "Lots of people only think about dogs or cats when they hear the word pet.",
        "Another benefit is the way a pigeon makes a place feel interesting.",
        "Visitors could be nervous at first because they have never been close.",
        "The first reason is companionship, which is important in every country.",
        "In conclusion a pigeon can make a great pet.",
    ]
    move = misplaced_first_reason(paragraphs)

    assert move == (5, 3)
    moved = move_paragraph(paragraphs, *move)
    assert moved[3].startswith("The first reason")
    # Every other paragraph keeps its order, and none is lost.
    assert sorted(moved) == sorted(paragraphs)


def test_a_first_reason_already_in_place_is_left_alone():
    paragraphs = [
        "Why Pigeons Make Good Pets",
        "A practice essay.",
        "Lots of people only think about dogs or cats.",
        "The first reason is companionship.",
        "Another benefit is the way a pigeon makes a place feel interesting.",
    ]
    assert misplaced_first_reason(paragraphs) is None


def test_a_short_document_is_not_reordered():
    # Three paragraphs are not an essay with a structure to fix.
    assert misplaced_first_reason(["A title", "The first reason is cost."]) is None


def test_an_essay_with_no_ordinal_marker_is_not_reordered():
    paragraphs = [
        "A title",
        "A practice essay.",
        "Lots of people only think about dogs or cats.",
        "Another benefit is the way a pigeon makes a place feel interesting.",
        "In conclusion a pigeon can make a great pet.",
    ]
    assert misplaced_first_reason(paragraphs) is None
