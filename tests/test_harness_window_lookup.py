"""The batch harness must not search for windows by walking the UIA tree.

This rule cost four days. `COMError (-2146233083)` out of
`GetNextSiblingControl` took three to five essays out of every release gate run
between 2026-08-26 and 2026-08-30, and was twice diagnosed as Word instability -
once as a stale WINWORD process, which was real but was treating a symptom:
killing Word shrank the desktop enough that the walk usually survived.

The actual cause is that a window search starting at the UI Automation root
enumerates every top-level window on the DESKTOP and reads each one through UI
Automation, repeatedly until it matches or times out. Any slow or dying window
in any other application raises COMError out of the sibling walk and ends the
run. Nothing about that is under this project's control, which is why it
presented as random.

`full_page_user_journey_probe._find_window_handle_by_prefix` is the answer:
EnumWindows plus GetWindowTextW is a direct Win32 read that cannot raise
COMError, and only the final `ControlFromHandle` touches UI Automation, once, on
a window already known to exist.

Scoped to the modules `run_essays_batch` actually imports, and the second test
keeps that list honest as those imports change.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"

# The batch's own module plus everything it imports at module scope. If that
# import list grows, this list grows with it - that is the point, because the
# defect arrives through a helper nobody re-read.
BATCH_PATH_MODULES = (
    "run_essays_batch.py",
    "repro_accept_advance.py",
    "repro_zero_of_zero.py",
    "word_essay_session.py",
    # Added 2026-09-30 when run_essays_batch took its --tone choices from
    # VISIBLE_TONES, which lives in this module.
    "animal_essay_corpus_inventory.py",
    # Added 2026-09-07 when the rotation-aware log cursor moved out of the batch
    # into its own module, and this test caught the omission on the same run.
    # It opens no windows today, which is exactly the state this list exists to
    # keep true: the rule is about every module the batch imports, not about the
    # ones that happen to search a control tree right now.
    "application_log_cursor.py",
    # Added 2026-09-08 with the liveness check the capture wait now asks.
    "restart_app_and_wait.py",
)

# Matched against the PARSED TREE, not the text.
#
# Every fix for this defect carries a comment quoting the call it replaced -
# that is what makes the fix reviewable - and a text search flags those
# explanations as the defect itself. The first version of this test did exactly
# that: it failed on two docstrings while the one real hazard sat in a third
# file, which is a rule that punishes documenting the fix.
_SEARCH_METHODS = {"GetChildren", "WindowControl"}


def _tree_searches(path: pathlib.Path) -> list[int]:
    """Lines calling `GetRootControl().GetChildren(...)` or `.WindowControl(...)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = node.func
        if not isinstance(method, ast.Attribute) or method.attr not in _SEARCH_METHODS:
            continue
        inner = method.value
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "GetRootControl"
        ):
            found.append(node.lineno)
    return found


@pytest.mark.parametrize("name", BATCH_PATH_MODULES)
def test_no_desktop_wide_tree_search_in_the_batch_path(name):
    """A window search must not start at the UIA root in the batch path."""
    hits = _tree_searches(TOOLS / name)
    assert not hits, (
        f"{name} lines {hits} search for a window by walking the UI Automation "
        f"tree from the root. That reads every top-level window on the desktop "
        f"and raises COMError (-2146233083) when any of them is slow to answer. "
        f"Find the window by handle instead - see "
        f"full_page_user_journey_probe._find_window_handle_by_prefix."
    )


def test_the_batch_path_modules_are_the_ones_the_batch_imports():
    """Keep the list above honest as the imports change.

    A rule guarding a stale list of files is worse than no rule: it stays green
    while the defect walks back in through a module nobody added here.
    """
    source = (TOOLS / "run_essays_batch.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^from (\w+) import", source, re.MULTILINE))
    local = {
        name
        for name in imported
        if (TOOLS / f"{name}.py").exists()
        # animal_essay_corpus_probe holds corpus paths and opens no windows.
        and name != "animal_essay_corpus_probe"
    }
    guarded = {name[:-3] for name in BATCH_PATH_MODULES}
    missing = sorted(local - guarded)
    assert not missing, (
        f"run_essays_batch imports {missing}, which BATCH_PATH_MODULES does not "
        f"guard. Add them, or the tree-search rule silently stops covering the "
        f"batch."
    )


def test_the_win32_lookup_still_exists_to_point_at():
    """The rule tells people to use a helper; the helper has to be there."""
    source = (TOOLS / "full_page_user_journey_probe.py").read_text(encoding="utf-8")
    assert "def _find_window_handle_by_prefix" in source
    assert "EnumWindows" in source


def test_the_detector_actually_detects():
    """A source rule that cannot fail is decoration, and this one nearly was.

    The text version of this test passed on the file holding the real hazard
    while failing on two that only described it. Feeding the detector the exact
    shape it exists to catch is the cheapest way to know it still works.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        sample = pathlib.Path(directory) / "sample.py"
        sample.write_text(
            "import uiautomation as automation\n"
            "def find():\n"
            "    return automation.GetRootControl().WindowControl(searchDepth=3)\n",
            encoding="utf-8",
        )
        assert _tree_searches(sample) == [3]

        clean = pathlib.Path(directory) / "clean.py"
        clean.write_text(
            '"""Do not call automation.GetRootControl().WindowControl here."""\n'
            "import uiautomation as automation\n"
            "def find(handle):\n"
            "    return automation.ControlFromHandle(handle)\n",
            encoding="utf-8",
        )
        assert _tree_searches(clean) == []
