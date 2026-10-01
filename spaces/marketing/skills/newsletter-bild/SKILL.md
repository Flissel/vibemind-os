---
name: newsletter-bild
description: Bilder fuer einen Newsletter erzeugen lassen - Layout abfragen, vorgesehenen Bildplatz finden, mit passendem Hinweis beauftragen. Nutzen, wenn ein Newsletter Bilder braucht oder der Betreiber ein Bild anders haben will.
---

# Newsletter-Bild

Du erzeugst keine Bilder selbst. Du findest heraus, **wo** im Newsletter Bilder
vorgesehen sind, und **beauftragst** den Bild-Arbeiter. Er erzeugt am PC mit
einem offenen Modell (FLUX.1-schnell), prueft das Ergebnis und setzt es genau
in den Platz. Jede Aenderung wird eine neue Fassung, die der Betreiber im Pult
freigibt.

## Ablauf

1. `newsletter_bildplaetze(inhalt_id)` aufrufen. Du bekommst je Platz: `id`,
   `anzeige_breite`x`anzeige_hoehe`, `verhaeltnis` (z. B. 2:1), `leer`, `alt`
   (worum es geht) und `kontext` (Text drumherum), dazu den Stand laufender
   Auftraege.
2. Den richtigen Platz waehlen:
   - "Kopfbild", "oben", "grosses Bild" -> der erste Platz mit Verhaeltnis 2:1 oder 3:1.
   - "Thema 1/2", "links/rechts" -> Plaetze in Spalten (kleinere Breite, 4:3 oder 1:1), in Dokumentreihenfolge.
   - Unklar -> den Betreiber mit der Liste (id + alt) fragen, nicht raten.
3. `newsletter_bild_beauftragen(inhalt_id, platz=<id>, hinweis=<Wunsch>)`.
   - Hinweis in Worten des Betreibers, knapp ("waermer", "Menschen statt Technik").
   - Das Bild wird immer neu aus der Motivbeschreibung plus dem Wunsch erzeugt
     (keine Pixel-Bearbeitung). `staerke` 0-100 (Standard 55) steuert, wie nah es
     am Original bleibt: `staerke` 35 = nah am Original (Motiv, Umgebung und
     Bildaufbau der Beschreibung bleiben), 75 = freier (nur das Thema bleibt),
     100 oder `modus="neu"` = ganz neu.
   - "Schrift weg", "waermer", "heller" -> 35; "anderer Stil, gleiches Thema" -> 75;
     "anderes Motiv" -> 100 oder neu.
   - Nach dem Lauf steht im Stand je Platz `messung.aehnlich_original` (0-1): die
     "Themen-Aehnlichkeit" zwischen neuem und altem Bild. Nenne dem Betreiber diesen Wert.
   - Alle leeren Plaetze fuellen: `platz` leer lassen, `nur_leere=True`.
   - Alle neu: `platz` leer lassen, `nur_leere=False`, `staerke=100` (oder `modus="neu"`).
4. Dem Betreiber sagen, dass das Bild erzeugt wird, sobald der PC laeuft, und
   dass es als neue Fassung im Pult erscheint.

## Grenzen

- Ist der Newsletter schon freigegeben, lehnt die Datenbank neue Auftraege ab - sag das so.
- Ein Platz, den es nur im ungespeicherten Editor gibt, ist fuer dich unsichtbar: erst speichern lassen.
- Pro Platz wartet hoechstens ein Auftrag; ein neuer ersetzt den wartenden.
