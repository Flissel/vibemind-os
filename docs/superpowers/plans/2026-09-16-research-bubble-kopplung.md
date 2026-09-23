# Research-Bubble-Kopplung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Research-Auftrag wird aus einer Bubble ausgelöst, bezieht deren Inhalt ein und legt den Report als Artefakt plus Kurzfassungs-Knoten in genau dieser Bubble ab.

**Architecture:** Zwei neue MCP-Werkzeuge (`research_start`, `research_status`) in einem eigenen stdio-Server `spaces/research/mcp_server.py`, gebaut nach dem Muster von `spaces/ideas/mcp_server.py`. Der Lauf geht an den bereits aktiven OpenFang-Agenten `researcher-hand`; der Beleg für einen echten Lauf ist die Report-Datei am diktierten absoluten Pfad plus selbst gezählte Quell-URLs, nicht das (immer leere) `tool_calls`-Feld.

**Tech Stack:** Python 3.11, stdlib only (`urllib`, `json`, `subprocess`, `secrets`) — keine neuen Abhängigkeiten. Tests mit `unittest` (Muster `spaces/ideas/tests/`) bzw. `pytest` für die Brain-Tests. PostgREST gegen Supabase.

**Spec:** `docs/superpowers/specs/2026-09-16-research-bubble-kopplung-design.md`

## Global Constraints

- **Keine neuen Abhängigkeiten.** Nur Python-Standardbibliothek, wie `spaces/ideas/mcp_server.py`.
- **Artefaktverzeichnis:** `~/.openfang/research-artifacts/` — absolut, außerhalb jedes Repos. Nie in den Arbeitsbaum schreiben.
- **Agent:** `researcher-hand`, `agent_id` `52a6d4df-6eb0-5c55-a200-b984514886ab`, erreichbar über `POST {OPENFANG_URL}/api/agents/<id>/message`. `OPENFANG_URL` Standard `http://127.0.0.1:4200`.
- **Fail-closed:** Kein Ergebnis ohne Datei am diktierten Pfad und ohne mindestens eine Quell-URL. Nie den Selbstbericht des Agenten als Beleg nehmen.
- **ID-Formate** (CHECK-Constraints der Migration, exakt einzuhalten):
  - `job_id` ~ `^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$`
  - `artifact_ref` ~ `^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$`
  - `subject_type` ∈ `topic|idea|bubble`, `format` ∈ `markdown|text|json`
  - `depth` ∈ `quick|thorough|exhaustive`, `output_style` ∈ `brief|detailed|academic|executive`
  - `citation_count` ≥ 1, und `internal_context_used OR context_disclosure IS NOT NULL`
- **Supabase-Zugang:** `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` aus der Prozessumgebung, wie in `spaces/ideas/mcp_server.py:335-344`. Nie hart verdrahten, nie Schlüssel ausgeben.
- **Git:** Nur die eigenen Dateien mit Namen stagen. Beide Repos tragen fremde uncommittete Arbeit — niemals `git add -A`.

---

### Task 1: Reine Bausteine — IDs, Zitatzählung, Auftragsbau

Alles ohne Netz und ohne Datenbank, damit der Rest darauf aufbauen kann.

**Files:**
- Create: `spaces/research/brief.py`
- Test: `spaces/research/tests/test_brief.py`
- Create: `spaces/research/tests/__init__.py` (leer)

**Interfaces:**
- Consumes: nichts
- Produces:
  - `new_ulid(now_ms: int | None = None, rand: int | None = None) -> str` — 26 Zeichen Crockford-Base32
  - `new_job_id() -> str` — `job_v1_<ULID>`
  - `new_artifact_ref() -> str` — `artifact_v1_<ULID>`
  - `count_citations(text: str) -> int` — Anzahl **eindeutiger** http(s)-URLs
  - `extract_expected(user_brief: str) -> str`
  - `compose_brief(*, bubble_title: str, bubble_nodes: list[dict], user_brief: str, output_path: str, depth: str, output_style: str, citation_style: str, language: str) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""Reine Bausteine der Research-Bubble-Kopplung."""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "brief.py"


def load_brief():
    spec = importlib.util.spec_from_file_location("spaces_research_brief", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("spaces/research/brief.py must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JOB_RE = re.compile(r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
ARTIFACT_RE = re.compile(r"^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")


class UlidTests(unittest.TestCase):
    def test_job_id_matches_the_migration_check_constraint(self) -> None:
        brief = load_brief()
        for _ in range(50):
            self.assertRegex(brief.new_job_id(), JOB_RE)

    def test_artifact_ref_matches_the_migration_check_constraint(self) -> None:
        brief = load_brief()
        for _ in range(50):
            self.assertRegex(brief.new_artifact_ref(), ARTIFACT_RE)

    def test_ulid_excludes_the_ambiguous_crockford_letters(self) -> None:
        brief = load_brief()
        produced = "".join(brief.new_ulid() for _ in range(200))
        for forbidden in "ILOU":
            self.assertNotIn(forbidden, produced)

    def test_ulid_is_deterministic_for_fixed_inputs(self) -> None:
        brief = load_brief()
        first = brief.new_ulid(now_ms=1_726_000_000_000, rand=12345)
        second = brief.new_ulid(now_ms=1_726_000_000_000, rand=12345)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 26)


class CitationTests(unittest.TestCase):
    def test_counts_unique_urls_only(self) -> None:
        brief = load_brief()
        text = (
            "Siehe https://example.test/a und https://example.test/b\n"
            "nochmals https://example.test/a\n"
        )
        self.assertEqual(brief.count_citations(text), 2)

    def test_report_without_sources_counts_zero(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.count_citations("Kein einziger Beleg."), 0)

    def test_trailing_punctuation_is_stripped(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.count_citations("von https://example.test/x."), 1)


class ExpectedTests(unittest.TestCase):
    def test_lifts_lines_under_a_recognised_heading(self) -> None:
        brief = load_brief()
        text = "Rolle\nBlabla\n\nErgebnis\nDrei USPs nennen.\nJede Aussage belegen.\n"
        expected = brief.extract_expected(text)
        self.assertIn("Drei USPs nennen.", expected)
        self.assertIn("Jede Aussage belegen.", expected)
        self.assertNotIn("Blabla", expected)

    def test_returns_empty_when_no_heading_matches(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.extract_expected("Nur Fliesstext ohne Gliederung."), "")


class ComposeTests(unittest.TestCase):
    def _compose(self, **overrides):
        brief = load_brief()
        kwargs = dict(
            bubble_title="Sheerlay",
            bubble_nodes=[{"title": "Kernidee", "content": "OCR-Etikettenscan"}],
            user_brief="Aufgabe\nWettbewerbsanalyse durchfuehren.",
            output_path="/tmp/research_job_v1_X.md",
            depth="thorough",
            output_style="detailed",
            citation_style="academic_apa",
            language="german",
        )
        kwargs.update(overrides)
        return brief.compose_brief(**kwargs)

    def test_carries_bubble_context_and_user_brief(self) -> None:
        text = self._compose()
        self.assertIn("Sheerlay", text)
        self.assertIn("OCR-Etikettenscan", text)
        self.assertIn("Wettbewerbsanalyse durchfuehren.", text)

    def test_states_the_absolute_output_path_verbatim(self) -> None:
        text = self._compose(output_path="/tmp/research_job_v1_ABC.md")
        self.assertIn("/tmp/research_job_v1_ABC.md", text)

    def test_carries_the_per_run_overrides(self) -> None:
        text = self._compose()
        for token in ("thorough", "detailed", "academic_apa", "german"):
            self.assertIn(token, text)

    def test_names_the_missing_expected_section_when_brief_has_none(self) -> None:
        text = self._compose(user_brief="Nur Fliesstext.")
        self.assertIn("Erwartetes Ergebnis", text)
        self.assertIn("in der Vorschau", text)

    def test_empty_bubble_still_produces_a_usable_brief(self) -> None:
        text = self._compose(bubble_nodes=[])
        self.assertIn("Wettbewerbsanalyse durchfuehren.", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_brief -v`
Expected: FAIL — `spaces/research/brief.py must be importable` (Datei existiert nicht).

- [ ] **Step 3: Write minimal implementation**

```python
"""Reine Bausteine der Research-Bubble-Kopplung: IDs, Zitate, Auftragsbau.

Kein Netz, keine Datenbank - damit der Rest der Kopplung gegen diese
Funktionen getestet werden kann, ohne einen Agenten zu starten.
"""

from __future__ import annotations

import re
import secrets
import time

# Crockford-Base32: ohne I, L, O, U. Deckungsgleich mit der Zeichenklasse der
# CHECK-Constraints in 20260817_research_report_artifacts.sql.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+")

_EXPECTED_HEADINGS = (
    "ergebnis", "ergebnisse", "aufgabe", "aufgaben",
    "anforderungen", "deliverables", "ziel", "ziele",
)


def new_ulid(now_ms: int | None = None, rand: int | None = None) -> str:
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    randomness = secrets.randbits(80) if rand is None else rand
    value = (timestamp << 80) | randomness
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_job_id() -> str:
    return f"job_v1_{new_ulid()}"


def new_artifact_ref() -> str:
    return f"artifact_v1_{new_ulid()}"


def count_citations(text: str) -> int:
    found = {url.rstrip(".,;:") for url in _URL_RE.findall(text or "")}
    return len(found)


def extract_expected(user_brief: str) -> str:
    """Hebt die Zeilen unter einer erkennbaren Ergebnis-Ueberschrift heraus.

    Bewusst mechanisch: kein Modellaufruf, weil dessen Ergebnis niemand
    pruefen wuerde. Findet sich keine Ueberschrift, fuellt der Mensch den
    Abschnitt im Vorschau-Gate.
    """
    lines = (user_brief or "").splitlines()
    collected: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        normalized = stripped.lower().rstrip(":").strip("# ").strip()
        if normalized in _EXPECTED_HEADINGS:
            capturing = True
            continue
        if capturing:
            if not stripped:
                if collected:
                    break
                continue
            if normalized in _EXPECTED_HEADINGS:
                break
            collected.append(stripped)
    return "\n".join(collected)


def compose_brief(
    *,
    bubble_title: str,
    bubble_nodes: list[dict],
    user_brief: str,
    output_path: str,
    depth: str,
    output_style: str,
    citation_style: str,
    language: str,
) -> str:
    context_lines = []
    for node in bubble_nodes:
        title = (node.get("title") or "").strip()
        content = (node.get("content") or "").strip()
        if not title and not content:
            continue
        context_lines.append(f"- {title}: {content}" if content else f"- {title}")
    context = "\n".join(context_lines) if context_lines else "- (keine Inhalte hinterlegt)"

    expected = extract_expected(user_brief)
    if not expected:
        expected = (
            "(Im Brief nicht erkennbar - bitte in der Vorschau selbst eintragen, "
            "damit klar ist, was herauskommen soll.)"
        )

    return f"""[Hintergrund aus Bubble "{bubble_title}"]
{context}

[Auftrag]
{user_brief.strip()}

[Erwartetes Ergebnis]
{expected}

[Vorgaben fuer diesen Lauf - sie ueberschreiben deine User Configuration]
- Tiefe: {depth}
- Ausgabestil: {output_style}
- Zitierweise: {citation_style}
- Sprache: {language}

[Pflicht]
1. Schreibe den vollstaendigen Report mit file_write unter EXAKT diesem
   absoluten Pfad: {output_path}
2. Der Report muss mindestens eine echte Quelle mit URL enthalten.
   Erfinde nichts. Fehlende Information wird als fehlend benannt.
3. Antworte am Ende mit genau diesem Pfad.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_brief -v`
Expected: PASS, 14 Tests (4 Ulid, 3 Citation, 2 Expected, 5 Compose).

- [ ] **Step 5: Commit**

```bash
git add spaces/research/brief.py spaces/research/tests/__init__.py spaces/research/tests/test_brief.py
git commit -m "feat(research): IDs, Zitatzaehlung und Auftragsbau als reine Funktionen"
```

---

### Task 2: Migration lokal anwenden und die Constraints nachweisen

Kein Anwendungscode. Deliverable ist eine Tabelle, deren fail-closed-Verhalten gemessen ist.

**Files:**
- Apply: `supabase/migrations/20260817_research_report_artifacts.sql` (unverändert, nur anwenden)
- Create: `docs/operations/2026-09-16-research-report-artifacts-apply.md`

**Interfaces:**
- Consumes: nichts
- Produces: Tabelle `public.research_report_artifacts` auf der lokalen Supabase, erreichbar unter `http://127.0.0.1:54321/rest/v1/research_report_artifacts`

- [ ] **Step 1: Ausgangszustand feststellen**

```bash
curl -s -o /dev/null -w "vorher=%{http_code}\n" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  "http://127.0.0.1:54321/rest/v1/research_report_artifacts?select=id&limit=1"
```

Expected: `vorher=404` — die Tabelle existiert noch nicht.

- [ ] **Step 2: Container ermitteln**

```bash
docker ps --filter name=vibemind_supabase-db --format "{{.Names}}"
```

Expected: eine Zeile, z.B. `vibemind_supabase-db.1.<task-id>`. Diesen Namen unten als `<DB>` einsetzen.

- [ ] **Step 3: Migration anwenden**

```bash
docker cp supabase/migrations/20260817_research_report_artifacts.sql "<DB>:/tmp/"
MSYS_NO_PATHCONV=1 docker exec "<DB>" psql -U supabase_admin -d postgres \
  -f /tmp/20260817_research_report_artifacts.sql
```

Expected: `BEGIN`, `CREATE TABLE`, fünfmal `CREATE INDEX`, `COMMIT` — kein `ERROR`.

- [ ] **Step 4: PostgREST-Cache neu laden und Erreichbarkeit prüfen**

```bash
MSYS_NO_PATHCONV=1 docker exec "<DB>" psql -U supabase_admin -d postgres \
  -c "NOTIFY pgrst, 'reload schema';"
curl -s -o /dev/null -w "nachher=%{http_code}\n" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  "http://127.0.0.1:54321/rest/v1/research_report_artifacts?select=id&limit=1"
```

Expected: `nachher=200`.

- [ ] **Step 5: Das fail-closed-Verhalten messen, nicht annehmen**

```bash
MSYS_NO_PATHCONV=1 docker exec "<DB>" psql -U supabase_admin -d postgres -c \
"INSERT INTO public.research_report_artifacts
 (artifact_ref, job_id, subject_type, subject_ref, name, citation_count, internal_context_used)
 VALUES ('artifact_v1_00000000000000000000000000','job_v1_00000000000000000000000000',
         'topic','probe','probe.md',0,true);"
```

Expected: `ERROR: new row for relation "research_report_artifacts" violates check constraint "research_report_artifacts_citation_count_check"` — ein Report ohne Zitat ist nicht speicherbar. Schlägt der Befehl **nicht** fehl, ist die Migration nicht korrekt angewendet; dann anhalten.

- [ ] **Step 6: Anwendung dokumentieren**

Datei `docs/operations/2026-09-16-research-report-artifacts-apply.md` mit: Datum, Zielinstanz (lokal), Containername, die vier Messwerte aus Schritt 1/4/5, und dem Hinweis, dass die VM-Anwendung noch aussteht (Task 8).

- [ ] **Step 7: Commit**

```bash
git add docs/operations/2026-09-16-research-report-artifacts-apply.md
git commit -m "docs(research): lokale Anwendung der Artefakt-Migration belegt"
```

---

### Task 3: MCP-Server mit Vorschau-Stufe

`research_start` ohne `confirm` liest die Bubble, baut den Auftrag und gibt ihn zurück — **ohne** einen Lauf zu starten.

**Files:**
- Create: `spaces/research/mcp_server.py`
- Test: `spaces/research/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `brief.compose_brief`, `brief.new_job_id` aus Task 1
- Produces:
  - `TOOLS: list[dict]` mit den Namen `research_start`, `research_status`
  - `handle_message(message: dict) -> dict` (JSON-RPC, Muster `spaces/ideas/mcp_server.py`)
  - `call_tool(name: str, arguments: dict) -> dict`
  - `ToolError(Exception)`
  - `_read_bubble(bubble_id: str) -> dict`, `_read_nodes(bubble_id: str) -> list[dict]`
  - `ARTIFACT_DIR: pathlib.Path`
  - `_write_job_file(job_id: str, payload: dict) -> None`
  - `_spawn_agent_call(job_id: str, brief_text: str) -> None` (hier nur als Stub, der wirft; in Task 4 gefüllt)

- [ ] **Step 1: Write the failing test**

```python
"""Vertragstests fuer den Research-MCP-Server."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_research_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("spaces/research/mcp_server.py must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUBBLE = {"id": "bub123", "title": "Sheerlay", "description": "OCR-Etikettenscan"}
NODES = [{"title": "Kernidee", "content": "Etiketten scannen und bewerten"}]


class ToolSurfaceTests(unittest.TestCase):
    def test_tools_list_is_stable(self) -> None:
        server = load_server()
        response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(
            [tool["name"] for tool in response["result"]["tools"]],
            ["research_start", "research_status"],
        )

    def test_research_start_requires_bubble_id_and_brief(self) -> None:
        server = load_server()
        response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        start = response["result"]["tools"][0]
        # sorted() liefert ["brief", "bubble_id"] - "brief" kommt zuerst, weil
        # an Position 1 'r' < 'u' gilt. Die naheliegende Schreibweise
        # ["bubble_id", "brief"] koennte nie bestehen.
        self.assertEqual(sorted(start["inputSchema"]["required"]), ["brief", "bubble_id"])


class PreviewTests(unittest.TestCase):
    def test_preview_returns_the_assembled_brief(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn:
            result = server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nWettbewerbsanalyse."},
            )

        self.assertEqual(result["status"], "preview")
        self.assertIn("Sheerlay", result["brief"])
        self.assertIn("Etiketten scannen und bewerten", result["brief"])
        self.assertIn("Wettbewerbsanalyse.", result["brief"])
        spawn.assert_not_called()

    def test_preview_does_not_spend_a_job_id_slot_on_disk(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file") as write:
            server.call_tool("research_start", {"bubble_id": "bub123", "brief": "x"})
        write.assert_not_called()

    def test_unknown_bubble_is_rejected_before_any_run(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", side_effect=server.ToolError("not_found: bubble")), \
             mock.patch.object(server, "_spawn_agent_call") as spawn:
            with self.assertRaises(server.ToolError):
                server.call_tool("research_start", {"bubble_id": "nope", "brief": "x"})
        spawn.assert_not_called()

    def test_rejects_depth_outside_the_closed_value_set(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES):
            with self.assertRaises(server.ToolError):
                server.call_tool(
                    "research_start",
                    {"bubble_id": "bub123", "brief": "x", "depth": "ludicrous"},
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server -v`
Expected: FAIL — `spaces/research/mcp_server.py must be importable`.

- [ ] **Step 3: Write minimal implementation**

```python
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
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

SERVER_NAME = "spaces-research"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15

RESEARCHER_AGENT_ID = "52a6d4df-6eb0-5c55-a200-b984514886ab"
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


def _write_job_file(job_id: str, payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _job_state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _spawn_agent_call(job_id: str, brief_text: str) -> None:
    raise ToolError("not_implemented: _spawn_agent_call wird in Task 4 gefuellt")


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

        if not arguments.get("confirm"):
            return {
                "status": "preview",
                "brief": brief_text,
                "note": (
                    "Kein Lauf gestartet. Brief pruefen, bei Bedarf bearbeiten, "
                    "dann mit confirm=true erneut aufrufen."
                ),
            }

        raise ToolError("not_implemented: confirm wird in Task 4 gefuellt")

    if name == "research_status":
        raise ToolError("not_implemented: research_status wird in Task 5 gefuellt")

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
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server -v`
Expected: PASS, 6 Tests.

- [ ] **Step 5: Commit**

```bash
git add spaces/research/mcp_server.py spaces/research/tests/test_mcp_server.py
git commit -m "feat(research): MCP-Server mit Vorschau-Gate fuer Bubble-Recherche"
```

---

### Task 4: Lauf starten, Job-Zustand ablegen

`confirm=true` stößt den Agenten im Hintergrund an und kehrt sofort mit `job_id` zurück.

**Files:**
- Modify: `spaces/research/mcp_server.py` (`_start_run`, `call_tool`-Zweig `confirm`)
- Test: `spaces/research/tests/test_mcp_server.py` (neue Klasse anhängen)

**Interfaces:**
- Consumes: `_write_job_file`, `_job_path` aus Task 3
- Produces: `research_start(..., confirm=True)` liefert `{"status": "started", "job_id": str, "report_path": str}`

- [ ] **Step 1: Write the failing test**

Diese Klasse an `spaces/research/tests/test_mcp_server.py` anhängen:

```python
class StartTests(unittest.TestCase):
    def test_confirm_starts_a_run_and_returns_a_job_id(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn, \
             mock.patch.object(server, "_write_job_file") as write:
            result = server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nX.", "confirm": True},
            )

        self.assertEqual(result["status"], "started")
        self.assertRegex(result["job_id"], r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(result["job_id"], result["report_path"])
        spawn.assert_called_once()
        write.assert_called_once()

    def test_job_file_records_the_bubble_and_the_settings(self) -> None:
        server = load_server()
        captured = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file",
                               side_effect=lambda job_id, payload: captured.update(payload)):
            server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nX.", "confirm": True,
                 "depth": "exhaustive", "output_style": "academic"},
            )

        self.assertEqual(captured["bubble_id"], "bub123")
        self.assertEqual(captured["depth"], "exhaustive")
        self.assertEqual(captured["output_style"], "academic")
        self.assertIn("brief", captured)

    def test_edited_brief_is_used_verbatim_when_supplied(self) -> None:
        server = load_server()
        sent = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call",
                               side_effect=lambda job_id, text: sent.update(text=text)), \
             mock.patch.object(server, "_write_job_file"):
            server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "ignoriert", "confirm": True,
                 "final_brief": "MEIN BEARBEITETER AUFTRAG"},
            )

        self.assertEqual(sent["text"], "MEIN BEARBEITETER AUFTRAG")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server.StartTests -v`
Expected: FAIL — `_spawn_agent_call` existiert nicht bzw. `not_implemented: confirm`.

- [ ] **Step 3: Write minimal implementation**

`final_brief` in das `inputSchema` von `research_start` aufnehmen:

```python
                "final_brief": {"type": "string"},
```

Den Stub `_spawn_agent_call` aus Task 3 durch die echte Fassung ersetzen:

```python
def _spawn_agent_call(job_id: str, brief_text: str) -> None:
    """Stoesst den Agentenlauf an, ohne auf ihn zu warten.

    Der Aufruf blockiert serverseitig ohne Timeout bis zum Ende der
    Agentenschleife - beim Rauchtest 80 s fuer eine triviale Frage. Der
    Abholweg ist deshalb die Reportdatei, nicht diese Antwort.
    """
    base = (os.environ.get("OPENFANG_URL") or "http://127.0.0.1:4200").rstrip("/")
    url = f"{base}/api/agents/{RESEARCHER_AGENT_ID}/message"
    payload = json.dumps({"message": brief_text}, ensure_ascii=False)
    script = (
        "import json,sys,urllib.request\n"
        "req=urllib.request.Request(sys.argv[1],"
        "data=sys.argv[2].encode('utf-8'),method='POST',"
        "headers={'Content-Type':'application/json'})\n"
        "urllib.request.urlopen(req,timeout=5400).read()\n"
    )
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    log = open(ARTIFACT_DIR / f"research_{job_id}.spawn.log", "wb")
    subprocess.Popen(
        [sys.executable, "-c", script, url, payload],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
    )
```

`import subprocess` und `import time` zu den Importen hinzufügen. Im `confirm`-Zweig die Zeile `raise ToolError("not_implemented: confirm ...")` ersetzen durch:

```python
        final = arguments.get("final_brief")
        if final is not None:
            if not isinstance(final, str) or not final.strip():
                raise ToolError("invalid_arguments: 'final_brief' must be a non-empty string")
            brief_text = final

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server -v`
Expected: PASS, 9 Tests.

- [ ] **Step 5: Commit**

```bash
git add spaces/research/mcp_server.py spaces/research/tests/test_mcp_server.py
git commit -m "feat(research): Lauf im Hintergrund starten und Job-Zustand ablegen"
```

---

### Task 5: Ergebnis abholen, prüfen und in der Bubble verbuchen

**Files:**
- Modify: `spaces/research/mcp_server.py` (`research_status`-Zweig, `_persist_result`)
- Test: `spaces/research/tests/test_mcp_server.py` (neue Klasse anhängen)

**Interfaces:**
- Consumes: `brief.count_citations`, `brief.new_artifact_ref`, `_job_path`, `_job_state_path`, `_request`
- Produces: `research_status(job_id)` liefert `{"status": "pending"|"done"|"failed", ...}`; bei `done` zusätzlich `artifact_ref`, `citation_count`, `node_id`

- [ ] **Step 1: Write the failing test**

```python
import json as _json
import tempfile


class StatusTests(unittest.TestCase):
    def _server_with_artifacts(self, tmp: Path):
        server = load_server()
        server.ARTIFACT_DIR = tmp
        return server

    def _job_state(self, server, job_id: str) -> None:
        server._job_state_path(job_id).write_text(_json.dumps({
            "job_id": job_id, "bubble_id": "bub123", "bubble_title": "Sheerlay",
            "depth": "thorough", "output_style": "detailed",
            "citation_style": "academic_apa", "language": "german",
            "brief": "x", "report_path": str(server._job_path(job_id)),
            "started_at": 0,
        }, ensure_ascii=False), encoding="utf-8")

    def test_missing_report_is_pending_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000A"
            self._job_state(server, job_id)
            result = server.call_tool("research_status", {"job_id": job_id})
        self.assertEqual(result["status"], "pending")

    def test_dead_spawn_is_reported_as_failed_not_pending_forever(self) -> None:
        """Ist OpenFang unerreichbar, stirbt das Kind mit Traceback im spawn.log.

        Ohne diese Pruefung meldete research_status bis in alle Ewigkeit
        'pending', obwohl nie wieder etwas passieren wird.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000E"
            self._job_state(server, job_id)
            (tmp / f"research_{job_id}.spawn.log").write_text(
                "Traceback (most recent call last):\n"
                "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>\n",
                encoding="utf-8",
            )
            result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "failed")
        self.assertIn("agent call failed", result["error"])
        self.assertIn("Connection refused", result["detail"])

    def test_report_without_citations_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000B"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("Kein Beleg.", encoding="utf-8")
            with mock.patch.object(server, "_request") as request:
                result = server.call_tool("research_status", {"job_id": job_id})
            request.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("citation", result["error"])

    def test_cited_report_is_persisted_as_artifact_and_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000C"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text(
                "# Report\nQuelle: https://example.test/a\n", encoding="utf-8"
            )
            calls = []

            def fake_request(method, path, **kwargs):
                calls.append((method, path))
                if path == "research_report_artifacts":
                    return [{"id": "art1"}]
                return [{"id": "node1"}]

            with mock.patch.object(server, "_request", side_effect=fake_request):
                result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["citation_count"], 1)
        self.assertRegex(result["artifact_ref"], r"^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(("POST", "research_report_artifacts"), calls)
        self.assertIn(("POST", "canvas_nodes"), calls)

    def test_artifact_row_binds_the_report_to_the_triggering_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000D"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            bodies = {}

            def fake_request(method, path, **kwargs):
                if path == "research_report_artifacts":
                    bodies["artifact"] = kwargs.get("body")
                    return [{"id": "art1"}]
                bodies["node"] = kwargs.get("body")
                return [{"id": "node1"}]

            with mock.patch.object(server, "_request", side_effect=fake_request):
                server.call_tool("research_status", {"job_id": job_id})

        artifact = bodies["artifact"]
        self.assertEqual(artifact["subject_type"], "bubble")
        self.assertEqual(artifact["bubble_id"], "bub123")
        self.assertEqual(artifact["citation_count"], 1)
        self.assertEqual(artifact["depth"], "thorough")
        self.assertTrue(artifact["internal_context_used"])
        self.assertEqual(bodies["node"]["linked_idea_id"], "bub123")

    def test_unknown_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            server = self._server_with_artifacts(Path(raw))
            with self.assertRaises(server.ToolError):
                server.call_tool("research_status", {"job_id": "job_v1_0000000000000000000000000Z"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server.StatusTests -v`
Expected: FAIL — `not_implemented: research_status`.

- [ ] **Step 3: Write minimal implementation**

`research_status`-Zweig in `call_tool` ersetzen:

```python
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
```

`_persist_result` ergänzen:

```python
def _persist_result(job_id: str, state: Mapping[str, Any], text: str, citations: int) -> dict:
    artifact_ref = brief_mod.new_artifact_ref()
    bubble_id = state["bubble_id"]
    title = f"Research: {state.get('bubble_title') or bubble_id}"

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
        "internal_context_used": True,
        "context_disclosure": None,
    })

    summary = text.strip().split("\n\n", 1)[0][:1500]
    node = _request("POST", "canvas_nodes", body={
        "id": artifact_ref[-8:],
        "node_type": "research",
        "title": title[:120],
        "content": f"{summary}\n\nVollstaendiger Report: {artifact_ref}\nQuellen: {citations}",
        "x": 0, "y": 0,
        "linked_idea_id": bubble_id,
        "metadata": {"width": 260.0, "height": 160.0, "artifact_ref": artifact_ref},
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os && python -m unittest spaces.research.tests.test_mcp_server -v`
Expected: PASS, 15 Tests.

- [ ] **Step 5: Commit**

```bash
git add spaces/research/mcp_server.py spaces/research/tests/test_mcp_server.py
git commit -m "feat(research): Ergebnis fail-closed pruefen und in der Bubble verbuchen"
```

---

### Task 6: `ResearchTarget` auf einen erfüllbaren Beleg umstellen

Der eigentliche Fehler: die Prüfung verlangt `tool_calls`, der Treiber gibt sie nie zurück.

**Files:**
- Modify: `spaces/research/execution_target.py:50-90` (`call`), `:135-141` (`_instruction`)
- Modify: `brain/the_brain/tests/test_research_execution_contract.py:59-104`

**Interfaces:**
- Consumes: `brief.count_citations`, `brief.new_job_id`, `ARTIFACT_DIR`-Konvention aus Task 1/3
- Produces: `ResearchTarget.call()` liefert `{"ok": True, "result": {"operation", "content", "sources", "evidence": {"report_path", "citation_count"}}}`

- [ ] **Step 1: Write the failing test**

In `brain/the_brain/tests/test_research_execution_contract.py` die beiden Tests
`test_research_target_returns_sources_and_tool_evidence` und
`test_research_target_fails_closed_without_external_tool_evidence` **ersetzen** durch:

```python
def test_research_target_verifies_the_report_file_not_the_agent_claim(tmp_path, monkeypatch):
    executor = build_executor("research:web")
    report = tmp_path / "report.md"
    report.write_text("Ergebnis von https://example.test/report\n", encoding="utf-8")
    monkeypatch.setattr(execution_target, "_report_path", lambda run_id: report)

    agent_result = {"ok": True, "result": {"response": "fertig"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(query="evidence based topic")

    assert result["ok"] is True
    assert result["result"]["sources"] == ["https://example.test/report"]
    assert result["result"]["evidence"]["citation_count"] == 1


def test_research_target_fails_closed_when_the_report_file_is_absent(tmp_path, monkeypatch):
    executor = build_executor("research:scrape")
    monkeypatch.setattr(
        execution_target, "_report_path", lambda run_id: tmp_path / "never-written.md"
    )

    agent_result = {"ok": True, "result": {"response": "plausibel, aber nichts geschrieben"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(url="https://example.test")

    assert result["ok"] is False
    assert "report file" in result["error"]


def test_research_target_fails_closed_on_a_report_without_citations(tmp_path, monkeypatch):
    executor = build_executor("research:web")
    report = tmp_path / "report.md"
    report.write_text("Kein einziger Beleg.", encoding="utf-8")
    monkeypatch.setattr(execution_target, "_report_path", lambda run_id: report)

    agent_result = {"ok": True, "result": {"response": "fertig"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(query="x")

    assert result["ok"] is False
    assert "citation" in result["error"]


def test_instruction_dictates_an_absolute_output_path(monkeypatch):
    executor = build_executor("research:web")
    instruction = executor._instruction({"query": "x"}, "job_v1_0000000000000000000000000A")
    assert "job_v1_0000000000000000000000000A" in instruction
    assert "file_write" in instruction
```

Der Test `test_to_idea_requires_persisted_queryable_artifact_evidence` entfällt: die
Artefakt-Persistenz liegt jetzt in `research_status` (Task 5), nicht mehr in diesem Pfad.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibemind-os/brain/the_brain && python -m pytest tests/test_research_execution_contract.py -v`
Expected: FAIL — `_report_path` existiert nicht, `_instruction` nimmt nur ein Argument.

- [ ] **Step 3: Write minimal implementation**

In `spaces/research/execution_target.py`:

```python
import pathlib

ARTIFACT_DIR = pathlib.Path.home() / ".openfang" / "research-artifacts"


def _report_path(run_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{run_id}.md"
```

`call()` ersetzen:

```python
    def call(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            payload = self._payload(args, kwargs)
            run_id = _new_run_id()
            report = _report_path(run_id)
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            delegated = self._agent.call(message=self._instruction(payload, run_id))
            if not delegated.get("ok"):
                return {
                    "ok": False,
                    "error": f"research infrastructure unavailable: {delegated.get('error', 'agent call failed')}",
                    "target": self.target,
                }
            # Der Beleg ist die Datei, nicht die Antwort des Agenten. Der
            # claude-code-Treiber liefert tool_calls grundsaetzlich leer
            # (claude_code.rs:579), darum waere jede Pruefung darauf unerfuellbar.
            if not report.is_file():
                return self._unverified("report file was never written")
            text = report.read_text(encoding="utf-8", errors="replace")
            sources = _urls(text)
            if not sources:
                return self._unverified("no citation found in report")
            return {
                "ok": True,
                "result": {
                    "operation": self.operation,
                    "content": text,
                    "sources": sources,
                    "evidence": {
                        "report_path": str(report),
                        "citation_count": len(sources),
                    },
                },
                "target": self.target,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "target": self.target,
            }
```

`_new_run_id` ergänzen (eigenständig, damit dieses Modul nicht von `brief.py` abhängt):

```python
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _new_run_id() -> str:
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "job_v1_" + "".join(reversed(chars))
```

`_instruction` ersetzen:

```python
    def _instruction(self, payload: Dict[str, Any], run_id: str) -> str:
        return (
            f"Execute research.{self.operation} with the available Fetch and web tools. "
            "Do not answer from memory. Every claim needs a source URL.\n"
            f"Write the full report with file_write to EXACTLY this absolute path: "
            f"{_report_path(run_id)}\n"
            f"Input: {payload!r}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vibemind-os/brain/the_brain && python -m pytest tests/test_research_execution_contract.py -v`
Expected: PASS. Die beiden Registry-Tests (`test_research_events_have_brain_execution_targets`, `test_research_operations_use_the_canonical_openfang_researcher`) müssen unverändert grün bleiben.

- [ ] **Step 5: Commit**

```bash
git add spaces/research/execution_target.py brain/the_brain/tests/test_research_execution_contract.py
git commit -m "fix(research): Beleg auf Reportdatei umstellen, tool_calls ist nie befuellt"
```

---

### Task 7: Validierungslauf mit dem Sheerlay-Brief (lokal)

Deliverable ist ein echter Report in einer echten Bubble, nicht ein grüner Test.

**Files:**
- Create: `docs/operations/2026-09-16-research-bubble-validierung.md`

**Interfaces:**
- Consumes: alles aus Task 1-6
- Produces: Beleg, dass der Weg trägt

- [ ] **Step 1: Sheerlay-Bubble anlegen**

```bash
curl -s -X POST "http://127.0.0.1:54321/rest/v1/ideas" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" -H "Prefer: return=representation" \
  -d '{"title":"Sheerlay","description":"B2C-Mobile-App: scannt Pflege- und Zusammensetzungsetiketten per OCR und uebersetzt Materialangaben in Aussagen zum Bedenklichkeitsgrad, inkl. Alternativen (neu, secondhand, Miete). Zielmarkt DACH.","source":"manual"}'
```

Die zurückgegebene `id` notieren, unten als `<BUBBLE>` einsetzen.

- [ ] **Step 2: Vorschau erzeugen und lesen**

`research_start` mit `bubble_id=<BUBBLE>` und dem vollständigen Brief aus **Anhang A**
aufrufen, **ohne** `confirm`. Prüfen: enthält der Brief den Bubble-Kontext,
den absoluten Ausgabepfad, `academic_apa` und `german`? Erwartet: `status=preview`, und
es wurde **kein** Lauf gestartet (keine neue Datei unter `~/.openfang/research-artifacts/`).

- [ ] **Step 3: Lauf starten**

Denselben Aufruf mit `confirm=true` und `depth` bewusst gewählt. **Achtung:** `exhaustive`
bedeutet 50+ Quellen; der Rauchtest kostete 0,054 USD für eine triviale Frage. Für den
ersten echten Lauf `thorough` nehmen. Erwartet: `status=started` mit `job_id`.

- [ ] **Step 4: Bis zum Ergebnis pollen**

`research_status(job_id)` wiederholt aufrufen. Erwartet: mehrfach `pending` mit
steigendem `waited_seconds`, dann einmal `done` mit `citation_count >= 1` und `node_id`.

- [ ] **Step 5: Unabhängig nachprüfen — nicht dem Rückgabewert glauben**

```bash
curl -s -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  "http://127.0.0.1:54321/rest/v1/research_report_artifacts?select=artifact_ref,bubble_id,citation_count,depth&order=created_at.desc&limit=1"
curl -s -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  "http://127.0.0.1:54321/rest/v1/canvas_nodes?select=id,title,linked_idea_id&linked_idea_id=eq.<BUBBLE>"
```

Erwartet: `bubble_id` der Artefaktzeile == `<BUBBLE>`, und der Knoten hängt an derselben
Bubble. Zusätzlich den Report selbst lesen: ist er auf Deutsch, trägt er APA-Quellen mit
Abrufdatum, und benennt er Lücken statt zu schätzen?

- [ ] **Step 6: Ergebnis dokumentieren**

`docs/operations/2026-09-16-research-bubble-validierung.md`: Laufzeit, Kosten (aus der
Antwort des Agenten, falls vorhanden), `citation_count`, `artifact_ref`, Bubble-ID, und
eine ehrliche Bewertung der inhaltlichen Qualität gegen die Anforderungen des Briefs —
inklusive dessen, was **nicht** geliefert wurde.

- [ ] **Step 7: Commit**

```bash
git add docs/operations/2026-09-16-research-bubble-validierung.md
git commit -m "docs(research): Validierungslauf Sheerlay belegt"
```

---

### Task 8: Migration auf der VM anwenden

Erst nach grünem Task 7. Produktionssystem — jeder Schritt wird gemessen.

**Files:**
- Modify: `docs/operations/2026-09-16-research-report-artifacts-apply.md` (VM-Abschnitt ergänzen)

**Interfaces:**
- Consumes: die in Task 2 erprobte Befehlsfolge
- Produces: Tabelle auf `vibemind-offload-1`

- [ ] **Step 1: Freigabe einholen**

Vor dem ersten Befehl den Betreiber ausdrücklich bestätigen lassen, dass die Migration
jetzt auf das Produktionssystem soll. Ohne diese Bestätigung anhalten.

- [ ] **Step 2: Ausgangszustand feststellen**

```bash
curl -s -o /dev/null -w "vm_vorher=%{http_code}\n" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  "http://100.67.177.45:54321/rest/v1/research_report_artifacts?select=id&limit=1"
```

Expected: `404`. Ist es `200`, existiert die Tabelle schon — dann anhalten und die
Migration **nicht** anwenden (sie meldet laut ihrem eigenen Kopf `IF NOT EXISTS`-Erfolg,
ohne zu konvergieren).

- [ ] **Step 3: Migration anwenden**

Datei übertragen und auf der VM anwenden:

```bash
scp supabase/migrations/20260817_research_report_artifacts.sql \
    vibemind-offload-1:/tmp/20260817_research_report_artifacts.sql

ssh vibemind-offload-1 'DB=$(docker ps --filter name=supabase-db --format "{{.Names}}" | head -1); \
  echo "container=$DB"; \
  docker cp /tmp/20260817_research_report_artifacts.sql "$DB:/tmp/"; \
  docker exec "$DB" psql -U supabase_admin -d postgres \
    -f /tmp/20260817_research_report_artifacts.sql; \
  docker exec "$DB" psql -U supabase_admin -d postgres -c "NOTIFY pgrst, '"'"'reload schema'"'"';"'
```

Expected: `BEGIN`, `CREATE TABLE`, fünfmal `CREATE INDEX`, `COMMIT`, `NOTIFY` — kein
`ERROR`. Der ausgegebene Containername wird im Protokoll festgehalten.

**Gemessene Warnung aus Task 2 — hier zählt sie doppelt:** `NOTIFY pgrst, 'reload schema'`
ist **nicht verlässlich**. Lokal blieb die Tabelle danach mit `404` unsichtbar, obwohl das
DDL sauber durchlief, weil PostgREST seinen LISTEN/NOTIFY-Kanal nach einem
Verbindungsabriss nicht neu abonniert hatte — der Container sah dabei gesund aus. Ein
erfolgreiches `COMMIT` ist also **kein** Beleg für Sichtbarkeit. Erst Schritt 4 entscheidet.

- [ ] **Step 3b: Sichtbarkeit erzwingen, falls nötig**

Nur ausführen, wenn Schritt 4 unten `404` liefert. PostgREST lädt das Schema auch auf
`SIGUSR1` neu — ein dokumentierter Mechanismus, kein Eingriff in Daten oder Code:

```bash
ssh vibemind-offload-1 'REST=$(docker ps --filter name=supabase-rest --format "{{.Names}}" | head -1); \
  echo "rest=$REST"; docker kill --signal=SIGUSR1 "$REST"'
```

Danach Schritt 4 wiederholen. Bleibt es auch dann bei `404`, anhalten und berichten —
nicht den Dienst neu starten und nicht am SQL drehen.

- [ ] **Step 4: Nachprüfen**

```bash
curl -s -o /dev/null -w "vm_nachher=%{http_code}\n" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  "http://100.67.177.45:54321/rest/v1/research_report_artifacts?select=id&limit=1"
```

Expected: `200`. Zusätzlich beweisen, dass das Zitat-Tor auch dort scharf ist:

```bash
ssh vibemind-offload-1 'DB=$(docker ps --filter name=supabase-db --format "{{.Names}}" | head -1); \
  docker exec "$DB" psql -U supabase_admin -d postgres -c \
  "INSERT INTO public.research_report_artifacts
   (artifact_ref, job_id, subject_type, subject_ref, name, citation_count, internal_context_used)
   VALUES ('"'"'artifact_v1_00000000000000000000000000'"'"','"'"'job_v1_00000000000000000000000000'"'"',
           '"'"'topic'"'"','"'"'probe'"'"','"'"'probe.md'"'"',0,true);"'
```

Expected: `ERROR: ... violates check constraint "research_report_artifacts_citation_count_check"`.
Geht der Befehl durch, ist etwas falsch — dann die eingefügte Zeile sofort wieder
entfernen und anhalten.

- [ ] **Step 5: Dokumentieren und committen**

```bash
git add docs/operations/2026-09-16-research-report-artifacts-apply.md
git commit -m "docs(research): Artefakt-Migration auf der VM angewendet"
```

---

## Anhang A — Der Validierungs-Brief (Sheerlay), wörtlich

Dieser Text wird in Task 7 unverändert als `brief` übergeben. Er ist der Prüfstein: er
verlangt APA-Quellen mit Abrufdatum, Deutsch, das Benennen von Lücken statt Schätzungen
und ausdrücklich Widerspruch, wo die Daten die Annahmen nicht stützen.

```text
Rolle und Kontext

Du bist Strategieanalyst mit Schwerpunkt Wettbewerbsanalyse und Blue-Ocean-Strategie. Ich baue Sheerlay auf, eine B2C-Mobile-App, die Pflege- und Zusammensetzungsetiketten von Kleidung per Texterkennung (OCR) scannt und die Materialangaben in verständliche Aussagen zum Bedenklichkeitsgrad übersetzt, inklusive Alternativen (neu, secondhand, Miete). Zielmarkt ist zunächst der DACH-Raum.

Aufgabe

Führe eine strukturierte Wettbewerbsanalyse durch und leite daraus eine Blue-Ocean-Positionierung ab.

Direkte Wettbewerber (Textil): Taxome, Wove, FiberCheck.
Benachbarte Scan-Apps (andere Produktkategorien): Yuka, CodeCheck, Good On You, ToxFox, Scan4Chem.
Ergänze eigenständig weitere relevante Anbieter, falls du welche findest, und nenne sie explizit als Ergänzung.

Analysedimensionen

Erfasse für jeden Anbieter, soweit öffentlich verfügbar: Kernversprechen und Zielgruppe; Datengrundlage und Methodik der Bewertung, inklusive Transparenz und Quellen; Umfang und Abdeckung der Datenbank; Erfassungsweg (Barcode, Etikett, manuell); Bewertungslogik und Darstellung des Ergebnisses; Handlungsempfehlungen und Alternativen; Geschäftsmodell und Monetarisierung; Reichweite, Downloads, Bewertungen; regionale Verfügbarkeit und Sprachen; regulatorischer Bezug (etwa Digitaler Produktpass, ESPR); Stand der Aktivität (letztes Update, Anzeichen für Inaktivität).

Nutzerperspektive

Werte App-Store- und Play-Store-Rezensionen sowie Foren- und Social-Media-Beiträge aus. Arbeite heraus, was Nutzerinnen konkret loben, was sie frustriert, und welche Erwartungen unerfüllt bleiben. Unterscheide dabei zwischen Kritik am Produkt und Kritik am Konzept.

Blue-Ocean-Analyse

Erstelle eine Strategy Canvas: Identifiziere die fünf bis acht Wettbewerbsfaktoren, um die die Branche derzeit konkurriert, und bewerte jeden Anbieter darauf (niedrig, mittel, hoch) mit kurzer Begründung. Wende anschließend das ERRC-Raster an und benenne, welche Faktoren Sheerlay eliminieren, reduzieren, steigern und neu schaffen sollte. Prüfe außerdem, welche Nicht-Kunden heute keine dieser Apps nutzen und warum.

Ergebnis

Leite daraus drei bis fünf mögliche USP-Positionierungen für Sheerlay ab. Bewerte jede nach Differenzierungsgrad, Umsetzbarkeit für ein Ein-Personen-Team mit begrenzten Ressourcen, und Verteidigungsfähigkeit gegen Nachahmung. Gib eine begründete Empfehlung, welche Positionierung du für die tragfähigste hältst, und nenne jeweils die größte Schwachstelle.

Anforderungen an die Arbeitsweise

Belege jede Aussage mit Quelle (APA Style) und Abrufdatum. Kennzeichne klar, wo Informationen fehlen oder unsicher sind, statt zu schätzen. Unterscheide zwischen Selbstdarstellung der Anbieter und unabhängig überprüfbaren Angaben. Widersprich mir, wo die Daten meine Annahmen nicht stützen.
```

**Nachgerechnet, damit niemand eine falsche Erwartung hat:** `extract_expected` springt
auf die **erste** passende Überschrift an. Das ist hier `Aufgabe`, nicht `Ergebnis` —
zurück kommt also die Zeile „Führe eine strukturierte Wettbewerbsanalyse durch und leite
daraus eine Blue-Ocean-Positionierung ab.", und wegen der Leerzeile danach bricht die
Sammlung dort ab. Das ist eine brauchbare, aber magere Zusammenfassung des Gewünschten.

Genau deshalb existiert das Vorschau-Gate: in Task 7 Schritt 2 gehört der Abschnitt
„Erwartetes Ergebnis" von Hand um die eigentlichen Liefergegenstände ergänzt (Strategy
Canvas mit 5-8 Faktoren, ERRC-Raster, 3-5 USP-Positionierungen, APA-Quellen mit
Abrufdatum), und der so bearbeitete Text wird in Schritt 3 als `final_brief` übergeben.
Ein Lauf, der ohne diese Ergänzung startet, ist kein Fehler des Mechanismus — aber er
prüft weniger, als er könnte.

## Nach dem Plan

Nicht Teil dieses Plans, bewusst offen gelassen:

- Registrierung des Servers `spaces-research` in der MCP-Konfiguration bzw. als
  `mcp_servers`-Eintrag am Agenten — abhängig davon, welcher Konsument die Werkzeuge
  sehen soll.
- Der UI-Knopf in der Electron-App.
- D3 der Migration: ein eigener Sync-Lauf für Artefakte.
- Ursache des OpenFang-Absturzes vom 2026-09-16 04:05.
