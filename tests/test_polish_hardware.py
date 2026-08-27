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
        # From 2026-08-25 a card with room for a 12B gets one. Curtis chose
        # biggest model first, then highest precision, so these two moved off
        # the 4B pack they used to take. The 6 GiB and 8 GiB cards above did
        # NOT move, which is the half of this that matters: the ladder still
        # adjusts to the machine rather than handing every system the largest
        # pack that exists.
        (
            snapshot(ram=32, vram=12, available_vram=12, backend=RuntimeBackend.CUDA),
            "gemma4-12b-q4-k-m-v1",
            RuntimeBackend.CUDA,
        ),
        (
            snapshot(ram=64, vram=16, available_vram=16, backend=RuntimeBackend.CUDA),
            "gemma4-12b-q4-k-m-v1",
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
    # BALANCED takes the 12B from 2026-08-25 - biggest model first. The
    # subject of this test is the retry limit and the context bound, which
    # did not change.
    assert balanced.pack_id == "gemma4-12b-q4-k-m-v1"
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

    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"


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


def test_policy_prefers_the_higher_ranked_pack_after_both_pass_the_same_gate():
    """Rank decides once both packs have passed; speed no longer overrides it.

    This asserted the opposite until 2026-08-26. Selection among packs with
    passing records was min() on elapsed seconds alone - "prefer its measured
    speed instead of assuming the larger pack is faster". Sound reasoning,
    opposite result: the smallest pack is always the fastest, so once every
    pack had been measured the ladder never applied.

    Measured that day: a run held gemma4-12b-qat-v1 with a passing CUDA record
    and still planned gemma4-e2b 234 times. Every tier on this machine had been
    collapsing the same way, which is why the same three correction numbers
    came back run after run - they were always the 2B pack.

    Curtis chose rank first, speed as tiebreak only.
    """
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

    # e4b outranks e2b in the ladder, and is 4.8s against 1.1s. Rank wins.
    assert plan.pack_id == "gemma4-e4b-qat-v1"
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

    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"
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


def test_a_manual_section_always_gets_a_retry_before_the_rules_take_over():
    """A refusal spends the section, so one attempt is too cheap.

    When the meaning guards refuse an answer the section falls back to the
    rules repair, and for grammar the rules are not a weaker correction, they
    are no correction: "duck are funny pets" comes back unchanged from every
    deterministic pass in the product.

    Measured on 2026-08-20 across one session, 36 sections fell back to rules
    against 424 AI completions - about 8.5 percent - and the reasons were modal
    meaning, actor changed, protected content and prohibition. Those are drift
    the retry prompt anchors, because it feeds the actor and negation
    inventories back to the model on the second attempt.

    AUTO with a low severity used to get no retry at all, which is the case
    this fixes.
    """
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.AUTO,
        classifier=decision(severity=0.1),
        packs=PACKS,
        manual=True,
    )

    assert plan.retry_limit >= 1


def test_the_retry_floor_does_not_apply_to_instant():
    """Instant is a latency promise, and a second generation would break it."""
    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.INSTANT,
        classifier=decision(severity=0.1, needs_llm=False),
        packs=PACKS,
        manual=True,
    )

    assert plan.retry_limit == 0


def test_a_measurement_can_pin_a_pack_without_changing_the_default(monkeypatch):
    """Two models can only be compared if both can be made to run.

    The app cannot be told which pack to use - engine.py clears
    polish_model_pack_id on every start on purpose - so the pack comes from
    this policy. Without an override the only way to measure a candidate is to
    promote it, which makes the comparison and the shipping decision the same
    act and moves the default before any number justifies it.

    Being in the mode order makes a pack selectable. Being FIRST makes it the
    default. This pins both halves.
    """
    from echo_link_core.polish_hardware import _MODE_PACK_ORDER, _measurement_pack_order
    from echo_link_core.polish_types import PolishMode

    order = _MODE_PACK_ORDER[PolishMode.BALANCED]
    default_first = order[0]
    pinned_id = "gemma4-e2b-qat-v1"
    assert pinned_id in order and pinned_id != default_first

    monkeypatch.delenv("MANUSCRIPT_PREFERRED_PACK", raising=False)
    assert _measurement_pack_order(order)[0] == default_first

    monkeypatch.setenv("MANUSCRIPT_PREFERRED_PACK", pinned_id)
    pinned = _measurement_pack_order(order)
    assert pinned[0] == pinned_id
    # Pinning reorders; it must never drop a pack, or a pinned run would lose
    # the fallbacks the unpinned run had.
    assert sorted(pinned) == sorted(order)

    # A typo in a measurement command must not become "the product refused to
    # start" - it falls back to the real default.
    monkeypatch.setenv("MANUSCRIPT_PREFERRED_PACK", "no-such-pack-v9")
    assert _measurement_pack_order(order)[0] == default_first


def test_a_measurement_can_pin_the_tier_as_well_as_the_pack(monkeypatch):
    """Comparing packs needs the tier, because the tier sets the speed limit.

    The warm-latency limit is per mode - 2s Instant, 5s Auto and Balanced, 15s
    Professional - and it decides which packs survive their acceptance probe.
    Measured 2026-08-25: qwen3:14b takes 5.23s, is refused by the 5s
    interactive limit, and two pinned acceptance tiers therefore measured
    Gemma while reporting the result as Qwen's.

    Setting it per process means a comparison never has to write to the
    writer's saved settings and remember to put them back.
    """
    from echo_link_core.polish_hardware import _measurement_mode
    from echo_link_core.polish_types import PolishMode

    monkeypatch.delenv("MANUSCRIPT_PREFERRED_MODE", raising=False)
    assert _measurement_mode(PolishMode.BALANCED) is PolishMode.BALANCED

    monkeypatch.setenv("MANUSCRIPT_PREFERRED_MODE", "professional")
    assert _measurement_mode(PolishMode.BALANCED) is PolishMode.PROFESSIONAL

    # A typo must not become "the product refused to start".
    monkeypatch.setenv("MANUSCRIPT_PREFERRED_MODE", "not-a-mode")
    assert _measurement_mode(PolishMode.BALANCED) is PolishMode.BALANCED


def test_a_held_pack_keeps_its_place_while_the_card_is_momentarily_busy():
    """The pack a session accepted must survive a busy moment.

    Eligibility is re-evaluated on every request, and the free-VRAM term moves
    second to second while the product works. Measured 2026-08-26: a full tier
    prewarmed gemma4-12b-qat and then planned gemma4-e2b 216 times with
    reason=- - the policy choosing the smallest pack outright. The 12B asks
    for 10 GiB and idle usable VRAM was 11.92-12.67 GiB, so one busy moment
    erased the margin and it never climbed back. Every tier collapsed the same
    way, which is why the same three correction numbers came back run after
    run: they were always the 2B pack.
    """
    busy = snapshot(
        ram=64,
        vram=16,
        available_vram=2,
        backend=RuntimeBackend.CUDA,
    )

    # Nothing held: the busy card pushes the policy down the ladder.
    unheld = HardwarePolicyManager().plan(
        busy,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
    )

    # The 12B held from earlier in the session keeps its place.
    held = HardwarePolicyManager().plan(
        busy,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        preferred_pack_id="gemma4-12b-q4-k-m-v1",
    )

    assert held.pack_id == "gemma4-12b-q4-k-m-v1"
    assert held.pack_id != unheld.pack_id


def test_holding_a_pack_cannot_put_it_on_a_card_that_is_too_small():
    """A held pack still has to fit the machine.

    The exemption covers the free-VRAM term only. The card's size is a fact
    about the hardware, so a small card never reaches a big pack however long
    the session has been running - otherwise this would hand a 6 GiB laptop a
    12B and call it holding.
    """
    small = snapshot(
        ram=64,
        vram=6,
        available_vram=6,
        backend=RuntimeBackend.CUDA,
    )

    plan = HardwarePolicyManager().plan(
        small,
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        preferred_pack_id="gemma4-12b-q4-k-m-v1",
    )

    assert plan.pack_id != "gemma4-12b-q4-k-m-v1"


def test_ladder_rank_beats_a_faster_smaller_pack():
    """The biggest pack a machine can hold wins, even when a smaller one is quicker.

    Selection among packs with passing benchmark records used to be min() on
    elapsed seconds alone - "prefer its measured speed instead of assuming the
    larger pack is faster". Sound reasoning, opposite result: the smallest pack
    is always the fastest, so once every pack had been measured the ladder
    never applied and the smallest won every time.

    Measured 2026-08-26: a run held gemma4-12b-qat-v1 with a passing CUDA
    record and still planned gemma4-e2b 234 times. Every tier on this machine
    collapsed the same way, which is why the same three correction numbers came
    back run after run - they were always the 2B pack.

    Curtis chose rank first, speed as tiebreak only.
    """
    def record(elapsed: float) -> BenchmarkRecord:
        return BenchmarkRecord(
            accepted=True,
            correctness_passed=True,
            protected_passed=True,
            elapsed_seconds=elapsed,
            backend="cuda",
            reason="",
            measured_at="2026-08-26T00:00:00+00:00",
        )

    plan = HardwarePolicyManager().plan(
        snapshot(ram=64, vram=16, available_vram=16, backend=RuntimeBackend.CUDA),
        PolishTask.PROOFREAD,
        PolishMode.BALANCED,
        classifier=decision(),
        packs=PACKS,
        benchmark_records={
            "gemma4-e2b-qat-v1": record(0.5),
            "gemma4-12b-q4-k-m-v1": record(9.0),
        },
    )

    assert plan.pack_id == "gemma4-12b-q4-k-m-v1"


def test_speed_still_separates_packs_of_the_same_rank():
    """Rank first does not mean speed stops mattering.

    Two packs at the same place in the ladder are separated by their measured
    time, which is the part of the old rule worth keeping.
    """
    from echo_link_core.polish_hardware import _MODE_PACK_ORDER

    order = _MODE_PACK_ORDER[PolishMode.BALANCED]
    assert order.index("gemma4-12b-qat-v1") < order.index("gemma4-e2b-qat-v1")
