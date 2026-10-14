# Agents

One role is built: the **Mix-Engineer** (`mix_engineer.py`). The others are
listed in [`../AGENTS.md`](../AGENTS.md) and none of them exists.

It came first because it is the only role whose work can be checked the same
evening: it drives a transition, and `musik-kritik` finds that transition again
in the recording — with duration, phrase position and level curve. That run has
happened; what it measured is in [`../docs/ABNAHME.md`](../docs/ABNAHME.md).

## How it is put together

`plan()` is **pure**: a `Situation` goes in, a `Plan` or a `Refusal` comes out.
No engine, no clock, no I/O. That is where every rule lives and where every
rule is tested — 25 tests, none of which needs a running mixer.

`drive()` is the thin part around it: read status, plan, issue the calls. It
sends nothing when the plan refuses.

Anything added here obeys the seven Hard Rules, and the first of them is the
one that makes the rest hold: no shell, no filesystem tools.
