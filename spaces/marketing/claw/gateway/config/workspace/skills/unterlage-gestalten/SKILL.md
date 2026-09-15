---
name: unterlage-gestalten
description: "Eine Unterlage so setzen, dass ein Kunde sie annimmt — Vorlage wählen oder vorschlagen, Musterblatt zeigen, Freigabe beim Betreiber holen, Text auszeichnen. Bei jedem PDF und jedem neuen Layout nutzen."
metadata: { "openclaw": { "emoji": "🎨" } }
---

# Unterlage gestalten — damit sie beim Kunden ankommt

Diese Fertigkeit ist für das da, was nach dem Text kommt: das Aussehen. Sie
ist aus **einer echten Rückmeldung** entstanden — ein Mensch hat am
11.09.2026 das erste PDF dieses Hauses gelesen und geantwortet. Vier Sätze,
und jeder davon steht unten als Regel.

Merk dir den Grund dahinter: eine Unterlage wird nicht schön, weil sie
jemandem gefällt, sondern weil ein Leser sie **annimmt** — er liest sie zu
Ende, er versteht beim Überfliegen, worum es geht, und nichts daran lässt
ihn misstrauisch werden.

---

## Schritt 1 — Vorlage wählen, nicht erfinden

`vorlagen_auflisten()` zeigt, was es gibt und was davon **freigegeben** ist.

Setzen kannst du nur eine freigegebene. Das ist kein Hindernis, sondern der
Sinn: das Layout ist das, was ein Kunde von diesem Haus sieht, und ein
Mensch muss es einmal angesehen haben.

* `dunkel` — Pitch-Deck-Gewand, für den Bildschirm. Freigegeben.
* `hell` — dasselbe Gerüst für Druck und Weiterleitung. **Steht als
  Vorschlag**; wer es braucht, bittet den Betreiber um die Freigabe.

Passt eine davon, nimm sie und geh zu Schritt 4. Der häufigste Fehler an
dieser Stelle ist, ein neues Layout zu bauen, weil es sich nach Arbeit
anfühlt — drei Vorlagen, die fast gleich aussehen, sind schlechter als eine.

## Schritt 2 — Nur wenn wirklich eine neue gebraucht wird: vorschlagen

Ein neues Layout ist gerechtfertigt, wenn ein **Anlass** es verlangt: die
Hausfarben eines Kunden, ein Druckerzeugnis, eine Reihe, die sich absetzen
soll. Nicht, weil dir eine Farbe besser gefällt.

```
vorlage_vorschlagen(
    name="kunde-blau",
    beschreibung="Für Unterlagen an <Kunde>, in deren Hausfarben.",
    gestalt={ ...acht Farben... })
```

Acht Farben, alle als `#rrggbb`:

| Schlüssel | wofür |
|---|---|
| `grund` | Seitenhintergrund |
| `flaeche` | Kopf- und Fußband |
| `akzent` | Linien, Untertitel, der **gefüllte** Handlungskasten |
| `gold` | Rubriken (BELEGE, ZU KLÄREN) |
| `text` | Fließtext |
| `text_hell` | Titel |
| `text_leise` | Fußzeile, Kleingedrucktes |
| `handlung_text` | Schrift **im** Handlungskasten (liegt auf `akzent`) |

**Die Prüfung läuft, du musst sie nicht aufrufen.** `vorlage_vorschlagen`
rechnet den Kontrast jeder Farbkombination nach WCAG 2.1 nach und weist ab,
was nicht lesbar ist — mit der gemessenen Zahl und der Schwelle im
Fehlertext. Das ist keine Schikane: eine Zahl wie „3.6:1" sieht man einem
Musterblatt nicht an, und der Betreiber soll nicht das prüfen müssen, was
eine Maschine ausrechnen kann.

Gemessen am 15.09.2026: die Vorlage `warm-sand`, die vorher durchkam, reißt
**vier** Schwellen. Niemand hatte es bemerkt.

**Drei Regeln, die aus Schaden entstanden sind:**

1. **`text` ist nie reines Weiß und nie reines Schwarz.** Wörtlich vom
   Leser: *„Finde gut, dass die Schrift Farbe nicht ganz weiß ist, weil
   leicht gräulich ist laut Studien besser fürs Auge zu lesen."* Reines
   `#ffffff` auf dunklem Grund flimmert. Nimm einen Ton, der zum Grund
   gehört — bei `dunkel` ist das `#cfe3df`.
2. **`handlung_text` muss auf `akzent` lesbar sein.** Der Handlungskasten
   ist die einzige gefüllte Fläche auf der Seite; Schrift in derselben Farbe
   wie die Füllung ergibt einen leeren Kasten. Die Datenbank weist das ab,
   aber merk es dir trotzdem — sie prüft nur Gleichheit, nicht schwachen
   Kontrast.
3. **Zwei Farben tragen, nicht fünf.** `akzent` und `gold` reichen. Wer
   jeder Rubrik eine eigene Farbe gibt, baut keine Unterlage, sondern eine
   Farbtafel.

## Schritt 3 — Musterblatt zeigen, dann warten

```
vorlage_muster("kunde-blau")
```

Das setzt ein Musterblatt mit allen Elementen — Titel, Untertitel,
Fließtext, Stichpunkte mit Betonung, Handlungskasten, Rubriken — und legt es
dort ab, wo der Betreiber es findet. **Gib ihm den Pfad und sag, worum du
bittest.**

Dann **wartest du**. Freigeben kann ausschließlich er; du hast das Werkzeug
dafür nicht, und das ist Absicht. Eine Vorlage freizugeben, ohne sie gesehen
zu haben, wäre keine Freigabe — deshalb zuerst das Blatt, dann die Frage.

Kommt eine Ablehnung, steht der Grund an der Vorlage. Lies ihn, bevor du
etwas Neues vorschlägst.

## Schritt 4 — Setzen, und den Text auszeichnen

```
pdf_erstellen(name=..., titel=..., untertitel=..., text=...,
              handlung=..., belege=..., zu_klaeren=...,
              vorlage="dunkel", zweck="email")
```

**`**wort**` wird fett.** Wörtlich vom Leser: *„Ich würde bei den
Stichpunkten die folgenden Wörter fett machen: Marketing-Beiträge, Support
Antworten, Entwicklung."* Er hat damit recht, und der Grund ist mechanisch:
wer eine Unterlage überfliegt, liest die fetten Wörter zuerst. Betone

* **das Substantiv, um das es im Stichpunkt geht** — nicht den ganzen Satz,
* höchstens **ein bis zwei Wörter je Punkt**,
* und in jedem Punkt dasselbe Muster. Wer alles betont, betont nichts.

**Der `untertitel` ist der zweite Satz der Überschrift.** Er steht in
Türkis und fett (auch das kam vom Leser) und beantwortet eine Frage:
**für wen** ist diese Unterlage? Nicht: was steht drin.

**Keine langen Gedankenstriche.** Wörtlich: *„Die langen Gedankenstriche
wegmachen oder kürzen. Sieht zu sehr nach KI aus."* Schreib `-`. Tust du es
nicht, kürzt der Space sie selbst und sagt dir in der Antwort, wie viele es
waren. Ein Bereich wie `10–12 Uhr` ist davon nicht betroffen.

## Schritt 5 — Ansehen, bevor du es weitergibst

**Das Nachrechenbare prüft `pdf_erstellen` schon selbst.** Es weist ab, was
einen Kunden nicht erreichen darf — einen Platzhalter im Handlungskasten
oder im Text — und nennt dir alles Weitere als `anmerkungen` in der Antwort:
Stichpunkte ohne Betonung, zu viel Betonung, zweite Seite, fehlende Belege,
fehlender Handlungskasten. Lies sie. Sie blockieren nicht, weil ein Mensch
gute Gründe haben kann; sie stehen da, weil man den eigenen Text nach dem
dritten Lesen nicht mehr sieht.

Die Datei liegt jetzt da, wo sales-claw sie anhängen kann. Was die Maschine
NICHT prüfen kann, bleibt deine Arbeit:

1. Erkennt man beim **Überfliegen** in fünf Sekunden, worum es geht — nur an
   Titel, Untertitel und den fetten Wörtern?
2. Ist der **eine Gedanke** durchgehalten, oder sind es drei?
3. Stimmt, was da steht — und steht es so, dass es der Beleg deckt?
4. Würdest du das einem Menschen geben, den du siezt?

Diese vier kann keine Prüfung für dich beantworten. Alles andere schon —
und genau deshalb steht es nicht mehr hier.

## Wann du gar kein PDF machst

Eine kurze Nachricht mit Anhang ist schlechter als eine ohne. Ein PDF
gehört dazu, wenn der Empfänger etwas **behalten** soll — ein Einseiter, ein
Angebotsblatt, eine Übersicht. Für drei Sätze Einladung nicht.

Und: **bei Telegram gibt es keine Anhänge.** Ein Telegram-Auftrag mit
`medien_datei` fällt beim Versand durch. Gehört eine Unterlage dazu, nimm
E-Mail.

---

## Warum diese Fertigkeit existiert

Weil ein Mensch sich die Mühe gemacht hat, fünf Sätze zurückzuschreiben,
statt „passt schon" zu sagen. Alles hier ist aus diesen fünf Sätzen und aus
dem, was beim Umsetzen auffiel.

**Fällt dir am Ergebnis etwas auf, das hier nicht steht: sag es dem
Betreiber in einem Satz.** Genau so ist diese Liste entstanden.
