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
  four gated. This branch makes three plugins installable that previously were
  not (cloudflare, github, notion — each has exactly one admitted MCP
  component beside non-admitted siblings); linear is 5/5 admitted and was
  always installable.

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

---

# Part III — a provider ACCEPTING a released call

Date: 2026-09-02 (UTC). `master` at the commit adding
`apps/rowboat/test/plugins/live-credential-acceptance-e2e.test.ts` — the
opt-in test that produced every line below in one run, one test, green:

```text
 ✓ test/plugins/live-credential-acceptance-e2e.test.ts (1 test) 5716ms
```

## III.1 Headline

Parts I and II proved the chain up to the provider's own refusal and said so
plainly: *"Neither proves a provider accepting a call."* This run closes that
claim. A real fine-grained GitHub PAT was placed in the isolated daemon's
process environment (never on disk beyond the user's own `.env`, never in
this repository, never in any log), allowlisted as the one issuable
reference, and the same chain that ended in 401s now ends in GitHub's answer:

```text
04  install with [edaa0cfffb94...] -> receipt 153f200b-… status=success
05  tool bound: plugin_github_github added=true
06  runtime composed against http://127.0.0.1:4273; operation=get_me
    (no readOnlyOperations declared -> classified write)
07  OpenFang approval raised   id=63618280-… tool_name=get_me
08    action_summary  = component edaa0cff…e2e0fa350 arguments 56551660…0706980
09    approve() -> {"status":200,"body":{"status":"approved", …}}
10  invocation outcome: success after ~3.6s; approval raised: 63618280-…
11  provider result: status=success, authenticated github login=Vibemind-LAB
12  execution receipt: status=success reason=undefined approvalId=63618280-…
13  decision for 63618280-…: status=approved decided_at=2026-09-02T22:40:58Z
```

## III.2 What this run proves

- **The credential transport the user chose works end to end.** OpenFang's
  `/api/credentials/issue` released `GITHUB_PAT_TOKEN` for exactly this call
  (the daemon logged `Credential issuance enabled for 1 reference(s):
  GITHUB_PAT_TOKEN` at boot); `HttpMcpProvider` put it on the wire as a
  bearer; `https://api.githubcopilot.com/mcp/` accepted it and answered the
  `get_me` tool call with the authenticated identity. Rowboat held no
  standing copy at any point — the test process itself only ever held the
  daemon's api_key.
- **The operation was chosen to be side-effect-free while still exercising
  the write gate.** The pinned github component declares no
  `readOnlyOperations`, so the real classifier routes `get_me` through the
  release gate: approval raised, human-role decision, THEN issuance, THEN
  the provider call. Maximal path, zero mutation on GitHub.
- **The install was component-scoped (W3).** github is 1-of-8 admitted; the
  selection `[edaa0cff…]` produced exactly one admission row and one
  provider binding, and the tool bound against that selection.
- **The approval never carried argument values** — `get_me` takes `{}`, and
  the summary shows only the component digest and the arguments digest
  (asserted in the test for Parts I–III alike).
- **The receipt closes the audit loop**: `status=success`, the component
  digest, and the very approval id OpenFang shows as `approved`.

## III.3 What is still not proven

- Acceptance for the OAuth-resource components (cloudflare, linear, notion):
  their references normalize to the resource URL, which OpenFang's
  `^[A-Za-z_][A-Za-z0-9_]{0,127}$` reference rule refuses, so today the
  chain fails closed at `credential_missing` before any network I/O for
  linear/notion, and cloudflare (no declaration at all) still goes out
  unauthenticated. The name-derivation fix is designed
  (`OAUTH_BEARER_<HOST_PATH>`) and is Rowboat-side only.
- Anything on the shared `:4200` daemon, which runs key-less and refuses
  issuance by design. This proof used an isolated daemon on `:4273`,
  destroyed after the run.

## III.4 Cleanup and hygiene

The PAT was read from the user's root `.env` by the daemon start script at
launch time and existed only in that process's environment; no second copy
was written to disk. Post-run scans: `github_pat_` appears 0 times in the
evidence log, both daemon logs, and the `plugin_receipts` collection; the
daemon api_key appears 0 times in the evidence. The `:4273` daemon was
stopped and its home (including the generated api_key) deleted; `rowboat-rs`
keeps the run's two projects as evidence; `:4200` was never touched.

---

# Part IV — the connector bridge, up to OpenAI's own 401

Date: 2026-09-03 (UTC). `master` at the commit adding
`apps/rowboat/test/plugins/live-connector-bridge-e2e.test.ts` — the opt-in
test that produced every line below in one run, one test, green:

```text
 ✓ test/plugins/live-connector-bridge-e2e.test.ts (1 test) 7666ms
```

## IV.1 Headline

Parts I–III proved the full mechanical chain for `mcp`-kind components. This
run closes the last uncovered shape: an `app`-kind component whose
`.app.json` declares a `connector_...` id, executed through the new
`ConnectorBridgeProvider` instead of `HttpMcpProvider`. By workboard policy
this repository has no OpenAI API budget (spec D6), so both credentials the
isolated daemon issued for this run are obviously-fake test values.
`ConnectorBridgeProvider` never surfaces an upstream HTTP status by design —
a non-200 response and a network failure both collapse to the same
`provider_failed` — so this run's own `provider_failed` outcome does not by
itself distinguish "reached OpenAI and was rejected" from "never left the
host". What this run shows, combined with independent evidence: the real
global `fetch` issued a POST per the provider's pinned request shape (IV.3),
and a `curl` against the identical endpoint with a fake key of the same
shape (IV.6, claim 3) answers `401` — together, the provider's POST reached
OpenAI and was rejected; the provider itself surfaces no upstream status:

```text
07  OpenFang approval raised   id=2610ea8b-ec52-40da-84d3-4ec35603cc80 tool_name=export_design
08    action_summary  = component 48550845d04c04e2eb7a4d2b849eb0264045933a7be4c299d335071c198cb85d arguments 565516602728af2eacf99cf56cbdb0a4d778f122af0d15e2f43e71b0e0706980
09    approve() -> {"status":200,"body":{"decided_at":"2026-09-03T07:42:41.486046700+00:00","id":"2610ea8b-ec52-40da-84d3-4ec35603cc80","status":"approved"}}
10  invocation outcome: provider_failed after 1792ms; approval raised: 2610ea8b-ec52-40da-84d3-4ec35603cc80
11  execution receipt: status=failed reason=provider_unavailable componentKind=app approvalId=2610ea8b-ec52-40da-84d3-4ec35603cc80
12  decision for 2610ea8b-ec52-40da-84d3-4ec35603cc80: status=approved decided_at=2026-09-03T07:42:41.486046700Z
```

A second, negative branch in the same run installs `actively`, an admitted
`app` component whose declared id is `asdk_app_6a15fca0d57c8191a204ffdd12fbbef2`
— no `connector_` prefix, no public invocation path outside ChatGPT (spec
D4):

```text
13  actively: 2 components, 2 admitted, selecting actively 4e69eeaa70b0... (asdk_app_6a15fca0d57c8191a204ffdd12fbbef2)
14  install with [4e69eeaa70b0...] -> receipt 588fff53-3a3c-4b2e-86ad-042cf916db2b status=success; bindings=1
15  tool bound: plugin_actively_actively added=true (no kind gate in AddPluginToolUseCase -- an asdk_app_ component binds exactly like a connector_ one)
16  OpenFang approval raised   id=63169442-f616-4ce3-be42-cc2dc96e91c2 tool_name=run_action
17    action_summary  = component 4e69eeaa70b0e44d212f92970fe3e285a21fccc8484b7d079247d75d67f0b4a2 arguments 565516602728af2eacf99cf56cbdb0a4d778f122af0d15e2f43e71b0e0706980
18    approve() -> {"status":200,"body":{"decided_at":"2026-09-03T07:42:45.484793+00:00","id":"63169442-f616-4ce3-be42-cc2dc96e91c2","status":"approved"}}
19  invocation outcome: provider_unavailable after 1094ms; approval raised: 63169442-f616-4ce3-be42-cc2dc96e91c2
20  execution receipt: status=failed reason=provider_unavailable componentKind=app approvalId=63169442-f616-4ce3-be42-cc2dc96e91c2
21  leak check: fake OPENAI_API_KEY / CONNECTOR_CANVA values and the daemon api_key -- 0 occurrences in stored receipts
```

Notice line 16: an OpenFang approval is raised, and approved, for `actively`
too — even though the call never reaches a provider and sends zero bytes to
anywhere. That is not a bug in this run; it is what the production code
actually does, pinned rather than assumed. See IV.4.

## IV.2 What was run, exactly

### IV.2.1 The isolated OpenFang daemon

The shared daemon on `127.0.0.1:4200` was never stopped, restarted,
reconfigured, or sent a state-changing request. A separate daemon was
started from the same binary, on its own port, with its own `OPENFANG_HOME`:

| | |
| --- | --- |
| Binary | `E:/RustTargets/openfang-credential/release-fast/openfang.exe` |
| Port | `127.0.0.1:4273` (verified free before start) |
| `OPENFANG_HOME` | `…/scratchpad/openfang-e2e-home6` (created empty for this run) |
| `OPENFANG_ISSUABLE_CREDENTIALS` | `OPENAI_API_KEY,CONNECTOR_CANVA` |
| `OPENAI_API_KEY` | `sk-obviously-fake-e2e-value` — obviously-fake by construction, present only in the daemon's process environment |
| `CONNECTOR_CANVA` | `obviously-fake-connector-token` — same |
| `api_key` | a throwaway value generated for this run (`openssl rand -hex 24`), in the isolated `config.toml`, exactly two lines. Never printed. |

`config.toml` (api_key elided):

```toml
api_listen = "127.0.0.1:4273"
api_key = "<generated, never printed>"
```

Boot line proving the comma-separated allowlist parsed correctly (reference
**names** only):

```text
INFO openfang_api::server: Credential issuance enabled for 2 reference(s): CONNECTOR_CANVA, OPENAI_API_KEY
INFO openfang_api::server: OpenFang API server listening on http://127.0.0.1:4273
```

The daemon was **not** weakened: an unauthenticated `POST /api/approvals`
was still refused before the test ran (`HTTP 401`), same as every prior
part.

### IV.2.2 MongoDB, and a stale-catalog collision this run found and fixed

The already-running replica set was used as-is: `mongodb://127.0.0.1:27017/rowboat`,
container `rowboat-rs`. The database was **not** dropped.

The first attempt at this run failed before ever reaching OpenFang, with
`installation_catalog_mismatch` thrown from
`MongodbPluginsRepository.requireCatalogEntryForInstallation`. Root cause,
confirmed by reading the stored documents directly: this shared, long-lived
database still held a **complete catalog snapshot and all 180 catalog
entries at the OLD, pre-Task-1 digest**
(`11035eb884d88be51337853010fc67502f8f6ced64287382a3bb56d24a8c524e`), left
over from live-test runs before the catalog was re-pinned. Task 1's re-sync
changed the `bindingDigest` of every `app`-kind component (it now pins
`appDeclaration`), but left each entry's `manifestDigest`/`treeDigest`/
`sourceCommit`/`pluginVersion`/`policyVersion` — the exact tuple
`requireCatalogEntryForInstallation` matches a plugin name against, across
*every* stored catalog digest, expecting exactly one hit — byte-identical to
the old catalog. With both digests present, `canva`'s (and `github`'s,
`cloudflare`'s, `linear`'s) entry tuple matched **two** stored catalog
documents, not one, and every install for every plugin in this shared
database failed closed with `installation_catalog_mismatch` — not only for
this run's `canva`/`actively` installs, but latently for the pre-existing
`github`/`cloudflare`/`linear` installations too, on their next lookup.

No `app`-kind (`canva`, or any other connector) installation existed yet in
this database — component-scoped app installs are new as of this task — so
the fix was narrow and safe: delete only the OLD-digest rows from
`plugin_catalog_snapshots` (1 document) and `plugin_catalog_entries` (180
documents), touching nothing else — no project, installation, admission,
credential-slot, or receipt document, in any collection, was read, modified
or deleted. Verified before and after:

```text
before: catalog_snapshots(old)=1   catalog_entries(old)=180
deleted entries: 180               deleted snapshots: 1
after:  catalog_snapshots total=1  catalog_entries total=180   (new digest only)
```

and, per plugin, exactly one stored catalog digest afterward:

```text
github/cloudflare/linear/canva -> ["5c9ea0690406824b3e78751ee0bc7765e3d4a7d0ae40afdbd9f4757c22666c94"]
```

This is an operational finding worth carrying forward, not a defect in this
task's own code: any shared, persistent database that accumulates catalog
snapshots across a digest re-pin can reproduce this exact collision, because
`requireCatalogEntryForInstallation` matches by content tuple across *all*
stored digests rather than by digest alone. A deployment that re-pins the
catalog should retire the superseded catalog rows as part of that
migration, the same way this run did by hand.

### IV.2.3 The proof

`apps/rowboat/test/plugins/live-connector-bridge-e2e.test.ts`

```sh
export OPENFANG_API_KEY="<the isolated daemon's api_key, from config.toml>"
export ROWBOAT_LIVE_MONGO_URL="mongodb://127.0.0.1:27017/rowboat"
export ROWBOAT_LIVE_OPENFANG_URL="http://127.0.0.1:4273"
export ROWBOAT_LIVE_CONNECTOR=1
export ROWBOAT_E2E_EVIDENCE="$SCRATCH/e2e-evidence6.log"
cd apps/rowboat && npx vitest run test/plugins/live-connector-bridge-e2e.test.ts
```

`ROWBOAT_LIVE_CONNECTOR` is a dedicated fourth gate variable, deliberately
not reusing `ROWBOAT_LIVE_ACCEPTANCE` from Part III — that would let this
run silently piggyback on a daemon provisioned only for the GitHub proof,
holding neither `OPENAI_API_KEY` nor `CONNECTOR_CANVA`. The test skips
itself cleanly without all four variables, so it does not disturb
`npm run test:plugins` (confirmed: 760 passed / 6 skipped / 0 failed in
`apps/rowboat`, one more skip than Part III's baseline for this new file).

## IV.3 What is real, and what is composed by hand

Same composition seams as Parts I–III (`resolveOpenFangComposedProvider`,
`resolveOpenFangReleaseWrite`, `resolveOpenFangApprovalWindowMs`,
`deriveRuntimeDeadlineMs`, `resolveOpenFangCredentialTimeoutMs`,
`classifyPluginOperation`), called the way `createToolRuntime` calls them,
with the real global `fetch`. `OPENAI_RESPONSES_MODEL` and `OPENAI_BASE_URL`
were deliberately left unset, so the kernel's own defaults were exercised —
`gpt-5.6` and `https://api.openai.com` — not a test-supplied override.

The provider under test is the real `ConnectorBridgeProvider`, resolved
through the real `resolvePluginProvider` app branch, from the real pinned
catalog record (`metadata.appDeclaration`, pinned by Task 1's re-sync) —
nothing about the provider construction is a mock. `canva`'s install was
component-scoped (W3): selecting `[48550845d04c…]` alone produced exactly
one admission row and one provider binding (line 04 of the evidence,
`admissions=1 bindings=1`), and that binding's `providerKind` is
`openai-connector-bridge` — asserted, not eyeballed.

`export_design` was chosen as the operation name for canva: a plausible,
deterministic string, never resolved against a real canva session in this
run, because the fake `OPENAI_API_KEY` draws OpenAI's own 401 before any
connector-side authorization could even be attempted.

## IV.4 The negative branch, and why an approval is raised for a component that can never run

`actively`'s app component is admitted by the catalog and binds as a tool
exactly like `canva`'s — `AddPluginToolUseCase` has no kind- or
id-shaped gate (line 15). The interesting question is what
`PluginToolRuntime.invoke()` does with it, and the answer was read from the
code, then confirmed live rather than assumed:

`PluginToolRuntime.invoke()` calls `releaseWrite` (the OpenFang release
gate) *before* it ever calls `resolveProvider` — the release sits earlier in
the method than resolution. So a write-classified call (every operation on
an app component is classified `write`; no `readOnlyOperations` is declared)
against `actively` still raises a real OpenFang approval, and if a human
approves it — as this run's `approveWhenRaised` did, exactly as it did for
`canva` — that approval is genuinely consumed, before resolution ever runs.
Only afterward does `resolvePluginProvider`'s app branch see the
`asdk_app_...` id, refuse it, and resolve `UNAVAILABLE` with no provider
ever constructed and no network I/O attempted; `exactProvider` turns that
into a thrown `provider_unavailable`.

The observed, pinned outcome is therefore: **one approval raised and
approved, zero bytes sent to any provider, call still ends in
`provider_unavailable`** (lines 16–20) — not "no approval at all", which an
a-priori reading of "resolution refuses before release" would have
predicted, and which this codebase, as it stands today, does not do. The
receipt still carries the consumed approval id (`63169442-…`, line 20),
exactly the way a write a human approved and that then died further
downstream still leaves a trace elsewhere in this runtime (see Part I §5).

## IV.5 Hygiene

Leak checks, all **0 hits**: both fake credential values
(`sk-obviously-fake-e2e-value`, `obviously-fake-connector-token`) and the
daemon's own `api_key`, searched across the evidence log, the daemon's
stdout, the daemon's stderr, and the two stored `plugin_receipts` documents
this run produced (asserted inside the test itself, and re-checked
separately against the daemon's own log files). The daemon's credential-
issuance log line names only the reference, never a value:

```text
INFO openfang_api::routes: Credential issued reference=OPENAI_API_KEY
INFO openfang_api::routes: Credential issued reference=CONNECTOR_CANVA
```

## IV.6 What is proven, and what is not

**Proven, in one run, against real components, with the production wiring:**

1. An `app`-kind catalog component with a pinned `connector_...` id installs
   component-scoped, binds as a tool, and its write is released through a
   real OpenFang approval — same mechanics as every MCP proof before it.
2. Both required credentials (`OPENAI_API_KEY`, then `CONNECTOR_CANVA`) are
   resolved through OpenFang's `/api/credentials/issue`, by name only, before
   any network I/O — confirmed by the daemon's own log naming both
   references issued, in order.
3. The real `ConnectorBridgeProvider`, using the real global `fetch`, issued
   a real POST to `https://api.openai.com/v1/responses` per its pinned
   request shape (IV.3): `provider_failed`, receipt `status:"failed"`,
   `componentKind:"app"`, stamped with the approval id.
   `ConnectorBridgeProvider` never surfaces an upstream HTTP status by
   design — a non-200 response and a network failure both collapse to the
   same `provider_failed` — so that outcome alone does not distinguish
   "reached OpenAI and was rejected" from "never left the host". Independent
   evidence closes that gap: a single `curl` against the identical endpoint,
   with a fake key of the same shape, run once for this proof:

   ```sh
   curl -s -o /dev/null -w '%{http_code}' https://api.openai.com/v1/responses \
     -H "Authorization: Bearer sk-obviously-fake-e2e-value" \
     -H "content-type: application/json" \
     -d '{}'
   # -> 401
   ```

   Together: the endpoint answers `401` to exactly this fake key, and the
   provider issued a real POST to that same endpoint per its pinned request
   shape — not that this run itself observed OpenAI's status code, which
   `ConnectorBridgeProvider` never surfaces to the caller.
4. An `asdk_app_...` app — admitted, installable, bindable — still raises
   and can consume a real OpenFang approval, but never reaches a provider
   and sends no network request: `provider_unavailable`, receipt likewise
   stamped with its own consumed approval id.
5. No credential value, and no daemon api_key, reached any log, evidence
   file, or stored receipt.

**Not proven, and not claimed:**

- **That OpenAI accepts anything.** Both credentials are fake by design
  (spec D6); only the rejection path is exercised. Whether the pinned
  `connector_...` hex ids are valid entries in OpenAI's own connector
  directory at all stays unproven until a real, accepted call is made — a
  later, explicit user decision, same posture as Part I's unproven GitHub
  acceptance before Part III closed it.
- **Anything about the Next.js container path itself**, for the same reason
  as every prior part: the runtime dependency object was assembled from the
  container's own exported composition seams, not via
  `createPluginControllers()`.
- **Anything about revocation, rotation, or concurrent calls.** Two calls,
  once, sequentially, in one project.
- **Anything about the shared daemon on `:4200`.** Not exercised, not
  touched.
- **Argument fidelity and single-execution across the model boundary.** The
  OpenFang approval's arguments digest binds the arguments this call
  *requested* of the model, not what the model actually executes; nothing
  bounds the model to calling the tool exactly once, and `allowed_tools`
  pins only the tool name, not call count or argument values. Unlike the MCP
  path (`HttpMcpProvider`, which calls the named tool directly with the
  exact arguments given), this is advisory here, not enforced, and this run
  neither exercises nor proves it either way.

## IV.7 Cleanup

The isolated daemon on port 4273 was stopped after the evidence above was
collected (killed by the PID holding that listening port), and its
`OPENFANG_HOME` (including the generated `config.toml` and its api_key)
deleted from the scratchpad. The `rowboat-rs` MongoDB container was left
running and untouched; its database was not dropped — the two projects this
run inserted, and their four receipts (two installs, two executions), remain
as evidence, alongside the OLD-digest catalog rows' removal from IV.2.2,
which is permanent (the superseded catalog can be re-synced from source at
the pinned commit at any time, but nothing in this codebase or its tests
reads it by that digest any more). The shared OpenFang daemon on `:4200` was
never touched.

---

# Part V — the plugin-setup-agent's own chain: proven as three adjacent halves

Date: 2026-09-11 (UTC). `master` at the commit adding
`apps/rowboat/test/plugins/live-setup-agent-e2e.test.ts`, updated by a
review-round-1 fix commit the same day. Checkout:
`C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.worktrees/setup-agent`.

**Fix round 1 (2026-09-11, same day):** an independent reviewer found the
first version of this section overclaimed what one assertion actually
established (§V.4's old "15-21" bullet — see the correction below), found
the docker-logs zero-control only bracketed one end of the window, and
found D could report a fully green verdict without ever checking the two
credential values that reach a real store. All three are fixed below, plus
two smaller items (approval-poll misattribution, a sleep-based race). This
section reflects the fixed test and a fresh live run, not the original one.

Parts I-IV proved the release/approval/provider chain from Rowboat's side of
a credential OpenFang already held. This part proves the piece before that:
the plugin-setup-agent's own job (`spaces/plugin-setup/`) of taking custody
of a credential in the first place — Supabase intake, provider verification,
and the handoff into OpenFang — on top of the same release chain.

## V.0 Two corrections to the plan this task was handed

The brief that drove this task (`.superpowers/sdd/2026-09-08-plugin-setup-agent/task-8-brief.md`)
named two paths that do not exist in this checkout:

1. It says to append this section to `spaces/rowboat/rowboat/E2E-PROOF.md`.
   That file does not exist; this document, at the **repository root**, is
   the one every prior part lives in, and is where this section landed.
2. It implies Rowboat's compose file lives under `apps/rowboat/`. It is
   actually at `spaces/rowboat/rowboat/docker-compose.yml` — not touched by
   this task (the environment was prepared and running before this task
   started, and stayed running throughout, per its own instructions).

## V.1 The contradiction, and the ruling that resolves it

The setup-agent's own gate is explicit about ordering
(`spaces/plugin-setup/werkzeuge.py::schluessel_entgegennehmen`, docstring
lines 16-47): Supabase intake, THEN `pruefung.pruefe()` against the real
provider, and OpenFang is only ever contacted **after** that verification
returns `gut: True`. No real provider credential was in scope for this task
— every value anywhere in this proof is obviously invented — so a value
this proof controls can never pass verification. "Verification passes and
OpenFang then takes custody" is therefore **not** a claim this proof can
make as one continuous run without faking the one gate the whole design
exists to enforce. It was not faked. Instead this proof runs three adjacent,
independently-live pieces, plus a hygiene pass that spans all three:

- **A — the fail-closed half**: Supabase intake, a REAL call to the real
  provider that is REAL-ly refused (GitHub's genuine 401 on an invented
  bearer token), `fehlschlagen()` recording only the status code, and the
  Supabase copy staying in place for diagnosis. This is the half the design
  exists to guarantee, and it is proven live, end to end, through the actual
  MCP tool (`werkzeuge.schluessel_entgegennehmen`), not a reimplementation
  of it.
- **B — the custody half, direct**: bypassing the verification gate on
  purpose and calling OpenFang's own `POST /api/credentials/store` and
  `POST /api/credentials/issue` directly, the same way Aufgabe 1's own
  design intends an already-verified value to be handed over. This proves
  the mechanism the agent's gate *depends on* works, independently of the
  gate itself.
- **C — the Rowboat half**: component-scoped install, tool binding,
  invocation, the release gate raising a real OpenFang approval, a
  decision, the real provider called over real HTTPS, and a receipt
  carrying the approval id — the same production seams Parts I-IV proved,
  run again here with a value this task placed into OpenFang's custody
  itself (via the exact mechanism B just proved), not a value the agent's
  gate ever verified.
- **D — hygiene, asserted, not claimed**: every invented value used across
  A-C is searched for — in `docker logs` (with a deliberate zero-control
  marker, because a prior finding on this task showed a value can leak into
  the Postgres server log via a *failing* statement), in `vault.secrets`,
  in the OpenFang daemon's own log file, in the Mongo `plugin_receipts`
  collection, and in this test's own evidence log — and asserted absent in
  every one, with a test that fails if any value shows up.

**What this does NOT prove, stated plainly** — four things, not one. The
first was named from the start; the other three were missing from this list
until the whole-branch final review found them, and they matter more,
because this section's own title claims "the plugin-setup-agent's own
chain" while three of that agent's four tools and its entire delivery
surface sit outside the proof.

1. **The verification→custody join.** That a value this proof controls can
   pass real verification and then be taken into OpenFang's custody, in one
   continuous run. That specific link — verification success immediately
   followed by this same value's custody handoff — is proven only as two
   separate mechanisms (A's gate refuses correctly; B's handoff mechanism
   works correctly when reached), not as one join. Closing that gap for real
   needs a real, accepted provider credential — the same posture Parts I/II
   left open and Part III later closed for the release/approval/provider
   chain. No decision was made here to obtain one; that stays a later,
   explicit choice, same as Part III's.
2. **D5's window is entirely unproven.** No test in this proof drives a
   browser, and the agent itself never ran. The visible Chrome window the
   operator is supposed to type into — the decision D5 exists for, and the
   reason this space carries its own openclaw gateway — has no evidence
   anywhere in Part V. `config/workspace/AGENTS.md` says so of itself
   ("UNGEKLAERT, nicht annehmen"); this list says it too, because a proof
   that stays silent about it reads as if it had been covered.
3. **No tool was called through MCP or openclaw at all.** A shells
   `python -c "import werkzeuge; ..."` straight into the module. That proves
   the Python function; it proves nothing about `server.py`, the
   streamable-HTTP transport on `:8131`, the tool registration, the
   container reaching the host sidecar through `host.docker.internal`, or
   the agent being able to call any of it. The delivery surface is untested.
4. **C bypasses the new REST route and two of the four tools.** It calls
   `InstallPluginUseCase` and `AddPluginToolUseCase` directly, with a
   hand-picked componentDigest — not
   `POST /api/v1/projects/:id/plugins`, and not
   `werkzeuge.plugin_installieren` / `werkzeuge.plugin_werkzeug_binden`.
   That is exactly why this proof did not catch the fact that
   `plugin_installieren` defaulted to *every* component and was therefore
   rejected with `component_not_admitted` for three of the four plugins that
   carry an admitted HTTP-MCP component (fixed in the final-fix round; its
   report lives in this checkout's plan workspace, which is gitignored, so
   that round's commit message carries the same accounting): the one path
   the agent is documented to walk was never walked here.

## V.2 The environment (prepared before this task, used as-is)

| | |
| --- | --- |
| OpenFang | already-running isolated daemon, `127.0.0.1:4273`, its own `OPENFANG_HOME` under this session's scratchpad, api_key read from a file, never printed or hardcoded |
| Mongo | `rowboat-rs`, `mongodb://127.0.0.1:27017/rowboat?replicaSet=rs0`, single-node `rs0`, transactions already verified |
| Supabase | container matched by `supabase-db`, schema `plugin_setup`, migrations 0001-0004 already applied, role `plugin_setup_agent` present |

This task did not start, stop, restart, or reconfigure any of the above.
The daemon's issuable list already held one probe entry from the
controller's own earlier verification, `PLUGIN_SETUP_PROBE_TOKEN`; this
proof used its own uuid-suffixed reference names throughout and never
touched that entry. (The evidence log below shows the list's total growing
past two — that is this file's own repeated development/review runs
accumulating references with no delete endpoint to remove them, not
unaccounted activity; see the V.4 note at the `issuable_credentials.list`
line.)

## V.3 One run, five assertions, all live

`apps/rowboat/test/plugins/live-setup-agent-e2e.test.ts`, opt-in by its own
variable (`ROWBOAT_LIVE_SETUP_AGENT=1`, alongside the `ROWBOAT_LIVE_MONGO_URL`
/ `ROWBOAT_LIVE_OPENFANG_URL` / `OPENFANG_API_KEY` convention every prior
part uses):

```text
 ✓ test/plugins/live-setup-agent-e2e.test.ts (5 tests) 8.1s
   ✓ A -- fail-closed: Supabase intake, a real provider 401, nothing reaches OpenFang
   ✓ A2 -- OpenFang never saw the fail-closed reference
   ✓ B -- custody, direct: /store -> 200, issuable without a restart, unknown name -> 404
   ✓ C -- Rowboat: component-scoped install, tool binding, release, decision, provider, receipt
   ✓ D -- hygiene: every invented value, asserted absent, with a zero-control window
```

The evidence log, in full, from the fix-round-1 re-run (values are never in
it — see V.5):

```text
  01  head marker emitted (brackets the window's start): HEAD_MARKER_TASK8_a01a0f2e... [expected to fail, unknown referenz] -> exit=3
  02  setup: project=e4733bfa-... db=rowboat pluginSetupDir=.../spaces/plugin-setup
  03  schluessel_entgegennehmen(TASK8_FAILCLOSED_..., art=bearer, <invented>) -> ok=false status=401
  04  Supabase row: status=fehlgeschlagen hinweis=401 vault_secret_present=t
  05  OpenFang issue(TASK8_FAILCLOSED_...) after the fail-closed run -> HTTP 404
  06  OpenFang store(TASK8_CUSTODY_...) -> HTTP 200
  07  OpenFang issue(TASK8_CUSTODY_...) -> HTTP 200 value_matches_stored=true
  08  OpenFang issue(TASK8_NEVERSTORED_...) [never stored] -> HTTP 404
  09  issuable_credentials.list: contains TASK8_CUSTODY_...=true, 10 total reference(s)
      (grows across repeated runs; no delete endpoint)
  10  project e4733bfa-... inserted into its own database rowboat
  11  github: 8 components, 1 admitted, selecting github edaa0cfffb94...
  12    credentialSlots = ["GITHUB_PAT_TOKEN"]
  13  install with [edaa0cfffb94...] -> receipt b166bc89-... status=success
  14  tool bound: plugin_github_github added=true
  15  OpenFang store(GITHUB_PAT_TOKEN) [for the runtime's own credential resolver] -> HTTP 200
  16  runtime composed against http://127.0.0.1:4273; operation=get_me (write)
  17  OpenFang approval raised   id=c6d2fd13-... tool_name=get_me
  18    action_summary  = component edaa0cff...fa350 arguments 56551660...0706980
  19    approve() -> {"status":200,"body":{"status":"approved", ...}}
  20  invocation outcome: provider_failed after 1796ms; approval raised: c6d2fd13-...
  21  independent probe: POST https://api.githubcopilot.com/mcp/ with the same fake
      GITHUB_PAT_TOKEN -> HTTP 401
  22  execution receipt: status=failed reason=provider_unavailable approvalId=c6d2fd13-...
  23  decision for c6d2fd13-...: status=approved decided_at=2026-09-11T10:34:07.813349300Z
  24  positive control (tail): fehlschlagen(CONTROL_MARKER_TASK8_...) [expected to fail] -> exit=3
  25  docker logs --since 2026-09-11T10:34:02.253Z: head marker hits=2, tail marker hits=2
      (brackets the whole window)
  26  docker logs --since 2026-09-11T10:34:02.253Z: 0 occurrences of any of the 3 invented values
  27  vault.secrets scan across 3 invented values: counts=[0,0,0]
  28  daemon log ...live-daemon.log: size 56607 -> 61928
  29  daemon log: 0 occurrences of any invented value; reference name GITHUB_PAT_TOKEN present (name only)
  30  plugin_receipts (6 document(s)): 0 occurrences of any invented value
  31  evidence log: 0 occurrences of any invented value (self-check)
  32  cleanup: Mongo rows for project e4733bfa-... removed -- projects=1 installations=1
      admissions=1 credentialSlots=0 executionClaims=1 receipts=1
  33  cleanup: Supabase row+vault-secret for TASK8_FAILCLOSED_... -> exit=0
  34  cleanup: OpenFang has no credential-delete endpoint -- stored references remain
      until the controller tears the isolated daemon down
```

## V.4 What each line proves

- **01** — the head marker (fix round 1): emitted in `beforeAll`,
  immediately after `windowStartIso` is computed and before A/B/C run
  anything. Its only job is to be found later, in D, inside the exact same
  `docker logs --since windowStartIso` fetch the tail marker is checked
  against — see 25 below for why one marker alone was not enough.
- **03-04** — `schluessel_entgegennehmen` (the real MCP tool, called
  directly, not reimplemented) ran the real order: Supabase insert first
  (`ablage.entgegennehmen`, status `entgegengenommen`), then the real
  network call to `https://api.github.com/user` with an obviously-fake
  bearer, which GitHub genuinely answered `401`. `ablage.fehlschlagen`
  moved the row to `fehlgeschlagen` and recorded **only** the status code
  (`hinweis=401`) — never the value, never the response body.
  `vault_secret_present=t` confirms the Supabase copy is deliberately still
  there, for diagnosis, exactly as the fail-closed path intends.
- **05** — OpenFang's own `/api/credentials/issue` answers `404` for that
  exact reference name: it never received it. This is checked directly
  against the daemon, not inferred from the Python tool's return value.
- **06-09** — bypassing the agent's gate on purpose, calling
  `/api/credentials/store` directly: `200`, immediately followed (same
  process, no restart) by `/api/credentials/issue` returning the identical
  value, and a never-stored name refused with `404`. Line 09 confirms the
  name landed in the daemon's own `issuable_credentials.list` file by
  reading it directly — the exact artifact the task brief names. The "10
  total reference(s)" is not a claim about how many *should* be there — see
  the V.2 note; the only thing asserted is that this run's own name is
  present and the never-stored one is not.
- **10-14** — component-scoped install (github: 8 components, 1 admitted)
  through the real `InstallPluginUseCase`, and tool binding through the
  real `AddPluginToolUseCase` — identical mechanics to Parts I-IV.
- **15** — the github component's fixed credential slot name
  (`GITHUB_PAT_TOKEN`, not something this task can rename) is placed into
  OpenFang's custody through the **same** `/store` endpoint B just proved,
  not injected any other way. The value itself is now `ghp_`-shaped
  (`ghp_OBVIOUSLYFAKETASK8...`) rather than a free-form string — see 21
  below for why that shape matters.
- **16-20, 22-23** — the real release/approval/provider chain: a
  write-classified call raises a real approval, a decision releases it, the
  real `OpenFangCredentialResolver` issues the fake `GITHUB_PAT_TOKEN`, the
  real `HttpMcpProvider` carries it over real HTTPS to
  `https://api.githubcopilot.com/mcp/`, and the exchange ends in
  `provider_failed` (not `credential_missing` — the credential resolved to
  a value; the HTTP exchange itself failed; see Part I §5 for the exact
  code path this distinguishes). The receipt (`plugin_receipts`) carries
  the same approval id OpenFang shows as `approved`. The pending approval
  this run decides is filtered by `tool_name` before it is approved (fix
  round 1), so a stale or unrelated pending approval already sitting on
  this shared, long-lived daemon can never be misattributed as this run's
  own decision.
- **21 — the correction this fix round makes.** `provider_failed` by
  itself does not prove GitHub's own rejection: `HttpMcpProvider` collapses
  every non-credential failure — a real 401, a DNS failure, a dropped
  connection — into the identical outcome, and the first version of this
  section claimed "GitHub's own rejection produces `provider_failed`" on
  that assertion alone, which the assertion does not establish. This line
  is the fix: an INDEPENDENT `fetch` to the identical endpoint
  (`https://api.githubcopilot.com/mcp/`), with the exact same fake
  `GITHUB_PAT_TOKEN` value the invocation just used, asserting a real HTTP
  status code. It returned `401` — the same shape Part I §8 used to close
  this exact gap for its own proof (a direct probe, not the runtime's own
  collapsed error code). One measured wrinkle while building this: this
  endpoint validates bearer-token *shape* before validity — a value with no
  recognized `gh*_` prefix draws `400` ("Authorization header is badly
  formatted"), not `401`; only a plausible-length `ghp_`-shaped value
  reaches the "wrong token" check this proof needs. `githubWert` was
  changed to that shape for exactly this reason (still unmistakably
  invented — see 15 above).
- **24-26** — the zero-control window, bracketed at BOTH ends (fix round
  1). A single tail-only control (a marker emitted only at the end, in D)
  proves the window's *end* is captured but says nothing about its
  *start*: `docker logs --since` compares a HOST-supplied timestamp against
  timestamps the DAEMON itself recorded, and this daemon runs inside a VM
  with documented clock drift — a window that silently started later than
  intended would exclude exactly the stretch (A's run) where a real leak
  could occur, while a tail-only control would still pass. Here both the
  head marker (line 01, from `beforeAll`) and the tail marker (line 24,
  freshly triggered in D) are searched for in the same
  `docker logs --since <RFC3339>` fetch and both are found (`hits=2` each —
  psql echoes the failing statement's own class name once in addition to
  the server's `ERROR:` line). Only against that doubly-proven-working
  window are all three invented values from A/B searched for and found
  **zero** times. `vault.secrets` is scanned directly (not assumed safe
  merely because it is encrypted) and also comes back zero for all three.
- **28-29 — no longer conditional (fix round 1).** The daemon's own log
  file check used to be skipped, silently reporting green, when its path
  was not given — but `custodyWert` and `githubWert` never touch Postgres,
  so this file is the *only* surface either of them is ever checked
  against; a run without it would claim "every invented value asserted
  absent" having genuinely checked neither. The test now throws if the
  path is missing rather than skip. It grew by 5,321 bytes during this run
  (a zero-control check in its own right: an unchanged file would mean
  nothing was captured), and contains zero occurrences of any invented
  value while still naming `GITHUB_PAT_TOKEN` by reference.
- **30** — the `plugin_receipts` collection (system-wide, not filtered to
  this run's own project — a broader check than strictly required) carries
  zero occurrences of any invented value.
- **31** — the evidence log itself — this file's own `log()` output — is
  scanned last, as a self-check that the harness's own logging discipline
  held.
- **32-34** — cleanup. Mongo: `MongodbProjectsRepository` binds to a
  module-level singleton (`app/lib/mongodb.ts`: `mongoClient.db("rowboat")`,
  hardcoded, not parameterized) — unlike `MongodbPluginsRepository`, it
  cannot be pointed at a dedicated database, so this run used the same
  shared `rowboat` db every prior Part used and deleted only the rows its
  own random `projectId` touched (projects, installation, admission,
  execution claim, receipts) — the shared, reused 180-entry plugin catalog
  was left alone, deliberately. Supabase: the one row and vault secret this
  run created were deleted as `postgres` (`plugin_setup_agent` has no
  DELETE by design). OpenFang: this run probed for a delete/revoke
  endpoint directly against the running daemon — `DELETE` and `POST` to
  `/api/credentials/<ref>`, `/revoke`, `/remove`, `/delete` all answered
  `404` — none exists. The references this run stored
  (`TASK8_CUSTODY_...`, and `GITHUB_PAT_TOKEN` with an overwritten fake
  value) therefore remain in the daemon's issuable list until the
  controller's own teardown of the whole isolated daemon — the identical
  disposition the environment already specified for
  `PLUGIN_SETUP_PROBE_TOKEN`. Editing the daemon's live state file by hand
  while it keeps running was ruled out as a bigger risk than leaving these
  present: it would mean writing to `OPENFANG_HOME` outside any API the
  daemon exposes while the process has it open, with no way to verify the
  daemon does not independently rewrite the same file — exactly the kind
  of un-reviewable, out-of-band mutation the global constraints ask this
  task to avoid.

## V.5 Hygiene, as an assertion

Every one of the three invented values used in this proof
(`TASK8_FAILCLOSED_...`'s bearer value, `TASK8_CUSTODY_...`'s direct value,
and `GITHUB_PAT_TOKEN`'s fake value) was searched for, by the test itself
(not by eyeballing output afterward), in:

- the Supabase state row (`hinweis` carries only the status code, `401`)
- `vault.secrets` (`secret`/`name`/`description` columns, scanned directly)
- `docker logs` on the Supabase container, across a window bracketed at
  both ends by a deliberate positive-control marker (fix round 1 — see
  V.4's "24-26")
- the OpenFang daemon's own log file — the ONLY surface where the two
  values that ever reach a real credential store (`custodyWert`,
  `githubWert`) are checked at all, so this check is now required rather
  than skipped when its path is absent (fix round 1 — see V.4's "28-29");
  its growth during the run is itself asserted, so the absence check is
  not vacuous
- the `plugin_receipts` Mongo collection
- this test's own evidence log

Zero occurrences, in all six, asserted by the test — a run with any
occurrence would fail, not merely note it.

One legitimate, expected exception, not a leak: OpenFang's own
`POST /api/credentials/issue` response body to the authorized caller in B
and C's own custody resolver **does** carry the value back — that is the
endpoint's job, the intended recipient is this same process, and the test
never prints or logs that response body's value field (it only compares it
for equality and logs the boolean result).

## V.6 Verification commands and their results

Re-run after the fix-round-1 changes above (the token-shape finding in
V.4's "21" required one more fix mid-round: the direct probe's first
attempt used a free-form fake value and drew GitHub's edge `400`
"badly formatted" rather than the `401` "wrong token" this proof needs --
`githubWert` was changed to a `ghp_`-shaped value, measured live before
being wired in).

```text
$ cd spaces/plugin-setup && python -m pytest -q
  68 passed in 13.77s
```

```text
$ cd spaces/rowboat/rowboat/apps/rowboat && npx tsc --noEmit -p tsconfig.json
  exit 0
```

```text
$ npm run test:plugins        # without the live env vars -- default run
  Test Files  31 passed | 6 skipped (37)
       Tests  792 passed | 11 skipped (803)
```

Baseline in this worktree, before this task's file existed: 31 passed | 5
skipped (36 files); 792 passed | 6 skipped (798 tests). The delta is exactly
**+1 skipped file, +5 skipped tests** (this file's five `it()`s, skipped
without the opt-in variable) and **0 change** to the 792 previously-passing
tests — this task added no failures and no newly-passing tests to the
default run.

The skip guard was checked explicitly, both directions, not just inferred
from the aggregate counts above:

```text
$ npx vitest run test/plugins/live-setup-agent-e2e.test.ts    # no opt-in vars set
  Test Files  1 skipped (1)
       Tests  5 skipped (5)

$ ROWBOAT_LIVE_MONGO_URL=... ROWBOAT_LIVE_OPENFANG_URL=... OPENFANG_API_KEY=... \
  ROWBOAT_LIVE_SETUP_AGENT=1 PLUGIN_SETUP_OPENFANG_LOG_FILE=... PLUGIN_SETUP_OPENFANG_HOME=... \
  npx vitest run test/plugins/live-setup-agent-e2e.test.ts
  Test Files  1 passed (1)
       Tests  5 passed (5)
```

## V.7 Cleanup

- Mongo: the rows this run's own `projectId` created (projects,
  installation, admission, execution claim, two receipts) were deleted;
  the shared plugin catalog was left untouched. `rowboat-rs` stays up.
- Supabase: the one `plugin_setup.einrichtungen` row and its vault secret
  were deleted as `postgres`. The container was not stopped, restarted, or
  reconfigured.
- OpenFang: no delete/revoke endpoint exists (probed directly, see V.4);
  the isolated daemon on `:4273` was not restarted, stopped, or
  reconfigured, and `PLUGIN_SETUP_PROBE_TOKEN` was left untouched. The few
  additional issuable references this run stored remain until the
  controller's own teardown of the whole daemon.
- The shared OpenFang daemon on `:4200` and `~/.openfang/` were never
  touched, at any point in this task.

# Part VI — the eingabefenster's own live proof: the value through the form, and nowhere else

Date: 2026-09-12 (UTC+2 shell; Postgres/docker log timestamps below are UTC).
`master` at `2e87bcc3` (139 tests green before this task's file existed).
Checkout: `C:/Users/User/Desktop/Vibemind_V1/vibemind-os/.worktrees/setup-agent`.
Docker: client and server both `29.7.2`.

## VI.1 Headline

**One live sidecar process, one real GET/POST round trip through
`/fenster/{token}`, one real unauthenticated call to `https://api.github.com/user`
— and an invented value that shows up in none of seven places checked,
including the one place this branch has already leaked a credential-shaped
value into twice.**

Part V proved the setup-agent's Supabase→provider→OpenFang chain by calling
`werkzeuge.schluessel_entgegennehmen` directly. This part proves the piece
in front of it: the actual HTTP surface a human sits at
(`spaces/plugin-setup/server.py`'s `/fenster/{token}` routes, run as the
real subprocess `python server.py`, reached only over real sockets) — the
"the agent gets a link, a human types the value, the agent never sees it"
design this whole task line exists for.

## VI.2 What ran, and the real output

`spaces/plugin-setup/tests/test_fenster_live.py`, opt-in by
`PLUGIN_SETUP_FENSTER_LIVE=1`, mirrors the pattern every other live proof in
this file uses. One test:

1. Starts `server.py` as a real subprocess on a free loopback port
   (`PLUGIN_SETUP_MCP_HOST=127.0.0.1`, own port, own
   `PLUGIN_SETUP_FENSTER_BASIS`), waits for the MCP endpoint to answer (a
   bare GET on `/mcp` returning `406` — no `Accept: text/event-stream` —
   means the process is up).
2. Calls the real MCP tool `eingabe_anfordern` over `/mcp` (`initialize`,
   then `tools/call`) with `art="bearer"`, `ziel=""` — the combination the
   code explicitly allows for `bearer` (`werkzeuge._ziel_pruefen` rejects a
   *non-empty* `ziel` for `art="bearer"`, since the check address is
   hardcoded to `api.github.com/user`).
3. GETs the returned one-time link, then POSTs an invented value
   (`offensichtlich-erfunden-fenster-<uuid4 hex>`) to it — a real HTTP
   round trip against the real subprocess, not a function call.
4. Reads the resulting Supabase state, the Postgres server log, and the
   sidecar's own stdout, asserting the invented value is in none of them.

Run without the opt-in variable:

```text
$ PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q
s                                                                        [100%]
1 skipped in 0.02s
```

Run with it:

```text
$ PLUGIN_SETUP_FENSTER_LIVE=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q -s
.
1 passed in 3.26s
```

Both directions required, both shown, per this task's own instruction: a
live proof that greens itself without the opt-in is worthless. The `s`/`.`
progress characters above are pytest's own, not edited in.

## VI.3 What each of the seven places proves, and why the seventh was added

The brief that drove this task named six places the value must never
appear, all asserted inside the test itself (not eyeballed after the
fact): the tool's own response text (before the link is ever followed),
the GET page's HTML, the POST response's HTML, the Supabase `hinweis`
column (`LIKE '%<wert>%'`, zero rows), and the sidecar subprocess's own
stdout log file. `status == "fehlgeschlagen"` is the discriminating
assertion the brief calls out explicitly: an invented value **must** fail
real provider verification, or the check proves nothing. It did —
confirmed live: the operator's environment probe before this task started
(`https://api.github.com/user` answering unauthenticated requests with
`401`) is exactly the path this test's `art="bearer"` check walks, and the
assertion passed, meaning `pruefung.pruefe()` made a real call and got a
real non-200 back (had the intake step failed before reaching the
provider, the status would have stayed `entgegengenommen`, not advanced to
`fehlgeschlagen`).

**The seventh place — added by this task's own ruling, not the original
brief — is the Postgres CONTAINER LOG.** This is not a hypothetical
concern on this branch: `spaces/plugin-setup/ablage.py`'s own C1-fix
documents a literal credential value appearing in `docker logs` **37
times** after a single deliberately-triggered `referenz_name` collision,
because this instance runs with `log_min_error_statement = error` and logs
every *failing* statement's text verbatim — and a second, separate
incident on this same line (Part V, fix round 1, §V.4/§V.5) found the
Postgres server log carrying a credential-shaped value that a zero-control
check had missed because it only bracketed one end of the search window. A
proof that checked six places and skipped the one place this actually
happened would have been the same mistake a third time.

## VI.4 The null control, and why it runs before the real assertion

An absence assertion against a log is only as good as the search that
produced it. Before trusting "the invented value is not in the Postgres
container log," the test first proves the search *would* find something if
it were there: it deliberately triggers a real Postgres error —
`SELECT 1 FROM <a fresh, never-existing table name>;`, run as `postgres`,
not the fake credential value — and asserts that error's own text shows up
in `docker logs --since 5m <container>` before checking that the fake
value does not. Manually verified against the live shared container
(`vibemind_supabase-db.1.szlphscs1k4me4vwfkck8d6k6`, matched by the
`supabase-db` substring, the same discovery pattern `ablage.py` and
`tests/test_eingang.py` already use) before this was wired into the test:

```text
ERROR:  relation "nullkontrolle_probe_<...>" does not exist
LINE 1: SELECT 1 FROM nullkontrolle_probe_<...>;

$ docker logs --since 5m <container> | grep -F "nullkontrolle_probe_<...>"
[local] 2026-09-12 13:40:41.433 UTC [99656] postgres@postgres ERROR:  relation "nullkontrolle_probe_<...>" does not exist at character 15
[local] 2026-09-12 13:40:41.433 UTC [99656] postgres@postgres STATEMENT:  SELECT 1 FROM nullkontrolle_probe_<...>;
```

and, in the same window, a string guaranteed absent found nothing (checked
by grep's own exit status, not by eyeballing whether a line printed — an
earlier draft of this same manual check piped `grep` into `tail` and read
`$?` off `tail`, which exits `0` regardless of whether anything matched;
that bug was caught before it reached the test file, not after).

`--since` takes a Go **duration** (`5m`), never a timestamp, on this
task's explicit instruction — a timestamp missing a timezone is named as a
prior, costly trap on this branch.

This paragraph previously recorded a negative finding: that the trap could
not be reproduced on this Docker Desktop 29.7.2. **That negative finding was
itself wrong, and is retracted here.** It rested on two non-discriminating
probes — a space-separated timestamp, which fails loudly with a CLI parse
error and was never the reported symptom, and a naive ISO timestamp that
happened to be the host's LOCAL time, which is precisely the case that
works. Re-measured on the same container and client, with the *same instant*
expressed four ways (host is UTC+2):

```
$ docker logs --tail 1000000 <container> | wc -l
41471
$ docker logs --since 5m <container> | wc -l
544
$ docker logs --since 2026-09-12T13:43:53Z <container> | wc -l     # UTC, WITH Z
544
$ docker logs --since 2026-09-12T15:43:53 <container> | wc -l      # local, no offset
544
$ docker logs --since 2026-09-12T13:43:53 <container> | wc -l      # UTC, no offset
19063
```

So the trap is real, and the earlier description of it — "silently ignored,
returns the entire log" — was imprecise in a way that matters for anyone
trying to avoid it. The accurate statement: **a timestamp with no offset is
interpreted in the HOST's local timezone.** Compute the instant the obvious
way (`date -u`), hand it over without the `Z`, and the window silently
reaches back by the UTC offset — here two hours, 19063 lines instead of 544,
a 35x overshoot with no error and no warning. That is exactly how a window
meant to start *after* a fix comes to include log lines from *before* it,
and how a closed leak reads as an open one.

The duration form is used here regardless, and remains the right default:
it has no timezone to omit, so the whole failure class is structurally
unavailable to it rather than merely avoided by care.

## VI.5 What this does NOT prove

Two answers this task was told not to guess at, stated plainly:

1. **The OAuth branch did not run live here.** This test exercises only
   `art="bearer"` — the combination `_ziel_pruefen` allows with an empty
   `ziel`. `fenster.oauth_entgegennehmen` and the real provisioner
   (`provision-oauth-token.py`'s `token_holen`) were not invoked by this
   task at all; a live OAuth round trip needs an actual browser login
   against a real provider, which is out of scope here. What covers that
   branch is Task 6's tests with an injected acquisition seam, not a live
   run: `tests/test_fenster.py`'s
   `test_oauth_fehlschlag_zeigt_keinen_token_und_keine_ursache_im_klartext`,
   and `tests/test_server_formularrouten.py`'s
   `test_oauth_post_ruft_den_echten_provisioner_statt_den_formular_wert`,
   `test_oauth_post_bei_beschaffer_fehlschlag_zeigt_keine_ausnahme_und_keinen_token`,
   and `test_oauth_beschaffung_blockiert_ein_gleichzeitiges_get_nicht` — all
   four with a fake `beschaffer` standing in for the real provisioner, no
   network.
2. **The form was reached only from the host, never through a container.**
   This test's HTTP client is the pytest process itself on
   `127.0.0.1`, never `host.docker.internal`, never a container. Whether
   the guard in front of `/fenster/{token}` (`server.py::_ist_loopback`)
   behaves differently for container-originated traffic was not tested by
   this task. What is already known, measured before this task and
   documented in `server.py`'s own module docstring, and not re-verified
   here: on this Docker Desktop + WSL-mirrored-networking host,
   `_ist_loopback` does **not** separate the operator from a container —
   a request from a container to `host.docker.internal` arrives at the
   Python process with `request.client.host == "127.0.0.1"`, the same as a
   host-originated request, even against a listener bound only to
   `127.0.0.1`. Nothing beyond that measured, narrower claim is asserted
   here.

## VI.6 What I did not measure myself, named rather than left implicit

This branch's history (see Part V, §V.1) records five prior overclaims
that were each accurate about what they named and wrong about what sat
next to it. In that spirit, named plainly rather than smoothed over:

- **No container-originated request was made.** Not `marketing-claw`, not
  openclaw, not any container hitting `host.docker.internal:<port>` — the
  host.docker.internal-equals-loopback finding cited above is read from
  `server.py`'s docstring and Task 5's report, not re-measured in this
  task.
- **OpenFang custody was not exercised by this specific test.** Because
  the invented value fails real verification by design, `pruefung.pruefe()`
  returns not-good and `schluessel_entgegennehmen` returns before ever
  calling `_openfang_uebernehmen` — this test never talks to OpenFang, live
  or otherwise. The custody handoff itself is covered live only by Part
  V's channel B, through a different mechanism (a direct call to OpenFang's
  own store/issue endpoints), not by anything in this section.
- **The Postgres error deliberately triggered for the null control is a
  real, permanent line in a log shared by every other session using this
  same `supabase-db` container.** It is inert (no table, row, or state
  touched — `relation ... does not exist`) and carries no credential-shaped
  content, but `docker logs` cannot be edited or pruned after the fact;
  this is a small, deliberate, and irreversible side effect of proving the
  search mechanism works, not something this task cleaned up (there is
  nothing to clean up in a log stream).
- **This test ran once, sequentially, not concurrently with another
  session's activity against the same container.** Whether the same
  assertions hold under concurrent load from another session's tests
  (mentioned as a live possibility in this repo's multi-session-coordination
  notes) was not checked.
- **Whether some *future* configuration change to the Supabase instance
  (a different `log_min_error_statement`, a different log destination)
  would silently defeat the null control itself** was not checked — the
  control proves today's configuration surfaces a triggered error; it is
  not a structural guarantee independent of that configuration.

## VI.7 Verification commands and their results

```text
$ PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q
s                                                                        [100%]
1 skipped in 0.02s

$ PLUGIN_SETUP_FENSTER_LIVE=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q -s
.
1 passed in 3.26s

$ PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q
............................................................s......... [ 51%]
....................................................................    [100%]
139 passed, 1 skipped in 13.68s
```

Baseline before this task's file existed: 139 passed (Task 6, fix round 1).
Delta: **+1 skipped file** in the default run (this test, correctly opted
out without the live variable), **+1 passed** when opted in, **0 change**
to the 139 previously-passing tests.

Post-run row count, confirming cleanup (see VI.8):

```text
$ docker exec -i <supabase-db-container> psql -U postgres -d postgres -tA \
    -c "SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name LIKE 'PYTEST_FENSTER_%';"
0
```

## VI.8 Cleanup

- Supabase: the test's own `finally` block deletes the vault secret (by
  `vault_secret_id`, if one was ever assigned — for `art="bearer"` the
  intake step's own Vault write happens before verification, so a row is
  created and then the whole row is deleted) and the `plugin_setup.einrichtungen`
  row for its own `referenz_name`, unconditionally, every run whether the
  test passes or fails. Confirmed empty afterward (§VI.7).
- The sidecar subprocess is terminated and waited on in the same `finally`
  block; its stdout log file (a per-run temp file, not a project file) is
  deleted after being read for the leak check.
- No container was stopped, restarted, or reconfigured, at any point in
  this task — `docker exec`/`docker logs`/`docker ps` only, all read-only
  or additive (the deliberate null-control error, see §VI.6).
- `~/.openfang/` and the shared daemon on `:4200` were never touched.
- Nothing was pushed; no gitlink was bumped; nothing was deployed.
