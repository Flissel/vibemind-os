# Die kanonische Space-Routing-Schicht war in der ausgebrachten Brain tot

**Datum:** 2026-09-15
**Gefunden durch:** den ersten echten Lauf über `/api/multihop/execute` —
nicht durch Tests, nicht durch Lesen.
**Geändert:** vier Stellen in `brain/the_brain/core/`, plus das Dockerfile.

## Was passiert ist

Nach dem Start von OpenFang :4200 waren 74 von 94 Capabilities erreichbar. Um
das nicht nur zu behaupten, habe ich eine **lesende** Capability durch die
volle Kette geschickt — Intent → Plan → Ausführung, über den echten Eintritt
in `brain-core`, nicht am Dienst vorbei:

```
POST /api/multihop/execute  {"intent": "liste meine bubbles auf"}
```

Der Plan entstand korrekt (ein Hop, `bubble_list`, Ziel `supabase:bubble.list`).
Die Ausführung nicht:

```
s1  ok=false
    error: "canonical OpenFang routing: IndexError: 3"
    reward: -1.0
```

## Die Ursache

```python
# brain/the_brain/core/space_contract.py:22  (Modulebene!)
_REPO_ROOT = Path(__file__).resolve().parents[3]
```

Im Repo liegt die Datei als `brain/the_brain/core/space_contract.py` — vier
Eltern bis `vibemind-os`, die Rechnung geht auf. **Im Container liegt dieselbe
Datei als `/app/core/space_contract.py`** — drei Eltern. `parents[3]` wirft
`IndexError: 3`.

Weil die Zeile auf **Modulebene** steht, scheitert schon der Import. Und
`_canonical_space_event_agent` (`plan_executor.py:66`) importiert das Modul für
**jeden** Hop. Der Fehler wird dort in einen `HopResult` verpackt — die Meldung
`canonical OpenFang routing: IndexError: 3` sagt nichts über die Ursache und
klingt nach einem Problem von OpenFang, das gar nicht beteiligt war.

Im laufenden Container gemessen, nicht hergeleitet:

```
space_contract (Import)                -> FEHLER IndexError: 3
capability_targets._space_registry_path-> FEHLER IndexError: 3
agent_yaml_registry.get_event_agent    -> FEHLER IndexError: 3
desktop_orchestration.from_repository  -> FEHLER IndexError: 3
```

**Vier Stellen, derselbe Denkfehler.** Damit war die kanonische
Space-Routing-Schicht in der ausgebrachten Brain vollständig unbenutzbar.

### Warum das so lange unsichtbar blieb

**Nativ aus dem Repo funktioniert es.** Jeder Test, jede lokale Probe, jeder
Live-Beweis, der mit einer nativ gestarteten Brain geführt wurde, sah einen
gesunden Pfad. Der Fehler existiert ausschließlich im Image — also genau dort,
wo niemand hinschaut, solange die Komponenten grün sind.

Der Kommentar im Dockerfile sagte sogar ausdrücklich das Gegenteil:

```
# 4. Full brain source into /app (so relative paths are unchanged vs. native).
```

Sie sind nicht unverändert. Das ist der Satz, der den Fehler getragen hat.

## Der zweite Fehler darunter

Selbst mit korrektem Pfad findet der Container nichts: **die Registry liegt gar
nicht im Image.** `COPY brain/the_brain/ /app/` nimmt `config/` nicht mit, weil
es im Repo *daneben* liegt, nicht darunter. `/app/config` existiert nicht.

Bemerkenswert: `Dockerfile.deterministic-gateway` macht es seit jeher richtig —
es kopiert die Registry **und** setzt `SPACE_AGENT_REGISTRY_PATH`. Das Wissen
war da, nur nicht im Haupt-Image.

## Was geändert wurde

**Ein gemeinsamer Resolver** (`space_contract.resolve_registry_path`), den alle
vier Stellen benutzen:

1. `SPACE_AGENT_REGISTRY_PATH` — die ausdrückliche Ansage gewinnt. Derselbe
   Name, den das Gateway-Image schon setzt.
2. Sonst aufwärts suchen, bis `config/space_agent_registry.yml` auftaucht —
   ohne jede Annahme über die Ordnertiefe.
3. Sonst `RegistryNotFound` **mit allen geprüften Pfaden**. Wer das liest, weiß
   wo gesucht wurde; `IndexError: 3` wusste das niemand.

Die Auflösung ist jetzt **faul**: `load_space_contract()` löst beim Aufruf auf,
nicht beim Import. Ein Modul, das sich beim Laden am Dateisystem festbeißt,
nimmt jedem Aufrufer die Möglichkeit, den Pfad selbst zu setzen — das war der
eigentliche Schaden.

**Das Dockerfile** kopiert die Registry jetzt und setzt den Zeiger, nach dem
Muster des Gateway-Images. Der irreführende Kommentar ist durch die Messung
ersetzt.

## Belege

* 12 Tests in `brain/the_brain/tests/test_space_registry_path.py`. Sie halten
  das **flache** Layout fest, nicht das bequeme: ein Modul mit drei Eltern darf
  keinen `IndexError` mehr werfen, die Fehlermeldung muss den Ausweg nennen,
  und auf Modulebene darf kein `parents[` mehr stehen.
* Ein Test prüft, dass das Image die Registry überhaupt enthält — der Pfad-Fix
  allein wäre wirkungslos gewesen.
* **Kein Test ist durch die Änderung kaputtgegangen.** Gegenprobe: dieselben
  zehn Vertrags- und Routing-Testdateien mit und ohne die Änderung gelaufen,
  beide Male **14 Fehlschläge, identische Namen**. Die 14 bestanden vorher und
  sind ein eigener, offener Befund (siehe unten).
* 76 Tests in den angrenzenden Dateien grün.

## Was noch offen ist

**Der Fix wirkt erst nach einem Neubau des brain-Images.** Der laufende
Container trägt weiter den alten Stand; bis zum Deploy scheitert jeder Hop
weiterhin, nur künftig mit lesbarer Meldung statt `IndexError: 3`. Deploys
laufen hier ausschließlich über Launcher/stack-deploy — **nie**
`service update`, das räumt den ganzen Stack ab.

**14 vorbestehende Testfehlschläge** in den Routing-Tests (u.a. „agent scope
drift", n8n-MCP-Routing, `idea.connect`, `minibook.status`). Sie sind nicht
Teil dieses Befunds, aber sie stehen an derselben Schicht und gehören
angesehen.

**`embedding-service` ist verschwunden.** Beim Lesen der brain-core-Logs fiel
auf: `Failed to resolve 'embedding-service'` — der Dienst ist im Swarm nicht
mehr vorhanden, die KG-Suche der Brain fällt bei jeder Anfrage aus. Eigener
Befund, hier nur festgehalten.
