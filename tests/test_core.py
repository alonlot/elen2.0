import asyncio
import json

from conftest import call, say

from elen.history import History


async def test_plain_answer_and_history_persist(make_core, env):
    core = await make_core([say("Good morning.")])
    assert await core.ask("hello") == "Good morning."
    h = History(env / "data" / "history.json")
    assert [m["role"] for m in h.display] == ["user", "assistant"]
    assert [m["role"] for m in h.transcript] == ["user", "assistant"]


async def test_history_is_context_until_cleared(make_core):
    core = await make_core([say("one"), say("two"), say("three")])
    await core.ask("first")
    await core.ask("second")
    msgs = core.brain.requests[1]["messages"]
    assert msgs[0]["content"] == "first" and msgs[-1]["content"] == "second"
    core.clear_history()
    await core.ask("third")
    assert len(core.brain.requests[2]["messages"]) == 1


async def test_visual_tool_emits_event(make_core):
    spec = {"type": "stats", "title": "T", "items": [{"label": "CPU", "value": 5}]}
    core = await make_core([call("ui__show_visual", visual=spec), say("Shown.")])
    events = []
    core.subscribe(lambda k, p: events.append((k, p)))
    await core.ask("show")
    visuals = [p for k, p in events if k == "visual"]
    assert visuals and visuals[0]["type"] == "stats"


async def test_write_tool_needs_approval_and_rejection_stops_it(make_core, env):
    (env / "cfg" / "contacts.toml").write_text(
        '[[contact]]\nname = "Dana Levi"\nemails = ["dana@example.com"]\n'
    )
    core = await make_core(
        [call("contacts__add_contact", name="Bob", emails=["bob@x.io"]), say("Okay, I did not add him.")]
    )
    confirms = []

    def on_event(kind, payload):
        if kind == "confirm":
            confirms.append(payload)
            core.resolve_confirmation(payload["id"], False, None, "no")

    core.subscribe(on_event)
    await core.ask("add bob")
    assert confirms and confirms[0]["risk"] == "write"
    tool_msg = core.history.transcript[2]
    assert json.loads(tool_msg["content"])["status"] == "rejected_by_user"
    assert "Bob" not in (env / "cfg" / "contacts.toml").read_text()


async def test_user_edit_is_used(make_core, env):
    core = await make_core([call("contacts__add_contact", name="Bob", emails=["bob@x.io"]), say("Added.")])

    def on_event(kind, payload):
        if kind == "confirm":
            args = dict(payload["arguments"], name="Robert")
            core.resolve_confirmation(payload["id"], True, json.dumps(args))

    core.subscribe(on_event)
    await core.ask("add bob")
    assert "Robert" in (env / "cfg" / "contacts.toml").read_text()
    result = json.loads(core.history.transcript[2]["content"])
    assert result["user_edited"] is True and result["final_arguments"]["name"] == "Robert"


async def test_action_claim_without_action_is_flagged(make_core):
    core = await make_core([say("I've sent the email to Dana.")])
    reply = await core.ask("email dana")
    assert "Guard" in reply
    assert core.history.display[-1]["meta"]["warning"]


async def test_unknown_tool_and_missing_args(make_core):
    core = await make_core([call("nope__x"), call("contacts__find_contact"), say("done")])
    await core.ask("x")
    t = core.history.transcript
    assert t[2]["is_error"] and "Unknown tool" in t[2]["content"]
    assert t[4]["is_error"] and "Missing" in t[4]["content"]


async def test_confirmation_timeout_rejects(make_core):
    core = await make_core(
        [call("system__run_command", command="echo hi"), say("Not run.")],
        guard={"confirm_timeout": 0.05},
    )
    await core.ask("run it")
    assert json.loads(core.history.transcript[2]["content"])["status"] == "rejected_by_user"


async def test_approved_dangerous_command_runs(make_core):
    core = await make_core([call("system__run_command", command="echo elen-ok"), say("Done.")])
    core.subscribe(lambda k, p: k == "confirm" and core.resolve_confirmation(p["id"], True, None))
    await core.ask("run it")
    result = json.loads(core.history.transcript[2]["content"])
    assert result["status"] == "done" and "elen-ok" in result["result"]["output"]


async def test_submit_returns_quickly(make_core):
    core = await make_core([say("hi")])
    rid = core.submit("hey")
    assert isinstance(rid, str)
    await asyncio.sleep(0.05)
    assert core.history.display[-1]["text"] == "hi"


async def test_visual_is_saved_and_can_be_opened_again(make_core, env):
    spec = {"type": "text", "title": "Note", "body": "hi"}
    core = await make_core([call("ui__show_visual", visual=spec), say("Shown.")])
    shown = []
    core.subscribe(lambda k, p: k == "visual" and shown.append(p))
    await core.ask("show a note")
    reply = core.history.display[-1]
    (ref,) = reply["meta"]["visuals"]
    assert ref["title"] == "Note" and ref["id"] == shown[0]["id"]
    # after a restart the saved visual is still there
    again = History(env / "data" / "history.json")
    assert again.visuals[ref["id"]]["body"] == "hi"
    assert core.reshow_visual(ref["id"]) is True and shown[-1]["id"] == ref["id"]
    assert core.reshow_visual("nope") is False
    core.clear_history()
    assert core.reshow_visual(ref["id"]) is False
