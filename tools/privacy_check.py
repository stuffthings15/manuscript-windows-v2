from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echo_link_core.paths import data_root
from echo_link_core.data_protection import audit_persisted_text_schema
from echo_link_core.storage import EchoLinkStore
from echo_link_core.transcription import local_model_path


URL_PATTERN = re.compile(r"https?://[^\s\"']+")
ALLOWED_ENDPOINTS = ("http://127.0.0.1", "http://localhost")
ALLOWED_REMOTE_ENDPOINTS = {
    Path("src/echo_link_core/subscription.py"): {"https://{parsed.hostname}"},
    Path("src/echo_link_core/update_check.py"): {"https://{parsed.hostname}"},
}
BANNED_IMPORTS = (
    "import openai",
    "from openai",
    "import anthropic",
    "from anthropic",
    "google.generativeai",
)
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


def scan_source(root: Path = SRC) -> list[str]:
    failures: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        for value in BANNED_IMPORTS:
            if value in lowered:
                failures.append(f"Hosted AI import in {path.relative_to(ROOT)}: {value}")
        for endpoint in URL_PATTERN.findall(text):
            relative = path.relative_to(ROOT)
            approved_remote = endpoint in ALLOWED_REMOTE_ENDPOINTS.get(relative, set())
            if not endpoint.startswith(ALLOWED_ENDPOINTS) and not approved_remote:
                failures.append(f"Non-local endpoint in {path.relative_to(ROOT)}: {endpoint}")
    return failures


def inspect_local_data() -> list[str]:
    failures: list[str] = []
    root = data_root()
    if root.exists():
        audio_files = [path for path in root.rglob("*") if path.suffix.casefold() in AUDIO_SUFFIXES]
        if audio_files:
            failures.append(f"Retained audio found in Manuscript data: {audio_files[0]}")

    store = EchoLinkStore()
    security = store.database_security_status()
    if security.get("encrypted") is not True:
        failures.append("Manuscript database is not encrypted.")
    if security.get("key_backend") != "Windows DPAPI current user":
        failures.append("Manuscript database key is not protected for the current Windows user.")
    if security.get("temp_store_memory") is not True:
        failures.append("Manuscript SQLCipher temporary storage is not memory-only.")
    with store._connect() as connection:
        failures.extend(audit_persisted_text_schema(connection))
        feedback_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(review_feedback)").fetchall()
        }
        recovery_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(recovery_items)").fetchall()
        }
        evaluation_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(evaluation_trials)").fetchall()
        }
        learning_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(learned_corrections)").fetchall()
        }
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        revision_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(document_revisions)").fetchall()
        }
    forbidden = {"raw_text", "final_text", "transcript", "audio"}.intersection(feedback_columns)
    if forbidden:
        failures.append(f"Review feedback stores private content columns: {sorted(forbidden)}")
    recovery_forbidden = {"raw_text", "audio", "audio_path", "transcript"}.intersection(
        recovery_columns
    )
    if recovery_forbidden:
        failures.append(f"Recovery stores forbidden private columns: {sorted(recovery_forbidden)}")
    evaluation_forbidden = {"audio", "audio_path", "audio_blob"}.intersection(
        evaluation_columns
    )
    if evaluation_forbidden:
        failures.append(f"Evaluation stores audio columns: {sorted(evaluation_forbidden)}")
    learning_forbidden = {
        "raw_text",
        "final_text",
        "transcript",
        "audio",
        "audio_path",
        "window_title",
        "application_name",
    }.intersection(learning_columns)
    if learning_forbidden:
        failures.append(
            f"Learned corrections store forbidden context columns: {sorted(learning_forbidden)}"
        )
    document_forbidden = {
        "audio",
        "audio_path",
        "recording_path",
        "remote_url",
        "source_path",
        "network_path",
        "endpoint",
    }.intersection(document_columns | revision_columns)
    if document_forbidden:
        failures.append(
            f"Documents store forbidden audio or remote-location columns: {sorted(document_forbidden)}"
        )
    return failures


def main() -> int:
    failures = scan_source() + inspect_local_data()
    if local_model_path("base.en") is None:
        failures.append("Bundled local base.en speech model is missing.")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        "Privacy check passed: only local AI plus the approved subscription endpoint, no retained audio, metadata-only feedback, "
        "phrase-only opt-in learning, encrypted database/WAL storage, and local document "
        "revisions without remote-location fields."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
