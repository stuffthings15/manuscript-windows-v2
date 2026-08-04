from __future__ import annotations

import wave

import numpy as np

from echo_link_app import sound_cues
from echo_link_core.models import DictationSettings


def test_sound_cues_can_be_disabled_for_private_silent_use(monkeypatch):
    monkeypatch.setenv("ECHOLINK_DISABLE_SOUNDS", "1")
    assert sound_cues.play_start() is False
    assert sound_cues.play_stop() is False


def test_auto_bluetooth_delay_uses_original_700_ms_wake_behavior():
    assert sound_cues.resolve_delay_ms(-1, "JBL Bluetooth Speaker") == 700
    assert sound_cues.resolve_delay_ms(-1, "Realtek Speakers") == 0
    assert sound_cues.resolve_delay_ms(1500, "Realtek Speakers") == 1500


def test_required_activation_cues_are_built_in(monkeypatch):
    monkeypatch.delenv("ECHOLINK_DISABLE_SOUNDS", raising=False)
    calls = []
    monkeypatch.setattr(
        sound_cues.sd,
        "play",
        lambda data, sample_rate, *, device, blocking: calls.append(
            (data.copy(), sample_rate, device, blocking)
        ),
    )
    monkeypatch.setattr(sound_cues, "output_device_name", lambda _index: "Local speakers")

    assert sound_cues.list_sound_choices()[:2] == [
        sound_cues.BUILTIN_START,
        sound_cues.BUILTIN_STOP,
    ]
    assert sound_cues.play_cue(
        sound_cues.BUILTIN_START,
        default_name=sound_cues.BUILTIN_START,
        delay_ms=0,
        blocking=True,
    )
    assert len(calls) == 1
    assert calls[0][0].shape == (7056,)
    assert calls[0][1:] == (44100, None, True)


def test_legacy_default_names_fall_back_to_built_in_tones(monkeypatch):
    monkeypatch.delenv("ECHOLINK_DISABLE_SOUNDS", raising=False)
    monkeypatch.setattr(sound_cues, "resolve_cue_path", lambda *_args: None)
    monkeypatch.setattr(
        sound_cues,
        "_play_builtin_blocking",
        lambda kind, *_args: kind == "start",
    )

    assert sound_cues.play_cue(
        "Speech On.wav",
        default_name="Speech On.wav",
        delay_ms=0,
        blocking=True,
    )


def test_activation_sound_uses_original_windows_speech_on_cue(monkeypatch, tmp_path):
    media = tmp_path / "Media"
    media.mkdir()
    cue = media / "Speech On.wav"
    cue.write_bytes(b"original activation cue")
    monkeypatch.setenv("WINDIR", str(tmp_path))

    assert DictationSettings().start_sound == "Speech On.wav"
    assert sound_cues.resolve_cue_path("Speech On.wav", "Speech On.wav") == cue


def test_original_windows_activation_file_is_played_instead_of_generated_tone(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("ECHOLINK_DISABLE_SOUNDS", raising=False)
    media = tmp_path / "Media"
    media.mkdir()
    cue = media / "Speech On.wav"
    cue.write_bytes(b"original activation cue")
    monkeypatch.setenv("WINDIR", str(tmp_path))
    played_files = []
    generated_tones = []
    monkeypatch.setattr(
        sound_cues,
        "_play_blocking",
        lambda path, *_args: played_files.append(path) or True,
    )
    monkeypatch.setattr(
        sound_cues,
        "_play_builtin_blocking",
        lambda kind, *_args: generated_tones.append(kind) or True,
    )

    assert sound_cues.play_cue(
        "Speech On.wav",
        default_name="Speech On.wav",
        delay_ms=0,
        blocking=True,
    )
    assert played_files == [cue]
    assert generated_tones == []


def test_existing_builtin_activation_profiles_migrate_to_original_sound():
    settings = DictationSettings.from_dict(
        {"start_sound": sound_cues.BUILTIN_START}
    )

    assert settings.start_sound == "Speech On.wav"


def test_selected_output_receives_local_wav(monkeypatch, tmp_path):
    cue = tmp_path / "cue.wav"
    samples = (np.sin(np.linspace(0, 8, 800)) * 12000).astype("<i2")
    with wave.open(str(cue), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(samples.tobytes())
    calls = []

    def fake_play(data, sample_rate, *, device, blocking):
        calls.append((data.copy(), sample_rate, device, blocking))

    monkeypatch.delenv("ECHOLINK_DISABLE_SOUNDS", raising=False)
    monkeypatch.setattr(sound_cues.sd, "play", fake_play)
    monkeypatch.setattr(sound_cues, "output_device_name", lambda _index: "USB Speakers")

    assert sound_cues.play_cue(
        str(cue),
        default_name="unused.wav",
        output_index=7,
        delay_ms=0,
        blocking=True,
    )
    assert len(calls) == 1
    assert calls[0][1:] == (16000, 7, True)
    assert calls[0][0].shape == (800,)


def test_muted_cue_does_not_queue_playback(monkeypatch):
    monkeypatch.delenv("ECHOLINK_DISABLE_SOUNDS", raising=False)
    assert sound_cues.play_cue("Muted", default_name="Speech On.wav") is False
