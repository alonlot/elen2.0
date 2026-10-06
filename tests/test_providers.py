from elen.llm.anthropic_provider import to_anthropic_messages
from elen.llm.openai_provider import to_openai_messages

MSGS = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "name": "x__y", "arguments": {"q": 1}},
        {"id": "b", "name": "x__z", "arguments": {}},
    ]},
    {"role": "tool", "tool_call_id": "a", "name": "x__y", "content": "1"},
    {"role": "tool", "tool_call_id": "b", "name": "x__z", "content": "2", "is_error": True},
    {"role": "assistant", "content": "done"},
]


def test_anthropic_merges_tool_results():
    out = to_anthropic_messages(MSGS)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
    results = out[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert results[1]["is_error"] is True
    assert out[1]["content"][0]["type"] == "tool_use"


def test_anthropic_replays_raw_content():
    raw = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "x"}]
    out = to_anthropic_messages([{"role": "user", "content": "q"},
                                 {"role": "assistant", "content": "x", "_raw": {"provider": "anthropic", "content": raw}}])
    assert out[1]["content"] == raw


def test_openai_format():
    out = to_openai_messages("sys", MSGS)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"q": 1}'
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "a"


def test_image_parts():
    msg = [{"role": "user", "content": [{"type": "image", "media_type": "image/png", "data": "AA"}, {"type": "text", "text": "?"}]}]
    assert to_anthropic_messages(msg)[0]["content"][0]["source"]["data"] == "AA"
    assert to_openai_messages("s", msg)[1]["content"][0]["image_url"]["url"].startswith("data:image/png")


async def test_anthropic_request_and_parse():
    import json as _json

    import anthropic

    try:  # anthropic 1.x uses httpx2
        import httpx2 as httpx
    except ImportError:  # pragma: no cover
        import httpx

    from elen.llm.anthropic_provider import AnthropicProvider

    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = _json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tu_1", "name": "calendar__get_events", "input": {"start_date": "today"}},
            ],
        })

    p = AnthropicProvider({"model": "claude-opus-5-5", "api_key": "sk-test", "effort": "low", "fallbacks": True, "max_tokens": 100})
    p.client = anthropic.AsyncAnthropic(api_key="sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    tools = [{"name": "calendar__get_events", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    r = await p.chat("sys", [{"role": "user", "content": "hi"}], tools)
    assert seen["body"]["output_config"] == {"effort": "low"}
    assert seen["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert seen["body"]["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert r.text == "Checking." and r.tool_calls[0].arguments == {"start_date": "today"}
    assert r.raw["content"][1]["type"] == "tool_use"
