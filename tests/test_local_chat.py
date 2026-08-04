from __future__ import annotations

import json
from pathlib import Path

import pytest

from echo_link_core.local_chat import ApprovedSourceChat
from echo_link_core.local_sources import LocalSourceIndex


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return json.dumps(self.payload).encode("utf-8")


def _approved_index(tmp_path: Path) -> tuple[LocalSourceIndex, Path]:
    source = tmp_path / "approved.md"
    source.write_text(
        "Echo-Link privacy notes\n"
        "Speech stays on this computer during local dictation.\n"
        "Approved documents remain in the session memory index.\n",
        encoding="utf-8",
    )
    index = LocalSourceIndex()
    assert index.approve([source])[0].indexed
    return index, source.resolve()


def test_chat_uses_only_approved_excerpts_and_returns_structured_citations(
    tmp_path: Path,
    monkeypatch,
):
    index, path = _approved_index(tmp_path)

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        assert body["model"] == "qwen-local:latest"
        assert body["stream"] is False
        assert body["keep_alive"] == "300s"
        assert "Answer only from the approved excerpts" in body["prompt"]
        assert f"[S1] {path}:2" in body["prompt"]
        assert "Speech stays on this computer" in body["prompt"]
        assert timeout == 45.0
        return _Response({"response": "Speech stays local [S1]."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = ApprovedSourceChat().ask(
        "Where does speech stay during local dictation?",
        sources=index,
        model="qwen-local:latest",
    )

    assert result.answer == "Speech stays local [S1]."
    assert result.model == "qwen-local:latest"
    assert len(result.citations) == 1
    assert result.citations[0].source_id == "S1"
    assert result.citations[0].path == str(path)
    assert result.citations[0].line_number == 2
    assert result.citations[0].location == f"{path}:2"


def test_chat_requires_a_configured_local_model(tmp_path: Path):
    index, _path = _approved_index(tmp_path)
    with pytest.raises(ValueError, match="local writing model"):
        ApprovedSourceChat().ask("Where does speech stay?", sources=index, model=" ")


@pytest.mark.parametrize("question", ["", "   \n  "])
def test_chat_rejects_empty_questions(question: str, tmp_path: Path):
    index, _path = _approved_index(tmp_path)
    with pytest.raises(ValueError, match="question"):
        ApprovedSourceChat().ask(question, sources=index, model="local-model")


def test_chat_rejects_missing_approval_and_no_matching_excerpt(tmp_path: Path):
    empty_index = LocalSourceIndex()
    with pytest.raises(ValueError, match="Approve at least one"):
        ApprovedSourceChat().ask("What is private?", sources=empty_index, model="local")

    index, _path = _approved_index(tmp_path)
    with pytest.raises(ValueError, match="No approved source lines"):
        ApprovedSourceChat().ask("zebra quasar", sources=index, model="local")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:11434",
        "http://localhost:11434",
        "http://example.com:11434",
        "http://localhost.example.com:11434",
        "http://user@127.0.0.1:11434",
        "http://localhost:11434/api/generate",
    ],
)
def test_chat_rejects_every_non_loopback_or_prebuilt_endpoint(endpoint: str):
    with pytest.raises(ValueError, match="loopback"):
        ApprovedSourceChat(endpoint)


def test_chat_context_is_bounded_and_keeps_exact_locations(tmp_path: Path, monkeypatch):
    source = tmp_path / "many.txt"
    source.write_text(
        "\n".join(f"local detail {number} " + ("x" * 100) for number in range(20)),
        encoding="utf-8",
    )
    index = LocalSourceIndex(max_snippet_chars=500)
    index.approve([source])
    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _Response({"response": "Bounded [S1]."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    result = ApprovedSourceChat(max_excerpts=3, max_context_chars=300).ask(
        "local detail",
        sources=index,
        model="local",
    )

    assert 1 <= len(result.citations) <= 3
    source_context = "\n\n".join(
        f"[{item.source_id}] {item.location}\n{item.excerpt}" for item in result.citations
    )
    assert len(source_context) <= 300
    for item in result.citations:
        assert item.location in captured["prompt"]


def test_chat_does_not_retry_or_fall_back_after_local_failure(tmp_path: Path, monkeypatch):
    index, _path = _approved_index(tmp_path)
    calls = []

    def fail_once(request, timeout):
        calls.append(request.full_url)
        raise OSError("local service stopped")

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fail_once)
    with pytest.raises(RuntimeError, match="Local source chat model unavailable"):
        ApprovedSourceChat("http://127.0.0.1:11434").ask(
            "Where does speech stay?",
            sources=index,
            model="local",
        )

    assert calls == ["http://127.0.0.1:11434/api/generate"]


@pytest.mark.parametrize("payload", [{}, {"response": ""}, [], {"error": "bad model"}])
def test_chat_rejects_invalid_local_model_responses(tmp_path: Path, monkeypatch, payload):
    index, _path = _approved_index(tmp_path)
    monkeypatch.setattr(
        "echo_link_core.local_transport._open_request",
        lambda *_args, **_kwargs: _Response(payload),
    )
    with pytest.raises(RuntimeError, match="Local source chat model"):
        ApprovedSourceChat().ask(
            "Where does speech stay?",
            sources=index,
            model="local",
        )
