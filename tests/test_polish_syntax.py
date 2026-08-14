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
    punctuate_bare_list,
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
