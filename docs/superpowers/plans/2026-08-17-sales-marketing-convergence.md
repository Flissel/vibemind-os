# Sales ↔ Marketing Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the three duplicated delivery concerns between `spaces/sales` (aisalesorgcore submodule) and `spaces/marketing` — email send, channels, approval — by having sales *consume* marketing's already-hardened implementations instead of shipping its own, without coupling the sales submodule to marketing's Python internals.

**Architecture:** sales stays a standalone submodule. It reaches marketing capabilities the way it already reaches every external tool: `_mcp_call(name, args)` → `POST {MCP_GATEWAY_URL}/tools/call` (OpenFang MCP gateway). Marketing registers three thin MCP tools (`marketing.send_email`, `marketing.send_channel`, `marketing.request_approval`) that wrap its existing gated send / channel bus / approval flow. sales' local `send_email` / `send_slack_message` / `_approval_required` become thin wrappers that delegate through the gateway and fall back to their current local behaviour when `MCP_GATEWAY_URL` is unset. No sales file ever imports `spaces.marketing.*`; no marketing file imports sales.

**Tech Stack:** Python 3.10+, httpx (already a sales dep), FastAPI (marketing api), OpenFang MCP gateway (:4200), Mailcow SMTP, Supabase (Proxmox). AutoGen on the sales side.

**Spec:** This plan is its own spec (grounded in the Schritt-3 analysis, 2026-08-17). The convergence decision and rationale live in `WORKBOARD.md` (Erledigt 2026-08-17) and the sales repo's `VIBEMIND_ADAPTATION.md`.

## Global Constraints

- **No cross-repo Python import.** sales must not `import spaces.marketing.*` and marketing must not import sales. The only bridge is the MCP gateway (HTTP) — verified by a grep test in Task E7.
- **Sandbox-first, gates intact.** Every send path keeps marketing's existing gates: `MARKETING_SEND_ENABLED=true` env, `logs/marketing/FREEZE` absent, sandbox-lock. Default mode for all new tools is `dry_run`. Live requires the same confirm-token contract as `_send_paranoid.py`.
- **Fail-closed auth on the new MCP tools.** A missing/incorrect gateway shared secret → refuse, never send.
- **mkt-opus code.** Marketing edits touch mkt-opus' active-claim code — only with explicit user go, and each marketing task preserves the existing send-gate semantics byte-for-byte (new code paths, no gate weakening).
- **Path anchors.** New marketing files use the established `PKG_ROOT` / `REPO_ROOT` split (parent-of-`spaces` for imports; nearest ancestor with `vibemind-os/` for `.env`/logs), never `parents[3]`.

---

## Phase 0 — Decisions & shared tool contract

These block the workstreams and must be resolved with the user before coding. They are decisions, not code.

- **D-E1 (CRM substrate):** sales `crm_*` + `leads/deals/activities` vs marketing `accounts/audiences`. Options: (a) keep sales' own `sales` Supabase schema, share only the instance; (b) map sales `leads` onto marketing `accounts`. **Recommendation: (a)** — different domains, one Supabase instance (Proxmox), no schema merge. Out of scope for WS1–WS3; noted for a later data-alignment plan.
- **D-E2 (SMS):** `send_sms` (Twilio) → drop and route to `send_whatsapp` via the channel bus. **Recommendation: drop Twilio, WhatsApp only.** Affects WS2.
- **D-E5 (LLM):** point sales `OPENAI_BASE_URL` at the local server. Already smoke-tested per `VIBEMIND_ADAPTATION.md`; independent of WS1–WS3 — apply as a one-line env change whenever desired.
- **D-CONTRACT (the shared MCP tool contract):** the three tool names, argument shapes, and return shapes below are the frozen interface both sides code against.

```
marketing.send_email
  args:    {to: str, subject: str, body_html: str, body_text?: str,
            mode: "dry_run"|"shadow"|"live" = "dry_run", confirm_token?: str,
            source: str}                      # source = calling agent, for audit
  returns: {ok: bool, mode: str, message_id?: str, gated_by?: str, error?: str}

marketing.send_channel
  args:    {channel: str, recipient: str, body: str,
            mode: "dry_run"|"live" = "dry_run", confirm_token?: str, source: str}
  returns: {ok: bool, channel: str, mode: str, adapter?: str, gated_by?: str, error?: str}

marketing.request_approval
  args:    {kind: "broadcast"|"reply", payload: dict, source: str}
  returns: {ok: bool, proposal_id?: str, status: "pending_review", error?: str}
```

**Phase 0 exit:** user has answered D-E1/D-E2/D-E5 and approved D-CONTRACT. No commits.

---

## Workstream 1 — Email (`marketing.send_email` + sales delegation)

Highest value, most self-contained. Produces: a gated single-message send tool in marketing, exposed over the OpenFang gateway, and sales' `send_email` delegating to it with a safe local fallback.

### File structure (WS1)

- Create `spaces/marketing/tools/transactional_send.py` — one responsibility: send ONE email through Mailcow, reusing the `_send_paranoid` gate checks (not campaign-scoped). Writes an `audit_log` row. No new gate logic — imports the existing gate helpers.
- Create `spaces/marketing/tools/tests/test_transactional_send.py` — gate + dry_run + audit tests (no live SMTP).
- Modify `spaces/marketing/tools/_send_openfang.py` (or the OpenFang MCP tool registry it feeds) — register `marketing.send_email` → `transactional_send.send_one`.
- Modify `spaces/sales/src/tools.py:send_email` — delegate to `_mcp_call("marketing.send_email", …)` when `MCP_GATEWAY_URL` set; else current local behaviour.
- Test `spaces/sales/` — sales-side unit test that `send_email` calls `_mcp_call` with the D-CONTRACT arg shape when the gateway is set, and falls back when unset.

### Task E1: `transactional_send.send_one` — dry_run + gate reuse

**Files:**
- Create: `spaces/marketing/tools/transactional_send.py`
- Test: `spaces/marketing/tools/tests/test_transactional_send.py`

**Interfaces:**
- Consumes: existing gate helpers in `spaces/marketing/tools/_send_paranoid.py` (`FREEZE_PATH`, the `MARKETING_SEND_ENABLED` check, the sandbox lock — read that file first and reuse its exact predicates; do not re-implement).
- Produces: `async def send_one(*, to: str, subject: str, body_html: str, body_text: str | None = None, mode: str = "dry_run", confirm_token: str | None = None, source: str) -> dict` returning the `marketing.send_email` shape from D-CONTRACT.

- [ ] **Step 1: Write the failing test** (dry_run never opens SMTP, returns ok + mode)

```python
import asyncio
from spaces.marketing.tools import transactional_send as ts

def test_dry_run_never_sends_and_is_ok():
    res = asyncio.run(ts.send_one(
        to="lead@example.com", subject="hi", body_html="<p>hi</p>",
        mode="dry_run", source="OutreachAgent"))
    assert res["ok"] is True
    assert res["mode"] == "dry_run"
    assert "message_id" not in res or res["message_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py::test_dry_run_never_sends_and_is_ok -v`
Expected: FAIL — module `transactional_send` does not exist.

- [ ] **Step 3: Write minimal implementation** (dry_run branch only; import gate helpers but don't require live yet)

```python
"""transactional_send — send ONE email through Mailcow, gated exactly like
the campaign send-worker (reuses _send_paranoid predicates). Not campaign-scoped."""
from __future__ import annotations
import os
from pathlib import Path

PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
REPO_ROOT = next((p for p in (PKG_ROOT, *PKG_ROOT.parents) if (p / "vibemind-os").is_dir()), PKG_ROOT)

async def send_one(*, to: str, subject: str, body_html: str,
                   body_text: str | None = None, mode: str = "dry_run",
                   confirm_token: str | None = None, source: str) -> dict:
    if mode == "dry_run":
        return {"ok": True, "mode": "dry_run", "message_id": None}
    return {"ok": False, "mode": mode, "error": "not_implemented"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py::test_dry_run_never_sends_and_is_ok -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add spaces/marketing/tools/transactional_send.py spaces/marketing/tools/tests/test_transactional_send.py
git commit -m "feat(marketing): transactional_send.send_one dry_run scaffold"
```

### Task E2: live path is gated (FREEZE / MARKETING_SEND_ENABLED / sandbox)

**Files:**
- Modify: `spaces/marketing/tools/transactional_send.py`
- Test: `spaces/marketing/tools/tests/test_transactional_send.py`

**Interfaces:**
- Consumes: `_send_paranoid` gate predicates (exact names discovered in E1).
- Produces: `send_one(mode="live")` returns `{"ok": False, "gated_by": <gate>}` unless every gate passes; only then attempts SMTP.

- [ ] **Step 1: Write the failing test** (live is refused when FREEZE present / env off)

```python
import asyncio, os
from pathlib import Path
from spaces.marketing.tools import transactional_send as ts

def test_live_refused_without_enable(monkeypatch):
    monkeypatch.delenv("MARKETING_SEND_ENABLED", raising=False)
    res = asyncio.run(ts.send_one(
        to="lead@example.com", subject="s", body_html="<p>x</p>",
        mode="live", confirm_token="deadbeef", source="OutreachAgent"))
    assert res["ok"] is False
    assert res["gated_by"]  # names the failing gate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py::test_live_refused_without_enable -v`
Expected: FAIL — current code returns `error=not_implemented`, no `gated_by`.

- [ ] **Step 3: Write minimal implementation** (reuse the real gate predicates from `_send_paranoid.py`; the exact import is read from that file in E1 — replace the placeholder predicate names below with the real ones)

```python
from spaces.marketing.tools import _send_paranoid as _sp  # gate predicates live here

def _gate_failure(mode: str, confirm_token: str | None) -> str | None:
    if os.environ.get("MARKETING_SEND_ENABLED", "").lower() != "true":
        return "MARKETING_SEND_ENABLED"
    if (REPO_ROOT / "logs" / "marketing" / "FREEZE").exists():
        return "FREEZE"
    # sandbox-lock + confirm-token: call the same predicate the send-worker uses
    return _sp.confirm_gate_failure(mode, confirm_token)  # <- real name from E1
```

Wire `_gate_failure` into `send_one`: on non-dry_run, if it returns a gate name, return `{"ok": False, "mode": mode, "gated_by": gate}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add spaces/marketing/tools/transactional_send.py spaces/marketing/tools/tests/test_transactional_send.py
git commit -m "feat(marketing): gate transactional live send via _send_paranoid predicates"
```

### Task E3: live SMTP send + audit_log row

**Files:**
- Modify: `spaces/marketing/tools/transactional_send.py`
- Test: `spaces/marketing/tools/tests/test_transactional_send.py`

**Interfaces:**
- Consumes: Mailcow SMTP env (`SMTP_HOST/PORT/USER/PASSWORD/FROM`), `spaces.marketing.sync._db` for the `audit_log` write.
- Produces: on all gates passing, sends via `smtplib` (STARTTLS) and returns `{"ok": True, "mode": "live", "message_id": <id>}`; writes one `marketing.audit_log` row.

- [ ] **Step 1: Write the failing test** (gates forced open via monkeypatch; SMTP monkeypatched to a fake; assert audit row + message_id). Full test body:

```python
import asyncio, types
from spaces.marketing.tools import transactional_send as ts

def test_live_sends_and_audits(monkeypatch):
    monkeypatch.setenv("MARKETING_SEND_ENABLED", "true")
    monkeypatch.setattr(ts, "_gate_failure", lambda *a, **k: None)
    sent = {}
    def fake_smtp(*a, **k):
        class C:
            def __enter__(s): return s
            def __exit__(s, *e): return False
            def starttls(s): pass
            def login(s, *a): pass
            def send_message(s, msg): sent["to"] = msg["To"]
        return C()
    monkeypatch.setattr(ts.smtplib, "SMTP", fake_smtp)
    audits = []
    monkeypatch.setattr(ts, "_audit", lambda **kw: audits.append(kw))
    res = asyncio.run(ts.send_one(to="lead@example.com", subject="s",
        body_html="<p>x</p>", mode="live", confirm_token="ok", source="OutreachAgent"))
    assert res["ok"] and res["message_id"]
    assert sent["to"] == "lead@example.com"
    assert audits and audits[0]["source"] == "OutreachAgent"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py::test_live_sends_and_audits -v`
Expected: FAIL — no SMTP/audit code yet.

- [ ] **Step 3: Implement** the live branch: build a `MIMEMultipart` (html + optional text), `smtplib.SMTP(host, port)` → `starttls()` → `login()` → `send_message()`; generate `message_id`; call `_audit(source=…, to=…, subject=…, message_id=…)` which inserts one `marketing.audit_log` row via `_db`. (Import `smtplib` at module top so the test can monkeypatch `ts.smtplib`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add -A spaces/marketing/tools
git commit -m "feat(marketing): transactional live SMTP send + audit_log row"
```

### Task E4: register `marketing.send_email` as an OpenFang MCP tool

**Files:**
- Modify: `spaces/marketing/tools/_send_openfang.py` (read it first — it already talks to OpenFang; add the tool registration next to the existing channel send).
- Test: `spaces/marketing/tools/tests/test_transactional_send.py` (add a registration-shape test)

**Interfaces:**
- Consumes: `transactional_send.send_one`.
- Produces: a gateway-callable tool named `marketing.send_email` whose handler maps the D-CONTRACT args to `send_one(**args)` and returns its dict.

- [ ] **Step 1: Write the failing test** — the tool handler exists and forwards args:

```python
import asyncio
from spaces.marketing.tools import _send_openfang as sof

def test_send_email_tool_forwards(monkeypatch):
    seen = {}
    async def fake_send_one(**kw): seen.update(kw); return {"ok": True, "mode": kw["mode"]}
    monkeypatch.setattr("spaces.marketing.tools.transactional_send.send_one", fake_send_one)
    res = asyncio.run(sof.MCP_TOOLS["marketing.send_email"](
        {"to": "a@b.c", "subject": "s", "body_html": "<p>x</p>", "source": "T"}))
    assert res["ok"] and seen["to"] == "a@b.c" and seen["mode"] == "dry_run"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py::test_send_email_tool_forwards -v`
Expected: FAIL — `MCP_TOOLS["marketing.send_email"]` missing.

- [ ] **Step 3: Implement** a `MCP_TOOLS` dict entry in `_send_openfang.py` mapping `"marketing.send_email"` to an async handler `lambda args: transactional_send.send_one(**{**{"mode": "dry_run"}, **args})`. (Match whatever registration mechanism `_send_openfang.py` already uses; if it registers tools with OpenFang at import, add this alongside.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest spaces/marketing/tools/tests/test_transactional_send.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A spaces/marketing/tools
git commit -m "feat(marketing): expose marketing.send_email over the OpenFang MCP gateway"
```

### Task E5: sales `send_email` delegates via `_mcp_call` (with local fallback)

**Files:**
- Modify: `spaces/sales/src/tools.py` (the `send_email` function; read `_mcp_call` in `src/main.py` for the call convention — sales tools reach the gateway through the runtime, so expose delegation as an env-gated branch).
- Test: `spaces/sales/tests/test_send_email_delegation.py` (create)

**Interfaces:**
- Consumes: `marketing.send_email` over the gateway (D-CONTRACT).
- Produces: `send_email` returns the marketing tool's result when `MCP_GATEWAY_URL` is set; otherwise the current local/mock behaviour (unchanged).

- [ ] **Step 1: Write the failing test** — delegation path builds the D-CONTRACT args:

```python
import asyncio, os, json
import src.tools as tools

def test_send_email_delegates_when_gateway_set(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://gw")
    calls = {}
    async def fake_mcp(name, args): calls["name"] = name; calls["args"] = args; return json.dumps({"ok": True, "mode": "dry_run"})
    monkeypatch.setattr(tools, "_mcp_call", fake_mcp, raising=False)
    res = asyncio.run(tools.send_email(to="a@b.c", subject="s", body="<p>x</p>"))
    assert calls["name"] == "marketing.send_email"
    assert calls["args"]["to"] == "a@b.c" and calls["args"]["source"]
```

- [ ] **Step 2: Run to verify it fails**

Run (from `spaces/sales`): `python -m pytest tests/test_send_email_delegation.py -v`
Expected: FAIL — `send_email` doesn't branch on the gateway yet.

- [ ] **Step 3: Implement** the branch in `send_email`: if `os.environ.get("MCP_GATEWAY_URL")`, `return json.loads(await _mcp_call("marketing.send_email", {"to": to, "subject": subject, "body_html": body, "mode": os.environ.get("MARKETING_SEND_MODE", "dry_run"), "source": _agent_name()}))`; else fall through to the existing implementation untouched. Import `_mcp_call` from the runtime (match how other sales tools access it).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_send_email_delegation.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (inside the sales submodule)

```bash
cd spaces/sales && git add src/tools.py tests/test_send_email_delegation.py
git commit -m "feat: delegate send_email to marketing.send_email via MCP gateway (local fallback kept)"
```

### Task E6: end-to-end dry_run smoke (real gateway, sandbox)

**Files:**
- Test: `spaces/marketing/tools/tests/test_transactional_send.py` (add an integration marker) — or a standalone smoke script `spaces/marketing/scripts/smoke_transactional.py`.

**Interfaces:** consumes the running OpenFang gateway + marketing tool; asserts a `dry_run` round-trip returns `ok`.

- [ ] **Step 1** Write `scripts/smoke_transactional.py` that POSTs to `{MCP_GATEWAY_URL}/tools/call` with `{"name": "marketing.send_email", "arguments": {"to": "smoke@example.com", "subject": "smoke", "body_html": "<p>x</p>", "source": "smoke"}}` and prints the result.
- [ ] **Step 2** Run with the gateway up: `python spaces/marketing/scripts/smoke_transactional.py`. Expected: `{"ok": true, "mode": "dry_run", ...}`. No email leaves the system (dry_run).
- [ ] **Step 3** Commit the smoke script.

### Task E7: no-cross-import guard

**Files:**
- Test: `spaces/marketing/tools/tests/test_no_cross_import.py` (create)

- [ ] **Step 1** Write a test that greps the sales tree for `import spaces.marketing` and the marketing tree for `import` of any sales module, asserting zero matches (the architectural invariant).

```python
import subprocess, pathlib
def test_no_cross_repo_imports():
    root = pathlib.Path(__file__).resolve().parents[4]  # vibemind-os
    sales_hits = subprocess.run(["grep","-rn","import spaces.marketing", str(root/"spaces"/"sales")],
                                capture_output=True, text=True).stdout
    assert sales_hits == "", sales_hits
```

- [ ] **Step 2** Run: `python -m pytest spaces/marketing/tools/tests/test_no_cross_import.py -v`. Expected: PASS.
- [ ] **Step 3** Commit.

**WS1 exit:** sales' `send_email` sends through marketing's gated Mailcow path over the gateway in dry_run; live stays behind the unchanged gates; no cross-repo import; audit row written.

---

## Workstream 2 — Channels (`marketing.send_channel`) — follow-on plan

Same shape as WS1, against different entry points. Scope, to be expanded into full TDD tasks once WS1 lands and D-E2 is decided:

- Marketing: add `tools/transactional_send.py`-sibling `send_channel(channel, recipient, body, mode)` that calls the existing `_send_openfang.py` adapter path after `channels.assert_channel_configured(channel)`; reuse the same gate predicates as WS1. Register MCP tool `marketing.send_channel`.
- sales: `send_slack_message` / `send_whatsapp` / (dropped `send_sms`, per D-E2) delegate to `marketing.send_channel` with the right `channel` value; local fallback kept.
- Entry points already present: `spaces/marketing/tools/channels.py` (`assert_channel_configured`, `detect_channel_readiness`), `spaces/marketing/tools/_send_openfang.py`.

## Workstream 3 — Approval (`marketing.request_approval`) — follow-on plan

Marketing already exposes the full approval flow over HTTP (`:5510`), so WS3 can go through the API instead of a new MCP tool if preferred:

- Marketing: wrap `POST /api/curator/broadcast_proposals` + `.../request_approval` behind one MCP tool `marketing.request_approval` (or document the two HTTP calls as the contract).
- sales: `_approval_required` / `_store_draft` delegate — a sales draft that needs human sign-off becomes a marketing broadcast/reply proposal (`status=pending_review`), approved via the existing `approve_via_bridge` OpenFang path.
- Entry points already present: `spaces/marketing/api/server.py` routes `/api/broadcast_proposals/{id}/approve[_via_bridge]`, `/api/reply_proposals/{id}/approve`, `/api/curator/broadcast_proposals/{id}/request_approval`.

---

## Self-review notes

- **Coverage:** WS1 fully covers the email dedup (the highest-value of the three). WS2/WS3 are scoped, not detailed — they are separate plans by design (Scope Check: independent subsystems). D-E1/E2/E5 are decisions, surfaced in Phase 0, not silently assumed.
- **Interfaces:** the three tool names + arg/return shapes are frozen in D-CONTRACT and reused verbatim in E4/E5. `send_one`'s signature in E1 matches its calls in E4.
- **Known unknown:** the exact `_send_paranoid` gate-predicate names (E2 Step 3) and the `_send_openfang.py` registration mechanism (E4 Step 3) must be read from those files at execution time — the tasks say so explicitly rather than inventing names. This is a read-then-wire step, not a placeholder.
- **Cross-repo:** every marketing↔sales interaction is HTTP via the gateway; Task E7 enforces the no-import invariant as a test.
