"""Vision: ask the configured vision model about an image."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from .llm import make_provider

VISION_SYSTEM = (
    "You describe images of a computer screen for an assistant. Be precise and factual. "
    "Quote visible text exactly. If text is too small or unclear, say it is unreadable. "
    "Never guess content you cannot see."
)


class Vision:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._provider = None

    @property
    def provider(self):
        if self._provider is None:
            self._provider = make_provider(self.cfg)
        return self._provider

    async def describe(self, image: Path, question: str) -> str:
        return await self.ask([image], question or "Describe what is on the screen.")

    async def ask(self, images: list[Path], question: str, system: str = VISION_SYSTEM) -> str:
        """Ask about one or more images in one request (in this order)."""
        content: list[dict[str, Any]] = []
        for image in images:
            data = base64.b64encode(Path(image).read_bytes()).decode()
            media = mimetypes.guess_type(str(image))[0] or "image/png"
            content.append({"type": "image", "media_type": media, "data": data})
        content.append({"type": "text", "text": question})
        try:
            resp = await self.provider.chat(system, [{"role": "user", "content": content}], None)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"The vision model could not read the image ({e}). Use a model that accepts images "
                "in [vision], or set [vision] provider/model to a vision model."
            ) from e
        return resp.text
