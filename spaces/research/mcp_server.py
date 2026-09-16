"""Versionierter MCP-Server fuer die Research-Bubble-Kopplung.

Startet Recherchelaeufe aus einer Bubble heraus und legt das Ergebnis in
derselben Bubble ab. Der Beleg fuer einen echten Lauf ist die Reportdatei
am diktierten Pfad plus selbst gezaehlte Quellen - nie der Selbstbericht
des Agenten.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

SERVER_NAME = "spaces-research"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15

RESEARCHER_AGENT_ID = "52a6d4df-6eb0-5c55-a200-b984514886ab"
OPENFANG_URL = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200")
ARTIFACT_DIR = pathlib.Path.home() / ".openfang" / "research-artifacts"

DEPTHS = ("quick", "thorough", "exhaustive")
OUTPUT_STYLES = ("brief", "detailed", "academic", "executive")
CITATION_STYLES = ("inline_url", "footnotes", "academic_apa", "numbered")


class ToolError(Exception):
    pass


def _load_brief_module():
    path = pathlib.Path(__file__).resolve().parent / "brief.py"
    spec = importlib.util.spec_from_file_location("spaces_research_brief", path)
    if spec is None or spec.loader is None:
        raise ToolError("configuration_error: brief.py missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief_mod = _load_brief_module()


TOOLS: list[dict[str, Any]] = [
    {
        "name": "research_start",
        "description": (
            "Baut aus Bubble-Inhalt und Brief einen Recherche-Auftrag. Ohne "
            "confirm wird nur die Vorschau zurueckgegeben, kein Lauf gestartet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1},
                "brief": {"type": "string", "minLength": 1},
                "depth": {"type": "string", "default": "thorough"},
                "output_style": {"type": "string", "default": "detailed"},
                "citation_style": {"type": "string", "default": "academic_apa"},
                "language": {"type": "string", "default": "german"},
                "confirm": {"type": "boolean", "default": False},
                "final_brief": {"type": "string"},
            },
            "required": ["bubble_id", "brief"],
            "additionalProperties": False,
        },
    },
    {
        "name": "research_status",
        "description": "Prueft einen laufenden Auftrag und verbucht ein fertiges Ergebnis.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "minLength": 1}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
]


def _config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = env or os.environ
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise ToolError(
            "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
        )
    if not url.startswith(("http://", "https://")):
        raise ToolError("configuration_error: SUPABASE_URL must use http or https")
    return url, key


def _request(method: str, path: str, *, params: Mapping[str, str] | None = None, body: Any = None) -> Any:
    url, key = _config()
    endpoint = f"{url}/rest/v1/{path.lstrip('/')}"
    if params:
        endpoint = f"{endpoint}?{urllib.parse.urlencode(params)}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ToolError(f"supabase_http_error: status={exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ToolError("supabase_unreachable") from exc
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("supabase_invalid_response") from exc


def _required_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"invalid_arguments: '{key}' must be a non-empty string")
    return value.strip()


def _choice(arguments: Mapping[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise ToolError(f"invalid_arguments: '{key}' must be one of {', '.join(allowed)}")
    return value


def _confirm_flag(arguments: Mapping[str, Any]) -> bool:
    value = arguments.get("confirm", False)
    if not isinstance(value, bool):
        raise ToolError("invalid_arguments: 'confirm' must be a boolean")
    return value


def _read_bubble(bubble_id: str) -> dict:
    rows = _request(
        "GET", "ideas",
        params={"select": "id,title,description", "id": f"eq.{bubble_id}", "limit": "1"},
    )
    if not isinstance(rows, list) or not rows:
        raise ToolError(f"not_found: bubble '{bubble_id}'")
    return rows[0]


def _read_nodes(bubble_id: str) -> list[dict]:
    rows = _request(
        "GET", "canvas_nodes",
        params={"select": "title,content", "linked_idea_id": f"eq.{bubble_id}", "limit": "100"},
    )
    return rows if isinstance(rows, list) else []


def _job_path(job_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{job_id}.md"


def _job_state_path(job_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{job_id}.job.json"


def _request_path(job_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{job_id}.request.json"


def _write_job_file(job_id: str, payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _job_state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _spawn_agent_call(job_id: str, brief_text: str) -> None:
    """Stoesst den Agentenlauf an, ohne auf ihn zu warten.

    Der Aufruf blockiert serverseitig ohne Timeout bis zum Ende der
    Agentenschleife - beim Rauchtest 80 s fuer eine triviale Frage. Der
    Abholweg ist deshalb die Reportdatei, nicht diese Antwort.

    Der Request-Body geht als Datei an das Kind, nicht als argv: Windows
    kappt eine Kommandozeile bei rund 32767 Zeichen, und ein langer Brief
    (Auftrag plus Bubble-Inhalt) kann das reissen - genau dann, wenn der
    Auftrag gross und teuer ist. Die Datei ist zugleich der dauerhafte
    Beleg dessen, was tatsaechlich gesendet wurde.

    Verzeichnis anlegen, Request-Datei schreiben, Log oeffnen und der
    Spawn selbst sind allesamt E/A - jeder Schritt kann mit OSError
    scheitern (Datentraeger voll, Rechte entzogen, sys.executable nicht
    auffindbar). Das darf nicht ungefangen aus dieser Funktion und damit
    aus dem lang laufenden stdio-Server herausfallen: es wird zu einem
    ToolError, mit der urspruenglichen Fehlermeldung darin sichtbar.
    Zusaetzlich landet der Grund - best effort - in
    research_<job_id>.spawn.log: _write_job_file laeuft im Aufrufer vor
    diesem Aufruf, der Job-Zustand kann also schon existieren, und ohne
    einen Eintrag hier saehe research_status (Task 5) keine Logdatei und
    meldete den Job fuer immer 'pending' statt 'failed'. Scheitert sogar
    das Schreiben dieser Logdatei (z.B. weil schon das Anlegen des
    Verzeichnisses gescheitert ist), bleibt der ToolError das einzige
    Signal - eine zweite Exception darf dabei nicht nach aussen dringen.
    """
    base = (os.environ.get("OPENFANG_URL") or "http://127.0.0.1:4200").rstrip("/")
    url = f"{base}/api/agents/{RESEARCHER_AGENT_ID}/message"
    payload = json.dumps({"message": brief_text}, ensure_ascii=False)

    script = (
        "import sys,urllib.request\n"
        "with open(sys.argv[2],'r',encoding='utf-8') as fh:\n"
        "    body=fh.read()\n"
        "req=urllib.request.Request(sys.argv[1],"
        "data=body.encode('utf-8'),method='POST',"
        "headers={'Content-Type':'application/json'})\n"
        "urllib.request.urlopen(req,timeout=5400).read()\n"
    )

    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        request_path = _request_path(job_id)
        request_path.write_text(payload, encoding="utf-8")

        log = open(ARTIFACT_DIR / f"research_{job_id}.spawn.log", "wb")
        try:
            subprocess.Popen(
                [sys.executable, "-c", script, url, str(request_path)],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            )
        finally:
            # Das Kind erbt sein eigenes Handle; das Elternhandle hier zu
            # halten waere ein Leck ueber die Lebensdauer des lang
            # laufenden Servers.
            log.close()
    except OSError as exc:
        try:
            (ARTIFACT_DIR / f"research_{job_id}.spawn.log").write_text(
                f"spawn_failed: {exc!r}\n\n{traceback.format_exc()}",
                encoding="utf-8",
            )
        except OSError:
            pass
        raise ToolError(f"spawn_failed: {exc}") from exc


# compose_brief (brief.py) opens the bubble-background section of the
# assembled brief with exactly this literal. Coupled to that template on
# purpose: if compose_brief's wording ever changes, this constant has to
# change with it, and the coupling is named here rather than buried as a
# string literal in _context_disclosure below.
_BUBBLE_CONTEXT_MARKER = '[Hintergrund aus Bubble'


def _context_disclosure(brief_text: str) -> tuple[bool, str | None]:
    """Derives whether the run actually had the bubble's internal context.

    Never asserts it. research_start's final_brief lets a user replace the
    assembled instruction verbatim - including deleting the bubble-background
    section compose_brief emits - so only the brief that was actually sent
    can answer this, not the fact that a run was started from a bubble at
    all. internal_context_used is the one column whose purpose is to catch
    exactly that omission (E12); writing True unconditionally would defeat
    it.
    """
    if _BUBBLE_CONTEXT_MARKER in (brief_text or ""):
        return True, None
    return False, (
        "Ohne Bubble-Hintergrund gelaufen: der tatsaechlich gesendete Brief "
        "enthielt den von compose_brief erzeugten Abschnitt "
        f"'{_BUBBLE_CONTEXT_MARKER}...' nicht mehr - vermutlich durch einen "
        "bearbeiteten final_brief entfernt."
    )


def _find_existing_artifact_ref(job_id: str) -> str | None:
    """Looks up a report artifact already written for this job, if any.

    research_report_artifacts has UNIQUE(job_id, name), and name is
    deterministic (research_<job_id>.md). Without this check, a second
    research_status call after a partial failure (artifact row written,
    canvas_nodes insert then failing) would retry the artifact insert and
    hit that constraint forever - the job would be permanently stuck with
    orphaned evidence, unrecoverable by any amount of retrying. Checking
    first turns that dead end into ordinary retry.
    """
    rows = _request(
        "GET", "research_report_artifacts",
        params={"select": "artifact_ref", "job_id": f"eq.{job_id}", "limit": "1"},
    )
    if isinstance(rows, list) and rows:
        return rows[0].get("artifact_ref")
    return None


def _find_existing_node_id(bubble_id: str, artifact_ref: str) -> str | None:
    """Looks up the summary node already written for this artifact, if any.

    Keyed on metadata.artifact_ref, deliberately not on title: a human can
    rename a node in the bubble after the fact, and canvas_nodes generates
    its own id (gen_random_uuid()) that this caller never chooses - so
    artifact_ref, already carried in metadata on write, is the only stable
    handle a retry can look the node up by.
    """
    rows = _request(
        "GET", "canvas_nodes",
        params={
            "select": "id,metadata",
            "linked_idea_id": f"eq.{bubble_id}",
            "node_type": "eq.research",
            "limit": "100",
        },
    )
    if not isinstance(rows, list):
        return None
    for row in rows:
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata.get("artifact_ref") == artifact_ref:
            return row.get("id")
    return None


def _persist_result(job_id: str, state: Mapping[str, Any], text: str, citations: int) -> dict:
    bubble_id = state["bubble_id"]
    # Zeitstempel (nicht nur Datum) im Titel, damit mehrere Laeufe an
    # derselben Bubble unterscheidbar bleiben - auch zwei Laeufe am selben
    # Kalendertag. Datum allein waere hier eine falsche Behauptung: ein
    # Recherche-Lauf dauert lange genug, dass zwei echte Laeufe praktisch
    # nie in derselben Sekunde abschliessen, zwei am selben Tag aber
    # durchaus vorkommen. Ein Lauf je Knoten ist der Grund, warum der Knoten
    # write-once ist und der Volltext hier ueberhaupt stehen darf.
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    title = f"Research: {state.get('bubble_title') or bubble_id} {stamp}"

    # Idempotent by job_id: a prior call may already have written the
    # artifact row (see _find_existing_artifact_ref's docstring for why a
    # naive retry cannot simply insert again).
    artifact_ref = _find_existing_artifact_ref(job_id)
    if artifact_ref is None:
        artifact_ref = brief_mod.new_artifact_ref()
        internal_context_used, context_disclosure = _context_disclosure(state.get("brief") or "")
        _request("POST", "research_report_artifacts", body={
            "artifact_ref": artifact_ref,
            "job_id": job_id,
            "subject_type": "bubble",
            "subject_ref": bubble_id,
            "bubble_id": bubble_id,
            "artifact_type": "research_report",
            "name": f"research_{job_id}.md",
            "rel_path": state.get("report_path"),
            "format": "markdown",
            "content_text": text,
            "depth": state.get("depth"),
            "output_style": state.get("output_style"),
            "citation_count": citations,
            "internal_context_used": internal_context_used,
            "context_disclosure": context_disclosure,
        })

    # Idempotent by artifact_ref: same reasoning for the node half of the
    # write. No explicit id in the insert body - canvas_nodes.id defaults to
    # gen_random_uuid() at the database, which is the identity source of
    # truth this lookup relies on, rather than a client-picked 8-hex-char
    # slice of artifact_ref (roughly 40 bits) that could collide.
    node_id = _find_existing_node_id(bubble_id, artifact_ref)
    if node_id is None:
        node = _request("POST", "canvas_nodes", body={
            "node_type": "research",
            "title": title[:120],
            # Volltext, nicht nur eine Kurzfassung: der Knoten ist der Weg,
            # auf dem der Report in den Vault gelangt (render_canvas_note
            # schreibt eine Datei je Knoten, mit User-Fence fuer
            # Annotationen). Tragbar, weil jeder Lauf seinen eigenen Knoten
            # bekommt und dieser danach nie wieder angefasst wird.
            "content": text,
            "x": 0, "y": 0,
            "linked_idea_id": bubble_id,
            "metadata": {
                "width": 320.0, "height": 220.0,
                "artifact_ref": artifact_ref,
                "citation_count": citations,
            },
        })
        node_id = node[0]["id"] if isinstance(node, list) and node else None

    return {
        "status": "done",
        "job_id": job_id,
        "artifact_ref": artifact_ref,
        "citation_count": citations,
        "bubble_id": bubble_id,
        "node_id": node_id,
    }


def call_tool(name: str, arguments: Mapping[str, Any]) -> dict:
    if name == "research_start":
        bubble_id = _required_string(arguments, "bubble_id")
        user_brief = _required_string(arguments, "brief")
        depth = _choice(arguments, "depth", DEPTHS, "thorough")
        output_style = _choice(arguments, "output_style", OUTPUT_STYLES, "detailed")
        citation_style = _choice(arguments, "citation_style", CITATION_STYLES, "academic_apa")
        language = arguments.get("language", "german")
        if not isinstance(language, str) or not language.strip():
            raise ToolError("invalid_arguments: 'language' must be a non-empty string")
        confirm = _confirm_flag(arguments)

        bubble = _read_bubble(bubble_id)
        nodes = _read_nodes(bubble_id)
        job_id = brief_mod.new_job_id()
        brief_text = brief_mod.compose_brief(
            bubble_title=bubble.get("title") or bubble_id,
            bubble_nodes=nodes,
            user_brief=user_brief,
            output_path=str(_job_path(job_id)),
            depth=depth,
            output_style=output_style,
            citation_style=citation_style,
            language=language.strip(),
        )

        if not confirm:
            return {
                "status": "preview",
                "brief": brief_text,
                "note": (
                    "Kein Lauf gestartet. Brief pruefen, bei Bedarf bearbeiten, "
                    "dann mit confirm=true erneut aufrufen."
                ),
            }

        final = arguments.get("final_brief")
        if final is not None:
            if not isinstance(final, str) or not final.strip():
                raise ToolError("invalid_arguments: 'final_brief' must be a non-empty string")
            brief_text = final
            # Der bestaetigte Brief traegt den Pfad, gegen den der Agent
            # schreibt. Die job_id folgt IHM, nicht der oben erzeugten -
            # sonst prueft research_status einen Pfad, in den nie jemand
            # schreibt. Kein stiller Rueckfall: fehlt der Pfad, ist das ein
            # Fehler vor dem Start.
            from_brief = brief_mod.job_id_from_brief(brief_text)
            if from_brief is None:
                raise ToolError(
                    "invalid_arguments: 'final_brief' enthaelt keinen Ausgabepfad "
                    "der Form research_<job_id>.md - bearbeite die Vorschau, "
                    "ohne die Pfadzeile zu entfernen"
                )
            job_id = from_brief

        _write_job_file(job_id, {
            "job_id": job_id,
            "bubble_id": bubble_id,
            "bubble_title": bubble.get("title") or bubble_id,
            "depth": depth,
            "output_style": output_style,
            "citation_style": citation_style,
            "language": language.strip(),
            "brief": brief_text,
            "report_path": str(_job_path(job_id)),
            "started_at": time.time(),
        })
        _spawn_agent_call(job_id, brief_text)
        return {
            "status": "started",
            "job_id": job_id,
            "report_path": str(_job_path(job_id)),
            "note": "Mit research_status(job_id) den Fortschritt pruefen.",
        }

    if name == "research_status":
        job_id = _required_string(arguments, "job_id")
        state_path = _job_state_path(job_id)
        if not state_path.is_file():
            raise ToolError(f"not_found: job '{job_id}'")
        state = json.loads(state_path.read_text(encoding="utf-8"))

        report_path = _job_path(job_id)
        if not report_path.is_file():
            # Der Hintergrundprozess schreibt nur dann in spawn.log, wenn er
            # gescheitert ist (z.B. OpenFang nicht erreichbar). Ohne diese
            # Pruefung bliebe der Auftrag fuer immer 'pending'.
            spawn_log = ARTIFACT_DIR / f"research_{job_id}.spawn.log"
            if spawn_log.is_file() and spawn_log.stat().st_size > 0:
                detail = spawn_log.read_text(encoding="utf-8", errors="replace")
                return {
                    "status": "failed",
                    "job_id": job_id,
                    "error": "agent call failed before writing a report",
                    "detail": detail[-800:],
                }
            return {
                "status": "pending",
                "job_id": job_id,
                "waited_seconds": round(time.time() - float(state.get("started_at") or 0)),
            }

        text = report_path.read_text(encoding="utf-8", errors="replace")
        citations = brief_mod.count_citations(text)
        if citations < 1:
            return {
                "status": "failed",
                "job_id": job_id,
                "error": "no citation found in report - fail-closed",
            }
        return _persist_result(job_id, state, text, citations)

    raise ToolError(f"unknown_tool: {name}")


def handle_message(message: Mapping[str, Any]) -> dict | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {})
        except ToolError as exc:
            return {
                "jsonrpc": "2.0", "id": message_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
        return {
            "jsonrpc": "2.0", "id": message_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                ]
            },
        }
    if message_id is None:
        return None
    return {
        "jsonrpc": "2.0", "id": message_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = handle_message(message)
        except Exception as exc:  # noqa: BLE001 - letztes Fangnetz des Servers
            # handle_message faengt ToolError bereits selbst ab. Dieses
            # Fangnetz ist fuer alles andere: ein unerwarteter Bug oder ein
            # E/A-Fehler, der (noch) kein ToolError ist. Ohne dieses
            # try/except reisst eine einzelne fehlerhafte Anfrage den
            # gesamten lang laufenden stdio-Server fuer alle kuenftigen
            # Aufrufer mit, statt nur diese eine Anfrage fehlschlagen zu
            # lassen.
            message_id = message.get("id") if isinstance(message, Mapping) else None
            response = {
                "jsonrpc": "2.0", "id": message_id,
                "error": {"code": -32603, "message": f"internal_error: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
