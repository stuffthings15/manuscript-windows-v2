# Windows release implementation ledger

User-authorized target: every agreed essay error corrected without introduced
errors or changed meaning/formatting; all advertised Windows workflows verified;
a sellable Windows release. This is not an academic-grade guarantee.

## Contract and test profile

Authority: the user's approved implementation plan in Codex task
01a07562-c1ea-7801-b3ab-ec6b1ad49799, plus AI Brain/Echo-Link context and handoff.
This file is a local implementation contract, not a signed AI Brain service receipt.
The configured continuity profile store contains no project profiles. The legacy
session capture works; active service contract/profile bootstrap remains open.

- Work only in Windows Version 2; preserve existing untracked model assets.
- Keep program.md, scorer thresholds, protected-content guards and corpora intact.
- No hosted inference, new model download, purchase or public release here.
- Live desktop tests require an uninterrupted agreed window and disposable files.
- Check each changed behavior with a regression test, then run the full Python
  suite and repository lint. Website security/account tests are a separate gate.
- Source/unit results do not prove installed behavior or human microphone quality.

## Implemented acceptance changes

- Each acceptance invocation has a unique directory under build/acceptance.
- Each Word step has a separate output root, containing its documents, summary
  and correction reports; direct batch calls retain their old default paths.
- Grading refuses absent, stale, malformed, duplicate, unexpected or missing
  batch evidence. Failed, timed-out, dismissed, or unfinished runs cannot pass.
- Completion requires the app's explicit current-run completion statement.
  A final counter inside a missing-HUD diagnostic is insufficient.
- Missing build identity blocks measurement; abnormal test-process exit blocks
  acceptance; failed fast prerequisites skip the full desktop tier.
- Each palette tone now requires all twenty coherent essays and section-weighted
  JSON judge results. Missing judge results are invalid, never a score of zero.

These checks do NOT yet prove saved-document text/format fidelity, actual served
model identity, exact error correction, independent holdout quality, or user
journey completion. The current heuristic scorer remains unchanged.

## User-journey checklist

Entries remain UNVERIFIED except the explicitly scoped evidence below. Existing unit
tests are supporting evidence only. Record steps, expected/actual results,
build/model/profile, screenshots, final document hashes, and failure reason.

| ID | User steps | Required result | Existing starting point |
|---|---|---|---|
| W01 | Capture Word essay; move caret while AI works; accept each section | No reset, lost edit, or wrong target | run_essays_batch.py |
| W02 | Open twenty essays consecutively in one session | Each explicit completion and correct saved content | --as-a-writer |
| W03 | Review nested/restarted/mixed lists three times | Numbering and formatting preserved | document-shapes corpus |
| W04 | Select repeated text at different offsets; change selection | Only current selected occurrence changes | selected_text_matrix_probe.py |
| W05 | Accept; type a new edit; Undo | Guard prevents overwriting later user work | writing_hud_apply_probe.py |
| W06 | Cancel/close while generation runs; open another document | Late output never changes new review | writing_hud_interaction_loop_probe.py |
| W07 | Apply every tone to corpus and unseen essays | 95% section gate plus zero factual damage | release_acceptance.py --full |
| W08 | Expand, summarize, translate; review then accept | Action-specific output with protected content intact | full_page_user_journey_probe.py |
| D01 | Hold hotkey, speak, release in each dictation mode | Correct insertion once in captured target | windows_workflow_probe.py |
| D02 | Start hands-free; pause; cancel; hit recording limit | Expected stop/discard behavior | hands_free_live_probe.py |
| D03 | Speak quietly; unplug/reconnect microphone | Useful recovery, no freeze or duplicated output | microphone_level_probe.py |
| D04 | Enable partial text; speak corrections and vocabulary | Final output agrees with approved review | streaming_probe.py |
| I01 | Insert in Word/Outlook/Notepad/editor/terminal | Correct target, readable refusal for unsupported access | insertion_matrix_probe.py |
| I02 | Paste Unicode/emoji using each insertion mode | No partial corruption; clipboard restored where used | external_insertion_acceptance_probe.py |
| I03 | Stage private paste; insert; clear | Memory-only staging and unchanged system clipboard | windows_workflow_probe.py |
| B01 | Install browser bridge; replace selected field text | Reviewed replacement in Chrome and Edge | selected_text_browser_probe.py |
| B02 | Focus password/noneditable field | No capture or replacement | selected_text_browser_probe.py |
| F01 | Create, edit, save, reopen, export document | Exact content and persisted revision | test_document_gui.py plus manual |
| F02 | Restore old revision; reopen history | New revision, old history immutable | test_document_gui.py plus manual |
| F03 | Close/import with unsaved edits; cancel prompts | No silent loss | test_document_gui.py plus manual |
| S01 | Approve source; search; ask; remove source | Correct excerpts/citations, session-only scope | local_chat_probe.py |
| S02 | Missing/empty/unsupported source request | Clear error, no invented citation | local_chat_probe.py |
| P01 | Change shortcuts, cues, routing, tray, startup; restart | Persisted settings and working controls | echo_bar_live_probe.py |
| P02 | Import/remove model; corrupt import; run speed check | Integrity checks and safe recovery | model_coverage_probe.py |
| P03 | Add snippets/vocabulary; enable/disable/delete learning | Correct behavior and explicit consent | test_settings.py plus manual |
| R01 | Enable history; set retention; purge isolated profile | Verified storage/retention/deletion behavior | privacy_check.py |
| R02 | Simulate model failure, low memory, failed save, restart | Bounded waits and safe recovery | reliability_fault_probe.py |
| A01 | Navigate by keyboard and screen reader at 100-200% scale | Reachable controls, useful labels, no traps | control_surface_gate.py plus manual |
| A02 | Move between displays; drag bar; reopen dialogs | Visible usable placement, responsive dialogs | echo_bar_live_probe.py |
| L01 | Install/upgrade/repair/uninstall on clean Windows | Launch success and data preservation | installer_lifecycle_probe.py |
| L02 | Activate/cancel/expire/recover subscription; service outage | Correct entitlement without content transfer | website test suite plus sandbox |
| L03 | Signed installed app and bridge; offline local writing | Verified signatures and packaged workflows | packaged --workflow-probe |
| L04 | Eight-hour mixed workflow soak | No crash, stuck review, loss, or unbounded growth | sequential journey schedule |

## Remaining release blockers

Fresh Word reproduction; document readback/format proof; model provenance;
reviewed gold annotations and untouched holdouts; real feature journeys and soak;
clean Windows installation; signing; payment sandbox and owner launch inputs.
No 100% quality or release-ready claim is supported yet.


## Verified implementation evidence, 2026-09-06

- Real offscreen Qt mouse/key journeys found and fixed value-versus-identity
  comparisons in confirmation dialogs. Revision restore and Save/Discard/Cancel
  now pass. This does not establish installed-desktop coverage of F01-F03.
- Three sequential live Word runs of shape 03 completed: 21 accepts, no errors
  or dismissals, explicit application completion each time. All three saved
  hashes and paragraph numbering were verified against the original. This is
  partial W03 coverage, not all twenty shapes or a 100% essay-quality claim.
- The batch harness preserves unrelated open Word documents and uses unique
  disposable filenames. The old helper closed other documents without saving;
  earlier runs used that helper, so prior unsaved-work preservation is unknown.
- Completion is logged even when the corrected text was reviewed previously.
- Saved artifact hashes are required and must match the document being graded;
  changed, stale, or missing artifacts cannot pass acceptance.
- Python: 2703 passed, 2 skipped; the process then crashed at Qt shutdown.
  Explicit QApplication.shutdown reproduced the native crash. These results
  are NOT a clean suite pass. Root-cause isolation remains in progress.
- Website: production build, 36 tests, TypeScript and lint passed. Restored the
  missing build plugin to tracked tooling. No public deployment occurred.
- Production build dry-run passed. It only printed build commands; it did not
  produce, sign, install, or qualify a release artifact.

Machine-readable hashes and scoped results: evidence/windows-reliability-2026-09-06.json.
The original scorer and corpus remain unchanged. Full essay/tone/holdout evidence,
all advertised user journeys, crash resolution, eight-hour soak, clean install,
trusted signing, and owner/payment service inputs remain release blockers.


## Follow-up verification

- The native test shutdown defect is repaired in the pytest lifecycle. The
  isolated GUI diagnostic found 2,000 remaining top-level widgets; deleting
  them while Python callbacks and Qt remain alive produced exit 0. The complete
  suite with this cleanup passed 2720 tests, skipped 2, and exited 0.
  No native exit is ignored or converted into a pass.
- Live Word testing exposed a second harness defect: the application rotated
  its one-megabyte log during a review. Line offsets then missed completion and
  commands. File identity plus byte-offset cursors now follow retained logs;
  losing a cursor is an explicit measurement error. Regression tests force
  rotation and loss. The initial mixed-list result is invalid measurement.
- Mixed lists (11) and restarted numbering (12) each completed three consecutive
  runs with the repaired harness: 33 accepts total,
  no errors or dismissals, explicit completion each time. Every saved hash and
  paragraph numbering property matched its source. Together with shape 03,
  this is nine completed runs over three shapes, not the full shape matrix.
- These timings were observed while offscreen tests ran; they are not approved
  latency measurements. The full essay corpus, all tones and holdouts, remaining
  user journeys, eight-hour soak, installed/signing/licensing qualification,
  and owner launch inputs remain incomplete. Release remains blocked.

## Source rollback

Preserve any later working changes, then revert the reliability commits on
codex/windows-reliability-acceptance in newest-first order with git revert.
Restart the source application and rerun the relevant acceptance checks.
Keep corpus originals, saved evidence, and untracked model assets intact.
No installed or public release artifact was replaced by these commits.


## Final receipt, 2026-09-07

The fixes were reviewed and committed by the concurrent project session as
e7d6c9f, following this task's 2d022b3. The 2,720-pass full suite and clean exit
apply to that repair snapshot. Subsequent harness commits culminate at e795cf1.
Fresh focused verification of the current tree passed 91 tests; lint and diff
whitespace checks passed. This is not a fresh full-suite claim for every later
harness change.

The later twenty-document baseline at e795cf1 is 75% workflow completion,
recorded in results.tsv and build/batch-summary.json. Five cases remain
incomplete: 06_table, 08_field_result, 09_internal_hyperlink, 10_nested_bullets,
and 20_mixed_everything. This percentage is not an essay-quality score.

The table repair now maps UIA cell terminators consistently and converts raw
COM text offsets to native Word positions, including UTF-16 characters. Native
range text is checked before assignment and cell-spanning replacements remain
refused. These repairs have regression proof, but repeated live table reviews
still did not finish. The table workflow therefore remains an open release
defect; neither its arithmetic fix nor the clean Python exit qualifies it.

The outstanding open-slice change in tools/autoresearch_reliability_cycle.py
belongs to the concurrent project session and is preserved separately from this
receipt commit. The original model-assets directory remains untouched.

The full implementation plan is not complete. Do not ship or claim 100% essay
acceptance from these results. Finish the five document failures, full essay and
tone checks, holdout review, remaining advertised journeys, soak, and installed
release qualification before changing that status.
