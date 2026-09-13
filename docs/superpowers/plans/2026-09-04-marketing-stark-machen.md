# Marketing-Space stark machen — Implementierungsplan

> **Für agentische Worker:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` (empfohlen) oder
> `superpowers:executing-plans`, Task für Task. Schritte nutzen Checkbox-Syntax (`- [ ]`).

**Ziel:** Der Marketing-Agent schreibt Kampagnen aus dem gesamten vorhandenen Material statt aus einer einzigen
Textquelle — mit Videos, Transkripten und PDFs als Beleg, nach festen Handwerksregeln, und legt das Ergebnis dort ab,
wo sales-claw es findet.

**Architektur:** Vier Schichten, von unten nach oben. Zuerst der Rohstoff (Laura-Videos, Rowboat-Chat über alle 14
Quellen), dann das Handwerk (Skills mit Pflichtablauf pro Kanal), dann die Ablage (`/media-erzeugt`), zuletzt die
Sichtbarkeit (Freigabe-Ansicht). Jede Schicht ist für sich nutzbar; keine baut auf einer unfertigen auf.

**Tech Stack:** Python 3.11, FastMCP-Sidecar (`spaces/marketing/claw/`), Rowboat-MCP über HTTP, Laura-MCP (stdio,
`services/local-api`, 28 Werkzeuge), Postgres/Supabase auf der VM, openclaw-Gateway 2026.7.1.

**Spec:** Keine eigene Spec — dieser Plan folgt den Messungen vom 03./04.09.2026, die unten als Global Constraints
stehen. Die Betreiber-Entscheide (Belegpflicht, Freigabe-Gate, Agent entscheidet Empfänger) stammen aus
`docs/superpowers/specs/2026-09-02-marketing-sales-rowboat-metaebene-design.md`.

## Global Constraints

- **Nichts wird versendet.** Jedes Werkzeug erzeugt Entwürfe. Der Versand bleibt am Freigabe-Gate des Betreibers.
- **Belegpflicht** (Entscheid 03.09.2026): jede Produktaussage nennt Quelle und Dokument; Unbelegtes steht sichtbar
  unter „Zu klären". Bestehendes Verhalten von `kampagne_entwerfen`, nicht aufweichen.
- **`/media` ist der Menschen-Ordner**, für den Agenten nur lesbar. Maschinell Erzeugtes geht nach `/media-erzeugt`
  (`sales-mcp/medien.py:45-49`). Diese Trennung ist Absicht und bleibt.
- **Fail-soft ist der Vertrag**: ein nicht erreichbarer Fremddienst liefert `{"ok": False, "fehler": …}`, wirft nie.
  Muster: `claw/werkzeuge.py:_rowboat`.
- **Keine Geheimnisse in Fehlertexten** (`_ohne_schluessel`), keine Schlüssel in argv.
- **Gemessener Ausgangszustand 04.09.2026:** 1 von 14 Rowboat-Quellen genutzt · 7 Produktvideos + 3 PDFs in
  `sales-claw:/media` ungenutzt · Laura-API startet nicht · `marketing/` hat 0 Skills, sales-claw hat 2 ·
  Oberfläche `/mockup/` ist statisch („no backend wiring") · 21 Proposals, davon 1 versendet.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `spaces/video/laura/services/local-api/pyproject.toml` | `httpx` von der dev-Gruppe in die Laufzeit-Abhängigkeiten |
| `spaces/marketing/claw/laura.py` (neu) | Passthrough zum Laura-MCP, fail-soft, keine Geschäftslogik |
| `spaces/marketing/claw/werkzeuge.py` | neue Werkzeuge: `videos`, `video_transkript`, `wissen_fragen` |
| `spaces/marketing/claw/server.py` | Registrierung im `WERKZEUGE`-Tupel |
| `spaces/marketing/claw/ablage.py` (neu) | Schreiben nach `/media-erzeugt`, Pfad-Härtung |
| `spaces/marketing/claw/gateway/config/workspace/skills/` (neu) | `email-kampagne/SKILL.md`, `whatsapp-nachricht/SKILL.md` |
| `spaces/marketing/claw/gateway/config/workspace/AGENTS.md` | Verweis auf die Skills, Rohstoff-Reihenfolge |
| `spaces/marketing/claw/tests/` | je Task eine Testdatei, Muster `test_wissensbasis.py` |

---

### Task 1: Laura startklar machen

**Files:**
- Modify: `spaces/video/laura/services/local-api/pyproject.toml:54-59`

**Interfaces:**
- Produces: erreichbare Laura-HTTP-API auf `127.0.0.1:8765`, Laura-MCP nutzbar (Task 2 baut darauf).

- [ ] **Schritt 1: Den Fehler reproduzieren**

```bash
docker logs deploy-api-1 --tail 5
```
Erwartet: `ModuleNotFoundError: No module named 'httpx'` aus `src/laura/ingest/download.py:20`.

- [ ] **Schritt 2: `httpx` in die Laufzeit-Abhängigkeiten**

In `pyproject.toml` die Zeile `"httpx>=0.27",` aus `[dependency-groups] dev` entfernen und in
`[project] dependencies` aufnehmen. Begründung als Kommentar dazu:

```toml
    "httpx>=0.27",               # Laufzeit, nicht dev: ingest/download.py importiert beim Laden
```

- [ ] **Schritt 3: Neu bauen und starten**

```bash
cd vibemind-os/spaces/video/laura/deploy
docker compose up -d --no-deps --build api
```
Der vorhandene `laura-qdrant` bleibt unangetastet — er hält die Collections `transcripts` und
`project_test_service` und hängt bereits mit Alias `qdrant` im Netz `deploy_default`.

- [ ] **Schritt 4: Beweis führen**

```bash
curl -s -m 10 http://127.0.0.1:8765/health
```
Erwartet: HTTP 200. Zusätzlich muss `docker logs deploy-api-1 --tail 5` ohne Traceback enden.

- [ ] **Schritt 5: Commit**

```bash
git add spaces/video/laura/services/local-api/pyproject.toml
git commit -m "fix(laura-api): httpx ist Laufzeit-Abhaengigkeit, nicht dev"
```

---

### Task 2: Videos und Transkripte für den Marketing-Agenten

**Files:**
- Create: `spaces/marketing/claw/laura.py`
- Create: `spaces/marketing/claw/tests/test_laura.py`
- Modify: `spaces/marketing/claw/werkzeuge.py` (Ende), `spaces/marketing/claw/server.py:48-66`

**Interfaces:**
- Consumes: Laura-API aus Task 1.
- Produces: `werkzeuge.videos() -> dict`, `werkzeuge.video_transkript(video_id: str) -> dict`, beide in der
  Form `{"ok": True, "daten": …}` / `{"ok": False, "fehler": str}` wie `_rowboat`.

- [ ] **Schritt 1: Den fehlschlagenden Test schreiben**

`tests/test_laura.py`, Muster aus `test_wissensbasis.py` (HTTP-Stub, kein echtes Netz):

```python
def test_videos_liefert_liste_bei_erreichbarer_api(laura_stub):
    ergebnis = werkzeuge.videos()
    assert ergebnis["ok"] is True
    assert ergebnis["daten"][0]["name"] == "VibeMind-Laura-Produktvideo.mp4"


def test_videos_ist_fail_soft_wenn_laura_aus_ist(monkeypatch):
    monkeypatch.setenv("LAURA_API_URL", "http://127.0.0.1:1")
    ergebnis = werkzeuge.videos()
    assert ergebnis["ok"] is False
    assert "nicht erreichbar" in ergebnis["fehler"]


def test_kein_token_im_fehlertext(monkeypatch):
    monkeypatch.setenv("LAURA_TOKEN", "streng-geheim")
    monkeypatch.setenv("LAURA_API_URL", "http://127.0.0.1:1")
    assert "streng-geheim" not in werkzeuge.videos()["fehler"]
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
cd vibemind-os && python -m pytest spaces/marketing/claw/tests/test_laura.py -q
```
Erwartet: FAIL, `AttributeError: module 'werkzeuge' has no attribute 'videos'`.

- [ ] **Schritt 3: `laura.py` schreiben**

Aufbau exakt wie `_rowboat` in `werkzeuge.py:140-165`: Adresse und Token zur Aufrufzeit aus der Umgebung
(`LAURA_API_URL`, Vorgabe `http://127.0.0.1:8765`, `LAURA_TOKEN`), `urllib.request`, Zeitlimit 20 s,
`_ohne_token` filtert das Token aus jedem Fehlertext, jede Ausnahme wird zu `{"ok": False, "fehler": …}`.

- [ ] **Schritt 4: Werkzeuge ergänzen und registrieren**

In `werkzeuge.py`:

```python
def videos() -> dict:
    """Die fertigen Videos aus Laura (Name, Dauer, Projekt). Nur lesend."""
    return laura.hole("/api/assets")


def video_transkript(video_id: str) -> dict:
    """Das Transkript eines Videos — der belegbare Text zum Bild. Nur lesend."""
    return laura.hole(f"/api/assets/{video_id}/transcript")
```

In `server.py` beide ans Ende des `WERKZEUGE`-Tupels, hinter `werkzeuge.dokumente`.

- [ ] **Schritt 5: Tests grün, dann Commit**

```bash
python -m pytest spaces/marketing/claw/tests/test_laura.py -q
git add spaces/marketing/claw/laura.py spaces/marketing/claw/tests/test_laura.py \
        spaces/marketing/claw/werkzeuge.py spaces/marketing/claw/server.py
git commit -m "feat(marketing): Videos und Transkripte aus Laura als Belegquelle"
```

---

### Task 3: Die ganze Wissensbasis befragen statt eine Quelle lesen

**Files:**
- Modify: `spaces/marketing/claw/werkzeuge.py`, `spaces/marketing/claw/server.py`
- Create: `spaces/marketing/claw/tests/test_wissen_fragen.py`

**Interfaces:**
- Consumes: Rowboat-Chat-Route (`POST /api/v1/{projectId}/chat`, Bearer aus `ROWBOAT_API_KEY`).
- Produces: `werkzeuge.wissen_fragen(frage: str) -> dict` mit `{"ok": True, "daten": {"antwort": str,
  "quellen": [str]}}`.

- [ ] **Schritt 1: Test schreiben**

```python
def test_wissen_fragen_gibt_antwort_und_quellen(chat_stub):
    ergebnis = werkzeuge.wissen_fragen("Was kann der Ideaspace?")
    assert ergebnis["ok"] is True
    assert ergebnis["daten"]["quellen"], "ohne Quellen ist die Antwort als Beleg wertlos"


def test_wissen_fragen_ist_fail_soft(monkeypatch):
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:1")
    assert werkzeuge.wissen_fragen("egal")["ok"] is False
```

- [ ] **Schritt 2: Fehlschlag bestätigen**

```bash
python -m pytest spaces/marketing/claw/tests/test_wissen_fragen.py -q
```

- [ ] **Schritt 3: Implementieren**

`wissen_fragen` ruft die Chat-Route, gibt Antworttext und die genannten Quellen zurück. Kein Umbau von
`_rowboat` — die Chat-Route ist kein MCP-Werkzeug, sondern eine eigene HTTP-Route und bekommt eine eigene
Hilfsfunktion `_rowboat_chat` daneben.

- [ ] **Schritt 4: Tests grün, Commit**

```bash
git commit -m "feat(marketing): wissen_fragen befragt alle Quellen statt eine zu lesen"
```

---

### Task 4: Handwerk als Skills

**Files:**
- Create: `spaces/marketing/claw/gateway/config/workspace/skills/email-kampagne/SKILL.md`
- Create: `spaces/marketing/claw/gateway/config/workspace/skills/whatsapp-nachricht/SKILL.md`
- Modify: `spaces/marketing/claw/gateway/config/workspace/AGENTS.md`

**Interfaces:**
- Consumes: `videos`, `video_transkript`, `wissen_fragen` aus Task 2 und 3.
- Produces: fester Ablauf, den der Agent bei jedem Entwurf einhält.

- [ ] **Schritt 1: Material auswerten**

Die sechs Telegram-Entwürfe vom 02./03.09. lesen
(`spaces/marketing/claw/schaufenster/2026-09-0*/briefing*.md`) und in der Datenbank abgleichen, welche der 21
Proposals `rejected` sind:

```bash
ssh offload-vm "docker exec debian-supabase-db-1 psql -U postgres -tAq -c \
  \"select status, left(coalesce(draft_subject,''),50) from marketing.broadcast_proposals order by created_at desc;\""
```
Aus dem Vergleich abgelehnt/durchgegangen entstehen die Stil-Leitplanken — so wie der `linkedin-post`-Skill
von sales-claw aus der Auswertung erfolgreicher Praxis entstand.

- [ ] **Schritt 2: `email-kampagne/SKILL.md` schreiben**

Aufbau exakt wie `sales-claw/config/workspace/skills/linkedin-post/SKILL.md`: Frontmatter mit `name`,
`description`, `metadata: { "openclaw": { "emoji": "✉️" } }`, dann ein nummerierter Pflichtablauf. Inhaltlich
mindestens: Schritt 1 Rohstoff holen (`wissen_fragen` zum Thema, `videos` für passendes Bewegtbild), Schritt 2
ein Gedanke pro Mail statt Funktionsliste, Schritt 3 Nutzen statt Merkmal, Schritt 4 echter Link statt
Platzhalter — der Entwurf vom 03.09. enthielt wörtlich `[Link]`, Schritt 5 Belege und „Zu klären" füllen,
Schritt 6 Ergebnis ist immer ein Entwurf.

- [ ] **Schritt 3: `whatsapp-nachricht/SKILL.md` schreiben**

Gleicher Aufbau, andere Leitplanken: sehr kurz, kein Werbeton, Einwilligung zwingend vor jeder Ansprache
(UWG, dieselbe Regel wie im Vertrieb), kein Anhang ohne Bezug, und die Nachricht muss ohne Vorschau lesbar sein.

- [ ] **Schritt 4: AGENTS.md verweisen lassen**

Abschnitt „Kampagnen entwerfen" ergänzen: welcher Skill für welchen Kanal gilt, und dass der Rohstoff-Schritt
Pflicht ist, bevor Text entsteht.

- [ ] **Schritt 5: Saat ins Volume, Abnahme, Commit**

```bash
cd spaces/marketing/claw/gateway && bash anbinden.sh
```
Erwartet: 11 Werkzeuge grün (die Abnahme kennt die neuen noch nicht — Zahl im Skript auf 14 anheben).

- [ ] **Schritt 6: Am echten Entwurf prüfen**

```bash
docker compose exec -T marketing-claw openclaw agent --agent main \
  --session-key "agent:main:skilltest-$(date +%H%M%S)" \
  -m "Entwirf eine E-Mail-Kampagne fuer den Early Access, Zielgruppe Solo-Gruender." --json
```
Erwartet: der Entwurf nennt mindestens zwei verschiedene Quellen und enthält keinen `[Link]`-Platzhalter.

---

### Task 5: Ablage nach `/media-erzeugt`

**Files:**
- Create: `spaces/marketing/claw/ablage.py`, `spaces/marketing/claw/tests/test_ablage.py`
- Modify: `spaces/marketing/claw/werkzeuge.py`, `spaces/marketing/claw/server.py`

**Interfaces:**
- Produces: `werkzeuge.post_ablegen(name: str, inhalt: str, art: str = "md") -> dict` mit
  `{"ok": True, "daten": {"pfad": str}}`.

- [ ] **Schritt 1: Test schreiben**

```python
def test_post_landet_in_erzeugt_nicht_in_media(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ERZEUGT_DIR", str(tmp_path))
    ergebnis = werkzeuge.post_ablegen("early-access", "# Post\n\nText")
    assert ergebnis["ok"] is True
    assert (tmp_path / "early-access.md").exists()


def test_pfad_ausbruch_wird_abgelehnt(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ERZEUGT_DIR", str(tmp_path))
    assert werkzeuge.post_ablegen("../../etc/passwd", "x")["ok"] is False
```

- [ ] **Schritt 2: Fehlschlag bestätigen, dann implementieren**

`ablage.py` härtet den Pfad mit `os.path.realpath` gegen Ausbruch (Muster: `medien.py:118-123`), legt das
Verzeichnis an und schreibt UTF-8 mit `\n`. Schreibt **nie** nach `MEDIA_DIR`.

- [ ] **Schritt 3: Tests grün, Commit**

```bash
git commit -m "feat(marketing): Posts landen in media-erzeugt, wo sales-claw sie findet"
```

---

### Task 6: Freigabe-Ansicht, die echte Entwürfe zeigt

**Files:**
- Modify: `spaces/marketing/api/server.py` (Route `/api/campaigns` um Proposals erweitern)
- Modify: `spaces/marketing/mockup/index.html`
- Create: `spaces/marketing/api/tests/test_proposals_route.py`

**Interfaces:**
- Consumes: Tabelle `marketing.broadcast_proposals` (Spalten `id, channel, status, draft_subject,
  draft_body_text, created_at`).

- [ ] **Schritt 1: Test schreiben**

```python
def test_proposals_route_liefert_die_entwuerfe(client):
    antwort = client.get("/api/proposals?status=draft")
    assert antwort.status_code == 200
    assert antwort.json()["data"], "die sechs Telegram-Entwuerfe muessen erscheinen"
```

- [ ] **Schritt 2: Route bauen, Test grün**

- [ ] **Schritt 3: Mockup anschließen**

Der Reiter „Proposals" liest die neue Route statt der eingebauten Beispieldaten. Die Fußzeile „static, no
backend wiring" entfernen, sobald sie nicht mehr stimmt.

- [ ] **Schritt 4: Am Bildschirm prüfen und committen**

`http://127.0.0.1:5510/mockup/#proposals` zeigt die sechs Telegram-Entwürfe vom 02./03.09. mit Betreff,
Status und Belegen.

---

## Reihenfolge und Wirkung

Task 1 bis 3 heben die Textqualität, weil sie den Rohstoff anschließen — das ist der gemessene Engpass.
Task 4 macht aus Rohstoff Handwerk. Task 5 verbindet Marketing mit sales-claw. Task 6 macht das Ergebnis
sichtbar und kommt bewusst zuletzt: eine Oberfläche über blassen Texten nützt niemandem.

Nach Task 3 lohnt ein Zwischenstopp: denselben Kampagnenauftrag wie am 03.09. noch einmal geben und die zwei
Entwürfe nebeneinander legen. Wenn der neue nicht sichtbar besser ist, liegt es am Handwerk und nicht am
Rohstoff — dann Task 4 vorziehen und größer anlegen.
