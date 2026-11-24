# Sales-Space: Antwortsätze im Gehirn

- **Stand:** 24.11.2026. Umgesetzt in `brain/the_brain/core/sales_antwort.py`.
- **Testfälle:** `antwortsaetze-faelle.json` in diesem Ordner, je Fall Art, Stand, Auftragsnummer, Ergebnis und
  der erwartete Satz. `brain/the_brain/tests/test_sales_saetze.py` prüft jeden Fall wortgleich.

## Regeln

1. Der Satz nennt nur die gekürzte Auftragsnummer (die ersten 8 Zeichen), die Art (als festes Wort), den Stand
   und Zahlen. Er nennt nie Namen, Firmen, Adressen, Nummern, Betreffs oder Inhalte.
2. Zahlen kommen nur aus Feldern, die laut Vertrag ganze Zahlen sind, und nur, wenn der Wert eine ganze Zahl ab 0
   ist. `titel` und `link` eines Treffers gehen nie in den Satz.
3. Passt das Ergebnis nicht zur Form der Art (fremdes Feld, falscher Typ), sagt der Satz nur das:
   „Die Antwort von Sales passt nicht zur Form (Auftrag 4aef2c02)." Die Prüfung ist dieselbe wie im Vertrag.
4. `fehler` und `abgelaufen` ergeben denselben Satz je Art. Der Grund aus dem Auftrag kommt nicht in den Satz.
5. `offen` und `in_arbeit` ergeben den Satz „läuft" mit Auftragsnummer.
6. Einzahl und Mehrzahl werden unterschieden („1 Termin", „3 Termine"); 0 hat einen eigenen Satz.
7. Unbekannte Art bei `fertig`: „Auftrag 4aef2c02 ist fertig." ohne Zahlen.

## Sätze je Art

| Art | fertig | läuft | fehler / abgelaufen |
|---|---|---|---|
| `termine_heute` | „Heute stehen N Termine an. K Kundentermine sind für heute eingetragen." Bei `quellen_stumm` > 0 zusätzlich: „S Kalender antworten gerade nicht, es können mehr sein." Ohne Kundentermine fällt der zweite Satz weg. | „Ich schaue in den Kalender (Auftrag …)." | „Die Termine konnte ich nicht lesen (Auftrag …)." |
| `freigaben_zaehlen` | „In den Freigaben warten N Entwürfe." Bei `fehlgeschlagen` > 0 zusätzlich: „F Entwürfe sind fehlgeschlagen." Bei 0 wartenden: „In den Freigaben wartet nichts." | „Ich schaue in die Freigaben (Auftrag …)." | „Die Freigaben konnte ich nicht lesen (Auftrag …)." |
| `post_offen` | „N Chats sind unbeantwortet, M Mails sind offen." Bei beiden 0: „Keine Post ist offen." | „Ich schaue in die Post (Auftrag …)." | „Die Post konnte ich nicht lesen (Auftrag …)." |
| `kontakt_finden` | „N Kontakte gefunden." / „1 Kontakt gefunden." / „Kein Kontakt gefunden." (Zahl aus `anzahl`) | „Ich suche (Auftrag …)." | „Die Suche ist fehlgeschlagen (Auftrag …)." |

„ungelesen" kommt nicht vor: Sales kennt keinen Stand „gelesen". Gezählt werden unbeantwortete Chats und offene
Mails.
