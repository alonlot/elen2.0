import json

from conftest import call, say
from elen.guard import scope_value


def test_scope_value():
    assert scope_value("https://www.YouTube.com/watch?v=1") == "youtube.com"
    assert scope_value("Dana@Example.com, bob@x.io") == "bob@x.io, dana@example.com"
    assert scope_value("Firefox") == "firefox"


def auto_confirm(core, seen, remember=False, approve=True):
    def on_event(kind, payload):
        if kind == "confirm":
            seen.append(payload)
            core.resolve_confirmation(payload["id"], approve, None, "", remember)
    core.subscribe(on_event)


async def test_dont_ask_again_for_the_same_domain(make_core, env):
    (env / "cfg" / "contacts.toml").write_text("")
    core = await make_core(
        [call("contacts__add_contact", name="A"), say("ok"),
         call("contacts__add_contact", name="B"), say("ok")],
    )
    seen = []
    auto_confirm(core, seen, remember=True)
    await core.ask("add A")
    assert seen[0]["allow_label"] == "Add contact (any)"
    assert len(core.allow_rules.rules) == 1
    await core.ask("add B")
    assert len(seen) == 1  # the second time nobody was asked
    assert any("Allowed by your rule" in m["text"] for m in core.history.display)


async def test_rule_is_narrow_and_not_for_dangerous(make_core, env):
    core = await make_core([], guard={"overrides": {"system__open_link": "write"}})
    spec = core.tools["system__open_link"]
    scope = core.guard.allow_scope(spec, {"target": "https://youtube.com/x"})
    core.allow_rules.add(spec.full_name, scope, core.guard.scope_label(spec, scope))
    assert core.allow_rules.match(spec.full_name, core.guard.allow_scope(spec, {"target": "https://www.youtube.com/y"}))
    assert not core.allow_rules.match(spec.full_name, core.guard.allow_scope(spec, {"target": "https://evil.example/?d=1"}))
    assert core.guard.allow_scope(core.tools["system__run_command"], {"command": "ls"}) is None


async def test_unverified_recipient_never_auto_approved(make_core, env):
    core = await make_core([], plugins={"system": {"enabled": True}})
    from elen.plugins import Plugin, PluginContext, tool

    class Mail(Plugin):
        name = "mail"

        @tool("Send.", params={"to": "string", "body": "string"}, risk="write", recipients=["to"])
        async def send(self, to, body):
            return {"sent": True}

    for spec in Mail({}, PluginContext(core, "mail", core.data_dir)).tools():
        core.tools[spec.full_name] = spec
    spec = core.tools["mail__send"]
    scope = core.guard.allow_scope(spec, {"to": "ghost@nowhere.com"})
    core.allow_rules.add(spec.full_name, scope, "x")
    core.brain.responses = [call("mail__send", to="ghost@nowhere.com", body="hi"), say("not sent")]
    seen = []
    auto_confirm(core, seen, approve=False)
    await core.ask("send it")
    assert len(seen) == 1 and seen[0]["allow_label"] == ""  # asked, and no "don't ask again" offer
    assert json.loads(core.history.transcript[2]["content"])["status"] == "rejected_by_user"
