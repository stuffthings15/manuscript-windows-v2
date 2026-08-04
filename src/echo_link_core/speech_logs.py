from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*|[.!?]")


def analyze_punctuation(
    raw_text: str,
    deterministic_text: str,
    suggested_text: str,
    final_text: str,
) -> dict[str, Any]:
    """Compare transcript stages and identify where internal periods appeared."""
    stages = {
        "raw": _internal_periods(raw_text),
        "deterministic": _internal_periods(deterministic_text),
        "suggested": _internal_periods(suggested_text),
        "final": _internal_periods(final_text),
    }
    raw_keys = set(stages["raw"])
    deterministic_keys = set(stages["deterministic"])
    suggested_keys = set(stages["suggested"])
    final_keys = set(stages["final"])
    issues: list[dict[str, Any]] = []
    for key, item in stages["raw"].items():
        issues.append({**item, "origin": "speech recognition", "kept_in_final": key in final_keys})
    for key, item in stages["deterministic"].items():
        if key not in raw_keys:
            issues.append({**item, "origin": "deterministic cleanup", "kept_in_final": key in final_keys})
    for key, item in stages["suggested"].items():
        if key not in deterministic_keys:
            issues.append({**item, "origin": "local refinement", "kept_in_final": key in final_keys})
    counts = {
        "speech_recognition": len(raw_keys),
        "deterministic_cleanup": len(deterministic_keys - raw_keys),
        "local_refinement": len(suggested_keys - deterministic_keys),
        "final_internal_periods": len(final_keys),
        "removed_during_review": len(suggested_keys - final_keys),
    }
    likely = max(
        ("speech recognition", counts["speech_recognition"]),
        ("deterministic cleanup", counts["deterministic_cleanup"]),
        ("local refinement", counts["local_refinement"]),
        key=lambda value: value[1],
    )
    if not issues:
        summary = "No internal periods were detected in this session."
    elif likely[1] == 0:
        summary = "Internal periods were detected, but their source was unclear."
    else:
        suffix = "s" if likely[1] != 1 else ""
        summary = f"Likely source: {likely[0]} ({likely[1]} internal period{suffix})."
    if counts["removed_during_review"]:
        summary += f" Review removed {counts['removed_during_review']}."
    return {"version": 1, "summary": summary, "counts": counts, "issues": issues}


def format_punctuation_analysis(analysis: dict[str, Any]) -> str:
    lines = [str(analysis.get("summary") or "No analysis is available.")]
    for issue in analysis.get("issues") or ():
        status = "kept" if issue.get("kept_in_final") else "removed"
        lines.append(
            f"• After “{issue.get('after', '')}” — {issue.get('origin', 'unknown')} "
            f"({status}); context: {issue.get('context', '')}"
        )
    return "\n".join(lines)


def _internal_periods(text: str) -> dict[str, dict[str, Any]]:
    tokens = _TOKEN_RE.findall(text or "")
    word_positions = [
        (index, token) for index, token in enumerate(tokens) if token not in {".", "!", "?"}
    ]
    total_words = len(word_positions)
    words_seen = 0
    occurrence: dict[str, int] = {}
    result: dict[str, dict[str, Any]] = {}
    for index, token in enumerate(tokens):
        if token not in {".", "!", "?"}:
            words_seen += 1
            continue
        if token != "." or words_seen <= 0 or words_seen >= total_words:
            continue
        previous = next((value for position, value in reversed(word_positions) if position < index), "")
        following = next((value for position, value in word_positions if position > index), "")
        base = f"{previous.casefold()}|{following.casefold()}"
        occurrence[base] = occurrence.get(base, 0) + 1
        key = f"{base}|{occurrence[base]}"
        context = _join_tokens(tokens[max(0, index - 3) : min(len(tokens), index + 4)])
        result[key] = {
            "after": previous,
            "before": following,
            "word_index": words_seen,
            "context": context,
        }
    return result


def _join_tokens(tokens: list[str]) -> str:
    return re.sub(r"\s+([.!?])", r"\1", " ".join(tokens))


__all__ = ["analyze_punctuation", "format_punctuation_analysis"]
