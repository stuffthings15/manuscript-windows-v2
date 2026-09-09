"""Run a queue of experiments end to end, with nothing between them.

    .venv/Scripts/python.exe tools/autoresearch_queue.py

This is the piece the loop was missing. `autoresearch_cycle.py` collapsed one
experiment into one command, but the AGENT still sat between experiments,
deciding what to try next - and every one of those gaps was a place where Curtis
had to say "keep going". He was right that this is the whole problem: a loop
that needs a human between its iterations is not a loop.

So the ideas go in a list, in advance, and this runs the list. Each entry is a
description, a set of exact substitutions to make in a source file, and the
slice to measure them on. It edits, runs the cycle, and moves to the next one
whatever the answer - keep, discard or crash. Nothing here decides anything;
`autoresearch_cycle` still owns keep and discard, and it still resets the branch
on a discard, so a bad idea leaves no trace but a row in results.tsv.

WHY THESE IDEAS AND NOT AN OFFLINE SCREEN. The first five entries were all ones
I had talked myself out of by measuring them against the 24,690-pair bundled
misspelling list. That list is a PROXY. The metric is a corpus score from a Word
run, and the two disagree in BOTH directions: the split-compound rule scored
zero false accepts on the proxy and measured flat, while the tie-break ratio and
the ranker margin looked like bad trades on the proxy and together took essay 10
from 96 to 100. Refusing a lever on proxy evidence decides the experiment's
outcome without running it, which is the one thing this apparatus exists to stop.

Deliberately dumb about text: a substitution matching neither its before nor its
after form is a hard error, because an experiment that silently changed nothing
would be recorded as a discard and would look exactly like an idea that failed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import autoresearch_cycle  # noqa: E402
import autoresearch_status  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# The cycles return this when nobody measured anything because a person is
# using the machine. program.md: treat it as "come back later".
_SKIPPED_NOBODY_HOME = 2
_SKIP_WAIT_SECONDS = 600.0

SPELLING = "src/echo_link_core/polish_spelling.py"
CLASSIFIER = "src/echo_link_core/polish_classifier.py"
INSERTION = "src/echo_link_core/insertion.py"
POLISH = "src/echo_link_core/polish.py"
HARDWARE = "src/echo_link_core/polish_hardware.py"
SYNTAX = "src/echo_link_core/polish_syntax.py"
ADVICE = "src/echo_link_core/polish_advice.py"
HUD = "src/echo_link_app/window.py"
QUALITY = "src/echo_link_core/polish_quality.py"

# The lookup inside `suggestion`, NOT the one inside `has_close_candidate`.
# Both read `max_edit_distance=1,` and the wrong one is FIRST in the file, so a
# bare substitution would silently widen the guard that REFUSES repairs instead
# of the lookup that offers them - an experiment measuring the opposite of what
# its own description claims.
_LOOKUP_ONE_EDIT = "\n".join(
    (
        "word.casefold(),",
        "                Verbosity.CLOSEST,",
        "                max_edit_distance=1,",
    )
)
_LOOKUP_TWO_EDITS = _LOOKUP_ONE_EDIT.replace(
    "max_edit_distance=1,", "max_edit_distance=2,"
)

_TAILS_BEFORE = '        "only", "more", "most",'
_TAILS_AFTER = "\n".join(
    (
        '        "only", "more", "most",',
        "        # Prepositions and pronouns. Everything above is a modal, a",
        '        # determiner or a conjunction, which is why "vendorsmay" split',
        '        # and "closelyat" did not - an arbitrary gap rather than a',
        "        # decision anyone took.",
        '        "at", "in", "on", "of", "to", "by", "as", "or", "up", "out",',
        '        "is", "be", "it", "its", "his", "her", "our", "your",',
    )
)

_PREFIXES_BEFORE = (
    '    "pre", "post", "anti", "non", "semi", "sub", "super", "inter", "ultra",'
)
_PREFIXES_AFTER = "\n".join(
    (
        _PREFIXES_BEFORE,
        "    # Native English prefixes, every bit as productive as the Greek and",
        '    # Latin ones above and left out for no stated reason. "outplanting"',
        "    # is ordinary English that both bundled lists lack, and",
        "    # RELEASE_ACCEPTANCE.md names it as a word the spelling term charges",
        "    # wrongly. DERIVING it is exactly what this table is for; listing the",
        "    # word itself would be memorising the corpus.",
        '    "out", "over", "under", "up", "down", "off", "back", "cross",',
        '    "counter", "self", "re", "un", "mis", "co",',
    )
)

_SUFFIX_CONSTANT_BEFORE = "_COMPOUND_PREFIXES = ("
_SUFFIX_CONSTANT_AFTER = "\n".join(
    (
        "# Productive English suffixes. A known stem carrying one of these is a",
        '# real word: "passivation" is passive + ation, ordinary English that',
        "# neither bundled list has. Same principle as the prefixes below -",
        "# derive the word, never list it.",
        "_COMPOUND_SUFFIXES = (",
        '    "ation", "ations", "isation", "ization", "ising", "izing",',
        '    "ised", "ized", "ment", "ments", "ness", "less", "able", "ible",',
        ")",
        "",
        "_COMPOUND_PREFIXES = (",
    )
)

_SUFFIX_LOOP_BEFORE = (
    '        # Both parts at least four characters: shorter splits are how "acid" +'
)
_SUFFIX_LOOP_AFTER = "\n".join(
    (
        "        for suffix in _COMPOUND_SUFFIXES:",
        "            if not folded.endswith(suffix):",
        "                continue",
        "            stem = folded[: -len(suffix)]",
        "            # The stem as written, and with a dropped final -e restored:",
        '            # "passivation" is passive + ation with the e elided, which',
        "            # is how English actually forms these.",
        '            for candidate in (stem, stem + "e"):',
        "                if len(candidate) > 3 and real(candidate) and common(candidate):",
        "                    return True",
        "",
        _SUFFIX_LOOP_BEFORE,
    )
)

# The tone contract edits, built with join rather than embedded escapes. Every
# one adds a checkable target beside constraints that were already concrete.
_SHORTEN_BEFORE = "\n".join(
    (
        '"Remove repetition and filler while retaining all actors, factual clauses, "',
        '        "important details, and boundaries.",',
    )
)
_SHORTEN_AFTER = "\n".join(
    (
        '"Remove repetition and filler while retaining all actors, factual clauses, "',
        '        "important details, and boundaries. Aim for at least twenty percent "',
        '        "fewer words than the source. A sentence that is merely tidied has not "',
        '        "been shortened.",',
    )
)

_REPHRASE_BEFORE = '"different natural wording without changing meaning.",'
_REPHRASE_AFTER = "\n".join(
    (
        '"different natural wording without changing meaning. Every sentence must "',
        '        "differ from the source in word order or word choice; a sentence that "',
        '        "only fixes spelling, capitalisation or punctuation has not been "',
        '        "rephrased.",',
    )
)

_FRIENDLY_BEFORE = (
    '"claims around helping people and making their work easier wherever '
    'the source supports it. "'
)
_FRIENDLY_AFTER = "\n".join(
    (
        _FRIENDLY_BEFORE,
        '        "Address the reader as you at least once in every paragraph where the "',
        '        "source allows it, and open at least one paragraph with a short "',
        '        "sentence of under ten words. "',
    )
)


# (description, file, ((before, after), ...), slice, kind)
#
# The slice is PER ENTRY, because each lever below aims at the one word a
# particular essay has left. Measuring it on an essay that does not contain that
# word would score flat and read as a failed idea.
#
# Ordered cheapest to riskiest, combinations last, so that a combination is
# measured against whatever bar its own parts have already raised.
QUEUE: tuple[tuple[str, str, tuple[tuple[str, str], ...], str], ...] = (
    (
        "look two edits out, not one, for a spelling repair",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
        "10",
    ),
    (
        "let frequency break a tie at 30x rather than 50x",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 50", "_DOMINANT_FREQUENCY_RATIO = 30"),),
        "10",
    ),
    (
        "narrow the context ranker's margin from 0.02 to 0.012",
        CLASSIFIER,
        (("_MARGIN = 0.02", "_MARGIN = 0.012"),),
        "10",
    ),
    (
        "let a run-together word end in a preposition, not only a verb",
        SPELLING,
        ((_TAILS_BEFORE, _TAILS_AFTER),),
        "6",
    ),
    (
        "recognise out-, over- and under- as productive prefixes",
        SPELLING,
        ((_PREFIXES_BEFORE, _PREFIXES_AFTER),),
        "4",
    ),
    (
        "recognise a known stem carrying a productive suffix",
        SPELLING,
        (
            (_SUFFIX_CONSTANT_BEFORE, _SUFFIX_CONSTANT_AFTER),
            (_SUFFIX_LOOP_BEFORE, _SUFFIX_LOOP_AFTER),
        ),
        "5",
    ),
    # Two edits out, retried. It was DISCARDED on essay 10 and that verdict
    # stands for the tree it ran against - but the tie-break ratio has come down
    # to 30 since, and that changes what a wider lookup can deliver. "serreous"
    # finds serious at 38.3M against ferrous at 1.0M: a 38x lead, refused as a
    # tie under the old 50x bar and decisive under the new one. The lookup never
    # reached those candidates because it only ever looked one edit out.
    #
    # A discard is a verdict on an idea in a context, not on the idea forever.
    (
        "look two edits out, now that a 38x lead is no longer a tie",
        SPELLING,
        ((_LOOKUP_ONE_EDIT, _LOOKUP_TWO_EDITS),),
        "12",
    ),
    # "mayfund" is may + fund, and it fails on one character: the rule wants the
    # content half to be five long and "fund" is four. The comment on the
    # constant records what 4 cost when it was last measured - gallare, hearwith,
    # origonly, beththe - so this is a known trade being re-measured against the
    # metric rather than against that note.
    (
        "let the content half of a run-together word be four letters",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 5", "_RUN_TOGETHER_HEAD = 4"),),
        "13",
    ),
    # "makels" is makes at 87.8M against makers at 13.3M - a 6.6x lead, still a
    # tie at 30x. Rather than drop the ratio again and let raw frequency decide
    # more often, narrow the ranker's margin so the SENTENCE gets to decide: it
    # is the layer that can tell "makels sense" from a plural noun, and 0.02 to
    # 0.012 already took essay 10 to 100.
    (
        "narrow the ranker's margin again, from 0.012 to 0.008",
        CLASSIFIER,
        (("_MARGIN = 0.012", "_MARGIN = 0.008"),),
        "19",
    ),
    # TONE entries, measured by autoresearch_tone_cycle against the judge's pass
    # percentage. The corpus score does not move for a tone change, so these
    # cannot go through the correction cycle at all.
    #
    # ONE diagnosis, three edits. Across every failing tone the judge reports
    # usefulness 4.0-4.21 and target_style 2.21-3.21: the output is good English
    # that will not COMMIT to the transformation. The contracts say why - each
    # states its CONSTRAINTS concretely and its TARGET vaguely, so the model
    # optimises for the half it can check. Clear, the only tone at 100 percent,
    # is one sentence with nothing competing against it.
    #
    # Every edit is ADDITIVE. No guard is removed and the Modality clause stays:
    # 14 of 19 Friendly refusals on 2026-08-20 were real modal shifts, where
    # "hesitation blocks progress" became "hesitation can block progress".
    (
        "give Shorten a measurable target, not only things to retain",
        QUALITY,
        ((_SHORTEN_BEFORE, _SHORTEN_AFTER),),
        "Shorten",
        "tone",
    ),
    (
        "tell Rephrase what counts as rephrased",
        QUALITY,
        ((_REPHRASE_BEFORE, _REPHRASE_AFTER),),
        "Rephrase",
        "tone",
    ),
    (
        "give Friendly a concrete warmth move it is allowed to make",
        QUALITY,
        ((_FRIENDLY_BEFORE, _FRIENDLY_AFTER),),
        "Friendly",
        "tone",
    ),

    # ---------------------------------------------------------------- shapes
    # RELIABILITY, measured on test-corpus/document-shapes-hard: the three list
    # documents that fail. program.md fixes an experiment at five minutes and
    # three documents at ~100s each is exactly that, so each of these costs one
    # budget rather than the twenty-document 36 minutes that made the loop run
    # at 1.6 experiments an hour.
    #
    # None of these has ever been run. The twelve entries above are all in
    # results.tsv already, which is why starting this queue unchanged would have
    # repeated the lot.
    #
    # Every one is a single constant, because a constant is the only kind of
    # change whose effect the completion metric can actually attribute. The
    # ideas are the B and C blocks of the experiment plan: insertion timing and
    # batching, then the HUD's retry and recapture behaviour.
    (
        "send keystrokes in larger batches, 60 units to 200",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 200"),),
        "hard",
        "reliability",
    ),
    (
        "send keystrokes in smaller batches, 60 units to 20",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 20"),),
        "hard",
        "reliability",
    ),
    (
        "no delay between keystroke batches at all",
        INSERTION,
        ((
            "_DIRECT_CHARACTER_DELAY_SECONDS = 0.001",
            "_DIRECT_CHARACTER_DELAY_SECONDS = 0.0",
        ),),
        "hard",
        "reliability",
    ),
    (
        "five attempts to insert into the target, not three",
        INSERTION,
        (("_TARGET_INSERT_ATTEMPTS = 3", "_TARGET_INSERT_ATTEMPTS = 5"),),
        "hard",
        "reliability",
    ),
    (
        "wait longer between insert attempts, 0.05s to 0.2s",
        INSERTION,
        (("_TARGET_INSERT_RETRY_SECONDS = 0.05", "_TARGET_INSERT_RETRY_SECONDS = 0.2"),),
        "hard",
        "reliability",
    ),
    (
        "let Word settle for less after a replacement, 0.5s to 0.2s",
        INSERTION,
        ((
            "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5",
            "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.2",
        ),),
        "hard",
        "reliability",
    ),
    (
        "let Word settle for longer after a replacement, 0.5s to 1.0s",
        INSERTION,
        ((
            "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5",
            "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 1.0",
        ),),
        "hard",
        "reliability",
    ),
    (
        "five attempts to restore the selection, not three",
        INSERTION,
        (("_SELECTION_RESTORE_ATTEMPTS = 3", "_SELECTION_RESTORE_ATTEMPTS = 5"),),
        "hard",
        "reliability",
    ),
    (
        "allow a wider unreported list label, 4 characters to 8",
        INSERTION,
        ((
            "_MAX_UNREPORTED_LABEL_CHARACTERS = 4",
            "_MAX_UNREPORTED_LABEL_CHARACTERS = 8",
        ),),
        "hard",
        "reliability",
    ),
    (
        "four apply attempts before asking for a fresh capture, not two",
        HUD,
        (("_APPLY_RETRY_BEFORE_RECAPTURE = 2", "_APPLY_RETRY_BEFORE_RECAPTURE = 4"),),
        "hard",
        "reliability",
    ),
    (
        "suppress recapture for half as long, 3.0s to 1.5s",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 1.5"),),
        "hard",
        "reliability",
    ),
    (
        "suppress recapture for twice as long, 3.0s to 6.0s",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 6.0"),),
        "hard",
        "reliability",
    ),

    # --------------------------------------------------- generated 2026-09-04
    # Every remaining single-constant experiment this codebase supports, with
    # each anchor verified to appear exactly once before any of it was written.
    #
    # The reliability entries run on document-shapes-hard, which has the only
    # large headroom left: the slice scores near zero and the three documents
    # all fail today. The quality entries run on essays 8, 12 and 17, the ONLY
    # essay slices not already at 100 - nine others are pinned there, where the
    # keep rule is a strict improvement and an experiment is a guaranteed
    # discard before it starts.
    #
    # Both directions of every constant, because a bound is a claim in two
    # directions and only one of them has ever been tested. Half of these will
    # be discards; that is the point, and a discard costs five minutes and
    # leaves a row.
    #
    # WHAT IS NOT HERE, and why the list stops at 88 rather than 150: the rest
    # of the experiment plan is not expressible as a substitution. Reconstruct
    # footnote reference marks, refuse a document containing a text box, cache
    # the capture's canonical form, two-pass proofread-then-tone - each is code
    # somebody has to write, and encoding them as one-line edits would either
    # not compile or measure something other than the idea. Padding this list to
    # 150 with narrower slices of the same constants would produce rows, not
    # knowledge.
    (
        "half again as many keystrokes per SendInput call",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 120"),),
        "hard",
        "reliability",
    ),
    (
        "as few SendInput calls as the array will hold",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 400"),),
        "hard",
        "reliability",
    ),
    (
        "three times the pause between keystroke batches",
        INSERTION,
        (("_DIRECT_CHARACTER_DELAY_SECONDS = 0.001", "_DIRECT_CHARACTER_DELAY_SECONDS = 0.003"),),
        "hard",
        "reliability",
    ),
    (
        "ten times the pause, for an app that cannot keep up",
        INSERTION,
        (("_DIRECT_CHARACTER_DELAY_SECONDS = 0.001", "_DIRECT_CHARACTER_DELAY_SECONDS = 0.01"),),
        "hard",
        "reliability",
    ),
    (
        "give up on a refusing target sooner",
        INSERTION,
        (("_TARGET_INSERT_ATTEMPTS = 3", "_TARGET_INSERT_ATTEMPTS = 2"),),
        "hard",
        "reliability",
    ),
    (
        "keep trying a refusing target far longer",
        INSERTION,
        (("_TARGET_INSERT_ATTEMPTS = 3", "_TARGET_INSERT_ATTEMPTS = 8"),),
        "hard",
        "reliability",
    ),
    (
        "retry an insert almost immediately",
        INSERTION,
        (("_TARGET_INSERT_RETRY_SECONDS = 0.05", "_TARGET_INSERT_RETRY_SECONDS = 0.02"),),
        "hard",
        "reliability",
    ),
    (
        "let the target settle a long time between inserts",
        INSERTION,
        (("_TARGET_INSERT_RETRY_SECONDS = 0.05", "_TARGET_INSERT_RETRY_SECONDS = 0.5"),),
        "hard",
        "reliability",
    ),
    (
        "barely wait for Word after a replacement",
        INSERTION,
        (("_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5", "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.05"),),
        "hard",
        "reliability",
    ),
    (
        "wait four times as long for Word to settle",
        INSERTION,
        (("_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5", "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 2.0"),),
        "hard",
        "reliability",
    ),
    (
        "stop restoring the selection sooner",
        INSERTION,
        (("_SELECTION_RESTORE_ATTEMPTS = 3", "_SELECTION_RESTORE_ATTEMPTS = 2"),),
        "hard",
        "reliability",
    ),
    (
        "restore the selection far more persistently",
        INSERTION,
        (("_SELECTION_RESTORE_ATTEMPTS = 3", "_SELECTION_RESTORE_ATTEMPTS = 8"),),
        "hard",
        "reliability",
    ),
    (
        "retry the selection restore almost immediately",
        INSERTION,
        (("_SELECTION_RESTORE_RETRY_SECONDS = 0.06", "_SELECTION_RESTORE_RETRY_SECONDS = 0.02"),),
        "hard",
        "reliability",
    ),
    (
        "leave longer between selection restore attempts",
        INSERTION,
        (("_SELECTION_RESTORE_RETRY_SECONDS = 0.06", "_SELECTION_RESTORE_RETRY_SECONDS = 0.2"),),
        "hard",
        "reliability",
    ),
    (
        "only the narrowest unreported list label counts",
        INSERTION,
        (("_MAX_UNREPORTED_LABEL_CHARACTERS = 4", "_MAX_UNREPORTED_LABEL_CHARACTERS = 2"),),
        "hard",
        "reliability",
    ),
    (
        "allow a very wide unreported label",
        INSERTION,
        (("_MAX_UNREPORTED_LABEL_CHARACTERS = 4", "_MAX_UNREPORTED_LABEL_CHARACTERS = 16"),),
        "hard",
        "reliability",
    ),
    (
        "paste settles almost immediately",
        INSERTION,
        (("_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.04", "_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.01"),),
        "hard",
        "reliability",
    ),
    (
        "three times the base settle after a paste",
        INSERTION,
        (("_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.04", "_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.12"),),
        "hard",
        "reliability",
    ),
    (
        "cap the paste settle at half",
        INSERTION,
        (("_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.3", "_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.15"),),
        "hard",
        "reliability",
    ),
    (
        "let a long paste settle twice as long",
        INSERTION,
        (("_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.3", "_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.6"),),
        "hard",
        "reliability",
    ),
    (
        "return the writer's clipboard sooner",
        INSERTION,
        (("_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.2", "_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.05"),),
        "hard",
        "reliability",
    ),
    (
        "leave the clipboard alone longer before restoring",
        INSERTION,
        (("_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.2", "_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.5"),),
        "hard",
        "reliability",
    ),
    (
        "recapture after a single failed apply",
        HUD,
        (("_APPLY_RETRY_BEFORE_RECAPTURE = 2", "_APPLY_RETRY_BEFORE_RECAPTURE = 1"),),
        "hard",
        "reliability",
    ),
    (
        "try six applies before asking for a fresh capture",
        HUD,
        (("_APPLY_RETRY_BEFORE_RECAPTURE = 2", "_APPLY_RETRY_BEFORE_RECAPTURE = 6"),),
        "hard",
        "reliability",
    ),
    (
        "allow a recapture almost at once",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 0.5"),),
        "hard",
        "reliability",
    ),
    (
        "suppress recapture for far longer",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 10.0"),),
        "hard",
        "reliability",
    ),
    (
        "stop retrying a cold model sooner",
        HUD,
        (("_WARM_RETRY_ATTEMPTS = 4", "_WARM_RETRY_ATTEMPTS = 2"),),
        "hard",
        "reliability",
    ),
    (
        "retry a cold model twice as many times",
        HUD,
        (("_WARM_RETRY_ATTEMPTS = 4", "_WARM_RETRY_ATTEMPTS = 8"),),
        "hard",
        "reliability",
    ),
    (
        "retry a cold model twice as often",
        HUD,
        (("_WARM_RETRY_DELAY_SECONDS = 20.0", "_WARM_RETRY_DELAY_SECONDS = 10.0"),),
        "hard",
        "reliability",
    ),
    (
        "leave twice as long between warm retries",
        HUD,
        (("_WARM_RETRY_DELAY_SECONDS = 20.0", "_WARM_RETRY_DELAY_SECONDS = 40.0"),),
        "hard",
        "reliability",
    ),
    (
        "forgive a failed pack after one minute, not five",
        POLISH,
        (("_FAILED_PACK_TTL_SECONDS = 300.0", "_FAILED_PACK_TTL_SECONDS = 60.0"),),
        "hard",
        "reliability",
    ),
    (
        "remember a failed pack for fifteen minutes",
        POLISH,
        (("_FAILED_PACK_TTL_SECONDS = 300.0", "_FAILED_PACK_TTL_SECONDS = 900.0"),),
        "hard",
        "reliability",
    ),
    (
        "forget a validation failure almost at once",
        POLISH,
        (("_VALIDATION_FAILURE_TTL_SECONDS = 0.25", "_VALIDATION_FAILURE_TTL_SECONDS = 0.05"),),
        "hard",
        "reliability",
    ),
    (
        "remember a validation failure four times as long",
        POLISH,
        (("_VALIDATION_FAILURE_TTL_SECONDS = 0.25", "_VALIDATION_FAILURE_TTL_SECONDS = 1.0"),),
        "hard",
        "reliability",
    ),
    (
        "give up on a model after two bad answers",
        POLISH,
        (("_INVALID_RESPONSE_STREAK = 3", "_INVALID_RESPONSE_STREAK = 2"),),
        "hard",
        "reliability",
    ),
    (
        "tolerate five bad answers before falling back",
        POLISH,
        (("_INVALID_RESPONSE_STREAK = 3", "_INVALID_RESPONSE_STREAK = 5"),),
        "hard",
        "reliability",
    ),
    (
        "only count failures inside a ten second window",
        POLISH,
        (("_FAILURE_STREAK_WINDOW_SECONDS = 30.0", "_FAILURE_STREAK_WINDOW_SECONDS = 10.0"),),
        "hard",
        "reliability",
    ),
    (
        "count failures across a ninety second window",
        POLISH,
        (("_FAILURE_STREAK_WINDOW_SECONDS = 30.0", "_FAILURE_STREAK_WINDOW_SECONDS = 90.0"),),
        "hard",
        "reliability",
    ),
    (
        "re-check which model is loaded four times as often",
        HARDWARE,
        (("_LOADED_MODEL_CACHE_SECONDS = 120.0", "_LOADED_MODEL_CACHE_SECONDS = 30.0"),),
        "hard",
        "reliability",
    ),
    (
        "trust the loaded-model cache for five minutes",
        HARDWARE,
        (("_LOADED_MODEL_CACHE_SECONDS = 120.0", "_LOADED_MODEL_CACHE_SECONDS = 300.0"),),
        "hard",
        "reliability",
    ),
    (
        "allow a closer compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 1"),),
        "8",
    ),
    (
        "allow a closer compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 1"),),
        "12",
    ),
    (
        "allow a closer compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 1"),),
        "17",
    ),
    (
        "demand a wider separation before splitting a compound",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 1", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 3"),),
        "8",
    ),
    (
        "demand a wider separation before splitting a compound",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 1", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 3"),),
        "12",
    ),
    (
        "demand a wider separation before splitting a compound",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 1", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 3"),),
        "17",
    ),
    (
        "let a weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 20"),),
        "8",
    ),
    (
        "let a weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 20"),),
        "12",
    ),
    (
        "let a weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 20"),),
        "17",
    ),
    (
        "demand a stronger frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 45"),),
        "8",
    ),
    (
        "demand a stronger frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 45"),),
        "12",
    ),
    (
        "demand a stronger frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 45"),),
        "17",
    ),
    (
        "let a weaker lead override a probable name",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 35"),),
        "8",
    ),
    (
        "let a weaker lead override a probable name",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 35"),),
        "12",
    ),
    (
        "let a weaker lead override a probable name",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 35"),),
        "17",
    ),
    (
        "protect probable names more strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 70"),),
        "8",
    ),
    (
        "protect probable names more strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 70"),),
        "12",
    ),
    (
        "protect probable names more strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 70"),),
        "17",
    ),
    (
        "call a word mangled on weaker evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.55"),),
        "8",
    ),
    (
        "call a word mangled on weaker evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.55"),),
        "12",
    ),
    (
        "call a word mangled on weaker evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.55"),),
        "17",
    ),
    (
        "demand stronger evidence before calling a word mangled",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.85"),),
        "8",
    ),
    (
        "demand stronger evidence before calling a word mangled",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.85"),),
        "12",
    ),
    (
        "demand stronger evidence before calling a word mangled",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.85"),),
        "17",
    ),
    (
        "allow a three letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 3"),),
        "8",
    ),
    (
        "allow a three letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 3"),),
        "12",
    ),
    (
        "allow a three letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 3"),),
        "17",
    ),
    (
        "demand a five letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 5"),),
        "8",
    ),
    (
        "demand a five letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 5"),),
        "12",
    ),
    (
        "demand a five letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 5"),),
        "17",
    ),
    (
        "narrow the context ranker margin again",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.005"),),
        "8",
    ),
    (
        "narrow the context ranker margin again",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.005"),),
        "12",
    ),
    (
        "narrow the context ranker margin again",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.005"),),
        "17",
    ),
    (
        "widen the ranker margin back out",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.012"),),
        "8",
    ),
    (
        "widen the ranker margin back out",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.012"),),
        "12",
    ),
    (
        "widen the ranker margin back out",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.012"),),
        "17",
    ),

    # --------------------------------------------- second batch, 2026-09-04
    # Curtis asked for the full 180. These are the rest, and they are of two
    # kinds.
    #
    # A THIRD AND FOURTH POINT on constants already swept two ways, including
    # the extremes - no delay at all, no retry at all, never cache, never
    # forget. Two points give the sign of a change; four give the shape, and an
    # extreme says whether the mechanism matters at all. A constant whose value
    # from zero to twenty changes nothing is a constant nobody needs to tune.
    #
    # FIVE CONSTANTS NEVER SWEPT: _MINIMUM_REMAINING_WORDS,
    # _MAXIMUM_SENTENCE_WORDS, _MAXIMUM_REMOVED_WORDS, _INTRODUCTION_PARAGRAPHS
    # and LONG_SENTENCE_WORDS. Every quality experiment to date has moved
    # spelling or the ranker; the syntax and advice layers have never been
    # touched by the loop at all.
    #
    # A NOTE ON RESOLUTION, because it decides how to read the results. The hard
    # slice is three documents, so a completion score can only be 0, 33.3, 66.7
    # or 100. The baseline is 66.7. Only a change that fixes the remaining
    # document can register, and everything else ties and discards. That is the
    # price of the five-minute budget and it is worth knowing before reading a
    # column of discards as eleven refuted ideas.
    (
        "halve the keystroke batch",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 30"),),
        "hard",
        "reliability",
    ),
    (
        "one SendInput call for a whole section",
        INSERTION,
        (("_DIRECT_BATCH_UNITS = 60", "_DIRECT_BATCH_UNITS = 500"),),
        "hard",
        "reliability",
    ),
    (
        "halve the pause between keystroke batches",
        INSERTION,
        (("_DIRECT_CHARACTER_DELAY_SECONDS = 0.001", "_DIRECT_CHARACTER_DELAY_SECONDS = 0.0005"),),
        "hard",
        "reliability",
    ),
    (
        "twenty times the pause, for a slow target",
        INSERTION,
        (("_DIRECT_CHARACTER_DELAY_SECONDS = 0.001", "_DIRECT_CHARACTER_DELAY_SECONDS = 0.02"),),
        "hard",
        "reliability",
    ),
    (
        "one attempt only, never retry a refusing target",
        INSERTION,
        (("_TARGET_INSERT_ATTEMPTS = 3", "_TARGET_INSERT_ATTEMPTS = 1"),),
        "hard",
        "reliability",
    ),
    (
        "retry a refusing target until it gives in",
        INSERTION,
        (("_TARGET_INSERT_ATTEMPTS = 3", "_TARGET_INSERT_ATTEMPTS = 12"),),
        "hard",
        "reliability",
    ),
    (
        "retry an insert immediately",
        INSERTION,
        (("_TARGET_INSERT_RETRY_SECONDS = 0.05", "_TARGET_INSERT_RETRY_SECONDS = 0.01"),),
        "hard",
        "reliability",
    ),
    (
        "a full second between insert attempts",
        INSERTION,
        (("_TARGET_INSERT_RETRY_SECONDS = 0.05", "_TARGET_INSERT_RETRY_SECONDS = 1.0"),),
        "hard",
        "reliability",
    ),
    (
        "do not wait for Word at all after a replacement",
        INSERTION,
        (("_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5", "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "wait eight times as long for Word to settle",
        INSERTION,
        (("_OFFICE_REPLACEMENT_SETTLE_SECONDS = 0.5", "_OFFICE_REPLACEMENT_SETTLE_SECONDS = 4.0"),),
        "hard",
        "reliability",
    ),
    (
        "restore the selection once or not at all",
        INSERTION,
        (("_SELECTION_RESTORE_ATTEMPTS = 3", "_SELECTION_RESTORE_ATTEMPTS = 1"),),
        "hard",
        "reliability",
    ),
    (
        "restore the selection as persistently as possible",
        INSERTION,
        (("_SELECTION_RESTORE_ATTEMPTS = 3", "_SELECTION_RESTORE_ATTEMPTS = 12"),),
        "hard",
        "reliability",
    ),
    (
        "retry the selection restore at once",
        INSERTION,
        (("_SELECTION_RESTORE_RETRY_SECONDS = 0.06", "_SELECTION_RESTORE_RETRY_SECONDS = 0.005"),),
        "hard",
        "reliability",
    ),
    (
        "half a second between selection restores",
        INSERTION,
        (("_SELECTION_RESTORE_RETRY_SECONDS = 0.06", "_SELECTION_RESTORE_RETRY_SECONDS = 0.5"),),
        "hard",
        "reliability",
    ),
    (
        "only a single unreported label character counts",
        INSERTION,
        (("_MAX_UNREPORTED_LABEL_CHARACTERS = 4", "_MAX_UNREPORTED_LABEL_CHARACTERS = 1"),),
        "hard",
        "reliability",
    ),
    (
        "allow a very wide unreported label indeed",
        INSERTION,
        (("_MAX_UNREPORTED_LABEL_CHARACTERS = 4", "_MAX_UNREPORTED_LABEL_CHARACTERS = 32"),),
        "hard",
        "reliability",
    ),
    (
        "no base settle after a paste",
        INSERTION,
        (("_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.04", "_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "six times the base settle after a paste",
        INSERTION,
        (("_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.04", "_CLIPBOARD_PASTE_BASE_SETTLE_SECONDS = 0.25"),),
        "hard",
        "reliability",
    ),
    (
        "cap a long paste settle very short",
        INSERTION,
        (("_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.3", "_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.05"),),
        "hard",
        "reliability",
    ),
    (
        "let a long paste settle five times longer",
        INSERTION,
        (("_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 0.3", "_CLIPBOARD_PASTE_MAX_SETTLE_SECONDS = 1.5"),),
        "hard",
        "reliability",
    ),
    (
        "return the clipboard immediately",
        INSERTION,
        (("_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.2", "_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "wait a second before restoring the clipboard",
        INSERTION,
        (("_CLIPBOARD_RESTORE_DELAY_SECONDS = 0.2", "_CLIPBOARD_RESTORE_DELAY_SECONDS = 1.0"),),
        "hard",
        "reliability",
    ),
    (
        "scale the paste settle far less with length",
        INSERTION,
        (("_CLIPBOARD_PASTE_PER_CHARACTER_SECONDS = 0.0005", "_CLIPBOARD_PASTE_PER_CHARACTER_SECONDS = 0.0001"),),
        "hard",
        "reliability",
    ),
    (
        "scale the paste settle four times more with length",
        INSERTION,
        (("_CLIPBOARD_PASTE_PER_CHARACTER_SECONDS = 0.0005", "_CLIPBOARD_PASTE_PER_CHARACTER_SECONDS = 0.002"),),
        "hard",
        "reliability",
    ),
    (
        "retry the clipboard restore five times faster",
        INSERTION,
        (("_CLIPBOARD_RESTORE_RETRY_SECONDS = 0.05", "_CLIPBOARD_RESTORE_RETRY_SECONDS = 0.01"),),
        "hard",
        "reliability",
    ),
    (
        "leave longer between clipboard restore attempts",
        INSERTION,
        (("_CLIPBOARD_RESTORE_RETRY_SECONDS = 0.05", "_CLIPBOARD_RESTORE_RETRY_SECONDS = 0.2"),),
        "hard",
        "reliability",
    ),
    (
        "give up on restoring the clipboard sooner",
        INSERTION,
        (("_CLIPBOARD_RESTORE_TIMEOUT_SECONDS = 0.8", "_CLIPBOARD_RESTORE_TIMEOUT_SECONDS = 0.3"),),
        "hard",
        "reliability",
    ),
    (
        "keep trying to restore the clipboard far longer",
        INSERTION,
        (("_CLIPBOARD_RESTORE_TIMEOUT_SECONDS = 0.8", "_CLIPBOARD_RESTORE_TIMEOUT_SECONDS = 2.5"),),
        "hard",
        "reliability",
    ),
    (
        "one more apply before asking for a fresh capture",
        HUD,
        (("_APPLY_RETRY_BEFORE_RECAPTURE = 2", "_APPLY_RETRY_BEFORE_RECAPTURE = 3"),),
        "hard",
        "reliability",
    ),
    (
        "never recapture until ten applies have failed",
        HUD,
        (("_APPLY_RETRY_BEFORE_RECAPTURE = 2", "_APPLY_RETRY_BEFORE_RECAPTURE = 10"),),
        "hard",
        "reliability",
    ),
    (
        "never suppress a recapture",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "suppress recapture for twenty seconds",
        HUD,
        (("_RECAPTURE_SUPPRESS_SECONDS = 3.0", "_RECAPTURE_SUPPRESS_SECONDS = 20.0"),),
        "hard",
        "reliability",
    ),
    (
        "do not retry a cold model at all",
        HUD,
        (("_WARM_RETRY_ATTEMPTS = 4", "_WARM_RETRY_ATTEMPTS = 1"),),
        "hard",
        "reliability",
    ),
    (
        "retry a cold model until it warms",
        HUD,
        (("_WARM_RETRY_ATTEMPTS = 4", "_WARM_RETRY_ATTEMPTS = 12"),),
        "hard",
        "reliability",
    ),
    (
        "check a warming model four times as often",
        HUD,
        (("_WARM_RETRY_DELAY_SECONDS = 20.0", "_WARM_RETRY_DELAY_SECONDS = 5.0"),),
        "hard",
        "reliability",
    ),
    (
        "a full minute between warm retries",
        HUD,
        (("_WARM_RETRY_DELAY_SECONDS = 20.0", "_WARM_RETRY_DELAY_SECONDS = 60.0"),),
        "hard",
        "reliability",
    ),
    (
        "never remember that a pack failed",
        POLISH,
        (("_FAILED_PACK_TTL_SECONDS = 300.0", "_FAILED_PACK_TTL_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "remember a failed pack for an hour",
        POLISH,
        (("_FAILED_PACK_TTL_SECONDS = 300.0", "_FAILED_PACK_TTL_SECONDS = 3600.0"),),
        "hard",
        "reliability",
    ),
    (
        "never remember a validation failure",
        POLISH,
        (("_VALIDATION_FAILURE_TTL_SECONDS = 0.25", "_VALIDATION_FAILURE_TTL_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "remember a validation failure for five seconds",
        POLISH,
        (("_VALIDATION_FAILURE_TTL_SECONDS = 0.25", "_VALIDATION_FAILURE_TTL_SECONDS = 5.0"),),
        "hard",
        "reliability",
    ),
    (
        "fall back after a single bad answer",
        POLISH,
        (("_INVALID_RESPONSE_STREAK = 3", "_INVALID_RESPONSE_STREAK = 1"),),
        "hard",
        "reliability",
    ),
    (
        "tolerate eight bad answers before falling back",
        POLISH,
        (("_INVALID_RESPONSE_STREAK = 3", "_INVALID_RESPONSE_STREAK = 8"),),
        "hard",
        "reliability",
    ),
    (
        "count failures only within five seconds",
        POLISH,
        (("_FAILURE_STREAK_WINDOW_SECONDS = 30.0", "_FAILURE_STREAK_WINDOW_SECONDS = 5.0"),),
        "hard",
        "reliability",
    ),
    (
        "count failures across five minutes",
        POLISH,
        (("_FAILURE_STREAK_WINDOW_SECONDS = 30.0", "_FAILURE_STREAK_WINDOW_SECONDS = 300.0"),),
        "hard",
        "reliability",
    ),
    (
        "never cache which model is loaded",
        HARDWARE,
        (("_LOADED_MODEL_CACHE_SECONDS = 120.0", "_LOADED_MODEL_CACHE_SECONDS = 0.0"),),
        "hard",
        "reliability",
    ),
    (
        "trust the loaded-model cache for fifteen minutes",
        HARDWARE,
        (("_LOADED_MODEL_CACHE_SECONDS = 120.0", "_LOADED_MODEL_CACHE_SECONDS = 900.0"),),
        "hard",
        "reliability",
    ),
    (
        "put no separation requirement on a compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 0"),),
        "8",
    ),
    (
        "put no separation requirement on a compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 0"),),
        "12",
    ),
    (
        "put no separation requirement on a compound split",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 0"),),
        "17",
    ),
    (
        "demand a very wide separation before splitting",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 4"),),
        "8",
    ),
    (
        "demand a very wide separation before splitting",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 4"),),
        "12",
    ),
    (
        "demand a very wide separation before splitting",
        SPELLING,
        (("_COMPOUND_MINIMUM_EDIT_SEPARATION = 3", "_COMPOUND_MINIMUM_EDIT_SEPARATION = 4"),),
        "17",
    ),
    (
        "let a much weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 12"),),
        "8",
    ),
    (
        "let a much weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 12"),),
        "12",
    ),
    (
        "let a much weaker frequency lead break a tie",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 12"),),
        "17",
    ),
    (
        "demand an overwhelming frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 80"),),
        "8",
    ),
    (
        "demand an overwhelming frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 80"),),
        "12",
    ),
    (
        "demand an overwhelming frequency lead",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO = 30", "_DOMINANT_FREQUENCY_RATIO = 80"),),
        "17",
    ),
    (
        "barely protect probable names",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 20"),),
        "8",
    ),
    (
        "barely protect probable names",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 20"),),
        "12",
    ),
    (
        "barely protect probable names",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 20"),),
        "17",
    ),
    (
        "protect probable names very strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 120"),),
        "8",
    ),
    (
        "protect probable names very strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 120"),),
        "12",
    ),
    (
        "protect probable names very strongly",
        SPELLING,
        (("_DOMINANT_FREQUENCY_RATIO_NAMED = 50", "_DOMINANT_FREQUENCY_RATIO_NAMED = 120"),),
        "17",
    ),
    (
        "call a word mangled on weak evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.4"),),
        "8",
    ),
    (
        "call a word mangled on weak evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.4"),),
        "12",
    ),
    (
        "call a word mangled on weak evidence",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.4"),),
        "17",
    ),
    (
        "call a word mangled only when it is certain",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.95"),),
        "8",
    ),
    (
        "call a word mangled only when it is certain",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.95"),),
        "12",
    ),
    (
        "call a word mangled only when it is certain",
        SPELLING,
        (("_MANGLED_CASE_RATIO = 0.7", "_MANGLED_CASE_RATIO = 0.95"),),
        "17",
    ),
    (
        "allow a two letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 2"),),
        "8",
    ),
    (
        "allow a two letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 2"),),
        "12",
    ),
    (
        "allow a two letter head in a run-together split",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 2"),),
        "17",
    ),
    (
        "demand a six letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 6"),),
        "8",
    ),
    (
        "demand a six letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 6"),),
        "12",
    ),
    (
        "demand a six letter head",
        SPELLING,
        (("_RUN_TOGETHER_HEAD = 4", "_RUN_TOGETHER_HEAD = 6"),),
        "17",
    ),
    (
        "let the ranker decide almost every tie",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.002"),),
        "8",
    ),
    (
        "let the ranker decide almost every tie",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.002"),),
        "12",
    ),
    (
        "let the ranker decide almost every tie",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.002"),),
        "17",
    ),
    (
        "return the ranker margin to where it started",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.02"),),
        "8",
    ),
    (
        "return the ranker margin to where it started",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.02"),),
        "12",
    ),
    (
        "return the ranker margin to where it started",
        CLASSIFIER,
        (("_MARGIN = 0.008", "_MARGIN = 0.02"),),
        "17",
    ),
    (
        "allow a shorter remainder after a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 2"),),
        "8",
    ),
    (
        "allow a shorter remainder after a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 2"),),
        "12",
    ),
    (
        "allow a shorter remainder after a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 2"),),
        "17",
    ),
    (
        "demand more words survive a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 7"),),
        "8",
    ),
    (
        "demand more words survive a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 7"),),
        "12",
    ),
    (
        "demand more words survive a syntax edit",
        SYNTAX,
        (("_MINIMUM_REMAINING_WORDS = 4", "_MINIMUM_REMAINING_WORDS = 7"),),
        "17",
    ),
    (
        "treat a shorter sentence as too long to edit",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 40"),),
        "8",
    ),
    (
        "treat a shorter sentence as too long to edit",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 40"),),
        "12",
    ),
    (
        "treat a shorter sentence as too long to edit",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 40"),),
        "17",
    ),
    (
        "let syntax edits reach much longer sentences",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 100"),),
        "8",
    ),
    (
        "let syntax edits reach much longer sentences",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 100"),),
        "12",
    ),
    (
        "let syntax edits reach much longer sentences",
        SYNTAX,
        (("_MAXIMUM_SENTENCE_WORDS = 60", "_MAXIMUM_SENTENCE_WORDS = 100"),),
        "17",
    ),
    (
        "remove far fewer words in one syntax edit",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 10"),),
        "8",
    ),
    (
        "remove far fewer words in one syntax edit",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 10"),),
        "12",
    ),
    (
        "remove far fewer words in one syntax edit",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 10"),),
        "17",
    ),
    (
        "allow a syntax edit to remove twice as much",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 60"),),
        "8",
    ),
    (
        "allow a syntax edit to remove twice as much",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 60"),),
        "12",
    ),
    (
        "allow a syntax edit to remove twice as much",
        SYNTAX,
        (("_MAXIMUM_REMOVED_WORDS = 30", "_MAXIMUM_REMOVED_WORDS = 60"),),
        "17",
    ),
    (
        "treat only the first paragraph as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 1"),),
        "8",
    ),
    (
        "treat only the first paragraph as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 1"),),
        "12",
    ),
    (
        "treat only the first paragraph as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 1"),),
        "17",
    ),
    (
        "treat the first six paragraphs as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 6"),),
        "8",
    ),
    (
        "treat the first six paragraphs as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 6"),),
        "12",
    ),
    (
        "treat the first six paragraphs as introduction",
        SYNTAX,
        (("_INTRODUCTION_PARAGRAPHS = 3", "_INTRODUCTION_PARAGRAPHS = 6"),),
        "17",
    ),
    (
        "call a sentence long at thirty words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 30"),),
        "8",
    ),
    (
        "call a sentence long at thirty words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 30"),),
        "12",
    ),
    (
        "call a sentence long at thirty words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 30"),),
        "17",
    ),
    (
        "only call a sentence long past seventy words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 70"),),
        "8",
    ),
    (
        "only call a sentence long past seventy words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 70"),),
        "12",
    ),
    (
        "only call a sentence long past seventy words",
        ADVICE,
        (("LONG_SENTENCE_WORDS = 45", "LONG_SENTENCE_WORDS = 70"),),
        "17",
    ),
)

def apply(entry, root: Path) -> bool:
    """Make this entry's change. True when something actually changed.

    A substitution whose result is ALREADY in the file is not a failure, and
    treating it as one halted the first run. Entry 1 raised the tie-break ratio
    to 30 and was KEPT, so the entry combining it with a wider lookup could no
    longer find `= 50` and the queue stopped with two experiments unrun.
    Combination entries follow their own parts by construction, so this is the
    normal case rather than an edge case.

    A substitution matching NEITHER form is still a hard error: the anchor is
    wrong, and an experiment that silently changed nothing would be recorded as
    a discard and read as an idea that failed.
    """
    relative, edits = entry[1], entry[2]
    path = root / relative
    text = path.read_text(encoding="utf-8")
    changed = False
    for before, after in edits:
        if before in text:
            text = text.replace(before, after, 1)
            changed = True
        elif after not in text:
            raise SystemExit(f"substitution did not match in {relative}: {before!r}")
    path.write_text(text, encoding="utf-8")
    return changed


def stale_anchors(indices, root: Path) -> list[tuple[int, str, str]]:
    """Every pending entry whose anchor matches neither form in the tree.

    Reported ALL AT ONCE and before the run starts, because finding them one at
    a time is what this actually costs. A kept experiment rewrites the file that
    later entries are anchored to, so drift is a normal consequence of the queue
    working, not a mistake in it.

    Twice now that has stopped a run. `apply` records the first: entry 1 raised
    the tie-break ratio and was kept, so a later entry could no longer find
    `= 50`. The second was 2026-09-08 - entry 64 was kept at index 64 of 212,
    moving _COMPOUND_MINIMUM_EDIT_SEPARATION from 2 to 1, and NINE later entries
    were left anchored to `= 2`. Found one at a time they are nine halts and
    nine restarts, each after the queue has run for however long it takes to
    reach the next one. Found together they are one edit.

    This cannot see drift a keep will cause LATER in the same run - only the
    tree as it stands. That is the point at which the accumulated drift from
    every previous run is visible, and it is the case that has cost the time.
    """
    found: list[tuple[int, str, str]] = []
    for index in indices:
        entry = QUEUE[index]
        description, relative, edits = entry[0], entry[1], entry[2]
        slice_name = entry[3]
        kind = entry[4] if len(entry) > 4 else "essays"
        if already_measured(description, slice_name, kind):
            # No anchor is needed for an entry that will be skipped anyway.
            continue
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except OSError as reason:
            found.append((index, description, f"cannot read {relative}: {reason}"))
            continue
        for before, after in edits:
            if before not in text and after not in text:
                found.append(
                    (index, description, f"{relative}: {before!r}")
                )
                break
    return found


def already_measured(description: str, slice_name: str, kind: str) -> bool:
    """Is there already a row in the ledger for this exact experiment?

    Curtis, 2026-09-03: we should not repeat experiments. Without this, every
    restart of the queue re-runs everything it already did - and the queue gets
    restarted often, because a night gets interrupted, a defect gets fixed, or
    the machine gets used. Eleven entries had been measured when this was added,
    and starting again at the same index would have spent an hour re-measuring
    them.

    Matched on the slice tag AND the description, so the same idea measured on a
    different slice is a different experiment and still runs.
    """
    tag = autoresearch_cycle.slice_tag(slice_name, kind)
    try:
        lines = autoresearch_cycle.RESULTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    wanted = f"{tag} {description}"
    for line in lines[1:]:
        parts = line.split("	")
        if len(parts) == 5 and parts[3] != "void" and parts[4].startswith(wanted):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=int, default=0, help="skip earlier entries")
    parser.add_argument(
        "--essays",
        default="",
        help="override every entry's slice. Normally leave this alone: each "
        "entry names the essay holding the word it targets.",
    )
    arguments = parser.parse_args(argv)

    pending = [i for i in range(len(QUEUE)) if i >= arguments.start]
    # EVERY bad anchor, before the first experiment rather than during the
    # fifth hour. See stale_anchors: drift is what a keep does to the entries
    # behind it, and finding it one halt at a time has now cost two runs.
    stale = stale_anchors(pending, ROOT)
    if stale:
        print(  # noqa: T201
            f"{len(stale)} queue entries are anchored to text that is no "
            f"longer in the tree, most likely because an earlier experiment "
            f"was kept. Re-point them at what the file says now; each one's "
            f"target value still means what its description says.",
            flush=True,
        )
        for index, description, where in stale:
            print(f"  STALE {index}: {description}", flush=True)  # noqa: T201
            print(f"         {where}", flush=True)  # noqa: T201
        return 1
    total = len(pending)
    # Where the ledger stood when this run began, so the panel counts THIS
    # night's results rather than every experiment ever recorded.
    started_at = autoresearch_status.ledger_length()
    last_result = "-"
    autoresearch_status.start_monitor()

    while pending:
        index = pending.pop(0)
        entry = QUEUE[index]
        description, _relative, _edits, slice_name = entry[:4]
        # "essays" unless the entry says otherwise, so every entry written
        # before tones existed keeps its exact meaning.
        kind = entry[4] if len(entry) > 4 else "essays"
        if kind == "essays":
            slice_name = arguments.essays or slice_name
        if already_measured(description, slice_name, kind):
            # A row exists, so this question has an answer. Re-running it would
            # spend five minutes to write the same row twice.
            print(  # noqa: T201
                f"--- {index} already measured: {description}", flush=True
            )
            continue
        print(f"=== {index} ({kind} {slice_name}): {description}", flush=True)  # noqa: T201
        # THE PANEL, UPDATED PER ENTRY.
        #
        # Curtis, 2026-09-04: "it is not being updated with the results and does
        # not indicate that it is working correctly." Nothing had ever written
        # kept, discarded or crashed, so the monitor rendered its own zeroes from
        # the day it was built, and a working loop was indistinguishable from a
        # stuck one.
        #
        # Counts come from the ledger, not from a variable, because the ledger is
        # what anybody can check afterwards. A count held in memory and a row
        # written to disk are two things that can disagree, and the panel would
        # be the one nobody could audit.
        completed = total - len(pending) - 1
        autoresearch_status.write_status(
            {
                "phase": f"{kind} {slice_name}   entry {completed + 1} of {total}",
                "description": description,
                "essays": slice_name,
                "experiment": autoresearch_cycle._git("rev-parse", "--short", "HEAD"),
                "baseline": f"{autoresearch_cycle.current_baseline(slice_name, kind):.1f}",
                "last_result": last_result,
                **autoresearch_status.tally(since_line=started_at),
            }
        )
        if autoresearch_cycle.missing_baseline(slice_name, kind):
            # No baseline row for this slice means the bar is zero and the next
            # result is kept whatever it scores. That has already happened twice:
            # essays 4 and 6 were judged against 0.00, and only escaped being
            # wrong because both happened to score 100.
            #
            # Refusing here rather than warning, because a keep recorded against
            # no baseline is worse than a missing experiment - it puts a number
            # in results.tsv that nothing supports, and every later comparison on
            # that slice inherits it.
            print(  # noqa: T201
                f"NO BASELINE for {kind} {slice_name}. Measure it unchanged and "
                f"record the row before running experiments on this slice.",
                flush=True,
            )
            return 2
        try:
            changed = apply(entry, ROOT)
        except SystemExit as reason:
            # Loud, and it stops. A bad anchor makes every later entry suspect,
            # and a queue that skipped one would leave a gap in results.tsv that
            # nobody would ever notice.
            print(f"SKIPPED {index}: {reason}", flush=True)  # noqa: T201
            return 1
        if not changed:
            # Everything this entry asks for is already in the tree, because an
            # earlier entry was kept. There is nothing to measure and the cycle
            # would return NOCHANGE, so say so and carry on.
            print(f"ALREADY IN {index}: {description}", flush=True)  # noqa: T201
            continue
        # A tone is measured by the judge's pass percentage and a slice of
        # essays by the corpus score. They are different numbers on different
        # scales, so they need different cycles; sending a tone through the
        # correction cycle would record a flat corpus score and read as a
        # failed idea.
        if kind == "tone":
            command = [
                sys.executable,
                str(ROOT / "tools/autoresearch_tone_cycle.py"),
                "--tone",
                slice_name,
                "--describe",
                description,
            ]
        elif kind == "reliability":
            # Completion rate over test-corpus/document-shapes. Added because
            # the queue could dispatch only essays and tone, so the reliability
            # metric built on 2026-09-01 had to be run by hand, one experiment
            # per prompt - which is the exact thing this queue exists to stop.
            command = [
                sys.executable,
                str(ROOT / "tools/autoresearch_reliability_cycle.py"),
                "--slice",
                slice_name,
                "--describe",
                description,
            ]
        elif kind.startswith("essays-"):
            # A single error class: essays-grammar, essays-punctuation and so
            # on. The correction cycle knows the metric; the slice name is
            # still the essay numbers.
            command = [
                sys.executable,
                str(ROOT / "tools/autoresearch_cycle.py"),
                "--essays",
                slice_name,
                "--metric",
                kind.split("-", 1)[1],
                "--describe",
                description,
            ]
        else:
            command = [
                sys.executable,
                str(ROOT / "tools/autoresearch_cycle.py"),
                "--essays",
                slice_name,
                "--describe",
                description,
            ]
        done = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # Two hours, not forty minutes. A tone experiment restarts the
            # app, drives three essays through Word, and waits up to 300
            # seconds per slow section before judging with a 27B model - and
            # the 2400 second ceiling killed the Friendly experiment mid-run on
            # 2026-08-31. That is the worst possible outcome: the cycle had
            # already COMMITTED the change, so the timeout left an unmeasured
            # edit in the tree with no keep, no discard and no row, which is the
            # one state this loop must never produce.
            timeout=7200,
        )
        printed = (done.stdout or "").strip()
        print(printed or "no output", flush=True)  # noqa: T201
        # The cycle prints exactly one result line: KEEP 94.5, DISCARD 94.0 or
        # CRASH dead. That line is what a person wants to see on the panel.
        for line in reversed(printed.splitlines()):
            if line.split(" ")[0] in ("KEEP", "DISCARD", "CRASH", "SKIPPED"):
                last_result = f"{line[:34]}  ({slice_name})"
                break
        # ONE branch, and only one: exit 2 means the cycle measured NOTHING
        # because somebody is at the keyboard. It is not a result.
        #
        # Without this the queue would march through the entire list while a
        # person worked, and every entry would come back unmeasured. Because a
        # skip records no row, the ideas would not even be marked as tried -
        # they would simply be gone, and the loop would report a night's work
        # having tested nothing.
        #
        # So wait for the machine to go quiet and RETRY THE SAME ENTRY. The
        # spec's own words for exit 2 are "come back later".
        if done.returncode == _SKIPPED_NOBODY_HOME:
            print(  # noqa: T201
                f"WAITING {_SKIP_WAIT_SECONDS:.0f}s, then retrying {index}",
                flush=True,
            )
            time.sleep(_SKIP_WAIT_SECONDS)
            pending.insert(0, index)
    # The panel disappears when the work does. No file, no window.
    autoresearch_status.clear_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
