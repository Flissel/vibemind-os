# Lieferung Block 2: Herkunft einer Anfrage

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben gelten nach dem Stand vom 23.11. und wurden am Quelltext geprüft; bitte beim
Einbauen noch einmal gegenlesen, denn `plan_executor.py` und `introspection.py` werden gerade von anderer Seite
bearbeitet und die Zeilen verschieben sich.

## Das Problem in einem Satz

Das Gehirn kann heute nicht unterscheiden, ob eine Anfrage aus einer Eingabe des Betreibers kommt oder von
irgendeinem anderen Aufrufer. Ereignisse mit Außenwirkung (Vorschläge anlegen, Kontakte suchen, Post prüfen,
einen Desktop-Auftrag starten) sollen aber nur auf Wunsch des Betreibers laufen.

## Was mitgeliefert wird

Ordner `spaces/auftragsbuch/lieferung/herkunft/`, ohne Abhängigkeiten außer der Standardbibliothek:

- `herkunft.py` mit
  - `EINGAENGE_BETREIBER = frozenset({"app", "telegram-betreiber"})`
  - `eingang_pruefen(eingang, schluessel, umgebung) -> str`: gibt den Namen des Eingangs zurück, wenn der
    SHA-256 des Schlüssels (per `hmac.compare_digest`) zum Hash in der Umgebung passt, sonst `"unbekannt"`.
    Wirft nie, nennt den Schlüssel nie.
  - `braucht_betreiber(ereignis) -> bool`: wahr bei `required_origin: betreiber`.
  - `erlaubt(herkunft, ereignis) -> bool`: falsch nur, wenn der Betreiber verlangt wird und die Herkunft keiner
    seiner Eingänge ist.
  - `ABLEHNUNG = "Nur aus einer Eingabe des Betreibers"`
- `test_herkunft.py` (14 Tests). Aufruf von der Repo-Wurzel:
  `python -m pytest spaces/auftragsbuch/lieferung/herkunft/test_herkunft.py -q -p no:cacheprovider`.
  Der Test setzt seinen eigenen Ordner selbst auf den Suchpfad, ein Paket ist nicht nötig.

Beim Einbauen die Datei ins Gehirn kopieren (Vorschlag: `brain/the_brain/core/herkunft.py`) und dort
`from .herkunft import ...` verwenden.

## Schlüssel und Umgebungsvariablen

Für jeden Eingang des Betreibers gibt es eine Umgebungsvariable `BRAIN_EINGANG_<NAME>_SHA256`. `<NAME>` ist der
Eingangsname in Großbuchstaben, `-` wird zu `_`:

| Eingang | Variable |
| --- | --- |
| `app` | `BRAIN_EINGANG_APP_SHA256` |
| `telegram-betreiber` | `BRAIN_EINGANG_TELEGRAM_BETREIBER_SHA256` |

Der Wert ist der kleingeschriebene Hex-SHA-256 des Schlüssels, nie der Schlüssel selbst (Großbuchstaben im
Hash werden toleriert). Nur Namen aus `EINGAENGE_BETREIBER` werden je angenommen; jeder andere Name ergibt
`"unbekannt"`, auch wenn zufällig eine passende Variable gesetzt wäre. Ist die Variable nicht gesetzt oder
leer, ergibt das ebenfalls `"unbekannt"` (sicherer Ausfall: ohne Einrichtung öffnet sich nichts).

### Schlüssel und Hash erzeugen, ohne dass der Schlüssel auf einer Kommandozeile oder im Protokoll auftaucht

Der Betreiber erzeugt den Schlüssel in eine Datei, die nur er lesen kann, und rechnet daraus den Hash. Der
Schlüssel wird nirgends ausgegeben, nur der Hash:

```python
# schluessel_erzeugen.py <Datei> : legt die Datei an und druckt nur den Hash
import hashlib, secrets, sys
from pathlib import Path

pfad = Path(sys.argv[1])
if pfad.exists():
    raise SystemExit("Datei existiert schon, nichts überschrieben")
schluessel = secrets.token_urlsafe(32)
pfad.write_text(schluessel, encoding="utf-8")
print(hashlib.sha256(schluessel.encode("utf-8")).hexdigest())
```

Den Hash trägt man als Wert der Variable beim Gehirn ein. Den Inhalt der Datei bekommt nur der jeweilige
Aufrufer (App-Hülle, Betreiber-Chat) aus seiner eigenen Geheimnisablage. Für einen schon vorhandenen Schlüssel
gilt dasselbe Muster: aus der Datei lesen (`Path(...).read_text().strip()`), nie als Argument übergeben.

## Was eingebaut werden soll

### 1. Eingang: nur aus den Kopfzeilen

Datei `brain/the_brain/web/routers/introspection.py`, Funktion `multihop_execute` (ab Zeile 2574). Direkt vor
dem Aufruf von `pe.execute` (Zeile 2835,
`await _asyncio.to_thread(pe.execute, plan, antwortkanal=antwortkanal)`), neben der Bildung von
`antwortkanal` (Zeile 2834):

```python
herkunft = eingang_pruefen(
    request.headers.get("X-Brain-Eingang"),
    request.headers.get("X-Brain-Eingang-Schluessel"),
    os.environ,
)
...
exec_result = await _asyncio.to_thread(pe.execute, plan, antwortkanal=antwortkanal, herkunft=herkunft)
```

Wichtig: Die Herkunft kommt **nur** aus diesen zwei Kopfzeilen. Nie aus dem Körper (`body`), nie aus
`context`, nie aus `antwortkanal` (dort gilt weiter nur die feste Liste `api`, `telegram`, `dashboard`).

### 2. Durchreichen im Planausführer

Datei `brain/the_brain/core/plan_executor.py`:

- `execute(...)` (Zeile 721, Parameterliste bis 730): neuer Parameter `herkunft: Optional[str] = None`.
- `plan_ctx` (Zeilen 826 bis 842, dort stehen schon `confirmed_events` und `antwortkanal`): neuer Eintrag
  `"herkunft": herkunft or "unbekannt"`.
- Der Plan-Rest, der bei einem wartenden Auftrag gespeichert wird: `_auftrag_plan_rest_speichern`
  (Zeile 2260), Wörterbuch `plan_rest` (Zeilen 2323 bis 2328, dort steht schon `antwortkanal`): ergänzen um
  `"herkunft": plan_ctx.get("herkunft")`.
- Beim Wiederaufnehmen: `core/nachfasser.py` Zeile 161 (`self.plan_executor.execute(Plan.from_dict(rest["plan"]),
  start_state=state, antwortkanal=rest.get("antwortkanal"))`) bekommt zusätzlich
  `herkunft=rest.get("herkunft")`. Ein Plan-Rest ohne das Feld (aus der Zeit vor dieser Änderung) ergibt `None`
  und damit `"unbekannt"`: ein fortgesetzter Altbestand darf keine Betreiber-Ereignisse mehr auslösen. Das ist
  gewollt.

### 3. Tor im Hop

Datei `brain/the_brain/core/plan_executor.py`, `_exec_hop_kern` (Zeile 1442). Das Muster `confirmed_events`
steht bei Zeile 1519 (`confirmed = event_id in plan_ctx.get("confirmed_events", set())`), aber nur im
Desktop-Zweig. Achtung bei der Platzierung: `event_id` wird in Zeile 1511 aus `canonical_space_event_id(...)`
gebildet, aber für die Fähigkeiten `desktop_skill` und `browser_automation` in Zeile 1518
(`event_id = desktop_route.event_id`) überschrieben. Erst danach ist es endgültig, und genau dort entsteht
auch `desktop.task`. Ein Tor direkt nach Zeile 1511 sähe die falsche Kennung und ließe `desktop.task` durch.

Das Tor gehört deshalb **nach dem ganzen Desktop-Block** (nach dem Zweig
`if hop.capability in ("desktop_skill", "browser_automation"):`, der mit dem `return HopResult(...)` bei
Zeile 1531 endet) und **vor** den Kommentar „Resolve deterministic MCP metadata ...“ bzw.
`_canonical_space_event_agent(event_id)` (Zeile 1532 ff.). Dort ist `event_id` für alle Zweige endgültig.
Alternativ dasselbe Tor zusätzlich im Desktop-Zweig direkt nach Zeile 1518; zwingend ist nur: nie vor der
Überschreibung.

```python
ereignis = <Verzeichniseintrag zu event_id>   # siehe Abschnitt 5
if not erlaubt(plan_ctx.get("herkunft"), ereignis):
    return HopResult(step_id=hop.step_id, ok=False, error=ABLEHNUNG, capability=hop.capability,
                     target=target, rendered_arg=rendered_arg, kg_hits=kg_hits,
                     elapsed_s=time.time() - t0, contract_pass=False, reward=-1.0)
```

(Die Felder entsprechen dem bestehenden `HopResult` bei Zeile 1521 ff.; Zeilenangaben vor dem Einfügen gegenlesen.)

Das Tor gilt auch für ein **ausdrücklich genanntes** `execution_target` (Zeilen 1461 bis 1487: Ziel aus dem
Hop oder aus der Registry-Auflösung). Ein Aufrufer darf das Tor nicht umgehen, indem er statt der Fähigkeit
direkt ein Ziel `mcp:<agent>:<server>:<werkzeug>` nennt. Dort ist kein Ereignisname bekannt; deshalb das
Ereignis anhand des Ziels im Verzeichnis nachschlagen (Agent, Server, Werkzeug) und dieselbe Prüfung machen.
Die Stelle, an der das Ziel für `mcp:` zerlegt wird (`find_registered_mcp_authority`, Zeilen 1879 bis 1884),
ist dafür der natürliche Ort.

### 4. Zweite Prüfung an der Laufzeitgrenze

- `core/openfang_runtime_authority.py`, `RuntimeInvocationContext` (Zeile 18, eingefrorene Datenklasse): neues
  Feld `herkunft: str = "unbekannt"` (mit Vorgabewert, damit `deterministic_gateway.py` Zeile 57 und die
  Tests weiter laufen).
- `core/plan_executor.py` Zeile 1887: beim Bauen von `RuntimeInvocationContext(...)` zusätzlich
  `herkunft=plan_ctx.get("herkunft") or "unbekannt"`.
- `core/capability_targets.py`, `McpExecutor._call` (Klasse ab Zeile 855, `_call` bei Zeile 980; nicht in
  `openfang_runtime_authority.py`): direkt nach der Prüfung
  `authority.space_id != runtime_context.space_id ...` (Zeile 996) dasselbe Tor ein zweites Mal, mit dem
  Verzeichniseintrag zur Kombination Agent/Server/Werkzeug, und bei Ablehnung `PermissionError(ABLEHNUNG)`
  werfen (wie die Nachbarprüfungen). So hält die Sperre auch, wenn jemand den Hop am Planausführer vorbei in
  den Ausführer bringt.
- `deterministic_gateway.py` Zeile 57 baut ebenfalls einen `RuntimeInvocationContext`; mit dem Vorgabewert
  `"unbekannt"` bleibt dieser Weg für Betreiber-Ereignisse gesperrt. Braucht er welche, muss er die Herkunft
  ausdrücklich bekommen. Das ist eine eigene Entscheidung und nicht Teil dieser Lieferung.

### 5. Verzeichnis: wo `required_origin` steht

Das Verzeichnis der Ereignisse ist `config/space_agent_registry.yml` (Kopfzeile dort: „Single source of
truth: event_type → space → agent → mcp_scope → tool_hint“). Das neue Feld steht dort am Ereignis, neben
`requires_confirmation` (Beispiel `desktop.task`, Zeile 251 ff.):

```yaml
      desktop.task:
        tool: desktop_task_start
        ...
        required_origin: betreiber
```

Zu setzen an:

| Ereignis | Stand im Verzeichnis heute |
| --- | --- |
| `desktop.task` | vorhanden (Zeile 251), Feld ergänzen |
| `rowboat.vorschlaege_anlegen` | **noch kein Ereignis.** `vorschlaege_anlegen` ist bisher nur eine Auftragsart in `spaces/_contract/rowboat.contract.yaml` (Zeile 56). Das Feld kommt an das Ereignis, sobald es eingetragen wird. |
| `sales.kontakt_finden` | **noch kein Ereignis**, auch keine Auftragsart; gleiches Vorgehen |
| `sales.post_pruefen` | **noch kein Ereignis.** `post_pruefen` ist bisher nur eine Auftragsart in `spaces/_contract/sales.contract.yaml` (Zeile 16) |

Nicht setzen an `sales.auftrag_probe` und `sales.auftrag_lesen` (Zeilen 478 und 483): Sie sind die
Vor- und Nachprüfungen und müssen auch aus einer unbekannten Herkunft laufen.

Ob der Lader des Verzeichnisses ein unbekanntes Feld toleriert, wurde nicht geprüft; beim Einbauen nachsehen.

### 6. Aufrufer, die die Kopfzeilen senden

- Sprach- und Tipp-Weg der App: vorgesehen ist `voice/python/swarm/routing/brain_multihop_bridge.py` (Hülle).
  **Diese Datei gibt es in diesem Stand nicht:** `voice/` ist in diesem Arbeitsbaum leer, und unter dem Pfad
  liegt auch in `Vibemind_V1` nichts. Die Koordination muss die richtige Stelle der App-Hülle benennen, die
  `/api/multihop/execute` aufruft; dort kommen die beiden Kopfzeilen hin, mit dem Eingang `app` und dem
  Schlüssel aus dessen Geheimnisablage.
- Betreiber-Chat: `scripts/openfang_brain_gateway_wrapper.py`. Diese Datei gibt es in diesem Arbeitsbaum nicht
  (der Ordner `scripts/` enthält sie nicht); sie liegt nur im Stand `Vibemind_V1` auf der Maschine und ruft dort
  `POST /api/multihop/execute` auf. Eingang `telegram-betreiber`, Schlüssel aus der Geheimnisablage,
  ebenfalls als Kopfzeilen.
- Das Dashboard des Gehirns zählt **nicht** als Eingang des Betreibers, solange es keine Anmeldung hat. Es
  sendet keine Kopfzeilen und bekommt damit `"unbekannt"`. **[Wahl]** Bekommt es später eine Anmeldung, kann es
  als eigener Eingang in `EINGAENGE_BETREIBER` aufgenommen werden (eine Zeile plus Variable).

## Grenzen, die man kennen muss

- Die Herkunft ersetzt keine Anmeldung. Der Port 5000 des Gehirns lauscht auf allen Schnittstellen und hat
  keine Anmeldung. Wer die Kopfzeilen samt gültigem Schlüssel schicken kann, gilt als Betreiber.
- Der Schlüssel reist im Klartext in der Kopfzeile. Solange die Strecke nur lokal ist, ist das vertretbar;
  über ein Netz nur mit gesicherter Verbindung.
- Schlüssel wechseln heißt: neuen Hash in die Variable, den Aufrufer neu bestücken, beides gleichzeitig.
- Der Test belegt die Prüfung selbst, nicht das Einhängen. Nach dem Einbauen je ein Test am Hop: ohne Kopfzeilen
  abgelehnt, mit richtigen angenommen, ausdrücklich genanntes `execution_target` ohne Kopfzeilen abgelehnt,
  `desktop_skill`- und `browser_automation`-Hop ohne Eingangs-Kopf scheitert mit `ABLEHNUNG` (belegt, dass das
  Tor nach der Überschreibung von `event_id` sitzt),
  fortgesetzter Plan-Rest bewahrt die Herkunft.
