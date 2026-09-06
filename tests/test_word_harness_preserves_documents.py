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


def test_save_refuses_a_different_active_document(tmp_path):
    other = SimpleNamespace(FullName=str(tmp_path / "personal.docx"), Save=Mock())
    report = {}
    batch.save_review_document(SimpleNamespace(ActiveDocument=other), tmp_path / "test.docx", report)
    other.Save.assert_not_called()
    assert "target changed" in report["error"]
    assert "after" not in report


def test_save_reads_back_the_exact_target(tmp_path):
    path = tmp_path / "test.docx"
    target = SimpleNamespace(FullName=str(path), Save=Mock(), Saved=True,
                             Content=SimpleNamespace(Text="Corrected text"))
    report = {}
    batch.save_review_document(SimpleNamespace(ActiveDocument=target), path, report)
    target.Save.assert_called_once()
    assert report == {"after": "Corrected text"}


def test_save_failure_is_explicit(tmp_path):
    path = tmp_path / "test.docx"
    target = SimpleNamespace(FullName=str(path), Save=Mock(side_effect=OSError("read only")))
    report = {}
    batch.save_review_document(SimpleNamespace(ActiveDocument=target), path, report)
    assert "read only" in report["error"]
