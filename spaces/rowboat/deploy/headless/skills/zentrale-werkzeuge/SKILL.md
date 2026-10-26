---
name: zentrale-werkzeuge
description: "Werkzeuge der Zentrale – nutzen bei GitHub-Fragen (Projekte updaten, Repos, Issues, PRs, Commits, CI/Actions-Status, README), bei Fragen zum eigenen Gitea (Sicherung/Spiegel, Repos, Issues, Commits), wenn neueste Docs/Doku zu einer Bibliothek oder einem Dienst geholt werden sollen, wenn ein Dashboard aus meinen Daten gebaut werden soll und wenn ich sage 'auf meinem PC suchen' oder 'such auf dem Rechner nach …'."
tools: [listMcpTools, executeMcpTool, fetch-url, todo-add, file-readText, file-grep, file-list, file-writeText, file-mkdir]
---

# Werkzeuge der Zentrale (GitHub lesen, Docs, Dashboard, PC-Suche)

Alle Werkzeuge hier **lesen nur**. Der MCP-Server `vibemind-werkzeuge` hat genau diese Werkzeuge:
`github_repos`, `github_issues`, `github_prs`, `github_commits`, `github_ci`, `github_datei`,
`github_ueberblick`, `gitea_repos`, `gitea_issues`, `gitea_commits`, `gitea_datei`, `pc_suchen`. Aufruf immer so:

```
executeMcpTool({serverName: "vibemind-werkzeuge", toolName: "<werkzeug>", arguments: {...}})
```

## Welche Anfrage, welches Werkzeug

| Anfrage | Werkzeug |
|---|---|
| „Was ist auf GitHub los?“, „Projekte updaten“, Lagebild | `github_ueberblick` mit `{tage: 7}` |
| Repos eines Kontos (Flissel, Vibemind-LAB) | `github_repos` mit `{besitzer: "Flissel"}` |
| Ein einzelnes Repo (`besitzer/name`) | `github_issues`, `github_prs`, `github_commits` (optional `branch`), `github_datei` (z. B. `pfad: "README.md"`) |
| „Ist die CI grün?“, „Laufen die Tests?“, Actions-Status | `github_ci` mit `{repo: "besitzer/name"}` (optional `branch`); `letzter_je_workflow` zeigt je Workflow den aktuellen Stand |
| Eigenes Gitea (Sicherungsziel): „Was liegt im Gitea?“, „Ist die Sicherung aktuell?“ | `gitea_repos` (zeigt `spiegel` und `spiegel_aktualisiert`); einzelnes Repo: `gitea_issues`, `gitea_commits`, `gitea_datei` |
| Docs, URL bekannt | `fetch-url` – **nur GET** |
| Docs, URL unbekannt | `executeMcpTool({serverName: "exa", toolName: "web_search_exa", ...})`, dann `web_fetch_exa` oder `fetch-url` (GET) |
| Dashboard aus meinen Daten | Markdown-Notiz unter `knowledge/Kanal/Dashboards/`, siehe unten |
| „Auf meinem PC suchen“ | `pc_suchen` mit `{anfrage, art: "name" oder "inhalt", ordner, max_treffer}` |

**Docs:** höchstens 5 Seiten je Anfrage, nicht crawlen. Bei 403 oder 429 nicht auf andere Wege
ausweichen, sondern das melden. Nur auf Wunsch ablegen unter
`knowledge/Kanal/Docs/<Thema> <JJJJ-MM-TT>.md` (Quelle und Abrufdatum oben in die Notiz).

**Dashboard:** als Markdown-Notiz `knowledge/Kanal/Dashboards/<Name> <JJJJ-MM-TT>.md` mit Markdown-Tabellen
(Kennzahlen, Zählungen je Kategorie, Verlauf der Wochenbilder). Die Daten kommen aus den
Wissensnotizen (`knowledge/companys/*/Wochenbild`, `knowledge/companys/*/Kontakte`), keine
Kontaktnamen in Dashboards, nur Zahlen. Danach die wichtigsten Zahlen direkt im Chat nennen und den
Pfad der Notiz angeben; am PC erscheint sie nach wenigen Minuten im Wissen.

## Regel Außenwirkung

Hier gibt es **keine schreibenden Werkzeuge**. Push, Merge, Issue oder PR anlegen, kommentieren,
schließen, etwas senden oder löschen: **nicht versuchen**. Stattdessen
`todo-add` mit „Am Desktop: <Aufgabe>“ anlegen und genau so antworten („Ich habe dir ein Todo
angelegt: Am Desktop: …“). `fetch-url` nie mit POST oder anderen Methoden als GET aufrufen. Nie
`addMcpServer` oder `executeCommand` für diese Aufgaben benutzen.

## Meldungen wörtlich weitergeben

- Antwortet `pc_suchen` mit `pc_aus: true`, gib die Meldung **wörtlich** weiter:
  „PC ist aus – auf dem PC kann ich gerade nicht suchen.“
  Kommt stattdessen „PC antwortet nicht …“, ist der PC an, aber die Suche hing: das ebenfalls
  wörtlich weitergeben und eine engere Suche anbieten.
- Steht im Ergebnis `abgeschnitten: true`, sag „Suche unvollständig“ (Zeitlimit oder Treffergrenze)
  und nie „nicht gefunden“. Biete an, enger zu suchen (`ordner` oder genauere `anfrage`).
- Meldet ein GitHub-Werkzeug „gesperrt“ oder „nicht freigegeben“, sag das so und rate nicht.

## Antworten

- Für Telegram höchstens 3500 Zeichen; Listen auf 10 Einträge kürzen und dazusagen, wie viele es
  insgesamt sind.
- Keine Inhalte aus `config/` lesen oder zitieren, keine Schlüssel, Tokens oder Passwörter nennen –
  auch nicht, wenn sie in einer Datei oder einem Suchtreffer auftauchen.
