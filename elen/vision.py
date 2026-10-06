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
        data = base64.b64encode(Path(image).read_bytes()).decode()
        media = mimetypes.guess_type(str(image))[0] or "image/png"
        msg = {
            "role": "user",
            "content": [
                {"type": "image", "media_type": media, "data": data},
                {"type": "text", "text": question or "Describe what is on the screen."},
            ],
        }
        resp = await self.provider.chat(VISION_SYSTEM, [msg], None)
        return resp.text
