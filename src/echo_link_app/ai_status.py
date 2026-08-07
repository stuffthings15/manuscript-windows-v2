from __future__ import annotations

import re


_AI_VENDOR_NAME = re.compile(
    r"\b(?:qwen\w*|gemma\w*|ollama|llama(?:\.cpp)?)\b",
    re.IGNORECASE,
)


def user_ai_status(message: str) -> str:
    """Keep low-level runtime and model names out of user-facing status text."""
    value = " ".join(str(message or "").split()).strip()
    if not value or _AI_VENDOR_NAME.search(value) is None:
        return value
    lowered = value.casefold()
    if "numeric loopback" in lowered:
        return "Local AI access requires a numeric loopback endpoint."
    if "ready" in lowered:
        backend = next(
            (
                name
                for name in ("CUDA", "Vulkan", "HIP", "SYCL", "CPU")
                if name.casefold() in lowered
            ),
            "",
        )
        suffix = " after automatic repair" if "repair" in lowered else ""
        return (
            f"Local AI | {backend} correction ready{suffix}."
            if backend
            else f"Local AI correction ready{suffix}."
        )
    if "load" in lowered:
        return "Local AI could not load. Retry shortly."
    if any(
        word in lowered
        for word in ("fail", "error", "stopped", "timeout", "unavailable")
    ):
        return "Local AI could not finish this section. Retry."
    return "Local AI status updated."


__all__ = ["user_ai_status"]
