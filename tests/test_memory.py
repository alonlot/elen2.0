import json

from conftest import ScriptedProvider, call, say


def tool_result(core, index):
    return json.loads(core.history.transcript[index]["content"])


def violation(ids, why):
    return say(json.dumps({"violates": True, "rule_ids": ids, "explanation": why}))


OK = say('{"violates": false, "rule_ids": [], "explanation": ""}')


async def test_rule_is_saved_and_in_every_later_prompt(make_core):
    core = await make_core(
        [call("memory__add_rule", rule="Never use emojis in replies.", mistake="Used emojis"),
         say("Understood. Rule saved."),
         say("Hello.")]
    )
    core.checker = ScriptedProvider([OK, OK])
    await core.ask("you used emojis, never do that again")
    rule = core.plugins["memory"].rules()[0]
    assert rule["text"] == "Never use emojis in replies."
    await core.ask("hi")
    system = core.brain.requests[-1]["system"]
    assert "PERMANENT RULES" in system and "Never use emojis in replies." in system


async def test_correction_hint_and_missing_rule_note(make_core):
    core = await make_core([say("Sorry.")])
    reply = await core.ask("that was wrong, don't do that again")
    assert "save it now with memory__add_rule" in core.brain.requests[0]["system"]
    assert "No permanent rule was saved" in reply
    assert core.history.display[-1]["meta"]["rule_not_saved"]


async def test_rules_survive_clear_and_restart(make_core, env):
    core = await make_core([call("memory__add_rule", rule="Always answer in English."), say("Saved.")])
    core.checker = ScriptedProvider([OK])
    await core.ask("from now on always answer in English")
    core.clear_history()
    core2 = await make_core([])
    assert core2.rules() and "Always answer in English." in core2.rules()[0]


async def test_low_action_that_breaks_rule_is_blocked(make_core):
    core = await make_core(
        [call("memory__add_rule", rule="Never open YouTube."), say("Saved."),
         call("system__open_link", target="https://youtube.com"), say("I will not open it.")]
    )
    core.checker = ScriptedProvider([OK, violation(["abc"], "Opening YouTube breaks the rule."), OK])
    await core.ask("never open youtube again")
    await core.ask("open youtube")
    result = tool_result(core, -2)
    assert result["status"] == "blocked_by_rule"
    assert "YouTube" in result["explanation"]


async def test_write_action_conflict_is_shown_in_dialog(make_core):
    core = await make_core(
        [call("memory__add_rule", rule="Never add contacts without a phone number."), say("Saved."),
         call("contacts__add_contact", name="Bob", emails=["bob@x.io"]), say("Not added.")]
    )
    core.checker = ScriptedProvider([OK, violation(["r1"], "No phone number given."), OK])
    seen = []

    def on_event(kind, payload):
        if kind == "confirm":
            seen.append(payload)
            core.resolve_confirmation(payload["id"], False)

    core.subscribe(on_event)
    await core.ask("never add contacts without a phone number")
    await core.ask("add bob")
    assert seen[0]["warnings"][0].startswith("RULE CONFLICT")


async def test_reply_that_breaks_rule_is_rewritten(make_core):
    core = await make_core(
        [call("memory__add_rule", rule="Never call me sir."), say("Saved."),
         say("Of course, sir."), say("Of course.")]
    )
    core.checker = ScriptedProvider([OK, violation(["r1"], "Uses 'sir'.")])
    await core.ask("never call me sir again")
    reply = await core.ask("thanks")
    assert reply == "Of course."
    assert core.history.transcript[-1]["content"] == "Of course."


async def test_deleting_a_rule_needs_approval(make_core):
    core = await make_core(
        [call("memory__add_rule", rule="Never use emojis."), say("Saved.")]
    )
    core.checker = ScriptedProvider([OK])
    await core.ask("never use emojis again")
    rid = core.plugins["memory"].rules()[0]["id"]
    core.brain = ScriptedProvider([call("memory__delete_rule", id=rid), say("Kept.")])
    core.checker = ScriptedProvider([OK])
    core.subscribe(lambda k, p: k == "confirm" and core.resolve_confirmation(p["id"], False))
    await core.ask("delete the emoji rule")
    assert core.plugins["memory"].rules()


async def test_remember_and_recall(make_core):
    core = await make_core(
        [call("memory__remember", fact="My wife's name is Dana."), say("Saved."),
         call("memory__recall", query="wife name"), say("Dana.")]
    )
    await core.ask("remember my wife is Dana")
    await core.ask("what is my wife's name?")
    matches = tool_result(core, -2)["result"]["matches"]
    assert matches[0]["text"] == "My wife's name is Dana."
    assert "Dana" in core.brain.requests[-1]["system"]
