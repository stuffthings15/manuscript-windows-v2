from __future__ import annotations

from pathlib import Path

from tools import animal_essay_corpus_inventory as corpus
from tools import animal_essay_corpus_probe as hud_probe
from tools.full_page_tone_quality_probe import TONES


EXPECTED_SIZES = (
    39937, 39892, 39935, 39944, 39887,
    39925, 39939, 39921, 39923, 39968,
    39969, 39967, 39920, 39926, 39986,
    39951, 39931, 39943, 39949, 39922,
)


def test_locked_inventory_has_all_twenty_exact_existing_word_files() -> None:
    records = corpus.load_inventory()

    assert corpus.validate_inventory(records) == ()
    assert tuple(record.index for record in records) == tuple(range(1, 21))
    assert tuple(record.size_bytes for record in records) == EXPECTED_SIZES
    assert all(record.extension == ".docx" for record in records)
    assert all(record.media_type == corpus.DOCX_FORMAT for record in records)
    assert all(record.archive_matches for record in records)
    assert all(record.paragraph_count == 10 for record in records)
    assert all(record.word_count > 500 for record in records)
    assert all(Path(record.path).is_absolute() for record in records)


def test_inventory_is_stable_and_matches_the_physical_hud_loader() -> None:
    first = corpus.load_inventory()
    second = corpus.load_inventory()
    hud_essays = hud_probe.load_essays(hud_probe.CORPUS)

    assert first == second
    assert tuple(record.page_id for record in first) == tuple(
        str(essay["page_id"]) for essay in hud_essays
    )
    assert tuple(record.text for record in first) == tuple(
        str(essay["text"]) for essay in hud_essays
    )


def test_complete_visible_matrix_is_twenty_essays_by_seven_modes() -> None:
    records = corpus.load_inventory()

    assert corpus.VISIBLE_TONES == TONES
    # Proofread plus the six visible tones, cut from seven on 2026-08-16.
    assert len(corpus.ALL_VISIBLE_MODES) == 7
    assert len(corpus.expected_case_matrix(records)) == 140


def test_current_physical_hud_probe_covers_proofread_but_not_visible_tones() -> None:
    records = corpus.load_inventory()
    current_cases = set(
        corpus.expected_case_matrix(records, corpus.PHYSICAL_HUD_MODES)
    )
    gaps = corpus.coverage_gaps(records, current_cases)

    assert len(current_cases) == 20
    assert len(gaps) == 120
    assert {mode for _page_id, mode in gaps} == set(corpus.VISIBLE_TONES)
    assert all(mode != "Proofread" for _page_id, mode in gaps)
