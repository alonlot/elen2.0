import json

import httpx

from elen.config import DEFAULTS, deep_merge
from elen.llm import make_provider
from elen.llm.openai_provider import OpenAICompatProvider
from elen.llm.prompt_tools import PromptToolsAdapter, parse_tool_calls, to_plain_messages

from conftest import ScriptedProvider, say

TOOLS = [{"name": "system__get_datetime", "description": "time", "parameters": {"type": "object", "properties": {}, "required": []}}]


def test_default_brain_is_generic():
    p = make_provider(DEFAULTS["brain"])
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://localhost:11434/v1"


def test_parse_tool_calls():
    text, calls = parse_tool_calls('Checking.\n```json\n{"tool_calls": [{"name": "a__b", "arguments": {"x": 1}}]}\n```', set())
    assert text == "Checking." and calls[0].name == "a__b" and calls[0].arguments == {"x": 1}
    text, calls = parse_tool_calls('{"tool_calls": [{"name": "a__b"}]}', set())
    assert calls and text == ""
    text, calls = parse_tool_calls("Plain answer.", set())
    assert text == "Plain answer." and not calls


def test_plain_messages():
    out = to_plain_messages([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "t", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "1", "name": "t", "content": "r1"},
        {"role": "tool", "tool_call_id": "2", "name": "u", "content": "r2"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert "r1" in out[2]["content"] and "r2" in out[2]["content"]


async def test_prompt_tools_adapter_runs_tools_with_any_model(make_core):
    inner = ScriptedProvider([say('```json\n{"tool_calls": [{"name": "system__get_datetime", "arguments": {}}]}\n```'),
                              say("It is Tuesday.")])
    core = await make_core([])
    core.brain = PromptToolsAdapter(inner)
    assert await core.ask("what day is it?") == "It is Tuesday."
    assert "# Tools" in inner.requests[0]["system"]
    assert "Tool result for system__get_datetime" in inner.requests[1]["messages"][-1]["content"]


async def test_auto_falls_back_to_prompt_tools():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "model does not support tools"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]})

    p = OpenAICompatProvider({"model": "tiny", "base_url": "http://x/v1"})
    p._transport = httpx.MockTransport(handler)
    r = await p.chat("sys", [{"role": "user", "content": "hi"}], TOOLS)
    assert r.text == "hello" and p.tool_mode == "prompt"
    assert "tools" not in seen[-1] and "# Tools" in seen[-1]["messages"][0]["content"]


async def test_headers_and_extra_body():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["x"] = request.headers.get("x-team")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    p = OpenAICompatProvider({"model": "m", "base_url": "http://x/v1", "api_key": "k",
                              "headers": {"X-Team": "a"}, "extra_body": {"temperature": 0.2}})
    p._transport = httpx.MockTransport(handler)
    await p.chat("s", [{"role": "user", "content": "q"}])
    assert seen["auth"] == "Bearer k" and seen["x"] == "a" and seen["body"]["temperature"] == 0.2


async def test_vision_and_checker_inherit_brain(make_core):
    core = await make_core([], brain={"provider": "openai_compatible", "model": "big", "base_url": "http://gw/v1"},
                           vision={"model": "big-vl"})
    assert core.model_config("vision")["base_url"] == "http://gw/v1"
    assert core.model_config("vision")["model"] == "big-vl"
    assert core.model_config("checker")["model"] == "big"
