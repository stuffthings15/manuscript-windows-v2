"""A release build must be a release build, not a relabelled test package.

Curtis asked on 2026-09-24 for the testing channel to stop being what ships.
It could not simply be renamed. Three separate things were welded to the
"testing-unlocked" marker, and only one of them was really about testing:

  1. whether the probe harness is in the archive   (correctly the channel's job)
  2. whether --workflow-probe is reachable at runtime (was keyed on subscription)
  3. whether subscription is enforced               (was keyed on the channel)

Because of (2) and (3), "not subscription-gated" could only be expressed by
marking the package as a test build. Separating them is what these cover, and
(2) is the one with teeth: had subscription been switched off while that gate
still read subscription_required, every shipped build would have exposed
keystroke synthesis, the microphone and a spawned process. That is the
2026-08-16 incident, which shipped exactly that way.
"""

from __future__ import annotations

import json

from echo_link_core.subscription import (
    is_testing_unlocked_build,
    subscription_enforcement_required,
)


def _manifest(tmp_path, **payload):
    path = tmp_path / "subscription.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_release_build_is_never_mistaken_for_a_test_build(tmp_path):
    release = _manifest(
        tmp_path, origin="https://x.invalid",
        subscription_required=False, build_channel="production",
    )
    assert is_testing_unlocked_build(release) is False
    # and it is genuinely unsubscribed, which is the whole point
    assert subscription_enforcement_required(release) is False


def test_only_the_exact_testing_marker_opens_the_probe(tmp_path):
    testing = _manifest(
        tmp_path, origin="https://x.invalid",
        subscription_required=False, build_channel="testing-unlocked",
    )
    assert is_testing_unlocked_build(testing) is True

    for channel in ("production", "TESTING-UNLOCKED", "testing", "", None):
        other = _manifest(
            tmp_path, origin="https://x.invalid",
            subscription_required=False, build_channel=channel,
        )
        assert is_testing_unlocked_build(other) is False, channel


def test_an_unreadable_manifest_keeps_the_probe_shut(tmp_path):
    """Fail closed the strict way: unknown build means no probe.

    The opposite of subscription, which fails closed by DEMANDING payment. For
    the probe the safe default is to refuse, so a corrupt or missing manifest
    must not be a way to open it.
    """
    missing = tmp_path / "gone.json"
    assert is_testing_unlocked_build(missing) is False

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert is_testing_unlocked_build(broken) is False

    # ... while subscription fails closed in its own direction
    assert subscription_enforcement_required(missing) is True
    assert subscription_enforcement_required(broken) is True


def test_a_half_written_manifest_does_not_unlock_anything(tmp_path):
    """subscription_required=false with no channel is a lost key, not a claim."""
    incomplete = _manifest(tmp_path, subscription_required=False)
    assert subscription_enforcement_required(incomplete) is True
    assert is_testing_unlocked_build(incomplete) is False
