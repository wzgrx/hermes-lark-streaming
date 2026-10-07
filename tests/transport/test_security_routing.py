from __future__ import annotations

import pytest

from hermes_lark_streaming.transport.routing import BotRegistry
from hermes_lark_streaming.transport.security import ReplayGuard, sign_callback


def test_callback_proof_rejects_replay_and_tampering() -> None:
    body = b'{"event":"done"}'
    proof = sign_callback("secret", body, domain="test", timestamp=100, nonce="nonce")
    guard = ReplayGuard(ttl_sec=30)
    assert guard.verify(proof, body, secret="secret", domain="test", now=100).nonce == "nonce"
    with pytest.raises(ValueError, match="replay"):
        guard.verify(proof, body, secret="secret", domain="test", now=100)
    with pytest.raises(ValueError):
        ReplayGuard(ttl_sec=30).verify(proof, b"changed", secret="secret", domain="test", now=100)


def test_callback_proof_rejects_expired() -> None:
    proof = sign_callback("secret", b"x", domain="test", timestamp=100, nonce="n")
    with pytest.raises(ValueError, match="expired"):
        ReplayGuard(ttl_sec=5).verify(proof, b"x", secret="secret", domain="test", now=106)


def test_exact_chat_multi_bot_routing(monkeypatch) -> None:
    monkeypatch.setenv("BOT_A_ID", "a")
    monkeypatch.setenv("BOT_A_SECRET", "sa")
    monkeypatch.setenv("BOT_B_ID", "b")
    monkeypatch.setenv("BOT_B_SECRET", "sb")
    registry = BotRegistry.from_config(
        {
            "default": "a",
            "chat_bindings": {"oc_exact": "b"},
            "items": {
                "a": {"app_id_env": "BOT_A_ID", "app_secret_env": "BOT_A_SECRET"},
                "b": {"app_id_env": "BOT_B_ID", "app_secret_env": "BOT_B_SECRET"},
            },
        }
    )
    assert registry.resolve("other").bot_id == "a"  # type: ignore[union-attr]
    assert registry.resolve("oc_exact").bot_id == "b"  # type: ignore[union-attr]
    report = registry.diagnostics()
    assert report["bot_count"] == 2
    assert "BOT_A_SECRET" in str(report)
    assert "sa" not in str(report)
