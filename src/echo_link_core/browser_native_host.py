from __future__ import annotations

import json
import re
import struct
import sys
import threading
import time
from typing import BinaryIO, Callable

from .polish import PolishEngine
from .polish_types import CorrectionRequest, PolishMode, PolishTask
from .selected_text import COMMANDS, PROFILES
from .subscription import (
    EntitlementClient,
    SubscriptionStateStore,
    subscription_enforcement_required,
    subscription_origin,
)


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_TEXT_CHARACTERS = 500_000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class NativeMessageError(ValueError):
    """A bounded Native Messaging protocol error safe to return to a browser."""


def read_native_message(
    stream: BinaryIO,
    *,
    max_bytes: int = MAX_MESSAGE_BYTES,
) -> dict[str, object] | None:
    header = stream.read(4)
    if header == b"":
        return None
    if len(header) != 4:
        raise NativeMessageError("Native message size header is incomplete.")
    size = struct.unpack("<I", header)[0]
    if size <= 0 or size > max_bytes:
        raise NativeMessageError("Native message size is invalid.")
    raw = stream.read(size)
    if len(raw) != size:
        raise NativeMessageError("Native message body is incomplete.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeMessageError("Native message JSON is invalid.") from exc
    if not isinstance(payload, dict):
        raise NativeMessageError("Native message JSON must be an object.")
    return payload


def write_native_message(
    stream: BinaryIO,
    payload: dict[str, object],
    *,
    max_bytes: int = MAX_MESSAGE_BYTES,
) -> None:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if not raw or len(raw) > max_bytes:
        raise NativeMessageError("Native response size is invalid.")
    stream.write(struct.pack("<I", len(raw)))
    stream.write(raw)
    stream.flush()


class ActiveSubscriptionGate:
    """Fail closed and cache only a recently verified active entitlement."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._active_until = 0.0

    def __call__(self) -> bool:
        if not subscription_enforcement_required():
            return True
        now = self._clock()
        with self._lock:
            if now < self._active_until:
                return True
            try:
                state = SubscriptionStateStore().load()
                if state is None:
                    return False
                result = EntitlementClient(subscription_origin()).validate(
                    state.license_key,
                    state.device_hash,
                    timeout=10.0,
                )
            except Exception:
                return False
            if not result.active:
                self._active_until = 0.0
                return False
            self._active_until = now + min(900, result.next_check_seconds)
            return True


class BrowserNativeHost:
    """Validate browser requests and return review-only local corrections."""

    def __init__(
        self,
        *,
        engine_factory: Callable[[], PolishEngine] = PolishEngine,
        subscription_gate: Callable[[], bool] | None = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._subscription_gate = subscription_gate or ActiveSubscriptionGate()
        self._engine: PolishEngine | None = None
        self._lock = threading.Lock()

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        request_id = payload.get("id")
        safe_id = request_id if isinstance(request_id, str) else ""
        try:
            request = _correction_request(payload)
        except NativeMessageError:
            return {"id": safe_id, "ok": False, "error": "invalid_request"}
        if not self._subscription_gate():
            return {
                "id": safe_id,
                "ok": False,
                "error": "active_subscription_required",
            }
        try:
            with self._lock:
                if self._engine is None:
                    self._engine = self._engine_factory()
                result = self._engine.analyze(request)
        except Exception:
            return {
                "id": safe_id,
                "ok": False,
                "error": "local_correction_failed",
            }
        return {
            "id": safe_id,
            "ok": True,
            "text": result.text,
            "edits": [
                {
                    "operation": edit.operation,
                    "original": edit.original,
                    "replacement": edit.replacement,
                    "original_start": edit.original_start,
                    "original_end": edit.original_end,
                }
                for edit in result.edits
            ],
            "message": result.message,
            "backend": result.plan.status_label,
            "used_llm": result.used_llm,
        }


def _correction_request(payload: dict[str, object]) -> CorrectionRequest:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise NativeMessageError("Native request id is invalid.")
    if payload.get("type") != "analyze":
        raise NativeMessageError("Native request type is invalid.")
    text = payload.get("text")
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > MAX_TEXT_CHARACTERS
    ):
        raise NativeMessageError("Native request text is invalid.")
    command = payload.get("command", "Proofread")
    if not isinstance(command, str) or command not in COMMANDS:
        raise NativeMessageError("Native request command is invalid.")
    profile = payload.get("profile", "General")
    if not isinstance(profile, str) or profile not in PROFILES:
        raise NativeMessageError("Native request profile is invalid.")
    mode = payload.get("mode", PolishMode.AUTO.value)
    if not isinstance(mode, str) or mode.strip().casefold() not in {
        value.value for value in PolishMode
    }:
        raise NativeMessageError("Native request mode is invalid.")
    language = payload.get("language", "")
    if not isinstance(language, str) or len(language) > 100:
        raise NativeMessageError("Native request language is invalid.")
    task = (
        PolishTask.PROOFREAD
        if command == "Proofread"
        else PolishTask.TONE
        if command
        in {
            "Formal",
            "Friendly",
            "Professional",
            "Diplomatic",
            "Empathetic",
            "Cooperative",
            "Assertive",
            "Caveman",
        }
        else PolishTask.REWRITE
    )
    return CorrectionRequest(
        text=text,
        task=task,
        mode=PolishMode.coerce(mode),
        command=command,
        profile=profile,
        instruction=COMMANDS[command],
        language=language,
        manual=True,
    )


def process_native_messages(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    host: BrowserNativeHost | None = None,
) -> int:
    bridge = host or BrowserNativeHost()
    while True:
        try:
            payload = read_native_message(input_stream)
        except NativeMessageError:
            write_native_message(
                output_stream,
                {"id": "", "ok": False, "error": "invalid_request"},
            )
            return 1
        if payload is None:
            return 0
        write_native_message(output_stream, bridge.handle(payload))


def main() -> int:
    input_stream = getattr(sys.stdin, "buffer", None)
    output_stream = getattr(sys.stdout, "buffer", None)
    if input_stream is None or output_stream is None:
        return 2
    return process_native_messages(input_stream, output_stream)


__all__ = [
    "ActiveSubscriptionGate",
    "BrowserNativeHost",
    "MAX_MESSAGE_BYTES",
    "MAX_TEXT_CHARACTERS",
    "NativeMessageError",
    "main",
    "process_native_messages",
    "read_native_message",
    "write_native_message",
]
