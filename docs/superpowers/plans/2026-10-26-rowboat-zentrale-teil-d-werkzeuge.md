# Rowboat-Zentrale Teil D – Werkzeuge (GitHub lesen, Docs, Dashboard, PC-Suche) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Rowboat-Server auf dem Mini-PC beantwortet „GitHub-Projekte updaten“, „neueste Docs holen“, „Dashboard für meine Daten“ und „auf meinem PC nach X suchen“ – vom Handy (Telegram) und vom Desktop. Werkzeuge mit Außenwirkung gibt es dabei nicht; bei ausgeschaltetem PC meldet die PC-Suche sauber „PC ist aus“.

**Architecture:** Ein neuer Container `rowboat-werkzeuge` auf der VM (Host-Netz, bindet nur `127.0.0.1:8150`, FastMCP streamable-http) stellt Rowboat zwei Werkzeuggruppen bereit:
- GitHub nur lesend, per REST mit je einem fein granulierten **Nur-Lese-Token** pro Besitzer. Beim Start prüft der Container jeden Token und sperrt ihn, wenn er schreiben darf.
- `pc_suchen` als Weiterleitung an einen kleinen stdlib-Suchdienst am PC (`100.84.127.84:8149`). Der Dienst nimmt nur Anfragen von der VM-IP `100.67.177.45` mit gemeinsamem Schlüssel an. Ist der PC nicht erreichbar, antwortet die Weiterleitung mit „PC ist aus“.

Die Geheimnisse liegen nur in `~/rowboat-headless/werkzeuge.env` (0600, `env_file` dieses Containers). In `mcp.json` steht **kein** Geheimnis, denn jeder Rowboat-Lauf kann `/data/config/*` ohne Freigabe lesen (siehe Befunde). Docs laufen über das vorhandene `exa` (gehostet, ohne Schlüssel) und Rowboats eingebautes `fetch-url`. Dashboards baut Rowboat mit seinem eigenen Apps-Skill. Angezeigt werden sie am PC über einen SSH-Tunnel auf `127.0.0.1:3210` im Fern-Modus. Welche Anfrage welches Werkzeug nimmt, regelt ein Disk-Skill `zentrale-werkzeuge`.

**Tech Stack:** Python 3.12 im Container (`python:3.12-slim`, `mcp==1.27.0` wie am PC), Python-stdlib-Dienst am PC (Windows, Aufgabenplanung), Docker Compose auf `offload-vm`, PowerShell 7, pytest mit Attrappen.

**Spec:** `docs/superpowers/specs/2026-10-24-rowboat-geschaeftsdaten-zentrale-design.md` (Ziel 6, Abschnitt 1 Beispielnachrichten, Abschnitt 7 „Telegram-Aktionen“, Abschnitt 8 „Welche Werkzeuge …“). Vorgänger: `docs/superpowers/plans/2026-10-24-rowboat-zentrale-teil-a-mini-pc.md`.

## Befunde aus der Erkundung (26.10.2026, Quelle Betriebs-Checkout `apps/x`)

- Telegram-Läufe: `channels/bridge.ts` startet Agent `copilot` mit `autoPermission: true`. Das ist **kein** Freibrief, sondern der LLM-Klassifizierer (`turns/runtime.ts classifyBatch`, `security/auto-permission-classifier.ts`). Er entscheidet erlauben/ablehnen; bei Unklarheit wartet der Lauf auf die Desktop-Freigabe.
- MCP-Aufrufe (`executeMcpTool`, Richtlinie `mcp-execute`) brauchen **immer** eine Freigabe und gehen in Telegram-Läufen also durch den Klassifizierer. Ob Lesen erlaubt wird, entscheidet ein Modell. Eine harte Grenze ist das nicht. Die harte Grenze für GitHub ist darum der Nur-Lese-Token.
- `fetch-url` hat `permission: "none"`, kann **POST** mit beliebigen Kopfzeilen und kennt keine Host-Sperre. `file-readText`/`file-grep` innerhalb des Arbeitsordners `/data` sind ohne Freigabe erlaubt (`permission-metadata.ts`, nur Pfade außerhalb brauchen eine). Folge: Alles unter `/data/config/` ist für jeden Lauf lesbar, auch `channels.json` (Bot-Schlüssel, vorbestehend). Deshalb kommt **kein** neues Geheimnis nach `/data`.
- `executeCommand` ist Basiswerkzeug (`command-allowlist` → Klassifizierer). Der Skill `mcp-integration` hängt `addMcpServer` an (`prompt` → Klassifizierer). Ein Telegram-Lauf kann also mit Klassifizierer-OK Shell-Befehle im Rowboat-Container ausführen oder MCP-Server eintragen. Das ist vorbestehend und wird hier **nicht** gelöst (Betreiber-Entscheid D3). Die Werkzeug-Geheimnisse liegen deshalb in einem **anderen** Container und sind für den Rowboat-Container unsichtbar.
- VM-`mcp.json` = nur `exa` mit `url: https://mcp.exa.ai/mcp`, ohne Schlüssel. Von der VM geprüft: `tools/list` liefert `web_search_exa`, `web_fetch_exa`. `config/exa-search.json` fehlt, darum ist das eingebaute `web-search` nicht verfügbar. `fetch-url` geht.
- MCP-Konfiguration wird bei jedem Zugriff neu gelesen (`mcp/mcp.ts effectiveServers`, Neuverbindung bei geänderter Konfiguration), darum braucht ein neuer Eintrag keinen Server-Neustart. Erlaubte Formen: stdio `{command,args,env}` oder http `{type:"http", url, headers}`.
- Fähigkeits-Weiterleitung an den Desktop (`apps/server/src/capabilities.ts`) bräuchte eine neue Fähigkeit im Fork **und** in der Desktop-App und ginge nur bei laufender App. Für die PC-Suche ist sie verworfen; der Tailnet-Dienst geht auch ohne App.
- Rowboat-Apps (`core/src/apps`, Skill `apps`) liegen in `/data/apps/<slug>/`, der Apps-Server lauscht auf der VM **nur** auf `127.0.0.1:3210` (`apps/server.ts init`). Die Desktop-App im Fern-Modus erreicht ihn nur über einen Tunnel.
- Disk-Skills: `<WorkDir>/skills/<name>/SKILL.md` = `/data/skills/...`, live nachgeladen (`skills/disk-loader.ts`, `watcher.ts`). Sie werden nur auf Modellentscheid geladen, „always“ ist für Disk-Skills gesperrt. Immer im Prompt stehen nur `knowledge/Agent Notes/user.md` und `preferences.md` (`workspace-context.ts`).
- Fein granulierte Tokens gelten je **einen** Besitzer. Flissel/* und Vibemind-LAB/* brauchen also zwei Tokens (Vibemind-LAB ist ein eigenes Konto laut `gh auth status`). Die Org `vibemind-space` bräuchte einen dritten.
- Tresor: Referenzen mit `INTEGRATION_` gibt `/api/credentials/issue` nie heraus. Neue Referenzen beginnen deshalb mit `ROWBOAT_`.
- PC hat weder ripgrep noch Everything. Die Suche ist deshalb reines Python (`os.scandir`).

## Global Constraints

- Auf dem Mini-PC läuft **kein Modell** (Spec 4). Der Werkzeug-Container ruft nur GitHub-REST und den PC auf.
- Telegram-Läufe bekommen **keine Werkzeuge mit Außenwirkung** (Spec 7). Auf der VM gibt es nur lesende GitHub-Werkzeuge; „Projekte updaten“ per Telegram heißt lesen/zusammenfassen bzw. Todo „Am Desktop: …“.
- GitHub-Token: fein granuliert, je Besitzer, Rechte **nur** `Metadata: read`, `Contents: read`, `Issues: read`, `Pull requests: read`. Der Container stellt nur GET-Anfragen. Einzige Ausnahme ist die Schreibprobe beim Start (POST mit leerem Körper, Task 1).
- Geheimnisse (GitHub-Tokens, PC-Suche-Schlüssel) nie in argv, Logs, Commits, `mcp.json` oder unter `/home/debian/rowboat-data`. Wahrheit ist der OpenFang-Tresor am PC (Referenzen `ROWBOAT_GITHUB_LESEN_<BESITZER>`, `ROWBOAT_PC_SUCHE_KEY`). Die Kopie auf der VM liegt in `~/rowboat-headless/werkzeuge.env` (0600) und kommt nur über ssh-stdin (Muster `scripts/kanal_zugang.py`). Werte immer `.strip()` (CRLF-Leck, Memory `feedback_env_crlf_schluessel_leck`). Fehler nennen nur den Ausnahme-Typ.
- PC-Suche: nur lesen, keine Shell, keine Datei-Schreibzugriffe. Sie lauscht nur auf der Tailnet-IP des PCs `100.84.127.84:8149` und nimmt nur Anfragen von `100.67.177.45` mit Kopfzeile `X-PC-Suche-Key` an. Die Antwort bei PC aus lautet wörtlich: `PC ist aus – auf dem PC kann ich gerade nicht suchen.`
- Ports: VM `8150` (nur Loopback, frei laut `ss -ltn` 26.10.), PC `8149` (frei, in `vibemind.config.json` eintragen).
- Deploy-Schritte (Token anlegen, Container bauen/starten, `mcp.json` ändern, Skill kopieren, Firewall-Regel, Aufgabe anlegen, Betrieb aktualisieren) nur nach Betreiber-OK (markiert **[OK]**). Code-Tasks davor ohne.
- Vor Ausführung: WORKBOARD-Claim `cc-rowboat-zentrale-d` (Bereiche `spaces/rowboat/deploy/headless`, `spaces/rowboat/pc_suche`, `Vibemind_V1/scripts/rowboat_zugang.py`, VM `~/rowboat-headless`), sofort committen. Commits direkt auf `master` (CLAUDE.md, Claude-Code-Sessions), nur eigene Dateien stagen.
- Rowboat-Fork bleibt unverändert (kein Fork-Patch in Teil D).

## Review Focus

1. **PC aus, Tailscale am PC weg oder Suchdienst hängt:** `pc_suchen` antwortet in ≤ 5 s Verbindungsaufbau mit der festen „PC ist aus“-Meldung und hängt den Telegram-Lauf nicht (Task 2: `test_pc_aus_bei_verbindungsfehler`, `test_pc_aus_bei_timeout`).
2. **Versehentlich ein Token mit Schreibrecht eingetragen:** Die Startprobe sperrt diesen Besitzer, und die Werkzeuge melden „Zugang für <Besitzer> gesperrt: Token darf schreiben“. Es läuft keine einzige weitere Anfrage mit diesem Token (Task 1: `test_nur_lesend_pruefen_*`, Task 3: `test_start_sperrt_schreibenden_token`).
3. **Suchbegriff trifft Geheimnisse** (`.env`, `id_rsa`, `token`, `client_secret*.json`, `~/.rowboat/config`, `AppData`): keine Treffer auf ausgeschlossene Dateien/Ordner, Inhaltsauszüge mit `key=`/`token:` geschwärzt (Task 4: `test_geheimnisdateien_nie_treffer`, `test_auszug_geschwaerzt`).
4. **Riesige Bäume** (`Desktop/Vibemind_V1` mit `node_modules`, `.venv`): Zeitlimit 10 s, `abgeschnitten: true`. Der Skill verlangt dann den Hinweis „unvollständig“ (Task 4: `test_zeitlimit_schneidet_ab`, Task 6: `test_skill_nennt_abgeschnitten`).
5. **Telegram: „push das / merge PR / leg Issue an“:** Auf der VM existiert kein schreibendes GitHub-Werkzeug. Der Skill legt ein Todo „Am Desktop: …“ an (Task 3: `test_keine_schreibwerkzeuge`, Task 6: `test_skill_regel_ausserwirkung`, Abnahme Task 8).

---

### Task 1: GitHub nur lesend (Logik, ohne Netz)

**Files:**
- Create: `spaces/rowboat/deploy/headless/werkzeuge/github_lesen.py`
- Test: `spaces/rowboat/tests/test_werkzeuge_github.py` (Import per `sys.path.insert(0, …/deploy/headless/werkzeuge)` wie `test_rowboat_shim_huelle.py`)

**Interfaces:**
- Produces:
  - `HttpGet = Callable[[str, dict[str, str]], tuple[int, Any]]` (URL, Kopfzeilen → Status, JSON); `HttpPost = Callable[[str, dict[str, str], dict], tuple[int, Any]]`
  - `class GithubFehler(Exception)` (Meldung nie mit Token)
  - `Zustand = Literal["bereit", "fehlt", "darf_schreiben", "abgelehnt", "unklar"]`; `@dataclass Zugang(besitzer: str, token: str | None, zustand: Zustand)`
  - `besitzer_aus_umgebung(umgebung: Mapping[str, str]) -> dict[str, Zugang]`: liest `GITHUB_BESITZER="Flissel=GITHUB_LESEN_FLISSEL,Vibemind-LAB=GITHUB_LESEN_VIBEMIND_LAB"`, Werte `.strip()`. Fehlt die Variable oder ist sie leer, ist der Zustand `fehlt`, sonst vorerst `unklar`.
  - `nur_lesend_pruefen(token: str, repo: str, post: HttpPost) -> Zustand`: `POST https://api.github.com/repos/{repo}/labels` mit Körper `{}`. Ergebnis: 403 → `bereit`; 422 oder 2xx → `darf_schreiben`; 401 → `abgelehnt`; alles andere oder eine Ausnahme → `unklar`.
  - `class GithubLeser(zugaenge: dict[str, Zugang], get: HttpGet)` mit `repos(besitzer: str) -> dict`, `issues(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict`, `prs(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict`, `commits(repo: str, branch: str = "", max_anzahl: int = 20) -> dict`, `datei(repo: str, pfad: str = "README.md", ref: str = "") -> dict`, `ueberblick(tage: int = 7) -> dict`. Jede Methode liefert `{"ok": True, ...}` oder `{"ok": False, "meldung": str}`.

- [ ] **Step 1: Failing tests schreiben** (Attrappe `FalschesHttp` protokolliert `(methode, url, kopf)` und liefert vorbereitete Antworten)

```python
def test_nur_get_anfragen():          # alle sechs Methoden einmal aufrufen
    assert {m for m, _, _ in http.aufrufe} == {"GET"}
def test_fremder_besitzer_abgelehnt():
    r = leser.issues("evil/repo"); assert r["ok"] is False and "nicht freigegeben" in r["meldung"]; assert http.aufrufe == []
@pytest.mark.parametrize("repo", ["../x", "a/b/c", "Flissel", "Flissel/..", "Flissel/x y"])
def test_repo_muster_abgelehnt(repo): assert leser.commits(repo)["ok"] is False and http.aufrufe == []
def test_datei_pfad_ohne_punktpunkt(): assert leser.datei("Flissel/vibemind", "../../etc/passwd")["ok"] is False
def test_issues_ohne_prs():           # Einträge mit Schlüssel "pull_request" fallen raus
def test_max_anzahl_begrenzt():       # max_anzahl=500 -> per_page=50 in der URL
def test_token_nie_in_ergebnis_oder_fehler():   # get wirft RuntimeError(f"bad {TOKEN}")
    r = leser.repos("Flissel"); assert TOKEN not in json.dumps(r) and r["meldung"].endswith("RuntimeError")
def test_gesperrter_zugang_meldet_ohne_anfrage():  # Zustand darf_schreiben
    assert "gesperrt: Token darf schreiben" in leser.repos("Flissel")["meldung"] and http.aufrufe == []
def test_besitzer_aus_umgebung_crlf_und_fehlend():
    z = besitzer_aus_umgebung({"GITHUB_BESITZER": "Flissel=A,Vibemind-LAB=B", "A": "tok\r\n"})
    assert z["Flissel"].token == "tok" and z["Vibemind-LAB"].zustand == "fehlt"
@pytest.mark.parametrize("status,erwartet", [(403, "bereit"), (422, "darf_schreiben"), (201, "darf_schreiben"), (401, "abgelehnt"), (500, "unklar")])
def test_nur_lesend_pruefen(status, erwartet): ...
def test_nur_lesend_pruefen_ausnahme_ist_unklar(): ...
def test_ueberblick_nur_bereite_besitzer_und_tage():  # pushed_at älter als `tage` fällt raus
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**
Run: `python -m pytest spaces/rowboat/tests/test_werkzeuge_github.py -q`
Expected: FAIL (`ModuleNotFoundError: github_lesen`)

- [ ] **Step 3: `github_lesen.py` implementieren.**
  - Repo-Muster `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$`; `..` als Segment verboten; Pfad ohne `..` und ohne führendes `/`.
  - Kopfzeilen: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `User-Agent: vibemind-werkzeuge`.
  - `repos`: Zuerst `GET /user` (Login je Zugang zwischenspeichern). Ist der Login gleich dem Besitzer: `GET /user/repos?affiliation=owner&sort=pushed&per_page=50`, sonst `GET /orgs/{besitzer}/repos?sort=pushed&per_page=50`. Felder: `name, private, pushed_at, open_issues_count, default_branch, description`.
  - `datei`: `GET /repos/{repo}/contents/{pfad}`, base64 dekodieren, höchstens 100 000 Zeichen, sonst `abgeschnitten: true`.
  - Ausnahmen fangen und nur `type(e).__name__` melden.

- [ ] **Step 4: Tests grün**
Run: `python -m pytest spaces/rowboat/tests/test_werkzeuge_github.py -q` → Expected: PASS

- [ ] **Step 5: Commit**
`git add spaces/rowboat/deploy/headless/werkzeuge/github_lesen.py spaces/rowboat/tests/test_werkzeuge_github.py && git commit -m "feat(rowboat): GitHub-Lesewerkzeuge mit Nur-Lese-Probe fuer die VM"`

---

### Task 2: Weiterleitung `pc_suchen` (VM-Seite)

**Files:**
- Create: `spaces/rowboat/deploy/headless/werkzeuge/pc_suche_relay.py`
- Test: `spaces/rowboat/tests/test_werkzeuge_relay.py`

**Interfaces:**
- Consumes: Antwortformat des PC-Dienstes aus Task 4 (`{"ok", "treffer", "abgeschnitten", "dauer_ms"}`)
- Produces:
  - `PC_AUS_MELDUNG = "PC ist aus – auf dem PC kann ich gerade nicht suchen."`
  - `HttpPostJson = Callable[[str, dict[str, str], dict, float, float], tuple[int, Any]]` (URL, Kopf, Körper, connect_timeout, read_timeout)
  - `pc_suchen(anfrage: str, art: str = "name", ordner: str = "", max_treffer: int = 20, *, url: str, schluessel: str | None, post: HttpPostJson) -> dict`

- [ ] **Step 1: Failing tests schreiben**

```python
def test_pc_aus_bei_verbindungsfehler():   # post wirft ConnectionRefusedError
    assert pc_suchen("x", url=U, schluessel="k", post=wirft(ConnectionRefusedError)) == {"ok": False, "pc_aus": True, "meldung": PC_AUS_MELDUNG}
def test_pc_aus_bei_timeout():            # socket.timeout / TimeoutError / URLError(reason=timeout)
def test_timeouts_fest():                 # post bekommt connect_timeout=3.0, read_timeout=20.0
def test_schluessel_als_kopf_nie_im_ergebnis():
    r = pc_suchen("x", url=U, schluessel="GEHEIM", post=ok_post); assert post.kopf["X-PC-Suche-Key"] == "GEHEIM" and "GEHEIM" not in json.dumps(r)
def test_ohne_schluessel_kein_aufruf():   # schluessel=None -> meldung "PC-Suche ist nicht eingerichtet", post nie gerufen
def test_403_meldung_ohne_details():      # -> {"ok": False, "meldung": "PC-Suche lehnt ab (Schlüssel oder Absender)."}
def test_max_treffer_und_art_geklemmt():  # max_treffer=999 -> 50, 0 -> 1; art="rm -rf" -> ok False ohne Aufruf
def test_anfrage_laenge():                # "" und 201 Zeichen -> ok False ohne Aufruf
def test_erfolg_durchgereicht():          # Treffer und abgeschnitten unverändert
```

- [ ] **Step 2: Fehlschlag prüfen**: `python -m pytest spaces/rowboat/tests/test_werkzeuge_relay.py -q` → FAIL
- [ ] **Step 3: `pc_suche_relay.py` implementieren.**
  - Ziel ist `POST {url}/suchen` mit `{"anfrage", "art", "ordner", "max_treffer"}`.
  - Die Standard-Umsetzung von `post` nutzt `http.client.HTTPConnection(timeout=connect)` und setzt nach dem Verbinden `sock.settimeout(read)`.
  - Erlaubte `art`-Werte: `name`, `inhalt`.
- [ ] **Step 4: Tests grün**: gleicher Befehl → PASS
- [ ] **Step 5: Commit** `feat(rowboat): pc_suchen-Weiterleitung meldet "PC ist aus"`

---

### Task 3: MCP-Server `vibemind-werkzeuge`, Container, Compose, Bau-Skript

**Files:**
- Create: `spaces/rowboat/deploy/headless/werkzeuge/server.py`, `spaces/rowboat/deploy/headless/werkzeuge/Dockerfile`
- Create: `spaces/rowboat/deploy/headless/mcp-eintrag.json`
- Modify: `spaces/rowboat/deploy/headless/compose.yml` (neuer Dienst), `spaces/rowboat/deploy/headless/.dockerignore` (`werkzeuge.env`), `spaces/rowboat/deploy/headless/bauen.ps1` (Schalter `-NurWerkzeuge`, Kopie `werkzeuge/` und `werkzeuge-zugang-eintragen.sh` aus Task 5, `mcp-eintrag.json`)
- Modify: `spaces/rowboat/tests/test_headless_deploy.py`
- Test: `spaces/rowboat/tests/test_werkzeuge_server.py`

**Interfaces:**
- Consumes: Task 1 (`besitzer_aus_umgebung`, `nur_lesend_pruefen`, `GithubLeser`), Task 2 (`pc_suchen`, `PC_AUS_MELDUNG`)
- Produces:
  - Werkzeugnamen, die exakt so in Task 6 stehen: `github_repos(besitzer)`, `github_issues(repo, zustand="open", max_anzahl=20)`, `github_prs(repo, zustand="open", max_anzahl=20)`, `github_commits(repo, branch="", max_anzahl=20)`, `github_datei(repo, pfad="README.md", ref="")`, `github_ueberblick(tage=7)`, `pc_suchen(anfrage, art="name", ordner="", max_treffer=20)`
  - `WERKZEUGE: list[Callable]` (genau diese sieben)
  - `zugaenge_beim_start(umgebung: Mapping[str, str], get: HttpGet, post: HttpPost) -> dict[str, Zugang]`: Für jeden Zugang mit Token zuerst das erste Repo über `repos` holen, dann `nur_lesend_pruefen`. Ohne Repo ist der Zustand `unklar`. Nur `bereit` wird benutzt; alles andere ist gesperrt (fail-closed). Pro Besitzer wird genau eine Zeile `Zugang <Besitzer>: <zustand>` protokolliert, ohne Token.
  - `bauen(umgebung: Mapping[str, str] = os.environ) -> FastMCP`: Name `vibemind-werkzeuge`, Host `127.0.0.1`, Port aus `WERKZEUGE_PORT` (Vorgabe `8150`), `log_level="WARNING"`. Werkzeuge laufen über `anyio.to_thread.run_sync` (eigenes kleines `im_thread`, Muster `spaces/marketing/video_claw/server.py`). Dazu `GET /health` → `{"status":"ok"}`.
  - Compose-Dienst `rowboat-werkzeuge`: `build: ./werkzeuge`, `image: rowboat-werkzeuge:local`, `network_mode: host`, `restart: unless-stopped`, `mem_limit: 256m`, `env_file: ./werkzeuge.env`.
    - `environment`: `WERKZEUGE_PORT: "8150"`, `PC_SUCHE_URL: http://100.84.127.84:8149`, `GITHUB_BESITZER: Flissel=GITHUB_LESEN_FLISSEL,Vibemind-LAB=GITHUB_LESEN_VIBEMIND_LAB`.
    - Healthcheck über `python3 -c urllib … http://127.0.0.1:8150/health`.
    - `rowboat-server` hängt **nicht** davon ab.
  - `mcp-eintrag.json`: `{"vibemind-werkzeuge": {"type": "http", "url": "http://127.0.0.1:8150/mcp"}}`
  - Dockerfile: `FROM python:3.12-slim`, `pip install --no-cache-dir mcp==1.27.0`, die drei `.py` nach `/opt/werkzeuge`, `USER 1000:1000`, `CMD ["python3", "/opt/werkzeuge/server.py"]`.

- [ ] **Step 1: Failing tests schreiben**

```python
# test_werkzeuge_server.py
def test_genau_die_sieben_werkzeuge():
    tools = {t.name for t in anyio.run(server.bauen(UMG).list_tools)}
    assert tools == {"github_repos","github_issues","github_prs","github_commits","github_datei","github_ueberblick","pc_suchen"}
def test_keine_schreibwerkzeuge():   # kein Name enthält push|create|merge|delete|update|write|comment|close
def test_bindet_nur_loopback(): s = server.bauen(UMG).settings; assert (s.host, s.port) == ("127.0.0.1", 8150)
def test_health(): with TestClient(server.bauen(UMG).streamable_http_app()) as c: assert c.get("/health").json() == {"status": "ok"}
def test_start_sperrt_schreibenden_token(caplog):   # post-Attrappe 422 -> zustand darf_schreiben, Log ohne Token
def test_start_ohne_repo_ist_unklar_und_gesperrt(): ...
def test_pc_suchen_ohne_key_in_umgebung_meldet_nicht_eingerichtet(): ...
# test_headless_deploy.py (anpassen)
def dienst(...): assert sorted(compose["services"]) == ["rowboat-server", "rowboat-shim", "rowboat-werkzeuge"]
def test_compose_ohne_geheimnis():  # Schleife über alle drei Dienste; env_file nur bei shim (./claude-token) und werkzeuge (./werkzeuge.env)
def test_werkzeuge_dienst_vertrag(): # network_mode host, kein ports:, mem_limit 256m, kein depends_on von rowboat-server auf werkzeuge
def test_mcp_eintrag_ohne_geheimnis():
    e = json.loads((HEADLESS / "mcp-eintrag.json").read_text())["vibemind-werkzeuge"]
    assert e == {"type": "http", "url": "http://127.0.0.1:8150/mcp"}
def test_dockerignore_haelt_werkzeuge_env_fern(): assert "werkzeuge.env" in (HEADLESS / ".dockerignore").read_text()
def test_werkzeuge_dockerfile_pinnt_mcp(): assert "mcp==1.27.0" in ... and "USER 1000:1000" in ...
```

- [ ] **Step 2: Fehlschlag prüfen**: `python -m pytest spaces/rowboat/tests/test_werkzeuge_server.py spaces/rowboat/tests/test_headless_deploy.py -q` → FAIL
- [ ] **Step 3: Implementieren:** `server.py`, Dockerfile, Compose-Dienst, `mcp-eintrag.json`, `.dockerignore`.
  - `bauen.ps1 -NurWerkzeuge` überspringt `npm run deps`/`build:headless`. Er kopiert nur `werkzeuge/*.py`, `werkzeuge/Dockerfile`, `compose.yml`, `.dockerignore`, `werkzeuge-zugang-eintragen.sh` und `mcp-eintrag.json` nach `~/rowboat-headless/` und legt dort `werkzeuge.env` **nicht** an.
  - Ohne Schalter kopiert `bauen.ps1` dieselben Dateien zusätzlich zum bisherigen Ablauf.
- [ ] **Step 4: Tests grün**: gleicher Befehl → PASS; dazu `python -m pytest spaces/rowboat/tests -q` (keine Regression)
- [ ] **Step 5: Commit** `feat(rowboat): Werkzeug-Container vibemind-werkzeuge (GitHub lesen, PC-Suche) fuer den Mini-PC`

---

### Task 4: PC-Suchdienst (Windows, nur lesen) und Aufgabe

**Files:**
- Create: `spaces/rowboat/pc_suche/pc_suche.py` (nur stdlib; als Datei starten, nicht `-m`, wegen `spaces/rowboat/__init__.py`)
- Create: `spaces/rowboat/pc_suche/pc_suche_aufgabe.ps1` (Muster `wissens_spiegel_aufgabe.ps1`: `-Install`, `-Remove`, `-Stop`, ohne Schalter = Vordergrundlauf)
- Modify (äußeres Repo): `Vibemind_V1/vibemind.config.json` → `ports.pc_suche: 8149`
- Test: `spaces/rowboat/tests/test_pc_suche.py`

**Interfaces:**
- Consumes: `rowboat_zugang.tresor_wert(name: str) -> str | None` aus Task 5 (geladen per `importlib` aus `<VIBEMIND_ROOT>/scripts/rowboat_zugang.py`)
- Produces:
  - `@dataclass(frozen=True) Einstellungen(wurzeln: tuple[Path, ...], host: str = "100.84.127.84", port: int = 8149, vm_ip: str = "100.67.177.45", zeitlimit_s: float = 10.0, max_treffer: int = 50)`
  - `WURZELN_VORGABE = (Path("C:/Users/User/Desktop"), Path("C:/Users/User/Documents"))` (änderbar per Env `PC_SUCHE_WURZELN`, `;`-getrennt; Entscheid D2)
  - `ORDNER_AUS = {".git", "node_modules", ".venv", "venv", "__pycache__", "AppData", ".rowboat", ".ssh", ".aws", ".openfang", ".claude", ".gnupg", ".docker", ".kube", "target", "dist", "build", ".next", ".cache", ".worktrees"}`
  - `DATEI_AUS` (Muster, Groß/klein egal): `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.kdbx`, `id_rsa*`, `id_ed25519*`, `*secret*`, `*credential*`, `*token*`, `*passw*`, `client_secret*`, `server-key`, `cipher-key`, `claude-token`, `rowboat-fern.json`, `werkzeuge.env`
  - `INHALT_ENDUNGEN = {".md", ".txt", ".py", ".ts", ".tsx", ".js", ".ps1", ".sh", ".html", ".css", ".csv"}` (kein json/yaml/toml/ini/env), Inhaltssuche nur bis 2 MB je Datei
  - `ist_ausgeschlossen(pfad: Path) -> bool`; `ordner_aufloesen(ordner: str, wurzeln) -> list[Path] | None` (leer = alle Wurzeln; sonst muss der aufgelöste Pfad in einer Wurzel liegen; `None` = verboten)
  - `auszug_schwaerzen(zeile: str) -> str`: Zeilen, die `(?i)(api[_-]?key|token|secret|passw|bearer|authorization|private[_ -]?key)\s*[:=]` treffen, werden zu `[ausgeblendet]`; sonst Auszug ≤ 200 Zeichen.
  - `suchen(anfrage: str, art: str, ordner: str, max_treffer: int, einst: Einstellungen, uhr: Callable[[], float] = time.monotonic) -> dict`: Ergebnis `{"ok": True, "treffer": [{"pfad", "groesse", "geaendert", "zeile"?, "auszug"?}], "abgeschnitten": bool, "dauer_ms": int}`. `os.scandir` mit `follow_symlinks=False`, Junctions/Symlinks werden nicht betreten. Namenssuche ignoriert Groß/klein und sucht Teilstrings.
  - `handler_bauen(einst: Einstellungen, schluessel: str) -> type[BaseHTTPRequestHandler]`:
    - Gibt 403 ohne Text zurück, wenn `client_address[0] != einst.vm_ip` oder der Schlüssel nach `hmac.compare_digest` nicht passt.
    - `GET /health` → `{"status":"ok"}`, `POST /suchen` → `suchen(...)`, alles andere 405.
    - Kein Zugriffslog (`log_message` stumm).
  - `main(argv) -> int`: holt den Schlüssel per `tresor_wert("PC_SUCHE_KEY")` (bis zu 10 Versuche mit je 30 s Abstand, OpenFang startet evtl. später) und endet ohne Schlüssel mit Exit 2 („Schlüssel fehlt im Tresor“). Danach `ThreadingHTTPServer((einst.host, einst.port), …)`.

- [ ] **Step 1: Failing tests schreiben** (Baum in `tmp_path`, Wurzeln = `tmp_path`, Server in Thread auf `127.0.0.1` mit `vm_ip="127.0.0.1"` bzw. falscher IP)

```python
def test_namenssuche_findet(): assert [t["pfad"] for t in suchen("bericht", "name", "", 20, E)["treffer"]] == [str(tmp/"a"/"Bericht.md")]
@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "x.pem", "client_secret_123.json", "my_token.txt", "server-key"])
def test_geheimnisdateien_nie_treffer(name): ...   # weder Namens- noch Inhaltssuche
@pytest.mark.parametrize("ordner", ["node_modules", ".git", "AppData", ".rowboat", ".ssh"])
def test_ausgeschlossene_ordner_nicht_betreten(ordner): ...
def test_ordner_ausserhalb_der_wurzeln_verboten(): assert suchen("x", "name", "C:/Windows", 20, E)["ok"] is False
def test_punktpunkt_verboten(): assert suchen("x", "name", str(tmp/".."), 20, E)["ok"] is False
def test_symlink_nicht_gefolgt(): ...               # skip, wenn os.symlink nicht erlaubt
def test_inhalt_nur_erlaubte_endungen(): ...        # Treffer in .md ja, in .json nein
def test_auszug_geschwaerzt(): assert auszug_schwaerzen("API_KEY=abc123") == "[ausgeblendet]"
def test_zeitlimit_schneidet_ab():                  # uhr springt nach 1. Datei um 11 s
    r = suchen("a", "name", "", 20, E, uhr=springende_uhr); assert r["abgeschnitten"] is True
def test_max_treffer(): assert len(suchen("datei", "name", "", 3, E)["treffer"]) == 3 and r["abgeschnitten"] is True
def test_http_falsche_ip_403(): ...; def test_http_falscher_schluessel_403(): ...; def test_http_richtig_200(): ...
def test_http_andere_methoden_405(): ...            # PUT, DELETE, GET /suchen
def test_main_ohne_schluessel_exit_2(): ...         # tresor_wert-Attrappe liefert None, Wartezeit gepatcht
```

- [ ] **Step 2: Fehlschlag prüfen**: `python -m pytest spaces/rowboat/tests/test_pc_suche.py -q` → FAIL
- [ ] **Step 3: `pc_suche.py` implementieren und die Aufgabe `VibeMind PC-Suche` anlegen.**
  - Die Aufgabe nutzt `pythonw` aus der venv-Suche wie im Spiegel und startet bei Anmeldung.
  - Einstellungen: `RestartCount 999`, `RestartInterval 1 min`, `ExecutionTimeLimit 0`, `MultipleInstances IgnoreNew`.
  - Log nach `<VIBEMIND_ROOT>\logs\rowboat\pc_suche.log`, nur Start/Stopp/Fehlertyp, keine Suchbegriffe.
  - `-Stop` beendet die Aufgabe per `Stop-ScheduledTask`, nicht per Prozessmuster (Memory `feedback_prozess_kill_muster_trifft_eigene_shell`).
  - Eintrag `pc_suche: 8149` in `vibemind.config.json`.
- [ ] **Step 4: Tests grün**: gleicher Befehl → PASS; `python scripts/port_kollisionen.py` (äußeres Repo) → Exit 0
- [ ] **Step 5: Commits.** In vibemind-os: `feat(rowboat): PC-Suche nur lesend fuer Rowboat auf dem Mini-PC`. Im äußeren Repo: `chore(ports): pc_suche 8149`. Jeweils nur die eigenen Dateien stagen.

---

### Task 5: Zugänge Tresor → VM (`rowboat-zugang`)

**Files:**
- Create (äußeres Repo): `Vibemind_V1/scripts/rowboat_zugang.py`, `Vibemind_V1/scripts/rowboat-zugang.cmd` (wie `kanal-zugang.cmd`)
- Create: `spaces/rowboat/deploy/headless/werkzeuge-zugang-eintragen.sh` (Muster `~/sales-claw/deploy/kanal-zugang-eintragen.sh`)
- Test: `Vibemind_V1/scripts/tests/test_rowboat_zugang.py`; `spaces/rowboat/tests/test_werkzeuge_zugang_skript.py`

**Interfaces:**
- Consumes: aus `scripts/kanal_zugang.py`: `_http`, `_kopf_api`, `_issue_schluessel`, `_fehlercode`, `Abbruch`, `ERLAUBT` (Import, keine Kopie)
- Produces:
  - `PRAEFIX = "ROWBOAT_"`. Gruppen: `GRUPPEN = {"github_flissel": ["GITHUB_LESEN_FLISSEL"], "github_vibemind_lab": ["GITHUB_LESEN_VIBEMIND_LAB"], "pc_suche": ["PC_SUCHE_KEY"]}`. Die Namen müssen dieselben sein wie im Shell-Skript und im Compose-`GITHUB_BESITZER`.
  - `speichern(gruppe: str, quelle: Callable[[str], str] | None = None) -> int`: verdeckt abfragen, prüfen, im Tresor ablegen, abgleichen
  - `pc_schluessel() -> int`: erzeugt `secrets.token_urlsafe(32)`, speichert ihn unter `ROWBOAT_PC_SUCHE_KEY` (overwrite) und gleicht ab. Ausgabe nur „gespeichert“ und die ersten 12 Hex-Zeichen von SHA-256.
  - `abgleichen(gruppe: str) -> Literal["ok", "fehlt", "fehler"]`: Werte über ssh-stdin an `bash ~/rowboat-headless/werkzeuge-zugang-eintragen.sh <gruppe>`.
  - `tresor_wert(name: str) -> str | None`: für den PC-Dienst, gibt nie etwas aus.
  - CLI: `rowboat-zugang speichern <gruppe> | abgleichen <gruppe>|--alle | pc-schluessel`
  - Shell-Skript:
    - `--gruppen` und `--namen <gruppe>` (ohne Geheimnis).
    - Liest die Werte mit `read -r -s` und prüft `^[A-Za-z0-9._:/@+=~%?&-]+$`, bevor geschrieben wird.
    - Ersetzt nur die Zeilen der Gruppe in `${WERKZEUGE_ENV:-$HOME/rowboat-headless/werkzeuge.env}`: `umask 077`, Sicherung 0600, neue Datei, `mv`.
    - Erzeugt `rowboat-werkzeuge` nur dann per `docker compose up -d --no-deps --force-recreate rowboat-werkzeuge` neu, wenn der Container schon läuft.

- [ ] **Step 1: Failing tests schreiben**

```python
# Vibemind_V1/scripts/tests/test_rowboat_zugang.py (Attrappen für _http/_ssh/_issue_schluessel wie test_kanal_zugang.py)
def test_referenzen_mit_rowboat_praefix_nie_integration():
    assert all(not (PRAEFIX + n).startswith("INTEGRATION_") for ns in GRUPPEN.values() for n in ns)
def test_abgleichen_wert_nur_ueber_stdin():    # ssh-Befehl enthält Gruppe, nie den Wert; eingabe == b"wert\n"
def test_fehlender_wert_meldet_fehlt_ohne_ssh(): ...
def test_pc_schluessel_gibt_nur_hash_aus(capsys): out = capsys.readouterr(); assert wert not in out.out + out.err
def test_tresor_wert_none_bei_404(): ...
def test_unbekannte_gruppe_abbruch(): ...
# spaces/rowboat/tests/test_werkzeuge_zugang_skript.py (bash nötig; skip ohne)
def test_namen_gleichlauf():   # --namen jeder Gruppe == rowboat_zugang.GRUPPEN, GITHUB_BESITZER-Namen in compose.yml ⊆ Namen
def test_schreibt_0600_und_ersetzt_nur_gruppe(tmp_path):  # WERKZEUGE_ENV=tmp, DOCKER=false; andere Zeile bleibt
def test_unzulaessiges_zeichen_nichts_geschrieben(): ...
def test_kein_set_x_und_read_s(): t = SKRIPT.read_text(); assert "set -x" not in t.replace("set +x", "") and "read -r -s" in t
```

- [ ] **Step 2: Fehlschlag prüfen**:
  - `python -m pytest scripts/tests/test_rowboat_zugang.py -q` (im äußeren Repo)
  - `python -m pytest spaces/rowboat/tests/test_werkzeuge_zugang_skript.py -q` (in vibemind-os)
  - Erwartet: FAIL
- [ ] **Step 3: Implementieren** (Ausnahmen nur als Typ melden, `del` der Werte nach Gebrauch wie in `kanal_zugang.abgleichen`)
- [ ] **Step 4: Tests grün**: beide Befehle → PASS
- [ ] **Step 5: Commits.** Im äußeren Repo `feat(scripts): rowboat-zugang – Werkzeug-Zugaenge Tresor -> VM`, in vibemind-os `feat(rowboat): werkzeuge-zugang-eintragen.sh`.

---

### Task 6: Skill `zentrale-werkzeuge` (Anweisungen an copilot)

**Files:**
- Create: `spaces/rowboat/deploy/headless/skills/zentrale-werkzeuge/SKILL.md`
- Test: `spaces/rowboat/tests/test_zentrale_skill.py`

**Interfaces:**
- Consumes: Werkzeugnamen aus Task 3 (`server.WERKZEUGE`), MCP-Servername `vibemind-werkzeuge`, `exa`-Werkzeuge `web_search_exa`/`web_fetch_exa`, `PC_AUS_MELDUNG`
- Produces: Disk-Skill (Ziel auf der VM `~/rowboat-data/skills/zentrale-werkzeuge/SKILL.md`). Die Frontmatter `name: zentrale-werkzeuge` und `description:` nennen ausdrücklich GitHub, Repos, Issues, PRs, Commits, Docs, Doku, Dashboard, „auf meinem PC suchen“. Dazu `tools: [listMcpTools, executeMcpTool, fetch-url, todo-add, file-readText, file-grep, file-list, file-writeText, file-mkdir, app-set-data]`.

Inhalt (deutsch, knapp, verbindlich):
1. **Tabelle Anfrage → Werkzeug:**
   - GitHub-Lagebild → `github_ueberblick`
   - Einzelnes Repo → `github_issues`/`github_prs`/`github_commits`/`github_datei`
   - Docs: URL bekannt → `fetch-url` nur GET; sonst `exa`/`web_search_exa`, dann `web_fetch_exa` oder `fetch-url`. Höchstens 5 Seiten je Anfrage, nicht crawlen, bei 403/429 nicht ausweichen, sondern melden. Auf Wunsch ablegen unter `knowledge/Notes/Docs/<Thema> <JJJJ-MM-TT>.md`.
   - Dashboard → `loadSkill('apps')`, App unter `apps/<slug>/`, Daten aus Wissensnotizen (`companys/*/Wochenbild`, `Kontakte`), danach kurze Zusammenfassung plus Satz „Am PC unter Apps → <Name> öffnen“.
   - PC-Suche → `pc_suchen`.
2. **Regel Außenwirkung:** Hier gibt es keine schreibenden Werkzeuge. Push, Merge, Issue/PR anlegen, kommentieren, senden oder löschen: nicht versuchen, sondern `todo-add` „Am Desktop: <Aufgabe>“ und das so beantworten. `fetch-url` nie mit POST. Nie `addMcpServer` oder `executeCommand` für diese Aufgaben.
3. **„PC ist aus“** wörtlich weitergeben. Bei `abgeschnitten: true` „Suche unvollständig“ sagen, nie „nicht gefunden“.
4. Antworten für Telegram ≤ 3500 Zeichen, Listen auf 10 Einträge kürzen.
5. Keine Inhalte aus `config/`, keine Schlüssel zitieren.

- [ ] **Step 1: Failing tests schreiben**

```python
def test_frontmatter_und_tools():
    fm = yaml.safe_load(SKILL.read_text().split("---")[1])
    assert fm["name"] == "zentrale-werkzeuge" and not ({"executeCommand", "addMcpServer"} & set(fm["tools"]))
def test_alle_werkzeugnamen_genannt():   # jeder fn.__name__ aus server.WERKZEUGE und "vibemind-werkzeuge" kommt im Text vor
def test_skill_regel_ausserwirkung(): t = SKILL.read_text(); assert "Am Desktop:" in t and "POST" in t
def test_skill_nennt_pc_aus_woertlich(): assert PC_AUS_MELDUNG in SKILL.read_text()
def test_skill_nennt_abgeschnitten(): assert "abgeschnitten" in SKILL.read_text() and "unvollständig" in SKILL.read_text()
def test_description_triggerwoerter(): # GitHub, Docs, Dashboard, PC in description
```

- [ ] **Step 2: Fehlschlag prüfen**: `python -m pytest spaces/rowboat/tests/test_zentrale_skill.py -q` → FAIL
- [ ] **Step 3: `SKILL.md` schreiben**
- [ ] **Step 4: Tests grün** → PASS
- [ ] **Step 5: Commit** `feat(rowboat): Skill zentrale-werkzeuge fuer copilot auf dem Mini-PC`

---

### Task 7: Dashboard sichtbar am PC (Spike, dann Tunnel im Fern-Modus)

**Files:**
- Modify: `spaces/rowboat/scripts/start_desktop.py` (`jobs()` im Fern-Modus)
- Test: `spaces/rowboat/tests/test_start_desktop.py`

**Interfaces:**
- Produces: `apps_tunnel_befehl(ssh_ziel: str = "offload-vm") -> list[str]` = `["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-L", "127.0.0.1:3210:127.0.0.1:3210", ssh_ziel]`. Im Fern-Modus startet `start()` die Jobs `Rowboat` und `Apps-Tunnel`, sonst wie bisher.

- [ ] **Step 1 [OK]: Spike.**
  - Auf der VM eine Test-App per Telegram oder Desktop bauen lassen („Bau mir ein Dashboard mit der Anzahl Kontakte je Kategorie“).
  - Am PC von Hand `ssh -N -L 127.0.0.1:3210:127.0.0.1:3210 offload-vm` starten.
  - Erwartet: In der Desktop-App (Fern-Modus) zeigt **Apps** die App und sie lädt. Zusätzlich liefert `curl -s -o NUL -w "%{http_code}" http://<slug>.apps.localhost:3210/` den Code 200.
  - **Gate:** Bei rotem Spike fällt Task 7 Step 2–5 weg. Ersatz ist eine Zeile in Task 6: Dashboard als Markdown-Notiz `knowledge/Dashboards/<Name>.md` mit Tabellen; VM-eigen, der Spiegel bringt sie an den PC. Der Betreiber erfährt das als Entscheid D4.
- [ ] **Step 2: Failing tests schreiben**

```python
def test_fern_modus_startet_apps_tunnel(): # start() mit Fern-Datei und FakePopen: Namen == ["Rowboat", "Apps-Tunnel"], Befehl == apps_tunnel_befehl()
def test_lokal_kein_tunnel(): ...
def test_tunnel_bindet_nur_loopback(): assert "127.0.0.1:3210:127.0.0.1:3210" in apps_tunnel_befehl()
```

- [ ] **Step 3: Fehlschlag prüfen**: `python -m pytest spaces/rowboat/tests/test_start_desktop.py -q` → FAIL
- [ ] **Step 4: Implementieren, Tests grün.** Der Tunnel stirbt mit dem Rowboat-Job wie die anderen Jobs (`stop()`) → PASS
- [ ] **Step 5: Commit** `feat(rowboat): Apps-Tunnel im Fern-Modus fuer Dashboards vom Mini-PC`

---

### Task 8: Ausrollen und Abnahme (nur mit Betreiber-OK)

**Files:**
- Modify: `spaces/rowboat/deploy/headless/README.md` (Abschnitt „Werkzeuge (Teil D)“: Dienst, Port, Zugänge, Probe, Rückweg)
- Modify: `spaces/rowboat/deploy/headless/UMSTELLUNG.md` (Betreiberhinweise: neue `mcp.json`-Zeile, Skill, PC-Suche)

- [ ] **Step 1: README/UMSTELLUNG ergänzen und committen.** `docs(rowboat): Teil D Werkzeuge – Betrieb und Rueckweg`
- [ ] **Step 2 [OK]: Tokens anlegen und ablegen.**
  - Der Betreiber erstellt im Browser die fein granulierten Tokens aus den „Global Constraints“: als Flissel (Besitzer Flissel, alle Repos) und als Vibemind-LAB (Besitzer Vibemind-LAB). Laufzeit nach D1.
  - Dann `scripts\rowboat-zugang.cmd speichern github_flissel` und `… github_vibemind_lab`.
  - Danach `scripts\rowboat-zugang.cmd pc-schluessel`.
- [ ] **Step 3 [OK]: VM.**
  - `pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurWerkzeuge`
  - `scripts\rowboat-zugang.cmd abgleichen --alle`; prüfen mit `ssh offload-vm 'stat -c %a ~/rowboat-headless/werkzeuge.env'` → `600`
  - `ssh offload-vm 'cd ~/rowboat-headless && docker compose up -d --build rowboat-werkzeuge'`
  - Probe: `docker compose logs rowboat-werkzeuge | grep "^Zugang"` → je Besitzer `bereit`. Fehlt `bereit`, Rollout stoppen.
  - Probe: `curl -s http://127.0.0.1:8150/health` → `{"status":"ok"}`
- [ ] **Step 4 [OK]: Rowboat verbinden.**
  - Sicherung: `cp ~/rowboat-data/config/mcp.json ~/rowboat-data/config/mcp.json.vor-teil-d`.
  - Eintrag einmischen, `exa` bleibt: `python3 -c` liest `mcp.json` und `~/rowboat-headless/mcp-eintrag.json`, setzt `mcpServers["vibemind-werkzeuge"]`, schreibt über eine temporäre Datei und `os.replace`, Modus 0600.
  - Skill: `ssh offload-vm 'mkdir -p ~/rowboat-data/skills/zentrale-werkzeuge'` und `scp spaces/rowboat/deploy/headless/skills/zentrale-werkzeuge/SKILL.md offload-vm:~/rowboat-data/skills/zentrale-werkzeuge/`.
- [ ] **Step 5 [OK]: PC.**
  - Firewall (Admin): `New-NetFirewallRule -DisplayName "VibeMind PC-Suche (VM)" -Direction Inbound -Protocol TCP -LocalPort 8149 -RemoteAddress 100.67.177.45 -Action Allow`
  - Aufgabe: `pwsh -File spaces\rowboat\pc_suche\pc_suche_aufgabe.ps1 -Install` (aus dem vibemind-os-Haupt-Checkout)
  - Betrieb aktualisieren: `pwsh -File scripts/voice_betrieb_update.ps1` (äußeres Repo), dann die Aufgabe „VibeMind Rowboat (Einbettung)“ neu starten. Erwartet: Tunnel-Job läuft.
  - Probe von der VM: `curl -s -o /dev/null -w '%{http_code}' -X POST http://100.84.127.84:8149/suchen` ohne Schlüssel → `403`.
  - Probe aus dem LAN oder von einem anderen Tailnet-Gerät: Timeout oder `403`.
- [ ] **Step 6 [OK]: Abnahme per Telegram** (Antwort und Budget-Zeile je Nachricht notieren)
  1. „Was ist neu in meinen GitHub-Projekten diese Woche?“ → Zusammenfassung aus `github_ueberblick`.
  2. „Push bitte den Branch X in vibemind-os“ → kein Versuch; Todo „Am Desktop: …“ steht in `todo.md`.
  3. „Hol mir die neuesten Docs zu FastMCP streamable-http und speicher sie“ → Notiz unter `knowledge/Notes/Docs/`.
  4. „Bau mir ein Dashboard: Kontakte je Kategorie“ → App unter `apps/`; am PC über Apps sichtbar (oder D4-Ersatz).
  5. „Such auf meinem PC nach Rowboat-Fern“ → Treffer ohne `rowboat-fern.json` selbst; „such nach .env“ → keine Geheimnisdatei.
  6. PC-Suche aus (`pc_suche_aufgabe.ps1 -Stop`), Frage 5 wiederholen → wörtlich „PC ist aus – …“ in < 30 s. Danach Aufgabe wieder starten.
  - Hängt ein Lauf mit „waiting for a permission approval“ (Klassifizierer lehnte MCP-Lesen ab), notieren. Das ist Eingabe für D3 und kein Rollout-Stopp.
- [ ] **Step 7 [OK]: Leck-Scan.**
  - VM: `docker compose logs rowboat-werkzeuge rowboat-server 2>&1 | grep -c github_pat_` → `0`
  - `grep -c github_pat_ ~/rowboat-data/config/mcp.json` → `0`
  - PC-Log `logs\rowboat\pc_suche.log` enthält keinen Suchbegriff und keinen Schlüssel.
  - Ergebnis im WORKBOARD-Eintrag vermerken, Claim schließen, Memory `project_rowboat_zentrale` (bzw. neue Datei) ergänzen.

**Rückweg (Reihenfolge):**
1. `mcp.json.vor-teil-d` zurückkopieren.
2. `~/rowboat-data/skills/zentrale-werkzeuge` umbenennen (`.aus`), nicht löschen.
3. `docker compose stop rowboat-werkzeuge`.
4. `pc_suche_aufgabe.ps1 -Remove`, Firewall-Regel deaktivieren.
5. Tokens auf GitHub widerrufen, wenn sie nicht mehr gebraucht werden.

---

## Betreiber-Entscheide (vor Task 8)

- **D1 GitHub-Besitzer und Laufzeit:** Flissel und Vibemind-LAB (zwei Tokens). Soll die Org `vibemind-space` als dritter Besitzer dazu (Gruppe `github_vibemind_space`)? Laufzeit: Vorschlag 1 Jahr mit Kalender-Erinnerung.
- **D2 PC-Suche, Umfang:** Wurzeln `Desktop` und `Documents`. `Downloads` und `secondbrain` sind ausgeschlossen bzw. offen. Inhaltssuche in Text-/Code-Dateien an oder nur Namenssuche? Auszüge gehen an Telegram und das Claude-Abo.
- **D3 Härtung der Kanal-Läufe (Fork-Patch, eigener Teil E):** Vorbestehend kann ein Telegram-Lauf `fetch-url` mit POST nutzen (ohne Freigabe) und `/data/config/channels.json` lesen (Bot-Schlüssel, ohne Freigabe). Mit Klassifizierer-OK gehen auch `executeCommand` und `addMcpServer`. Spec 7 ist damit nur für Teil-D-Werkzeuge hart erfüllt. Vorschlag: Bridge markiert Kanal-Läufe, die Laufzeit entzieht dort POST, Shell und `addMcpServer`, und lesende MCP-Server bekommen eine Freigabeliste ohne Klassifizierer.
- **D4 Dashboard-Ansicht:** Tunnel `127.0.0.1:3210` im Fern-Modus (wenn der Spike in Task 7 grün ist) oder Markdown-Notizen `knowledge/Dashboards/`.
- **D5 Exa ohne Schlüssel:** Der gehostete Dienst ist ratenbegrenzt; ein späterer Exa-Schlüssel käme über den Tresor in einen eigenen Container, nicht nach `mcp.json`.
- **D6 Budget:** Jeder MCP-Aufruf eines Telegram-Laufs kostet voraussichtlich einen zusätzlichen Klassifizierer-Aufruf. Diese Aufrufe zählen gegen 150/Tag (`rowboat-server`). `github_ueberblick` bündelt deshalb. Grenze anheben?
