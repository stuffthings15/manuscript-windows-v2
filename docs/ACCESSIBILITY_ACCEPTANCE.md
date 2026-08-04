# Echo-Link Accessibility Acceptance

## Scope and source

This acceptance set implements the desktop accessibility requirements in `Echo-Link_Implementation_Plan.md`: keyboard-only use, logical focus order, accessible names, compact layouts, and Windows display scaling at 100%, 125%, 150%, and 200%.

The controls remain local-only. Accessibility events contain short status text already visible in Echo-Link. They do not contain microphone audio and do not use a network service.

## Implemented behavior

- Every non-obvious input, output, table, status area, section rail, tab set, and microphone meter has a stable accessible name.
- Settings form labels are programmatically associated with their fields.
- Dictation state changes emit Qt screen-reader announcements without stealing focus. Errors use an assertive announcement; normal progress is polite.
- Tab and Shift+Tab follow the visual workflow. Up and Down Arrow change the selected page when the section rail has focus. Space or Enter activates the focused button.
- The main window can shrink to 720 x 520 logical pixels. Settings remain vertically scrollable and do not require horizontal scrolling.
- Echo Bar never accepts focus and now narrows to the available screen width instead of extending off-screen.
- Long status text wraps. The review option has its own row so it is not clipped at compact widths.
- The floating Writing HUD stays inside the available screen at 100%, 125%, 150%, and 200%, uses a vertical scroll area, exposes named suggestion filters and actions, and never requires a horizontal scrollbar. Automatic selection detection shows it beside the accessible selection without moving keyboard focus; clicking the HUD makes its controls available normally.

## Keyboard acceptance path

1. Open Echo-Link. Press Tab from Settings into the section rail and then through Dictate controls.
2. Confirm focus order: Fast, Developer, Refine, Accuracy, Review before insert, Start recording, Undo, review tabs, and review text. Review actions appear only when text is ready.
3. On the section rail, use Up/Down Arrow to change sections.
4. In Writing tools, confirm focus follows Capture, all eight quick actions, command, profile, language, Run, optional direction, Analyze, review text, and review actions.
5. Highlight text in another app. Confirm the Writing HUD appears automatically while keyboard focus stays in the source app. Click the HUD and confirm focus reaches suggestion filters, the suggestion list, Apply suggestion, Apply all, all rewrite/style actions, the editable preview, Replace, private staging, and Reject. Press Escape and confirm the source text is unchanged. Repeat with the manual hotkey fallback.
6. In Drafts, confirm focus reaches draft type, facts, optional direction, Generate, editable result, Save to Documents, Stage private paste, and Reject.
7. In Documents, confirm focus reaches Editor, Revisions, Analytics, and Assistant. Verify document controls, analytics output, all assistant commands, editable review, Apply, Stage private paste, and Reject.
8. In tables, use Arrow keys to move rows and Tab to leave the table.
9. Open Settings. Confirm Tab and Shift+Tab reach every field and button; use Page Up, Page Down, or the mouse wheel to scroll. Check Local Models at 100%, 125%, 150%, and 200%.
10. Press Escape to close a dialog. During hands-free dictation, Escape cancels the active recording.

## Automated checks

Run:

```powershell
python -m pytest tests/test_accessibility.py -q
```

The tests verify:

- required accessible names and form-label associations;
- keyboard focus order for Dictate, Writing tools, Drafts, and all four Documents views;
- polite/assertive screen-reader announcement events;
- layout bounds, text clipping, Settings scrolling, Echo Bar bounds, and device-pixel ratio in fresh Qt processes at 100%, 125%, 150%, and 200% scaling.

## Manual assistive-technology check

Automated Qt metadata does not prove how every Windows screen reader voices the application. Before public release, repeat this short check with Windows Narrator and current NVDA:

1. Disable the screen reader's mouse mode and navigate with the keyboard only.
2. Confirm each section item, field, review editor, table, and microphone level has a useful name and role.
3. Start and stop one local dictation. Confirm Listening, Processing, Done, and a forced microphone error are announced once without moving focus.
4. Repeat at Windows display scaling of 100%, 125%, 150%, and 200%. Check the main window, Settings top/middle/bottom, every section, and Echo Bar.
5. Confirm no control overlaps, no action is covered, and all long status/error text remains readable.

Record the Windows version, screen reader/version, monitor resolution, scale, and pass/fail result with the release evidence.

## Motion

Echo-Link has no decorative animation. Microphone-level changes, state changes, and timed Echo Bar hiding communicate live function rather than visual decoration. No separate reduced-motion mode is required by the current specification. Reassess this if animated transitions are added later.
