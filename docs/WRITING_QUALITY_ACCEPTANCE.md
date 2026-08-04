# Local Writing Quality Acceptance

Echo-Link uses a locked set of 15 synthetic cases to expose writing-model gaps without uploading or retaining user text. There are three cases each for grammar/punctuation, style/tone, rewrite meaning, summary, and translation.

## What the gate proves

- The corpus and scorer are deterministic and work offline.
- The reference self-check passes 15/15.
- The live probe accepts only exact `127.0.0.1` loopback Ollama.
- Every live case uses the configured local model and current writing pipeline.
- Evidence contains case IDs, checks, counts, and hashes only. It contains no source, prompt, response, or model name.

## Current live result

| Local model | Strict cases passed | Execution refusals | Result |
|---|---:|---:|---|
| Configured 3B model | 5/15 | 1 | Not release-grade |
| Installed 14B comparison | 6/15 | 1 | Better grammar; still not release-grade |

The fixed checks are deliberately narrow. Passing them does not prove general language quality. Failing a marker can also mean the model used a valid synonym. Human review remains required for style, meaning, and fluent translation.

## Run it

```powershell
.\.venv\Scripts\python.exe .\tools\evaluate_writing_quality.py --reference-self-check --check
.\.venv\Scripts\python.exe .\tools\writing_quality_live_probe.py
```

The live command exits nonzero until every strict case passes, but still writes complete content-free evidence to `evidence\writing-quality-live.json`.

## Release gate

Do not describe local writing as release-grade until the chosen shipping model passes an approved threshold and a human reviews grammar, tone, summary, meaning preservation, French, Spanish, and German outputs. The current evidence is a measured gap, not a quality claim.
