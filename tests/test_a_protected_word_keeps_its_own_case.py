"""A protected word comes back exactly as the writer wrote it at that spot.

Found 2026-09-30 in a Word run of essay 1, Friendly: "they're usually
speaking before Learning much about it" and "forces the owner to Learn more",
a capital in the middle of a sentence.

The chain: the proofread text of that tone batch opened a sentence with
"Learning from a mistake", the entity extractor kept "Learning" as a name
(its "appears lowercase later" rule only looks forward, and the lowercase
"learning" came earlier), and _runtime_opaque_entities protected it. The
protector matches case-insensitively, so it also swallowed the lowercase
"learning" in "before learning much", and every token came back as the
protected value's spelling: "Learning".

A protected token promises the writer's exact text. It must give back the
bytes it replaced, not the casing of whichever occurrence happened to be
harvested.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_selected_text import _Response  # noqa: E402

from echo_link_core.selected_text import LocalWritingAssistant  # noqa: E402

SOURCE = (
    "They usually speak before learning much about it. "
    "Learning from a mistake is better than protecting pride."
)


def _echo_the_model(monkeypatch):
    """A model that returns exactly the text it was given, tokens included."""

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        selected = body["prompt"].split("SELECTED TEXT:\n", 1)[1]
        return _Response({"response": selected})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)


def test_a_lowercase_occurrence_of_a_protected_word_stays_lowercase(monkeypatch):
    _echo_the_model(monkeypatch)
    result = LocalWritingAssistant().run(
        SOURCE,
        command="Proofread",
        model="local-model",
        protected_terms=("Learning",),
        defer_meaning_validation=True,
    )
    assert "before learning much" in result.text
    assert "Before Learning" not in result.text
    assert "before Learning" not in result.text
    # The occurrence that really was capitalised keeps its capital.
    assert "Learning from a mistake" in result.text


def test_a_protected_name_written_lowercase_by_the_writer_is_not_recapitalised(
    monkeypatch,
):
    # The protector's job is to copy, not to correct. Casing repair belongs to
    # the deterministic rules and the model, which see the word unprotected.
    _echo_the_model(monkeypatch)
    source = "Ask Malik first. Then ask malik again tomorrow."
    result = LocalWritingAssistant().run(
        source,
        command="Proofread",
        model="local-model",
        protected_terms=("Malik",),
        defer_meaning_validation=True,
    )
    assert result.text == source
