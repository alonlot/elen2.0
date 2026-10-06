import asyncio
import json

import httpx

from conftest import ScriptedProvider, call, say
from elen.llm.openai_provider import OpenAICompatProvider
from elen.plugins import Plugin, PluginContext, tool
from elen.tts import Speaker


def sse(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


async def test_openai_stream_text_and_tool_calls():
    body = sse([
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "system__get_datetime", "arguments": "{\"a\""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}, "finish_reason": "tool_calls"}]},
    ])
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    p = OpenAICompatProvider({"model": "m", "base_url": "http://x/v1"})
    p._transport = httpx.MockTransport(handler)
    pieces = []
    r = await p.chat("s", [{"role": "user", "content": "q"}], None, on_text=pieces.append)
    assert sent["body"]["stream"] is True
    assert pieces == ["Hel", "lo."] and r.text == "Hello."
    assert r.tool_calls[0].name == "system__get_datetime" and r.tool_calls[0].arguments == {"a": 1}


async def test_anthropic_stream():
    import anthropic

    try:
        import httpx2 as hx
    except ImportError:  # pragma: no cover
        hx = httpx
    from elen.llm.anthropic_provider import AnthropicProvider

    def ev(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    body = (
        ev("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": "x", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}})
        + ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        + ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Good "}})
        + ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "morning."}})
        + ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        + ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}})
        + ev("message_stop", {"type": "message_stop"})
    )
    p = AnthropicProvider({"model": "x", "api_key": "k", "fallbacks": False})
    p.client = anthropic.AsyncAnthropic(
        api_key="k",
        http_client=hx.AsyncClient(transport=hx.MockTransport(lambda r: hx.Response(200, text=body, headers={"content-type": "text/event-stream"}))),
    )
    pieces = []
    r = await p.chat("s", [{"role": "user", "content": "q"}], None, on_text=pieces.append)
    assert pieces == ["Good ", "morning."] and r.text == "Good morning."


async def test_core_emits_deltas_and_delta_end(make_core):
    core = await make_core([LLMText("Checking now.", call_name="system__get_datetime"), say("It is noon.")])
    events = []
    core.subscribe(lambda k, p: events.append((k, p)))
    await core.ask("time?")
    kinds = [k for k, _ in events]
    assert "delta" in kinds and "delta_end" in kinds
    first_end = kinds.index("delta_end")
    assert "".join(p["text"] for k, p in events[:first_end] if k == "delta") == "Checking now."


def LLMText(text, call_name):
    r = call(call_name)
    r.text = text
    return r


async def test_stop_cancels_a_running_tool(make_core):
    started = asyncio.Event()

    class Slow(Plugin):
        name = "slow"

        @tool("Takes forever.")
        async def wait(self):
            started.set()
            await asyncio.sleep(60)

    core = await make_core([call("slow__wait"), say("never")])
    plugin = Slow({}, PluginContext(core, "slow", core.data_dir))
    for spec in plugin.tools():
        core.tools[spec.full_name] = spec
    task = asyncio.create_task(core.ask("go"))
    await asyncio.wait_for(started.wait(), 2)
    assert core.cancel() is True
    reply = await asyncio.wait_for(task, 2)
    assert reply.startswith("Stopped")
    assert core.state == "idle" and core.history.transcript[-1]["content"] == "(stopped by the user)"
    # Elen still works after a stop
    core.brain = ScriptedProvider([say("Back.")])
    assert await core.ask("hi") == "Back."


async def test_speaker_speaks_sentence_by_sentence(tmp_path):
    out = tmp_path / "spoken.txt"
    sp = Speaker({"enabled": True, "command": ["sh", "-c", f'printf "%s|" "$0" >> {out}', "{text}"], "max_chars": 1000})
    sp.begin()
    sp.feed("Hello th")
    assert not out.exists()  # no complete sentence yet
    sp.feed("ere. How are")
    sp.feed(" you? Here is **bold** text")
    sp.flush()
    await sp._queue.join()
    assert out.read_text() == "Hello there.|How are you?|Here is bold text|"


async def test_speaker_skips_code_and_respects_budget(tmp_path):
    out = tmp_path / "s.txt"
    sp = Speaker({"enabled": True, "command": ["sh", "-c", f'printf "%s|" "$0" >> {out}', "{text}"], "max_chars": 12})
    await sp.say("Short one. This part is too long to speak.")
    assert out.read_text() == "Short one.|"
