from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from echo_link_app.settings_dialog import SettingsDialog
from echo_link_core.models import DictationSettings
from echo_link_core.storage import EchoLinkStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path, monkeypatch, settings: DictationSettings | None = None):
    store = EchoLinkStore(tmp_path / "manuscript-v2.db")
    if settings is not None:
        store.save_settings(settings)
    monkeypatch.setattr(SettingsDialog, "_refresh_model_status", lambda _self: None)
    return SettingsDialog(store, SimpleNamespace()), store


def test_settings_save_does_not_create_or_accept_a_manual_writing_model(
    qapp,
    tmp_path,
    monkeypatch,
):
    dialog, _store = _dialog(tmp_path, monkeypatch)
    assert dialog.ollama_model_edit.text() == ""

    dialog.ollama_model_edit.setText("gemma4:12b")
    dialog._save()

    assert dialog.settings.ollama_model == ""
    dialog.close()


def test_settings_preserves_model_only_when_an_automatic_plan_exists(
    qapp,
    tmp_path,
    monkeypatch,
):
    accepted = DictationSettings(
        ollama_model="gemma4:e4b-it-qat",
        polish_model_pack_id="gemma4-e4b-it-qat-v1",
    )
    dialog, _store = _dialog(tmp_path, monkeypatch, accepted)

    dialog.ollama_model_edit.setText("arbitrary:latest")
    dialog._save()

    assert dialog.settings.ollama_model == "gemma4:e4b-it-qat"
    dialog.close()


def test_local_ai_status_uses_automatic_plan_not_direct_model_probe(
    qapp,
    tmp_path,
    monkeypatch,
):
    class Speech:
        def status(self):
            return {"device": "cpu"}

    class Correction:
        def local_model_status(self, **_kwargs):
            raise AssertionError("direct writing model probe must not run")

    dialog, _store = _dialog(tmp_path, monkeypatch)
    dialog.engine = SimpleNamespace(transcriber=Speech(), correction=Correction())
    messages = []
    dialog.model_status_ready.connect(messages.append)

    dialog._collect_model_status("base.en")

    assert messages
    assert "Writing AI will be selected automatically" in messages[-1]
    assert "gemma" not in messages[-1].casefold()
    assert "ollama" not in messages[-1].casefold()
    dialog.close()
