"""The part-of-speech table, and the promises the rules built on it rely on."""

import ast
from pathlib import Path

import pytest

from echo_link_core import polish_pos

SRC = Path(__file__).resolve().parents[1] / "src"


def test_inflection_evidence_gives_a_word_its_class():
    assert polish_pos.is_only("entertainment", "n")
    assert polish_pos.is_only("companionship", "n")
    assert polish_pos.is_only("quickly", "r")
    assert polish_pos.can_be("walked", "v")


def test_a_word_with_two_readings_keeps_both():
    # The reason this layer exists. "volunteer hours" is a noun phrase and
    # "they volunteer hours" is a clause; a table that picked one would make a
    # rule confident and wrong.
    assert polish_pos.tags("volunteer") == frozenset({"n", "v"})
    assert not polish_pos.is_only("volunteer", "n")
    assert not polish_pos.is_only("volunteer", "v")
    assert polish_pos.tags("needs") == frozenset({"n", "v"})


def test_an_unknown_word_is_never_reported_as_certain():
    assert polish_pos.tags("qwertyuiop") == frozenset()
    assert not polish_pos.is_only("qwertyuiop", "n")
    # can_be is the permissive direction: an unknown word could be anything,
    # so a rule asking "might this be a verb" gets yes and stays cautious.
    assert polish_pos.can_be("qwertyuiop", "v")


def test_closed_class_words_override_the_generated_table():
    # The generated table reports "the" as a verb and "is" as a noun, because
    # those entries carry affix flags that mean something else. Function words
    # are declared by hand for exactly this reason.
    assert polish_pos.tags("the") == frozenset({"det"})
    assert polish_pos.is_only("of", "prep")
    assert polish_pos.can_be("is", "aux")
    assert not polish_pos.is_only("the", "v")


@pytest.mark.parametrize("word", ("that", "which", "there"))
def test_the_words_that_broke_the_run_on_rule_stay_ambiguous(word):
    # "entertainment that is educational" was split into two sentences because
    # nothing recorded that "that" introduces a relative clause.
    assert len(polish_pos.tags(word)) > 1
    assert not polish_pos.is_only(word, "pron")


def test_a_missing_table_degrades_to_unknown_rather_than_failing(monkeypatch):
    monkeypatch.setattr(polish_pos, "_LEXICON", None)
    monkeypatch.setattr(
        polish_pos, "_lexicon_path", lambda: Path("no-such-directory/en_US.pos")
    )

    assert polish_pos.tags("entertainment") == frozenset()
    assert not polish_pos.is_only("entertainment", "n")

    monkeypatch.setattr(polish_pos, "_LEXICON", None)


def test_the_suggestion_engine_does_not_reach_this_module():
    # writing_suggestions.py is required to stay free of I/O so the HUD badge
    # stays fast. This table is 800KB read from disk, so importing it there
    # would break that invariant silently.
    tree = ast.parse((SRC / "echo_link_core" / "writing_suggestions.py").read_text())
    imported = {
        name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for name in node.names
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert "polish_pos" not in imported
    assert not any(name and "polish_pos" in name for name in modules)
