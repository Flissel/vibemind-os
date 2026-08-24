# VibeMind Learning Space

The Learning Space integrates controlled forks of LearnHouse and PenEcho. The
VibeMind wrapper owns authority, MCP contracts, orchestration, truth readback,
and Learning-specific services. The embedded applications remain independent
projects with their own test suites and licenses.

## Pinned Sources

Pins and provenance are authoritative in `deployment/upstream-lock.yml`.

- LearnHouse: `Flissel/learnhouse`, derived from `learnhouse/learnhouse`.
- PenEcho: `Flissel/penecho`, derived from `penecho/penecho`.
- Both sources are licensed `AGPL-3.0-only` at the recorded pins.

Distribution and network use must preserve copyright notices, license text,
and corresponding source availability for the exact deployed modifications.
VibeMind does not claim a right to relicense either embedded application.

## Clone

Clone the parent repository recursively or initialize these paths afterward:

```powershell
git submodule update --init --recursive spaces/learning/learnhouse spaces/learning/penecho
```

## Upstream Update Procedure

Perform updates in one child repository at a time. Start from a clean parent
worktree, create a feature branch in the fork, and retain the upstream remote:
inside each child repository the update operation is `git fetch upstream`.

```powershell
git -C spaces/learning/learnhouse remote add upstream https://github.com/learnhouse/learnhouse.git
git -C spaces/learning/learnhouse fetch upstream
git -C spaces/learning/penecho remote add upstream https://github.com/penecho/penecho.git
git -C spaces/learning/penecho fetch upstream
```

Run the relevant baseline and Learning-focused suites before committing a new
child SHA. Update `deployment/upstream-lock.yml` and the parent Gitlink in the
same parent task. Never advance a Gitlink without recording the exact source,
commit, license, test evidence, and known failures.

## Upstream Baseline Commands

LearnHouse API:

```powershell
Set-Location spaces/learning/learnhouse/apps/api
uv sync
uv run pytest -q
```

LearnHouse Web:

```powershell
bun install --cwd spaces/learning/learnhouse/apps/web --frozen-lockfile
bun --cwd spaces/learning/learnhouse/apps/web test
```

PenEcho:

```powershell
npm --prefix spaces/learning/penecho ci
npm --prefix spaces/learning/penecho run check
```

Record dependency/bootstrap failures as baseline limitations. A focused green
test never implies that an unavailable or unexecuted upstream suite is green.

### Baseline Snapshot: 2026-08-24

- PenEcho `d580110`: `npm run check` discovered 527 tests; 519 passed,
  7 failed, and 1 was skipped on Windows. The failures cover three POSIX file
  mode assertions, three macOS/POSIX path assertions, and one temporary-file
  cleanup `EPERM`. No Learning-specific PenEcho changes existed for this run.
- LearnHouse API `5a58c3d`: `uv sync --frozen` could not start because the
  pinned project requires Python `>=3.14.6,<3.14.7`, which was not installed.
- LearnHouse Web `5a58c3d`: Bun 1.3.11 rejected the pinned lockfile version 3;
  frozen installation stopped without modifying the lockfile.

These are upstream/environment baseline limitations, not green-suite claims.
They must be refreshed after the required isolated toolchains are provisioned;
they are not repaired as part of the parent pin commit.

## Current Evidence Boundary

The pins and registry documentation are structural evidence only. They do not
prove that the Learning runtime, OpenFang agent, providers, application
readback, or MCP Golden Path is live.
