from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Protocol

from .transcription import LocalModelInfo, cuda_available


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    cpu_threads: int
    memory_gb: float | None
    cuda: bool


@dataclass(frozen=True, slots=True)
class ModelRecommendation:
    model_name: str
    device: str
    reason: str


class SettingsStore(Protocol):
    def get_meta(self, key: str, default=None): ...

    def set_meta(self, key: str, value) -> None: ...

    def get_settings(self): ...

    def save_settings(self, settings) -> None: ...


def local_hardware_profile() -> HardwareProfile:
    return HardwareProfile(
        cpu_threads=max(1, int(os.cpu_count() or 1)),
        memory_gb=_system_memory_gb(),
        cuda=cuda_runtime_ready(),
    )


def cuda_runtime_ready() -> bool:
    if not cuda_available():
        return False
    libraries = (
        ("cublas64_12.dll", "cudnn64_9.dll")
        if sys.platform == "win32"
        else ("libcublas.so.12", "libcudnn.so.9")
    )
    loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
    try:
        for library in libraries:
            loader(library)
    except OSError:
        return False
    return True


def recommend_local_speech(
    models: list[LocalModelInfo],
    *,
    language: str,
    profile: HardwareProfile | None = None,
) -> ModelRecommendation | None:
    if not models:
        return None
    profile = profile or local_hardware_profile()
    language = (language or "en").casefold()
    eligible = [model for model in models if language in {"en", "auto"} or model.multilingual]
    if not eligible:
        return None

    capacity = 2
    if profile.cuda:
        capacity = 4 if (profile.memory_gb or 0) >= 16 else 3
    elif profile.cpu_threads >= 8 and (profile.memory_gb or 0) >= 12:
        capacity = 3

    ranked = sorted(eligible, key=lambda item: (_model_rank(item.name), item.name.casefold()))
    within_capacity = [model for model in ranked if _model_rank(model.name) <= capacity]
    selected = within_capacity[-1] if within_capacity else ranked[0]
    device = "cuda" if profile.cuda else "cpu"
    memory = f", {profile.memory_gb:.0f} GB RAM" if profile.memory_gb is not None else ""
    reason = (
        f"{selected.name} on {'NVIDIA GPU' if device == 'cuda' else 'CPU'} "
        f"for {profile.cpu_threads} threads{memory}. Only installed local models were considered."
    )
    return ModelRecommendation(selected.name, device, reason)


def apply_first_run_recommendation(
    store: SettingsStore,
    models: list[LocalModelInfo],
    *,
    profile: HardwareProfile,
) -> ModelRecommendation | None:
    if not bool(store.get_meta("first_run_setup_pending", False)):
        return None
    settings = store.get_settings()
    recommendation = recommend_local_speech(
        models,
        language=settings.language,
        profile=profile,
    )
    if recommendation is None:
        return None
    settings.model_name = recommendation.model_name
    settings.device = recommendation.device
    settings.compute_type = "float16" if recommendation.device == "cuda" else "int8"
    store.save_settings(settings)
    store.set_meta("first_run_setup_pending", False)
    store.set_meta(
        "first_run_setup_applied",
        {"model": recommendation.model_name, "device": recommendation.device},
    )
    return recommendation


def _model_rank(name: str) -> int:
    value = name.casefold().replace("_", "-")
    for token, rank in (
        ("large", 5),
        ("medium", 4),
        ("small", 3),
        ("base", 2),
        ("tiny", 1),
    ):
        if token in value:
            return rank
    return 2


def _system_memory_gb() -> float | None:
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
            return round(status.total_physical / (1024**3), 1)
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return round((page_size * pages) / (1024**3), 1)
    except (AttributeError, OSError, ValueError):
        return None
