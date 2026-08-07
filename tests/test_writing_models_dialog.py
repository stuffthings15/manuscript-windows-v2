from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from echo_link_app.writing_models_dialog import WritingModelsDialog
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


def test_dialog_lists_version_digest_without_exposing_manual_model_choice(qapp):
    dialog = WritingModelsDialog(
        "",
        manager=_Manager(),
        auto_refresh=False,
    )
    dialog._handle_refresh(("models", "1.2.3", _models()))
    assert dialog.table.rowCount() == 2
    assert dialog.configured_model == ""
    assert dialog.selected_model == ""
    assert dialog._selected_name() == ""
    assert dialog.table.item(1, 0).text() == "Local AI 2"
    assert dialog.table.item(1, 1).text() == "3b"
    assert dialog.table.item(1, 3).text() == "abc123456789"
    assert dialog.status.text() == "2 local writing AI packs found."
    assert "qwen" not in dialog.status.text().casefold()
    assert "ollama" not in dialog.status.text().casefold()
    visible_table_text = " ".join(
        dialog.table.item(row, column).text()
        for row in range(dialog.table.rowCount())
        for column in range(dialog.table.columnCount())
    ).casefold()
    assert "qwen" not in visible_table_text
    assert "ollama" not in visible_table_text
    button_text = {button.text() for button in dialog.findChildren(QPushButton)}
    assert "Use selected" not in button_text
    assert "Import local GGUF" not in button_text
    dialog.close()


def test_dialog_reports_benchmark_but_cannot_choose_another_model(qapp):
    dialog = WritingModelsDialog(
        "",
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
    assert dialog.selected_model == ""
    dialog.close()


def test_automatic_dialog_does_not_preselect_a_configured_tag(qapp):
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
    assert dialog.configured_model == ""
    assert dialog._selected_name() == ""
    dialog.table.selectRow(0)
    assert dialog._selected_name() == "qwen2.5:1b"
    assert dialog.selected_model == ""
    dialog.close()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (("error", "Ollama qwen service failed"), "diagnostics are unavailable"),
        (("action-error", "Gemma model failed"), "speed check could not finish"),
    ],
)
def test_dialog_hides_runtime_and_model_names_from_errors(qapp, payload, expected):
    dialog = WritingModelsDialog("", manager=_Manager(), auto_refresh=False)
    if payload[0] == "error":
        dialog._handle_refresh(payload)
    else:
        dialog._handle_action(("error", payload[1]))
    visible = dialog.status.text().casefold()
    assert expected in visible
    assert "ollama" not in visible
    assert "qwen" not in visible
    assert "gemma" not in visible
    assert "model" not in visible
    dialog.close()
