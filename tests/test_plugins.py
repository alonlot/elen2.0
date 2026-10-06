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
    v = normalise({"type": "weird", "body": "x" * 9000})
    assert v["type"] == "text" and len(v["body"]) <= 4000
    p = normalise({"type": "panels", "panels": [{"type": "panels", "panels": [{}]}]})
    assert p["panels"][0]["panels"] == []
