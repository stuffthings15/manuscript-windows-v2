# AutoResearch reference pin

Source: https://github.com/karpathy/autoresearch
Pinned commit: `228791fb499afffb54b46200aca536f79142f117` (2026-03-26)

Read-only. Do not edit these files to change Manuscript behaviour; the runtime
port lives in `tools/autoresearch_run.py`, `tools/autoresearch_analysis.py` and
`program.md` at the repository root.

Verified against upstream master on 2026-08-27 in two ways, because comparing
only the files already held cannot detect a file upstream added: the full
recursive tree matched at all ten paths, and every file was byte-identical
ignoring line endings. Upstream had not moved in five months.

Mirrored here on 2026-08-28 so this port does not depend on a sibling
repository. `progress.png` and `uv.lock` are omitted: one is a teaser image and
the other pins PyTorch wheels this project never installs.
