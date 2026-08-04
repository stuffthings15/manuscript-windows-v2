# Echo-Link Legacy Feature Parity

Source of truth for the original Windows dictation behavior:

- `C:\Users\stuff\Documents\Vedit-AI\app\floating_bar.py`
- `C:\Users\stuff\Documents\Vedit-AI\app\settings_window.py`
- `C:\Users\stuff\Documents\Vedit-AI\core\pipeline.py`
- `C:\Users\stuff\Documents\Vedit-AI\docs\archive\dictation_app_legacy.py`

This list prevents the standalone Echo-Link rebuild from silently dropping working behavior.

| Original behavior | Current state | Evidence or next work |
|---|---|---|
| Global push-to-talk | Done | Live hook probe and hotkey tests |
| Paste into the app active at hotkey press | Partial | Captured-target Auto/Clipboard/Direct modes, a repeatable full workflow probe, live Word, external textbox, chat-like multiline, and real Windows terminal passes; real third-party chat and elevated-app acceptance remain |
| Floating status bar | Done | Focus-safe Echo Bar shows state/level, accepts drag without focus, and restores saved screen-safe placement |
| Start notification sound | Done | Local Windows `Speech On.wav`, enabled by default |
| Stop notification sound | Done | Local Windows `Speech Off.wav`, enabled by default |
| Sound mute setting | Done | Settings toggle disables both cues |
| Selectable cue file and Test Audio | Done | Start/stop WAV selectors, local file browser, and Test buttons |
| Bluetooth speaker wake delay | Partial | Auto detection and configurable 0-2000 ms delay exist; physical Bluetooth proof remains |
| Microphone selector | Done | System default or local input device |
| Output-device selector | Done | System default or discovered local output routes WAV cue playback |
| 500 ms pre-roll buffer | Done | Configurable memory-only ring buffer; selected microphone passed a live ready/close probe |
| Language selection | Partial | Common languages and local packs exist; hardware coverage remains |
| Streaming local preview | Done | Stable-prefix local preview |
| Local-only status | Done | Echo Bar says `LOCAL ONLY`; privacy gate blocks hosted endpoints |
| Windows startup | Done | Existing startup setting |
| System tray availability | Done | Show, Dictate, Settings, Quit, and close-to-tray are implemented |
| Clipboard preservation | Done | Conditional delayed restore does not overwrite newer clipboard content |
| Safe undo | Done | Same-window guarded undo |
| Custom vocabulary | Done | Local SQLite vocabulary |
| Snippets | Done | Local exact trigger expansion |
| Optional local history | Done | Off by default |
| Quiet speech mode | Partial | Gain and VAD tuning exist; live hardware proof remains |
| Automatic hardware/model choice | Done | Fresh profiles apply the verified installed-model recommendation once; one-click selection and an optional local speed check remain available in Settings |
| Selected-text command mode | Partial | Global capture hotkey, local rewrite menu, human review, protected tokens, and guarded replacement pass Qt, Chrome, and VS Code; Word and chat remain |
| Hands-free dictation hotkey | Done | Separate global toggle chord plus tray action; Esc discards active audio |
| Silence auto-stop and maximum duration | Done | Sustained-speech gate, configurable 2.5-second silence stop, and hard limit |
| Pause/resume other media | Done | Local Windows media sessions pause only when playing; Echo-Link resumes only sessions it paused |

## Next parity order

1. Real third-party chat and elevated-window insertion acceptance.
2. Physical Bluetooth and wider microphone acceptance.
