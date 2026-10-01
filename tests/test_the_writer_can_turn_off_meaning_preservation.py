"""The writer can turn meaning preservation off.

Asked for on 2026-09-30, three times: "it should fix the meaning as well",
"I don't care about preserving the intent, I just want it corrected properly;
remove that limitation", "Fully unlock the power". Until then every guard in
PolishValidator refused any answer that moved an actor, a modal, a negation,
a named entity or a paragraph boundary, and the owner read the corrected
corpus essay and said it did not make sense. It did not: the corpus is
written not to, and the guards kept it that way.

The switch is `faithful`. ON (the default) is every guard exactly as before.
OFF keeps only the failures that mean corruption rather than intent: empty
output, an em dash, a changed code span or protected token, and a runtime
that did not run. Everything else becomes an advisory, the actor and modal
restorers stand down, and the prompt tells the model the writer wants sense
over fidelity. Names, numbers, dates, URLs and code stay exact either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_polish_engine import FakeClassifier, FakeRuntime, engine  # noqa: E402
from test_selected_text import _Response  # noqa: E402

from echo_link_core.models import DictationSettings  # noqa: E402
from echo_link_core.polish import PolishWritingAssistantAdapter  # noqa: E402
from echo_link_core.polish_types import (  # noqa: E402
    CorrectionRequest,
    PolishMode,
    PolishTask,
)
from echo_link_core.selected_text import LocalWritingAssistant  # noqa: E402

SOURCE = "The owner feeds the dog every morning before work."
# A warm rewrite that swaps the actor: exactly what the actor guard refuses.
FREE = "You feed your dog every morning before work."


def _analyze(*, faithful: bool, outputs=(FREE, FREE)):
    runtime = FakeRuntime(outputs)
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    result = value.analyze(
        CorrectionRequest(
            SOURCE,
            task=PolishTask.TONE,
            mode=PolishMode.BALANCED,
            command="Friendly",
            manual=True,
            faithful=faithful,
        )
    )
    return result, runtime


def test_the_default_still_refuses_a_changed_actor():
    result, _ = _analyze(faithful=True)
    assert result.text != FREE
    assert result.rejected_llm


def test_turned_off_the_changed_actor_is_accepted_and_recorded_as_advisory():
    result, runtime = _analyze(faithful=False)
    assert result.text == FREE
    assert result.validation.accepted
    assert result.used_llm
    assert any("actor changed" in note for note in result.validation.advisories)
    # The flag travels to the model, so the prompt can say so too.
    assert runtime.generate_calls[0].faithful is False


def test_turned_off_an_empty_answer_is_still_refused():
    result, _ = _analyze(faithful=False, outputs=("", ""))
    assert result.text.strip()
    assert not result.used_llm


def test_the_setting_defaults_on_and_round_trips():
    assert DictationSettings().polish_faithful is True
    assert DictationSettings.from_dict({"polish_faithful": False}).polish_faithful is False
    assert DictationSettings.from_dict({}).polish_faithful is True
    restored = DictationSettings.from_dict(
        DictationSettings(polish_faithful=False).to_dict()
    )
    assert restored.polish_faithful is False


def test_the_adapter_passes_the_setting_into_every_request():
    runtime = FakeRuntime(("The owner feeds the dog every morning before work.",))
    value, _ = engine(classifier=FakeClassifier(), runtime=runtime)
    adapter = PolishWritingAssistantAdapter(
        value,
        mode_provider=lambda: PolishMode.BALANCED,
        consent_provider=lambda: True,
        faithful_provider=lambda: False,
    )
    adapter.run("The owner feed the dog every morning before work.", command="Proofread")
    assert runtime.generate_calls
    assert all(call.faithful is False for call in runtime.generate_calls)


def test_the_prompt_tells_the_model_when_preservation_is_off(monkeypatch):
    prompts = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompts.append(body["prompt"])
        return _Response({"response": "The owner feeds the dog every morning."})

    monkeypatch.setattr("echo_link_core.local_transport._open_request", fake_urlopen)
    assistant = LocalWritingAssistant()
    for faithful in (True, False):
        assistant.run(
            "The owner feed the dog every morning.",
            command="Friendly",
            model="local-model",
            defer_meaning_validation=True,
            faithful=faithful,
        )
    faithful_prompt, free_prompt = prompts[0], prompts[-1]
    assert "Preserve every fact" in faithful_prompt
    assert "turned off meaning preservation" not in faithful_prompt
    assert "turned off meaning preservation" in free_prompt
    assert "Preserve every fact" not in free_prompt
    # What stays hard either way.
    assert "Never use an em dash" in free_prompt
