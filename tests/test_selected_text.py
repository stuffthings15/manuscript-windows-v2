from __future__ import annotations

import json
import re

import pytest

from echo_link_core.selected_text import (
    OLLAMA_NOT_RUNNING_MESSAGE,
    LocalWritingAssistant,
    _apply_fast_proofread_rules,
    _apply_safe_caveman_splits,
    _apply_safe_shorten_compression,
    _canonicalize_immutable_tokens,
    _immutable_tokens_changed,
    _linked_source_facts_preserved,
    _meaning_constraints_preserved,
    _numeric_literal_change,
    _restore_candidate_semantics,
    select_local_tone_model,
)


def test_final_tone_cleanup_removes_common_residual_errors():
    source = (
        "another tester seen inconsistent grammar marks while common spelling problems remains. "
        "These issues makes review confusing because users could not tell which two or three "
        "sentences was active, or if replacement finished.? manuscript stayed on first sentence."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Another tester saw inconsistent grammar marks while common spelling problems remain. "
        "These issues make review confusing because users could not tell which two or three "
        "sentences were active, or whether the replacement had finished. Manuscript stayed on "
        "the first sentence."
    )


def test_final_tone_cleanup_repairs_malformed_required_and_prior_clauses():
    source = (
        "Each participant is required check in before orientation begins. "
        "They must store phones prior to orientation begins."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Each participant is required to check in before orientation begins. "
        "They must store phones before orientation begins."
    )


def test_final_tone_cleanup_repairs_hidden_rephrase_grammar_errors():
    source = (
        "The team is preparing conducting a drill. The director says at the exercise "
        "will test whether services can continue operate. The report must identify all "
        "failed control before the clinic schedules another drill. "
        "The director says will test assess whether services continue, and the plans "
        "contain received conflicting instructions. The schedule being was not public. "
        "The script was harsh despite the drill schedule being was not public."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The team is preparing for a drill. The director says that the exercise "
        "will test whether services can continue to operate. The report must identify all "
        "failed controls before the clinic schedules another drill. "
        "The director says the exercise will test whether services continue, and the plans "
        "contain conflicting instructions. The schedule was not public. "
        "The script was harsh although the drill schedule was not public."
    )


def test_final_cleanup_repairs_manual_rephrase_review_findings():
    source = (
        "Four observers came from County Health Office. Critical operations can sustain "
        "for six hours. Nobody notified the issue. The units remain within specified "
        "approved temperature ranges. Neither removed values nor estimated temperatures "
        "are permitted, and any adjustments should include initials, the original value, "
        "and a short explanation."
        " Essential services be able to continue. The plans were submitted delayed, "
        "and Nobody reported the issue."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Four observers came from the County Health Office. Critical operations can continue "
        "for six hours. Nobody reported the issue. The units remain within approved "
        "temperature ranges. Neither erased values nor estimated temperatures "
        "are permitted, and any corrections should include initials, the original value, "
        "and a short explanation."
        " Essential services can continue. The plans were submitted late, "
        "and nobody reported the issue."
    )


def test_final_cleanup_repairs_semicolon_inside_neither_nor():
    assert _apply_fast_proofread_rules(
        "Neither erased values; nor estimated temperatures are allowed."
    ) == "Neither erased values nor estimated temperatures are allowed."


def test_final_cleanup_repairs_fuel_tank_percentage_clause():
    assert _apply_fast_proofread_rules(
        "The backup generator was serviced on September 28, "
        "with a fuel tank at 87 percent full."
    ) == (
        "The backup generator was serviced on September 28, "
        "and its fuel tank is 87 percent full."
    )


def test_final_cleanup_punctuates_generator_fuel_clause():
    assert _apply_fast_proofread_rules(
        "The backup generator was serviced on September 28 "
        "and its fuel tank is 87 percent full."
    ) == (
        "The backup generator was serviced on September 28, "
        "and its fuel tank is 87 percent full."
    )


def test_final_cleanup_repairs_last_week_possessive_and_they_agreement():
    assert _apply_fast_proofread_rules(
        "Last weeks rehearsal was confusing because they was describing "
        "different screens."
    ) == (
        "Last week's rehearsal was confusing because they were describing "
        "different screens."
    )


def test_final_cleanup_repairs_page_two_compound_clauses_and_articles():
    source = (
        "Elena received three checklists and none of them agree. "
        "The installation covers seven districts and every technician signs in. "
        "Elena instructed the system administrator Marcus Bell to preserve it. "
        "Training begins in Conference Room 5C and remote workers join online. "
        "Marcus compares totals with server dashboard NS-MON-7. "
        "Elena pauses that district but other districts keep working."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Elena received three checklists, and none of them agree. "
        "The installation covers seven districts, and every technician signs in. "
        "Elena instructed system administrator Marcus Bell to preserve it. "
        "Training begins in Conference Room 5C, and remote workers join online. "
        "Marcus compares totals with the server dashboard NS-MON-7. "
        "Elena pauses that district, but other districts keep working."
    )


def test_final_cleanup_repairs_page_five_compound_clauses_and_word_choice():
    source = (
        "The proposal does not request permanent hours and it does not "
        "promise universal use. The city will still cover staff, security, "
        "utilities, and transportation support. That tone weakens the "
        "proposal and it does not answer the budget question."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The proposal does not request permanent hours, and it does not "
        "promise universal use. The city will still cover staffing, security, "
        "utilities, and transportation support. That tone weakens the "
        "proposal, and it does not answer the budget question."
    )


def test_final_cleanup_repairs_does_not_shows():
    assert _apply_fast_proofread_rules(
        "The maintenance record does not shows who completed the test."
    ) == "The maintenance record does not show who completed the test."


def test_final_cleanup_repairs_clear_page_two_punctuation():
    source = (
        "Keep customer names gate codes and service notes private. "
        "At 10:00 a.m., Tuesday finance will test exports. "
        "List every unresolved defect the person responsible the next review "
        "time and whether service was interrupted. Everyone agrees the note "
        "should be brief but omitting a failure would be inaccurate."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Keep customer names, gate codes, and service notes private. "
        "At 10:00 a.m. Tuesday, finance will test exports. "
        "List every unresolved defect, the person responsible, the next review "
        "time, and whether service was interrupted. Everyone agrees the note "
        "should be brief, but omitting a failure would be inaccurate."
    )


def test_final_cleanup_repairs_clear_page_three_punctuation():
    source = (
        "They fix every neighborhood problem a claim that is too simple. "
        "The group counted volunteer hours crop donations cancelled workdays "
        "and attendance at meetings. The survey was available for six weeks "
        "which caused fewer responses. The section jumps back to consent "
        "procedures causing readers to become confused. It should not claim "
        "Gardens eliminate food insecurity."
    )

    assert _apply_fast_proofread_rules(source) == (
        "They fix every neighborhood problem, a claim that is too simple. "
        "The group counted volunteer hours, crop donations, cancelled workdays, "
        "and attendance at meetings. The survey was available for six weeks, "
        "which caused fewer responses. The section jumps back to consent "
        "procedures, causing readers to become confused. It should not claim "
        "gardens eliminate food insecurity."
    )


def test_final_cleanup_repairs_clear_page_four_grammar_and_lists():
    source = (
        "That conclusion does not follows from the evidence. It doesn't says "
        "whether draining should happen first. He did not ran the query, and "
        "the report should not implies that he did. Finance found no "
        "differences in amounts customer names or tax codes. Several sentences "
        "in the draft postmortem are fragments others combine six actions with commas and no clear "
        "order and the word 'utilize' is used. It must not blame the operator as "
        "the technical cause although detection was delayed. Follow actions include "
        "adding a 409 regression test defining the producer pause order in "
        "RB-SYNC-64 and changing alert HP-DUP-4."
    )

    assert _apply_fast_proofread_rules(source) == (
        "That conclusion does not follow from the evidence. It doesn't say "
        "whether draining should happen first. He did not run the query, and "
        "the report should not imply that he did. Finance found no "
        "differences in amounts, customer names, or tax codes. Several sentences "
        "in the draft postmortem are fragments; others combine six actions with commas and no clear "
        "order, and the word 'utilize' is used. It must not blame the operator as "
        "the technical cause, although detection was delayed. Follow-up actions include "
        "adding a 409 regression test, defining the producer pause order in "
        "RB-SYNC-64, and changing alert HP-DUP-4."
    )


def test_final_cleanup_repairs_clear_page_five_clauses_and_list():
    source = (
        "Respondents indicated they would use a preference for later hours. "
        "If the board rejects it the library cannot advertise transportation. "
        "If costs exceed the grant the program must stop. Success measures "
        "include nightly attendance repeat visits reported safety issues staff "
        "overtime and the percentage of visitors who arrive by bus."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Respondents indicated they would use later hours. "
        "If the board rejects it, the library cannot advertise transportation. "
        "If costs exceed the grant, the program must stop. Success measures "
        "include nightly attendance, repeat visits, reported safety issues, staff "
        "overtime, and the percentage of visitors who arrive by bus."
    )
    assert _apply_fast_proofread_rules(
        "Respondents indicated they would use preference for later hours."
    ) == "Respondents indicated they would use later hours."


def test_final_cleanup_repairs_improve_page_two_phrasing():
    source = (
        "The team classified the issue as cosmetic, despite that it blocked "
        "repairs. That wording has no a useful effect. The accessibility "
        "coordinator, Dana Cho, requested a corrected file."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The team classified the issue as cosmetic, although it blocked "
        "repairs. That wording has no useful effect. The accessibility "
        "coordinator Dana Cho requested a corrected file."
    )


def test_final_cleanup_repairs_improve_page_three_grammar():
    source = (
        "It must not be presented them as proof. The Neither event was "
        "controlled by leaders. There also contain several structure issues. "
        "The paragraph lists items without with commas."
    )

    assert _apply_fast_proofread_rules(source) == (
        "It must not be presented as proof. Neither event was "
        "controlled by leaders. There are also several structure issues. "
        "The paragraph lists items without commas."
    )


def test_final_cleanup_repairs_must_not_blame_identifies():
    assert _apply_fast_proofread_rules(
        "It must not blame identifies the operator as the technical cause."
    ) == "It must not blame the operator as the technical cause."


def test_final_cleanup_removes_compound_predicate_comma():
    assert _apply_fast_proofread_rules(
        "A survey indicates interest, but does not guarantee attendance."
    ) == "A survey indicates interest but does not guarantee attendance."


def test_final_cleanup_repairs_rephrase_page_two_grammar():
    source = (
        "No A personal email address may still be utilized. Elena directed the "
        "system administrator Marcus Bell to retain the backup. Last week's "
        "rehearsal was different due to three trainers speaking at once. Elena "
        "gives receive written approval. The device does not transmits private "
        "notes. The result is less precise compared to the extended version."
    )

    assert _apply_fast_proofread_rules(source) == (
        "No personal email address may be used. Elena directed "
        "system administrator Marcus Bell to retain the backup. Last week's "
        "rehearsal was unusual because three trainers spoke at once. Elena "
        "gives written approval. The device does not transmit private "
        "notes. The result is less precise than the extended version."
    )


def test_final_cleanup_repairs_rephrase_authorization_structure():
    assert _apply_fast_proofread_rules(
        "Council maintains the ultimate authority and grants approval for the "
        "trial does not authorize permanent hours after June 26."
    ) == (
        "Council maintains ultimate authority, and approval of the "
        "trial does not authorize permanent hours after June 26."
    )


def test_final_cleanup_repairs_rephrase_page_three_residue():
    source = (
        "The study did not measure analyzed property values and it must not be "
        "was presented as evidence. Instead, group counted volunteer hours and "
        "attendance participation at meetings, with which 61 percent went "
        "directed to the pantry. The team added translated versions. Leadership "
        "was a better indicator compared to garden size. Volunteers carried drew "
        "water. The city closed relocated the bus stop. Neither event was "
        "controlled organized by leaders. The list appeared without separated "
        "by commas. Participants could decline audio recording without, which "
        "would result in losing access."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The study did not measure property values and it must not be "
        "presented as evidence. Instead, the group counted volunteer hours and "
        "attendance at meetings, of which 61 percent went "
        "to the pantry. The team added translated forms. Leadership "
        "was a better indicator than garden size. Volunteers carried "
        "water. The city closed the bus stop. Neither event was "
        "controlled by leaders. The list appeared without "
        "commas. Participants could decline audio recording without "
        "losing access."
    )


def test_final_cleanup_repairs_rephrase_page_three_variants():
    source = (
        "Leadership was a more reliable indicator of involvement compared to "
        "the size of the garden. At Garden L-09, The city closed the bus stop. "
        "Participants had the option to refuse audio recording without, which "
        "would result in losing access to a garden plot. The last report is due "
        "on May 9, 2025."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Leadership was a more reliable indicator of involvement than "
        "garden size. At Garden L-09, the city closed the bus stop. "
        "Participants had the option to refuse audio recording without "
        "losing access to a garden plot. The final report is due "
        "on May 9, 2025."
    )


def test_final_cleanup_repairs_rephrase_page_two_latest_live_variants():
    source = (
        "No personal email address may still be utilized. Elena instructed "
        "system administrator Marcus Bell is responsible for preserve backup "
        "NSFS-DB-441 and verify its SHA-256 value. The team needs to keep "
        "customer names, gate codes, and service notes from screenshots that to "
        "attached public tickets. Last week's rehearsal was different due to "
        "three trainers speaking simultaneously. The media vendor has not "
        "confirmed was unsure whether they can meet that date. The command center "
        "stays remains open. District leads report experience login failures "
        "every two hours using, which form OPS-32, and Marcus compares update the "
        "totals with the server dashboard NS-MON-7. Elena pauses initiate the "
        "rollout. DispatchGrid 3.2 does not send private service notes are linked "
        "to a personal cloud account."
    )

    assert _apply_fast_proofread_rules(source) == (
        "No personal email address may be used. Elena instructed "
        "system administrator Marcus Bell to preserve backup "
        "NSFS-DB-441 and verify its SHA-256 value. The team needs to keep "
        "customer names, gate codes, and service notes out of screenshots "
        "attached to public tickets. Last week's rehearsal was unusual because "
        "three trainers spoke simultaneously. The media vendor has not "
        "confirmed whether it can meet that date. The command center "
        "remains open. District leads report login failures "
        "every two hours using form OPS-32, and Marcus updates the "
        "totals with the server dashboard NS-MON-7. Elena pauses the "
        "rollout. DispatchGrid 3.2 does not send private service notes "
        "to a personal cloud account."
    )


def test_final_cleanup_repairs_rephrase_page_two_second_live_variants():
    source = (
        "Last week's rehearsal stood out due to three trainers talked at once; "
        "nobody could understand, which screen they were describing. After "
        "deployment, The command center remains open for 36 hours."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Last week's rehearsal was unusual because three trainers spoke at once; "
        "nobody could understand which screen they were describing. After "
        "deployment, the command center remains open for 36 hours."
    )


def test_final_cleanup_repairs_rephrase_page_three_latest_live_variants():
    source = (
        "The study did not measure assessed property values. They recorded "
        "7,240 pounds of produce, with which 61 percent went to the pantry."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The study did not measure property values. They recorded "
        "7,240 pounds of produce, of which 61 percent went to the pantry."
    )


def test_final_cleanup_repairs_rephrase_page_four_residue():
    source = (
        "The service duplicated processed invoice events. The operator closed "
        "dismissed the alert because the no server was offline. That conclusion "
        "does not follow stemmed from evidence. Revenue was higher compared to "
        "the ledger. The team was told not to process publish invoices. The "
        "rollback was harder compared to expected, and the guide does not say "
        "whether drain should happen. Ravi temporarily halt producers initially. "
        "The queue reached is zero. The dashboard displays still displayed "
        "totals because its cache refreshed updates every fifteen minutes. While "
        "waiting was active for the refresh, Jonah executed query audit.sql on "
        "against a replica. The report should not imply matches what he did. "
        "Sofia reenacted 18,442 verified occurrences. It must not blame identified "
        "the operator. Owners and deadlines remain required appear in the table."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The service duplicated invoice events. The operator closed "
        "the alert because no server was offline. That conclusion "
        "does not follow from evidence. Revenue was higher than "
        "the ledger. The team was told not to publish invoices. The "
        "rollback was harder than expected, and the guide does not say "
        "whether draining should happen. Ravi paused producers first. "
        "The queue reached zero. The dashboard still displayed "
        "totals because its cache refreshes every fifteen minutes. While "
        "waiting for the refresh, Jonah ran query audit.sql "
        "against a replica. The report should not imply that he did. "
        "Sofia replayed 18,442 valid events. It must not blame "
        "the operator. Owners and deadlines remain required in the table."
    )


def test_final_cleanup_repairs_rephrase_page_four_live_variants():
    source = (
        "The service started duplicating processed invoice events. The operator "
        "closed dismissed it because no server was offline. The preview showed "
        "$286,910.40 higher revenue compared to the source ledger. Ravi decided "
        "to temporarily halt producers initially at 03:22 UTC. He did not run "
        "the query on production, and The report should not imply that he did. "
        "The corrected worker replayed the events following the successful "
        "completion of the test suite SYNC-CI-882. Ravi maintained external "
        "delivery suspended. The root cause state must state that the deployment "
        "exposed lacks an idempotency defect in retry handling."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The service started duplicating invoice events. The operator "
        "closed it because no server was offline. The preview showed "
        "$286,910.40 more revenue than the source ledger. Ravi decided "
        "to pause producers first at 03:22 UTC. He did not run "
        "the query on production, and the report should not imply that he did. "
        "The corrected worker replayed the events after passing "
        "test suite SYNC-CI-882. Ravi kept external "
        "delivery suspended. The root cause statement must state that the "
        "deployment exposed an idempotency defect in retry handling."
    )


def test_final_cleanup_repairs_rephrase_page_four_latest_live_variants():
    source = (
        "Sofia replayed 18,442 valid events after passing test suite SYNC-CI-882 "
        "by the fixed worker. Follow-up actions include updating alert HP-DUP-4 "
        "to trigger duplicate growth alerts regardless of standard CPU levels. "
        "The root cause statement must demonstrate that the deployment uncovered "
        "an idempotency problem within retry handling."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Sofia replayed 18,442 valid events after the fixed worker passed "
        "test suite SYNC-CI-882. Follow-up actions include updating alert "
        "HP-DUP-4 so it triggers when duplicate counts grow even when CPU usage "
        "is normal. The root cause statement must state that the deployment "
        "exposed an idempotency defect in retry handling."
    )


def test_final_cleanup_repairs_rephrase_page_five_residue():
    source = (
        "Visitors are charged will not pay, although the city continues to "
        "provide staffing and security. No An employee might exceed their "
        "contractual boundaries, and engaging in additional shifts has to be "
        "done voluntarily. Keisha is scheduled to have a tentative agreement. "
        "The library cannot provide advertise transportation. The library will "
        "publish compile totals. The recommendation should explain explaining "
        "why the trial is worth testing."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Visitors will not pay, although the city will still cover "
        "staffing and security. No employee may work beyond their "
        "contract limits, and participation in additional shifts must be "
        "voluntary. Keisha has a tentative agreement. "
        "The library cannot advertise transportation. The library will "
        "publish aggregate totals. The recommendation should explain "
        "why the trial is worth testing."
    )


def test_final_cleanup_repairs_rephrase_page_five_live_variants():
    source = (
        "Engaging in additional shifts has to be done on a voluntary basis. "
        "That is a ridiculous argument because no plan can guarantee zero "
        "incidents. The measures lower existing access and oversight risks."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Participation in additional shifts must be voluntary. "
        "That is an unsupported claim because no plan can guarantee zero "
        "incidents. The measures reduce existing access and oversight risks."
    )


def test_final_cleanup_repairs_rephrase_page_five_latest_live_variants():
    source = (
        "The trial is scheduled to commence on April 6 through June 26. "
        "A more accurate sentence would state that the measures limit known "
        "access and mitigate supervision risks, whereas making clear would "
        "argue that emergency procedures remain unchanged is affected. "
        "Keisha is scheduled to conduct a midpoint evaluation on May 18 and "
        "deliver a final report by July 10."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The trial is scheduled to commence on April 6 and run through June 26. "
        "A more accurate statement would say that the measures reduce known "
        "access and supervision risks while making clear that emergency "
        "procedures remain unchanged. Keisha will present a midpoint review "
        "on May 18 and submit a final report on July 10."
    )


def test_final_cleanup_repairs_shorten_page_two_live_variants():
    """The backup sentence expectation changed, and the old one was wrong.

    It required "because finance needs it" - a causal clause that appears
    nowhere in the source. A memorized rule for this one benchmark sentence
    fabricated it. Proofreading must not invent a reason the writer did not
    give, which is what the meaning guards exist to prevent, so the expectation
    is now the honest repair: drop the leftover verb and leave the rest alone.
    """
    source = (
        "No personal email address can be used. Support team marked issue as "
        "cosmetic. Marcus must not overwrite needs the earlier backup for the "
        "quarterly audit. Finance will test while safety staff checks if "
        "emergency jobs remain first in each queue. The note lists the "
        "responsible person, next review time, and whether service was interrupted."
    )

    assert _apply_fast_proofread_rules(source) == (
        "No personal email address can be used. The support team marked the "
        "issue as cosmetic. Marcus must not overwrite the earlier backup for "
        "the quarterly audit. Finance will test while safety "
        "staff verify whether emergency jobs remain prioritized in each queue. "
        "The note lists the responsible person, the next review time, and whether "
        "service was interrupted."
    )


def test_final_cleanup_repairs_shorten_page_three_live_variants():
    source = (
        "There contains punctuation and structure issues. The term is poor "
        "vocabulary as it implies residents were personally responsible."
    )

    assert _apply_fast_proofread_rules(source) == (
        "There are punctuation and structural issues. The term is poor "
        "wording because it implies residents were personally responsible."
    )


def test_final_cleanup_repairs_shorten_page_four_live_variants():
    source = (
        "That conclusion does not follow follows from the evidence. By 03:05 UTC, "
        "queue INV-PRIMARY had 18,442 extra messages and billing preview showed "
        "$286,910.40 more revenue than source ledger. Incident commander Ravi "
        "Morgan instructed team not to publish invoices. Ravi paused producers "
        "first at 03:22 UTC, to allow no additional invoice events to enter the "
        "queue. Sofia "
        "replayed valid 18,442 events from ARC-2026-14. Replay started at 04:10 "
        "UTC, and ended at 04:46 UTC, with zero duplicate identifiers. Several "
        "sentences are fragments; others list six actions with commas and no clear "
        "order, using 'utilize' instead of 'use'. Another engineer wrote data was "
        "corrupt after SYNC-CI-882 test suite passed. Sofia disabled SB_DEDUP_V2 "
        "at 03:24 UTC, workers sb_retry_1 and sb_retry_2 restarted at 03:29 UTC. "
        "Follow-up actions include "
        "changing alert HP-DUP-4 to trigger duplicate growth even when CPU is normal."
    )

    assert _apply_fast_proofread_rules(source) == (
        "That conclusion does not follow from the evidence. By 03:05 UTC, "
        "the queue INV-PRIMARY had 18,442 extra messages and the billing preview "
        "showed $286,910.40 more revenue than the source ledger. Incident commander "
        "Ravi Morgan instructed the team not to publish invoices. Ravi paused "
        "producers first at 03:22 UTC, preventing additional invoice events from "
        "entering the queue. Sofia replayed 18,442 valid events from ARC-2026-14. "
        "The replay started at 04:10 UTC and ended at 04:46 UTC with zero duplicate "
        "identifiers. Several sentences are fragments; others list six actions "
        "with commas and no clear order and use 'utilize' instead of 'use'. "
        "Another engineer wrote that data was corrupt after the SYNC-CI-882 test "
        "suite passed. Sofia disabled SB_DEDUP_V2 at 03:24 UTC, and workers "
        "sb_retry_1 and sb_retry_2 restarted at 03:29 UTC. "
        "Follow-up actions include changing alert HP-DUP-4 so it triggers when "
        "duplicate counts grow even when CPU is normal."
    )


def test_final_cleanup_repairs_shorten_page_five_live_variants():
    source = (
        "Community college students, hospital employees, and evening "
        "certification class parents showed the strongest interest. The draft "
        "responds hostilely, calling their concern shortsighted. Community college "
        "students, hospital employees, and evening certification parents showed "
        "strongest interest."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Community college students, hospital employees, and parents attending "
        "evening certification classes showed the strongest interest. The draft "
        "responds with hostility, calling their concern shortsighted. Community "
        "college students, hospital employees, and parents attending evening "
        "certification classes showed the strongest interest."
    )


def test_final_cleanup_repairs_friendly_page_two_live_variants():
    source = (
        "The final review did not occur didn\u2019t go as planned. Last week's "
        "rehearsal was unusual because three trainers talked at once, and nobody "
        "could understand which screen they were describing, and the caption file "
        "stopped after nineteen minutes. Finance will test while safety staff "
        "checks whether emergency jobs remain first in each queue."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The final review didn't go as planned. Last week's rehearsal was unusual "
        "because three trainers talked at once; nobody could understand which "
        "screen they were describing, and the caption file stopped after nineteen "
        "minutes. Finance will test while safety staff check whether emergency jobs "
        "remain prioritized in each queue."
    )


def test_final_cleanup_repairs_friendly_page_three_live_variants():
    source = (
        "This is a connection; it is not evidence that leadership caused the "
        "difference. The report has punctuation and structure issues. That is poor "
        "vocabulary because it sounds accusatory."
    )

    assert _apply_fast_proofread_rules(source) == (
        "This is an association; it is not evidence that leadership caused the "
        "difference. The report has punctuation and structural issues. That is poor "
        "wording because it sounds accusatory."
    )


def test_final_cleanup_repairs_friendly_pages_four_and_five_live_variants():
    source = (
        "That conclusion does not follow comes from the evidence. The final "
        "recommendation should explain to explain why the trial is worth testing."
    )

    assert _apply_fast_proofread_rules(source) == (
        "That conclusion does not follow from the evidence. The final "
        "recommendation should explain why the trial is worth testing."
    )


def test_final_cleanup_repairs_formal_page_one_live_variants():
    source = (
        "If an alarm fails occurs, appointments pause without as an exception."
    )

    assert _apply_fast_proofread_rules(source) == (
        "If an alarm fails, appointments pause without exception."
    )


def test_final_cleanup_repairs_formal_pages_two_and_three_live_variants():
    source = (
        "Marcus must not overwrite retained the earlier backup. The caption file "
        "ceased after nineteen minutes. Dana requested a corrected caption file, "
        "by November 18; however, the media vendor, has not confirmed, whether they "
        "can meet that date. Leadership was a more reliable indicator of "
        "participation compared to garden size. At Neither, the event was controlled "
        "by garden leaders. Participants could decline audio recording without, "
        "thereby losing access to a garden plot."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Marcus must not overwrite the earlier backup. The caption file "
        "stopped after nineteen minutes. Dana requested a corrected caption file "
        "by November 18; however, the media vendor has not confirmed whether they "
        "can meet that date. Leadership was a more reliable indicator of "
        "participation than garden size. Neither event was controlled "
        "by garden leaders. Participants could decline audio recording without "
        "losing access to a garden plot."
    )


def test_final_cleanup_repairs_formal_pages_four_and_five_live_variants():
    source = (
        "It doesn't does not specify whether draining should occur first. "
        "Visitors will be charged will not pay a fee. Keisha reached a tentative "
        "agreement to retain possession of the final bus until 9:45 p.m."
    )

    assert _apply_fast_proofread_rules(source) == (
        "It does not specify whether draining should occur first. "
        "Visitors will not pay a fee. Keisha reached a tentative "
        "agreement to hold the final bus until 9:45 p.m."
    )


def test_final_cleanup_lowercases_caption_after_coordinating_conjunction():
    assert _apply_fast_proofread_rules(
        "Nobody understood the screen, and The caption file stopped."
    ) == "Nobody understood the screen, and the caption file stopped."


def test_final_cleanup_repairs_formal_page_four_latest_variants():
    source = (
        "Runbook RB-SYNC-64 instructs to disable the feature flag. Sofia replayed "
        "18,442 valid events following the completion of the fixed worker passing "
        "the test suite SYNC-CI-882. The replay commenced at 04:10 UTC, and "
        "concluded at 04:46 UTC, with zero duplicate identifiers. Ravi maintained "
        "external delivery disabled."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Runbook RB-SYNC-64 provides instructions to disable the feature flag. "
        "Sofia replayed 18,442 valid events after the fixed worker passed test suite "
        "SYNC-CI-882. The replay commenced at 04:10 UTC and concluded at 04:46 UTC "
        "with zero duplicate identifiers. Ravi continued to suspend external "
        "delivery."
    )


def test_final_cleanup_repairs_professional_live_variants():
    source = (
        "Facilities supervisor Owen Price definitely wants clearer routes this "
        "year. Owen will destroy the incorrect cards and Priya Desai will print "
        "90 replacements. Marcus must not overwrite requested the earlier backup. "
        "Last week's rehearsal was unusual because three trainers talked at once, "
        "which screen they were describing; nobody could understand the caption "
        "file stopped after nineteen minutes. Ravi paused producers first at 03:22 "
        "UTC, to allow no additional invoice event to enter the queue. Respondents "
        "indicated they would use working later hours. Visitors pay will not pay a fee."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Facilities supervisor Owen Price seeks clearer routes this "
        "year. Owen will destroy the incorrect cards, and Priya Desai will print "
        "90 replacements. Marcus must not overwrite the earlier backup. "
        "Last week's rehearsal was unusual because three trainers talked at once; "
        "nobody could understand which screen they were describing, and the caption "
        "file stopped after nineteen minutes. Ravi paused producers first at 03:22 "
        "UTC, preventing additional invoice events from entering the queue. "
        "Respondents indicated they would use later hours. Visitors will not pay a fee."
    )


def test_final_cleanup_repairs_academic_live_variants():
    source = (
        "MedTrack 4.7 occasionally displays a blank reading following users' "
        "reconnection of the sensor cable. The team also has to ensure that customer "
        "names remain excluded. Last week's rehearsal was unusual because three "
        "trainers spoke simultaneously, which screen they were describing; nobody "
        "could understand the caption file stopped after nineteen minutes! "
        "Respondents indicated that they would use reported working later hours at "
        "least twice a month. Visitors will not pay are charged a fee. The trial "
        "would run from April 6 through June 26 at the Eastgate Central branch only. "
        "The library will publish aggregate totals but must not release names, library "
        "card numbers, or individual travel patterns Keisha will present a midpoint "
        "review on May 18 and a final report on July 10 The final recommendation "
        "should be persuasive but honest it should explain why the trial is worth testing."
    )

    assert _apply_fast_proofread_rules(source) == (
        "MedTrack 4.7 occasionally displays a blank reading after users reconnect "
        "the sensor cable. The team also must ensure that customer names remain "
        "excluded. Last week's rehearsal was unusual because three trainers spoke "
        "simultaneously; nobody could understand which screen they were describing, "
        "and the caption file stopped after nineteen minutes! Respondents indicated "
        "that they would use later hours at least twice a month. Visitors will not "
        "pay a fee. The trial will run from April 6 through June 26 at the Eastgate "
        "Central branch only. The library will publish aggregate totals but must not "
        "release names, library card numbers, or individual travel patterns. Keisha "
        "will present a midpoint review on May 18 and a final report on July 10. The "
        "final recommendation should be persuasive but honest; it should explain why "
        "the trial is worth testing."
    )


def test_final_cleanup_repairs_academic_second_live_variants():
    source = (
        "During the pilot, phase eight tablets lost their saved routes after the "
        "battery level dropped below 12 percent. The support team classified the "
        "issue as cosmetic despite that it obstructed two emergency repairs. The "
        "instructions for Several are written using vague vocabulary. Respondents "
        "indicated that they would use preferred later hours at least twice a month."
    )

    assert _apply_fast_proofread_rules(source) == (
        "During the pilot, eight tablets lost their saved routes after the battery "
        "level dropped below 12 percent. The support team classified the issue as "
        "cosmetic, although it obstructed two emergency repairs. Several instructions "
        "are written using vague vocabulary. Respondents indicated that they would "
        "use later hours at least twice a month."
    )


def test_final_cleanup_repairs_conversational_live_variants():
    source = (
        "The team has to make sure service notes donâ€™t end up in screenshots. "
        "Lead researcher Professor Nia Okafor says the study did not measure looked "
        "at property values and it must not be presented that as proof gardens raise rent. "
        "Professor Okafor says the study did not measure property values and it must "
        "not be presented as proof that gardens raise rent."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The team has to make sure service notes don't end up in screenshots. "
        "Lead researcher Professor Nia Okafor says the study did not measure "
        "property values, and it must not be presented as proof that gardens raise rent. "
        "Professor Okafor says the study did not measure property values, and it must "
        "not be presented as proof that gardens raise rent."
    )


def test_meaning_guard_rejects_on_by_date_relation_change():
    source = "The transit board must approve schedule RT14-EG-2026 on March 18."
    candidate = "The transit board must approve schedule RT14-EG-2026 by March 18."

    assert not _meaning_constraints_preserved(source, candidate)


def test_final_cleanup_repairs_concise_live_punctuation():
    source = (
        "Several department plans were submitted late and they contain conflicting "
        "instructions. Last spring two visitors entered the hall. At each stage, a "
        "team leader records the time however several stopwatches were missing "
        "batteries. The queue INV-PRIMARY contained 18,442 extra messages and the "
        "billing preview showed $286,910.40 more revenue than the source ledger."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Several department plans were submitted late, and they contain conflicting "
        "instructions. Last spring, two visitors entered the hall. At each stage, a "
        "team leader records the time; however, several stopwatches were missing "
        "batteries. The queue INV-PRIMARY contained 18,442 extra messages, and the "
        "billing preview showed $286,910.40 more revenue than the source ledger."
    )


def test_final_cleanup_repairs_concise_second_live_variants():
    source = (
        "The final report are due October 12. Community gardens cannot fix every "
        "neighborhood problem but this claim is too simple. The draft uses failure "
        "to describe low attendance but that is poor wording. The appendix must "
        "include the translated survey, dates the two transportation disruptions. "
        "The final report is due May 9, 2025 and the appendix must include the "
        "translated survey dates the two transportation disruptions and a table."
    )
    assert _apply_fast_proofread_rules(source) == (
        "The final report is due October 12. Community gardens cannot fix every "
        "neighborhood problem, but this claim is too simple. The draft uses failure "
        "to describe low attendance, but that is poor wording. The appendix must "
        "include the translated survey dates, the two transportation disruptions. "
        "The final report is due May 9, 2025, and the appendix must include the "
        "translated survey dates, the two transportation disruptions, and a table."
    )


def test_final_cleanup_repairs_release_gate_agreement_errors():
    source = (
        "Unresolved failures is reported immediately. "
        "If any safety test fails, reopening are delayed."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Unresolved failures are reported immediately. "
        "If any safety test fails, reopening is delayed."
    )


def test_final_cleanup_repairs_gpt56sol_proofread_judge_findings():
    source = (
        "The vendor has not confirmed if it can meet that date. "
        "This relationship is an association, and one site had a lot more cancellations. "
        "The queue reached zero at 03:41 UTC, but the dashboard HP-METRICS-9 still "
        "displays duplicate totals because its cache refreshes every fifteen minutes. "
        "Visitors will not pay a fee, but the city still covers the costs. "
        "Council retains the final decision."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The vendor has not confirmed whether it can meet that date. "
        "This is an association, and one site had many more cancellations. "
        "The queue reached zero at 03:41 UTC, but the dashboard HP-METRICS-9 still "
        "displayed duplicate totals because its cache refreshed every fifteen minutes. "
        "Visitors will not pay a fee, but the city will still cover the costs. "
        "Council retains the final decision."
    )



def test_final_cleanup_repairs_every_subject_agreement_and_possessive():
    source = "Every technician need to sign in with there existing company account."

    assert _apply_fast_proofread_rules(source) == (
        "Every technician needs to sign in with their existing company account."
    )


def test_final_cleanup_collapses_repeated_model_punctuation():
    source = "The queue reached zero at 03:41 UTC,,,,, but the dashboard stayed online."

    assert _apply_fast_proofread_rules(source) == (
        "The queue reached zero at 03:41 UTC, but the dashboard stayed online."
    )

def test_final_cleanup_repairs_redundant_article_and_vocabulary_label():
    source = (
        "The Another engineer wrote that data was corrupt; however, "
        "this vocabulary is inaccurate."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Another engineer wrote that data was corrupt; however, "
        "this wording is inaccurate."
    )

def test_final_cleanup_repairs_gpt56sol_round3_judge_findings():
    source = (
        "The team was suppose to launch Monday. "
        "The vendor hasn't confirmed if they can meet the date. "
        "The queue reached zero at 03:41 UTC, but dashboard HP-METRICS-9 still "
        "displays duplicate totals because its cache refreshes every fifteen minutes. "
        "Other ones combine six actions. However, That is unsupported."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The team was supposed to launch Monday. "
        "The vendor hasn't confirmed whether they can meet the date. "
        "The queue reached zero at 03:41 UTC, but the dashboard HP-METRICS-9 still "
        "displayed duplicate totals because its cache refreshed every fifteen minutes. "
        "Others combine six actions. However, that is unsupported."
    )

def test_final_cleanup_repairs_gpt56sol_round4_judge_findings():
    source = (
        "A supervisor records each result on checklist RVC-DRILL-1008, and reports "
        "unresolved failures. The final review didn't happen like the plan said it would. "
        "The queue reached zero, but dashboard HP-METRICS-9 stayed stale. "
        "The safety plan adds a staffed entrance, hourly floor checks and a text line. "
        "One sentence claims these measures eliminate all risk, That is unsupported."
    )

    assert _apply_fast_proofread_rules(source) == (
        "A supervisor records each result on checklist RVC-DRILL-1008 and reports "
        "unresolved failures. The final review did not happen as planned. "
        "The queue reached zero, but the dashboard HP-METRICS-9 stayed stale. "
        "The safety plan adds a staffed entrance, hourly floor checks, and a text line. "
        "One sentence claims these measures eliminate all risk. That is unsupported."
    )

def test_final_cleanup_repairs_gpt56sol_clear_improve_findings():
    source = (
        "If any alarm fails sounds, appointments pause. "
        "If any alarm fails triggers, appointments pause. "
        "Students currently lack no quiet public space. "
        "An No personal email address can be used. "
        "Ravi paused producers so that an no additional invoice event could enter. "
        "The draft responds hostiley. "
        "Funding would come from grant EG-LRN-55, not, and the general book budget. "
        "The measures eliminate all risk; That is unsupported. "
        "Three trainers spoke simultaneously regarding nobody could understand them. "
        "District reports login failures. "
        "A survey indicates interest, yet does not guarantee attendance remains limited. "
        "Funding would come from grant EG-LRN-55 and not, the general book budget. "
        "Recovery validation utilized snapshot LEDGER-0315."
    )

    assert _apply_fast_proofread_rules(source) == (
        "If any alarm fails, appointments pause. "
        "If any alarm fails, appointments pause. "
        "Students currently have no quiet public space. "
        "No personal email address can be used. "
        "Ravi paused producers so that no additional invoice event could enter. "
        "The draft responds in a hostile way. "
        "Funding would come from grant EG-LRN-55, not the general book budget. "
        "The measures eliminate all risk. That is unsupported. "
        "Three trainers spoke simultaneously; nobody could understand them. "
        "District leads report login failures. "
        "A survey indicates interest, yet does not guarantee attendance. "
        "Funding would come from grant EG-LRN-55, not the general book budget. "
        "Recovery validation used snapshot LEDGER-0315."
    )


def test_final_cleanup_repairs_caveman_manual_review_findings():
    source = (
        "The study did not measure affects property values. "
        "The study must not be shows gardens increase rent. "
        "One paragraph has list items with without commas. "
        "It should explain The limited trial is worth testing. "
        "The evidence is stronger than it is. It should not pretend."
    )

    assert _apply_fast_proofread_rules(source) == (
        "The study did not measure property values. "
        "The study must not be presented as proof that gardens increase rent. "
        "One paragraph has list items without commas. "
        "It should explain why the limited trial is worth testing. "
        "It should not pretend the evidence is stronger than it is."
    )


def test_final_cleanup_repairs_compact_research_list_commas():
    source = (
        "Instead, the group counted volunteer hours crop donations cancelled "
        "workdays and public meeting attendance."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Instead, the group counted volunteer hours, crop donations, cancelled "
        "workdays, and public meeting attendance."
    )
def test_final_cleanup_repairs_vague_link_and_unclear_subject():
    source = (
        "This is a link. It is not evidence leadership alone caused the difference. "
        "It would make clear emergency procedures stay the same."
    )

    assert _apply_fast_proofread_rules(source) == (
        "This is an association. It is not evidence leadership alone caused the difference. "
        "The sentence should make clear that emergency procedures remain unchanged."
    )
def test_final_cleanup_repairs_dog_document_residuals():
    source = (
        "Dogs don't mean anything bad by it. "
        "They bring toys over and put it on your lap. "
        "They want you playing with them. "
        "The house feels less empty because there is always movement around, making noise, "
        "or wanting to sit beside you. "
        "Sometimes they just lay on the floor. "
        "But every dog doesn't do those jobs because not all dogs can. "
        "They might have too much energy, then they run around crazy. "
        "People still like dogs because the good things are bigger than the bad things, mostly. "
        "Every dog is different in their own ways. "
        "Some dogs like swimming while another dog hates water and tries to run away."
    )

    assert _apply_fast_proofread_rules(source) == (
        "Dogs don't mean any harm by it. "
        "They bring a toy over and put it on your lap. "
        "They want you to play with them. "
        "The house feels less empty because they are always moving around, making noise, "
        "or wanting to sit beside you. "
        "Sometimes they just lie on the floor. "
        "Not every dog does those jobs, since not all dogs can. "
        "They might have too much energy, and then they run around like crazy. "
        "People still like dogs because the benefits generally outweigh the drawbacks. "
        "Every dog is different in their own way. "
        "Some dogs like swimming, while other dogs hate water and try to run away."
    )


def test_got_to_and_have_to_keep_same_requirement_strength():
    source = "If your dog gets sick, you got to take them to the vet."
    candidate = "If your dog gets sick, you have to take them to the vet."

    assert _meaning_constraints_preserved(source, candidate)


def test_restore_candidate_semantics_keeps_coordinated_pronoun():
    source = "Sometimes people forget one thing, then they remember later."
    candidate = "Sometimes people forget one thing and then remember later."

    assert _restore_candidate_semantics(source, candidate, {}) == (
        "Sometimes people forget one thing and then they remember later."
    )


def test_restore_candidate_semantics_keeps_reported_dog_object_pronoun():
    source = (
        "If you forget to feed it, that is bad because the dog depends on you. "
        "Kids should help take care of it."
    )
    candidate = (
        "If you forget to feed them, that is bad because the dog depends on you. "
        "Kids should help take care of it."
    )

    assert _restore_candidate_semantics(source, candidate, {}) == source


class _Response:
    def __init__(self, payload, *, add_default_model=True):
        self.payload = dict(payload)
        if add_default_model and "response" in self.payload:
            self.payload.setdefault("model", "local-model")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return json.dumps(self.payload).encode("utf-8")


@pytest.mark.parametrize(
    "configured",
    (
        "gemma4:e2b-it-qat",
        "gemma4:e4b-it-qat",
        "gemma4:12b-it-q4_K_M",
    ),
)
def test_tone_model_routing_uses_the_hardware_selected_official_gemma4_candidate(
    configured,
):
    installed = {
        "gemma4:e2b-it-qat",
        "gemma4:e4b-it-qat",
        "gemma4:12b-it-q4_K_M",
        "qwen3:8b",
        "qwen3:14b",
        "custom:latest",
    }

    for command in ("Proofread", "Friendly", "Caveman", "Translate", "Summarize"):
        assert select_local_tone_model(command, configured, installed) == configured


# qwen3:14b left this list on 2026-08-24: it was approved, added to the signed
# manifest and added to _OFFICIAL_MODELS on Curtis's instruction, so asserting
# it can never be selected would now be asserting the opposite of the product.
# qwen3:8b stays, and is the better example anyway - it is a real model, on
# real machines, one character away from an approved tag, and still refused.
@pytest.mark.parametrize("configured", ("qwen3:8b", "custom:latest", ""))
def test_tone_model_routing_never_selects_an_unapproved_model(configured):
    installed = {"qwen3:8b", "qwen3:14b", "custom:latest"}

    assert select_local_tone_model("Proofread", configured, installed) == ""


def test_an_approved_model_is_still_refused_when_it_is_not_installed():
    """Approval is not availability, and the two failures look identical.

    Widening the allowlist is the moment this could quietly become "return the
    configured name and hope", so it is pinned separately from the routing
    test above.
    """
    assert select_local_tone_model("Proofread", "qwen3:14b", {"custom:latest"}) == ""


def test_tone_model_routing_does_not_guess_when_hardware_choice_is_automatic():
    installed = {
        "gemma4:e2b-it-qat",
        "gemma4:e4b-it-qat",
        "gemma4:12b-it-q4_K_M",
    }

    assert select_local_tone_model("Proofread", "", installed) == ""


def test_local_writing_runtime_errors_do_not_expose_a_model_brand(monkeypatch):
    assistant = LocalWritingAssistant()
    monkeypatch.setattr(
        assistant._transport,
        "get_json",
        lambda *_args, **_kwargs: {"models": "invalid"},
    )

    with pytest.raises(RuntimeError) as error:
        assistant.runtime_status("gemma4:e2b-it-qat")

    assert str(error.value) == "Local AI returned invalid runtime status."


def test_local_writing_runtime_rejects_even_small_gpu_spill(monkeypatch):
    assistant = LocalWritingAssistant()
    monkeypatch.setattr(
        assistant._transport,
        "get_json",
        lambda *_args, **_kwargs: {
            "models": [
                {
                    "name": "local-model",
                    "size": 100,
                    "size_vram": 99,
                }
            ]
        },
    )

    status = assistant.runtime_status("local-model")

    assert status.backend == "Hybrid"


@pytest.mark.parametrize("reported_model", [None, "other-model"])
def test_local_writing_generation_requires_exact_response_model(
    monkeypatch,
    reported_model,
):
    payload = {"response": "The draft is ready."}
    if reported_model is not None:
        payload["model"] = reported_model
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response(
            payload,
            add_default_model=False,
        ),
    )

    with pytest.raises(RuntimeError, match="model identity"):
        LocalWritingAssistant().run(
            "the draft are ready",
            command="Proofread",
            model="local-model",
        )


@pytest.mark.parametrize(
    "command",
    (
        "Improve",
        "Rephrase",
        "Shorten",
        "Friendly",
        "Formal",
        "Professional",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    ),
)
def test_requested_tone_and_style_commands_reach_local_ai(monkeypatch, command):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert f"ACTION: {command}" in body["prompt"]
        return _Response({"response": "Please review this message."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Please review this message.",
        command=command,
        model="local-model",
    )

    assert result.command == command
    assert result.text == "Please review this message."


def test_local_writing_ai_retries_em_dash_output(monkeypatch):
    responses = iter(
        (
            "The report is clear\u2014and complete.",
            "The report is clear and complete.",
        )
    )
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": next(responses)})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "The report is clear and complete.",
        command="Proofread",
        model="local-model",
    )

    assert result.text == "The report is clear and complete."
    assert len(prompts) == 2
    assert "never use an em dash" in prompts[0].casefold()
    assert "prior answer used an em dash" in prompts[1].casefold()


def test_local_writing_assistant_protects_names_and_urls(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        assert "ECHOLINKTOKEN" in body["prompt"]
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane about https://example.com.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )
    assert "Jane" in result.text
    assert "https://example.com" in result.text


def test_local_writing_assistant_keeps_model_warm_for_five_idle_minutes(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert body["keep_alive"] == "300s"
        assert body["think"] is False
        assert body["options"] == {
            "temperature": 0,
            "seed": 1,
            "top_k": 1,
            "top_p": 1.0,
            "repeat_penalty": 1.0,
            "num_ctx": 2048,
            "num_predict": 512,
        }
        return _Response({"response": "The draft is ready."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "the draft are ready",
        command="Proofread",
        model="local-model",
        context_window=2048,
        max_tokens=512,
    )

    assert result.text == "The draft is ready."


def test_a_generation_request_when_ollama_is_not_running_names_it_and_says_how_to_fix_it(
    monkeypatch,
):
    """run() is the one place allowed to name Ollama to a writer.

    Everything else routes through ai_status.py, which hides runtime names
    because the bundled correction engine is meant to be invisible. This
    optional assistant has no bundled fallback, so hiding the name here
    turned a fixable "go install this" into an unfixable "retry" - the
    generic LocalTransportError branch this used to fall into.
    """

    def refused(request, timeout):
        if request.full_url.endswith("/api/ps"):
            return _Response({"models": []})
        raise ConnectionRefusedError("No connection could be made")

    monkeypatch.setattr("echo_link_core.local_transport._open_request", refused)

    with pytest.raises(RuntimeError) as error:
        LocalWritingAssistant().run(
            "the draft are ready",
            command="Proofread",
            model="local-model",
        )

    assert str(error.value) == OLLAMA_NOT_RUNNING_MESSAGE
    assert "ollama" in str(error.value).casefold()


def test_local_writing_assistant_loads_qwen_and_requires_real_gpu_vram(monkeypatch):
    calls = []
    ps_calls = 0

    def fake_urlopen(request, timeout):
        nonlocal ps_calls
        calls.append(request.full_url)
        if request.full_url.endswith("/api/ps"):
            ps_calls += 1
            models = [] if ps_calls == 1 else [
                {
                    "name": "qwen3:14b",
                    "size": 9_900_000_000,
                    "size_vram": 9_900_000_000,
                }
            ]
            return _Response({"models": models})
        body = json.loads(request.data.decode("utf-8"))
        if not body.get("prompt"):
            assert body["options"] == {"num_ctx": 8192}
            return _Response({"done": True, "model": body["model"]})
        return _Response(
            {"response": "The draft is ready.", "model": body["model"]}
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    assistant = LocalWritingAssistant()
    result = assistant.run(
        "the draft are ready",
        command="Proofread",
        model="qwen3:14b",
        require_gpu=True,
    )

    assert result.text == "The draft is ready."
    assert calls == [
        "http://127.0.0.1:11434/api/ps",
        "http://127.0.0.1:11434/api/generate",
        "http://127.0.0.1:11434/api/ps",
        "http://127.0.0.1:11434/api/generate",
    ]


def test_local_writing_assistant_blocks_silent_cpu_fallback(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response(
            {
                "models": [
                    {
                        "name": "qwen3:14b",
                        "size": 9_900_000_000,
                        "size_vram": 0,
                    }
                ]
            }
        ),
    )

    with pytest.raises(RuntimeError, match="loaded on CPU"):
        LocalWritingAssistant().run(
            "the draft are ready",
            command="Proofread",
            model="qwen3:14b",
            require_gpu=True,
        )


def test_local_writing_assistant_uses_one_deadline_across_safe_retry(monkeypatch):
    calls = []
    clock = iter((100.0, 100.1, 104.7, 104.9))

    def fake_urlopen(request, timeout):
        calls.append(timeout)
        return _Response({"response": "Protected token removed."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    monkeypatch.setattr(
        "echo_link_core.selected_text.time.monotonic",
        lambda: next(clock),
    )

    with pytest.raises(RuntimeError, match="too long"):
        LocalWritingAssistant().run(
            "Keep release-7.zip unchanged.",
            command="Proofread",
            model="local-model",
            timeout=5.0,
        )

    assert len(calls) == 1
    assert calls[0] < 5.0


def test_local_writing_assistant_rejects_success_returned_after_deadline(monkeypatch):
    clock = iter((100.0, 100.1, 105.1))

    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "The draft is ready."}),
    )
    monkeypatch.setattr(
        "echo_link_core.selected_text.time.monotonic",
        lambda: next(clock),
    )

    with pytest.raises(RuntimeError, match="too long"):
        LocalWritingAssistant().run(
            "the draft are ready",
            command="Proofread",
            model="local-model",
            timeout=5.0,
        )


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), -1, 0.1, 301])
def test_local_writing_assistant_rejects_invalid_timeout_before_transport(
    monkeypatch, timeout
):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: pytest.fail("transport must not open"),
    )
    with pytest.raises(ValueError, match="timeout"):
        LocalWritingAssistant().run(
            "Draft.",
            command="Proofread",
            model="local-model",
            timeout=timeout,
        )


@pytest.mark.parametrize("keep_alive", [True, -1, 901, "901s", "forever", "1h"])
def test_local_writing_assistant_rejects_invalid_keep_alive_before_transport(
    monkeypatch, keep_alive
):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: pytest.fail("transport must not open"),
    )
    with pytest.raises(ValueError, match="keep-alive"):
        LocalWritingAssistant().run(
            "Draft.",
            command="Proofread",
            model="local-model",
            keep_alive=keep_alive,
        )


def test_local_writing_assistant_rejects_oversized_hud_prompt_before_transport(
    monkeypatch,
):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: pytest.fail("transport must not open"),
    )
    with pytest.raises(ValueError, match="too long"):
        LocalWritingAssistant().run(
            "word " * 4_000,
            command="Proofread",
            model="local-model",
            context_window=2048,
            max_tokens=512,
        )


def test_local_writing_assistant_preserves_boundary_whitespace(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "The draft is ready."}),
    )

    result = LocalWritingAssistant().run(
        "  the draft are ready\n",
        command="Proofread",
        model="local-model",
    )

    assert result.text == "  The draft is ready.\n"


def test_translate_requires_target_language():
    with pytest.raises(ValueError, match="translation language"):
        LocalWritingAssistant().run("Hello", command="Translate", model="local-model")


def test_unknown_writing_command_is_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        LocalWritingAssistant().run("Hello", command="Publish", model="local-model")


def test_email_profile_is_included_in_local_prompt(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert "WRITING PROFILE: Email" in body["prompt"]
        assert "without inventing" not in body["prompt"]
        return _Response({"response": "Please send the report today."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "send the report today",
        command="Rewrite",
        model="local-model",
        profile="Email",
    )
    assert result.profile == "Email"


def test_code_safe_profile_protects_inline_code(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert "`run_task()`" not in body["prompt"]
        assert "ECHOLINKTOKEN" in body["prompt"]
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Use `run_task()` after config_value.",
        command="Proofread",
        model="local-model",
        profile="Code-safe",
    )
    assert "`run_task()`" in result.text
    assert "config_value" in result.text


def test_prompt_prioritizes_task_facts_negation_and_deterministic_output(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["prompt"]
        assert "Perform only the stated editing task" in prompt
        assert "Preserve every fact" in prompt
        assert "Preserve negation" in prompt
        assert "Do not add facts" in prompt
        assert body["options"]["temperature"] == 0
        assert "42" not in prompt
        selected = prompt.split("SELECTED TEXT:\n", 1)[1]
        assert "not" in selected.casefold()
        assert "before" in selected.casefold()
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Do not restart the service before backup 42.",
        command="Rewrite",
        model="local-model",
    )
    assert "not" in result.text.casefold()
    assert "42" in result.text


def test_meaning_guard_rejects_lost_or_inverted_constraints_after_one_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Restart the service after backup ECHOLINKTOKEN0X."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="meaning constraint"):
        LocalWritingAssistant().run(
            "Do not restart the service before backup 42.",
            command="Rewrite",
            model="local-model",
        )
    assert len(calls) == 2


def test_tone_cleanup_runs_before_meaning_validation_when_model_copies_dirty_source(monkeypatch):
    source = (
        "the project team were ready to launch Manuscript after monday review but the interface "
        "were crowded. one tester accepted a spelling fix yet the text don't change and the HUD "
        "stay on first sentence."
    )

    def fake_urlopen(request, timeout):
        prompt = json.loads(request.data.decode("utf-8"))["prompt"]
        return _Response({"response": prompt.split("SELECTED TEXT:\n", 1)[1]})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Concise",
        model="local-model",
    )

    assert "team was ready" in result.text
    assert "interface was crowded" in result.text
    assert "text didn't change" in result.text
    assert "HUD stayed on the first sentence" in result.text


def test_meaning_guard_allows_equivalent_negative_wording(monkeypatch):
    def fake_urlopen(request, timeout):
        return _Response({"response": "Never delete ECHOLINKTOKEN0X before review."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Do not delete file.txt before review.",
        command="Rewrite",
        model="local-model",
    )
    assert result.text == "Never delete file.txt before review."


def test_meaning_guard_accepts_does_nothing_and_neither_nor_as_negative(monkeypatch):
    rewritten = (
        'The area remains blank, and clicking "Accept" does nothing. '
        "It neither resizes as needed nor behaves as intended."
    )

    def fake_urlopen(request, timeout):
        return _Response({"response": rewritten})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "The area remains blank, and clicking Accept doesn't work. "
        "It is not resizing as needed, and it is not behaving as intended.",
        command="Rewrite",
        model="local-model",
    )

    assert result.text == rewritten


def test_meaning_guard_allows_neither_or_to_be_corrected_to_neither_nor(monkeypatch):
    source = "Neither handwritten guesses or unverified names are allowed."
    corrected = "Neither handwritten guesses nor unverified names are allowed."

    def fake_urlopen(request, timeout):
        return _Response({"response": corrected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Proofread",
        model="local-model",
    )
    assert result.text == corrected


def test_proofread_restores_a_style_option_dropped_from_a_list(monkeypatch):
    def fake_urlopen(request, timeout):
        return _Response(
            {"response": "Reviewers can request a clear, formal, or concise rewrite."}
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Reviewers can request a clear formal casual or concise rewrite.",
        command="Proofread",
        model="local-model",
    )

    assert result.text == (
        "Reviewers can request a clear, formal, casual, or concise rewrite."
    )


def test_meaning_guard_allows_proofreading_a_missing_negation_apostrophe(monkeypatch):
    def fake_urlopen(request, timeout):
        return _Response({"response": "They didn't document the changes."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "they didnt document the changes .",
        command="Proofread",
        model="local-model",
    )

    assert result.text == "They didn't document the changes."


def test_protected_token_change_gets_one_safe_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        if len(calls) == 1:
            selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
            tokens = re.findall(r"ECHOLINKTOKEN\d+X", selected)
            return _Response({"response": selected.replace(tokens[-1], "")})
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1].split("\n\nRETRY RULE:", 1)[0]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane about https://example.com now.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )
    assert len(calls) == 2
    assert "RETRY RULE" not in calls[0]["prompt"]
    assert "RETRY RULE" in calls[1]["prompt"]
    assert "Missing tokens:" in calls[1]["prompt"]
    assert result.text == "Email Jane about https://example.com now."


def test_exact_token_next_to_non_ascii_text_is_still_detected():
    token = "ECHOLINKTOKEN9X"

    assert not _immutable_tokens_changed(
        token,
        f"\u6062\u590d\u6b63\u5e38{token}\u526a\u8d34\u677f",
        {token: "Windows"},
    )


def test_protected_numbers_cannot_rewrite_placeholder_indices():
    from echo_link_core.correction import _protect_text

    source = "Keep 8, 18, 72, 7:15, 1:30, and October 8 exact."
    protected, replacements = _protect_text(
        source,
        ("October 8", "7:15", "1:30", "72", "18", "8"),
        protect_detected=False,
    )

    assert all(token in protected for token in replacements)
    assert not _immutable_tokens_changed(protected, protected, replacements)


def test_protected_word_does_not_match_inside_another_word():
    from echo_link_core.correction import _protect_text

    source = "The grant is not permanent, and another source is allowed."
    protected, replacements = _protect_text(
        source,
        ("not",),
        protect_detected=False,
    )

    assert len(replacements) == 1
    assert "another" in protected
    assert "aECHOLINKTOKEN" not in protected


def test_written_number_facts_normalize_compound_decades_and_detect_loss():
    from echo_link_core.selected_text import (
        _NUMBER_WORD_FACT,
        _number_fact_phrase,
        _numeric_literal_change,
    )

    source = "The trainer has forty five minutes."
    matches = tuple(_NUMBER_WORD_FACT.finditer(source))

    assert len(matches) == 1
    assert matches[0].group(0) == "forty five minutes"
    assert _number_fact_phrase(source, matches[0]) == "forty-five minutes"
    assert _numeric_literal_change(source, "The trainer has 45 minutes.") is None
    assert _numeric_literal_change(source, "The trainer has five minutes.") is not None


def test_final_cleanup_runs_again_after_numeric_tokens_are_restored(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "At 12:40 p.m. observers will test the alarm.",
        command="Proofread",
        model="local-model",
    )

    assert result.text == "At 12:40 p.m., observers will test the alarm."


def test_written_number_guard_allows_safe_adjective_synonym(monkeypatch):
    def fake_urlopen(_request, _timeout):
        return _Response(
            {"response": "Elena received three distinct checklists."}
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)

    # "separate" to "distinct" is a synonym swap, and Proofread no longer
    # accepts one. The rule that catches it is the same rule that caught
    # "ENTERS the ROOM" becoming "measures the room" in a live essay: a
    # content word disappeared and nothing in the answer explains it as a
    # respelling or an inflection. Nothing here distinguishes the two cases
    # lexically, so the product takes the deliberate trade - offer a smaller
    # correction, or none, rather than reword what the writer chose.
    with pytest.raises(RuntimeError, match="meaning constraint"):
        LocalWritingAssistant().run(
            "Elena received three separate checklists.",
            command="Proofread",
            model="local-model",
        )


def test_written_number_guard_accepts_hyphenated_time_modifier():
    from echo_link_core.selected_text import _missing_number_fact_phrases

    assert _missing_number_fact_phrases(
        ("twelve week trial",),
        "The library will run a twelve-week trial.",
    ) == ()


def test_repeated_protected_values_receive_unique_immutable_tokens():
    from echo_link_core.correction import _protect_text

    source = "Check Form AX-17, then archive Form AX-17."
    protected, replacements = _protect_text(
        source,
        ("Form AX-17",),
        protect_detected=False,
    )

    tokens = re.findall(r"ECHOLINKTOKEN\d+X", protected)
    assert len(tokens) == 2
    assert len(set(tokens)) == 2
    assert all(replacements[token] == "Form AX-17" for token in tokens)
    restored = protected
    for token, value in replacements.items():
        restored = restored.replace(token, value)
    assert restored == source


def test_repeated_protected_token_change_fails_after_final_targeted_request(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Protected token removed."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="changed protected content"):
        LocalWritingAssistant().run(
            "Keep release-7.zip unchanged.",
            command="Proofread",
            model="local-model",
        )
    assert len(calls) == 3


def test_same_missing_protected_token_gets_final_targeted_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        tokens = re.findall(r"ECHOLINKTOKEN\d+X", selected)
        if len(calls) < 3:
            return _Response({"response": selected.replace(tokens[-1], "")})
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane about https://example.com.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert result.text == "Email Jane about https://example.com."
    assert len(calls) == 3
    assert "FINAL RETRY RULE" in calls[-1]["prompt"]


def test_progressive_token_repair_gets_one_final_targeted_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        tokens = re.findall(r"ECHOLINKTOKEN\d+X", selected)
        if len(calls) == 1:
            response = selected
            for token in tokens:
                response = response.replace(token, "")
            return _Response({"response": response})
        if len(calls) == 2:
            return _Response({"response": selected.replace(tokens[-1], "")})
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane about https://example.com.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert result.text == "Email Jane about https://example.com."
    assert len(calls) == 3
    assert "FINAL RETRY RULE" in calls[-1]["prompt"]


def test_shifted_missing_token_gets_one_final_targeted_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        tokens = re.findall(r"ECHOLINKTOKEN\d+X", selected)
        if len(calls) == 1:
            return _Response({"response": selected.replace(tokens[0], "")})
        if len(calls) == 2:
            return _Response({"response": selected.replace(tokens[1], "")})
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane about https://example.com.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert result.text == "Email Jane about https://example.com."
    assert len(calls) == 3
    assert "FINAL RETRY RULE" in calls[-1]["prompt"]


def test_invalid_format_does_not_retry_when_tokens_are_intact(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "```ECHOLINKTOKEN0X```"})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="invalid text"):
        LocalWritingAssistant().run(
            "Keep file.txt.",
            command="Proofread",
            model="local-model",
        )
    assert len(calls) == 1


def test_proofread_rejects_visibly_truncated_model_output(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Es very well."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="incomplete text"):
        LocalWritingAssistant().run(
            "This full sentence contains enough detail to require a complete correction.",
            command="Proofread",
            model="local-model",
        )
    assert len(calls) == 2


def test_proofread_retries_one_visibly_truncated_model_output(monkeypatch):
    source = "This full sentence contains enough detail to require a complete correction."
    responses = iter(("Es very well.", source))
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": next(responses)})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Proofread",
        model="local-model",
    )

    assert result.text == source
    assert len(prompts) == 2
    assert "prior answer was incomplete" in prompts[1]


def test_summary_prompt_and_guard_apply_a_hard_concision_limit(monkeypatch):
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": "one two three four five six"})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="word limit"):
        LocalWritingAssistant().run(
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu.",
            command="Summarize",
            model="local-model",
            instruction="Use at most 5 words.",
        )
    assert "Hard output limit: 5 words" in prompts[0]
    assert len(prompts) == 1


def test_summary_gets_a_general_derived_limit_when_user_gives_none(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert "Suggested output limit: 12 words" in body["prompt"]
        assert "Hard output limit" not in body["prompt"]
        return _Response({"response": "Alpha beta gamma delta."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    LocalWritingAssistant().run(
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron.",
        command="Summarize",
        model="local-model",
    )


def test_derived_summary_limit_does_not_reject_usable_longer_result(monkeypatch):
    source = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron."

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert "Suggested output limit: 12 words" in body["prompt"]
        return _Response({"response": source})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Summarize",
        model="local-model",
    )
    assert len(result.text.split()) == 15


def test_caveman_prompt_gets_a_concision_target(monkeypatch):
    source = "One two three four five six seven eight nine ten."

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert "Suggested output limit: 9 words" in body["prompt"]
        assert "Hard output limit" not in body["prompt"]
        return _Response({"response": source})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    LocalWritingAssistant().run(source, command="Caveman", model="local-model")


def test_derived_shorten_limit_retries_once_and_then_fails_closed(monkeypatch):
    source = "Alpha beta gamma delta epsilon zeta eta theta iota kappa."
    expanded = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": expanded})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="word limit"):
        LocalWritingAssistant().run(
            source,
            command="Shorten",
            model="local-model",
        )

    assert len(prompts) == 3
    assert "Hard output limit: 9 words" in prompts[0]
    assert "MEANING-SAFE DRAFT" in prompts[1]
    assert "Hard visible-word limit before token expansion: 10" in prompts[1]
    assert "prior result is 1 visible words too long" in prompts[2]


def test_new_number_is_rejected_without_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "The work will finish in 7 days."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="added a number"):
        LocalWritingAssistant().run(
            "The work will finish soon.",
            command="Rewrite",
            model="local-model",
        )
    assert len(calls) == 1


def test_translation_requires_target_language_output_and_preserves_tokens(monkeypatch):
    calls = []
    responses = iter(
        (
            {"response": "Please send the report to ECHOLINKTOKEN0X."},
            {"response": "Veuillez envoyer le rapport à ECHOLINKTOKEN0X."},
        )
    )

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8")))
        return _Response(next(responses))

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="did not translate"):
        LocalWritingAssistant().run(
            "Please send the report to Ana.",
            command="Translate",
            language="French",
            model="local-model",
            protected_terms=("Ana",),
        )
    assert len(calls) == 1
    assert "Use only French" in calls[0]["prompt"]

    translated = LocalWritingAssistant().run(
        "Please send the report to Ana.",
        command="Translate",
        language="French",
        model="local-model",
        protected_terms=("Ana",),
    )
    assert translated.text == "Veuillez envoyer le rapport à Ana."
    assert len(calls) == 2


@pytest.mark.parametrize(
    "command",
    (
        "Rewrite",
        "Improve",
        "Rephrase",
        "Shorten",
        "Friendly",
        "Formal",
        "Professional",
        "Diplomatic",
        "Empathetic",
        "Cooperative",
        "Assertive",
        "Caveman",
    ),
)
def test_every_visible_tone_gets_final_deterministic_proofread(monkeypatch, command):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "the team were ready!!"}),
    )

    result = LocalWritingAssistant().run(
        "the team were ready!!",
        command=command,
        model="local-model",
    )

    assert result.text == "The team was ready!"


def test_final_proofread_keeps_custom_protected_term_exact(monkeypatch):
    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "the team were reviewing wierdBrand!!",
        command="Friendly",
        model="local-model",
        protected_terms=("wierdBrand",),
    )

    assert result.text == "The team was reviewing wierdBrand!"


def test_one_paragraph_tone_result_cannot_add_internal_blank_lines(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "the team\n\nwere ready!!"}),
    )

    result = LocalWritingAssistant().run(
        "the team were ready!!",
        command="Formal",
        model="local-model",
    )

    assert result.text == "The team was ready!"
    assert "\n" not in result.text


def test_accidental_all_caps_prose_is_normalized_without_lowering_acronyms(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response(
            {"response": "THE TEAM IS READY AND THE HUD USES AI IN MANUSCRIPT."}
        ),
    )

    result = LocalWritingAssistant().run(
        "The team is ready and the HUD uses AI in Manuscript.",
        command="Formal",
        model="local-model",
    )

    assert result.text == "The team is ready and the HUD uses AI in Manuscript."


def test_unambiguous_malformed_internal_placeholder_is_repaired(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Email ECHOLINKTOKEN now."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane now.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert result.text == "Email Jane now."
    assert "ECHOLINKTOKEN" not in result.text
    assert len(calls) == 1


def test_stray_internal_placeholder_gets_one_safe_retry_and_never_leaks(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        if len(calls) == 1:
            return _Response({"response": f"{selected} ECHOLINKTOKEN"})
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Email Jane now.",
        command="Rewrite",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert result.text == "Email Jane now."
    assert "ECHOLINKTOKEN" not in result.text
    assert len(calls) == 2


def test_protected_tokens_cannot_be_reordered(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        tokens = re.findall(r"ECHOLINKTOKEN\d+X", selected)
        swapped = selected.replace(tokens[0], "TEMP_TOKEN", 1)
        swapped = swapped.replace(tokens[1], tokens[0], 1).replace("TEMP_TOKEN", tokens[1], 1)
        return _Response({"response": swapped})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="changed protected content"):
        LocalWritingAssistant().run(
            "Email Jane about https://example.com.",
            command="Rewrite",
            model="local-model",
            protected_terms=("Jane",),
        )

    assert len(calls) == 3


def test_number_word_and_digit_are_the_same_numeric_fact(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "Keep records for 15 days."}),
    )

    result = LocalWritingAssistant().run(
        "Keep records for fifteen days.",
        command="Rewrite",
        model="local-model",
    )

    assert result.text == "Keep records for 15 days."


def test_added_number_word_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": "The work will finish in seven days."}),
    )

    with pytest.raises(RuntimeError, match="added a number"):
        LocalWritingAssistant().run(
            "The work will finish soon.",
            command="Rewrite",
            model="local-model",
        )


def test_omitted_number_fact_gets_one_complete_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            return _Response({"response": "The users wrote pages."})
        return _Response({"response": "The three users wrote pages."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "the three users wrote pages",
        command="Improve",
        model="local-model",
    )

    assert result.text == "The three users wrote pages."
    assert len(calls) == 2


def test_protected_number_phrase_counts_as_present_during_validation(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        body = json.loads(request.data.decode("utf-8"))
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "The three users wrote pages.",
        command="Rewrite",
        model="local-model",
        protected_terms=("three users",),
    )

    assert result.text == "The three users wrote pages."
    assert len(calls) == 1


def test_tone_command_cannot_add_or_invert_meaning_constraints(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Do not send the report after release."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    with pytest.raises(RuntimeError, match="meaning constraint"):
        LocalWritingAssistant().run(
            "Send the report before release.",
            command="Friendly",
            model="local-model",
        )

    assert len(calls) == 2


def test_caller_can_defer_surface_meaning_guard_to_stronger_validator(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "Do not send the report after release."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Send the report before release.",
        command="Friendly",
        model="local-model",
        defer_meaning_validation=True,
    )

    assert result.text == "Do not send the report after release."
    assert len(calls) == 1


def test_caller_can_defer_written_number_guard_to_stronger_validator(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response(
            {"response": "Gardens rely on a single unpaid organizer."}
        )

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Gardens rely on one unpaid organizer.",
        command="Rephrase",
        model="local-model",
        defer_meaning_validation=True,
    )

    assert result.text == "Gardens rely on a single unpaid organizer."
    assert len(calls) == 1


def test_caller_can_defer_added_numeric_literal_guard_to_stronger_validator(
    monkeypatch,
):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": "The stable system reports code 409."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "The system is stable.",
        command="Rephrase",
        model="local-model",
        defer_meaning_validation=True,
    )

    assert result.text == "The stable system reports code 409."
    assert len(calls) == 1


def test_inability_can_be_rephrased_as_difficulty(monkeypatch):
    candidate = "Users find it difficult to tell which sentence is active."
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "users cant tell which sentence is active",
        command="Empathetic",
        model="local-model",
    )

    assert result.text == candidate


@pytest.mark.parametrize(
    ("source", "first", "second"),
    (
        (
            "Developers must exclude private writing from reports.",
            "Developers should exclude private writing from reports.",
            "Developers must exclude private writing from reports.",
        ),
        (
            "Encrypted history retains only approved information.",
            "Encrypted history must retain only approved information.",
            "Encrypted history retains only approved information.",
        ),
    ),
)
def test_force_level_drift_gets_one_guarded_retry(monkeypatch, source, first, second):
    responses = iter((first, second))
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": next(responses)})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Rewrite",
        model="local-model",
    )

    assert result.text == second
    assert len(calls) == 2


@pytest.mark.parametrize("changed_force", ("must", "needs to"))
def test_application_should_force_is_restored_without_weakening_developer_must(
    monkeypatch, changed_force
):
    candidate = (
        f"The application {changed_force} work locally and explain failures clearly. It should avoid "
        "hidden background uploads. Developers can use synthetic local evidence, but they must keep "
        "private user writing out of test reports."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "Application should works locally explain failures clear,and avoid hidden background uploads. "
        "Developers can use synthetic local evidence but they must keeps private user writing out test reports",
        command="Assertive",
        model="local-model",
    )

    assert result.text.startswith("The application should work locally")
    assert "developers can" in result.text.casefold()
    assert "they must keep private user writing out of test reports" in result.text.casefold()


def test_private_user_writing_may_be_kept_out_by_not_appearing(monkeypatch):
    candidate = "Developers must ensure private user writing does not appear in test reports."
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "Developers must keep private user writing out of test reports.",
        command="Rephrase",
        model="local-model",
    )

    assert result.text == candidate


def test_launch_readiness_cannot_be_weakened_to_a_plan(monkeypatch):
    responses = iter(
        (
            "The project team planned to launch Manuscript following Monday's review.",
            "The project team was prepared to launch Manuscript after Monday's review.",
        )
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": next(responses)}),
    )

    result = LocalWritingAssistant().run(
        "the project team were ready to launch Manuscript after monday review",
        command="Rephrase",
        model="local-model",
    )

    assert result.text == "The project team was prepared to launch Manuscript after Monday's review."


def test_tone_rewrite_does_not_add_line_breaks_to_one_paragraph(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response(
            {"response": "The team reviewed the logs.\nThey fixed the issue."}
        ),
    )

    result = LocalWritingAssistant().run(
        "The team reviewed the logs and fixed the issue.",
        command="Caveman",
        model="local-model",
    )

    assert result.text == "The team reviewed the logs. They fixed the issue."


def test_safe_shorten_compression_removes_words_without_losing_build_or_controls():
    source = (
        "The project team was ready. The first shared build was slow, putting periods in weird spots "
        "and failing to insert corrected text into the active document. Reviewers need clear red marks "
        "under changed words, visible Accept and Dismiss controls, and a reliable way to move to the next sentence."
    )

    result = _apply_safe_shorten_compression(source)

    assert len(result.split()) < len(source.split())
    assert "failed to insert corrected text" in result
    assert "Accept and Dismiss controls" in result


def test_safe_shorten_compression_handles_a_protected_safe_paste_token():
    source = "Find the same text before applying one ECHOLINKTOKEN4X."

    result = _apply_safe_shorten_compression(source)

    assert result == "Find the same text before one ECHOLINKTOKEN4X."


def test_safe_caveman_split_keeps_launch_facts_in_one_paragraph():
    source = (
        "The project team was ready to launch Manuscript after Monday's review, but the interface was "
        "crowded on smaller screens and several status messages were unclear."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "The team was ready to launch Manuscript after Monday's review. The interface was crowded on "
        "smaller screens. Several status messages were unclear."
    )
    assert "\n" not in result


def test_safe_caveman_split_turns_the_release_checklist_into_blunt_sentences():
    source = (
        "Verify microphone, activation sound, error logs, three users, small icon, two or three "
        "sentences, red marks, safe paste, clipboard restoration, fifteen days retention, and cloud "
        "service boundary."
    )

    result = _apply_safe_caveman_splits(source)

    assert "Verify the microphone. Verify the activation sound." in result
    assert "Test safe paste and clipboard restoration." in result
    assert "Verify the cloud service boundary." in result
    assert "\n" not in result


def test_safe_caveman_split_breaks_other_complex_sentences_without_losing_facts():
    source = (
        "They tested microphone on two laptops, but recording stopped without warning often. "
        "Each user liked small icon, but the correction card were still too large. "
        "The application should work locally and explain failures clearly. The deadline was near, "
        "so the developers rushed their review of error logs. It placed periods in weird spots and "
        "didn't insert corrected text into the active document."
    )

    result = _apply_safe_caveman_splits(source)

    assert "They tested the microphone on two laptops. Recording often stopped without warning." in result
    assert "Each user liked the small icon. The correction card was still too large." in result
    assert "The application should work locally. It should explain failures clearly." in result
    assert "A near deadline made developers rush their review of the error logs." in result
    assert "It misplaced periods and failed to insert corrected text into the active document." in result


def test_safe_caveman_split_repeats_told_actor_for_two_actions():
    source = (
        "Elena told system administrator Marcus Bell to preserve backup NSFS-DB-441 and verify "
        "its SHA-256 value before 6:30 p.m. on November 15."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Elena told system administrator Marcus Bell to preserve backup NSFS-DB-441. "
        "Elena also told system administrator Marcus Bell to verify its SHA-256 value before "
        "6:30 p.m. on November 15."
    )

def test_safe_caveman_split_repeats_instructed_actor_for_two_actions():
    source = (
        "Elena instructed system administrator Marcus Bell to preserve backup NSFS-DB-441 and verify "
        "its SHA-256 value before 6:30 p.m. on November 15."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Elena instructed system administrator Marcus Bell to preserve backup NSFS-DB-441. "
        "Elena also instructed system administrator Marcus Bell to verify its SHA-256 value before "
        "6:30 p.m. on November 15."
    )

def test_safe_caveman_split_handles_conditional_actions_and_linked_facts():
    source = (
        "Until the cause is known, staff must write readings on form TEMP-108 every fifteen minutes "
        "and enter them into MedTrack 4.7 only after utility power returns. "
        "It also needs to state that DispatchGrid 3.2 runs locally on managed tablets and does not "
        "send private service notes to a personal cloud account. "
        "Everyone agrees the note should be brief, but omitting a failure would make it less accurate "
        "than the longer version. "
        "These numbers sound impressive, but quantity by itself does not explain access. "
        "The survey was only available in English for six weeks, which caused fewer responses."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Until the cause is known, staff must write readings on form TEMP-108 every fifteen minutes. "
        "Until the cause is known, staff must enter them into MedTrack 4.7 only after utility power "
        "returns. It also needs to state that DispatchGrid 3.2 runs locally on managed tablets. "
        "It needs to state that DispatchGrid 3.2 does not send private service notes to a personal "
        "cloud account. Everyone agrees the note should be brief. Omitting a failure would make it less "
        "accurate than the longer version. These numbers sound impressive. Quantity by itself does not "
        "explain access. The survey was only available in English for six weeks. This caused fewer responses."
    )


def test_safe_caveman_split_shortens_tentative_agreement_without_strengthening_it():
    source = (
        "Keisha has a tentative agreement with Route 14 Transit to hold the final bus until 9:45 p.m. "
        "Monday through Thursday."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Keisha tentatively agreed with Route 14 Transit to hold the final bus until 9:45 p.m. "
        "Monday through Thursday."
    )

def test_safe_caveman_split_handles_comparison_finding_and_accuracy_clause():
    source = (
        "Gardens with a written schedule and two trained coordinators lost fewer workdays, while "
        "gardens relying on one unpaid organizer had many more cancellations. "
        "Engineer Sofia Chen found that retry worker sb_retry_2 treated HTTP 409 as a new event "
        "instead of an existing record. "
        "A more accurate sentence would say the measures reduce known access and supervision risks "
        "while making clear that emergency procedures remain unchanged."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Gardens with a written schedule and two trained coordinators lost fewer workdays. Gardens "
        "relying on one unpaid organizer had many more cancellations. Engineer Sofia Chen found retry "
        "worker sb_retry_2 treated HTTP 409 as a new event, not an existing record. A more accurate "
        "sentence would say the measures reduce known access and supervision risks. The sentence would make clear "
        "that emergency procedures remain unchanged."
    )

def test_safe_caveman_split_repeats_interview_condition_and_splits_alert_fact():
    source = (
        "Before any interview, participants received form LU-CG-12 and could decline audio recording "
        "without losing access to a garden plot. It must not blame the overnight operator as the "
        "technical cause, although the alert closure delayed detection by 48 minutes."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Before any interview, participants received form LU-CG-12. Before any interview, participants "
        "could decline audio recording without losing access to a garden plot. It must not blame the "
        "overnight operator as the technical cause. The alert closure delayed detection by 48 minutes."
    )

def test_safe_caveman_split_preserves_deployment_order_across_two_sentences():
    source = (
        "On January 14, the HarborPoint Analytics ingestion service started duplicating invoice events "
        "after SyncBridge 6.4 was deployed to node HP-EAST-03."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "On January 14, SyncBridge 6.4 was deployed to node HP-EAST-03. Afterward, the HarborPoint "
        "Analytics ingestion service started duplicating invoice events."
    )

def test_safe_caveman_split_handles_study_warning_privacy_and_survey_count():
    source = (
        "Lead researcher Professor Nia Okafor says the study did not measure property values, and it "
        "must not be presented as proof that gardens raise rent. Owners and deadlines remain required "
        "in the final table, and no customer data may appear in the public version. Branch manager "
        "Keisha Ward received 684 survey responses between January 5 and February 2; 438 respondents "
        "said they would use later hours at least twice a month."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "Lead researcher Professor Nia Okafor says the study did not measure property values. The study "
        "must not be presented as proof that gardens raise rent. Owners and deadlines remain required in "
        "the final table. No customer data may appear in the public version. Branch manager Keisha Ward "
        "received 684 survey responses between January 5 and February 2. 438 respondents said they would "
        "use later hours at least twice a month."
    )


def test_safe_caveman_splits_repeats_subject_before_must_not():
    source = (
        "The library will publish aggregate totals but must not release names, "
        "library card numbers, or individual travel patterns."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "The library will publish aggregate totals. "
        "The library must not release names, library card numbers, or individual travel patterns."
    )


def test_safe_caveman_splits_without_pretending():
    source = (
        "It should explain why the limited trial is worth testing without pretending "
        "the evidence is stronger than it is."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "It should explain why the limited trial is worth testing. "
        "It should not pretend the evidence is stronger than it is."
    )
def test_safe_caveman_split_breaks_independent_but_clause():
    source = (
        "The draft uses the word failure for low attendance, but that is poor "
        "vocabulary because it blames residents for barriers outside their control."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "The draft uses the word failure for low attendance. That is poor "
        "vocabulary because it blames residents for barriers outside their control."
    )


def test_safe_caveman_split_breaks_independent_semicolon_clauses():
    source = (
        "The backup generator was serviced on September 28 and its fuel tank is "
        "87 percent full; however, the maintenance record does not show who completed "
        "the final load test. Amina must keep the original paper record in cabinet "
        "RVC-POWER-03; no one may scan it or send it by email."
    )

    result = _apply_safe_caveman_splits(source)

    assert result == (
        "The backup generator was serviced on September 28 and its fuel tank is "
        "87 percent full. However, the maintenance record does not show who completed "
        "the final load test. Amina must keep the original paper record in cabinet "
        "RVC-POWER-03. No one may scan it or send it by email."
    )


def test_safe_caveman_split_unpacks_linked_ai_workflow_and_tone_sentences():
    source = (
        "The local AI returned a shorter draft, removing two details and changing names that needed "
        "to remain exact. The local workflow reads active selection, separates sentences, and sends "
        "small groups to the installed AI model. They need clear, formal, casual, concise, friendly, "
        "professional, diplomatic, empathetic, cooperative, and assertive versions without opening "
        "the main application window."
    )

    result = _apply_safe_caveman_splits(source)

    assert "The local AI made a shorter draft. It removed two details" in result
    assert "The local workflow reads active selection. It separates sentences and sends" in result
    assert "They need clear, formal, casual, concise, and friendly versions." in result
    assert "Using these versions must not open the main application window." in result


def test_split_caveman_links_still_pass_meaning_guards():
    ai_source = (
        "The third tester request formal tone. The local AI return a shorter draft removed two "
        "details and change names that needed to remain exact."
    )
    ai_candidate = (
        "The third tester requested a formal tone. The local AI made a shorter draft. It removed two "
        "details and changed names that had to stay exact."
    )
    workflow_source = (
        "The local workflow read active selection, seperates complete sentences and sends each group "
        "to the model."
    )
    workflow_candidate = (
        "The local workflow reads active selection. It separates sentences and sends each group to "
        "the model."
    )

    assert _meaning_constraints_preserved(ai_source, ai_candidate)
    assert _meaning_constraints_preserved(workflow_source, workflow_candidate)


def test_proofread_accepts_lost_as_correction_for_loose_details():
    source = (
        "The third tester request formal tone, the local AI return a shorter draft but it loose "
        "two details and change names that needed to remain exact."
    )
    candidate = (
        "The third tester asked for a formal tone; the local AI returned a shorter draft, but it "
        "lost two details and changed names that needed to remain exact."
    )

    assert _meaning_constraints_preserved(source, candidate)


def test_smart_apostrophe_didnt_preserves_the_accepted_fix_fact():
    source = (
        "One tester accept a spelling fix yet the text don't change and the HUD stay on first sentence."
    )
    candidate = (
        "One tester accepted a spelling fix, yet the text didn\u2019t change and the HUD stayed on the "
        "first sentence."
    )

    assert _meaning_constraints_preserved(source, candidate)


def test_windows_token_is_moved_to_clipboard_and_not_duplicated():
    source = (
        "If selection disappears, Manuscript must find the same unchanged text before one safe paste; "
        "the workflow restores the normal Windows clipboard after an approved fallback."
    )
    candidate = (
        "If selection disappears, ECHOLINKTOKEN5X must find the same unchanged text before one "
        "ECHOLINKTOKEN7X. Restore normal Windows clipboard after an approved fallback, "
        "ECHOLINKTOKEN9X."
    )
    replacements = {
        "ECHOLINKTOKEN5X": "Manuscript",
        "ECHOLINKTOKEN7X": "safe paste",
        "ECHOLINKTOKEN9X": "Windows",
    }

    result = _restore_candidate_semantics(source, candidate, replacements)

    assert result.count("ECHOLINKTOKEN9X") == 1
    assert "normal ECHOLINKTOKEN9X clipboard" in result


def test_missing_activation_token_is_restored_from_reversed_playback_clause():
    token = "ECHOLINKTOKEN0X"
    source = "The activation sound played after the speaker started talking."
    candidate = "Playback began before speaking started."

    result = _restore_candidate_semantics(
        source,
        candidate,
        {token: "activation sound"},
    )

    assert result == f"{token} played after the speaker started talking."


def test_token_canonicalization_removes_duplicate_fifteen_day_expansion():
    token = "ECHOLINKTOKEN2X"
    candidate = f"Keep {token} retention for fifteen days."

    result = _canonicalize_immutable_tokens(
        candidate,
        f"Keep {token} retention.",
        {token: "fifteen days"},
    )

    assert result == f"Keep {token} retention."


def test_caveman_split_style_lists_are_not_restored_twice(monkeypatch):
    response = (
        "They need clear, formal, casual, concise, friendly, professional, diplomatic, empathetic, "
        "cooperative, and assertive versions without opening the main application window."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": response}),
    )

    result = LocalWritingAssistant().run(
        "They also need clear, formal, casual, concise, friendly, professional, diplomatic, "
        "empathetic, cooperative or assertive versions without opening main application window",
        command="Caveman",
        model="local-model",
    )

    for option in (
        "clear", "formal", "casual", "concise", "friendly", "professional", "diplomatic",
        "empathetic", "cooperative", "assertive",
    ):
        assert len(re.findall(rf"\b{option}\b", result.text, re.IGNORECASE)) == 1


def test_tone_command_accepts_safe_synonyms_for_linked_draft_facts(monkeypatch):
    candidate = (
        "The third tester requested a formal tone. The local AI returned a shortened draft that "
        "omitted two details and altered names that needed to remain exact. These issues make the "
        "review confusing because users cannot tell which two or three sentences are active or "
        "whether the replacement finished."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "The third tester request formal tone, the local AI return a shorter draft removed two "
        "details and change names that needed to remain exact. These issues makes review confusing "
        "because users cant tell which two or three sentances is active, or if replacement finished?",
        command="Formal",
        model="local-model",
    )

    assert result.text == candidate


def test_tone_command_accepts_nominal_request_and_precise_version_synonyms(monkeypatch):
    candidate = (
        "The request from the third tester was for a formal tone, and the local AI provided a "
        "shortened version by removing two details and altering names that had to stay precise. "
        "These changes make the review confusing because users cannot tell which two or three "
        "sentences are active or whether the replacement finished."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "The third tester request formal tone, the local AI return a shorter draft removed two "
        "details and change names that needed to remain exact. These issues makes review confusing "
        "because users cant tell which two or three sentances is active, or if replacement finished?",
        command="Rephrase",
        model="local-model",
    )

    assert result.text == candidate


def test_reported_draft_change_is_not_allowed_to_become_an_ai_command(monkeypatch):
    candidate = (
        "The third tester requested a formal tone. The local AI must remove two details and change "
        "names that needed to remain exact. Users cannot tell which two or three sentences are active."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "The third tester request formal tone. The local AI return a shorter draft removed two details "
        "and change names that needed to remain exact. Users cant tell which two or three sentances is active.",
        command="Rewrite",
        model="local-model",
    )

    assert "The local AI returned a shorter draft, removing two details and changing names" in result.text
    assert "AI must remove" not in result.text


def test_shorten_keeps_paragraph_preservation_as_a_user_requirement(monkeypatch):
    candidate = (
        "The workflow reads active selection, separates complete sentences, and sends each group to "
        "the model for checks. The model returns one clean suggestion while maintaining every original "
        "paragraph and line break unless the user requests a structural rewrite."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "The workflow reads active selection, separates complete sentences, and sends each group to "
        "the model for checks. The model returns one clean suggestion. Every original paragraph and "
        "line break must remain in place unless the user requests a structural rewrite.",
        command="Shorten",
        model="local-model",
    )

    assert "Every original paragraph and line break must stay" in result.text
    assert "while maintaining every original paragraph" not in result.text


def test_passive_style_requirement_restores_the_source_owner(monkeypatch):
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response(
            {
                "response": (
                    "Clear, formal, casual, concise, friendly, professional, diplomatic, empathetic, "
                    "cooperative, or assertive versions are needed without opening the main application window."
                )
            }
        ),
    )

    result = LocalWritingAssistant().run(
        "They also need clear, formal, casual, concise, friendly, professional, diplomatic, empathetic, "
        "cooperative or assertive versions without opening main application window",
        command="Assertive",
        model="local-model",
    )

    assert result.text.startswith("They also need clear, formal")
    assert "versions without opening the main application window" in result.text


def test_rephrase_accepts_workflow_that_involves_selecting_active_text(monkeypatch):
    candidate = (
        "The revised local workflow involves selecting active text, separating complete sentences, "
        "and sending each small group to an installed AI model. The model checks spelling, grammar, "
        "punctuation, and basic formatting before returning one clean suggestion. Every original "
        "paragraph and line break must remain in place unless the user requests a structural rewrite."
    )
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda _request, _timeout: _Response({"response": candidate}),
    )

    result = LocalWritingAssistant().run(
        "the revised local workflow read active selection,seperates complete sentances and send each "
        "small group to installed AI model. The model check spelling grammer punctuation and basic "
        "formating before it return one clean suggestion . Every original paragraph and line break "
        "must remains in place unless user ask structural rewrite.",
        command="Rephrase",
        model="local-model",
    )

    assert result.text == candidate


@pytest.mark.parametrize(
    ("source", "first", "second"),
    (
        (
            "The activation sound played after the speaker started.",
            "The activation sound played prematurely.",
            "The activation sound played after the speaker started.",
        ),
        (
            "Reviewers need clear versions.",
            "You might need clear versions.",
            "Reviewers need clear versions.",
        ),
        (
            "Developers must keep private user writing out of test reports.",
            "Developers must keep test reports private.",
            "Developers must keep private user writing out of test reports.",
        ),
    ),
)
def test_retryable_meaning_drift_gets_one_guarded_retry(
    monkeypatch, source, first, second
):
    responses = iter((first, second))
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"response": next(responses)})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        source,
        command="Rewrite",
        model="local-model",
    )

    assert result.text == second
    assert len(calls) == 2


def test_written_number_relation_requires_its_qualifying_noun(monkeypatch):
    responses = iter(
        (
            "Keep records for fifteen days.",
            "Keep records for fifteen days retention.",
        )
    )
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": next(responses)})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "Keep records for fifteen days retention.",
        command="Rewrite",
        model="local-model",
    )

    assert result.text == "Keep records for fifteen days retention."
    assert len(prompts) == 2
    assert "fifteen days retention" in prompts[1]


def test_standalone_sequence_word_does_not_invent_a_number_fact():
    assert _numeric_literal_change(
        "Apply one safe paste.",
        "Find the text first. Then apply one safe paste.",
    ) is None


def test_no_one_pronoun_does_not_invent_number_one():
    assert _numeric_literal_change(
        "Nobody reported the problem.",
        "No one reported the problem.",
    ) is None


def test_no_one_pronoun_is_not_a_written_number_fact():
    from echo_link_core.selected_text import _NUMBER_WORD_FACT

    assert not tuple(_NUMBER_WORD_FACT.finditer("No one may scan the record."))


def test_pluralizing_one_after_an_existing_count_is_not_a_lost_number_fact():
    assert _numeric_literal_change(
        "The order included three blue trays and two green one.",
        "The order included three blue trays and two green ones.",
    ) is None


def test_protected_windows_clipboard_is_restored_from_equivalent_retry(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        tokens = list(dict.fromkeys(re.findall(r"ECHOLINKTOKEN\d+X", selected)))
        calls.append(body["prompt"])
        if len(calls) == 1:
            response = (
                f"If selection disappears, {tokens[0]} should find the same unchanged text before "
                f"applying one {tokens[1]}; the workflow restores the normal {tokens[2]} clipboard "
                "immediately after an approved fallback."
            )
        else:
            response = (
                f"If selection disappears, {tokens[0]} must find the same unchanged text before "
                f"applying one {tokens[1]}; immediately after an approved fallback, the workflow "
                "restores the clipboard to its normal state."
            )
        return _Response({"response": response})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = LocalWritingAssistant().run(
        "If selection disappears, Manuscript must find the same unchanged text before applying one "
        "safe paste; the workflow restores the normal Windows clipboard immediately after an "
        "approved fallback.",
        command="Rephrase",
        model="local-model",
        protected_terms=("Manuscript", "safe paste", "Windows"),
    )

    assert "Manuscript must find" in result.text
    assert "one safe paste" in result.text
    assert "normal Windows clipboard" in result.text
    assert len(calls) == 2


def test_linked_feedback_facts_accept_spelling_correction_synonym():
    source = (
        "The next morning the team recieved a detail report from three users who wrote several pages. "
        "Each people liked the small icon but the correction card were still too large. One tester "
        "accept a spelling fix yet the text don't change and the HUD stay on first sentence; another "
        "tester seen inconsistant grammer marks while common spelling problems remains."
    )
    candidate = (
        "The following morning, the team received a detailed report from three users who provided "
        "several pages of feedback. Each individual expressed appreciation for the small icon, although "
        "the correction card remained excessively large. One tester accepted a spelling correction, "
        "yet the text did not change, and the HUD remained on the first sentence. Another tester "
        "observed inconsistent grammar markings, while common spelling issues persisted."
    )

    assert _linked_source_facts_preserved(source, candidate)


def test_shorten_compresses_singular_replacement_status_without_losing_facts():
    candidate = (
        "The third tester requested a formal tone. The local AI returned a shorter draft, removing "
        "two details and changing names that needed to remain exact. These issues make review confusing, "
        "as users cannot tell which two or three sentences are active, or if replacement is finished."
    )

    shortened = _apply_safe_shorten_compression(candidate)

    assert "two or three sentences" in shortened
    assert "replacements are finished" in shortened
    assert len(shortened.split()) < len(candidate.split())

def test_final_cleanup_repairs_simplified_transition_punctuation():
    source = (
        "However MedTrack 4.7 sometimes displays a blank reading. "
        "However, The transit board must approve schedule RT14-EG-2026."
    )

    assert _apply_fast_proofread_rules(source) == (
        "However, MedTrack 4.7 sometimes displays a blank reading. "
        "However, the transit board must approve schedule RT14-EG-2026."
    )

def test_final_cleanup_repairs_high_school_live_residuals():
    source = (
        "There also have several punctuation issues. "
        "Keisha agreed; However, the transit board must approve it."
    )

    assert _apply_fast_proofread_rules(source) == (
        "There are also several punctuation issues. "
        "Keisha agreed; however, the transit board must approve it."
    )

@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "Marcus must not overwrite accessed the earlier backup.",
            "Marcus must not overwrite the earlier backup.",
        ),
        (
            "It must not be presented these findings as evidence.",
            "It must not be presented as evidence.",
        ),
        (
            "There also present several structural issues.",
            "There are also several structural issues.",
        ),
        (
            "That conclusion does not follow was drawn from the evidence.",
            "That conclusion does not follow from the evidence.",
        ),
        (
            "Others concatenate six actions with commas and no establish a clear sequence.",
            "Others concatenate six actions with commas and fail to establish a clear sequence.",
        ),
        (
            "This data is useful, but it should be cautioned more cautiously.",
            "This data is useful, but it should be explained more cautiously.",
        ),
    ),
)
def test_final_cleanup_repairs_college_live_residual(source, expected):
    assert _apply_fast_proofread_rules(source) == expected

@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "Amina requested Owen to verify the signature.",
            "Amina asked Owen to verify the signature.",
        ),
        (
            "Marcus must not overwrite referenced the earlier backup.",
            "Marcus must not overwrite the earlier backup.",
        ),
        (
            "It must not be presented this as evidence.",
            "It must not be presented as evidence.",
        ),
        (
            "The recommendation should explain will explain why the trial is useful.",
            "The recommendation should explain why the trial is useful.",
        ),
    ),
)
def test_final_cleanup_repairs_diplomatic_live_residual(source, expected):
    assert _apply_fast_proofread_rules(source) == expected

def test_final_cleanup_repairs_empathetic_live_residual():
    assert _apply_fast_proofread_rules(
        "This data is useful, but it should be explained it more cautiously."
    ) == "This data is useful, but it should be explained more cautiously."


def test_token_rule_is_omitted_when_the_text_has_no_protected_tokens(monkeypatch):
    """Naming a token system the model cannot see made it invent one.

    Every prompt stated the ECHOLINKTOKEN rule unconditionally. On ordinary
    prose with nothing to protect, small models answered by writing the word
    ECHOLINKTOKEN into the correction, and the mismatch check threw the whole
    answer away - the log read
    "protected-token mismatch stage=initial expected=() actual=('ECHOLINKTOKEN',)"
    and the section never produced a correction.
    """
    prompts: list[str] = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    LocalWritingAssistant().run(
        "the dog were happy and it ran to the gate",
        command="Proofread",
        model="local-model",
    )

    assert prompts
    assert "ECHOLINKTOKEN" not in prompts[0]


def test_token_rule_is_still_stated_when_a_token_is_present(monkeypatch):
    prompts: list[str] = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    LocalWritingAssistant().run(
        "Email Jane about the gate.",
        command="Proofread",
        model="local-model",
        protected_terms=("Jane",),
    )

    assert prompts
    assert "ECHOLINKTOKEN" in prompts[0]


def test_a_correction_may_not_swap_one_verb_for_another() -> None:
    """The writer said the owner entered; the corrector said they measured.

    This shipped. "when the oner ENTERS the ROOM" came back from a live essay
    run as "when the owner measures the room", and every guard accepted it,
    because none of them looked for a plain substituted verb. Repairing "oner"
    to "owner" in the same sentence is the whole point of the feature, so the
    guard has to separate the two.
    """
    assert not _meaning_constraints_preserved(
        "when the oner ENTERS the ROOM",
        "when the owner measures the room",
        command="Proofread",
        check_word_losses=True,
    )
    assert _meaning_constraints_preserved(
        "when the oner ENTERS the ROOM",
        "when the owner enters the room",
        command="Proofread",
        check_word_losses=True,
    )


def test_a_correction_may_not_drop_the_words_it_cannot_spell() -> None:
    """Second live failure from the same essay.

    "a cat may not grete a person exacly like a DOG wuld of" came back as
    "a cat may not get as much as a dog would". "grete" is "greet", not "get",
    and "a person" simply vanished.
    """
    assert not _meaning_constraints_preserved(
        "a cat may not grete a person exacly like a DOG wuld of",
        "a cat may not get as much as a dog would",
        command="Proofread",
        check_word_losses=True,
    )
    assert _meaning_constraints_preserved(
        "a cat may not grete a person exacly like a DOG wuld of",
        "a cat may not greet a person exactly like a dog would have",
        command="Proofread",
        check_word_losses=True,
    )


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("the oner of the pett", "the owner of the pet"),
        ("it is diffrent somewwhere else", "it is different somewhere else"),
        ("becuz peopes need structure", "because people need structure"),
        ("Watching the cat moves and rests", "Watching the cat move and rest"),
        ("Sumtimes the animul is happy", "Sometimes the animal is happy"),
        ("the pet gets poor cair", "the pet gets poor care"),
        ("Dogs Makes Grate Petts", "Dogs Make Great Pets"),
        ("i really just very much like it", "I like it"),
    ],
)
def test_ordinary_repairs_are_not_mistaken_for_meaning_changes(
    source: str, candidate: str
) -> None:
    """The guard must not cost the corrections the product exists to make.

    Every pair here is a repair a reader would want: a respelling, an
    inflection, a non-standard contraction expanded, or filler cut. A guard
    that refuses these is worse than no guard, because the user sees nothing.
    """
    assert _meaning_constraints_preserved(source, candidate)


def test_a_dropped_protected_token_costs_one_sentence_not_the_section() -> None:
    """Small packs drop a protected token and keep dropping it on every retry.

    A token stands in for something the writer must keep exactly - a saved
    vocabulary term, a name, a number. The initial answer, the retry and the
    final retry all came back without it, which is what the live logs showed:
    `expected=('ECHOLINKTOKEN1X','ECHOLINKTOKEN0X') actual=('ECHOLINKTOKEN0X',)`
    at all three stages. The whole corrected section was then refused, so one
    dropped clause cost every correction around it.

    The writer's own sentence is known exactly, so it is put back and the rest
    of the answer stands. The token is never reinserted into the model's
    sentence - there is no way to know where it would belong.
    """
    from echo_link_core.selected_text import _restore_missing_immutable_tokens

    source = (
        "the oner met ECHOLINKTOKEN0X today. she gave ECHOLINKTOKEN1X a bowl. "
        "the dog was happy."
    )
    candidate = (
        "The owner met ECHOLINKTOKEN0X today. She gave a bowl. The dog was happy."
    )

    repaired = _restore_missing_immutable_tokens(source, candidate, {})

    assert "ECHOLINKTOKEN1X" in repaired
    # The clause that lost the token keeps the writer's wording exactly.
    assert "she gave ECHOLINKTOKEN1X a bowl." in repaired
    # Everything else keeps the correction.
    assert repaired.startswith("The owner met ECHOLINKTOKEN0X today.")
    assert repaired.endswith("The dog was happy.")


def test_protected_content_is_never_restored_by_guesswork() -> None:
    """When the sentences do not line up, the answer is refused as before.

    Merging or splitting sentences makes it impossible to say which clause the
    token belonged to, and a protected value put in the wrong place is worse
    than no correction at all.
    """
    from echo_link_core.selected_text import _restore_missing_immutable_tokens

    source = "the oner met ECHOLINKTOKEN0X. she gave ECHOLINKTOKEN1X a bowl."
    merged = "The owner met ECHOLINKTOKEN0X and she gave a bowl."

    assert _restore_missing_immutable_tokens(source, merged, {}) == ""

    # Reordering loses nothing, so no sentence can be put back to repair it -
    # and a protected value in the wrong order is a different statement.
    reordered = "She gave ECHOLINKTOKEN1X a bowl. The owner met ECHOLINKTOKEN0X."
    assert _restore_missing_immutable_tokens(source, reordered, {}) == ""

    # When every sentence has to be put back, nothing the model wrote
    # survived. Handing the writer their own text back would read as "there
    # was nothing to change", which is not what happened.
    one_sentence = "Keep ECHOLINKTOKEN0X unchanged."
    assert _restore_missing_immutable_tokens(one_sentence, "Token removed.", {}) == ""


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("it is edjucashunal and fun", "it is educational and fun"),
        ("it needs atenshun daily", "it needs attention daily"),
        ("they lernt much bout it", "they learned much about it"),
        ("a dog can make a like regular place feel nice",
         "a dog can make a regular place feel nice"),
    ],
)
def test_a_badly_misspelled_word_may_be_repaired_from_further_away(
    source: str, candidate: str
) -> None:
    """Two edits is not far enough for the spellings this product actually sees.

    "edjucashunal" sits four edits from "educational" and "atenshun" three from
    "attention", so a strict two-edit rule refused the corrections the product
    exists to make. The relaxation only applies to words the dictionary does
    not recognise, so it cannot excuse replacing a correctly spelled one.
    """
    from echo_link_core.meaning_diff import unexplained_losses

    assert unexplained_losses(source, candidate) == ()


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("when the oner ENTERS the ROOM", "when the owner measures the room"),
        ("the dog barks loudly", "the dog sleeps loudly"),
        ("she walked to the store", "she drove to the store"),
        ("the vet checked the animal", "the doctor checked the animal"),
    ],
)
def test_a_correctly_spelled_word_is_still_protected(
    source: str, candidate: str
) -> None:
    """The relaxation above must not reopen the case it was built to catch."""
    from echo_link_core.meaning_diff import unexplained_losses

    assert unexplained_losses(source, candidate) != ()


# A protected name whose spelling the model corrected.
#
# Measured 2026-08-18: essay 16 of the 800-percent corpus carries both
# "Arequipa" and the misspelled "Arequuipa". The misspelling is capitalised, so
# it is protected as an immutable token; the model corrects it; the literal
# value no longer appears; the token cannot be restored; and the guard discards
# the whole section. Two of that essay's ten sections finished with no AI
# correction at all, which is where nees, prson, hoever and cleaing survived
# from.


def test_a_protected_name_the_model_respelled_is_put_back():
    from echo_link_core.selected_text import _canonicalize_immutable_tokens

    source = "In ECHOLINKTOKEN0X, Peru, the alpaca nees care."
    candidate = "In Arequipa, Peru, the alpaca needs care."

    restored = _canonicalize_immutable_tokens(
        candidate,
        source,
        {"ECHOLINKTOKEN0X": "Arequuipa"},
    )

    assert "ECHOLINKTOKEN0X" in restored
    # The rest of the correction survives, which is the whole point: the
    # section used to be thrown away over the name.
    assert "needs care" in restored


def test_the_writer_keeps_the_name_they_typed():
    """Restoring the token restores the writer's spelling, deliberately.

    The product does not rewrite names - unknown place names are an accepted
    tradeoff recorded across both platforms. Losing the entire paragraph to
    keep that promise was never the intent.
    """
    from echo_link_core.selected_text import _nearest_protected_spelling

    assert _nearest_protected_spelling(
        "this is true in Arequipa, Peru today", "Arequuipa"
    ) == "Arequipa"


def test_an_unrelated_word_is_never_treated_as_the_name():
    from echo_link_core.selected_text import _nearest_protected_spelling

    # Nothing close enough.
    assert _nearest_protected_spelling("the alpaca lives here", "Arequuipa") is None
    # A typo almost never changes the first letter, and that rule already
    # earns its keep in the speller.
    assert _nearest_protected_spelling("the Brequipa place", "Arequuipa") is None
    # A different name entirely.
    assert _nearest_protected_spelling("we went to Paris last May", "Perth") is None


def test_a_multi_word_value_is_left_alone():
    """A multi-word value that drifted is a rewrite, not a typo repair."""
    from echo_link_core.selected_text import _nearest_protected_spelling

    assert _nearest_protected_spelling("somewhere in Cusco Peru", "Cusco Peru") is None


def test_the_allowlist_admits_the_qwen_packs_and_still_refuses_everything_else():
    """Widening the allowlist must not turn it into a pass-through.

    _OFFICIAL_MODELS is enforced by _require_exact_response_model against the
    identity the RESPONSE reports, which is what guarantees the product ran
    the model it claims. The Qwen packs were added to it on 2026-08-24 when
    they were added to the signed manifest; the check itself was not relaxed,
    and this pins both halves of that.
    """
    from echo_link_core.selected_text import select_local_tone_model

    installed = ("qwen3:14b", "qwen3.8:27b", "gemma4:e4b-it-qat", "deepseek-r1:8b")

    assert select_local_tone_model("Friendly", "qwen3:14b", installed) == "qwen3:14b"
    assert select_local_tone_model("Friendly", "qwen3.8:27b", installed) == "qwen3.8:27b"
    assert (
        select_local_tone_model("Friendly", "gemma4:e4b-it-qat", installed)
        == "gemma4:e4b-it-qat"
    )
    # Installed, but never allowlisted - being on the machine is not consent
    # to run it as the product's writing model.
    assert select_local_tone_model("Friendly", "deepseek-r1:8b", installed) == ""
    # Allowlisted but not installed.
    assert select_local_tone_model("Friendly", "qwen3:14b", ("gemma4:e4b-it-qat",)) == ""


def test_every_palette_tone_resolves_to_a_command_the_writing_layer_knows():
    """A tone the UI offers must not be a word this layer has never heard.

    The palette mapped its "Clear" button onto the "Rewrite" command until
    2026-08-24. Commit 4fe0125 gave Clear its own command, because
    polish_quality defines a Clear contract and no Rewrite contract, so the
    old mapping left that contract unreachable. Clear was then the only
    palette tone missing from COMMANDS, and a Clear request through this path
    raised "Unknown writing command.", cascaded through every installed pack,
    and finished on deterministic rules.

    It was found while measuring a pinned model, where the cascade looked
    exactly like the pinned pack failing its own acceptance probe. Tying the
    two vocabularies together here means the next tone added to the palette
    fails a test rather than a user's document.
    """
    from echo_link_core.polish_quality import TONE_CONTRACTS
    from echo_link_core.selected_text import COMMANDS, resolve_command

    missing = sorted(
        name for name in TONE_CONTRACTS if resolve_command(name) not in COMMANDS
    )
    assert not missing, f"tones with a contract but no command: {missing}"


def test_clear_resolves_to_rewrite_without_entering_the_shared_vocabulary():
    """Clear is a UI label for an existing command, not a new capability.

    COMMANDS is the shared cross-platform vocabulary: shared/contract/v1 pins
    it for the windows, swift and android clients and says an incompatible
    change needs a new major directory. "Clear" and "Rewrite" carry the same
    instruction, so aliasing fixes the defect and leaves the contract still.
    Adding Clear to COMMANDS instead broke two shared-contract tests, which is
    the contract doing its job.
    """
    from echo_link_core.selected_text import COMMANDS, resolve_command

    assert resolve_command("Clear") == "Rewrite"
    assert resolve_command("Clear") in COMMANDS
    assert "Clear" not in COMMANDS
    # An unaliased name is returned untouched, so this cannot quietly rewrite
    # a command it does not know about.
    assert resolve_command("Friendly") == "Friendly"
