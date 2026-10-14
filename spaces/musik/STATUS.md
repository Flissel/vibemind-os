# Musik-Space — Status

> **Evidence contract.** Every claim below carries a date and its kind:
> *measured* (a number produced by a run), *static* (counted in the source),
> *configured* (declared but not exercised), or *open*. The one live claim —
> an agent driving a transition — is marked *measured* and names the run;
> everything it rests on is synthetic material, which is said plainly rather
> than dressed up as `verified_live`.

_Snapshot: 2026-09-03._

## Phase

**One agent, driven end to end.** The acceptance criterion — a Mix-Engineer
whose transition the critic finds again in the recording — is **met**, on
material this project built itself, headless, with nobody listening. What that
run measured, and the four attempts that measured nothing first, is in
[`docs/ABNAHME.md`](docs/ABNAHME.md).

## What holds

| Claim | Kind | Evidence |
| --- | --- | --- |
| Engine builds, ~540 tests green | static | `cargo test --workspace`, CI on every push |
| MCP bridge exposes 16 tools | measured | `tools/list` over a real stdio client, 2026-09-03 |
| Bridge runs on mcp 1.29.1 and 2.1.1 | measured | server imported under both, 16 tools each |
| Approval gate refuses without a file | measured | `musik_set` over MCP → `freigabe verweigert`, 2026-09-03 |
| Approval gate allows a granted class | measured | same run, `ok channel1.fader 0.800000` |
| Revocation works without restart | measured | file deleted mid-process, next call refused |
| One note per approval window | measured | 5 calls → 1 note; a second window → a second note |
| 194 controls all classified | static | `mcp/musik/controls.txt` + test |
| 18 gate tests run in CI | static | engine CI, step *Freigabe-Gatter*, `18 passed` |
| No copyleft without a permissive branch | measured | 474 packages of `Cargo.lock` resolved, 2026-09-03 |
| An agent drove a transition the critic found again | measured | 2 runs, grip 0.04 beats into the phrase, 32.0 of 32 beats, 2026-09-03 |
| Tempo holds across that transition | measured | 127.99 → 127.96 BPM, both runs |
| The engine counted it as a transition | measured | `master.transitions` = `blende`, `repeats` = 1 |
| No frames dropped during the run | measured | `master.record_dropped` = 0, both runs |
| 25 tests over the planner and the api | static | `python -m pytest spaces/musik` |
| Every one of those guards bites | measured | 12 defects reintroduced singly, each turned the intended test red |

## What does not

| Gap | Kind | Consequence |
| --- | --- | --- |
| No agent has driven a *set* | open | one transition is not an evening; the queue, the arc and a second grip are untouched |
| Four of the five grips have only run in unit tests | open | `bassswap`, `filter`, `schnitt`, `schleife` have never been played |
| The planner's room arithmetic is mirrored by hand | open | a change to the engine's grips invalidates it silently, across a repo boundary |
| No `repo-manifest.yaml` entry yet | open, **deliberately** | see below |
| Analysis never run against real music with heard truth | open | `section`/`energie` are unverified numbers |
| Below 70 BPM the detector reports wrong, not nothing | measured | 66 BPM → grid at 71.51; see engine `FAHRPLAN.md` N1 |
| A blend longer than two phrases is invisible to the critic | measured | 3 of 4 at 64 beats missed; engine `FAHRPLAN.md` N3 |
| No signal source | open | crowd-driven energy is not possible |
| No generation | open | Suno has no public API |
| Phase 1 not accepted | open | needs a four-output interface on real hardware |

## Why the engine is not in the manifest yet

It was tried and taken back out, and the reason is worth writing down.

`repo-manifest.yaml` accepts two evidence shapes: `local-git` + `local`, which
requires a real `.gitmodules` entry and a gitlink, or `pinned-remote-tree` +
`pinned-not-live`, which requires the parent's pin. A third state — "declared,
not yet pinned" — does not exist for a child of the superproject, and
`tools.v1_governance manifest validate` says so:

```json
{"code": "invalid-evidence-state", "path": "spaces/musik/engine", "severity": "error"}
```

Adding it properly would mean pinning a commit. The engine's default branch
`main` sits at `3f4faf7` — the **base** of the open pull request, an engine
without the bridge, without the approval gate, without the transition
repertoire. Pinning that would be worse than nothing. Pinning the PR branch
instead pins a commit that a squash-merge will orphan.

So the entry waits for [Flissel/Musik#1][pr] to land. The repo's own
`submodule_policy` expects this anyway:
`pin_change_requires_separate_commit: true`.

[pr]: https://github.com/Flissel/Musik/pull/1

## Next

1. **The remaining four grips**, through the same run. A blend is the easy one;
   the loop and the cut are where the timing is unforgiving.
2. **The Track-Wähler**, so a second transition has somewhere to go — one
   transition is not a set.
3. Manifest entry, once the engine's pull request has landed and there is a
   commit on `main` worth pinning.
