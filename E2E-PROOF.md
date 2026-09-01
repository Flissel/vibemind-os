# E2E proof: one plugin write through release, credential issuance and a real GitHub call

Date: 2026-08-31. Timestamps are UTC, as the daemon logs them; the shell that
ran the commands is on UTC+2.

Checkout: `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.worktrees/rowboat-e2e`,
branch `master`, on top of `5e78e84`.

---

## Headline

**One call, one run, the production wiring, no hand-added headers anywhere:
`provider_failed`.**

```text
09  OpenFang approval raised   id=ce1694d2-ed36-44fd-9483-5ae3a0baa63a
13    action_summary  = component edaa0cff…fa350 arguments 30c66642…119e
14    approve() -> {"status":200,"body":{"id":"ce1694d2-…","status":"approved"}}
16  invocation outcome: provider_failed after 1535ms
19  receipt: status=failed reason=provider_unavailable approvalId=ce1694d2-ed36-44fd-9483-5ae3a0baa63a
21  decision for ce1694d2-…: status=approved decided_by=api decided_at=2026-08-31T04:28:55.926100100Z
```

That is the intended outcome. The credential on the daemon's allowlist is an
obviously-fake test value, so GitHub rejecting the call is what success looks
like: the release landed, the credential was issued per call, the real
`HttpMcpProvider` carried it over HTTPS to `https://api.githubcopilot.com/mcp/`,
and only GitHub's authorization failed. `write_review_required` would have
meant the release never landed; `credential_missing` would have meant the
issuance never landed. Neither happened.

**This required a fix.** The first end-to-end run of this chain — documented in
section 7 below — found the release call unauthenticated and OpenFang refusing
it with 401, so every write stayed under review no matter who approved it. No
unit test on this branch could have caught it, because every unit test injects
its own `fetch`. The fix is in section 6.

---

## 1. What was run, exactly

### 1.1 The isolated OpenFang daemon

The shared daemon on `127.0.0.1:4200` was never stopped, restarted,
reconfigured, or sent a state-changing request. A separate daemon was started
from the same binary, on its own port, with its own `OPENFANG_HOME`, wiped and
recreated immediately before the recorded run so its log holds exactly one call:

| | |
| --- | --- |
| Binary | `E:/RustTargets/openfang-credential/release-fast/openfang.exe` (v0.5.1) |
| Port | `127.0.0.1:4273` (verified free before start) |
| `OPENFANG_HOME` | `…/scratchpad/openfang-e2e-home2` (created empty for this run) |
| `OPENFANG_ISSUABLE_CREDENTIALS` | `GITHUB_PAT_TOKEN` |
| `GITHUB_PAT_TOKEN` | an obviously-fake test value, generated for this run, present only in the daemon's process environment. **Its value appears nowhere in this report, in any log, or in any command echo.** |
| `api_key` | a throwaway value generated for this run, in the isolated `config.toml`. Also never printed. |

`config.toml` (api_key elided):

```toml
api_listen = "127.0.0.1:4273"
log_level  = "info"
api_key    = "<generated, never printed>"

[default_model]
provider = "ollama"
model = "llama3.2"
api_key_env = "OLLAMA_API_KEY"

[approval]
timeout_secs = 300

[memory]
decay_rate = 0.05
```

Launcher (`scratchpad/start-daemon2.sh`) — it sources the two generated values
from a file, so neither ever reaches a command line or a log:

```sh
set -a; . "$SCRATCH/e2e-secrets2.env"; set +a     # OPENFANG_API_KEY, GITHUB_PAT_TOKEN
export OPENFANG_HOME="$SCRATCH/openfang-e2e-home2"
export OPENFANG_ISSUABLE_CREDENTIALS=GITHUB_PAT_TOKEN
unset OPENFANG_API_KEY                            # the daemon reads its key from config.toml
exec "E:/RustTargets/openfang-credential/release-fast/openfang.exe" start
```

```sh
sh "$SCRATCH/start-daemon2.sh" > "$SCRATCH/daemon2.out.log" 2> "$SCRATCH/daemon2.err.log" &
```

Boot lines proving the allowlist took effect (reference **name** only):

```text
2026-08-31T04:28:39.050894Z  INFO openfang_api::server: Credential issuance enabled for 1 reference(s): GITHUB_PAT_TOKEN
2026-08-31T04:28:39.252884Z  INFO openfang_api::server: OpenFang API server listening on http://127.0.0.1:4273
```

State before the run:

```text
$ curl http://127.0.0.1:4273/api/approvals
{"approvals":[],"total":0}
```

The daemon was **not** weakened to make this pass. Its auth is exactly as
shipped — an unauthenticated `POST /api/approvals` is still refused:

```text
POST /api/approvals (no auth header) -> HTTP 401
```

### 1.2 MongoDB

The already-running replica set was used as-is and left running:
`mongodb://127.0.0.1:27017/rowboat`, container `rowboat-rs`. **The database was
not dropped** (unlike `test/plugins/live-mongo-runtime.test.ts`, which drops
it); this proof only inserts under fresh UUIDs.

### 1.3 The proof

`spaces/rowboat/rowboat/apps/rowboat/test/plugins/live-openfang-e2e.test.ts`

```sh
set -a; . "$SCRATCH/e2e-secrets2.env"; set +a
unset GITHUB_PAT_TOKEN      # Rowboat must hold no copy; the value can only come from OpenFang
export ROWBOAT_LIVE_MONGO_URL="mongodb://127.0.0.1:27017/rowboat"
export ROWBOAT_LIVE_OPENFANG_URL="http://127.0.0.1:4273"
export ROWBOAT_E2E_EVIDENCE="$SCRATCH/e2e-evidence2.log"
cd .../apps/rowboat && npx vitest run test/plugins/live-openfang-e2e.test.ts
```

The Rowboat process had `OPENFANG_URL` and `OPENFANG_API_KEY` set, and **no
`GITHUB_PAT_TOKEN` of its own** — the test asserts that
(`expect(process.env.GITHUB_PAT_TOKEN).toBeUndefined()`, evidence line 08), so
the credential could only have come from OpenFang.

The test skips itself cleanly when those variables are absent, so it does not
disturb `npm run test:plugins`.

---

## 2. What is real, and what is composed by hand

Real, production classes, no stubs:

| Piece | Class actually used |
| --- | --- |
| catalog / installations / admissions / receipts / dispatch claim | `MongodbPluginsRepository` + `MongoPluginTransactionRunner` over the live replica set |
| project + draft workflow | `MongodbProjectsRepository` |
| index bootstrap | `ensureAllIndexes` (what `npm run mongodb-ensure-indexes` runs) |
| catalog seed | `readCatalogLock` from `scripts/load-plugin-catalog.ts`, the pinned `config/openai-plugin-catalog.lock.json` |
| tool binding | `AddPluginToolUseCase` |
| write/read classification | `classifyPluginOperation` — the real one the container wires |
| release gate | `resolveOpenFangReleaseWrite` (exported from `di/plugins-container.ts`) driving the real `OpenFangWriteReleasePolicy` over real HTTP, **with the plain global `fetch`** |
| provider + credential | `resolveOpenFangComposedProvider` → `resolvePluginProvider` → real `HttpMcpProvider`, with `OpenFangCredentialResolver` chosen by `resolveOpenFangCredentialSource` from the live environment |
| runtime | `PluginToolRuntime` |
| timeouts | `resolveOpenFangApprovalWindowMs` / `deriveRuntimeDeadlineMs` / `resolveOpenFangCredentialTimeoutMs` → 120000 / 180000 / 5000 ms |

Composed by hand: only the `PluginToolRuntimeDependencies` object itself,
because the container's `createPluginControllers()` needs Auth0/session
machinery this proof has no business standing up. Every field of it is an
exported composition seam called the way `createToolRuntime` calls it —
including `fetchImpl: fetch`, the same global `fetch` production passes.
**There is no injected or wrapped `fetch` anywhere on the path under test.**

The operation driven is `create_issue` on the pinned `github` plugin's MCP
component — a genuine write. The pinned component declares no
`readOnlyOperations`, so the real classifier calls it a write and the release
gate applies.

---

## 3. The approval request in OpenFang

Raised by `OpenFangWriteReleasePolicy`, read back from `GET /api/approvals`:

```json
{
  "id": "ce1694d2-ed36-44fd-9483-5ae3a0baa63a",
  "agent_id": "97b0037d-0aae-4d12-a049-d4d898a2f2fb",
  "tool_name": "create_issue",
  "description": "Rowboat plugin write: github",
  "action_summary": "component edaa0cfffb94f6829ce551b57fa130cd5a448551c23698673938effe2e0fa350 arguments 30c66642367c7380035516a04ec0e3fcb0e1d4cec879eaccf963dbd5cbef119e",
  "risk_level": "high",
  "requested_at": "2026-08-31T04:28:55.771889600Z",
  "timeout_secs": 300,
  "status": "approved",
  "decided_at": "2026-08-31T04:28:55.926100100Z",
  "decided_by": "api"
}
```

`agent_id` is the Rowboat project id. `action_summary` carries the component
digest and the arguments digest **and no argument values**. Checked, not
assumed:

- The arguments actually sent were
  `{"owner":"rowboat-e2e-proof","repo":"does-not-exist","title":"openfang release proof"}`.
- Recomputing the runtime's digest independently
  (`sha256("rowboat:plugin-tool-runtime:arguments:v1" || 0x00 || canonicalJSON)`)
  gives `30c66642367c7380035516a04ec0e3fcb0e1d4cec879eaccf963dbd5cbef119e` —
  **identical** to the one in `action_summary`.
- Searching for each of the three argument values in `GET /api/approvals` and
  in `plugin_receipts`: **0 occurrences each.**

**The decision.** The human's role was played by an authenticated
`POST /api/approvals/{id}/approve` (a human at the dashboard authenticates
too). The decision is read back **from OpenFang** afterwards rather than taken
from the approve call's own answer — evidence line 21:

```text
decision for ce1694d2-ed36-44fd-9483-5ae3a0baa63a:
  status=approved  decided_by=api  decided_at=2026-08-31T04:28:55.926100100Z
```

---

## 4. The daemon's own log for this call

The complete non-routine log of this daemon's lifetime (the release policy's
1-second `GET /api/approvals` polls are elided; nothing else is omitted). No
credential value appears — only the reference **name**:

```text
2026-08-31T04:28:55.771907Z  INFO openfang_api::middleware: API request method=POST path=/api/approvals status=201 latency_ms=0
2026-08-31T04:28:55.772157Z  INFO openfang_kernel::approval:  Approval request submitted, waiting for resolution request_id=ce1694d2-ed36-44fd-9483-5ae3a0baa63a
2026-08-31T04:28:55.926105Z  INFO openfang_kernel::approval:  Approval request resolved  request_id=ce1694d2-ed36-44fd-9483-5ae3a0baa63a decision=Approved
2026-08-31T04:28:55.926169Z  INFO openfang_api::middleware: API request method=POST path=/api/approvals/ce1694d2-ed36-44fd-9483-5ae3a0baa63a/approve status=200 latency_ms=0
2026-08-31T04:28:56.822228Z  INFO openfang_api::routes:      Credential issued reference=GITHUB_PAT_TOKEN
2026-08-31T04:28:56.822277Z  INFO openfang_api::middleware: API request method=POST path=/api/credentials/issue status=200 latency_ms=0
```

Two things worth reading twice:

- **`POST /api/approvals` → 201 on the first attempt.** Grepping this daemon's
  entire log for `status=401`: **0 hits.** Before the fix, that same POST was
  the 401 that ended the chain.
- **`Credential issued reference=GITHUB_PAT_TOKEN`** — the reference name, no
  value.

Leak checks, all **0 hits**: the fake credential value and the api_key, each
against the daemon's stdout, the daemon's stderr, and the test evidence log.

---

## 5. The invocation, and why `provider_failed` means what it says

```text
invocation window : 2026-08-31T04:28:55.664Z .. 2026-08-31T04:28:57.199Z
invocation outcome: provider_failed after 1535 ms
```

From the code, not from optimism:

- `write_review_required` is thrown when `evaluateCapability` refuses, which
  happens whenever the release did **not** elevate the per-call policy.
  Getting past it means the release landed.
- `credential_missing` is a **distinct** code.
  `HttpMcpProvider.#resolveCredential` raises `CredentialResolutionError` on
  any credential failure, the provider reports `reason: "credential_missing"`,
  and the runtime maps that reason to the `credential_missing` error code
  specifically (`plugin-tool-runtime.ts`:
  `result.reason === "credential_missing" ? "credential_missing" : "provider_failed"`).
  `provider_failed` is therefore only reachable **after** the credential
  resolved to a value and the HTTP exchange itself failed.
- The daemon confirms it independently, inside the window:
  `Credential issued` at 04:28:56.822, 0.90 s after the approval.

The remaining ~377 ms (04:28:56.822 → 04:28:57.199) is the HTTPS exchange with
`api.githubcopilot.com`. Section 8 shows what came back.

---

## 6. The fix this run required

### 6.1 What was wrong

`OpenFangWriteReleasePolicy.release()` posted to `/api/approvals` with
`headers: { "content-type": "application/json" }` and nothing else.
`OpenFangWriteReleaseOptions` had no field for a token, and
`resolveOpenFangReleaseWrite` constructed the policy with only `baseUrl`,
`fetch`, `timeoutMs`, `pollIntervalMs` — even though the same composition
already reads `OPENFANG_API_KEY` for the credential path two functions above.

OpenFang's auth middleware (`crates/openfang-api/src/middleware.rs`) makes
`/api/approvals` public for **GET only**:

```rust
|| (path == "/api/approvals" && is_get)
```

So every create POST was answered 401, `#fetchBounded` threw, `release()`
returned `unavailable`, and the runtime kept the write under review.

And it could not be configured around. The two requirements were the same
predicate, inverted:

- the middleware skips auth entirely **iff** `api_key.trim().is_empty() && !auth.enabled`;
- `issue_credential` (`crates/openfang-api/src/routes.rs`) refuses outright
  **iff** `api_key.trim().is_empty() && !auth.enabled` — its deliberate
  fail-open refusal.

On any one daemon, at most one of {unauthenticated `POST /api/approvals`
succeeds, credential issuance works} could hold. Dashboard session auth was no
escape either: the release policy sends no cookie.

### 6.2 What changed

`src/infrastructure/policies/openfang.plugin-write-release.policy.ts`

- `OpenFangWriteReleaseOptions` gains an optional `apiKey`.
- A new private `#authorized(init)` adds `Authorization: Bearer <apiKey>`
  while preserving whatever headers the request already carries (the create
  POST's `content-type`).
- It is applied inside `#fetchBounded`, so it covers **every** exchange the
  adapter makes — the create POST and each poll GET alike. Only the POST is
  refused by OpenFang's current middleware, but a daemon may protect the GET
  too, and a release that can be created but never read back is no release at
  all.

`di/plugins-container.ts`

- `resolveOpenFangReleaseWrite` reads `process.env.OPENFANG_API_KEY`, trims it
  the same way `resolveOpenFangCredentialSource` trims its own, and passes it
  through — per call, not memoized, so a config change takes effect on the
  next call.

### 6.3 What deliberately did **not** change

An absent or blank key is **not** a precondition for asking. With no key the
adapter sends no `Authorization` header at all — never an empty `Bearer ` —
and if the daemon then answers 401 the policy fails closed to `unavailable`
and the write stays under review, exactly as before. This is the opposite
choice from `resolveOpenFangCredentialSource`, which *does* refuse to
construct without a key, and deliberately so: there, a missing key would mean
sending a plaintext credential to an unauthenticated endpoint; here, the
request carries only identifiers and digests, and refusing to ask would turn a
configuration gap into a silent refusal of every write.

### 6.4 The tests, written before the fix

Both suites failed on exactly the new assertions before the change (4 failures)
and pass after.

`test/plugins/openfang-write-release.test.ts` (+5):

- the header is present and carries the configured key on the create POST
  **and on the poll GET** — asserting the exact value `Bearer <key>`, not just
  that a request happened;
- the create POST's `content-type` survives alongside the new bearer;
- with no key: no `Authorization` header at all, and a 401 still fails closed
  to `unavailable`;
- with a blank or whitespace-only key: still no header, never an empty bearer;
- the key never reaches the request body, the URL, or a thrown message.

`test/plugins/plugin-openfang-container-wiring.test.ts` (+3):

- `resolveOpenFangReleaseWrite` passes `OPENFANG_API_KEY` through to the policy;
- it trims what it passes;
- absent/blank key → no `apiKey` on the options, **and the release is still
  attempted** (the policy is still constructed, the decision still returned) —
  pinning that a missing key never becomes a reason to skip the release.

---

## 7. The original finding, kept — why the fix exists

The first end-to-end attempt ran the chain twice in one process against an
earlier isolated daemon (port 4272), and is the reason any of section 6 was
written.

**Run A — the production wiring as it then stood:**

```text
RUN A outcome: write_review_required
approval raised in OpenFang: NONE
daemon: 2026-08-31T04:14:49.274204Z  method=POST path=/api/approvals status=401
```

One release POST, refused, no approval ever created — so no human could have
released anything, and the runtime's fail-closed path did exactly what it
should with a release it could not obtain.

**Run B — identical in every respect except one hand-added header**
(`fetchImpl` wrapped to add `Authorization`, using the seam
`resolveOpenFangReleaseWrite` already exposes):

```text
RUN B outcome: provider_failed after 1682ms
approval:     b40c144d-dc0c-47a6-9d47-ec70694a112c (approved)
receipt:      status=failed reason=provider_unavailable approvalId=b40c144d-…
```

That pair isolated the defect to precisely one missing header: everything
downstream of the release already worked. Section 6 removes the hand-added
header by putting it where it belongs, and section 1–5 above is the same chain
re-run with nothing added by hand.

The two runs also produced the paired receipts that show both directions of
the gate — the refused one carrying **no** `approvalId` (correct: no decision
was ever made) and the released one carrying it. Those documents are still in
`rowboat`, under projects `025b54f6-…` and `959818ae-…`.

---

## 8. The separate direct HTTPS probe against GitHub

Run outside Rowboat entirely, with the same fake token this run's daemon
issued, so `provider_failed` cannot be mistaken for a network fault.

### 8.1 `curl`, two variants against the identical endpoint

```sh
curl -sS -m 25 -D - -X POST https://api.githubcopilot.com/mcp/ \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  [-H "Authorization: Bearer $GITHUB_PAT_TOKEN"] \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize", …}'
```

**(a) no Authorization header at all**

```text
connect=0.104s tls=0.213s total=0.315s   HTTP 401
www-authenticate: Bearer error="invalid_request",
                  error_description="No access token was provided in this request"
body: bad request: missing required Authorization header
```

**(b) the same fake token the daemon issued**

```text
connect=0.104s tls=0.218s total=0.325s   HTTP 401
www-authenticate: Bearer error="invalid_token",
                  error_description="Token is not authorized"
x-github-request-id: 2E14:3B4826:4049167:595BC42:6A95032D
body: unauthorized: AuthenticateToken authentication failed
```

The two 401s are **different**: `invalid_request` / "No access token was
provided" versus `invalid_token` / "Token is not authorized". GitHub read the
token, evaluated it, and rejected it. TCP connected in ~104 ms and TLS
completed in ~218 ms — the host is reachable and the answer is GitHub's, not a
transport failure.

### 8.2 The same MCP SDK transport the provider uses

`HttpMcpProvider` connects through `StreamableHTTPClientTransport` + `Client`.
Driven by hand with the same fake token, from `packages/openai-plugin-runtime`:

```text
elapsed_ms                     : 359
error.constructor              : StreamableHTTPError
instanceof StreamableHTTPError : true
error.code                     : 401
error.message                  : Streamable HTTP error: Error POSTing to endpoint:
                                 unauthorized: AuthenticateToken authentication failed
triggers SSE fallback (404/405)?: false
```

This is exactly what the run's provider saw: a `StreamableHTTPError` with code
401 — not 404/405, so `SdkHttpMcpClient.connect` does **not** raise
`McpCompatibilityError` and there is no SSE retry; the error propagates to
`HttpMcpProvider.invoke`'s catch, which is neither a timeout nor a credential
error, so `reason: "mcp_http_failed"` → runtime `provider_failed`. The 359 ms
matches the ~377 ms observed inside the run.

---

## 9. The receipt, as stored in `plugin_receipts`

Read back raw from the live collection (`MongodbPluginsRepository` stores
`{_id, receiptId, payload}` with the canonical receipt JSON as `payload`).
**Exactly one receipt for this project** — one call, one record, no refused
attempt in front of it:

```json
{
  "_id": "6a950309b25b6ad6f7df6f4b",
  "receiptId": "6af08002-e239-4c03-9cdc-7fbf514d5770",
  "payload": {
    "type": "execution", "status": "failed", "reason": "provider_unavailable",
    "pluginName": "github", "componentKind": "mcp",
    "projectId": "97b0037d-0aae-4d12-a049-d4d898a2f2fb",
    "receiptId": "6af08002-e239-4c03-9cdc-7fbf514d5770", "redactions": [],
    "output": {
      "approvalId": "ce1694d2-ed36-44fd-9483-5ae3a0baa63a",
      "capability": "write",
      "catalogDigest": "11035eb884d88be51337853010fc67502f8f6ced64287382a3bb56d24a8c524e",
      "componentDigest": "edaa0cfffb94f6829ce551b57fa130cd5a448551c23698673938effe2e0fa350",
      "installationId": "b95af29c-bb1b-40b4-9cf2-6fcd83d0af40",
      "installationRevision": 1, "providerBindingId": "mcp.github"
    }
  }
}
```

`output.approvalId` is **the same UUID** OpenFang decided in section 3. The
human's release and the call that consumed it are joined by that id, across two
systems.

Note on the receipt vocabulary: `reason` is a four-value enum
(`credential_missing | provider_unavailable | execution_state_changed |
write_review_required`), so a `provider_failed` invocation is recorded as
`status: "failed", reason: "provider_unavailable"`. That is the runtime's
existing mapping, not something introduced here — but it does mean the receipt
alone cannot distinguish "the provider could not be built" from "the provider
ran and the remote refused". The error code returned to the caller does.

The receipt contains no argument value (0 occurrences of each of the three) and
no credential value.

---

## 10. Verification commands and their results

Run separately, as required; the typecheck from inside `apps/rowboat`.

```text
$ npm run test:plugins
  Test Files  28 passed | 2 skipped (30)
       Tests  692 passed | 3 skipped (695)
```

Baseline in this worktree was 684 passed / 2 skipped. The delta is **+8
passed** (5 new in `openfang-write-release.test.ts`, 3 new in
`plugin-openfang-container-wiring.test.ts`) and **+1 skipped**
(`live-openfang-e2e.test.ts`, which skips without the three live env vars —
alongside the pre-existing live-mongo gate and the evidence test that has no
artifacts in a fresh checkout).

```text
$ npx tsc --noEmit -p tsconfig.json      # from apps/rowboat
  exit 0
```

One caveat worth recording so the next person does not chase it: on a fresh
checkout `tsc` first reported 6 `TS2307: Cannot find module '@/public/*.png'`
errors. Those are not from this change — they are the absence of
`next-env.d.ts`, which is gitignored and generated by `next build`/`next dev`
and is listed in `tsconfig.json`'s `include`. Generating that file (the
standard two-line Next reference stub) gives exit 0, as above.

---

## 11. What is proven, and what is not

**Proven, in one run, against real components, with the production wiring:**

1. The pinned catalog seeds into a real MongoDB replica set (180 entries at
   `11035eb884d8…`), the `github` plugin installs, and its admitted MCP
   component binds as a workflow tool through `AddPluginToolUseCase`.
2. A plugin write raises a real approval in a real OpenFang daemon —
   authenticating itself, first attempt, 201 — carrying the component digest
   and an arguments digest that was recomputed independently and matched, and
   carrying **no argument values**.
3. A human decision (`approved`, read back from OpenFang) on that approval
   elevates that one call's policy in Rowboat.
4. The released call asks OpenFang for the credential per call and gets it:
   `POST /api/credentials/issue → 200`, logged as `Credential issued
   reference=GITHUB_PAT_TOKEN`. Rowboat held no standing copy — the process
   had no `GITHUB_PAT_TOKEN` at all, asserted in the test.
5. The real `HttpMcpProvider` carried that value over HTTPS to
   `https://api.githubcopilot.com/mcp/`, and GitHub rejected it. The rejection
   is GitHub's, not the network's: two different 401s for "no token" versus
   "this token", a 104 ms TCP connect, a 218 ms TLS handshake, and the same
   `StreamableHTTPError code=401` reproduced with the provider's own SDK
   transport.
6. The outcome is recorded in `plugin_receipts`, stamped with the approval id,
   carrying no arguments and no secrets.
7. No credential value reached any log: 0 hits in the daemon's stdout/stderr,
   0 in the test evidence log, 0 in the receipt, 0 in OpenFang's approval
   records.

**Not proven, and not claimed:**

- **That GitHub accepts anything.** The token is fake by design; only the
  rejection path is exercised. A real token, a successful `create_issue`, and a
  `status: "success"` receipt remain unproven.
- **That the Next.js container itself behaves this way end to end.** The
  runtime dependency object was assembled by the test from the container's
  *exported* seams (`resolveOpenFangComposedProvider`,
  `resolveOpenFangReleaseWrite`, `resolveOpenFangApprovalWindowMs`,
  `deriveRuntimeDeadlineMs`, `resolveOpenFangCredentialTimeoutMs`,
  `classifyPluginOperation`) rather than by calling
  `createPluginControllers()`, which needs Auth0/session infrastructure. The
  seams are the real ones, and they are now called with the same global `fetch`
  production uses; the assembly is still the test's.
- **Anything about revocation latency, rotation, or concurrent calls.** One
  call, once.
- **That the poll GET's new bearer is exercised against a daemon that requires
  it.** OpenFang currently leaves `GET /api/approvals` public, so the header
  rides along unverified there in the live run; only the unit test pins that it
  is sent.
- **Anything about the shared daemon on :4200.** It was not exercised, not
  configured, not restarted, and is still running.

---

## 12. Cleanup

- The isolated daemon on port 4273 was stopped after the evidence above was
  collected, and its `OPENFANG_HOME` under the scratchpad deleted along with
  the generated-secrets file. The earlier 4272 daemon from section 7 was
  stopped and removed the same way.
- The `rowboat-rs` MongoDB container was left running, untouched, and its
  database was never dropped. The projects this proof inserted and their
  receipts remain in `rowboat` as evidence.
- The shared OpenFang daemon on :4200 was never touched.

---

# Part II — component-scoped installation, proven against Cloudflare

Date: 2026-09-01 (UTC). Branch `claude/rowboat-w3-component-install-v1`
(`737f32cb`, two commits on top of `c4f869df`), same checkout as Part I.

Test: `apps/rowboat/test/plugins/live-component-install-e2e.test.ts` — opt-in
by the same three environment variables as Part I. One run, one test, green:

```text
 ✓ test/plugins/live-component-install-e2e.test.ts (1 test) 4411ms
```

## II.1 Headline

**Before this branch, `cloudflare` could not be installed at all.** Thirteen of
its fourteen components are `review_required`; the install gate was
whole-plugin, so the one admitted component (the `cloudflare-api` MCP server)
was unreachable. This run installs exactly that component through the real
`InstallPluginUseCase`, binds it as a workflow tool, releases a call through a
real OpenFang approval, reaches `https://mcp.cloudflare.com/mcp` over HTTPS,
and stops at Cloudflare's own 401 — the same shape as Part I's GitHub proof.

```text
04  cloudflare: 14 components, 1 admitted, selecting cloudflare-api 9d39d5e6ba55...
05  install without a selection -> component_not_admitted
06  install with [9d39d5e6ba55...] -> receipt 98858cad-… status=success
07  installation 6d18b2d6-…: revision=0 providerBindings=["9d39d5e6ba55"] admissions=["cloudflare-api:admitted"]
08  same idempotency key, selection widened by a non-admitted component -> component_not_admitted
09  same idempotency key, same selection -> receipt 98858cad-… (replayed)
10  linear: 5 components, 5 admitted; install [671b665bbf1f...] -> receipt 8f013414-…
11  linear: same idempotency key, different admitted selection [mcp, app] -> idempotency_conflict
12  linear admissions after the conflict: ["linear:admitted"]
13  tool bound: plugin_cloudflare_cloudflare_api added=true
14  add-tool for a non-selected component -> provider_unavailable
16  OpenFang approval raised   id=c707c5b4-0d3d-434d-ba22-e4bb8ec3a620 tool_name=search
17    action_summary  = component 9d39d5e6ba55c780…3ebc6aed arguments d86f6f088747cc14…8fcfa78
18    approve() -> {"status":200,"body":{"decided_at":"2026-09-01T20:45:21.296384100+00:00","id":"c707c5b4-…","status":"approved"}}
19  invocation outcome: provider_failed after 1300ms; approval raised: c707c5b4-…
20  runtime receipt: type=execution status=failed reason=provider_unavailable componentDigest=9d39d5e6ba55... approvalId=c707c5b4-…
21  decision for c707c5b4-…: status=approved decided_at=2026-09-01T20:45:21.296384100Z
```

## II.2 What each line proves

- **05** — the old refusal is intact: with no selection, "all components" is
  meant, and `cloudflare` still fails closed with `component_not_admitted`.
- **06/07** — with `componentDigests: [9d39d5e6…]` the real use case admits
  the selection, and the real Mongo repository stores **exactly one**
  admission row and **exactly one** provider binding — the selection, not the
  plugin, is what was installed. Asserted, not eyeballed.
- **08** — widening the same idempotency key by a non-admitted component is
  refused before the key is even compared: admission runs before the
  idempotent write. Fail-closed either way.
- **09** — the same key with the same selection replays the same receipt
  (`98858cad…` both times). The fingerprint binds the selection digest.
- **10–12** — the genuine idempotency conflict needs two selections that are
  both admitted, which `cloudflare` cannot offer. `linear` (five admitted
  components) does: same key, `[mcp]` then `[mcp, app]` →
  `idempotency_conflict`, and the installation still holds one admission row.
- **13/14** — `AddPluginToolUseCase` binds the selected component and refuses
  a non-selected one (it has no provider binding, so the use case reports
  `provider_unavailable`; a more specific name is a follow-up, the refusal is
  what matters).
- **16–21** — identical mechanics to Part I: the runtime classifies `search`
  as a write (the pinned component declares no `readOnlyOperations`), OpenFang
  raises an approval carrying the component digest and an arguments digest
  and **no argument values** (asserted), a human decision releases the call,
  the real `HttpMcpProvider` reaches Cloudflare, Cloudflare rejects it, and
  the execution receipt lands in `plugin_receipts` stamped with the approval
  id.

## II.3 The rejection is Cloudflare's, not the network's

Direct probe from the same shell, same endpoint, real `initialize` body:

```text
(a) no Authorization header
    connect=0.023s tls=0.048s total=0.071s   HTTP/2 401
    www-authenticate: Bearer realm="OAuth",
        resource_metadata="https://mcp.cloudflare.com/.well-known/oauth-protected-resource/mcp"
    server: cloudflare   cf-ray: a3471f52…-TXL

(b) Authorization: Bearer <obviously fake value>
    connect=0.020s tls=0.046s total=0.287s   HTTP/2 401
    www-authenticate: Bearer realm="OAuth", resource_metadata="…", error="invalid_token"
```

Two different 401s — an OAuth challenge for "no token" versus
`error="invalid_token"` for "this token" — and the second takes four times as
long: Cloudflare read the token and evaluated it. TCP and TLS complete in
tens of milliseconds. The run's 1300 ms is the approval round-trip plus this.

## II.4 The finding this run forces into the open

**`credentialSlots: []` does not mean "no credential".** The pinned catalog
declares no credential slot for the `cloudflare`, `linear` and `notion` MCP
components, and the GitHub component declares `GITHUB_PAT_TOKEN`. All four —
every admitted MCP component in the 180-entry catalog — are OAuth-gated at the
provider and answer 401 to an unauthenticated `initialize`. So:

- Part I and Part II together prove the **full mechanical chain** for the
  only two component shapes the catalog has (a declared slot, no slot).
- Neither proves a provider **accepting** a call. For github, OpenFang can
  issue the slot; for cloudflare/linear/notion there is no slot for OpenFang
  to fill, so today's issuance path cannot carry an OAuth bearer to them at
  all. That is a design gap, not a bug in this branch: it needs a decision on
  how an operator-obtained OAuth token enters OpenFang's allowlist and under
  which slot name the provider binding asks for it.
- "118 admitted components" (the catalog-wide number) is **not** "118
  executable components": 114 are `app`, `asset` and `skill` components with
  no runtime call path. The executable set is the four MCP components, all
  four gated. This branch makes three of them installable that previously
  were not (cloudflare, linear, notion — each sits beside non-admitted
  siblings); github was installable before.

## II.5 What is proven, and what is not

**Proven:** component-scoped install through the real use case and the real
repository, exact admission/binding cardinality, replay and conflict semantics
of the selection-bound idempotency key, tool binding restricted to the
selection, and the unchanged release → approval → provider → receipt chain for
a component with no credential slot.

**Not proven, not claimed:** any provider accepting a call; the UI dialog
(covered by unit tests only — `plugin-ui-state.test.ts`, including the
`priorPreviewToken` re-preview); the REST route (`strictObject` +
`componentDigests` covered by unit tests; the 64-element array bound is a
parked finding); the Next.js container end to end (same caveat as Part I).

## II.6 Cleanup

The isolated daemon on :4273 (a fresh `OPENFANG_HOME` under the scratchpad,
generated api_key never printed) was stopped after this run and its home and
secrets file deleted. `rowboat-rs` stays up with the two projects this run
inserted as evidence. The shared daemon on :4200 was never touched.
