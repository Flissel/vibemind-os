# Rowboat-Zentrale Teil C – Bedeutungssuche

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rowboat auf dem Mini-PC findet Wissen nach Bedeutung („Kunden, die Interesse an Altersvorsorge hatten“) über ein MCP-Werkzeug `wissen_suchen`. Die Embeddings rechnet Ollama am PC, der Index liegt auf der VM. Ist der PC aus, liefert dasselbe Werkzeug Stichwort-Treffer und sagt das dazu.

**Architecture:** Ein neuer Dienst `rowboat-wissen` (Python, ein Prozess) läuft im Compose `~/rowboat-headless` auf `offload-vm`, im Host-Netz und **nur auf `127.0.0.1:3240`**. Er hat zwei Teile. (1) Ein Indexer-Thread prüft alle 10 min `knowledge/` (nur lesend eingehängt), zerlegt geänderte Notizen in Abschnitte und schickt sie an Ollama am PC (`http://100.84.127.84:11435`, das ist das schon bestehende `tailscale serve` auf `127.0.0.1:11434`). Ist der PC nicht erreichbar, bleiben die Dateien `offen` und kommen in der nächsten Runde dran. (2) Ein Streamable-HTTP-MCP-Server stellt `wissen_suchen(frage, anzahl)` und `wissen_stand()` bereit. Für die Frage holt er einen Vektor vom PC und rechnet den Kosinus per numpy über alle Abschnitte. Ohne PC sucht er per Stichwort über die Dateien. Rowboat bekommt den Server als `http`-Eintrag in `config/mcp.json` und eine Disk-Skill `skills/wissen-suchen/SKILL.md`.

**Tech Stack:** Python 3.11 (Container `python:3.11-slim`), `mcp==1.9.4` (FastMCP, streamable-http), `numpy==2.1.3`, SQLite (stdlib), Ollama 0.17.1 am PC mit `qwen3-embedding:0.6b` (1024 Dimensionen), pytest, Docker Compose auf der VM.

**Spec:** `docs/superpowers/specs/2026-10-24-rowboat-geschaeftsdaten-zentrale-design.md` (Ziel 5, Abschnitt 8 „Index-Technik“, Erfolg Zeile 2, Anti-Ziele „Modell auf dem Mini-PC“ und „Telegram-Antwort hängt still“). Vorgänger: `docs/superpowers/plans/2026-10-24-rowboat-zentrale-teil-a-mini-pc.md` (Compose, Firewall, Wissens-Spiegel, Shim).

## Entscheidungen (mit Begründung)

| Frage | Entscheid | Warum |
|---|---|---|
| Index-Speicher | SQLite-Datei `~/rowboat-headless/wissen-index/wissen.sqlite`. Vektoren als float32-BLOB, Suche per numpy über alle Abschnitte (Brute Force) | Etwa 1.200 Notizen ergeben höchstens rund 5.000 Abschnitte, also 5.000 × 1024 × 4 B = 20 MB. Eine Abfrage dauert damit Millisekunden. sqlite-vec bräuchte eine native Erweiterung. Qdrant ist auf der VM nur in learning-claw vorhanden, und am PC gab es schon Ärger mit Volumes (Memory `project_qdrant_volume_conflict`). Beides ist für diese Größe unnötig |
| Embedding-Modell | `qwen3-embedding:0.6b`, 1024 Dimensionen, per Env umstellbar | Liegt am PC schon vor (639 MB) und kann Deutsch. Der Brain nutzt dasselbe Modell (Rolle `brain_kg`). `4b` (2,5 GB) bzw. `8b` würden den PC zu stark belasten. Wechselt das Modell oder die Dimension, baut der Index automatisch neu auf |
| Zerlegung | Abschnitte nach Überschriften `#`–`###`; zu lange Abschnitte werden an Absätzen geteilt, höchstens **2.400 Zeichen** (≈ 800 Token Deutsch); Stücke unter 200 Zeichen werden mit dem nächsten verschmolzen; jedem Stück wird `Titel / Ordner / Abschnitt` vorangestellt | Der Ordner trägt die Kategorie der Steckbriefe (`Kontakte/<Kategorie>/`). Der Titel macht kurze Steckbriefe auffindbar |
| Wo der Indexer läuft | Als Thread im Dienst `rowboat-wissen` auf der VM. Er fragt Ollama am PC über das Tailnet. Fehlt die Verbindung, bleibt die Datei `offen`, es gibt keinen Fehlversuch, und die nächste Runde kommt nach 10 min | Es gibt nur einen Schreiber auf der SQLite-Datei, braucht keinen Cron, und der PC muss nichts anstoßen |
| Ollama-Zugang | Vorhandenes `tailscale serve --tcp 11435 → 127.0.0.1:11434` am PC (seit 22.09., am 26.10. von der VM aus gemessen: `/api/version` → `0.17.1`) | Es gibt keine neue Bindung und keine Portproxy-Regel, und es läuft ohne Admin. Ein Binden an die Tailnet-IP würde die Loopback-Nutzer am PC (Brain-Dienste über `host.docker.internal:11434`) abschneiden, und Ollama würde scheitern, wenn Tailscale noch nicht läuft |
| Anfrage-Parameter | `/api/embed` mit `options.num_ctx=2048`, `truncate=true`; `keep_alive` beim Indexieren `"60s"`, bei der Frage `"5m"` | Am 26.10. gemessen: Das Modell lag mit `context_length 32768` und **6,25 GB** im Speicher, weil die Ollama-App die Kontextlänge auf 131072 setzt. Mit 2048 liegt der Bedarf bei ≈ 1 GB |
| Abfrageweg | Streamable-HTTP-MCP auf `127.0.0.1:3240/mcp`, Werkzeuge `wissen_suchen(frage: str, anzahl: int = 8)` und `wissen_stand()` | Rowboat spricht `http` (`shared/src/mcp.ts`, StreamableHTTP mit SSE als Ausweichweg). Der Dienst ist nur über Loopback erreichbar, daher keine Firewall-Regel nötig. Port 3240 ist frei (`ss -ltn` am 26.10.) und liegt außerhalb von 3220–3229 |
| Rückfall | Ist der PC nicht erreichbar (Verbindungsaufbau > 3 s, Gesamtzeit > 15 s, 5xx, Verbindung zurückgesetzt), läuft Stichwortsuche über die Dateien. Das Ergebnis trägt `modus: "stichwort"` und einen festen Hinweis. Offene Dateien kommen auch bei Bedeutungssuche per Stichwort dazu | Anti-Ziel „hängt still“: Das Werkzeug antwortet in jedem Fall in ≤ 20 s |
| Rowboat dazu bringen | Disk-Skill `wissen-suchen` (`tools: [listMcpTools, executeMcpTool]`) in `/home/debian/rowboat-data/skills/` | Rowboat liest Disk-Skills ohne Änderung am Fork (`runtime/assembly/skills/disk-loader.ts`). Die Grundanweisung schickt das Modell zuerst zu `file-grep`. Ob die Skill dagegen ankommt, zeigt die Abnahme. Wenn nicht, entscheidet der Betreiber über eine Ergänzung im Fork (siehe unten) |

## Global Constraints

- **Auf dem Mini-PC läuft kein Modell.** Das Image `rowboat-wissen` enthält weder torch, onnx, sentence-transformers, llama.cpp noch ollama. Vektoren kommen nur per HTTP vom PC.
- Rowboat-Wissen nur **lesen**: `knowledge/` wird `ro` eingehängt; der Dienst schreibt nur in `~/rowboat-headless/wissen-index/`.
- Nie indexiert: `_konflikte/`, `.spiegel-eingang/`, `.git/`, versteckte Ordner, alles außer `*.md`.
- Keine Geheimnisse: Der Dienst braucht keinen Schlüssel. Ollama-URL und Modell stehen als Klartext-Env im Compose.
- Hinweistexte wörtlich:
  - `HINWEIS_STICHWORT = "Bedeutungssuche gerade nicht möglich (PC bzw. Ollama nicht erreichbar) – das sind Treffer der Stichwortsuche."`
  - `HINWEIS_OFFEN = "{n} Notizen sind noch nicht eingebettet und nur per Stichwort gefunden."`
- Pfade in Ergebnissen beginnen mit `knowledge/`, damit Rowboat sie direkt mit `file-readText` öffnen kann.
- Tests: `python -m pytest spaces/rowboat/tests -q --basetemp C:/Temp/pt-rb` (basetemp auf C:, Memory `feedback_pytest_basetemp_auf_c`). Kein Test ruft echtes Ollama oder ssh auf.
- Deploy auf der VM (Image bauen, Container starten, `mcp.json`, Skill kopieren) und alle Änderungen am PC (Ollama-Einstellungen, Firewall, Tailscale-ACL) **nur mit Betreiber-OK**. Die Code-Tasks 1–7 brauchen kein OK.
- Vor Task 8: Claim im `WORKBOARD.md` (äußeres Repo, nur die eigene Zeile stagen, Memory `feedback_board_dateien_nur_eigene_zeile_stagen`).

## Review Focus

1. Der PC ist aus oder das Tailnet weg, während Telegram fragt. Dann kommt in ≤ 20 s eine Stichwortantwort mit `HINWEIS_STICHWORT` statt eines Timeouts. Zweiter Fall: Tailscale läuft, aber Ollama nicht (tailscaled nimmt die Verbindung an und setzt sie zurück). Das wird genauso behandelt (Task 2, 5).
2. Der Mount `knowledge/` ist leer oder fehlt, etwa wenn die VM-Platte nicht da war. Dann leert der Index sich **nicht**. Fehlen mehr als 50 % der indexierten Dateien, wird nicht gelöscht, und der Lauf meldet das im Log (Task 4).
3. Ollama lehnt einen Text ab (400) oder liefert eine falsche Dimension. Bei 400 erhält nur diese Datei einen Fehlversuch und wird nach 3 Versuchen `fehler`. Die Runde läuft für die übrigen Dateien weiter, und eine falsche Dimension führt zu keinem kaputten Vektor im Index (Task 2, 4).
4. Eine Notiz ändert sich, während sie eingebettet wird. Der Index speichert den Hash der gelesenen Bytes. Die nächste Prüfung erkennt die Änderung und bettet die Notiz neu ein. Es bleiben keine Abschnitte zweier Fassungen gemischt im Index (Task 3, 4).
5. Dateinamen mit Umlauten und Leerzeichen sowie Notizen mit YAML-Kopf oder Codeblöcken, in denen `#` vorkommt, werden richtig zerlegt und gefunden. Der Pfad im Ergebnis lässt sich öffnen (Task 1, 5).

---

### Task 1: Zerlegen in Abschnitte

**Files:**
- Create: `spaces/rowboat/wissen_suche/__init__.py`, `spaces/rowboat/wissen_suche/zerlegen.py`
- Test: `spaces/rowboat/tests/test_wissen_zerlegen.py`

**Interfaces:**
- Produces:
  - `MAX_ZEICHEN = 2400`, `MIN_ZEICHEN = 200`
  - `Abschnitt = NamedTuple(nr: int, ueberschrift: str, text: str)`; `text` beginnt mit `f"{titel} / {ordner} / {ueberschrift}\n\n"` (`ordner` = Elternpfad relativ zu `knowledge/`, leer bei oberster Ebene)
  - `zerlegen(rel_pfad: str, inhalt: str) -> list[Abschnitt]`

- [ ] **Step 1: Tests schreiben**

```python
def test_zerlegt_nach_ueberschriften():
    a = zerlegen("Notes/Plan.md", "# A\n" + "x " * 300 + "\n## B\n" + "y " * 300)
    assert [s.ueberschrift for s in a] == ["A", "A > B"]
    assert a[0].text.startswith("Plan / Notes / A\n\n")

def test_lange_abschnitte_an_absaetzen_geteilt():
    absatz = ("Satz. " * 80).strip()                      # ~480 Zeichen
    a = zerlegen("x.md", "# H\n\n" + "\n\n".join([absatz] * 12))
    assert len(a) >= 3 and all(len(s.text) <= MAX_ZEICHEN + 120 for s in a)

def test_kleine_abschnitte_verschmolzen():
    a = zerlegen("x.md", "# A\nkurz\n# B\nauch kurz\n# C\n" + "z " * 200)
    assert len(a) == 1

def test_raute_im_codeblock_ist_keine_ueberschrift():
    a = zerlegen("x.md", "# A\n```\n# kein Titel\n```\n" + "t " * 150)
    assert [s.ueberschrift for s in a] == ["A"]

def test_yaml_kopf_bleibt_im_ersten_abschnitt():
    a = zerlegen("companys/VibeMind/Kontakte/Altersvorsorge/Max Muster.md",
                 "---\nschlagwoerter: [bav, person]\n---\n# Max\n" + "m " * 150)
    assert "schlagwoerter" in a[0].text and "Max Muster / companys/VibeMind/Kontakte/Altersvorsorge" in a[0].text

def test_leere_datei_ergibt_nichts():
    assert zerlegen("x.md", "  \n") == []

def test_bom_und_crlf():
    assert zerlegen("Ü b.md", "\ufeff# Ä\r\n" + "ö " * 150)[0].ueberschrift == "Ä"
```

- [ ] **Step 2: Laufen lassen, rot**: `python -m pytest spaces/rowboat/tests/test_wissen_zerlegen.py -q --basetemp C:/Temp/pt-rb` → FAIL (Modul fehlt)
- [ ] **Step 3: `zerlegen` umsetzen.** Zeilenweise Zustandsmaschine mit Codeblock-Schalter. `ueberschrift` ist der Pfad der Überschriften `#`–`###` mit ` > ` verbunden. Text vor der ersten Überschrift läuft unter dem Dateititel. Ein einzelner Absatz über `MAX_ZEICHEN` wird hart an der letzten Satzgrenze `". "` vor der Grenze geteilt. Höchstens 200 Abschnitte je Datei; der Rest wird verworfen, und `zerlegen` loggt eine Warnung.
- [ ] **Step 4: grün** (gleicher Befehl)
- [ ] **Step 5: Commit** `feat(rowboat): Bedeutungssuche zerlegt Notizen in Abschnitte`

### Task 2: Einbetter (Ollama am PC, Fake für Tests)

**Files:**
- Create: `spaces/rowboat/wissen_suche/einbetten.py`
- Test: `spaces/rowboat/tests/test_wissen_einbetten.py`

**Interfaces:**
- Produces:
  - `class PcNichtErreichbar(Exception)` für Verbindungsfehler, Zeitüberschreitung, Reset, HTTP 5xx, HTTP 404 (Modell fehlt) und falsche Dimension
  - `class EingabeAbgelehnt(Exception)` für HTTP 400 auf einen Text
  - `class Einbetter(Protocol)`: `modell: str`, `dim: int`, `dokumente(texte: list[str]) -> list[list[float]]`, `frage(text: str) -> list[float]`
  - `class OllamaEinbetter`: `__init__(url: str, modell: str, dim: int, num_ctx: int = 2048, verbinden_s: float = 3.0, gesamt_s: float = 15.0, oeffner=urllib.request.urlopen)`. Er schickt `POST {url}/api/embed` mit `{"model", "input": [...], "truncate": true, "options": {"num_ctx": num_ctx}, "keep_alive": "60s"|"5m"}` und gibt L2-normierte Vektoren zurück.
  - `FRAGE_PRAEFIX = "Instruct: Given a question, retrieve knowledge-base notes that answer it\nQuery: "` (nur für `frage`, Dokumente ohne Präfix; Konvention von qwen3-embedding)
  - `class FakeEinbetter` (für Tests, hier statt in `tests/`, damit Task 4–6 ihn teilen): 64 Dimensionen, Bag-of-Words über einen Begriffsplan `{"altersvorsorge": "rente", "rente": "rente", "pension": "rente", "rentenplanung": "rente", "hund": "tier", "katze": "tier"}`, sonst Hash des Worts; `erreichbar: bool` schaltet `PcNichtErreichbar`; zählt Aufrufe in `aufrufe`

- [ ] **Step 1: Tests** (`oeffner` wird gefälscht, kein Netz):
  - `test_sendet_num_ctx_truncate_keep_alive`: Der Körper der Anfrage enthält `options.num_ctx == 2048`, `truncate is True` und `keep_alive == "60s"`, bei `frage` dagegen `"5m"` und den Präfix.
  - `test_vektoren_normiert`: Die Antwort `[[3.0, 4.0, ...]]` mit `dim=2` ergibt `[0.6, 0.8]`.
  - `test_zeitueberschreitung_ist_pc_nicht_erreichbar`: `socket.timeout` und `URLError` führen zu `PcNichtErreichbar`.
  - `test_reset_ist_pc_nicht_erreichbar`: `ConnectionResetError` sowie `RemoteDisconnected` führen zu `PcNichtErreichbar`.
  - `test_5xx_und_404_sind_pc_nicht_erreichbar`; `test_400_ist_eingabe_abgelehnt`.
  - `test_falsche_dimension_ist_pc_nicht_erreichbar`: Die Antwort hat 512 Werte bei `dim=1024`.
  - `test_fake_findet_synonyme`: Der Kosinus zwischen `frage("Altersvorsorge")` und `dokumente(["Rentenplanung besprochen"])` ist größer als zu `["Hund gekauft"]`.
- [ ] **Step 2: rot**, **Step 3: umsetzen** (nur `urllib`, kein `requests`), **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): Einbetter fragt Ollama am PC, faellt sauber aus`

### Task 3: Wissens-Index (SQLite + Kosinus)

**Files:**
- Create: `spaces/rowboat/wissen_suche/index.py`
- Test: `spaces/rowboat/tests/test_wissen_index.py`

**Interfaces:**
- Consumes: `Abschnitt` (Task 1)
- Produces:
  - Schema: `meta(schluessel PRIMARY KEY, wert)` mit `modell` und `dim`. Außerdem `dateien(pfad PRIMARY KEY, sha256, groesse, mtime, status CHECK IN ('offen','fertig','fehler'), versuche INT, fehler TEXT, aktualisiert TEXT)` und `abschnitte(pfad, nr, ueberschrift, text, vektor BLOB, PRIMARY KEY(pfad, nr))`. WAL-Modus.
  - `DateiStand = NamedTuple(sha256: str, groesse: int, mtime: float, status: str, versuche: int)`
  - `Treffer = NamedTuple(pfad: str, ueberschrift: str, text: str, punkte: float)`
  - `Stand = NamedTuple(fertig: int, offen: int, fehler: int, abschnitte: int, modell: str)`
  - `class WissensIndex(pfad: Path, modell: str, dim: int)`. Weichen `modell` oder `dim` von `meta` ab, werden alle `abschnitte` gelöscht, alle Dateien auf `offen` gesetzt und `meta` neu geschrieben.
    - `dateien() -> dict[str, DateiStand]`
    - `als_offen(pfad: str, sha256: str, groesse: int, mtime: float) -> None` (setzt `versuche=0`)
    - `entfernen(pfad: str) -> None`
    - `offene(limit: int) -> list[str]` (nur `status='offen'`, älteste zuerst)
    - `ersetzen(pfad: str, sha256: str, abschnitte: list[Abschnitt], vektoren: list[list[float]]) -> None` ersetzt die alten Abschnitte in **einer** Transaktion und setzt `status='fertig'`. Weicht `sha256` vom gespeicherten Wert ab, tut es nichts und gibt eine Warnung aus, denn die Datei hat sich inzwischen geändert.
    - `fehlschlag(pfad: str, fehler: str, max_versuche: int = 3) -> None`
    - `suchen(vektor: list[float], anzahl: int) -> list[Treffer]` gibt je Datei nur den besten Abschnitt zurück, absteigend nach `punkte`. Die Matrix wird im Speicher gehalten und nach `ersetzen`/`entfernen` neu geladen (Generationszähler).
    - `stand() -> Stand`
    - `merken(schluessel: str, wert: str) -> None`, `lesen(schluessel: str) -> str | None` (Tabelle `meta`; Task 4 schreibt `letzte_runde`)

- [ ] **Step 1: Tests** (tmp-SQLite, Vektoren von Hand):
  - `test_ersetzen_ist_atomar_und_ersetzt_alte_abschnitte`: Zuerst 3 Abschnitte, dann 1. Am Ende ist genau 1 Abschnitt da, Status `fertig`.
  - `test_ersetzen_mit_veraltetem_hash_tut_nichts`
  - `test_suchen_ein_treffer_je_datei_sortiert`: Datei A hat zwei Abschnitte nahe an der Frage, Datei B einen ferneren. Ergebnis ist `[A, B]`, und A erscheint nur einmal.
  - `test_modellwechsel_setzt_alles_offen`: Dieselbe Datei wird mit `modell="x"` neu geöffnet. Danach gilt `stand().abschnitte == 0`, und alle Dateien sind `offen`.
  - `test_fehlschlag_nach_drei_versuchen_fehler`
  - `test_suchen_nach_entfernen_findet_datei_nicht_mehr`
  - `test_leerer_index_liefert_leere_liste`
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): Wissens-Index in SQLite mit Kosinus-Suche`

### Task 4: Indexer (Abgleich mit `knowledge/`, Runden, Wiederholung)

**Files:**
- Create: `spaces/rowboat/wissen_suche/indexer.py`
- Test: `spaces/rowboat/tests/test_wissen_indexer.py`

**Interfaces:**
- Consumes: Task 1–3.
- Produces:
  - `AUSGESCHLOSSEN = ("_konflikte/", ".spiegel-eingang/", ".git/")`
  - `bestand_scannen(wurzel: Path) -> dict[str, tuple[int, float]]` (POSIX-Pfad relativ, NFC, nur `*.md`, keine versteckten Ordner, keine Symlinks außerhalb der Wurzel; Wert = `(groesse, mtime)`)
  - `Abgleich = NamedTuple(neu_oder_geaendert: int, entfernt: int, loeschen_ausgesetzt: bool)`
  - `abgleichen(index: WissensIndex, wurzel: Path) -> Abgleich`. Bei gleicher Größe und gleichem mtime wird die Datei übersprungen, sonst `sha256` gebildet. Fehlen mehr als 50 % der indexierten Dateien (oder ist der Bestand leer, während der Index nicht leer ist), wird nicht gelöscht: `loeschen_ausgesetzt=True` mit Warnung im Log.
  - `Runde = NamedTuple(eingebettet: int, offen: int, pc_erreichbar: bool)`
  - `einbetten_runde(index, wurzel: Path, einbetter: Einbetter, max_abschnitte: int = 2000, stapel: int = 32) -> Runde`. Die Runde liest jede offene Datei, bildet `sha256` aus den gelesenen Bytes, zerlegt sie und bettet in Stapeln ein; danach folgt `ersetzen`. `PcNichtErreichbar` beendet die Runde sofort, ohne `fehlschlag`. `EingabeAbgelehnt` führt zu `fehlschlag(pfad)`, danach geht es mit der nächsten Datei weiter. Eine leere Datei wird `ersetzen(pfad, sha, [], [])`.
  - `class Indexer(threading.Thread)`: `__init__(index, wurzel, einbetter, intervall_s: int = 600)`, `daemon=True`. Die erste Runde läuft sofort; jede Runde besteht aus `abgleichen` und danach `einbetten_runde`, am Ende `index.merken("letzte_runde", <ISO-Zeit>)`. Ausnahmen werden geloggt, und der Thread läuft weiter.

- [ ] **Step 1: Tests** (tmp-Ordner + `FakeEinbetter`):
  - `test_neue_und_geaenderte_dateien_werden_offen_dann_fertig`
  - `test_ausgeschlossene_ordner_und_nicht_md`: `_konflikte/x.md`, `.git/y.md` und `a.txt` kommen nicht in den Index.
  - `test_pc_aus_bleibt_offen_ohne_fehlversuch`: Mit `erreichbar=False` gilt `Runde.pc_erreichbar is False`, alles bleibt `offen` mit `versuche == 0`. Danach wird `erreichbar=True` gesetzt, und die nächste Runde macht alles `fertig`.
  - `test_eingabe_abgelehnt_trifft_nur_diese_datei`
  - `test_leerer_ordner_loescht_index_nicht`: 10 Dateien werden indexiert, dann ist der Ordner leer. Ergebnis: `loeschen_ausgesetzt is True`, `stand().fertig == 10`.
  - `test_geloeschte_datei_verschwindet`: Von 10 Dateien wird eine gelöscht, danach ist `fertig == 9`.
  - `test_aenderung_waehrend_einbetten`: Das Fake ändert die Datei im ersten `dokumente`-Aufruf. Nach der Runde ist die Datei nicht `fertig` mit dem alten Inhalt, und nach dem nächsten `abgleichen` wieder `offen`.
  - `test_max_abschnitte_begrenzt_runde`: Bei 50 Dateien und `max_abschnitte=10` gilt `eingebettet <= 10` und `offen > 0`.
  - `test_umlaute_im_pfad`: `Agent Notes/Über (neu).md` wird gefunden und indexiert.
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): Indexer bettet geaenderte Notizen ein, wartet bei PC aus`

### Task 5: Suche (Bedeutung, Stichwort-Rückfall, Stand)

**Files:**
- Create: `spaces/rowboat/wissen_suche/suche.py`
- Test: `spaces/rowboat/tests/test_wissen_suche.py`

**Interfaces:**
- Consumes: Task 2–4.
- Produces:
  - `HINWEIS_STICHWORT`, `HINWEIS_OFFEN` (wörtlich aus Global Constraints)
  - `stichwort_suchen(wurzel: Path, frage: str, anzahl: int, nur: set[str] | None = None) -> list[dict]`. Begriffe sind die Wörter mit mindestens 3 Zeichen ohne kleine deutsch/englische Stoppwortliste. Umlaute werden beidseitig gefaltet (`ä↔ae`, `ö↔oe`, `ü↔ue`, `ß↔ss`), verglichen wird als Teilstring in Kleinschreibung. Punkte = 3 × Begriffe im Dateinamen + Summe aus `min(Vorkommen, 5)`. Der Auszug ist der Absatz mit den meisten Begriffen (≤ 300 Zeichen). Ausschlüsse wie in Task 4.
  - `wissen_suchen(frage: str, anzahl: int, index: WissensIndex, wurzel: Path, einbetter: Einbetter) -> dict`. `anzahl` wird auf 1..20 begrenzt; eine leere `frage` gibt `{"fehler": "frage fehlt"}` zurück. Das Ergebnis hat die Form:
    `{"modus": "bedeutung"|"stichwort", "hinweis": str|None, "treffer": [{"pfad": "knowledge/…", "titel", "abschnitt", "auszug", "punkte", "quelle": "bedeutung"|"stichwort"}], "stand": {"fertig", "offen", "fehler"}}`
    - Bedeutungsmodus: Index-Treffer, danach höchstens 3 Stichwort-Treffer aus `offen`-Dateien (`nur=`) mit `quelle="stichwort"`. `hinweis = HINWEIS_OFFEN.format(n=…)`, sobald es offene Dateien gibt.
    - Bei `PcNichtErreichbar` oder einem leeren Index: `modus="stichwort"`, `hinweis=HINWEIS_STICHWORT`.
  - `wissen_stand(index, einbetter_url: str) -> dict` liefert `{"fertig", "offen", "fehler", "abschnitte", "modell", "pc_erreichbar": bool, "letzte_runde": str|None}`. `pc_erreichbar` prüft `GET /api/version` mit einer Zeitgrenze von 2 s.

- [ ] **Step 1: Tests** (`FakeEinbetter`, tmp-Wissen mit `companys/VibeMind/Kontakte/Altersvorsorge/Anna.md` „Rentenplanung besprochen“ und `Notes/Hund.md`):
  - `test_bedeutung_findet_ohne_genaues_wort`: Die Frage „Kunden mit Interesse an Altersvorsorge“ liefert als ersten Treffer `knowledge/companys/VibeMind/Kontakte/Altersvorsorge/Anna.md`, `modus == "bedeutung"`. (Spec 9, Zeile 2)
  - `test_pc_aus_stichwort_mit_hinweis`: Bei `erreichbar=False` gilt `modus == "stichwort"` und `hinweis == HINWEIS_STICHWORT`. Die Frage „Rentenplanung“ findet Anna.
  - `test_pc_aus_antwortet_schnell`: Eine Fake-Verbindung schläft 30 s, der echte `OllamaEinbetter` hat `gesamt_s=0.5`. `wissen_suchen` kommt in unter 2 s zurück, mit `modus "stichwort"`.
  - `test_offene_dateien_per_stichwort_ergaenzt`: Eine neue Datei ist noch nicht eingebettet. Sie erscheint mit `quelle "stichwort"`, und `hinweis` enthält „1 Notizen“.
  - `test_umlaut_faltung`: „Uebergabe“ findet „Übergabe“.
  - `test_anzahl_begrenzt_und_leere_frage`
  - `test_pfade_beginnen_mit_knowledge`
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): wissen_suchen mit Stichwort-Rueckfall und Hinweis`

### Task 6: MCP-Server, Container, Compose

**Files:**
- Create: `spaces/rowboat/wissen_suche/server.py`, `spaces/rowboat/deploy/headless/wissen/Dockerfile`, `spaces/rowboat/deploy/headless/wissen/requirements.txt`
- Modify: `spaces/rowboat/deploy/headless/compose.yml` (Dienst `rowboat-wissen`), `spaces/rowboat/deploy/headless/bauen.ps1` (kopiert `wissen_suche/` und `wissen/` nach `~/rowboat-headless/wissen/` und legt `~/rowboat-headless/wissen-index` mit Modus 0700 an), `spaces/rowboat/deploy/headless/README.md` (Abschnitt „Bedeutungssuche“)
- Modify (äußeres Repo): `vibemind.config.json` – Port `rowboat-wissen: 3240`
- Test: `spaces/rowboat/tests/test_wissen_server.py`, `spaces/rowboat/tests/test_headless_deploy.py`

**Interfaces:**
- Consumes: Task 3–5.
- Produces:
  - `einstellungen_laden(env: Mapping[str, str]) -> Einstellungen`. Die Felder heißen `wissen_ordner` (Vorgabe `/wissen`), `index_datei` (`/index/wissen.sqlite`), `ollama_url` (`http://100.84.127.84:11435`), `modell` (`qwen3-embedding:0.6b`), `dim` (`1024`), `num_ctx` (`2048`), `intervall_s` (`600`) und `port` (`3240`). Die Env-Namen sind `WISSEN_ORDNER`, `WISSEN_INDEX`, `OLLAMA_URL`, `EMBED_MODELL`, `EMBED_DIM`, `EMBED_NUM_CTX`, `INDEX_INTERVALL_S` und `WISSEN_PORT`. Jeder Wert wird mit `.strip()` gelesen (Memory `feedback_env_crlf_schluessel_leck`).
  - `baue_server(e: Einstellungen, einbetter: Einbetter | None = None) -> FastMCP` mit dem Namen `"wissen"`, `stateless_http=True`, `json_response=True` und Host `127.0.0.1`. Er stellt die Werkzeuge `wissen_suchen(frage: str, anzahl: int = 8) -> str` (JSON aus Task 5) und `wissen_stand() -> str` bereit, dazu die Route `GET /gesund` mit 200 und `{"ok": true}`.
  - `main()` startet den `Indexer` und danach `mcp.run(transport="streamable-http")` auf dem Pfad `/mcp`.
  - Werkzeugbeschreibung (wörtlich): `"Durchsucht das Rowboat-Wissen (Notizen, Kontakt-Steckbriefe, Newsletter, Wochenbild) nach BEDEUTUNG, auch ohne exakte Wörter. Liefert Pfade unter knowledge/, die mit file-readText geöffnet werden können. Ist der PC aus, kommen Stichwort-Treffer mit Hinweis."`
  - Compose-Dienst `rowboat-wissen`: `build: ./wissen`, `network_mode: host`, `restart: unless-stopped`, `mem_limit: 512m`, `user: "1000:1000"`. Volumes: `/home/debian/rowboat-data/knowledge:/wissen:ro` und `./wissen-index:/index`, beide mit `create_host_path: false`. Env wie oben, Healthcheck `GET http://127.0.0.1:3240/gesund`, **kein** `ports:`, kein `depends_on` auf den Rowboat-Server.
  - Dockerfile: `FROM python:3.11-slim`, `pip install --no-cache-dir -r requirements.txt` (`mcp==1.9.4`, `numpy==2.1.3`), `COPY wissen_suche/ /opt/wissen_suche/`, `CMD ["python", "-m", "wissen_suche.server"]`.

- [ ] **Step 1: Tests**
  - `test_werkzeuge_und_schemas` (`test_wissen_server.py`): `baue_server` mit `FakeEinbetter` und tmp-Pfaden. `list_tools()` liefert genau `{"wissen_suchen", "wissen_stand"}`; `wissen_suchen` verlangt `frage` und hat `anzahl` als Integer mit Vorgabe 8.
  - `test_aufruf_liefert_json`: `call_tool("wissen_suchen", {"frage": "Rente"})` liefert Text, der als JSON gelesen werden kann und den Schlüssel `modus` enthält.
  - `test_einstellungen_strip_und_vorgaben`: Bei `OLLAMA_URL="http://x:1\r"` ergibt sich `http://x:1`.
  - `test_wissen_dienst_vertrag` (`test_headless_deploy.py`): Im Compose hat `rowboat-wissen` `network_mode == "host"`, kein `ports`, `knowledge` ist mit `:ro` eingehängt, `WISSEN_PORT == "3240"`, `create_host_path` ist überall `false` und der Healthcheck zeigt auf `/gesund`.
  - `test_kein_modell_im_image`: `requirements.txt` und `Dockerfile` enthalten keines von `torch|onnx|sentence-transformers|transformers|llama|ollama/ollama|vllm`.
  - `test_server_bindet_nur_loopback`: In `server.py` taucht `0.0.0.0` nicht auf, `127.0.0.1` dagegen schon.
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün** (`python -m pytest spaces/rowboat/tests -q --basetemp C:/Temp/pt-rb`)
- [ ] **Step 5: Portprüfung**: `python scripts/port_kollisionen.py` (äußeres Repo) → Exit 0
- [ ] **Step 6: Bauprobe am PC**: `docker build -t rowboat-wissen:probe -f spaces/rowboat/deploy/headless/wissen/Dockerfile <Kontext wie bauen.ps1>`. Danach `docker run --rm -e WISSEN_ORDNER=/tmp -e OLLAMA_URL=http://127.0.0.1:1 rowboat-wissen:probe python -c "import wissen_suche.server"` → Exit 0
- [ ] **Step 7: Commit** (vibemind-os) `feat(rowboat): MCP-Dienst rowboat-wissen fuer den Mini-PC`; (äußeres Repo) `chore(ports): rowboat-wissen 3240`

### Task 7: Rowboat-Anbindung (mcp.json-Eintrag, Skill)

**Files:**
- Create: `spaces/rowboat/deploy/headless/skills/wissen-suchen/SKILL.md`, `spaces/rowboat/scripts/wissen_mcp_eintragen.py`
- Modify: `spaces/rowboat/deploy/headless/bauen.ps1` (kopiert `skills/wissen-suchen/` nach `~/rowboat-data/skills/wissen-suchen/`, überschreibt nur diesen Ordner)
- Test: `spaces/rowboat/tests/test_wissen_anbindung.py`

**Interfaces:**
- Produces:
  - `EINTRAG = {"type": "http", "url": "http://127.0.0.1:3240/mcp"}`; `eintragen(config: dict) -> dict` setzt `mcpServers["wissen"] = EINTRAG`; andere Server bleiben unverändert (heute nur `exa`)
  - CLI `python3 wissen_mcp_eintragen.py <pfad zu mcp.json>` (wird per `ssh offload-vm 'python3 - …' < skript` ausgeführt, nur stdlib). Es schreibt zuerst `mcp.json.bak-vor-wissen` (nur wenn noch keine da ist), dann atomar per `os.replace`, und gibt die Servernamen aus.
  - SKILL.md-Frontmatter: `name: wissen-suchen`; `description: "Bedeutungssuche im Rowboat-Wissen – IMMER zuerst nutzen, wenn nach Kunden, Kontakten, Themen, Interessen, Notizen oder 'was wissen wir über …' gefragt wird, besonders wenn die genauen Wörter unklar sind."`; `tools: [listMcpTools, executeMcpTool]`.
  - Inhalt der Skill. Erstens: Der Aufruf lautet `executeMcpTool({serverName: "wissen", toolName: "wissen_suchen", arguments: {frage, anzahl}})`. Zweitens: Danach die besten Pfade mit `file-readText` lesen. Drittens: Ist `hinweis` gesetzt, steht er **wörtlich** in der Antwort an den Betreiber (auch per Telegram). Viertens: Bei `modus "stichwort"` nicht behaupten, es gebe nichts weiter. Fünftens: `file-grep` nur für exakte Namen und Zeichenfolgen. Sechstens: Pfade mit `_archiv/` sind archivierte Kontakte.

- [ ] **Step 1: Tests**: `test_eintragen_behaelt_andere_server` (exa bleibt bestehen), `test_eintragen_ist_idempotent`, `test_cli_schreibt_backup_einmal_und_atomar(tmp_path)` und `test_skill_frontmatter`. Der letzte liest die Datei mit `yaml.safe_load` und prüft, dass `name` und `description` gesetzt sind und `tools` gleich `["listMcpTools", "executeMcpTool"]` ist. Außerdem muss der Text die Wörter `wissen_suchen` und `wörtlich` enthalten.
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): Rowboat bekommt die Bedeutungssuche als MCP und Skill`

### Task 8: PC vorbereiten, ausrollen, Abnahme (Betreiber-OK, live)

**Files:**
- Modify: `spaces/rowboat/deploy/headless/README.md` (Betrieb, Rückweg), äußeres Repo `WORKBOARD.md` (eigene Zeile), Memory `project_rowboat_ui_cdp.md` bzw. neue Memory `project_rowboat_bedeutungssuche.md`

- [ ] **Step 1: Claim** in `WORKBOARD.md` (VM-Compose `rowboat-headless`, `rowboat-data/config/mcp.json`, `rowboat-data/skills/`) eintragen und sofort committen.
- [ ] **Step 2: PC-Seite nach den Betreiber-Entscheiden 1–4 (unten)**. Danach messen, von der VM aus:
  - `curl -s --max-time 5 http://100.84.127.84:11435/api/version` liefert eine Versionsangabe.
  - `curl -s http://100.84.127.84:11435/api/embed -d '{"model":"qwen3-embedding:0.6b","input":["Test"],"options":{"num_ctx":2048},"keep_alive":"60s"}' | python3 -c "import json,sys;print(len(json.load(sys.stdin)['embeddings'][0]))"` liefert `1024`.
  - Danach `/api/ps` prüfen: `context_length 2048`, `size` unter 1,5 GB.
- [ ] **Step 3: Ausrollen**: `bauen.ps1` ausführen und `ssh offload-vm 'install -d -m 700 ~/rowboat-headless/wissen-index && cd ~/rowboat-headless && docker compose up -d --build rowboat-wissen'`. Der Rowboat-Server bleibt dabei unberührt; prüfen mit `docker compose ps`, dessen Laufzeit nicht zurückgesetzt sein darf. Danach `curl -s 127.0.0.1:3240/gesund` auf der VM → 200.
- [ ] **Step 4: Erster Index**: Logs mit `docker compose logs -f rowboat-wissen` verfolgen, bis `wissen_stand` `offen == 0` meldet. Erwartet: etwa 620 Dateien (mit den Steckbriefen aus Teil B ~1.200), `fehler == 0`. Laufzeit und Größe von `wissen.sqlite` notieren.
- [ ] **Step 5: mcp.json + Skill**: `ssh offload-vm 'python3 - ~/rowboat-data/config/mcp.json' < spaces/rowboat/scripts/wissen_mcp_eintragen.py` ausführen und die Skill kopieren (über `bauen.ps1`). Liest Rowboat `mcp.json` nicht neu ein, folgt `docker compose restart rowboat-server`, und `/health` muss danach 200 liefern.
- [ ] **Step 6: Abnahme (Spec 9, Zeile 2)**:
  - PC an: Per Telegram „Kunden, die Interesse an Altersvorsorge hatten“ fragen. Die Antwort nennt passende Steckbriefe; im Turn-Log (`storage/turns/*.jsonl`) steht ein Aufruf `executeMcpTool` → `wissen/wissen_suchen`.
  - Ollama aus: Der Betreiber beendet die Ollama-App (nicht `pkill -f`, Memory `feedback_prozess_kill_muster_trifft_eigene_shell`) und stellt dieselbe Frage. Die Antwort kommt in < 30 s und enthält den Stichwort-Hinweis. Wenn möglich, auch einmal mit PC ganz aus (Tailnet weg).
  - Eine neue Notiz bei PC aus („Speicher die Notiz: Testkunde will Riester-Beratung“) ist sofort per Stichwort auffindbar. Nach dem Einschalten von Ollama ist sie spätestens nach 10 min `fertig` (`wissen_stand`).
  - Kein Modell auf der VM: `ssh offload-vm 'ps aux | grep -i -E "ollama|llama|vllm|torch|onnx" | grep -v grep'` → leer.
  - Ruft der Agent die Skill nicht von selbst auf (3 von 3 Testfragen ohne `wissen_suchen`), Ergebnis festhalten; das ist Betreiber-Entscheid 5.
- [ ] **Step 7: Rückweg dokumentieren** (README): `wissen` aus `mcp.json` nehmen (Sicherung `.bak-vor-wissen`), Skill-Ordner umbenennen, `docker compose stop rowboat-wissen`. Rowboat sucht dann wie vorher per `file-grep`.
- [ ] **Step 8: Abschluss**: WORKBOARD-Zeile schließen, Memory anlegen, Commit `docs(rowboat): Bedeutungssuche live`.

## Betreiber-Entscheide (vor Task 8)

1. **Ollama ist heute im LAN offen.** `server.log` zeigt `OLLAMA_HOST:http://0.0.0.0:11434` (Ollama-App, Einstellung „Expose Ollama to the network“), und die Firewall-Regeln `ollama.exe` (TCP/UDP, Profile Privat+Öffentlich, Remote *Any*) lassen Zugriffe zu. Vorschlag: Die Einstellung aus, sodass nur noch Loopback gilt, und die beiden Allow-Any-Regeln deaktivieren. Vorher prüfen, ob die Brain-Dienste Ollama über `host.docker.internal:11434` weiter erreichen (WSL Mirrored). Die alte Regel „Ollama fuer Rowboat-VM“ (TCP 11435 von `192.168.178.65`, Rest der Portproxy-Zeit vor dem 22.09.) entfernen.
2. **Tailnet-ACL:** `100.84.127.84:11435` ist heute für jedes Gerät im Tailnet offen. Vorschlag: In der Tailscale-Admin-Konsole nur `offload-vm` (100.67.177.45) auf `desktop-p303ria:11435` erlauben. Die Windows-Firewall kann das nicht leisten, weil `tailscale serve` die Verbindung über Loopback weitergibt.
3. **Ollama „nur bei Bedarf“:** Die Bedeutungssuche funktioniert nur, solange `ollama serve` läuft. Die Aufgabe `OllamaServe` ist seit 24.09. aus. Vorschlag: Beim Anmelden nur `ollama serve` starten. Das kostet im Leerlauf ≈ 70 MB, denn das Modell lädt erst bei Anfrage und wird nach 60 s bzw. 5 min wieder entladen. Alternative: so lassen, die Suche fällt dann öfter auf Stichworte zurück.
4. **Kontextlänge der Ollama-App (131072):** Jedes Modell lädt dadurch mit maximalem Kontext; `qwen3-embedding:0.6b` lag am 26.10. mit 6,25 GB im Speicher, der Verbraucher ist unbekannt, vermutlich learning-claw (Rolle `brain_kg`). Teil C sendet `num_ctx 2048`. Wechseln zwei Verbraucher mit verschiedenen Werten, lädt Ollama das Modell jedes Mal neu (wenige Sekunden). Vorschlag: Den Regler in der App auf 8192 setzen, oder akzeptieren, dass es Neuladen gibt.
5. **Wenn der Agent die Skill übergeht:** Eine Zeile in `runtime/assembly/copilot/instructions.ts` im Fork `Flissel/rowboat` (Branch `vibemind`) ergänzen, etwa „für Wissensfragen zuerst `loadSkill('wissen-suchen')`“. Das läuft über `sync_desktop.py` und Neubau. Nur machen, wenn die Abnahme es zeigt.
6. **Freigabe zum Ausrollen** auf der VM (Task 8 Step 3–5) und zum Neustart des Rowboat-Servers, falls `mcp.json` nicht live neu geladen wird.

## Folgepläne (nicht Teil C)

- Teil D: Werkzeuge (GitHub, Docs, Dashboard, PC-Suche über den verbundenen PC), Freigaben per Telegram.
- Optional: `wissen_suchen` auch für Chats (`storage/turns`). Für den Start liegt das nicht im Umfang, weil Chats Mail-Auszüge enthalten können (Spec, Anti-Ziel sensible Inhalte).
