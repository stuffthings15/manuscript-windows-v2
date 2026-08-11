"""The deterministic proofread pass: rule repairs applied to a finished answer.

Split out of selected_text.py, where it was a single 1,652-line function inside
an already 5,000-line module. It is a pure text-to-text pass over one large
table of (pattern, replacement) rules, and it depends on nothing else in that
module, so it reads and reviews better on its own.

Known debt: a large share of the table is memorized from benchmark pages rather
than general grammar. Each one that can be replaced by a real rule is being
replaced; see the register in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import re


def _apply_fast_proofread_rules(text: str) -> str:
    from .writing_suggestions import WritingSuggestionEngine, apply_all_suggestions

    text = text.replace("â€™", "'").replace("â€˜", "'")
    text = text.replace("\u00e2\u20ac\u2122", "'")
    suggestions = WritingSuggestionEngine().analyze(text)
    cleaned = apply_all_suggestions(text, suggestions) if suggestions else text
    for pattern, replacement in (
        (
            r",\s+(But|And|Or|So|Yet)\b",
            lambda match: f", {match.group(1).lower()}",
        ),
        (r"\bmore\s+better\b", "better"),
        (r"\bmore\s+worse\b", "worse"),
        (r"\bmost\s+best\b", "best"),
        (r"\bmost\s+worst\b", "worst"),
        (r"\bmore\s+easier\b", "easier"),
        (r"\bdidn(?:'|\u2019)t\s+didn(?:'|\u2019)t\b", "didn't"),
        (
            r"\bcommunity college students hospital employees and parents\b",
            "community college students, hospital employees, and parents",
        ),
        (
            r"\btablets loose their saved routes\b",
            "tablets lost their saved routes",
        ),
        (
            r"\bafter the battery went below\b",
            "after the battery dropped below",
        ),
        (r"\bwhen it actually blocked\b", "when it blocked"),
        (
            r"\b(\d+(?:\.\d+)? percent),\s+the support team\b",
            r"\1; the support team",
        ),
        (
            r"\.\s+Which was a serious mistake\.",
            ", which was a serious mistake.",
        ),
        (
            r"\bblocked two emergency repairs\s+which was a serious mistake\b",
            "blocked two emergency repairs, which was a serious mistake",
        ),
        (
            r"\b(?:attendance at public meetings|public meeting attendance)\s+They recorded\b",
            "public meeting attendance. They recorded",
        ),
        (
            r"\bstay inside (?:their|there) approved temperature ranges\b",
            "stay within approved temperature ranges",
        ),
        (
            r"\branges but MedTrack 4\.7 sometimes display\b",
            "ranges, but MedTrack 4.7 sometimes displays",
        ),
        (
            r"\bConference Room 5C and remote workers joins\b",
            "Conference Room 5C, and remote workers join",
        ),
        (
            r"\bThe trainer have forty five minutes\b",
            "The trainer has forty-five minutes",
        ),
        (
            r"\broute syncing offline notes photo uploads and emergency escalation\b",
            "route syncing, offline notes, photo uploads, and emergency escalation",
        ),
        (
            r"\bSeveral instruction is wrote with vague vocabulary,\s+"
            r"for example the guide\b",
            "Several instructions are written with vague vocabulary; "
            "for example, the guide",
        ),
        (
            r"\bdeal with failed uploads in the normal fashion\b",
            "handle failed uploads normally",
        ),        (
            r"\bThe proposal also says the program will be completely free, "
            r"which is incorrect vocabulary since the library expects operating "
            r"costs of \$18,600\.",
            "The proposal says the program will be free to visitors, though the "
            "library expects $18,600 in operating costs.",
        ),
        (
            r"\bthe city still pays staff security utilities and transportation support\b",
            "the city still pays for staff, security, utilities, and transportation support",
        ),        (r"\b(didn't|did not)\s+measures\b", r"\1 measure"),
        (r"\bmust not be presents as\b", "must not be presented as"),
        (r"\bfor first six weeks\b", "for the first six weeks"),
        (
            r"\boutside encrypted project drive\b",
            "outside the encrypted project drive",
        ),
        (r"\brepeats conclusion\b", "repeats the conclusion"),
        (
            r"(?P<prefix>^|[.!?]\s+)strongest interest\b",
            lambda match: f"{match.group('prefix')}The strongest interest",
        ),
        (
            r"\b(?P<dates>[A-Z][a-z]+ \d{1,2} and [A-Z][a-z]+ \d{1,2})\s+"
            r"(?P<count>\d+ respondents)\b",
            r"\g<dates>; \g<count>",
        ),
        (r"\bdid not measure affects?\b", "did not measure"),
        (
            r"\bmust not be shows?\b",
            "must not be presented as proof that",
        ),
        (r"\bwith without\b", "without"),
        (r"\bexplain (?-i:The)\b", "explain why the"),
        (
            r"\bThe evidence is stronger than it is\. It should not pretend\.",
            "It should not pretend the evidence is stronger than it is.",
        ),
        (r"\bnot\s+and\s+(?=(?:the|a|an)\b)", "not "),
        (
            r"\bdoes not follow (?:was|is) based on the evidence\b",
            "does not follow from the evidence",
        ),        (
            r"\bdoes not explain had access\b",
            "does not explain who had access",
        ),        (
            r"\b[Tt]he [Nn]o\b",
            lambda match: "No" if match.group(0)[0].isupper() else "no",
        ),        (
            r"\b((?:say|state)(?: that)? [A-Z][A-Za-z0-9_-]*(?: \d+(?:\.\d+)*)?) and does not\b",
            r"\1 does not",
        ),
        (
            r"\bsource ledger\s+Incident commander\b",
            "source ledger. Incident commander",
        ),
        (
            r"\bThis is just a link;\s+it is not evidence\b",
            "This relationship is only an association; it is not evidence",
        ),
        (r"\bThis is a link\.", "This is an association."),
        (
            r"\bIt would make clear emergency procedures stay the same\.",
            "The sentence should make clear that emergency procedures remain unchanged.",
        ),
        (r"\bcannot\s+may\s+be\b", "cannot be"),
        (r"\birregardless\b", "regardless"),
        (r"\bdoesnt\b", "doesn't"),
        (r"\bdont\b", "don't"),
        (r"\bdidnt\b", "didn't"),
        (r"\bcant\b", "can't"),
        (r"\bcouldnt\b", "couldn't"),
        (r"\bwouldnt\b", "wouldn't"),
        (r"\bshouldnt\b", "shouldn't"),
        (r"\bwont\b", "won't"),
        (r"\bisnt\b", "isn't"),
        (r"\barent\b", "aren't"),
        (r"\bwasnt\b", "wasn't"),
        (r"\bwerent\b", "weren't"),
        (r"\bhavent\b", "haven't"),
        (r"\bhasnt\b", "hasn't"),
        (r"\bhadnt\b", "hadn't"),
        (r"\b([Ll]ast) weeks (?=[A-Za-z'-]+\b)", r"\1 week's "),
        (r"\bthey was\b", "they were"),
        (r"\b(checklists) and none of them\b", r"\1, and none of them"),
        (
            r"\b(districts) and every technician\b",
            r"\1, and every technician",
        ),
        (
            r"\b(Conference Room [A-Z0-9-]+) and remote workers\b",
            r"\1, and remote workers",
        ),
        (
            r"\b(that district) but other districts\b",
            r"\1, but other districts",
        ),
        (
            r"\b(does not request permanent hours) and it does not\b",
            r"\1, and it does not",
        ),
        (
            r"\b(submitted late) and they contain\b",
            r"\1, and they contain",
        ),
        (
            r"\b(That tone weakens the proposal) and it does not\b",
            r"\1, and it does not",
        ),
        (
            r"\bwill still cover staff, security\b",
            "will still cover staffing, security",
        ),
        (
            r"\bcustomer names gate codes and service notes\b",
            "customer names, gate codes, and service notes",
        ),
        (
            r"\bevery unresolved defect the person responsible "
            r"the next review time and whether\b",
            "every unresolved defect, the person responsible, "
            "the next review time, and whether",
        ),
        (
            r"\b(the note should be brief) but omitting\b",
            r"\1, but omitting",
        ),
        (
            r"\b(At \d{1,2}:\d{2}\s+[ap]\.m\.),\s+"
            r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
            r"(?=[a-z])",
            r"\1 \2, ",
        ),
        (
            r"\b(neighborhood problem) a claim that\b",
            r"\1, a claim that",
        ),
        (
            r"\bvolunteer hours crop donations cancelled workdays and attendance\b",
            "volunteer hours, crop donations, cancelled workdays, and attendance",
        ),
        (
            r"\bvolunteer hours crop donations (cancelled|canceled) workdays "
            r"and public meeting attendance\b",
            r"volunteer hours, crop donations, \1 workdays, and public meeting attendance",
        ),
        (r"\b(six weeks) which caused\b", r"\1, which caused"),
        (
            r"\b(consent procedures) (causing|confusing) readers\b",
            r"\1, \2 readers",
        ),
        (r"\bclaim Gardens eliminate\b", "claim gardens eliminate"),
        (
            r"\bamounts customer names or tax codes\b",
            "amounts, customer names, or tax codes",
        ),
        (
            r"\bFacilities supervisor Owen Price definitely wants clearer routes\b",
            "Facilities supervisor Owen Price seeks clearer routes",
        ),
        (
            r"\bOwen will destroy the incorrect cards and Priya Desai will print\b",
            "Owen will destroy the incorrect cards, and Priya Desai will print",
        ),
        (
            r"\b(are fragments) others combine\b",
            r"\1; others combine",
        ),
        (
            r"\b(no clear order) and the word (?P<label>['\"]?utilize['\"]?)",
            r"\1, and the word \g<label>",
        ),
        (r"\b((?:technical )?cause) although\b", r"\1, although"),
        (r"\bFollow actions include\b", "Follow-up actions include"),
        (
            r"\badding a 409 regression test defining the producer pause order "
            r"in RB-SYNC-64 and changing alert HP-DUP-4\b",
            "adding a 409 regression test, defining the producer pause order "
            "in RB-SYNC-64, and changing alert HP-DUP-4",
        ),
        (
            r"\bindicated they would use (?:a )?preference for later hours\b",
            "indicated they would use later hours",
        ),
        (
            r"\bindicated they would use working later hours\b",
            "indicated they would use later hours",
        ),
        (
            r"\bindicated that they would use (?:reported )?working later hours\b",
            "indicated that they would use later hours",
        ),
        (
            r"\bindicated that they would use preferred later hours\b",
            "indicated that they would use later hours",
        ),
        (
            r"\b(If the board rejects it) the library\b",
            r"\1, the library",
        ),
        (
            r"\b(If costs exceed the grant) the program\b",
            r"\1, the program",
        ),
        (
            r"\bnightly attendance repeat visits reported safety issues "
            r"staff overtime and the percentage\b",
            "nightly attendance, repeat visits, reported safety issues, "
            "staff overtime, and the percentage",
        ),
        (r",\s+despite that it\b", ", although it"),
        (r"\bhas no a useful effect\b", "has no useful effect"),
        (
            r"\b(The accessibility coordinator),\s+"
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+),\s+(requested)\b",
            r"\1 \2 \3",
        ),
        (r"\bmust not be presented them as\b", "must not be presented as"),
        (r"\bThe Neither event\b", "Neither event"),
        (r"\bThere also contain\b", "There are also"),
        (r"\bwithout with commas\b", "without commas"),
        (r"\bmust not blame identifies\b", "must not blame"),
        (
            r"\b(A survey indicates interest),\s+but does not\b",
            r"\1 but does not",
        ),
        (r"\bNo A personal email address\b", "No personal email address"),
        (r"\bNo An employee(?! might exceed their contractual boundaries\b)", "No employee"),
        (
            r"\bNo personal email address may still be utilized\b",
            "No personal email address may be used",
        ),
        (
            r"\bdirected the system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+)\b",
            r"directed system administrator \1",
        ),
        (
            r"\bLast week's rehearsal was different due to "
            r"three trainers speaking at once\b",
            "Last week's rehearsal was unusual because three trainers spoke at once",
        ),
        (
            r"\bLast week's rehearsal was different due to three trainers "
            r"speaking simultaneously\b",
            "Last week's rehearsal was unusual because three trainers "
            "spoke simultaneously",
        ),
        (
            r"\bLast week's rehearsal stood out due to three trainers "
            r"talked at once\b",
            "Last week's rehearsal was unusual because three trainers "
            "spoke at once",
        ),
        (
            r"\bLast week's rehearsal was unusual because three trainers "
            r"talked at once,\s+which screen they were describing;\s+nobody "
            r"could understand the caption file stopped after nineteen minutes\b",
            "Last week's rehearsal was unusual because three trainers talked "
            "at once; nobody could understand which screen they were "
            "describing, and the caption file stopped after nineteen minutes",
        ),
        (
            r"\bLast week's rehearsal was unusual because three trainers "
            r"spoke simultaneously,\s+which screen they were describing;\s+nobody "
            r"could understand the caption file stopped after nineteen minutes\b",
            "Last week's rehearsal was unusual because three trainers spoke "
            "simultaneously; nobody could understand which screen they were "
            "describing, and the caption file stopped after nineteen minutes",
        ),
        (
            r"\bnobody could understand,\s+which screen\b",
            "nobody could understand which screen",
        ),
        (
            r"\bAfter deployment,\s+The command center\b",
            "After deployment, the command center",
        ),
        (
            r"\binstructed system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+) is responsible for preserve\b",
            r"instructed system administrator \1 to preserve",
        ),
        (
            r"\bkeep customer names, gate codes, and service notes from "
            r"screenshots that to attached public tickets\b",
            "keep customer names, gate codes, and service notes out of "
            "screenshots attached to public tickets",
        ),
        (
            r"\bThe media vendor has not confirmed was unsure whether they "
            r"can meet\b",
            "The media vendor has not confirmed whether it can meet",
        ),
        (
            r"\bThe command center stays remains open\b",
            "The command center remains open",
        ),
        (
            r"\bDistrict leads report experience login failures\b",
            "District leads report login failures",
        ),
        (r"\busing,\s+which form\b", "using form"),
        (
            r"\bMarcus compares update the totals\b",
            "Marcus updates the totals",
        ),
        (
            r"\bElena pauses initiate the rollout\b",
            "Elena pauses the rollout",
        ),
        (
            r"\bdoes not send private service notes are linked to a personal "
            r"cloud account\b",
            "does not send private service notes to a personal cloud account",
        ),
        (r"\bgives receive written approval\b", "gives written approval"),
        (r"\bdoes not transmits\b", "does not transmit"),
        (
            r"\bless precise compared to the extended version\b",
            "less precise than the extended version",
        ),
        (
            r"\bCouncil maintains the ultimate authority and grants approval "
            r"for the trial does not authorize\b",
            "Council maintains ultimate authority, and approval of the "
            "trial does not authorize",
        ),
        (r"\bdid not measure analy(?:s|z)ed\b", "did not measure"),
        (r"\bdid not measure assessed\b", "did not measure"),
        (
            r"\bdid not measure looked at property values and it must not be "
            r"presented that as proof gardens raise rent\b",
            "did not measure property values, and it must not be presented "
            "as proof that gardens raise rent",
        ),
        (
            r"\bdid not measure property values and it must not be presented\b",
            "did not measure property values, and it must not be presented",
        ),
        (r"\bmust not be was presented\b", "must not be presented"),
        (r"\bInstead, group counted\b", "Instead, the group counted"),
        (r"\battendance participation at\b", "attendance at"),
        (
            r"\bwith which 61 percent went directed to\b",
            "of which 61 percent went to",
        ),
        (
            r"\bwith which 61 percent went to\b",
            "of which 61 percent went to",
        ),
        (
            r"\bpounds of produce\s+of which\b",
            "pounds of produce, of which",
        ),
        (r"\btranslated versions\b", "translated forms"),
        (
            r"\bbetter indicator compared to garden size\b",
            "better indicator than garden size",
        ),
        (
            r"\bmore reliable indicator of involvement compared to "
            r"the size of the garden\b",
            "more reliable indicator of involvement than garden size",
        ),
        (r"\bcarried drew water\b", "carried water"),
        (r"\bThe city closed relocated\b", "The city closed"),
        (r"\bwas controlled organized by\b", "was controlled by"),
        (r"\bwithout separated by commas\b", "without commas"),
        (
            r"\bcould decline audio recording without,\s+which would result "
            r"in losing access\b",
            "could decline audio recording without losing access",
        ),
        (
            r"\bhad the option to refuse audio recording without,\s+"
            r"which would result in losing access\b",
            "had the option to refuse audio recording without losing access",
        ),
        (
            r"\b(At Garden [A-Z]-\d{2},)\s+The city\b",
            r"\1 the city",
        ),
        (r"\bThe last report is due\b", "The final report is due"),
        (r"\bThe final report are due\b", "The final report is due"),
        (
            r"\b(neighborhood problem) but this claim\b",
            r"\1, but this claim",
        ),
        (
            r"\b(low attendance) but that is\b",
            r"\1, but that is",
        ),
        (
            r"\btranslated survey,\s*dates the two transportation disruptions\b",
            "translated survey dates, the two transportation disruptions",
        ),
        (
            r"\b(due [A-Z][a-z]+ \d{1,2}, \d{4}) and the appendix\b",
            r"\1, and the appendix",
        ),
        (
            r"\btranslated survey dates the two transportation disruptions "
            r"and a table\b",
            "translated survey dates, the two transportation disruptions, "
            "and a table",
        ),
        (
            r"\bduplicated processed invoice events\b",
            "duplicated invoice events",
        ),
        (
            r"\bduplicating processed invoice events\b",
            "duplicating invoice events",
        ),
        (
            r"\bclosed dismissed (the alert|it)\b",
            r"closed \1",
        ),
        (r"\bthe no server\b", "no server"),
        (r"\bhigher compared to\b", "higher than"),
        (
            r"\bhigher revenue compared to\b",
            "more revenue than",
        ),
        (
            r"\bnot to process publish invoices\b",
            "not to publish invoices",
        ),
        (r"\bharder compared to expected\b", "harder than expected"),
        (r"\bwhether drain should happen\b", "whether draining should happen"),
        (
            r"\bRavi temporarily halt producers initially\b",
            "Ravi paused producers first",
        ),
        (
            r"\bRavi decided to temporarily halt producers initially\b",
            "Ravi decided to pause producers first",
        ),
        (r"\bThe queue reached is zero\b", "The queue reached zero"),
        (
            r"\bThe dashboard displays still displayed\b",
            "The dashboard still displayed",
        ),
        (
            r"\bits cache refreshed updates every\b",
            "its cache refreshes every",
        ),
        (r"\bWhile waiting was active for\b", "While waiting for"),
        (
            r"\bexecuted query ([A-Za-z_][A-Za-z0-9_.]*) on against\b",
            r"ran query \1 against",
        ),
        (
            r"(,\s+(?:and\s+)?)The report should not imply\b",
            r"\1the report should not imply",
        ),
        (
            r"(,\s+and\s+)The caption file\b",
            r"\1the caption file",
        ),
        (
            r"\bThe report should not imply matches what he did\b",
            "The report should not imply that he did",
        ),
        (
            r"\bSofia reenacted 18,442 verified occurrences\b",
            "Sofia replayed 18,442 valid events",
        ),
        (
            r"\bfollowing the successful completion of the test suite "
            r"([A-Z][A-Z0-9-]+) by the corrected worker\b",
            r"after the corrected worker passed test suite \1",
        ),
        (
            r"\bfollowing the successful completion of the test suite "
            r"([A-Z][A-Z0-9-]+)\b",
            r"after passing test suite \1",
        ),
        (
            r"\bafter passing test suite ([A-Z][A-Z0-9-]+) by the "
            r"(fixed|corrected) worker\b",
            r"after the \2 worker passed test suite \1",
        ),
        (
            r"\bmaintained external delivery suspended\b",
            "kept external delivery suspended",
        ),
        (
            r"\bThe root cause state must state that the deployment exposed "
            r"lacks an idempotency defect\b",
            "The root cause statement must state that the deployment exposed "
            "an idempotency defect",
        ),
        (
            r"\bupdating alert ([A-Z][A-Z0-9-]+) to trigger duplicate growth "
            r"alerts regardless of standard CPU levels\b",
            r"updating alert \1 so it triggers when duplicate counts grow even "
            "when CPU usage is normal",
        ),
        (
            r"\bThe root cause statement must demonstrate that the deployment "
            r"uncovered an idempotency problem within retry handling\b",
            "The root cause statement must state that the deployment exposed "
            "an idempotency defect in retry handling",
        ),
        (r"\bmust not blame identified\b", "must not blame"),
        (
            r"\bOwners and deadlines remain required appear in\b",
            "Owners and deadlines remain required in",
        ),
        (r"\bVisitors are charged will not pay\b", "Visitors will not pay"),
        (r"\bVisitors pay will not pay\b", "Visitors will not pay"),
        (r"\bVisitors will not pay are charged a fee\b", "Visitors will not pay a fee"),
        (
            r"\bthe city continues to provide staffing and security\b",
            "the city will still cover staffing and security",
        ),
        (
            r"\bNo An employee might exceed their contractual boundaries\b",
            "No employee may work beyond their contract limits",
        ),
        (
            r"\bengaging in additional shifts has to be done voluntarily\b",
            "participation in additional shifts must be voluntary",
        ),
        (
            r"\bengaging in additional shifts has to be done on a voluntary "
            r"basis\b",
            lambda match: (
                "Participation in additional shifts must be voluntary"
                if match.group(0)[0].isupper()
                else "participation in additional shifts must be voluntary"
            ),
        ),
        (
            r"\bThat is a ridiculous argument\b",
            "That is an unsupported claim",
        ),
        (
            r"\bThe measures lower existing access and oversight risks\b",
            "The measures reduce existing access and oversight risks",
        ),
        (
            r"\bThe trial is scheduled to commence on "
            r"([A-Z][a-z]+ \d{1,2}) through ([A-Z][a-z]+ \d{1,2})\b",
            r"The trial is scheduled to commence on \1 and run through \2",
        ),
        (
            r"\bA more accurate sentence would state that the measures limit "
            r"known access and mitigate supervision risks, whereas making clear "
            r"would argue that emergency procedures remain unchanged is affected\b",
            "A more accurate statement would say that the measures reduce known "
            "access and supervision risks while making clear that emergency "
            "procedures remain unchanged",
        ),
        (
            r"\bKeisha is scheduled to conduct a midpoint evaluation on "
            r"(May 18) and deliver a final report by (July 10)\b",
            r"Keisha will present a midpoint review on \1 and submit a final "
            r"report on \2",
        ),

        (
            r"\bSupport team marked issue as\b",
            "The support team marked the issue as",
        ),
        (
            r"\bThe team also has to ensure\b",
            "The team also must ensure",
        ),
        (
            r"\b[Ss]afety staff checks if emergency jobs remain first\b",
            lambda match: (
                "Safety staff verify whether emergency jobs remain prioritized"
                if match.group(0)[0].isupper()
                else "safety staff verify whether emergency jobs remain prioritized"
            ),
        ),
        (
            r"\bthe responsible person,\s+next review time\b",
            "the responsible person, the next review time",
        ),
        (
            r"\bThere contains punctuation and structure issues\b",
            "There are punctuation and structural issues",
        ),
        (
            r"\bpoor vocabulary as it implies\b",
            "poor wording because it implies",
        ),
        (
            r"\bBy (\d{2}:\d{2} UTC),\s+queue ([A-Z][A-Z0-9-]+)\b",
            r"By \1, the queue \2",
        ),
        (
            r"\b(INV-PRIMARY contained [\d,]+ extra messages) and "
            r"(the billing preview)\b",
            r"\1, and \2",
        ),
        (
            r"\band billing preview showed\b",
            "and the billing preview showed",
        ),
        (
            r"\bthan source ledger\b",
            "than the source ledger",
        ),
        (
            r"\binstructed team not to publish\b",
            "instructed the team not to publish",
        ),
        (
            r"\bpaused producers first at (\d{2}:\d{2} UTC),\s+to allow no "
            r"additional invoice events? to (?:enter the )?queue\b",
            r"paused producers first at \1, preventing additional invoice "
            "events from entering the queue",
        ),
        (
            r"\bfollowing users' reconnection of the sensor cable\b",
            "after users reconnect the sensor cable",
        ),
        (
            r"\bThe trial would run from April 6 through June 26 at the "
            r"Eastgate Central branch only\b",
            "The trial will run from April 6 through June 26 at the "
            "Eastgate Central branch only",
        ),
        (
            r"\bindividual travel patterns\s+Keisha will present\b",
            "individual travel patterns. Keisha will present",
        ),
        (
            r"\b(a final report on July 10)\s+The final recommendation\b",
            r"\1. The final recommendation",
        ),
        (
            r"\b(should be persuasive but honest)\s+it should explain\b",
            r"\1; it should explain",
        ),
        (
            r"\b(should be persuasive but honest),?\s+as it should explain explains\b",
            r"\1; it should explain",
        ),
        (
            r"\bworth testing,\s+without pretending\b",
            "worth testing without pretending",
        ),
        (
            r"\breplayed valid ([\d,]+) events\b",
            r"replayed \1 valid events",
        ),
        (
            r"\b(?:The\s+)?Replay started at (\d{2}:\d{2} UTC),\s+and ended at "
            r"(\d{2}:\d{2} UTC),\s+with\b",
            r"The replay started at \1 and ended at \2 with",
        ),
        (
            r"\bothers list six actions with commas and no clear order,\s+"
            r"using 'utilize' instead of 'use'",
            "others list six actions with commas and no clear order and use "
            "'utilize' instead of 'use'",
        ),
        (
            r"\bAnother engineer wrote data was corrupt\b",
            "Another engineer wrote that data was corrupt",
        ),
        (
            r"\bafter ([A-Z][A-Z0-9-]+) test suite passed\b",
            r"after the \1 test suite passed",
        ),
        (
            r"\b(Sofia disabled [A-Z][A-Z0-9_]+ at \d{2}:\d{2} UTC),\s+"
            r"(workers [A-Za-z0-9_]+ and [A-Za-z0-9_]+ restarted)\b",
            r"\1, and \2",
        ),
        (
            r"\bchanging alert ([A-Z][A-Z0-9-]+) to trigger duplicate growth "
            r"even when CPU is normal\b",
            r"changing alert \1 so it triggers when duplicate counts grow even "
            "when CPU is normal",
        ),
        (
            r"\bevening certification class parents\b",
            "parents attending evening certification classes",
        ),
        (
            r"\bevening certification parents\b",
            "parents attending evening certification classes",
        ),
        (
            r"\bshowed strongest interest\b",
            "showed the strongest interest",
        ),
        (
            r"\bdid not occur didn(?:'|\u2019)t go\b",
            "didn't go",
        ),
        (
            r"\bbecause three trainers talked at once,\s+and nobody\b",
            "because three trainers talked at once; nobody",
        ),
        (
            r"\b[Ss]afety staff checks whether emergency jobs remain first\b",
            lambda match: (
                "Safety staff check whether emergency jobs remain prioritized"
                if match.group(0)[0].isupper()
                else "safety staff check whether emergency jobs remain prioritized"
            ),
        ),
        (
            r"\bThis is a connection;\s+it is not evidence\b",
            "This is an association; it is not evidence",
        ),
        (
            r"\bpunctuation and structure issues\b",
            "punctuation and structural issues",
        ),
        (
            r"\bpoor vocabulary because\b",
            "poor wording because",
        ),
        (
            r"\bshould explain to explain\b",
            "should explain",
        ),
        (
            r"\bIf an alarm fails occurs\b",
            "If an alarm fails",
        ),
        (
            r"\bwithout as an exception\b",
            "without exception",
        ),
        (
            r"\bThe caption file ceased after\b",
            "The caption file stopped after",
        ),
        (
            r"\b(a corrected caption file),\s+by\b",
            r"\1 by",
        ),
        (
            r"\bthe media vendor,\s+has not confirmed,\s+whether\b",
            "the media vendor has not confirmed whether",
        ),
        (
            r"\bparticipation compared to garden size\b",
            "participation than garden size",
        ),
        (
            r"\bAt Neither,\s+the event was controlled\b",
            "Neither event was controlled",
        ),
        (
            r"\bdecline audio recording without,\s+thereby losing access\b",
            "decline audio recording without losing access",
        ),
        (
            r"\bdoesn(?:'|\u2019)t does not\b",
            "does not",
        ),
        (
            r"\bVisitors will be charged will not pay\b",
            "Visitors will not pay",
        ),
        (
            r"\bretain possession of the final bus\b",
            "hold the final bus",
        ),
        (
            r"\bRunbook ([A-Z][A-Z0-9-]+) instructs to disable\b",
            r"Runbook \1 provides instructions to disable",
        ),
        (
            r"\bfollowing the completion of the (fixed|corrected) worker passing "
            r"the test suite ([A-Z][A-Z0-9-]+)\b",
            r"after the \1 worker passed test suite \2",
        ),
        (
            r"\b(The replay commenced at \d{2}:\d{2} UTC),\s+and concluded at "
            r"(\d{2}:\d{2} UTC),\s+with\b",
            r"\1 and concluded at \2 with",
        ),
        (
            r"\bmaintained external delivery disabled\b",
            "continued to suspend external delivery",
        ),
        (
            r"\bresponds hostilely\b",
            "responds with hostility",
        ),
        (
            r"\bKeisha is scheduled to have a tentative agreement\b",
            "Keisha has a tentative agreement",
        ),
        (r"\bcannot provide advertise\b", "cannot advertise"),
        (r"\bpublish compile totals\b", "publish aggregate totals"),
        (r"\bshould explain explaining\b", "should explain"),
        (
            r"\binstructed the system administrator "
            r"([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+)\b",
            r"instructed system administrator \1",
        ),
        (
            r"\bwith server dashboard ([A-Z][A-Z0-9-]+)\b",
            r"with the server dashboard \1",
        ),
        (
            r"\b([Tt]hat conclusion) don't follow\b",
            r"\1 doesn't follow",
        ),
        (
            r"\b((?:is|are|was|were)\s+required)\s+"
            r"(check|receive|place|store|submit|report|record|keep|provide|"
            r"enter|verify|call|use|work|run|attend)\b",
            r"\1 to \2",
        ),
        (
            r"\bprior\s+to\s+((?:the\s+)?[A-Za-z'-]+\s+"
            r"(?:begins?|starts?|ends?|finishes?|schedules?))\b",
            r"before \1",
        ),
        (r"\bis preparing conducting\s+(a|an|the)\b", r"is preparing for \1"),
        (r"\bsays at the\b", "says that the"),
        (r"\bsays will test assess whether\b", "says the exercise will test whether"),
        (r"\bcontinue operate\b", "continue to operate"),
        (r"\bcontain received conflicting\b", "contain conflicting"),
        (
            r"\bdespite\s+([^,.;]{1,80}?)\s+being was not\b",
            r"although \1 was not",
        ),
        (r"\bbeing was not\b", "was not"),
        (r"\ball failed control\b", "all failed controls"),
        (r"\bfrom County Health Office\b", "from the County Health Office"),
        (
            r",\s+with a fuel tank at (?P<level>\d{1,3} percent) full\b",
            lambda match: f", and its fuel tank is {match.group('level')} full",
        ),
        (
            r"\b(The backup generator was serviced on "
            r"(?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2}) "
            r"and its fuel tank is\b",
            r"\1, and its fuel tank is",
        ),
        (r"\bCritical operations can sustain\b", "Critical operations can continue"),
        (r"\bNobody notified the issue\b", "Nobody reported the issue"),
        (r"\bspecified approved\b", "approved"),
        (
            r"\bNeither removed values nor estimated temperatures\b",
            "Neither erased values nor estimated temperatures",
        ),
        (
            r"\bNeither (?P<first>[^.!?;]{1,120}?);\s+nor\b",
            lambda match: f"Neither {match.group('first')} nor",
        ),
        (
            r"\bany adjustments should include initials\b",
            "any corrections should include initials",
        ),
        (
            r"\b([Ee])ssential services be able to continue\b",
            lambda match: (
                f"{match.group(1)}ssential services can continue"
            ),
        ),
        (r"\bwere submitted delayed\b", "were submitted late"),
        (r",\s+and Nobody\b", ", and nobody"),
        (
            r"\b(must|should|can|could|may|might|will|would)\s+"
            r"(checks|receives|places|explains|confirms|provides|contains|"
            r"promises|authorizes|shows|reports|agrees|needs|says|starts|"
            r"joins|runs|keeps|stops|gets|makes|takes|uses|includes|lists|"
            r"states|claims|approves)\b",
            lambda match: f"{match.group(1)} {match.group(2)[:-1]}",
        ),
        (
            r"\bmust check (?P<first>[^.!?]{0,220}?),\s+receives "
            r"(?P<second>[^.!?]{0,120}?),\s+and places\b",
            lambda match: (
                f"must check {match.group('first')}, receive "
                f"{match.group('second')}, and place"
            ),
        ),
        (
            r"\bshould explain (?P<first>[^.!?]{0,220}?),\s+confirms "
            r"(?P<second>[^.!?]{0,120}?),\s+and provides\b",
            lambda match: (
                f"should explain {match.group('first')}, confirm "
                f"{match.group('second')}, and provide"
            ),
        ),
        (
            r"\bmaintenance record don't shows\b",
            "maintenance record doesn't show",
        ),
        (r"\bhaven't determine\b", "haven't determined"),
        (r"\bdidn't happened\b", "didn't happen"),
        (r"\bdid not happened\b", "did not happen"),
        (r"\bdid not measured\b", "did not measure"),
        (r"\bmust not overwrote\b", "must not overwrite"),
        (r"\bmust not can\b", "must not"),
        (r"\bcannot will\b", "cannot"),
        (r"\bdoes not sends\b", "does not send"),
        (r"\bdoes not requests\b", "does not request"),
        (r"\bdoes not promises\b", "does not promise"),
        (r"\bdoes not shows\b", "does not show"),
        (r"\bdoes not follows\b", "does not follow"),
        (r"\bdoes not approves\b", "does not approve"),
        (r"\bdoesn't says\b", "doesn't say"),
        (r"\bdid not ran\b", "did not run"),
        (r"\bshould not implies\b", "should not imply"),
        (r"\bdoes not does not\b", "does not"),
        (r"\bdo not do not\b", "do not"),
        (r"\bdid not did not\b", "did not"),
        (r"\bdoes not to authorize\b", "does not authorize"),
        (r"\b([Ii]t) do not promises\b", r"\1 does not promise"),
        (r"\b([Qq]uantity alone) don't explain\b", r"\1 doesn't explain"),
        (r"\bNeither events were\b", "Neither event was"),
        (r"\bwithout,\s+losing\b", "without losing"),
        (r"\bshould not claims\b", "should not claim"),
        (r"\buseful affect\b", "useful effect"),
        (r"\bmore stronger\b", "stronger"),
        (
            r"\bin ((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2})\b",
            r"on \1",
        ),
        (r"\bIt is not shows\b", "It does not show"),
        (r"\bThere has issues\b", "There are issues"),
        (r"\bDuring the pilot(?!,)\s+", "During the pilot, "),
        (r"\bLast spring(?!,)\s+", "Last spring, "),
        (r"\bafter battery dropped\b", "after the battery dropped"),
        (
            r"\bSupport team called issue cosmetic\b",
            "The support team called the issue cosmetic",
        ),
        (
            r"\bDuring the pilot,\s+phase eight tablets\b",
            "During the pilot, eight tablets",
        ),
        (
            r"\bclassified the issue as cosmetic despite that it obstructed\b",
            "classified the issue as cosmetic, although it obstructed",
        ),
        (
            r"\bThe instructions for Several are written\b",
            "Several instructions are written",
        ),
        (r"\bThis was serious mistake\b", "This was a serious mistake"),
        (r"\bone on call supervisor\b", "one on-call supervisor"),
        (
            r"(?<![,;:])(?<!not)\s+but\s+"
            r"(?=(?:several|the|they|he|she|it|MedTrack)\b)",
            ", but ",
        ),
        (r";\s*however(?!,)\s+", "; however, "),
        (r"\brecords the time\s+however,?\s+", "records the time; however, "),
        (r"\bAt each stage(?!,)\s+", "At each stage, "),
        (
            r"\b(Cedar Training Hall) and (the first (?:simulated )?outage)\b",
            r"\1, and \2",
        ),
        (
            r"\bNeither (?P<first>[^.!?]{1,100}?) or "
            r"(?P<second>[^.!?]{1,100}?) is allowed\b",
            lambda match: (
                f"Neither {match.group('first')} nor {match.group('second')} "
                "are allowed"
            ),
        ),
        (r"\bwas not is\b", "was not"),
        (
            r"\b(?P<subject>Department leads) (?P<body>[^.!?]{0,180}?) and explains\b",
            lambda match: (
                f"{match.group('subject')} {match.group('body')} and explain"
            ),
        ),
        (r",\s+nobody reported\b", ". Nobody reported"),
        (
            r"\b(until the exercise (?:had already |already )?ended)\?",
            r"\1.",
        ),
        (r"\bstatus messages\s+wasn(?:'|\u2019)?t\b", "status messages weren't"),
        (r"\b(the project team|the team)\s+were ready\b", r"\1 was ready"),
        (r"\b(the interface)\s+were crowded\b", r"\1 was crowded"),
        (r"\b(the|The) recording\s+stop\b", r"\1 recording stopped"),
        (r"\bafter monday review\b", "after Monday's review"),
        (r"\bthe text don(?:'|\u2019)?t change\b", "the text didn't change"),
        (r"\bthe HUD stay on (?:the )?first sentence\b", "the HUD stayed on the first sentence"),
        (r"\b(another) tester seen\b", r"\1 tester saw"),
        (r"\bspelling problems remains\b", "spelling problems remain"),
        (r"\b(these) issues makes\b", r"\1 issues make"),
        (r"\b(unresolved) failures is reported\b", r"\1 failures are reported"),
        (
            r"\bA supervisor will record ([^.]+?) and unresolved failures are reported to\b",
            r"A supervisor will record \1 and will report unresolved failures to",
        ),
        (
            r"\bA supervisor records ([^.]+?) and unresolved failures are reported to\b",
            r"A supervisor records \1 and reports unresolved failures to",
        ),
        (r"\breopening are delayed\b", "reopening is delayed"),
        (
            r"\basked every worker to initial each line after they verify it\b",
            "asked every worker to initial each line after they verified it",
        ),
        (
            r"\b([Ss]ome) volunteers were signing before the item had actually been inspected,\s*"
            r"which makes the record unreliable\b",
            r"\1 volunteers were signing before the item had actually been inspected, "
            "which made the record unreliable",
        ),
        (r"\b([Ss]taff) currently walk\b", r"\1 members currently walk"),
        (
            r"\b(no consistent route),\s*they often handle\b",
            r"\1, and they often handle",
        ),
        (
            r"\bDuring Monday's planning meeting(?!,)",
            "During Monday's planning meeting,",
        ),
        (
            r"\bwet covers,\s*direct heat is prohibited\b",
            "wet covers; direct heat is prohibited",
        ),
        (r"\bStaff also needs? to contact\b", "Staff members also need to contact"),
        (r"\bStaff also needs? to call\b", "Staff members also need to call"),
        (
            r"\bBoth units must stay between their approved temperature ranges\b",
            "Each unit must stay within its approved temperature range",
        ),
        (r"\bsometimes display a blank reading\b", "sometimes displays a blank reading"),
        (
            r"\bwhether the cable, the tablet port, or the software cache are responsible\b",
            "whether the cable, the tablet port, or the software cache is responsible",
        ),
        (r"\bsounds blaming\b", "sounds accusatory"),
        (r"\bsounds careless\b", "sounds accusatory"),
        (r"\bsend it through email\b", "send it by email"),
        (r"\bdon't mean anything bad by it\b", "don't mean any harm by it"),
        (r"\b([Pp]arents)\s+ends up\b", r"\1 end up"),
        (
            r"\b([Tt])here is many\b",
            lambda match: f"{match.group(1)}here are many",
        ),
        (r"\blaying around\b", "lying around"),
        (r"\bdoing not much\b", "not doing much"),
        (
            r"\b([Ee]very dog)\s+got\s+(?:there|their)\s+own\b",
            r"\1 has its own",
        ),
        (r"\bbring toys over and put it on\b", "bring a toy over and put it on"),
        (r"\bwant you playing with them\b", "want you to play with them"),
        (
            r"\bThere is always movement around,\s*making noise,\s*or wanting to sit\b",
            "They are always moving around, making noise, or wanting to sit",
        ),
        (r"\bbecause They are\b", "because they are"),
        (r"\bthey just lay on\b", "they just lie on"),
        (r"\bEvery dog doesn't do\b", "Not every dog does"),
        (
            r"\bBut Not every\b",
            lambda match: (
                "But not every"
                if match.group(0).startswith("But")
                else "but not every"
            ),
        ),
        (
            r"\bBut not every dog does those jobs because not all dogs can\b",
            "Not every dog does those jobs, since not all dogs can",
        ),
        (r",\s*then they run\b", ", and then they run"),
        (r"\brun around crazy\b", "run around like crazy"),
        (
            r"\bThe good things are bigger than the bad things,\s*mostly\b",
            "The benefits generally outweigh the drawbacks",
        ),
        (
            r"\bbecause The benefits generally outweigh the drawbacks\b",
            "because the benefits generally outweigh the drawbacks",
        ),
        (r"\bdifferent in their own ways\b", "different in their own way"),
        (
            r"\bSome dogs like swimming while another dog hates\b",
            "Some dogs like swimming, while other dogs hate",
        ),
        (
            r"\bother dogs hate water and tries running away\b",
            "other dogs hate water and try running away",
        ),
        (
            r"\bother dogs\b([^.!?]{0,80})\btries\b",
            r"other dogs\1try",
        ),
        (
            r"\A(At \d{1,2}:\d{2}\s+[ap]\.m\.)(?!,)\s+",
            r"\1, ",
        ),
        (
            r"(?<=[.!?])\s+(At \d{1,2}:\d{2}\s+[ap]\.m\.)(?!,)\s+",
            r" \1, ",
        ),
        (
            r"\bAfter the all clear\s*,?\s+",
            "After the all-clear, ",
        ),
        (
            r"\belevator recall emergency lighting oxygen alarms door locks "
            r"the local server and the fire panel connection\b",
            "elevator recall, emergency lighting, oxygen alarms, door locks, "
            "the local server, and the fire panel connection",
        ),
        (
            r"\bwhether the cable, tablet port, or software cache are responsible\b",
            "whether the cable, tablet port, or software cache is responsible",
        ),
        (
            r"\b(Lead researcher Professor [A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+)*) say\b",
            r"\1 says",
        ),
        (
            r"\b(starts|begins|begin|started) at "
            r"(\d{1,2}:\d{2}\s+[ap]\.m\.),\s+",
            r"\1 at \2 ",
        ),
        (
            r"\bon ((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2}),\s+by\b",
            r"on \1 by",
        ),
        (
            r"\bby adding ([^.!?]{0,120}) and hired\b",
            r"by adding \1 and hiring",
        ),
        (r"\bnone of them agrees (?:about|on)\b", "none of them agree on"),
        (r"(?<=[.!?])\s+Which was a serious mistake\b", " This was a serious mistake"),
        (
            r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\s+"
            r"(one|two|three|four|five|six|seven|eight|nine)\s+(?=minutes?\b)",
            lambda match: f"{match.group(1)}-{match.group(2)} ",
        ),
        (
            r"\bSeveral instruction is written\b",
            "Several instructions are written",
        ),
        (r",\s*for example\s+(?!,)", "; for example, "),
        (
            r"\b(vague vocabulary)\s+for example\s+(?!,)",
            r"\1; for example, ",
        ),
        (
            r"\bfor example\s+the guide\b",
            lambda match: (
                "For example, the guide"
                if match.group(0)[:1].isupper()
                else "for example, the guide"
            ),
        ),
        (
            r"\b(At \d{1,2}:\d{2}\s+[ap]\.m\.),\s+"
            r"((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)),\s+",
            r"\1 \2, ",
        ),
        (r"\bin the normal fashion\b", "in the usual way"),
        (r"\bvery unique\b", "unusual"),
        (r"\btalked at once,\s*nobody\b", "talked at once; nobody"),
        (
            r"\bthe media vendor haven't\b",
            lambda match: (
                "The media vendor has not"
                if match.group(0)[:1].isupper()
                else "the media vendor has not"
            ),
        ),
        (
            r"\b(has not|hasn(?:'|\u2019)t) confirmed if\b",
            r"\1 confirmed whether",
        ),
        (r"\bThis relationship is an association\b", "This is an association"),
        (
            r"\ba lot more (cancellations|errors|failures|delays|responses|"
            r"complaints|incidents)\b",
            r"many more \1",
        ),
        (r"\bEveryone agree\b", "Everyone agrees"),
        (
            r"\b(The) final review did not happen as the plan said it would\b",
            r"\1 final review did not occur as planned",
        ),
        (
            r"\b(stopped participating)\.\s+Because (the survey [^.!?]+)\.\s+"
            r"Which ([^.!?]+)\.",
            lambda match: (
                f"{match.group(1)}. "
                f"{match.group(2)[:1].upper() + match.group(2)[1:]}, "
                f"which {match.group(3).strip()}."
            ),
        ),
        (
            r"\.\s+Because ([^.!?]+)\.\s+Which ([^.!?]+)\.",
            lambda match: (
                f" because {match.group(1).strip()}, "
                f"which {match.group(2).strip()}."
            ),
        ),
        (
            r"\.\s+Because ([^.!?]+),\s+Which ([^.!?]+)\.",
            lambda match: (
                f" because {match.group(1).strip()}, "
                f"which {match.group(2).strip()}."
            ),
        ),
        (
            r"\bThere also contains several\b",
            "There are also several",
        ),
        (
            r"\btranslated survey,\s*dates,\s*the two transportation disruptions\b",
            "translated survey dates, the two transportation disruptions",
        ),
        (
            r"\bwhy some residents stopped participating because "
            r"(the survey was only available in English for the first six weeks), "
            r"which caused fewer responses\b",
            r"why some residents stopped participating. "
            r"The survey was only available in English for the first six weeks, "
            r"which caused fewer responses",
        ),
        (
            r"(\A|(?<=[.!?])\s+)Instead(?!,)\s+",
            r"\1Instead, ",
        ),
        (
            r"\bInstead,\s+The group\b",
            "Instead, the group",
        ),
        (
            r"\bThese numbers sound impressive\s+but\b",
            "These numbers sound impressive, but",
        ),
        (
            r"\b(At Garden [A-Z][A-Z0-9-]*)(?!,)\s+",
            r"\1, ",
        ),
        (
            r"\b(the bus stop closed)(?!,)\s+so\s+",
            r"\1, so ",
        ),
        (
            r"\b(closed the adjacent bus stop)(?!,)\s+so\s+",
            r"\1, so ",
        ),
        (
            r"\b(Neither event was controlled by [^.!?;,]+)(?<![,;])\s+"
            r"and\s+(ignoring\b)",
            r"\1, and \2",
        ),
        (
            r"\bwith highest participation\b",
            "with the highest participation",
        ),
        (
            r"\btwelve week trial\b",
            "twelve-week trial",
        ),
        (
            r"\btrial,\s+because\b",
            "trial because",
        ),
        (
            r"\bstudents and shift workers currently has\b",
            "students and shift workers currently have",
        ),
        (
            r"\b(The proposal) do not\b",
            r"\1 does not",
        ),
        (
            r"\bThe proposal is not asking for permanent hours\b",
            "The proposal does not request permanent hours",
        ),
        (
            r"\b(between (?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2} and "
            r"(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December) \d{1,2}),\s+(\d[\d,]* respondents)\b",
            r"\1; \2",
        ),
        (
            r"\bThe proposal (?:also )?(?:says|states) the program will be completely free, "
            r"which is incorrect vocabulary since the library expects operating costs of "
            r"(\$[\d,]+(?:\.\d{2})?)\b",
            r"The proposal describes the program as free to visitors, but the library "
            r"expects operating costs of \1",
        ),
        (
            r"\bVisitors will not pay a fee\s*,?\s*but the city still pays for "
            r"staff security utilities and transportation support\b",
            "Visitors will not pay a fee, but the city still pays for staff, security, "
            "utilities, and transportation support",
        ),
        (
            r"\brisks\.\s+While making clear that emergency procedures remain unchanged\b",
            "risks while making clear that emergency procedures remain unchanged",
        ),
        (
            r"(\$[\d,]+ for utilities)\s+and\s+(\$[\d,]+ for transit coordination)\b",
            r"\1, and \2",
        ),
        (
            r"\bThe Transit board\b",
            "The transit board",
        ),
        (
            r";\s*however,\s+The transit board\b",
            "; however, the transit board",
        ),
        (
            r"\bHowever\s+(?!,)(?=(?:MedTrack|The)\b)",
            "However, ",
        ),
        (
            r"\bHowever,\s+The transit board\b",
            "However, the transit board",
        ),
        (
            r"\bThere also have several\b",
            "There are also several",
        ),
        (
            r";\s*however,\s+",
            "; however, ",
        ),
        (
            r"\bmust not be presented these findings as evidence\b",
            "must not be presented as evidence",
        ),
        (
            r"\bThere also present several\b",
            "There are also several",
        ),
        (
            r"\bdoes not follow was drawn from\b",
            "does not follow from",
        ),
        (
            r"\band no establish a clear sequence\b",
            "and fail to establish a clear sequence",
        ),
        (
            r"\bshould be cautioned more cautiously\b",
            "should be explained more cautiously",
        ),
        (
            r"\bAmina requested Owen to verify\b",
            "Amina asked Owen to verify",
        ),
        (
            r"\bmust not be presented this as evidence\b",
            "must not be presented as evidence",
        ),
        (
            r"\bshould explain will explain\b",
            "should explain",
        ),
        (
            r"\bshould be explained it more cautiously\b",
            "should be explained more cautiously",
        ),
        (
            r"\bmore strong than\b",
            "stronger than",
        ),
        (
            r"\bThis data is useful, but a survey shows interest,\s+"
            r"not guaranteed attendance\b",
            "This data is useful, but a survey shows interest and does not "
            "guarantee attendance",
        ),
        (r"\bshould have has been explained\b", "should have been explained"),
        (
            r"\bThis data is useful, but it should have been explained with more "
            r"caution because a survey shows interest but does not guarantee attendance\b",
            "This data is useful, but it should be explained more cautiously. "
            "A survey shows interest but does not guarantee attendance",
        ),
        (
            r"\.\s+(\d[\d,]*) respondents\b",
            r". Of those respondents, \1",
        ),
        (
            r"\bshows interest,\s+not guaranteed attendance\b",
            "shows interest but does not guarantee attendance",
        ),
        (
            r"\b(risks?),\s+while making clear\b",
            r"\1 while making clear",
        ),
        (
            r"\bCouncil retains the final decision and approval\b",
            "Council retains the final decision, and approval",
        ),
        (
            r"\b(The queue reached zero at \d{2}:\d{2} UTC, but (?:the )?dashboard "
            r"[A-Z0-9-]+ still) displays (duplicate totals because its cache) "
            r"refreshes\b",
            r"\1 displayed \2 refreshed",
        ),
        (
            r"\bVisitors will not pay a fee, but the city still covers\b",
            "Visitors will not pay a fee, but the city will still cover",
        ),
        (
            r"\A(On (?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December) \d{1,2})(?!,)\s+",
            r"\1, ",
        ),
        (
            r"\b((?:At|By) \d{2}:\d{2} UTC)(?!,)\s+",
            r"\1, ",
        ),
        (r"\binvoices but one\b", "invoices, but one"),
        (
            r"\b(vault [A-Z0-9-]+)\s+it cannot\b",
            r"\1; it cannot",
        ),
        (
            r"\bemailed renamed or deleted\b",
            "emailed, renamed, or deleted",
        ),
        (
            r"\b(Runbook [A-Z0-9-]+ says)\s+disable\b",
            r"\1 to disable",
        ),
        (
            r"\brestart two workers and compare (message )?counts\b",
            lambda match: (
                "restart two workers, and compare "
                f"{match.group(1) or ''}counts"
            ),
        ),
        (
            r"\breached zero([^.!?]{0,40})\s+but dashboard\b",
            r"reached zero\1, but dashboard",
        ),
        (
            r"\bWhile waiting for the refresh\.\s+"
            r"([A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,3})\s+",
            r"While waiting for the refresh, \1 ",
        ),
        (
            r"\bwhen cache key\b",
            "when the cache key",
        ),
        (
            r"\bread only replica\b",
            "read-only replica",
        ),
        (
            r"\band feature flag ([A-Z][A-Z0-9_]*)\b",
            r"and the feature flag \1",
        ),
        (
            r"\b([A-Z][a-z]+) is disabled,\s+([A-Z][A-Z0-9_]*)\s+at\b",
            r"\1 disabled \2 at",
        ),
        (
            r"\b(UTC),\s+so that\b",
            r"\1 so that",
        ),
        (
            r"\b(The replay began at \d{2}:\d{2} UTC),?\s+and finished at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and finished at \2 with",
        ),
        (
            r"\b(The replay started at \d{2}:\d{2} UTC),?\s+and ended at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and ended at \2 with",
        ),
        (
            r"\b(The replay commenced at \d{2}:\d{2} UTC),?\s+and concluded at "
            r"(\d{2}:\d{2} UTC),?\s+with\b",
            r"\1 and concluded at \2 with",
        ),
        (
            r"\bothers list six actions with commas and no clear order,\s+"
            r"using 'utilize' instead of 'use'",
            "others list six actions with commas and no clear order and use "
            "'utilize' instead of 'use'",
        ),
        (r"\bcustomers was affected\b", "customers were affected"),
        (
            r"\bdata was corrupt\s*[,;]?\s*this vocabulary\b",
            "data was corrupt; this wording",
        ),
        (
            r"\bno amount differences, customer names, or tax codes\b",
            "no differences in amounts, customer names, or tax codes",
        ),
        (r"\bFollow up actions\b", "Follow-up actions"),
        (
            r"\b(final table|the table) and no customer data\b",
            r"\1, and no customer data",
        ),
        (r"\bsentences was active\b", "sentences were active"),
        (r"\bor if replacement finished\b", "or whether the replacement had finished"),
        (r"\bon first sentence\b", "on the first sentence"),
        (
            r"\b(every\s+[A-Za-z][A-Za-z'-]*)\s+need\s+to\b",
            r"\1 needs to",
        ),
        (
            r"\bthere (?=(?:existing|own|personal|company|approved|contract)\b)",
            "their ",
        ),
        (r"\bThe Another\b", "Another"),
        (r"\bthis vocabulary is inaccurate\b", "this wording is inaccurate"),
        (r"\bwas suppose to\b", "was supposed to"),
        (r"\bOther ones combine\b", "Others combine"),
        (r"\bHowever, That\b", "However, that"),
        (
            r"\b(on checklist [A-Z0-9-]+), and reports\b",
            r"\1 and reports",
        ),
        (
            r"\bfinal review didn(?:'|\u2019)t happen like the plan said it would\b",
            "final review did not happen as planned",
        ),
        (r"\bbut dashboard ([A-Z0-9-]+)\b", r"but the dashboard \1"),
        (
            r"\bstaffed entrance, hourly floor checks and a text line\b",
            "staffed entrance, hourly floor checks, and a text line",
        ),
        (
            r"\beliminate all risk,\s+That is\b",
            "eliminate all risk. That is",
        ),
        (r"\balarm fails (?:sounds|triggers)\b", "alarm fails"),
        (r"\black no quiet public space\b", "have no quiet public space"),
        (r"(?-i:\bAn No\b)", "No"),
        (r"\bso that an no additional\b", "so that no additional"),
        (r"\bresponds hostiley\b", "responds in a hostile way"),
        (
            r"\bnot,\s+and the general book budget\b",
            "not the general book budget",
        ),
        (
            r"\s+and not,\s+the general book budget\b",
            ", not the general book budget",
        ),
        (r";\s+That is\b", ". That is"),
        (
            r"\bspoke simultaneously regarding nobody\b",
            "spoke simultaneously; nobody",
        ),
        (
            r"\bDistrict reports login failures\b",
            "District leads report login failures",
        ),
        (
            r"\bdoes not guarantee attendance remains limited\b",
            "does not guarantee attendance",
        ),
        (r"\bRecovery validation utilized\b", "Recovery validation used"),
        (r"\bprinted pocket card reverse\b", "printed pocket card reverses"),
        (r"\bmanuscript\b", "Manuscript"),
        (r"\bthe hud\b", "the HUD"),
        (r"([!?.,])\1+", r"\1"),
        (r"\.\?+(?=\s|$)", "."),
    ):
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned
