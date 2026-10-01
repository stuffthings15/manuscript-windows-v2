"""A document one run leaks is a document every later run trips over.

The batch names its disposable target "<session_tag>-<essay>.docx" with a fresh
uuid per batch, and release_document matched the FULL PATH. So it released the
document this run opened and could never release one an earlier run left
behind. Measured 2026-09-09: build/working held 656 files across 192 distinct
session prefixes, several with Word's "~$" owner locks still beside them, and
two WINWORD processes were holding two copies of essay 20 at once.

A leaked window is not inert. The Writing HUD attaches to it, its
`live-review-open` guard then refuses every focus signal from the real
document, and `_hud_source_window_alive` correctly reports the source alive
because it genuinely is. Every failure of the 2026-09-09 animal-pet run came
from that one leak:

    8  "save/readback failed: AttributeError: ActiveDocument" - Word held
       several documents and the active one was not this essay. Several of
       these say "the review finished after press 11", so the product had done
       the work and the harness lost it at the last step.
    3  a review served from the WRONG essay - essay 3's against essay 2,
       essay 6's against essay 4.
    1  a card stuck on the previous copy, which a person had to close by hand.

4 complete, 12 error, 4 dead, and the run correctly refused to score.
"""
from __future__ import annotations

import pathlib
import sys
import types
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _entry in (ROOT / "tools", ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

sys.modules.setdefault("uiautomation", types.ModuleType("uiautomation"))
import run_essays_batch as batch  # noqa: E402


class _Document:
    def __init__(self, full_name):
        self.FullName = str(full_name)
        self.closed_with = None

    def Close(self, save_changes):
        self.closed_with = save_changes


class _Word:
    def __init__(self, documents):
        self.Documents = documents


def _release(tmp_path, target_name, open_names, monkeypatch):
    documents = [_Document(tmp_path / name) for name in open_names]
    module = types.ModuleType("comtypes.client")
    module.GetActiveObject = lambda *_a, **_k: _Word(documents)
    package = types.ModuleType("comtypes")
    package.client = module
    monkeypatch.setitem(sys.modules, "comtypes", package)
    monkeypatch.setitem(sys.modules, "comtypes.client", module)
    batch.release_document(Path(tmp_path / target_name))
    return documents


def test_a_copy_from_an_earlier_run_is_released(tmp_path, monkeypatch):
    """The exact leak: same essay, different session tag."""
    documents = _release(
        tmp_path,
        "b9f10f4703b3-20_Axolotls_in_Puebla_Mexico.docx",
        ["49319dc95fc1-20_Axolotls_in_Puebla_Mexico.docx"],
        monkeypatch,
    )
    assert documents[0].closed_with == 0, (
        "The copy left open by an earlier run was not released. That window is "
        "what the HUD pins itself to, and it is why essay 20 was served a "
        "review captured from '49319dc95fc1-...' instead of its own."
    )


def test_this_run_s_own_target_is_still_released(tmp_path, monkeypatch):
    """The behaviour that already existed must not be lost."""
    documents = _release(
        tmp_path,
        "b9f10f4703b3-20_Axolotls_in_Puebla_Mexico.docx",
        ["b9f10f4703b3-20_Axolotls_in_Puebla_Mexico.docx"],
        monkeypatch,
    )
    assert documents[0].closed_with == 0


def test_a_different_essay_is_never_touched(tmp_path, monkeypatch):
    """The safety property. Releasing somebody else's document loses work.

    close_word() in this module refuses to terminate Word for exactly this
    reason: unrelated work may be unsaved.
    """
    documents = _release(
        tmp_path,
        "b9f10f4703b3-20_Axolotls_in_Puebla_Mexico.docx",
        [
            "aaaaaaaaaaaa-19_Hermit_Crabs_in_Belize_City_Belize.docx",
            "a-real-users-thesis.docx",
        ],
        monkeypatch,
    )
    assert all(document.closed_with is None for document in documents), (
        "a document belonging to another essay, or to the user, was closed"
    )


def test_a_longer_number_does_not_match_a_shorter_one(tmp_path, monkeypatch):
    """'-1_Dogs.docx' must not match '-11_Dogs.docx'.

    The match is anchored on the leading '-' that separates the session tag
    from the essay name, so a suffix match cannot span the boundary.
    """
    documents = _release(
        tmp_path,
        "bbbbbbbbbbbb-1_Dogs.docx",
        ["aaaaaaaaaaaa-11_Dogs.docx"],
        monkeypatch,
    )
    assert documents[0].closed_with is None, "essay 11 was released for essay 1"


def test_word_not_running_is_not_an_error(tmp_path, monkeypatch):
    """Nothing holds the document, so there is nothing to release."""
    module = types.ModuleType("comtypes.client")

    def _refuse(*_args, **_kwargs):
        raise OSError("Word is not running")

    module.GetActiveObject = _refuse
    package = types.ModuleType("comtypes")
    package.client = module
    monkeypatch.setitem(sys.modules, "comtypes", package)
    monkeypatch.setitem(sys.modules, "comtypes.client", module)
    batch.release_document(Path(tmp_path / "aaaaaaaaaaaa-01_Dogs.docx"))


def test_the_save_finds_its_document_when_another_is_active(tmp_path, monkeypatch):
    """The second half: ActiveDocument is not the target, and used to raise."""
    target = tmp_path / "b9f10f4703b3-20_Axolotls_in_Puebla_Mexico.docx"
    target.write_text("x", encoding="utf-8")
    other = tmp_path / "49319dc95fc1-20_Axolotls_in_Puebla_Mexico.docx"
    other.write_text("x", encoding="utf-8")

    class _Saveable(_Document):
        Saved = True

        def __init__(self, full_name, text):
            super().__init__(full_name)
            self.Content = types.SimpleNamespace(Text=text)
            self.saved_calls = 0

        def Save(self):
            self.saved_calls += 1

    wanted = _Saveable(target, "the corrected essay")
    stale = _Saveable(other, "the previous copy")

    class _WordNoActive:
        Documents = [stale, wanted]

        def __getattr__(self, name):
            # Exactly what Word did: ActiveDocument raised AttributeError.
            raise AttributeError(name)

    report: dict = {}
    batch.save_review_document(_WordNoActive(), target, report)

    assert "error" not in report, f"save still failed: {report.get('error')}"
    assert report["after"] == "the corrected essay"
    assert wanted.saved_calls == 1
    assert stale.saved_calls == 0, "the stale copy was saved instead"


def test_every_copy_a_run_opened_is_released_at_the_end(tmp_path, monkeypatch):
    """The last essay's copy is the one nothing else releases.

    release_document runs at the START of each essay, so essay 20's copy was
    still open in Word when the batch finished - one leaked document per run,
    192 runs, 656 files in build/working.
    """
    released = []
    monkeypatch.setattr(batch, "release_document", released.append)
    targets = [Path(tmp_path / f"aaaaaaaaaaaa-{n}_Essay.docx") for n in (1, 2, 3)]

    batch.release_opened_targets(targets)

    assert released == targets, (
        "a run's own disposable copies were not all released; the last one is "
        "the one that stays open in Word and that the next run trips over"
    )


def test_releasing_nothing_is_harmless(tmp_path, monkeypatch):
    """A run that opened no documents - the idle gate stopped it first."""
    released = []
    monkeypatch.setattr(batch, "release_document", released.append)
    batch.release_opened_targets([])
    assert released == []


def test_the_cleanup_runs_on_every_exit_from_main():
    """main() returns early when the app will not start.

    A cleanup that only runs on the happy path is the same defect wearing a
    different hat. Parsed rather than grepped, and scoped to main(), so the
    rule cannot be satisfied by the word appearing in a comment or by the two
    tests above calling the helper directly.
    """
    import ast

    tree = ast.parse((ROOT / "tools" / "run_essays_batch.py").read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "release_opened_targets"
    ]
    returns = [node for node in ast.walk(main) if isinstance(node, ast.Return)]
    assert len(calls) >= len(returns), (
        f"main() has {len(returns)} return statements but only {len(calls)} "
        f"release_opened_targets calls. An exit that does not release leaks "
        f"whatever the run had already opened, which is the defect this file "
        f"exists for."
    )
