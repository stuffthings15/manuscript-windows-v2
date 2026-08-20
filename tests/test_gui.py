from __future__ import annotations

from collections import defaultdict
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QSystemTrayIcon,
)

import echo_link_app.window as window_module
from echo_link_app.learning_dialog import LearningManagerDialog, LearningProposalDialog
from echo_link_app.settings_dialog import SettingsDialog
from echo_link_app.echo_bar import EchoBar
from echo_link_app.theme import COLORS, apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_core.models import DictationMode, InsertionMode, SessionEvent, SessionState
from echo_link_core.insertion import WindowSnapshot
from echo_link_core.learning import LearningProposal
from echo_link_core.storage import EchoLinkStore
from echo_link_core.polish_types import (
    ExecutionPlan,
    PolishMode,
    PolishTask,
    RuntimeBackend,
    RuntimeFailureKind,
)


@pytest.fixture(scope="module")
def app():
    os.environ["ECHOLINK_DISABLE_SOUNDS"] = "1"
    os.environ["ECHOLINK_DISABLE_MEDIA_CONTROL"] = "1"
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    apply_dark_theme(application)
    return application


def _assert_no_sibling_overlap(root):
    by_parent = defaultdict(list)
    for widget_type in (QLabel, QLineEdit, QPushButton):
        for widget in root.findChildren(widget_type):
            if widget.isVisible() and widget.width() > 0 and widget.height() > 0:
                by_parent[widget.parentWidget()].append(widget)
    for widgets in by_parent.values():
        for index, left in enumerate(widgets):
            left_rect = QRect(left.pos(), left.size())
            for right in widgets[index + 1 :]:
                overlap = left_rect.intersected(QRect(right.pos(), right.size()))
                assert overlap.width() <= 0 or overlap.height() <= 0, (
                    f"{left.objectName() or left.text()} overlaps {right.objectName() or right.text()}"
                )


def _show_named_tab(window: EchoLinkWindow, name: str) -> None:
    window._show_tab(name)
    assert window.tabs.tabText(window.tabs.currentIndex()) == name


def _contrast_ratio(left: str, right: str) -> float:
    def luminance(value: str) -> float:
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_startup_shares_the_model_returned_by_the_acceptance_probe(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    persisted = store.get_settings()
    persisted.ollama_model = "gemma4:e2b-it-qat"
    persisted.polish_model_pack_id = "gemma4-e2b-qat-v1"
    store.save_settings(persisted)
    window = EchoLinkWindow(store, enable_hotkey=False)
    attempted = ExecutionPlan(
        policy_version=7,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-12b-q4-k-m-v1",
        model="gemma4:12b-it-q4_K_M",
        backend=RuntimeBackend.CUDA,
    )
    accepted = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
    )
    pack = SimpleNamespace(pack_id=attempted.pack_id, installed=True, validated=True)
    startup_events = []
    runtime = SimpleNamespace(
        runtime_id="adaptive:fallback:test",
        prepare=lambda plan, consent: startup_events.append(
            ("prepare", plan.pack_id, consent)
        )
        or True,
        status=lambda _model, timeout: SimpleNamespace(
            loaded=True,
            model=accepted.model,
            backend=accepted.backend,
            model_bytes=4_000,
            gpu_bytes=4_000,
        ),
        close=lambda: None,
    )

    def accept_after_prepare(
        plan,
        _snapshot,
        selected_pack,
        *,
        allow_installed_fallback,
    ):
        assert startup_events == [("prepare", attempted.pack_id, False)]
        assert plan is attempted
        assert selected_pack is pack
        assert allow_installed_fallback is False
        return accepted

    window.polish_engine = SimpleNamespace(
        warm=lambda: {"dictionary": True, "classifier": True},
        hardware_provider=lambda: SimpleNamespace(),
        packs=SimpleNamespace(
            catalog=lambda refresh_installed: (pack,),
            migrate_pack_id=lambda value: value,
        ),
        policy=SimpleNamespace(plan=lambda *_args, **_kwargs: attempted),
        _accept_plan=accept_after_prepare,
        runtime=window.ai_coordinator,
    )
    window.ai_coordinator._runtime = runtime

    assert window.engine.settings.ollama_model == ""
    assert window.drafts_panel._model_provider() == ""
    assert window.document_panel._writing_model_provider() == ""

    window._warm_adaptive_polish()

    saved = store.get_settings()
    assert saved.ollama_model == accepted.model
    assert saved.polish_model_pack_id == accepted.pack_id
    assert saved.polish_policy_version == accepted.policy_version
    assert window.engine.correction.accepted_model == accepted.model
    assert window.drafts_panel._model_provider() == accepted.model
    assert window.document_panel._writing_model_provider() == accepted.model
    assert window._accepted_writing_model() == accepted.model
    assert attempted.model not in window._installed_writing_models
    window.close()


def test_startup_validates_compatibility_runtime_after_bundled_prepare_fails(
    app,
    tmp_path,
):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
        require_full_gpu=True,
    )
    pack = SimpleNamespace(
        pack_id=plan.pack_id,
        model=plan.model,
        installed=True,
        validated=True,
    )
    events = []
    runtime = SimpleNamespace(
        runtime_id="adaptive:fallback:test",
        prepare=lambda selected, consent: events.append(
            ("prepare-failed", selected.pack_id, consent)
        )
        or False,
        acceptance_probe=lambda selected: events.append(
            ("fallback-probed", selected.pack_id)
        )
        or SimpleNamespace(accepted=True),
        status=lambda *_args, **_kwargs: SimpleNamespace(
            loaded=True,
            model=plan.model,
            backend=plan.backend,
            model_bytes=4_000,
            gpu_bytes=4_000,
        ),
        close=lambda: None,
    )
    window.ai_coordinator._runtime = runtime

    def accept_fallback(selected, _snapshot, selected_pack, **_kwargs):
        assert selected is plan
        assert selected_pack is pack
        probe = window.ai_coordinator.acceptance_probe(selected)
        assert probe.accepted
        return selected

    window.polish_engine = SimpleNamespace(
        warm=lambda: {"dictionary": True, "classifier": True},
        hardware_provider=lambda: SimpleNamespace(),
        packs=SimpleNamespace(
            catalog=lambda refresh_installed: (pack,),
            migrate_pack_id=lambda value: value,
        ),
        policy=SimpleNamespace(plan=lambda *_args, **_kwargs: plan),
        _accept_plan=accept_fallback,
        runtime=window.ai_coordinator,
    )

    window._warm_adaptive_polish()

    assert events == [
        ("prepare-failed", plan.pack_id, False),
        ("fallback-probed", plan.pack_id),
    ]
    lease = window.ai_coordinator.lease()
    assert lease is not None
    assert lease.plan == plan
    assert store.get_settings().ollama_model == plan.model
    window.close()


@pytest.mark.parametrize(
    ("status", "expected_model", "expected_backend"),
    (
        (
            SimpleNamespace(
                loaded=True,
                model="wrong:model",
                backend=RuntimeBackend.CUDA,
                model_bytes=4_000,
                gpu_bytes=4_000,
            ),
            "gemma4:e4b-it-qat",
            RuntimeBackend.CUDA,
        ),
        (
            SimpleNamespace(
                loaded=True,
                model="gemma4:e4b-it-qat",
                backend=RuntimeBackend.CPU,
                model_bytes=4_000,
                gpu_bytes=0,
            ),
            "gemma4:e4b-it-qat",
            RuntimeBackend.CUDA,
        ),
        (
            SimpleNamespace(
                loaded=True,
                model="gemma4:e4b-it-qat",
                backend=RuntimeBackend.CUDA,
                model_bytes=4_000,
                gpu_bytes=3_999,
            ),
            "gemma4:e4b-it-qat",
            RuntimeBackend.CUDA,
        ),
    ),
)
def test_startup_runtime_verification_requires_exact_identity_and_full_gpu(
    status,
    expected_model,
    expected_backend,
):
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model=expected_model,
        backend=expected_backend,
        require_full_gpu=True,
    )

    assert not window_module._runtime_status_matches_plan(
        status,
        plan,
        expected_model=expected_model,
        expected_backend=expected_backend,
    )


def test_startup_does_not_accept_a_model_that_fails_to_warm(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    accepted = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
        context_window=2048,
    )
    pack = SimpleNamespace(pack_id=accepted.pack_id, installed=True, validated=True)

    def fail_warm(_request):
        raise RuntimeError("GPU load failed")

    window.polish_engine = SimpleNamespace(
        warm=lambda: {"dictionary": True, "classifier": True},
        hardware_provider=lambda: SimpleNamespace(),
        packs=SimpleNamespace(
            catalog=lambda refresh_installed: (pack,),
            migrate_pack_id=lambda value: value,
        ),
        policy=SimpleNamespace(plan=lambda *_args, **_kwargs: accepted),
        _accept_plan=lambda *_args, **_kwargs: accepted,
        runtime=SimpleNamespace(
            status=lambda _model, timeout: SimpleNamespace(loaded=False),
            generate=fail_warm,
        ),
    )

    window._warm_adaptive_polish()

    assert window.engine.settings.ollama_model == ""
    assert window.engine.correction.accepted_model == ""
    assert store.get_settings().ollama_model == ""
    window.close()


def test_startup_never_probes_or_accepts_an_uninstalled_pack(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    attempted = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
    )
    probes = []
    window.polish_engine = SimpleNamespace(
        warm=lambda: {"dictionary": True, "classifier": True},
        hardware_provider=lambda: SimpleNamespace(),
        packs=SimpleNamespace(
            catalog=lambda refresh_installed: (
                SimpleNamespace(
                    pack_id=attempted.pack_id,
                    installed=False,
                    validated=False,
                ),
            ),
            migrate_pack_id=lambda value: value,
        ),
        policy=SimpleNamespace(plan=lambda *_args, **_kwargs: attempted),
        _accept_plan=lambda *_args, **_kwargs: probes.append(True) or attempted,
        runtime=SimpleNamespace(),
    )

    window._warm_adaptive_polish()

    assert probes == []
    assert window.engine.settings.ollama_model == ""
    assert store.get_settings().ollama_model == ""
    window.close()


def test_completed_polish_requests_refresh_and_clear_the_shared_ai_plan(
    app,
    tmp_path,
    monkeypatch,
):
    accepted = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
    )

    class FakeAdapter:
        def __init__(self, *_args, **_kwargs):
            self.last_correction_result = SimpleNamespace(
                used_llm=True,
                review_ready=True,
                plan=accepted,
                runtime_failure_kind=SimpleNamespace(value="none"),
            )

        def run(self, *_args, **_kwargs):
            return SimpleNamespace(text="Corrected.")

    monkeypatch.setattr(window_module, "PolishWritingAssistantAdapter", FakeAdapter)
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)

    # A result object alone is not proof that the accepted runtime generated
    # it. The shared routes are wired correctly, but the claimed result must
    # not publish a model without matching generation evidence.
    window.document_panel.writing_assistant.run("Source", command="Proofread")

    assert window.document_panel.writing_assistant is window.writing_assistant
    assert window.writing_assistant._coordinator is window.ai_coordinator
    assert window.drafts_panel.assistant._inference is window.ai_coordinator
    assert window.source_chat._inference is window.ai_coordinator
    assert window.engine.settings.ollama_model == ""
    assert window.drafts_panel._model_provider() == ""

    window.ai_coordinator.accept_verified(accepted)
    assert window.engine.settings.ollama_model == accepted.model

    window.writing_assistant._delegate.last_correction_result = SimpleNamespace(
        used_llm=False,
        review_ready=False,
        plan=accepted,
        runtime_failure_kind=SimpleNamespace(value="gpu_failure"),
    )
    window.writing_assistant.run("Source", command="Proofread")

    assert window.engine.settings.ollama_model == ""
    assert window.engine.correction.accepted_model == ""
    window.close()


def test_writing_commands_never_select_an_unaccepted_installed_model(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
    )
    window.ai_coordinator.accept_verified(plan)
    window._installed_writing_models = {
        plan.model,
        "gemma4:unaccepted-larger-model",
    }

    assert window._writing_model_for("Professional") == plan.model
    assert window._writing_model_for("Proofread") == plan.model
    window.close()


def test_startup_rejects_live_cpu_spill_for_a_gpu_plan(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    plan = ExecutionPlan(
        policy_version=8,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
        require_full_gpu=True,
    )
    pack = SimpleNamespace(
        pack_id=plan.pack_id,
        model=plan.model,
        installed=True,
        validated=True,
    )
    runtime = SimpleNamespace(
        runtime_id="adaptive:fallback:test",
        prepare=lambda *_args, **_kwargs: True,
        status=lambda *_args, **_kwargs: SimpleNamespace(
                loaded=True,
                model=plan.model,
                backend=RuntimeBackend.CPU,
                model_bytes=6_000,
                gpu_bytes=0,
        ),
        close=lambda: None,
    )
    window.ai_coordinator._runtime = runtime
    window.polish_engine = SimpleNamespace(
        warm=lambda: {"dictionary": True, "classifier": True},
        packs=SimpleNamespace(
            catalog=lambda refresh_installed: (pack,),
            migrate_pack_id=lambda value: value,
        ),
        hardware_provider=lambda: SimpleNamespace(),
        policy=SimpleNamespace(plan=lambda *_args, **_kwargs: plan),
        _accept_plan=lambda *_args, **_kwargs: plan,
        runtime=window.ai_coordinator,
    )

    window._warm_adaptive_polish()

    assert window.ai_coordinator.lease() is None
    assert window.engine.settings.ollama_model == ""
    window.close()


def test_rules_only_manual_fallback_is_not_presented_as_ai_correction():
    result = SimpleNamespace(
        quality_complete=False,
        review_ready=True,
        used_llm=False,
        validation=SimpleNamespace(accepted=False),
    )

    assert not window_module._polish_result_ready_for_review(result)


def test_prewarm_retries_while_the_gpu_is_still_letting_go():
    """A busy GPU at launch must not cost the user their first correction.

    Relaunching while the previous copy still held VRAM made the startup
    planner pick rules-only and skip the prewarm. Nothing revisited it, so the
    first correction paid a full cold load - 91 seconds for a 20-character
    sentence, against 2 seconds warm.
    """
    attempts = []
    window = SimpleNamespace(
        _warm_retry_stop=threading.Event(),
        _gpu_pack_is_installed=lambda: True,
    )

    def warm() -> bool:
        attempts.append(len(attempts))
        # No room the first two times, then the GPU frees up.
        return len(attempts) >= 3

    window._warm_adaptive_polish = warm
    with mock.patch.object(window_module, "_WARM_RETRY_DELAY_SECONDS", 0.0):
        window_module.EchoLinkWindow._warm_adaptive_polish_until_ready(window)

    assert len(attempts) == 3


def test_prewarm_does_not_retry_without_a_model_to_load():
    """A machine with no installed pack must not keep a thread spinning."""
    attempts = []
    window = SimpleNamespace(
        _warm_retry_stop=threading.Event(),
        _gpu_pack_is_installed=lambda: False,
        _warm_adaptive_polish=lambda: bool(attempts.append(1)) or False,
    )

    with mock.patch.object(window_module, "_WARM_RETRY_DELAY_SECONDS", 0.0):
        window_module.EchoLinkWindow._warm_adaptive_polish_until_ready(window)

    assert len(attempts) == 1


def test_prewarm_retry_stops_at_once_when_the_window_closes():
    stop = threading.Event()
    stop.set()
    attempts = []
    window = SimpleNamespace(
        _warm_retry_stop=stop,
        _gpu_pack_is_installed=lambda: True,
        _warm_adaptive_polish=lambda: bool(attempts.append(1)) or False,
    )

    with mock.patch.object(window_module, "_WARM_RETRY_DELAY_SECONDS", 30.0):
        window_module.EchoLinkWindow._warm_adaptive_polish_until_ready(window)

    assert len(attempts) == 1


def test_rejected_ai_answer_still_shows_its_deterministic_repair():
    """A turned-down AI answer must not swallow the whole section.

    Essay 1 lost three of ten sections this way: the AI answer tripped a
    meaning guard, the HUD refused the result because ``used_llm`` was False,
    and the reader got no review card at all - not even the spelling and
    punctuation repairs the rules layer had already made. The HUD sat on
    "analyzing" until the run timed out.
    """
    result = SimpleNamespace(
        quality_complete=False,
        review_ready=False,
        used_llm=False,
        text="The reports are ready.",
        validation=SimpleNamespace(accepted=False),
        runtime_failure_kind=RuntimeFailureKind.VALIDATION_FAILURE,
    )

    assert window_module._polish_result_ready_for_review(result)


def test_a_broken_runtime_is_not_offered_as_a_correction():
    """The escape hatch above is only for a rejected answer.

    No model, no GPU or a dead connection means there was no AI stage to fall
    back from, so those keep the error path.
    """
    for kind in (
        RuntimeFailureKind.MODEL_UNAVAILABLE,
        RuntimeFailureKind.GPU_FAILURE,
        RuntimeFailureKind.CONNECTION_FAILURE,
        RuntimeFailureKind.LOAD_TIMEOUT,
    ):
        result = SimpleNamespace(
            quality_complete=False,
            review_ready=False,
            used_llm=False,
            text="The reports are ready.",
            validation=SimpleNamespace(accepted=False),
            runtime_failure_kind=kind,
        )

        assert not window_module._polish_result_ready_for_review(result), kind


def test_a_rejected_answer_with_no_text_is_still_refused():
    result = SimpleNamespace(
        quality_complete=False,
        review_ready=False,
        used_llm=False,
        text="   ",
        validation=SimpleNamespace(accepted=False),
        runtime_failure_kind=RuntimeFailureKind.VALIDATION_FAILURE,
    )

    assert not window_module._polish_result_ready_for_review(result)


def test_validated_ai_result_is_ready_for_review():
    result = SimpleNamespace(
        quality_complete=True,
        review_ready=True,
        used_llm=True,
        validation=SimpleNamespace(accepted=True),
    )

    assert window_module._polish_result_ready_for_review(result)


def test_main_window_compact_layout_has_readable_text(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window.resize(720, 620)
    window.show()
    app.processEvents()
    _assert_no_sibling_overlap(window)
    assert window.windowTitle() == "Manuscript Windows Version 2"
    assert any(label.text() == "Manuscript V2" for label in window.findChildren(QLabel))
    assert any(
        label.text() == "Flawless writing powered locally"
        for label in window.findChildren(QLabel)
    )
    assert window.record_button.isVisible()
    assert window.status_text.text()
    assert window.tabs.count() == 9
    assert window.section_nav.count() == 9
    assert all(window.tabs.tabText(index) != "Capstone" for index in range(window.tabs.count()))
    assert window.tabs.indexOf(window.document_panel) >= 0
    assert window.section_nav.verticalScrollBar().maximum() == 0
    for index in range(window.section_nav.count()):
        item = window.section_nav.item(index)
        assert window.section_nav.viewport().rect().contains(
            window.section_nav.visualItemRect(item)
        )
    assert window.review_tabs.count() == 3
    assert window.review_tabs.tabText(0) == "Dictation history"
    assert window.review_check.isVisible()
    for button in window.findChildren(QPushButton):
        if button.isVisible() and button.text():
            needed = button.fontMetrics().horizontalAdvance(button.text()) + 24
            assert button.width() >= min(needed, 104)
    _save_capture(window, "manuscript-compact.png")
    window.resize(900, 720)
    app.processEvents()
    _save_capture(window, "manuscript-desktop.png")
    window.resize(1024, 700)
    app.processEvents()
    expected_sections = [
        "Dictate",
        "Writing tools",
        "Drafts",
        "Documents",
        "Vocabulary",
        "Snippets",
        "Recovery",
        "History",
        "Sources",
    ]
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == expected_sections
    for index, section in enumerate(expected_sections):
        window.section_nav.setCurrentRow(index)
        app.processEvents()
        assert window.tabs.currentIndex() == index
        assert window.tabs.tabText(index) == section
        assert window.main_scroll.horizontalScrollBar().maximum() == 0
        _assert_no_sibling_overlap(window)
        for button in window.tabs.currentWidget().findChildren(QPushButton):
            if button.isVisible() and button.text():
                needed = button.fontMetrics().horizontalAdvance(button.text()) + 28
                assert button.width() >= needed, f"button text clipped: {button.text()}"
    _show_named_tab(window, "Recovery")
    app.processEvents()
    _assert_no_sibling_overlap(window)
    assert "Audio is never saved" in window.recovery_state.text() or "Recovery is off" in window.recovery_state.text()
    _save_capture(window, "manuscript-recovery.png")
    window.close()


def test_internal_evaluation_requires_explicit_launcher_flag(app, tmp_path):
    consumer = EchoLinkWindow(EchoLinkStore(tmp_path / "consumer.db"), enable_hotkey=False)
    assert "Internal evaluation" not in [
        consumer.tabs.tabText(index) for index in range(consumer.tabs.count())
    ]
    consumer.close()

    internal = EchoLinkWindow(
        EchoLinkStore(tmp_path / "internal.db"),
        enable_hotkey=False,
        internal_evaluation=True,
    )
    sections = [internal.tabs.tabText(index) for index in range(internal.tabs.count())]
    assert "Capstone" not in sections
    assert sections.count("Internal evaluation") == 1
    assert internal.tabs.tabText(internal.tabs.currentIndex()) == "Internal evaluation"
    assert internal.windowTitle() == "Manuscript Windows Version 2 - Internal Evaluation"
    assert internal.evaluation_prompt.placeholderText() == "Start a run to load case 1."
    internal.close()


def test_recovery_marks_uncertain_insertion_as_check_target(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.keep_recovery_text = True
    store.save_settings(settings)
    store.checkpoint_recovery(
        "uncertain-session",
        "Text that may already be present.",
        "fast",
        status="insertion_uncertain",
        error_message="Check the target before copying. Echo-Link will not retry automatically.",
    )
    window = EchoLinkWindow(store, enable_hotkey=False)
    window._refresh_recovery()

    status = window.recovery_table.item(0, 1)
    assert status.text() == "Check target"
    assert "will not retry" in status.toolTip()
    window.close()


def test_settings_window_has_no_stacked_labels(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    dialog = SettingsDialog(window.store, window.engine, window)
    dialog.resize(660, 620)
    dialog.show()
    app.processEvents()
    _assert_no_sibling_overlap(dialog)
    assert dialog.model_status.text()
    assert dialog.recommendation_label.text()
    assert dialog.use_recommended_button.isEnabled()
    assert dialog.speed_check_button.isEnabled()
    assert dialog.import_model_button.isEnabled()
    assert not dialog.remove_model_button.isEnabled()
    dialog._use_recommended_setup()
    assert dialog.model_combo.currentData() == dialog.local_recommendation.model_name
    assert dialog.device_combo.currentData() == dialog.local_recommendation.device
    assert dialog.model_combo.currentData() == "base.en"
    assert dialog.language_combo.isEnabled() is False
    assert dialog.streaming_check.isChecked() is True
    assert dialog.echo_bar_check.isChecked() is True
    assert dialog.echo_bar_position == (None, None)
    assert dialog.pause_media_check.isChecked() is True
    dialog.echo_bar_position = (100, 200)
    dialog._reset_echo_bar_position()
    assert dialog.echo_bar_position == (None, None)
    assert dialog.echo_bar_position_label.text() == "Top center"
    assert dialog.sound_check.isChecked() is True
    assert dialog.pre_roll_spin.value() == 500
    assert dialog.max_recording_spin.value() == 120
    assert dialog.close_to_tray_check.isChecked() is True
    assert dialog.startup_check.isChecked() is False
    assert "system tray" in dialog.startup_check.toolTip()
    assert dialog.learning_check.isChecked() is False
    assert dialog.history_check.isChecked() is True
    assert dialog.history_retention_spin.value() == 3
    assert dialog.speech_logging_check.isChecked() is False
    assert dialog.speech_retention_spin.value() == 15
    assert dialog.speech_retention_spin.isEnabled() is False
    assert dialog.insertion_mode_combo.currentData() == "direct"
    assert dialog.mode_combo.findData(DictationMode.DEVELOPER.value) >= 0
    assert dialog.start_sound_combo.currentText() == "Speech On.wav"
    assert dialog.stop_sound_combo.currentText() == "Built-in completion tone"
    assert dialog.output_combo.currentData() is None
    assert dialog.bluetooth_delay_combo.currentData() == -1
    assert dialog.hands_free_check.isChecked() is True
    assert dialog.hands_free_hotkey_edit.text() == "ctrl+alt+space"
    assert dialog.command_hotkey_edit.text() == "ctrl+shift+space"
    assert dialog.automatic_writing_hud_check.isChecked() is True
    assert dialog.private_paste_hotkey_edit.text() == "ctrl+alt+v"
    assert dialog.silence_stop_check.isChecked() is True
    assert dialog.silence_timeout_spin.value() == 2.5
    assert [
        dialog.settings_tabs.tabText(index)
        for index in range(dialog.settings_tabs.count())
    ] == ["Input and audio", "Writing", "Local AI", "App", "Privacy"]
    title = next(label for label in dialog.findChildren(QLabel) if label.text() == "Settings")
    assert title.height() >= title.fontMetrics().height()
    _save_capture(dialog, "manuscript-settings.png")
    scroll = dialog.settings_scrolls["Input and audio"]
    assert scroll is not None
    scroll.ensureWidgetVisible(dialog.pause_media_check)
    app.processEvents()
    _save_capture(dialog, "manuscript-settings-media-hud.png")
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() // 2)
    app.processEvents()
    _save_capture(dialog, "manuscript-settings-audio.png")
    dialog.settings_tabs.setCurrentIndex(2)
    model_scroll = dialog.settings_scrolls["Local AI"]
    assert model_scroll.horizontalScrollBar().maximum() == 0
    model_scroll.verticalScrollBar().setValue(0)
    app.processEvents()
    assert dialog.model_combo.isVisible()
    assert dialog.model_combo.width() > 0
    assert dialog.device_combo.width() > 0
    assert dialog.model_folder_button.isVisible()
    assert dialog.import_model_button.isVisible()
    assert dialog.remove_model_button.isVisible()
    for button in (
        dialog.model_folder_button,
        dialog.import_model_button,
        dialog.remove_model_button,
        dialog.use_recommended_button,
        dialog.speed_check_button,
        dialog.check_button,
    ):
        needed = button.fontMetrics().horizontalAdvance(button.text()) + 28
        assert button.width() >= needed, f"button text clipped: {button.text()}"
    _save_capture(dialog, "manuscript-settings-models-top.png")
    model_scroll.verticalScrollBar().setValue(model_scroll.verticalScrollBar().maximum())
    app.processEvents()
    bottom_left = dialog.check_button.mapTo(model_scroll.viewport(), QPoint(0, 0))
    bottom_rect = QRect(bottom_left, dialog.check_button.size())
    assert model_scroll.viewport().rect().contains(bottom_rect)
    assert dialog.model_status.text()
    _save_capture(dialog, "manuscript-settings-models-bottom.png")
    dialog.settings_tabs.setCurrentIndex(3)
    app_scroll = dialog.settings_scrolls["App"]
    assert app_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.startup_check.isVisible()
    _save_capture(dialog, "manuscript-settings-app.png")
    dialog.settings_tabs.setCurrentIndex(4)
    privacy_scroll = dialog.settings_scrolls["Privacy"]
    privacy_scroll.ensureWidgetVisible(dialog.learning_check)
    app.processEvents()
    _save_capture(dialog, "manuscript-settings-learning.png")
    dialog.close()
    window.close()


def test_history_exposes_encrypted_speech_log_analysis_and_review(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    settings = store.get_settings()
    settings.speech_logging_enabled = True
    store.save_settings(settings)
    store.add_speech_improvement_log(
        raw_text="We should go. to the store",
        deterministic_text="We should go. to the store.",
        suggested_text="We should go. to the store.",
        final_text="We should go to the store.",
        audio_pcm=b"\x01\x00" * 160,
        sample_rate=16000,
        mode="fast",
        analysis={
            "version": 1,
            "summary": "Likely source: speech recognition (1 internal period).",
            "counts": {"speech_recognition": 1},
            "issues": [],
        },
    )
    window = EchoLinkWindow(store, enable_hotkey=False)
    _show_named_tab(window, "History")
    window.history_sections.setCurrentIndex(1)
    window.show()
    app.processEvents()
    assert window.history_sections.tabText(1) == "Speech improvement logs"
    assert window.speech_log_table.rowCount() == 1
    assert "speech recognition" in window.speech_analysis_text.toPlainText()
    assert window.speech_raw_text.toPlainText() == "We should go. to the store"
    window.speech_punctuation_wrong.setChecked(True)
    window.speech_notes.setText("The period split one sentence.")
    window._save_speech_log_review()
    reviewed = store.list_speech_improvement_logs()[0]
    assert reviewed["punctuation_wrong"] == 1
    assert reviewed["user_notes"] == "The period split one sentence."
    _save_capture(window, "manuscript-speech-history.png")
    window.close()


def test_system_tray_keeps_hotkey_service_ready_when_window_closes(
    app, tmp_path, monkeypatch
):
    restored_handles = []
    monkeypatch.setattr(
        window_module,
        "_restore_native_window",
        lambda handle: restored_handles.append(handle),
        raising=False,
    )
    app.setQuitOnLastWindowClosed(True)
    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=True,
    )
    window.show()
    app.processEvents()
    assert window.tray_enabled
    assert window.tray_show_action.text() == "Show Manuscript"
    assert window.tray_dictate_action.text() == "Dictate in Manuscript"
    assert window.tray_hands_free_action.text() == "Start hands-free dictation"
    assert window.tray_command_action.text() == "Writing HUD for selected text"
    assert window.tray_settings_action.text() == "Settings"
    assert window.tray_quit_action.text() == "Quit Manuscript"
    assert app.quitOnLastWindowClosed() is False
    window.close()
    app.processEvents()
    assert window.isHidden()
    assert window._shutdown_complete is False
    window.tray_show_action.trigger()
    app.processEvents()
    assert window.isVisible()
    assert restored_handles == [int(window.winId())]
    window.hide()
    window._tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    app.processEvents()
    assert window.isVisible()
    assert restored_handles == [int(window.winId()), int(window.winId())]
    window._quitting = True
    window.close()
    assert window._shutdown_complete is True
    app.setQuitOnLastWindowClosed(True)


def test_dark_palette_keeps_text_contrast(app):
    assert _contrast_ratio(COLORS["text"], COLORS["window"]) >= 4.5
    assert _contrast_ratio(COLORS["text"], COLORS["surface"]) >= 4.5
    assert _contrast_ratio(COLORS["muted"], COLORS["surface"]) >= 4.5
    assert _contrast_ratio(COLORS["on_accent"], COLORS["accent"]) >= 4.5
    assert _contrast_ratio(COLORS["border"], COLORS["surface"]) >= 3.0
    assert _contrast_ratio(COLORS["red"], COLORS["window"]) >= 3.0


def test_selected_text_tab_requires_review_before_replace(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    _show_named_tab(window, "Writing tools")
    assert window.writing_quick_buttons == {}
    assert window.writing_command_label.text() == "Tone and style"
    assert window.writing_command_combo.count() >= 20
    assert window.writing_profile_combo.count() == 6
    assert window.writing_profile_combo.itemText(5) == "Code-safe"
    window.writing_source.setPlainText("This are rough text.")
    window._analyze_writing()
    assert "Readability score:" in window.writing_analysis.toPlainText()
    assert window.writing_editors.currentWidget() is window.writing_analysis
    _save_capture(window, "manuscript-readability.png")
    window._handle_writing_result((True, "This is polished text.", "Ready for review."))
    assert window.writing_result_edit.toPlainText() == "This is polished text."
    assert window.writing_editors.currentWidget() is window.writing_result_edit
    assert window.writing_replace_button.isEnabled()
    assert window.writing_copy_button.isEnabled()
    window._reject_writing_result()
    assert window.writing_result_edit.toPlainText() == ""
    assert not window.writing_replace_button.isEnabled()
    window.close()


def test_local_sources_tab_returns_path_and_line_citations(app, tmp_path):
    source = tmp_path / "approved.md"
    source.write_text("Echo-Link keeps selected text private and local.\n", encoding="utf-8")
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    assert window.local_source_index.approve([source])[0].indexed is True
    window._refresh_local_source_state()
    window.source_query.setPlainText("selected text private")
    window._search_local_sources()
    assert f"{source.resolve()}:1" in window.source_results.toPlainText()
    assert "1 local source" in window.source_state.text()
    window.source_query.setPlainText("Echo-Link keeps selected text private and local")
    window._check_local_similarity()
    deadline = time.monotonic() + 2.0
    while not window.source_similarity_button.isEnabled() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert "Local similarity: 100.0%" in window.source_results.toPlainText()
    _show_named_tab(window, "Sources")
    window.show()
    app.processEvents()
    _save_capture(window, "manuscript-local-sources.png")
    window._clear_local_sources()
    assert window.local_source_index.approved_paths == ()
    window.close()


def test_review_event_exposes_raw_corrected_diff_and_actions(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window._handle_session_event(
        SessionEvent(
            state=SessionState.COMPLETE,
            message="Review the text.",
            raw_text="hello world",
            final_text="Hello world.",
            review_pending=True,
        )
    )
    assert window.raw_text.toPlainText() == "hello world"
    assert window.result_text.toPlainText() == "Hello world."
    assert window.diff_text.toPlainText()
    assert window.result_text.isReadOnly() is False
    assert window.accept_button.isEnabled()
    assert window.insert_button.isEnabled()
    assert window.retry_button.isEnabled()
    assert window.reject_button.isEnabled()
    window.close()


def test_partial_insertion_event_shows_only_unsent_suffix(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window._handle_session_event(
        SessionEvent(
            state=SessionState.COMPLETE,
            message="Ready",
            raw_text="hello complete text",
            final_text="Hello complete text.",
        )
    )
    window._handle_session_event(
        SessionEvent(
            state=SessionState.ERROR,
            message="Direct input stopped. Check the target.",
            final_text="unsent suffix",
            partial_insertion=True,
            possibly_inserted_utf16_units=12,
            total_insertion_utf16_units=25,
        )
    )

    assert window.result_text.toPlainText() == "unsent suffix"
    assert window.raw_text.toPlainText() == ""
    assert window.latest_text == "unsent suffix"
    assert window.copy_button.isEnabled() is True
    window.close()


def test_learning_proposals_default_to_not_saved_and_manager_can_disable(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    proposal = LearningProposal("teh", "the")
    dialog = LearningProposalDialog((proposal,))
    assert dialog.selected_proposals() == ()
    assert dialog.save_button.isEnabled() is False
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    assert dialog.selected_proposals() == (proposal,)
    assert dialog.save_button.isEnabled() is True
    dialog.show()
    app.processEvents()
    _save_capture(dialog, "manuscript-learning-proposal.png")
    dialog.close()

    item_id = store.add_learned_correction("teh", "the")
    manager = LearningManagerDialog(store)
    manager.show()
    app.processEvents()
    _save_capture(manager, "manuscript-learning-manager.png")
    manager.table.selectRow(0)
    manager._toggle_selected()
    assert store.list_learned_corrections()[0]["enabled"] == 0
    manager.table.selectRow(0)
    manager._delete_selected()
    assert store.list_learned_corrections() == []
    assert item_id > 0
    manager.close()


def test_review_saves_only_explicitly_selected_learning(app, tmp_path, monkeypatch):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    proposal = LearningProposal("teh", "the")
    monkeypatch.setattr(
        window.engine,
        "learning_proposals_for_review",
        lambda _text, *, decision: (proposal,) if decision == "accepted" else (),
    )
    monkeypatch.setattr(
        window.engine,
        "complete_review",
        lambda _text, *, decision: decision == "accepted",
    )

    class ApprovedDialog:
        def __init__(self, proposals, _parent):
            self.proposals = proposals

        def exec(self):
            return 1

        def selected_proposals(self):
            return self.proposals

    monkeypatch.setattr(window_module, "LearningProposalDialog", ApprovedDialog)
    window.result_text.setPlainText("Send the report.")
    window._complete_review("accepted")
    assert store.learned_correction_map() == {"teh": "the"}
    window.close()


def test_echo_bar_shows_live_status_without_accepting_focus(app, tmp_path):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    window._handle_session_event(
        SessionEvent(state=SessionState.LISTENING, message="Listening...")
    )
    app.processEvents()
    assert window.echo_bar.isVisible()
    assert "Listening" in window.echo_bar.status.text()
    assert window.echo_bar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    window._handle_level(0.5)
    assert window.echo_bar.level.value() == 50
    window.echo_bar.show_state(
        SessionState.LISTENING,
        "Hands-free listening... Press Esc to cancel.",
    )
    assert window.echo_bar.status.text() == "Hands-free listening - Esc cancels"
    _save_capture(window.echo_bar, "manuscript-hud-listening.png")
    window.close()


def test_dictation_hotkey_uses_target_captured_at_physical_keypress(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    target = WindowSnapshot(81, 91, "Draft - Word", "OpusApp")
    captured = []
    monkeypatch.setattr(window_module, "active_window", lambda: target)
    monkeypatch.setattr(
        window.engine,
        "start_recording",
        lambda **kwargs: captured.append(kwargs.get("target_window")) or True,
    )

    window._capture_dictation_hotkey_target()
    app.processEvents()

    assert captured == [target]
    window.close()


def test_dictation_hotkey_starts_during_processing_without_reactivating_target(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    target = WindowSnapshot(81, 91, "Draft - Word", "OpusApp")
    captured = []
    activated = []
    monkeypatch.setattr(
        window.engine,
        "start_recording",
        lambda **kwargs: captured.append(kwargs.get("target_window")) or True,
    )
    monkeypatch.setattr(
        window.engine.inserter,
        "activate_window",
        lambda value: activated.append(value),
    )
    with window.engine._lock:
        window.engine._active_job = SimpleNamespace(sequence=1)

    window._hotkey_press(target)

    assert captured == [target]
    assert activated == []
    with window.engine._lock:
        window.engine._active_job = None
    window.close()


def test_processing_event_during_capture_keeps_recording_enabled_and_sounds_exact(
    app,
    tmp_path,
    monkeypatch,
):
    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    sounds = []
    tray_actions = []
    monkeypatch.setattr(window_module, "play_start", lambda _settings: sounds.append("start"))
    monkeypatch.setattr(window_module, "play_stop", lambda _settings: sounds.append("stop"))
    monkeypatch.setattr(
        type(window.engine),
        "state",
        property(lambda _engine: SessionState.LISTENING),
    )
    window.tray_icon = SimpleNamespace(
        setToolTip=lambda _text: None,
        hide=lambda: None,
        deleteLater=lambda: None,
    )
    window.tray_dictate_action = SimpleNamespace(
        setText=lambda text: tray_actions.append(text)
    )
    window.tray_hands_free_action = SimpleNamespace(setText=lambda _text: None)

    window._handle_session_event(
        SessionEvent(
            SessionState.LISTENING,
            "Listening...",
            capture_id=2,
            capture_started=True,
        )
    )
    window._handle_session_event(
        SessionEvent(
            SessionState.REFINING,
            "AI processing 1. 1 queued.",
            capture_id=2,
            job_sequence=1,
        )
    )

    assert sounds == ["start"]
    assert window.record_button.text() == "Stop recording"
    assert window.record_button.isEnabled()
    assert tray_actions[-1] == "Stop dictation"
    window._handle_session_event(
        SessionEvent(
            SessionState.TRANSCRIBING,
            "Transcribing locally...",
            capture_id=2,
            job_sequence=2,
            capture_stopped=True,
        )
    )
    assert sounds == ["start", "stop"]
    window.close()


def test_echo_bar_position_is_clamped_and_saved(app, tmp_path):
    saved = []
    bar = EchoBar(position_changed=lambda x, y: saved.append((x, y)))
    bar.show_state(SessionState.LISTENING, "Listening...")
    app.processEvents()
    point = bar.move_to(QPoint(999999, 999999), save=True)
    screen = app.primaryScreen().availableGeometry()
    assert screen.contains(point)
    assert bar.frameGeometry().right() <= screen.right()
    assert bar.frameGeometry().bottom() <= screen.bottom()
    assert saved[-1] == (point.x(), point.y())
    bar.set_saved_position(None, None)
    assert bar.x() == screen.x() + (screen.width() - bar.width()) // 2
    bar.close()


def test_dictation_state_owns_media_pause_and_resume(app, tmp_path):
    class MediaSpy:
        def __init__(self):
            self.calls = []

        def pause(self):
            self.calls.append("pause")

        def resume(self):
            self.calls.append("resume")

        def shutdown(self):
            self.calls.append("shutdown")

    window = EchoLinkWindow(EchoLinkStore(tmp_path / "echo-link.db"), enable_hotkey=False)
    media = MediaSpy()
    window.media_pause = media
    window._handle_session_event(SessionEvent(SessionState.LISTENING, "Listening..."))
    window._handle_session_event(SessionEvent(SessionState.LISTENING, "Still listening..."))
    window._handle_session_event(SessionEvent(SessionState.TRANSCRIBING, "Processing..."))
    assert media.calls == ["pause", "resume"]
    window.close()
    assert media.calls[-1] == "shutdown"


def test_capstone_run_records_edited_acceptance(app, tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    window._start_evaluation_run()
    case = window._current_evaluation_case()
    window.evaluation_trial_id = store.start_evaluation_trial(window.evaluation_run_id, case.id)
    window._handle_evaluation_event(
        SessionEvent(
            state=SessionState.COMPLETE,
            message="Ready",
            raw_text="hello there",
            final_text="Hello their.",
        )
    )
    window.evaluation_output.setPlainText("Hello there.")
    window._complete_evaluation_case("accepted")
    row = store.list_evaluation_trials(window.evaluation_run_id)[0]
    assert row["decision"] == "accepted"
    assert row["edited"] == 1
    assert row["final_text"] == "Hello there."
    window.close()


def test_echo_link_private_paste_stages_pastes_and_clears_in_memory(
    app,
    tmp_path,
    monkeypatch,
):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    window = EchoLinkWindow(store, enable_hotkey=False)
    inserted: list[str] = []
    monkeypatch.setattr(
        window.private_paste_inserter,
        "insert",
        lambda text: inserted.append(text) or object(),
    )

    window.latest_text = "memory-only private text"
    window._copy_latest()

    assert window.private_text_buffer.peek() == "memory-only private text"
    assert window.clear_private_paste_button.isEnabled()
    assert "24 characters" in window.private_paste_state.text()
    assert "Windows clipboard unchanged" in window.status_text.text()
    assert window.private_paste_inserter.mode is InsertionMode.DIRECT

    window._paste_private_text()
    assert inserted == ["memory-only private text"]
    assert window.private_text_buffer.peek() == "memory-only private text"

    window._clear_private_text()
    assert window.private_text_buffer.peek() is None
    assert not window.clear_private_paste_button.isEnabled()

    window.close()


def _save_capture(widget, filename: str) -> None:
    target = os.environ.get("ECHOLINK_SCREENSHOT_DIR")
    if not target:
        return
    directory = Path(target)
    directory.mkdir(parents=True, exist_ok=True)
    assert widget.grab().save(str(directory / filename))


def test_the_hud_is_lowered_even_while_the_selection_monitor_is_paused(
    app, tmp_path, monkeypatch
):
    """The HUD must not behave as pinned when Pin was never pressed.

    The window is created with WindowStaysOnTopHint and set_stays_on_top(True)
    runs the moment the writer is detected in a document, so the only thing
    that can lower it again is _release_hud_top_if_focus_left. That had one
    caller, _handle_automatic_selection_cleared, which runs from the selection
    monitor - and the monitor returns immediately while paused. It is paused
    for the whole time a review is being worked on.

    So during a review nothing could lower the HUD, which is exactly what the
    owner reported: it stays in front of everything without Pin being clicked.
    """
    from echo_link_app import window as window_module

    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=False,
    )
    palette = window.rewrite_palette
    palette.show()
    app.processEvents()
    assert not palette.pinned
    palette.set_stays_on_top(True)
    assert bool(palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    # The writer has clicked something that is neither the HUD nor the
    # document under review.
    class _Other:
        handle = 987654
        process_id = 4242

    monkeypatch.setattr(window_module, "active_window", lambda: _Other())
    window.selected_text_record = None

    window._check_palette_focus()
    app.processEvents()

    assert not bool(palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    window.close()


def test_a_pinned_hud_is_not_lowered_by_the_focus_check(app, tmp_path, monkeypatch):
    """Pin is the writer's explicit instruction and outranks focus following.

    The fix above must not turn Pin into a suggestion.
    """
    from echo_link_app import window as window_module

    window = EchoLinkWindow(
        EchoLinkStore(tmp_path / "echo-link.db"),
        enable_hotkey=False,
        enable_tray=False,
    )
    palette = window.rewrite_palette
    palette.show()
    palette._set_pinned(True)
    app.processEvents()
    assert bool(palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    class _Other:
        handle = 987654
        process_id = 4242

    monkeypatch.setattr(window_module, "active_window", lambda: _Other())
    window.selected_text_record = None

    window._check_palette_focus()
    app.processEvents()

    assert bool(palette.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    window.close()
