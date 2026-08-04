from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_subscription_release_documents_match_paypal_runtime() -> None:
    audit = (ROOT / "docs" / "SUBSCRIPTION_SECURITY_AUDIT.md").read_text(
        encoding="utf-8"
    )
    readiness = (ROOT / "docs" / "SALE_READINESS.md").read_text(encoding="utf-8")
    threat_model = (ROOT / "docs" / "SECURITY_THREAT_MODEL.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((audit, readiness, threat_model))

    assert "PayPal" in audit
    assert "PAYPAL_CLIENT_SECRET" in audit
    assert "ACTIVE" in audit
    assert "$5 USD" in readiness
    assert "PayPal subscription API" in threat_model
    assert "Stripe" not in combined
