# Manuscript V2 and Windows Repair Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a safe, separate Manuscript Windows Version 2 workspace and repair the existing Windows app through repeatable real-user testing until the locked 20-essay corpus and full product workflow pass.

**Architecture:** Keep copying/version isolation separate from correction and UI repair. Use a clean, manifest-bound source snapshot for Version 2, then run correction work in an isolated branch with fast layer probes, real local-AI section probes, real UI Automation click-throughs, and GPT-5.6 Sol review through this interface. Promote no change without regression evidence.

**Tech Stack:** Python 3.12, PySide6, pytest, Windows UI Automation, Notepad, Microsoft Word, local AI runtime, PyInstaller, Inno Setup, PowerShell, Git, Android Kotlin/Gradle only if the naming clarification selects Android.

## Global Constraints

- Planning assumption: the requested source path and product name mean a second Windows app. If “Android version” was literal, stop before copying and use `C:\Users\stuff\Documents\Echo-Link\Manuscript\Android` as the source instead.
- Preserve the original Windows and Android folders. Never overwrite either.
- Keep all correction, dictation, and document content local.
- Every manual correction section must attempt real local AI. A failed AI attempt may expose a safe deterministic correction, but the run still fails the AI gate and must be repaired.
- Manuscript-generated text must never contain an em dash.
- Preserve names, numbers, dates, locations, negations, obligations, ordering, identifiers, quotations, paragraph boundaries, and document formatting at 100 percent on the locked corpus.
- Keep development entitlement bypass separate from production subscription enforcement.
- Do not publish, upload, sign, or sell a build during this repair plan.
- “Perfect” means no defect identified by the locked automated gates and the final GPT-5.6 Sol review of these fixtures. It is not a guarantee for every future sentence.
- Current source anchor: Windows subtree is clean at commit `b2baa9442858a8f20218d50d0f9d1d235ec17e55`.
- Corpus anchor: `20_Animal_Pet_Essays_800_Percent_More_Errors.zip`, SHA-256 `C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771`, containing exactly 20 DOCX essays plus `README.txt`.

---

## Execution Order

1. Execute [Manuscript Windows Version 2 Copy Plan](2026-08-04-manuscript-windows-v2-copy-plan.md).
2. Execute [Manuscript Windows Full Repair Loop](2026-08-04-manuscript-windows-full-repair-loop.md).
3. Keep verified fixes in the Version 2 branch until Curtis approves promotion to the original Windows app.
4. Rebuild only the testing-unlocked executable and installer for owner validation.
5. Record physical Microsoft Word results separately from automated evidence.

## Current Baseline

- `build/animal-essay-corpus-all.json` contains only 14 of 20 attempted essays.
- Essays 03 and 04 failed from a missing badge or stuck `1 of 12` counter.
- Essays 08 through 14 include missing replacement/skip controls or section timeouts.
- Essay 07 completed only by skipping seven sections.
- Six essays were never reached.
- Latest AI Brain diagnosis measured 159 of 200 sections vetoed before the recent comparative-quality and spelling fixes. The new baseline must be measured again from commit `b2baa94`; old numbers are diagnostic history, not current proof.

## Completion Gate

- Version 2 exists in its own folder, Git repository, data directory, installer identity, process identity, and hotkey namespace.
- The original app and corpus remain byte-for-byte intact.
- All 20 essays complete through the visible HUD with zero skipped sections, zero repeated sections, zero stale replacements, and every section receiving a real AI attempt.
- GPT-5.6 Sol finds no spelling, grammar, punctuation, vocabulary, sentence-structure, tone, factual, or formatting defect in any final essay.
- Every user-visible feature passes its automated or physical user-journey check three consecutive times.
- Full tests, privacy checks, packaged smoke, testing-unlocked installer lifecycle, and offline checks pass.
- Any unautomated physical gap is listed plainly. No completion claim hides it.
