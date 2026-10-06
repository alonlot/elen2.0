from elen.plugins.builtin.claude_code import describe_tool_use


def test_describe_tool_use():
    assert describe_tool_use({"name": "Bash", "input": {"command": "apt  install\nhtop"}}) == "Bash apt install htop"
    assert describe_tool_use({"name": "Edit", "input": {"file_path": "/a/b.txt"}}) == "Edit /a/b.txt"


async def test_claude_code_is_a_dangerous_tool_not_a_brain(make_core, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    core = await make_core([])
    spec = core.tools["claude_code__run_task"]
    assert spec.risk == "dangerous" and "task" in spec.editable
