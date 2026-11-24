from __future__ import annotations

import importlib.util
from pathlib import Path
import tomllib
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_openfang_agents.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_openfang_agents", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure_registry(module, tmp_path, monkeypatch, contents: str) -> Path:
    registry = tmp_path / "space_agent_registry.yml"
    registry.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(module, "REGISTRY", registry)
    monkeypatch.setattr(module, "AGENTS_DIR", tmp_path / "agents")
    return registry


def test_registry_mcp_authority_requires_allowed_server_and_provenance_contract(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch,
        """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [vibemind-db]
    events:
      idea.create:
        tool: db_ideas_create
        execution:
          kind: mcp
          server: spaces-ideas
""")

    errors = module.validate_mcp_authority()

    assert any("mcp_servers" in error for error in errors)
    assert any("mcp_tools" in error for error in errors)
    assert any("required_provenance" in error for error in errors)


def test_sync_emits_top_level_agent_manifest_mcp_servers(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [vibemind-db, spaces-ideas]
    events: {}
""")

    assert module.sync() == 0
    document = tomllib.loads(
        (tmp_path / "agents" / "brain-ideas" / "agent.toml").read_text(
            encoding="utf-8"
        )
    )

    assert document["mcp_servers"] == ["vibemind-db", "spaces-ideas"]
    assert "mcp_allowed" not in document


def test_sync_emits_only_declared_normalized_mcp_tool_capabilities(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas]
    mcp_tools: { spaces-ideas: [zeta-tool, idea-connect, idea-connect] }
    events: {}
""")

    assert module.sync() == 0
    document = tomllib.loads(
        (tmp_path / "agents" / "brain-ideas" / "agent.toml").read_text(
            encoding="utf-8"
        )
    )

    assert document["capabilities"]["tools"] == [
        "memory_store",
        "memory_recall",
        "mcp_spaces_ideas_idea_connect",
        "mcp_spaces_ideas_zeta_tool",
    ]
    assert "mcp_spaces_ideas_idea_delete" not in document["capabilities"]["tools"]
    assert "mcp_foreign_server_foreign_tool" not in document["capabilities"]["tools"]
    assert "mcp_allowed" not in document


def test_mcp_tool_name_normalizes_hyphens_and_case():
    module = _load_sync_module()

    assert (
        module.format_mcp_tool_name("Spaces-Ideas", "Idea-Connect")
        == "mcp_spaces_ideas_idea_connect"
    )


def test_sync_fails_closed_for_tool_name_normalization_collision(
    tmp_path, monkeypatch, capsys
):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas]
    mcp_tools: { spaces-ideas: [idea-connect, idea_connect] }
    events: {}
""")

    assert module.sync() == 1
    output = capsys.readouterr().out
    assert "normalization collision" in output
    assert "idea-connect" in output
    assert "idea_connect" in output
    assert "mcp_spaces_ideas_idea_connect" in output


def test_sync_fails_closed_for_server_name_normalization_collision(
    tmp_path, monkeypatch, capsys
):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas, spaces_ideas]
    mcp_tools:
      spaces-ideas: [idea_connect]
      spaces_ideas: [idea_connect]
    events: {}
""")

    assert module.sync() == 1
    output = capsys.readouterr().out
    assert "normalization collision" in output
    assert "spaces-ideas" in output
    assert "spaces_ideas" in output
    assert "mcp_spaces_ideas_idea_connect" in output


def test_sync_fails_closed_for_mcp_tools_outside_the_server_scope(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas]
    mcp_tools: { foreign-server: [idea_connect] }
    events: {}
""")

    assert module.sync() == 1


def test_sync_fails_closed_for_empty_mcp_tool_name(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas]
    mcp_tools: { spaces-ideas: [""] }
    events: {}
""")

    assert module.sync() == 1


def test_external_runtime_may_explicitly_disable_agent_generation(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  agentfarm:
    agent: null
    enabled: true
    generate_agent: false
""")

    assert module.validate_agent_generation_contract() == []


def test_enabled_space_without_agent_requires_explicit_generation_opt_out(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  agentfarm:
    agent: null
    enabled: true
""")

    assert module.validate_agent_generation_contract() == [
        "agentfarm requires non-empty agent when generate_agent is true"
    ]


def test_generation_opt_out_rejects_named_agent(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  agentfarm:
    agent: brain-agentfarm
    enabled: true
    generate_agent: false
""")

    assert module.validate_agent_generation_contract() == [
        "agentfarm must omit agent or set it to null when generate_agent is false"
    ]


def test_generate_agent_must_be_boolean(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  agentfarm:
    agent: null
    enabled: true
    generate_agent: "false"
""")

    assert module.validate_agent_generation_contract() == [
        "agentfarm generate_agent must be a boolean"
    ]


def test_sync_skips_explicit_external_runtime_without_manifest(
    tmp_path, monkeypatch, capsys
):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  agentfarm:
    agent: null
    enabled: true
    generate_agent: false
    events: {}
""")

    assert module.sync(check=True) == 0
    output = capsys.readouterr().out
    assert "external runtime (generation disabled)" in output
    assert not (tmp_path / "agents").exists()


def test_real_agentfarm_registry_explicitly_disables_agent_generation():
    module = _load_sync_module()
    with open(module.REGISTRY, "r", encoding="utf-8") as f:
        data = module.yaml.safe_load(f)
    agentfarm = data["spaces"]["agentfarm"]

    assert agentfarm["generate_agent"] is False
    assert agentfarm["agent"] is None
    assert module._skip_reason("agentfarm", agentfarm) == (
        "external runtime (generation disabled)"
    )


def test_enabled_generated_agent_requires_non_empty_mcp_servers(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: []
    events: {}
""")

    errors = module.validate_generated_agent_mcp_scopes()

    assert errors == ["ideas requires non-empty mcp_servers for generated agent brain-ideas"]


def test_explicit_no_mcp_space_may_generate_an_empty_manifest_scope(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  archive:
    agent: brain-archive
    enabled: true
    no_mcp: true
    mcp_servers: []
    events: {}
""")

    assert module.validate_generated_agent_mcp_scopes() == []
    assert module.sync() == 0
    document = tomllib.loads(
        (tmp_path / "agents" / "brain-archive" / "agent.toml").read_text(
            encoding="utf-8"
        )
    )
    assert document["mcp_servers"] == []


def test_model_block_keeps_template_defaults_without_an_override(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [spaces-ideas]
    events: {}
""")

    assert module.sync() == 0
    document = tomllib.loads(
        (tmp_path / "agents" / "brain-ideas" / "agent.toml").read_text(
            encoding="utf-8"
        )
    )

    model = document["model"]
    assert model["provider"] == "openai"
    assert model["model"] == "gpt-4o-mini"
    assert model["max_tokens"] == 4096
    assert model["temperature"] == 0.2


def test_model_override_is_emitted_per_space(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  research:
    agent: brain-researcher
    enabled: true
    mcp_servers: [fetch]
    model: { model: gpt-4o, max_tokens: 16384 }
    events: {}
""")

    assert module.sync() == 0
    document = tomllib.loads(
        (tmp_path / "agents" / "brain-researcher" / "agent.toml").read_text(
            encoding="utf-8"
        )
    )

    model = document["model"]
    assert model["provider"] == "openai"
    assert model["model"] == "gpt-4o"
    assert model["max_tokens"] == 16384
    assert model["temperature"] == 0.2


ABO = """version: 1
spaces:
  research:
    agent: brain-researcher
    enabled: true
    mcp_servers: [fetch]
    model: {modell}
    events: {{}}
"""


def test_abo_modell_traegt_den_wrapper_ins_manifest(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, ABO.format(modell=(
        "{ provider: claude-code, model: claude-code/sonnet, "
        "base_url: openfang_claude_subscription_wrapper.cmd }")))

    assert module.sync() == 0
    raw = (tmp_path / "agents" / "brain-researcher" / "agent.toml").read_text(encoding="utf-8")
    model = tomllib.loads(raw)["model"]
    assert model["provider"] == "claude-code" and model["model"] == "claude-code/sonnet"
    assert model["base_url"] == "openfang_claude_subscription_wrapper.cmd"
    assert "api_key_env" not in model and "openai" not in raw.lower() and "openrouter" not in raw.lower()


def test_ohne_base_url_kein_wrapper_im_manifest(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, ABO.format(modell="{ model: gpt-4o }"))
    assert module.sync() == 0
    raw = (tmp_path / "agents" / "brain-researcher" / "agent.toml").read_text(encoding="utf-8")
    assert "base_url" not in raw


@pytest.mark.parametrize("modell", [
    # base_url ist kein API-Endpunkt
    "{ provider: claude-code, model: claude-code/sonnet, base_url: 'https://api.example.com/v1' }",
    # base_url nur mit dem Abo-Treiber
    "{ provider: openai, model: gpt-4o, base_url: openfang_claude_subscription_wrapper.cmd }",
    # Abo-Treiber nur mit dem Abo-Wrapper
    "{ provider: claude-code, model: claude-code/sonnet }",
    # Abo-Treiber nur mit einem Abo-Modell
    "{ provider: claude-code, model: gpt-4o, base_url: openfang_claude_subscription_wrapper.cmd }",
])
def test_abo_vorgaben_fail_closed(tmp_path, monkeypatch, modell):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, ABO.format(modell=modell))
    assert module.sync() == 1


def test_echte_registry_stellt_den_rechercheagenten_aufs_abo():
    module = _load_sync_module()
    registry = yaml.safe_load(module.REGISTRY.read_text(encoding="utf-8"))
    model = registry["spaces"]["research"]["model"]
    assert model["provider"] == "claude-code" and model["model"] == "claude-code/sonnet"
    assert model["base_url"] == "openfang_claude_subscription_wrapper.cmd"
    assert module.validate_model_overrides() == []


def test_sync_fails_closed_for_unknown_model_override_key(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  research:
    agent: brain-researcher
    enabled: true
    mcp_servers: [fetch]
    model: { model: gpt-4o, top_p: 0.9 }
    events: {}
""")

    assert module.sync() == 1


def test_sync_fails_closed_for_non_positive_max_tokens(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  research:
    agent: brain-researcher
    enabled: true
    mcp_servers: [fetch]
    model: { max_tokens: 0 }
    events: {}
""")

    assert module.sync() == 1


def test_sync_fails_closed_for_empty_model_name(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, """version: 1
spaces:
  research:
    agent: brain-researcher
    enabled: true
    mcp_servers: [fetch]
    model: { model: "  " }
    events: {}
""")

    assert module.sync() == 1


TRAEGER = """version: 1
spaces:
  sales:
    agent: brain-sales
    rights_carrier: true
    enabled: true
    mcp_servers: [auftragsbuch]
    mcp_tools: {auftragsbuch: [auftrag_probe, auftrag_lesen]}
    events:
      sales.auftrag_probe:
        tool: auftrag_probe
        required_provenance: [approval_ref, cost_ref]
        execution: {kind: mcp, server: auftragsbuch}
"""


def test_rights_carrier_manifest_has_no_agent_rights(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch, TRAEGER)

    assert module.sync() == 0
    text = (tmp_path / "agents" / "brain-sales" / "agent.toml").read_text(encoding="utf-8")
    document = tomllib.loads(text)

    assert document["mcp_servers"] == ["auftragsbuch"]
    assert document["capabilities"]["tools"] == [
        "mcp_auftragsbuch_auftrag_lesen",
        "mcp_auftragsbuch_auftrag_probe",
    ]
    assert document["capabilities"]["network"] == []
    assert document["capabilities"]["memory_read"] == []
    assert document["capabilities"]["memory_write"] == []
    assert "rights-carrier" in document["tags"]
    assert document["description"].startswith("Rights carrier (no agent): ")
    assert document["resources"]["max_llm_tokens_per_hour"] == 2000
    prompt = document["model"]["system_prompt"]
    assert "brain-sales accepts no messages." in prompt
    assert "user_text" not in prompt
    assert "memory_store" not in text and "memory_recall" not in text


def _traeger_mit(zeile_alt: str, zeile_neu: str) -> str:
    assert zeile_alt in TRAEGER
    return TRAEGER.replace(zeile_alt, zeile_neu)


def test_rights_carrier_must_be_boolean(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch,
                        _traeger_mit("rights_carrier: true", 'rights_carrier: "ja"'))
    assert any("rights_carrier" in e for e in module.validate_rights_carrier())
    assert module.sync() == 1


def test_rights_carrier_requires_mcp_tools(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch,
                        _traeger_mit("mcp_tools: {auftragsbuch: [auftrag_probe, auftrag_lesen]}", "mcp_tools: {}"))
    assert any("mcp_tools" in e for e in module.validate_rights_carrier())


def test_rights_carrier_rejects_model_override_and_prompt_hint(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch,
                        TRAEGER + "    model: {max_tokens: 100}\n    system_prompt_hint: hallo\n")
    fehler = module.validate_rights_carrier()
    assert any("model" in e for e in fehler)
    assert any("system_prompt_hint" in e for e in fehler)


def test_rights_carrier_events_must_be_mcp(tmp_path, monkeypatch):
    module = _load_sync_module()
    _configure_registry(module, tmp_path, monkeypatch,
                        TRAEGER + "      sales.anderes: {tool: x, required_params: []}\n")
    assert any("sales.anderes" in e for e in module.validate_rights_carrier())


def test_space_without_rights_carrier_renders_as_before(tmp_path, monkeypatch):
    """Der echte Eintrag `ideas` erzeugt weiter byte-gleich das eingecheckte Manifest."""
    import pytest
    modul = _load_sync_module()
    vorhanden = ROOT / "openfang" / "agents" / "brain-ideas" / "agent.toml"
    if not vorhanden.exists():
        pytest.skip("Submodul openfang nicht ausgecheckt")
    import yaml
    spec = yaml.safe_load(modul.REGISTRY.read_text(encoding="utf-8"))["spaces"]["ideas"]
    assert "rights_carrier" not in spec
    assert modul._render_agent_toml("ideas", spec) == vorhanden.read_text(encoding="utf-8")
