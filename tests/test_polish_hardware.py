from __future__ import annotations

from dataclasses import replace

import pytest

from echo_link_core.polish_hardware import (
    BenchmarkRecord,
    HardwarePolicyManager,
    POLISH_POLICY_VERSION,
)
from echo_link_core.polish_types import (
    ClassifierDecision,
    ContextScope,
    HardwareSnapshot,
    PolishMode,
    PolishModelPack,
    PolishTask,
    RuntimeBackend,
)


GIB = 1024**3


def test_quality_first_policy_invalidates_old_cold_probe_cache():
    assert POLISH_POLICY_VERSION == 5


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
        family="Qwen3",
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
    pack("qwen3-1.7b-q4-k-m-v1", size_gb=1.3, ram_gb=8, vram_gb=0, cpu=True),
    pack("qwen3-4b-q4-k-m-v1", size_gb=2.5, ram_gb=16, vram_gb=4, cpu=True),
    pack("qwen3-8b-q4-k-m-v1", size_gb=5.2, ram_gb=32, vram_gb=8, cpu=False),
    pack(
        "qwen3-14b-q4-k-m-v1",
        size_gb=9.0,
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
        (snapshot(ram=10), "qwen3-1.7b-q4-k-m-v1", RuntimeBackend.CPU),
        (snapshot(ram=16), "qwen3-4b-q4-k-m-v1", RuntimeBackend.CPU),
        (
            snapshot(ram=32, vram=8, available_vram=8, backend=RuntimeBackend.CUDA),
            "qwen3-8b-q4-k-m-v1",
            RuntimeBackend.CUDA,
        ),
        (
            snapshot(ram=64, vram=16, available_vram=16, backend=RuntimeBackend.CUDA),
            "qwen3-8b-q4-k-m-v1",
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


def test_manual_instant_proofread_still_uses_qwen_and_has_cold_load_budget():
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
    assert plan.retry_limit == 0
    assert plan.warm_latency_limit_seconds == 6.0


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
    assert balanced.pack_id == "qwen3-8b-q4-k-m-v1"
    assert balanced.context_window <= 4096
    assert professional.retry_limit == 1
    assert professional.pack_id == "qwen3-14b-q4-k-m-v1"
    assert professional.context_window == 4096
    assert professional.warm_latency_limit_seconds == 30.0


def test_balanced_prefers_gemma4_12b_when_gpu_fits():
    gemma = pack(
        "gemma4-12b-q4-k-m-v1",
        size_gb=7.6,
        ram_gb=32,
        vram_gb=12,
        cpu=False,
        installed=True,
    )
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=(*PACKS, gemma),
    )

    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"


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


def test_manual_correction_reuses_installed_quality_pack_after_latency_failure():
    installed_gemma = pack(
        "gemma4-12b-q4-k-m-v1",
        size_gb=7.6,
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
        PolishMode.BALANCED,
        classifier=decision(),
        packs=(installed_gemma,),
        benchmark_records={installed_gemma.pack_id: slow_but_correct},
        manual=True,
    )

    assert plan.use_llm
    assert plan.pack_id == installed_gemma.pack_id
    assert plan.backend is RuntimeBackend.CUDA


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
    assert plan.pack_id == "qwen3-4b-q4-k-m-v1"
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
        PolishMode.AUTO,
        classifier=decision(),
        packs=PACKS,
    )
    recovered = manager.plan(
        snapshot(ram=64, available_ram=50, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(),
        packs=PACKS,
        preferred_pack_id="qwen3-4b-q4-k-m-v1",
    )
    assert not pressured.use_llm
    assert recovered.pack_id == "qwen3-8b-q4-k-m-v1"


def test_vram_pressure_downgrades_and_recovery_restores_largest_pack():
    manager = HardwarePolicyManager()
    pressured = manager.plan(
        snapshot(
            ram=64,
            available_ram=50,
            vram=16,
            available_vram=7,
            backend=RuntimeBackend.CUDA,
        ),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
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
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    assert pressured.pack_id == "qwen3-4b-q4-k-m-v1"
    assert pressured.backend is RuntimeBackend.CUDA
    assert recovered.pack_id == "qwen3-8b-q4-k-m-v1"


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
        loaded_models=("qwen3-8b-q4-k-m-v1",),
        loaded_model_vram=(("qwen3-8b-q4-k-m-v1", 5 * GIB),),
    )
    plan = manager.plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == "qwen3-8b-q4-k-m-v1"
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
        loaded_models=("qwen3:14b",),
        loaded_model_vram=(("qwen3:14b", 10 * GIB),),
    )
    plan = manager.plan(
        hardware,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )
    assert plan.pack_id == "qwen3-8b-q4-k-m-v1"
    assert plan.backend is RuntimeBackend.CUDA


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
