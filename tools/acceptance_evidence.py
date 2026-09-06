"""Supplementary release evidence checks; no changes to quality scoring."""
from __future__ import annotations

from collections import Counter
import json
import hashlib
from pathlib import Path


def validate_batch(summary: Path, expected: set[str], output: Path, after: float) -> dict:
    """Require the requested documents and explicit completion, never HUD absence."""
    failures = []
    try:
        if summary.stat().st_mtime < after:
            return {"status": "invalid", "failures": ["stale batch summary"]}
        rows = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "invalid", "failures": ["missing or unreadable batch summary"]}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {"status": "invalid", "failures": ["invalid batch summary shape"]}
    names = [row.get("essay") for row in rows]
    if any(not isinstance(name, str) for name in names):
        return {"status": "invalid", "failures": ["invalid essay identity"]}
    counts = Counter(names)
    wanted = {Path(name).stem for name in expected}
    if not wanted:
        failures.append("empty expected corpus")
    for name in sorted(wanted):
        if counts[name] != 1:
            failures.append(f"{name}: expected one result, found {counts[name]}")
    for name in sorted(set(names) - wanted):
        failures.append(f"unexpected essay: {name}")
    if failures:
        return {"status": "invalid", "failures": failures}
    for row in rows:
        name = row["essay"]
        if row.get("error") or row.get("timed_out") or row.get("stalled_at"):
            failures.append(f"{name}: failed or interrupted")
        if not isinstance(row.get("presses"), int) or row["presses"] <= 0:
            failures.append(f"{name}: no accepted interaction")
        if row.get("dismissed"):
            failures.append(f"{name}: dismissed corrections need review")
        # Existing runner reads this statement from the app's current-run log.
        # A section N/N in a missing-HUD diagnostic is not an acknowledgement.
        if not str(row.get("stopped_because") or "").startswith("the review finished after press "):
            failures.append(f"{name}: explicit review completion missing")
        saved = row.get("saved_document")
        digest = row.get("saved_sha256")
        try:
            if not isinstance(saved, str) or not isinstance(digest, str):
                raise ValueError("missing saved artifact identity")
            artifact = Path(saved)
            if not artifact.is_file() or artifact.stat().st_mtime < after:
                raise ValueError("missing or stale saved artifact")
            data = artifact.read_bytes()
            if not data or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("saved artifact hash mismatch")
            graded = output / next(filename for filename in expected if Path(filename).stem == name)
            if graded.read_bytes() != data:
                raise ValueError("graded document differs from saved artifact")
        except (OSError, ValueError) as exc:
            failures.append(f"{name}: {exc}")
    for filename in sorted(expected):
        path = output / filename
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"{filename}: saved document missing")
    return {
        "status": "incomplete" if failures else "passed",
        "expected": len(wanted), "observed": len(rows), "failures": failures,
    }


def aggregate_tones(reports: list, expected: int, tone: str) -> dict:
    """Weight by judged sections, not by document averages or parsed stdout."""
    if not expected or len(reports) != expected:
        return {"status": "invalid"}
    for row in reports:
        if (not isinstance(row, dict) or row.get("error") or row.get("tone") != tone
                or type(row.get("sections")) is not int
                or type(row.get("passed")) is not int
                or not 0 <= row["passed"] <= row["sections"]
                or row["sections"] <= 0):
            return {"status": "invalid"}
    sections = sum(row["sections"] for row in reports)
    passed = sum(row["passed"] for row in reports)
    return {"status": "passed", "documents": expected, "sections": sections,
            "passed": passed, "percent": 100.0 * passed / sections}
