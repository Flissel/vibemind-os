# Kopie vom 02.10.2026 aus ~/.local/bin/claude_code_openai_shim.py; gehoert ab jetzt dem Marketing-Claw.
# Aenderung gegenueber dem Original: Body-Flag "marketing_stream": true streamt echt (stream-json), siehe stream_argv/text_stuecke.
# Ohne das Flag verhaelt sich stream:true exakt wie im Original (ein Chunk + stop + [DONE], 502 bei CLI-Fehler).
"""OpenAI-compatible HTTP shim backed by the Claude Code CLI subscription.

Hermes' ``custom`` provider speaks OpenAI's wire format and resolves its
endpoint from ``OPENAI_BASE_URL`` (see ``_resolve_custom_runtime`` in the
Hermes agent).  This server implements just enough of that surface to let
Captain's Hermes planner run on a Claude Code subscription instead of a
metered API key:

    GET  /v1/models             -> advertises the shim's model ids
    POST /v1/chat/completions   -> runs ``claude -p --output-format json``

Every request runs the CLI in an empty working directory with project and
user settings switched off, so a planning call does not drag the ~44k token
repository context into the subscription on every turn.

Standard library only, so it runs anywhere the CLI does.

Usage:
    python scripts/claude_code_openai_shim.py [--host 127.0.0.1] [--port 8114]

Then point Hermes at it:
    OPENAI_BASE_URL=http://127.0.0.1:8114/v1
    OPENAI_API_KEY=<any non-empty placeholder>
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Iterator


_BUDGET_CACHE: dict[str, Any] = {}


def _budget_modul() -> Any:
    pfad = os.environ.get("VIBEMIND_BUDGET_MODUL", "").strip()
    if pfad not in _BUDGET_CACHE:
        spec = importlib.util.spec_from_file_location("claude_budget_ext", pfad)
        if spec is None or spec.loader is None:
            raise ImportError(pfad)
        modul = importlib.util.module_from_spec(spec)
        # @dataclass sucht sys.modules[cls.__module__]; ohne Eintrag scheitert das Laden.
        sys.modules[spec.name] = modul
        try:
            spec.loader.exec_module(modul)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        _BUDGET_CACHE[pfad] = modul
    return _BUDGET_CACHE[pfad]


def budget_pruefen() -> tuple[bool, str]:
    """Budget-Waechter (Spec 2026-10-06). Ohne VIBEMIND_AGENT aus (Tests, VM)."""
    agent = os.environ.get("VIBEMIND_AGENT", "").strip()
    if not agent:
        return True, "aus"
    try:
        e = _budget_modul().pruefen(agent)
    except Exception:  # noqa: BLE001 - fail-closed
        return False, "waechter_fehler"
    return bool(e.erlaubt), str(e.grund)


def budget_buchen(ergebnis: str, dauer_s: float) -> None:
    agent = os.environ.get("VIBEMIND_AGENT", "").strip()
    if not agent:
        return
    try:
        _budget_modul().buchen(agent, ergebnis, dauer_s)
    except Exception as exc:  # noqa: BLE001 - Buchung darf den Dienst nicht kippen
        sys.stderr.write("budget: buchung fehlgeschlagen: %s\n" % exc)


def _gebucht(pieces: Iterator[str], start: float) -> Iterator[str]:
    """Reicht die Text-Stuecke durch und bucht am Ende (ok, oder fehler bei Ausnahme)."""
    ergebnis = "ok"
    try:
        yield from pieces
    except BaseException:
        ergebnis = "fehler"
        raise
    finally:
        close = getattr(pieces, "close", None)
        if close:
            close()
        budget_buchen(ergebnis, time.monotonic() - start)

DEFAULT_MODEL_ID = "claude-code"
# Model ids we advertise. The bare id lets the CLI pick the account default;
# the explicit aliases let a caller pin a tier.
ADVERTISED_MODELS = (DEFAULT_MODEL_ID, "claude-code-opus", "claude-code-sonnet")
_MODEL_ALIASES = {
    "claude-code-opus": "opus",
    "claude-code-sonnet": "sonnet",
    "claude-code-haiku": "haiku",
}


class ShimError(RuntimeError):
    """Raised when the CLI could not produce a usable answer."""


class ShimEingabeFehler(ValueError):
    """Die Anfrage selbst ist ungueltig (z. B. falsches Bildformat) -> HTTP 400."""


MAX_BILDER = 6
MAX_BILD_BYTES = 10 * 1024 * 1024
_BILD_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,(.*)$", re.DOTALL)
_BILD_GESPERRT = ("Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch")
_BILD_ENDUNG = {"png": "png", "jpeg": "jpg", "webp": "webp"}


def bildteile_ablegen(
    messages: list[dict[str, Any]], ordner: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ersetzt image_url-Teile der user-Nachrichten durch Textverweise auf Dateien.

    Die dekodierten Bytes landen als bild-N.<png|jpg|webp> in ``ordner``; die CLI
    liest sie dort mit dem Read-Werkzeug. Gibt (neue Nachrichten, Pfade) zurueck,
    die Originale bleiben unveraendert. Wirft ShimEingabeFehler bei anderem Schema,
    mehr als 6 Bildern, Bildern ueber 10 MB (dekodiert) oder ungueltigem Base64.
    """

    pfade: list[str] = []
    neu: list[dict[str, Any]] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not (isinstance(message, dict) and message.get("role") == "user" and isinstance(content, list)):
            neu.append(message)
            continue
        teile: list[Any] = []
        for teil in content:
            if not (isinstance(teil, dict) and teil.get("type") == "image_url"):
                teile.append(teil)
                continue
            if len(pfade) >= MAX_BILDER:
                raise ShimEingabeFehler(f"mehr als {MAX_BILDER} Bilder in einer Anfrage")
            bild = teil.get("image_url")
            url = bild.get("url") if isinstance(bild, dict) else bild
            treffer = _BILD_URL.match(url) if isinstance(url, str) else None
            if not treffer:
                raise ShimEingabeFehler("Bildteile nur als data:image/(png|jpeg|webp);base64,...")
            art, b64 = treffer.groups()
            if len(b64) > MAX_BILD_BYTES * 4 // 3 + 8:
                raise ShimEingabeFehler("Bild groesser als 10 MB")
            try:
                daten = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ShimEingabeFehler("Bild ist kein gueltiges Base64") from exc
            if not daten:
                raise ShimEingabeFehler("Bild ist leer")
            if len(daten) > MAX_BILD_BYTES:
                raise ShimEingabeFehler("Bild groesser als 10 MB")
            pfad = os.path.join(ordner, f"bild-{len(pfade) + 1}.{_BILD_ENDUNG[art]}")
            with open(pfad, "wb") as datei:
                datei.write(daten)
            pfade.append(pfad)
            teile.append({
                "type": "text",
                "text": f"\n[Bild {len(pfade)}: {pfad} – lies die Datei mit dem Read-Werkzeug]",
            })
        neu.append({**message, "content": teile})
    return neu, pfade


def resolve_cli() -> str:
    explicit = os.environ.get("CLAUDE_CODE_CLI", "").strip()
    if explicit:
        return explicit
    # Prefer the native install over PATH: a stale npm launcher can sit earlier
    # on PATH and fail with "claude.cmd not found" while this one works.
    for candidate in (
        os.path.expanduser(r"~\.local\bin\claude.exe"),
        os.path.expanduser("~/.local/bin/claude"),
    ):
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("claude")
    if found:
        return found
    raise ShimError("no Claude Code CLI found (set CLAUDE_CODE_CLI)")


def render_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Split OpenAI messages into (system prompt, conversation transcript)."""

    system_parts: list[str] = []
    turns: list[str] = []
    for message in messages or []:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if isinstance(content, list):
            # OpenAI content parts: keep the text ones, in order.
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content)
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            turns.append(f"Assistant: {content}")
        else:
            turns.append(f"{role.capitalize()}: {content}")

    transcript = "\n\n".join(turns).strip()
    if not transcript:
        transcript = "(no user message)"
    return "\n\n".join(system_parts).strip(), transcript


def _hat_bildteile(messages: Any) -> bool:
    return any(
        isinstance(m, dict)
        and m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(t, dict) and t.get("type") == "image_url" for t in m["content"])
        for m in (messages if isinstance(messages, list) else [])
    )


def schema_instruction(response_format: Any) -> str:
    """Turn an OpenAI ``response_format`` into an instruction the CLI can honour.

    The CLI has no structured-output mode, so the contract is carried in the
    prompt instead. Callers that ask for a schema get one described to them
    verbatim; anything less would mean advertising structured output the
    backend cannot actually deliver.
    """

    if not isinstance(response_format, dict):
        return ""
    kind = response_format.get("type")
    if kind == "json_object":
        return (
            "\n\nOutput format: reply with a single JSON object and nothing "
            "else -- no preamble, no code fences, no text outside the braces."
        )
    if kind != "json_schema":
        return ""
    spec = response_format.get("json_schema") or {}
    schema = spec.get("schema") or spec.get("json_schema")
    if not schema:
        return ""
    name = spec.get("name") or "the requested object"
    return (
        f"\n\nOutput format: reply with a single JSON object named {name} that "
        "validates against this JSON Schema, and nothing else -- no preamble, "
        "no code fences, no text before the opening brace or after the closing "
        "brace. Include every required property and add no property the schema "
        "does not define.\n\n" + json.dumps(schema)
    )


def _model_args(model: str | None) -> list[str]:
    resolved_model = _MODEL_ALIASES.get((model or "").strip())
    return ["--model", resolved_model] if resolved_model else []


def stream_argv(cli: str, model: str | None) -> list[str]:
    """Kopf der CLI-Kommandozeile fuer echtes Streaming (stream-json)."""

    return [
        cli, "-p", "--output-format", "stream-json",
        "--include-partial-messages", "--verbose",
    ] + _model_args(model)


DENK_TOKEN = 4000
DENKEN_NICHT_VERFUEGBAR = "(Denken nicht verfügbar)"


class Denken(str):
    """Ein Strom-Stueck mit Claudes (zusammengefasstem) Denken - nie Antworttext."""


def _denken_erlaubt() -> bool:
    return os.environ.get("MARKETING_DENKEN", "1").strip() != "0"


def _build_command(
    *,
    system_prompt: str,
    model: str | None,
    response_format: Any,
    streaming: bool,
    bilder_ordner: str | None = None,
    ohne_werkzeuge: bool = False,
    denken: bool = False,
) -> tuple[list[str], str | None, dict[str, str]]:
    """Baut (argv, system_prompt_file, environment) -- gemeinsam fuer beide Pfade."""

    cli = resolve_cli()
    if streaming:
        argv = stream_argv(cli, model)
        if denken and _denken_erlaubt():
            argv += ["--max-thinking-tokens", str(DENK_TOKEN), "--thinking-display", "summarized"]
    else:
        argv = [cli, "-p", "--output-format", "json"] + _model_args(model)

    # Keep the call cheap and deterministic: no inherited project/user settings
    # and no ambient MCP servers.
    #
    # The one exception is Captain's artifact store. A planner is required to
    # return resolvable artifact:// references; without a way to persist one it
    # can only inline the plan or fabricate a digest, and fabricating is the
    # wrong answer. So when an artifact root is configured we hand the model
    # exactly two tools -- write_artifact and read_artifact -- and nothing else.
    mcp_servers: dict[str, Any] = {}
    allowed_tools: list[str] = []
    store_root = os.environ.get("CAPTAIN_ARTIFACT_ROOT", "").strip()
    mcp_script = os.environ.get(
        "CAPTAIN_ARTIFACT_MCP",
        str(pathlib.Path(__file__).resolve().parent / "captain_artifact_mcp.py"),
    )
    if store_root and os.path.isfile(mcp_script):
        # get_contract_schema imports Captain's Pydantic contracts, so the server
        # needs an interpreter that has them -- usually the repo venv, not this
        # process's own interpreter.
        mcp_python = os.environ.get("CAPTAIN_MCP_PYTHON", "").strip() or sys.executable
        mcp_env = {"CAPTAIN_ARTIFACT_ROOT": store_root}
        repo_root = os.environ.get("CAPTAIN_REPO_ROOT", "").strip()
        if repo_root:
            mcp_env["CAPTAIN_REPO_ROOT"] = repo_root
        mcp_servers["captain"] = {
            "command": mcp_python,
            "args": [mcp_script],
            "env": mcp_env,
        }
        allowed_tools = [
            "mcp__captain__write_artifact",
            "mcp__captain__read_artifact",
            "mcp__captain__get_contract_schema",
        ]

    # Zusaetzliche MCP-Server aus einer Datei -- env-gated, Vorgabe: keine.
    #
    # WARUM. Dieser Shim reicht die `tools` des Aufrufers NICHT als tool_calls
    # zurueck; er protokolliert sie nur. Ein Aufrufer mit eigener Agenten-
    # schleife (marketing-claw ueber openclaw) bekommt seine Werkzeuge deshalb
    # nie gerufen. Loesung ohne Aenderung an der Antwortform: die Werkzeuge in
    # die CLI-Schleife hineinreichen, wo sie tatsaechlich benutzt werden.
    # Ohne SHIM_EXTRA_MCP_CONFIG aendert sich fuer bestehende Verbraucher
    # (Hermes, Captain) nichts -- das ist der Sinn des Tors.
    extra_pfad = os.environ.get("SHIM_EXTRA_MCP_CONFIG", "").strip()
    # ohne_werkzeuge (Body-Flag marketing_ohne_werkzeuge): der Chat-Agent antwortet nur mit JSON und
    # liest fremden Text (Unterlagen, Bilder) -- er bekommt die Marketing-Werkzeuge nicht.
    if extra_pfad and os.path.isfile(extra_pfad) and not ohne_werkzeuge:
        try:
            extra = json.loads(pathlib.Path(extra_pfad).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 -- eine kaputte Datei darf den Dienst nicht kippen
            sys.stderr.write(f"extra mcp config unreadable ({extra_pfad}): {exc}\n")
        else:
            for name, spec in (extra.get("mcpServers") or {}).items():
                mcp_servers[name] = spec
            allowed_tools += [t for t in (extra.get("allowedTools") or []) if isinstance(t, str)]

    argv += ["--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": mcp_servers})]
    argv += ["--setting-sources", ""]
    # Bilder: Ordner freigeben und Read erlauben. allowed_tools bleibt bewusst
    # unberuehrt, denn der Captain-Systemprompt-Zusatz unten haengt daran.
    cli_tools = list(allowed_tools)
    if bilder_ordner:
        argv += ["--add-dir", bilder_ordner]
        # Nie das nackte "Read": es liesse die CLI jede Datei des PCs lesen
        # (Prompt-Injection auf .env). Nur der Bildordner ist lesbar.
        cli_tools.append(f"Read({bilder_ordner}/**)")
    if cli_tools:
        argv += ["--allowedTools", *cli_tools]
    if bilder_ordner:
        argv += ["--disallowedTools", *_BILD_GESPERRT]

    # The caller's own prompt asks for opaque artifact references but cannot know
    # how this backend produces one, so the side that supplies the tool documents
    # it. Without this the model has no way to persist anything and falls back to
    # inlining the body -- which then fails the caller's schema.
    if allowed_tools and system_prompt:
        system_prompt += (
            "\n\nTooling for this backend. Before composing a typed result, call "
            "get_contract_schema with the schema name you were asked to return "
            "(for example captain.hermes-plan-result.v1) and follow it exactly: "
            "supply every required field and add no field it does not define, "
            "because the contract rejects both omissions and extras. Persist every "
            "plan body, decision log and blueprint with write_artifact and use the "
            "uri and sha256 it returns verbatim in the matching *_ref field. Never "
            "inline such a body where a reference is expected, and never invent a "
            "digest. Use read_artifact to dereference any artifact:// uri you are "
            "given.\n\nOutput format: when a typed result is requested, your entire "
            "final message must be that single JSON object and nothing else -- no "
            "preamble, no explanation, no code fences, no text before the opening "
            "brace or after the closing brace. The caller parses from the first "
            "character and any prose makes the whole result unreadable. Put "
            "anything you would have explained into the decision log artifact "
            "instead."
        )

    system_prompt += schema_instruction(response_format)

    # The system prompt goes through a file, never the command line: a real
    # agent system prompt runs to tens of thousands of characters and Windows
    # caps a whole command line at 32767, where the CLI aborts before it reads
    # any input (observed: is_error with 0 input tokens after ~800ms).
    # Entschaerfung fuer openclaw-Prompts (env-gated, Vorgabe: aus). GEMESSEN
    # 03.09.2026 durch Bisektion eines 1:1 nachgespielten openclaw-Aufrufs: die
    # Zeilen "first token [[reply_to_current]]; use [[reply_to:<id>]] ..." und
    # "Supported directives are stripped before rendering ..." ZUSAMMEN lassen
    # die CLI mit "API Error: 400 You're out of extra usage" abbrechen -- bei
    # vollem Kontingent, 0 Tokens, duration_api_ms 0; jede Zeile allein und
    # jeder Marker allein laufen durch. Das Ersetzen von "[[" durch "[ [" heilt
    # es deterministisch. Ohne SHIM_NEUTRALIZE_DOUBLE_BRACKETS=1 bleibt der
    # Prompt unveraendert (Hermes, Captain).
    if system_prompt and os.environ.get("SHIM_NEUTRALIZE_DOUBLE_BRACKETS", "").strip() == "1":
        system_prompt = system_prompt.replace("[[", "[ [").replace("]]", "] ]")

    system_prompt_file: str | None = None
    if system_prompt:
        handle, system_prompt_file = tempfile.mkstemp(
            prefix="claude-shim-system-", suffix=".txt"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(system_prompt)
        argv += ["--system-prompt-file", system_prompt_file]

    environment = os.environ.copy()
    # Never let an ambient key silently redirect the CLI itself.
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("ANTHROPIC_AUTH_TOKEN", None)
    return argv, system_prompt_file, environment


def _unlink_quiet(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_claude(
    *,
    system_prompt: str,
    transcript: str,
    model: str | None,
    timeout: float,
    response_format: Any = None,
    bilder_ordner: str | None = None,
    ohne_werkzeuge: bool = False,
) -> dict[str, Any]:
    argv, system_prompt_file, environment = _build_command(
        system_prompt=system_prompt,
        model=model,
        response_format=response_format,
        streaming=False,
        bilder_ordner=bilder_ordner,
        ohne_werkzeuge=ohne_werkzeuge,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="claude-shim-") as workdir:
            try:
                completed = subprocess.run(
                    argv,
                    input=transcript,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    cwd=workdir,
                    env=environment,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ShimError(f"Claude Code CLI timed out after {timeout}s") from exc
    finally:
        _unlink_quiet(system_prompt_file)

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        sys.stderr.write(f"cli rc={completed.returncode} output={detail[:4000]}\n")
        sys.stderr.flush()
        raise ShimError(f"Claude Code CLI exited {completed.returncode}: {detail[:500]}")

    raw = (completed.stdout or "").strip()
    if not raw:
        # The CLI reports some failures on stdout with a zero exit status, so an
        # empty stdout is its own distinct fault worth naming.
        raise ShimError("Claude Code CLI produced no output")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShimError(f"Claude Code CLI returned non-JSON output: {raw[:500]}") from exc

    if payload.get("is_error"):
        raise ShimError(f"Claude Code reported an error: {str(payload.get('result'))[:500]}")
    return payload


def text_stuecke(zeilen: Iterable[str], mit_denken: bool = False) -> Iterator[str]:
    """Aus stream-json-Zeilen nur die text_delta-Texte liefern.

    Alles andere (Signaturen, Metaereignisse, kaputte Zeilen) wird uebersprungen.
    ``result`` mit is_error wirft ShimError. Kam gar kein Delta, folgt als
    Rueckfall der Text aus ``result``, damit eine Antwort nie still leer bleibt.
    """

    geliefert = False
    neuer_turn = False
    for zeile in zeilen:
        try:
            obj = json.loads(zeile)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        art = obj.get("type")
        if art == "stream_event":
            event = obj.get("event")
            delta = event.get("delta") if isinstance(event, dict) else None
            if isinstance(event, dict) and event.get("type") == "message_start":
                # Neue Assistenten-Nachricht (nach Werkzeugaufruf): Text davor
                # sauber trennen, sonst klebt "Ich suche ..." an der Antwort.
                neuer_turn = geliefert
            elif (
                mit_denken
                and isinstance(event, dict)
                and event.get("type") == "content_block_delta"
                and isinstance(delta, dict)
                and delta.get("type") == "thinking_delta"
                and isinstance(delta.get("thinking"), str)
                and delta["thinking"]
            ):
                yield Denken(delta["thinking"])
            elif (
                isinstance(event, dict)
                and event.get("type") == "content_block_delta"
                and isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
                and delta["text"]
            ):
                if neuer_turn:
                    neuer_turn = False
                    yield "\n\n"
                geliefert = True
                yield delta["text"]
        elif art == "result":
            if obj.get("is_error"):
                raise ShimError(f"Claude Code reported an error: {str(obj.get('result'))[:500]}")
            if not geliefert and isinstance(obj.get("result"), str) and obj["result"]:
                geliefert = True
                yield obj["result"]


def stream_claude(
    *,
    system_prompt: str,
    transcript: str,
    model: str | None,
    timeout: float,
    response_format: Any = None,
    bilder_ordner: str | None = None,
    ohne_werkzeuge: bool = False,
    denken: bool = False,
) -> Iterator[str]:
    """Startet die CLI mit stream-json und liefert Text-Stuecke, sobald sie kommen."""

    argv, system_prompt_file, environment = _build_command(
        system_prompt=system_prompt,
        model=model,
        response_format=response_format,
        streaming=True,
        bilder_ordner=bilder_ordner,
        ohne_werkzeuge=ohne_werkzeuge,
        denken=denken,
    )
    fehler_datei = tempfile.TemporaryFile()
    proc: subprocess.Popen[str] | None = None
    timer: threading.Timer | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="claude-shim-") as workdir:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=fehler_datei,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=workdir,
                env=environment,
            )
            zeit_ueberschritten = threading.Event()

            def _abbruch() -> None:
                zeit_ueberschritten.set()
                proc.kill()

            timer = threading.Timer(timeout, _abbruch)
            timer.daemon = True
            timer.start()

            def _eingabe() -> None:
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(transcript)
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass

            threading.Thread(target=_eingabe, daemon=True).start()
            assert proc.stdout is not None
            yield from text_stuecke(proc.stdout, mit_denken=denken)
            rc = proc.wait()
            if zeit_ueberschritten.is_set():
                raise ShimError(f"Claude Code CLI timed out after {timeout}s")
            if rc != 0:
                fehler_datei.seek(0)
                detail = fehler_datei.read().decode("utf-8", "replace").strip()
                sys.stderr.write(f"cli rc={rc} output={detail[:4000]}\n")
                sys.stderr.flush()
                raise ShimError(f"Claude Code CLI exited {rc}: {detail[:500]}")
    finally:
        # Auch bei Abbruch des Verbrauchers (Client weg) darf keine CLI weiterlaufen.
        if timer:
            timer.cancel()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        fehler_datei.close()
        _unlink_quiet(system_prompt_file)


def stream_denkend(**kw: Any) -> Iterator[str]:
    """stream_claude mit Rueckfall: lehnt die CLI den (undokumentierten) Denk-Schalter ab, bevor
    etwas kam, einmal ohne ihn - vorher ein Denk-Stueck "(Denken nicht verfuegbar)"."""
    if not kw.get("denken"):
        yield from stream_claude(**kw)
        return
    geliefert = False
    try:
        for stueck in stream_claude(**kw):
            geliefert = True
            yield stueck
        return
    except ShimError as exc:
        if geliefert or "unknown option" not in str(exc).lower():
            raise
    yield Denken(DENKEN_NICHT_VERFUEGBAR)
    yield from stream_claude(**{**kw, "denken": False})


def to_completion(payload: dict[str, Any], model: str) -> dict[str, Any]:
    text = payload.get("result")
    if not isinstance(text, str):
        raise ShimError("Claude Code result did not contain text")

    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0) + int(
        usage.get("cache_read_input_tokens") or 0
    )
    completion_tokens = int(usage.get("output_tokens") or 0)

    stop_reason = str(payload.get("stop_reason") or "end_turn")
    finish_reason = "length" if stop_reason == "max_tokens" else "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeCodeOpenAIShim/1.0"
    timeout_seconds = 600.0

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers -------------------------------------------------------
    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": {"message": message, "type": "shim_error"}})

    def _send_stream(self, pieces: Iterator[str], model: str) -> None:
        """Echtes SSE: je Text-Stueck sofort ein chat.completion.chunk, dann [DONE].

        Ein CLI-Fehler waehrend des Laufs wird der letzte Chunk mit
        finish_reason "error" und der Meldung im delta.content.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        erster = True

        def senden(delta: dict[str, Any], finish: str | None) -> None:
            item = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(item)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            try:
                for text in pieces:
                    delta: dict[str, Any] = (
                        {"reasoning_content": str(text)} if isinstance(text, Denken) else {"content": text})
                    if erster:
                        delta["role"] = "assistant"
                        erster = False
                    senden(delta, None)
                senden({}, "stop")
            except ShimError as exc:
                senden({"content": str(exc)}, "error")
            except (BrokenPipeError, ConnectionError):
                raise
            except Exception as exc:  # noqa: BLE001 -- jeder Fehler muss als Chunk ankommen
                sys.stderr.write(f"stream failure: {exc!r}\n")
                sys.stderr.flush()
                senden({"content": f"unexpected shim failure ({type(exc).__name__})"}, "error")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionError):
            # Client weg: Generator schliessen, damit die CLI beendet wird.
            pass
        finally:
            close = getattr(pieces, "close", None)
            if close:
                close()

    def _send_stream_einzeln(self, completion: dict[str, Any]) -> None:
        """Wie das Original: die fertige Antwort als ein SSE-Chunk, dann stop und [DONE]."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        content = completion["choices"][0]["message"]["content"]
        chunk = {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": completion["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }
        final = dict(chunk)
        final["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        for item in (chunk, final):
            self.wfile.write(f"data: {json.dumps(item)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # -- routes --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/v1/models", "/models"):
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": model_id,
                            "object": "model",
                            "created": 0,
                            "owned_by": "anthropic",
                        }
                        for model_id in ADVERTISED_MODELS
                    ],
                },
            )
            return
        if path in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok"})
            return
        self._send_error(404, f"unknown path {path}")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_error(404, f"unknown path {path}")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error(400, f"invalid request body: {exc}")
            return

        erlaubt, grund = budget_pruefen()
        if not erlaubt:
            agent = os.environ.get("VIBEMIND_AGENT", "")
            self._send_json(429, {"error": {"message": f"budget: abgelehnt ({grund}) fuer {agent}", "type": "budget"}})
            return
        budget_start = time.monotonic()

        model = str(body.get("model") or DEFAULT_MODEL_ID)
        messages = body.get("messages") or []
        bilder_ordner: str | None = None
        try:
            if _hat_bildteile(messages):
                bilder_ordner = tempfile.mkdtemp(prefix="mshim-")
                messages, _ = bildteile_ablegen(messages, bilder_ordner)
            self._antworten(body, messages, model, bilder_ordner, budget_start)
        except ShimEingabeFehler as exc:
            self._send_error(400, str(exc))
        finally:
            if bilder_ordner:
                shutil.rmtree(bilder_ordner, ignore_errors=True)

    def _antworten(
        self, body: dict[str, Any], messages: list[dict[str, Any]], model: str,
        bilder_ordner: str | None, budget_start: float,
    ) -> None:
        system_prompt, transcript = render_messages(messages)
        ohne_werkzeuge = body.get("marketing_ohne_werkzeuge") is True

        roles = [str(m.get("role", "?")) for m in messages]
        tool_names = [
            str((t.get("function") or {}).get("name") or t.get("name") or "?")
            for t in (body.get("tools") or [])
            if isinstance(t, dict)
        ]
        if tool_names:
            sys.stderr.write("tools offered: %s\n" % ", ".join(tool_names))
        sys.stderr.write(
            "request: model=%s roles=%s system=%dch transcript=%dch stream=%s tools=%d response_format=%s\n"
            % (
                model,
                ",".join(roles),
                len(system_prompt),
                len(transcript),
                bool(body.get("stream")),
                len(body.get("tools") or []),
                body.get("response_format", {}).get("type", "none")
                if isinstance(body.get("response_format"), dict)
                else "none",
            )
        )
        sys.stderr.flush()

        # Diagnose-Tor (env-gated, Vorgabe: aus): jede Anfrage als Datei ablegen,
        # damit ein fehlschlagender Aufruf 1:1 nachgespielt werden kann. Ohne
        # SHIM_DUMP_DIR passiert nichts.
        dump_dir = os.environ.get("SHIM_DUMP_DIR", "").strip()
        if dump_dir:
            try:
                os.makedirs(dump_dir, exist_ok=True)
                dump_path = os.path.join(dump_dir, "request-%d.json" % int(time.time() * 1000))
                with open(dump_path, "w", encoding="utf-8") as dump:
                    json.dump(body, dump, ensure_ascii=False)
                sys.stderr.write("request dumped to %s\n" % dump_path)
            except Exception as exc:  # noqa: BLE001 -- Diagnose darf den Dienst nicht kippen
                sys.stderr.write("request dump failed: %s\n" % exc)

        # Echtes Streaming nur auf ausdruecklichen Wunsch (der Chat-Arbeiter sendet das Flag);
        # andere Aufrufer (z. B. openclaw mit stream:true) bekommen das Original-Verhalten.
        if body.get("stream") and body.get("marketing_stream") is True:
            self._send_stream(
                _gebucht(
                    stream_denkend(
                        system_prompt=system_prompt,
                        transcript=transcript,
                        model=model,
                        timeout=self.timeout_seconds,
                        response_format=body.get("response_format"),
                        bilder_ordner=bilder_ordner,
                        ohne_werkzeuge=ohne_werkzeuge,
                        denken=body.get("marketing_denken") is True,
                    ),
                    budget_start,
                ),
                model,
            )
            return

        try:
            payload = run_claude(
                system_prompt=system_prompt,
                transcript=transcript,
                model=model,
                timeout=self.timeout_seconds,
                response_format=body.get("response_format"),
                bilder_ordner=bilder_ordner,
                ohne_werkzeuge=ohne_werkzeuge,
            )
            completion = to_completion(payload, model)
        except ShimError as exc:
            budget_buchen("fehler", time.monotonic() - budget_start)
            self._send_error(502, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            budget_buchen("fehler", time.monotonic() - budget_start)
            self._send_error(500, f"unexpected shim failure: {exc}")
            return

        budget_buchen("ok", time.monotonic() - budget_start)
        if body.get("stream"):
            self._send_stream_einzeln(completion)
            return
        self._send_json(200, completion)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8114)
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="seconds to wait for one CLI turn (default: 600)",
    )
    args = parser.parse_args()

    Handler.timeout_seconds = args.timeout
    cli = resolve_cli()

    # The artifact root is pinned to whichever Captain checkout is in use. If
    # that moves, the tools would silently disappear and the planner would fall
    # back to inlining plans -- a failure that surfaces far from its cause. Say
    # so at startup instead.
    store_root = os.environ.get("CAPTAIN_ARTIFACT_ROOT", "").strip()
    if not store_root:
        sys.stderr.write(
            "warning: CAPTAIN_ARTIFACT_ROOT is unset - serving without artifact "
            "tools; a planner asked for artifact:// refs will not be able to "
            "produce them\n"
        )
    elif not os.path.isdir(store_root):
        sys.stderr.write(
            f"warning: CAPTAIN_ARTIFACT_ROOT does not exist: {store_root}\n"
            "         the artifact tools will fail; has the Captain checkout moved?\n"
        )
    else:
        sys.stderr.write(f"artifact tools enabled against {store_root}\n")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(
        f"claude-code-openai-shim listening on http://{args.host}:{args.port}/v1 "
        f"(cli: {cli})\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
