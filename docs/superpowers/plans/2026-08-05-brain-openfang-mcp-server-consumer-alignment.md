# Brain OpenFang MCP Server Consumer Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Brain inventory and introspection consumer read OpenFang's canonical top-level `mcp_servers` field with fail-closed precedence and bounded legacy compatibility.

**Architecture:** Add a dependency-free Brain-core TOML helper that owns MCP scope precedence and validation. Keep capability inventory and introspection response shapes stable, but route their MCP scope reads through the helper; consolidate the two duplicated introspection inventory loops behind one internal loader.

**Tech Stack:** Python 3.11+, standard-library `tomllib`, FastAPI route functions, pytest.

---

### Task 1: Establish the manifest parsing contract

**Files:**
- Create: `brain/the_brain/core/openfang_agent_manifest.py`
- Create: `brain/the_brain/tests/test_openfang_agent_manifest.py`

- [ ] **Step 1: Write failing precedence and validation tests**

Add parameterized tests that call `extract_mcp_servers` with these exact cases:

```python
@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"mcp_servers": ["canonical"], "mcp_allowed": {"servers": ["legacy"]}}, ["canonical"]),
        ({"mcp_servers": [], "capabilities": {"mcp_servers": ["transitional"]}}, []),
        ({"capabilities": {"mcp_servers": ["transitional"]}}, ["transitional"]),
        ({"mcp_allowed": {"servers": ["legacy"]}}, ["legacy"]),
        ({"mcp_servers": "all"}, []),
        ({"mcp_servers": ["valid", 3]}, []),
    ],
)
def test_extract_mcp_servers_is_canonical_and_fail_closed(manifest, expected):
    assert extract_mcp_servers(manifest) == expected
```

Also write a temporary-file test for valid TOML and malformed TOML through `load_mcp_servers`.

- [ ] **Step 2: Run tests and confirm RED**

Run from `brain/the_brain`:

```powershell
python -m pytest tests/test_openfang_agent_manifest.py -q
```

Expected: collection fails because `core.openfang_agent_manifest` does not exist.

- [ ] **Step 3: Implement the minimal parser**

Create a helper with these public functions:

```python
def extract_mcp_servers(manifest: Mapping[str, object]) -> list[str]:
    for container, key in (
        (manifest, "mcp_servers"),
        (_mapping(manifest.get("capabilities")), "mcp_servers"),
        (_mapping(manifest.get("mcp_allowed")), "servers"),
    ):
        if key in container:
            return _validated_string_list(container[key])
    return []


def load_mcp_servers(path: str | os.PathLike[str]) -> list[str]:
    try:
        with open(path, "rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return extract_mcp_servers(manifest)
```

`_mapping` returns an empty mapping for non-mappings. `_validated_string_list` accepts only a list of non-empty strings and otherwise returns `[]`; it preserves declared order.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the Task 1 command. Expected: all parser tests pass.

- [ ] **Step 5: Commit the parser unit**

```powershell
git add brain/the_brain/core/openfang_agent_manifest.py brain/the_brain/tests/test_openfang_agent_manifest.py
git commit -m "feat(brain): parse canonical OpenFang MCP scope"
```

### Task 2: Route all Brain consumers through the parser

**Files:**
- Modify: `brain/the_brain/core/capability_discovery.py:74-108`
- Modify: `brain/the_brain/web/routers/introspection.py:3585-3970`
- Modify: `brain/the_brain/tests/test_openfang_agent_manifest.py`

- [ ] **Step 1: Add failing consumer tests**

Add a temporary `brain-ideas/agent.toml` with top-level `mcp_servers = ["spaces-ideas"]`. Assert:

```python
assert discover_agents(str(agents_dir))[0]["mcp_servers"] == ["spaces-ideas"]
assert _load_openfang_agents(str(agents_dir))[0]["mcp_servers"] == ["spaces-ideas"]
```

For `agent_tools`, monkeypatch the internal agents directory to the temporary directory and provide a fake discovery object whose `list_tools` records the requested server. Await the route directly and assert the JSON body contains only `spaces-ideas` and the fake was called once for that server.

- [ ] **Step 2: Run consumer tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_openfang_agent_manifest.py -q
```

Expected: capability discovery returns no canonical servers and introspection has no shared loader.

- [ ] **Step 3: Wire capability discovery**

Import `extract_mcp_servers` and replace the current `or` chain with:

```python
mcp = extract_mcp_servers(t)
```

Keep malformed-file skipping, model formatting, and output keys unchanged.

- [ ] **Step 4: Consolidate introspection reads**

Add `_OPENFANG_AGENTS_DIR` with the current path as its default and `_load_openfang_agents(agents_dir: str | None = None)`. It loads top-level name, description, tags, and the existing model representation while obtaining MCP scope only through `load_mcp_servers`. Replace the JSON and XLSX duplicate loops with calls to this loader. Make `agent_tools` use `load_mcp_servers` on its selected manifest and update comments/docstrings from `mcp_allowed` to `mcp_servers`.

Do not change event-to-agent routing, tool discovery, endpoint response fields, or error status codes.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the Task 2 command. Expected: all tests pass without starting OpenFang or MCP.

- [ ] **Step 6: Commit consumer alignment**

```powershell
git add brain/the_brain/core/capability_discovery.py brain/the_brain/web/routers/introspection.py brain/the_brain/tests/test_openfang_agent_manifest.py
git commit -m "fix(brain): align MCP scope manifest consumers"
```

### Task 3: Verify bounded integration and provenance

**Files:**
- Verify only; no additional product files.

- [ ] **Step 1: Run focused and adjacent tests**

```powershell
python -m pytest tests/test_openfang_agent_manifest.py tests/test_space_contract.py tests/test_agentfarm_brain_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run static checks**

```powershell
python -m compileall -q core/openfang_agent_manifest.py core/capability_discovery.py web/routers/introspection.py tests/test_openfang_agent_manifest.py
git diff --check origin/master...HEAD
```

Expected: both commands exit 0.

- [ ] **Step 3: Confirm scope and non-claims**

Verify `git diff --name-only origin/master...HEAD` contains exactly the six paths allowed by the session request. Confirm no MCP connection, Fungus process, provider request, database mutation, deployment, or Proxmox action occurred.
