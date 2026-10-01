"""No test harness inside the product a writer installs.

`tools/windows_workflow_probe.py` is not a unit test. It synthesizes keystrokes
through `user32.keybd_event`, spawns a second process, opens the microphone and
writes screenshots to disk. On 2026-09-09 it was found compiled into the
shipped executable and reachable there:

    src/echo_link_app/main.py       from tools import windows_workflow_probe
    EchoLink.spec  hiddenimports    "tools.windows_workflow_probe"
    dist/ManuscriptV2/ManuscriptV2.exe   contains the string

and the gate that was supposed to stop it read

    return not uses_internal_command or not subscription_required

with the shipped `_internal/resources/subscription.json` saying
`"subscription_required": false, "build_channel": "testing-unlocked"` - a plain
text file inside the install directory, editable by anyone who can write to it.

Nothing ran autonomously: the probe needs an explicit command-line flag, and
there is no scheduled task, service or watchdog anywhere in `src/`. The defect
is that the harness was present at all, and that whether it could run depended
on a configuration file being correct rather than on a property of the build.

The runtime gate is correct as designed - a production build sets
`subscription_required` and refuses. It cannot simply be tightened to "refuse
whenever frozen", because `tools/installer_lifecycle_probe.py` drives
`--workflow-probe` against the INSTALLED executable and that is how release
acceptance checks the packaged hotkey, HUD, transcription and insertion path.

So the fix is in packaging: a PRODUCTION build does not contain the harness at
all, a testing build does. That is a property of the build rather than of a
file a user could edit, and it is what this file keeps honest.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The runtime gate is covered by tests/test_subscription.py
# (test_production_runtime_rejects_hidden_test_commands). This file covers
# the packaging barrier, which is the one that was missing.


def _channel_reader(root):
    """The spec's own _build_channel, evaluated against a chosen root."""
    tree = ast.parse((ROOT / "EchoLink.spec").read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_channel"
    )
    namespace = {"ROOT": root}
    exec(compile(ast.Module([helper], []), "<spec>", "exec"), namespace)  # noqa: S102
    return namespace["_build_channel"]


def test_a_testing_build_is_recognised(tmp_path):
    """Release acceptance needs the probe, so this channel must keep it."""
    generated = tmp_path / "build" / "generated"
    generated.mkdir(parents=True)
    (generated / "build_info.json").write_text(
        '{"commit": "abc123", "build_channel": "testing-unlocked"}', encoding="utf-8"
    )
    assert _channel_reader(tmp_path)() == "testing-unlocked"


def test_a_production_build_is_recognised(tmp_path):
    generated = tmp_path / "build" / "generated"
    generated.mkdir(parents=True)
    (generated / "build_info.json").write_text(
        '{"commit": "abc123", "build_channel": "production"}', encoding="utf-8"
    )
    assert _channel_reader(tmp_path)() == "production"


def test_an_unknown_build_is_treated_as_production(tmp_path):
    """The safe default for a build we cannot identify ships less, not more."""
    assert _channel_reader(tmp_path)() == "production"


def test_the_harness_is_only_listed_behind_the_testing_flag():
    """A bare listing would put it in every build, which is what it did."""
    spec = (ROOT / "EchoLink.spec").read_text(encoding="utf-8")
    for line in spec.splitlines():
        if "tools.windows_workflow_probe" in line and not line.strip().startswith("#"):
            assert "IS_TESTING_BUILD" in line, (
                f"windows_workflow_probe is packaged unconditionally: {line.strip()!r}. "
                f"It must be gated on the build channel."
            )


def test_no_shipped_module_imports_the_harness_at_module_scope():
    """A module-scope import puts it in EVERY build, whatever the spec says.

    main.py imports it inside _run_workflow_probe_cli, which only runs when an
    internal command was accepted - so it does not drag the module into a
    production archive. A module-scope import would.
    """
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "tools":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "tools":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        f"Shipped code imports the measurement harness at module scope: "
        f"{offenders}. That places it in every packaged build."
    )
