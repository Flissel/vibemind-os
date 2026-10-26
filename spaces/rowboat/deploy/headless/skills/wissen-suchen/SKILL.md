---
name: wissen-suchen
description: "Bedeutungssuche im Rowboat-Wissen – IMMER zuerst nutzen, wenn nach Kunden, Kontakten, Themen, Interessen, Notizen oder 'was wissen wir über …' gefragt wird, besonders wenn die genauen Wörter unklar sind."
tools: [listMcpTools, executeMcpTool]
---

# Wissen nach Bedeutung suchen

Das Wissen (Notizen, Kontakt-Steckbriefe, Newsletter, Wochenbild) liegt unter `knowledge/`. Der
MCP-Server `wissen` findet passende Notizen nach Bedeutung, auch wenn die Frage andere Wörter benutzt
als die Notiz (z. B. „Altersvorsorge“ findet „Rentenplanung“).

## So gehst du vor

1. Suchen:

   ```
   executeMcpTool({serverName: "wissen", toolName: "wissen_suchen", arguments: {frage: "<die Frage in eigenen Worten>", anzahl: 8}})
   ```

   Die Antwort ist JSON: `modus` (`"bedeutung"` oder `"stichwort"`), `hinweis`, `treffer` (je Treffer
   `pfad`, `titel`, `abschnitt`, `auszug`, `quelle`) und `stand`.
2. Die besten Treffer mit `file-readText` öffnen (der `pfad` beginnt mit `knowledge/` und lässt sich
   direkt öffnen) und erst danach antworten. Nicht nur aus den Auszügen antworten.
3. Ist `hinweis` gesetzt, steht er **wörtlich** in deiner Antwort an den Betreiber – auch per Telegram.
   Nicht umformulieren, nicht weglassen.
4. Bei `modus: "stichwort"` war die Bedeutungssuche nicht möglich (PC aus). Dann nie behaupten, es gebe
   nichts weiter; sag, dass es nur Stichwort-Treffer sind, und probiere bei Bedarf weitere Stichwörter.
5. `file-grep` nur für exakte Namen, Nummern oder Zeichenfolgen benutzen, nicht für Themen-Fragen.
6. Pfade mit `_archiv/` sind archivierte Kontakte. Erwähne das, wenn du sie nennst.

Wenn `wissen` nicht antwortet (Fehler statt JSON), mit `listMcpTools({serverName: "wissen"})` prüfen,
ob der Server da ist, und sonst mit `file-grep` weitersuchen und das dazusagen.
