from __future__ import annotations

import json
from pathlib import Path

import pytest

from echo_link_core.polish_quality import (
    CorrectionQualityGate,
    TONE_CONTRACTS,
    ToneContract,
)
from echo_link_core.polish import (
    _final_cleanup_candidate,
    _persuasive_rewrite_is_visible,
    _runtime_alternative_fact_phrases,
    _runtime_temporal_fact_phrases,
)
from echo_link_core.polish_spelling import PolishRuleEngine
from echo_link_core.selected_text import (
    _apply_fast_proofread_rules,
    _linked_source_facts_preserved,
    _task_rules,
)


def test_deterministic_spelling_restores_common_contractions_and_possessives():
    result = PolishRuleEngine().correct(
        "Thats why nobodys ready, and I dont want the unsafe choice."
    )
    assert result.text == (
        "That's why nobody's ready, and I don't want the unsafe choice."
    )
    assert " done " not in f" {result.text.casefold()} "


def test_final_cleanup_repairs_mojibake_duplicate_and_interest_list():
    source = (
        "The review didn't didn\u2019t happen. Interest came from community "
        "college students hospital employees and parents."
    )

    assert _final_cleanup_candidate("Concise", source) == (
        "The review didn't happen. Interest came from community college students, "
        "hospital employees, and parents."
    )

def test_quality_gate_reports_residual_grammar_instead_of_claiming_complete():
    result = CorrectionQualityGate().evaluate(
        "The reports is ready and the team dont know why.",
        command="Proofread",
    )
    assert not result.complete
    assert "subject-verb agreement" in result.residual_errors
    assert "missing apostrophe" in result.residual_errors


def test_final_cleanup_repairs_observed_concise_auxiliary_and_article_errors():
    text = (
        "The study didn't measures property values and it must not be presents as "
        "proof. The survey was available for first six weeks. No recording was "
        "shared outside encrypted project drive. Another paragraph repeats "
        "conclusion three times."
    )

    assert _final_cleanup_candidate("Concise", text) == (
        "The study didn't measure property values and it must not be presented as "
        "proof. The survey was available for the first six weeks. No recording was "
        "shared outside the encrypted project drive. Another paragraph repeats "
        "the conclusion three times."
    )


def test_final_cleanup_repairs_missing_comma_before_confusing_readers():
    text = (
        "The methods section jumps to consent procedures confusing readers about "
        "the sequence."
    )

    assert _final_cleanup_candidate("Concise", text) == (
        "The methods section jumps to consent procedures, confusing readers about "
        "the sequence."
    )
    result = CorrectionQualityGate().evaluate(text, command="Concise")
    assert not result.complete
    assert "missing clause comma" in result.residual_errors

def test_final_cleanup_repairs_response_separator_and_sentence_article():
    text = (
        "Responses arrived between January 5 and February 2 438 respondents replied. "
        "strongest interest came from students."
    )

    assert _final_cleanup_candidate("Concise", text) == (
        "Responses arrived between January 5 and February 2; 438 respondents replied. "
        "The strongest interest was from students."
    )
    result = CorrectionQualityGate().evaluate(text, command="Concise")
    assert "lowercase sentence start" in result.residual_errors
    assert "missing date-response separator" in result.residual_errors

@pytest.mark.parametrize(
    "text",
    (
        "The study didn't measures property values.",
        "It must not be presents as proof.",
        "The survey was available for first six weeks.",
        "No recording was shared outside encrypted project drive.",
        "Another paragraph repeats conclusion three times.",
    ),
)
def test_quality_gate_rejects_observed_concise_grammar_residue(text):
    result = CorrectionQualityGate().evaluate(text, command="Concise")

    assert not result.complete
    assert "malformed final grammar" in result.residual_errors

def test_formal_tone_gate_rejects_all_common_contractions():
    for text in (
        "They haven't determined the cause.",
        "The team couldn\u2019t complete the review.",
        "The operator shouldn't remove the file.",
    ):
        result = CorrectionQualityGate().evaluate(text, command="Formal")
        assert not result.complete
        assert not result.tone_verified


def test_formal_final_cleanup_expands_unambiguous_negative_contractions():
    assert _final_cleanup_candidate(
        "Formal",
        "They haven't determined the cause, and it isn't safe.",
    ) == "They have not determined the cause, and it is not safe."
    assert _final_cleanup_candidate(
        "Friendly",
        "They haven't determined the cause.",
    ) == "They haven't determined the cause."


def test_academic_tone_gate_and_cleanup_reject_contractions():
    result = CorrectionQualityGate().evaluate(
        "They haven't determined the cause.",
        command="Academic",
    )
    assert not result.complete
    assert not result.tone_verified
    assert _final_cleanup_candidate(
        "Academic",
        "They haven't determined the cause.",
    ) == "They have not determined the cause."


def test_quality_gate_rejects_academic_live_malformed_output():
    result = CorrectionQualityGate().evaluate(
        "They would use reported working later hours. "
        "Visitors will not pay are charged a fee. "
        "During the pilot, phase eight tablets lost their routes. "
        "The instructions for Several are written using vague vocabulary. "
        "Last week's rehearsal was unusual because three trainers spoke "
        "simultaneously, which screen they were describing; nobody could "
        "understand the caption file stopped after nineteen minutes.",
        command="Academic",
    )
    assert not result.complete
    assert "malformed rewrite residue" in result.residual_errors


def test_quality_gate_rejects_conversational_live_malformed_output():
    result = CorrectionQualityGate().evaluate(
        "Service notes donâ€™t appear. The study did not measure looked at property "
        "values and it must not be presented that as proof.",
        command="Conversational",
    )
    assert not result.complete
    assert "encoding artifact" in result.residual_errors
    assert "malformed rewrite residue" in result.residual_errors


def test_quality_gate_rejects_concise_live_punctuation():
    result = CorrectionQualityGate().evaluate(
        "Plans were submitted late and they contain conflicts. Last spring two "
        "visitors arrived. A leader records the time however several clocks failed. "
        "INV-PRIMARY contained 18,442 messages and the billing preview changed.",
        command="Concise",
    )
    assert not result.complete
    assert "missing compound comma" in result.residual_errors


def test_runtime_protects_due_date_and_numeric_comparison_phrases():
    text = (
        "The final report is due May 9, 2025, and the appendix must include a table "
        "showing all twelve gardens rather than only the six with highest participation."
    )
    assert "due May 9, 2025" in _runtime_temporal_fact_phrases(text)
    assert (
        "a table showing all twelve gardens rather than only the six with highest participation"
        in _runtime_alternative_fact_phrases(text)
    )


def test_persuasive_contract_requires_visible_evidence_led_rewrite():
    rules = _task_rules("Persuasive", "", None, hard_word_limit=False)
    contract = TONE_CONTRACTS["Persuasive"]

    assert "Do not merely proofread" in rules
    assert "existing recommendation" in rules
    assert "evidence-led" in contract.instruction


def test_cleanup_repairs_malformed_negative_coordination():
    source = "Funding comes from the grant, not and the general book budget."

    assert _apply_fast_proofread_rules(source) == (
        "Funding comes from the grant, not the general book budget."
    )


def test_quality_gate_rejects_malformed_negative_coordination():
    result = CorrectionQualityGate().evaluate(
        "Funding comes from the grant, not and the general book budget.",
        command="Persuasive",
    )

    assert not result.complete
    assert "malformed negative coordination" in result.residual_errors


def test_cleanup_repairs_malformed_evidence_clause():
    source = "That conclusion does not follow was based on the evidence."

    assert _apply_fast_proofread_rules(source) == (
        "That conclusion does not follow from the evidence."
    )


def test_quality_gate_rejects_malformed_evidence_clause():
    result = CorrectionQualityGate().evaluate(
        "That conclusion does not follow was based on the evidence.",
        command="Persuasive",
    )

    assert not result.complete
    assert "malformed evidence clause" in result.residual_errors


def test_cleanup_repairs_casual_live_residuals():
    source = (
        "Marcus must not overwrite needs the earlier backup. "
        "This is just a link; it is not evidence that leadership caused the difference."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Marcus must not overwrite the earlier backup. "
        "This relationship is only an association; it is not evidence that "
        "leadership caused the difference."
    )
    assert _apply_fast_proofread_rules(
        "Marcus must not overwrite needed the earlier backup."
    ) == "Marcus must not overwrite the earlier backup."


def test_quality_gate_rejects_casual_live_residuals():
    cases = (
        ("Marcus must not overwrite needs the earlier backup.", "malformed obligation phrase"),
        ("Marcus must not overwrite needed the earlier backup.", "malformed obligation phrase"),
        ("This is just a link; it is not evidence of causation.", "weakened association terminology"),
    )
    for text, expected in cases:
        result = CorrectionQualityGate().evaluate(text, command="Casual")
        assert not result.complete
        assert expected in result.residual_errors



def test_quality_gate_rejects_vague_link_and_unclear_subject():
    vague = CorrectionQualityGate().evaluate(
        "This is a link. It is not evidence leadership alone caused the difference.",
        command="Caveman",
    )
    unclear = CorrectionQualityGate().evaluate(
        "It would make clear emergency procedures stay the same.",
        command="Caveman",
    )

    assert not vague.complete
    assert "weakened association terminology" in vague.residual_errors
    assert not unclear.complete
    assert "malformed final grammar" in unclear.residual_errors
def test_cleanup_repairs_confident_live_residuals():
    source = (
        "For example the guide was vague. That conclusion does not follow "
        "followed from the evidence. Revenue exceeded the source ledger "
        "Incident commander Ravi responded."
    )

    assert _apply_fast_proofread_rules(source) == (
        "For example, the guide was vague. That conclusion does not follow "
        "from the evidence. Revenue exceeded the source ledger. "
        "Incident commander Ravi responded."
    )


def test_quality_gate_rejects_confident_live_residuals():
    cases = (
        ("That conclusion does not follow followed from the evidence.", "duplicated follow verb"),
        ("Revenue exceeded the source ledger Incident commander Ravi responded.", "missing incident boundary"),
        ("For example the guide was vague.", "missing example comma"),
    )
    for text, expected in cases:
        result = CorrectionQualityGate().evaluate(text, command="Confident")
        assert not result.complete
        assert expected in result.residual_errors


def test_cleanup_repairs_stacked_modal():
    source = "The file cannot may be emailed."

    assert _apply_fast_proofread_rules(source) == (
        "The file cannot be emailed."
    )


def test_quality_gate_rejects_stacked_modal():
    result = CorrectionQualityGate().evaluate(
        "The file cannot may be emailed.",
        command="Persuasive",
    )

    assert not result.complete
    assert "stacked modal" in result.residual_errors


def test_quality_gate_rejects_accidental_repeated_word():
    result = CorrectionQualityGate().evaluate(
        "By by the time the team arrived, the floor was wet.",
        command="Proofread",
    )
    assert not result.complete
    assert "repeated word" in result.residual_errors


def test_quality_gate_rejects_malformed_rephrase_verb_sequences():
    result = CorrectionQualityGate().evaluate(
        "Each participant is required check in before orientation begins. "
        "Two visitors wandered entered a restricted hallway. "
        "The team is preparing conducting a drill. The director says at the "
        "exercise will test whether services can continue operate. "
        "The report lists all failed control prior to the clinic schedules another drill. "
        "The director says will test assess whether services continue, and the plans "
        "contain received conflicting instructions. The schedule being was not public. "
        "The script was harsh despite the drill schedule was not public.",
        command="Rephrase",
    )

    assert not result.complete
    assert "malformed verb sequence" in result.residual_errors


def test_quality_gate_rejects_manual_rephrase_review_findings():
    result = CorrectionQualityGate().evaluate(
        "Observers came from County Health Office. Critical operations can sustain. "
        "Nobody notified the issue. The units use specified approved ranges. "
        "Neither removed values nor estimated temperatures are permitted, and any "
        "adjustments should include initials and the original value. Essential services "
        "be able to continue. The plans were submitted delayed, and Nobody reported it.",
        command="Rephrase",
    )

    assert not result.complete
    assert "awkward rephrase phrase" in result.residual_errors


def test_quality_gate_rejects_semicolon_inside_neither_nor():
    result = CorrectionQualityGate().evaluate(
        "Neither erased values; nor estimated temperatures are allowed.",
        command="Shorten",
    )

    assert not result.complete
    assert "correlative punctuation" in result.residual_errors


def test_quality_gate_rejects_malformed_fuel_percentage_clause():
    result = CorrectionQualityGate().evaluate(
        "The generator was serviced, with a fuel tank at 87 percent full.",
        command="Shorten",
    )

    assert not result.complete
    assert "malformed percentage clause" in result.residual_errors


def test_quality_gate_rejects_unpunctuated_generator_fuel_clause():
    result = CorrectionQualityGate().evaluate(
        "The backup generator was serviced on September 28 "
        "and its fuel tank is 87 percent full.",
        command="Proofread",
    )

    assert not result.complete
    assert "generator comma splice" in result.residual_errors


@pytest.mark.parametrize(
    "text",
    (
        "Elena received three checklists and none of them agree.",
        "The installation covers seven districts and every technician signs in.",
        "Training begins in Conference Room 5C and remote workers join online.",
        "Elena pauses that district but other districts keep working.",
        "The proposal does not request permanent hours and it does not promise use.",
        "That tone weakens the proposal and it does not answer the question.",
    ),
)
def test_quality_gate_rejects_page_two_missing_compound_commas(text):
    result = CorrectionQualityGate().evaluate(text, command="Proofread")

    assert not result.complete
    assert "missing compound comma" in result.residual_errors


def test_quality_gate_rejects_does_not_shows():
    result = CorrectionQualityGate().evaluate(
        "The maintenance record does not shows who completed the test.",
        command="Clear",
    )

    assert not result.complete
    assert "auxiliary agreement" in result.residual_errors


def test_quality_gate_rejects_clear_page_two_punctuation_losses():
    result = CorrectionQualityGate().evaluate(
        "Keep customer names gate codes and service notes private. "
        "At 10:00 a.m., Tuesday finance will test exports. "
        "List every unresolved defect the person responsible the next review "
        "time and whether service was interrupted. Everyone agrees the note "
        "should be brief but omitting a failure would be inaccurate.",
        command="Clear",
    )

    assert not result.complete
    assert "unpunctuated list" in result.residual_errors
    assert "misplaced time comma" in result.residual_errors
    assert "missing compound comma" in result.residual_errors


def test_quality_gate_rejects_clear_page_three_punctuation_losses():
    result = CorrectionQualityGate().evaluate(
        "They fix every neighborhood problem a claim that is too simple. "
        "The group counted volunteer hours crop donations cancelled workdays "
        "and attendance. The survey ran for six weeks which caused fewer "
        "responses. It jumps to consent procedures causing readers to become "
        "confused. It should not claim Gardens eliminate food insecurity.",
        command="Clear",
    )

    assert not result.complete
    assert "unpunctuated list" in result.residual_errors
    assert "missing clause comma" in result.residual_errors
    assert "mid-sentence capitalization" in result.residual_errors


def test_quality_gate_rejects_clear_page_four_grammar_and_list_losses():
    result = CorrectionQualityGate().evaluate(
        "That conclusion does not follows. It doesn't says whether. "
        "He did not ran the query, and the report should not implies that. "
        "There were no differences in amounts customer names or tax codes. "
        "Several sentences in the postmortem are fragments others combine six actions with no "
        "clear order and the word 'utilize' is used. It must not blame the "
        "operator as the technical cause although detection was delayed. Follow actions "
        "include adding a 409 regression test defining the order in RB-SYNC-64 "
        "and changing alert HP-DUP-4.",
        command="Clear",
    )

    assert not result.complete
    assert "auxiliary agreement" in result.residual_errors
    assert "unpunctuated list" in result.residual_errors
    assert "missing clause comma" in result.residual_errors
    assert "fused sentence" in result.residual_errors
    assert "missing compound comma" in result.residual_errors
    assert "uncorrected vocabulary" in result.residual_errors


def test_quality_gate_rejects_clear_page_five_clauses_and_list():
    result = CorrectionQualityGate().evaluate(
        "Respondents indicated they would use a preference for later hours. "
        "If the board rejects it the library cannot advertise transportation. "
        "If costs exceed the grant the program must stop. Success measures "
        "include nightly attendance repeat visits reported safety issues staff "
        "overtime and the percentage of visitors who arrive by bus.",
        command="Clear",
    )

    assert not result.complete
    assert "awkward preference phrase" in result.residual_errors
    assert "missing introductory comma" in result.residual_errors
    assert "unpunctuated list" in result.residual_errors

    variant = CorrectionQualityGate().evaluate(
        "Respondents indicated they would use preference for later hours.",
        command="Technical",
    )
    assert not variant.complete
    assert "awkward preference phrase" in variant.residual_errors


def test_quality_gate_rejects_improve_page_two_phrasing():
    result = CorrectionQualityGate().evaluate(
        "The team classified the issue as cosmetic, despite that it blocked "
        "repairs. That wording has no a useful effect. The accessibility "
        "coordinator, Dana Cho, requested a corrected file.",
        command="Improve",
    )

    assert not result.complete
    assert "awkward connector" in result.residual_errors
    assert "malformed article phrase" in result.residual_errors
    assert "unnecessary role commas" in result.residual_errors


def test_quality_gate_rejects_improve_page_three_grammar():
    result = CorrectionQualityGate().evaluate(
        "It must not be presented them as proof. The Neither event was "
        "controlled by leaders. There also contain several structure issues. "
        "The paragraph lists items without with commas.",
        command="Improve",
    )

    assert not result.complete
    assert "malformed passive phrase" in result.residual_errors
    assert "malformed determiner" in result.residual_errors
    assert "existential agreement" in result.residual_errors
    assert "duplicate preposition" in result.residual_errors


def test_quality_gate_rejects_must_not_blame_identifies():
    result = CorrectionQualityGate().evaluate(
        "It must not blame identifies the operator as the technical cause.",
        command="Improve",
    )

    assert not result.complete
    assert "malformed verb sequence" in result.residual_errors


def test_quality_gate_rejects_compound_predicate_comma():
    result = CorrectionQualityGate().evaluate(
        "A survey indicates interest, but does not guarantee attendance.",
        command="Improve",
    )

    assert not result.complete
    assert "compound predicate comma" in result.residual_errors


def test_quality_gate_rejects_rephrase_page_two_grammar():
    result = CorrectionQualityGate().evaluate(
        "No A personal email address may still be utilized. Elena directed the "
        "system administrator Marcus Bell to retain the backup. Last week's "
        "rehearsal was different due to three trainers speaking at once. Elena "
        "gives receive written approval. The device does not transmits private "
        "notes. The result is less precise compared to the extended version.",
        command="Rephrase",
    )

    assert not result.complete
    assert "malformed determiner" in result.residual_errors
    assert "awkward title article" in result.residual_errors
    assert "vague rephrase" in result.residual_errors
    assert "malformed verb sequence" in result.residual_errors
    assert "auxiliary agreement" in result.residual_errors
    assert "faulty comparison" in result.residual_errors


def test_quality_gate_rejects_rephrase_authorization_structure():
    result = CorrectionQualityGate().evaluate(
        "Council maintains the ultimate authority and grants approval for the "
        "trial does not authorize permanent hours after June 26.",
        command="Rephrase",
    )

    assert not result.complete
    assert "malformed verb sequence" in result.residual_errors


@pytest.mark.parametrize(
    "text",
    (
        "The study did not measure analyzed values and must not be was presented.",
        "Volunteers carried drew water and the city closed relocated the stop.",
        "The queue reached is zero and the dashboard displays still displayed totals.",
        "Visitors are charged will not pay and the library cannot provide advertise.",
        "The recommendation should explain explaining why.",
        "The drill included includes 72 volunteers.",
    ),
)
def test_quality_gate_rejects_rephrase_generation_residue(text):
    result = CorrectionQualityGate().evaluate(text, command="Rephrase")

    assert not result.complete
    assert "malformed rewrite residue" in result.residual_errors


def test_quality_gate_rejects_observed_rephrase_merge_residue():
    result = CorrectionQualityGate().evaluate(
        "They recorded produce, which represented which 61 percent went to the pantry. "
        "These numbers sound are impressive. There were which caused fewer responses. "
        "The researchers added appointing hiring coordinators. "
        "Garden leaders controlled Neither event. "
        "The water line at Garden L-04, remained broken.",
        command="Rephrase",
    )

    assert not result.complete
    assert "malformed rewrite residue" in result.residual_errors


def test_quality_gate_rejects_rephrase_page_three_variants():
    result = CorrectionQualityGate().evaluate(
        "Leadership was a more reliable indicator of involvement compared to "
        "the size of the garden. At Garden L-09, The city closed the bus stop. "
        "Participants had the option to refuse audio recording without, which "
        "would result in losing access. The last report is due May 9.",
        command="Rephrase",
    )

    assert not result.complete
    assert "faulty comparison" in result.residual_errors
    assert "mid-sentence capitalization" in result.residual_errors
    assert "malformed access clause" in result.residual_errors
    assert "uncorrected vocabulary" in result.residual_errors


def test_quality_gate_rejects_em_dashes():
    result = CorrectionQualityGate().evaluate(
        "The report is clear\u2014and complete.",
        command="Proofread",
    )

    assert not result.complete
    assert "em dash" in result.residual_errors


def test_quality_gate_rejects_introductory_clause_fragments():
    result = CorrectionQualityGate().evaluate(
        "The survey was incomplete. Because it was only available in English. "
        "Which caused fewer responses from some households. "
        "The measures reduce risk. While making clear that procedures remain unchanged.",
        command="Proofread",
    )

    assert not result.complete
    assert "sentence fragment" in result.residual_errors
    while_result = CorrectionQualityGate().evaluate(
        "The measures reduce risk. While making clear that procedures remain unchanged.",
        command="Proofread",
    )
    assert "sentence fragment" in while_result.residual_errors


def test_quality_gate_rejects_unjoined_independent_clauses():
    result = CorrectionQualityGate().evaluate(
        "Two visitors entered the hallway, nobody reported the problem. "
        "A manager suggested removing it regardless, that argument was rejected.",
        command="Proofread",
    )

    assert not result.complete
    assert "comma splice" in result.residual_errors


def test_quality_gate_rejects_locked_agreement_and_vocabulary_residuals():
    result = CorrectionQualityGate().evaluate(
        "Neither erased values nor estimated temperatures is allowed. "
        "There is also several punctuation problems. "
        "The media vendor haven't confirmed the date. "
        "The rehearsal was very unique.",
        command="Proofread",
    )

    assert not result.complete
    assert "subject-verb agreement" in result.residual_errors
    assert "existential agreement" in result.residual_errors
    assert "uncorrected vocabulary" in result.residual_errors


def test_quality_gate_rejects_locked_fused_sentences_and_unpunctuated_lists():
    result = CorrectionQualityGate().evaluate(
        "One paragraph lists soil testing tool storage compost training and language access "
        "without commas another paragraph repeats the conclusion. "
        "The trainer explains route syncing offline notes photo uploads and emergency escalation.",
        command="Proofread",
    )

    assert not result.complete
    assert "fused sentence" in result.residual_errors
    assert "unpunctuated list" in result.residual_errors


def test_quality_gate_allows_grammatical_repeated_word():
    result = CorrectionQualityGate().evaluate(
        "She said that that report was final.",
        command="Proofread",
    )
    assert result.complete


def test_quality_gate_allows_about_dogs_is_that_construction():
    result = CorrectionQualityGate().evaluate(
        "One thing that is good about dogs is that they help people feel less lonely.",
        command="Proofread",
    )

    assert result.complete
    assert "subject-verb agreement" not in result.residual_errors


def test_quality_gate_allows_after_dogs_is_not_fun_construction():
    result = CorrectionQualityGate().evaluate(
        "Cleaning up after dogs is not fun.",
        command="Proofread",
    )

    assert result.complete
    assert "subject-verb agreement" not in result.residual_errors


def test_quality_gate_rejects_dog_document_pronoun_and_structure_residuals():
    result = CorrectionQualityGate().evaluate(
        "They bring toys over and put it on your lap. "
        "Sometimes they just lay on the floor. "
        "They might have too much energy, then they run around crazy. "
        "Every dog is different in their own ways.",
        command="Proofread",
    )

    assert not result.complete
    assert "pronoun agreement" in result.residual_errors
    assert "verb choice" in result.residual_errors
    assert "comma splice" in result.residual_errors


def test_quality_gate_rejects_capitalized_conjunction_after_comma():
    result = CorrectionQualityGate().evaluate(
        "Dogs make people happy, But not every dog is the same.",
        command="Proofread",
    )

    assert "capitalization after comma" in result.residual_errors
    assert result.complete is False


def test_quality_gate_allows_if_then_construction():
    result = CorrectionQualityGate().evaluate(
        "If your dog gets sick, then you have to take them to the vet.",
        command="Proofread",
    )

    assert result.complete
    assert "comma splice" not in result.residual_errors


def test_quality_gate_rejects_other_dogs_tries_agreement():
    result = CorrectionQualityGate().evaluate(
        "Other dogs hate water and tries to run away from it.",
        command="Proofread",
    )

    assert not result.complete
    assert "subject-verb agreement" in result.residual_errors


def test_quality_gate_rejects_reported_dog_queue_agreement_residual():
    result = CorrectionQualityGate().evaluate(
        "Dogs also teach responsibility because they need food, water, walks, baths, "
        "and visits to the veterinarian. Kids can learn from having a dog if they help "
        "care for it, but sometimes they do not, and the parents ends up doing all the work.",
        command="Proofread",
    )

    assert not result.complete
    assert "subject-verb agreement" in result.residual_errors


def test_quality_gate_rejects_reported_dog_queue_hallucination_and_residuals():
    result = CorrectionQualityGate().evaluate(
        "There is many kinds of dogs, so almost everybody can find one they like. "
        "Big includes dogs, little dogs, fluffy dogs, fast dogs, lazy dogs, and "
        "funny-looking dogs too. Some are good for hunting, some are good for helping "
        "blind people, and some just like laying around on the couch all day doing not much.",
        command="Proofread",
    )

    assert not result.complete
    assert "existential agreement" in result.residual_errors
    assert "malformed list phrase" in result.residual_errors
    assert "verb choice" in result.residual_errors
    assert "awkward word order" in result.residual_errors


def test_quality_gate_rejects_reported_dog_queue_possessive_residual():
    result = CorrectionQualityGate().evaluate(
        "That's okay because every dog got their own personality and stuff.",
        command="Proofread",
    )

    assert not result.complete
    assert "verb and possessive agreement" in result.residual_errors


def test_all_visible_tones_have_strict_contracts():
    expected = {
        "Clear",
        "Improve",
        "Rephrase",
        "Friendly",
        "Formal",
        "Professional",
        "Academic",
        "Conversational",
        "Concise",
        "Detailed",
        "Persuasive",
        "Confident",
        "Casual",
        "Technical",
        "Simplified",
        "High-school level",
        "College level",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    }
    assert set(TONE_CONTRACTS) == expected
    assert all(isinstance(value, ToneContract) for value in TONE_CONTRACTS.values())
    assert all(value.instruction.strip() for value in TONE_CONTRACTS.values())
    assert all(value.prohibited_behaviors for value in TONE_CONTRACTS.values())


def test_concise_contract_explicitly_forbids_clause_loss():
    instruction = TONE_CONTRACTS["Concise"].instruction.casefold()
    assert "every factual clause" in instruction
    assert "actor" in instruction
    assert "constraint" in instruction




def test_proofread_prompt_requests_polished_vocabulary_and_sentence_structure():
    rules = _task_rules(
        "Proofread",
        "",
        None,
        hard_word_limit=False,
    )
    assert "polished rewrite" in rules.casefold()
    assert "vocabulary" in rules.casefold()
    assert "sentence structure" in rules.casefold()
    assert "preserve sentence order" in rules.casefold()
    assert "immutable values" in rules.casefold()
    assert "final verification pass" in rules.casefold()
    assert "comma splices" in rules.casefold()
    assert "sentence fragments" in rules.casefold()


def test_dog_documents_are_locked_as_polished_and_professional_regressions():
    path = Path(__file__).parent / "fixtures" / "dog_correction_regression.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    assert "becaus" in case["source"]
    assert "dont" in case["source"]
    assert "responsiblity" in case["source"]
    assert CorrectionQualityGate().evaluate(
        case["polished_proofread_reference"],
        command="Proofread",
    ).complete
    assert CorrectionQualityGate().evaluate(
        case["professional_reference"],
        command="Professional",
    ).complete


def test_final_proofread_repairs_safe_double_comparatives():
    assert _apply_fast_proofread_rules(
        "This is more better, but that is most worst."
    ) == "This is better, but that is worst."


def test_final_proofread_repairs_capitalized_conjunction_after_comma():
    assert _apply_fast_proofread_rules(
        "Dogs make people happy, But not every dog is the same."
    ) == "Dogs make people happy, but not every dog is the same."


def test_final_proofread_repairs_locked_page_agreement_patterns():
    assert _apply_fast_proofread_rules(
        "Several status messages wasn't clear. The recording stop without warning after Monday review."
    ) == (
        "Several status messages weren't clear. The recording stopped without warning after Monday's review."
    )


def test_final_proofread_repairs_reported_dog_queue_residuals():
    assert _apply_fast_proofread_rules(
        "Sometimes they don't help, and the parents ends up doing all the work. "
        "There is many kinds of dogs. Some like laying around all day doing not much. "
        "Every dog got their own personality."
    ) == (
        "Sometimes they don't help, and the parents end up doing all the work. "
        "There are many kinds of dogs. Some like lying around all day not doing much. "
        "Every dog has its own personality."
    )


def test_final_proofread_repairs_full_page_structure_and_vocabulary_residuals():
    source = (
        "Priya asked every worker to initial each line after they verify it. "
        "Some volunteers were signing before the item had actually been inspected, "
        "which makes the record unreliable. Staff currently walk between rooms. "
        "He told the group to use blotting paper for wet covers, direct heat is prohibited. "
        "Staff also needs to contact patrons. The draft sounds blaming. "
        "A supervisor will record each result on form TOKEN and unresolved failures is "
        "reported to Priya immediately."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Priya asked every worker to initial each line after they verified it. "
        "Some volunteers were signing before the item had actually been inspected, "
        "which made the record unreliable. Staff members currently walk between rooms. "
        "He told the group to use blotting paper for wet covers; direct heat is prohibited. "
        "Staff members also need to contact patrons. The draft sounds accusatory. "
        "A supervisor will record each result on form TOKEN and will report unresolved failures "
        "to Priya immediately."
    )


def test_final_proofread_repairs_unseen_clinic_page_residuals():
    source = (
        "Both units must stay between their approved temperature ranges, but "
        "MedTrack 4.7 sometimes display a blank reading. Technicians have not determined "
        "whether the cable, the tablet port, or the software cache are responsible. "
        "Staff also needs to call patients. The script sounds careless. "
        "No one may send it through email. A supervisor records each result on form TOKEN "
        "and unresolved failures are reported to Amina immediately."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Each unit must stay within its approved temperature range, but "
        "MedTrack 4.7 sometimes displays a blank reading. Technicians have not determined "
        "whether the cable, the tablet port, or the software cache is responsible. "
        "Staff members also need to call patients. The script sounds accusatory. "
        "No one may send it by email. A supervisor records each result on form TOKEN "
        "and reports unresolved failures to Amina immediately."
    )


def test_final_proofread_repairs_locked_clinic_punctuation_residuals():
    source = (
        "At 12:40 p.m. observers will test elevator recall emergency lighting oxygen alarms "
        "door locks the local server and the fire panel connection. "
        "After the all clear, department leads compare their logs. Technicians have not determined "
        "whether the cable, tablet port, or software cache are responsible."
    )

    assert _apply_fast_proofread_rules(source) == (
        "At 12:40 p.m., observers will test elevator recall, emergency lighting, oxygen alarms, "
        "door locks, the local server, and the fire panel connection. "
        "After the all-clear, department leads compare their logs. Technicians have not determined "
        "whether the cable, tablet port, or software cache is responsible."
    )


def test_final_proofread_repairs_locked_launch_residuals():
    source = (
        "The launch was scheduled, but the final review did not happen as the plan said it would. "
        "Three checklists arrived and none of them agrees about ownership. "
        "Three revised checklists arrived and none of them agrees on ownership. "
        "Which was a serious mistake. The trainer has forty five minutes to explain route syncing "
        "offline notes photo uploads and emergency escalation. Several instruction is written with "
        "vague vocabulary, for example the guide says to handle it in the normal fashion. "
        "Several instructions are written with vague vocabulary for example the guide is unclear. "
        "At 10:00 a.m., Tuesday, finance will test exports. "
        "The rehearsal was very unique because three trainers talked at once, nobody understood. "
        "The media vendor haven't confirmed the date. Everyone agree the note should be brief."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The launch was scheduled, but the final review did not occur as planned. "
        "Three checklists arrived and none of them agree on ownership. "
        "Three revised checklists arrived and none of them agree on ownership, which was a "
        "serious mistake. The trainer has forty-five minutes to explain route syncing, "
        "offline notes, photo uploads, and emergency escalation. Several instructions are written with "
        "vague vocabulary; for example, the guide says to handle it in the usual way. "
        "Several instructions are written with vague vocabulary; for example, the guide is unclear. "
        "At 10:00 a.m. Tuesday, finance will test exports. "
        "The rehearsal was unusual because three trainers talked at once; nobody understood. "
        "The media vendor has not confirmed the date. Everyone agrees the note should be brief."
    )


def test_final_proofread_replaces_nonstandard_irregardless():
    source = "A rushed manager suggested they could remove it irregardless."

    assert _apply_fast_proofread_rules(source) == (
        "A rushed manager suggested they could remove it regardless."
    )


def test_final_proofread_restores_common_contraction_apostrophes():
    source = "It doesnt work, they dont know why, and the test didnt finish."

    assert _apply_fast_proofread_rules(source) == (
        "It doesn't work, they don't know why, and the test didn't finish."
    )


def test_final_proofread_repairs_modal_and_auxiliary_drift():
    source = (
        "Every participant must checks in, receives a badge, and places a phone in a locker. "
        "The maintenance record don't shows who completed the test. "
        "They haven't determine whether the cable is responsible. "
        "Neither erased values or estimated temperatures is allowed. "
        "The checklist says it should contains twelve items. "
        "The message should explains the delay, confirms care remains available, "
        "and provides the clinic's number. "
        "The schedule was not is public. "
        "Department leads compare their logs and explains any gaps."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Every participant must check in, receive a badge, and place a phone in a locker. "
        "The maintenance record doesn't show who completed the test. "
        "They haven't determined whether the cable is responsible. "
        "Neither erased values nor estimated temperatures are allowed. "
        "The checklist says it should contain twelve items. "
        "The message should explain the delay, confirm care remains available, "
        "and provide the clinic's number. "
        "The schedule was not public. "
        "Department leads compare their logs and explain any gaps."
    )


def test_final_proofread_repairs_overwrite_used_modal_drift():
    source = "Marcus must not overwrite used the older backup because finance needs it."

    assert _apply_fast_proofread_rules(source) == (
        "Marcus must not overwrite the older backup because finance needs it."
    )


def test_final_proofread_repairs_overwrite_kept_modal_drift():
    source = "Marcus must not overwrite kept the earlier backup because finance needs it."

    assert _apply_fast_proofread_rules(source) == (
        "Marcus must not overwrite the earlier backup because finance needs it."
    )


def test_final_proofread_repairs_missing_who_before_had_access():
    source = "Quantity does not explain had access to plots."

    assert _apply_fast_proofread_rules(source) == (
        "Quantity does not explain who had access to plots."
    )


def test_final_proofread_repairs_subject_and_does_not_coordination():
    source = "It needs to say DispatchGrid 3.2 and does not send private service notes."

    assert _apply_fast_proofread_rules(source) == (
        "It needs to say DispatchGrid 3.2 does not send private service notes."
    )


def test_quality_gate_rejects_subject_and_does_not_coordination():
    result = CorrectionQualityGate().evaluate(
        "It needs to say DispatchGrid 3.2 and does not send private service notes.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed auxiliary coordination" in result.residual_errors

def test_quality_gate_rejects_subjectless_not_auxiliary_clause():
    result = CorrectionQualityGate().evaluate(
        "It treated HTTP 409 as a new event. Not was an existing record.",
        command="Caveman",
    )

    assert not result.complete
    assert "incomplete clause" in result.residual_errors

def test_final_proofread_repairs_the_no_determiner():
    source = "The No recording was shared outside the encrypted drive."

    assert _apply_fast_proofread_rules(source) == (
        "No recording was shared outside the encrypted drive."
    )


@pytest.mark.parametrize(
    "text",
    (
        "The maintenance record does not show completed the final load test.",
        "It goes to a personal account or sends it by email.",
        "No one may scan.",
        "If any alarm fails triggers.",
        "If refrigerator FR-22 goes above 46 degrees Fahrenheit.",
        "They should never.",
        "The study did not measure looks at property values.",
        "Instead, includes volunteer hours and crop donations.",
        "There has punctuation and structure problems in the report.",
        "Not treated it as existing.",
        "No customer data might show in the public version.",
        "They said they would use work late at least twice a month.",
        "The trial starts April 6 through June 26.",
        "Funding also comes from not the general book budget.",
        "Safety issues reported. Staff overtime tracked. Bus use percentage noted.",
        "It should explain Pretending the evidence is stronger than it is.",
        "The study did not measure affects property values.",
        "The study must not be shows gardens increase rent.",
        "One paragraph has list items with without commas.",
        "It should explain The limited trial is worth testing.",
        "The evidence is stronger than it is. It should not pretend.",
        "Without.",
    ),
)
def test_quality_gate_rejects_final_caveman_false_positive_grammar(text):
    result = CorrectionQualityGate().evaluate(text, command="Caveman")

    assert not result.complete
    # Dangling conditionals moved to a Caveman-only tone check so real
    # documents' short If-clauses stop failing Proofread output.
    assert (
        "malformed final grammar" in result.residual_errors
        or not result.tone_verified
    )

def test_quality_gate_rejects_the_no_determiner():
    result = CorrectionQualityGate().evaluate(
        "The No recording was shared outside the encrypted drive.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed determiner" in result.residual_errors

def test_quality_gate_rejects_treated_not_as_clause():
    result = CorrectionQualityGate().evaluate(
        "It treated not as existing.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed comparison clause" in result.residual_errors

def test_quality_gate_rejects_malformed_send_go_clause():
    result = CorrectionQualityGate().evaluate(
        "DispatchGrid 3.2 does not send private service notes go to a personal cloud account.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed send clause" in result.residual_errors


def test_quality_gate_rejects_stray_gerund_sentence():
    result = CorrectionQualityGate().evaluate(
        "A failure would make it less accurate than the longer version. Omitting.",
        command="Caveman",
    )

    assert not result.complete
    assert "incomplete clause" in result.residual_errors

def test_quality_gate_rejects_missing_who_before_had_access():
    result = CorrectionQualityGate().evaluate(
        "Quantity does not explain had access to plots.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed access clause" in result.residual_errors

def test_quality_gate_rejects_overwrite_kept_modal_drift():
    result = CorrectionQualityGate().evaluate(
        "Marcus must not overwrite kept the earlier backup because finance needs it.",
        command="Caveman",
    )

    assert not result.complete
    assert "malformed obligation phrase" in result.residual_errors

def test_quality_gate_rejects_overwrite_used_modal_drift():
    result = CorrectionQualityGate().evaluate(
        "Marcus must not overwrite used the older backup because finance needs it.",
        command="Proofread",
    )

    assert not result.complete
    assert "malformed obligation phrase" in result.residual_errors

def test_quality_gate_rejects_modal_and_auxiliary_drift():
    result = CorrectionQualityGate().evaluate(
        "Every participant must checks in. The maintenance record don't shows the owner. "
        "They haven't determine the cause. The schedule was not is public.",
        command="Proofread",
    )

    assert not result.complete
    assert "modal agreement" in result.residual_errors
    assert "auxiliary agreement" in result.residual_errors


def test_final_proofread_repairs_full_page_auxiliary_variants():
    source = (
        "The review didn't happened. The final review did not happened. "
        "Marcus must not overwrote the backup. "
        "The wording has no useful affect. The old system cannot will be removed. "
        "The note must lists defects and does not sends private notes. "
        "The study did not measured values. It must not can be presented as proof. "
        "Quantity alone don't explain access. Neither events were controlled. "
        "Participants could decline recording without, losing access. "
        "The conclusion should states the limits and should not claims certainty. "
        "It do not promises attendance. It does not requests permanent hours. "
        "It does not promises attendance. It does not approves the schedule. "
        "The board must approves the schedule. "
        "The evidence is more stronger. Approval does not to authorize permanent hours."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The review didn't happen. The final review did not happen. "
        "Marcus must not overwrite the backup. "
        "The wording has no useful effect. The old system cannot be removed. "
        "The note must list defects and does not send private notes. "
        "The study did not measure values. It must not be presented as proof. "
        "Quantity alone doesn't explain access. Neither event was controlled. "
        "Participants could decline recording without losing access. "
        "The conclusion should state the limits and should not claim certainty. "
        "It does not promise attendance. It does not request permanent hours. "
        "It does not promise attendance. It does not approve the schedule. "
        "The board must approve the schedule. "
        "The evidence is stronger. Approval does not authorize permanent hours."
    )


def test_final_proofread_repairs_reported_comma_splice():
    source = (
        "The signs were vague, nobody reported the problem until the exercise ended?"
    )

    assert _apply_fast_proofread_rules(source) == (
        "The signs were vague. Nobody reported the problem until the exercise ended."
    )


def test_final_proofread_repairs_singular_dont_follow():
    source = "That conclusion dont follow from the evidence."

    assert _apply_fast_proofread_rules(source) == (
        "That conclusion doesn't follow from the evidence."
    )


def test_quality_gate_counts_underscore_identifier_as_one_caveman_word():
    result = CorrectionQualityGate().evaluate(
        "Engineer Sofia Chen found retry worker sb_retry_2 treated HTTP 409 as a new event, not an existing record.",
        command="Caveman",
    )

    assert result.complete
    assert result.tone_verified

def test_quality_gate_counts_decimal_as_one_caveman_word():
    result = CorrectionQualityGate().evaluate(
        "It needs to state that DispatchGrid 3.2 does not send private service notes to a personal cloud account.",
        command="Caveman",
    )

    assert result.complete
    assert result.tone_verified

def test_quality_gate_rejects_long_ordinary_caveman_sentence():
    result = CorrectionQualityGate().evaluate(
        "These numbers sound impressive, but quantity by itself does not explain who had access to plots or why residents stopped participating.",
        command="Caveman",
    )

    assert not result.complete
    assert not result.tone_verified


def test_quality_gate_allows_long_caveman_structured_fact_list():
    result = CorrectionQualityGate().evaluate(
        "The final report must list every unresolved defect, the person responsible, the next review time, and whether customer service was interrupted.",
        command="Caveman",
    )

    assert result.complete
    assert result.tone_verified

def test_quality_gate_rejects_incomplete_caveman_clauses():
    result = CorrectionQualityGate().evaluate(
        "Normal appointments stop without. Instead, tracked volunteer hours. "
        "Without uses commas. Not comes from the book budget. "
        "But does not guarantee attendance. Does not send private notes. "
        "June 26 authorizes permanent hours after.",
        command="Caveman",
    )

    assert not result.complete
    assert "incomplete clause" in result.residual_errors


def test_quality_gate_rejects_subjectless_negation_and_trailing_after():
    result = CorrectionQualityGate().evaluate(
        "The app runs locally. Does not send private notes. "
        "June 26 authorizes permanent hours after.",
        command="Caveman",
    )

    assert not result.complete
    assert "incomplete clause" in result.residual_errors


def test_final_proofread_repairs_small_caveman_auxiliary_errors():
    source = (
        "The researchers fixed the problem in April 22. "
        "It is not shows that leadership caused the difference. "
        "There has issues with punctuation."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The researchers fixed the problem on April 22. "
        "It does not show that leadership caused the difference. "
        "There are issues with punctuation."
    )


def test_final_proofread_repairs_caveman_articles_and_compound_modifier():
    source = (
        "During the pilot eight tablets lost routes after battery dropped. "
        "Support team called issue cosmetic. This was serious mistake. "
        "The trial needs one on call supervisor."
    )

    assert _apply_fast_proofread_rules(source) == (
        "During the pilot, eight tablets lost routes after the battery dropped. "
        "The support team called the issue cosmetic. This was a serious mistake. "
        "The trial needs one on-call supervisor."
    )


def test_final_proofread_repairs_missing_clear_mode_commas():
    source = (
        "Services can continue without utility power but several plans arrived late. "
        "The tank is full; however the record is incomplete. "
        "At each stage a leader records the time; however several timers failed. "
        "Both units must stay in range but MedTrack 4.7 may show a blank reading. "
        "Orientation begins in Cedar Training Hall and the first outage starts later."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Services can continue without utility power, but several plans arrived late. "
        "The tank is full; however, the record is incomplete. "
        "At each stage, a leader records the time; however, several timers failed. "
        "Both units must stay in range, but MedTrack 4.7 may show a blank reading. "
        "Orientation begins in Cedar Training Hall, and the first outage starts later."
    )


def test_final_proofread_collapses_duplicate_protected_negation():
    source = "The maintenance record does not does not show who ran the test."

    assert _apply_fast_proofread_rules(source) == (
        "The maintenance record does not show who ran the test."
    )


def test_quality_gate_rejects_missing_clear_mode_commas():
    result = CorrectionQualityGate().evaluate(
        "The tank is full; however the record is incomplete. "
        "At each stage a leader records time. "
        "Services continue but several plans conflict.",
        command="Clear",
    )

    assert not result.complete
    assert "missing clause comma" in result.residual_errors
    assert CorrectionQualityGate().evaluate(
        "Services continue, but several plans conflict.",
        command="Clear",
    ).complete


def test_final_proofread_repairs_locked_research_fragments_and_appendix_phrase():
    source = (
        "Some residents stopped participating. Because the survey was only available in English. "
        "Which caused fewer responses. There also contains several punctuation problems. "
        "Instead the group counted attendance. Instead, The group recorded donations. "
        "These numbers sound impressive but quantity alone is incomplete. "
        "At Garden L-04 the water line broke. At Garden L-09 the bus stop closed so older residents "
        "had fewer ways to arrive. At Garden L-09 the city closed the adjacent bus stop so older "
        "participants had fewer ways to arrive. Neither event was controlled by leaders and ignoring "
        "either event would misuse the data. "
        "The appendix must include the translated survey, dates, the two transportation disruptions, "
        "and a table showing six with highest participation."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Some residents stopped participating. The survey was only available in English, "
        "which caused fewer responses. There are also several punctuation problems. "
        "Instead, the group counted attendance. Instead, the group recorded donations. "
        "These numbers sound impressive, but quantity alone is incomplete. "
        "At Garden L-04, the water line broke. At Garden L-09, the bus stop closed, so older residents "
        "had fewer ways to arrive. At Garden L-09, the city closed the adjacent bus stop, so older "
        "participants had fewer ways to arrive. Neither event was controlled by leaders, and ignoring "
        "either event would misuse the data. "
        "The appendix must include the translated survey dates, the two transportation disruptions, "
        "and a table showing six with the highest participation."
    )


def test_final_proofread_repairs_locked_technical_run_ons_without_fact_loss():
    source = (
        "On January 14 the service failed. The team did not publish invoices but one export ran. "
        "By 03:05 UTC queue INV-PRIMARY contained extra messages. "
        "The file remains in vault Q-17 it cannot be emailed renamed or deleted. "
        "The behavior appeared when the cache key customer_id was blank and feature flag "
        "SB_DEDUP_V2 was enabled. Sofia is disabled, SB_DEDUP_V2 at 03:24 UTC. "
        "Ravi paused producers first at 03:22 UTC, so that no new event could enter. "
        "The replay began at 04:10 UTC, and finished at 04:46 UTC, with zero duplicates. "
        "Runbook RB-SYNC-64 says disable the flag, restart two workers and compare counts. "
        "The queue reached zero but dashboard HP-METRICS-9 stayed stale. "
        "The cache failed. While waiting for the refresh. Analyst Jonah Price checked the queue. "
        "Analyst Jonah Price used a read only replica. "
        "The query confirmed 2,106 customers was affected. An engineer wrote that data was corrupt "
        "this vocabulary is inaccurate. Finance found no amount differences, customer names, or tax codes. "
        "Another engineer wrote that data was corrupt, this vocabulary is inaccurate. "
        "A third engineer wrote that data was corrupt; this vocabulary is inaccurate. "
        "Follow up actions remain open. Owners remain in the table and no customer data may appear."
    )

    assert _apply_fast_proofread_rules(source) == (
        "On January 14, the service failed. The team did not publish invoices, but one export ran. "
        "By 03:05 UTC, queue INV-PRIMARY contained extra messages. "
        "The file remains in vault Q-17; it cannot be emailed, renamed, or deleted. "
        "The behavior appeared when the cache key customer_id was blank and the feature flag "
        "SB_DEDUP_V2 was enabled. Sofia disabled SB_DEDUP_V2 at 03:24 UTC. "
        "Ravi paused producers first at 03:22 UTC so that no new event could enter. "
        "The replay began at 04:10 UTC and finished at 04:46 UTC with zero duplicates. "
        "Runbook RB-SYNC-64 says to disable the flag, restart two workers, and compare counts. "
        "The queue reached zero, but the dashboard HP-METRICS-9 stayed stale. "
        "The cache failed. While waiting for the refresh, Analyst Jonah Price checked the queue. "
        "Analyst Jonah Price used a read-only replica. "
        "The query confirmed 2,106 customers were affected. An engineer wrote that data was corrupt; "
        "this wording is inaccurate. Finance found no differences in amounts, customer names, or tax codes. "
        "Another engineer wrote that data was corrupt; this wording is inaccurate. "
        "A third engineer wrote that data was corrupt; this wording is inaccurate. "
        "Follow-up actions remain open. Owners remain in the table, and no customer data may appear."
    )


def test_final_proofread_repairs_locked_library_trial_residuals():
    source = (
        "The library will run a twelve week trial, because students and shift workers currently "
        "has no quiet space. The proposal do not promise permanent hours. The proposal is not asking "
        "for permanent hours. Keisha Ward received "
        "684 responses between January 5 and February 2, 438 respondents supported later hours. "
        "The proposal also states the program will be completely free, which is incorrect vocabulary "
        "since the library expects operating costs of $18,600. Visitors will not pay a fee but the "
        "city still pays for staff security utilities and transportation support. The measures reduce "
        "known risks. While making clear that emergency procedures remain unchanged. The $18,600 "
        "estimate includes staffing, security, utilities and transit. The $18,600 estimate includes "
        "$11,200 for staffing, $3,400 for security, $2,000 for utilities and $2,000 for transit "
        "coordination. The Transit board votes Tuesday. The evidence is more strong than expected. "
        "This data is useful, but a survey shows interest, not guaranteed attendance. "
        "This data is useful, but it should have been explained with more caution because a survey "
        "shows interest but does not guarantee attendance. Branch manager Keisha Ward received 684 "
        "survey responses between January 5 and February 2. 438 respondents supported later hours. "
        "The measures reduce risk, while making clear that procedures remain unchanged. "
        "Council retains the final decision and approval remains temporary."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The library will run a twelve-week trial because students and shift workers currently "
        "have no quiet space. The proposal does not promise permanent hours. The proposal does not "
        "request permanent hours. Keisha Ward received "
        "684 responses between January 5 and February 2; 438 respondents supported later hours. "
        "The proposal describes the program as free to visitors, but the library expects operating "
        "costs of $18,600. Visitors will not pay a fee, but the city still pays for staff, security, "
        "utilities, and transportation support. The measures reduce known risks while making clear "
        "that emergency procedures remain unchanged. The $18,600 estimate includes staffing, security, "
        "utilities, and transit. The $18,600 estimate includes $11,200 for staffing, $3,400 for security, "
        "$2,000 for utilities, and $2,000 for transit coordination. The transit board votes Tuesday. "
        "The evidence is stronger than expected. This data is useful, but a survey shows interest "
        "and does not guarantee attendance. This data is useful, but it should be explained more "
        "cautiously. A survey shows interest but does not guarantee attendance. Branch manager Keisha "
        "Ward received 684 survey responses between January 5 and February 2. Of those respondents, "
        "438 supported later hours. The measures reduce risk while making clear that "
        "procedures remain unchanged. Council retains the final decision, and approval remains temporary."
    )


def test_final_proofread_repairs_library_page_commas():
    assert _apply_fast_proofread_rules(
        "Staff members currently walk between rooms with no consistent route, they often "
        "handle the same box twice. During Monday's planning meeting Daniel explained it."
    ) == (
        "Staff members currently walk between rooms with no consistent route, and they often "
        "handle the same box twice. During Monday's planning meeting, Daniel explained it."
    )


def test_fact_guard_accepts_formal_equivalent_for_written_feedback_pages():
    source = (
        "The team received a report from three users who wrote several pages. "
        "Each person liked the small icon, but the correction card was too large."
    )
    candidate = (
        "The team received a report from three users who provided several pages of feedback. "
        "Each person liked the small icon, but the correction card was too large. "
        "One tester accepted a spelling correction."
    )
    source += " One tester accepted a spelling fix."
    assert _linked_source_facts_preserved(source, candidate)


def test_fact_guard_accepts_caveman_split_without_losing_two_failures():
    source = (
        "The third tester requested a formal tone. The local AI returned a shorter draft "
        "that removed two details and changed names that needed to remain exact."
    )
    candidate = (
        "The third tester requested a formal tone. The local AI returned a shorter draft. "
        "It removed two details. It changed names that needed to remain exact."
    )
    assert _linked_source_facts_preserved(source, candidate)




def test_visible_tone_prompt_includes_shared_strict_contract():
    rules = _task_rules("Friendly", "", None, hard_word_limit=False)
    assert "tone contract:" in rules.casefold()
    assert "warm" in rules.casefold()

def test_quality_gate_rejects_simplified_transition_punctuation():
    result = CorrectionQualityGate().evaluate(
        "However MedTrack 4.7 failed. However, The transit board must approve it.",
        command="Simplified",
    )

    assert not result.complete
    assert "missing clause comma" in result.residual_errors
    assert "capitalized article after transition" in result.residual_errors

def test_quality_gate_rejects_high_school_live_residuals():
    result = CorrectionQualityGate().evaluate(
        "There also have several punctuation issues. "
        "Keisha agreed; However, the transit board must approve it.",
        command="High-school level",
    )

    assert not result.complete
    assert "malformed existential phrase" in result.residual_errors
    assert "capitalized transition after semicolon" in result.residual_errors

@pytest.mark.parametrize(
    "text",
    (
        "Marcus must not overwrite accessed the earlier backup.",
        "It must not be presented these findings as evidence.",
        "There also present several structural issues.",
        "That conclusion does not follow was drawn from the evidence.",
        "Others concatenate six actions with commas and no establish a clear sequence.",
        "This data is useful, but it should be cautioned more cautiously.",
    ),
)
def test_quality_gate_rejects_college_live_residual(text):
    result = CorrectionQualityGate().evaluate(text, command="College level")

    assert not result.complete
    assert "malformed college phrase" in result.residual_errors

@pytest.mark.parametrize(
    "text",
    (
        "Amina requested Owen to verify the signature.",
        "Marcus must not overwrite referenced the earlier backup.",
        "It must not be presented this as evidence.",
        "The recommendation should explain will explain why the trial is useful.",
    ),
)
def test_quality_gate_rejects_diplomatic_live_residual(text):
    result = CorrectionQualityGate().evaluate(text, command="Diplomatic")

    assert not result.complete
    assert "malformed diplomatic phrase" in result.residual_errors

def test_quality_gate_rejects_empathetic_live_residual():
    result = CorrectionQualityGate().evaluate(
        "This data should be explained it more cautiously.",
        command="Empathetic",
    )

    assert not result.complete
    assert "malformed empathetic phrase" in result.residual_errors


def test_detailed_live_output_repairs_and_gate_rejects_regressions():
    source = (
        "The issue blocked two emergency repairs which was a serious mistake. "
        "The group counted attendance at public meetings They recorded 4,860 hours. "
        "No An employee may work beyond their contract limits."
    )

    quality = CorrectionQualityGate().evaluate(source, command="Detailed")
    assert not quality.complete
    assert "missing clause comma" in quality.residual_errors
    assert "fused sentence" in quality.residual_errors
    assert "malformed determiner" in quality.residual_errors
    assert _apply_fast_proofread_rules(source) == (
        "The issue blocked two emergency repairs, which was a serious mistake. "
        "The group counted public meeting attendance. They recorded 4,860 hours. "
        "No employee may work beyond their contract limits."
    )

def test_persuasive_cleanup_restructures_factual_action_without_new_facts():
    source = (
        "The researchers corrected that problem on April 22 by adding translated "
        "forms and hiring Linh Tran and Mateo Silva as paid outreach coordinators."
    )

    result = _final_cleanup_candidate("Persuasive", source)

    assert result == (
        "By adding translated forms and hiring Linh Tran and Mateo Silva as paid "
        "outreach coordinators, the researchers corrected that problem on April 22."
    )
    assert _persuasive_rewrite_is_visible(source, result)

def test_final_proofread_repairs_missing_percentage_clause_comma():
    source = "They recorded 7,240 pounds of produce of which 61 percent was donated."

    quality = CorrectionQualityGate().evaluate(source, command="Persuasive")
    assert not quality.complete
    assert "missing clause comma" in quality.residual_errors
    assert _apply_fast_proofread_rules(source) == (
        "They recorded 7,240 pounds of produce, of which 61 percent was donated."
    )

def test_final_proofread_repairs_malformed_should_have_has_sequence():
    source = (
        "This data is useful, but it should have has been explained with more "
        "caution because a survey shows interest but does not guarantee attendance."
    )

    quality = CorrectionQualityGate().evaluate(source, command="Persuasive")
    assert not quality.complete
    assert "malformed verb sequence" in quality.residual_errors
    assert _apply_fast_proofread_rules(source) == (
        "This data is useful, but it should be explained more cautiously. "
        "A survey shows interest but does not guarantee attendance."
    )

def test_final_proofread_repairs_malformed_persuasive_recommendation():
    source = (
        "The final recommendation should be persuasive but honest, as it should "
        "explain explains why the limited trial is worth testing, without pretending "
        "the evidence is stronger than it is."
    )

    quality = CorrectionQualityGate().evaluate(source, command="Persuasive")
    assert not quality.complete
    assert "malformed verb sequence" in quality.residual_errors
    assert _apply_fast_proofread_rules(source) == (
        "The final recommendation should be persuasive but honest; it should explain "
        "why the limited trial is worth testing without pretending the evidence is "
        "stronger than it is."
    )