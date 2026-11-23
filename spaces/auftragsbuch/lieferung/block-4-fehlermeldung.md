# Lieferung Block 4: Fehlermeldung des Auftragsbuchs

Fertiger Block zum Einbauen durch die Koordination (sie betreut den Kern des Gehirns). Dieses Repo ändert am
Gehirn nichts. Die Zeilenangaben sind gegen `origin/master` (`948f436f`, geprüft am 23.11.; vorher `ae9f2dfe`) nachgezogen.
Master hat im selben `isError`-Zweig einen neuen Fall für `spaces-desktop` eingefügt (`kollisionen-948f436f.md`,
Nr. 3). Bitte beim Einbauen trotzdem noch einmal gegenlesen, denn die Zeilen verschieben sich.

## Das Problem in einem Satz

Scheitert ein Werkzeug des Auftragsbuchs, sieht der Betreiber heute nur
`OpenFang MCP tool returned isError=true` oder `OpenFang MCP JSON-RPC error: <Text>`: ohne Dienstnamen, und der
freie Text des Werkzeugs könnte Inhalte von Aufträgen enthalten.

## Was mitgeliefert wird

Ordner `spaces/auftragsbuch/lieferung/fehlermeldung/`, nur Standardbibliothek:

- `fehlertext.py` mit `fehlertext(server, werkzeug, mcp_ergebnis) -> str`. Steht der Text des Werkzeugs in der
  festen Liste, lautet die Meldung `<server>: <Text>`; beginnt der Text schon mit dem Namen des Servers, bleibt er
  wörtlich. Sonst: `<server>/<werkzeug>: Werkzeug meldet einen Fehler`. Die Funktion wirft nie.
  Die feste Liste (jeweils der ganze Text, nie ein Teilstück):
  - die Texte der Brücke (`spaces/auftragsbuch/bruecke.py`, 160 bis 175): `auftragsbuch nicht erreichbar`,
    `auftragsbuch: Schlüssel abgelehnt`, `auftragsbuch: Zertifikat nicht pruefbar` und
    `auftragsbuch: unerwartete Antwort (<dreistelliger Status>)` (auch ohne den Vorsatz `auftragsbuch: `),
  - die sechs Ablehnungen aus Migration 100 (`spaces/marketing/db/100_*.sql`): `Unbekannte Auftragsart`,
    `Auftrag braucht Laden und Mandant`, `Frist ausserhalb 1 bis 120 Minuten`, `Plan-Kennung ungueltig`,
    `Tagesgrenze erreicht: hoechstens 50 Proben pro Tag`,
    `Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag`,
  - `Ergebnis in falscher Form`.
  Bewusst nicht in der Liste: `nicht gefunden`, `interner Fehler` und `Eingabe passt nicht zum Vertrag: ...` des
  Servers (der letzte trägt freien Text); sie ergeben die allgemeine Meldung.
- `test_fehlertext.py` (20 Tests). Aufruf von der Repo-Wurzel:
  `python -m pytest spaces/auftragsbuch/lieferung/fehlermeldung/test_fehlertext.py -q -p no:cacheprovider`.

## Welche Formen ankommen

Zwei Wege führen im Gehirn zum Fehler, beide in `McpExecutor._call`
(`brain/the_brain/core/capability_targets.py`, Klasse ab 855):

1. **JSON-RPC-Fehler.** Die Brücke antwortet bei Verbindungs-, Schlüssel- und Zertifikatsproblemen mit einem
   `error`-Objekt, `message` trägt den Text, zum Beispiel `auftragsbuch nicht erreichbar`. Bei 1050 wird `error`
   (geschwärzt) gebildet, `message` daraus gelesen; es endet in
   `raise RuntimeError(f"OpenFang MCP JSON-RPC error: {message}")` (1072).
2. **Werkzeugfehler.** Der Dienst antwortet mit `isError: true` und einem Text in `content[0].text` (so bei den
   Ablehnungen der Datenbank, bei `unerwartete Antwort` und bei `Ergebnis in falscher Form`). `result` ist das
   MCP-Ergebnis; es endet in `raise RuntimeError("OpenFang MCP tool returned isError=true")` (heute 1094).

`fehlertext` nimmt beide Formen: ein MCP-Ergebnis (`content[].text`), ein Fehlerobjekt (`message`), ein
Wörterbuch mit `error` darin oder `text`. Alles andere (fehlende Teile, falsche Typen, `None`) ergibt die
allgemeine Meldung. Ob OpenFang den Fehler der Brücke unverändert durchreicht oder einpackt, ist aus dem Code
nicht zu sehen; eine eingepackte Meldung fällt in die allgemeine Form, nie in einen Klartext.

## Wo einhängen

In `McpExecutor._call`, nur für den Dienst des Auftragsbuchs (`self.server == "auftragsbuch"`; den Namen des
Servers in der Konfiguration bitte prüfen):

```python
# bei 1072, statt des bisherigen raise:
if self.server == "auftragsbuch":
    raise RuntimeError(fehlertext(self.server, self.tool, error if isinstance(error, dict) else {"message": message}))
raise RuntimeError(f"OpenFang MCP JSON-RPC error: {message}")

# bei 1088, im isError-Zweig: Der Fall für spaces-desktop steht dort seit master (1089 bis 1093); das
# bisherige generische raise steht jetzt bei 1094. Der neue Fall kommt davor oder danach, nie dazwischen:
if result.get("isError") is True:
    if self.server == "auftragsbuch":
        raise RuntimeError(fehlertext(self.server, self.tool, result))
    raise RuntimeError("OpenFang MCP tool returned isError=true")
```

Die Meldung erscheint dann im Schritt-Ergebnis als `error` (`call` bei 119 bis 150 in
`capability_targets.py` macht daraus `"RuntimeError: <Meldung>"`). Die Fälle `pending_approval`,
`outcome_unknown` und `in_progress` (1060 bis 1071) bleiben vor dem neuen Aufruf unverändert.

Beim Einbauen die Datei ins Gehirn kopieren (Vorschlag: `brain/the_brain/core/fehlertext.py`) und dort
`from .fehlertext import fehlertext` verwenden.

## Prüfen nach dem Einbau

- Dienst absichtlich ausschalten: die Meldung lautet `auftragsbuch nicht erreichbar`.
- `auftrag_probe` mit ungültiger Plan-Kennung: `auftragsbuch: Plan-Kennung ungueltig`.
- Ein Fehler eines anderen Dienstes bleibt wie bisher.
