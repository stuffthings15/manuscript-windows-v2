

def test_the_dictionary_knows_the_forms_its_affix_rules_describe() -> None:
    """The word list was read and the affix file beside it was ignored.

    A Hunspell dictionary is a word list plus an affix file, and the flags
    after each entry's slash say which rules that entry takes. Reading only the
    list left 48,262 base forms: the speller knew "cat" but not "cats",
    "change" but not "changes", and did not know "after" at all. Every spelling
    decision inherited it - whether a word is a typo, whether a repair is
    trustworthy, and the residual-error count in the quality scorer, which
    turned out to be mostly plurals.
    """
    from echo_link_core.polish_spelling import EnglishDictionary

    dictionary = EnglishDictionary()
    dictionary.warm()

    assert dictionary.ready
    # Base forms alone came to 48,262.
    assert len(dictionary._words) > 100_000

    for word in (
        "cat", "cats", "change", "changes", "answer", "answering",
        "become", "becomes", "benefit", "benefits", "after", "alarms",
        "chores", "checking", "walked", "walking", "happiest",
    ):
        assert dictionary.contains(word), word

    # Expanding the rules must not start accepting misspellings.
    for word in ("cair", "pett", "esay", "wehre", "teh", "animul", "somewwhere"):
        assert not dictionary.contains(word), word


def test_affix_conditions_are_honoured_rather_than_applied_blindly() -> None:
    """A rule only applies where its condition matches."""
    from echo_link_core.polish_spelling import _affix_condition_matches, _apply_affix

    assert _affix_condition_matches("[^aeiou]y", "happy", suffix=True)
    assert not _affix_condition_matches("[^aeiou]y", "play", suffix=True)
    assert _affix_condition_matches(".", "anything", suffix=True)

    # "happy" + SFX (strip "y", add "iest") where the stem ends consonant + y.
    assert _apply_affix("happy", "SFX", "y", "iest", "[^aeiou]y") == "happiest"
    # The same rule must refuse a stem it does not fit.
    assert _apply_affix("play", "SFX", "y", "iest", "[^aeiou]y") == ""
    # Prefixes attach to the front.
    assert _apply_affix("check", "PFX", "", "re", ".") == "recheck"


def _repairs(text: str):
    """What the deterministic speller would change, as original -> replacement."""
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()
    engine.warm()
    return {s.original.casefold(): s.replacement for s in engine.analyze_spelling(text)}


def test_the_measured_misspellings_are_repaired():
    """Each appeared in 13 to 20 of the 20 corpus essays after the speller ran."""
    cases = {
        "proper cair": ("cair", "care"),
        "pay atenshun": ("atenshun", "attention"),
        "becuz of rain": ("becuz", "because"),
        "the oner left": ("oner", "owner"),
        "probly true": ("probly", "probably"),
        "agin and agin": ("agin", "again"),
        "sumtimes it rains": ("sumtimes", "sometimes"),
        "an edjucashunal toy": ("edjucashunal", "educational"),
        "not avalible": ("avalible", "available"),
        "buying equipments": ("equipments", "equipment"),
        "even worser": ("worser", "worse"),
    }
    for text, (word, expected) in cases.items():
        assert _repairs(text).get(word) == expected, text


def test_the_speller_no_longer_turns_school_into_a_drinking_toast():
    """Measured: "skool" resolved to "skol", a Nordic toast, in 13 essays."""
    assert _repairs("go to skool")["skool"] == "school"


def test_british_forms_are_americanised():
    """Both the misspelling and the correct British spelling land on "learned"."""
    assert _repairs("she lernt it")["lernt"] == "learned"
    assert _repairs("she learnt it")["learnt"] == "learned"


def test_wether_becomes_whether_only_as_a_conjunction():
    assert _repairs("wether or not it rains")["wether"] == "whether"
    assert "wether" not in _repairs("the wether was sheared")
    assert "wether" not in _repairs("a wether in the field")


def test_aint_expands_to_match_its_subject():
    assert _repairs("it aint ready")["aint"] == "is not"
    assert _repairs("they aint ready")["aint"] == "are not"
    assert _repairs("I aint ready")["aint"] == "am not"
    assert _repairs("forgetting aint good")["aint"] == "is not"


def test_aint_is_left_alone_when_the_subject_is_unclear():
    """Writing the wrong verb is worse than leaving the word for a reader."""
    assert "aint" not in _repairs("the dogs aint ready")
    assert "aint" not in _repairs("a king aint here")


def test_the_common_misspelling_list_is_internally_valid():
    """The check that must hold for every entry, now and after any addition.

    A misspelling that is really a word would corrupt correct English, and a
    correction the dictionary does not know would trade one unknown word for
    another. This check rejected "defiantly", "florescent" and "therefor" when
    the list was first assembled.
    """
    from echo_link_core.polish_spelling import EnglishDictionary
    from echo_link_core.writing_suggestions import SPELLING_CORRECTIONS

    dictionary = EnglishDictionary()
    dictionary.warm()
    for wrong, right in SPELLING_CORRECTIONS.items():
        assert not dictionary.contains(wrong), f"{wrong!r} is a real word"
        for part in right.split():
            assert dictionary.contains(part), f"{right!r} is not in the dictionary"


def test_common_misspellings_are_repaired():
    for text, wrong, right in (
        ("definately going", "definately", "definitely"),
        ("please seperate them", "seperate", "separate"),
        ("recieve the calender", "recieve", "receive"),
        ("accomodate my freind", "accomodate", "accommodate"),
        ("goverment enviroment", "goverment", "government"),
    ):
        assert _repairs(text).get(wrong) == right, text


def test_words_that_only_look_like_misspellings_are_untouched():
    """Each is correct English that a naive list maps onto something else."""
    for text, word in (
        ("a defiantly rude reply", "defiantly"),
        ("the florescent stage of the plant", "florescent"),
        ("the sum reserved therefor", "therefor"),
    ):
        assert word not in _repairs(text), text


def test_the_measured_list_wins_over_the_common_list():
    """Context-aware entries must never be overridden by a general one."""
    from echo_link_core.polish_spelling import _SUBJECT_SENSITIVE_MISSPELLINGS
    from echo_link_core.writing_suggestions import SPELLING_CORRECTIONS

    for word in _SUBJECT_SENSITIVE_MISSPELLINGS:
        assert word not in SPELLING_CORRECTIONS, word
    assert "wether" not in SPELLING_CORRECTIONS


def test_the_bundled_corpus_holds_the_validated_pairs():
    from echo_link_core.polish_spelling import _bundled_misspellings

    pairs = _bundled_misspellings()
    assert len(pairs) > 24000
    assert pairs["ceasar"] == "caesar"
    assert pairs["massachussets"] == "massachusetts"
    # Transpositions and keyboard slips survive the plausibility gate.
    assert pairs["ocuntries"] == "countries"
    assert pairs["vetween"] == "between"


def test_the_bundled_corpus_never_rewrites_a_real_word():
    """The gate that matters: the raw dataset would have "corrected" about,
    accept and 2,055 other perfectly good words."""
    from echo_link_core.polish_spelling import EnglishDictionary, _bundled_misspellings

    dictionary = EnglishDictionary()
    dictionary.warm()
    offenders = [word for word in _bundled_misspellings() if dictionary.contains(word)]
    assert not offenders, offenders[:20]


def test_common_real_words_survive_the_bundled_corpus():
    for text in ("it is about time", "please accept this", "sponges absorb water"):
        assert _repairs(text) == {}, text


def test_the_bundled_corpus_never_outranks_a_context_rule():
    """A flat lookup must not overrule logic that reads the sentence."""
    from echo_link_core.polish_spelling import (
        _bundled_misspellings,
        _SUBJECT_SENSITIVE_MISSPELLINGS,
    )
    from echo_link_core.writing_suggestions import SPELLING_CORRECTIONS

    pairs = _bundled_misspellings()
    for word in (*_SUBJECT_SENSITIVE_MISSPELLINGS, "wether"):
        assert word not in pairs, word
    for word, expected in SPELLING_CORRECTIONS.items():
        assert pairs.get(word, expected) == expected, word


def test_a_missing_corpus_file_loses_coverage_not_the_review(monkeypatch):
    from echo_link_core import polish_spelling

    polish_spelling._bundled_misspellings.cache_clear()
    monkeypatch.setattr(
        polish_spelling, "resource_path", lambda *parts: "no-such-file.txt"
    )
    assert polish_spelling._bundled_misspellings() == {}
    polish_spelling._bundled_misspellings.cache_clear()


def test_implausible_pairs_are_not_in_the_corpus():
    """The source data maps "aacock" to "helicopter". Validity is not enough:
    a pair can pass both dictionary gates and still be nonsense, so the
    correction must also resemble the misspelling."""
    from difflib import SequenceMatcher

    from echo_link_core.polish_spelling import _bundled_misspellings

    for wrong, right in _bundled_misspellings().items():
        ratio = SequenceMatcher(a=wrong, b=right, autojunk=False).ratio()
        assert ratio >= 0.65, f"{wrong} -> {right} at {ratio:.2f}"


def test_the_corpus_cannot_answer_for_a_word_the_speller_declines():
    """"esay" is one edit from both "essay" and "easy". The speller refuses to
    choose and leaves it to the model, which reads the sentence; a flat corpus
    lookup must not override that decision."""
    from echo_link_core.polish_spelling import (
        _AMBIGUOUS_LEAVE_TO_MODEL,
        _bundled_misspellings,
    )

    pairs = _bundled_misspellings()
    for word in _AMBIGUOUS_LEAVE_TO_MODEL:
        assert word not in pairs, word
    assert "esay" not in _repairs("the adress is wrong in esay 1 with a pett")


# A stray apostrophe inside a word.
#
# Eleven of these survived the whole 500-percent corpus while the dictionary
# held the answer: it ranks "focus" one edit from "foc'us" with a frequency of
# 65 million. Every word carrying an apostrophe was skipped before the repair
# was ever considered.
STRAY_APOSTROPHE = (
    ("critics may foc'us on serious failures", "foc'us", "focus"),
    ("review failures as i'nformation about the system", "i'nformation", "information"),
    ("could prepare s'hoes near the door", "s'hoes", "shoes"),
    ("measure total people moved rath'er than automobiles", "rath'er", "rather"),
    ("the triba'l council met on Tuesday", "triba'l", "tribal"),
    ("this approach requir'e patience", "requir'e", "require"),
)

# The apostrophe doing its job. None of these may be touched.
HONEST_APOSTROPHE = (
    ("the dog's bowl is empty", "dog's"),
    ("they don't agree with it", "don't"),
    ("it's raining again outside", "it's"),
    ("we met at six o'clock today", "o'clock"),
    ("she can't come tomorrow", "can't"),
    ("the council's decision stands", "council's"),
    ("we've seen this before", "we've"),
    # A possessive of a word the dictionary does not know. This is the one the
    # shape test earns: flattening it to a plural would be a real edit to the
    # writer's meaning, and "microgrids" is exactly one apostrophe away.
    ("the microgrid's output fell", "microgrid's"),
)


def test_a_stray_apostrophe_inside_a_word_is_taken_out():
    for text, wrong, right in STRAY_APOSTROPHE:
        assert _repairs(text).get(wrong) == right, text


def test_a_possessive_or_a_contraction_is_left_alone():
    for text, word in HONEST_APOSTROPHE:
        assert word not in _repairs(text), text


def test_every_repair_here_only_removes_the_apostrophe():
    """The engine's answer, not the fixture's: nothing else about the word moves.

    A word the reader misspelled once must not be respelled twice on the
    strength of that. This reads what the engine actually returned, so it fails
    if a future change lets the repair wander.
    """
    for text, wrong, _ in STRAY_APOSTROPHE:
        repaired = _repairs(text)[wrong]
        assert repaired.casefold() == wrong.replace("'", "").casefold(), text


def test_alternating_case_damage_is_repaired_but_real_mixed_case_is_not():
    """A word wearing mangled capitals looked correct and was left alone.

    The scan asks `dictionary.contains(word)` and skips anything it knows, but
    `_dictionary_form` casefolds and the word set holds only casefolded
    entries, so `contains("SuSpEcTeD")` is True. The word was recognised, so
    nothing repaired it, and only the model could - which it did
    inconsistently. Essay 17 of error-riddled-20-docx finished with SuSpEcTeD,
    OnE and ArChAeOlOgIsTs still in it while AnCiEnT and ThIs in the same
    document were fixed.

    The dictionary cannot decide this: iPhone and SuSpEcTeD are the same to it.
    The shape of the capitals decides instead, by the RATIO of case flips to
    letters. Counting flips alone put JavaScript and McDonald on three and
    lowercased both.
    """
    from echo_link_core.polish_spelling import EnglishDictionary, _mangled_case_repair

    dictionary = EnglishDictionary()
    dictionary.warm()

    for damaged, expected in (
        ("SuSpEcTeD", "suspected"),
        ("ArChAeOlOgIsTs", "archaeologists"),
        ("ThIs", "this"),
        ("AnCiEnT", "ancient"),
    ):
        assert _mangled_case_repair(damaged, dictionary) == expected

    # Real mixed-case words are left exactly as written. eBay is the closest of
    # these to the line and is why the ratio is not set lower.
    for kept in ("iPhone", "JavaScript", "McDonald", "MacBook", "YouTube", "eBay"):
        assert _mangled_case_repair(kept, dictionary) == kept


def test_mangled_case_repair_leaves_what_it_cannot_be_sure_of():
    """It only touches words the dictionary knows once folded.

    RePahIr is damage too, but lowercasing gives "repahir", which is not a
    word. That one belongs to the ordinary spelling repair, which is better at
    it. ALLCAPS is out of scope entirely - _should_skip_word already refuses
    those - so CANLA is not this rule's business, and short words are skipped
    before it, which is what keeps PhD and OnE out of reach.
    """
    from echo_link_core.polish_spelling import EnglishDictionary, _mangled_case_repair

    dictionary = EnglishDictionary()
    dictionary.warm()

    for untouched in ("RePahIr", "CANLA", "PhD", "OnE", "Ancient", "ancient"):
        assert _mangled_case_repair(untouched, dictionary) == untouched


def test_a_word_wearing_damaged_capitals_is_not_protected_as_a_name():
    """Nobody is called RePahIr.

    Capitalized words are shielded because a name and its near neighbours are
    both real - "Malik" and "Mali" - and guessing between them renames someone.
    That reasoning only holds where the capitalisation could be deliberate.
    RePahIr reached the guard with the answer already found: symspell ranked
    "Repair" one edit away at a frequency of 49 million, and the name guard
    refused it, so the word survived a whole Word run of essay 17.

    The name protection itself is unchanged for real names.
    """
    from echo_link_core.polish_spelling import _capitalized_repair_is_safe

    assert _capitalized_repair_is_safe("RePahIr", "Repair")
    # Still refused, which is the case the guard exists for.
    assert not _capitalized_repair_is_safe("Malik", "Mali")


def test_a_repaired_word_does_not_inherit_the_damaged_capitals():
    """The repair takes the case its position calls for, not the source's.

    SymSpell returns its own casing, so RePahIr came back as "Repair" and left
    a capital mid-sentence. Lowering it unconditionally was tried next and
    produced "repair work is needed here." at the start of a sentence, because
    nothing downstream puts that capital back on this path.
    """
    from echo_link_core.polish_spelling import PolishRuleEngine

    engine = PolishRuleEngine()
    engine.warm()

    assert (
        engine.correct("the bridge needs RePahIr work before winter").text
        == "The bridge needs repair work before winter."
    )
    assert (
        engine.correct("RePahIr work is needed here").text
        == "Repair work is needed here."
    )
    assert (
        engine.correct("The road was fine. RePahIr came later.").text
        == "The road was fine. Repair came later."
    )
    # A real name is still left exactly as the writer typed it.
    assert (
        engine.correct("Malik visited the site last spring").text
        == "Malik visited the site last spring."
    )


def test_context_settles_a_spelling_tie_the_dictionary_has_to_refuse():
    """"esay" is one edit from essay and one from easy, so the speller refuses.

    Refusing is right - frequency favours "easy" and would write the wrong word
    - but it leaves the misspelling on the page. The sentence knows the answer,
    and the shipped MiniLM encoder can read it: substitute each candidate, and
    compare each version against the paragraph around it.

    Measured with that encoder on 2026-08-19:

        care over chair    +0.040      chair over care    +0.055
        easy over essay    +0.022      essay over easy    +0.006
        neutral context    +0.008, +0.010
        their over there   +0.004, and it picks the WRONG one

    The margin gate is what makes this safe. A tie it cannot separate falls
    back to the refusal that was there before, which is why the second half of
    this test drives the same text with the ranker switched off and asserts
    the old behaviour is still exactly what happens without it.
    """
    from echo_link_core.polish_spelling import PolishRuleEngine

    text = (
        "The exam was next week and the reading list was long. She had three "
        "assignments due before Friday. She started the esay on Sunday night."
    )

    with_context = PolishRuleEngine()
    with_context.warm()
    assert "essay" in with_context.correct(text).text

    # Without the ranker the speller refuses, exactly as it did before, and the
    # word is left for the model to read in context. Asserted so this test
    # fails if the ranker stops being what makes the difference.
    without_context = PolishRuleEngine(context_ranker=False)
    without_context.warm()
    assert "esay" in without_context.correct(text).text


def test_a_context_that_cannot_settle_the_tie_still_refuses():
    """A thin or neutral context must leave the misspelling alone.

    This is the half that keeps the change safe. The ranker scores topical fit,
    so a sentence with no topic around it produces two nearly equal scores, and
    anything below the margin has to come back as no opinion rather than as a
    coin toss.
    """
    from echo_link_core.polish_classifier import ContextWordRanker

    ranker = ContextWordRanker()
    sentence = "It was a cair."

    assert ranker.choose(sentence, 9, 13, ("care", "chair"), "") is None
    assert (
        ranker.choose(sentence, 9, 13, ("care", "chair"), "Thursday. Grey.")
        is None
    )
