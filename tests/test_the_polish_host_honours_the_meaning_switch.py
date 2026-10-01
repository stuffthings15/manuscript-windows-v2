"""The meaning switch reaches the bundled polish host.

Found 2026-09-30 by tracing the running app: the HUD generates through the
polish host over its named pipe, not through LocalWritingAssistant. The
"Keep my meaning exactly" switch reached the host only through the
instruction string, while the host's own system prompt still said "Preserve
every ... actor, negation, obligation ... and factual relationship exactly.
Apply the smallest complete correction." on every request.

Two halves, tested where each one lives: the Python client must send the
flag, and the host executable the app loads must change its prompt for it.
The second runs the real binary on purpose. A test that reads Program.cs
would pass while an old executable sat in resources/, which is the
probe-in-the-archive mistake of 2026-09-24 again.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from test_polish_runtime import _generation_request, pipe_runtime, plan

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "resources" / "polish-host" / "Manuscript.PolishHost.exe"


@pytest.mark.parametrize("faithful", [True, False])
def test_the_pipe_client_sends_the_meaning_switch(monkeypatch, faithful):
    runtime = pipe_runtime()
    sent = []

    def capture(payload, *_args, **_kwargs):
        sent.append(payload)
        return {
            "ok": True,
            "text": "Dogs are good.",
            "model": plan().model,
            "backend": "cuda",
            "full_gpu_resident": True,
        }

    monkeypatch.setattr(runtime, "_exchange", capture)
    request = replace(_generation_request(plan()), faithful=faithful)
    runtime.generate(request)
    assert sent[0]["type"] == "generate"
    assert sent[0]["faithful"] is faithful


def _system_prompt(mode: str) -> str:
    if not HOST.is_file():
        pytest.skip("the polish host has not been built into resources/")
    completed = subprocess.run(
        [str(HOST), "--print-system-prompt", mode],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_the_host_keeps_the_faithful_prompt_by_default():
    prompt = _system_prompt("faithful")
    assert "Preserve every protected value" in prompt
    assert "turned off meaning preservation" not in prompt


def test_the_host_unlocks_its_prompt_when_the_writer_turns_the_switch_off():
    prompt = _system_prompt("free")
    assert "turned off meaning preservation" in prompt
    assert "Apply the smallest complete correction" not in prompt
    assert "actor, negation, obligation" not in prompt
    # What stays exact either way.
    assert "name, number, date, URL, path, and code span exact" in prompt
    assert "Return only the finished text" in prompt
