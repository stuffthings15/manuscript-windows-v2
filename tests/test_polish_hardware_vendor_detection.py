"""Coverage for detect_hardware_snapshot()'s vendor-to-backend mapping.

Every other test in test_polish_hardware.py builds a HardwareSnapshot by
hand and never exercises detect_hardware_snapshot() itself, so the AMD, Intel
and ARM branches of the real vendor detection - the code that decides which
runtime backend a writer's actual machine gets offered - had zero coverage.
This file exercises that function directly by monkeypatching its private
probes (_nvidia_status, _dxgi_adapters, _memory_status, _power_status,
_ollama_loaded_models, _cpu_features), the same functions the real detector
calls, rather than adding a second detection path for tests to run.

The declared hardware floor is "GPU required, small GPU degrades to a
smaller pack." This file makes the "degrades" half a measured property for
every non-NVIDIA vendor path that ships today, not an assumption.
"""

from __future__ import annotations

import echo_link_core.polish_hardware as polish_hardware
from echo_link_core.polish_types import RuntimeBackend


GIB = 1024**3


def _patch_common(monkeypatch) -> None:
    monkeypatch.setattr(polish_hardware, "_memory_status", lambda: (32 * GIB, 20 * GIB))
    monkeypatch.setattr(polish_hardware, "_cpu_features", lambda: ("avx2",))
    monkeypatch.setattr(polish_hardware, "_power_status", lambda: (False, None))
    monkeypatch.setattr(polish_hardware, "_ollama_loaded_models", lambda: ((), 0))


def _dxgi_adapter(*, vendor: str, vram_bytes: int) -> dict[str, object]:
    return {
        "name": f"{vendor} test adapter",
        "vendor": vendor,
        "dedicated_vram_bytes": vram_bytes,
        "available_vram_bytes": vram_bytes,
    }


def test_amd_gpu_offers_hip_first_then_vulkan_then_cpu(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(polish_hardware, "_nvidia_status", lambda: None)
    monkeypatch.setattr(
        polish_hardware,
        "_dxgi_adapters",
        lambda: (_dxgi_adapter(vendor="amd", vram_bytes=8 * GIB),),
    )

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert snapshot.gpu_vendor == "amd"
    assert snapshot.driver_healthy is True
    assert snapshot.supported_backends == (
        RuntimeBackend.HIP,
        RuntimeBackend.VULKAN,
        RuntimeBackend.CPU,
    )


def test_intel_gpu_offers_sycl_first_then_vulkan_then_cpu(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(polish_hardware, "_nvidia_status", lambda: None)
    monkeypatch.setattr(
        polish_hardware,
        "_dxgi_adapters",
        lambda: (_dxgi_adapter(vendor="intel", vram_bytes=4 * GIB),),
    )

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert snapshot.gpu_vendor == "intel"
    assert snapshot.driver_healthy is True
    assert snapshot.supported_backends == (
        RuntimeBackend.SYCL,
        RuntimeBackend.VULKAN,
        RuntimeBackend.CPU,
    )


def test_a_small_amd_card_still_gets_hip_offered_not_silently_downgraded(monkeypatch):
    # "GPU required, small GPU degrades to a SMALLER PACK" - the vendor
    # detector's job is only to report what backend the hardware can run;
    # HardwarePolicyManager.plan() is what picks a smaller pack for a small
    # card. A vendor detector that quietly stopped offering a backend for a
    # small card would hide the degradation from the layer responsible for
    # it, so a 2 GiB AMD card must still surface HIP + VULKAN here.
    _patch_common(monkeypatch)
    monkeypatch.setattr(polish_hardware, "_nvidia_status", lambda: None)
    monkeypatch.setattr(
        polish_hardware,
        "_dxgi_adapters",
        lambda: (_dxgi_adapter(vendor="amd", vram_bytes=2 * GIB),),
    )

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert RuntimeBackend.HIP in snapshot.supported_backends
    assert RuntimeBackend.VULKAN in snapshot.supported_backends
    assert snapshot.dedicated_vram_bytes == 2 * GIB


def test_arm_or_any_machine_with_no_healthy_gpu_gets_cpu_only(monkeypatch):
    # This is the hardware-floor refusal path: no vendor GPU means no
    # accelerated backend is offered at all, whatever the CPU architecture.
    # ARM ships no vendor-specific DXGI path in this detector, so an ARM
    # machine and any machine with no working adapter take the same route
    # here; the floor is enforced once, in HardwarePolicyManager.plan(),
    # not duplicated per architecture.
    _patch_common(monkeypatch)
    monkeypatch.setattr(polish_hardware, "_nvidia_status", lambda: None)
    monkeypatch.setattr(polish_hardware, "_dxgi_adapters", lambda: ())

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert snapshot.gpu_vendor == ""
    assert snapshot.driver_healthy is False
    assert snapshot.supported_backends == (RuntimeBackend.CPU,)


def test_an_unhealthy_amd_adapter_falls_back_to_cpu_only(monkeypatch):
    # A DXGI adapter that reports zero dedicated VRAM is not healthy, and an
    # unhealthy GPU must not be offered as a backend candidate.
    _patch_common(monkeypatch)
    monkeypatch.setattr(polish_hardware, "_nvidia_status", lambda: None)
    monkeypatch.setattr(
        polish_hardware,
        "_dxgi_adapters",
        lambda: (_dxgi_adapter(vendor="amd", vram_bytes=0),),
    )

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert snapshot.driver_healthy is False
    assert snapshot.supported_backends == (RuntimeBackend.CPU,)


def test_nvidia_still_wins_cuda_first_when_multiple_adapters_present(monkeypatch):
    # Regression guard for the branch order itself: NVIDIA's own status probe
    # takes priority over the generic DXGI vendor path, and CUDA must still be
    # the first backend offered, not HIP or SYCL from a second adapter.
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        polish_hardware,
        "_nvidia_status",
        lambda: {
            "name": "nvidia test adapter",
            "vendor": "nvidia",
            "dedicated_vram_bytes": 16 * GIB,
            "available_vram_bytes": 16 * GIB,
            "driver_version": "1.0",
            "healthy": True,
        },
    )
    monkeypatch.setattr(
        polish_hardware,
        "_dxgi_adapters",
        lambda: (_dxgi_adapter(vendor="amd", vram_bytes=8 * GIB),),
    )

    snapshot = polish_hardware.detect_hardware_snapshot()

    assert snapshot.gpu_vendor == "nvidia"
    assert snapshot.supported_backends[0] == RuntimeBackend.CUDA
    assert RuntimeBackend.HIP not in snapshot.supported_backends
