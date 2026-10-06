import textwrap

from elen.plugins import Plugin, PluginContext, tool
from elen.plugins.builtin.contacts import parse_vcf
from elen.visuals import normalise


class Demo(Plugin):
    name = "demo"

    @tool("Do it.", params={"a": "string: thing", "n": "integer: count"}, risk="low")
    async def do_it(self, a, n=1):
        return a * n


def test_tool_schema():
    (spec,) = Demo({}, None).tools()
    assert spec.full_name == "demo__do_it"
    assert spec.parameters["required"] == ["a"]
    assert spec.parameters["properties"]["n"]["type"] == "integer"
    assert spec.schema()["description"].startswith("[low]")


async def test_user_plugin_folder_is_loaded(make_core, env):
    folder = env / "cfg" / "plugins" / "hello"
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(textwrap.dedent('''
        from elen.plugins import Plugin, tool
        class Hello(Plugin):
            name = "hello"
            @tool("Greet.", params={"who": "string: name"})
            async def greet(self, who):
                return {"text": "hi " + who}
    '''))
    core = await make_core([])
    assert "hello__greet" in core.tools


async def test_disabled_plugin_not_loaded(make_core):
    core = await make_core([], plugins={"system": {"enabled": False}})
    assert "system" not in core.plugins
    assert "email" not in core.plugins  # off by default


def test_vcf():
    data = "BEGIN:VCARD\nFN:Dana Levi\nEMAIL;TYPE=work:dana@example.com\nTEL:+972 50 000 0000\nEND:VCARD\n"
    (c,) = parse_vcf(data)
    assert c["name"] == "Dana Levi" and c["emails"] == ["dana@example.com"]


def test_visual_normalise():
    v = normalise({"type": "text", "body": "x" * 9000})
    assert v["type"] == "text" and len(v["body"]) <= 4000
    p = normalise({"type": "panels", "panels": [{"type": "panels", "panels": [{}]}]})
    assert p["panels"][0]["panels"] == []


def test_unknown_visual_becomes_auto_data():
    v = normalise({"type": "person", "title": "Dana", "name": "Dana Levi", "kids": ["Noa", "Ari"]})
    assert v["type"] == "data" and v["title"] == "Dana"
    assert v["data"] == {"name": "Dana Levi", "kids": ["Noa", "Ari"]}


def test_data_any_json_is_trimmed():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}, "long": "x" * 5000, "many": list(range(100))}
    v = normalise({"type": "data", "data": deep})
    assert len(v["data"]["many"]) == 40 and len(v["data"]["long"]) <= 600
    assert isinstance(v["data"]["a"]["b"]["c"]["d"], str)  # depth limit


def test_card_and_chart():
    c = normalise({"type": "card", "title": "Dana", "fields": [{"label": "Email", "value": "d@x.io"}], "tags": ["VIP"]})
    assert c["fields"][0]["value"] == "d@x.io" and c["tags"] == ["VIP"]
    ch = normalise({"type": "chart", "kind": "line", "labels": ["a", "b"], "series": [{"name": "s", "values": [1, "2", "x"]}]})
    assert ch["series"][0]["values"] == [1.0, 2.0]


async def test_single_contact_shows_a_card(make_core, env):
    (env / "cfg" / "contacts.toml").write_text('[[contact]]\nname = "Dana Levi"\nemails = ["dana@example.com"]\n')
    core = await make_core([])
    res = await core.tools["contacts__find_contact"].func(query="dana")
    assert res.visual["type"] == "card" and res.visual["fields"][0]["value"] == "dana@example.com"
