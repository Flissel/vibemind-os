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


# --- Bildteile (Task 1) ----------------------------------------------------

import base64  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20


def _bild(mime: str = "png", daten: bytes = PNG) -> dict:
    url = f"data:image/{mime};base64," + base64.b64encode(daten).decode()
    return {"type": "image_url", "image_url": {"url": url}}


def test_bildteile_ablegen_ersetzt_durch_verweis_und_schreibt_datei(tmp_path):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "Schau:"}, _bild("png"), _bild("jpeg", b"JPG!")]},
    ]
    neu, pfade = shim.bildteile_ablegen(messages, str(tmp_path))
    assert [Path(p).name for p in pfade] == ["bild-1.png", "bild-2.jpg"]
    assert Path(pfade[0]).read_bytes() == PNG and Path(pfade[1]).read_bytes() == b"JPG!"
    teile = neu[1]["content"]
    assert teile[0] == {"type": "text", "text": "Schau:"}
    assert teile[1] == {"type": "text", "text": f"\n[Bild 1: {pfade[0]} – lies die Datei mit dem Read-Werkzeug]"}
    assert teile[2]["text"].startswith("\n[Bild 2: ")
    assert neu[0] == messages[0]
    assert messages[1]["content"][1]["type"] == "image_url"  # Original unberuehrt


def test_bildteile_ablegen_webp_endung(tmp_path):
    _, pfade = shim.bildteile_ablegen([{"role": "user", "content": [_bild("webp")]}], str(tmp_path))
    assert Path(pfade[0]).name == "bild-1.webp"


@pytest.mark.parametrize("url", [
    "data:image/gif;base64,R0lGOA==",
    "https://example.com/x.png",
    "data:image/png;base64,@@@nicht-base64@@@",
    "data:image/png,rohtext",
])
def test_bildteile_ablegen_lehnt_falsches_ab(tmp_path, url):
    msg = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]
    with pytest.raises(shim.ShimEingabeFehler):
        shim.bildteile_ablegen(msg, str(tmp_path))


def test_bildteile_ablegen_grenzen(tmp_path):
    zu_gross = [{"role": "user", "content": [_bild("png", b"x" * (10 * 1024 * 1024 + 1))]}]
    with pytest.raises(shim.ShimEingabeFehler):
        shim.bildteile_ablegen(zu_gross, str(tmp_path))
    genau = [{"role": "user", "content": [_bild("png", b"x" * (10 * 1024 * 1024))]}]
    assert len(shim.bildteile_ablegen(genau, str(tmp_path))[1]) == 1
    sechs = [{"role": "user", "content": [_bild() for _ in range(6)]}]
    assert len(shim.bildteile_ablegen(sechs, str(tmp_path))[1]) == 6
    sieben = [{"role": "user", "content": [_bild() for _ in range(7)]}]
    with pytest.raises(shim.ShimEingabeFehler):
        shim.bildteile_ablegen(sieben, str(tmp_path))


def test_render_messages_liste_nur_text_wie_string():
    als_liste = [{"role": "user", "content": [{"type": "text", "text": "hallo"}]}]
    als_text = [{"role": "user", "content": "hallo"}]
    assert shim.render_messages(als_liste) == shim.render_messages(als_text)


def _post_bilder(port: int, bilder: int, stream: bool = False, echt: bool = False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    body = {"model": "claude-code-sonnet", "stream": stream,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Was siehst du?"}]
                          + [_bild() for _ in range(bilder)]}]}
    if echt:
        body["marketing_stream"] = True
    conn.request("POST", "/v1/chat/completions", json.dumps(body),
                 {"Content-Type": "application/json"})
    return conn.getresponse()


@pytest.fixture()
def protokoll(tmp_path, monkeypatch):
    pfad = tmp_path / "protokoll.json"
    monkeypatch.setenv("FALSCH_PROTOKOLL", str(pfad))
    return pfad


def _geladen(pfad: Path) -> dict:
    return json.loads(pfad.read_text(encoding="utf-8"))


def test_bild_anfrage_cli_sieht_datei_flags_und_verweis_dann_geloescht(server, protokoll):
    resp = _post_bilder(server(), 1)
    assert resp.status == 200
    p = _geladen(protokoll)
    argv = p["argv"]
    assert argv[argv.index("--add-dir") + 1] == p["ordner"]
    assert Path(p["ordner"]).name.startswith("mshim-")
    erlaubt = argv[argv.index("--allowedTools") + 1:]
    assert f"Read({p['ordner']}/**)" in erlaubt
    assert "Read" not in argv  # nie das nackte Read: sonst liest die CLI jede Datei
    gesperrt = argv[argv.index("--disallowedTools") + 1:]
    for werkzeug in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert werkzeug in gesperrt
    assert p["dateien"] == {"bild-1.png": PNG.hex()}
    assert f"[Bild 1: {os.path.join(p['ordner'], 'bild-1.png')}" in p["stdin"]
    assert "Read-Werkzeug" in p["stdin"]
    assert not os.path.exists(p["ordner"])
    # Read allein loest den Captain-Systemprompt-Zusatz NICHT aus
    assert "--system-prompt-file" not in argv


def test_read_als_einziges_werkzeug_laesst_systemprompt_unveraendert(server, protokoll):
    conn = http.client.HTTPConnection("127.0.0.1", server(), timeout=30)
    body = {"messages": [{"role": "system", "content": "Du bist Designer."},
                         {"role": "user", "content": [_bild()]}]}
    conn.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
    assert conn.getresponse().status == 200
    p = _geladen(protokoll)
    assert p["system"] == "Du bist Designer."
    assert f"Read({p['ordner']}/**)" in p["argv"] and "Read" not in p["argv"]


def test_bild_anfrage_cli_exit_loescht_ordner_und_gibt_502(server, protokoll):
    resp = _post_bilder(server("exit"), 1)
    assert resp.status == 502
    assert not os.path.exists(_geladen(protokoll)["ordner"])


def test_bild_anfrage_im_stream_pfad(server, protokoll):
    daten = list(_chunks(_post_bilder(server(), 2, stream=True, echt=True)))
    assert daten[-1][1] == "[DONE]"
    p = _geladen(protokoll)
    assert "--add-dir" in p["argv"] and "Read" not in p["argv"]
    assert f"Read({p['ordner']}/**)" in p["argv"] and "--disallowedTools" in p["argv"]
    assert sorted(p["dateien"]) == ["bild-1.png", "bild-2.png"]
    assert not os.path.exists(p["ordner"])


def test_bild_anfrage_im_stream_pfad_cli_exit_loescht_ordner(server, protokoll):
    daten = list(_chunks(_post_bilder(server("exit"), 1, stream=True, echt=True)))
    assert json.loads(daten[-2][1])["choices"][0]["finish_reason"] == "error"
    assert not os.path.exists(_geladen(protokoll)["ordner"])


def test_sieben_bilder_ist_400(server, protokoll):
    resp = _post_bilder(server(), 7)
    assert resp.status == 400
    assert not protokoll.exists()  # CLI nie gestartet


def test_gif_ist_400(server):
    conn = http.client.HTTPConnection("127.0.0.1", server(), timeout=30)
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGOA=="}}]}]}
    conn.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
    assert conn.getresponse().status == 400


def test_ohne_bildteile_argumente_unveraendert(server, protokoll):
    assert _post(server(), False).status == 200
    argv = _geladen(protokoll)["argv"]
    assert "--add-dir" not in argv and "--allowedTools" not in argv and "Read" not in argv
    assert "--disallowedTools" not in argv
    assert argv[:3] == ["-p", "--output-format", "json"]


# -- Budget-Waechter (Spec 2026-10-06) ---------------------------------------
def _budget_modul(tmp_path, erlaubt: bool, grund: str):
    p = tmp_path / "budget_fake.py"
    p.write_text(
        "from types import SimpleNamespace\n"
        "GEBUCHT = []\n"
        f"def pruefen(agent, *, umgebung=None):\n    return SimpleNamespace(erlaubt={erlaubt}, grund={grund!r}, agent=agent)\n"
        "def buchen(agent, ergebnis, dauer_s, *, umgebung=None):\n    GEBUCHT.append((agent, ergebnis))\n",
        encoding="utf-8")
    return p


def test_budget_modul_mit_dataclass_und_future_annotations(monkeypatch, tmp_path):
    """Regression: ohne sys.modules-Eintrag scheitert @dataclass -> alles 429."""
    p = tmp_path / "budget_dataclass.py"
    p.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class Entscheidung:\n    erlaubt: bool\n    grund: str\n    agent: str\n"
        "def pruefen(agent, *, umgebung=None):\n    return Entscheidung(True, 'ok_dc', agent)\n"
        "def buchen(agent, ergebnis, dauer_s, *, umgebung=None):\n    pass\n",
        encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(p))
    shim._BUDGET_CACHE.clear()
    assert shim.budget_pruefen() == (True, "ok_dc")
    shim._BUDGET_CACHE.clear()


def test_budget_aus_ohne_agentenname(monkeypatch):
    monkeypatch.delenv("VIBEMIND_AGENT", raising=False)
    assert shim.budget_pruefen() == (True, "aus")


def test_budget_ablehnung(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(_budget_modul(tmp_path, False, "tagesgrenze")))
    shim._BUDGET_CACHE.clear()
    assert shim.budget_pruefen() == (False, "tagesgrenze")


def test_budget_erlaubt_uebergang(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(_budget_modul(tmp_path, True, "ok_uebergang")))
    shim._BUDGET_CACHE.clear()
    assert shim.budget_pruefen() == (True, "ok_uebergang")


def test_budget_modul_fehlt_ist_waechter_fehler(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(tmp_path / "gibtsnicht.py"))
    shim._BUDGET_CACHE.clear()
    assert shim.budget_pruefen() == (False, "waechter_fehler")


def test_budget_ablehnung_http_429_ohne_cli_aufruf(server, protokoll, monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(_budget_modul(tmp_path, False, "tagesgrenze")))
    resp = _post(server(), False)
    assert resp.status == 429
    err = json.loads(resp.read())["error"]
    assert err["type"] == "budget"
    assert "tagesgrenze" in err["message"] and "marketing-chat" in err["message"]
    assert not protokoll.exists()  # falsche_cli.py wurde nie gestartet


def test_budget_erlaubt_http_bucht_ok(server, protokoll, monkeypatch, tmp_path):
    modul = _budget_modul(tmp_path, True, "ok")
    log = tmp_path / "gebucht.txt"
    modul.write_text(modul.read_text(encoding="utf-8").replace(
        "GEBUCHT.append((agent, ergebnis))",
        f"open({str(log)!r}, 'a').write(agent + ':' + ergebnis + chr(10))"), encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_AGENT", "marketing-chat")
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(modul))
    resp = _post(server(), False)
    assert resp.status == 200
    resp.read()
    assert protokoll.exists()
    assert log.read_text(encoding="utf-8").strip() == "marketing-chat:ok"


def _extra_config(tmp_path: Path, monkeypatch) -> None:
    datei = tmp_path / "extra-mcp.json"
    datei.write_text(json.dumps({"mcpServers": {"marketing": {"command": "x"}},
                                 "allowedTools": ["mcp__marketing__versand_beauftragen"]}), encoding="utf-8")
    monkeypatch.setenv("SHIM_EXTRA_MCP_CONFIG", str(datei))
    monkeypatch.setattr(shim, "resolve_cli", lambda: "cli")


def _bauen(**kw):
    argv, datei, _ = shim._build_command(system_prompt="S", model=None, response_format=None, streaming=False, **kw)
    if datei:
        os.unlink(datei)
    return argv


def test_ohne_flag_behaelt_marketing_werkzeuge(tmp_path, monkeypatch):
    _extra_config(tmp_path, monkeypatch)
    argv = _bauen()
    assert "marketing" in json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert "mcp__marketing__versand_beauftragen" in argv


def test_flag_ohne_werkzeuge_laesst_extra_server_weg(tmp_path, monkeypatch):
    _extra_config(tmp_path, monkeypatch)
    argv = _bauen(ohne_werkzeuge=True)
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert not any(a.startswith("mcp__") for a in argv) and "--allowedTools" not in argv


def test_flag_ohne_werkzeuge_behaelt_bild_read_und_sperren(tmp_path, monkeypatch):
    _extra_config(tmp_path, monkeypatch)
    argv = _bauen(ohne_werkzeuge=True, bilder_ordner=str(tmp_path))
    assert argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")] == [f"Read({tmp_path}/**)"]
    assert "Bash" in argv[argv.index("--disallowedTools") + 1:]
    assert not any(a.startswith("mcp__") for a in argv)


@pytest.mark.parametrize("flag,stream", [(True, False), (True, True), (False, False)])
def test_body_flag_marketing_ohne_werkzeuge_erreicht_die_cli(server, protokoll, tmp_path, monkeypatch, flag, stream):
    _extra_config(tmp_path, monkeypatch)
    conn = http.client.HTTPConnection("127.0.0.1", server(), timeout=30)
    body = {"stream": stream, "messages": [{"role": "user", "content": "hi"}]}
    if stream:
        body["marketing_stream"] = True
    if flag:
        body["marketing_ohne_werkzeuge"] = True
    conn.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
    conn.getresponse().read()
    argv = _geladen(protokoll)["argv"]
    server_cfg = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert ("marketing" in server_cfg) is (not flag)
    assert ("mcp__marketing__versand_beauftragen" in argv) is (not flag)


# --- Denken sichtbar -------------------------------------------------------
def _sse(server, body, modus: str = ""):
    port = server(modus)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("POST", "/v1/chat/completions", json.dumps(body),
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    return [json.loads(p) for _, p in _chunks(resp) if p != "[DONE]"]


def _denk_body(**extra):
    return {"model": "claude-code-sonnet", "stream": True, "marketing_stream": True,
            "messages": [{"role": "user", "content": "hi"}], **extra}


def test_denk_schalter_nur_auf_anforderung(server, protokoll):
    _sse(server, _denk_body())
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    assert "--thinking-display" not in argv
    _sse(server, _denk_body(marketing_denken=True))
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    i = argv.index("--thinking-display")
    assert argv[i + 1] == "summarized"
    assert argv[argv.index("--max-thinking-tokens") + 1] == "4000"


def test_marketing_denken_0_schaltet_ab(server, protokoll, monkeypatch):
    monkeypatch.setenv("MARKETING_DENKEN", "0")
    _sse(server, _denk_body(marketing_denken=True))
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    assert "--thinking-display" not in argv and "--max-thinking-tokens" not in argv


def test_denken_kommt_als_reasoning_content(server):
    chunks = _sse(server, _denk_body(marketing_denken=True))
    deltas = [c["choices"][0]["delta"] for c in chunks]
    denken = "".join(d.get("reasoning_content", "") for d in deltas)
    inhalt = "".join(d.get("content", "") or "" for d in deltas)
    assert denken == "Let me think about it."
    assert inhalt == "Antwort"                 # Denken nie im Inhalt


def test_rueckfall_ohne_denk_schalter(server):
    chunks = _sse(server, _denk_body(marketing_denken=True), modus="denken_abgelehnt")
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert "".join(d.get("reasoning_content", "") for d in deltas) == "(Denken nicht verfügbar)"
    assert "".join(d.get("content", "") or "" for d in deltas) == "eins zwei drei"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def _laeufe(zaehler: Path) -> int:
    return len(zaehler.read_text(encoding="utf-8").splitlines()) if zaehler.exists() else 0


def test_rueckfall_bei_anderem_fehlertext(server, tmp_path, monkeypatch):
    zaehler = tmp_path / "zaehler.txt"
    monkeypatch.setenv("FALSCH_ZAEHLER", str(zaehler))
    chunks = _sse(server, _denk_body(marketing_denken=True), modus="denken_wert_ungueltig")
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert "".join(d.get("reasoning_content", "") for d in deltas) == "(Denken nicht verfügbar)"
    assert "".join(d.get("content", "") or "" for d in deltas) == "eins zwei drei"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert _laeufe(zaehler) == 2


def test_fehler_nach_erstem_stueck_wird_nicht_wiederholt(server, tmp_path, monkeypatch):
    zaehler = tmp_path / "zaehler.txt"
    monkeypatch.setenv("FALSCH_ZAEHLER", str(zaehler))
    chunks = _sse(server, _denk_body(marketing_denken=True), modus="fehler_nach_stueck")
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert "reasoning_content" not in "".join(d for x in deltas for d in x)
    assert chunks[-1]["choices"][0]["finish_reason"] == "error"
    assert _laeufe(zaehler) == 1


def test_fehler_ohne_denken_wird_nicht_wiederholt(server, tmp_path, monkeypatch):
    zaehler = tmp_path / "zaehler.txt"
    monkeypatch.setenv("FALSCH_ZAEHLER", str(zaehler))
    chunks = _sse(server, _denk_body(), modus="exit")
    assert chunks[-1]["choices"][0]["finish_reason"] == "error"
    assert _laeufe(zaehler) == 1


def test_rueckfall_wird_nicht_selbst_wiederholt():
    aufrufe = []

    def kaputt(**kw):
        aufrufe.append(kw.get("denken"))
        raise shim.ShimError("immer kaputt")
        yield  # pragma: no cover

    original = shim.stream_claude
    shim.stream_claude = kaputt
    try:
        with pytest.raises(shim.ShimError, match="immer kaputt"):
            list(shim.stream_denkend(denken=True))
    finally:
        shim.stream_claude = original
    assert aufrufe == [True, False]


def test_text_stuecke_ohne_mit_denken_ignoriert_thinking():
    zeilen = ['{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"x"}}}',
              '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"a"}}}']
    assert list(shim.text_stuecke(zeilen)) == ["a"]
    mit = list(shim.text_stuecke(zeilen, mit_denken=True))
    assert mit == ["x", "a"] and isinstance(mit[0], shim.Denken) and not isinstance(mit[1], shim.Denken)


# --- Websuche (Spec 2026-10-09-marke-exakt §2) -------------------------------
def _web_body(**extra):
    return _denk_body(marketing_websuche=True, marketing_ohne_werkzeuge=True, **extra)


def _liste(argv, schalter):
    i, aus = argv.index(schalter) + 1, []
    while i < len(argv) and not argv[i].startswith("--"):
        aus.append(argv[i])
        i += 1
    return aus


def test_websuche_nur_mit_flag(server, protokoll):
    _sse(server, _denk_body())
    assert "WebSearch" not in json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    _sse(server, _web_body())
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    assert "WebSearch" in _liste(argv, "--allowedTools")
    gesperrt = _liste(argv, "--disallowedTools")
    assert "WebFetch" in gesperrt and "Bash" in gesperrt and "WebSearch" not in gesperrt


def test_websuche_mit_bildern_webfetch_bleibt_gesperrt(tmp_path, monkeypatch):
    monkeypatch.setattr(shim, "resolve_cli", lambda: "cli")
    argv = _bauen(ohne_werkzeuge=True, bilder_ordner=str(tmp_path), websuche=True)
    assert _liste(argv, "--allowedTools")[-2:] == [f"Read({tmp_path}/**)", "WebSearch"]
    gesperrt = _liste(argv, "--disallowedTools")
    assert "WebFetch" in gesperrt and "WebSearch" not in gesperrt


def test_websuche_genutzt_kommt_als_marketing_werkzeug(server):
    chunks = _sse(server, _web_body(), modus="websuche_genutzt")
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert [d["marketing_werkzeug"] for d in deltas if "marketing_werkzeug" in d] == ["WebSearch"]
    assert "".join(d.get("content", "") or "" for d in deltas) == "Antwort"


def test_websuche_abgelehnt_einmal_ohne(server, tmp_path, monkeypatch):
    zaehler = tmp_path / "zaehler.txt"
    monkeypatch.setenv("FALSCH_ZAEHLER", str(zaehler))
    chunks = _sse(server, _web_body(), modus="websuche_abgelehnt")
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert [d["marketing_werkzeug"] for d in deltas if "marketing_werkzeug" in d] == ["WebSearch:aus"]
    assert "".join(d.get("content", "") or "" for d in deltas) == "eins zwei drei"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and _laeufe(zaehler) == 2


def test_rueckfall_reihenfolge_erst_websuche_dann_denken():
    aufrufe = []

    def kaputt(**kw):
        aufrufe.append((kw.get("websuche"), kw.get("denken")))
        raise shim.ShimError("immer kaputt")
        yield  # pragma: no cover

    original = shim.stream_claude
    shim.stream_claude = kaputt
    try:
        with pytest.raises(shim.ShimError, match="immer kaputt"):
            list(shim.stream_denkend(websuche=True, denken=True))
    finally:
        shim.stream_claude = original
    assert aufrufe == [(True, True), (False, True), (False, False)]


def test_text_stuecke_meldet_websuche_nur_auf_wunsch():
    zeilen = ['{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":'
              '{"type":"server_tool_use","id":"s","name":"web_search","input":{}}}}',
              '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"a"}}}']
    assert list(shim.text_stuecke(zeilen)) == ["a"]
    mit = list(shim.text_stuecke(zeilen, mit_werkzeug=True))
    assert mit == ["WebSearch", "a"] and isinstance(mit[0], shim.Werkzeug) and not isinstance(mit[1], shim.Werkzeug)


def test_websuche_ohne_ohne_werkzeuge_wird_ignoriert(server, protokoll):
    _sse(server, _denk_body(marketing_websuche=True))
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    assert "WebSearch" not in argv and "--allowedTools" not in argv

# --- Eingebaute Werkzeuge sperren (--tools) -----------------------------------
def _tools(argv):
    i = argv.index("--tools") + 1
    aus = []
    while i < len(argv) and not (argv[i].startswith("--") and argv[i] != ""):
        aus.append(argv[i])
        i += 1
    return aus


def test_tools_leer_ohne_bilder_ohne_websuche():
    argv = _bauen(ohne_werkzeuge=True)
    assert argv[argv.index("--tools") + 1] == ""
    assert _tools(argv) == [""]


def test_tools_nur_read_mit_bildern(tmp_path):
    argv = _bauen(ohne_werkzeuge=True, bilder_ordner=str(tmp_path))
    assert _tools(argv) == ["Read"]


def test_tools_nur_websearch_mit_websuche():
    argv = _bauen(ohne_werkzeuge=True, websuche=True)
    assert _tools(argv) == ["WebSearch"]


def test_tools_read_und_websearch(tmp_path):
    argv = _bauen(ohne_werkzeuge=True, bilder_ordner=str(tmp_path), websuche=True)
    assert _tools(argv) == ["Read", "WebSearch"]


def test_tools_fehlt_ohne_ohne_werkzeuge(tmp_path):
    assert "--tools" not in _bauen()
    assert "--tools" not in _bauen(bilder_ordner=str(tmp_path))


def test_tools_websuche_rueckfall_ohne_websearch(server, protokoll, tmp_path, monkeypatch):
    monkeypatch.setenv("FALSCH_ZAEHLER", str(tmp_path / "zaehler.txt"))
    _sse(server, _web_body(), modus="websuche_abgelehnt")
    argv = json.loads(protokoll.read_text(encoding="utf-8"))["argv"]
    assert _tools(argv) == [""]
