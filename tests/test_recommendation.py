from __future__ import annotations

from pathlib import Path

from echo_link_core import recommendation
from echo_link_core.recommendation import (
    HardwareProfile,
    apply_first_run_recommendation,
    recommend_local_speech,
)
from echo_link_core.storage import EchoLinkStore
from echo_link_core.transcription import LocalModelInfo


def model(name: str, multilingual: bool = False) -> LocalModelInfo:
    return LocalModelInfo(name, Path(name), multilingual)


def test_modest_cpu_prefers_base_over_larger_installed_model():
    result = recommend_local_speech(
        [model("tiny.en"), model("base.en"), model("small.en")],
        language="en",
        profile=HardwareProfile(cpu_threads=4, memory_gb=8, cuda=False),
    )
    assert result.model_name == "base.en"
    assert result.device == "cpu"


def test_strong_cpu_uses_installed_small_model():
    result = recommend_local_speech(
        [model("base.en"), model("small.en")],
        language="en",
        profile=HardwareProfile(cpu_threads=12, memory_gb=32, cuda=False),
    )
    assert result.model_name == "small.en"
    assert result.device == "cpu"


def test_cuda_profile_uses_installed_medium_model():
    result = recommend_local_speech(
        [model("base.en"), model("small.en"), model("medium.en")],
        language="en",
        profile=HardwareProfile(cpu_threads=8, memory_gb=32, cuda=True),
    )
    assert result.model_name == "medium.en"
    assert result.device == "cuda"


def test_non_english_requires_multilingual_pack():
    assert recommend_local_speech(
        [model("base.en")],
        language="es",
        profile=HardwareProfile(cpu_threads=8, memory_gb=16, cuda=False),
    ) is None
    result = recommend_local_speech(
        [model("base.en"), model("small", multilingual=True)],
        language="es",
        profile=HardwareProfile(cpu_threads=8, memory_gb=16, cuda=False),
    )
    assert result.model_name == "small"


def test_cuda_runtime_requires_local_inference_libraries(monkeypatch):
    monkeypatch.setattr(recommendation, "cuda_available", lambda: True)
    monkeypatch.setattr(recommendation.sys, "platform", "win32")
    monkeypatch.setattr(recommendation.ctypes, "WinDLL", lambda _name: object())
    assert recommendation.cuda_runtime_ready() is True

    def missing(_name):
        raise OSError("missing")

    monkeypatch.setattr(recommendation.ctypes, "WinDLL", missing)
    assert recommendation.cuda_runtime_ready() is False


def test_first_run_applies_recommendation_once(tmp_path):
    store = EchoLinkStore(tmp_path / "echo-link.db")
    result = apply_first_run_recommendation(
        store,
        [model("base.en"), model("small.en")],
        profile=HardwareProfile(cpu_threads=16, memory_gb=32, cuda=False),
    )
    assert result is not None
    assert store.get_settings().model_name == "small.en"
    assert store.get_settings().device == "cpu"
    assert store.get_meta("first_run_setup_pending") is False

    settings = store.get_settings()
    settings.model_name = "base.en"
    store.save_settings(settings)
    assert apply_first_run_recommendation(
        store,
        [model("small.en")],
        profile=HardwareProfile(cpu_threads=16, memory_gb=32, cuda=False),
    ) is None
    assert store.get_settings().model_name == "base.en"
