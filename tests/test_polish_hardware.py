from __future__ import annotations

from dataclasses import replace

import pytest

from echo_link_core.polish_hardware import (
    BenchmarkRecord,
    HardwarePolicyManager,
    POLISH_POLICY_VERSION,
    PolishBenchmarkCache,
)
from echo_link_core.polish_types import (
    ClassifierDecision,
    ContextScope,
    ExecutionPlan,
    HardwareSnapshot,
    PolishMode,
    PolishModelPack,
    PolishTask,
    RuntimeBackend,
)


GIB = 1024**3


def test_benchmark_cache_key_binds_probe_contract():
    cache = PolishBenchmarkCache(None)
    hardware = snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA)
    selected_pack = PACKS[1]
    base = ExecutionPlan(
        policy_version=POLISH_POLICY_VERSION,
        mode=PolishMode.BALANCED,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id=selected_pack.pack_id,
        model=selected_pack.model,
        backend=RuntimeBackend.CUDA,
        require_full_gpu=True,
        warm_latency_limit_seconds=5.0,
    )

    def key(plan):
        return cache.key(
            hardware,
            runtime_id="runtime:1",
            pack=selected_pack,
            plan=plan,
        )

    assert key(base) != key(
        replace(
            base,
            mode=PolishMode.PROFESSIONAL,
            warm_latency_limit_seconds=15.0,
        )
    )
    assert key(base) != key(replace(base, backend=RuntimeBackend.VULKAN))
    assert key(base) != key(replace(base, require_full_gpu=False))
    assert key(base) != key(replace(base, warm_latency_limit_seconds=4.5))


def test_quality_first_policy_invalidates_old_cold_probe_cache():
    assert POLISH_POLICY_VERSION == 6


def snapshot(
    *,
    ram: int,
    available_ram: int | None = None,
    vram: int = 0,
    available_vram: int | None = None,
    backend: RuntimeBackend = RuntimeBackend.CPU,
    healthy: bool = True,
    battery: int | None = None,
) -> HardwareSnapshot:
    backends = (backend, RuntimeBackend.CPU) if backend is not RuntimeBackend.CPU else (backend,)
    return HardwareSnapshot(
        cpu_name="Test CPU",
        cpu_threads=16,
        cpu_features=("avx2",),
        total_ram_bytes=ram * GIB,
        available_ram_bytes=(available_ram if available_ram is not None else ram - 2) * GIB,
        gpu_name="Test GPU" if vram else "",
        gpu_vendor="NVIDIA" if backend is RuntimeBackend.CUDA else "",
        dedicated_vram_bytes=vram * GIB,
        available_vram_bytes=(available_vram if available_vram is not None else vram) * GIB,
        driver_version="1",
        driver_healthy=healthy and bool(vram),
        on_battery=battery is not None,
        battery_percent=battery,
        supported_backends=backends,
        runtime_version="test",
    )


def pack(
    pack_id: str,
    *,
    size_gb: float,
    ram_gb: int,
    vram_gb: int,
    cpu: bool,
    installed: bool = False,
) -> PolishModelPack:
    backends = [RuntimeBackend.CUDA]
    if cpu:
        backends.append(RuntimeBackend.CPU)
    return PolishModelPack(
        pack_id=pack_id,
        display_name=pack_id,
        model=pack_id,
        family="Gemma 4",
        parameters="test",
        quantization="Q4_K_M",
        runtime="ollama",
        manifest_digest="a" * 64,
        size_bytes=int(size_gb * GIB),
        min_ram_bytes=ram_gb * GIB,
        min_vram_bytes=vram_gb * GIB,
        max_context_tokens=4096,
        supported_backends=tuple(backends),
        license_id="Apache-2.0",
        source_url="https://example.test",
        download_ref="ollama://test",
        installed=installed,
        validated=True,
    )


PACKS = (
    pack("gemma4-e2b-qat-v1", size_gb=4.0, ram_gb=8, vram_gb=6, cpu=True),
    pack("gemma4-e4b-qat-v1", size_gb=5.7, ram_gb=16, vram_gb=8, cpu=True),
    pack(
        "gemma4-12b-q4-k-m-v1",
        size_gb=7.1,
        ram_gb=32,
        vram_gb=12,
        cpu=False,
        installed=True,
    ),
)


def decision(*, severity: float = 0.9, needs_llm: bool = True) -> ClassifierDecision:
    return ClassifierDecision(
        needs_llm=needs_llm,
        error_categories=("agreement",),
        severity=severity,
        context_scope=ContextScope.SENTENCE,
        english_confidence=1.0,
        backend="test",
        elapsed_ms=1.0,
    )


@pytest.mark.parametrize(
    ("hardware", "expected", "backend"),
    (
        (snapshot(ram=6), "", RuntimeBackend.RULES),
        (snapshot(ram=10), "gemma4-e2b-qat-v1", RuntimeBackend.CPU),
        (snapshot(ram=16), "gemma4-e4b-qat-v1", RuntimeBackend.CPU),
        (
            snapshot(ram=16, vram=6, available_vram=6, backend=RuntimeBackend.CUDA),
            "gemma4-e2b-qat-v1",
            RuntimeBackend.CUDA,
        ),
        (
            snapshot(ram=32, vram=8, available_vram=8, backend=RuntimeBackend.CUDA),
            "gemma4-e4b-qat-v1",
            RuntimeBackend.CUDA,
        ),
        (
            snapshot(ram=32, vram=12, available_vram=12, backend=RuntimeBackend.CUDA),
            "gemma4-e4b-qat-v1",
            RuntimeBackend.CUDA,
        ),
        (
            snapshot(ram=64, vram=16, available_vram=16, backend=RuntimeBackend.CUDA),
            "gemma4-e4b-qat-v1",
            RuntimeBackend.CUDA,
        ),
    ),
)
def test_policy_hardware_matrix(hardware, expected, backend):
    plan = HardwarePolicyManager().plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == expected
    assert plan.backend is backend


def test_instant_uses_rules_for_nonsevere_errors():
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.INSTANT,
        classifier=decision(severity=0.7),
        packs=PACKS,
    )
    assert not plan.use_llm
    assert "Instant" in plan.reason


def test_manual_instant_proofread_still_uses_gemma_and_has_cold_load_budget():
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.INSTANT,
        classifier=decision(severity=0.1, needs_llm=False),
        packs=PACKS,
        manual=True,
    )
    assert plan.use_llm
    assert plan.cold_load_timeout_seconds == 90.0
    assert plan.absolute_timeout_seconds == 30.0


def test_instant_allows_severe_sentence_ai_without_retry():
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.INSTANT,
        classifier=decision(severity=0.95),
        packs=PACKS,
    )
    assert plan.use_llm
    assert plan.pack_id == "gemma4-e2b-qat-v1"
    assert plan.retry_limit == 0
    assert plan.warm_latency_limit_seconds == 2.0


def test_balanced_and_professional_have_one_retry_and_bounded_context():
    manager = HardwarePolicyManager()
    hardware = snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA)
    balanced = manager.plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    professional = manager.plan(
        hardware,
        PolishTask.DOCUMENT,
        PolishMode.PROFESSIONAL,
        classifier=replace(decision(), context_scope=ContextScope.DOCUMENT),
        packs=PACKS,
    )
    assert balanced.retry_limit == 1
    assert balanced.pack_id == "gemma4-e4b-qat-v1"
    assert "gemma" not in balanced.reason.casefold()
    assert balanced.context_window <= 4096
    assert balanced.warm_latency_limit_seconds == 5.0
    assert professional.retry_limit == 1
    assert professional.pack_id == "gemma4-12b-q4-k-m-v1"
    assert professional.context_window == 4096
    assert professional.warm_latency_limit_seconds == 15.0


def test_policy_ignores_non_gemma_pack_even_when_it_is_ranked_first_by_size():
    other_family = replace(
        pack("other-32b-q4", size_gb=2.0, ram_gb=8, vram_gb=1, cpu=True),
        family="Other",
    )
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=(other_family, *PACKS),
    )

    assert plan.pack_id == "gemma4-e4b-qat-v1"


def test_policy_ignores_unapproved_gemma_pack_id():
    unapproved = pack(
        "gemma4-31b-q4",
        size_gb=2.0,
        ram_gb=8,
        vram_gb=1,
        cpu=True,
    )
    plan = HardwarePolicyManager().plan(
        snapshot(ram=16),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=(unapproved, *PACKS),
    )

    assert plan.pack_id == "gemma4-e4b-qat-v1"


def test_manual_balanced_hud_sections_receive_full_short_paragraph_context():
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        manual=True,
    )

    assert plan.context_window == 4096


def test_auto_can_reuse_an_already_installed_12b_pack_when_it_is_the_only_pack():
    installed = PACKS[2]

    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(),
        packs=(installed,),
        manual=True,
    )

    assert plan.pack_id == installed.pack_id
    assert plan.model == installed.model
    assert plan.backend is RuntimeBackend.CUDA
    assert plan.requires_probe


def test_manual_correction_rejects_installed_pack_after_latency_failure():
    installed_gemma = pack(
        "gemma4-12b-q4-k-m-v1",
        size_gb=7.1,
        ram_gb=32,
        vram_gb=12,
        cpu=False,
        installed=True,
    )
    slow_but_correct = BenchmarkRecord(
        accepted=False,
        correctness_passed=True,
        protected_passed=True,
        elapsed_seconds=6.3,
        backend="cuda",
        reason="warm latency exceeded 5 seconds",
        measured_at="2026-07-29T00:00:00+00:00",
    )

    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=(installed_gemma,),
        benchmark_records={installed_gemma.pack_id: slow_but_correct},
        manual=True,
    )

    assert not plan.use_llm
    assert plan.pack_id == ""
    assert plan.backend is RuntimeBackend.RULES
    assert "warm latency" in plan.reason.casefold()


def test_failed_large_gemma_probe_downgrades_to_next_safe_gemma_pack():
    failed = BenchmarkRecord(
        accepted=False,
        correctness_passed=True,
        protected_passed=True,
        elapsed_seconds=12.5,
        backend="cuda",
        reason="warm latency exceeded limit",
        measured_at="2026-08-04T00:00:00+00:00",
    )
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
        benchmark_records={"gemma4-12b-q4-k-m-v1": failed},
        manual=True,
    )

    assert plan.pack_id == "gemma4-e4b-qat-v1"
    assert plan.backend is RuntimeBackend.CUDA
    assert "warm latency" in plan.downgrade_reason.casefold()
    assert "gemma" not in plan.downgrade_reason.casefold()


def test_policy_selects_fastest_pack_after_both_pass_the_same_gate():
    measured_at = "2026-08-04T00:00:00+00:00"
    records = {
        "gemma4-e4b-qat-v1": BenchmarkRecord(
            accepted=True,
            correctness_passed=True,
            protected_passed=True,
            elapsed_seconds=4.8,
            backend="cuda",
            reason="",
            measured_at=measured_at,
        ),
        "gemma4-e2b-qat-v1": BenchmarkRecord(
            accepted=True,
            correctness_passed=True,
            protected_passed=True,
            elapsed_seconds=1.1,
            backend="cuda",
            reason="",
            measured_at=measured_at,
        ),
    }

    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        benchmark_records=records,
        manual=True,
    )

    assert plan.pack_id == "gemma4-e2b-qat-v1"
    assert plan.backend is RuntimeBackend.CUDA
    assert not plan.requires_probe


def test_policy_does_not_reuse_a_measurement_from_another_backend():
    record = BenchmarkRecord(
        accepted=True,
        correctness_passed=True,
        protected_passed=True,
        elapsed_seconds=1.0,
        backend="cpu",
        reason="",
        measured_at="2026-08-04T00:00:00+00:00",
    )

    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        benchmark_records={"gemma4-e2b-qat-v1": record},
        manual=True,
    )

    assert plan.pack_id == "gemma4-e4b-qat-v1"
    assert plan.requires_probe


def test_driver_failure_downgrades_to_cpu_pack():
    plan = HardwarePolicyManager().plan(
        snapshot(
            ram=64,
            vram=16,
            backend=RuntimeBackend.CUDA,
            healthy=False,
        ),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == "gemma4-e4b-qat-v1"
    assert plan.backend is RuntimeBackend.CPU


def test_low_ram_pressure_and_low_battery_pause_ai():
    manager = HardwarePolicyManager()
    low_ram = manager.plan(
        snapshot(ram=64, available_ram=3, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    battery = manager.plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA, battery=15),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    assert not low_ram.use_llm
    assert "RAM" in low_ram.reason
    assert not battery.use_llm
    assert "Battery" in battery.reason


def test_recovery_restores_largest_safe_pack():
    manager = HardwarePolicyManager()
    pressured = manager.plan(
        snapshot(ram=64, available_ram=3, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )
    recovered = manager.plan(
        snapshot(ram=64, available_ram=50, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
        preferred_pack_id="gemma4-e2b-qat-v1",
    )
    assert not pressured.use_llm
    assert recovered.pack_id == "gemma4-12b-q4-k-m-v1"


def test_vram_pressure_downgrades_and_recovery_restores_largest_pack():
    manager = HardwarePolicyManager()
    pressured = manager.plan(
        snapshot(
            ram=64,
            available_ram=50,
            vram=16,
            available_vram=8,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )
    recovered = manager.plan(
        snapshot(
            ram=64,
            available_ram=50,
            vram=16,
            available_vram=16,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )
    assert pressured.pack_id == "gemma4-e2b-qat-v1"
    assert pressured.backend is RuntimeBackend.CUDA
    assert recovered.pack_id == "gemma4-12b-q4-k-m-v1"


def test_available_ram_pressure_selects_a_smaller_gemma_pack():
    plan = HardwarePolicyManager().plan(
        snapshot(
            ram=64,
            available_ram=6,
            vram=16,
            available_vram=16,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )

    assert plan.pack_id == "gemma4-e4b-qat-v1"
    assert plan.backend is RuntimeBackend.CUDA


def test_loaded_full_gpu_pack_is_not_mistaken_for_vram_pressure():
    manager = HardwarePolicyManager()
    hardware = replace(
        snapshot(
            ram=64,
            available_ram=50,
            vram=16,
            available_vram=7,
            backend=RuntimeBackend.CUDA,
        ),
        loaded_models=("gemma4-12b-q4-k-m-v1",),
        loaded_model_vram=(("gemma4-12b-q4-k-m-v1", 8 * GIB),),
    )
    plan = manager.plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"
    assert plan.backend is RuntimeBackend.CUDA


def test_loaded_different_gpu_pack_is_reclaimable_for_fast_pack():
    manager = HardwarePolicyManager()
    hardware = replace(
        snapshot(
            ram=64,
            available_ram=50,
            vram=16,
            available_vram=2,
            backend=RuntimeBackend.CUDA,
        ),
        loaded_models=("gemma4:31b",),
        loaded_model_vram=(("gemma4:31b", 12 * GIB),),
    )
    plan = manager.plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"
    assert plan.backend is RuntimeBackend.CUDA


def test_standby_model_never_uses_a_non_gemma_pack():
    other_family = replace(
        pack(
            "other-1.7b-q4",
            size_gb=1.0,
            ram_gb=8,
            vram_gb=1,
            cpu=True,
            installed=True,
        ),
        family="Other",
    )
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=(*PACKS, other_family),
    )

    assert plan.standby_model != other_family.model


def test_gpu_plan_does_not_offer_a_cpu_spill_standby():
    installed_packs = tuple(replace(value, installed=True) for value in PACKS)
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=12, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.PROFESSIONAL,
        classifier=decision(),
        packs=installed_packs,
    )

    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"
    assert plan.require_full_gpu
    assert plan.standby_model == ""


def test_small_healthy_gpu_allows_explicit_cpu_only_gemma_pack():
    cpu_capable = PACKS[0]
    assert RuntimeBackend.CUDA in cpu_capable.supported_backends
    assert RuntimeBackend.CPU in cpu_capable.supported_backends
    plan = HardwarePolicyManager().plan(
        snapshot(
            ram=32,
            available_ram=24,
            vram=4,
            available_vram=4,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(),
        packs=(cpu_capable,),
    )

    assert plan.pack_id == cpu_capable.pack_id
    assert plan.backend is RuntimeBackend.CPU
    assert not plan.require_full_gpu


def test_ram_and_vram_budgets_follow_policy_caps():
    plan = HardwarePolicyManager().plan(
        snapshot(
            ram=64,
            available_ram=20,
            vram=16,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.memory_budget_bytes == int(20 * GIB * 0.70)
    assert plan.vram_reserve_bytes == 3 * GIB
    assert plan.require_full_gpu


def test_vram_held_by_our_own_loaded_model_does_not_disable_the_local_ai() -> None:
    """The app filled the card with its own models, then switched itself off.

    Every pack the fallback cascade tries stays resident afterwards, so a few
    refused corrections were enough to leave almost no free VRAM. `_resource_reason`
    read that as a full card and dropped to rules - permanently, because
    nothing unloads while the plan says rules. Measured on a 16 GB card:
    essays 1 to 3 corrected normally, then every paragraph after came back
    rules-only with "Available VRAM is low", and a fresh engine on the same
    text was fine.

    `_select_backend` already counted loaded model VRAM as reclaimable, since
    Ollama unloads one model before loading the next. The two checks disagreed.
    """
    from dataclasses import replace as _replace

    from echo_link_core.polish_hardware import HardwarePolicyManager

    full_card = snapshot(
        ram=64,
        vram=16,
        available_vram=0,
        backend=RuntimeBackend.CUDA,
    )
    # Nothing loaded: the card really is full and rules are correct.
    assert "VRAM" in HardwarePolicyManager._resource_reason(full_card)

    # The same card, with our own models accounting for the space.
    ours = _replace(
        full_card,
        loaded_model_vram=(("gemma4:e4b-it-qat", 3 * GIB), ("gemma4:12b", 8 * GIB)),
    )
    assert HardwarePolicyManager._resource_reason(ours) == ""


def test_an_unanswered_runtime_query_is_not_read_as_an_empty_card() -> None:
    """A timeout means "not known", not "nothing is loaded".

    The loaded-model query ran with a half-second ceiling and was asked while
    the server was generating, which is when it is least likely to answer. A
    lost answer zeroed the reclaimable VRAM and sent the policy to rules at the
    exact moment the app was busiest and the card was fullest.
    """
    from echo_link_core import polish_hardware

    polish_hardware._LOADED_MODEL_CACHE["at"] = polish_hardware.time.time()
    polish_hardware._LOADED_MODEL_CACHE["value"] = (
        ("gemma4:e2b-it-qat",),
        (("gemma4:e2b-it-qat", 2 * GIB),),
    )
    assert polish_hardware._last_loaded_models()[1] == (("gemma4:e2b-it-qat", 2 * GIB),)

    # Stale readings are dropped rather than kept forever.
    polish_hardware._LOADED_MODEL_CACHE["at"] = (
        polish_hardware.time.time() - polish_hardware._LOADED_MODEL_CACHE_SECONDS - 1.0
    )
    assert polish_hardware._last_loaded_models() == ((), ())
