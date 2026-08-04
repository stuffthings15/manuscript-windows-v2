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


POLISH_POLICY_VERSION = 5
_GIB = 1024**3
_PACK_ORDER = (
    "gemma4-12b-q4-k-m-v1",
    "qwen3-14b-q6-v1",
    "qwen3-14b-q5-v1",
    "qwen3-14b-q4-k-m-v1",
    "qwen3-8b-q4-k-m-v1",
    "qwen3-4b-q4-k-m-v1",
    "qwen3-1.7b-q4-k-m-v1",
)


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
    """Choose the largest safe, available, locally validated model pack."""

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
        mode = PolishMode.coerce(mode)
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
        selected: PolishModelPack | None = None
        selected_probe = False
        for pack in candidates:
            record = records.get(pack.pack_id)
            if record is not None and not record.accepted:
                failed_reasons.append(f"{pack.display_name}: {record.reason or 'local probe failed'}")
                continue
            selected = pack
            selected_probe = record is None
            break
        if selected is None:
            reason = failed_reasons[0] if failed_reasons else "Every local model probe failed."
            return self._rules_plan(mode, task, reason)

        backend = self._select_backend(snapshot, selected)
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
                f"{selected.display_name} selected for {mode.value} "
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
        pack_values = [pack for pack in packs if pack.available]
        rank = {pack_id: index for index, pack_id in enumerate(_PACK_ORDER)}
        pack_values.sort(
            key=lambda pack: (
                rank.get(pack.pack_id, len(rank)),
                0 if pack.pack_id == preferred_pack_id else 1,
            )
        )
        fast_interactive_pack_available = any(
            pack.pack_id.startswith("qwen3-8b")
            and snapshot.total_ram_bytes >= pack.min_ram_bytes
            and self._select_backend(snapshot, pack) is not RuntimeBackend.RULES
            for pack in pack_values
        )
        eligible: list[PolishModelPack] = []
        for pack in pack_values:
            if (
                mode is not PolishMode.PROFESSIONAL
                and fast_interactive_pack_available
                and pack.pack_id.startswith("qwen3-14b")
            ):
                continue
            if snapshot.total_ram_bytes < pack.min_ram_bytes:
                continue
            if self._select_backend(snapshot, pack) is RuntimeBackend.RULES:
                continue
            eligible.append(pack)
        return eligible

    @staticmethod
    def _select_backend(
        snapshot: HardwareSnapshot,
        pack: PolishModelPack,
    ) -> RuntimeBackend:
        available = set(snapshot.supported_backends) & set(pack.supported_backends)
        reserve = _vram_reserve(snapshot.dedicated_vram_bytes)
        # Ollama can unload the currently resident model before loading the
        # selected pack. Count all model VRAM as reclaimable so a loaded larger
        # model cannot falsely force the policy down to CPU or a smaller pack.
        loaded_vram = sum(
            max(0, int(value))
            for _model, value in snapshot.loaded_model_vram
        )
        effective_available_vram = min(
            snapshot.dedicated_vram_bytes,
            snapshot.available_vram_bytes + loaded_vram,
        )
        gpu_fits = (
            snapshot.driver_healthy
            and snapshot.dedicated_vram_bytes >= pack.min_vram_bytes
            and max(0, effective_available_vram - reserve) >= pack.size_bytes
        )
        for backend in (
            RuntimeBackend.CUDA,
            RuntimeBackend.HIP,
            RuntimeBackend.VULKAN,
            RuntimeBackend.SYCL,
        ):
            if backend in available and gpu_fits:
                return backend
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
        if snapshot.gpu_name and snapshot.available_vram_bytes < 2 * _GIB:
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

    It is only offered when it genuinely fits beside the primary in VRAM, or
    when the machine has the RAM to run it on the CPU. On a small machine there
    is no standby and nothing changes - the Windows build ships to a wide range
    of hardware and must not assume headroom.
    """
    smaller = sorted(
        (
            pack
            for pack in packs
            if pack.installed
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
    # No room on the card. The CPU can still hold it, provided the machine is
    # not already tight on memory.
    if snapshot.available_ram_bytes >= candidate.size_bytes * 2:
        return candidate.model
    return ""


def _latency_limit(mode: PolishMode) -> float:
    """How long a warm probe may take before a pack is considered slow.

    This ranks packs; it no longer decides whether the local AI runs at all, so
    it can afford to be realistic. The old 2s Instant limit was unreachable for
    any pack the app actually ships - a measured section costs about 10s on the
    8B pack - which meant the probe reported failure on healthy hardware.
    """
    if mode is PolishMode.INSTANT:
        return 6.0
    if mode is PolishMode.PROFESSIONAL:
        return 30.0
    return 12.0


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


def _ollama_loaded_models() -> tuple[
    tuple[str, ...],
    tuple[tuple[str, int], ...],
]:
    try:
        payload = LoopbackJSONTransport("http://127.0.0.1:11434").get_json(
            "/api/ps",
            timeout=0.5,
        )
    except (LocalTransportError, OSError, RuntimeError, TypeError, ValueError):
        return (), ()
    values = payload.get("models")
    if not isinstance(values, list):
        return (), ()
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
    return tuple(dict.fromkeys(names)), tuple(vram)


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
