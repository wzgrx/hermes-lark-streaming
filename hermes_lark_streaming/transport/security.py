"""Domain-separated callback proofs and bounded replay protection."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


def _canonical(domain: str, timestamp: int, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"hls1\n{domain}\n{timestamp}\n{nonce}\n{digest}".encode()


def sign_callback(
    secret: str, body: bytes, *, domain: str, timestamp: int | None = None, nonce: str | None = None
) -> str:
    """Return ``hls1:timestamp:nonce:hex-hmac`` for a callback payload."""
    if not secret:
        raise ValueError("callback secret is required")
    ts = int(time.time() if timestamp is None else timestamp)
    token = nonce or secrets.token_urlsafe(18)
    signature = hmac.new(secret.encode(), _canonical(domain, ts, token, body), hashlib.sha256).hexdigest()
    return f"hls1:{ts}:{token}:{signature}"


@dataclass(frozen=True)
class VerifiedCallback:
    timestamp: int
    nonce: str


class ReplayGuard:
    """Verify callback age, HMAC and nonce uniqueness with bounded memory."""

    def __init__(self, *, ttl_sec: int = 300, capacity: int = 4096) -> None:
        self.ttl_sec = max(1, ttl_sec)
        self.capacity = max(32, capacity)
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def verify(self, proof: str, body: bytes, *, secret: str, domain: str, now: int | None = None) -> VerifiedCallback:
        try:
            version, ts_raw, nonce, supplied = proof.split(":", 3)
            timestamp = int(ts_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed callback proof") from exc
        current = int(time.time() if now is None else now)
        if version != "hls1" or not nonce or abs(current - timestamp) > self.ttl_sec:
            raise ValueError("expired callback proof")
        expected = hmac.new(secret.encode(), _canonical(domain, timestamp, nonce, body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid callback proof")
        key = f"{domain}:{nonce}"
        with self._lock:
            cutoff = current - self.ttl_sec
            while self._seen and next(iter(self._seen.values())) < cutoff:
                self._seen.popitem(last=False)
            if key in self._seen:
                raise ValueError("callback replay detected")
            self._seen[key] = current
            while len(self._seen) > self.capacity:
                self._seen.popitem(last=False)
        return VerifiedCallback(timestamp=timestamp, nonce=nonce)
