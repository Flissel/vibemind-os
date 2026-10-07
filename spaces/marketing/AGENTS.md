# Marketing-Ops — Agent + Developer Guide

> **Cockpit evidence contract:** [COCKPIT_CONTRACT.md](docs/COCKPIT_CONTRACT.md)
> is authoritative for the static inventory (migrations, event-to-tool mappings,
> pytest definitions). Counts live only there, so they cannot drift here.

This file is the entry-point for any agent (or human) touching
`spaces/marketing/`. It describes the modules, their contracts, the
12+3 safety gates of the (now locked) legacy send path, and the things that
look like footguns but are deliberate. Overview, services and ports: `README.md`;
dated numbers and known gaps: `STATUS.md`.

**Sending:** this space does NOT send. Since the operator decision of
2026-09-12, sales-claw is the only way out: `versand_beauftragen` ->
`POST /api/versandauftraege` -> `marketing.versandauftrag_anlegen`
(`db/043_marketing_versandauftraege.sql`) -> at most one pending draft in
sales-claw that a human approves. The old senders are locked by
`tools/versandsperre.py` (see Gate graph).

If you came here from a Claude Code session: read the **Hard Rules**
section first, then **Gate graph**, then **Module map**. Skip the rest.

---

## Hard Rules — read before touching ANY file under spaces/marketing/

1. **Never write `consent_given_at` to a non-NULL value** from code.
   It is the GDPR signal that a recipient has affirmatively opted in.
   No path in the current codebase sets it. New paths must not either.
2. **Never write `investor_already_sent = true`** from code. The DB
   trigger `trg_flip_investor_sent` (migration 005) is the only allowed
   writer; it fires when `campaign_sends.delivered_at` transitions
   NULL→set. Worker D (`workers/delivered_webhook.py`) is the only
   process that should call `mark_delivered`. Skip-listed in tests.
3. **Never write `campaign_sends.delivered_at`** from code outside
   Worker D. The send-worker (`tools/_send_paranoid.py`) DELIBERATELY
   leaves it NULL on success — verified by `gate12_*` tests.
4. **Never bypass `_require_proposal_api_key`** on a new POST route.
   The `test_auth_guard.test_other_mutating_routes_also_guarded` test
   fails CI if you do. The helper refuses (503) when the env is unset
   — fail-closed by design.
5. **Never lift `external_sources.can_send` above false** without a
   new migration that drops the CHECK constraint AND documents the
   replacement gate stack for that channel. The constraint exists
   precisely so this requires a code review.
6. **Never send to a non-`@vibemind.space` recipient** from any path.
   The send-worker has a hardcoded `ALLOWED_DOMAINS = {"vibemind.space"}`
   plus an IDNA round-trip check against unicode lookalikes (gate 5).
   Postfix has a PCRE block as the second line of defense.
7. **Never embed an OpenAI/Anthropic/Mailcow API key in this codebase.**
   They live in `.env` only; tools read them from `os.environ` at call
   time so a rotated key takes effect on next request.

---

## Gate graph — what stands between a Hand discovery and a sent mail

**gesperrt by `versandsperre`:** every external-send layer below (GATE 1 to
GATE 12 and the Postfix layer, i.e. `_send_paranoid.py`, `_send_telegram.py`,
`_send_openfang.py`) is behind Gate 0, `tools/versandsperre.py`. Gate 0 fires
first in the LIVE branch only (DRY_RUN and SHADOW send nothing and stay usable).
The override `MARKETING_VERSAND_TROTZDEM=1` exists and logs a warning. The only
path out of this space is a Versandauftrag to sales-claw; the layers below are
kept because sales-claw's missing Telegram dispatcher would reuse them, not
because they are active. The staging layers (allowlist, CHECK constraint,
proposal staging, human key, MX validation, atomic promotion) still apply.

15 independent layers. Removing any one of them is a separate, reviewable
migration or commit. Numbers match the gates discussed in commit messages.

```
INPUT
 │
 │  Hand discovery (Lead/Researcher/Collector) · Gmail · Notion ·
 │  Sheets · Tavily · manual CSV · or operator HTTP POST
 │
 │  ALLOWLIST                          (Python frozenset, hardcoded)
 ▼  ALLOWED_INTEGRATION_KINDS
 │      see tools/integrations.py
 │
 │  CHECK CONSTRAINT                   (schema, can_send = false)
 ▼  marketing.external_sources
 │
 │  PROPOSAL STAGING                   (audit + status='pending_review')
 ▼  propose_audience / propose_audience_from_source
 │      writes marketing.audience_proposals + lead_candidates
 │
 │  HUMAN GATE                         (HTTP 503 if env unset, 401 if wrong)
 ▼  MARKETING_PROPOSAL_API_KEY
 │      _require_proposal_api_key in api/server.py
 │
 │  MX VALIDATION (DNS only, never SMTP)
 ▼  validate_proposal_mx
 │      sets smtp_valid = 1/0/-1
 │
 │  ATOMIC PROMOTION                   (stored function, all-or-rollback)
 ▼  marketing.approve_audience_proposal()
 │      writes accounts + emails (consent_given_at = NULL) + members
 │
 │  GATE 0  versandsperre              gesperrt (LIVE only; override logs)
 │  --- external-send layers below: gesperrt by versandsperre ---
 │  GATE 1  kill-switch                MARKETING_SEND_ENABLED == "true"
 │  GATE 2  freeze-file absent         logs/marketing/FREEZE
 │  GATE 3  campaign status terminal?
 │  GATE 4  recipient snapshot + cap   HARD_RECIPIENT_CAP=1000
 │  GATE 5  domain allowlist + IDNA    {vibemind.space} only
 │  GATE 6  investor-lockout re-check  defense-in-depth recount
 │  GATE 7  confirm-token              SHA256 over sorted recipient set
 │  GATE 8  SHADOW pre-ping            (SHADOW mode only)
 │  GATE 9  Postfix loopback probe     RCPT TO external = expect 554
 │  GATE 10 per-recipient RCPT probe   on SAME connection (TOCTOU defense)
 │          + atomic claim             ON CONFLICT (campaign_id, email)
 │  GATE 11 mailq post-send audit      external in queue = FREEZE
 │  GATE 12 atomic status flip         campaigns.status='sent'
 ▼
 │  POSTFIX SERVER-SIDE                check_recipient_access PCRE
 ▼      554 LOOPBACK-MODE for any non-vibemind.space
 │
 ▼  MAIL EXTERN (legacy path; locked — the live path is a Versandauftrag
                to sales-claw, which a human approves)
```

`delivered_at` is written ONLY by Worker D (`workers/delivered_webhook.py`)
which has its own ALLOWED_DOMAINS defense-in-depth recheck.

---

## Module map

Where things live (counts and the full list are in code and `STATUS.md`, not here):

```
spaces/marketing/
├── api/        FastAPI. server.py (main app), pult.py (/api/pult/*), bilder.py,
│               chat.py, gestaltung.py, medien_mandant.py
├── claw/       marketing-claw: server.py (MCP sidecar :8130), werkzeuge.py (tools),
│               agent_werkzeuge.py (design-agent operations), markenwissen.py,
│               bild_*.py (ComfyUI), shim/ (own shim :8117), gateway/ (openclaw),
│               scripts/ (service starter)
├── db/         migrations 001-062 (039 absent, two files 013) + verify_*.sql
├── workers/    vorlagen/bild/chat workers, delivered_webhook (Worker D),
│               bubble_*, export_worker, mx_worker, webhook_delivery, ...
├── sync/       Worker A/B/C (DB <-> vault, IMAP)
├── agents/     marketing_agent.py (13 EVENT_TO_TOOL), runner.py
├── tools/      marketing_tools.py, approval.py, integrations.py, hand_bridge.py,
│               versandsperre.py, locked senders (_send_*.py)
├── mirofish/   quality simulation (AGPL, see NOTICE-AGPL.md)
├── curator/ mockup/ vorlagen/ bilder/ skills/ n8n_workflows/ docs/ scripts/
└── tests/      cockpit drift guard (more tests sit next to their modules)
```

## Event ↔ tool table (MarketingBackendAgent)

| event_type                       | tool function                       | writes to                                     |
|----------------------------------|-------------------------------------|-----------------------------------------------|
| `marketing.stats`                | `get_stats`                         | (read-only)                                   |
| `marketing.list_audiences`       | `list_audiences`                    | (read-only)                                   |
| `marketing.list_templates`       | `list_templates`                    | (read-only)                                   |
| `marketing.list_campaigns`       | `list_campaigns`                    | (read-only)                                   |
| `marketing.inbox`                | `get_inbox_unread`                  | (read-only)                                   |
| `marketing.audience_count`       | `audience_count`                    | (read-only)                                   |
| `marketing.create_audience`      | `create_audience`                   | `marketing.audiences`                         |
| `marketing.create_template`      | `create_template`                   | `marketing.templates`                         |
| `marketing.send_campaign`        | `send_campaign`                     | `marketing.campaign_sends` (NOT delivered_at) |
| `marketing.audience_proposal`    | `propose_audience`                  | `audience_proposals` + `lead_candidates`      |
| `marketing.list_proposals`       | `list_proposals`                    | (read-only)                                   |
| `marketing.get_proposal`         | `get_proposal`                      | (read-only)                                   |
| `marketing.request_hand`         | `request_hand_research`             | `audit_log` only (Hand callback later)        |

`marketing.send_campaign` ends in a locked sender (Gate 0) in LIVE mode.
The MCP tools of marketing-claw (`claw/werkzeuge.py`, 27 defined and
registered in `claw/server.py`) are a separate surface from these 13 events.

Param-aliasing (DE/EN) lives in `marketing_agent.py:PARAM_MAPPING`.

---

## HTTP routes

133 route decorators plus two static mounts (`/mockup`, `/curator`): `api/server.py`
84, `api/pult.py` 16, `api/chat.py` 18, `api/bilder.py` 10, `api/medien_mandant.py` 4,
`api/gestaltung.py` 1. Look them up with a search for `@router.` / `@pult_router.` /
`@app.` in `api/`. Auth by layer:

| Layer | Header / key | Scope |
|-------|--------------|-------|
| global middleware | `X-API-Key` = `MARKETING_API_KEY` | `/api/*` |
| mutating proposal routes | `MARKETING_PROPOSAL_API_KEY` via `_require_proposal_api_key` | proposals, approve, reject, validate_mx, integrations import |
| Pult | `X-Pult-Key` = `MARKETING_PULT_KEY` | `/api/pult/*` |
| workers | `X-Bild-Key` = `MARKETING_BILD_KEY` | `/api/bilder/arbeiter/*`, `/api/chat/arbeiter/*` |
| unsubscribe | per-recipient HMAC token | `/api/unsubscribe` |

`MARKETING_PROPOSAL_API_KEY` MUST be set; the helper returns 503 if absent.
The header of `api/server.py` still says "Phase 1 read-only"; that is stale
(see `STATUS.md`, known gaps). Sending is not an API route of this space except
`POST /api/versandauftraege`, which only creates an order for sales-claw.

---

## Schema reality (from psql, not commit messages)

Several columns have non-obvious names because the original pathx
import dictated them:

- `marketing.emails.handle` — FK to `marketing.accounts(handle)`. NOT
  named `account_handle`.
- `marketing.emails.strategy_id` — text provenance label, NOT named
  `source`. The approval flow stores `'proposal:<uuid>'` here.
- `marketing.emails.smtp_valid` — smallint tri-state (-1/0/1), NOT a
  boolean `is_verified`. The send-worker requires `=1`; MX validation
  flips to 1, NXDOMAIN to 0; default -1.
- `marketing.audiences.filter_dsl` — jsonb, NOT named `definition`.
- `marketing.audience_members` — composite PK (`audience_id`, `email`).
- `marketing.campaign_sends.message_id` — text, no `<>`, partial UNIQUE
  index (where NOT NULL). Migration 007.
- `marketing.campaign_sends` UNIQUE (`campaign_id`, `email`). Migration
  008. The atomic claim depends on this.

`marketing.audit_log` columns:
`id, actor, action, target_table, target_id, payload (jsonb), created_at`.

---

## Send-worker modes (`tools/_send_paranoid.py`) — LIVE is locked

LIVE fails at Gate 0 (`versandsperre`) unless `MARKETING_VERSAND_TROTZDEM=1`
is set (logged as a warning). Real delivery goes through sales-claw.

```
SendMode.DRY_RUN   - resolves recipients + computes confirm_token; NEVER opens SMTP
SendMode.SHADOW    - opens SMTP to MARKETING_SHADOW_HOST:PORT (default 127.0.0.1:0 = disabled);
                     send goes to Mailpit, never to extern. Aborts loud if envs unset.
SendMode.LIVE      - all 12 gates fire. Requires MARKETING_SEND_ENABLED=true,
                     FREEZE-file absent, valid confirm_token, real SMTP creds.
```

The auto-mode classifier in Claude Code blocks `--mode live` by default;
it's a deliberate operator action, not a dev-flow side-effect.

---

## Env vars

Required for full operation:

```
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
MAILCOW_URL / MAILCOW_API_KEY
MARKETING_PROPOSAL_API_KEY       — required for ANY POST endpoint
MARKETING_UNSUB_SECRET           — ≥32 chars; required to build/verify unsubscribe tokens
```

Required only for specific modes:

```
MARKETING_SEND_ENABLED=true      — LIVE send mode (per-run env, not .env recommended)
MARKETING_SHADOW_HOST / _PORT    — SHADOW mode pin (Mailpit container)
MARKETING_WEBHOOK_SECRET         — delivered_webhook HTTP listener
MARKETING_API_KEY                — global X-API-Key on all /api/* (vs the per-route key)
MARKETING_PULT_KEY               — X-Pult-Key for /api/pult/*
MARKETING_BILD_KEY               — X-Bild-Key for the image/chat worker routes
MARKETING_PRUEFADRESSE           — where layout templates go for review (operator-owned)
MARKETING_VERSAND_TROTZDEM=1     — override of the send lock (logs a warning)
ROWBOAT_WISSEN_ORDNER            — brand-knowledge folder (default: Rowboat knowledge/companys)
COMFYUI_URL                      — default http://127.0.0.1:8188
MARKETING_CLAW_LLM_URL           — default points at the shared shim :8114 (the marketing shim is :8117)
```

Optional sync workers:

```
MARKETING_VAULT_DIR              — default ~/.rowboat/knowledge/Marketing/People/
MARKETING_HASH_STORE             — Worker A/B SHA256 echo-defense state
MARKETING_IMAP_*                 — Worker C IMAP creds + poll-interval
```

---

## Tests

76 test files with 1285 test definitions (AST count) under `spaces/marketing`;
the drift guard `tests/test_cockpit_contract.py` pins the number together with
`docs/COCKPIT_CONTRACT.md`. Run from `vibemind-os`:

```
python -m pytest spaces/marketing -q
python -m pytest spaces/marketing/tests/test_cockpit_contract.py -q
```

`scripts/conftest.py` excludes `real_case_test.py` (a command-line tool against
real mailboxes, not a test). Many older test modules also run as
`python -m <module>`. Regression guards worth knowing: `test_auth_guard`
(every mutating route is guarded), `test_versandsperre` (Gate 0),
`gate12_*` in `test_send_paranoid` (`delivered_at` stays NULL).

---

## Common pitfalls (real bugs I made)

- **`query_via_docker` wraps SQL in `SELECT ... FROM (<sql>) t`** — does
  NOT work for `INSERT ... RETURNING`. Use `execute_via_docker` + parse
  the stdout (drop the trailing `INSERT N M` status line). Bug fixed
  in commit `3aec7f1`.
- **PL/pgSQL variable names matching table columns** cause "ambiguous"
  errors on `ON CONFLICT (col_name)`. Use `v_<short>` prefixes that
  don't collide. Bug fixed in `36f0e47`.
- **Swarm-mode docker ports don't support hostip binding** — `"54325:1025"`
  binds 0.0.0.0:54325, not 127.0.0.1:54325. For loopback-only sinks,
  start an extra non-stack container. Bug fixed in `070a2a2`.
- **`audiences.member_count` is a cached column** — does NOT auto-update
  on `audience_members` INSERT. The approval stored function recomputes
  it inside the same transaction.

---

## When a future change wants to add a send-path

First: the operator decision of 2026-09-12 says sales-claw is the only way
out. A new channel normally means a new dispatcher in sales-claw, not here;
a send-path in this space needs an explicit new operator decision. If that
exists, it must do all four:

1. New migration that registers the channel in `marketing.external_sources`
   with `can_send=true` AND simultaneously drops the CHECK constraint
   for that row only (or rewrite the check to allow specific kinds).
2. New module in `spaces/marketing/tools/` that mirrors the 12-gate
   structure of `_send_paranoid.py`: kill-switch, freeze-file, allowlist,
   confirm-token, per-recipient pre-flight, atomic claim, post-send
   audit. Don't take shortcuts; the gates compose.
3. New 30+ tests covering each gate, plus a `never_calls_other_channels`
   regression-guard.
4. Update this file + STATUS.md before merging.

Code-review checklist: any commit that grows `_send_paranoid.py` or
adds new SMTP/HTTP-out-to-recipient calls is automatically suspect.
The reviewer should see `Co-Authored-By: <name>` in the commit and at
least one new gate-test.
