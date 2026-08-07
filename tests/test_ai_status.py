from echo_link_app.ai_status import user_ai_status


def test_user_ai_status_hides_runtime_and_model_names():
    values = (
        "CUDA | qwen3:8b correction ready after automatic repair.",
        "Gemma 4 12B failed to load.",
        "Ollama service unavailable.",
        "Bundled llama.cpp host stopped.",
    )

    sanitized = tuple(user_ai_status(value) for value in values)

    assert sanitized[0] == "Local AI | CUDA correction ready after automatic repair."
    assert sanitized[1] == "Local AI could not load. Retry shortly."
    assert sanitized[2:] == (
        "Local AI could not finish this section. Retry.",
        "Local AI could not finish this section. Retry.",
    )
    folded = " ".join(sanitized).casefold()
    assert all(name not in folded for name in ("qwen", "gemma", "ollama", "llama"))


def test_user_ai_status_preserves_safe_product_message():
    message = "Local AI correction ready."
    assert user_ai_status(message) == message


def test_user_ai_status_preserves_loopback_security_reason_without_vendor():
    message = "Local model access requires a numeric loopback Ollama endpoint."
    result = user_ai_status(message)
    assert result == "Local AI access requires a numeric loopback endpoint."
    assert "ollama" not in result.casefold()
