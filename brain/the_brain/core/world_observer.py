"""World Observer — ground-truth post-condition checks (Baustein D.1).

The validation that exists today (capability_validator) only inspects what a
tool *claims* it returned. This module checks what the *world* actually looks
like after an action: is the process running, is the port open, does the file
exist, does the HTTP endpoint respond. That observed signal is the honest
ground truth behind "action_verified".

Design rules:
  - **Fail-safe**: an observer error NEVER counts as an action failure. If we
    cannot observe, we return UNVERIFIED (not FAILURE) — the action's own
    claimed outcome stands, we just couldn't confirm it.
  - **Flag-gated**: does nothing unless GROUND_TRUTH_ENABLED. Default OFF, so
    the live Brain is unchanged.
  - **Declarative**: a capability declares its post-condition in YAML, e.g.
        postcondition: { check: process_running, name: chrome }
        postcondition: { check: port_open, port: 9223 }
        postcondition: { check: file_exists, path: /tmp/out.json }
        postcondition: { check: http_ok, url: http://127.0.0.1:4200/health }
  - **No hard deps**: psutil/requests imported lazily; absence → UNVERIFIED.

Returns a `Verification` with verdict in {VERIFIED, UNVERIFIED, REFUTED}:
  VERIFIED  — world confirms the action took effect
  REFUTED   — world contradicts the claim (e.g. process not found)
  UNVERIFIED — could not observe (no check declared, observer error, missing dep)
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


GROUND_TRUTH_ENABLED = _flag("GROUND_TRUTH_ENABLED")
# Network/probe timeout (seconds) — kept short so we never block a hot path.
OBSERVE_TIMEOUT = float(os.environ.get("GROUND_TRUTH_TIMEOUT", "2.0"))


# ── Verdict ────────────────────────────────────────────────────────────

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
REFUTED = "REFUTED"


@dataclass
class Verification:
    """Result of a ground-truth observation."""
    verdict: str = UNVERIFIED          # VERIFIED | UNVERIFIED | REFUTED
    check: str = ""                    # which check ran
    signal: Dict[str, Any] = field(default_factory=dict)  # observed facts
    reason: str = ""
    latency_ms: float = 0.0

    @property
    def verified_ok(self) -> Optional[bool]:
        """True/False if conclusive, None if unverified."""
        if self.verdict == VERIFIED:
            return True
        if self.verdict == REFUTED:
            return False
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "check": self.check,
            "signal": self.signal,
            "reason": self.reason,
            "latency_ms": round(self.latency_ms, 2),
        }


# ── Individual checks (each returns (ok: Optional[bool], signal, reason)) ──

def _check_process_running(spec: Dict[str, Any]):
    name = (spec.get("name") or "").lower()
    if not name:
        return None, {}, "no process name given"
    try:
        import psutil  # lazy
    except Exception:
        return None, {}, "psutil not available"
    found = []
    for p in psutil.process_iter(["name"]):
        try:
            pn = (p.info.get("name") or "").lower()
            if name in pn:
                found.append(pn)
        except Exception:
            continue
    if found:
        return True, {"process": name, "matches": found[:5]}, f"process running: {found[0]}"
    return False, {"process": name}, "process not found"


def _check_port_open(spec: Dict[str, Any]):
    port = spec.get("port")
    host = spec.get("host", "127.0.0.1")
    if not port:
        return None, {}, "no port given"
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(OBSERVE_TIMEOUT)
    try:
        rc = s.connect_ex((host, int(port)))
        if rc == 0:
            return True, {"host": host, "port": port}, f"port {port} open"
        return False, {"host": host, "port": port, "rc": rc}, f"port {port} closed (rc={rc})"
    except Exception as e:
        return None, {"host": host, "port": port}, f"port probe error: {e}"
    finally:
        try:
            s.close()
        except Exception:
            pass


def _map_host_path(path: str) -> str:
    """F4 (2026-08-30): translate HOST path prefixes to container mounts.

    Coding agents execute on the Windows host while this validator runs in
    the brain-core container — without a mapping every successful coding op
    is refuted ("file missing") and the reward signal is poisoned (the
    learner would train AWAY from working coding routes).

    GROUND_TRUTH_PATH_MAP holds `;`-separated `HOSTPREFIX=>CONTAINERPREFIX`
    pairs, e.g. `C:\\Users\\User=>/host_users` (pair with a read-only bind
    mount in the stack file). Prefix matching is case-insensitive and treats
    `\\` and `/` alike; the first matching pair wins; malformed entries are
    skipped. Unset/empty map or no match -> path returned unchanged, which
    is the exact legacy (native-host) behaviour.
    """
    raw_map = os.environ.get("GROUND_TRUTH_PATH_MAP", "").strip()
    if not raw_map or not path:
        return path
    # Collapse separator runs: the {result_path} extractor can deliver
    # JSON-escaped paths with doubled backslashes (seen live 2026-08-30).
    # UNC paths would collapse too — out of scope for this mapping; an
    # unmatched path is returned unchanged anyway.
    import re as _re
    normalized = _re.sub(r"[\\/]+", "/", path)
    folded = normalized.casefold()
    for entry in raw_map.split(";"):
        if "=>" not in entry:
            continue
        host_prefix, _, target_prefix = entry.partition("=>")
        host_prefix = _re.sub(r"[\\/]+", "/", host_prefix.strip()).rstrip("/")
        target_prefix = target_prefix.strip().rstrip("/")
        if not host_prefix or not target_prefix:
            continue
        prefix_folded = host_prefix.casefold()
        if folded == prefix_folded:
            return target_prefix
        if folded.startswith(prefix_folded + "/"):
            return target_prefix + normalized[len(host_prefix):]
    return path


def _check_file_exists(spec: Dict[str, Any]):
    path = spec.get("path")
    if not path:
        return None, {}, "no path given"
    probe_path = _map_host_path(path)
    signal: Dict[str, Any] = {"path": path}
    if probe_path != path:
        signal["mapped_path"] = probe_path
    try:
        exists = os.path.exists(probe_path)
        if exists:
            sz = os.path.getsize(probe_path) if os.path.isfile(probe_path) else None
            signal["size"] = sz
            return True, signal, "file exists"
        return False, signal, "file missing"
    except Exception as e:
        return None, signal, f"stat error: {e}"


def _check_http_ok(spec: Dict[str, Any]):
    url = spec.get("url")
    if not url:
        env_name = (spec.get("url_env") or "").strip()
        if env_name:
            url = (os.environ.get(env_name) or "").strip()
            if not url:
                return None, {}, f"{env_name} is required"
            path = (spec.get("path") or "").strip()
            if path:
                url = url.rstrip("/") + "/" + path.lstrip("/")
    if not url:
        return None, {}, "no url given"
    expect_lt = int(spec.get("expect_status_lt", 400))
    try:
        import requests  # lazy
    except Exception:
        return None, {}, "requests not available"
    try:
        method = str(spec.get("method", "GET")).upper()
        if method == "HEAD":
            r = requests.head(url, timeout=OBSERVE_TIMEOUT, allow_redirects=False)
        elif method == "GET":
            r = requests.get(url, timeout=OBSERVE_TIMEOUT)
        else:
            return None, {"url": url}, f"unsupported http method {method}"
        ok = r.status_code < expect_lt
        return ok, {"url": url, "status_code": r.status_code, "method": method}, f"http {r.status_code}"
    except Exception as e:
        return None, {"url": url}, f"http probe error: {e}"


def _supabase_zugang():
    """(base, headers, grund) fuer eine unabhaengige Supabase-Nachfrage.

    Keine Vorgabewerte: eine tote Vorgabeadresse oder ein Platzhalter-Schluessel
    liessen jede Nachfrage still ins Leere laufen (bis 2026-09-23 der Fall).
    Der Schluessel kommt ueber config.get_secret, das SUPABASE_ANON_KEY_FILE,
    /run/secrets und die Umgebung der Reihe nach liest. Nur `apikey`, ohne
    Bearer: PostgREST behandelt die Anfrage dann als anon, so wie die Clients
    im Betrieb.
    """
    base = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    if not base:
        return None, {}, "SUPABASE_URL fehlt - keine Nachfrage moeglich"
    try:
        from core.config import get_secret
        key = get_secret("SUPABASE_ANON_KEY")
    except Exception:
        key = os.environ.get("SUPABASE_ANON_KEY")
    key = (key or "").strip()
    if not key:
        return None, {}, "kein Supabase-Schluessel (SUPABASE_ANON_KEY[_FILE])"
    return base, {"apikey": key}, ""


def _check_supabase_row(spec: Dict[str, Any]):
    """Ground-truth for supabase ops via an INDEPENDENT re-query (Baustein D.1).

    Instead of trusting the op's self-reported result, ask supabase directly
    whether the expected end-state holds. spec:
        {check: supabase_row, table: ideas, match: "id=eq.<x>", expect: present}
        {check: supabase_row, table: ideas, match: "title=eq.<x>", expect: absent}
    `match` is a PostgREST filter; `expect` ∈ {present, absent}. Returns
    (ok|None, signal, reason). Observer/transport errors → None (UNVERIFIED,
    fail-safe — a probe failure must never fail the action itself)."""
    table = (spec.get("table") or "ideas").strip()
    match = (spec.get("match") or "").strip()
    expect = (spec.get("expect") or "present").lower()
    if not match:
        return None, {}, "no match filter (nothing to re-query)"
    base, hdr, grund = _supabase_zugang()
    if base is None:
        return None, {}, grund
    try:
        import requests  # lazy
    except Exception:
        return None, {}, "requests not available"
    try:
        r = requests.get(
            f"{base}/rest/v1/{table}?{match}&select=id&limit=1",
            headers=hdr, timeout=OBSERVE_TIMEOUT,
        )
        if r.status_code >= 400:
            return None, {"status_code": r.status_code}, f"supabase re-query {r.status_code}"
        rows = r.json() if r.content else []
        present = isinstance(rows, list) and len(rows) > 0
        ok = present if expect == "present" else (not present)
        return ok, {
            "table": table, "match": match, "expect": expect,
            "rows_found": len(rows) if isinstance(rows, list) else 0,
        }, f"supabase: {'row present' if present else 'row absent'} (expected {expect})"
    except Exception as e:  # fail-safe
        return None, {"match": match}, f"supabase probe error: {e}"


def _check_supabase_edge(spec: Dict[str, Any]):
    """Ground-truth for idea connect/disconnect — resolve the two node TITLES to ids,
    then check whether an edge exists between them (bidirectional). spec:
        {check: supabase_edge, title_a: X, title_b: Y, expect: present|absent}
    A missing endpoint / transport error → None (UNVERIFIED, fail-safe)."""
    title_a = (spec.get("title_a") or "").strip()
    title_b = (spec.get("title_b") or "").strip()
    expect = (spec.get("expect") or "present").lower()
    if not title_a or not title_b:
        return None, {}, "missing edge endpoints (cannot verify)"
    base, hdr, grund = _supabase_zugang()
    if base is None:
        return None, {}, grund
    try:
        import requests  # lazy
        import urllib.parse as _u
    except Exception:
        return None, {}, "requests not available"

    def _node_id(title):
        r = requests.get(
            f"{base}/rest/v1/canvas_nodes?title=eq.{_u.quote(title)}&select=id&limit=1",
            headers=hdr, timeout=OBSERVE_TIMEOUT)
        rows = r.json() if (r.status_code < 400 and r.content) else []
        return rows[0]["id"] if rows else None

    try:
        a, b = _node_id(title_a), _node_id(title_b)
        if not a or not b:
            return None, {"id_a": a, "id_b": b}, "edge endpoint node not found (cannot verify)"
        flt = (f"or=(and(from_node_id.eq.{a},to_node_id.eq.{b}),"
               f"and(from_node_id.eq.{b},to_node_id.eq.{a}))")
        r = requests.get(f"{base}/rest/v1/canvas_edges?{flt}&select=id&limit=1",
                         headers=hdr, timeout=OBSERVE_TIMEOUT)
        if r.status_code >= 400:
            return None, {"status_code": r.status_code}, f"edge re-query {r.status_code}"
        rows = r.json() if r.content else []
        present = isinstance(rows, list) and len(rows) > 0
        ok = present if expect == "present" else (not present)
        return ok, {
            "title_a": title_a, "title_b": title_b, "expect": expect,
            "edges_found": len(rows) if isinstance(rows, list) else 0,
        }, f"supabase: edge {'present' if present else 'absent'} (expected {expect})"
    except Exception as e:  # fail-safe
        return None, {"a": title_a, "b": title_b}, f"edge probe error: {e}"


def _check_supabase_edge_ids(spec: Dict[str, Any]):
    """Independently read one exact durable edge with explicit service config."""
    from core.idea_connect_contract import (
        is_canonical_durable_id,
        is_canonical_edge_type,
    )

    required = ("edge_id", "from_id", "to_id", "edge_type")
    values = {name: spec.get(name) for name in required}
    if (not all(is_canonical_durable_id(values[name]) for name in required[:3])
            or not is_canonical_edge_type(values["edge_type"])
            or values["from_id"] == values["to_id"]):
        return None, {"status": "unverified"}, "invalid durable edge receipt"
    if str(spec.get("expect", "present")).lower() != "present":
        return None, {"status": "unverified"}, "invalid durable edge expectation"
    raw_base = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not raw_base.strip():
        return None, {"status": "unverified"}, "SUPABASE_URL is required"
    try:
        parsed = urlsplit(raw_base)
        valid_base = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
        _ = parsed.port
    except (TypeError, ValueError):
        valid_base = False
    if not valid_base:
        return None, {"status": "unverified"}, "SUPABASE_URL is invalid"
    base = raw_base.rstrip("/")
    if not key:
        return None, {"status": "unverified"}, "SUPABASE_SERVICE_ROLE_KEY is required"
    try:
        import requests
        response = requests.get(
            f"{base}/rest/v1/canvas_edges",
            params={
                "select": "id,from_node_id,to_node_id,edge_type",
                "id": f"eq.{values['edge_id']}",
                "limit": "2",
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=OBSERVE_TIMEOUT,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400 or response.status_code >= 400:
            return None, {"status": "unverified"}, "durable edge read-back unavailable"
        rows = response.json()
        if not isinstance(rows, list):
            return None, {"status": "unverified"}, "durable edge read-back malformed"
    except Exception:
        return None, {"status": "unverified"}, "durable edge read-back unavailable"
    if not rows:
        return False, {"edge_id": values["edge_id"], "rows_found": 0}, "durable edge not found"
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return None, {"status": "unverified"}, "durable edge read-back malformed"
    row = rows[0]
    expected = {
        "id": values["edge_id"],
        "from_node_id": values["from_id"],
        "to_node_id": values["to_id"],
        "edge_type": values["edge_type"],
    }
    if any(row.get(field) != value for field, value in expected.items()):
        return False, {"edge_id": expected["id"], "rows_found": 1}, "durable edge receipt refuted"
    return True, {"edge_id": expected["id"], "rows_found": 1}, "durable edge verified"


def _check_supabase_node_in_bubble(spec: Dict[str, Any]):
    """Ground-truth for idea_move — confirm the node is now under the target bubble.
    Resolve the bubble TITLE to its id (ideas row), then check canvas_nodes for the
    node under that bubble_id. spec:
        {check: supabase_node_in_bubble, node_title: X, bubble_title: Y, expect: present}
    Unresolved bubble / transport error → None (UNVERIFIED, fail-safe)."""
    node_title = (spec.get("node_title") or "").strip()
    bubble_title = (spec.get("bubble_title") or "").strip()
    expect = (spec.get("expect") or "present").lower()
    if not node_title or not bubble_title:
        return None, {}, "missing node/bubble (cannot verify)"
    base, hdr, grund = _supabase_zugang()
    if base is None:
        return None, {}, grund
    try:
        import requests  # lazy
        import urllib.parse as _u
    except Exception:
        return None, {}, "requests not available"
    try:
        r = requests.get(
            f"{base}/rest/v1/ideas?title=eq.{_u.quote(bubble_title)}&select=id&limit=1",
            headers=hdr, timeout=OBSERVE_TIMEOUT)
        brows = r.json() if (r.status_code < 400 and r.content) else []
        if not brows:
            return None, {"bubble": bubble_title}, "target bubble not found (cannot verify)"
        bid = brows[0]["id"]
        # canvas_nodes link to their bubble via linked_idea_id (the bubble is an
        # ideas row); confirmed against the live schema 2026-06-24.
        r2 = requests.get(
            f"{base}/rest/v1/canvas_nodes?title=eq.{_u.quote(node_title)}"
            f"&linked_idea_id=eq.{bid}&select=id&limit=1",
            headers=hdr, timeout=OBSERVE_TIMEOUT)
        if r2.status_code >= 400:
            return None, {"status_code": r2.status_code}, f"node re-query {r2.status_code}"
        rows = r2.json() if r2.content else []
        present = isinstance(rows, list) and len(rows) > 0
        ok = present if expect == "present" else (not present)
        return ok, {
            "node": node_title, "bubble": bubble_title, "expect": expect,
            "found": len(rows) if isinstance(rows, list) else 0,
        }, f"supabase: node {'in' if present else 'not in'} bubble (expected {expect})"
    except Exception as e:  # fail-safe
        return None, {"node": node_title, "bubble": bubble_title}, f"node-in-bubble probe error: {e}"


def _check_supabase_bubble_node_count(spec: Dict[str, Any]):
    """Nachpruefung einer Zaehlung (idea_count): canvas_nodes einer Bubble auf
    zweitem Weg zaehlen. spec:
        {check: supabase_bubble_node_count, bubble_title: X, expect_count: "12"}
    Die Zahl kommt aus dem Ergebnistext der Operation ({result_count}). Ist sie
    keine ganze Zahl, wird nicht nachgefragt (UNVERIFIED)."""
    bubble_title = (spec.get("bubble_title") or "").strip()
    try:
        behauptet = int(str(spec.get("expect_count", "")).strip())
    except ValueError:
        return None, {}, "keine behauptete Zahl (nichts nachzuzaehlen)"
    if not bubble_title:
        return None, {}, "keine Bubble angegeben"
    base, hdr, grund = _supabase_zugang()
    if base is None:
        return None, {}, grund
    try:
        import requests  # lazy
        import urllib.parse as _u
    except Exception:
        return None, {}, "requests not available"
    try:
        r = requests.get(
            f"{base}/rest/v1/ideas?title=eq.{_u.quote(bubble_title)}&select=id&limit=1",
            headers=hdr, timeout=OBSERVE_TIMEOUT)
        brows = r.json() if (r.status_code < 400 and r.content) else []
        if not brows:
            return None, {"bubble": bubble_title}, "Bubble nicht gefunden (nicht pruefbar)"
        bid = brows[0]["id"]
        r2 = requests.get(
            f"{base}/rest/v1/canvas_nodes?linked_idea_id=eq.{bid}&select=id&limit=1",
            headers={**hdr, "Prefer": "count=exact"}, timeout=OBSERVE_TIMEOUT)
        if r2.status_code >= 400:
            return None, {"status_code": r2.status_code}, f"Zaehl-Nachfrage {r2.status_code}"
        cr = (r2.headers or {}).get("Content-Range", "")
        if "/" not in cr or not cr.rsplit("/", 1)[1].isdigit():
            return None, {"content_range": cr}, "keine Gesamtzahl in Content-Range"
        gezaehlt = int(cr.rsplit("/", 1)[1])
        ok = gezaehlt == behauptet
        return ok, {"bubble": bubble_title, "behauptet": behauptet, "gezaehlt": gezaehlt}, (
            f"supabase: {gezaehlt} Knoten gezaehlt, {behauptet} behauptet")
    except Exception as e:  # fail-safe
        return None, {"bubble": bubble_title}, f"Zaehl-Probe Fehler: {e}"


_SQLITE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_sqlite_row(spec: Dict[str, Any]):
    """Ground-truth for ops that persist into a local SQLite store.

    The Supabase checks cannot serve every space: `schedule` commits into
    `scheduled_tasks` in a SQLite file, so its own read-back (the repository
    re-reads after writing) stays invisible from the capability layer. This
    check asks the file directly, which is the whole difference between a
    verified write and a self-report. spec:

        {check: sqlite_row, path_env: SCHEDULE_DB_PATH, path: <fallback>,
         table: scheduled_tasks, match: "id={result_id}",
         expect_column: status, expect_value: cancelled, expect: present}

    `match` is a single `column=value` pair, split on the FIRST `=`. The
    optional `expect_column`/`expect_value` pair narrows the row further -
    that is how "cancel really left status=cancelled" gets asserted instead
    of merely "the row is still there".

    Table and column names must match a strict identifier pattern, and
    values are ALWAYS bound as query parameters, never interpolated: the
    value reaches us from a regex over the op's own result text, so it is
    influenced from outside and must never be able to become SQL.

    A missing file, an unknown table or any error yields None (UNVERIFIED,
    fail-safe) - a probe that cannot look must never claim the act failed.
    """
    table = str(spec.get("table") or "").strip()
    match = str(spec.get("match") or "").strip()
    expect = (spec.get("expect") or "present").lower()
    if not _SQLITE_IDENTIFIER.match(table):
        return None, {"table": table}, "invalid or missing table name"
    if "=" not in match:
        return None, {"match": match}, "match must be column=value"
    column, _, value = match.partition("=")
    column, value = column.strip(), value.strip()
    if not _SQLITE_IDENTIFIER.match(column):
        return None, {"match": match}, "invalid match column"
    if not value:
        return None, {"match": match}, "empty match value (nothing to re-query)"

    where = [(column, value)]
    expect_column = str(spec.get("expect_column") or "").strip()
    if expect_column:
        if not _SQLITE_IDENTIFIER.match(expect_column):
            return None, {"expect_column": expect_column}, "invalid expect column"
        expect_value = spec.get("expect_value")
        where.append((expect_column, str(expect_value if expect_value is not None else "")))

    path = os.environ.get(str(spec.get("path_env") or "")) or spec.get("path")
    if not path:
        return None, {}, "no sqlite path given"
    probe_path = _map_host_path(str(path))
    signal: Dict[str, Any] = {"table": table, "match": match, "expect": expect,
                              "path": probe_path}
    if expect_column:
        signal["expect_column"] = expect_column
    if not os.path.isfile(probe_path):
        return None, signal, "sqlite file not found (cannot verify)"

    try:
        import sqlite3  # lazy: the probe must cost nothing when unused
        clause = " AND ".join(f'"{name}" = ?' for name, _ in where)
        query = f'SELECT 1 FROM "{table}" WHERE {clause} LIMIT 1'
        connection = sqlite3.connect(
            f"file:{probe_path}?mode=ro", uri=True, timeout=OBSERVE_TIMEOUT)
        try:
            row = connection.execute(query, [v for _, v in where]).fetchone()
        finally:
            connection.close()
    except Exception as exc:  # fail-safe
        return None, signal, f"sqlite probe error: {exc}"

    present = row is not None
    signal["rows_found"] = 1 if present else 0
    ok = present if expect == "present" else (not present)
    # Die Bedingung mitnennen: "row absent" allein liest sich, als fehle die
    # Zeile, waehrend in Wahrheit oft nur die erwartete WIRKUNG ausblieb
    # (Zeile da, aber status noch active).
    described = " and ".join(f"{name}={value}" for name, value in where)
    return ok, signal, (f"sqlite: {'row present' if present else 'no row'} "
                        f"for {described} (expected {expect})")


# --- event_app_change: Wahrheitspruefung gegen die Event-App (MCP) -----------
# list_recent_changes liefert TEXT, je Zeile:
#   - YYYY-MM-DD HH:MM · <actor> (<quelle>): „<titel>" ...
_EVENT_ZEILE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (.+) \(([^()]*)\): „")
# Stempel sind nur minutengenau; so viel Spielraum an beiden Fensterraendern.
_EVENT_TOLERANZ_S = 120
# aenderungen_seit (event-mcp) liefert hoechstens so viele Zeilen, neueste zuerst
# (werkzeuge.MAX_AENDERUNGEN). Volle Seite ohne Treffer = abgeschnitten = unklar.
_EVENT_VERLAUF_MAX = 100


def _event_jetzt():
    """Aktuelle Zeit (aware). Eigene Funktion, damit Tests sie festsetzen."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _event_antwort_lesen(r):
    """JSON-RPC-Objekt aus einer SSE- oder JSON-Antwort."""
    import json
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "text/event-stream" in ctype:
        daten = [z[5:].strip() for z in r.text.splitlines() if z.startswith("data:")]
        daten = [d for d in daten if d]
        if not daten:
            raise ValueError("keine data-Zeile")
        return json.loads(daten[-1])
    return json.loads(r.text)


# Grenzen dieser Pruefung (bewusst, die App ist nicht aenderbar):
# - Die Antwort ist Freitext, verglichen wird der Anzeigename. Wer Aufgaben oder
#   Mitglieder anlegen kann, kann einen Treffer faelschen (Mitglied mit gleichem
#   Namen; Titel mit Zeilenumbruch und nachgebauter Zeile). Es gibt keinen Schutz
#   gegen gefaelschte Titel oder Mitgliedsnamen.
# - Milderung: optionaler spec-Schluessel `quelle`; ist er gesetzt, muss die
#   Quelle in Klammern exakt passen. Die Zeile wird an der LETZTEN Stelle
#   " (<quelle>): „" verankert (rechte Seite, von der App gesetzt); ein Titel mit
#   Zeilenumbruch und nachgebauter Zeile bleibt die bekannte Grenze. Das Ergebnis ist ein Beleg, keine
#   Autorisierung - darum bleibt on_fail "report".
# - Jede VibeMind-Zeile im Fenster zaehlt; die Pruefung ist nicht an den
#   einzelnen Auftrag gebunden (zwei Auftraege im Fenster belegen sich gegenseitig).
# - Sommerzeit: Stempel werden mit fold=0 gelesen; in der doppelten Herbststunde
#   gilt die erste Lesart, die zweite wird hingenommen.
def _check_event_app_change(spec: Dict[str, Any]):
    """Hat `actor` in den letzten `since_s` Sekunden etwas in der Event-App geaendert?

    Unabhaengige Nachfrage ueber das MCP-Werkzeug list_recent_changes; die Antwort
    des Bots zaehlt nicht. spec: {check: event_app_change, actor: "VibeMind",
    since_s: 600, tz: "Europe/Berlin"}. Alles ausser einem klaren Ja/Nein -> None.
    detail/signal nennen nur Zaehlwerte, nie Token, Namen oder Titel.

    EVENT_APP_URL darf die Basis-URL der App sein (wie im Space, dort haengt
    openclaw.json /api/mcp an) oder schon die MCP-Adresse; beides geht."""
    url = (os.environ.get("EVENT_APP_URL") or "").strip().rstrip("/")
    if not url:
        return None, {}, "EVENT_APP_URL fehlt - keine Nachfrage moeglich"
    if not url.endswith("/api/mcp"):
        url += "/api/mcp"
    try:
        from core.config import get_secret
        token = get_secret("EVENT_APP_TOKEN")
    except Exception:
        token = os.environ.get("EVENT_APP_TOKEN")
    token = (token or "").strip()
    if not token:
        return None, {}, "kein Token (EVENT_APP_TOKEN[_FILE])"
    try:
        import requests  # lazy
    except Exception:
        return None, {}, "requests not available"
    signal: Dict[str, Any] = {}
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        actor = str(spec.get("actor") or "VibeMind").strip()
        since_s = float(spec.get("since_s", 600))
        tz = ZoneInfo(str(spec.get("tz") or "Europe/Berlin"))
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", "Authorization": token}

        def rpc(payload):
            r = requests.post(url, json=payload, headers=headers, timeout=OBSERVE_TIMEOUT)
            signal["status_code"] = r.status_code
            if r.status_code >= 400:
                return None, f"http {r.status_code}"
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                headers["Mcp-Session-Id"] = sid
            return _event_antwort_lesen(r), ""

        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "world_observer", "version": "1"}}}
        antwort, grund = rpc(init)
        if antwort is None or "error" in antwort:
            return None, signal, grund or "initialize abgelehnt"
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "list_recent_changes", "arguments": {"limit": 50}}}
        antwort, grund = rpc(call)
        if antwort is None:
            return None, signal, grund
        result = antwort.get("result")
        if "error" in antwort or not isinstance(result, dict) or result.get("isError"):
            return None, signal, "list_recent_changes lieferte einen Fehler"
        text = "\n".join(str(c.get("text", "")) for c in result.get("content") or []
                       if isinstance(c, dict))
        jetzt = _event_jetzt()
        von = jetzt - timedelta(seconds=since_s + _EVENT_TOLERANZ_S)
        bis = jetzt + timedelta(seconds=_EVENT_TOLERANZ_S)
        quelle = spec.get("quelle")
        quelle = str(quelle).strip() if quelle is not None else None
        zeilen = treffer = 0
        for zeile in text.splitlines():
            m = _EVENT_ZEILE.match(zeile)
            if not m:
                continue
            zeilen += 1
            if m.group(2).strip() != actor:
                continue
            if quelle is not None and m.group(3).strip() != quelle:
                continue
            try:
                stempel = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                continue  # unmoegliches Datum: Zeile zaehlt nicht, Pruefung laeuft weiter
            if von <= stempel <= bis:
                treffer += 1
        signal.update({"zeilen": zeilen, "treffer": treffer})
        return treffer > 0, signal, f"{zeilen} Zeilen gelesen, {treffer} von {actor} im Fenster"
    except Exception as e:
        # Nur der Typ: Meldungen koennten URL oder Inhalte enthalten.
        return None, signal, f"event_app_change nicht pruefbar ({type(e).__name__})"


# --- event_change: zweite Quelle `verlauf` (event-mcp) + Legacy-Weg ------------------
# Der Verlauf ist strukturiert (actor, zeitpunkt exakt); der Vergleich ist exakt auf
# `actor_verlauf` (z. B. "bot:betreiber:VibeMind"). Der Schluessel ist ein eigener
# betreiber_lesen-Schluessel (Name "Pruefung"), unabhaengig vom Bot.
def _check_event_verlauf(spec: Dict[str, Any]):
    """Hat `actor_verlauf` in den letzten `since_s` Sekunden einen Verlaufseintrag
    (Werkzeug aenderungen_seit des event-mcp)? Alles ausser Ja/Nein -> None.
    detail/signal nennen nur Zaehlwerte, nie Token, URL, Namen oder Titel."""
    base = (os.environ.get("EVENT_MCP_URL") or "").strip().rstrip("/")
    if not base:
        return None, {}, "EVENT_MCP_URL fehlt - keine Nachfrage moeglich"
    url = base if base.endswith("/mcp") else base + "/mcp"
    try:
        from core.config import get_secret
        token = get_secret("EVENT_VERLAUF_TOKEN")
    except Exception:
        token = os.environ.get("EVENT_VERLAUF_TOKEN")
    token = (token or "").strip()
    if not token:
        return None, {}, "kein Token (EVENT_VERLAUF_TOKEN[_FILE])"
    try:
        import requests  # lazy
    except Exception:
        return None, {}, "requests not available"
    signal: Dict[str, Any] = {}
    try:
        import json
        import math
        from datetime import datetime, timedelta, timezone
        actor = str(spec.get("actor_verlauf") or "").strip()
        if not actor:
            return None, {}, "actor_verlauf fehlt"
        since_s = float(spec.get("since_s", 600))
        minuten = min(1440, max(1, math.ceil((since_s + _EVENT_TOLERANZ_S) / 60)))
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", "Authorization": f"Bearer {token}"}

        def rpc(payload):
            r = requests.post(url, json=payload, headers=headers, timeout=OBSERVE_TIMEOUT)
            signal["verlauf_status"] = r.status_code
            if r.status_code >= 400:
                return None, f"http {r.status_code}"
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                headers["Mcp-Session-Id"] = sid
            return _event_antwort_lesen(r), ""

        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "world_observer", "version": "1"}}}
        antwort, grund = rpc(init)
        if antwort is None or "error" in antwort:
            return None, signal, grund or "initialize abgelehnt"
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "aenderungen_seit", "arguments": {"minuten": minuten}}}
        antwort, grund = rpc(call)
        if antwort is None:
            return None, signal, grund
        result = antwort.get("result")
        if "error" in antwort or not isinstance(result, dict) or result.get("isError"):
            return None, signal, "aenderungen_seit lieferte einen Fehler"
        text = "".join(str(c.get("text", "")) for c in result.get("content") or []
                       if isinstance(c, dict))
        zeilen_roh = json.loads(text).get("aenderungen")
        if not isinstance(zeilen_roh, list):
            return None, signal, "aenderungen_seit: unerwartete Antwort"
        jetzt = _event_jetzt()
        von = jetzt - timedelta(seconds=since_s + _EVENT_TOLERANZ_S)
        bis = jetzt + timedelta(seconds=_EVENT_TOLERANZ_S)
        zeilen = treffer = 0
        for z in zeilen_roh:
            if not isinstance(z, dict):
                continue
            zeilen += 1
            if z.get("actor") != actor:
                continue
            try:
                stempel = datetime.fromisoformat(str(z.get("zeitpunkt")))
            except ValueError:
                continue
            if stempel.tzinfo is None:
                stempel = stempel.replace(tzinfo=timezone.utc)
            if von <= stempel <= bis:
                treffer += 1
        signal.update({"verlauf_zeilen": zeilen, "verlauf_treffer": treffer})
        if treffer == 0 and zeilen >= _EVENT_VERLAUF_MAX:
            # Review T5: die Seite ist voll, der Eintrag des Bots kann abgeschnitten sein.
            return None, signal, f"verlauf: {zeilen} Zeilen (abgeschnitten), 0 vom Bot im Fenster"
        return treffer > 0, signal, f"verlauf: {zeilen} Zeilen gelesen, {treffer} vom Bot im Fenster"
    except Exception as e:
        # Nur der Typ: Meldungen koennten URL oder Inhalte enthalten.
        return None, signal, f"verlauf nicht pruefbar ({type(e).__name__})"


def _check_event_change(spec: Dict[str, Any]):
    """Wahrheitspruefung fuer event_do: erst `verlauf` (event-mcp), dann der Legacy-Weg
    (_check_event_app_change). True, wenn eine Quelle True liefert; False nur, wenn beide
    klar False liefern; sonst None. spec: {actor_verlauf, actor_legacy, since_s,
    [quelle, tz]}. detail/signal nennen nur Zaehlwerte."""
    ok_v, sig_v, det_v = _check_event_verlauf(spec)
    legacy_spec = {k: v for k, v in spec.items() if k not in ("actor_verlauf", "actor_legacy")}
    legacy_spec["actor"] = spec.get("actor_legacy") or "VibeMind"
    ok_l, sig_l, det_l = _check_event_app_change(legacy_spec)
    signal: Dict[str, Any] = {"verlauf_treffer": sig_v.get("verlauf_treffer"),
                              "verlauf_zeilen": sig_v.get("verlauf_zeilen"),
                              "legacy_treffer": sig_l.get("treffer"),
                              "legacy_zeilen": sig_l.get("zeilen")}
    signal = {k: v for k, v in signal.items() if v is not None}
    detail = f"{det_v}; legacy: {det_l}"
    if ok_v is True or ok_l is True:
        return True, signal, detail
    if ok_v is False and ok_l is False:
        return False, signal, detail
    return None, signal, detail


# Map of check-name → fn. To add a check, add one function above + an entry here.
_CHECKS = {
    "process_running": _check_process_running,
    "port_open": _check_port_open,
    "file_exists": _check_file_exists,
    "http_ok": _check_http_ok,
    "supabase_row": _check_supabase_row,
    "sqlite_row": _check_sqlite_row,
    "supabase_edge": _check_supabase_edge,
    "supabase_edge_ids": _check_supabase_edge_ids,
    "supabase_node_in_bubble": _check_supabase_node_in_bubble,
    "supabase_bubble_node_count": _check_supabase_bubble_node_count,
    "event_app_change": _check_event_app_change,
    "event_change": _check_event_change,
}


def available_checks() -> list:
    return sorted(_CHECKS.keys())


def observe(postcondition: Optional[Dict[str, Any]]) -> Verification:
    """Run a declared post-condition check against the real world.

    `postcondition` is a dict like {check: process_running, name: chrome}.
    Returns a Verification; never raises.
    """
    t0 = time.time()
    if not GROUND_TRUTH_ENABLED:
        return Verification(verdict=UNVERIFIED, reason="GROUND_TRUTH_ENABLED off")
    if not postcondition or not isinstance(postcondition, dict):
        return Verification(verdict=UNVERIFIED, reason="no postcondition declared")
    check = postcondition.get("check") or ""
    fn = _CHECKS.get(check)
    if fn is None:
        return Verification(verdict=UNVERIFIED, check=check,
                            reason=f"unknown check '{check}'")
    try:
        ok, signal, reason = fn(postcondition)
    except Exception as e:  # fail-safe: observer error ≠ action failure
        logger.debug("[world_observer] check %s errored: %s", check, e)
        return Verification(verdict=UNVERIFIED, check=check,
                            reason=f"observer error: {e}",
                            latency_ms=(time.time() - t0) * 1000)
    dt = (time.time() - t0) * 1000
    if ok is True:
        return Verification(verdict=VERIFIED, check=check, signal=signal,
                            reason=reason, latency_ms=dt)
    if ok is False:
        return Verification(verdict=REFUTED, check=check, signal=signal,
                            reason=reason, latency_ms=dt)
    return Verification(verdict=UNVERIFIED, check=check, signal=signal,
                        reason=reason, latency_ms=dt)
