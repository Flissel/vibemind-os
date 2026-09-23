# Marketing cockpit contract

> Moved on 2026-09-23 from the outer repo, where it guarded an unmaintained
> copy of this space. The inventory below was re-measured against this copy.

## Purpose and authority

This document is the static source of truth for the Marketing cockpit. It
classifies repository evidence; it does not start, probe, configure, or claim
the state of a service. `AGENTS.md`, `README.md`, and `STATUS.md` defer to this
contract when describing cockpit readiness.

`verified_live` is reserved for fresh, operation-specific evidence. A route,
workflow, historical note, open port, test result, or static registry is never `verified_live` without fresh evidence.

## Static inventory

The following facts are derived from the committed repository tree and are
protected by `spaces/marketing/tests/test_cockpit_contract.py`:

- 43 migration files: `001`–`044`; `039` is absent.
- 13 Marketing event-to-tool mappings in `MarketingBackendAgent.EVENT_TO_TOOL`.
- 408 static pytest test definitions under `spaces/marketing`.
- zero bool-returning pytest tests in `sync/tests/test_render_md.py`; the nine
  former bool-returning tests now use pytest assertions while the local runner
  remains compatible.

The inventory is static and does not assert that migrations were applied, that
an agent ran, or that any test suite passed.

## Evidence classification

| Subject | Classification | Permitted statement |
| --- | --- | --- |
| `API :5510` | historical | Earlier documentation recorded a service endpoint; no current API execution is established here. |
| `n8n` | configured/static | Repository workflow/configuration artifacts may exist; no current workflow execution is established here. |
| OpenFang execution | required/not verified | OpenFang execution is required by the MVP but not implemented by this cockpit contract. |
| Proxmox repoint | not integrated | Proxmox repoint is not an integrated or live claim. |

No entry in this table is `verified_live` without fresh evidence. Fresh evidence
must identify the exercised operation, time, target, result, and any relevant
approval boundary.

## Cockpit boundaries

- This is a documentation and static-drift contract only.
- It does not authorize migration, database, API, n8n, OpenFang, send,
  Proxmox, deployment, runtime, network, or external actions.
- Marketing sending remains governed by the existing code and its explicit
  operator gates; this contract neither verifies nor changes those paths.
- A future integration or live claim requires a separately approved task and
  fresh evidence classified for that exact operation.

## Drift guard

The architecture test verifies the static inventory, the repaired pytest
return contract, the evidence classifications, and the required references
from the three Marketing entry documents. Update this contract and its test
together in an approved task whenever the underlying inventory legitimately
changes.
