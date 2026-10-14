# Vibemind Musik-Space

> **Evidence contract:** Nothing in this file is `verified_live` unless it says
> so with a date and the operation it came from. The engine's own docs
> distinguish *measured* from *asserted*; this space keeps that discipline.
>
> **Status as of 2026-09-03:** one agent, driven end to end. Step 5 of the
> plan — a Mix-Engineer whose transition the critic finds again in the
> recording — is the acceptance criterion, and it is **met on built material,
> headless**: [`docs/ABNAHME.md`](docs/ABNAHME.md). Nothing here has run
> against real music, and nobody has heard any of it.

A Vibemind-Space for **DJing**: two decks, a mixer, beat-grid-aware scheduling
and a repertoire of transitions, driven over MCP.

The audio engine is a separate Rust project — [Flissel/Musik][engine] — and
stays that way. It is not a Python package and does not become one; it speaks a
line protocol over a socket, and `mcp/musik/` in that repo turns it into MCP
tools.

[engine]: https://github.com/Flissel/Musik

## What is actually here

| | |
| --- | --- |
| Engine (Rust) | separate repo; ~540 tests, CI green |
| MCP bridge | 16 tools, in the engine repo under `mcp/musik/` |
| Approval gate | built, 18 tests, runs in the engine's CI |
| Mix-Engineer | `agents/mix_engineer.py` — chooses a grip, says why, drives it |
| `api/` | a thin shell over the 16 tools; no capability of its own |
| `acceptance/` | the end-to-end run that produced `docs/ABNAHME.md` |
| Other agents | **none** — Track-Wähler, Set-Planer and Guardrail are still the plan |

## Two things this space assumes

**One machine.** The bridge reaches the engine over a Unix socket, or over the
loopback with a key where there are no Unix sockets (Windows). Both end at the
machine boundary. An agent on another host would need a third transport, and
that is a separate piece of work.

**A human grants the set approval.** The engine refuses every state-changing
call unless a file — outside the agent's tool surface — says otherwise, and the
file expires. Brain could write that file later; it does not today, and a
system that grants itself capability is exactly what the design avoids.

## Getting it running

```sh
# on the DJ machine
musik-app --socket "$XDG_RUNTIME_DIR/musik.sock"      # Linux/macOS
musik-app --tcp                                        # Windows, prints a key
```

```jsonc
// MCP server entry
{
  "command": "python", "args": ["-m", "musik.server"],
  "cwd": "<engine>/mcp",
  "env": {
    "MUSIK_SOCKET": "/run/user/1000/musik.sock",
    "MUSIK_FREIGABE_DATEI": "/run/user/1000/musik.freigabe"
    // Windows instead: MUSIK_TCP, MUSIK_SCHLUESSEL
  }
}
```

The approval file, written by a human, not by an agent:

```text
klassen mischen spielen zeit
bis    2026-09-03T23:30:00Z
von    felix
```

Without it, every write is refused and says so. Read tools stay free.

## What this space cannot do

- **No crowd sensing.** The engine has four signal slots that steer the set's
  energy target, but nothing fills them. An agent that invents a crowd is worse
  than none.
- **No generation.** No track has ever been generated; Suno has no public API.
  See `GENERATOR.md` in the engine repo.
- **The analysis has never run against real music with heard ground truth.** An
  agent planning on `section` and `energie` is planning on unverified numbers.

See [AGENTS.md](AGENTS.md) before touching anything here.
