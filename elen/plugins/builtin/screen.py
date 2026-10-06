"""Screen capture and screen understanding."""

from __future__ import annotations

from elen.plugins import Plugin, ToolResult, tool


class ScreenPlugin(Plugin):
    name = "screen"
    description = "Capture and understand the screen."

    def prompt_hint(self) -> str:
        return (
            "You can look at the user's screen with screen__look_at_screen when the user refers to "
            "something visible ('this', 'what I see', an error on screen)."
        )

    @tool(
        "Take a screenshot of the whole screen and ask the vision model a question about it. "
        "Returns the vision model's answer.",
        params={"question": "string: what you want to know about the screen"},
    )
    async def look_at_screen(self, question: str = "Describe what is on the screen."):
        path = await self.ctx.screenshot()
        answer = await self.ctx.look(path, question)
        return {"screenshot": str(path), "vision_answer": answer}

    @tool(
        "Take a screenshot and save it. Optionally show it in the HUD.",
        params={"show": "boolean: show the screenshot on screen"},
    )
    async def take_screenshot(self, show: bool = False):
        path = await self.ctx.screenshot()
        visual = {"type": "image", "title": "Screenshot", "path": str(path), "caption": str(path)} if show else None
        return ToolResult(data={"path": str(path)}, visual=visual)
