# Sales-Space: feste Lesesätze für das Gehirn

- **Stand:** 24.11.2026. Umgesetzt in `brain/the_brain/core/sales_saetze.py`, Tests in
  `brain/the_brain/tests/test_sales_saetze.py` (alle Testsätze unten).
- **Vorbild:** `brain/the_brain/core/ideen_abkuerzung.py`. Gleiche Form: Satz und Kontext der App ergeben einen
  festen Aufruf ohne Modell, eine Rückfrage oder `None`.
- **Weg:** Im Sales-Reiter (`context.space = "sales"`) wird der Satz im Gehirn aufgelöst und geht als Auftrag
  fester Art ins Auftragsbuch (Werkzeug `sales_<art>`). Ausgeführt wird bei Sales. Die Sätze brauchen keine
  Anmeldung an der Sales-Oberfläche; nur der Sprung in die Seite danach braucht sie.
- **Bezug:** Antwortsätze in `ANTWORTSAETZE.md` und `antwortsaetze-faelle.json`.

## Ergebnis von `loese(satz, kontext)`

| Ergebnis | Wann |
|---|---|
| `None` | `kontext["space"]` ist nicht `"sales"`, oder kein Muster passt. Dann entscheidet der bisherige Weg. Ein Satz, der verändern würde, landet nie hier (siehe „Nie ein fester Aufruf"). |
| `{"rueckfrage": text}` | Ein Muster passt, aber die Art ist gesperrt oder die Eingabe taugt nicht. Es entsteht kein Auftrag. |
| `{"ereignis", "capability", "argumente", "antwort"}` | Ein fester Aufruf. `ereignis` und `capability` sind gleich (`sales.<art>`). `antwort` ist der Satz, den die App sofort zeigt. Der Satz mit Auftragsnummer und der Ergebnissatz folgen aus `ANTWORTSAETZE.md`. |

Vorbereitung wie bei den Ideen: `text = re.sub(r"\s+", " ", satz).strip().rstrip(".!")`. Jedes Muster gilt mit
`re.fullmatch(muster, text, re.IGNORECASE)`. Ein Fragezeichen am Ende ist im Muster erlaubt (`\s*\??`). Umlaute
gehen in beiden Schreibweisen (`ä|ae`).

## Muster

```python
_FRAGE = r"\s*\??"
_HEUTE = r"heute(?:\s+noch)?"

SALES_SAETZE = {
    "termine_heute": [
        r"(?:welche|was\s+f(?:ü|ue)r)\s+termine\s+(?:habe|hab|hätte|haette)\s+ich\s+" + _HEUTE + _FRAGE,
        r"wie\s+viele\s+termine\s+(?:habe|hab)\s+ich\s+" + _HEUTE + _FRAGE,
        r"(?:habe|hab)\s+ich\s+" + _HEUTE + r"\s+(?:einen\s+termin|termine)" + _FRAGE,
        r"was\s+steht\s+heute\s+(?:noch\s+)?(?:im|in\s+meinem)\s+kalender(?:\s+an)?" + _FRAGE,
        r"was\s+steht\s+heute\s+(?:noch\s+)?an" + _FRAGE,
        r"wie\s+voll\s+ist\s+(?:mein|der)\s+tag(?:\s+heute)?" + _FRAGE,
        r"(?:zeig(?:e)?\s+(?:mir\s+)?)?(?:meine\s+|die\s+)?termine\s+(?:f(?:ü|ue)r|von)\s+heute" + _FRAGE,
    ],
    "freigaben_zaehlen": [
        r"was\s+liegt\s+(?:in\s+den|bei\s+den)\s+freigaben" + _FRAGE,
        r"muss\s+ich\s+(?:noch\s+)?(?:was|etwas|irgendwas)\s+freigeben" + _FRAGE,
        r"wie\s+viele\s+entw(?:ü|ue)rfe\s+(?:warten(?:\s+auf\s+(?:meine\s+)?freigabe)?|sind\s+offen"
        r"|liegen\s+(?:in\s+den\s+freigaben|zur\s+freigabe))" + _FRAGE,
        r"(?:gibt\s+es|liegen)\s+(?:offene\s+|neue\s+)?freigaben" + _FRAGE,
        r"wartet\s+(?:was|etwas)\s+auf\s+(?:meine\s+)?freigabe" + _FRAGE,
    ],
    "post_offen": [
        r"wie\s+viele\s+(?:ungelesene|unbeantwortete|offene|neue)\s+(?:nachrichten|mails|chats)"
        r"(?:\s+(?:habe|hab)\s+ich)?" + _FRAGE,
        r"hat\s+mir\s+(?:heute\s+)?(?:jemand|wer)\s+geschrieben" + _FRAGE,
        r"ist\s+(?:noch\s+)?post\s+offen" + _FRAGE,
        r"(?:habe|hab)\s+ich\s+(?:neue\s+|ungelesene\s+|offene\s+)?(?:post|nachrichten|mails)" + _FRAGE,
        r"was\s+ist\s+(?:im\s+posteingang|in\s+der\s+post)\s+offen" + _FRAGE,
    ],
    # Gruppe "suche" wird die Eingabe {"suche": ...}.
    "kontakt_finden": [
        r"zeig(?:e)?\s+(?:mir\s+)?den\s+kontakt\s+(?P<suche>.+?)",
        r"(?:such(?:e)?|finde?)\s+(?:mir\s+)?(?:den\s+)?kontakt\s+(?P<suche>.+?)",
        r"wen\s+haben\s+wir\s+(?:bei|von)\s+(?:der\s+|dem\s+|den\s+)?(?P<suche>.+?)" + _FRAGE,
        r"(?:gibt\s+es|haben\s+wir)\s+(?:einen\s+)?kontakt\s+(?:namens\s+|zu\s+|mit\s+dem\s+namen\s+)?"
        r"(?P<suche>.+?)" + _FRAGE,
    ],
}
```

Reihenfolge: die Arten in dieser Reihenfolge prüfen, der erste Treffer gilt. Die Muster überschneiden sich nicht;
die Testsätze unten prüfen das.

## Eingabe für `kontakt_finden`

1. `suche` ist die Gruppe aus dem Muster, ohne Anführungszeichen („ “ " ' ‚ ‘) und ohne Leerzeichen am Rand.
2. Hat `suche` weniger als zwei Zeichen, die keine Leerzeichen sind, oder mehr als 100 Zeichen, kommt eine
   Rückfrage: „Nach welchem Namen soll ich suchen? Bitte mindestens zwei Buchstaben." Es entsteht kein Auftrag.
   Der Vertrag lässt zwei Leerzeichen durch; das Gehirn prüft hier also strenger als der Vertrag.
3. `argumente = {"suche": suche}`. Den Wert schreibt das Gehirn nie in Standzeile, Protokoll oder Antwortsatz.

Die drei Zählarten haben immer `argumente = {}`.

## Sofortige Antwort und gesperrte Arten

| Art | `antwort` beim Aufruf | Satz, solange die Art gesperrt ist |
|---|---|---|
| `termine_heute` | „Ich schaue in den Kalender." | „Das ist noch nicht freigeschaltet. Die Termine von heute siehst du in der Sales-Oberfläche unter Kalender." |
| `freigaben_zaehlen` | „Ich schaue in die Freigaben." | „Das ist noch nicht freigeschaltet. Die Entwürfe siehst du in der Sales-Oberfläche unter Freigaben." |
| `post_offen` | „Ich schaue in die Post." | „Das ist noch nicht freigeschaltet. Die offene Post siehst du in der Sales-Oberfläche unter Posteingang." |
| `kontakt_finden` | „Ich suche." | „Das ist noch nicht freigeschaltet. Kontakte findest du in der Sales-Oberfläche unter Kontakte." |

„Gesperrt" heißt: Die Art hat keine Zeile in `auftrag.brain_arten`, oder der Auftragsbuch-Dienst kennt ihr
Werkzeug nicht. Das Auftragsbuch lehnt dann schon das Anlegen ab („Unbekannte Auftragsart" bzw. „nicht
gefunden"), es entsteht kein Auftrag. Das Gehirn zeigt statt des Werkzeugtexts den festen Satz aus der Tabelle
(`core/sales_antwort.py`, `GESPERRT`). Die Tagesgrenze ergibt einen eigenen festen Satz, jeder andere Fehler
„Das Auftragsbuch antwortet gerade nicht. Geändert habe ich nichts."

## Nie ein fester Aufruf

Diese Sätze ergeben `None`, auch wenn ein Wort aus den Mustern vorkommt. Für sie gibt es keine Auftragsart. Und
sie dürfen nie über das Auftragsbuch laufen: Freigegeben wird nur in der Sales-Oberfläche.

- „Gib alle Freigaben frei."
- „Schick die Entwürfe ab."
- „Schreib dem Kontakt Muster eine Mail."
- „Lösch den Kontakt Muster."
- „Trag mir heute um 15 Uhr einen Termin ein."
- „Markier die Post als erledigt."

## Testsätze

Satz → Art (oder `None`) und Eingabe. Alle Namen sind erfunden.

| Satz | Art | Eingabe |
|---|---|---|
| Welche Termine habe ich heute? | `termine_heute` | `{}` |
| Was für Termine hab ich heute noch | `termine_heute` | `{}` |
| Wie viele Termine habe ich heute? | `termine_heute` | `{}` |
| Hab ich heute noch Termine? | `termine_heute` | `{}` |
| Was steht heute im Kalender? | `termine_heute` | `{}` |
| Was steht heute noch an? | `termine_heute` | `{}` |
| Wie voll ist mein Tag? | `termine_heute` | `{}` |
| Zeig mir die Termine für heute. | `termine_heute` | `{}` |
| Was liegt in den Freigaben? | `freigaben_zaehlen` | `{}` |
| Muss ich was freigeben? | `freigaben_zaehlen` | `{}` |
| Wie viele Entwürfe warten? | `freigaben_zaehlen` | `{}` |
| Wie viele Entwürfe warten auf meine Freigabe? | `freigaben_zaehlen` | `{}` |
| Gibt es offene Freigaben? | `freigaben_zaehlen` | `{}` |
| Wartet etwas auf Freigabe? | `freigaben_zaehlen` | `{}` |
| Wie viele ungelesene Nachrichten? | `post_offen` | `{}` |
| Wie viele offene Mails hab ich? | `post_offen` | `{}` |
| Hat mir jemand geschrieben? | `post_offen` | `{}` |
| Ist Post offen? | `post_offen` | `{}` |
| Hab ich neue Nachrichten? | `post_offen` | `{}` |
| Was ist im Posteingang offen? | `post_offen` | `{}` |
| Zeig den Kontakt Muster. | `kontakt_finden` | `{"suche": "Muster"}` |
| Zeig mir den Kontakt „Erika Muster“ | `kontakt_finden` | `{"suche": "Erika Muster"}` |
| Such den Kontakt Beispiel AG | `kontakt_finden` | `{"suche": "Beispiel AG"}` |
| Wen haben wir bei der Beispielbäckerei? | `kontakt_finden` | `{"suche": "Beispielbäckerei"}` |
| Gibt es einen Kontakt namens Muster? | `kontakt_finden` | `{"suche": "Muster"}` |
| Zeig den Kontakt X. | `kontakt_finden` | Rückfrage „Nach welchem Namen …" |
| Gib alle Freigaben frei. | `None` | – |
| Schick die Entwürfe ab. | `None` | – |
| Schreib dem Kontakt Muster eine Mail. | `None` | – |
| Lösch den Kontakt Muster. | `None` | – |
| Trag mir heute um 15 Uhr einen Termin ein. | `None` | – |
| Markier die Post als erledigt. | `None` | – |
| Welche Termine habe ich morgen? | `None` | – |
