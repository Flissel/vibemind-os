"""Tests fuer den Marketing-Shim: echtes Streaming ueber stream-json."""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SHIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SHIM_DIR))
import marketing_shim as shim  # noqa: E402

FALSCHE_CLI = Path(__file__).with_name("falsche_cli.py")

ZEILEN = [
    '{"type":"stream_event","event":{"type":"message_start"}}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"eins"}}}',
    "das ist kein json",
    '{"type":"rate_limit_event"}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"x"}}}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" zwei"}}}',
    '{"type":"assistant","message":{}}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" drei"}}}',
    '{"type":"result","subtype":"success","is_error":false,"result":"eins zwei drei"}',
]


def test_text_stuecke_liefert_nur_text_deltas():
    assert list(shim.text_stuecke(ZEILEN)) == ["eins", " zwei", " drei"]


def test_text_stuecke_result_fehler_wirft():
    zeilen = ZEILEN[:2] + ['{"type":"result","is_error":true,"result":"boese"}']
    gen = shim.text_stuecke(zeilen)
    assert next(gen) == "eins"
    with pytest.raises(shim.ShimError, match="boese"):
        next(gen)


def test_stream_argv_nutzt_stream_json():
    argv = shim.stream_argv("cli", "claude-code-sonnet")
    assert argv[:2] == ["cli", "-p"]
    assert "stream-json" in argv and "json" not in argv
    assert "--include-partial-messages" in argv and "--verbose" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"


def _cli_huelle(tmp_path: Path) -> str:
    huelle = tmp_path / "falsche_cli.cmd"
    huelle.write_text(f'@"{sys.executable}" "{FALSCHE_CLI}" %*\r\n', encoding="utf-8")
    return str(huelle)


@pytest.fixture()
def server(tmp_path):
    def starten(modus: str = "", cli: str | None = None):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = dict(os.environ, CLAUDE_CODE_CLI=cli or _cli_huelle(tmp_path), FALSCH_MODUS=modus)
        proc = subprocess.Popen(
            [sys.executable, str(SHIM_DIR / "marketing_shim.py"), "--port", str(port)],
            env=env, stderr=subprocess.DEVNULL,
        )
        procs.append(proc)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return port
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Shim startet nicht")

    procs: list[subprocess.Popen] = []
    yield starten
    for p in procs:
        p.kill()
        p.wait()


def _post(port: int, stream: bool, echt: bool = False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    body = {"model": "claude-code-sonnet", "stream": stream,
            "messages": [{"role": "user", "content": "hallo"}]}
    if echt:
        body["marketing_stream"] = True
    conn.request("POST", "/v1/chat/completions", json.dumps(body),
                 {"Content-Type": "application/json"})
    return conn.getresponse()


def _chunks(resp):
    """Liest SSE-Zeilen, liefert (zeit, payload)."""
    while True:
        zeile = resp.fp.readline()
        if not zeile:
            return
        zeile = zeile.decode("utf-8").strip()
        if zeile.startswith("data: "):
            yield time.monotonic(), zeile[6:]


def test_stream_echt_und_in_reihenfolge(server):
    port = server()
    start = time.monotonic()
    resp = _post(port, True, echt=True)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    daten = list(_chunks(resp))
    assert daten[-1][1] == "[DONE]"
    chunks = [json.loads(p) for _, p in daten[:-1]]
    texte = [c["choices"][0]["delta"].get("content") for c in chunks]
    assert [t for t in texte if t] == ["eins", " zwei", " drei"]
    assert len(daten) >= 4
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # erster Textchunk kommt, bevor die CLI fertig ist (die wartet 0,3 s nach dem letzten Delta)
    erste = next(t for (t, p), c in zip(daten, chunks) if c["choices"][0]["delta"].get("content"))
    assert daten[-1][0] - erste >= 0.25
    assert erste - start < daten[-1][0] - start


def test_ohne_stream_normale_completion(server):
    resp = _post(server(), False)
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "eins zwei drei"


def test_cli_exit_im_stream_gibt_error_chunk(server):
    resp = _post(server("exit"), True, echt=True)
    daten = list(_chunks(resp))
    assert daten[-1][1] == "[DONE]"
    letzter = json.loads(daten[-2][1])
    assert letzter["choices"][0]["finish_reason"] == "error"
    assert "3" in letzter["choices"][0]["delta"]["content"]


def _delta(text: str) -> str:
    return ('{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"%s"}}}' % text)


START = '{"type":"stream_event","event":{"type":"message_start"}}'


def test_text_stuecke_trennt_zwischenturns():
    zeilen = [START, _delta("Ich suche"), START, _delta("Fertig"), START]
    assert list(shim.text_stuecke(zeilen)) == ["Ich suche", "\n\n", "Fertig"]


def test_text_stuecke_result_rueckfall_ohne_delta():
    zeilen = ['{"type":"result","is_error":false,"result":"nur result"}']
    assert list(shim.text_stuecke(zeilen)) == ["nur result"]


def test_stream_zwei_turns_e2e(server):
    daten = list(_chunks(_post(server("zweiturns"), True, echt=True)))
    texte = [json.loads(p)["choices"][0]["delta"].get("content") for _, p in daten[:-1]]
    assert "".join(t for t in texte if t) == "Ich suche\n\nFertig"


def test_fehlende_cli_gibt_error_chunk(server, tmp_path):
    nichts = str(tmp_path / "gibt-es-nicht.exe")
    daten = list(_chunks(_post(server(cli=nichts), True, echt=True)))
    assert daten[-1][1] == "[DONE]"
    inhalt = json.loads(daten[-2][1])["choices"][0]
    assert inhalt["finish_reason"] == "error"
    assert "FileNotFoundError" in inhalt["delta"]["content"]
    assert nichts not in inhalt["delta"]["content"]


def test_stream_ohne_flag_ist_ein_chunk_wie_das_original(server):
    resp = _post(server(), True)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    daten = list(_chunks(resp))
    assert daten[-1][1] == "[DONE]"
    chunks = [json.loads(p) for _, p in daten[:-1]]
    assert len(chunks) == 2
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": "eins zwei drei"}
    assert chunks[0]["choices"][0]["finish_reason"] is None
    assert chunks[1]["choices"][0]["delta"] == {}
    assert chunks[1]["choices"][0]["finish_reason"] == "stop"


def test_stream_ohne_flag_cli_fehler_ist_502(server):
    resp = _post(server("exit"), True)
    assert resp.status == 502
    body = json.loads(resp.read())
    assert body["error"]["type"] == "shim_error"
