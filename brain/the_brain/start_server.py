"""Quick start script for the Brain Nervous System server."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Phase 11.I — load .env so all LLM-using tools (idea_expand, classify, etc)
# inherit OPENROUTER_API_KEY / OPENAI_API_KEY / GROQ_API_KEY from the repo
# .env when brain is spawned without env inheritance (debug.ps1, electron).
try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    _candidates = [
        os.path.join(_here, ".env"),
        os.path.normpath(os.path.join(_here, "..", "..", "..", ".env")),
        os.path.normpath(os.path.join(_here, "..", "..", ".env")),
    ]
    for _envp in _candidates:
        if os.path.isfile(_envp):
            load_dotenv(_envp)
            print(f"[brain-start] loaded env from {_envp}")
            break
except Exception as _e:
    print(f"[brain-start] dotenv unavailable: {_e}")

import uvicorn
from web.brain_server import create_app

app = create_app(testing=False)

if __name__ == "__main__":
    # Port is overridable via argv[1] or BRAIN_PORT so the debug launcher can
    # pass CONFIG.ports.brain_dashboard. Default 5000 keeps the documented
    # `python -m web.brain_server` / `python start_server.py` behaviour.
    # NOTE: the app object is passed to uvicorn.run() directly (not an import
    # string) on purpose — an import string makes uvicorn fork a worker that
    # re-resolves `python` via PATH (pyenv-3.11 instead of the active venv),
    # producing two competing processes that never bind the port cleanly.
    _port = 5000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        _port = int(sys.argv[1])
    elif os.environ.get("BRAIN_PORT", "").isdigit():
        _port = int(os.environ["BRAIN_PORT"])
    print(f"[brain-start] starting uvicorn on 0.0.0.0:{_port}")
    uvicorn.run(app, host="0.0.0.0", port=_port)
