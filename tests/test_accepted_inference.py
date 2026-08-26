from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from echo_link_core.accepted_inference import AcceptedInferenceCoordinator
from echo_link_core.correction import CorrectionPipeline
from echo_link_core.drafting import LocalDraftAssistant
from echo_link_core.local_chat import ApprovedSourceChat
from echo_link_core.local_sources import LocalSourceIndex
from echo_link_core.models import DictationMode, DictationSettings
from echo_link_core.polish_types import (
    AcceptanceProbeResult,
    ExecutionPlan,
    GenerationRequest,
    GenerationResult,
    PolishMode,
    PolishTask,
    RuntimeBackend,
    RuntimeFailureKind,
    RuntimeStatus,
)


def _plan(
    *,
    pack_id: str = "gemma4-e4b-qat-v1",
    model: str = "gemma4:e4b-it-qat",
    standby_model: str = "gemma4:e2b-it-qat",
) -> ExecutionPlan:
    return ExecutionPlan(
        policy_version=6,
        mode=PolishMode.AUTO,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id=pack_id,
        model=model,
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=512,
        require_full_gpu=True,
        standby_model=standby_model,
    )


class _Runtime:
    runtime_id = "named-pipe:test"
    parallel_slots = 3

    def __init__(self) -> None:
        self.calls: list[GenerationRequest] = []
        self.status_calls = 0
        self.actual_model = ""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        if request.command == "Proofread":
            text = "Dogs are good."
        elif request.command == "Summarize":
            text = "Speech stays local [S1]."
        else:
            text = request.text
        return GenerationResult(
            text=text,
            model=self.actual_model or request.plan.model,
            backend=RuntimeBackend.CUDA,
            elapsed_seconds=0.01,
        )

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
        self.status_calls += 1
        return RuntimeStatus(
            model=model,
            loaded=True,
            backend=RuntimeBackend.CUDA,
            model_bytes=4_000,
            gpu_bytes=4_000,
        )

    def acceptance_probe(self, plan: ExecutionPlan) -> AcceptanceProbeResult:
        return AcceptanceProbeResult(
            accepted=True,
            elapsed_seconds=0.1,
            correctness_passed=True,
            protected_passed=True,
            backend=plan.backend,
            model_identity=plan.model,
        )

    def prepare(self, _plan: ExecutionPlan, *, consent: bool) -> bool:
        return True

    def close(self) -> None:
        return None


def _result(plan: ExecutionPlan, *, failed: bool = False):
    return SimpleNamespace(
        plan=plan,
        used_llm=not failed,
        review_ready=not failed,
        runtime_failure_kind=(
            RuntimeFailureKind.GPU_FAILURE if failed else RuntimeFailureKind.NONE
        ),
    )


def test_stale_result_cannot_replace_or_clear_a_newer_accepted_plan():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    newer_plan = _plan(
        pack_id="gemma4-e2b-qat-v1",
        model="gemma4:e2b-it-qat",
        standby_model="",
    )
    older = coordinator.begin_update()
    newer = coordinator.begin_update()

    coordinator.generate(
        GenerationRequest(
            text="Dogs is good.",
            command="Proofread",
            profile="General",
            instruction="",
            language="",
            protected_terms=(),
            plan=newer_plan,
        )
    )
    assert coordinator.publish_correction_result(_result(newer_plan), newer)
    assert not coordinator.publish_correction_result(_result(_plan(), failed=True), older)

    lease = coordinator.lease()
    assert lease is not None
    assert lease.plan == newer_plan


def test_actual_standby_model_becomes_the_full_accepted_plan():
    runtime = _Runtime()
    runtime.actual_model = "gemma4:e2b-it-qat"
    coordinator = AcceptedInferenceCoordinator(runtime)
    coordinator.register_model_pack("gemma4-e4b-qat-v1", "gemma4:e4b-it-qat")
    coordinator.register_model_pack("gemma4-e2b-qat-v1", "gemma4:e2b-it-qat")
    token = coordinator.begin_update()
    plan = _plan()

    generated = coordinator.generate(
        GenerationRequest(
            text="Dogs is good.",
            command="Proofread",
            profile="General",
            instruction="",
            language="",
            protected_terms=(),
            plan=plan,
        )
    )
    assert generated.model == "gemma4:e2b-it-qat"
    assert coordinator.publish_correction_result(_result(plan), token)

    lease = coordinator.lease()
    assert lease is not None
    assert lease.plan.model == "gemma4:e2b-it-qat"
    assert lease.plan.pack_id == "gemma4-e2b-qat-v1"
    assert lease.plan.standby_model == ""


def test_actual_cpu_spill_cannot_replace_a_full_gpu_lease():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    plan = _plan(standby_model="")
    coordinator.accept_verified(plan)
    runtime.actual_model = plan.model

    original_generate = runtime.generate

    def generate_on_cpu(request):
        generated = original_generate(request)
        return GenerationResult(
            text=generated.text,
            model=generated.model,
            backend=RuntimeBackend.CPU,
            elapsed_seconds=generated.elapsed_seconds,
        )

    runtime.generate = generate_on_cpu

    try:
        coordinator.generate_task(
            "Dogs is good.",
            command="Proofread",
            model=plan.model,
        )
    except RuntimeError as exc:
        assert "spilled to the CPU" in str(exc)
    else:
        raise AssertionError("A full-GPU lease accepted CPU inference")

    lease = coordinator.lease()
    assert lease is not None
    assert lease.plan == plan


def test_cached_lease_checks_are_constant_time_and_preload_checks_residency():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    plan = _plan(standby_model="")
    assert coordinator.accept_verified(plan)

    for _ in range(10):
        assert coordinator.is_current(
            plan.pack_id,
            plan.model,
            plan.policy_version,
            require_loaded=True,
        )
        assert coordinator.preload(model=plan.model, timeout=90.0)

    assert runtime.status_calls == 10
    assert runtime.calls == []


def test_claimed_success_without_matching_generation_proof_is_not_published():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    token = coordinator.begin_update()

    assert not coordinator.publish_correction_result(_result(_plan()), token)
    assert coordinator.lease() is None


def test_unrelated_failed_candidate_preserves_a_healthy_accepted_lease():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    healthy = _plan(standby_model="")
    failed = _plan(
        pack_id="gemma4-e2b-qat-v1",
        model="gemma4:e2b-it-qat",
        standby_model="",
    )
    coordinator.accept_verified(healthy)
    token = coordinator.begin_update()

    assert not coordinator.publish_correction_result(_result(failed, failed=True), token)
    lease = coordinator.lease()
    assert lease is not None
    assert lease.plan == healthy


def test_nested_generation_fails_fast_instead_of_deadlocking():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    accepted = _plan(standby_model="")
    coordinator.accept_verified(accepted)
    original_generate = runtime.generate

    def nested(request):
        coordinator.generate_task(
            "nested",
            command="Proofread",
            model=accepted.model,
        )
        return original_generate(request)

    runtime.generate = nested
    try:
        coordinator.generate_task(
            "outer",
            command="Proofread",
            model=accepted.model,
        )
    except RuntimeError as exc:
        assert "Nested" in str(exc)
    else:
        raise AssertionError("Nested local AI work did not fail fast")


def test_dictation_drafts_and_source_chat_share_the_accepted_runtime(tmp_path):
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    plan = _plan(standby_model="")
    assert coordinator.accept_verified(plan)

    settings = DictationSettings(
        mode=DictationMode.ACCURACY,
        append_trailing_space=False,
        local_refinement_enabled=True,
    )
    correction = CorrectionPipeline(lambda: {}, settings, coordinator)
    correction.accept_model(plan.model)
    corrected = correction.correct("dogs is good", DictationMode.ACCURACY)

    drafted = LocalDraftAssistant(inference=coordinator).generate(
        "Email",
        facts="Alpha launch is ready.",
        model=plan.model,
    )

    source = tmp_path / "source.txt"
    source.write_text("Speech stays local on this computer.\n", encoding="utf-8")
    index = LocalSourceIndex()
    assert index.approve((source,))[0].indexed
    answered = ApprovedSourceChat(inference=coordinator).ask(
        "Where does speech stay?",
        sources=index,
        model=plan.model,
    )

    assert corrected.text == "Dogs are good."
    assert corrected.source == "ai"
    assert drafted.text == "Alpha launch is ready."
    assert answered.answer == "Speech stays local [S1]."
    assert [request.command for request in runtime.calls] == [
        "Proofread",
        "Rewrite",
        "Summarize",
    ]


def test_all_consumers_share_one_fifo_generation_lane():
    entered = threading.Event()
    release = threading.Event()

    class BlockingRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.order: list[str] = []
            self.state_lock = threading.Lock()

        def generate(self, request: GenerationRequest) -> GenerationResult:
            with self.state_lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.order.append(request.command)
                if len(self.order) == 1:
                    entered.set()
            if len(self.order) == 1:
                release.wait(timeout=2.0)
            with self.state_lock:
                self.active -= 1
            return GenerationResult(
                text=request.text,
                model=request.plan.model,
                backend=request.plan.backend,
                elapsed_seconds=0.01,
            )

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    plan = _plan(standby_model="")
    coordinator.accept_verified(plan)

    workers = []
    for command in ("Proofread", "Rewrite", "Summarize"):
        worker = threading.Thread(
            target=lambda value=command: coordinator.generate_task(
                value,
                command=value,
                model=plan.model,
            )
        )
        workers.append(worker)
        worker.start()
        if command == "Proofread":
            assert entered.wait(timeout=1.0)
        else:
            time.sleep(0.03)

    release.set()
    for worker in workers:
        worker.join(timeout=2.0)

    assert coordinator.parallel_slots == 1
    assert runtime.max_active == 1
    assert runtime.order == ["Proofread", "Rewrite", "Summarize"]


def test_queued_consumer_cannot_run_after_accepted_plan_changes():
    first_entered = threading.Event()
    release_first = threading.Event()

    class BlockingRuntime(_Runtime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls.append(request)
            if len(self.calls) == 1:
                first_entered.set()
                release_first.wait(timeout=2.0)
            return GenerationResult(
                text=request.text,
                model=request.plan.model,
                backend=request.plan.backend,
                elapsed_seconds=0.01,
            )

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    original = _plan(standby_model="")
    replacement = _plan(
        pack_id="gemma4-e2b-qat-v1",
        model="gemma4:e2b-it-qat",
        standby_model="",
    )
    coordinator.accept_verified(original)
    failures: list[str] = []

    def run_first() -> None:
        try:
            coordinator.generate_task(
                "first",
                command="Proofread",
                model=original.model,
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    first = threading.Thread(target=run_first)

    def run_queued() -> None:
        try:
            coordinator.generate_task(
                "second",
                command="Proofread",
                model=original.model,
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    second = threading.Thread(target=run_queued)
    first.start()
    assert first_entered.wait(timeout=1.0)
    second.start()
    time.sleep(0.03)
    coordinator.accept_verified(replacement)
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert len(runtime.calls) == 1
    assert len(failures) == 2
    assert all("plan changed" in value for value in failures)


def test_consumer_discards_result_when_lease_changes_during_generation():
    entered = threading.Event()
    release = threading.Event()

    class BlockingRuntime(_Runtime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls.append(request)
            entered.set()
            release.wait(timeout=2.0)
            return GenerationResult(
                text=request.text,
                model=request.plan.model,
                backend=request.plan.backend,
                elapsed_seconds=0.01,
            )

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    original = _plan(standby_model="")
    replacement = _plan(
        pack_id="gemma4-e2b-qat-v1",
        model="gemma4:e2b-it-qat",
        standby_model="",
    )
    coordinator.accept_verified(original)
    failures: list[str] = []

    def run_generation() -> None:
        try:
            coordinator.generate_task(
                "first",
                command="Proofread",
                model=original.model,
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    worker = threading.Thread(target=run_generation)
    worker.start()
    assert entered.wait(timeout=1.0)
    coordinator.accept_verified(replacement)
    release.set()
    worker.join(timeout=2.0)

    assert failures and "plan changed" in failures[0]


def test_close_rejects_waiting_and_future_generation_without_deadlock():
    entered = threading.Event()
    release = threading.Event()

    class BlockingRuntime(_Runtime):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls.append(request)
            entered.set()
            release.wait(timeout=2.0)
            return GenerationResult(
                text=request.text,
                model=request.plan.model,
                backend=request.plan.backend,
                elapsed_seconds=0.01,
            )

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    accepted = _plan(standby_model="")
    coordinator.accept_verified(accepted)
    failures: list[str] = []

    def run_active() -> None:
        try:
            coordinator.generate_task(
                "active",
                command="Proofread",
                model=accepted.model,
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    active = threading.Thread(target=run_active)

    def run_waiting() -> None:
        try:
            coordinator.generate_task(
                "waiting",
                command="Proofread",
                model=accepted.model,
            )
        except RuntimeError as exc:
            failures.append(str(exc))

    waiting = threading.Thread(target=run_waiting)
    active.start()
    assert entered.wait(timeout=1.0)
    waiting.start()
    time.sleep(0.03)
    coordinator.close()
    release.set()
    active.join(timeout=2.0)
    waiting.join(timeout=2.0)

    assert len(failures) == 2
    assert any("shutting down" in value for value in failures)
    try:
        coordinator.generate_task(
            "future",
            command="Proofread",
            model=accepted.model,
        )
    except RuntimeError as exc:
        assert "not ready" in str(exc) or "shutting down" in str(exc)
    else:
        raise AssertionError("Closed local AI accepted a new generation")


def test_prepare_and_probe_wait_for_active_generation_lane():
    entered = threading.Event()
    release = threading.Event()

    class BlockingRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.operations: list[str] = []

        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.operations.append("generate-start")
            entered.set()
            release.wait(timeout=2.0)
            self.operations.append("generate-end")
            return super().generate(request)

        def prepare(self, _plan: ExecutionPlan, *, consent: bool) -> bool:
            self.operations.append("prepare")
            return True

        def acceptance_probe(self, plan: ExecutionPlan) -> AcceptanceProbeResult:
            self.operations.append("probe")
            return super().acceptance_probe(plan)

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    accepted = _plan(standby_model="")
    coordinator.accept_verified(accepted)
    generation = threading.Thread(
        target=lambda: coordinator.generate_task(
            "active",
            command="Proofread",
            model=accepted.model,
        )
    )
    generation.start()
    assert entered.wait(timeout=1.0)

    prepare = threading.Thread(
        target=lambda: coordinator.prepare(accepted, consent=False)
    )
    probe = threading.Thread(target=lambda: coordinator.acceptance_probe(accepted))
    prepare.start()
    probe.start()
    time.sleep(0.05)
    assert runtime.operations == ["generate-start"]
    release.set()
    generation.join(timeout=2.0)
    prepare.join(timeout=2.0)
    probe.join(timeout=2.0)

    assert runtime.operations == [
        "generate-start",
        "generate-end",
        "prepare",
        "probe",
    ]


def test_stale_update_cannot_prepare_after_newer_request_begins():
    blocker_entered = threading.Event()
    release_blocker = threading.Event()

    class BlockingRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.prepare_calls = 0

        def generate(self, request: GenerationRequest) -> GenerationResult:
            blocker_entered.set()
            release_blocker.wait(timeout=2.0)
            return super().generate(request)

        def prepare(self, _plan: ExecutionPlan, *, consent: bool) -> bool:
            self.prepare_calls += 1
            return True

    runtime = BlockingRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    accepted = _plan(standby_model="")
    coordinator.accept_verified(accepted)
    blocker = threading.Thread(
        target=lambda: coordinator.generate_task(
            "active",
            command="Proofread",
            model=accepted.model,
        )
    )
    blocker.start()
    assert blocker_entered.wait(timeout=1.0)
    failures: list[str] = []

    def stale_prepare() -> None:
        coordinator.begin_update()
        try:
            coordinator.prepare(accepted, consent=False)
        except RuntimeError as exc:
            failures.append(str(exc))

    stale = threading.Thread(target=stale_prepare)
    stale.start()
    time.sleep(0.03)
    coordinator.begin_update()
    release_blocker.set()
    blocker.join(timeout=2.0)
    stale.join(timeout=2.0)

    assert runtime.prepare_calls == 0
    assert failures and "newer local AI request" in failures[0]


def test_listener_delivery_cannot_apply_an_older_lease_after_a_newer_one():
    runtime = _Runtime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    older = _plan(standby_model="")
    newer = _plan(
        pack_id="gemma4-e2b-qat-v1",
        model="gemma4:e2b-it-qat",
        standby_model="",
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    delivered: list[str] = []

    def listener(lease) -> None:
        delivered.append(lease.plan.model if lease is not None else "cleared")
        if len(delivered) == 1:
            first_entered.set()
            release_first.wait(timeout=2.0)

    coordinator.add_listener(listener)
    first = threading.Thread(target=lambda: coordinator.accept_verified(older))
    second = threading.Thread(target=lambda: coordinator.accept_verified(newer))
    first.start()
    assert first_entered.wait(timeout=1.0)
    second.start()
    time.sleep(0.05)
    assert delivered == [older.model]
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert delivered == [older.model, newer.model]
    lease = coordinator.lease()
    assert lease is not None and lease.plan == newer


def test_preload_warms_an_unloaded_accepted_runtime_once():
    class ColdRuntime(_Runtime):
        def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus:
            self.status_calls += 1
            return RuntimeStatus(
                model=model,
                loaded=False,
                backend=RuntimeBackend.CUDA,
            )

    runtime = ColdRuntime()
    coordinator = AcceptedInferenceCoordinator(runtime)
    accepted = _plan(standby_model="")
    coordinator.accept_verified(accepted)

    assert coordinator.preload(model=accepted.model, timeout=30.0)
    assert runtime.status_calls == 1
    assert len(runtime.calls) == 1
    assert runtime.calls[0].text == "Warm start."
