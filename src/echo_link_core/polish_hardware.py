from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from .local_transport import LocalTransportError, LoopbackJSONTransport
from .polish_types import (
    ClassifierDecision,
    ContextScope,
    ExecutionPlan,
    HardwareSnapshot,
    PolishMode,
    PolishModelPack,
    PolishSettingsStore,
    PolishTask,
    RuntimeBackend,
)


def _pinned_pack() -> str:
    """The pack a measurement pinned, or "" when nothing is pinned."""
    return os.environ.get("MANUSCRIPT_PREFERRED_PACK", "").strip()


def _measurement_mode(mode: PolishMode) -> PolishMode:
    """Let a measurement choose the tier, without changing saved settings.

    The warm-latency limit is per mode - 2s for Instant, 5s for Auto and
    Balanced, 15s for Professional - and it decides which packs survive their
    acceptance probe. Measured 2026-08-25: qwen3:14b takes 5.23s and is
    refused by the 5s interactive limit, so two pinned acceptance tiers
    silently measured Gemma and reported the result as Qwen's.

    Comparing packs therefore needs the tier as well as the pack. This sets it
    for one process, so the comparison does not have to write to the writer's
    saved settings and remember to put them back. An unrecognised value is
    ignored rather than raising, for the same reason the pack pin ignores a
    typo: a bad measurement argument must not become "the product refused to
    start".

        $env:MANUSCRIPT_PREFERRED_MODE = "professional"
    """
    wanted = os.environ.get("MANUSCRIPT_PREFERRED_MODE", "").strip()
    if not wanted:
        return mode
    try:
        return PolishMode(wanted.casefold())
    except ValueError:
        return mode


def _measurement_pack_order(order: tuple[str, ...]) -> tuple[str, ...]:
    """Let a measurement pin one pack, without shipping it as the default.

    Two models can only be compared if both can be made to run on the same
    corpus, and the app cannot be told which to use: `engine.py` clears
    `polish_model_pack_id` on every start on purpose, so that each start
    re-accepts a model for the current hardware. The pack therefore comes from
    this policy, and the only way to measure a candidate used to be to promote
    it - which means the comparison and the shipping decision become the same
    act, and the default moves before there is a number to justify it.

    MANUSCRIPT_PREFERRED_PACK moves one pack to the front for this process
    only. It changes no default and no file. An unknown id is ignored rather
    than raising, because a typo in a measurement command must not silently
    become "the product refused to start".

        $env:MANUSCRIPT_PREFERRED_PACK = "qwen3-14b-q4-k-m-v1"
    """
    pinned = _pinned_pack()
    if not pinned or pinned not in order:
        return order
    return (pinned,) + tuple(item for item in order if item != pinned)


POLISH_POLICY_VERSION = 6
_GIB = 1024**3
# Biggest model first, then the highest precision that still fits.
#
# Curtis chose that ranking on 2026-08-25: parameter count moves correction
# quality more than quantization does, so a 12B at 4 bits outranks a 4B at 8
# bits, and among packs of the same parameter count the better quantization
# wins. QAT sits above Q4_K_M because it is quantized during training rather
# than after it; both report Q4_0 to the runtime, so only the tag tells them
# apart.
#
# Eligibility decides what a machine actually gets - a pack whose min_vram
# exceeds the card is never reached, so this list can name packs no laptop
# will ever load. Measured on this 16 GiB card: usable VRAM held 11.92-12.67
# GiB through a live Word run, so it lands on gemma4-12b-qat-v1 and runs a
# 12B where it ran a 4B before.
#
# The Qwen packs were removed on 2026-08-25 when Curtis chose to stay with
# Gemma. They remain in the signed manifest; nothing selects them.
_PACK_ORDER = (
    "gemma4-12b-qat-v1",
    "gemma4-12b-q4-k-m-v1",
    "gemma4-e4b-qat-v1",
    "gemma4-e2b-qat-v1",
)
_PACK_IDS = frozenset(_PACK_ORDER)
_MODE_PACK_ORDER = {
    PolishMode.INSTANT: ("gemma4-e2b-qat-v1",),
    # Auto and Balanced share their pack with dictation refinement, so they
    # take the same ladder: biggest model first, then best precision, with
    # eligibility deciding what each machine actually reaches.
    PolishMode.AUTO: (
        "gemma4-12b-qat-v1",
        "gemma4-12b-q4-k-m-v1",
        "gemma4-e4b-qat-v1",
        "gemma4-e2b-qat-v1",
    ),
    PolishMode.BALANCED: (
        "gemma4-12b-qat-v1",
        "gemma4-12b-q4-k-m-v1",
        "gemma4-e4b-qat-v1",
        "gemma4-e2b-qat-v1",
    ),
    PolishMode.PROFESSIONAL: _PACK_ORDER,
}
_PACK_MIN_RAM = {
    "gemma4-e2b-qat-v1": 8 * _GIB,
    "gemma4-e4b-qat-v1": 16 * _GIB,
    # 16 GiB rather than the 32 the q4_K_M build asks for: this pack is
    # SMALLER on disk (6.66 GiB against 7.04) despite being the same 12B
    # model, so the larger host-memory floor would exclude machines that can
    # comfortably stage it.
    "gemma4-12b-qat-v1": 16 * _GIB,
    "gemma4-12b-q4-k-m-v1": 32 * _GIB,
}
_PACK_MIN_VRAM = {
    "gemma4-e2b-qat-v1": 6 * _GIB,
    "gemma4-e4b-qat-v1": 8 * _GIB,
    # 10 GiB: the blob is 6.66 GiB and the rest is context and runtime
    # overhead. A pack with no headroom loses the cold-load race and gets
    # demoted mid-document, so the floor is set above the bare blob size.
    "gemma4-12b-qat-v1": 10 * _GIB,
    "gemma4-12b-q4-k-m-v1": 12 * _GIB,
    # 18 GiB, stated honestly rather than optimistically. The model blob is
    # 15.65 GiB and a 16 GiB card has 15.99 GiB, so it does not fit once
    # context is added and it spills to CPU. Measured 2026-08-24: 1.47s per
    # warm dictation refinement against 0.33s for qwen3:14b on the same
    # machine. A policy that claimed this fits would hand a writer the slow
    # path and call it the best one.
}
# The 12B pack is intentionally GPU-only. Letting it spill to the CPU makes
# interactive correction unusably slow. E2B and E4B remain the CPU tiers.
_CPU_PACK_IDS = frozenset({"gemma4-e2b-qat-v1", "gemma4-e4b-qat-v1"})


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    accepted: bool
    correctness_passed: bool
    protected_passed: bool
    elapsed_seconds: float
    backend: str
    reason: str
    measured_at: str


class PolishBenchmarkCache:
    """Persist only hardware/model measurements. Never accepts source text."""

    META_KEY = "polish_benchmark_cache"
    MAX_RECORDS = 32
    # A failed probe is often transient: another local app was mid-generation
    # and the model could not get full GPU residency. Without an expiry, that
    # one bad moment marked the pack failed forever (the cache key has no time
    # component), and every later correction silently ran rules-only. Failed
    # records now expire so the pack is probed again; passing records are
    # stable measurements and are kept. Five minutes matches the runtime
    # keep-alive window, so recovery follows soon after the other workload
    # releases the GPU.
    FAILED_RECORD_TTL_SECONDS = 5 * 60.0

    def __init__(self, store: PolishSettingsStore | None) -> None:
        self.store = store

    def key(
        self,
        snapshot: HardwareSnapshot,
        *,
        runtime_id: str,
        pack: PolishModelPack,
        plan: ExecutionPlan,
    ) -> str:
        material = {
            "policy": POLISH_POLICY_VERSION,
            "gpu": snapshot.gpu_name,
            "driver": snapshot.driver_version,
            "cpu": snapshot.cpu_name,
            "threads": snapshot.cpu_threads,
            "ram": snapshot.total_ram_bytes,
            "runtime": runtime_id,
            "pack": pack.pack_id,
            "pack_version": pack.version,
            "digest": pack.manifest_digest,
            # A probe authorizes one exact execution contract. A slower
            # Professional pass must never authorize Balanced or Instant, and
            # a CPU or Vulkan result must never authorize a CUDA full-GPU run.
            # These fields also make every legacy key miss naturally.
            "mode": PolishMode.coerce(plan.mode).value,
            "warm_latency_limit_seconds": max(
                0.0,
                float(plan.warm_latency_limit_seconds),
            ),
            "backend": plan.backend.value,
            "require_full_gpu": bool(plan.require_full_gpu),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str) -> BenchmarkRecord | None:
        if self.store is None:
            return None
        raw = self.store.get_meta(self.META_KEY, {})
        if not isinstance(raw, dict):
            return None
        value = raw.get(key)
        if not isinstance(value, dict):
            return None
        try:
            record = BenchmarkRecord(
                accepted=bool(value["accepted"]),
                correctness_passed=bool(value["correctness_passed"]),
                protected_passed=bool(value["protected_passed"]),
                elapsed_seconds=max(0.0, float(value["elapsed_seconds"])),
                backend=str(value["backend"]),
                reason=str(value.get("reason") or ""),
                measured_at=str(value["measured_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not record.accepted and self._expired(record.measured_at):
            return None
        return record

    @classmethod
    def _expired(cls, measured_at: str) -> bool:
        try:
            measured = datetime.fromisoformat(measured_at)
        except ValueError:
            return True
        if measured.tzinfo is None:
            measured = measured.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - measured).total_seconds()
        return age >= cls.FAILED_RECORD_TTL_SECONDS

    def put(self, key: str, record: BenchmarkRecord) -> None:
        if self.store is None:
            return
        raw = self.store.get_meta(self.META_KEY, {})
        values = dict(raw) if isinstance(raw, dict) else {}
        # A record contains measurements and identifiers only.
        values[key] = asdict(record)
        if len(values) > self.MAX_RECORDS:
            ordered = sorted(
                values.items(),
                key=lambda item: str(item[1].get("measured_at") or ""),
                reverse=True,
            )
            values = dict(ordered[: self.MAX_RECORDS])
        self.store.set_meta(self.META_KEY, values)


class HardwarePolicyManager:
    """Choose the fastest suitable, available, locally validated model pack."""

    policy_version = POLISH_POLICY_VERSION

    def plan(
        self,
        snapshot: HardwareSnapshot,
        task: PolishTask,
        mode: PolishMode,
        *,
        classifier: ClassifierDecision | None = None,
        packs: Iterable[PolishModelPack] = (),
        preferred_pack_id: str = "",
        benchmark_records: Mapping[str, BenchmarkRecord] | None = None,
        manual: bool = False,
    ) -> ExecutionPlan:
        mode = _measurement_mode(PolishMode.coerce(mode))
        classifier = classifier or ClassifierDecision(
            needs_llm=task is not PolishTask.PROOFREAD,
            error_categories=(),
            severity=1.0 if task is not PolishTask.PROOFREAD else 0.0,
            context_scope=ContextScope.SENTENCE,
            english_confidence=1.0,
            backend="policy-default",
            elapsed_ms=0.0,
        )
        rules_reason = self._rules_reason(
            snapshot,
            task,
            mode,
            classifier,
            manual=manual,
        )
        if rules_reason:
            return self._rules_plan(mode, task, rules_reason)

        candidates = self._eligible_packs(
            snapshot,
            packs,
            preferred_pack_id=preferred_pack_id,
            mode=mode,
        )
        if not candidates:
            reason = self._resource_reason(snapshot)
            return self._rules_plan(mode, task, reason or "No validated model pack fits.")

        records = benchmark_records or {}
        failed_reasons: list[str] = []
        passing: list[tuple[float, int, PolishModelPack]] = []
        unmeasured: list[PolishModelPack] = []
        for pack in candidates:
            record = records.get(pack.pack_id)
            if record is not None and not record.accepted:
                failed_reasons.append(
                    "A larger local AI pack failed: "
                    f"{record.reason or 'local probe failed'}"
                )
                continue
            backend = self._select_backend(
                snapshot, pack, held=pack.pack_id == preferred_pack_id
            )
            if record is not None and record.backend.casefold() == backend.value:
                passing.append(
                    (max(0.0, record.elapsed_seconds), len(passing), pack)
                )
            else:
                unmeasured.append(pack)
        selected: PolishModelPack | None
        selected_probe: bool
        if passing:
            # Every accepted record passed the same mode-specific correctness,
            # preservation, residency, and latency gates. Prefer its measured
            # speed instead of assuming the larger pack is faster.
            selected = min(passing, key=lambda value: (value[0], value[1]))[2]
            selected_probe = False
        elif unmeasured:
            selected = unmeasured[0]
            selected_probe = True
        else:
            selected = None
            selected_probe = False
        if selected is None:
            reason = failed_reasons[0] if failed_reasons else "Every local model probe failed."
            return self._rules_plan(mode, task, reason)

        # Same held exemption as eligibility used. Without it the held pack is
        # admitted by _eligible_packs and then rejected here with "no supported
        # runtime", which is how the first version of this change turned a busy
        # card into a rules-only plan.
        backend = self._select_backend(
            snapshot, selected, held=selected.pack_id == preferred_pack_id
        )
        if backend is RuntimeBackend.RULES:
            return self._rules_plan(mode, task, "No supported runtime can run the selected pack.")

        reserve = _vram_reserve(snapshot.dedicated_vram_bytes)
        memory_budget = min(
            max(0, snapshot.total_ram_bytes // 2),
            max(0, int(snapshot.available_ram_bytes * 0.70)),
        )
        scope = classifier.context_scope
        context_window, max_tokens = _request_budget(mode, task, scope, selected)
        if manual and mode is not PolishMode.INSTANT:
            context_window = min(4096, selected.max_context_tokens)
        latency_limit = _latency_limit(mode)
        retry_limit = 1 if mode in {PolishMode.BALANCED, PolishMode.PROFESSIONAL} else 0
        if mode is PolishMode.AUTO:
            retry_limit = 1 if classifier.severity >= 0.75 else 0
        # A section a writer asked for by hand gets at least one retry, whatever
        # the mode decided.
        #
        # When the meaning guards refuse an answer the section falls back to the
        # rules repair, and for grammar the rules are not a weaker correction,
        # they are no correction: "duck are funny pets" comes back unchanged
        # from every deterministic pass in the product. So a refusal spends the
        # section, and spending it on a single attempt is too cheap.
        #
        # Measured on 2026-08-20 across one working session: 36 sections fell
        # back to rules against 424 AI completions, about 8.5 percent, and 25
        # of those were plain Proofreads. The reasons were modal meaning (9),
        # actor changed (7), protected content (7) and prohibition (6) - drift
        # the retry prompt is built to anchor, since it feeds the actor and
        # negation inventories back to the model on the second attempt.
        #
        # This costs nothing on the sections that already work: the attempt
        # loop breaks as soon as validation accepts, so only a section that was
        # about to be refused ever spends the extra generation.
        #
        # Deliberately not a cascade to a smaller pack, which is what was first
        # proposed. The refusals here are meaning drift, and a smaller model
        # drifts more, not less.
        if manual and mode is not PolishMode.INSTANT:
            retry_limit = max(retry_limit, 1)
        require_full_gpu = backend in {
            RuntimeBackend.CUDA,
            RuntimeBackend.VULKAN,
            RuntimeBackend.HIP,
            RuntimeBackend.SYCL,
        }
        standby = _standby_model(packs, selected, snapshot, reserve)
        return ExecutionPlan(
            policy_version=self.policy_version,
            mode=mode,
            task=task,
            use_llm=True,
            pack_id=selected.pack_id,
            model=selected.model,
            backend=backend,
            context_window=context_window,
            max_output_tokens=max_tokens,
            memory_budget_bytes=memory_budget,
            vram_reserve_bytes=reserve,
            require_full_gpu=require_full_gpu,
            retry_limit=retry_limit,
            warm_latency_limit_seconds=latency_limit,
            absolute_timeout_seconds=30.0,
            cold_load_timeout_seconds=90.0,
            requires_probe=selected_probe,
            standby_model=standby,
            reason=(
                f"Local AI selected for {mode.value} "
                f"{scope.value} correction."
            ),
            downgrade_reason=failed_reasons[0] if failed_reasons else "",
        )

    def _rules_reason(
        self,
        snapshot: HardwareSnapshot,
        task: PolishTask,
        mode: PolishMode,
        classifier: ClassifierDecision,
        *,
        manual: bool = False,
    ) -> str:
        if classifier.english_confidence < 0.72 and not manual:
            return "English correction confidence is too low."
        if snapshot.total_ram_bytes < 8 * _GIB:
            return "Less than 8 GB RAM is available for local AI."
        if snapshot.available_ram_bytes < 4 * _GIB:
            return "Available RAM is low, so local AI is paused."
        if mode is PolishMode.INSTANT and not manual:
            severe_sentence = (
                classifier.needs_llm
                and classifier.severity >= 0.85
                and classifier.context_scope is ContextScope.SENTENCE
            )
            if not severe_sentence:
                return "Instant mode kept this correction rules-only."
        elif task is PolishTask.PROOFREAD and not classifier.needs_llm and not manual:
            return "Rules resolved the detected English errors."
        if mode is PolishMode.PROFESSIONAL and task is not PolishTask.DOCUMENT:
            # Manual sentence/paragraph actions are still allowed; automatic
            # background document work is never started by this policy.
            pass
        if snapshot.on_battery and snapshot.battery_percent is not None:
            if snapshot.battery_percent <= 20:
                return "Battery is low, so local AI is paused."
        return ""

    def _eligible_packs(
        self,
        snapshot: HardwareSnapshot,
        packs: Iterable[PolishModelPack],
        *,
        preferred_pack_id: str,
        mode: PolishMode,
    ) -> list[PolishModelPack]:
        pack_values = [
            pack
            for pack in packs
            if pack.available
            and pack.pack_id in _PACK_IDS
            and snapshot.total_ram_bytes
            >= max(pack.min_ram_bytes, _PACK_MIN_RAM[pack.pack_id])
            # A fully resident GPU model still needs enough host memory to be
            # staged. Under pressure, choose the next smaller Gemma pack.
            and snapshot.available_ram_bytes >= pack.size_bytes
        ]
        mode_order = _measurement_pack_order(_MODE_PACK_ORDER[mode])
        rank = {pack_id: index for index, pack_id in enumerate(mode_order)}
        pack_values = [pack for pack in pack_values if pack.pack_id in rank]
        gpu_backends = {
            RuntimeBackend.CUDA,
            RuntimeBackend.HIP,
            RuntimeBackend.VULKAN,
            RuntimeBackend.SYCL,
        }
        pack_values.sort(
            key=lambda pack: (
                0
                if self._select_backend(
                    snapshot, pack, held=pack.pack_id == preferred_pack_id
                )
                in gpu_backends
                else 1,
                rank.get(pack.pack_id, len(rank)),
                0 if pack.pack_id == preferred_pack_id else 1,
            )
        )
        eligible: list[PolishModelPack] = []
        for pack in pack_values:
            held = bool(preferred_pack_id) and pack.pack_id == preferred_pack_id
            if self._select_backend(snapshot, pack, held=held) is RuntimeBackend.RULES:
                continue
            eligible.append(pack)
        return eligible

    @staticmethod
    def _select_backend(
        snapshot: HardwareSnapshot,
        pack: PolishModelPack,
        *,
        held: bool = False,
    ) -> RuntimeBackend:
        available = set(snapshot.supported_backends) & set(pack.supported_backends)
        reserve = _vram_reserve(snapshot.dedicated_vram_bytes)
        # Ollama can unload the currently resident model before loading the
        # selected pack. Count all model VRAM as reclaimable so a loaded larger
        # model cannot falsely force the policy down to CPU or a smaller pack.
        effective_available_vram = _reclaimable_vram(snapshot)
        hard_min_vram = _PACK_MIN_VRAM.get(pack.pack_id, pack.min_vram_bytes)
        # A pinned pack is being measured, and a pack that cannot be selected
        # cannot be measured. Curtis asked for the 27B numbers on hardware that
        # cannot hold it: qwen3.8:27b needs 18 GiB and this card has 15.99, so
        # the policy refused it and fell back to Gemma - correctly, and
        # unhelpfully for a comparison.
        #
        # The alternative was to lower the requirement in the signed manifest,
        # which would ship a claim that a 16.5 GB model fits a 14 GiB card and
        # hand every customer with a mid-size GPU a silently spilling model.
        # This bypass lives in the pin instead: one process, no default moved,
        # no false claim shipped, and the shipped requirement stays honest.
        #
        # What is measured this way is a REAL spill, not a simulation - Ollama
        # offloads what will not fit and the numbers include that cost, which
        # is the thing worth knowing.
        if _pinned_pack() == pack.pack_id:
            hard_min_vram = 0
        # A pack this session already accepted keeps its GPU place while the
        # card is momentarily busy.
        #
        # The card's SIZE still decides - a 6 GiB laptop never reaches a 12B,
        # because that is a fact about the machine. What is exempted is the
        # instantaneous free-VRAM term, the one that moves second to second
        # while the product is working.
        #
        # Measured 2026-08-26: a full tier prewarmed gemma4-12b-qat and then
        # planned gemma4-e2b 216 times with reason=- - the policy choosing the
        # smallest pack outright, not a downgrade and not a standby adoption.
        # The 12B asks for 10 GiB and idle usable VRAM was 11.92-12.67 GiB, so
        # one busy moment erased the margin and it never climbed back. Every
        # tier on this machine collapsed the same way, which is why the same
        # three numbers came back run after run - they were always the 2B.
        #
        # Ollama unloads what it holds before loading the next model, so free
        # VRAM understates what is reclaimable. _reclaimable_vram exists for
        # that reason and is still not enough on its own under sustained work.
        gpu_fits = (
            snapshot.driver_healthy
            and snapshot.dedicated_vram_bytes
            >= max(pack.min_vram_bytes, hard_min_vram)
            and (
                held
                or max(0, effective_available_vram - reserve) >= pack.size_bytes
            )
        )
        for backend in (
            RuntimeBackend.CUDA,
            RuntimeBackend.HIP,
            RuntimeBackend.VULKAN,
            RuntimeBackend.SYCL,
        ):
            if backend in available and gpu_fits:
                return backend
        # A selected GPU plan must stay fully resident. When the GPU is healthy
        # but too small, selecting the CPU backend is a separate whole-model
        # plan, not partial offload or GPU spill.
        if pack.pack_id not in _CPU_PACK_IDS:
            return RuntimeBackend.RULES
        ram_budget = min(
            snapshot.total_ram_bytes // 2,
            int(snapshot.available_ram_bytes * 0.70),
        )
        if RuntimeBackend.CPU in available and pack.size_bytes <= ram_budget:
            return RuntimeBackend.CPU
        return RuntimeBackend.RULES

    @staticmethod
    def _rules_plan(
        mode: PolishMode,
        task: PolishTask,
        reason: str,
    ) -> ExecutionPlan:
        return ExecutionPlan(
            policy_version=POLISH_POLICY_VERSION,
            mode=mode,
            task=task,
            use_llm=False,
            reason=reason,
            downgrade_reason=reason,
        )

    @staticmethod
    def _resource_reason(snapshot: HardwareSnapshot) -> str:
        if not snapshot.driver_healthy and snapshot.gpu_name:
            return "Graphics driver is unhealthy; using rules and MiniLM."
        if snapshot.available_ram_bytes < 4 * _GIB:
            return "Available RAM is low; using rules and MiniLM."
        # Reclaimable, not free. Every pack the cascade tries stays resident on
        # the card afterwards, so a few refused corrections were enough to fill
        # the GPU with this app's own models - and then this check saw a full
        # card, dropped to rules, and never recovered, because nothing unloads
        # while the plan says rules. Measured on a 16 GB card: essays 1 to 3
        # corrected normally, then every paragraph after came back rules-only
        # with "Available VRAM is low". `_select_backend` already counts loaded
        # model VRAM as reclaimable for exactly this reason; the two disagreed.
        if snapshot.gpu_name and _reclaimable_vram(snapshot) < 2 * _GIB:
            return "Available VRAM is low; using rules and MiniLM."
        return ""


class HardwareDetector:
    """Short-lived cached detector for RAM, DXGI adapters, NVIDIA, and power."""

    def __init__(self, *, cache_seconds: float = 5.0) -> None:
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._lock = threading.Lock()
        self._cached: HardwareSnapshot | None = None
        self._cached_at = 0.0

    def snapshot(self, *, force: bool = False) -> HardwareSnapshot:
        with self._lock:
            if (
                not force
                and self._cached is not None
                and time.monotonic() - self._cached_at <= self.cache_seconds
            ):
                return self._cached
            value = detect_hardware_snapshot()
            self._cached = value
            self._cached_at = time.monotonic()
            return value


def detect_hardware_snapshot() -> HardwareSnapshot:
    total_ram, available_ram = _memory_status()
    cpu_features = _cpu_features()
    gpu = _nvidia_status()
    dxgi = _dxgi_adapters()
    if gpu is None and dxgi:
        best = max(dxgi, key=lambda item: item["dedicated_vram_bytes"])
        gpu = {
            "name": best["name"],
            "vendor": best["vendor"],
            "dedicated_vram_bytes": best["dedicated_vram_bytes"],
            "available_vram_bytes": best["available_vram_bytes"],
            "driver_version": "",
            "healthy": best["dedicated_vram_bytes"] > 0,
        }
    gpu = gpu or {
        "name": "",
        "vendor": "",
        "dedicated_vram_bytes": 0,
        "available_vram_bytes": 0,
        "driver_version": "",
        "healthy": False,
    }
    loaded_models, loaded_model_vram = _ollama_loaded_models()
    on_battery, battery_percent = _power_status()
    backends = [RuntimeBackend.CPU]
    if gpu["healthy"] and gpu["vendor"].casefold() == "nvidia":
        backends.insert(0, RuntimeBackend.CUDA)
    elif gpu["healthy"] and gpu["vendor"].casefold() == "amd":
        backends.insert(0, RuntimeBackend.HIP)
        backends.insert(1, RuntimeBackend.VULKAN)
    elif gpu["healthy"] and gpu["vendor"].casefold() == "intel":
        backends.insert(0, RuntimeBackend.SYCL)
        backends.insert(1, RuntimeBackend.VULKAN)
    return HardwareSnapshot(
        cpu_name=(platform.processor() or platform.machine() or "Unknown CPU").strip(),
        cpu_threads=max(1, int(os.cpu_count() or 1)),
        cpu_features=cpu_features,
        total_ram_bytes=total_ram,
        available_ram_bytes=available_ram,
        gpu_name=str(gpu["name"]),
        gpu_vendor=str(gpu["vendor"]),
        dedicated_vram_bytes=max(0, int(gpu["dedicated_vram_bytes"])),
        available_vram_bytes=max(0, int(gpu["available_vram_bytes"])),
        driver_version=str(gpu["driver_version"]),
        driver_healthy=bool(gpu["healthy"]),
        on_battery=on_battery,
        battery_percent=battery_percent,
        supported_backends=tuple(dict.fromkeys(backends)),
        runtime_version=_ollama_version(),
        loaded_models=loaded_models,
        loaded_model_vram=loaded_model_vram,
    )


def _request_budget(
    mode: PolishMode,
    task: PolishTask,
    scope: ContextScope,
    pack: PolishModelPack,
) -> tuple[int, int]:
    if mode is PolishMode.INSTANT:
        desired_context, output = 1024, 256
    elif mode is PolishMode.PROFESSIONAL:
        desired_context, output = 4096, 1024
    elif scope in {ContextScope.PARAGRAPH, ContextScope.DOCUMENT}:
        desired_context, output = 4096, 768
    else:
        desired_context, output = 2048, 512
    if task is PolishTask.DOCUMENT and mode is not PolishMode.PROFESSIONAL:
        desired_context = min(desired_context, 2048)
    return min(desired_context, pack.max_context_tokens), output


def _standby_model(
    packs,
    selected,
    snapshot,
    vram_reserve_bytes: int,
) -> str:
    """The smallest installed pack to fall back on when the main one stalls.

    A cold load, or another application taking the GPU mid-document, used to
    cost the reader the whole section. A small model answering late beats no
    answer, so one is kept loaded beside the primary.

    It is only offered when it genuinely fits beside the primary in VRAM.
    A GPU plan never advertises a CPU-spill standby.
    """
    smaller = sorted(
        (
            pack
            for pack in packs
            if pack.installed
            and pack.pack_id in _PACK_IDS
            and pack.pack_id != selected.pack_id
            and pack.size_bytes < selected.size_bytes
        ),
        key=lambda pack: pack.size_bytes,
    )
    if not smaller:
        return ""
    candidate = smaller[0]
    spare_vram = (
        snapshot.dedicated_vram_bytes
        - vram_reserve_bytes
        - selected.size_bytes
    )
    if spare_vram >= candidate.size_bytes:
        return candidate.model
    return ""


def _latency_limit(mode: PolishMode) -> float:
    """Hard warm-generation limit before policy selects a smaller pack."""
    if mode is PolishMode.INSTANT:
        return 2.0
    if mode is PolishMode.PROFESSIONAL:
        return 15.0
    return 5.0


def _reclaimable_vram(snapshot: HardwareSnapshot) -> int:
    """Free VRAM plus whatever a resident model could give back.

    Ollama unloads the model it is holding before it loads another one, so
    VRAM occupied by a language model is available to the next one. Treating
    it as unavailable makes the app's own success look like a full card.
    """
    loaded_vram = sum(
        max(0, int(value)) for _model, value in snapshot.loaded_model_vram
    )
    return min(
        snapshot.dedicated_vram_bytes,
        snapshot.available_vram_bytes + loaded_vram,
    )


def _vram_reserve(dedicated_vram_bytes: int) -> int:
    gib = dedicated_vram_bytes / _GIB
    if gib < 8:
        return int(1.5 * _GIB)
    if gib < 12:
        return 2 * _GIB
    if gib < 20:
        return 3 * _GIB
    return 4 * _GIB


def _memory_status() -> tuple[int, int]:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical), int(status.available_physical)
    try:
        page = int(os.sysconf("SC_PAGE_SIZE"))
        return page * int(os.sysconf("SC_PHYS_PAGES")), page * int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return 0, 0


def _cpu_features() -> tuple[str, ...]:
    features: list[str] = []
    if sys.platform == "win32":
        checks = ((10, "sse2"), (18, "avx"), (40, "avx2"), (41, "avx512f"))
        for code, name in checks:
            try:
                if ctypes.windll.kernel32.IsProcessorFeaturePresent(code):
                    features.append(name)
            except (AttributeError, OSError):
                break
    return tuple(features)


def _power_status() -> tuple[bool, int | None]:
    if sys.platform != "win32":
        return False, None

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ac_line_status", ctypes.c_ubyte),
            ("battery_flag", ctypes.c_ubyte),
            ("battery_life_percent", ctypes.c_ubyte),
            ("system_status_flag", ctypes.c_ubyte),
            ("battery_life_time", ctypes.c_ulong),
            ("battery_full_life_time", ctypes.c_ulong),
        ]

    status = PowerStatus()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return False, None
    except (AttributeError, OSError):
        return False, None
    percent = int(status.battery_life_percent)
    return status.ac_line_status == 0, percent if 0 <= percent <= 100 else None


def _nvidia_status() -> dict[str, object] | None:
    executable = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
    if not executable:
        system = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = os.path.join(system, "System32", "nvidia-smi.exe")
        executable = candidate if os.path.isfile(candidate) else None
    if not executable:
        return None
    query = (
        "name,driver_version,memory.total,memory.free"
    )
    try:
        completed = subprocess.run(
            [
                executable,
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return {
            "name": "NVIDIA GPU",
            "vendor": "NVIDIA",
            "dedicated_vram_bytes": 0,
            "available_vram_bytes": 0,
            "driver_version": "",
            "healthy": False,
        }
    first = next((line for line in completed.stdout.splitlines() if line.strip()), "")
    parts = [part.strip() for part in first.split(",")]
    if len(parts) != 4:
        return None
    try:
        total = int(float(parts[2])) * 1024**2
        available = int(float(parts[3])) * 1024**2
    except ValueError:
        return None
    return {
        "name": parts[0],
        "vendor": "NVIDIA",
        "dedicated_vram_bytes": total,
        "available_vram_bytes": available,
        "driver_version": parts[1],
        "healthy": total > 0,
    }


_LOADED_MODEL_CACHE: dict[str, object] = {"at": 0.0, "value": ((), ())}
# Long enough to cover a generation, short enough that a model genuinely
# unloaded by the keep-alive window is forgotten soon after.
_LOADED_MODEL_CACHE_SECONDS = 120.0


def _last_loaded_models() -> tuple[
    tuple[str, ...],
    tuple[tuple[str, int], ...],
]:
    """The most recent successful reading, while it is still plausible.

    A failed query means "not known", not "nothing is loaded", and the two
    answers send the policy in opposite directions. Treating a timeout as an
    empty card made the app disable itself precisely when it was busiest.
    """
    if time.time() - float(_LOADED_MODEL_CACHE["at"]) <= _LOADED_MODEL_CACHE_SECONDS:
        return _LOADED_MODEL_CACHE["value"]  # type: ignore[return-value]
    return (), ()


def _ollama_loaded_models() -> tuple[
    tuple[str, ...],
    tuple[tuple[str, int], ...],
]:
    try:
        payload = LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
            "/api/ps",
            # Half a second is not long enough while the server is generating,
            # which is exactly when this is asked. The answer decides whether
            # VRAM held by a loaded model counts as reclaimable, so losing it
            # reads as "the card is full and nothing can be freed" and drops
            # the whole app to rules.
            timeout=2.0,
        )
    except (LocalTransportError, OSError, RuntimeError, TypeError, ValueError):
        return _last_loaded_models()
    values = payload.get("models")
    if not isinstance(values, list):
        return _last_loaded_models()
    names: list[str] = []
    vram: list[tuple[str, int]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip().casefold()
        if not name:
            continue
        names.append(name)
        try:
            gpu_bytes = max(0, int(item.get("size_vram") or 0))
        except (TypeError, ValueError):
            gpu_bytes = 0
        vram.append((name, gpu_bytes))
    value = (tuple(dict.fromkeys(names)), tuple(vram))
    _LOADED_MODEL_CACHE["at"] = time.time()
    _LOADED_MODEL_CACHE["value"] = value
    return value


def _ollama_version() -> str:
    executable = shutil.which("ollama") or shutil.which("ollama.exe")
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join((completed.stdout or completed.stderr).split())[:120]


def _dxgi_adapters() -> tuple[dict[str, object], ...]:
    """Enumerate DXGI 1.1 adapters and query DXGI 1.4 local-memory budgets."""
    if sys.platform != "win32":
        return ()
    try:
        return _dxgi_adapters_windows()
    except (AttributeError, OSError, TypeError, ValueError):
        return ()


def _dxgi_adapters_windows() -> tuple[dict[str, object], ...]:
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_ulong),
            ("data2", ctypes.c_ushort),
            ("data3", ctypes.c_ushort),
            ("data4", ctypes.c_ubyte * 8),
        ]

    class LUID(ctypes.Structure):
        _fields_ = [("low_part", wintypes.DWORD), ("high_part", wintypes.LONG)]

    class AdapterDesc1(ctypes.Structure):
        _fields_ = [
            ("description", wintypes.WCHAR * 128),
            ("vendor_id", wintypes.UINT),
            ("device_id", wintypes.UINT),
            ("subsys_id", wintypes.UINT),
            ("revision", wintypes.UINT),
            ("dedicated_video_memory", ctypes.c_size_t),
            ("dedicated_system_memory", ctypes.c_size_t),
            ("shared_system_memory", ctypes.c_size_t),
            ("adapter_luid", LUID),
            ("flags", wintypes.UINT),
        ]

    class QueryVideoMemoryInfo(ctypes.Structure):
        _fields_ = [
            ("budget", ctypes.c_uint64),
            ("current_usage", ctypes.c_uint64),
            ("available_for_reservation", ctypes.c_uint64),
            ("current_reservation", ctypes.c_uint64),
        ]

    def guid(value: str) -> GUID:
        import uuid

        raw = uuid.UUID(value).bytes_le
        result = GUID()
        ctypes.memmove(ctypes.byref(result), raw, 16)
        return result

    factory_iid = guid("770aae78-f26f-4dba-a829-253c83d1b387")
    adapter3_iid = guid("645967a4-1392-4310-a798-8053ce3e93fd")
    dxgi = ctypes.WinDLL("dxgi")
    create_factory = dxgi.CreateDXGIFactory1
    create_factory.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create_factory.restype = ctypes.c_long
    factory = ctypes.c_void_p()
    if create_factory(ctypes.byref(factory_iid), ctypes.byref(factory)) < 0:
        return ()

    def method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
        table = ctypes.cast(
            pointer,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])

    release = method(factory, 2, wintypes.ULONG)
    enum_adapters = method(
        factory,
        12,
        ctypes.c_long,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    results: list[dict[str, object]] = []
    try:
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            code = enum_adapters(factory, index, ctypes.byref(adapter))
            if code == 0x887A0002 or code < 0:
                break
            adapter_release = method(adapter, 2, wintypes.ULONG)
            try:
                get_desc = method(
                    adapter,
                    10,
                    ctypes.c_long,
                    ctypes.POINTER(AdapterDesc1),
                )
                desc = AdapterDesc1()
                if get_desc(adapter, ctypes.byref(desc)) < 0:
                    index += 1
                    continue
                available = max(0, int(desc.dedicated_video_memory))
                query_interface = method(
                    adapter,
                    0,
                    ctypes.c_long,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                adapter3 = ctypes.c_void_p()
                if query_interface(adapter, ctypes.byref(adapter3_iid), ctypes.byref(adapter3)) >= 0:
                    adapter3_release = method(adapter3, 2, wintypes.ULONG)
                    try:
                        query_memory = method(
                            adapter3,
                            14,
                            ctypes.c_long,
                            wintypes.UINT,
                            ctypes.c_int,
                            ctypes.POINTER(QueryVideoMemoryInfo),
                        )
                        info = QueryVideoMemoryInfo()
                        if query_memory(adapter3, 0, 0, ctypes.byref(info)) >= 0:
                            available = max(0, int(info.budget - info.current_usage))
                    finally:
                        adapter3_release(adapter3)
                vendor = {
                    0x10DE: "NVIDIA",
                    0x1002: "AMD",
                    0x8086: "Intel",
                }.get(int(desc.vendor_id), f"0x{int(desc.vendor_id):04X}")
                results.append(
                    {
                        "name": str(desc.description).strip("\x00 "),
                        "vendor": vendor,
                        "dedicated_vram_bytes": int(desc.dedicated_video_memory),
                        "available_vram_bytes": available,
                    }
                )
            finally:
                adapter_release(adapter)
            index += 1
    finally:
        release(factory)
    return tuple(results)


__all__ = [
    "BenchmarkRecord",
    "HardwareDetector",
    "HardwarePolicyManager",
    "POLISH_POLICY_VERSION",
    "PolishBenchmarkCache",
    "detect_hardware_snapshot",
]
