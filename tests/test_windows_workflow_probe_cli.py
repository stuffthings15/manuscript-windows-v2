from __future__ import annotations

import inspect
import os
import socket
from pathlib import Path

import pytest

import echo_link_app.main as app_main
from tools import windows_workflow_probe as probe


def test_probe_environment_is_isolated_offline_and_restored(monkeypatch) -> None:
    monkeypatch.setenv("ECHOLINK_DATA_DIR", "original-private-data-root")
    observed = {}

    def inspect(root: Path):
        isolated = root / "data"
        observed["data"] = os.environ["ECHOLINK_DATA_DIR"]
        observed["offline"] = (
            os.environ["HF_HUB_OFFLINE"],
            os.environ["TRANSFORMERS_OFFLINE"],
        )
        assert isolated.is_dir()
        with socket.socket() as client:
            with pytest.raises(probe.ProbeNetworkDisabledError, match="disabled"):
                client.connect(("127.0.0.1", 11434))
        return "complete"

    assert probe._run_in_isolated_environment(inspect) == "complete"
    assert Path(observed["data"]).name == "data"
    assert observed["offline"] == ("1", "1")
    assert os.environ["ECHOLINK_DATA_DIR"] == "original-private-data-root"


def test_packaged_target_relaunches_same_executable(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "EchoLink.exe"
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe.sys, "executable", str(executable))

    command = probe._target_command("Synthetic target", tmp_path / "status.json")

    assert command[0] == str(executable)
    assert command[1:4] == [
        "--workflow-probe-target",
        "--probe-target-title",
        "Synthetic target",
    ]
    assert command[-2] == "--probe-target-status"


def test_source_target_reuses_canonical_tool(monkeypatch, tmp_path) -> None:
    monkeypatch.delattr(probe.sys, "frozen", raising=False)
    monkeypatch.setattr(probe.sys, "executable", "python.exe")

    command = probe._target_command("Synthetic target", tmp_path / "status.json")

    assert command[0] == "python.exe"
    assert Path(command[1]).resolve() == Path(probe.__file__).resolve()
    assert command[2] == "--target-mode"


def test_app_cli_dispatches_probe_before_live_logging(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", "1")
    report = tmp_path / "report.json"
    screenshot = tmp_path / "hud.png"
    calls = []
    monkeypatch.setattr(
        app_main,
        "configure_logging",
        lambda: pytest.fail("workflow probe must not open the live product log"),
    )
    monkeypatch.setattr(probe, "main", lambda argv: calls.append(argv) or 7)

    result = app_main.main(
        [
            "--workflow-probe",
            "--probe-output",
            str(report),
            "--probe-screenshot",
            str(screenshot),
            "--probe-hotkey",
            "ctrl+alt+f11",
            "--probe-timeout",
            "60",
        ]
    )

    assert result == 7
    assert calls == [
        [
            "--packaged-mode",
            "--output",
            str(report),
            "--screenshot",
            str(screenshot),
            "--hotkey",
            "ctrl+alt+f11",
            "--timeout",
            "60.0",
        ]
    ]


def test_app_cli_target_mode_is_hidden_and_does_not_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION", "1")
    status = tmp_path / "target.json"
    calls = []
    monkeypatch.setattr(
        app_main,
        "configure_logging",
        lambda: pytest.fail("target helper must not open the live product log"),
    )
    monkeypatch.setattr(
        probe,
        "_run_target",
        lambda title, path: calls.append((title, path)) or 9,
    )

    result = app_main.main(
        [
            "--workflow-probe-target",
            "--probe-target-title",
            "Synthetic target",
            "--probe-target-status",
            str(status),
        ]
    )

    assert result == 9
    assert calls == [("Synthetic target", status)]


def test_probe_requires_fixed_synthetic_private_boundaries() -> None:
    assert probe.SYNTHETIC_PHRASE == "Echo Link packaged workflow test"
    assert "live_microphone_not_opened" in probe.PACKAGED_REQUIRED_CHECKS
    assert "isolated_data_dir" in probe.PACKAGED_REQUIRED_CHECKS
    assert "network_disabled" in probe.PACKAGED_REQUIRED_CHECKS
    assert "microphone_stream_ready" not in probe.PACKAGED_REQUIRED_CHECKS
    assert "direct_unicode_newline_inserted" not in probe.PACKAGED_REQUIRED_CHECKS
    assert "direct_mode_left_clipboard_untouched" in probe.PACKAGED_REQUIRED_CHECKS
    assert "clipboard_restored" not in probe.PACKAGED_REQUIRED_CHECKS
    assert len(probe.SOURCE_REQUIRED_CHECKS) == 27
    assert "microphone_stream_ready" in probe.SOURCE_REQUIRED_CHECKS
    assert "direct_unicode_newline_inserted" in probe.SOURCE_REQUIRED_CHECKS


def test_probe_constructs_inserter_with_saved_mode() -> None:
    source = inspect.getsource(probe._run_probe_impl)
    assert "WindowsTextInserter(settings.insertion_mode)" in source


def test_default_source_probe_keeps_live_microphone_mode(monkeypatch) -> None:
    args = probe._parser().parse_args([])
    calls = []
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "_run_in_isolated_environment",
        lambda _callback: pytest.fail("default source mode must keep its original path"),
    )
    monkeypatch.setattr(
        probe,
        "_run_probe_impl",
        lambda parsed, root, *, packaged_mode: calls.append(
            (parsed, root, packaged_mode)
        )
        or {"schema_version": 3},
    )

    assert probe.run_probe(args) == {"schema_version": 3}
    assert calls == [(args, None, False)]
    assert args.skip_microphone is False
    assert args.phrase == "Echo Link workflow test"


def test_packaged_probe_uses_isolated_wrapper(monkeypatch, tmp_path) -> None:
    args = probe._parser().parse_args(["--packaged-mode"])
    calls = []
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "_run_in_isolated_environment",
        lambda callback: callback(tmp_path),
    )
    monkeypatch.setattr(
        probe,
        "_run_probe_impl",
        lambda parsed, root, *, packaged_mode: calls.append(
            (parsed, root, packaged_mode)
        )
        or {"schema_version": 4},
    )

    assert probe.run_probe(args) == {"schema_version": 4}
    assert calls == [(args, tmp_path, True)]


def test_windowed_packaged_probe_does_not_require_stdout(monkeypatch) -> None:
    monkeypatch.setattr(probe, "run_probe", lambda _args: {"passed": True})
    monkeypatch.setattr(probe.sys, "stdout", None)

    assert probe.main([]) == 0
