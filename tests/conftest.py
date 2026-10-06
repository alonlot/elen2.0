import pytest

from elen.config import DEFAULTS, deep_merge
from elen.llm.base import LLMResponse, ToolCall


class ScriptedProvider:
    """Fake model: returns the scripted responses in order and records requests."""

    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat(self, system, messages, tools=None):
        self.requests.append({"system": system, "messages": messages, "tools": tools})
        return self.responses.pop(0)


def call(tool_name, **args):
    return LLMResponse(tool_calls=[ToolCall(id=f"c_{tool_name}", name=tool_name, arguments=args)])


def say(text):
    return LLMResponse(text=text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("ELEN_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "cfg").mkdir()
    return tmp_path


@pytest.fixture
def make_core(env):
    from elen.core import Elen

    async def factory(responses, **overrides):
        cfg = deep_merge(DEFAULTS, overrides)
        core = Elen(cfg)
        core.brain = ScriptedProvider(responses)
        await core.start()
        return core

    return factory
