from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from echo_link_core import audio
from echo_link_core.audio import AudioCaptureError, MicrophoneRecorder


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "reliability_fault_probe.py"
SPEC = importlib.util.spec_from_file_location("reliability_fault_probe", MODULE_PATH)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

CRASH_MODULE_PATH = ROOT / "tools" / "recovery_crash_probe.py"
CRASH_SPEC = importlib.util.spec_from_file_location(
    "recovery_crash_probe", CRASH_MODULE_PATH
)
assert CRASH_SPEC and CRASH_SPEC.loader
CRASH_PROBE = importlib.util.module_from_spec(CRASH_SPEC)
CRASH_SPEC.loader.exec_module(CRASH_PROBE)


@pytest.mark.parametrize("stage", tuple(PROBE.SCENARIOS))
def test_each_reliability_fault_stage_is_contained_without_audio(tmp_path, stage):
    result = PROBE.run_fault_scenario(stage, tmp_path / stage)

    assert result["passed"], result
    assert result["audio_files"] == []


def test_fault_matrix_is_local_complete_and_audio_free(tmp_path):
    report = PROBE.run_fault_matrix(tmp_path)

    assert report["passed"], report
    assert report["local_only"] is True
    assert report["raw_audio_retained"] is False
    assert report["stage_count"] == len(PROBE.SCENARIOS)


def test_process_crash_matrix_relaunches_without_audio_or_automatic_retry(tmp_path):
    report = CRASH_PROBE.run_crash_matrix(tmp_path)

    assert report["passed"], report
    assert report["local_only"] is True
    assert report["raw_audio_retained"] is False
    assert report["stage_count"] == len(CRASH_PROBE.CRASH_STAGES)
    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert stages["after_deterministic_checkpoint"]["recovery_text"] == CRASH_PROBE.FINAL_TEXT
    assert stages["insertion_uncertain"]["recovery_status"] == "insertion_uncertain"
    assert stages["insertion_uncertain"]["automatic_retry_observed"] is False
    assert stages["post_insert_refinement"]["recovery_count"] == 0


def test_crash_probe_never_opens_child_dpapi_key_in_pytest_process(
    tmp_path,
    monkeypatch,
):
    class ParentStoreMustNotOpen:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("The pytest process must not open a child DPAPI key.")

    monkeypatch.setattr(CRASH_PROBE, "EchoLinkStore", ParentStoreMustNotOpen)

    result = CRASH_PROBE._run_stage("recording", tmp_path / "recording")

    assert result["passed"], result
    assert result["first_relaunch_exit"] == 0
    assert result["second_relaunch_exit"] == 0


class StartFailingStream:
    instances = []

    def __init__(self, **_kwargs):
        self.stopped = False
        self.closed = False
        self.instances.append(self)

    def start(self):
        raise RuntimeError("injected stream start failure")

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize("operation", ("start", "arm"))
def test_failed_microphone_stream_is_closed(monkeypatch, operation):
    StartFailingStream.instances.clear()
    monkeypatch.setattr(audio.sd, "InputStream", StartFailingStream)
    recorder = MicrophoneRecorder()

    with pytest.raises(AudioCaptureError):
        if operation == "start":
            recorder.start()
        else:
            recorder.arm(pre_roll_ms=500)

    stream = StartFailingStream.instances[0]
    assert stream.stopped is True
    assert stream.closed is True
    assert recorder.is_recording is False
