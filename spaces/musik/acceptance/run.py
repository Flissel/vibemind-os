"""The acceptance run: an agent drives a transition, and the critic looks for it.

This is step 5 of the engine's `docs/SPACE.md`, and the only step that proves
anything. Everything else in this space is wiring; here the whole chain runs
end to end —

    Mix-Engineer → api → MCP client → bridge → approval gate → line protocol
      → socket → Steuerpult → mixer → recording → musik-kritik

— and the last link reads back what the first one decided.

## What it needs

The engine repo checked out and built (`cargo build --release --bins`), an
X server stand-in (`xvfb-run`), and the MCP SDK in the environment running
this script. It does **not** need an audio device: without one the engine
renders from an idle clock, and the recording is written all the same.

    python -m spaces.musik.acceptance.run --engine /path/to/Musik --out /tmp/abnahme

## What it does not prove

The material is built, not heard (see `material.py`), the engine runs headless,
and one run is one run. What it shows is that the chain carries a decision from
end to end and that the result is measurable. Whether the decision was *good* is
a question for someone who was in the room.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

#: Where the outgoing track is placed before it starts, in seconds. Chosen so
#: that after the lead-in below it sits about two phrases short of its outro —
#: the situation the planner's first rule is about.
START_AT = 72.0

#: How long the outgoing track runs alone before anything is decided, and how
#: long the incoming one runs alone afterwards.
#:
#: Not padding. The critic compares each window against one sixteen seconds
#: earlier (`ABSTAND_SEK` in `analysis::wechsel`), so with less lead than that
#: there is nothing on the far side to compare against and it reports nothing —
#: correctly, and the first attempt at this run did exactly that.
LEAD_SECONDS = 20.0

#: Approval classes this run needs. `lesen` is free and needs no grant;
#: `erzeugen` is refused in the file by construction and is not wanted here.
CLASSES = "mischen spielen zeit datei dramaturgie"


async def _session(engine: Path, socket: Path, approval: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["PYTHONPATH"] = str(engine / "mcp")
    env["MUSIK_SOCKET"] = str(socket)
    env["MUSIK_FREIGABE_DATEI"] = str(approval)
    return stdio_client(
        StdioServerParameters(command=sys.executable, args=["-m", "musik.server"], env=env)
    ), ClientSession


async def drive_one(engine: Path, out: Path, socket: Path, approval: Path, repo: Path) -> int:
    sys.path.insert(0, str(repo))
    from spaces.musik.agents.mix_engineer import drive
    from spaces.musik.api import Console

    ctx, ClientSession = await _session(engine, socket, approval)
    async with ctx as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print(f"tools/list: {len(tools)} tools")

            async def call(name: str, args: dict) -> str:
                result = await session.call_tool(name, {"params": args})
                return "\n".join(c.text for c in result.content if hasattr(c, "text"))

            console = Console(call)

            print("\n-- before --")
            for d in (await console.status()).decks:
                print(
                    f"  {d.name} {d.title!r} bpm={d.bpm} dur={d.duration:.1f} "
                    f"load={d.load_status!r}"
                )

            # Setting up the desk, not mixing. The engine starts with both
            # channel faders down and the crossfader in the middle; a recording
            # of a closed mixer is silence, which is what the first attempt
            # produced. The crossfader is parked on the outgoing side so the
            # transition has somewhere to come from.
            for control, value in (
                ("channel1.fader", "1"),
                ("channel2.fader", "1"),
                ("master.crossfader", "-1"),
            ):
                print(" ", await call("musik_set", {"control": control, "wert": value}))

            recording = out / "mitschnitt.wav"
            print(" ", await call(
                "musik_do", {"aktion": "master.record", "argument": str(recording)}
            ))
            print(" ", await call(
                "musik_set", {"control": "deck1.position", "wert": str(START_AT)}
            ))
            print(" ", await call("musik_set", {"control": "deck1.play", "wert": "1"}))
            await asyncio.sleep(LEAD_SECONDS)

            print("\n-- the situation the agent sees --")
            state = await console.status()
            for d in state.decks:
                print(
                    f"  {d.name} pos={d.position:.1f} left={d.beats_left} "
                    f"phrase={d.beats_to_phrase} outro={d.beats_to_outro} "
                    f"intro={d.intro_beats}"
                )

            print("\n-- drive --")
            decision = await drive(console)
            print(f"  {type(decision).__name__}: {decision}")
            if hasattr(decision, "note"):
                print(f"  note:  {decision.note()}")
                print(f"  calls: {decision.calls}")
            else:
                print("  nothing was sent.")
                print(" ", await call("musik_do", {"aktion": "master.record_stop"}))
                return 1

            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                await asyncio.sleep(2.0)
                state = await console.status()
                playing = [d.name for d in state.decks if d.playing]
                print(f"  xf={state.master.crossfader:+.2f} playing={playing}")
                if playing == ["deck2"]:
                    break
            else:
                print("  the transition never finished.")

            await asyncio.sleep(LEAD_SECONDS)
            state = await console.status()
            print(
                f"\nafter: xf={state.master.crossfader:+.2f} "
                f"transitions={state.master.transitions} repeats={state.master.repeats} "
                f"dropped={state.master.record_dropped:g}"
            )
            print(" ", await call("musik_do", {"aktion": "master.record_stop"}))
            return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", type=Path, required=True, help="the Musik repo, already built")
    ap.add_argument("--out", type=Path, default=Path("/tmp/abnahme"))
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import material

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    print("material:")
    outgoing, incoming = material.make(out)

    socket = out / "musik.sock"
    approval = out / "freigabe.txt"
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    approval.write_text(f"klassen {CLASSES}\nbis {until}\nvon abnahme\n")
    socket.unlink(missing_ok=True)

    app = args.engine / "target/release/musik-app"
    if not app.exists():
        print(f"{app} is missing — cargo build --release --bins in the engine repo first")
        return 2

    log_path = out / "app.log"
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [
            "xvfb-run", "-a", str(app),
            "--a", str(outgoing),
            "--b", str(incoming),
            "--socket", str(socket),
            "--cache", str(out / ".cache"),
        ],
        env=dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", WGPU_BACKEND="gl"),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(120):
            if socket.exists():
                break
            time.sleep(0.5)
        else:
            print(f"the socket never appeared. {log_path}:")
            print(log_path.read_text()[-2000:])
            return 2
        time.sleep(1.0)
        code = asyncio.run(drive_one(args.engine, out, socket, approval, args.repo))
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    print(f"\nnow let the critic look:\n  {args.engine}/target/release/musik-kritik "
          f"{out}/mitschnitt.wav")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
