import sys

from elen.config_edit import apply_model_settings, get_model_settings, set_dotted, set_values

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def test_set_values_keeps_comments(env):
    path = env / "cfg" / "config.toml"
    path.write_text('# top\n[brain]\nprovider = "anthropic"  # which\nmodel = "x"\n\n[ui]\nvisual_seconds = 5\n')
    set_values("brain", {"model": "opus", "base_url": "https://gw.example"}, path)
    text = path.read_text()
    assert "# top" in text and "# which" in text
    data = tomllib.loads(text)
    assert data["brain"] == {"provider": "anthropic", "model": "opus", "base_url": "https://gw.example"}
    assert data["ui"]["visual_seconds"] == 5


def test_apply_and_read_models(env):
    (env / "cfg" / "config.toml").write_text("[brain]\n")
    apply_model_settings({"brain": {"provider": "openai_compatible", "model": "my-model", "base_url": "http://localhost:4000/v1"},
                          "checker": {"provider": "", "model": ""}})
    got = get_model_settings()
    assert got["brain"]["model"] == "my-model" and got["brain"]["base_url"] == "http://localhost:4000/v1"
    assert got["checker"]["provider"] == ""
    assert any(p["name"].startswith("Ollama") for p in got["presets"])


def test_set_dotted(env):
    (env / "cfg" / "config.toml").write_text("")
    set_dotted("brain.timeout", "900")
    set_dotted("brain.builtin_tools", '["WebSearch"]')
    data = tomllib.loads((env / "cfg" / "config.toml").read_text())
    assert data["brain"] == {"timeout": 900, "builtin_tools": ["WebSearch"]}
