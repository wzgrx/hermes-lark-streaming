"""Markdown image resolution: download remote images, re-upload to Feishu, swap the URL for an img_key."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from ._fence import map_outside_fences

_logger = logging.getLogger("hermes_lark_streaming")

_IMG_PATTERN = re.compile(r"!\[(.*?)\]\((https?://[^\s)]+)\)")


class ImageUploader(Protocol):
    def upload_image(self, image_url: str) -> Awaitable[str | None]: ...


class ImageResolver:
    """Resolves ``![alt](https://...)`` references to ``![alt](img_key)``.

    ``resolve_images`` is synchronous: cache hits are replaced, unknown URLs are stripped from the current
    frame and uploaded in the background (``on_image_resolved`` fires when one lands). Failed URLs are
    never retried. Fenced code examples are neither uploaded nor rewritten.
    """

    def __init__(
        self,
        client: ImageUploader,
        timeout: float = 15.0,
        on_image_resolved: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._on_image_resolved = on_image_resolved
        self._cache: dict[str, str] = {}  # url -> img_key
        self._pending: dict[str, asyncio.Task[str | None]] = {}
        self._failed: set[str] = set()

    def resolve_images(self, text: str) -> str:
        if "![" not in text:
            return text

        def replace(m: re.Match[str]) -> str:
            alt, url = m.group(1), m.group(2)
            if url.startswith("img_"):
                return m.group(0)
            if url in self._cache:
                return f"![{alt}]({self._cache[url]})"
            if url in self._failed or url in self._pending:
                return ""
            self._start_upload(url)
            return ""

        return map_outside_fences(text, lambda prose: _IMG_PATTERN.sub(replace, prose))

    async def resolve_await(self, text: str) -> str:
        """Wait (bounded by ``timeout``) for pending uploads, then resolve; used for the terminal card."""
        self.resolve_images(text)
        if self._pending:
            _logger.info("image_resolver: waiting for %d uploads", len(self._pending))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._pending.values(), return_exceptions=True), timeout=self._timeout
                )
            except TimeoutError:
                _logger.warning("image_resolver: timeout waiting for uploads")
        return self.resolve_images(text)

    def cancel_pending(self) -> None:
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()

    def _start_upload(self, url: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending[url] = loop.create_task(self._do_upload(url))

    async def _do_upload(self, url: str) -> str | None:
        try:
            img_key = await self._client.upload_image(url)
            if img_key:
                self._cache[url] = img_key
                if self._on_image_resolved:
                    self._on_image_resolved()
                return img_key
            self._failed.add(url)
            return None
        except Exception:
            _logger.debug("image_resolver: upload failed", exc_info=True)
            self._failed.add(url)
            return None
        finally:
            self._pending.pop(url, None)
