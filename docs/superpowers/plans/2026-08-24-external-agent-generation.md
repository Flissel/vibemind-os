# External Agent Generation Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model external agentless runtimes explicitly so the OpenFang agent sync rejects accidental missing agents and skips intentional external runtimes without crashing.

**Architecture:** Add a closed `generate_agent` boolean contract to the space registry, defaulting to `true`. Validate the contract before all existing generator checks, then make the skip/reporting path handle `generate_agent: false` without weakening MCP authority, event, or model validation.

**Tech Stack:** Python 3.11, PyYAML, pytest, TOML output, YAML registry, Git submodules

---

## File Map

- Modify `config/space_agent_registry.yml`: mark `agentfarm` as an explicit external runtime.
- Modify `scripts/sync_openfang_agents.py`: validate the generation contract and safely skip/report external runtimes.
- Modify `scripts/tests/test_sync_openfang_agents.py`: prove fail-closed validation, stable skipping, and the real `agentfarm` contract.
- Reference `docs/superpowers/specs/2026-08-24-external-agent-generation-design.md`: approved behavior and non-goals.

### Task 1: Fail-closed generation contract

**Files:**
- Modify: `scripts/tests/test_sync_openfang_agents.py:189`
- Modify: `scripts/sync_openfang_agents.py:167`
- Modify: `scripts/sync_openfang_agents.py:375`

- [ ] **Step 1: Add failing validation tests**

Add these tests before the existing generated-agent MCP-scope tests:

```python
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
```

- [ ] **Step 2: Run the four tests and verify RED**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -k "external_runtime_may or explicit_generation_opt_out or generation_opt_out_rejects or generate_agent_must" -q
```

Expected: four failures with `AttributeError` because
`validate_agent_generation_contract` does not exist.

- [ ] **Step 3: Implement the minimal registry validator**

Add this function immediately before `validate_generated_agent_mcp_scopes()`:

```python
def validate_agent_generation_contract() -> list[str]:
    """Validate whether each enabled space should generate an OpenFang agent."""
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict):
            continue
        generate_agent = spec.get("generate_agent", True)
        if not isinstance(generate_agent, bool):
            errors.append(f"{space_name} generate_agent must be a boolean")
            continue
        agent = spec.get("agent")
        if generate_agent is False:
            if agent is not None:
                errors.append(
                    f"{space_name} must omit agent or set it to null when "
                    "generate_agent is false"
                )
            continue
        if spec.get("enabled", True) and (
            not isinstance(agent, str) or not agent.strip()
        ):
            errors.append(
                f"{space_name} requires non-empty agent when generate_agent is true"
            )
    return errors
```

Prepend it to `sync()`'s validation chain:

```python
validation_errors = (
    validate_agent_generation_contract()
    + validate_generated_agent_mcp_scopes()
    + validate_mcp_tool_scopes()
    + validate_model_overrides()
    + validate_mcp_authority()
)
```

- [ ] **Step 4: Run focused and existing tests and verify GREEN**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -q
```

Expected: 19 passed.

- [ ] **Step 5: Verify the intermediate real-registry failure is controlled**

Run:

```text
python scripts/sync_openfang_agents.py --check
```

Expected: exit 1 with
`INVALID agentfarm requires non-empty agent when generate_agent is true` and no
`Traceback` or `TypeError`.

- [ ] **Step 6: Commit the contract validation**

```text
git add scripts/sync_openfang_agents.py scripts/tests/test_sync_openfang_agents.py
git commit -m "fix(openfang-sync): validate agent generation contract"
```

### Task 2: Explicit external-runtime skip

**Files:**
- Modify: `config/space_agent_registry.yml:289-300`
- Modify: `scripts/tests/test_sync_openfang_agents.py:189`
- Modify: `scripts/sync_openfang_agents.py:154-164`
- Modify: `scripts/sync_openfang_agents.py:395-401`

- [ ] **Step 1: Add failing skip and real-registry tests**

Add:

```python
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
```

- [ ] **Step 2: Run the two tests and verify RED**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -k "skips_explicit_external or real_agentfarm_registry" -q
```

Expected: the temporary-registry test crashes in the null-agent formatting
path, and the real-registry test fails because `generate_agent` is absent.

- [ ] **Step 3: Mark `agentfarm` as external**

Change the existing registry block to:

```yaml
    agent: null                    # tool-only: no chat agent; `vibemind`
                                   # (openrouter) becomes obsolete, not replaced
    generate_agent: false         # Captain Cook is the runtime; no OpenFang agent.toml
```

- [ ] **Step 4: Make skip ordering and reporting null-safe**

Replace `_skip_reason()` with:

```python
def _skip_reason(space: str, spec: dict) -> str | None:
    if not spec.get("enabled", True):
        return "disabled"
    if spec.get("generate_agent", True) is False:
        return "external runtime (generation disabled)"
    agent = spec.get("agent", "")
    if not isinstance(agent, str) or not agent.strip():
        return "invalid agent name"
    # Don't overwrite pre-existing hand-curated agents
    for protected in ("brain-coder", "rowboat-chat", "brain-fallback"):
        if agent == protected:
            return f"protected (pre-existing): {agent}"
    return None
```

In `sync()`, derive a display-only label before formatting the skip line:

```python
reason = _skip_reason(space, spec)
agent = spec.get("agent")
agent_label = agent if isinstance(agent, str) else "-"
if reason:
    print(f"  skip  {space:<12} ({agent_label:<24}) — {reason}")
    skipped += 1
    continue
```

The validator guarantees that execution reaches manifest path construction only
when `agent` is a non-empty string.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -k "skips_explicit_external or real_agentfarm_registry" -q
```

Expected: 2 passed.

- [ ] **Step 6: Run the complete generator test file**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -q
```

Expected: 21 passed.

- [ ] **Step 7: Commit the explicit skip behavior**

```text
git add config/space_agent_registry.yml scripts/sync_openfang_agents.py scripts/tests/test_sync_openfang_agents.py
git commit -m "fix(openfang-sync): skip explicit external runtimes"
```

### Task 3: Integrated drift and scope gate

**Files:**
- Verify: `openfang` gitlink and generated agent manifests
- Verify: `config/space_agent_registry.yml`
- Verify: `scripts/sync_openfang_agents.py`
- Verify: `scripts/tests/test_sync_openfang_agents.py`

- [ ] **Step 1: Initialize the exact pinned OpenFang submodule**

Run:

```text
git submodule update --init -- openfang
git rev-parse HEAD:openfang
git -C openfang rev-parse HEAD
```

Expected: both SHA outputs equal the gitlink recorded by the current parent
commit. Do not advance or edit the submodule.

- [ ] **Step 2: Run the focused unit gate**

Run:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -q
```

Expected: 21 passed.

- [ ] **Step 3: Run the real generated-manifest drift gate**

Run:

```text
python scripts/sync_openfang_agents.py --check
```

Expected: exit 0; output contains the `agentfarm` external-runtime skip and
contains neither `DRIFT`, `Traceback`, nor `TypeError`.

- [ ] **Step 4: Verify diff integrity and exact scope**

Run:

```text
git diff --check origin/master...HEAD
git diff --name-only origin/master...HEAD
git status --short --branch
```

Expected tracked scope:

```text
config/space_agent_registry.yml
docs/superpowers/plans/2026-08-24-external-agent-generation.md
docs/superpowers/specs/2026-08-24-external-agent-generation-design.md
scripts/sync_openfang_agents.py
scripts/tests/test_sync_openfang_agents.py
```

The branch must have no uncommitted changes. Do not push, merge, deploy, restart
OpenFang, or delete any branch/worktree during this task.
