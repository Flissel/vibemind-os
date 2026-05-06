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
    uvicorn.run(app, host="0.0.0.0", port=5000)
