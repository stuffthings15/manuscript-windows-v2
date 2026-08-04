from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

import echo_link_app.settings_dialog as settings_module
from echo_link_app.settings_dialog import SettingsDialog
from echo_link_app.window import EchoLinkWindow
from echo_link_app.writing_models_dialog import WritingModelsDialog
from echo_link_core.storage import EchoLinkStore
from echo_link_core.writing_models import WritingModelBenchmark, WritingModelInfo


class _Manager:
    def server_version(self):
        return "1.2.3"

    def list_models(self):
        return ()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _models():
    return (
        WritingModelInfo(
            "other:latest",
            "def4567890ab",
            500_000_000,
            "2026-07-19",
            "gguf",
            "llama",
            "1B",
            "Q4_K_M",
        ),
        WritingModelInfo(
            "qwen2.5:3b",
            "abc123456789",
            2_000_000_000,
            "2026-07-19",
            "gguf",
            "qwen2",
            "3B",
            "Q4_K_M",
        ),
    )


def test_dialog_lists_version_digest_and_selects_configured_model(qapp):
    dialog = WritingModelsDialog(
        "qwen2.5:3b",
        manager=_Manager(),
        auto_refresh=False,
    )
    dialog._handle_refresh(("models", "1.2.3", _models()))
    assert dialog.table.rowCount() == 2
    assert dialog._selected_name() == "qwen2.5:3b"
    assert dialog.table.item(1, 0).text() == "Local AI 2"
    assert dialog.table.item(1, 1).text() == "3b"
    assert dialog.table.item(1, 3).text() == "abc123456789"
    assert dialog.status.text() == "2 local writing AI packs found."
    assert "qwen" not in dialog.status.text().casefold()
    assert "ollama" not in dialog.status.text().casefold()
    assert dialog.use_button.isEnabled()
    assert not dialog.remove_button.isEnabled()
    dialog.close()


def test_dialog_reports_benchmark_and_can_choose_another_model(qapp, monkeypatch):
    dialog = WritingModelsDialog(
        "qwen2.5:3b",
        manager=_Manager(),
        auto_refresh=False,
    )
    dialog._handle_refresh(("models", "1.2.3", _models()))
    dialog.table.selectRow(0)
    dialog._handle_action(
        (
            "benchmark",
            WritingModelBenchmark("other:latest", 1.5, 0.2, 1.0, 20, 20.0, True),
        )
    )
    assert "20.0 tokens/second" in dialog.status.text()
    assert dialog.remove_button.isEnabled()
    accepted = []
    monkeypatch.setattr(dialog, "accept", lambda: accepted.append(True))
    dialog._use_selected()
    assert dialog.selected_model == "other:latest"
    assert accepted == [True]
    dialog.close()


def test_different_tags_are_distinct_installed_models(qapp):
    dialog = WritingModelsDialog(
        "qwen2.5:3b",
        manager=_Manager(),
        auto_refresh=False,
    )
    models = (
        WritingModelInfo("qwen2.5:1b", "one", 1, "", "gguf", "qwen2", "1B", "Q4"),
        WritingModelInfo("qwen2.5:3b", "three", 3, "", "gguf", "qwen2", "3B", "Q4"),
    )
    dialog._handle_refresh(("models", "1.2.3", models))
    assert dialog._selected_name() == "qwen2.5:3b"
    dialog.table.selectRow(0)
    assert dialog.remove_button.isEnabled()
    dialog.close()


def test_settings_uses_only_explicitly_selected_writing_model(qapp, tmp_path, monkeypatch):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    dialog = SettingsDialog(window.store, window.engine, window)

    class AcceptedModelDialog:
        def __init__(self, configured, _parent):
            assert configured == "gemma4:12b"
            self.selected_model = "qwen2.5:7b"

        def exec(self):
            return 1

    monkeypatch.setattr(settings_module, "WritingModelsDialog", AcceptedModelDialog)
    dialog._manage_writing_models()
    assert dialog.ollama_model_edit.text() == "qwen2.5:7b"
    assert dialog.model_status.text() == "Writing AI selected. Save Settings to apply it."
    dialog.close()
    window.close()
