# VibeMind Adaptive Skill Library

App-specific desktop-automation skills, indexed semantically in Qdrant and selected by a coordinator agent.

## Layout

```
skills/
  excel/
    fill-cell/SKILL.md
    select-range/SKILL.md
    ...
  word/
  file-explorer/
  vscode/
  claude-desktop/
  chrome/
  _loader.py        # parses SKILL.md frontmatter + body
  _indexer.py       # syncs to Qdrant collection 'vibemind_skills'
```

## SKILL.md format

YAML frontmatter + Markdown body. Required fields:

| Field | Type | Purpose |
|---|---|---|
| `name` | string | unique slug (`<app>-<verb>-<noun>`) |
| `description` | string | one-line, used as embedding source |
| `app` | string | `excel`, `word`, etc. |
| `agents` | list | which agents may load this skill (`desktop`, `openclaude`, `*`) |
| `trigger` | string (regex) | natural-language phrases that should activate the skill |
| `inputs` | list | named args the skill expects (cell, value, …) |
| `expected_state` | object | `{description, verification_tool}` for the validator |
| `secrets` | list | optional credential prompts (see Secrets section) |
| `confidence` | float 0..1 | success rate, updated by coordinator |
| `attempts` / `successes` | int | counters |
| `last_adjusted` | iso8601 \| null | timestamp of last adjustment |

The Markdown body holds the **steps** — sequential instructions that the executor LLM follows to perform the action via desktop-automation MCP tools (`handoff_action`, `handoff_get_focus`, `vision_analyze`, etc.).

## Lifecycle

1. **Manual seed** — write SKILL.md by hand for the most common app interactions.
2. **Indexer** — `python _indexer.py --rebuild` embeds every SKILL.md into Qdrant.
3. **Selection** — Skill-Coordinator queries Qdrant with the user's natural-language request, gets top-K candidates filtered by `agents` whitelist.
4. **Execution** — selected SKILL.md is injected into the executor agent's prompt, the agent runs the steps via MCP.
5. **Validation** — coordinator runs `vision_analyze` against `expected_state.description`; success=True → increment `successes`, recompute `confidence`. Failure → diagnose + adjust + retry.
6. **Decay** — skills not used in N days have their `confidence` multiplied by `(1 - decay_rate)` to prefer fresh patterns.

## Secrets

If a skill needs credentials, declare them in frontmatter:

```yaml
secrets:
  - credential_id: github_pat
    form_schema:
      - {name: token, label: "GitHub PAT", type: password, required: true}
```

The skill-runner calls `handoff_clarify` with `form_schema=…`, which renders an HTML form on `http://localhost:8007/api/clarify/<id>/form`. After the user submits, the value is stored encrypted via Windows DPAPI; the skill receives only an opaque token (`{{secret:github_pat}}`) that resolves at run-time.
