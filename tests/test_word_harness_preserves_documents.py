"""The Word harness must preserve unrelated documents, including unsaved work."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_essays_batch as batch  # noqa: E402


def test_opening_a_test_copy_never_closes_other_word_documents(tmp_path, monkeypatch):
    import comtypes.client
    other = SimpleNamespace(FullName="Unsaved personal draft", Name="Personal draft", Close=Mock())
    target = SimpleNamespace(Activate=Mock(), ActiveWindow=SimpleNamespace(Activate=Mock()))
    class Documents(list):
        def Open(self, path):
            assert path == str((tmp_path / "test.docx").resolve())
            return target
    word = SimpleNamespace(Documents=Documents([other]), Visible=False)
    monkeypatch.setattr(comtypes.client, "GetActiveObject", lambda *_a, **_k: word)
    monkeypatch.setattr(batch.time, "sleep", lambda *_: None)
    batch.open_in_word(tmp_path / "test.docx")
    other.Close.assert_not_called()
    target.Activate.assert_called_once()
    target.ActiveWindow.Activate.assert_called_once()


def _word(*documents):
    """A Word with a Documents collection, which is how the save now finds its
    target. It used to read ActiveDocument, and on 2026-09-09 that raised
    AttributeError on 8 of 20 essays because earlier runs had leaked their
    copies and the active document was somebody else's."""
    return SimpleNamespace(Documents=list(documents))


def test_save_never_touches_an_unrelated_document(tmp_path):
    """The property this file exists for, now tested the harder way round.

    The old version proved only that a WRONG active document was refused. This
    puts an unrelated document in Word ALONGSIDE the target and requires that
    the target is the one saved - which is the case that actually arises, and
    the one the previous mechanism got wrong.
    """
    path = tmp_path / "test.docx"
    personal = SimpleNamespace(FullName=str(tmp_path / "personal.docx"), Save=Mock())
    target = SimpleNamespace(FullName=str(path), Save=Mock(), Saved=True,
                             Content=SimpleNamespace(Text="Corrected text"))
    report = {}
    batch.save_review_document(_word(personal, target), path, report)
    personal.Save.assert_not_called()
    target.Save.assert_called_once()
    assert report == {"after": "Corrected text"}


def test_save_writes_nothing_when_the_target_is_not_open(tmp_path):
    """No target, no save, and the report says which document was missing."""
    personal = SimpleNamespace(FullName=str(tmp_path / "personal.docx"), Save=Mock())
    report = {}
    batch.save_review_document(_word(personal), tmp_path / "test.docx", report)
    personal.Save.assert_not_called()
    assert "test.docx" in report["error"]
    assert "after" not in report


def test_save_reads_back_the_exact_target(tmp_path):
    path = tmp_path / "test.docx"
    target = SimpleNamespace(FullName=str(path), Save=Mock(), Saved=True,
                             Content=SimpleNamespace(Text="Corrected text"))
    report = {}
    batch.save_review_document(_word(target), path, report)
    target.Save.assert_called_once()
    assert report == {"after": "Corrected text"}


def test_save_failure_is_explicit(tmp_path):
    path = tmp_path / "test.docx"
    target = SimpleNamespace(FullName=str(path), Save=Mock(side_effect=OSError("read only")))
    report = {}
    batch.save_review_document(_word(target), path, report)
    assert "read only" in report["error"]