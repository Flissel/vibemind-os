# Lieferung Block 2: Herkunft einer Anfrage

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben sind gegen `origin/master` (`948f436f`, geprüft am 23.11.; vorher `ae9f2dfe`) nachgezogen.
Was master inzwischen an denselben Stellen geändert hat (vor allem die Herkunft "getippt" der Desktop-Schleife),
steht in `kollisionen-948f436f.md`. Bitte beim Einbauen trotzdem noch einmal gegenlesen, denn die Zeilen
verschieben sich mit jeder weiteren Änderung.

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
  - `erlaubt_werkzeug(herkunft, server, tool, ereignis_oder_None) -> bool`: das Tor für ein ausdrücklich genanntes Ziel
    `mcp:<agent>:<server>:<werkzeug>`. Ein Werkzeug am Server `auftragsbuch`, zu dem die Suche im Verzeichnis **kein**
    Ereignis fand (`None` oder leer), wird abgelehnt, auch bei Betreiber-Herkunft; ausgenommen sind `auftrag_lesen`
    und `auftrag_probe`. Jeder andere Server verhält sich wie `erlaubt`. Wirft nie.
  - `ABLEHNUNG = "Nur aus einer Eingabe des Betreibers"`
- `test_herkunft.py` (19 Tests). Aufruf von der Repo-Wurzel:
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

Datei `brain/the_brain/web/routers/introspection.py`, Funktion `multihop_execute` (ab Zeile 2588). Direkt vor
dem Aufruf von `pe.execute` (heute Zeile 2852 bis 2859; seit master schon mit dem Parameter
`eingabe_herkunft=_herkunft_pruefen(...)`, der die Herkunft "getippt" der Desktop-Schleife trägt, siehe
`kollisionen-948f436f.md`, Nr. 1), neben der Bildung von `antwortkanal` (Zeile 2848):

```python
herkunft = eingang_pruefen(
    request.headers.get("X-Brain-Eingang"),
    request.headers.get("X-Brain-Eingang-Schluessel"),
    os.environ,
)
...
exec_result = await _asyncio.to_thread(
    pe.execute, plan, antwortkanal=antwortkanal,
    eingabe_herkunft=<unverändert, wie auf master>,   # Desktop-Schleife, eigenes Zeichen
    herkunft=herkunft,                                # dieser Block
)
```

Beide Parameter bleiben getrennt, solange die Koordination nichts anderes entscheidet (Begründung in
`kollisionen-948f436f.md`, Nr. 1).

Wichtig: Die Herkunft kommt **nur** aus diesen zwei Kopfzeilen. Nie aus dem Körper (`body`), nie aus
`context`, nie aus `antwortkanal` (dort gilt weiter nur die feste Liste `api`, `telegram`, `dashboard`).

### 2. Durchreichen im Planausführer

Datei `brain/the_brain/core/plan_executor.py`:

- `execute(...)` (Zeile 726, Parameterliste bis 736): neuer Parameter `herkunft: Optional[str] = None`.
- `plan_ctx` (Zeilen 832 bis 852, dort stehen schon `confirmed_events`, `antwortkanal` und seit master `eingabe_herkunft`): neuer Eintrag
  `"herkunft": herkunft or "unbekannt"`.
- Der Plan-Rest, der bei einem wartenden Auftrag gespeichert wird: `_auftrag_plan_rest_speichern`
  (Zeile 2325), Wörterbuch `plan_rest` (Zeilen 2388 bis 2393, dort steht schon `antwortkanal`): ergänzen um
  `"herkunft": plan_ctx.get("herkunft")`.
- Beim Wiederaufnehmen: `core/nachfasser.py` Zeile 161 (`self.plan_executor.execute(Plan.from_dict(rest["plan"]),
  start_state=state, antwortkanal=rest.get("antwortkanal"))`) bekommt zusätzlich
  `herkunft=rest.get("herkunft")`. Ein Plan-Rest ohne das Feld (aus der Zeit vor dieser Änderung) ergibt `None`
  und damit `"unbekannt"`: ein fortgesetzter Altbestand darf keine Betreiber-Ereignisse mehr auslösen. Das ist
  gewollt.

### 3. Tor im Hop

Datei `brain/the_brain/core/plan_executor.py`, `_exec_hop_kern` (Zeile 1452). Das Muster `confirmed_events`
steht bei Zeile 1529 (`confirmed = event_id in plan_ctx.get("confirmed_events", set())`), aber nur im
Desktop-Zweig. Achtung bei der Platzierung: `event_id` wird in Zeile 1521 aus `canonical_space_event_id(...)`
gebildet, aber für die Fähigkeiten `desktop_skill` und `browser_automation` in Zeile 1528
(`event_id = desktop_route.event_id`) überschrieben. Erst danach ist es endgültig, und genau dort entsteht
auch `desktop.task`. Ein Tor direkt nach Zeile 1521 sähe die falsche Kennung und ließe `desktop.task` durch.

Das Tor gehört deshalb **nach dem ganzen Desktop-Block** (nach dem Zweig
`if hop.capability in ("desktop_skill", "browser_automation"):`, der mit dem `return HopResult(...)` bei
Zeile 1541 endet) und **vor** den Kommentar „Resolve deterministic MCP metadata ...“ bzw.
`_canonical_space_event_agent(event_id)` (Zeile 1543 ff.). Dort ist `event_id` für alle Zweige endgültig.
Alternativ dasselbe Tor zusätzlich im Desktop-Zweig direkt nach Zeile 1528; zwingend ist nur: nie vor der
Überschreibung.

```python
ereignis = <Verzeichniseintrag zu event_id>   # siehe Abschnitt 5
if not erlaubt(plan_ctx.get("herkunft"), ereignis):
    return HopResult(step_id=hop.step_id, ok=False, error=ABLEHNUNG, capability=hop.capability,
                     target=target, rendered_arg=rendered_arg, kg_hits=kg_hits,
                     elapsed_s=time.time() - t0, contract_pass=False, reward=-1.0)
```

(Die Felder entsprechen dem bestehenden `HopResult` bei Zeile 1531 ff.; Zeilenangaben vor dem Einfügen gegenlesen.)

Das Tor gilt auch für ein **ausdrücklich genanntes** `execution_target` (Zeilen 1471 bis 1497: Ziel aus dem
Hop oder aus der Registry-Auflösung). Ein Aufrufer darf das Tor nicht umgehen, indem er statt der Fähigkeit
direkt ein Ziel `mcp:<agent>:<server>:<werkzeug>` nennt. Dort ist kein Ereignisname bekannt; deshalb das
Ereignis anhand des Ziels im Verzeichnis nachschlagen (Agent, Server, Werkzeug) und dieselbe Prüfung machen.
Die Stelle, an der das Ziel für `mcp:` zerlegt wird (`find_registered_mcp_authority`, Zeilen 1919 bis 1924),
ist dafür der natürliche Ort.

**Kein Durchlass bei Fehlgriff.** Findet die Suche zu (Agent, Server, Werkzeug) kein Ereignis, darf sie nicht `{}`
weitergeben, denn `erlaubt` lässt ein Ereignis ohne `required_origin` durch. Stattdessen
`erlaubt_werkzeug(plan_ctx.get("herkunft"), server, werkzeug, ereignis_oder_None)` aufrufen (`None`, wenn nichts
gefunden wurde): Ein Werkzeug am Auftragsbuch ohne Verzeichnis-Ereignis wird abgelehnt, nur `auftrag_lesen` und
`auftrag_probe` laufen auch ohne. Das ist wichtig, weil Block 1 den Suchschlüssel ändert (Träger `brain-rowboat`
statt Space-Agent); trifft die Suche danach daneben, bleibt das Tor zu, statt offen zu stehen. Dasselbe gilt in der
zweiten Prüfung an der Laufzeitgrenze (Abschnitt 4).

### 4. Zweite Prüfung an der Laufzeitgrenze

- `core/openfang_runtime_authority.py`, `RuntimeInvocationContext` (Zeile 18, eingefrorene Datenklasse): neues
  Feld `herkunft: str = "unbekannt"` (mit Vorgabewert, damit `brain/the_brain/deterministic_gateway.py` Zeile 57 und die
  Tests weiter laufen).
- `core/plan_executor.py` Zeile 1927: beim Bauen von `RuntimeInvocationContext(...)` zusätzlich
  `herkunft=plan_ctx.get("herkunft") or "unbekannt"`.
- `core/capability_targets.py`, `McpExecutor._call` (Klasse ab Zeile 855, `_call` bei Zeile 980; nicht in
  `openfang_runtime_authority.py`): direkt nach der Prüfung
  `authority.space_id != runtime_context.space_id ...` (Zeile 996) dasselbe Tor ein zweites Mal, mit dem
  Verzeichniseintrag zur Kombination Agent/Server/Werkzeug, und bei Ablehnung `PermissionError(ABLEHNUNG)`
  werfen (wie die Nachbarprüfungen). So hält die Sperre auch, wenn jemand den Hop am Planausführer vorbei in
  den Ausführer bringt.
- `brain/the_brain/deterministic_gateway.py` Zeile 57 baut ebenfalls einen `RuntimeInvocationContext`; mit dem Vorgabewert
  `"unbekannt"` bleibt dieser Weg für Betreiber-Ereignisse gesperrt. Braucht er welche, muss er die Herkunft
  ausdrücklich bekommen. Das ist eine eigene Entscheidung und nicht Teil dieser Lieferung.

### 5. Verzeichnis: wo `required_origin` steht

Das Verzeichnis der Ereignisse ist `config/space_agent_registry.yml` (Kopfzeile dort: „Single source of
truth: event_type → space → agent → mcp_scope → tool_hint“). Das neue Feld steht dort am Ereignis, neben
`requires_confirmation` (Beispiel `desktop.task`, Zeile 276 ff.):

```yaml
      desktop.task:
        tool: desktop_task_start
        ...
        required_origin: betreiber
```

Zu setzen an:

| Ereignis | Stand im Verzeichnis heute |
| --- | --- |
| `desktop.task` | vorhanden (Zeile 276). **Nicht blind ergänzen:** master sperrt `desktop.task` schon selbst über `eingabe_herkunft == "getippt"`, siehe `kollisionen-948f436f.md`, Nr. 1 |
| `rowboat.vorschlaege_anlegen` | **noch kein Ereignis.** `vorschlaege_anlegen` ist bisher nur eine Auftragsart in `spaces/_contract/rowboat.contract.yaml` (Zeile 56). Das Feld kommt an das Ereignis, sobald es eingetragen wird. |
| `sales.kontakt_finden` | **noch kein Ereignis**, auch keine Auftragsart; gleiches Vorgehen |
| `sales.post_pruefen` | **noch kein Ereignis.** `post_pruefen` ist bisher nur eine Auftragsart in `spaces/_contract/sales.contract.yaml` (Zeile 16) |

Nicht setzen an `sales.auftrag_probe` und `sales.auftrag_lesen` (Zeilen 503 und 508): Sie sind die
Vor- und Nachprüfungen und müssen auch aus einer unbekannten Herkunft laufen.

Ob der Lader des Verzeichnisses ein unbekanntes Feld toleriert, wurde nicht geprüft; beim Einbauen nachsehen.

### 6. Aufrufer, die die Kopfzeilen senden

- Sprach- und Tipp-Weg der App: vorgesehen ist `voice/python/swarm/routing/brain_multihop_bridge.py` (Hülle).
  **Diese Datei gibt es in diesem Stand nicht:** `voice/` ist in diesem Arbeitsbaum leer, und unter dem Pfad
  liegt auch in `Vibemind_V1` nichts. Die Koordination muss die richtige Stelle der App-Hülle benennen, die
  `/api/multihop/execute` aufruft; dort kommen die beiden Kopfzeilen hin, mit dem Eingang `app` und dem
  Schlüssel aus dessen Geheimnisablage. Neu seit master (`948f436f`): Der Pin der Hülle (`voice`) wurde laut
  Commit-Text auf einen Stand mit „Zeichen für getippte Sätze" gesetzt; die Hülle sendet also für getippte
  Sätze schon die Kopfzeile `X-Vibemind-Eingabe`. Der Inhalt der Hülle liegt in diesem Arbeitsbaum nicht vor und
  wurde nicht gelesen; die Zuordnung zu den Kopfzeilen dieses Blocks steht in `kollisionen-948f436f.md`, Nr. 1.
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
