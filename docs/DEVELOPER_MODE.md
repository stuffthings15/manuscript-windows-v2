# Developer Dictation Mode

Developer mode turns explicit spoken formatting commands into text. It is deterministic, local-only, and does not call Ollama, run a shell, execute code, or open files.

## Use it

1. Select **Developer** under Writing mode.
2. Hold the dictation hotkey, speak the text and commands, then release.
3. Review the result when the target is sensitive.

## Commands

| Say | Result |
|---|---|
| `camel case user name` | `userName` |
| `snake case user name` | `user_name` |
| `pascal case api response` | `APIResponse` |
| `kebab case feature branch` | `feature-branch` |
| `constant case api key` | `API_KEY` |
| `filename main dot py` | `main.py` |
| `tag file app slash routes dot ts` | `@app/routes.ts` |
| `git status dash dash short` | `git status --short` |
| `new line`, `new paragraph` | line breaks |
| `indent`, `dedent`, `tab` | local whitespace |

Core syntax includes parentheses, square brackets, braces, quotes, comma, colon, semicolon, dot, slash, backslash, operators, arrows, pipes, underscores, and dashes.

## Safety

- Ordinary prose is kept conservative.
- Existing URLs, email addresses, paths, filenames, and dotted identifiers are protected.
- Custom vocabulary remains local and can provide project terms or exact identifiers.
- The output is only text. A terminal will execute it only if the user separately submits it.
