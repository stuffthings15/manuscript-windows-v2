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
    # NOT INSTALLED is its own answer, and it is the likeliest one.
    #
    # "Ollama is not installed or is not available on PATH" is what a fresh
    # machine says, and it fell through every branch below to the catch-all
    # "Local AI status updated." - which tells the writer nothing at all about
    # the most common first-run state there is. That undercut the whole point
    # of surfacing the rules-only reason in the first place.
    #
    # The runtime is still not named, per the rule this function exists for.
    # What the writer needs is that it is missing rather than broken, and that
    # there is somewhere to go about it.
    if any(
        phrase in lowered
        for phrase in (
            "not installed",
            "not available on path",
            "not found on path",
            "is not on path",
        )
    ):
        return (
            "Local AI is not installed. Corrections use built-in rules; "
            "open Settings to set it up."
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
