# Learning Space Cutover Runbook

## Scope and Authority

The cutover controller changes only one versioned route-state JSON document. It does not
start or stop Docker services, import data, rebuild Qdrant, modify the legacy database,
or delete files, volumes, databases, receipts, or migrated records.

The retained rollback target is `C:/Users/User/Desktop/Learning_plattform`. It must remain
read-only and addressable for the full rollback window. The new target is the local
single-host profile at `spaces/learning/deployment/profile.yml`.

Only `plan` is permitted without action-time confirmation. `apply`, `verify`, and
`rollback` all require a confirmation token whose SHA-256 digest is configured separately.

## Required Evidence

Prepare a `learning-cutover-evidence-v1` JSON file containing:

- the exact applied migration batch ID;
- successful import reconciliation and Qdrant rebuild;
- a fresh deterministic Golden Path correlation ID with terminal readback;
- confirmation that the legacy source is mounted read-only;
- confirmation that the rollback target and receipt store are available.

All boolean gates must be `true`. A dry-run report alone is not import, rebuild, Golden
Path, or cutover evidence.

## Initialize Once

Provision the initial state through `initialize_route_state(...)` with revision `1`,
`active_target="legacy"`, both targets, and a read-only legacy target. Initialization
refuses to overwrite an existing state file. Keep the runtime state outside Git, for
example under `%LOCALAPPDATA%/VibeMind/Learning/route-state.json`.

## Plan

Planning probes the target and evaluates every gate but does not mutate route state:

```powershell
python -m spaces.learning.deployment.cutover plan `
  --state-file "$env:LOCALAPPDATA/VibeMind/Learning/route-state.json" `
  --expected-revision 1 `
  --target learning_space `
  --evidence-file "C:/path/to/learning-cutover-evidence.json"
```

The result must show `ready: true`, `blockers: []`, and
`destructive_operations: 0`. Record the evidence hash, exact batch, expected revision,
route change, and rollback command when requesting action-time confirmation.

## Apply

After explicit confirmation for that exact plan, expose the token and its digest only to
the cutover process. Do not place either value in Git, reports, shell history, or receipts.

```powershell
python -m spaces.learning.deployment.cutover apply `
  --state-file "$env:LOCALAPPDATA/VibeMind/Learning/route-state.json" `
  --expected-revision 1 `
  --target learning_space `
  --evidence-file "C:/path/to/learning-cutover-evidence.json" `
  --confirmation-ref "approved-change-reference"
```

The controller compare-and-swaps the expected revision and atomically replaces the state
file. The same confirmed command is idempotent. A different command against a stale
revision or active target fails closed.

## Verify

Verify the active route and current target health immediately after apply:

```powershell
python -m spaces.learning.deployment.cutover verify `
  --state-file "$env:LOCALAPPDATA/VibeMind/Learning/route-state.json" `
  --expected-revision 2 `
  --target learning_space
```

This verifies route-state readback and the configured health reference. It is not a new
Golden Path run and does not replace terminal application evidence.

## Rollback

Rollback is a separately confirmed route change. It restores the previous target stored
in the latest cutover receipt and appends a rollback receipt; both databases remain intact.

```powershell
python -m spaces.learning.deployment.cutover rollback `
  --state-file "$env:LOCALAPPDATA/VibeMind/Learning/route-state.json" `
  --expected-revision 2 `
  --confirmation-ref "approved-rollback-reference"
```

After rollback, verify `legacy` at revision `3`. Never remove the legacy prototype as part
of rollback. Deletion is a separate destructive request requiring its own scope and
confirmation.

## Current Non-Claims

The committed controller and tests prove deterministic planning, guarded state mutation,
schema validation, idempotency, health failure behavior, retained previous-state receipts,
and non-deletion against fixtures. They do not claim that a real import, Qdrant rebuild,
route cutover, rollback, or live-provider Golden Path has been executed.
