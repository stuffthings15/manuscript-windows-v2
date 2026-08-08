"""The Writing HUD must name the model that is actually loaded.

Settings held gemma4:e2b-it-qat while the runtime had prewarmed the e4b pack,
so every HUD request named the smaller model. It could not finish the repair:
section 3 of essay 13 spent four retries and still returned subject-verb
agreement, the section was discarded, and the review stalled with eight
sections untouched. The same text with the loaded model corrects with zero
retries.
"""
from __future__ import annotations

from types import SimpleNamespace

from echo_link_app import window as window_module


def _stub(*, lease_model: str | None, stored: str):
    lease = (
        None
        if lease_model is None
        else SimpleNamespace(plan=SimpleNamespace(model=lease_model))
    )
    return SimpleNamespace(
        ai_coordinator=SimpleNamespace(lease=lambda: lease),
        engine=SimpleNamespace(settings=SimpleNamespace(ollama_model=stored)),
    )


def _model(stub) -> str:
    return window_module.EchoLinkWindow._accepted_writing_model(stub)


def test_the_loaded_model_wins_over_a_stale_setting():
    """The whole bug: the stored e2b name was used while e4b was loaded."""
    stub = _stub(lease_model="gemma4:e4b-it-qat", stored="gemma4:e2b-it-qat")
    assert _model(stub) == "gemma4:e4b-it-qat"


def test_the_stored_model_is_used_when_nothing_has_been_accepted():
    assert _model(_stub(lease_model=None, stored="gemma4:e2b-it-qat")) == (
        "gemma4:e2b-it-qat"
    )


def test_an_accepted_plan_with_no_model_falls_back_to_the_setting():
    assert _model(_stub(lease_model="", stored="gemma4:e2b-it-qat")) == (
        "gemma4:e2b-it-qat"
    )


def test_no_model_anywhere_is_the_empty_string():
    """Empty means "engine chooses", which is what corrected essay 13."""
    assert _model(_stub(lease_model=None, stored="")) == ""


def test_the_accepted_model_is_stripped():
    assert _model(_stub(lease_model="  gemma4:e4b-it-qat  ", stored="x")) == (
        "gemma4:e4b-it-qat"
    )
