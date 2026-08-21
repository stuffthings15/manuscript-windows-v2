from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from echo_link_core.models import DictationMode, DictationSettings
from echo_link_core.transcription import (
    LocalWhisperTranscriber,
    _prepare_audio,
    discover_local_models,
    local_model_path,
)


class CaptureModel:
    def __init__(self):
        self.audio = None
        self.kwargs = {}

    def transcribe(self, audio, **kwargs):
        self.audio = audio
        self.kwargs = kwargs
        return [SimpleNamespace(text=" test phrase ")], object()


def test_float_microphone_scale_is_normalized():
    model = CaptureModel()
    transcriber = LocalWhisperTranscriber(DictationSettings())
    transcriber._get_model = lambda _callback=None: model
    source = np.full(8000, 16000.0, dtype=np.float32)
    assert transcriber.transcribe(source) == "test phrase"
    assert float(np.max(np.abs(model.audio))) <= 1.0


def test_developer_mode_keeps_fast_single_beam_transcription():
    model = CaptureModel()
    settings = DictationSettings(mode=DictationMode.DEVELOPER)
    transcriber = LocalWhisperTranscriber(settings)
    transcriber._get_model = lambda _callback=None: model
    source = np.full(8000, 16000.0, dtype=np.float32)
    assert transcriber.transcribe(source, mode=DictationMode.DEVELOPER) == "test phrase"
    assert model.kwargs["beam_size"] == 1
    assert model.kwargs["best_of"] == 1


def test_local_model_discovery_requires_core_files(monkeypatch, tmp_path):
    model_name = "echo-link-test-model"
    model = tmp_path / model_name
    model.mkdir()
    monkeypatch.setattr("echo_link_core.transcription.model_root", lambda: tmp_path)
    assert local_model_path(model_name) is None
    (model / "model.bin").write_bytes(b"model")
    (model / "config.json").write_text("{}", encoding="utf-8")
    assert local_model_path(model_name) == model


def test_whisper_mode_boosts_quiet_audio_without_clipping():
    source = np.full(8000, 0.02, dtype=np.float32)
    normal = _prepare_audio(source, whisper_mode=False)
    quiet = _prepare_audio(source, whisper_mode=True)
    assert np.isclose(float(np.max(np.abs(normal))), 0.02)
    assert float(np.max(np.abs(quiet))) > float(np.max(np.abs(normal)))
    assert float(np.max(np.abs(quiet))) <= 1.0


def test_partial_transcription_uses_local_model_and_can_return_empty_safely():
    model = CaptureModel()
    transcriber = LocalWhisperTranscriber(DictationSettings())
    transcriber._get_model = lambda _callback=None: model
    source = np.full(16000, 8000, dtype=np.int16)
    assert transcriber.transcribe_partial(source) == "test phrase"
    assert float(np.max(np.abs(model.audio))) <= 1.0


def test_local_speed_check_uses_fixed_local_audio():
    model = CaptureModel()
    transcriber = LocalWhisperTranscriber(DictationSettings())
    transcriber._get_model = lambda _callback=None: model
    result = transcriber.benchmark(duration_seconds=1.0)
    assert result["model"] == "base.en"
    assert result["device"] == "cpu"
    assert result["realtime_factor"] >= 0
    assert len(model.audio) == 16_000


def test_local_model_discovery_labels_english_and_multilingual_packs(tmp_path):
    for name in ("base.en", "small"):
        model = tmp_path / name
        model.mkdir()
        (model / "model.bin").write_bytes(b"model")
        (model / "config.json").write_text("{}", encoding="utf-8")
    models = discover_local_models([tmp_path])
    assert [model.name for model in models] == ["base.en", "small"]
    assert models[0].multilingual is False
    assert models[1].multilingual is True


def test_a_transcription_records_how_long_it_took(caplog):
    """Without this the log cannot say why a dictation felt slow.

    Curtis reported dictation "taking over a minute" and being "extremely
    inconsistent" on 2026-08-21. The log recorded that a transcription had
    STARTED and nothing else - no duration, no device, no model load - so a
    slow reload, a slow transcription and GPU contention all looked identical
    after the fact. Two of the three causes found that night were only
    identifiable because a separate preload line happened to exist.

    Never the words themselves. Durations and the device, nothing else.
    """
    import logging

    model = CaptureModel()
    transcriber = LocalWhisperTranscriber(DictationSettings())
    transcriber._get_model = lambda _callback=None: model
    source = np.full(8000, 16000.0, dtype=np.float32)
    with caplog.at_level(logging.INFO, logger="echo_link_core.transcription"):
        transcriber.transcribe(source)

    finished = [
        record.getMessage()
        for record in caplog.records
        if "Dictation transcription finished" in record.getMessage()
    ]
    assert finished, [record.getMessage() for record in caplog.records]
    line = finished[0]
    assert "seconds=" in line
    assert "device=" in line
    assert "test phrase" not in line, "the transcript must never be logged"


def test_releasing_the_speech_model_says_so(caplog):
    """A silent release is why the reload it causes looks like a mystery."""
    import logging

    transcriber = LocalWhisperTranscriber(DictationSettings())
    transcriber._model = CaptureModel()
    transcriber._model_key = ("base.en", "cuda", "int8")
    with caplog.at_level(logging.INFO, logger="echo_link_core.transcription"):
        transcriber.release_model()

    assert any(
        "Dictation speech model released" in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]
